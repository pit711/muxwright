// Teletext (DVB: EN 300 472 im Transportstrom, PAL: Austastzeilen). Seiten im Dashboard (Text mit {farbe}-Markierungen),
// aus einem Ordner (.tti/.txt) und automatische Seite 100 – Erzeugung in runner/teletext.py.

import { h, icon } from './ui.js';
import * as STD from './standards.js';
import { S, field, input, textarea, select, toggle, card, edited } from './core.js';
import { T, LANG } from './i18n.js';

/** Markierungen in der Oberflächensprache (der Runner versteht deutsche und englische Namen) */
const TAGS_UI = LANG === 'de'
  ? { colors: '{rot} {grün} {gelb} {blau} {magenta} {cyan} {weiß}', dh: '{doppelt}', bg: '{bg}', nobg: '{nobg}', flash: '{blink}' }
  : { colors: '{red} {green} {yellow} {blue} {magenta} {cyan} {white}', dh: '{double}', bg: '{bg}', nobg: '{nobg}', flash: '{flash}' };

/** Markierungen wie runner/teletext.py TAGS (je Markierung eine Zeichenzelle) */
const COLORS = { red: 1, green: 2, yellow: 3, blue: 4, magenta: 5, cyan: 6, white: 7, rot: 1, gruen: 2, 'grün': 2, gelb: 3, blau: 4, weiss: 7, 'weiß': 7 };
const OTHER = ['flash', 'blink', 'steady', 'normal', 'double', 'dh', 'doppelt', 'black-bg', 'nobg', 'new-bg', 'bg', 'hold', 'release'];
const TAG_RE = /\{([a-zA-Zäöüß-]+)\}/g;
const isTag = t => t.toLowerCase() in COLORS || OTHER.includes(t.toLowerCase());
const CSS = ['#000', '#f00', '#0f0', '#ff0', '#00f', '#f0f', '#0ff', '#fff'];

export const visibleLen = s => String(s || '').replace(TAG_RE, (m, t) => (isTag(t) ? '#' : m)).length;

/** Zeile -> Zellen [{ch, fg, bg, dh}] wie ein Level-1-Decoder (Attribute wirken ab der nächsten Zelle) */
function cells(line) {
  const out = []; let fg = 7, bg = 0, dh = false, last = 0;
  const push = (ch, ctrl) => { out.push({ ch: ctrl ? ' ' : ch, fg, bg, dh }); };
  for (const m of String(line || '').matchAll(TAG_RE)) {
    for (const ch of line.slice(last, m.index)) push(ch);
    last = m.index + m[0].length;
    const t = m[1].toLowerCase();
    if (!isTag(t)) { for (const ch of m[0]) push(ch); continue; }
    if (t === 'new-bg' || t === 'bg') { bg = fg; push(' ', true); continue; }
    if (t === 'black-bg' || t === 'nobg') { bg = 0; push(' ', true); continue; }
    if (['double', 'dh', 'doppelt'].includes(t)) { push(' ', true); dh = true; continue; }
    if (t === 'normal') { dh = false; push(' ', true); continue; }
    push(' ', true);
    if (t in COLORS) fg = COLORS[t];
  }
  for (const ch of String(line || '').slice(last)) push(ch);
  return out.slice(0, 40);
}

/** Seitenvorschau (40×24): Kopfzeile, Zeilen 1–23, Zeile 24 Fastext */
export function pagePreview(pg, tt) {
  const now = new Date();
  const hdr = `P${pg.page || '???'}  ${String(tt.header || 'Muxwright').slice(0, 9).padEnd(9)} ${pg.page || ''} ${now.toLocaleDateString('de-DE', { weekday: 'short' }).slice(0, 2)} ${now.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })} ${now.toLocaleTimeString('de-DE')}`;
  const rows = [cells(hdr)];
  const lines = pg.lines || [];
  let skip = false;
  for (let r = 0; r < 23; r++) {
    if (skip) { skip = false; rows.push(rows[rows.length - 1].map(c => ({ ...c, lower: true }))); continue; }
    const c = cells(lines[r] || '');
    rows.push(c);
    if (c.some(x => x.dh)) skip = true;
  }
  const lk = pg.links || [];
  rows.push(lk.some(Boolean) ? [
    ...cells(`{red}${(lk[0] || '').padEnd(8)}{green}${(lk[1] || '').padEnd(8)}{yellow}${(lk[2] || '').padEnd(8)}{cyan}${(lk[3] || '').padEnd(8)}`)] : []);
  return h('div.ttx-screen', { tid: 'ttx-preview' }, rows.map(row => {
    const full = row.concat(Array.from({ length: 40 - row.length }, () => ({ ch: ' ', fg: 7, bg: 0 })));
    return h('div.ttx-row', null, full.map(c => h('span', { style: { color: CSS[c.fg], background: CSS[c.bg] }, class: c.dh ? (c.lower ? 'dhl' : 'dh') : null }, c.ch)));
  }));
}

