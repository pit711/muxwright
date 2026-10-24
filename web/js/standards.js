// Mehrstandard: UKW (FM-Stereo + RDS), DVB-T, DVB-C – Daten, Defaults, Berechnungen, Validierung.
// Quellen: research/FM_RDS_RECHERCHE.md (IEC 60315-4, EN 50067/IEC 62106), research/DVB_T_C_RECHERCHE.md (EN 300 744 / EN 300 429).
// Schema-Annahme (bis SPEC.md die Abschnitte `mode: "fm" | "dvbt" | "dvbc"` festschreibt): config.mode = 1..4 → DAB,
// "fm"/"dvbt"/"dvbc" → neuer Standard; Parameter in config.fm, config.dvbt, config.dvbc, config.dvb (Programme + SI gemeinsam).

import { parseHex, hex, PTY, COUNTRIES, newService, normalizeConfig as normDab, validateConfig as valDab, isLivePath as liveDab,
  ATTEN_MAX, FREQ_MIN_HZ, FREQ_MAX_HZ } from './dab.js';
import { T } from './i18n.js';

export const STANDARDS = [
  { id: 'dab', name: 'DAB+', long: 'DAB / DAB+ (EN 300 401)' },
  { id: 'fm', name: T('UKW'), long: T('UKW FM-Stereo + RDS (BS.450 / IEC 62106)') },
  { id: 'dvbt', name: 'DVB-T', long: 'DVB-T (EN 300 744)' },
  { id: 'dvbc', name: 'DVB-C', long: 'DVB-C (EN 300 429)' },
  { id: 'dvbs', name: 'DVB-S', long: 'DVB-S / DVB-S2 (EN 300 421 / EN 302 307)' },
  { id: 'pal', name: 'PAL', long: T('Analog-TV PAL B/G (ITU-R BT.470 / BT.1700)') },
];
export const stdName = id => (STANDARDS.find(s => s.id === id) || STANDARDS[0]).name;
export function standardOf(cfg) {
  const m = cfg?.mode;
  return typeof m === 'string' && ['fm', 'dvbt', 'dvbc', 'dvbs', 'pal'].includes(m) ? m : 'dab';
}
export const isDvb = std => std === 'dvbt' || std === 'dvbc' || std === 'dvbs';
/** Ausgabeweg vom Server normalisieren: "usb" | "device"/"pluto" (plus path_label) → 'usb' | 'pluto' */
export function normPath(rf) { if (!rf) return null; const p = rf.path_label || rf.path; if (p == null) return null; return /^(pluto|device)$/i.test(p) ? 'pluto' : /^usb$/i.test(p) ? 'usb' : String(p); }

// ---------------------------------------------------------------- Kanaltabellen
export const FM_MIN_MHZ = 87.5, FM_MAX_MHZ = 108.0, FM_STEP_MHZ = 0.1;

function range(a, b) { const r = []; for (let i = a; i <= b; i++) r.push(i); return r; }
// DVB-T: VHF Band III E5–E12 (7 MHz), UHF K21–K69 (8 MHz)
export const DVBT_CHANNELS = [
  ...range(5, 12).map(n => ({ id: `E${n}`, mhz: 177.5 + 7 * (n - 5), band: 'VHF III', bw: 7 })),
  ...range(21, 69).map(n => ({ id: `K${n}`, mhz: 474 + 8 * (n - 21), band: 'UHF', bw: 8 })),
];
// DVB-C nach SPEC (deutsches 8-MHz-Digitalraster): K02–K04 (7 MHz), S02–S09 113…169, K05–K11 177…225, S11–S20 233…305,
// Hyperband S21–S41 306…466, K21–K69 474…858. ⚠️ VHF-Teil gegen die Netzbetreiberliste prüfen (manche Netze +1 MHz) → „frei“.
export const DVBC_CHANNELS = [
  ...[[2, 50.5], [3, 57.5], [4, 64.5]].map(([n, mhz]) => ({ id: `K0${n}`, mhz, band: 'VHF I', bw: 7 })),
  ...range(2, 9).map(n => ({ id: `S0${n}`, mhz: 113 + 8 * (n - 2), band: T('Sonderkanäle USB'), bw: 8 })),
  ...range(5, 11).map(n => ({ id: `K${String(n).padStart(2, '0')}`, mhz: 177 + 8 * (n - 5), band: 'VHF III', bw: 8 })),
  ...range(11, 20).map(n => ({ id: `S${n}`, mhz: 233 + 8 * (n - 11), band: T('Sonderkanäle OSB'), bw: 8 })),
  ...range(21, 41).map(n => ({ id: `S${n}`, mhz: 306 + 8 * (n - 21), band: 'Hyperband', bw: 8 })),
  ...range(21, 69).map(n => ({ id: `K${n}`, mhz: 474 + 8 * (n - 21), band: 'UHF', bw: 8 })),
];
// PAL (SPEC `mode: "pal"`): Bildträger MHz – E2–E4 48,25/55,25/62,25; E5–E12 175,25 + 7·(n−5); K21–K69 471,25 + 8·(n−21);
// Kabel S02–S10 112,25 + 7·(n−2), S11–S20 231,25 + 7·(n−11), S21–S41 303,25 + 8·(n−21)
export const PAL_CHANNELS = [
  ...[2, 3, 4].map(n => ({ id: `E${n}`, mhz: [48.25, 55.25, 62.25][n - 2], band: 'VHF I', bw: 7, sys: 'B' })),
  ...range(5, 12).map(n => ({ id: `E${n}`, mhz: 175.25 + 7 * (n - 5), band: 'VHF III', bw: 7, sys: 'B' })),
  ...range(21, 69).map(n => ({ id: `K${n}`, mhz: 471.25 + 8 * (n - 21), band: 'UHF', bw: 8, sys: 'G' })),
  ...range(2, 10).map(n => ({ id: `S${String(n).padStart(2, '0')}`, mhz: 112.25 + 7 * (n - 2), band: T('Kabel S (USB)'), bw: 7, sys: 'B' })),
  ...range(11, 20).map(n => ({ id: `S${n}`, mhz: 231.25 + 7 * (n - 11), band: T('Kabel S (OSB)'), bw: 7, sys: 'B' })),
  ...range(21, 41).map(n => ({ id: `S${n}`, mhz: 303.25 + 8 * (n - 21), band: T('Kabel Hyperband'), bw: 8, sys: 'G' })),
].map(c => ({ ...c, mhz: Math.round(c.mhz * 1000) / 1000, edge: Math.round((c.mhz - 1.25) * 1000) / 1000 }));
export const PAL_SYSTEMS = {
  BG: { name: T('B/G (Westeuropa)'), sound_mhz: 5.5, lines: 625, video_bw_mhz: 5.0, vsb_mhz: 0.75 },
  DK: { name: T('D/K (Osteuropa)'), sound_mhz: 6.5, lines: 625, video_bw_mhz: 6.0, vsb_mhz: 0.75 },
  I: { name: T('I (UK/Irland)'), sound_mhz: 6.0, lines: 625, video_bw_mhz: 5.5, vsb_mhz: 1.25 },
};
export const PAL_PATTERNS = { pal75bars: T('PAL-Farbbalken 75 %'), pal100bars: T('PAL-Farbbalken 100 %'), smptebars: T('SMPTE-Farbbalken'), testsrc2: T('ffmpeg testsrc2 (Zähler)'), rgbtestsrc: T('RGB-Testbild'), grey: T('Graustufentreppe'), black: T('Schwarzbild'), white: T('Weißbild') };
export const dvbChannels = std => std === 'dvbc' ? DVBC_CHANNELS : std === 'pal' ? PAL_CHANNELS : DVBT_CHANNELS;
export const dvbChannelHz = (std, id) => { const c = dvbChannels(std).find(x => x.id === id); return c ? Math.round(c.mhz * 1e6) : null; };

