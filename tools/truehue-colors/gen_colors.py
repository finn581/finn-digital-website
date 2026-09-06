#!/usr/bin/env python3
"""
Static-site generator for the TrueHue paint-color library on finndigital.net.

Reads the TrueHue Android catalogue (colornerd.json, 29,875 swatches) and emits
one static reference page per paint color under truehue/colors/, plus per-brand
hub pages, a library index and a sitemap.

Every page carries real, computed data: hex / RGB / HSL / CIELAB straight from the
catalogue, an approximate LRV derived from L*, a chroma+hue undertone descriptor,
and cross-brand nearest-neighbour matches ranked by CIEDE2000.

Dependencies: Python 3.9+, numpy.

Usage:
    python3 gen_colors.py --sample      # 5 reference pages, nothing else
    python3 gen_colors.py --all         # full library: 5,433 color pages + 72 hubs
    python3 gen_colors.py --stats       # counts only, writes nothing

Output is deterministic and idempotent: identical input produces byte-identical
files, and files whose contents have not changed are left untouched on disk.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

SITE = "https://finndigital.net"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]                      # .../finn-digital-website
DEFAULT_DATA = Path(
    "/Volumes/Project SD/code/TrueHue-Kotlin/app/src/main/assets/colornerd.json"
)
OUT_ROOT = REPO_ROOT / "truehue" / "colors"

# Wall-paint brands in colornerd.json.  The catalogue also carries Avery, RAL,
# Neenah, Trumatch, DIC, Toyo, HKS, MPC, H&L, Kobra and IKEA, which are film /
# ink / print / brand-identity systems, not wall paint, and are excluded from
# both the generated pages and the match search.
#
# NOTE: the Android app's onboarding copy claims "19,906 wall paints across 12
# brands".  That arithmetic only closes if IKEA (200 swatches) is counted as a
# wall-paint brand; the 11 brands below sum to 19,706.  The app has no explicit
# wall-paint brand constant to cross-check against -- only a pricing table
# (PaintCalculator.pricePerGallon) and a retailer directory, which between them
# name Behr, Valspar, PPG, Sherwin-Williams, Benjamin Moore, Dunn Edwards and
# Farrow & Ball.  Add "IKEA" here if the app's 12-brand count is the one to match.
WALL_BRANDS = [
    "Behr",
    "Benjamin Moore",
    "Colorhouse",
    "Dunn Edwards",
    "Dutch Boy",
    "Farrow & Ball",
    "KILZ",
    "PPG",
    "Sherwin-Williams",
    "Valspar",
    "Vista",
]

# Brands that get a page per colour.  Everything else is match-target only.
PAGE_BRANDS = ["Sherwin-Williams"]  # Benjamin Moore deferred: upstream names are truncated

# Codes in the catalogue are bare; these prefixes restore the customer-facing form.
CODE_PREFIX = {"Sherwin-Williams": "SW ", "Farrow & Ball": "No. "}

# colornerd.json truncates every Benjamin Moore name to a single word
# ("Hale" for Hale Navy, "Revere" for Revere Pewter, "Chantilly" for Chantilly
# Lace).  All 3,919 BM rows are affected.  Corrections live here, keyed by
# (brand, catalogue code).  Only manually verified entries belong in this map --
# never guess a name.
NAME_FIXES = {
    ("Benjamin Moore", "HC-154"): "Hale Navy",
    ("Benjamin Moore", "960"): "White Dove",
}

MATCHES_PER_OTHER_BRAND = 3
SIMILAR_IN_BRAND = 4
SHORTLIST = 24              # CIE76 candidates fed to CIEDE2000 per brand
HUB_PAGE_SIZE = 150

PLAY_URL = (
    "https://play.google.com/store/apps/details?id=com.finndigital.truehue"
    "&referrer=utm_source%3Dfinndigital%26utm_medium%3Dcolorlib"
    "%26utm_campaign%3Dtruehue-{brand_slug}"
)
APPSTORE_URL = "https://apps.apple.com/app/id6762074869"

TRADEMARK = (
    "Brand and color names are trademarks of their owners. TrueHue and Finn "
    "Digital LLC are not affiliated with any paint manufacturer. Matches are "
    "computed from published color values; always confirm with a physical chip."
)

SAMPLE_REQUESTS = [
    ("Sherwin-Williams", "Agreeable Gray", "7029"),
    ("Sherwin-Williams", "Sea Salt", "6204"),
]

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def slugify(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def esc(s: str) -> str:
    """Escape for an attribute value."""
    return html.escape(str(s), quote=True)


def txt(s: str) -> str:
    """Escape for element text -- leaves apostrophes readable (Miner's Dust)."""
    return html.escape(str(s), quote=False)


def hex_to_rgb(hexstr: str):
    h = hexstr.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hsl(r: int, g: int, b: int):
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0, 0.0, l * 100.0
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == rf:
        h = ((gf - bf) / d) % 6.0
    elif mx == gf:
        h = (bf - rf) / d + 2.0
    else:
        h = (rf - gf) / d + 4.0
    return int(round(h * 60.0)) % 360, s * 100.0, l * 100.0


def lrv_from_l(L: float) -> float:
    """Approximate LRV (= CIE Y) recovered from L*."""
    if L > 8.0:
        return ((L + 16.0) / 116.0) ** 3 * 100.0
    return L / 9.033


def relative_luminance(r: int, g: int, b: int) -> float:
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def best_ink(r: int, g: int, b: int) -> str:
    """Whichever of black / white has the higher contrast ratio on this color."""
    lum = relative_luminance(r, g, b)
    on_black = (lum + 0.05) / 0.05
    on_white = 1.05 / (lum + 0.05)
    return "#000" if on_black >= on_white else "#fff"


def lightness_band(L: float) -> str:
    if L > 85:
        return "off-white"
    if L >= 70:
        return "light"
    if L >= 50:
        return "mid-tone"
    if L >= 30:
        return "deep"
    return "dark"


# CIELAB hue angles, not RGB ones: pure red sits near 40 degrees, orange near 60,
# yellow near 103, green near 136, blue near 306.  Two scales are used -- a fine one
# for naming a saturated colour, and a coarse one for naming a near-neutral's undertone.
HUE_BANDS = [
    (20, "pink"), (50, "red"), (75, "orange"), (95, "amber"), (118, "yellow"),
    (160, "yellow-green"), (190, "green"), (250, "teal"), (300, "blue"),
    (335, "violet"), (360, "pink"),
]

UNDERTONE_BANDS = [
    (20, "pink"), (50, "red"), (80, "orange"), (120, "yellow"), (190, "green"),
    (240, "teal"), (300, "blue"), (340, "violet"), (360, "pink"),
]


def _band(bands, h: float) -> str:
    for limit, name in bands:
        if h < limit:
            return name
    return bands[0][1]


def hue_name(h: float) -> str:
    return _band(HUE_BANDS, h)


def undertone_name(h: float) -> str:
    return _band(UNDERTONE_BANDS, h)


def chromatic_noun(h: float, c: float, L: float) -> str:
    """Common name for a color with enough chroma to be called by its hue."""
    fam = hue_name(h)
    if fam == "red":
        if L < 35:
            return "maroon"
        if L > 80:
            return "pink"
        return "rose" if L >= 55 else "red"
    if fam == "pink":
        if L >= 65:
            return "pink"
        return "plum" if L < 45 else "rose"
    if fam in ("orange", "amber"):
        if L < 45:
            return "brown"
        if c < 25 and L <= 80:
            return "tan"
        if L > 85:
            return "cream"
        return "orange" if fam == "orange" else "gold"
    if fam == "yellow":
        if L > 85:
            return "cream"
        return "olive" if L < 45 else "yellow"
    if fam == "blue":
        return "navy" if L < 45 else "blue"
    if fam == "violet":
        return "plum" if L < 35 else "violet"
    if fam == "green" and L < 30:
        return "forest green"
    return fam


def article(word: str) -> str:
    return "an" if word[0] in "aeiou" else "a"


def descriptor(L: float, a: float, b: float) -> str:
    """Honest undertone descriptor built from chroma and CIELAB hue angle."""
    c = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0
    band = lightness_band(L)
    temp = "warm" if (h <= 120.0 or h >= 340.0) else "cool"

    if c < 4.0:
        if band == "off-white":
            return "a neutral off-white"
        if band == "dark":
            return "a near-black neutral"
        return "a {} neutral gray".format(band)

    if c < 12.0:
        hue = undertone_name(h)
        noun = "off-white" if band == "off-white" else (
            "near-black" if band == "dark" else "gray"
        )
        if band in ("off-white", "dark"):
            return "a {} {} with {} {} undertone".format(temp, noun, article(hue), hue)
        return "a {} {} {} with {} {} undertone".format(
            band, temp, noun, article(hue), hue)

    word = "pale" if band == "off-white" else band
    return "a {} {} {}".format(word, temp, chromatic_noun(h, c, L))


def verdict(de: float) -> str:
    if de <= 1.0:
        return "indistinguishable"
    if de <= 2.0:
        return "very close"
    if de <= 3.5:
        return "close"
    return "nearest available"


def fmt_de(de: float) -> str:
    return "{:.1f}".format(de)


# --------------------------------------------------------------------------
# CIEDE2000
# --------------------------------------------------------------------------


def delta_e2000(ref, lab):
    """ΔE00 between one reference Lab triple and an (M,3) array of Lab triples."""
    L1, a1, b1 = float(ref[0]), float(ref[1]), float(ref[2])
    L2 = lab[:, 0]
    a2 = lab[:, 1]
    b2 = lab[:, 2]

    C1 = math.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    Cbar7 = Cbar ** 7
    G = 0.5 * (1.0 - np.sqrt(Cbar7 / (Cbar7 + 25.0 ** 7)))

    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)

    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0

    dLp = L2 - L1
    dCp = C2p - C1p

    prod = C1p * C2p
    dh = h2p - h1p
    dh = np.where(dh > 180.0, dh - 360.0, dh)
    dh = np.where(dh < -180.0, dh + 360.0, dh)
    dh = np.where(prod == 0.0, 0.0, dh)
    dHp = 2.0 * np.sqrt(prod) * np.sin(np.radians(dh / 2.0))

    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hdiff = np.abs(h1p - h2p)
    hbp = np.where(
        prod == 0.0,
        hsum,
        np.where(
            hdiff <= 180.0,
            hsum / 2.0,
            np.where(hsum < 360.0, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0),
        ),
    )

    T = (
        1.0
        - 0.17 * np.cos(np.radians(hbp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hbp))
        + 0.32 * np.cos(np.radians(3.0 * hbp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hbp - 63.0))
    )
    dtheta = 30.0 * np.exp(-(((hbp - 275.0) / 25.0) ** 2))
    Cbp7 = Cbp ** 7
    Rc = 2.0 * np.sqrt(Cbp7 / (Cbp7 + 25.0 ** 7))
    Sl = 1.0 + (0.015 * (Lbp - 50.0) ** 2) / np.sqrt(20.0 + (Lbp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbp
    Sh = 1.0 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2.0 * dtheta)) * Rc

    return np.sqrt(
        (dLp / Sl) ** 2
        + (dCp / Sc) ** 2
        + (dHp / Sh) ** 2
        + Rt * (dCp / Sc) * (dHp / Sh)
    )


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


