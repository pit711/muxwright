// Darstellung von Prüf-/Kontrollberichten mit Soll/Ist-Vergleich und Protokoll-Export.

import { h, icon, esc, stamp } from './ui.js';
import { CODECS, PTY, LANGUAGES, protLabel, parseHex, normHex, ltoLabel, capacity, fmtMHz, fmtDb, TOTAL_CU } from './dab.js';
import { renderFmReport, renderDvbReport, renderPalReport, protocolStdSection, constellationSVG } from './report_std.js';
import { standardOf, isDvb, stdName, requiredSampleRate } from './standards.js';
import { APP_NAME } from './brand.js';
import { T, LANG } from './i18n.js';

const LOC = LANG === 'en' ? 'en-GB' : LANG;

const CODEC_IST = { 'aac-lc': 'AAC-LC', 'he-aac': 'HE-AAC', 'he-aac-v2': 'HE-AACv2', 'mp2': 'MP2' };
const norm = v => String(v ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');

function langName(v) {
  const n = parseHex(v); if (n == null) return String(v ?? '–');
  const l = LANGUAGES.find(x => parseHex(x.v) === n);
  return l ? `${l.name} (${l.v})` : String(v);
}
function ptyName(v) { const n = Number(v); return Number.isInteger(n) && PTY[n] ? `${n} · ${PTY[n]}` : String(v ?? '–'); }

/** Vergleichsregeln: [Bezeichnung, Sollwert, Istwert, gleich?] */
function cmpRow(name, soll, ist, eq) {
  const missing = ist === undefined || ist === null || ist === '';
  const ok = missing ? null : eq(soll, ist);
  return { name, soll, ist: missing ? '–' : ist, ok };
}
const eqHex = (a, b) => parseHex(a) != null && parseHex(a) === parseHex(b);
const eqLabel = (a, b) => String(a ?? '').trimEnd() === String(b ?? '').trimEnd();
const eqNum = (a, b) => Number(a) === Number(b);
const eqNorm = (a, b) => norm(a) === norm(b);

export function compareEnsemble(cfg, ist) {
  const e = cfg.ensemble; ist = ist || {};
  const rows = [
    cmpRow('EId', e.eid, ist.eid, eqHex),
    cmpRow('ECC', e.ecc, ist.ecc, eqHex),
    cmpRow('Label', e.label, ist.label, eqLabel),
    cmpRow(T('Kurzlabel'), e.shortlabel, ist.shortlabel, eqLabel),
  ];
  if (e.lto !== 'auto' && (ist.lto_halfhours !== undefined || ist.lto !== undefined)) rows.push(cmpRow('LTO', ltoLabel(e.lto), ist.lto_halfhours !== undefined ? ltoLabel(ist.lto_halfhours) : ist.lto, (a, b) => (ist.lto_halfhours !== undefined ? Number(e.lto) === Number(ist.lto_halfhours) : Number(e.lto) === Number(b) * 2) || a === b));
  if (e.datetime) rows.push(cmpRow(T('Datum/Uhrzeit'), T('gesendet'), ist.datetime, (a, b) => !!b));
  return rows;
}

export function compareService(s, ist) {
  ist = ist || {};
  const audio = s.type !== 'data';
  const rows = [
    cmpRow('SId', s.sid, ist.sid, eqHex),
    cmpRow('Label', s.label, ist.label, eqLabel),
    cmpRow(T('Kurzlabel'), s.shortlabel, ist.shortlabel, eqLabel),
    cmpRow('PTy', ptyName(s.pty), ist.pty == null ? null : ptyName(ist.pty), () => Number(s.pty) === Number(ist.pty)),
    cmpRow(T('Sprache'), langName(s.language), ist.language == null ? null : langName(ist.language), () => eqHex(s.language, ist.language) || norm(langName(s.language)).includes(norm(ist.language))),
  ];
  if (audio) {
    rows.push(cmpRow('Codec', CODEC_IST[s.audio.codec], ist.codec, eqNorm));
    rows.push(cmpRow(T('Bitrate'), `${s.audio.bitrate} kbit/s`, ist.bitrate == null ? null : `${ist.bitrate} kbit/s`, () => eqNum(s.audio.bitrate, ist.bitrate)));
    rows.push(cmpRow(T('Abtastrate'), `${s.audio.samplerate / 1000} kHz`, ist.samplerate == null ? null : `${ist.samplerate / 1000} kHz`, () => eqNum(s.audio.samplerate, ist.samplerate)));
    rows.push(cmpRow(T('Kanäle'), s.audio.channels === 2 ? 'Stereo' : 'Mono', ist.channels == null ? null : (ist.channels === 2 ? 'Stereo' : 'Mono'), () => eqNum(s.audio.channels, ist.channels)));
  }
  rows.push(cmpRow(T('Fehlerschutz'), protLabel(s.protection), ist.protection, eqNorm));
  if (audio && s.pad.enable && s.pad.dls.enable) {
    const allowed = [s.pad.dls.text, ...(s.pad.dls.rotate || [])].filter(Boolean);
    rows.push(cmpRow('DLS', s.pad.dls.text, ist.dls, (a, b) => allowed.some(t => eqLabel(t, b))));
  }
  if (audio && s.pad.enable && s.pad.sls.enable) {
    const n = (ist.slides || []).length;
    rows.push(cmpRow('Slideshow', T('{n} Bild(er)', { n: s.pad.sls.slides.length }), ist.slides ? T('{n} empfangen', { n }) : null, () => n > 0));
  }
  return rows;
}

export function slideSrc(f) {
  if (!f) return '';
  if (typeof f === 'string') f = { file: f };
  if (f.data) return f.data.startsWith('data:') ? f.data : `data:image/jpeg;base64,${f.data}`;
  const p = f.url || f.file || '';
  if (/^(https?:|data:|blob:)/.test(p)) return p;
  return new URL(p.startsWith('/') ? p : '/' + p, location.origin).href;
}

function badge(text, kind, tid) { return h('span.badge.' + kind, { tid }, text); }

function rowsTable(rows, tid) {
  return h('table.cmp', { tid },
    h('thead', null, h('tr', null, h('th', null, T('Merkmal')), h('th', null, T('Soll')), h('th', null, T('Ist')), h('th.st', null, ''))),
    h('tbody', null, rows.map(r => h('tr', { class: r.ok === false ? 'bad' : r.ok ? 'good' : 'na' },
      h('td', null, r.name), h('td.mono', null, String(r.soll ?? '–')), h('td.mono', null, String(r.ist)),
      h('td.st', null, r.ok === false ? icon('x') : r.ok ? icon('check') : h('span.muted', null, '–'))))));
}

function levelMeter(db) {
  const v = Math.max(-60, Math.min(0, Number(db)));
  const pct = ((v + 60) / 60) * 100;
  return h('div.meter', { title: `${fmtDb(db)} dBFS` }, h('i', { style: { width: pct + '%' } }), h('span', null, isFinite(db) ? `${Number(db).toFixed(1)} dB` : '–'));
}

/** Erzeugt die Berichtansicht. kind: 'check' | 'monitor' */
/** Eigentlicher Prüfbericht: der Monitor liefert ihn unter `decode`, die Offline-Prüfung flach */
export function decodeOf(report) { return report && typeof report === 'object' && report.decode && typeof report.decode === 'object' ? report.decode : report; }

function fmt1(v, unit = '') { return v == null || isNaN(v) ? '–' : `${Number(v).toLocaleString(LOC, { maximumFractionDigits: 1, minimumFractionDigits: 1 })}${unit}`; }

/** Spektrum als Inline-SVG; f relativ zur Mitte (Hz), DAB-Kanal 1,536 MHz markiert */
export function spectrumSVG(f, p, { width = 640, height = 170 } = {}) {
  const ns = 'http://www.w3.org/2000/svg';
  const el = (t, a, ...ch) => { const e = document.createElementNS(ns, t); for (const [k, v] of Object.entries(a || {})) e.setAttribute(k, v); e.append(...ch); return e; };
  if (!Array.isArray(f) || !Array.isArray(p) || f.length < 4 || f.length !== p.length) return null;
  const L = 44, R = 10, TOP = 10, B = 26;
  const fmin = Math.min(...f), fmax = Math.max(...f);
  const vals = p.filter(Number.isFinite);
  const pmin = Math.floor((Math.min(...vals) - 3) / 10) * 10, pmax = Math.ceil((Math.max(...vals) + 3) / 10) * 10;
  const X = v => L + (v - fmin) / (fmax - fmin) * (width - L - R);
  const Y = v => TOP + (pmax - v) / (pmax - pmin) * (height - TOP - B);
  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'spectrum', role: 'img', 'aria-label': T('Spektrum des Mitschnitts') });
  svg.append(el('rect', { x: X(-768000), y: TOP, width: X(768000) - X(-768000), height: height - TOP - B, class: 'sp-band' }));
  for (let db = pmin; db <= pmax; db += 10) {
    svg.append(el('line', { x1: L, x2: width - R, y1: Y(db), y2: Y(db), class: 'sp-grid' }));
    svg.append(el('text', { x: L - 4, y: Y(db) + 3, class: 'sp-lbl', 'text-anchor': 'end' }, `${db}`));
  }
  for (let mhz = Math.ceil(fmin / 1e6); mhz <= Math.floor(fmax / 1e6); mhz++) {
    svg.append(el('line', { x1: X(mhz * 1e6), x2: X(mhz * 1e6), y1: TOP, y2: height - B, class: 'sp-grid' }));
    svg.append(el('text', { x: X(mhz * 1e6), y: height - B + 13, class: 'sp-lbl', 'text-anchor': 'middle' }, `${mhz > 0 ? '+' : ''}${mhz} MHz`));
  }
  svg.append(el('path', { d: f.map((x, i) => `${i ? 'L' : 'M'}${X(x).toFixed(1)},${Y(Number.isFinite(p[i]) ? p[i] : pmin).toFixed(1)}`).join(' '), class: 'sp-line' }));
  svg.append(el('text', { x: X(0), y: TOP + 11, class: 'sp-lbl sp-band-lbl', 'text-anchor': 'middle' }, 'DAB 1,536 MHz'));
  svg.append(el('text', { x: L - 4, y: TOP + 4, class: 'sp-lbl', 'text-anchor': 'end' }, 'dB'));
  return svg;
}