function base() { return STD.standardOf(S.draft) === 'pal' ? 'pal.teletext' : 'dvb.teletext'; }

export function pageTeletext() {
  const b = base(), tt = b === 'pal.teletext' ? S.draft.pal.teletext : S.draft.dvb.teletext;
  const std = STD.standardOf(S.draft);
  const off = STD.teletextBlocked(S.draft);
  S.ttxSel = Math.min(S.ttxSel ?? 0, Math.max(0, tt.pages.length - 1));
  const head = card(T('Teletext'), 'text', [
    off ? h('p.alert', null, icon('alert'), off) : null,
    h('div.grid3', null,
      field(T('Senden'), toggle(`${b}.enable`, T('Teletext einschalten'), { tid: 'ttx-enable', rerender: true }),
        { hint: std === 'pal' ? T('Austastzeilen 7–22 und 320–335 (6,9375 Mbit/s)') : T('EN 300 472, ≈ {k} kbit/s im Transportstrom', { k: Math.round(STD.teletextBps(S.draft) / 1000) }) }),
      field(T('Kopfzeile'), input(`${b}.header`, { tid: 'ttx-header', maxlength: 9 }), { path: `${b}.header`, hint: T('bis 9 Zeichen, dahinter Seite, Datum, Uhrzeit') }),
      field(T('Startseite'), input(`${b}.initial_page`, { tid: 'ttx-initial', mono: true, maxlength: 3 }), { path: `${b}.initial_page`, hint: T('erscheint bei Taste TEXT') })),
    h('div.grid3', null,
      field(T('Zeichensatz'), select(`${b}.charset`, [{ v: 'de', label: T('Deutsch (ÄÖÜäöüß)') }, { v: 'en', label: T('Englisch') }], { tid: 'ttx-charset' })),
      field(T('Seite 100'), toggle(`${b}.auto_index`, T('automatisch (Programme, Seitenliste)'), { tid: 'ttx-autoindex' }), { hint: T('nur, wenn keine eigene Seite 100 existiert') }),
      field(T('Seitenordner'), input(`${b}.folder`, { tid: 'ttx-folder', mono: true, ph: 'C:\\Teletext' }),
        { path: `${b}.folder`, hint: T('.tti- und .txt-Dateien (101.txt …), alle 2 s neu eingelesen') })),
    b === 'dvb.teletext' ? programsField(tt) : null,
    std === 'pal' && !S.draft.pal.live ? h('p.hint', null, icon('info'), T('Standbild-Schleife (160 ms): nur so viele Seiten, wie in 128 Zeilen passen (etwa 5), Uhr steht. Im Live-Betrieb laufen alle Seiten und die Uhr.')) : null,
  ], { tid: 'card-ttx' });
  return h('div.page-stack', null, head, pagesCard(tt, b));
}

function programsField(tt) {
  const tv = S.draft.dvb.programs.filter(p => p.type === 'tv');
  const all = tt.programs === 'all' || !Array.isArray(tt.programs);
  const sel = new Set(all ? tv.map(p => p.key) : tt.programs);
  return field(T('In Programmen'), h('div.inrow.wrap', { tid: 'ttx-programs' }, tv.map(p => {
    const cb = h('input', { type: 'checkbox', tid: `ttx-prg-${p.key}` }); cb.checked = sel.has(p.key);
    cb.addEventListener('change', () => {
      if (cb.checked) sel.add(p.key); else sel.delete(p.key);
      tt.programs = sel.size === tv.length ? 'all' : tv.map(x => x.key).filter(k => sel.has(k));
      edited(`${base()}.programs`);
    });
    return h('label.inline', null, cb, ' ', p.name);
  })), { path: `${base()}.programs`, hint: T('ein Teletext-Strom, in den PMTs aller gewählten Programme') });
}