class Catalog:
    def __init__(self, path: Path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        seen = set()
        rows = []
        for rec in raw:
            brand = rec.get("brand")
            if brand not in WALL_BRANDS:
                continue
            hexv = (rec.get("hex") or "").strip()
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", hexv):
                continue
            code = rec.get("code")
            code = str(code).strip() if code is not None else ""
            name = NAME_FIXES.get((brand, code), rec.get("name", "")).strip()
            if brand == "Benjamin Moore" and (brand, code) not in NAME_FIXES:
                # colornerd truncates every BM name to one word ("Hale" for
                # Hale Navy). Never publish a wrong name: render by code only.
                name = ""
            if not name and brand != "Benjamin Moore":
                continue
            key = (brand, name, code, hexv.upper())
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "brand": brand,
                    "name": name,
                    "code": code,
                    "hex": hexv.upper(),
                    "L": float(rec["L"]),
                    "a": float(rec["a"]),
                    "b": float(rec["b"]),
                }
            )

        # Deterministic ordering, independent of the file's own order.
        rows.sort(key=lambda r: (r["brand"], r["name"], r["code"], r["hex"]))

        for i, r in enumerate(rows):
            r["idx"] = i
            r["display_code"] = (CODE_PREFIX.get(r["brand"], "") + r["code"]).strip()
            if r["brand"] == "Benjamin Moore" and not r["name"]:
                r["name"], r["display_code"] = r["display_code"], ""
            r["brand_slug"] = slugify(r["brand"])
            stem = r["name"] + " " + (r["display_code"] or r["hex"].lstrip("#"))
            r["slug"] = slugify(stem)
            r["url"] = "/truehue/colors/{}/{}/".format(r["brand_slug"], r["slug"])

        # Disambiguate any surviving slug collisions deterministically.
        by_slug = {}
        for r in rows:
            by_slug.setdefault((r["brand_slug"], r["slug"]), []).append(r)
        for (bslug, s), group in by_slug.items():
            if len(group) > 1:
                for n, r in enumerate(group[1:], start=2):
                    r["slug"] = "{}-{}".format(s, n)
                    r["url"] = "/truehue/colors/{}/{}/".format(bslug, r["slug"])

        self.rows = rows
        self.lab = np.array([[r["L"], r["a"], r["b"]] for r in rows], dtype=np.float64)
        self.brand_idx = {}
        for r in rows:
            self.brand_idx.setdefault(r["brand"], []).append(r["idx"])
        self.brand_idx = {b: np.array(v, dtype=np.int64) for b, v in self.brand_idx.items()}
        self.page_slugs = set()

    def mark_page_brands(self, brands):
        self.page_slugs = {r["idx"] for r in self.rows if r["brand"] in brands}

    def nearest(self, row, brand, k):
        """k nearest rows in `brand` to `row`, by ΔE2000, excluding `row` itself."""
        pool = self.brand_idx.get(brand)
        if pool is None or len(pool) == 0:
            return []
        ref = np.array([row["L"], row["a"], row["b"]], dtype=np.float64)
        pool_lab = self.lab[pool]

        # Stage 1: vectorised CIE76 shortlist.
        d = pool_lab - ref
        d2 = np.einsum("ij,ij->i", d, d)
        want = min(len(pool), max(k + 1, SHORTLIST))
        if want < len(pool):
            cand = np.argpartition(d2, want - 1)[:want]
        else:
            cand = np.arange(len(pool))

        # Stage 2: exact CIEDE2000 on the shortlist.
        de = delta_e2000(ref, pool_lab[cand])
        order = sorted(
            range(len(cand)),
            key=lambda i: (round(float(de[i]), 6), self.rows[int(pool[cand[i]])]["name"],
                           self.rows[int(pool[cand[i]])]["code"]),
        )
        out = []
        for i in order:
            gi = int(pool[cand[i]])
            if gi == row["idx"]:
                continue
            out.append((self.rows[gi], float(de[i])))
            if len(out) == k:
                break
        return out


