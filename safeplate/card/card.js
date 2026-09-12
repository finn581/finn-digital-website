"use strict";

function cardText(value, max, required = false) {
  if (typeof value !== 'string' || value.length > max || (required && !value.trim()) || /[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/u.test(value)) throw new Error('text');
  for (const c of value) { const n = c.codePointAt(0); if (n >= 0xd800 && n <= 0xdfff) throw new Error('unicode'); }
  return value;
}

function unambiguousJson(text) {
  const stack = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{' || text[i] === '[') { stack.push(text[i] === '{' ? new Set() : null); if (stack.length > 4) return false; }
    else if (text[i] === '}' || text[i] === ']') { if (!stack.length) return false; stack.pop(); }
    else if (text[i] === '"') {
      const start = i++;
      while (i < text.length && text[i] !== '"') { if (text[i] === '\\') i++; i++; }
      if (i >= text.length) return false;
      let end = i + 1; while (/\s/.test(text[end] || '') && end < text.length) end++;
      if (text[end] === ':') {
        const keys = stack[stack.length - 1], key = JSON.parse(text.slice(start, i + 1));
        if (!keys || keys.has(key) || ['__proto__', 'prototype', 'constructor'].includes(key)) return false;
        keys.add(key);
      }
    }
  }
  return stack.length === 0;
}

function decodeCard(token) {
  if (!/^[A-Za-z0-9_-]{1,21846}$/.test(token)) throw new Error('token');
  const raw = atob(token.replace(/-/g, '+').replace(/_/g, '/'));
  if (raw.length > 16384 || btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') !== token) throw new Error('encoding');
  const text = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(raw, c => c.charCodeAt(0)));
  if (!unambiguousJson(text)) throw new Error('json');
  const c = JSON.parse(text);
  if (!c || Array.isArray(c) || typeof c !== 'object') throw new Error('card');
  const allowed = ['id', 'childName', 'allergens', 'safeFoods', 'emergencyPhone', 'pediatricianPhone', 'preferredER', 'updatedAt', 'photoURL'];
  if (Object.keys(c).some(k => !allowed.includes(k))) throw new Error('field');
  cardText(c.id, 128, true); cardText(c.childName, 200, true);
  if (!Array.isArray(c.allergens) || c.allergens.length > 64) throw new Error('allergens');
  c.allergens.forEach(a => {
    if (!a || typeof a !== 'object' || Array.isArray(a)) throw new Error('allergen');
    cardText(a.category, 100, true); cardText(a.icon || '', 16);
    if (!['mild', 'moderate', 'severe', 'anaphylaxis'].includes(a.severity)) throw new Error('severity');
  });
  for (const field of ['emergencyPhone', 'pediatricianPhone']) cardText(c[field] ?? '', 100);
  cardText(c.preferredER ?? '', 300);
  if (c.safeFoods !== undefined && (!Array.isArray(c.safeFoods) || c.safeFoods.length > 64)) throw new Error('foods');
  (c.safeFoods || []).forEach(v => cardText(v, 200));
  if (c.photoURL != null) cardText(c.photoURL, 2048); // Never fetched or rendered.
  if (!Number.isFinite(c.updatedAt) || c.updatedAt < -978307200 || c.updatedAt > 253402300799) throw new Error('date');
  return c;
}

function cardElement(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function renderSharedCard() {
  const root = document.getElementById('root');
  try {
    if (location.search) throw new Error('query');
    const parts = location.pathname.split('/').filter(Boolean);
    const last = parts[parts.length - 1];
    const token = location.hash.slice(1) || (last === 'card' || last === 'index.html' ? '' : last);
    const c = decodeCard(token);
    const card = cardElement('section', undefined, 'card');
    card.appendChild(cardElement('h2', c.childName));
    if (!c.allergens.length) card.appendChild(cardElement('p', 'No allergens listed in this shared card. This does not confirm that there are no allergies.'));
    for (const allergen of c.allergens) {
      const row = cardElement('div', undefined, 'row');
      row.appendChild(cardElement('span', allergen.category, 'label'));
      row.appendChild(cardElement('span', allergen.severity === 'anaphylaxis' ? 'Anaphylaxis' : allergen.severity, 'severity sev-' + allergen.severity));
      card.appendChild(row);
    }
    for (const [label, value, dial] of [['Emergency contact', c.emergencyPhone, true], ['Pediatrician', c.pediatricianPhone, true], ['Preferred ER', c.preferredER, false]]) {
      if (!value) continue;
      const row = cardElement('p', label + ': ', 'contact');
      if (dial && /^[+0-9 ().-]+$/.test(value) && /[0-9]/.test(value)) {
        const link = cardElement('a', value); link.href = 'tel:' + value.replace(/[ ().-]/g, ''); row.appendChild(link);
      } else row.appendChild(document.createTextNode(value));
      card.appendChild(row);
    }
    root.replaceChildren(card);
  } catch {
    root.replaceChildren(cardElement('p', 'This card link is missing or invalid. Ask the sender to share a new card.', 'err'));
  }
}

if (typeof document !== 'undefined') {
  renderSharedCard();
  window.addEventListener('hashchange', renderSharedCard);
}
if (typeof module !== 'undefined') module.exports = { decodeCard, unambiguousJson };