// ---------------------------------------------------------------- Modulationsparameter
export const DVBT = {
  BW: [5, 6, 7, 8], FFT: ['2k', '8k'], CONST: ['QPSK', '16-QAM', '64-QAM'],
  CR: ['1/2', '2/3', '3/4', '5/6', '7/8'], GI: ['1/4', '1/8', '1/16', '1/32'],
};
export const DVBC = { CONST: ['16-QAM', '32-QAM', '64-QAM', '128-QAM', '256-QAM'], SR_PRESETS: [6875000, 6900000, 3450000] };
const BITS = { QPSK: 2, '16-QAM': 4, '32-QAM': 5, '64-QAM': 6, '128-QAM': 7, '256-QAM': 8 };
const frac = s => { const [a, b] = s.split('/').map(Number); return a / b; };

/** DVB-T-Nettobitrate (bit/s), EN 300 744: 6048 (8k) Datenträger je Symbol, Tu = 8192·T, T = 7/(8·BW) µs */
export function dvbtNetRate(p) {
  const bw = Number(p.bandwidth_mhz) || 8;
  const tu = 8192 * 7 / (8 * bw) * 1e-6;             // Nutzsymbol 8k (2k: ¼ Dauer, ¼ Träger → gleiche Rate)
  return 6048 * BITS[p.constellation] * frac(p.code_rate) * (188 / 204) / (tu * (1 + frac(p.guard)));
}
/** DVB-C-Nettobitrate (bit/s): SR · log2(M) · 188/204 */
export function dvbcNetRate(p) { return Number(p.symbol_rate) * BITS[p.constellation] * 188 / 204; }
// ---------------------------------------------------------------- DVB-S / DVB-S2
export const DVBS = {
  SYSTEMS: { dvbs: 'DVB-S (EN 300 421)', dvbs2: 'DVB-S2 (EN 302 307)' },
  FEC_S: ['1/2', '2/3', '3/4', '5/6', '7/8'],
  FEC_S2: { QPSK: ['1/4', '1/3', '2/5', '1/2', '3/5', '2/3', '3/4', '4/5', '5/6', '8/9', '9/10'], '8PSK': ['3/5', '2/3', '3/4', '5/6', '8/9', '9/10'] },
  ROLLOFF: [0.35, 0.25, 0.2], LOF_PRESETS: [9750, 10600, 5150, 10750, 11300],
  SR_PRESETS: [6000000, 27500000, 22000000, 3000000],
  IF_MIN_MHZ: 950, IF_MAX_MHZ: 2150,
};
/** Amateurfunk-DATV: Bänder (MHz), Symbolraten (S/s), Rufzeichen – wie runner/dvb/tables.py */
export const DATV = {
  BANDS: {
    qo100: { label: T('QO-100 Breitband-Uplink'), lo: 2401.5, hi: 2409.5, def: 2406.0, hint: T('Es’hail-2, Downlink = Uplink + 8089,5 MHz') },
    '13cm': { label: '13 cm', lo: 2320, hi: 2450, def: 2330.0, hint: T('terrestrisch, Bandplan beachten') },
    '23cm': { label: '23 cm', lo: 1240, hi: 1300, def: 1255.0, hint: T('terrestrisch, DATV-Segment 1240–1260 MHz') },
    '70cm': { label: '70 cm', lo: 430, hi: 440, def: 437.0, hint: T('nur schmale DATV-Signale (≤ 333 kS/s)') },
    '6cm': { label: '6 cm', lo: 5650, hi: 5850, def: 5665.0, hint: T('terrestrisch') },
  },
  SR_PRESETS: [125000, 250000, 333000, 500000, 1000000, 1500000, 2000000],
  SR_MIN: 25000, SR_MAX: 3500000, QO100_DL_OFFSET_MHZ: 8089.5,
  CALLSIGN_RE: /^(?=.*\d)[A-Z0-9]{1,3}\d[A-Z0-9]{0,3}[A-Z](\/[A-Z0-9]{1,4})?$/,
};
export const isHam = c => c?.dvbs?.use === 'ham';
/** DATV: Programme an die kleine Nutzrate anpassen (H.264 SD, AAC mono 32 kbit/s, Video = Rest) und Rufzeichen als Anbieter */
export function fitDatvPrograms(c) {
  const progs = c.dvb?.programs || [];
  if (!progs.length) return c;
  const net = netRate(c), tv = progs.filter(p => p.type === 'tv');
  for (const p of progs) {
    p.audio.codec = 'aac'; p.audio.bitrate_kbps = 32;
    if (c.dvbs.callsign) p.provider = String(c.dvbs.callsign).split('/')[0];
    if (p.type === 'tv') { p.video.codec = p.video.codec === 'hevc' ? 'hevc' : 'h264'; p.video.resolution = 'sd'; p.video.hdr = 'sdr'; }
  }
  const audio = progs.reduce((a, p) => a + p.audio.bitrate_kbps * 1000 * (1 + PES_OVERHEAD), 0);
  const share = (net * 0.94 - siOverhead(net) - audio) / Math.max(1, tv.length) / (1 + PES_OVERHEAD);
  for (const p of tv) p.video.bitrate_kbps = Math.max(100, Math.floor(share / 1000 / 10) * 10);
  return c;
}
/** Samples/Symbol für USB-IQ wie runner (≥ 600 kS/s am DAC) */
export const datvSps = sr => (sr * 2 >= 600000 ? 2 : sr * 4 >= 600000 ? 4 : 8);
/** belegte Bandbreite (Hz) */
export const datvBw = d => Number(d.symbol_rate) * (1 + Number(d.rolloff));
/** Sat-ZF (Hz) aus Transponder und LOF */
export const satIfHz = p => Math.round((Number(p.transponder_mhz) - Number(p.lof_mhz)) * 1e6);
/** DVB-S: SR · 2 · FEC · 188/204; DVB-S2 (Normal-Frame, Pilots): SR · bits · FEC · ≈ 0,97 (BBFRAME/PL-Overhead, Näherung) */
export function dvbsNetRate(p) {
  const fec = frac(p.fec);
  if (p.system === 'dvbs2') {
    const KBCH = { '1/4': 16008, '1/3': 21408, '2/5': 25728, '1/2': 32208, '3/5': 38688, '2/3': 43040, '3/4': 48408, '4/5': 51648, '5/6': 53840, '8/9': 57472, '9/10': 58192 };
    const ns = 64800 / (p.modulation === '8PSK' ? 3 : 2), pl = 90 + ns + (p.pilots ? 36 * Math.floor((ns / 90 - 1) / 16) : 0);
    return KBCH[p.fec] ? Number(p.symbol_rate) * (KBCH[p.fec] - 80) / pl : 0;
  }
  return Number(p.symbol_rate) * 2 * fec * 188 / 204;
}
export function netRate(cfg) { const s = standardOf(cfg); return s === 'dvbt' ? dvbtNetRate(cfg.dvbt) : s === 'dvbc' ? dvbcNetRate(cfg.dvbc) : s === 'dvbs' ? dvbsNetRate(cfg.dvbs) : 0; }

/** DATV am PC: kleinste Samples/Symbol ≥ 2, damit der Pluto ≥ 2,1 MS/s bekommt (wie runner/dvb/tables.py ham_sps) */
export const hamSps = sr => Math.max(2, Math.ceil(2100000 / sr));

