// Prüfberichte UKW (Hub/Pilot/RDS) und DVB (Transportstrom) mit Soll/Ist-Vergleich.
// Berichtformate (Annahme, bis Server/Runner sie festlegen – siehe TESTIDS.md „Berichtformate“):
//  UKW:  {ok, sync, t, snr_db, freq_corr_hz, deviation_peak_khz, deviation_rms_khz, pilot_khz, pilot_freq_hz, pilot_phase_deg,
//         rds_khz, mpx_power_dbr, stereo_separation_db, thd_pct, audio_level_db:[L,R], rds:{pi, ps, rt, rt_ab, rtplus:[{tag,text}],
//         pty, pty_name, tp, ta, ms, di_stereo, af:[MHz], ecc, ct, ptyn, lps, eon:[{pi,ps,pty,af,tp,ta}], group_rate, bler_pct, groups:{...}}}
//  DVB:  {ok, lock, t, mer_db, snr_db, ber_pre, ber_post, freq_corr_hz, bitrate_bps, pcr_jitter_ns_max, continuity_errors,
//         modulation:{...}, tables:{pat,pmt,sdt,nit,eit,tdt}, nit:{network_id,network_name,onid,tsid,delivery:{}},
//         services:[{sid,name,provider,type,lcn,video_codec,resolution,audio_codec,video_bitrate_kbps,audio_bitrate_kbps,eit_title,eit_description,pids:{}}],
//         ffprobe:{ok, streams:[{type,codec,width,height,fps,bitrate}]}}

import { h, icon, esc, stamp } from './ui.js';
import * as D from './dab.js';
import * as STD from './standards.js';
import { decodeOf, rxCaptureCard, slideSrc } from './report.js';
import { T, LANG } from './i18n.js';

const LOC = LANG === 'en' ? 'en-GB' : LANG;
/** Dezimalkomma in festen Soll-/Toleranztexten (Englisch: Punkt) */
const dc = s => LANG === 'en' ? s.replace(/(\d),(\d)/g, '$1.$2') : s;
const nDev = n => n === 1 ? T('{n} Abweichung', { n }) : T('{n} Abweichungen', { n });

const eqHex = (a, b) => D.parseHex(a) != null && D.parseHex(a) === D.parseHex(b);
const eqStr = (a, b) => String(a ?? '').trim() === String(b ?? '').trim();
const eqNum = (a, b) => Number(a) === Number(b);
const row = (name, soll, ist, eq, o = {}) => { const missing = ist === undefined || ist === null || ist === ''; return { name, soll, ist: missing ? '–' : ist, ok: missing ? null : eq(soll, ist), tol: o.tol }; };
const near = tol => (a, b) => Math.abs(Number(a) - Number(b)) <= tol;
const fmt1 = (v, u = '') => v == null || isNaN(v) ? '–' : `${Number(v).toLocaleString(LOC, { maximumFractionDigits: 2 })}${u}`;
const yn = v => v == null ? null : v ? T('ja') : T('nein');

function rowsTable(rows, tid) {
  return h('table.cmp', { tid },
    h('thead', null, h('tr', null, h('th', null, T('Merkmal')), h('th', null, T('Soll')), h('th', null, T('Ist')), h('th.st', null, ''))),
    h('tbody', null, rows.map(r => h('tr', { class: r.ok === false ? 'bad' : r.ok ? 'good' : 'na' },
      h('td', null, r.name, r.tol ? h('small.muted', null, ` (±${r.tol})`) : null), h('td.mono', null, String(r.soll ?? '–')), h('td.mono', null, String(r.ist)),
      h('td.st', null, r.ok === false ? icon('x') : r.ok ? icon('check') : h('span.muted', null, '–'))))));
}
const kpi = (label, value, kind) => h('div.kpi.' + kind, null, h('small', null, label), h('b', null, value));
const head = (ok, bad, text, when) => h('div.verdict.' + (ok && bad === 0 ? 'ok' : 'bad'), { tid: 'report-verdict' }, icon(ok && bad === 0 ? 'check' : 'alert'),
  h('div', null, h('strong', null, text), h('small', null, when ? stamp(new Date(when * (when < 1e12 ? 1000 : 1))) : '')));