# --------------------------------------------------------------------------
# CSS -- derived from truehue/index.html, trimmed to the blocks these pages use
# --------------------------------------------------------------------------

BASE_CSS = """@font-face{font-family:Syne;font-weight:100 1000;font-display:swap;src:url(/assets/fonts/Syne.woff2)}
:root{--bg:#0a0d14;--sf:#141821;--tx:#ece9e0;--mu:#a8a497;--ac:#c9a84c;--bd:rgba(255,255,255,.08)}
*{box-sizing:border-box;margin:0;padding:0}
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,Inter,sans-serif;background:var(--bg);color:var(--tx)}
.studio-wordmark,h1{font-family:Syne,sans-serif;font-weight:800;letter-spacing:-.02em}
.studio-header{text-align:center;padding:clamp(28px,4vw,40px) 20px 0}
.studio-wordmark{font-size:clamp(28px,4vw,40px);color:var(--tx);text-decoration:none}
.dot{color:var(--ac)}
.studio-nav{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;padding:15px 20px clamp(18px,3vw,24px);border-bottom:1px solid var(--bd);font-size:13px;font-weight:500}
.studio-nav a,.studio-nav span{color:var(--mu);text-decoration:none}
.sep{opacity:.45}
.container{max-width:1080px;margin:0 auto;padding:clamp(28px,4vw,44px) clamp(20px,4vw,48px) 56px}
h1{font-size:clamp(30px,4.4vw,46px);line-height:1.05}
h2{font-size:clamp(18px,2.1vw,23px);margin:clamp(34px,4vw,44px) 0 14px}
p{color:var(--mu);margin-bottom:14px}
.w{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:clamp(13px,1.4vw,14px)}
th{text-align:left;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--mu);padding:0 8px 8px;border-bottom:1px solid var(--bd)}
td{padding:9px 8px;border-bottom:1px solid var(--bd);vertical-align:middle;white-space:nowrap;font-variant-numeric:tabular-nums}
.m td:first-child,.hb td:first-child{width:36px;min-width:36px;padding:0;box-shadow:inset 0 0 0 5px var(--bg)}
td b{font-weight:600;color:var(--mu);font-size:13px}
td a{color:var(--tx);text-decoration:none;border-bottom:1px solid rgba(201,168,76,.5)}
td a:hover{color:var(--ac)}
.m td:nth-last-child(2){text-align:right}
.m td:last-child{color:var(--mu)}
.tm{font-size:12px;opacity:.75;margin-top:26px;padding-top:18px;border-top:1px solid var(--bd)}
footer{text-align:center;color:var(--mu);font-size:12px;padding:40px 16px;border-top:1px solid var(--bd);margin-top:56px}
footer a{color:var(--mu);text-decoration:none;margin:0 8px}"""