/** Abtastrate am Pluto (S/s) je Standard */
export function requiredSampleRate(cfg) {
  const s = standardOf(cfg);
  if (s === 'dvbt') return Math.round((Number(cfg.dvbt.bandwidth_mhz) || 8) * 8e6 / 7);
  if (s === 'dvbc') return (Number(cfg.dvbc.sps) || 2) * Number(cfg.dvbc.symbol_rate);
  if (s === 'dvbs') { const sr = Number(cfg.dvbs.symbol_rate); return cfg.dvbs.use === 'ham' ? hamSps(sr) * sr : 2 * sr; }
  if (s === 'pal') return cfg.pal?.live ? (Number(cfg.pal?.live_fs) || 7000000) : (Number(cfg.pal?.fs) || 8000000);
  return Number(cfg.rf.sample_rate) || 4096000;
}
export const USB_MAX_SPS = 7150000;
/** Nötiger Ausgabeweg: USB-Streaming (PC) oder Modulator/zyklischer Puffer auf dem Pluto */
export function requiredPath(cfg) {
  const s = standardOf(cfg);
  const sps = requiredSampleRate(cfg);
  if (s === 'dvbt' && Number(cfg.dvbt.bandwidth_mhz) >= 7) return { path: 'pluto', sps, reason: T('DVB-T {bw} MHz braucht {sps} MS/s – nur Modulator/Loop auf dem Pluto (USB max. 7,15 MS/s)', { bw: cfg.dvbt.bandwidth_mhz, sps: (sps / 1e6).toFixed(3) }) };
  if (s === 'dvbs') return sps > USB_MAX_SPS ? { path: 'pluto', sps, reason: T('{sr} MS/s Symbolrate × 2 = {sps} MS/s – Modulator auf dem Pluto (USB max. 7,15 MS/s)', { sr: (cfg.dvbs.symbol_rate / 1e6).toFixed(3), sps: (sps / 1e6).toFixed(2) }) }
    : { path: 'usb', sps, reason: T('{sps} MS/s – USB-Streaming möglich', { sps: (sps / 1e6).toFixed(2) }) };
  if (s === 'dvbc' && sps > USB_MAX_SPS) return { path: 'pluto', sps, reason: T('{sr} MBd × 2 = {sps} MS/s – nur zyklischer Puffer auf dem Pluto (USB max. 7,15 MS/s)', { sr: (cfg.dvbc.symbol_rate / 1e6).toFixed(3), sps: (sps / 1e6).toFixed(2) }) };
  if (s === 'pal') return cfg.pal?.live ? { path: 'usb', sps, reason: T('PAL live (bewegtes Bild) per USB mit {sps} MS/s (live_fs)', { sps: (sps / 1e6).toFixed(3) }) }
    : { path: 'pluto', sps, reason: T('PAL {sys}: Bildträger −1,25 … +{top} MHz, {sps} MS/s – zyklischer Analog-Loop (≈ 160 ms = 4 Bilder) auf dem Pluto', { sys: PAL_SYSTEMS[cfg.pal?.system]?.name || 'B/G', top: (PAL_SYSTEMS[cfg.pal?.system]?.sound_mhz || 5.5) + 0.25, sps: (sps / 1e6).toFixed(3) }) };
  if (s === 'dvbt') return { path: 'usb', sps, reason: T('DVB-T {bw} MHz = {sps} MS/s – USB-Streaming möglich ({pct} % der Reserve)', { bw: cfg.dvbt.bandwidth_mhz, sps: (sps / 1e6).toFixed(3), pct: Math.round(sps / USB_MAX_SPS * 100) }) };
  if (s === 'dvbc') return { path: 'usb', sps, reason: T('{sps} MS/s – USB-Streaming möglich (manueller Suchlauf am Receiver)', { sps: (sps / 1e6).toFixed(2) }) };
  return { path: 'usb', sps, reason: T('{sps} MS/s – USB-Streaming (PC)', { sps: (sps / 1e6).toFixed(3) }) };
}

// ---------------------------------------------------------------- Video / Programme
export const VIDEO = {
  CODECS: { mpeg2: 'MPEG-2 (MP@ML/HL)', h264: 'H.264 / AVC', hevc: 'HEVC / H.265' },
  RES: { sd: 'SD 720×576 (25 Hz)', hd720: 'HD 1280×720p (25 Hz)', hd: 'HD 1920×1080p (25 Hz)', uhd: 'UHD 3840×2160p (25/50 Hz)' },
  AUDIO: { mp2: 'MPEG-1 Layer II', aac: 'AAC (ADTS/LATM)', ac3: 'AC-3 (Dolby Digital)' },
  SRC: { file: T('Datei'), testpattern: T('Testbild'), youtube: 'YouTube', playlist: T('Video-Playlist'), capture: T('Capture/Webcam'), screen: T('Bildschirm') },
  ENCODERS: { cpu: 'CPU (libx264/libx265/mpeg2video)', nvenc: 'GPU (NVIDIA NVENC)' },
  HDR: { sdr: 'SDR (BT.709)', hdr10: 'HDR10 (PQ, BT.2020, Main10)', hlg: 'HLG (BT.2100, Main10)' },
  COOKIES: { none: T('keine'), chrome: T('aus {browser}', { browser: 'Chrome' }), firefox: T('aus {browser}', { browser: 'Firefox' }), edge: T('aus {browser}', { browser: 'Edge' }), file: 'cookies.txt' },
  PATTERNS: { smptebars: T('SMPTE-Farbbalken'), smptehdbars: T('SMPTE-HD-Farbbalken'), pal75bars: T('PAL-Farbbalken 75 %'), testsrc2: T('ffmpeg testsrc2 (Zähler)'), testsrc: 'ffmpeg testsrc', rgbtestsrc: T('RGB-Testbild') },
  VIDEO_BR: { mpeg2: { sd: [2000, 3500, 5000, 8000], hd: [8000, 12000, 15000, 20000], hd720: [6000, 8000, 10000, 12000] },
    h264: { sd: [1000, 1500, 2500, 4000], hd: [4000, 6000, 8000, 12000], hd720: [3000, 4000, 6000, 8000], uhd: [15000, 20000, 25000] },
    hevc: { sd: [800, 1200, 2000], hd720: [2000, 3000, 4000], hd: [3000, 5000, 7000, 10000], uhd: [10000, 14000, 18000, 25000] } },
  AUDIO_BR: { mp2: [64, 128, 192, 256, 384], aac: [48, 64, 96, 128, 192], ac3: [128, 192, 256, 384, 448] },
};
export const SERVICE_TYPES = { tv: T('Fernsehen (0x01 SD / 0x19 HD)'), radio: 'Radio (0x02)' };
export const SI_OVERHEAD_BPS = 60000;        // PAT/PMT/SDT/NIT/EIT/TDT, grob
/** SI-Reserve wie runner/dvb/config.py: bei kleinen Nutzraten (DATV) seltener wiederholt */
export const siOverhead = net => (net < 2e6 ? Math.max(25000, 0.08 * net) + 4000 : SI_OVERHEAD_BPS);
export const PES_OVERHEAD = 0.03;            // PES-/TS-Header je Elementarstrom