// ---------------------------------------------------------------- UKW
export function compareFM(cfg, r) {
  const f = cfg.fm, rds = f.rds, ir = r.rds || {};
  const mod = [
    row(T('Gesamthub (Spitze)'), `${fmt1(f.deviation_khz)} kHz`, r.deviation_peak_khz == null ? null : `${fmt1(r.deviation_peak_khz)} kHz`, () => near(f.deviation_khz * 0.1)(f.deviation_khz, r.deviation_peak_khz), { tol: '10 %' }),
    row(T('Pilothub (19 kHz)'), f.stereo ? `${fmt1(f.pilot_khz)} kHz` : T('aus'), r.pilot_khz == null ? null : `${fmt1(r.pilot_khz)} kHz`, () => f.stereo ? near(0.3)(f.pilot_khz, r.pilot_khz) : Number(r.pilot_khz) < 0.5, { tol: dc('0,3 kHz') }),
    row(T('Pilotfrequenz'), `${(19000).toLocaleString(LOC)} Hz`, r.pilot_freq_hz == null ? null : `${fmt1(r.pilot_freq_hz)} Hz`, () => near(2)(19000, r.pilot_freq_hz), { tol: '2 Hz' }),
    row(T('57-kHz-Phase zu Pilot'), `${rds.phase_deg}°`, r.pilot_phase_deg == null ? null : `${fmt1(r.pilot_phase_deg)}°`, () => near(10)(rds.phase_deg, r.pilot_phase_deg), { tol: '10°' }),
    row(T('RDS-Hub'), rds.enable ? `${fmt1(rds.level_khz)} kHz` : T('aus'), r.rds_khz == null ? null : `${fmt1(r.rds_khz)} kHz`, () => rds.enable ? near(0.2)(rds.level_khz, r.rds_khz) : Number(r.rds_khz) < 0.3, { tol: dc('0,2 kHz') }),
    row(T('MPX-Leistung'), `${fmt1(f.mpx_power_dbr)} dBr`, r.mpx_power_dbr == null ? null : `${fmt1(r.mpx_power_dbr)} dBr`, () => near(1)(f.mpx_power_dbr, r.mpx_power_dbr), { tol: '1 dBr' }),
    row(T('Stereotrennung'), f.stereo ? '≥ 40 dB' : '–', r.stereo_separation_db == null ? null : `${fmt1(r.stereo_separation_db)} dB`, () => !f.stereo || Number(r.stereo_separation_db) >= 40),
    row(T('Klirrfaktor'), dc('≤ 0,5 %'), r.thd_pct == null ? null : `${fmt1(r.thd_pct)} %`, () => Number(r.thd_pct) <= 0.5),
  ];
  const rdsRows = rds.enable ? [
    row('PI', rds.pi, ir.pi, eqHex),
    row('PS', rds.ps, ir.ps, eqStr),
    row(T('Radiotext'), rds.rt, ir.rt, eqStr),
    row('PTY', `${rds.pty} · ${STD.RDS_PTY[rds.pty]}`, ir.pty == null ? null : `${ir.pty} · ${ir.pty_name || STD.RDS_PTY[ir.pty] || ''}`, () => eqNum(rds.pty, ir.pty)),
    row('TP / TA', `${yn(rds.tp)} / ${yn(rds.ta)}`, ir.tp == null ? null : `${yn(ir.tp)} / ${yn(ir.ta)}`, () => !!rds.tp === !!ir.tp && !!rds.ta === !!ir.ta),
    row('MS / DI-Stereo', `${rds.ms ? T('Musik') : T('Sprache')} / ${yn(rds.di.stereo)}`, ir.ms == null ? null : `${ir.ms ? T('Musik') : T('Sprache')} / ${yn(ir.di?.stereo ?? ir.di_stereo)}`, () => !!rds.ms === !!ir.ms && !!rds.di.stereo === !!(ir.di?.stereo ?? ir.di_stereo)),
    row(T('AF-Liste'), rds.af.length ? rds.af.map(x => x.toFixed(1)).join(' ') : T('keine'), Array.isArray(ir.af) ? (ir.af.length ? ir.af.map(x => Number(x).toFixed(1)).join(' ') : T('keine')) : null,
      () => rds.af.length === (ir.af || []).length && rds.af.every(a => (ir.af || []).some(b => near(0.05)(a, b)))),
    row('ECC', rds.ecc || '–', ir.ecc, (a, b) => !rds.ecc || eqHex(a, b)),
    row('CT', rds.ct ? T('gesendet') : T('aus'), ir.ct === undefined ? null : (ir.ct ? String(ir.ct) : T('nicht empfangen')), () => !!rds.ct === !!ir.ct),
    rds.ptyn ? row('PTYN', rds.ptyn, ir.ptyn, eqStr) : null,
    (rds.rtplus || []).length || rds.rt_from_tags ? row('RT+', (rds.rtplus || []).map(t => `${t.type}: ${t.text}`).join(' · ') || T('aus Playlist'), Array.isArray(ir.rtplus) ? (ir.rtplus.length ? ir.rtplus.map(t => `${t.tag || t.type}: ${t.text}`).join(' · ') : T('keine Tags')) : null, () => (ir.rtplus || []).length > 0) : null,
    rds.eon.length ? row('EON', T('{n} Eintrag/Einträge', { n: rds.eon.length }), Array.isArray(ir.eon) ? T('{n} empfangen', { n: ir.eon.length }) : null, () => rds.eon.length === (ir.eon || []).length && rds.eon.every(e => (ir.eon || []).some(x => eqHex(e.pi, x.pi)))) : null,
  ].filter(Boolean) : [];
  return { mod, rds: rdsRows };
}

const FM_MEASURED_LABELS = { deviation_peak_khz: T('Spitzenhub'), deviation_audio_khz: T('Audiohub'), deviation_rms_khz: T('Hub (RMS)'), pilot_khz: T('Pilothub'), pilot_freq_hz: T('Pilotfrequenz (Hz)'),
  pilot_phase_deg: T('57-kHz-Phase (°)'), rds_khz: T('RDS-Hub'), carrier_38k_khz: T('38-kHz-Rest'), mpx_power_dbr: T('MPX-Leistung (dBr)'), stereo_separation_db: T('Stereotrennung (dB)'), separation_db: T('Stereotrennung (dB)'),
  sinad_db: T('SINAD (dB)'), snr_db: T('S/N (dB)'), thd_pct: T('Klirrfaktor (%)'), audio_freq_hz: T('Audiofrequenz (Hz)'), audio_level_dbfs: T('Audiopegel (dBFS)'), spurs_dbc: T('Spektrallinien (dBc)'), freq_corr_hz: T('Frequenzkorrektur (Hz)') };
const RDS_LABELS = { pi: 'PI', ps: 'PS', rt: 'RT', rtplus: 'RT+', pty: 'PTY', pty_name: T('PTY-Name'), tp: 'TP', ta: 'TA', ms: 'MS', di: 'DI', af: 'AF', af_method: T('AF-Methode'), ecc: 'ECC', country: T('Land'), ct: 'CT', ptyn: 'PTYN', lps: 'LPS', eon: 'EON', bler_pct: 'BLER (%)', bler: 'BLER', group_rate: T('Gruppen/s'), groups: T('Gruppen') };
const fmtAny = v => v == null ? '–' : Array.isArray(v) ? (v.length ? v.map(x => typeof x === 'object' ? (x.type ? `${x.type}: ${x.text}` : JSON.stringify(x)) : String(x)).join(' · ') : T('keine')) : typeof v === 'object' ? Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(' · ') : typeof v === 'boolean' ? (v ? T('ja') : T('nein')) : typeof v === 'number' ? v.toLocaleString(LOC, { maximumFractionDigits: 2 }) : String(v);
const kvGrid = (obj, labels, tid) => h('div.rx-grid', { tid }, Object.entries(obj || {}).filter(([, v]) => v != null && typeof v !== 'object' || Array.isArray(v)).map(([k, v]) => h('div.rx-kv', null, h('small', null, labels[k] || k), h('b', { title: fmtAny(v) }, fmtAny(v)))));