/** Karte „HF-Mitschnitt“ aus dem äußeren Monitor-Bericht (Pluto-RX) */
export function rxCaptureCard(report) {
  if (!report || report.decode === undefined && !report.spectrum) return null;
  const sp = report.spectrum || {};
  const clip = Number(sp.clip_pct);
  const clipBad = Number.isFinite(clip) && clip > 0.1;
  const delta = Number(sp.delta_db);
  const deltaKind = !Number.isFinite(delta) ? 'na' : delta >= 20 ? 'ok' : delta >= 10 ? 'warn' : 'bad';
  const under = report.tx_underruns_during;
  const kv = (k, v, cls = '') => h('div.rx-kv' + (cls ? '.' + cls : ''), null, h('small', null, k), h('b', null, v));
  const svg = spectrumSVG(sp.psd_f_hz, sp.psd_db);
  return h('section.rep-block.rxcap', { tid: 'report-monitor-rx' },
    h('h4', null, icon('antenna'), T('HF-Mitschnitt (Pluto-RX)'),
      report.channel ? h('span.mono.muted', null, ` ${report.channel}${report.rx_lo_hz ? ' · ' + fmtMHz(report.rx_lo_hz) + ' MHz' : ''}`) : null),
    h('div.rx-grid', null,
      kv(T('RX-Weg'), report.rx_path || '–'),
      kv('RX-Gain', report.rx_gain_db != null ? `${fmt1(report.rx_gain_db)} dB` : '–'),
      kv(T('Dauer'), report.seconds != null ? `${fmt1(report.seconds)} s` : '–'),
      kv(T('Band'), fmt1(sp.in_band_db, ' dB')),
      kv(T('Nachbarkanal'), fmt1(sp.adjacent_db, ' dB')),
      kv(T('Abstand'), fmt1(sp.delta_db, ' dB'), deltaKind),
      kv('RMS', fmt1(sp.rms_dbfs, ' dBFS')),
      kv('Clipping', Number.isFinite(clip) ? `${clip.toLocaleString(LOC, { maximumFractionDigits: 2 })} %` : '–', clipBad ? 'bad' : 'ok'),
      kv(T('Underruns dabei'), under == null ? '–' : String(under), under ? 'bad' : 'ok'),
      report.tx_running === false ? kv(T('Sender'), T('lief nicht'), 'bad') : null),
    clipBad ? h('p.alert', { tid: 'report-monitor-clip' }, icon('alert'), T('Übersteuerung: {pct} % der Abtastwerte am Anschlag – RX-Gain senken.', { pct: clip.toLocaleString(LOC, { maximumFractionDigits: 2 }) })) : null,
    deltaKind === 'bad' ? h('p.alert', null, icon('alert'), T('Geringer Abstand Band/Nachbarkanal – Signal schwach oder Rauschen dominiert.')) : null,
    svg ? h('div.spwrap', { tid: 'report-monitor-spectrum' }, svg) : null,
    report.file ? h('small.mono.muted', null, report.file) : null);
}