function pagesCard(tt, b) {
  const pages = tt.pages;
  const list = h('div.ttx-list', { tid: 'ttx-pages' }, pages.map((p, i) => h('button.btn.sm', { type: 'button', class: i === S.ttxSel ? 'accent' : null, tid: `ttx-page-${i}`, onclick: () => { S.ttxSel = i; S.render(); } },
    h('b.mono', null, p.page || '???'), ' ', p.title || '')),
  h('button.btn.sm', { type: 'button', tid: 'ttx-add', onclick: () => {
    let n = 101; const used = new Set(pages.map(p => String(p.page))); while (used.has(String(n)) && n < 899) n++;
    pages.push({ page: String(n), title: T('Neue Seite'), lines: ['{yellow}' + T('Neue Seite'), ''], links: [] });
    S.ttxSel = pages.length - 1; edited(`${b}.pages`, true);
  } }, icon('plus'), T('Seite')));
  const pg = pages[S.ttxSel];
  if (!pg) return card(T('Seiten'), 'file', [list, h('p.muted', null, T('Keine eigenen Seiten – Seite 100 wird automatisch erzeugt, weitere Seiten aus dem Ordner.'))], { tid: 'card-ttx-pages' });
  const pb = `${b}.pages.${S.ttxSel}`;
  pg.links ||= [];
  const prev = h('div', { tid: 'ttx-preview-wrap' }, pagePreview(pg, tt));
  S.watchers.push(() => prev.replaceChildren(pagePreview(pg, tt)));
  const over = (pg.lines || []).map((l, i) => [i + 1, visibleLen(l)]).filter(([, n]) => n > 40);
  const ta = textarea(`${pb}.lines`, { tid: 'ttx-lines', rows: 23, fmt: v => (v || []).join('\n'), parse: s => s.split('\n').slice(0, 23) });
  ta.classList.add('mono', 'ttx-edit');
  ta.setAttribute('cols', '46');
  const links = h('div.inrow.wrap', null, [T('rot'), T('grün'), T('gelb'), T('cyan')].map((l, i) => {
    const el = h('input.mono', { tid: `ttx-link-${i}`, maxLength: 3, placeholder: l, size: 4 });
    el.value = pg.links[i] || '';
    el.addEventListener('input', () => { pg.links[i] = el.value.trim(); pg.links = pg.links.slice(0, 4); edited(`${pb}.links`); });
    return h('label.inline', null, h('i.dot', { style: { background: ['#e33', '#2b2', '#dd2', '#2cc'][i] } }), ' ', el);
  }));
  return card(T('Seiten'), 'file', [list,
    h('div.ttx-layout', null,
      h('div.ttx-form', null,
        h('div.grid2', null,
          field(T('Seite'), input(`${pb}.page`, { tid: 'ttx-num', mono: true, maxlength: 3, rerender: true }), { path: `${pb}.page`, hint: '100–899' }),
          field(T('Titel'), input(`${pb}.title`, { tid: 'ttx-title', maxlength: 34 }), { hint: T('für das Verzeichnis auf Seite 100') })),
        field(T('Zeilen 1–23 (je 40 Zeichen)'), ta, { path: `${pb}.lines`, wide: true,
          hint: over.length ? T('Zeile {n} ist zu lang ({k} Zeichen)', { n: over[0][0], k: over[0][1] })
            : T('Farben: {colors} · {dh} doppelte Höhe · {bg} Hintergrund = aktuelle Farbe, {nobg} schwarz · {flash}. Jede Markierung belegt ein Zeichen.', TAGS_UI) }),
        field(T('Fastext-Links'), links, { hint: T('Farbtasten der Fernbedienung; Beschriftung in Zeile 24') }),
        h('button.btn.ghost.danger-text', { type: 'button', tid: 'ttx-del', onclick: () => { pages.splice(S.ttxSel, 1); S.ttxSel = Math.max(0, S.ttxSel - 1); edited(`${b}.pages`, true); } }, icon('trash'), T('Seite löschen'))),
      prev)], { tid: 'card-ttx-pages' });
}