COLOR_CSS = """.band{padding:clamp(40px,6vw,64px) clamp(20px,3vw,32px);text-align:center}
.band p{color:inherit}
.bl{font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 10px;opacity:.72}
.cd{font-weight:600;font-size:.52em;display:block;margin-top:8px;opacity:.78}
.hx{font-family:ui-monospace,Menlo,monospace;font-size:clamp(22px,3vw,34px);font-weight:600;margin:18px 0 0}
dl{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--bd);border:1px solid var(--bd);border-radius:14px;overflow:hidden}
dl div{background:var(--sf);padding:14px 16px}
dt{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--mu);margin-bottom:4px}
dd{font-weight:600;overflow-wrap:anywhere}
.badges{display:flex;gap:14px;flex-wrap:wrap;margin-top:14px}
.badges img{display:block;height:48px;width:auto}
@media(max-width:880px){dl{grid-template-columns:repeat(2,minmax(0,1fr))}}"""

HUB_CSS = """.hb td:last-child{text-align:right}
.pg a,.pg span{margin-right:10px;color:var(--mu);text-decoration:none}"""

HEADER = (
    '<header class="studio-header">'
    '<a href="/" class="studio-wordmark">FINN<span class="dot">.</span>DIGITAL</a>'
    "</header>"
)

FOOTER = (
    "<footer>© 2026 Finn Digital LLC · "
    '<a href="/">finndigital.net</a> · '
    '<a href="/truehue/">TrueHue</a> · '
    '<a href="mailto:contact@finndigital.net">Contact</a></footer>'
)


def join_parts(parts):
    """Join page fragments with no filler whitespace; the head/body boundaries
    already carry their own newlines."""
    return "".join(parts)


def nav(items):
    """items: list of (label, href|None)."""
    parts = []
    for i, (label, href) in enumerate(items):
        if i:
            parts.append('<span class="sep">·</span>')
        if href:
            parts.append('<a href="{}">{}</a>'.format(href, esc(label)))
        else:
            parts.append("<span>{}</span>".format(esc(label)))
    return '<nav class="studio-nav" aria-label="Breadcrumb">{}</nav>'.format("".join(parts))


def breadcrumb_ld(trail):
    """trail: list of (name, absolute-url). The last item's URL is the page itself
    and is omitted, which schema.org permits and Google recommends."""
    items = []
    for i, (name, url) in enumerate(trail):
        item = {"@type": "ListItem", "position": i + 1, "name": name}
        if i < len(trail) - 1:
            item["item"] = url
        items.append(item)
    return json.dumps(
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def head(title, description, canonical, ld, css):
    css = css.replace("\n", "")
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset=utf-8>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>{title}</title>\n"
        '<meta name="description" content="{desc}">\n'
        '<meta name="robots" content="index,follow,max-image-preview:large">\n'
        '<link rel="canonical" href="{canon}">\n'
        '<meta property="og:title" content="{title}">\n'
        '<meta property="og:description" content="{desc}">\n'
        '<script type="application/ld+json">{ld}</script>\n'
        "<style>{css}</style>\n</head>\n<body>\n".format(
            title=esc(title), desc=esc(description), canon=esc(canonical), ld=ld, css=css
        )
    )


# --------------------------------------------------------------------------
# Titles and descriptions
# --------------------------------------------------------------------------