export function renderFmReport(report0, cfg, kind) {
  const r = decodeOf(report0);
  // SPEC-Format des Runners: measured / rds / expected / fields[{name,soll,ist,ok}] / mismatches / second_carrier
  if (Array.isArray(r.fields)) {
    const rows = r.fields.map(f => ({ name: f.name, soll: fmtAny(f.soll), ist: fmtAny(f.ist), ok: f.ok == null ? null : !!f.ok }));
    const isRds = f => /^(rds\b|rt\+|eon\b|redsea)/i.test(f.name);
    const bad = rows.filter(x => x.ok === false).length;
    const m = r.measured || {}, ir = r.rds || {};
    const ok = !!r.ok && r.sync !== false && m.carrier !== false;
    return h('div.report', { tid: `report-${kind}` },
      h('div.report-head', null,
        head(ok, bad, m.carrier === false || r.sync === false ? T('Kein Träger') : bad === 0 ? T('Soll = Ist') : nDev(bad), r.t ?? report0.t ?? r.time),
        h('div.kpis', null,
          kpi(T('Spitzenhub'), fmt1(m.deviation_peak_khz, ' kHz'), m.deviation_peak_khz == null ? 'na' : m.deviation_peak_khz <= 75.5 ? 'ok' : 'warn'),
          kpi('Pilot', fmt1(m.pilot_khz, ' kHz'), 'na'),
          kpi('MPX', fmt1(m.mpx_power_dbr, ' dBr'), m.mpx_power_dbr == null ? 'na' : m.mpx_power_dbr <= 0.1 ? 'ok' : 'warn'),
          kpi('SINAD/S-N', fmt1(m.sinad_db ?? m.snr_db, ' dB'), 'na'),
          kpi('RDS-BLER', ir.bler_pct == null && ir.bler == null ? '–' : `${fmt1(ir.bler_pct ?? ir.bler)} %`, (ir.bler_pct ?? ir.bler) == null ? 'na' : (ir.bler_pct ?? ir.bler) === 0 ? 'ok' : (ir.bler_pct ?? ir.bler) < 5 ? 'warn' : 'bad'),
          kpi(T('Abweichungen'), String((r.mismatches || []).length || bad), bad ? 'bad' : 'ok'))),
      kind === 'monitor' ? rxCaptureCard(report0) : null,
      r.error ? h('p.alert', null, icon('alert'), String(r.error)) : null,
      h('section.rep-block', null, h('h4', null, icon('wave'), T('Soll/Ist – Multiplex')), rowsTable(rows.filter(x => !isRds(x)), `report-${kind}-mpx`),
        Object.keys(m).length ? h('details.sub', { open: true }, h('summary', null, T('Messwerte')), kvGrid(m, FM_MEASURED_LABELS, `report-${kind}-measured`)) : null),
      cfg.fm.rds.enable || Object.keys(ir).length ? h('section.rep-block', null, h('h4', null, icon('text'), T('Soll/Ist – RDS (redsea)')), rowsTable(rows.filter(isRds), `report-${kind}-rds`),
        Object.keys(ir).length ? h('details.sub', { open: true }, h('summary', null, T('Empfangene RDS-Daten')), kvGrid(ir, RDS_LABELS, `report-${kind}-rdsdata`)) : null) : null,
      r.second_carrier && Object.keys(r.second_carrier).length ? h('section.rep-block', null, h('h4', null, icon('antenna'), T('Zweiter Träger')), kvGrid(r.second_carrier, { ...FM_MEASURED_LABELS, ...RDS_LABELS, offset_khz: T('Versatz (kHz)'), level_db: T('Pegel (dB)') })) : null,
      (r.mismatches || []).length ? h('section.rep-block.bad', null, h('h4', null, icon('alert'), T('Abweichungen laut Runner')), h('ul', null, r.mismatches.map(x => h('li', null, typeof x === 'string' ? x : T('{name}: Soll {soll}, Ist {ist}', { name: x.name ?? x.field, soll: fmtAny(x.soll), ist: fmtAny(x.ist) }))))) : null,
      h('details.raw', null, h('summary', null, T('Rohdaten (JSON)')), h('pre', null, JSON.stringify(report0, null, 2))));
  }
  const cmp = compareFM(cfg, r);
  const all = [...cmp.mod, ...cmp.rds];
  const bad = all.filter(x => x.ok === false).length;
  const ok = !!r.ok && (r.sync !== false);
  const ir = r.rds || {};
  return h('div.report', { tid: `report-${kind}` },
    h('div.report-head', null,
      head(ok, bad, r.sync === false ? T('Kein Träger') : bad === 0 ? T('Soll = Ist') : nDev(bad), r.t ?? report0.t ?? r.time),
      h('div.kpis', null,
        kpi(T('Träger'), r.sync === false ? T('nein') : T('ja'), r.sync === false ? 'bad' : 'ok'),
        kpi('S/N', fmt1(r.snr_db, ' dB'), r.snr_db == null ? 'na' : r.snr_db >= 46 ? 'ok' : r.snr_db >= 26 ? 'warn' : 'bad'),
        kpi(T('Spitzenhub'), fmt1(r.deviation_peak_khz, ' kHz'), r.deviation_peak_khz == null ? 'na' : r.deviation_peak_khz <= 75.5 ? 'ok' : 'warn'),
        kpi('Pilot', fmt1(r.pilot_khz, ' kHz'), 'na'),
        kpi('RDS-BLER', ir.bler_pct == null ? '–' : `${fmt1(ir.bler_pct)} %`, ir.bler_pct == null ? 'na' : ir.bler_pct === 0 ? 'ok' : ir.bler_pct < 5 ? 'warn' : 'bad'),
        kpi(T('Gruppen/s'), fmt1(ir.group_rate), ir.group_rate == null ? 'na' : near(0.6)(11.4, ir.group_rate) ? 'ok' : 'warn'),
        kpi(T('Frequenzkorr.'), r.freq_corr_hz == null ? '–' : `${Math.round(r.freq_corr_hz)} Hz`, 'na'))),
    kind === 'monitor' ? rxCaptureCard(report0) : null,
    r.error ? h('p.alert', null, icon('alert'), String(r.error)) : null,
    h('section.rep-block', null, h('h4', null, icon('wave'), T('Multiplex / Modulation')), rowsTable(cmp.mod, `report-${kind}-mpx`),
      r.audio_level_db ? h('div.rep-extra', null, h('div.levels', null, h('label', null, T('Audio L/R')), ...r.audio_level_db.map(v => h('span.tag.mono', null, `${fmt1(v)} dBFS`)))) : null),
    cfg.fm.rds.enable ? h('section.rep-block', null, h('h4', null, icon('text'), 'RDS (redsea)'), rowsTable(cmp.rds, `report-${kind}-rds`),
      ir.groups ? h('div.rep-extra', null, h('label', null, T('Gruppen')), ...Object.entries(ir.groups).map(([g, n]) => h('span.tag.mono', null, `${g}: ${n}`))) : null,
      ir.rt && ir.rt !== cfg.fm.rds.rt ? null : null) : null,
    h('details.raw', null, h('summary', null, T('Rohdaten (JSON)')), h('pre', null, JSON.stringify(report0, null, 2))));
}

