// Live-Vorschau der gesendeten DVB-Programme, je Programm zwei Bilder:
//  Quelle – der Runner dekodiert den fertigen Transportstrom (runner/dvb/chain.py, ~12 Bilder/s), das Dashboard
//           fragt /api/preview/<key>.jpg fortlaufend ab (ETag/304) und zeigt so ein flüssiges Vorschauvideo.
//  RX     – Kontrollempfang über den RX-Pfad des Pluto (pc/server.py, _rx_preview_loop): ein Bild etwa alle 30–50 s,
//           unter /api/preview/rx/<key>.jpg. Läuft nur, solange eine Vorschau sichtbar ist (?rx=1).
// Bilder werden als Blob-URL zwischengespeichert, damit ein Neuaufbau der Seite nicht flackert.

import { h, api, icon } from './ui.js';
import { S, txRunning } from './core.js';
import { T } from './i18n.js';
import { fmtNum } from './dab.js';

const cache = { tx: new Map(), rx: new Map() };   // key -> { t, url, age }
let rxState = null;
let timer = null, busy = false, txLoop = false;
let txAvail = new Set();         // Programme, für die der Server ein Quellbild meldet (sonst 404 in der Konsole)
const TX_MS = 80;                 // Abfrageabstand des Quellvideos (Runner liefert ~12 Bilder/s)
const STALE_S = 5;                // älter: Bild eingefroren (Quelle hängt oder Programm ohne neue Keyframes)

async function fetchImages(kind, items, path) {
  const c = cache[kind], seen = new Set();
  for (const it of items || []) {
    seen.add(it.key);
    const old = c.get(it.key);
    if (old && old.t === it.t) { old.age = it.age_s; continue; }
    const res = await fetch(`${path}${encodeURIComponent(it.key)}.jpg`, { cache: 'no-store' });
    if (!res.ok) continue;
    const url = URL.createObjectURL(await res.blob());
    c.set(it.key, { t: it.t, url, age: it.age_s });
    if (old) setTimeout(() => URL.revokeObjectURL(old.url), 2000);
  }
  for (const [k, v] of c) if (!seen.has(k)) { URL.revokeObjectURL(v.url); c.delete(k); }
}

async function poll() {
  if (busy) return;
  if (!document.querySelector('[data-preview]')) { clearInterval(timer); timer = null; return; }
  if (!txRunning()) { paint(); return; }
  busy = true;
  try {
    const r = await api('GET', '/api/preview?rx=1');
    rxState = r.rx || null;
    txAvail = new Set((r.items || []).map(it => it.key));
    for (const it of r.items || []) { const c = cache.tx.get(it.key); if (c) c.age = it.age_s; }
    await fetchImages('rx', rxState?.items, '/api/preview/rx/');
  } catch { /* nächster Versuch */ } finally { busy = false; }
  paint();
  paintQuality();
}

function rxText(c) {
  if (!rxState?.available) return T('nur im Gerätepfad bei DVB-T/C/S');
  if (c) return rxState.busy ? T('vor {s} s · empfange …', { s: Math.round(c.age) }) : T('vor {s} s', { s: Math.round(c.age) });
  if (rxState.busy) return T('Empfange …');
  if (rxState.error_code === 'grdtv_missing') return T('GNU Radio gr-dtv fehlt – Einrichtung → Komponenten');
  if (rxState.error) return T('kein Empfang: {msg}', { msg: rxState.error });
  return T('warte auf Bild …');
}

function paint() {
  const on = txRunning();
  document.querySelectorAll('[data-preview] .pv-img').forEach(el => {
    const kind = el.dataset.kind, key = el.closest('[data-preview]').dataset.preview;
    const c = on ? cache[kind].get(key) : null;
    const img = el.querySelector('img'), st = el.querySelector('.pv-state');
    if (c && img.getAttribute('src') !== c.url) img.src = c.url;
    img.hidden = !c;
    const stale = kind === 'tx' && !!c && c.age > STALE_S;
    el.classList.toggle('stale', stale);
    if (kind === 'rx') {
      const qa = on ? rxState?.quality : null, lab = el.querySelector('.pv-label');
      lab.textContent = T('Empfang (Pluto-RX)') + (qa ? ` · MER ${fmtNum(qa.mer_db, 1)} dB` : '');
    }
    st.textContent = !on ? T('nicht auf Sendung')
      : kind === 'rx' ? rxText(c)
      : !c ? T('warte auf Bild …') : stale ? T('Bild seit {s} s unverändert', { s: Math.round(c.age) }) : '';
  });
}

