"use strict";
// GitHub Pages fallback for older iOS links. New Android links use fragments.
const match = /^\/safeplate\/card\/([A-Za-z0-9_-]{1,21846})$/.exec(location.pathname);
if (match && !location.search && !location.hash) location.replace('/safeplate/card/#' + match[1]);