// ---------------------------------------------------------------- DVB
export function compareDVB(cfg, r) {
  const std = STD.standardOf(cfg);
  const si = cfg.dvb.si, nit = r.nit || {};
  const m = std === 'dvbt' ? cfg.dvbt : std === 'dvbs' ? cfg.dvbs : cfg.dvbc, im = r.modulation || nit.delivery || {};
  const mod = std === 'dvbs' ? [
    row(T('System'), m.system === 'dvbs2' ? 'DVB-S2' : 'DVB-S', im.system ?? im.modulation_system, (a, b) => eqStr(a.replace('-', '').toLowerCase(), String(b).replace('-', '').toLowerCase())),
    row('Transponder', `${m.transponder_mhz} MHz`, im.transponder_mhz == null && im.frequency_hz == null ? null : `${im.transponder_mhz ?? Math.round(im.frequency_hz / 1e6)} MHz`, () => near(1)(m.transponder_mhz, im.transponder_mhz ?? im.frequency_hz / 1e6)),
    row(T('Polarisation'), m.polarization, im.polarization, (a, b) => String(b).toUpperCase().startsWith(a)),
    row(T('Symbolrate'), `${m.symbol_rate} Bd`, im.symbol_rate == null ? null : `${im.symbol_rate} Bd`, () => near(1000)(m.symbol_rate, im.symbol_rate)),
    row(T('Modulation'), m.modulation, im.modulation_type ?? im.modulation ?? im.constellation, eqStr),
    row('FEC', m.fec, im.fec ?? im.fec_inner ?? im.code_rate, eqStr),
  ] : std === 'dvbt' ? [
    row(T('Bandbreite'), `${m.bandwidth_mhz} MHz`, im.bandwidth_mhz == null && im.bandwidth == null ? null : `${parseFloat(im.bandwidth_mhz ?? im.bandwidth)} MHz`, () => eqNum(m.bandwidth_mhz, parseFloat(im.bandwidth_mhz ?? im.bandwidth))),
    row(T('Konstellation'), m.constellation, im.constellation, eqStr),
    row(T('Coderate'), m.code_rate, im.code_rate ?? im.code_rate_HP, eqStr),
    row('Guard', m.guard, im.guard ?? im.guard_interval, eqStr),
    row(T('Modus'), m.fft, im.fft ?? im.mode ?? im.transmission_mode, eqStr),
    row(T('Zellen-ID'), String(m.cell_id), im.cell_id == null ? null : String(im.cell_id), eqNum),
  ] : [
    row(T('Symbolrate'), `${m.symbol_rate} Bd`, im.symbol_rate == null ? null : `${im.symbol_rate} Bd`, () => near(1000)(m.symbol_rate, im.symbol_rate)),
    row(T('Konstellation'), m.constellation, im.constellation ?? im.modulation, eqStr),
  ];
  if (std !== 'dvbs') mod.push(row(T('Mittenfrequenz'), `${cfg.rf.freq_hz} Hz`, im.frequency_hz ?? im.centre_frequency ?? im.frequency, (a, b) => near(1000)(cfg.rf.freq_hz, b)));
  mod.push(row(T('Nutzbitrate'), `${(STD.netRate(cfg) / 1e6).toFixed(3)} Mbit/s`, r.bitrate_bps == null ? null : `${(r.bitrate_bps / 1e6).toFixed(3)} Mbit/s`, () => near(0.02 * STD.netRate(cfg))(STD.netRate(cfg), r.bitrate_bps), { tol: '2 %' }));
  const siRows = [
    row(T('Netzwerk-ID'), si.network_id, nit.network_id, eqHex),
    row(T('Netzwerkname'), si.network_name, nit.network_name, eqStr),
    row('ONID', si.onid, nit.onid, eqHex),
    row('TSID', si.tsid, nit.tsid ?? r.tsid, eqHex),
    row(T('Tabellen'), ['PAT', 'PMT', 'SDT', 'NIT', si.eit ? 'EIT' : null, si.tdt ? 'TDT' : null].filter(Boolean).join(' '), r.tables ? Object.entries(r.tables).filter(([, v]) => v).map(([k]) => k.toUpperCase()).join(' ') : null,
      () => ['pat', 'pmt', 'sdt', 'nit', si.eit ? 'eit' : null, si.tdt ? 'tdt' : null].filter(Boolean).every(k => r.tables?.[k])),
    row(T('LCN-Deskriptor'), si.lcn ? T('gesendet') : T('aus'), r.lcn === undefined ? null : (r.lcn && Object.keys(r.lcn).length ? T('vorhanden') : T('fehlt')), () => !!si.lcn === !!(r.lcn && Object.keys(r.lcn).length)),
    row('PCR-Jitter', '≤ 500 ns', r.pcr_jitter_ns_max == null ? null : `${Math.round(r.pcr_jitter_ns_max)} ns`, () => Number(r.pcr_jitter_ns_max) <= 500),
    row(T('Kontinuitätsfehler'), '0', r.continuity_errors == null ? null : String(r.continuity_errors), () => Number(r.continuity_errors) === 0),
  ];
  const svcs = (r.services || []);
  const programs = cfg.dvb.programs.map(p => {
    const ist = svcs.find(s => eqHex(s.sid, p.sid));
    if (!ist) return { p, ist: null, rows: [] };
    const rows = [
      row(T('Name'), p.name, ist.name, eqStr), row(T('Anbieter'), p.provider, ist.provider, eqStr),
      row(T('Typ'), p.type === 'radio' ? 'Radio' : 'TV', ist.type == null ? null : (/radio|0x02/i.test(String(ist.type)) ? 'Radio' : 'TV'), eqStr),
      si.lcn ? row('LCN', String(p.lcn), ist.lcn ?? r.lcn?.[p.sid] ?? r.lcn?.[String(D.parseHex(p.sid))], eqNum) : null,
      p.type !== 'radio' ? row(T('Videocodec'), STD.VIDEO.CODECS[p.video.codec].split(' ')[0], ist.video_codec, (a, b) => String(b).toLowerCase().replace(/[^a-z0-9]/g, '').includes(a.toLowerCase().replace(/[^a-z0-9]/g, ''))) : null,
      p.type !== 'radio' ? row(T('Auflösung'), STD.VIDEO.RES[p.video.resolution].split(' ')[1], ist.resolution, (a, b) => String(b).toLowerCase().replace(/×/g, 'x').replace(/[ip]$/, '') === a.toLowerCase().replace(/×/g, 'x').replace(/[ip]$/, '')) : null,
      row(T('Audiocodec'), { mp2: 'MP2', aac: 'AAC', ac3: 'AC-3' }[p.audio.codec], ist.audio_codec, (a, b) => { const n = x => String(x).toLowerCase().replace(/[^a-z0-9]/g, '').replace(/^mpeg1layer2$|^mpeg1l2$|^mpa$|^mp2a$/, 'mp2').replace(/^eac3$/, 'ac3'); return n(b) === n(a); }),
      si.eit ? row(T('EIT-Titel'), p.eit.title, ist.eit_title, eqStr) : null,
    ].filter(Boolean);
    return { p, ist, rows };
  });
  return { mod, si: siRows, programs, extra: svcs.filter(s => !cfg.dvb.programs.some(p => eqHex(p.sid, s.sid))) };
}