export function renderReport(report0, cfg, kind) {
  const report = decodeOf(report0);
  const stdR = standardOf(cfg);
  if (report && stdR === 'fm') return renderFmReport(report0, cfg, kind);
  if (report && isDvb(stdR)) return renderDvbReport(report0, cfg, kind);
  if (report && stdR === 'pal') return renderPalReport(report0, cfg, kind);
  if (!report) return h('div.empty', null, icon('scan', 'big'), h('p', null, kind === 'monitor' ? T('Noch kein Kontrollempfang durchgeführt.') : T('Noch keine Offline-Prüfung durchgeführt.')));
  const ens = compareEnsemble(cfg, report.ensemble);
  const svcBySid = new Map((report.services || []).map(s => [parseHex(s.sid), s]));
  const svcRows = cfg.services.map(s => ({ s, ist: svcBySid.get(parseHex(s.sid)) }));
  const extra = (report.services || []).filter(r => !cfg.services.some(s => parseHex(s.sid) === parseHex(r.sid)));
  const allRows = [...ens, ...svcRows.flatMap(x => x.ist ? compareService(x.s, x.ist) : [])];
  const bad = allRows.filter(r => r.ok === false).length + svcRows.filter(x => !x.ist).length + extra.length;
  const snr = report.snr_db;
  const snrKind = snr == null ? 'na' : snr >= 15 ? 'ok' : snr >= 8 ? 'warn' : 'bad';
  const errSum = (report.services || []).reduce((a, s) => a + Object.values(s.errors || {}).reduce((x, y) => x + (typeof y === 'number' ? y : 0), 0), 0);

  return h('div.report', { tid: `report-${kind}` },
    h('div.report-head', null,
      h('div.verdict.' + (report.ok && report.sync && bad === 0 ? 'ok' : 'bad'), { tid: `report-${kind}-verdict` },
        icon(report.ok && report.sync && bad === 0 ? 'check' : 'alert'),
        h('div', null,
          h('strong', null, !report.sync ? T('Kein Sync') : bad === 0 ? T('Soll = Ist') : (bad === 1 ? T('{n} Abweichung', { n: bad }) : T('{n} Abweichungen', { n: bad }))),
          h('small', null, (report.time ?? report.t ?? report0.t) ? stamp(new Date((report.time ?? report.t ?? report0.t) * ((report.time ?? report.t ?? report0.t) < 1e12 ? 1000 : 1))) : (kind === 'monitor' ? T('Kontrollempfang') : T('Offline-Prüfung'))))),
      h('div.kpis', null,
        kpi('Sync', report.sync ? T('ja') : T('nein'), report.sync ? 'ok' : 'bad'),
        kpi('SNR', snr == null ? '–' : `${Number(snr).toFixed(1)} dB`, snrKind),
        kpi(T('Frequenzkorr.'), report.freq_corr_hz == null ? '–' : `${Math.round(report.freq_corr_hz)} Hz`, 'na'),
        kpi(T('Dienste'), `${(report.services || []).length}/${cfg.services.length}`, (report.services || []).length === cfg.services.length ? 'ok' : 'bad'),
        kpi(T('Fehler'), String(errSum), errSum ? 'bad' : 'ok'),
        report.fic_crc_errors != null ? kpi('FIC-CRC', String(report.fic_crc_errors), report.fic_crc_errors ? 'warn' : 'ok') : null)),
    kind === 'monitor' ? rxCaptureCard(report0) : null,
    report.error || report.stderr ? h('p.alert', null, icon('alert'), String(report.error || ''), report.stderr ? h('code.mono.small', null, ' ' + String(report.stderr).slice(-300)) : null) : null,
    h('section.rep-block', null, h('h4', null, icon('antenna'), 'Ensemble'), rowsTable(ens, `report-${kind}-ensemble`)),
    h('div.rep-services', null, svcRows.map(({ s, ist }) => h('section.rep-block.svc' + (ist ? '' : '.missing'), { tid: `report-${kind}-svc-${s.key}` },
      h('h4', null, icon('radio'), s.label, h('span.mono.muted', null, ' ', s.sid),
        !ist ? badge(T('fehlt im Empfang'), 'bad') : null),
      ist ? [
        rowsTable(compareService(s, ist)),
        h('div.rep-extra', null,
          ist.audio_level_db ? h('div.levels', null, h('label', null, T('Pegel L/R')), ...ist.audio_level_db.map(levelMeter)) : null,
          ist.errors ? h('div.errs', null, Object.entries(ist.errors).filter(([, v]) => typeof v === 'number').map(([k, v]) =>
            h('span.err' + (Number(v) ? '.bad' : ''), null, h('b', null, v), ' ', { frame: 'Frame', rs: 'RS', aac: 'AAC', crc: 'CRC' }[k] || k))) : null,
          ist.dlplus && ist.dlplus.length ? h('div.dlp', null, h('label', null, 'DL Plus'), ist.dlplus.map(t => h('span.tag', null, typeof t === 'string' ? t : `${t.type || t.tag}: ${t.text || t.value}`))) : null),
        ist.slides && ist.slides.length ? h('div.slides', null, ist.slides.map(f => h('figure', null,
          h('img', { src: slideSrc(f), alt: 'Slide', loading: 'lazy', onerror: e => e.target.classList.add('broken') }),
          h('figcaption', null, (f.file || '').split('/').pop(), f.w ? ` · ${f.w}×${f.h}` : '')))) : null,
      ] : h('p.muted', null, T('Dieser Dienst wurde im Bericht nicht gefunden (SId-Vergleich).'))))),
    extra.length ? h('section.rep-block.bad', null, h('h4', null, icon('alert'), T('Unerwartete Dienste')),
      h('ul', null, extra.map(e => h('li', null, `${e.sid} ${e.label || ''}`)))) : null,
    (report.tii && report.tii.length) || (report.announcements && report.announcements.length) ?
      h('section.rep-block', null, h('h4', null, icon('info'), T('TII & Durchsagen')),
        h('p.mono', null, 'TII: ', JSON.stringify(report.tii || []), ' · ' + T('Durchsagen') + ': ', JSON.stringify(report.announcements || []))) : null,
    h('details.raw', null, h('summary', null, T('Rohdaten (JSON)')), h('pre', null, JSON.stringify(report0, null, 2))));
}
function kpi(label, value, kind) { return h('div.kpi.' + kind, null, h('small', null, label), h('b', null, value)); }

