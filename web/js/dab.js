// DAB/DAB+-Fachdaten: Kanäle, PTy, Sprachen, Länder, Kapazität (CU), gültige Parameter.
// Quellen: ETSI EN 300 401 (Tab. 6 UEP, EEP-Formeln), TS 101 756 (PTy, Sprachen, ECC), SPEC.md.

import { T, LOCALE } from './i18n.js';

export const TOTAL_CU = 864;
export const ATTEN_MAX = 89.75;
export const ATTEN_SAFE = 40;

// ---------- Kanaltabelle ----------
const BAND3 = [
  ['5A', 174.928], ['5B', 176.640], ['5C', 178.352], ['5D', 180.064],
  ['6A', 181.936], ['6B', 183.648], ['6C', 185.360], ['6D', 187.072],
  ['7A', 188.928], ['7B', 190.640], ['7C', 192.352], ['7D', 194.064],
  ['8A', 195.936], ['8B', 197.648], ['8C', 199.360], ['8D', 201.072],
  ['9A', 202.928], ['9B', 204.640], ['9C', 206.352], ['9D', 208.064],
  ['10A', 209.936], ['10N', 210.096], ['10B', 211.648], ['10C', 213.360], ['10D', 215.072],
  ['11A', 216.928], ['11N', 217.088], ['11B', 218.640], ['11C', 220.352], ['11D', 222.064],
  ['12A', 223.936], ['12N', 224.096], ['12B', 225.648], ['12C', 227.360], ['12D', 229.072],
  ['13A', 230.784], ['13B', 232.496], ['13C', 234.208], ['13D', 235.776], ['13E', 237.488], ['13F', 239.200],
];
const LBAND = 'ABCDEFGHIJKLMNOPQRSTUVW'.split('').map((c, i) =>
  ['L' + c, Math.round((1452.960 + i * 1.712) * 1000) / 1000]);

export const CHANNELS = [
  ...BAND3.map(([id, mhz]) => ({ id, mhz, band: 'Band III' })),
  ...LBAND.map(([id, mhz]) => ({ id, mhz, band: 'L-Band' })),
];
export const channelById = id => CHANNELS.find(c => c.id === id);
export const channelHz = id => { const c = channelById(id); return c ? Math.round(c.mhz * 1e6) : null; };
export const isLBand = id => /^L[A-W]$/.test(id || '');
export const FREQ_MIN_HZ = 70e6, FREQ_MAX_HZ = 6e9;

export const MODES = [
  { v: 1, label: 'Mode I', hint: T('Band III, Gleichwellennetze (Standard)') },
  { v: 2, label: 'Mode II', hint: T('L-Band, lokale Versorgung') },
  { v: 3, label: 'Mode III', hint: T('Satellit/unter 3 GHz, kabelgebunden') },
  { v: 4, label: 'Mode IV', hint: T('L-Band, Gleichwelle') },
];

export const SAMPLE_RATES = [
  { v: 2048000, label: '2,048 MS/s', warn: T('Wird vom AD9361-Treiber ohne FIR abgelehnt') },
  { v: 4096000, label: T('4,096 MS/s (empfohlen)') },
  { v: 6144000, label: '6,144 MS/s', warn: T('Nahe am USB-Limit (7,15 MS/s gemessen)') },
];

// ---------- Programmarten (PTy, RDS/DAB Europa) ----------
export const PTY = [
  T('Kein Programmtyp'), T('Nachrichten'), T('Aktuelles'), T('Information'), T('Sport'), T('Bildung'), T('Hörspiel'), T('Kultur'),
  T('Wissenschaft'), T('Unterhaltung'), T('Popmusik'), T('Rockmusik'), T('Unterhaltungsmusik'), T('Leichte Klassik'),
  T('Ernste Klassik'), T('Sonstige Musik'), T('Wetter'), T('Wirtschaft'), T('Kinderprogramm'), T('Soziales'), T('Religion'),
  T('Hörerbeteiligung'), T('Reise'), T('Freizeit'), T('Jazz'), T('Countrymusik'), T('Landesmusik'), T('Oldies'), T('Folklore'),
  T('Dokumentation'), T('Alarmtest'), T('Alarm'),
];