/** Konstellation {levels, points:[[i,q]]} als SVG – Gitterlinien auf den Entscheidungsgrenzen */
export function constellationSVG(c, size = 260) {
  if (!c?.points?.length) return null;
  const L = (c.levels || 7) + 1, ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `${-L} ${-L} ${2 * L} ${2 * L}`);
  svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('class', 'constellation');
  const add = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); svg.append(e); };
  add('rect', { x: -L, y: -L, width: 2 * L, height: 2 * L, class: 'cst-bg' });
  for (let k = -L + 2; k < L; k += 2) {
    add('line', { x1: k, y1: -L, x2: k, y2: L, class: 'cst-grid' });
    add('line', { x1: -L, y1: k, x2: L, y2: k, class: 'cst-grid' });
  }
  for (const [i, q] of c.points) add('circle', { cx: i, cy: -q, r: 0.07, class: 'cst-dot' });
  return svg;
}

export function renderDvbReport(report0, cfg, kind) {
  const r = decodeOf(report0);
  const cmp = compareDVB(cfg, r);
  const all = [...cmp.mod, ...cmp.si, ...cmp.programs.flatMap(x => x.rows)];
  const bad = all.filter(x => x.ok === false).length + cmp.programs.filter(x => !x.ist).length + cmp.extra.length;
  const ok = !!r.ok && r.lock !== false;
  const fp = r.ffprobe || {};
  return h('div.report', { tid: `report-${kind}` },
    h('div.report-head', null,
      head(ok, bad, r.lock === false ? T('Kein Lock') : bad === 0 ? T('Soll = Ist') : nDev(bad), r.t ?? report0.t ?? r.time),
      h('div.kpis', null,
        kpi('Lock', r.lock === false ? T('nein') : T('ja'), r.lock === false ? 'bad' : 'ok'),
        kpi('MER', fmt1(r.mer_db, ' dB'), r.mer_db == null ? 'na' : r.mer_db >= 28 ? 'ok' : r.mer_db >= 20 ? 'warn' : 'bad'),
        kpi(T('BER (vor RS)'), r.ber_pre == null ? '–' : Number(r.ber_pre).toExponential(1), r.ber_pre == null ? 'na' : r.ber_pre <= 2e-4 ? 'ok' : 'warn'),
        kpi(T('Rate'), r.bitrate_bps == null ? '–' : `${(r.bitrate_bps / 1e6).toFixed(2)} Mbit/s`, 'na'),
        kpi(T('Dienste'), `${(r.services || []).length}/${cfg.dvb.programs.length}`, (r.services || []).length === cfg.dvb.programs.length ? 'ok' : 'bad'),
        kpi('ffprobe', fp.ok == null ? '–' : fp.ok ? T('decodierbar') : T('Fehler'), fp.ok == null ? 'na' : fp.ok ? 'ok' : 'bad'))),
    kind === 'monitor' ? rxCaptureCard(report0) : null,
    r.error ? h('p.alert', null, icon('alert'), String(r.error)) : null,
    r.constellation ? h('section.rep-block.rep-cst', { tid: `report-${kind}-constellation` }, h('h4', null, icon('target'), T('Konstellation (entzerrte Datenträger)')),
      constellationSVG(r.constellation),
      h('p.hint', null, T('MER-Schätzung mit dem Pluto als Messempfänger: {n} Träger in Kanalmitte (DC) und {p} % Ausreißer nicht gewertet.', { n: r.mer_excluded_carriers ?? 0, p: D.fmtNum(r.mer_outlier_pct ?? 0, 2) }))) : null,
    h('div.grid2.repgrid', null,
      h('section.rep-block', null, h('h4', null, icon('wave'), T('Modulation / NIT-Delivery')), rowsTable(cmp.mod, `report-${kind}-mod`)),
      h('section.rep-block', null, h('h4', null, icon('layers'), T('SI-Tabellen')), rowsTable(cmp.si, `report-${kind}-si`))),
    h('div.rep-services', null, cmp.programs.map(({ p, ist, rows }) => h('section.rep-block.svc' + (ist ? '' : '.missing'), { tid: `report-${kind}-prg-${p.key}` },
      h('h4', null, icon('tv'), p.name, h('span.mono.muted', null, ` ${p.sid} · LCN ${p.lcn}`), ist ? null : h('span.badge.bad', null, T('fehlt im TS'))),
      ist ? [rowsTable(rows), ist.pids ? h('div.rep-extra', null, h('label', null, 'PIDs'), ...Object.entries(ist.pids).map(([k, v]) => h('span.tag.mono', null, `${k}: ${typeof v === 'number' ? '0x' + v.toString(16).toUpperCase().padStart(4, '0') : v}`))) : null]
        : h('p.muted', null, T('Dieses Programm wurde im Transportstrom nicht gefunden (SID-Vergleich).'))))),
    cmp.extra.length ? h('section.rep-block.bad', null, h('h4', null, icon('alert'), T('Unerwartete Dienste')), h('ul', null, cmp.extra.map(e => h('li', null, `${e.sid} ${e.name || ''}`)))) : null,
    fp.streams && fp.streams.length ? h('section.rep-block', null, h('h4', null, icon('scan'), T('ffprobe – Elementarströme')),
      h('div.tblwrap', null, h('table.tbl.compact', null, h('thead', null, h('tr', null, h('th', null, T('Typ')), h('th', null, 'Codec'), h('th', null, 'Format'), h('th.r', null, T('Bitrate')))),
        h('tbody', null, fp.streams.map(s => h('tr', null, h('td', null, s.type), h('td.mono', null, s.codec), h('td.mono', null, s.width ? `${s.width}×${s.height}${s.fps ? ` @ ${s.fps}` : ''}` : (s.samplerate ? `${s.samplerate} Hz ${s.channels || ''}ch` : '–')), h('td.r.mono', null, s.bitrate ? `${Math.round(s.bitrate / 1000)} kbit/s` : '–'))))))) : null,
    h('details.raw', null, h('summary', null, T('Rohdaten (JSON)')), h('pre', null, JSON.stringify(report0, null, 2))));
}