// ---------- Messprotokoll ----------
export function protocolData({ cfg, state, plan, results, notes }) {
  return {
    generator: `${APP_NAME} Dashboard`, created: new Date().toISOString(),
    operator_notes: notes || '',
    tx: state?.tx || null, pluto: state?.pluto || null,
    config: cfg, capacity: capacity(cfg),
    testplan: plan ? { id: plan.id, title: plan.title, results } : null,
    check: state?.check || null, monitor: state?.monitor || null,
  };
}

function previewSection(pv) {
  const img = (src, cap) => src ? `<figure><img src="${src}" alt="${esc(cap)}"><figcaption>${esc(cap)}</figcaption></figure>`
    : `<figure class="empty"><div>–</div><figcaption>${esc(cap)}</figcaption></figure>`;
  const qa = pv.quality;
  const cst = qa?.constellation ? constellationSVG(qa.constellation, 220)?.outerHTML || '' : '';
  const progs = (pv.programs || []).map(p => `<h3>${esc(p.name)}</h3><div class="pv2">${img(p.tx, T('Quelle'))}${
    img(p.rx, T('Empfang (Pluto-RX)') + (p.rx_age_s != null ? ` · ${T('vor {s} s', { s: Math.round(p.rx_age_s) })}` : ''))}</div>`).join('');
  const q = qa ? `<div class="pvq2">${cst}<table class="kv">${[
    ['MER', `${fmt1(qa.mer_db, ' dB')}`], [T('Ausreißer'), `${fmt1(qa.outlier_pct)} %`],
    [T('Transportfehler'), String(qa.transport_errors ?? '–')], [T('TS-Pakete'), String(qa.ts_packets ?? '–')],
    [T('RX-Verstärkung'), `${qa.rx_gain_db ?? '–'} dB`], [T('Messung'), T('vor {s} s', { s: Math.round(qa.age_s || 0) })]]
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table></div>
    <p>${esc(T('Schätzung mit dem Pluto als Messempfänger, kein kalibriertes Messgerät'))}</p>` : `<p>${esc(T('Keine RX-Messung vorhanden (Vorschau öffnen, Gerätepfad DVB-T/C/S).'))}</p>`;
  return `<p>${esc(T('Aufgenommen:'))} ${esc(stamp(new Date(pv.time)))}</p>${progs}${q}`;
}