// ---------- Sprachen (FIG 0/5, TS 101 756 Tab. 9/10) ----------
export const LANGUAGES = [
  [0x00, T('unbekannt / nicht gesetzt')], [0x01, T('Albanisch')], [0x02, T('Bretonisch')], [0x03, T('Katalanisch')],
  [0x04, T('Kroatisch')], [0x05, T('Walisisch')], [0x06, T('Tschechisch')], [0x07, T('Dänisch')], [0x08, T('Deutsch')],
  [0x09, T('Englisch')], [0x0A, T('Spanisch')], [0x0B, T('Esperanto')], [0x0C, T('Estnisch')], [0x0D, T('Baskisch')],
  [0x0E, T('Färöisch')], [0x0F, T('Französisch')], [0x10, T('Friesisch')], [0x11, T('Irisch')], [0x12, T('Gälisch')],
  [0x13, T('Galicisch')], [0x14, T('Isländisch')], [0x15, T('Italienisch')], [0x16, T('Samisch')], [0x17, T('Latein')],
  [0x18, T('Lettisch')], [0x19, T('Luxemburgisch')], [0x1A, T('Litauisch')], [0x1B, T('Ungarisch')], [0x1C, T('Maltesisch')],
  [0x1D, T('Niederländisch')], [0x1E, T('Norwegisch')], [0x1F, T('Okzitanisch')], [0x20, T('Polnisch')],
  [0x21, T('Portugiesisch')], [0x22, T('Rumänisch')], [0x23, T('Rätoromanisch')], [0x24, T('Serbisch')],
  [0x25, T('Slowakisch')], [0x26, T('Slowenisch')], [0x27, T('Finnisch')], [0x28, T('Schwedisch')], [0x29, T('Türkisch')],
  [0x2A, T('Flämisch')], [0x2B, T('Wallonisch')],
  [0x49, T('Ukrainisch')], [0x4A, T('Thai')], [0x56, T('Russisch')], [0x5A, T('Persisch')], [0x63, T('Mazedonisch')],
  [0x65, T('Koreanisch')], [0x69, T('Japanisch')], [0x6B, T('Hindi')], [0x6C, T('Hebräisch')], [0x70, T('Griechisch')],
  [0x75, T('Chinesisch')], [0x77, T('Bulgarisch')], [0x7E, T('Arabisch')],
].map(([v, name]) => ({ v: '0x' + v.toString(16).toUpperCase().padStart(2, '0'), name }));

// ---------- Länder (ECC + Länderkennung im ersten Nibble von EId/SId) ----------
export const COUNTRIES = [
  [T('Deutschland'), 'E0', ['D', '1']], [T('Österreich'), 'E0', ['A']], [T('Schweiz'), 'E1', ['4']],
  [T('Liechtenstein'), 'E2', ['9']], [T('Belgien'), 'E0', ['6']], [T('Niederlande'), 'E3', ['8']],
  [T('Luxemburg'), 'E1', ['7']], [T('Frankreich'), 'E1', ['F']], [T('Italien'), 'E0', ['5']], [T('Spanien'), 'E2', ['E']],
  [T('Portugal'), 'E4', ['8']], [T('Vereinigtes Königreich'), 'E1', ['C']], [T('Irland'), 'E3', ['2']],
  [T('Dänemark'), 'E1', ['9']], [T('Norwegen'), 'E2', ['F']], [T('Schweden'), 'E3', ['E']], [T('Finnland'), 'E1', ['6']],
  [T('Polen'), 'E2', ['3']], [T('Tschechien'), 'E2', ['2']], [T('Slowakei'), 'E2', ['5']], [T('Ungarn'), 'E0', ['B']],
  [T('Slowenien'), 'E4', ['9']], [T('Kroatien'), 'E3', ['C']], [T('Malta'), 'E0', ['C']], [T('Griechenland'), 'E1', ['1']],
  [T('Monaco'), 'E2', ['B']], [T('Australien'), 'F0', ['1', '2', '3', '4', '5', '6', '7', '8']],
].map(([name, ecc, cids]) => ({ name, ecc: '0x' + ecc, cids }));

