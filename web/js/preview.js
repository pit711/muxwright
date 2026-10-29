// Live-Vorschau der gesendeten DVB-Programme: der Runner dekodiert die Keyframes des fertigen Transportstroms
// (runner/dvb/chain.py, ~1–2 Bilder/s je Programm), der Server liefert sie unter /api/preview[/<key>.jpg].
// Bilder werden als Blob-URL zwischengespeichert, damit ein Neuaufbau der Seite nicht flackert.

import { h, api } from './ui.js';
import { S, txRunning } from './core.js';
import { T } from './i18n.js';

const cache = new Map();          // key -> { t, url, age }
let timer = null, busy = false;
const STALE_S = 5;                // älter: Bild eingefroren (Quelle hängt oder Programm ohne neue Keyframes)

async function poll() {
  if (busy) return;
  if (!document.querySelector('[data-preview]')) { clearInterval(timer); timer = null; return; }
  if (!txRunning()) { paint(); return; }
  busy = true;
  try {
    const r = await api('GET', '/api/preview');
    const seen = new Set();
    for (const it of r.items || []) {
      seen.add(it.key);
      const old = cache.get(it.key);
      if (old && old.t === it.t) { old.age = it.age_s; continue; }
      const res = await fetch(`/api/preview/${encodeURIComponent(it.key)}.jpg`, { cache: 'no-store' });
      if (!res.ok) continue;
      const url = URL.createObjectURL(await res.blob());
      cache.set(it.key, { t: it.t, url, age: it.age_s });
      if (old) setTimeout(() => URL.revokeObjectURL(old.url), 2000);
    }
    for (const [k, v] of cache) if (!seen.has(k)) { URL.revokeObjectURL(v.url); cache.delete(k); }
  } catch { /* nächster Versuch */ } finally { busy = false; }
  paint();
}

function paint() {
  const on = txRunning();
  document.querySelectorAll('[data-preview]').forEach(el => {
    const c = on ? cache.get(el.dataset.preview) : null;
    const img = el.querySelector('img'), st = el.querySelector('.pv-state');
    if (c && img.getAttribute('src') !== c.url) img.src = c.url;
    img.hidden = !c;
    el.classList.toggle('stale', !!c && c.age > STALE_S);
    st.textContent = !on ? T('nicht auf Sendung') : !c ? T('warte auf Bild …') : c.age > STALE_S ? T('Bild seit {s} s unverändert', { s: Math.round(c.age) }) : '';
  });
}

function ensureTimer() {
  if (!timer) timer = setInterval(poll, 1000);
  setTimeout(poll, 0);
}

/** Vorschaubild eines Programms (Schlüssel = dvb.programs[].key) */
export function previewTile(key, name, o = {}) {
  ensureTimer();
  const c = txRunning() ? cache.get(key) : null;
  return h('figure.pv' + (o.small ? '.sm' : ''), { dataset: { preview: key }, tid: o.tid || `preview-${key}` },
    h('div.pv-img', null, h('img', { src: c?.url || null, alt: name, hidden: !c }), h('span.pv-state.muted')),
    name ? h('figcaption', null, name) : null);
}

/** Leiste mit allen TV-Programmen der laufenden Konfiguration */
export function previewStrip(cfg, o = {}) {
  const tv = (cfg?.dvb?.programs || []).filter(p => p.type === 'tv');
  if (!tv.length) return null;
  return h('div.pv-strip' + (o.small ? '.sm' : ''), { tid: o.tid || 'preview-strip' }, tv.map(p => previewTile(p.key, p.name, { small: o.small })));
}

export const previewHint = () => T('Gesendetes Bild je Programm (Keyframes aus dem fertigen Transportstrom, etwa 1–2 Bilder/s).');

S.previewPaint = paint;