def page_title(row):
    n, c, b = row["name"], row["display_code"], row["brand"]
    candidates = [
        "{} {} by {} — closest paint matches".format(n, c, b) if c else
        "{} by {} — closest paint matches".format(n, b),
        "{} {} by {} — closest matches".format(n, c, b) if c else
        "{} by {} — closest matches".format(n, b),
        "{} by {} — closest matches".format(n, b),
        "{} {} — closest paint matches".format(n, c) if c else
        "{} — closest paint matches".format(n),
    ]
    for t in candidates:
        if len(t) <= 60:
            return t
    return candidates[-1][:60].rstrip(" —-")


def page_description(row, desc, lrv, best):
    """Front-loaded: brand, name, code, descriptor, then the single closest match."""
    ident = " ".join(x for x in (row["brand"], row["name"], row["display_code"]) if x)
    heads = [
        "{}: {}. Hex {}, LRV ~{:.0f}.".format(ident, desc, row["hex"], lrv),
        "{}: {}. Hex {}.".format(ident, desc, row["hex"]),
        "{}: {}.".format(ident, desc),
    ]
    if not best:
        return heads[0][:155]
    other, de = best
    tails = [
        " Closest {}: {} {}, ΔE {}.".format(
            other["brand"], other["name"], other["display_code"], fmt_de(de)),
        " Closest {}: {}, ΔE {}.".format(other["brand"], other["name"], fmt_de(de)),
    ]
    for h in heads:
        for t in tails:
            if len(h + t) <= 155:
                return h + t
    return heads[-1][:155]


# --------------------------------------------------------------------------
# Page rendering
# --------------------------------------------------------------------------


def color_cell(cat, other):
    label = "{}{}".format(
        txt(other["name"]),
        " <b>{}</b>".format(txt(other["display_code"])) if other["display_code"] else "",
    )
    if other["idx"] in cat.page_slugs:
        return '<a href="{}">{}</a>'.format(other["url"], label)
    return label


def match_rows(cat, pairs, with_brand):
    """Rows for a matches table. When `with_brand`, consecutive rows from the same
    brand share a single rowspanned brand cell."""
    runs = []
    for other, de in pairs:
        if with_brand and runs and runs[-1][0] == other["brand"]:
            runs[-1][1].append((other, de))
        else:
            runs.append((other["brand"], [(other, de)]))
    out = []
    for brand, group in runs:
        for i, (other, de) in enumerate(group):
            cells = ["<td style=background:{}></td>".format(other["hex"])]
            if with_brand and i == 0:
                cells.append(
                    "<td rowspan={}>{}</td>".format(len(group), txt(brand))
                    if len(group) > 1 else "<td>{}</td>".format(txt(brand))
                )
            cells.append("<td>{}</td>".format(color_cell(cat, other)))
            cells.append("<td>{}</td>".format(fmt_de(de)))
            cells.append("<td>{}</td>".format(verdict(de)))
            out.append("<tr>{}</tr>".format("".join(cells)))
    return "".join(out)


def summary_paragraph(row, cross, within):
    """2-3 sentences built only from the computed numbers."""
    if not cross:
        return "No cross-brand matches were computed for this color."
    best_other, best_de = cross[0]
    s1 = "The closest match outside {} is {} {} {} at ΔE2000 {}.".format(
        row["brand"], best_other["brand"], best_other["name"],
        best_other["display_code"], fmt_de(best_de)
    )
    # First entry from the second-best brand.
    runner = None
    for other, de in cross[1:]:
        if other["brand"] != best_other["brand"]:
            runner = (other, de)
            break
    if runner:
        gap = runner[1] - best_de
        if gap < 0.05:
            s2 = " The next brand, {}, is the same distance at ΔE {}.".format(
                runner[0]["brand"], fmt_de(runner[1]))
        else:
            s2 = " The next brand is {} at ΔE {}, {} further away.".format(
                runner[0]["brand"], fmt_de(runner[1]), fmt_de(gap))
    else:
        s2 = ""
    brands_under_2 = len({o["brand"] for o, d in cross if d <= 2.0})
    total_brands = len({o["brand"] for o, d in cross})
    s3 = " Of the {} other wall-paint brands measured, {} hold a match under ΔE 2.0".format(
        total_brands, brands_under_2
    )
    if within:
        s3 += ", and the nearest {} color is {} {} at ΔE {}.".format(
            row["brand"], within[0][0]["name"], within[0][0]["display_code"],
            fmt_de(within[0][1])
        )
    else:
        s3 += "."
    return s1 + s2 + s3