// ---------- Durchsagearten (FIG 0/18, 0/19) ----------
export const ANNOUNCEMENTS = [
  ['Alarm', T('Alarm')], ['Traffic', T('Verkehrsfunk')], ['Travel', T('ÖPNV/Reise')], ['Warning', T('Warnung/Service')],
  ['News', T('Nachrichten')], ['Weather', T('Wetter')], ['Event', T('Veranstaltung')], ['Special', T('Sonderereignis')],
  ['Rad_info', T('Programmhinweis')], ['Sports', T('Sport')], ['Finance', T('Finanzen')],
].map(([v, name]) => ({ v, name }));
export const annName = v => (ANNOUNCEMENTS.find(a => a.v === v) || { name: v }).name;

// ---------- Codecs & gültige Parameter ----------
export const CODECS = {
  'aac-lc': { name: 'AAC-LC', short: 'AAC-LC', type: 'dabplus' },
  'he-aac': { name: 'HE-AAC (v1, SBR)', short: 'HE-AAC', type: 'dabplus' },
  'he-aac-v2': { name: 'HE-AAC v2 (SBR+PS)', short: 'HE-AACv2', type: 'dabplus' },
  'mp2': { name: 'MPEG-1/2 Layer II', short: 'MP2', type: 'dab' },
};
export const MP2_MODES = [
  { v: 's', name: 'Stereo' }, { v: 'j', name: 'Joint Stereo' }, { v: 'd', name: 'Dual Channel' }, { v: 'm', name: 'Mono' },
];
const MP2_RATES_48 = [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384];
const MP2_RATES_24 = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];

export function samplerates(type, codec) {
  if (type === 'dab' || codec === 'mp2') return [48000, 24000];
  return [48000, 32000];
}
export function channelOptions(codec) { return codec === 'he-aac-v2' ? [2] : [2, 1]; }

export function bitrates(type, codec, samplerate, channels) {
  if (type === 'data') { const r = []; for (let b = 8; b <= 192; b += 8) r.push(b); return r; }
  if (codec === 'mp2') {
    let list = samplerate === 24000 ? MP2_RATES_24 : MP2_RATES_48;
    // ISO 11172-3: Mono nur bis 192 kbit/s, Stereo-Modi ab 64 (48 kHz)
    if (samplerate !== 24000) list = channels === 1 ? list.filter(b => b <= 192) : list.filter(b => b >= 64);
    return list;
  }
  const r = [];
  for (let b = 8; b <= 192; b += 8) r.push(b);
  return r;
}

export function bitrateHint(type, codec, br, channels) {
  if (type === 'dabplus') {
    if (codec === 'he-aac-v2' && br > 64) return T('HE-AAC v2 ist für niedrige Raten gedacht (≤ 64 kbit/s); höher funktioniert, ist aber unüblich.');
    if (codec === 'aac-lc' && br < 64 && channels === 2) return T('AAC-LC unter 64 kbit/s Stereo klingt deutlich hörbar komprimiert.');
    if (codec === 'he-aac' && br > 96) return T('Über 96 kbit/s ist AAC-LC üblicher.');
  }
  return '';
}