// ---------------------------------------------------------------- PAL
//  Bericht (Annahme): {ok, lock, t, vision_carrier_hz, sound_carrier_hz, sound_offset_hz, sound_deviation_khz, vision_sound_ratio_db,
//    levels:{sync_tip_pct, blanking_pct, black_pct, white_pct, residual_carrier_pct}, line_freq_hz, field_freq_hz, color_burst, colour_subcarrier_hz,
//    snr_db, image:"data:image/png;base64,…" | {file|url}, fields:[{name,soll,ist,ok}], mismatches}
export function comparePAL(cfg, r) {
  const p = cfg.pal, car = STD.palCarriers(cfg), lv = r.levels || {};
  const sys = STD.PAL_SYSTEMS[p.system] || STD.PAL_SYSTEMS.BG;
  const mhz = v => v == null ? null : `${D.fmtMHz(v)} MHz`;
  const g = (...ks) => { for (const k of ks) { const v = k.split('.').reduce((a, x) => a?.[x], r); if (v != null) return v; } return null; };
  const blank = g('blanking_rel', 'levels.blanking_rel', 'levels.blanking'), white = g('white_rel', 'levels.white_rel', 'levels.white');
  const ratio = g('sound_vision_db', 'sound_to_vision_db', 'vision_sound_ratio_db');
  const sndHz = g('sound_freq_hz', 'tone_freq_hz', 'audio_freq_hz'), dev = g('sound_deviation_hz', 'deviation_hz');
  const lineHz = g('line_freq_hz', 'line_hz'), corr = g('correlation', 'image_correlation');
  const rows = [
    row(T('Bildträger'), mhz(car.vision), mhz(g('vision_carrier_hz', 'lo_vision_hz')), () => near(2000)(car.vision, g('vision_carrier_hz', 'lo_vision_hz')), { tol: '2 kHz' }),
    row(T('Tonträger'), mhz(car.sound), mhz(g('sound_carrier_hz')), () => near(2000)(car.sound, g('sound_carrier_hz')), { tol: '2 kHz' }),
    row(T('Zeilenfrequenz'), `${(15625).toLocaleString(LOC)} Hz`, lineHz == null ? null : `${fmt1(lineHz)} Hz`, () => near(1)(15625, lineHz)),
    row(T('Austastwert (rel. Sync)'), dc('0,73'), blank == null ? null : fmt1(blank > 1.5 ? blank / 100 : blank), () => near(0.03)(0.73, blank > 1.5 ? blank / 100 : blank), { tol: dc('0,03') }),
    row(T('Weißwert (rel. Sync)'), dc('0,10'), white == null ? null : fmt1(white > 1.5 ? white / 100 : white), () => near(0.03)(0.10, white > 1.5 ? white / 100 : white), { tol: dc('0,03') }),
    !['off', 'silence'].includes(p.sound.kind) ? row(T('Ton/Bild'), `${fmt1(p.sound.level_db)} dB`, ratio == null ? null : `${fmt1(ratio > 0 ? -ratio : ratio)} dB`, () => near(1)(p.sound.level_db, ratio > 0 ? -ratio : ratio), { tol: '1 dB' }) : null,
    p.sound.kind === 'tone' ? row(T('Tonfrequenz'), `${p.sound.freq_hz} Hz`, sndHz == null ? null : `${fmt1(sndHz)} Hz`, () => near(7)(p.sound.freq_hz, sndHz), { tol: dc('6,25 Hz') }) : null,
    !['off', 'silence'].includes(p.sound.kind) ? row(T('Ton-Hub'), `${fmt1(p.sound.deviation_hz / 1000)} kHz`, dev == null ? null : `${fmt1(dev / 1000)} kHz`, () => near(p.sound.deviation_hz * 0.1)(p.sound.deviation_hz, dev), { tol: '10 %' }) : null,
    row(T('Abstand Ton–Bild'), `${D.fmtNum(sys.sound_mhz, 3)} MHz`, g('sound_offset_hz') == null ? null : `${D.fmtNum(g('sound_offset_hz') / 1e6, 3)} MHz`, () => near(1000)(sys.sound_mhz * 1e6, g('sound_offset_hz'))),
    row(T('Farbburst/-träger'), T('vorhanden'), g('color_burst', 'chroma_level', 'colour_level') == null ? null : (g('color_burst', 'chroma_level', 'colour_level') ? T('vorhanden') : T('fehlt')), () => !!g('color_burst', 'chroma_level', 'colour_level')),
    corr != null ? row(T('Korrelation mit Sollbild'), dc('≥ 0,95'), fmt1(corr), () => Number(corr) >= 0.95) : null,
    g('clipping', 'clip_pct', 'clips') != null ? row('Clipping', '0', String(g('clipping', 'clip_pct', 'clips')), () => !Number(g('clipping', 'clip_pct', 'clips'))) : null,
  ];
  return rows.filter(Boolean);
}