def render_color_page(cat, row):
    r, g, b = hex_to_rgb(row["hex"])
    hh, ss, ll = rgb_to_hsl(r, g, b)
    lrv = lrv_from_l(row["L"])
    desc = descriptor(row["L"], row["a"], row["b"])
    ink = best_ink(r, g, b)

    # Matches.
    cross = []
    for brand in WALL_BRANDS:
        if brand == row["brand"]:
            continue
        cross.extend(cat.nearest(row, brand, MATCHES_PER_OTHER_BRAND))
    # Group by brand, brands ordered by their best match.
    by_brand = {}
    for other, de in cross:
        by_brand.setdefault(other["brand"], []).append((other, de))
    for v in by_brand.values():
        v.sort(key=lambda p: (round(p[1], 6), p[0]["name"], p[0]["code"]))
    ordered_brands = sorted(by_brand, key=lambda bn: (round(by_brand[bn][0][1], 6), bn))
    cross_sorted = [p for bn in ordered_brands for p in by_brand[bn]]

    within = cat.nearest(row, row["brand"], SIMILAR_IN_BRAND)

    ident = " ".join(x for x in (row["name"], row["display_code"]) if x)
    full_ident = "{} {}".format(row["brand"], ident)
    canonical = SITE + row["url"]
    brand_url = "/truehue/colors/{}/".format(row["brand_slug"])

    trail = [
        ("Home", SITE + "/"),
        ("TrueHue", SITE + "/truehue/"),
        ("Colors", SITE + "/truehue/colors/"),
        (row["brand"], SITE + brand_url),
        (ident, canonical),
    ]

    parts = [
        head(
            page_title(row),
            page_description(row, desc, lrv, cross_sorted[0] if cross_sorted else None),
            canonical,
            breadcrumb_ld(trail),
            BASE_CSS + COLOR_CSS,
        ),
        HEADER,
        nav([("TrueHue", "/truehue/"), ("Color library", "/truehue/colors/"),
             (row["brand"], brand_url)]),
        "<main>",
        "<article>",
        # Hero band, painted in the colour itself.
        '<section class="band" style="background:{hex};color:{ink}">'
        '<p class="bl">{brand}</p><h1>{name}{code}</h1><p class="hx">{hex}</p>'
        "</section>".format(
            hex=row["hex"], ink=ink, brand=txt(row["brand"]), name=txt(row["name"]),
            code='<span class="cd">{}</span>'.format(txt(row["display_code"]))
            if row["display_code"] else "",
        ),
        '<div class="container">',
        # Data grid.
        "<section>",
        "<h2>What are the color values for {}?</h2>".format(txt(ident)),
        "<dl>"
        "<div><dt>Hex</dt><dd>{}</dd></div>"
        "<div><dt>RGB</dt><dd>{}, {}, {}</dd></div>"
        "<div><dt>HSL</dt><dd>{}°, {:.0f}%, {:.0f}%</dd></div>"
        "<div><dt>CIELAB</dt><dd>L {:.1f} · a {:.1f} · b {:.1f}</dd></div>"
        "<div><dt>Approx. LRV (from L*)</dt><dd>{:.0f}</dd></div>"
        "<div><dt>Undertone</dt><dd>{}</dd></div>"
        "<div><dt>Brand</dt><dd>{}</dd></div>"
        "<div><dt>Code</dt><dd>{}</dd></div>"
        "</dl>".format(
            row["hex"], r, g, b, hh, ss, ll, row["L"], row["a"], row["b"], lrv,
            txt(desc), txt(row["brand"]), txt(row["display_code"]) or "—"),
        "</section>",
        # Cross-brand matches.
        "<section>",
        "<h2>Which paint colors are closest to {}?</h2>".format(txt(ident)),
        '<div class="w"><table class="m">'
        "<thead><tr><th></th><th>Brand</th><th>Color</th><th>ΔE2000</th>"
        "<th>Verdict</th></tr></thead>",
        match_rows(cat, cross_sorted, with_brand=True),
        "</table></div>",
        "</section>",
        # Same-brand neighbours.
        "<section>",
        "<h2>Which {} colors are similar to {}?</h2>".format(
            txt(row["brand"]), txt(row["name"])),
        '<div class="w"><table class="m">'
        "<thead><tr><th></th><th>Color</th><th>ΔE2000</th><th>Verdict</th>"
        "</tr></thead>",
        match_rows(cat, within, with_brand=False),
        "</table></div>",
        "</section>",
        # CTA.
        "<section>",
        "<h2>Scan your wall and match it live</h2>",
        "<p>TrueHue reads the color off a real wall and returns the nearest paints "
        "in every brand on this page.</p>",
        '<div class="badges">',
        '<a href="{}" rel="noopener"><img src="/assets/app-store-badge.svg" '
        'alt="Download on the App Store" width="144" height="48" loading="lazy"></a>'
        .format(APPSTORE_URL),
        '<a href="{}" rel="noopener"><img src="/assets/google-play-badge.svg?v=2" '
        'alt="Get it on Google Play" width="162" height="48" loading="lazy"></a>'
        .format(PLAY_URL.format(brand_slug=row["brand_slug"])),
        "</div>",
        "</section>",
        # Data-driven summary + trademark note.
        "<section>",
        "<h2>How far apart are these matches?</h2>",
        "<p>{}</p>".format(txt(summary_paragraph(row, cross_sorted, within))),
        '<p class="tm">{}</p>'.format(txt(TRADEMARK)),
        "</section>",
        "</div>",
        "</article>",
        "</main>",
        FOOTER,
        "</body>\n</html>\n",
    ]
    return join_parts(parts)


