// Analog-TV PAL (SPEC `mode: "pal"`): Bereich „Bild & Ton“.
// Schema: pal{system, fs, live_fs, live, offset_hz, level, image{kind,pattern,file,url,text,text2,timestamp}, sound{kind,freq_hz,deviation_hz,level_db,preemphasis_us,tone_level,file}}
// rf.freq_hz = Bildträger (der Server stellt den LO auf rf.lo_hz bzw. lo_hz_live).

import { h, icon, toast } from './ui.js';
import * as D from './dab.js';
import * as STD from './standards.js';
import { S, field, input, select, toggle, segmented, card, counter, edited, previewCache, sendLive, txRunning } from './core.js';
import { uploadBtn } from './pages.js';
import { youtubeField } from './dvb.js';
import { slideSrc } from './report.js';
import { T, LANG } from './i18n.js';

const fmtMHz = hz => D.fmtMHz(hz);

/** Vorschau (SVG) des Testbilds mit Text 1/2 und Uhr */
export function palPreview(p) {
  const ns = 'http://www.w3.org/2000/svg';
  const el = (t, a, ...ch) => { const e = document.createElementNS(ns, t); for (const [k, v] of Object.entries(a || {})) e.setAttribute(k, v); e.append(...ch); return e; };
  const svg = el('svg', { viewBox: '0 0 768 576', class: 'palprev', role: 'img', 'aria-label': T('Testbild-Vorschau') });
  const im = p.image;
  if (im.kind === 'file' && im.file) svg.append(el('image', { href: previewCache.get(im.file) || slideSrc(im.file), x: 0, y: 0, width: 768, height: 576, preserveAspectRatio: 'xMidYMid slice' }));
  else if (im.kind !== 'testpattern') svg.append(el('rect', { x: 0, y: 0, width: 768, height: 576, fill: '#1e293b' }), el('text', { x: 384, y: 300, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': 30, 'font-family': 'sans-serif' }, im.kind === 'youtube' ? T('YouTube-Video') : im.kind === 'video' ? T('Videodatei') : T('Bild')));
  else {
    const pat = im.pattern;
    if (pat === 'grey') for (let i = 0; i < 8; i++) { const v = Math.round(i * 255 / 7); svg.append(el('rect', { x: i * 96, y: 0, width: 96, height: 576, fill: `rgb(${v},${v},${v})` })); }
    else if (pat === 'black' || pat === 'white') svg.append(el('rect', { x: 0, y: 0, width: 768, height: 576, fill: pat === 'black' ? '#000' : '#fff' }));
    else if (pat === 'testsrc2' || pat === 'rgbtestsrc') { svg.append(el('rect', { x: 0, y: 0, width: 768, height: 576, fill: '#333' })); ['#f00', '#0f0', '#00f', '#ff0', '#0ff', '#f0f'].forEach((c, i) => svg.append(el('rect', { x: 64 + i * 108, y: 160, width: 96, height: 256, fill: c }))); }
    else {
      const a = pat === 'pal100bars' ? 255 : 191;
      const bars = [[a, a, a], [a, a, 0], [0, a, a], [0, a, 0], [a, 0, a], [a, 0, 0], [0, 0, a], [0, 0, 0]];
      if (pat === 'pal100bars') bars[0] = [255, 255, 255];
      bars.forEach((c, i) => svg.append(el('rect', { x: i * 96, y: 0, width: 96, height: pat === 'smptebars' ? 384 : 576, fill: `rgb(${c.join(',')})` })));
      if (pat === 'smptebars') svg.append(el('rect', { x: 0, y: 384, width: 768, height: 192, fill: '#101030' }));
    }
  }
  const band = (y, t) => t ? [el('rect', { x: 0, y: y - 34, width: 768, height: 48, fill: '#000', 'fill-opacity': 0.75 }), el('text', { x: 384, y: y, 'text-anchor': 'middle', fill: '#fff', 'font-size': 30, 'font-family': 'monospace', 'font-weight': 700 }, t)] : [];
  svg.append(...band(64, im.text), ...band(546, im.text2 || defaultText2()));
  if (im.timestamp) svg.append(el('text', { x: 744, y: 120, 'text-anchor': 'end', fill: '#fff', 'font-size': 22, 'font-family': 'monospace', stroke: '#000', 'stroke-width': 3, 'paint-order': 'stroke' }, new Date().toLocaleTimeString(LANG === 'en' ? 'en-GB' : LANG)));
  return svg;
}
function defaultText2() { const rf = S.draft.rf; return `PAL ${S.draft.pal.system === 'BG' ? 'B/G' : S.draft.pal.system} ${rf.channel === 'custom' ? fmtMHz(rf.freq_hz) + ' MHz' : rf.channel}`; }

export function pagePAL() {
  const p = S.draft.pal, im = p.image, snd = p.sound;
  const prevBox = h('div.palprev-wrap', { tid: 'pal-preview' }, palPreview(p));
  const updPrev = () => prevBox.replaceChildren(palPreview(p));
  S.watchers.push(updPrev);
  const sys = STD.PAL_SYSTEMS[p.system] || STD.PAL_SYSTEMS.BG;
  const car = STD.palCarriers(S.draft);
  const kinds = [['testpattern', T('Testbild'), 'tv'], ['file', T('Bild'), 'image'], ['video', T('Video'), 'file'], ['youtube', 'YouTube', 'download']];
  const params = [];
  if (im.kind === 'testpattern') params.push(field(T('Muster'), select('pal.image.pattern', Object.entries(STD.PAL_PATTERNS).map(([k, l]) => ({ v: k, label: l })), { tid: 'pal-pattern', rerender: false, onchange: updPrev })));
  if (im.kind === 'file' || im.kind === 'video') {
    const isImg = im.kind === 'file';
    const list = S.media.filter(m => m.kind === (isImg ? 'image' : 'video'));
    const fsel = h('select', { tid: isImg ? 'pal-image' : 'pal-file' }, h('option', { value: '' }, list.length ? T('Datei wählen …') : T('Keine Dateien – hochladen')), list.map(m => h('option', { value: m.path }, m.name)));
    fsel.value = im.file || ''; if (im.file && fsel.selectedIndex < 0) { fsel.append(h('option', { value: im.file }, im.file)); fsel.value = im.file; }
    fsel.addEventListener('change', () => { im.file = fsel.value; edited('pal.image.file'); updPrev(); });
    params.push(field(isImg ? T('Bild (wird auf 720×576 skaliert)') : T('Videodatei'), h('div.inrow', null, fsel,
      uploadBtn(isImg ? 'slide' : 'video', isImg ? 'image/png,image/jpeg' : 'video/*,.ts,.mkv,.mpg', isImg ? 'pal-image-upload' : 'pal-upload', async path => { im.file = path; edited('pal.image.file', true); })), { path: 'pal.image.file', wide: true }));
  }
  if (im.kind === 'youtube') params.push(youtubeField(im, 'pal.image'));
  const serverLive = S.state.rf?.pal_live ?? null;
  const moving = ['video', 'youtube'].includes(im.kind) || im.timestamp;
  const loopHint = moving && !p.live
    ? h('p.alert', { tid: 'pal-loop-hint' }, icon('info'), T('Analog-Loop 160 ms: Im Schleifenbetrieb (8 MS/s, zyklischer Puffer) laufen nur 4 Vollbilder in Dauerschleife – kein Bewegtbild, Uhr/Bildzähler stehen. Für bewegtes Bild „Live (7 MS/s)“ einschalten.'))
    : p.live ? h('p.hint', { tid: 'pal-live-hint' }, icon('info'), T('Live: bewegtes Bild + Ton mit {msps} MS/s per USB (1–3 Encoder-Prozesse, ab 2 Prozessen Echtzeit).', { msps: D.fmtNum(p.live_fs / 1e6, 3) }) + (serverLive === false ? ' ' + T('Der Server meldet derzeit keinen Live-Betrieb.') : '')) : null;

  const video = card(T('Bild'), 'tv', [
    h('div.grid2.palgrid', null,
      prevBox,
      h('div', null,
        h('div.srcgrid', { tid: 'pal-kind' }, kinds.map(([k, l, ic]) => h('button', { type: 'button', class: im.kind === k ? 'on' : null, tid: `pal-kind-${k}`, onclick: () => { im.kind = k; edited('pal.image.kind', true); } }, icon(ic), h('span', null, l)))),
        params.length ? h('div.formgrid.mt', null, params) : null)),
    loopHint,
    h('div.grid2', null,
      field(T('Text 1 (oben)'), input('pal.image.text', { tid: 'pal-text-top', maxlength: 40, oninput: updPrev }), { path: 'pal.image.text', extra: counter(() => [...(im.text || '')].length, 40), hint: 'live' }),
      field(T('Text 2 (unten)'), input('pal.image.text2', { tid: 'pal-text-bottom', maxlength: 40, ph: defaultText2(), oninput: updPrev }), { path: 'pal.image.text2', extra: counter(() => [...(im.text2 || '')].length, 40), hint: T('leer = Norm und Kanal · live') })),
    h('div.inrow.wrap', null,
      toggle('pal.image.timestamp', T('Uhr + Bildzähler einblenden'), { tid: 'pal-timestamp', rerender: true }), h('span.grow'),
      h('button.btn.primary.sm', { type: 'button', tid: 'pal-text-send', onclick: async () => {
        if (!txRunning()) return toast(T('Kette läuft nicht – Text wird beim Start übernommen.'), 'info');
        if (await sendLive({ pal: { text: im.text, text2: im.text2 } }, T('Bildtext gesendet'))) { if (S.server) Object.assign(S.server.pal.image, { text: im.text, text2: im.text2 }); edited('pal.image'); }
      } }, icon('send'), T('Text live senden'))),
  ], { tid: 'card-pal-video' });

  const audio = card(T('Ton (FM-Tonträger)'), 'music', [
    field(T('Tonquelle'), segmented('pal.sound.kind', [{ v: 'tone', label: T('Ton') }, { v: 'file', label: T('Datei') }, { v: 'source', label: T('aus Video') }, { v: 'silence', label: T('Stille') }, { v: 'off', label: T('aus') }], { tid: 'pal-audio-kind' }),
      { hint: T('Stille = Tonträger ohne Modulation, aus = kein Tonträger; Datei/Video im Schleifenbetrieb nur ein Ausschnitt') }),
    snd.kind === 'tone' ? h('div.grid2', null,
      field(T('Frequenz (Hz)'), input('pal.sound.freq_hz', { type: 'number', min: 20, max: 15000, tid: 'pal-tone-freq', mono: true }), { hint: T('Schleife: auf Vielfaches von 6,25 Hz gerundet') }),
      field(T('Tonpegel (0–1)'), input('pal.sound.tone_level', { type: 'number', min: 0, max: 1, step: 0.05, tid: 'pal-tone-level', mono: true }))) : null,
    snd.kind === 'file' ? (() => {
      const auds = S.media.filter(m => m.kind === 'audio');
      const fsel = h('select', { tid: 'pal-audio-file' }, h('option', { value: '' }, T('Datei wählen …')), auds.map(m => h('option', { value: m.path }, m.name)));
      fsel.value = snd.file || ''; fsel.addEventListener('change', () => { snd.file = fsel.value; edited('pal.sound.file'); });
      return field(T('Tondatei'), fsel, { path: 'pal.sound.file' });
    })() : null,
    h('div.grid3', null,
      field(T('Ton-Hub (kHz)'), input('pal.sound.deviation_hz', { type: 'number', min: 1, max: 75, step: 1, tid: 'pal-audio-dev', mono: true, fmt: v => v == null ? '' : v / 1000, parse: v => Math.round(Number(v) * 1000) }), { path: 'pal.sound.deviation_hz', hint: T('Spitze ±50 kHz') }),
      field(T('Ton/Bild (dB)'), input('pal.sound.level_db', { type: 'number', min: -30, max: -3, step: 0.5, tid: 'pal-sound-level', mono: true }), { path: 'pal.sound.level_db', hint: T('bezogen auf die Synchronspitze; B/G −13 dB') }),
      field('Preemphasis', segmented('pal.sound.preemphasis_us', [{ v: 50, label: '50 µs' }, { v: 0, label: T('aus') }], { tid: 'pal-audio-preemph' }))),
  ], { tid: 'card-pal-audio' });

  const offAuto = p.offset_hz === 'auto';
  const system = card(T('System, Träger & Betrieb'), 'antenna', [
    field(T('Norm'), segmented('pal.system', Object.entries(STD.PAL_SYSTEMS).map(([k, v]) => ({ v: k, label: k === 'BG' ? 'B/G' : k === 'DK' ? 'D/K' : k, title: v.name })), { tid: 'pal-system' }),
      { path: 'pal.system', hint: T('{name}: Ton +{snd} MHz, Restseitenband {vsb} MHz, {lines} Zeilen', { name: sys.name, snd: D.fmtNum(sys.sound_mhz, 1), vsb: D.fmtNum(sys.vsb_mhz, 2), lines: sys.lines }) }),
    h('div.rx-grid', { tid: 'pal-system-carriers' },
      h('div.rx-kv', null, h('small', null, T('Bildträger')), h('b', null, `${fmtMHz(car.vision)} MHz`)),
      h('div.rx-kv', null, h('small', null, T('Tonträger')), h('b', null, `${fmtMHz(car.sound)} MHz`)),
      h('div.rx-kv', null, h('small', null, T('Farbträger')), h('b', null, `${fmtMHz(car.vision + 4433618.75)} MHz`)),
      h('div.rx-kv', null, h('small', null, 'Pluto-LO'), h('b', null, `${fmtMHz(car.vision + (offAuto ? STD.palAutoOffset(p) : Number(p.offset_hz)))} MHz`))),
    h('div.grid2', null,
      field(T('Betrieb'), segmented('pal.live', [{ v: false, label: T('Schleife 160 ms (8 MS/s)') }, { v: true, label: T('Live (7 MS/s)') }], { tid: 'pal-live' }), { hint: T('Schleife: zyklischer Puffer auf dem Pluto; live: bewegtes Bild per USB') }),
      field(T('Schleifen-Abtastrate'), segmented('pal.fs', [{ v: 8000000, label: '8 MS/s' }, { v: 7000000, label: '7 MS/s' }], { tid: 'pal-fs' }), { path: 'pal.fs' })),
    h('div.grid2', null,
      field(T('Basisband-Versatz'), h('div.inrow', null,
        (() => { const cb = h('input', { type: 'checkbox', role: 'switch', tid: 'pal-offset-auto' }); cb.checked = offAuto;
          cb.addEventListener('change', () => { p.offset_hz = cb.checked ? 'auto' : STD.palAutoOffset(p); edited('pal.offset_hz', true); });
          return h('label.switch', null, cb, h('span.track', null, h('i')), h('span.sw-label', null, 'auto')); })(),
        offAuto ? h('span.mono.muted', null, `${D.fmtNum(STD.palAutoOffset(p) / 1e6, 3)} MHz`) : input('pal.offset_hz', { type: 'number', min: 1000000, max: 3500000, step: 25, tid: 'pal-offset', mono: true })),
        { path: 'pal.offset_hz', hint: T('Bildträger bei −Versatz im Basisband; Vielfaches von 25 Hz') }),
      field(T('Pegel (Spitze)'), input('pal.level', { type: 'number', min: 0.1, max: 1, step: 0.05, tid: 'pal-level', mono: true }), { path: 'pal.level', hint: T('relativ zur Vollaussteuerung (Synchronboden + Ton); Standard 0,9') })),
    h('p.hint', null, icon('info'), T('Negative AM mit Restseitenband, PAL-Farbe (Burst ±135°, V-Schalter), FM-Ton. Austast-/Weißwert fest 0,73/0,10 der Synchronspitze. Kanal im Bereich HF; Norm, Betrieb und Bildquelle brauchen „Übernehmen (Neustart)“.')),
  ], { tid: 'card-pal-system' });

  return h('div.page-grid', null, h('div.col', null, video), h('div.col', null, system, audio));
}