export function programRate(p) {
  if (p.type === 'radio') return Math.round(p.audio.bitrate_kbps * 1000 * (1 + PES_OVERHEAD));
  return Math.round((p.video.bitrate_kbps + p.audio.bitrate_kbps) * 1000 * (1 + PES_OVERHEAD));
}
/** TS-Belegung: Nettorate gegen Programme + SI + Nullpakete */
export function tsBudget(cfg) {
  const net = netRate(cfg);
  const items = (cfg.dvb?.programs || []).map(p => ({ key: p.key, name: p.name, bps: programRate(p), prg: p }));
  const si = siOverhead(net);
  const used = items.reduce((a, b) => a + b.bps, 0) + si;
  return { net, items, si, used, nulls: Math.max(0, net - used), over: used > net, pct: net ? used / net : 0 };
}

// ---------------------------------------------------------------- Defaults
const audioSrc = (o = {}) => ({ kind: 'tone', file: '', freq_hz: 1000, level_dbfs: -18, left_hz: 1000, right_hz: 400,
  sweep_from_hz: 20, sweep_to_hz: 15000, sweep_s: 10, loop: true, playlist: '', shuffle: false, crossfade_s: 0, normalize: true, dls_from_tags: true, ...o });

export function defaultFM() {
  return {
    freq_mhz: 100.0, deviation_khz: 75, pilot_khz: 6.75, preemphasis_us: 50, stereo: true,
    limiter: { enable: true, threshold: 1.0, release_db_s: 3, clip: true }, mpx_power_dbr: null, audio_gain_db: 0,
    audio: { source: audioSrc() },
    rds: { enable: true, pi: '0xD3C4', ps: 'DABLAB', rt: 'DAB-Lab UKW-Testsender – RDS aktiv', rtplus: [], pty: 10, tp: true, ta: false, ms: true,
      di: { stereo: true, artificial_head: false, compressed: false, dynamic_pty: false },
      af: [], af_method: 'A', af_regional: [], ecc: '0xE0', lic: null, pin: null, ct: true, utc_offset_halfhours: 'auto', ct_latency_s: 0,
      ptyn: '', lps: '', eon: [], level_khz: 2.0, phase_deg: 0, group_version: 'A', rt_version: 'A',
      rates: { '1a': 60, '3a': 20, '10a': 10, '11a': 30, '14a': 8, '15a': 20 }, rt_from_tags: true },
    second_carrier: { enable: false, offset_khz: 200, level_db: -10, deviation_khz: 75, stereo: true, preemphasis_us: 50, pilot_khz: 6.75,
      audio: { source: audioSrc({ kind: 'noise' }) }, rds: { enable: false, pi: '0xD3C5', ps: 'ZWEITER' } },
  };
}
/** RT+-Tag lesen/schreiben (Liste [{type, text}]) */
export const RTPLUS_TYPES = ['ITEM.TITLE', 'ITEM.ARTIST', 'ITEM.ALBUM', 'STATIONNAME.LONG', 'PROGRAMME.NOW', 'PROGRAMME.HOMEPAGE', 'INFO.NEWS', 'PROGRAMME.FREQUENCY'];
export const rtplusGet = (r, type) => (r.rtplus || []).find(t => t && t.type === type)?.text || '';
export function rtplusSet(r, type, text) {
  r.rtplus = (r.rtplus || []).filter(t => t && t.type !== type);
  if (text) r.rtplus.push({ type, text });
}
/** Audiohub nach SPEC: Gesamthub − Pilot − RDS */
export function fmAudioDeviation(f) { return Number(f.deviation_khz) - (f.stereo ? Number(f.pilot_khz) : 0) - (f.rds.enable ? Number(f.rds.level_khz) : 0); }
export const defaultDvbt = () => ({ bandwidth_mhz: 8, fft: '8k', constellation: '64-QAM', code_rate: '2/3', guard: '1/32', cell_id: 0, hierarchy: 'none' });
export const defaultDvbc = () => ({ symbol_rate: 6900000, constellation: '256-QAM', sps: 2 });
export const defaultDvbs = () => ({ use: 'sat', system: 'dvbs', transponder_mhz: 11300, polarization: 'H', lof_mhz: 9750, tone_22khz: false, symbol_rate: 6000000, fec: '3/4', modulation: 'QPSK', rolloff: 0.35, pilots: false,
  ham_band: 'qo100', ham_freq_mhz: 2406.0, callsign: '' });
export const defaultPal = () => ({
  system: 'BG', fs: 8000000, live_fs: 7000000, live: false, offset_hz: 'auto', level: 0.9,
  image: { kind: 'testpattern', pattern: 'pal75bars', file: '', url: '', text: 'DABLAB Test', text2: '', timestamp: true },
  sound: { kind: 'tone', freq_hz: 1000, deviation_hz: 50000, level_db: -13, preemphasis_us: 50, tone_level: 1.0, file: '' },
});
/** Migration älterer Dashboard-Entwürfe (video/audio) auf das SPEC-Schema (image/sound) */
function migratePal(p0) {
  const p = { ...p0 };
  if (p.video && !p.image) {
    const v = p.video, src = v.source || {};
    const kind = src.kind === 'image' ? 'file' : src.kind === 'file' ? 'video' : src.kind || 'testpattern';
    p.image = { kind, pattern: { colorbars: 'pal75bars', pm5544: 'pal75bars', multiburst: 'testsrc2' }[src.pattern] || src.pattern || 'pal75bars', file: src.kind === 'image' ? src.image : src.file || '', url: src.url || '', text: v.text_top ?? '', text2: v.text_bottom ?? '', timestamp: true };
    if (v.level_db != null) p.level = Math.round(Math.pow(10, v.level_db / 20) * 0.9 * 100) / 100;
  }
  if (p.audio && !p.sound) { const a = p.audio, src = a.source || {}; p.sound = { kind: src.kind || 'tone', freq_hz: src.freq_hz ?? 1000, deviation_hz: (a.deviation_khz ?? 50) * 1000, level_db: a.sound_level_db ?? -13, preemphasis_us: a.preemphasis_us ?? 50, tone_level: 1.0, file: src.file || '' }; }
  if (p.sample_rate && !p.fs) p.fs = p.sample_rate;
  delete p.video; delete p.audio; delete p.sample_rate;
  return p;
}
/** Bildträger und Tonträger in Hz */
export function palCarriers(cfg) { const v = Number(cfg.rf.freq_hz); return { vision: v, sound: v + (PAL_SYSTEMS[cfg.pal.system]?.sound_mhz || 5.5) * 1e6 }; }
/** Automatischer Basisband-Versatz laut SPEC (Bildträger bei −offset) */
export function palAutoOffset(p) { const fs = p.live ? p.live_fs : p.fs; return p.system === 'DK' ? 2675000 : p.system === 'I' ? 2175000 : (Number(fs) === 7000000 ? 2100000 : 2250000); }
export function newProgram(key, n = 1) {
  return {
    key, name: `DAB-Lab TV ${n}`, provider: 'DAB-Lab', sid: hex(0x1000 + n, 4), lcn: n, type: 'tv',
    video: { source: { kind: 'testpattern', pattern: 'smptebars', file: '', url: '', playlist: '', loop: true, cookies: 'none', cookies_file: '', device: '', audio_device: '', size: '1920x1080', fps: 25, display: '', region: '', cursor: true },
      codec: 'mpeg2', resolution: 'sd', bitrate_kbps: 3500, encoder: 'cpu', hdr: 'sdr' },
    audio: { codec: 'mp2', bitrate_kbps: 192, source: audioSrc() },
    eit: { title: `Testsendung ${n}`, description: 'DAB-Lab Laborsender – Prüfprogramm', duration_min: 60 },
  };
}
export const defaultDvb = () => ({ programs: [newProgram('prg1', 1)], si: { network_id: '0xFF16', network_name: 'DAB-Lab Netz', onid: '0xFF16', tsid: '0x0016', lcn: true, eit: true, tdt: true, private_data: 'eacem' } });