def render_brand_hub(cat, brand, page, pages, rows):
    bslug = slugify(brand)
    base = "/truehue/colors/{}/".format(bslug)
    url = base if page == 1 else "{}page-{}/".format(base, page)
    canonical = SITE + url
    total = sum(len(c) for c in pages)
    suffix = "" if len(pages) == 1 else " (page {} of {})".format(page, len(pages))
    title = "{} paint colors — hex, LRV and matches".format(brand)
    if len(title) > 60:
        title = "{} paint colors — hex and LRV".format(brand)
    desc = (
        "{} paint color reference: {} colors with hex, RGB, CIELAB and approximate "
        "LRV, plus closest matches in other brands.".format(brand, total)
    )[:155]
    trail = [
        ("Home", SITE + "/"),
        ("TrueHue", SITE + "/truehue/"),
        ("Colors", SITE + "/truehue/colors/"),
        (brand, SITE + base),
    ]
    body_rows = []
    for r in rows:
        lrv = lrv_from_l(r["L"])
        name_cell = (
            '<a href="{}">{}</a>'.format(r["url"], txt(r["name"]))
            if r["idx"] in cat.page_slugs
            else txt(r["name"])
        )
        body_rows.append(
            '<tr><td style="background:{}"></td><td>{}</td>'
            "<td><b>{}</b></td><td>{}</td><td>{:.0f}</td></tr>".format(
                r["hex"], name_cell, txt(r["display_code"]) or "—", r["hex"], lrv
            )
        )
    pager = []
    if len(pages) > 1:
        for p in range(1, len(pages) + 1):
            href = base if p == 1 else "{}page-{}/".format(base, p)
            pager.append(
                "<span>{}</span>".format(p) if p == page
                else '<a href="{}">{}</a>'.format(href, p)
            )
    parts = [
        head(title, desc, canonical, breadcrumb_ld(trail), BASE_CSS + HUB_CSS),
        HEADER,
        nav([("TrueHue", "/truehue/"), ("Color library", "/truehue/colors/"), (brand, None)]),
        "<main>",
        '<div class="container">',
        "<h1>{} paint colors{}</h1>".format(txt(brand), suffix),
        "<p>{} {} colors from the TrueHue catalog, with hex and approximate LRV "
        "computed from L*.</p>".format(total, txt(brand)),
        '<div class="w"><table class="hb">'
        "<thead><tr><th></th><th>Color</th><th>Code</th><th>Hex</th>"
        "<th>Approx. LRV</th></tr></thead>",
        "".join(body_rows),
        "</table></div>",
        ('<p class="pg">{}</p>'.format("".join(pager)) if pager else ""),
        '<p class="tm">{}</p>'.format(txt(TRADEMARK)),
        "</div>",
        "</main>",
        FOOTER,
        "</body>\n</html>\n",
    ]
    return url, join_parts(parts)


def render_index(cat, brands_with_counts, page_count):
    canonical = SITE + "/truehue/colors/"
    title = "Paint color library — hex, LRV, cross-brand matches"
    desc = (
        "Paint color reference for {} wall-paint brands: hex, RGB, CIELAB, approximate "
        "LRV and closest cross-brand matches by CIEDE2000.".format(len(brands_with_counts))
    )[:155]
    trail = [
        ("Home", SITE + "/"),
        ("TrueHue", SITE + "/truehue/"),
        ("Colors", canonical),
    ]
    rows = []
    for brand, count, pages_generated in brands_with_counts:
        rows.append(
            '<tr><td><a href="/truehue/colors/{}/">{}</a></td>'
            "<td>{}</td><td>{}</td></tr>".format(
                slugify(brand), txt(brand), count, pages_generated or "—"
            )
        )
    parts = [
        head(title, desc, canonical, breadcrumb_ld(trail), BASE_CSS),
        HEADER,
        nav([("TrueHue", "/truehue/"), ("Color library", None)]),
        "<main>",
        '<div class="container">',
        "<h1>Paint color library</h1>",
        "<p>Hex, RGB, HSL, CIELAB and approximate LRV for every color in the TrueHue "
        "catalog, with the closest match in each other wall-paint brand computed by "
        "CIEDE2000. {} color pages across {} brand{}.</p>".format(
            page_count, len(brands_with_counts), "" if len(brands_with_counts) == 1 else "s"),
        '<div class="w"><table>'
        "<thead><tr><th>Brand</th><th>Colors</th><th>Color pages</th></tr></thead>",
        "".join(rows),
        "</table></div>",
        '<p class="tm">{}</p>'.format(txt(TRADEMARK)),
        "</div>",
        "</main>",
        FOOTER,
        "</body>\n</html>\n",
    ]
    return join_parts(parts)


def render_sitemap(urls):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append("<url><loc>{}{}</loc></url>".format(SITE, u))
    lines.append("</urlset>")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write(path: Path, content: str) -> bool:
    """Write only when the bytes differ, so reruns are idempotent and leave mtimes alone."""
    data = content.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def out_path(url: str) -> Path:
    rel = url.strip("/")
    return REPO_ROOT / rel / "index.html"


# --------------------------------------------------------------------------
# Colour resolution for --sample
# --------------------------------------------------------------------------


def resolve(cat, brand, name, code):
    """Find the catalogue record for a requested color; report any substitution."""
    pool = [r for r in cat.rows if r["brand"] == brand]
    want_code = (code or "").strip().upper()
    exact = [r for r in pool if r["name"].lower() == name.lower()
             and (not want_code or r["code"].upper() == want_code)]
    if exact:
        return exact[0], "exact"
    by_code = [r for r in pool if want_code and r["code"].upper() == want_code]
    if by_code:
        return by_code[0], "matched by code ({}); catalogue name is {!r}".format(
            want_code, by_code[0]["name"])
    by_name = [r for r in pool if r["name"].lower() == name.lower()]
    if by_name:
        return by_name[0], "matched by name; catalogue code is {!r}".format(by_name[0]["code"])
    import difflib
    best = max(
        pool,
        key=lambda r: (
            difflib.SequenceMatcher(None, name.lower(), r["name"].lower()).ratio(),
            -len(r["name"]),
        ),
    )
    ratio = difflib.SequenceMatcher(None, name.lower(), best["name"].lower()).ratio()
    return best, "NOT IN CATALOGUE - closest name {!r} {} (similarity {:.2f})".format(
        best["name"], best["display_code"], ratio)


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