export function renderPalReport(report0, cfg, kind) {
  const r = decodeOf(report0);
  const rows = Array.isArray(r.fields) && r.fields.length ? r.fields.map(f => ({ name: f.name, soll: String(f.soll ?? '–'), ist: String(f.ist ?? '–'), ok: f.ok == null ? null : !!f.ok })) : comparePAL(cfg, r);
  const bad = rows.filter(x => x.ok === false).length;
  const ok = !!r.ok && r.lock !== false;
  const im0 = r.image ?? r.png ?? r.image_png ?? (Array.isArray(r.images) ? r.images[0] : null) ?? (Array.isArray(r.pngs) ? r.pngs[0] : null);
  const img = im0 ? (typeof im0 === 'string' ? (im0.startsWith('data:') || /^(https?:|\/)/.test(im0) ? im0 : slideSrc(im0.replace(/^runtime\//, ''))) : slideSrc(im0)) : null;
  const lv = r.levels || {};
  return h('div.report', { tid: `report-${kind}` },
    h('div.report-head', null,
      head(ok, bad, r.lock === false ? T('Kein Bild-Sync') : bad === 0 ? T('Soll = Ist') : nDev(bad), r.t ?? report0.t ?? r.time),
      h('div.kpis', null,
        kpi('Sync', r.lock === false ? T('nein') : T('ja'), r.lock === false ? 'bad' : 'ok'),
        kpi(T('S/N Bild'), fmt1(r.snr_db, ' dB'), r.snr_db == null ? 'na' : r.snr_db >= 40 ? 'ok' : r.snr_db >= 30 ? 'warn' : 'bad'),
        kpi(T('Ton/Bild'), fmt1(r.sound_vision_db ?? (r.vision_sound_ratio_db != null ? -r.vision_sound_ratio_db : null), ' dB'), 'na'),
        kpi(T('Ton-Hub'), r.sound_deviation_hz != null ? fmt1(r.sound_deviation_hz / 1000, ' kHz') : fmt1(r.sound_deviation_khz, ' kHz'), 'na'),
        kpi(T('Weiß (rel.)'), (lv.white_rel ?? r.white_rel) == null ? '–' : fmt1(lv.white_rel ?? r.white_rel), 'na'),
        kpi(T('Abweichungen'), String((r.mismatches || []).length || bad), bad ? 'bad' : 'ok'))),
    kind === 'monitor' ? rxCaptureCard(report0) : null,
    r.error ? h('p.alert', null, icon('alert'), String(r.error)) : null,
    h('div.grid2.repgrid', null,
      h('section.rep-block', null, h('h4', null, icon('antenna'), T('Träger & Pegel (Hüllkurve)')), rowsTable(rows, `report-${kind}-pal`),
        Object.keys(lv).length ? h('div.rx-grid', { tid: `report-${kind}-levels` }, Object.entries(lv).map(([k, v]) => h('div.rx-kv', null, h('small', null, { sync_rel: T('Synchronspitze'), blanking_rel: T('Austastung'), black_rel: T('Schwarz'), white_rel: T('Weiß'), chroma_rel: T('Farbträger') }[k] || k), h('b', null, typeof v === 'number' ? fmt1(v) : String(v))))) : null),
      h('section.rep-block', null, h('h4', null, icon('tv'), T('Demoduliertes Bild')),
        img ? h('figure.palimg', { tid: `report-${kind}-image` }, h('img', { src: img, alt: T('Demoduliertes PAL-Bild'), onerror: e => e.target.classList.add('broken') }), h('figcaption', null, r.image_info || T('aus dem Bericht (PNG)')))
          : h('p.muted', null, T('Der Bericht enthält kein Bild.')))),
    h('details.raw', null, h('summary', null, T('Rohdaten (JSON)')), h('pre', null, JSON.stringify(report0, (k, v) => k === 'image' && typeof v === 'string' && v.length > 200 ? `${v.slice(0, 60)}… ` + T('({n} Zeichen)', { n: v.length }) : v, 2))));
}

/** Zusammenfassung für das Messprotokoll (HTML) */
export function protocolStdSection(cfg) {
  const std = STD.standardOf(cfg);
  const kv = rows => `<table class="kv">${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table>`;
  if (std === 'fm') {
    const f = cfg.fm, r = f.rds;
    return `<section><h2>${esc(T('UKW-Sender'))}</h2>${kv([[T('Frequenz'), `${(cfg.rf.freq_hz / 1e6).toFixed(1)} MHz`], [T('Gesamthub'), `${f.deviation_khz} kHz`], ['Pilot', f.stereo ? `${f.pilot_khz} kHz` : 'Mono'], ['Preemphasis', f.preemphasis_us ? `${f.preemphasis_us} µs` : T('aus')], [T('MPX-Leistung'), f.mpx_power_dbr == null ? T('unbegrenzt') : `${f.mpx_power_dbr} dBr`], ['Limiter', f.limiter.enable ? T('an (Schwelle {th})', { th: f.limiter.threshold }) : T('aus')], [T('Zweiter Träger'), f.second_carrier.enable ? `${f.second_carrier.offset_khz} kHz, ${f.second_carrier.level_db} dB` : T('aus')]])}</section>
<section><h2>RDS</h2>${r.enable ? kv([['PI', r.pi], ['PS', r.ps], ['RT', r.rt], ['PTY', `${r.pty} · ${STD.RDS_PTY[r.pty]}`], ['TP/TA', `${r.tp ? 'TP' : '–'} / ${r.ta ? 'TA' : '–'}`], ['AF', `${r.af.map(x => x.toFixed(1)).join(', ') || T('keine')} (${T('Methode')} ${r.af_method})`], ['ECC', r.ecc || '–'], ['CT', r.ct ? T('an') : T('aus')], ['RT+', (r.rtplus || []).map(t => `${t.type}: ${t.text}`).join('; ') || (r.rt_from_tags ? T('aus Playlist') : T('aus'))], ['EON', `${r.eon.length}`], [T('RDS-Hub'), `${r.level_khz} kHz, ${r.phase_deg}°`]]) : `<p>${esc(T('RDS aus.'))}</p>`}</section>`;
  }
  if (std === 'pal') {
    const p = cfg.pal, c = STD.palCarriers(cfg);
    return `<section><h2>${esc(T('Analog-TV PAL'))}</h2>${kv([[T('Kanal'), `${cfg.rf.channel}`], [T('Bildträger'), `${(c.vision / 1e6).toFixed(3)} MHz`], [T('Tonträger'), `${(c.sound / 1e6).toFixed(3)} MHz`], [T('Norm'), STD.PAL_SYSTEMS[p.system]?.name || p.system],
      [T('Bild'), p.image.kind === 'testpattern' ? STD.PAL_PATTERNS[p.image.pattern] || p.image.pattern : p.image.kind], ['Text', `${p.image.text || '–'} / ${p.image.text2 || '–'}`], [T('Pegel'), String(p.level)], [T('Abtastrate'), `${(STD.requiredSampleRate(cfg) / 1e6).toFixed(3)} MS/s ${p.live ? '(live)' : T('(Schleife 160 ms)')}`],
      [T('Ton'), ['off', 'silence'].includes(p.sound.kind) ? p.sound.kind : `${p.sound.kind === 'tone' ? `${p.sound.freq_hz} Hz` : p.sound.kind}, ${T('Hub')} ${p.sound.deviation_hz / 1000} kHz, ${p.sound.level_db} dB`]])}</section>`;
  }
  if (STD.isDvb(std)) {
    const m = std === 'dvbt' ? cfg.dvbt : std === 'dvbs' ? cfg.dvbs : cfg.dvbc, b = STD.tsBudget(cfg);
    const mod = std === 'dvbs' ? [[T('Transponder'), `${m.transponder_mhz} MHz ${m.polarization}`], ['LOF / 22 kHz', `${m.lof_mhz} MHz / ${m.tone_22khz ? T('an') : T('aus')}`], ['Sat-ZF', `${(cfg.rf.freq_hz / 1e6).toFixed(0)} MHz`], [T('System'), m.system === 'dvbs2' ? 'DVB-S2' : 'DVB-S'], [T('Modulation'), m.modulation], [T('Symbolrate'), `${m.symbol_rate / 1000} kS/s`], ['FEC', m.fec], ['Rolloff', String(m.rolloff)]]
      : std === 'dvbt' ? [[T('Kanal'), `${cfg.rf.channel} · ${(cfg.rf.freq_hz / 1e6).toFixed(3)} MHz`], [T('Bandbreite'), `${m.bandwidth_mhz} MHz`], [T('Modus'), m.fft], [T('Konstellation'), m.constellation], [T('Coderate'), m.code_rate], ['Guard', m.guard], [T('Zellen-ID'), String(m.cell_id)]]
      : [[T('Kanal'), `${cfg.rf.channel} · ${(cfg.rf.freq_hz / 1e6).toFixed(3)} MHz`], [T('Symbolrate'), `${(m.symbol_rate / 1e6).toFixed(3)} MBd`], [T('Konstellation'), m.constellation]];
    mod.push([T('Nutzbitrate'), `${(b.net / 1e6).toFixed(3)} Mbit/s`], [T('Belegt'), `${(b.used / 1e6).toFixed(3)} Mbit/s`], [T('Ausgabeweg'), STD.requiredPath(cfg).path === 'pluto' ? T('Modulator auf dem Pluto') : 'USB-Streaming']);
    const si = cfg.dvb.si;
    return `<section><h2>${std === 'dvbt' ? 'DVB-T' : std === 'dvbs' ? 'DVB-S' : 'DVB-C'}</h2>${kv(mod)}</section>
<section><h2>${esc(T('SI-Tabellen'))}</h2>${kv([[T('Netzwerk'), `${si.network_id} „${si.network_name}“`], ['ONID / TSID', `${si.onid} / ${si.tsid}`], ['LCN / EIT / TDT', `${si.lcn ? T('an') : T('aus')} / ${si.eit ? T('an') : T('aus')} / ${si.tdt ? T('an') : T('aus')}`]])}</section>
<section><h2>${esc(T('Programme'))}</h2><table class="grid"><thead><tr><th>SID</th><th>${esc(T('Name'))}</th><th>LCN</th><th>${esc(T('Typ'))}</th><th>${esc(T('Video'))}</th><th>${esc(T('Audio'))}</th><th>kbit/s</th></tr></thead><tbody>${cfg.dvb.programs.map(p => `<tr><td>${esc(p.sid)}</td><td>${esc(p.name)}</td><td>${p.lcn}</td><td>${p.type}</td><td>${p.type === 'radio' ? '–' : esc(`${p.video.codec} ${p.video.resolution} ${p.video.bitrate_kbps}`)}</td><td>${esc(`${p.audio.codec} ${p.audio.bitrate_kbps}`)}</td><td>${Math.round(STD.programRate(p) / 1000)}</td></tr>`).join('')}</tbody></table></section>`;
  }
  return '';
}