export function protocolHTML(data, cssText, sprite = '') {
  const cfg = data.config;
  const cap = data.capacity;
  const rf = cfg.rf;
  const sec = (title, inner) => `<section><h2>${esc(title)}</h2>${inner}</section>`;
  const kv = rows => `<table class="kv">${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table>`;
  const svcTable = `<table class="grid"><thead><tr><th>SId</th><th>Label</th><th>${esc(T('Kurz'))}</th><th>${esc(T('Typ'))}</th><th>Codec</th><th>kbit/s</th><th>${esc(T('Schutz'))}</th><th>CU</th></tr></thead><tbody>${
    cfg.services.map((s, i) => `<tr><td>${esc(s.sid)}</td><td>${esc(s.label)}</td><td>${esc(s.shortlabel)}</td><td>${esc(s.type)}</td><td>${esc(s.type === 'data' ? s.data.kind : CODECS[s.audio.codec]?.name)}</td><td>${s.type === 'data' ? s.data.bitrate : s.audio.bitrate}</td><td>${esc(protLabel(s.protection))}</td><td>${cap.items[i]?.cu ?? '–'}</td></tr>`).join('')
  }</tbody></table><p>${esc(T('Belegung: {used} von {total} CU', { used: cap.used, total: TOTAL_CU }))}</p>`;
  const results = data.testplan ? `<table class="grid"><thead><tr><th>#</th><th>${esc(T('Schritt'))}</th><th>${esc(T('Ergebnis'))}</th><th>${esc(T('Bemerkung'))}</th><th>${esc(T('Zeit'))}</th></tr></thead><tbody>${
    (data.testplan.results || []).map((r, i) => `<tr class="${r.result === 'fail' ? 'bad' : ''}"><td>${i + 1}</td><td>${esc(r.title)}</td><td>${esc({ pass: '✔ ' + T('bestanden'), fail: '✘ ' + T('nicht bestanden'), skip: '– ' + T('übersprungen') }[r.result] || T('offen'))}</td><td>${esc(r.note || '')}</td><td>${r.time ? esc(stamp(new Date(r.time))) : ''}</td></tr>`).join('')
  }</tbody></table>` : `<p>${esc(T('Kein Testplan ausgewählt.'))}</p>`;
  const rep = (r, kind) => {
    if (!r) return `<p>${esc(T('Kein Bericht vorhanden.'))}</p>`;
    const el = renderReport(r, cfg, kind);
    el.querySelectorAll('details').forEach(d => d.remove());
    return el.outerHTML;
  };
  return `<!doctype html><html lang="${LANG}"><head><meta charset="utf-8"><title>${esc(T('Messprotokoll'))} ${esc(APP_NAME)} ${esc(stamp(new Date(data.created)))}</title>
<style>${cssText}</style></head><body class="protocol">${sprite}
<header><h1>${esc(T('Messprotokoll'))} ${esc(APP_NAME)} · ${esc(stdName(standardOf(cfg)))}</h1><p>${esc(T('Erstellt:'))} <b>${esc(stamp(new Date(data.created)))}</b> · Preset: <b>${esc(cfg.name)}</b>${data.testplan ? ` · ${esc(T('Testplan:'))} <b>${esc(data.testplan.title)}</b>` : ''}</p>
<button class="noprint" onclick="print()">${esc(T('Drucken / PDF'))}</button></header>
${data.operator_notes ? sec(T('Bemerkungen'), `<p>${esc(data.operator_notes)}</p>`) : ''}
${sec(T('HF'), kv([[T('Kanal'), `${rf.channel} · ${fmtMHz(rf.freq_hz)} MHz`], [T('Versatz'), `${rf.offset_hz} Hz`], [T('Dämpfung'), `${fmtDb(rf.atten_db)} dB`],
    [T('Abtastrate'), `${requiredSampleRate(cfg) / 1e6} MS/s`], ['Backoff', `${rf.backoff_db} dB`], ['Standard', stdName(standardOf(cfg))],
    ...(standardOf(cfg) === 'dab' ? [[T('Modus'), `Mode ${['I', 'II', 'III', 'IV'][cfg.mode - 1]}`], ['TII', cfg.tii.enable ? `Comb ${cfg.tii.comb} / Pattern ${cfg.tii.pattern}` : T('aus')]] : []),
    ['Pluto', data.pluto ? `${data.pluto.uri || ''} FW ${data.pluto.fw || ''}` : '–']]))}
${standardOf(cfg) === 'dab' ? `${sec('Ensemble', kv([['EId', cfg.ensemble.eid], ['ECC', cfg.ensemble.ecc], ['Label', cfg.ensemble.label], [T('Kurzlabel'), cfg.ensemble.shortlabel],
    [T('Zeichensatz'), cfg.ensemble.charset], ['LTO', ltoLabel(cfg.ensemble.lto)], ['AL-Flag', cfg.ensemble.alarm_flag ? T('gesetzt') : T('aus')]]))}
${sec(T('Dienste'), svcTable)}` : protocolStdSection(cfg)}
${sec(T('Testplan-Ergebnisse'), results)}
${(data.sensitivity_marks || []).length ? sec(T('Empfindlichkeit (markierte Schwellen)'), `<table class="grid"><thead><tr><th>${esc(T('Zeit'))}</th><th>${esc(T('Kanal'))}</th><th>${esc(T('Dämpfung'))}</th><th>${esc(T('Pegel (geschätzt)'))}</th></tr></thead><tbody>${data.sensitivity_marks.map(m => `<tr><td>${esc(stamp(new Date(m.time)))}</td><td>${esc(m.channel)}</td><td>${fmtDb(m.atten_db)} dB</td><td>${m.dbm == null ? '–' : esc(fmtDb(m.dbm)) + ' dBm'}</td></tr>`).join('')}</tbody></table>`) : ''}
${data.preview ? sec(T('Live-Vorschau und Empfangsqualität'), previewSection(data.preview)) : ''}
${sec(T('Offline-Prüfung (Soll/Ist)'), rep(data.check, 'check'))}
${sec(T('Kontrollempfang (Soll/Ist)'), rep(data.monitor, 'monitor'))}
<details><summary>${esc(T('Konfiguration (JSON)'))}</summary><pre>${esc(JSON.stringify(cfg, null, 2))}</pre></details>
</body></html>`;
}