// MER-Bewertung grob für DVB-T 64-QAM 2/3 (Grenze fehlerfreier Empfang ~20 dB)
const merClass = db => db >= 25 ? 'ok' : db >= 20 ? 'warn' : 'bad';

function paintQuality() {
  const qa = txRunning() ? rxState?.quality : null;
  document.querySelectorAll('[data-quality]').forEach(el => {
    const cv = el.querySelector('canvas'), info = el.querySelector('.pvq-info');
    el.hidden = !qa;
    if (!qa || el.dataset.t === String(qa.t)) return;
    el.dataset.t = String(qa.t);
    const fmt = (v, d = 1) => D_fmt(v, d);
    info.replaceChildren(
      h('div.pvq-mer.' + merClass(qa.mer_db), { title: T('Schätzung mit dem Pluto als Messempfänger, kein kalibriertes Messgerät') },
        h('small', null, 'MER'), h('b.mono', null, `${fmt(qa.mer_db)} dB`)),
      h('dl', null,
        h('dt', null, T('Ausreißer')), h('dd.mono', null, `${fmt(qa.outlier_pct, 2)} %`),
        h('dt', null, T('Transportfehler')), h('dd.mono', null, String(qa.transport_errors ?? '–')),
        h('dt', null, T('TS-Pakete')), h('dd.mono', null, String(qa.ts_packets ?? '–')),
        h('dt', null, T('RX-Verstärkung')), h('dd.mono', null, `${fmt(qa.rx_gain_db, 0)} dB`),
        h('dt', null, T('Aussteuerung')), h('dd.mono', null, `${Math.round(qa.peak / 20.47)} % · Clipping ${fmt(qa.clip_pct, 2)} %`),
        h('dt', null, T('Messung')), h('dd.mono', null, T('vor {s} s', { s: Math.round(qa.age_s) }))),
      qa.excluded_carriers ? h('p.hint', null, icon('info'), T('{n} Träger in Kanalmitte nicht gewertet (DC-Übersprechen des Pluto-Empfängers, kein Senderfehler).', { n: qa.excluded_carriers })) : null);
    drawConstellation(cv, qa.constellation);
  });
}
const D_fmt = (v, d) => v == null ? '–' : fmtNum(v, d);

function drawConstellation(cv, c) {
  const n = 320, dpr = window.devicePixelRatio || 1, L = (c?.levels || 7) + 1;
  cv.width = cv.height = n * dpr;
  const g = cv.getContext('2d'), css = getComputedStyle(cv);
  g.scale(dpr, dpr);
  g.fillStyle = css.getPropertyValue('--pvq-bg') || '#000'; g.fillRect(0, 0, n, n);
  const X = v => (v + L) / (2 * L) * n, Y = v => (L - v) / (2 * L) * n;
  g.strokeStyle = css.getPropertyValue('--pvq-grid') || '#333'; g.lineWidth = 1;
  for (let k = -L + 1; k < L; k += 2) {                 // Entscheidungsgrenzen bei geraden Werten
    if (k === -L + 1) continue;
    g.beginPath(); g.moveTo(X(k - 1), 0); g.lineTo(X(k - 1), n); g.moveTo(0, Y(k - 1)); g.lineTo(n, Y(k - 1)); g.stroke();
  }
  g.fillStyle = css.getPropertyValue('--pvq-dot') || '#4ade80';
  for (const [i, q] of c?.points || []) g.fillRect(X(i) - 1, Y(q) - 1, 2, 2);
}

/** Quellvideo: sichtbare Programme fortlaufend abfragen; der Server antwortet bei unverändertem Bild mit 304 (ETag) */
async function txFrames() {
  if (txLoop) return;
  txLoop = true;
  const etag = new Map();
  try {
    while (document.querySelector('[data-preview]')) {
      const t0 = performance.now();
      if (txRunning() && !document.hidden) {
        const keys = new Set([...document.querySelectorAll('[data-preview]')].map(el => el.dataset.preview).filter(k => txAvail.has(k)));
        await Promise.all([...keys].map(async key => {
          try {
            const res = await fetch(`/api/preview/${encodeURIComponent(key)}.jpg`, { cache: 'no-cache', headers: etag.has(key) ? { 'If-None-Match': etag.get(key) } : {} });
            if (res.status === 304 || !res.ok) return;
            etag.set(key, res.headers.get('ETag'));
            const old = cache.tx.get(key);
            cache.tx.set(key, { t: res.headers.get('ETag'), url: URL.createObjectURL(await res.blob()), age: 0 });
            if (old) setTimeout(() => URL.revokeObjectURL(old.url), 1000);
            document.querySelectorAll(`[data-preview="${CSS.escape(key)}"] .pv-img[data-kind="tx"] img`).forEach(img => { img.src = cache.tx.get(key).url; img.hidden = false; });
          } catch { /* nächstes Bild */ }
        }));
      }
      await new Promise(ok => setTimeout(ok, Math.max(10, TX_MS - (performance.now() - t0))));
    }
  } finally { txLoop = false; }
}