/** Videobitraten so setzen, dass der TS passt (85 % der Nettorate auf die Programme verteilt) */
export function fitPrograms(c) {
  const net = netRate(c), n = c.dvb.programs.length;
  const share = (net - SI_OVERHEAD_BPS) * 0.85 / n;
  for (const p of c.dvb.programs) { if (p.type === 'tv') p.video.bitrate_kbps = Math.max(300, Math.floor((share / (1 + PES_OVERHEAD) - p.audio.bitrate_kbps * 1000) / 1000 / 100) * 100); }
  return c;
}

/** Füllt die standardspezifischen Teile auf (wie normalizeConfig für DAB) */
export function normalizeStd(c) {
  const dfm = defaultFM();
  const f0 = c.fm || {};
  // Migration älterer Entwürfe (boolescher Limiter, `second`, di_stereo, rtplus-Objekt)
  if (typeof f0.limiter === 'boolean') f0.limiter = { ...dfm.limiter, enable: f0.limiter };
  if (f0.second && !f0.second_carrier) { const sc = f0.second; f0.second_carrier = { ...sc, audio: { source: sc.source || sc.audio?.source }, rds: sc.rds }; }
  if (f0.audio && f0.audio.gain_db != null && f0.audio_gain_db == null) f0.audio_gain_db = f0.audio.gain_db;
  c.fm = { ...dfm, ...f0 };
  c.fm.limiter = { ...dfm.limiter, ...(f0.limiter || {}) };
  c.fm.audio = { source: { ...dfm.audio.source, ...((f0.audio || {}).source || {}) } };
  const r0 = f0.rds || {};
  if (r0.di_stereo != null && r0.di == null) r0.di = { ...dfm.rds.di, stereo: !!r0.di_stereo };
  if (typeof r0.di === 'number') r0.di = { stereo: !!(r0.di & 1), artificial_head: !!(r0.di & 2), compressed: !!(r0.di & 4), dynamic_pty: !!(r0.di & 8) };
  if (r0.rtplus && !Array.isArray(r0.rtplus)) { const o = r0.rtplus; r0.rtplus = [['ITEM.ARTIST', o.artist], ['ITEM.TITLE', o.title], ['ITEM.ALBUM', o.album], ['STATIONNAME.LONG', o.station], ['PROGRAMME.NOW', o.program], ['PROGRAMME.HOMEPAGE', o.url]].filter(([, t]) => t).map(([type, text]) => ({ type, text })); if (o.auto != null) r0.rt_from_tags = !!o.auto; }
  if (r0.rt_ab != null && r0.rt_version == null) r0.rt_version = 'A';
  c.fm.rds = { ...dfm.rds, ...r0 };
  c.fm.rds.di = { ...dfm.rds.di, ...(r0.di || {}) };
  c.fm.rds.rates = { ...dfm.rds.rates, ...(r0.rates || {}) };
  c.fm.rds.af = c.fm.rds.af || []; c.fm.rds.af_regional = c.fm.rds.af_regional || []; c.fm.rds.eon = c.fm.rds.eon || []; c.fm.rds.rtplus = (c.fm.rds.rtplus || []).filter(t => t && typeof t === 'object');
  const s0 = f0.second_carrier || {};
  c.fm.second_carrier = { ...dfm.second_carrier, ...s0 };
  c.fm.second_carrier.audio = { source: { ...dfm.second_carrier.audio.source, ...((s0.audio || {}).source || {}) } };
  c.fm.second_carrier.rds = { ...dfm.second_carrier.rds, ...(s0.rds || {}) };
  // Frequenz: rf.freq_hz ist führend; fm.freq_mhz wird gespiegelt (SPEC: freq_mhz → rf.freq_hz)
  if (standardOf(c) === 'fm') {
    const inBand = c.rf.freq_hz >= FM_MIN_MHZ * 1e6 && c.rf.freq_hz <= FM_MAX_MHZ * 1e6;
    if (!inBand && f0.freq_mhz) c.rf.freq_hz = Math.round(f0.freq_mhz * 1e6);
    c.fm.freq_mhz = Math.round(c.rf.freq_hz / 1e3) / 1e3;
  }
  c.dvbt = { ...defaultDvbt(), ...(c.dvbt || {}) };
  c.dvbc = { ...defaultDvbc(), ...(c.dvbc || {}) };
  c.dvbs = { ...defaultDvbs(), ...(c.dvbs || {}) };
  // Der Server speichert für den Runner „horizontal“/„vertical“ – das Dashboard arbeitet mit H/V
  c.dvbs.polarization = { horizontal: 'H', vertical: 'V', h: 'H', v: 'V' }[String(c.dvbs.polarization).toLowerCase()] || c.dvbs.polarization;
  const dp = defaultPal(), p0 = migratePal(c.pal || {});
  c.pal = { ...dp, ...p0, image: { ...dp.image, ...(p0.image || {}) }, sound: { ...dp.sound, ...(p0.sound || {}) } };
  const dd = defaultDvb();
  c.dvb = { ...dd, ...(c.dvb || {}) };
  c.dvb.si = { ...dd.si, ...(c.dvb.si || {}) };
  c.dvb.programs = (c.dvb.programs || []).map((p, i) => {
    const t = newProgram(p.key || `prg${i + 1}`, i + 1);
    const r = { ...t, ...p };
    r.video = { ...t.video, ...(p.video || {}) }; r.video.source = { ...t.video.source, ...((p.video || {}).source || {}) };
    r.audio = { ...t.audio, ...(p.audio || {}) }; r.audio.source = { ...t.audio.source, ...((p.audio || {}).source || {}) };
    r.eit = { ...t.eit, ...(p.eit || {}) };
    return r;
  });
  if (!c.dvb.programs.length) c.dvb.programs = [newProgram('prg1', 1)];
  return c;
}

/** Backoff je Standard: Standardwert und Untergrenze (OFDM/QAM brauchen Abstand für die Spitzen, FM hat konstante Hüllkurve) */
export const BACKOFF = { dab: [12, 10], dvbt: [12, 10], dvbc: [10, 9], dvbs: [6, 4], pal: [6, 6], fm: [3, 0] };

/** Standard umschalten: mode setzen, HF-Kanal auf einen sinnvollen Startwert, falls der alte nicht passt */
export function setStandard(cfg, std) {
  const cur = standardOf(cfg);
  if (cur === 'dab') cfg.dab_mode = cfg.mode;
  if (std === 'dab') { cfg.mode = [1, 2, 3, 4].includes(cfg.dab_mode) ? cfg.dab_mode : 1; delete cfg.dab_mode; }
  else cfg.mode = std;
  if (std !== cur) {
    const rf = cfg.rf;
    if (std === 'fm') { rf.channel = 'fm'; if (!(rf.freq_hz >= FM_MIN_MHZ * 1e6 && rf.freq_hz <= FM_MAX_MHZ * 1e6)) rf.freq_hz = Math.round((cfg.fm?.freq_mhz || 100.0) * 1e6); if (cfg.fm) cfg.fm.freq_mhz = rf.freq_hz / 1e6; }
    else if (std === 'dvbt') { rf.channel = 'K21'; rf.freq_hz = 474e6; }
    else if (std === 'dvbc') { rf.channel = 'S21'; rf.freq_hz = 306e6; }
    else if (std === 'dvbs') { const d = cfg.dvbs || defaultDvbs(); if (d.use === 'ham') { rf.channel = 'ham'; rf.freq_hz = Math.round(d.ham_freq_mhz * 1e6); } else { rf.channel = 'sat'; rf.freq_hz = satIfHz(d); } }
    else if (std === 'pal') { rf.channel = 'K21'; rf.freq_hz = 471.25e6; }
    else { rf.channel = '6C'; rf.freq_hz = 185360000; }
    // Abtastrate: DAB+/UKW laufen per USB mit 2,1–7 MS/s (PAL nimmt 8 MS/s mit, DVB rechnet der Server selbst)
    if (std === 'fm' || (std === 'dab' && !(rf.sample_rate >= 2.1e6 && rf.sample_rate <= 7e6))) rf.sample_rate = 4096000;   // UKW fest 4,096 MS/s
    // Backoff des alten Standards nicht übernehmen, wenn er für den neuen zu klein (bzw. bei FM unnötig groß) ist
    const [def, min] = BACKOFF[std] || BACKOFF.dab;
    if (!(Number(rf.backoff_db) >= min) || (std === 'fm' && rf.backoff_db > 6)) rf.backoff_db = def;
  }
  return cfg;
}