// ---------- Fehlerschutz & Kapazität ----------
// UEP nach EN 300 401 Tabelle 6: Bitrate → {Schutzstufe: CU}
export const UEP_TABLE = {
  32: { 5: 16, 4: 21, 3: 24, 2: 29, 1: 35 },
  48: { 5: 24, 4: 29, 3: 35, 2: 42, 1: 52 },
  56: { 5: 29, 4: 35, 3: 42, 2: 52 },
  64: { 5: 32, 4: 42, 3: 48, 2: 58, 1: 70 },
  80: { 5: 40, 4: 52, 3: 58, 2: 70, 1: 84 },
  96: { 5: 48, 4: 58, 3: 70, 2: 84, 1: 104 },
  112: { 5: 58, 4: 70, 3: 84, 2: 104 },
  128: { 5: 64, 4: 84, 3: 96, 2: 116, 1: 140 },
  160: { 5: 80, 4: 104, 3: 116, 2: 140, 1: 168 },
  192: { 5: 96, 4: 116, 3: 140, 2: 168, 1: 208 },
  224: { 5: 116, 4: 140, 3: 168, 2: 208, 1: 232 },
  256: { 5: 128, 4: 168, 3: 192, 2: 232, 1: 280 },
  320: { 5: 160, 4: 208, 2: 280 },
  384: { 5: 192, 3: 280, 1: 416 },
};
const EEP_A = [12, 8, 6, 4];
const EEP_B = [27, 21, 18, 15];

/** Liefert alle gültigen Schutzprofile für Typ/Codec/Bitrate: [{profile, level, cu, label}] */
export function protections(type, codec, bitrate) {
  const out = [];
  if ((type === 'dab' || codec === 'mp2') && UEP_TABLE[bitrate]) {
    for (const lvl of [1, 2, 3, 4, 5]) {
      const cu = UEP_TABLE[bitrate][lvl];
      if (cu) out.push({ profile: 'UEP', level: lvl, cu, label: `UEP ${lvl}` });
    }
  }
  if (bitrate % 8 === 0) for (let l = 1; l <= 4; l++)
    out.push({ profile: 'EEP_A', level: l, cu: EEP_A[l - 1] * bitrate / 8, label: `EEP ${l}-A` });
  if (bitrate % 32 === 0) for (let l = 1; l <= 4; l++)
    out.push({ profile: 'EEP_B', level: l, cu: EEP_B[l - 1] * bitrate / 32, label: `EEP ${l}-B` });
  return out;
}

export function protLabel(p) {
  if (!p) return '–';
  if (p.profile === 'UEP') return `UEP ${p.level}`;
  return `EEP ${p.level}-${p.profile === 'EEP_B' ? 'B' : 'A'}`;
}

export function serviceBitrate(s) {
  return s.type === 'data' ? Number(s.data?.bitrate || 0) : Number(s.audio?.bitrate || 0);
}

/** CU eines Dienstes; null wenn Kombination ungültig */
export function serviceCU(s) {
  const br = serviceBitrate(s);
  const p = s.protection || {};
  const m = protections(s.type, s.audio?.codec, br).find(x => x.profile === p.profile && x.level === Number(p.level));
  return m ? m.cu : null;
}

export function capacity(cfg) {
  const items = (cfg.services || []).map(s => ({ key: s.key, label: s.label, cu: serviceCU(s), svc: s }));
  const used = items.reduce((a, b) => a + (b.cu || 0), 0);
  return { items, used, free: TOTAL_CU - used, over: used > TOTAL_CU, invalid: items.filter(i => i.cu == null) };
}

// ---------- Zeichensatz / Labels ----------
// EBU Latin (TS 101 756 Anhang C) – vereinfachte Prüfung auf darstellbare Zeichen
const EBU_EXTRA = 'áàéèíìóòúùÑÇŞβ¡Ĳâäêëîïôöûüñçşğıĳªα©‰Ğěňőπ€£$←↑→↓º¹²³±İńűµ¿÷°¼½¾§ÁÀÉÈÍÌÓÒÚÙŘČŠŽÐĿÂÄÊËÎÏÔÖÛÜřčšžđŀÃÅÆŒŷÝÕØÞŊŔĆŚŹŦðãåæœŵýõøþŋŕćśźŧ';
export function ebuInvalidChars(text) {
  const bad = new Set();
  for (const ch of text || '') {
    const c = ch.codePointAt(0);
    if (c >= 0x20 && c <= 0x7E) continue;
    if (EBU_EXTRA.includes(ch)) continue;
    bad.add(ch);
  }
  return [...bad];
}
export const utf8Len = s => new TextEncoder().encode(s || '').length;
export function labelLen(text, charset) { return charset === 'utf8' ? utf8Len(text) : [...(text || '')].length; }