function ensureTimer() {
  if (!timer) timer = setInterval(poll, 1000);
  setTimeout(poll, 0);
  setTimeout(txFrames, 0);
}

function pane(kind, key, name, big) {
  return h('div.pv-img', { dataset: { kind }, title: big ? null : T('Vergrößern'), onclick: big ? null : () => previewBig(key, name) },
    h('span.pv-label', null, kind === 'rx' ? T('Empfang (Pluto-RX)') : T('Quelle')),
    h('img', { alt: `${name || key} – ${kind === 'rx' ? 'RX' : T('Quelle')}`, hidden: true }), h('span.pv-state.muted'),
    big ? null : h('span.pv-zoom', null, icon('scan')));
}

/** Vorschau eines Programms (Schlüssel = dvb.programs[].key): Quelle und Empfang nebeneinander; Klick öffnet sie groß */
export function previewTile(key, name, o = {}) {
  ensureTimer();
  const el = h('figure.pv' + (o.small ? '.sm' : '') + (o.big ? '.big' : ''), { dataset: { preview: key }, tid: o.tid || `preview-${key}` },
    h('div.pv-pair', null, pane('tx', key, name, o.big), pane('rx', key, name, o.big)),
    name ? h('figcaption', null, name) : null);
  queueMicrotask(paint);
  return el;
}

/** Großansicht im Dialog; läuft über data-preview mit dem 1-s-Takt weiter */
function previewBig(key, name) {
  const dlg = h('dialog.modal.pv-dlg', { tid: `preview-big-${key}` },
    h('form', { method: 'dialog' },
      h('h3', null, icon('tv'), name || key, h('span.grow'),
        h('button.btn.ghost', { value: 'close', tid: `preview-big-close-${key}`, title: T('Schließen') }, icon('x'))),
      previewTile(key, null, { big: true, tid: `preview-big-img-${key}` }),
      h('div.pvq', { dataset: { quality: '1' }, hidden: true, tid: `preview-quality-${key}` },
        h('figure', null, h('canvas', { width: 320, height: 320 }), h('figcaption', null, T('Konstellation (entzerrte Datenträger)'))),
        h('div.pvq-info'))));
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
  dlg.showModal();
  paint();
  paintQuality();
}

/** Leiste mit allen TV-Programmen der laufenden Konfiguration */
export function previewStrip(cfg, o = {}) {
  const tv = (cfg?.dvb?.programs || []).filter(p => p.type === 'tv');
  if (!tv.length) return null;
  return h('div.pv-strip' + (o.small ? '.sm' : ''), { tid: o.tid || 'preview-strip' }, tv.map(p => previewTile(p.key, p.name, { small: o.small })));
}

export const previewHint = () => T('Links das gesendete Video (aus dem fertigen Transportstrom, etwa 12 Bilder/s), rechts das über den RX-Pfad des Pluto empfangene und dekodierte Bild (etwa alle 30–50 s). Klick vergrößert.');

/** Momentaufnahme für das Messprotokoll: je TV-Programm Quell- und RX-Bild als data-URL, dazu die Empfangsqualität.
 *  Startet keinen RX-Mitschnitt (ohne ?rx=1), nimmt nur, was gerade vorliegt. */
export async function previewSnapshot(cfg) {
  if (!txRunning()) return null;
  const r = await api('GET', '/api/preview').catch(() => null);
  if (!r) return null;
  const asData = async url => {
    const res = await fetch(url, { cache: 'no-store' }).catch(() => null);
    if (!res?.ok) return null;
    const b = await res.blob();
    return new Promise(ok => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = () => ok(null); fr.readAsDataURL(b); });
  };
  const has = (items, k) => (items || []).some(it => it.key === k);
  const programs = [];
  for (const p of (cfg?.dvb?.programs || []).filter(x => x.type === 'tv')) {
    programs.push({ key: p.key, name: p.name,
      tx: has(r.items, p.key) ? await asData(`/api/preview/${encodeURIComponent(p.key)}.jpg`) : null,
      rx: has(r.rx?.items, p.key) ? await asData(`/api/preview/rx/${encodeURIComponent(p.key)}.jpg`) : null,
      rx_age_s: (r.rx?.items || []).find(it => it.key === p.key)?.age_s ?? null });
  }
  return { time: new Date().toISOString(), programs, quality: r.rx?.quality || null };
}

S.previewPaint = paint;