// ---------------------------------------------------------------- RDS-Hilfen
export const RDS_PTY = PTY;   // RDS-Tabelle (Europa) entspricht der DAB-Liste 0–31
export function rdsCountry(pi, ecc) {
  const n = parseHex(pi); if (n == null) return [];
  const nib = n.toString(16).toUpperCase().padStart(4, '0')[0];
  return COUNTRIES.filter(c => c.cids.includes(nib) && (!ecc || c.ecc.toUpperCase() === String(ecc).toUpperCase()));
}
export const RDS_CHARS_OK = /^[\x20-\x7E -ÿ€]*$/;   // Annex-E-Zeichensatz näherungsweise (Latin-1 + Euro)

// ---------------------------------------------------------------- Validierung
export function validateStd(c, add) {
  const std = standardOf(c);
  if (std === 'fm') {
    const f = c.fm, rf = c.rf;
    const mhz = rf.freq_hz / 1e6;
    if (!(mhz >= FM_MIN_MHZ && mhz <= FM_MAX_MHZ)) add('rf.freq_hz', T('UKW: Frequenz {mhz} MHz außerhalb 87,5–108 MHz', { mhz: mhz.toFixed(3) }));
    if (!(f.deviation_khz >= 5 && f.deviation_khz <= 150)) add('fm.deviation_khz', T('Gesamthub 5–150 kHz (Nennwert 75)'));
    if (!(f.pilot_khz >= 0 && f.pilot_khz <= 10)) add('fm.pilot_khz', T('Pilothub 0–10 kHz (Nennwert 6,75)'));
    if (![0, 50, 75].includes(Number(f.preemphasis_us))) add('fm.preemphasis_us', T('Preemphasis 50 µs, 75 µs oder aus'));
    if (f.mpx_power_dbr != null && f.mpx_power_dbr !== '' && !(f.mpx_power_dbr >= -12 && f.mpx_power_dbr <= 12)) add('fm.mpx_power_dbr', T('MPX-Leistung −12…+12 dBr oder leer (aus)'));
    if (!(f.audio_gain_db >= -40 && f.audio_gain_db <= 20)) add('fm.audio_gain_db', T('Audio-Gain −40…+20 dB'));
    if (f.limiter.enable && !(f.limiter.threshold > 0 && f.limiter.threshold <= 1.5)) add('fm.limiter.threshold', T('Limiter-Schwelle 0,1–1,5 (1,0 = 100 % Hub)'));
    if (fmAudioDeviation(f) < 5) add('fm.deviation_khz', T('Audiohub nur {khz} kHz (Gesamthub − Pilot − RDS) – Gesamthub erhöhen', { khz: fmAudioDeviation(f).toFixed(2) }));
    if (f.audio.source.kind === 'file' && !f.audio.source.file) add('fm.audio.source.file', T('UKW: Audiodatei fehlt'));
    if (f.audio.source.kind === 'playlist' && !f.audio.source.playlist) add('fm.audio.source.playlist', T('UKW: Playlist fehlt'));
    const sc = f.second_carrier;
    if (sc.enable) {
      if (!(Math.abs(sc.offset_khz) >= 20 && Math.abs(sc.offset_khz) <= 1800) && sc.offset_khz !== 0) add('fm.second_carrier.offset_khz', T('Zweiter Träger: Versatz ±20…±1800 kHz (0 = Gleichkanal)'));
      if (!(sc.level_db <= 0 && sc.level_db >= -60)) add('fm.second_carrier.level_db', T('Zweiter Träger: Pegel 0…−60 dB'));
      if (sc.rds.enable && !/^0x[0-9a-f]{4}$/i.test(sc.rds.pi || '')) add('fm.second_carrier.rds.pi', T('Zweiter Träger: PI 4-stellig hex'));
      if (sc.audio.source.kind === 'file' && !sc.audio.source.file) add('fm.second_carrier.audio.source.file', T('Zweiter Träger: Audiodatei fehlt'));
    }
    const r = f.rds;
    if (r.enable) {
      if (!/^0x[0-9a-f]{4}$/i.test(r.pi || '')) add('fm.rds.pi', T('PI: 4-stellig hex, z. B. 0xD3C4'));
      if ([...(r.ps || '')].length > 8) add('fm.rds.ps', T('PS: maximal 8 Zeichen'));
      if (!r.ps) add('fm.rds.ps', T('PS fehlt'));
      if (!RDS_CHARS_OK.test(r.ps || '')) add('fm.rds.ps', T('PS: Zeichen außerhalb des RDS-Zeichensatzes (Annex E)'));
      if ([...(r.rt || '')].length > 64) add('fm.rds.rt', T('Radiotext: maximal 64 Zeichen'));
      if ([...(r.ptyn || '')].length > 8) add('fm.rds.ptyn', T('PTYN: maximal 8 Zeichen'));
      if (new TextEncoder().encode(r.lps || '').length > 32) add('fm.rds.lps', T('LPS: maximal 32 Byte UTF-8'));
      if (!(r.pty >= 0 && r.pty <= 31)) add('fm.rds.pty', 'PTY 0–31');
      if (!r.tp && r.ta) add('fm.rds.ta', T('TA ohne TP ist ungültig (nur EON-Bedeutung)'));
      if ((r.af || []).length > 25) add('fm.rds.af', T('AF-Liste: maximal 25 Frequenzen'));
      (r.af || []).forEach(a => { if (!(a >= FM_MIN_MHZ && a <= FM_MAX_MHZ)) add('fm.rds.af', T('AF {mhz} MHz außerhalb des Bands', { mhz: a })); });
      if (!['A', 'B'].includes(r.af_method)) add('fm.rds.af_method', T('AF-Methode A oder B'));
      if (r.ecc && !/^0x[0-9a-f]{2}$/i.test(r.ecc)) add('fm.rds.ecc', T('ECC: 2-stellig hex'));
      if (r.pin && !/^\d{2}:\d{2}:\d{2}$/.test(r.pin)) add('fm.rds.pin', T('PIN: Format TT:HH:MM'));
      if (!(r.level_khz >= 0 && r.level_khz <= 7.5)) add('fm.rds.level_khz', T('RDS-Hub 0–7,5 kHz (Nennwert 2,0)'));
      if (![0, 90].includes(Number(r.phase_deg))) add('fm.rds.phase_deg', T('RDS-Phase 0° oder 90°'));
      if ((r.rtplus || []).some(t => !t.type || [...String(t.text || '')].length > 64)) add('fm.rds.rtplus', T('RT+: Typ fehlt oder Text zu lang'));
      (r.eon || []).forEach((e, i) => {
        if (!/^0x[0-9a-f]{4}$/i.test(e.pi || '')) add(`fm.rds.eon.${i}.pi`, T('EON {n}: PI 4-stellig hex', { n: i + 1 }));
        if ([...(e.ps || '')].length > 8) add(`fm.rds.eon.${i}.ps`, T('EON {n}: PS maximal 8 Zeichen', { n: i + 1 }));
      });
    }
  }
  if (std === 'pal') {
    const p = c.pal;
    if (!PAL_SYSTEMS[p.system]) add('pal.system', T('System B/G, D/K oder I'));
    if (![7000000, 8000000].includes(Number(p.fs))) add('pal.fs', T('Schleifen-Abtastrate 7 oder 8 MS/s'));
    if (!(c.rf.freq_hz >= 45e6 && c.rf.freq_hz <= 870e6)) add('rf.freq_hz', T('PAL: Bildträger 45–870 MHz'));
    if (p.offset_hz !== 'auto' && (!(Number(p.offset_hz) >= 1e6 && Number(p.offset_hz) <= 3.5e6) || Number(p.offset_hz) % 25 !== 0)) add('pal.offset_hz', T('Versatz 1,0–3,5 MHz, Vielfaches von 25 Hz (oder auto)'));
    if (!(p.level > 0.1 && p.level <= 1)) add('pal.level', T('Pegel 0,1–1,0 (Spitze relativ zur Vollaussteuerung)'));
    const k = p.image.kind;
    if (['file', 'video'].includes(k) && !p.image.file) add('pal.image.file', k === 'file' ? T('PAL: Bilddatei fehlt') : T('PAL: Videodatei fehlt'));
    if (k === 'youtube' && !p.image.file && !p.image.url) add('pal.image.url', T('PAL: YouTube-URL fehlt'));
    if ([...(p.image.text || '')].length > 40) add('pal.image.text', T('Text 1: maximal 40 Zeichen'));
    if ([...(p.image.text2 || '')].length > 40) add('pal.image.text2', T('Text 2: maximal 40 Zeichen'));
    if (!['off', 'silence'].includes(p.sound.kind)) {
      if (!(p.sound.deviation_hz >= 1000 && p.sound.deviation_hz <= 75000)) add('pal.sound.deviation_hz', T('Ton-Hub 1–75 kHz (Nenn ±50)'));
      if (p.sound.kind === 'file' && !p.sound.file) add('pal.sound.file', T('PAL: Tondatei fehlt'));
    }
    if (!(p.sound.level_db >= -30 && p.sound.level_db <= -3)) add('pal.sound.level_db', T('Ton/Bild −30…−3 dB (B/G −13 dB)'));
  }
  if (std === 'dvbs') {
    const d = c.dvbs;
    if (!DVBS.SYSTEMS[d.system]) add('dvbs.system', T('System DVB-S oder DVB-S2'));
    if (d.use === 'ham') {
      const b = DATV.BANDS[d.ham_band];
      if (!DATV.CALLSIGN_RE.test(String(d.callsign || '').toUpperCase())) add('dvbs.callsign', T('Rufzeichen fehlt oder ungültig (z. B. DL1ABC) – Pflicht im Amateurfunk'));
      if (!b) add('dvbs.ham_band', T('Band wählen'));
      else {
        const half = datvBw(d) / 2e6, f = Number(d.ham_freq_mhz);
        if (!(f - half >= b.lo && f + half <= b.hi)) add('dvbs.ham_freq_mhz', T('{f} MHz ± {khz} kHz liegt nicht vollständig in {band} ({lo}–{hi} MHz)', { f: f.toFixed(3), khz: (half * 1e3).toFixed(0), band: b.label, lo: b.lo, hi: b.hi }));
        const q = DATV.BANDS.qo100;
        if (d.ham_band !== 'qo100' && f + half > q.lo && f - half < q.hi) add('dvbs.ham_freq_mhz', T('Überschneidet den QO-100-Breitband-Uplink – dafür Band „QO-100“ wählen'));
      }
      if (Math.abs(c.rf.freq_hz - Math.round(Number(d.ham_freq_mhz) * 1e6)) > 1000) add('rf.freq_hz', T('HF-Frequenz {rf} MHz ≠ DATV-Frequenz {f} MHz', { rf: (c.rf.freq_hz / 1e6).toFixed(3), f: Number(d.ham_freq_mhz).toFixed(3) }));
      if (!(d.symbol_rate >= DATV.SR_MIN && d.symbol_rate <= DATV.SR_MAX)) add('dvbs.symbol_rate', `DATV: ${DATV.SR_MIN / 1000} kS/s … ${DATV.SR_MAX / 1e6} MS/s`);
      const cs = String(d.callsign || '').toUpperCase().split('/')[0];
      if (cs && !(c.dvb?.programs || []).some(p => `${p.name} ${p.provider}`.toUpperCase().includes(cs))) add('dvb.programs', T('Rufzeichen {cs} muss im Programm- oder Anbieternamen stehen (Stationskennung)', { cs }));
    } else {
    if (!['H', 'V'].includes(d.polarization)) add('dvbs.polarization', T('Polarisation H oder V'));
    const ifm = (d.transponder_mhz - d.lof_mhz);
    if (!(ifm >= DVBS.IF_MIN_MHZ && ifm <= DVBS.IF_MAX_MHZ)) add('dvbs.transponder_mhz', T('Sat-ZF {mhz} MHz außerhalb 950–2150 MHz (Transponder − LOF)', { mhz: ifm.toFixed(0) }));
    if (Math.abs(c.rf.freq_hz - satIfHz(d)) > 1000) add('rf.freq_hz', T('HF-Frequenz {rf} MHz ≠ Sat-ZF {f} MHz', { rf: (c.rf.freq_hz / 1e6).toFixed(3), f: (satIfHz(d) / 1e6).toFixed(3) }));
    if (!(d.symbol_rate >= 1e6 && d.symbol_rate <= 30e6)) add('dvbs.symbol_rate', T('Symbolrate 1–30 MS/s'));
    }
    const fecs = d.system === 'dvbs2' ? DVBS.FEC_S2[d.modulation] || [] : DVBS.FEC_S;
    if (!fecs.includes(d.fec)) add('dvbs.fec', T('FEC {fec} für {sys} nicht zulässig', { fec: d.fec, sys: d.system === 'dvbs2' ? 'DVB-S2 ' + d.modulation : 'DVB-S' }));
    if (d.system === 'dvbs' && d.modulation !== 'QPSK') add('dvbs.modulation', T('DVB-S kennt nur QPSK'));
    if (!DVBS.ROLLOFF.includes(Number(d.rolloff)) || (d.system === 'dvbs' && Number(d.rolloff) !== 0.35)) add('dvbs.rolloff', d.system === 'dvbs' ? T('DVB-S: Rolloff 0,35') : T('Rolloff 0,35/0,25/0,20'));
  }
  if (isDvb(std)) {
    const m = std === 'dvbt' ? c.dvbt : std === 'dvbs' ? c.dvbs : c.dvbc;
    if (std === 'dvbt') {
      if (!DVBT.BW.includes(Number(m.bandwidth_mhz))) add('dvbt.bandwidth_mhz', T('Bandbreite 5/6/7/8 MHz'));
      if (!DVBT.FFT.includes(m.fft)) add('dvbt.fft', T('Modus 2k oder 8k'));
      if (!DVBT.CONST.includes(m.constellation)) add('dvbt.constellation', T('Konstellation QPSK/16-QAM/64-QAM'));
      if (!DVBT.CR.includes(m.code_rate)) add('dvbt.code_rate', T('Coderate 1/2…7/8'));
      if (!DVBT.GI.includes(m.guard)) add('dvbt.guard', 'Guard 1/4…1/32');
      if (!(m.cell_id >= 0 && m.cell_id <= 65535)) add('dvbt.cell_id', T('Zellen-ID 0–65535'));
    } else if (std === 'dvbc') {
      if (!(m.symbol_rate >= 1e6 && m.symbol_rate <= 7.2e6)) add('dvbc.symbol_rate', T('Symbolrate 1,0–7,2 MBd'));
      if (!DVBC.CONST.includes(m.constellation)) add('dvbc.constellation', T('Konstellation 16…256-QAM'));
    }
    if (std !== 'dvbs' && !(c.rf.freq_hz >= 40e6 && c.rf.freq_hz <= 1000e6)) add('rf.freq_hz', T('DVB: Frequenz 40–1000 MHz'));
    const si = c.dvb.si;
    for (const k of ['network_id', 'onid', 'tsid']) if (!/^0x[0-9a-f]{4}$/i.test(si[k] || '')) add(`dvb.si.${k}`, T('SI: {field} 4-stellig hex', { field: k.toUpperCase() }));
    if (!si.network_name) add('dvb.si.network_name', T('SI: Netzwerkname fehlt'));
    const sids = new Set(), lcns = new Set(), keys = new Set();
    c.dvb.programs.forEach((p, i) => {
      const b = `dvb.programs.${i}`;
      if (!/^[a-z0-9_]+$/.test(p.key || '') || keys.has(p.key)) add(`${b}.key`, T('Programm {n}: Schlüssel ungültig/doppelt', { n: i + 1 })); keys.add(p.key);
      if (!p.name || [...p.name].length > 40) add(`${b}.name`, T('Programm {n}: Name 1–40 Zeichen', { n: i + 1 }));
      if (!/^0x[0-9a-f]{4}$/i.test(p.sid || '')) add(`${b}.sid`, T('{name}: SID 4-stellig hex', { name: p.name }));
      else if (sids.has(parseHex(p.sid))) add(`${b}.sid`, T('{name}: SID doppelt', { name: p.name })); sids.add(parseHex(p.sid));
      if (!(p.lcn >= 1 && p.lcn <= 999)) add(`${b}.lcn`, T('{name}: LCN 1–999', { name: p.name }));
      else if (lcns.has(Number(p.lcn))) add(`${b}.lcn`, T('{name}: LCN {lcn} doppelt', { name: p.name, lcn: p.lcn })); lcns.add(Number(p.lcn));
      if (p.type === 'tv') {
        const v = p.video;
        if (v.source.kind === 'file' && !v.source.file) add(`${b}.video.source.file`, T('{name}: Videodatei fehlt', { name: p.name }));
        if (v.source.kind === 'youtube' && !v.source.file) add(`${b}.video.source.url`, T('{name}: YouTube-Video noch nicht geladen', { name: p.name }));
        if (v.source.kind === 'playlist' && !v.source.playlist) add(`${b}.video.source.playlist`, T('{name}: Video-Playlist fehlt', { name: p.name }));
        if (!VIDEO.CODECS[v.codec]) add(`${b}.video.codec`, T('{name}: Videocodec', { name: p.name }));
        if (v.hdr && v.hdr !== 'sdr' && v.codec !== 'hevc') add(`${b}.video.hdr`, T('{name}: HDR10/HLG nur mit HEVC (Main10)', { name: p.name }));
        if (v.resolution === 'uhd' && v.codec === 'mpeg2') add(`${b}.video.resolution`, T('{name}: UHD nicht mit MPEG-2', { name: p.name }));
        if (v.encoder === 'nvenc' && v.codec === 'mpeg2') add(`${b}.video.encoder`, T('{name}: NVENC kann kein MPEG-2', { name: p.name }));
        if (v.source.kind === 'capture' && !v.source.device) add(`${b}.video.source.device`, T('{name}: Capture-Gerät wählen', { name: p.name }));
        const vmin = isHam(c) ? 100 : 300;
        if (!(v.bitrate_kbps >= vmin && v.bitrate_kbps <= 30000)) add(`${b}.video.bitrate_kbps`, T('{name}: Videobitrate {min}–30000 kbit/s', { name: p.name, min: vmin }));
      }
      if (!VIDEO.AUDIO[p.audio.codec]) add(`${b}.audio.codec`, T('{name}: Audiocodec', { name: p.name }));
      if (!(p.audio.bitrate_kbps >= 32 && p.audio.bitrate_kbps <= 640)) add(`${b}.audio.bitrate_kbps`, T('{name}: Audiobitrate 32–640 kbit/s', { name: p.name }));
      if (p.audio.source.kind === 'file' && !p.audio.source.file) add(`${b}.audio.source.file`, T('{name}: Audiodatei fehlt', { name: p.name }));
      if ([...(p.eit.title || '')].length > 96) add(`${b}.eit.title`, T('{name}: EIT-Titel maximal 96 Zeichen', { name: p.name }));
    });
    const bud = tsBudget(c);
    if (bud.over) add('tsbudget', T('Transportstrom überbucht: {used} von {net} Mbit/s', { used: (bud.used / 1e6).toFixed(2), net: (bud.net / 1e6).toFixed(2) })
      + (isHam(c) ? T(' – für DATV meist nur ein Programm: Programme entfernen oder Symbolrate erhöhen ({n} Programme)', { n: (c.dvb?.programs || []).length }) : ''));
  }
}