/** Prüft, ob kurz eine Teilfolge von lang ist; gibt die Indizes zurück oder null. */
export function shortlabelMask(label, short) {
  const L = [...(label || '')], S = [...(short || '')];
  const idx = []; let j = 0;
  for (let i = 0; i < L.length && j < S.length; i++) if (L[i] === S[j]) { idx.push(i); j++; }
  return j === S.length ? idx : null;
}

// ---------- Hex-Helfer ----------
export function parseHex(v) {
  if (v == null) return null;
  if (typeof v === 'number') return v;
  const s = String(v).trim();
  if (!/^(0x)?[0-9a-f]+$/i.test(s)) return null;
  return parseInt(s.replace(/^0x/i, ''), 16);
}
export const hex = (n, digits) => '0x' + Number(n).toString(16).toUpperCase().padStart(digits, '0');
export function normHex(v) { const n = parseHex(v); return n == null ? String(v ?? '') : n.toString(16).toUpperCase(); }

export function ltoLabel(v) {
  if (v === 'auto') return T('automatisch (Systemzeit)');
  const n = Number(v); const sign = n < 0 ? '−' : '+'; const a = Math.abs(n);
  return `UTC${sign}${Math.floor(a / 2)}:${a % 2 ? '30' : '00'}`;
}

export const fmtMHz = hz => (hz / 1e6).toLocaleString(LOCALE, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
export const fmtDb = v => Number(v).toLocaleString(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace('-', '−');
export const fmtNum = (v, d = 0) => v == null || isNaN(v) ? '–' : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', '−');

// ---------- Konfigurationsvorlagen ----------
export function newService(key, n = 1) {
  return {
    key, sid: hex(0xD000 + n, 4), ecc: null,
    label: `Dienst ${n}`, shortlabel: `Dienst${n}`.slice(0, 8),
    pty: 0, pty_dynamic: false, language: '0x08', type: 'dabplus',
    audio: {
      source: { kind: 'tone', file: '', freq_hz: 1000, level_dbfs: -18, left_hz: 1000, right_hz: 400,
        sweep_from_hz: 20, sweep_to_hz: 20000, sweep_s: 10, loop: true,
        playlist: '', shuffle: false, crossfade_s: 0, normalize: true, dls_from_tags: true },
      codec: 'he-aac-v2', samplerate: 48000, channels: 2, bitrate: 64, mp2_mode: 'j', gain_db: 0, bandwidth_hz: 0,
    },
    protection: { profile: 'EEP_A', level: 3 },
    pad: {
      enable: true, bytes: 58,
      dls: { enable: true, text: `DAB-Lab · Dienst ${n}`, dlplus: { enable: false, artist: '', title: '', album: '', station: '', program: '', url: '' },
        rotate: [], rotate_s: 10, charset: 'utf8', remove_label: false },
      sls: { enable: false, slides: [], interval_s: 10, max_bytes: 50000, raw: false, categories: false },
    },
    data: { kind: 'spi', bitrate: 16, packet_address: 1, files: [] },
    spi: { logo: '' },
    announcements: { supported: [], clusters: [] },
    linking: { fm_pi: null, fm_freqs_mhz: [], other_ensembles: [] },
  };
}

export function defaultConfig() {
  return {
    name: 'Standard', mode: 1,
    ensemble: { eid: '0x4FFF', ecc: '0xE0', label: 'DABLAB', shortlabel: 'DABLAB', charset: 'ebu',
      international_table: 1, lto: 'auto', datetime: true, alarm_flag: false, reconfig_counter: 'hash' },
    services: [newService('srv1', 1)],
    clusters: [],
    tii: { enable: false, comb: 1, pattern: 11, old_variant: false },
    rf: { channel: '6C', freq_hz: 185360000, offset_hz: 0, lo_offset_hz: 0, atten_db: 60, sample_rate: 4096000,
      backoff_db: 12, cfr: false, enabled: false, cal_dbm_at_0db: {} },
  };
}

/** Füllt fehlende Felder mit Standardwerten auf (Server-Configs können sparsamer sein). */
export function normalizeConfig(cfg) {
  const d = defaultConfig();
  const c = structuredClone(cfg || d);
  c.ensemble = { ...d.ensemble, ...(c.ensemble || {}) };
  c.tii = { ...d.tii, ...(c.tii || {}) };
  c.rf = { ...d.rf, ...(c.rf || {}) };
  c.rf.cal_dbm_at_0db = c.rf.cal_dbm_at_0db || {};
  c.clusters = c.clusters || [];
  c.mode = c.mode || 1;
  c.services = (c.services || []).map((s, i) => {
    const t = newService(s.key || `srv${i + 1}`, i + 1);
    const r = { ...t, ...s };
    r.audio = { ...t.audio, ...(s.audio || {}) };
    r.audio.source = { ...t.audio.source, ...((s.audio || {}).source || {}) };
    r.protection = { ...t.protection, ...(s.protection || {}) };
    r.pad = { ...t.pad, ...(s.pad || {}) };
    r.pad.dls = { ...t.pad.dls, ...((s.pad || {}).dls || {}) };
    r.pad.dls.dlplus = { ...t.pad.dls.dlplus, ...(((s.pad || {}).dls || {}).dlplus || {}) };
    r.pad.dls.rotate = r.pad.dls.rotate || [];
    r.pad.sls = { ...t.pad.sls, ...((s.pad || {}).sls || {}) };
    r.pad.sls.slides = r.pad.sls.slides || [];
    r.data = { ...t.data, ...(s.data || {}) };
    r.spi = { ...t.spi, ...(s.spi || {}) };
    r.announcements = { ...t.announcements, ...(s.announcements || {}) };
    r.linking = { ...t.linking, ...(s.linking || {}) };
    return r;
  });
  return c;
}

// ---------- Validierung ----------
export function validateConfig(c) {
  const e = [];
  const add = (path, msg) => e.push({ path, msg });
  const en = c.ensemble;
  const eid = parseHex(en.eid);
  if (eid == null || eid > 0xFFFF || !/^0x[0-9a-f]{4}$/i.test(en.eid)) add('ensemble.eid', T('EId: 4-stellig hex, z. B. 0x4FFF'));
  if (!/^0x[0-9a-f]{2}$/i.test(en.ecc || '')) add('ensemble.ecc', T('ECC: 2-stellig hex, z. B. 0xE0'));
  checkLabel(add, 'ensemble', en.label, en.shortlabel, en.charset);
  if (!(en.reconfig_counter === 'hash' || en.reconfig_counter === 'off' ||
        (Number.isInteger(Number(en.reconfig_counter)) && en.reconfig_counter >= 0 && en.reconfig_counter <= 1023)))
    add('ensemble.reconfig_counter', T('Reconfig-Counter: hash, off oder 0–1023'));

  const keys = new Set(), sids = new Set();
  c.services.forEach((s, i) => {
    const p = `services.${i}`;
    if (!/^[a-z0-9_]+$/.test(s.key || '')) add(`${p}.key`, T('Dienst {n}: Schlüssel nur a–z, 0–9, _', { n: i + 1 }));
    if (keys.has(s.key)) add(`${p}.key`, T('Dienst {n}: Schlüssel „{key}“ doppelt', { n: i + 1, key: s.key }));
    keys.add(s.key);
    const sid = parseHex(s.sid);
    const sidOk = s.type === 'data' ? /^0x[0-9a-f]{8}$/i.test(s.sid || '') : /^0x[0-9a-f]{4}$/i.test(s.sid || '');
    if (!sidOk) add(`${p}.sid`, T('{label}: SId {d}-stellig hex', { label: s.label, d: s.type === 'data' ? '8' : '4' }));
    else if (sids.has(sid)) add(`${p}.sid`, T('{label}: SId {sid} doppelt', { label: s.label, sid: s.sid }));
    sids.add(sid);
    if (s.ecc && !/^0x[0-9a-f]{2}$/i.test(s.ecc)) add(`${p}.ecc`, T('{label}: ECC 2-stellig hex', { label: s.label }));
    checkLabel(add, p, s.label, s.shortlabel, en.charset, s.label);
    if (s.type !== 'data') {
      const a = s.audio;
      if (s.type === 'dab' && a.codec !== 'mp2') add(`${p}.audio.codec`, T('{label}: DAB (klassisch) erfordert MP2', { label: s.label }));
      if (s.type === 'dabplus' && a.codec === 'mp2') add(`${p}.audio.codec`, T('{label}: MP2 nur bei Typ DAB', { label: s.label }));
      if (!samplerates(s.type, a.codec).includes(Number(a.samplerate))) add(`${p}.audio.samplerate`, T('{label}: Abtastrate ungültig', { label: s.label }));
      if (!channelOptions(a.codec).includes(Number(a.channels))) add(`${p}.audio.channels`, T('{label}: HE-AAC v2 braucht Stereo', { label: s.label }));
      if (!bitrates(s.type, a.codec, Number(a.samplerate), Number(a.channels)).includes(Number(a.bitrate)))
        add(`${p}.audio.bitrate`, T('{label}: Bitrate {br} kbit/s ungültig für {codec}', { label: s.label, br: a.bitrate, codec: CODECS[a.codec]?.name || a.codec }));
      if (a.source.kind === 'file' && !a.source.file) add(`${p}.audio.source.file`, T('{label}: Audiodatei fehlt', { label: s.label }));
      if (a.source.kind === 'playlist' && !a.source.playlist) add(`${p}.audio.source.playlist`, T('{label}: Playlist fehlt', { label: s.label }));
      if (a.source.kind === 'playlist' && !(a.source.crossfade_s >= 0 && a.source.crossfade_s <= 30)) add(`${p}.audio.source.crossfade_s`, T('{label}: Crossfade 0–30 s', { label: s.label }));
      if (s.pad.enable && (s.pad.bytes < 0 || s.pad.bytes > 255)) add(`${p}.pad.bytes`, T('{label}: PAD 0–255 Byte', { label: s.label }));
      if (s.pad.dls.enable && [...(s.pad.dls.text || '')].length > 128) add(`${p}.pad.dls.text`, T('{label}: DLS max. 128 Zeichen', { label: s.label }));
    }
    if (serviceCU(s) == null) add(`${p}.protection`, T('{label}: Fehlerschutz {prot} passt nicht zu {br} kbit/s', { label: s.label, prot: protLabel(s.protection), br: serviceBitrate(s) }));
    const lk = s.linking;
    if (lk.fm_pi && !/^0x[0-9a-f]{4}$/i.test(lk.fm_pi)) add(`${p}.linking.fm_pi`, T('{label}: FM-PI 4-stellig hex', { label: s.label }));
    (lk.fm_freqs_mhz || []).forEach(f => { if (!(f >= 87.5 && f <= 108)) add(`${p}.linking.fm_freqs_mhz`, T('{label}: UKW-Frequenz {f} außerhalb 87,5–108', { label: s.label, f })); });
  });
  if (c.services.length === 0) add('services', T('Mindestens ein Dienst nötig'));
  const cap = capacity(c);
  if (cap.over) add('capacity', T('Kapazität überschritten: {used} von {total} CU', { used: cap.used, total: TOTAL_CU }));
  const cids = new Set();
  c.clusters.forEach((cl, i) => {
    if (!(cl.id >= 1 && cl.id <= 255)) add(`clusters.${i}.id`, T('Cluster {n}: ID 1–255', { n: i + 1 }));
    if (cids.has(cl.id)) add(`clusters.${i}.id`, T('Cluster {id} doppelt', { id: cl.id }));
    cids.add(cl.id);
    if (!c.services.some(s => s.key === cl.service)) add(`clusters.${i}.service`, T('Cluster {id}: Dienst fehlt', { id: cl.id }));
  });
  const rf = c.rf;
  if (!(rf.freq_hz >= FREQ_MIN_HZ && rf.freq_hz <= FREQ_MAX_HZ)) add('rf.freq_hz', T('Frequenz 70 MHz – 6 GHz'));
  if (!(rf.atten_db >= 0 && rf.atten_db <= ATTEN_MAX)) add('rf.atten_db', T('Dämpfung 0 – 89,75 dB'));
  if (c.tii.enable && !(c.tii.comb >= 0 && c.tii.comb <= 23 && c.tii.pattern >= 0 && c.tii.pattern <= 69))
    add('tii', T('TII: Comb 0–23, Pattern 0–69'));
  return e;
}

function checkLabel(add, p, label, short, charset, who = 'Ensemble') {
  if (!label || !label.trim()) add(`${p}.label`, T('{who}: Label leer', { who }));
  const n = labelLen(label, charset);
  if (n > 16) add(`${p}.label`, T(charset === 'utf8' ? '{who}: Label {n}/16 Byte' : '{who}: Label {n}/16 Zeichen', { who, n }));
  if ([...(short || '')].length > 8) add(`${p}.shortlabel`, T('{who}: Kurzlabel max. 8 Zeichen', { who }));
  if (!short) add(`${p}.shortlabel`, T('{who}: Kurzlabel leer', { who }));
  else if (!shortlabelMask(label, short)) add(`${p}.shortlabel`, T('{who}: Kurzlabel muss aus Zeichen des Labels (in Reihenfolge) bestehen', { who }));
  if (charset !== 'utf8') {
    const bad = ebuInvalidChars(label);
    if (bad.length) add(`${p}.label`, T('{who}: Zeichen „{chars}“ nicht im EBU-Latin-Satz', { who, chars: bad.join('') }));
  }
}

// Felder, die ohne Neustart der Kette geändert werden können
export function isLivePath(path) {
  return /^rf\.(channel|freq_hz|offset_hz|atten_db|enabled|cal_dbm_at_0db)/.test(path)
    || /^ensemble\.(label|shortlabel)$/.test(path)
    || /^services\.\d+\.(label|shortlabel)$/.test(path)
    || /^services\.\d+\.pad\.dls\.(text|dlplus|rotate|rotate_s)/.test(path)
    || /^services\.\d+\.pad\.sls\.(slides|interval_s)/.test(path)
    || /^clusters\.\d+\.active$/.test(path)
    || path === 'name';
}

export function diffPaths(a, b, base = '', out = []) {
  if (a === b) return out;
  const ta = typeof a, tb = typeof b;
  if (a && b && ta === 'object' && tb === 'object' && Array.isArray(a) === Array.isArray(b)) {
    if (Array.isArray(a) && a.length !== b.length && base.match(/^services$|^clusters$/)) { out.push(base); return out; }
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) diffPaths(a[k], b[k], base ? `${base}.${k}` : k, out);
    return out;
  }
  if (JSON.stringify(a) !== JSON.stringify(b)) out.push(base);
  return out;
}

// ---------- Playlists (M3U/PLS) ----------
export const AUDIO_EXT = /\.(mp3|ogg|oga|opus|flac|wav|m4a|aac|wma|mp2)$/i;
export function fmtDur(s) {
  if (s == null || isNaN(s)) return '–';
  s = Math.round(s); const m = Math.floor(s / 60);
  const ss = String(s % 60).padStart(2, '0');
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
export function playlistToM3U(pl) {
  const out = ['#EXTM3U'];
  for (const it of pl.items || []) {
    const name = [it.artist, it.title].filter(Boolean).join(' - ') || (it.file || '').split('/').pop();
    out.push(`#EXTINF:${Math.round(it.duration_s ?? -1)},${name}`);
    out.push((it.file || '').replace(/^media\//, ''));
  }
  return out.join('\n') + '\n';
}