def run_sample(cat, args):
    cat.mark_page_brands(PAGE_BRANDS)
    written = []
    print("Resolving the 5 sample colors:")
    for brand, name, code in SAMPLE_REQUESTS:
        row, how = resolve(cat, brand, name, code)
        print("  {:16s} {!r} {} -> {} {} {}  [{}]".format(
            brand, name, code or "", row["name"], row["display_code"], row["hex"], how))
        htmlout = render_color_page(cat, row)
        p = out_path(row["url"])
        changed = write(p, htmlout)
        written.append((p, len(htmlout.encode("utf-8")), changed))
    print("\nWrote {} pages:".format(len(written)))
    for p, size, changed in written:
        flag = "" if changed else "  (unchanged)"
        warn = "  ** OVER 12 KB **" if size > 12288 else ""
        print("  {:6d} B  {}{}{}".format(size, p, flag, warn))
    return 0


def run_all(cat, args):
    cat.mark_page_brands(PAGE_BRANDS)
    urls = []
    n_written = 0
    n_pages = 0
    sizes = []

    page_rows = [r for r in cat.rows if r["brand"] in PAGE_BRANDS]
    for i, row in enumerate(page_rows, 1):
        htmlout = render_color_page(cat, row)
        if write(out_path(row["url"]), htmlout):
            n_written += 1
        sizes.append(len(htmlout.encode("utf-8")))
        urls.append(row["url"])
        n_pages += 1
        if args.verbose and i % 250 == 0:
            print("  {}/{} color pages".format(i, len(page_rows)), file=sys.stderr)

    brands_with_counts = []
    for brand in PAGE_BRANDS:  # hubs only for brands that have colour pages
        rows = [r for r in cat.rows if r["brand"] == brand]
        rows.sort(key=lambda r: (r["name"], r["code"]))
        chunks = [rows[i:i + HUB_PAGE_SIZE] for i in range(0, len(rows), HUB_PAGE_SIZE)] or [[]]
        generated = sum(1 for r in rows if r["idx"] in cat.page_slugs)
        brands_with_counts.append((brand, len(rows), generated))
        for pnum, chunk in enumerate(chunks, 1):
            url, htmlout = render_brand_hub(cat, brand, pnum, chunks, chunk)
            if write(out_path(url), htmlout):
                n_written += 1
            urls.append(url)
            n_pages += 1

    idx_html = render_index(cat, brands_with_counts, len(page_rows))
    if write(out_path("/truehue/colors/"), idx_html):
        n_written += 1
    urls.append("/truehue/colors/")
    n_pages += 1

    urls = sorted(set(urls))
    if write(OUT_ROOT / "sitemap.xml", render_sitemap(urls)):
        n_written += 1

    print("color pages : {}".format(len(page_rows)))
    print("hub + index  : {}".format(n_pages - len(page_rows)))
    print("total pages  : {}".format(n_pages))
    print("changed      : {}".format(n_written))
    print("largest page : {} B   median {} B".format(
        max(sizes), int(sorted(sizes)[len(sizes) // 2])))
    over = sum(1 for s in sizes if s > 12288)
    if over:
        print("WARNING: {} color pages exceed 12 KB".format(over))
    print("sitemap      : {} ({} URLs)".format(OUT_ROOT / "sitemap.xml", len(urls)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate the TrueHue paint-color library for finndigital.net."
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sample", action="store_true",
                      help="generate 5 reference pages only")
    mode.add_argument("--all", action="store_true",
                      help="generate every Sherwin-Williams and Benjamin Moore color page, "
                           "brand hubs for all wall-paint brands, the library index and "
                           "the sitemap")
    mode.add_argument("--stats", action="store_true",
                      help="print catalogue counts and exit without writing anything")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA,
                    help="path to colornerd.json (default: the TrueHue Android asset)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not args.data.exists():
        print("color catalogue not found: {}".format(args.data), file=sys.stderr)
        return 2

    cat = Catalog(args.data)

    if args.stats:
        cat.mark_page_brands(PAGE_BRANDS)
        total_hub = 0
        for brand in WALL_BRANDS:
            n = len(cat.brand_idx.get(brand, []))
            hubs = max(1, -(-n // HUB_PAGE_SIZE))
            total_hub += hubs
            print("{:18s} {:5d} colors   {:3d} hub page(s)".format(brand, n, hubs))
        n_color = len([r for r in cat.rows if r["brand"] in PAGE_BRANDS])
        print("-" * 52)
        print("wall-paint colors       : {}".format(len(cat.rows)))
        print("color pages (--all)     : {}".format(n_color))
        print("hub pages                : {}".format(total_hub))
        print("library index            : 1")
        print("TOTAL HTML pages (--all) : {}".format(n_color + total_hub + 1))
        return 0

    if args.sample:
        return run_sample(cat, args)
    return run_all(cat, args)


if __name__ == "__main__":
    sys.exit(main())