/** Live ohne Neustart änderbare Pfade der neuen Standards */
export function isLivePathStd(path) {
  return /^fm\.rds\.(ps|rt|rtplus|ta|tp|pty|ms|ct|ptyn|af|af_method|lps|eon|rt_from_tags|level_khz)$/.test(path) || /^fm\.rds\.(rtplus|af|eon)\./.test(path)
    || /^fm\.(deviation_khz|pilot_khz|mpx_power_dbr|audio_gain_db)$/.test(path) || /^fm\.second_carrier\.(level_db|enable)$/.test(path)
    || path === 'fm.freq_mhz' || /^dvb\.programs\.\d+\.eit\./.test(path);   // PAL: vorgerenderte Schleife auf dem Pluto – Text braucht Neustart
}

/** Dienst-Vorlage für DAB im Mehrstandard-Kontext (Wiederverwendung) */
export { newService };

// ---------------------------------------------------------------- Gesamt-Wrapper (von core.js/pages.js genutzt)
export function normalizeConfig(cfg) { return normalizeStd(normDab(cfg)); }
export function validateConfig(c) {
  const std = standardOf(c);
  const e = std === 'dab' ? valDab(c) : [];
  const add = (path, msg) => e.push({ path, msg });
  if (std !== 'dab') {
    const rf = c.rf;
    if (!(rf.freq_hz >= FREQ_MIN_HZ && rf.freq_hz <= FREQ_MAX_HZ)) add('rf.freq_hz', T('Frequenz 70 MHz – 6 GHz'));
    if (!(rf.atten_db >= 0 && rf.atten_db <= ATTEN_MAX)) add('rf.atten_db', T('Dämpfung 0 – 89,75 dB'));
  }
  validateStd(c, add);
  return e;
}
export function isLivePath(p) { return liveDab(p) || isLivePathStd(p); }
