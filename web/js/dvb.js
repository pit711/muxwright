// DVB-T / DVB-C: Bereiche „Modulation“, „Programme“ (Transportstrom) und „SI-Tabellen“.

import { h, icon, api, toast, dialog } from './ui.js';
import * as D from './dab.js';
import * as STD from './standards.js';
import { S, field, input, textarea, select, toggle, segmented, card, counter, edited, errorsFor, getPath, setPath, upload, previewCache, loadMedia, loadPlaylists, save, load } from './core.js';
import { audioSourceCard, uploadBtn } from './pages.js';
import { T, LANG } from './i18n.js';

const LOC = LANG === 'en' ? 'en-GB' : LANG;

const fmtMbit = bps => `${(bps / 1e6).toLocaleString(LOC, { minimumFractionDigits: 2, maximumFractionDigits: 3 })} Mbit/s`;
const prgColor = i => `var(--c${i % 10})`;

// ---------------------------------------------------------------- Modulation
export function pageModulation() {
  const std = STD.standardOf(S.draft);
  const rateEl = h('b.mono', { tid: 'dvb-netrate' });
  const spsEl = h('b.mono', { tid: 'dvb-sps' });
  const upd = () => { rateEl.textContent = fmtMbit(STD.netRate(S.draft)); const p = STD.requiredPath(S.draft); spsEl.textContent = `${(p.sps / 1e6).toLocaleString(LOC, { minimumFractionDigits: 3 })} MS/s`; spsEl.classList.toggle('bad', p.path === 'pluto'); };
  S.watchers.push(upd); upd();
  const head = h('div.bigread', null,
    h('div', null, h('small', null, T('Nutzbitrate (TS netto)')), h('span.big', null, rateEl)),
    h('div', null, h('small', null, T('Abtastrate / Ausgabeweg')), h('span.big', null, spsEl, h('em', null, STD.requiredPath(S.draft).path === 'pluto' ? ' ' + T('Pluto-Modulator') : ' USB'))));

  if (std === 'dvbs') return pageDvbs(head);
  if (std === 'atsc') return pageAtsc(head);
  if (std === 'isdbt') return pageIsdbt(head);
  if (std === 'dtmb') return pageDtmb(head);
  if (std === 'dvbc' && STD.isJ83b(S.draft)) return pageJ83b(head);
  if (std === 'dvbt') {
    const p = S.draft.dvbt;
    const chan = STD.DVBT_CHANNELS.find(c => c.id === S.draft.rf.channel);
    const bwWarn = chan && chan.bw !== Number(p.bandwidth_mhz) ? h('p.alert', null, icon('alert'), T('Kanal {ch} ist ein {bw}-MHz-Kanal, gewählt sind {sel} MHz.', { ch: chan.id, bw: chan.bw, sel: p.bandwidth_mhz })) : null;
    return h('div.page-grid', null,
      h('div.col', null, card('DVB-T (EN 300 744)', 'wave', [
        head,
        field(T('Bandbreite'), segmented('dvbt.bandwidth_mhz', STD.DVBT.BW.map(b => ({ v: b, label: `${b} MHz` })), { tid: 'dvbt-bandwidth' }),
          { path: 'dvbt.bandwidth_mhz', hint: T('7/8 MHz nur über den Modulator/Loop auf dem Pluto (9,14 bzw. 8,0 MS/s); 6 MHz läuft per USB (6,857 MS/s), ist in EU-Receivern aber oft nur manuell abstimmbar') }),
        bwWarn,
        h('div.grid2', null,
          field(T('Modus (FFT)'), segmented('dvbt.fft', STD.DVBT.FFT.map(v => ({ v, label: v })), { tid: 'dvbt-fft' }), { path: 'dvbt.fft', hint: T('8k: Gleichwelle/lange Echos, 2k: mobil') }),
          field(T('Konstellation'), segmented('dvbt.constellation', STD.DVBT.CONST.map(v => ({ v, label: v })), { tid: 'dvbt-constellation' }), { path: 'dvbt.constellation' })),
        h('div.grid2', null,
          field(T('Coderate'), segmented('dvbt.code_rate', STD.DVBT.CR.map(v => ({ v, label: v })), { tid: 'dvbt-coderate' }), { path: 'dvbt.code_rate' }),
          field(T('Guard-Intervall'), segmented('dvbt.guard', STD.DVBT.GI.map(v => ({ v, label: v })), { tid: 'dvbt-guard' }), { path: 'dvbt.guard' })),
        h('div.grid2', null,
          field(T('Zellen-ID (TPS)'), input('dvbt.cell_id', { type: 'number', min: 0, max: 65535, tid: 'dvbt-cellid', mono: true }), { path: 'dvbt.cell_id', hint: '0–65535' }),
          field(T('Hierarchie'), select('dvbt.hierarchy', [{ v: 'none', label: T('keine (nicht hierarchisch)') }], { tid: 'dvbt-hierarchy', rerender: false }), { hint: T('nur nicht-hierarchisch unterstützt') })),
        h('p.hint', null, icon('info'), T('Alle Modulationsparameter brauchen „Übernehmen (Neustart)“. Die Nutzbitrate bestimmt das TS-Budget im Bereich „Programme“.')),
      ], { tid: 'card-dvbt' })),
      h('div.col', null, rateTable('dvbt')));
  }
  const p = S.draft.dvbc;
  const srIn = h('input.mono', { type: 'number', min: 1000, max: 7200, step: 1, tid: 'dvbc-symbolrate' });
  srIn.value = Math.round(p.symbol_rate / 1000);
  srIn.addEventListener('change', () => { S.draft.dvbc.symbol_rate = Math.round(Number(srIn.value)) * 1000; edited('dvbc.symbol_rate', true); });
  return h('div.page-grid', null,
    h('div.col', null, card('DVB-C (EN 300 429, Annex A)', 'wave', [
      head,
      annexSwitch(),
      field(T('Symbolrate (kBd)'), h('div.inrow.wrap', null, srIn, ...STD.DVBC.SR_PRESETS.map(sr => h('button.btn.sm', { type: 'button', class: p.symbol_rate === sr ? 'accent' : null, tid: `dvbc-sr-${sr}`, onclick: () => { S.draft.dvbc.symbol_rate = sr; edited('dvbc.symbol_rate', true); } }, `${(sr / 1e6).toLocaleString(LOC, { minimumFractionDigits: 3 })} MBd`))),
        { path: 'dvbc.symbol_rate', hint: T('6,900 MBd = deutsches Kabel (Auto-Suchlauf), 6,875 Pflicht in Suchlauflisten, 3,450 MBd läuft per USB (manuelle Eingabe am Receiver)') }),
      field(T('Konstellation'), segmented('dvbc.constellation', STD.DVBC.CONST.map(v => ({ v, label: v })), { tid: 'dvbc-constellation' }), { path: 'dvbc.constellation', hint: T('Deutschland: 256-QAM; RRC α = 0,15') }),
      h('p.hint', null, icon('info'), T('Nutzbitrate = SR · log2(M) · 188/204. Für 6,9 MBd sind 13,8 MS/s nötig – nur zyklischer Puffer/Modulator auf dem Pluto.')),
    ], { tid: 'card-dvbc' })),
    h('div.col', null, rateTable('dvbc')));
}

export const untestedBadge = () => h('span.badge-untested', { title: T('Noch nicht an einem Empfänger geprüft') }, 'untested');

function annexSwitch() {
  return field(T('Norm'), segmented('dvbc.annex', [{ v: 'a', label: T('Annex A (Europa/China)') }, { v: 'b', label: T('Annex B (USA, J.83 B)') }, { v: 'c', label: T('Annex C (Japan, J.83 C)') }], { tid: 'dvbc-annex', rerender: true,
    onchange: v => {
      const d = S.draft.dvbc, rf = S.draft.rf;
      if (v === 'b') { if (!STD.DVBC.J83B[d.constellation]) d.constellation = '256-QAM'; d.symbol_rate = STD.DVBC.J83B[d.constellation].sr; d.sps = 1; rf.channel = 'C80'; rf.freq_hz = 561e6; STD.regionCodecs(S.draft, 'dvbc'); }
      else if (v === 'c') { if (!['64-QAM', '256-QAM'].includes(d.constellation)) d.constellation = '64-QAM'; d.symbol_rate = STD.DVBC.J83C_SR; d.sps = 1; }
      else { d.symbol_rate = 6900000; rf.channel = 'S21'; rf.freq_hz = 306e6; }
      S.rfIntent.add('channel'); S.rfIntent.add('freq_hz');
    } }), { path: 'dvbc.annex', hint: h('span', null, S.draft.dvbc.annex === 'c' ? T('Annex C: Codierung wie Annex A, RRC α 0,13, 6-MHz-Kanal; Japan 5,274 MBd') : T('Annex B: US-Kabel (SCTE 07), feste Symbolrate je QAM, PSIP statt DVB-SI'),
      S.draft.dvbc.annex !== 'a' ? untestedBadge() : null) });
}

function pageJ83b(head) {
  const d = S.draft.dvbc;
  return h('div.page-grid', null,
    h('div.col', null, card(h('span', null, 'DVB-C Annex B · ITU-T J.83 B (SCTE 07)', untestedBadge()), 'wave', [
      head,
      annexSwitch(),
      field(T('Konstellation'), segmented('dvbc.constellation', Object.keys(STD.DVBC.J83B).map(v => ({ v, label: v })), { tid: 'j83b-constellation',
        onchange: v => { S.draft.dvbc.symbol_rate = STD.DVBC.J83B[v].sr; } }), { path: 'dvbc.constellation',
        hint: T('64-QAM: 5,057 MBd → 26,97 Mbit/s · 256-QAM: 5,361 MBd → 38,81 Mbit/s (RRC α 0,18 / 0,12)') }),
      h('p.hint', null, icon('info'), T('Reed-Solomon (128,122) über GF(128), Faltungs-Interleaver, Trellis-codierte QAM. Programme: MPEG-2 oder H.264 mit AC-3; Kanaltabelle CVCT (PSIP). US-Kabelraster C2–C158 (EIA-542).')),
    ], { tid: 'card-j83b' })),
    h('div.col', null, null));
}

function pageAtsc(head) {
  return h('div.page-grid', null,
    h('div.col', null, card(h('span', null, 'ATSC 1.0 · 8-VSB (A/53)', untestedBadge()), 'wave', [
      head,
      h('div.rx-grid', { tid: 'atsc-info' },
        h('div.rx-kv', null, h('small', null, T('Modulation')), h('b', null, '8-VSB')),
        h('div.rx-kv', null, h('small', null, T('Symbolrate')), h('b', null, '10,762 MBd')),
        h('div.rx-kv', null, h('small', null, T('Nutzbitrate')), h('b', null, '19,39 Mbit/s')),
        h('div.rx-kv', null, h('small', null, T('Kanal')), h('b', null, '6 MHz'))),
      h('p.hint', null, icon('info'), T('Feste Parameter: RS (207,187), Trellis 2/3, Pilot, Segment- und Feld-Sync. Programme: MPEG-2-Video mit AC-3-Ton (A/52); virtuelle Kanäle major.minor in der TVCT, Uhrzeit per STT. Kanalraster A2–A69 (USA, Kanada, Mexiko, Südkorea).')),
    ], { tid: 'card-atsc' })),
    h('div.col', null, null));
}

function pageDtmb(head) {
  const d = S.draft.dtmb;
  const crs = STD.DTMB.ONLY08.includes(d.constellation) ? ['0.8'] : STD.DTMB.CR;
  return h('div.page-grid', null,
    h('div.col', null, card(h('span', null, 'DTMB · GB 20600-2006', untestedBadge()), 'wave', [
      head,
      h('div.grid2', null,
        field(T('Träger'), segmented('dtmb.carriers', [{ v: 3780, label: T('Mehrträger (C=3780)') }, { v: 1, label: T('Einträger (C=1)') }], { tid: 'dtmb-carriers' }), { path: 'dtmb.carriers', hint: T('C=3780: TDS-OFDM; C=1: Einträger mit 36 Systemsymbolen') }),
        field(T('Rahmenkopf'), segmented('dtmb.header', STD.DTMB.PN.map(v => ({ v, label: `PN${v}` })), { tid: 'dtmb-header' }), { path: 'dtmb.header', hint: T('PN945 = längste Echos, PN420 = höchste Rate') })),
      field(T('Konstellation'), segmented('dtmb.constellation', STD.DTMB.CONST.map(v => ({ v, label: v })), { tid: 'dtmb-constellation', rerender: true,
        onchange: v => { if (STD.DTMB.ONLY08.includes(v)) S.draft.dtmb.code_rate = '0.8'; } }), { path: 'dtmb.constellation' }),
      h('div.grid2', null,
        field(T('LDPC-Coderate'), segmented('dtmb.code_rate', crs.map(v => ({ v, label: v.replace('.', ',') })), { tid: 'dtmb-coderate' }), { path: 'dtmb.code_rate' }),
        field(T('Zeitverschachtelung'), segmented('dtmb.interleaver', STD.DTMB.TI.map(v => ({ v, label: `M=${v}` })), { tid: 'dtmb-ti' }), { path: 'dtmb.interleaver' })),
      h('p.hint', null, icon('info'), T('Programme: H.264 oder MPEG-2 mit MPEG-1 Layer II oder AC-3; DVB-SI (GB/T 17975). Kanalraster DS6–DS68 (China, Hongkong, Macau, Kuba).')),
    ], { tid: 'card-dtmb' })),
    h('div.col', null, null));
}

function pageIsdbt(head) {
  const p = S.draft.isdbt, two = Number(p.layers) === 2;
  const layer = (L, segs) => h('div.isdb-layer', { tid: `isdbt-layer-${L}` },
    h('h4', null, T('Schicht {l} · {n} Segment(e)', { l: L.toUpperCase(), n: segs }), h('span.muted.mono', null, ` · ${(STD.isdbtLayerRate(segs, p['layer_' + L], p.guard) / 1e6).toLocaleString(LOC, { maximumFractionDigits: 3 })} Mbit/s`)),
    h('div.grid3', null,
      field(T('Konstellation'), segmented(`isdbt.layer_${L}.constellation`, STD.ISDBT.CONST.map(v => ({ v, label: v })), { tid: `isdbt-${L}-const` }), { path: `isdbt.layer_${L}.constellation` }),
      field(T('Coderate'), segmented(`isdbt.layer_${L}.code_rate`, STD.ISDBT.CR.map(v => ({ v, label: v })), { tid: `isdbt-${L}-cr` }), { path: `isdbt.layer_${L}.code_rate` }),
      field(T('Zeitverschachtelung'), segmented(`isdbt.layer_${L}.ti`, STD.ISDBT.TI.map(v => ({ v, label: String(v) })), { tid: `isdbt-${L}-ti` }), { path: `isdbt.layer_${L}.ti` })));
  return h('div.page-grid', null,
    h('div.col', null, card(h('span', null, 'ISDB-T / ISDB-Tb (ARIB STD-B31, ABNT NBR 15601)', untestedBadge()), 'wave', [
      head,
      field(T('Region'), segmented('isdbt.region', [{ v: 'br', label: T('ISDB-Tb (Brasilien/Lateinamerika)') }, { v: 'jp', label: T('ISDB-T (Japan)') }], { tid: 'isdbt-region', rerender: true,
        onchange: v => { const rf = S.draft.rf; if (v === 'jp') { rf.channel = 'J13'; rf.freq_hz = 473142857; } else { rf.channel = 'A14'; rf.freq_hz = 473142857; } STD.regionCodecs(S.draft, 'isdbt'); S.rfIntent.add('channel'); S.rfIntent.add('freq_hz'); } }),
        { path: 'isdbt.region', hint: p.region === 'jp' ? T('Japan: MPEG-2-Video, AAC (ADTS), Kanäle J13–J62') : T('Brasilien: H.264, AAC (LATM), Kanäle A14–A69') }),
      h('div.grid3', null,
        field(T('Modus'), segmented('isdbt.mode', STD.ISDBT.MODES.map(v => ({ v, label: `Mode ${v}` })), { tid: 'isdbt-mode' }), { path: 'isdbt.mode', hint: T('Mode 3 = 8k (5617 Träger), üblich') }),
        field(T('Guard-Intervall'), segmented('isdbt.guard', STD.ISDBT.GI.map(v => ({ v, label: v })), { tid: 'isdbt-guard' }), { path: 'isdbt.guard' }),
        field(T('Schichten'), segmented('isdbt.layers', [{ v: 1, label: T('1 (13 Segmente)') }, { v: 2, label: T('2 (1seg + 12)') }], { tid: 'isdbt-layers', rerender: true,
          // Schicht A: 1seg robust (QPSK 2/3, I=4) bzw. bei 13 Segmenten 64-QAM 3/4 – wie runner/dvb/config.py
          onchange: v => { S.draft.isdbt.layer_a = Number(v) === 2 ? { constellation: 'QPSK', code_rate: '2/3', ti: 4 } : { constellation: '64-QAM', code_rate: '3/4', ti: 2 }; } }),
          { path: 'isdbt.layers', hint: T('2 Schichten: Schicht A trägt das Handy-Programm (1seg, QPSK), Schicht B das HD-Programm') })),
      layer('a', two ? 1 : 13),
      two ? layer('b', 12) : null,
      h('div.grid2', null,
        field(T('Fernbedienungstaste'), input('isdbt.remote_key', { type: 'number', min: 1, max: 12, tid: 'isdbt-remote-key', mono: true }), { path: 'isdbt.remote_key', hint: T('virtuelle Kanalnummer (ts_information_descriptor)') }),
        field(T('Area-Code'), input('isdbt.area_code', { type: 'number', min: 0, max: 4095, tid: 'isdbt-area', mono: true }), { path: 'isdbt.area_code' })),
      h('p.hint', null, icon('info'), T('Programme: H.264 mit AAC (LATM). Bei 2 Schichten im Bereich „Programme“ ein Programm als 1seg markieren (320×240). Kanalraster A14–A69, Mitte + 1/7 MHz (Brasilien, Lateinamerika, Japan).')),
    ], { tid: 'card-isdbt' })),
    h('div.col', null, null));
}

export function lnbWarning() {
  return h('div.alert.big.lnbwarn', { tid: 'dvbs-lnb-warning' }, icon('alert'), h('div', null,
    h('b', null, T('Sat-Eingang führt 13/18 V – DC-Block bzw. LNB-Speisung am Receiver aus!')),
    h('p', null, T('Der Receiver speist über den Sat-Eingang das LNB (13 V = V, 18 V = H, 22 kHz = High-Band). Ohne DC-Block (Gleichspannungstrenner) zwischen Pluto-TX und Receiver wird der Pluto beschädigt. Zusätzlich LNB-Speisung im Receiver-Menü abschalten, wenn möglich.'))));
}

function useSwitch() {
  return field(T('Anwendung'), segmented('dvbs.use', [{ v: 'sat', label: T('Satellit (LNB, Sat-ZF)') }, { v: 'ham', label: T('Amateurfunk (DATV)') }],
    { tid: 'dvbs-use', onchange: () => {
      const d = S.draft.dvbs, rf = S.draft.rf;
      if (d.use === 'ham') {
        d.system = 'dvbs2'; d.pilots = false;
        if (d.symbol_rate > STD.DATV.SR_MAX) d.symbol_rate = 333000;
        if (!STD.DVBS.FEC_S2[d.modulation]?.includes(d.fec)) d.fec = '2/3';
        rf.channel = 'ham'; rf.freq_hz = Math.round(Number(d.ham_freq_mhz) * 1e6);
        STD.fitDatvPrograms(S.draft);
      } else {
        if (d.symbol_rate < 1e6) d.symbol_rate = 6000000;
        rf.channel = 'sat'; rf.freq_hz = STD.satIfHz(d);
      }
      S.rfIntent.add('channel'); S.rfIntent.add('freq_hz');
    } }),
    { path: 'dvbs.use', hint: T('DATV sendet direkt auf der Amateurfunkfrequenz, IQ per USB – nur mit Amateurfunkzulassung') });
}

function pageDatv(head) {
  const d = S.draft.dvbs;
  const b = STD.DATV.BANDS[d.ham_band] || STD.DATV.BANDS.qo100;
  const syncF = () => { S.draft.rf.channel = 'ham'; S.draft.rf.freq_hz = Math.round(Number(S.draft.dvbs.ham_freq_mhz) * 1e6); S.rfIntent.add('channel'); S.rfIntent.add('freq_hz'); };
  const fecs = d.system === 'dvbs2' ? STD.DVBS.FEC_S2[d.modulation] : STD.DVBS.FEC_S;
  const bw = STD.datvBw(d), sps = STD.datvSps(d.symbol_rate);
  const dl = d.ham_band === 'qo100' ? Number(d.ham_freq_mhz) + STD.DATV.QO100_DL_OFFSET_MHZ : null;
  const srIn = h('input.mono', { type: 'number', min: STD.DATV.SR_MIN / 1000, max: STD.DATV.SR_MAX / 1000, step: 1, tid: 'datv-symbolrate' });
  srIn.value = Math.round(d.symbol_rate / 1000);
  srIn.addEventListener('change', () => { S.draft.dvbs.symbol_rate = Math.round(Number(srIn.value)) * 1000; STD.fitDatvPrograms(S.draft); edited('dvbs.symbol_rate', true); });
  const csIn = input('dvbs.callsign', { tid: 'datv-callsign', maxlength: 12, mono: true, ph: 'DL1ABC', parse: x => String(x || '').toUpperCase().trim(),
    // Stationskennung: Rufzeichen als Anbieter aller Programme (Name bleibt frei wählbar)
    oninput: v => { for (const p of S.draft.dvb?.programs || []) p.provider = String(v || '').split('/')[0]; } });
  const fix = () => { const l = S.draft.dvbs.system === 'dvbs2' ? STD.DVBS.FEC_S2[S.draft.dvbs.modulation] : STD.DVBS.FEC_S; if (!l.includes(S.draft.dvbs.fec)) S.draft.dvbs.fec = l.includes('2/3') ? '2/3' : l[0]; if (S.draft.dvbs.system === 'dvbs') { S.draft.dvbs.modulation = 'QPSK'; S.draft.dvbs.rolloff = 0.35; S.draft.dvbs.pilots = false; } };
  return h('div.page-stack', null,
    h('div.alert', { tid: 'datv-license' }, icon('alert'), h('div', null,
      h('b', null, T('Amateurfunk: Senden nur mit gültiger Zulassung und eigenem Rufzeichen.')),
      h('p', null, T('Der Pluto liefert höchstens +7 dBm; für QO-100 folgen Filter, Endstufe und Spiegel. Bandplan, Leistungsgrenzen und die Regeln des Transponders (BATC/AMSAT-DL) beachten. Stationskennung: das Rufzeichen steht im Programm-/Anbieternamen.')))),
    h('div.page-grid', null,
      h('div.col', null, card(T('Band & Station'), 'antenna', [
        useSwitch(),
        h('div.bigread', null,
          h('div', null, h('small', null, T('Sendefrequenz')), h('b.mono', { tid: 'datv-freq-read' }, `${D.fmtNum(Number(d.ham_freq_mhz), 3)}`, h('em', null, ' MHz'))),
          h('div', null, h('small', null, dl ? 'QO-100-Downlink' : T('belegte Bandbreite')), h('b.mono', { tid: 'datv-dl' }, dl ? `${D.fmtNum(dl, 3)}` : `${D.fmtNum(bw / 1e3, 0)}`, h('em', null, dl ? ' MHz' : ' kHz')))),
        h('div.grid2', null,
          field(T('Band'), select('dvbs.ham_band', Object.entries(STD.DATV.BANDS).map(([k, v]) => ({ v: k, label: `${v.label} (${v.lo}–${v.hi} MHz)` })),
            { tid: 'datv-band', onchange: () => { const nb = STD.DATV.BANDS[S.draft.dvbs.ham_band]; S.draft.dvbs.ham_freq_mhz = nb.def; syncF(); } }), { path: 'dvbs.ham_band', hint: b.hint }),
          field(T('Frequenz (MHz)'), input('dvbs.ham_freq_mhz', { type: 'number', min: b.lo, max: b.hi, step: 0.0005, tid: 'datv-freq', mono: true, oninput: syncF, rerender: true }),
            { path: 'dvbs.ham_freq_mhz', hint: T('Mitte des Signals; belegt {khz} kHz (SR · (1 + α))', { khz: D.fmtNum(bw / 1e3, 0) }) })),
        field(T('Rufzeichen'), csIn, { path: 'dvbs.callsign', hint: T('Pflicht: Stationskennung im Programm (Anbieter) und im NIT-Netznamen') }),
      ], { tid: 'card-datv' })),
      h('div.col', null, card(T('Modulation'), 'wave', [
        head,
        field(T('System'), segmented('dvbs.system', Object.entries(STD.DVBS.SYSTEMS).map(([k, l]) => ({ v: k, label: l.split(' (')[0] })), { tid: 'dvbs-system', onchange: fix }), { path: 'dvbs.system' }),
        field(T('Symbolrate (kS/s)'), h('div.inrow.wrap', null, srIn, ...STD.DATV.SR_PRESETS.map(sr => h('button.btn.sm', { type: 'button', class: d.symbol_rate === sr ? 'accent' : null, tid: `datv-sr-${sr}`, onclick: () => { S.draft.dvbs.symbol_rate = sr; STD.fitDatvPrograms(S.draft); edited('dvbs.symbol_rate', true); } }, `${sr / 1000}`))),
          { path: 'dvbs.symbol_rate', hint: T('QO-100 üblich: 333/500/1000/1500 kS/s · USB-IQ {msps} MS/s ({sps} Samples/Symbol)', { msps: D.fmtNum(d.symbol_rate * sps / 1e6, 3), sps }) }),
        h('div.grid2', null,
          field(T('Modulation'), segmented('dvbs.modulation', [{ v: 'QPSK', label: 'QPSK' }, { v: '8PSK', label: '8PSK', disabled: d.system === 'dvbs' }], { tid: 'dvbs-modulation', onchange: fix }), { path: 'dvbs.modulation' }),
          field('FEC', select('dvbs.fec', fecs.map(f => ({ v: f, label: f })), { tid: 'dvbs-fec' }), { path: 'dvbs.fec', hint: d.system === 'dvbs' ? 'Viterbi + RS(204,188)' : 'LDPC + BCH' })),
        d.system === 'dvbs2' ? h('div.grid2', null,
          field('Rolloff', segmented('dvbs.rolloff', STD.DVBS.ROLLOFF.map(v => ({ v, label: String(v).replace('.', ',') })), { tid: 'dvbs-rolloff' }), { path: 'dvbs.rolloff' }),
          field('Pilots', toggle('dvbs.pilots', T('Pilotblöcke'), { tid: 'dvbs-pilots' }), { hint: T('MiniTiouner/Longmynd: meist aus') })) : h('p.muted.small', null, T('DVB-S: Rolloff 0,35, keine Pilots.')),
        h('p.hint', null, icon('info'), T('Nutzrate ≈ {kbps} kbit/s – Video sparsam (H.264/H.265, 640×360 oder 1280×720), Ton mono 32 kbit/s.', { kbps: D.fmtNum(STD.netRate(S.draft) / 1e3, 0) })),
      ], { tid: 'card-dvbs' }))));
}

function pageDvbs(head) {
  if (STD.isHam(S.draft)) return pageDatv(head);
  const d = S.draft.dvbs;
  const ifHz = STD.satIfHz(d);
  const syncIf = () => { S.draft.rf.channel = 'sat'; S.draft.rf.freq_hz = STD.satIfHz(S.draft.dvbs); S.rfIntent.add('channel'); S.rfIntent.add('freq_hz'); };
  const band = Number(d.lof_mhz) === 9750 ? T('Low-Band, 22 kHz aus') : Number(d.lof_mhz) === 10600 ? T('High-Band, 22 kHz an') : d.lof_mhz < 9000 ? T('C-Band-LOF') : T('Sonder-LOF');
  const fecs = d.system === 'dvbs2' ? STD.DVBS.FEC_S2[d.modulation] : STD.DVBS.FEC_S;
  const srIn = h('input.mono', { type: 'number', min: 1000, max: 30000, step: 1, tid: 'dvbs-symbolrate' });
  srIn.value = Math.round(d.symbol_rate / 1000);
  srIn.addEventListener('change', () => { S.draft.dvbs.symbol_rate = Math.round(Number(srIn.value)) * 1000; edited('dvbs.symbol_rate', true); });
  const fix = () => { const l = S.draft.dvbs.system === 'dvbs2' ? STD.DVBS.FEC_S2[S.draft.dvbs.modulation] : STD.DVBS.FEC_S; if (!l.includes(S.draft.dvbs.fec)) S.draft.dvbs.fec = l.includes('3/4') ? '3/4' : l[0]; if (S.draft.dvbs.system === 'dvbs') { S.draft.dvbs.modulation = 'QPSK'; S.draft.dvbs.rolloff = 0.35; S.draft.dvbs.pilots = false; } };
  return h('div.page-stack', null,
    lnbWarning(),
    h('div.page-grid', null,
      h('div.col', null, card(T('Transponder & LNB'), 'antenna', [
        useSwitch(),
        h('div.bigread', null,
          h('div', null, h('small', null, T('Transponder')), h('b.mono', { tid: 'dvbs-transponder-read' }, `${D.fmtNum(d.transponder_mhz, 0)} MHz ${d.polarization}`)),
          h('div', null, h('small', null, T('Sat-ZF = Sendefrequenz')), h('b.mono', { tid: 'dvbs-if' }, `${D.fmtNum(ifHz / 1e6, 0)}`, h('em', null, ' MHz')))),
        h('div.grid2', null,
          field(T('Transponderfrequenz (MHz)'), input('dvbs.transponder_mhz', { type: 'number', min: 3400, max: 21200, step: 1, tid: 'dvbs-transponder', mono: true, oninput: syncIf, rerender: true }), { path: 'dvbs.transponder_mhz', hint: T('wie im Receiver-Suchlauf (z. B. 11300)') }),
          field('LOF (MHz)', h('div.inrow', null, input('dvbs.lof_mhz', { type: 'number', min: 5000, max: 11500, step: 1, tid: 'dvbs-lof', mono: true, oninput: syncIf, rerender: true }),
            (() => { const sl = h('select', { tid: 'dvbs-lof-preset' }, h('option', { value: '' }, T('Preset …')), STD.DVBS.LOF_PRESETS.map(v => h('option', { value: v }, `${v}`))); sl.addEventListener('change', () => { if (sl.value) { S.draft.dvbs.lof_mhz = Number(sl.value); S.draft.dvbs.tone_22khz = Number(sl.value) === 10600; syncIf(); edited('dvbs.lof_mhz', true); } }); return sl; })()),
            { path: 'dvbs.lof_mhz', hint: T('Universal-LNB: 9750 Low / 10600 High') })),
        h('div.grid2', null,
          field(T('Polarisation'), segmented('dvbs.polarization', [{ v: 'H', label: 'H (18 V)' }, { v: 'V', label: 'V (13 V)' }], { tid: 'dvbs-pol' }), { path: 'dvbs.polarization', hint: T('nur für NIT/Receiver-Eintrag; der Pluto sendet direkt auf der ZF') }),
          field(T('22 kHz / Band'), toggle('dvbs.tone_22khz', `22 kHz (${band})`, { tid: 'dvbs-22khz' }), { hint: T('muss zum LOF passen, sonst stimmt der Receiver falsch ab') })),
        h('p.hint', null, icon('info'), T('ZF {zf} MHz = {tp} − {lof} MHz; der Receiver muss mit derselben LOF ({band}) arbeiten.', { zf: D.fmtNum(ifHz / 1e6, 0), tp: D.fmtNum(d.transponder_mhz, 0), lof: D.fmtNum(d.lof_mhz, 0), band })),
      ], { tid: 'card-dvbs-lnb' })),
      h('div.col', null, card(T('Modulation'), 'wave', [
        head,
        field(T('System'), segmented('dvbs.system', Object.entries(STD.DVBS.SYSTEMS).map(([k, l]) => ({ v: k, label: l.split(' (')[0] })), { tid: 'dvbs-system', onchange: fix }), { path: 'dvbs.system' }),
        field(T('Symbolrate (kS/s)'), h('div.inrow.wrap', null, srIn, ...STD.DVBS.SR_PRESETS.map(sr => h('button.btn.sm', { type: 'button', class: d.symbol_rate === sr ? 'accent' : null, tid: `dvbs-sr-${sr}`, onclick: () => { S.draft.dvbs.symbol_rate = sr; edited('dvbs.symbol_rate', true); } }, `${sr / 1000}`))),
          { path: 'dvbs.symbol_rate', hint: T('USB-Streaming bis ≈ 3,5 MS/s, darüber Modulator auf dem Pluto') }),
        h('div.grid2', null,
          field(T('Modulation'), segmented('dvbs.modulation', [{ v: 'QPSK', label: 'QPSK' }, { v: '8PSK', label: '8PSK', disabled: d.system === 'dvbs' }], { tid: 'dvbs-modulation', onchange: fix }), { path: 'dvbs.modulation' }),
          field('FEC', select('dvbs.fec', fecs.map(f => ({ v: f, label: f })), { tid: 'dvbs-fec' }), { path: 'dvbs.fec', hint: d.system === 'dvbs' ? 'Viterbi + RS(204,188)' : 'LDPC + BCH' })),
        d.system === 'dvbs2' ? h('div.grid2', null,
          field('Rolloff', segmented('dvbs.rolloff', STD.DVBS.ROLLOFF.map(v => ({ v, label: String(v).replace('.', ',') })), { tid: 'dvbs-rolloff' }), { path: 'dvbs.rolloff' }),
          field('Pilots', toggle('dvbs.pilots', T('Pilotblöcke'), { tid: 'dvbs-pilots' }), { hint: T('robuster gegen Phasenrauschen') })) : h('p.muted.small', null, T('DVB-S: Rolloff 0,35, keine Pilots.')),
      ], { tid: 'card-dvbs' }))));
}

function rateTable(std) {
  if (std === 'dvbt') {
    const p = S.draft.dvbt;
    const rows = STD.DVBT.CONST.flatMap(c => STD.DVBT.CR.map(cr => ({ c, cr, r: STD.dvbtNetRate({ ...p, constellation: c, code_rate: cr }) })));
    return card(T('Nutzbitraten (aktuelle Bandbreite/Guard)'), 'table', h('div.tblwrap', null, h('table.tbl.compact', { tid: 'dvbt-ratetable' },
      h('thead', null, h('tr', null, h('th', null, T('Konstellation')), ...STD.DVBT.CR.map(cr => h('th.r', null, cr)))),
      h('tbody', null, STD.DVBT.CONST.map(c => h('tr', null, h('td', null, c), ...STD.DVBT.CR.map(cr => { const x = rows.find(r => r.c === c && r.cr === cr); const cur = c === p.constellation && cr === p.code_rate; return h('td.r.mono' + (cur ? '.accent' : ''), { style: cur ? { fontWeight: 700 } : null }, (x.r / 1e6).toFixed(2)); })))))));
  }
  const p = S.draft.dvbc;
  return card(T('Nutzbitraten (Mbit/s)'), 'table', h('div.tblwrap', null, h('table.tbl.compact', { tid: 'dvbc-ratetable' },
    h('thead', null, h('tr', null, h('th', null, T('Symbolrate')), ...STD.DVBC.CONST.map(c => h('th.r', null, c)))),
    h('tbody', null, [...new Set([...STD.DVBC.SR_PRESETS, p.symbol_rate])].sort((a, b) => a - b).map(sr => h('tr', null, h('td.mono', null, `${(sr / 1e6).toFixed(3)} MBd`),
      ...STD.DVBC.CONST.map(c => { const cur = sr === p.symbol_rate && c === p.constellation; return h('td.r.mono' + (cur ? '.accent' : ''), { style: cur ? { fontWeight: 700 } : null }, (STD.dvbcNetRate({ symbol_rate: sr, constellation: c }) / 1e6).toFixed(2)); })))))));
}

// ---------------------------------------------------------------- TS-Budget
export function tsBudgetBar(o = {}) {
  const b = STD.tsBudget(S.draft);
  const pct = x => `${Math.min(100, x / Math.max(b.net, b.used) * 100).toFixed(2)}%`;
  const bar = h('div.capbar' + (o.big ? '.big' : '.mini') + (b.over ? '.over' : ''), { tid: o.tid || 'ts-bar' },
    b.items.map((it, i) => h('i', { style: { width: pct(it.bps), background: prgColor(i) }, title: `${it.name}: ${fmtMbit(it.bps)}` }, o.big && it.bps / b.net > 0.12 ? h('span', null, it.name) : null)),
    h('i.si', { style: { width: pct(b.si) }, title: T('SI-Tabellen: {rate}', { rate: fmtMbit(b.si) }) }, o.big ? h('span', null, 'SI') : null),
    b.over ? h('b.limit', { style: { left: pct(b.net) } }) : null);
  return h('div.tsbudget' + (b.over ? '.over' : ''), null, bar,
    h('small', null, h('b', null, fmtMbit(b.used)), ' ' + T('von {net}', { net: fmtMbit(b.net) }) + ' · ', b.over ? h('b.bad', null, T('{rate} zu viel', { rate: fmtMbit(b.used - b.net) })) : T('{rate} Nullpakete ({pct} %)', { rate: fmtMbit(b.nulls), pct: Math.round((1 - b.pct) * 100) })));
}

// ---------------------------------------------------------------- Programme
const PRG_TABS = [['general', T('Allgemein'), 'id'], ['video', T('Video'), 'tv'], ['audio', T('Audio'), 'music'], ['eit', 'EIT', 'text']];

export function pagePrograms() {
  const prgs = S.draft.dvb.programs;
  if (S.prgSel == null || S.prgSel >= prgs.length) S.prgSel = Math.max(0, prgs.length - 1);
  S.prgTab ||= 'general';
  const b = STD.tsBudget(S.draft);
  const list = h('div.svclist', { tid: 'prg-list' }, prgs.map((p, i) => {
    const errs = errorsFor(`dvb.programs.${i}`).length;
    return h('button.svcitem', { type: 'button', class: i === S.prgSel ? 'on' : null, tid: `prg-item-${i}`, onclick: () => { S.prgSel = i; S.render(); } },
      h('i.dot', { style: { background: prgColor(i) } }),
      h('div.si-main', null, h('b', null, p.name || T('(ohne Namen)')), h('small.mono', null, `LCN ${p.lcn} · ${p.sid} · ${p.type === 'radio' ? 'Radio' : `${STD.VIDEO.CODECS[p.video.codec]?.split(' ')[0]} ${p.video.resolution.toUpperCase()}`} · ${STD.VIDEO.AUDIO[p.audio.codec]?.split(' ')[0]}`)),
      h('span.si-cu', null, (b.items[i].bps / 1e6).toFixed(1), h('small', null, ' Mbit/s')),
      errs ? h('span.errdot', { title: T('{n} Fehler', { n: errs }) }, errs) : null);
  }));
  const addBtn = h('button.btn.primary', { type: 'button', tid: 'prg-add', onclick: () => addProgram() }, icon('plus'), T('Programm'));
  const aside = h('aside.svcside', null,
    h('div.svcside-h', null, h('h3', null, T('Programme') + ' ', h('span.muted', null, `(${prgs.length})`)), addBtn),
    tsBudgetBar({ tid: 'ts-bar-mini' }), list);
  const p = prgs[S.prgSel];
  if (!p) return h('div.svc-layout', null, aside, h('div.empty', null, icon('tv', 'big'), h('p', null, T('Kein Programm – lege eines an.'))));
  const base = `dvb.programs.${S.prgSel}`;
  const tabs = PRG_TABS.filter(([k]) => p.type === 'radio' ? k !== 'video' : true);
  if (!tabs.some(([k]) => k === S.prgTab)) S.prgTab = 'general';
  const tabBar = h('div.subtabs', { role: 'tablist' }, tabs.map(([k, label, ic]) => h('button', { type: 'button', role: 'tab', class: k === S.prgTab ? 'on' : null, tid: `prg-tab-${k}`, onclick: () => { S.prgTab = k; S.render(); } }, icon(ic), h('span', null, label))));
  const body = { general: prgGeneral, video: prgVideo, audio: prgAudio, eit: prgEit }[S.prgTab](p, base);
  const head = h('div.svchead', null,
    h('i.dot.lg', { style: { background: prgColor(S.prgSel) } }),
    h('div', null, h('h2', null, p.name), h('small.mono.muted', null, `${p.key} · SID ${p.sid} · LCN ${p.lcn} · ${fmtMbit(b.items[S.prgSel].bps)}`)),
    h('span.grow'),
    h('button.btn.ghost', { type: 'button', tid: 'prg-dup', onclick: () => addProgram(structuredClone(p)) }, icon('copy'), h('span.hide-sm', null, T('Duplizieren'))),
    h('button.btn.ghost.danger-text', { type: 'button', tid: 'prg-del', onclick: async () => {
      if (!(await dialog({ title: T('Programm löschen?'), body: h('p', null, T('„{name}“ wird aus dem Transportstrom entfernt.', { name: p.name })), ok: T('Löschen'), danger: true, tid: 'confirm-prg-del' }))) return;
      prgs.splice(S.prgSel, 1); S.prgSel = Math.max(0, S.prgSel - 1); edited('dvb.programs', true);
    } }, icon('trash'), h('span.hide-sm', null, T('Löschen'))));
  return h('div.page-stack', null,
    card(T('Transportstrom-Belegung'), 'pie', [tsBudgetBar({ big: true, tid: 'ts-bar' }),
      b.over ? h('div.alert.big', { tid: 'ts-over' }, icon('alert'), h('div', null, h('b', null, T('Transportstrom um {rate} überbucht – Übernehmen ist gesperrt.', { rate: fmtMbit(b.used - b.net) })),
        h('p', null, T('Nutzbitrate steigt mit Konstellation/Coderate (DVB-T) bzw. Symbolrate/QAM (DVB-C); sonst Video-/Audiobitraten senken oder ein Programm entfernen. SI-Tabellen brauchen ≈ 60 kbit/s, Rest wird mit Nullpaketen aufgefüllt (CBR).')))) : null], { tid: 'card-ts' }),
    h('div.svc-layout', null, aside, h('div.svcedit', { tid: 'prg-editor' }, head, tabBar, h('div.svcbody', null, body))));
}

function addProgram(tpl) {
  const prgs = S.draft.dvb.programs;
  let n = prgs.length + 1; while (prgs.some(x => x.key === `prg${n}`)) n++;
  const p = tpl || STD.newProgram(`prg${n}`, n);
  p.key = `prg${n}`;
  let sid = D.parseHex(p.sid) || 0x1000; const used = new Set(prgs.map(x => D.parseHex(x.sid))); while (used.has(sid)) sid++;
  p.sid = D.hex(sid, 4);
  let lcn = Number(p.lcn) || 1; const usedL = new Set(prgs.map(x => Number(x.lcn))); while (usedL.has(lcn)) lcn++;
  p.lcn = lcn;
  if (tpl) p.name = [...T('{name} Kopie', { name: p.name })].slice(0, 40).join('');
  prgs.push(p); S.prgSel = prgs.length - 1; S.prgTab = 'general';
  edited('dvb.programs', true);
}

function prgGeneral(p, base) {
  return h('div.formgrid', null,
    card(T('Dienst (SDT/PAT/PMT)'), null, [
      h('div.grid2', null,
        field(T('Name (service_name)'), input(`${base}.name`, { tid: 'prg-name', maxlength: 40 }), { path: `${base}.name`, extra: counter(() => [...(p.name || '')].length, 40) }),
        field(T('Anbieter (service_provider)'), input(`${base}.provider`, { tid: 'prg-provider', maxlength: 40 }))),
      h('div.grid3', null,
        field('SID (hex)', input(`${base}.sid`, { tid: 'prg-sid', mono: true, hex: 4 }), { path: `${base}.sid`, hint: 'program_number' }),
        field('LCN', input(`${base}.lcn`, { type: 'number', min: 1, max: 999, tid: 'prg-lcn', mono: true }), { path: `${base}.lcn`, hint: 'EACEM logical_channel_number' }),
        field(T('Schlüssel'), input(`${base}.key`, { tid: 'prg-key', mono: true, parse: v => v.toLowerCase() }), { path: `${base}.key` })),
      field(T('Typ'), segmented(`${base}.type`, [{ v: 'tv', label: T('Fernsehen') }, { v: 'radio', label: 'Radio' }], { tid: 'prg-type' }), { hint: T('service_type 0x01/0x19 (TV SD/HD) bzw. 0x02 (Radio)') }),
      STD.standardOf(S.draft) === 'isdbt' && Number(S.draft.isdbt.layers) === 2
        ? field('1seg', toggle(`${base}.oneseg`, T('Handy-Programm in Schicht A (320×240)'), { tid: 'prg-oneseg' }), { hint: T('Teilempfang (partial reception), service_type 0xC0') }) : null,
      STD.standardOf(S.draft) === 'isdbt' ? h('p.hint', null, icon('info'), T('ISDB-Tb: SID wird aus Netzkennung, Diensttyp und Nummer gebildet (ABNT NBR 15603).')) : null,
    ]));
}

function prgVideo(p, base) {
  const v = p.video, src = v.source, ps = `${base}.video.source`;
  const brs = STD.VIDEO.VIDEO_BR[v.codec]?.[v.resolution] || [3500];
  const kinds = [['testpattern', T('Testbild'), 'tv'], ['file', T('Datei'), 'file'], ['youtube', 'YouTube', 'download'], ['playlist', T('Video-Playlist'), 'playlist'], ['capture', T('Capture/Webcam'), 'radio'], ['screen', T('Bildschirm'), 'grid']];
  const params = [];
  const dev = S.state.devices || {};
  if (src.kind === 'capture') {
    const vids = dev.video || [], auds = dev.audio || [];
    const dsel = h('select', { tid: 'vid-capture-device' }, h('option', { value: '' }, vids.length ? T('Gerät wählen …') : T('Keine Geräte gemeldet (state.devices.video)')), vids.map(d => h('option', { value: typeof d === 'string' ? d : d.name }, typeof d === 'string' ? d : d.name)));
    dsel.value = src.device || ''; if (src.device && dsel.selectedIndex < 0) { dsel.append(h('option', { value: src.device }, src.device)); dsel.value = src.device; }
    dsel.addEventListener('change', () => { src.device = dsel.value; edited(`${ps}.device`); });
    const asel = h('select', { tid: 'vid-capture-audio' }, h('option', { value: '' }, T('kein Ton vom Gerät')), auds.map(d => h('option', { value: typeof d === 'string' ? d : d.name }, typeof d === 'string' ? d : d.name)));
    asel.value = src.audio_device || ''; asel.addEventListener('change', () => { src.audio_device = asel.value; edited(`${ps}.audio_device`); });
    params.push(field(T('Videogerät (DirectShow)'), dsel, { path: `${ps}.device`, hint: T('ffmpeg -f dshow -i video="…" (Capture-Karte, Webcam)') }));
    params.push(field(T('Tongerät'), asel, { hint: T('sonst Tonquelle im Reiter Audio') }));
    params.push(field(T('Eingangsformat'), h('div.inrow', null, input(`${ps}.size`, { tid: 'vid-capture-size', mono: true, ph: '1920x1080' }), input(`${ps}.fps`, { type: 'number', min: 1, max: 60, tid: 'vid-capture-fps', mono: true })), { hint: T('Auflösung · Bilder/s des Geräts') }));
  }
  if (src.kind === 'screen') {
    const scr = dev.screens || [];
    params.push(field(T('Bildschirm'), select(`${ps}.display`, [{ v: '', label: T('Hauptbildschirm') }, ...scr.map(d => ({ v: typeof d === 'string' ? d : d.name, label: typeof d === 'string' ? d : d.name }))], { tid: 'vid-screen-display', rerender: false }), { hint: 'ffmpeg gdigrab/ddagrab (Windows)' }));
    params.push(field(T('Ausschnitt (x,y,b,h)'), input(`${ps}.region`, { tid: 'vid-screen-region', mono: true, ph: T('leer = ganzer Bildschirm') })));
    params.push(field(T('Bilder/s'), input(`${ps}.fps`, { type: 'number', min: 1, max: 60, tid: 'vid-screen-fps', mono: true })));
    params.push(field(T('Mauszeiger'), toggle(`${ps}.cursor`, T('einblenden'), { tid: 'vid-screen-cursor' })));
  }
  if (src.kind === 'testpattern') {
    params.push(field(T('Muster'), select(`${ps}.pattern`, Object.entries(STD.VIDEO.PATTERNS).map(([k, l]) => ({ v: k, label: l })), { tid: 'vid-pattern', rerender: false }), { hint: T('ffmpeg smptebars/testsrc + Ton aus dem Audio-Reiter') }));
  }
  if (src.kind === 'file') {
    const vids = S.media.filter(m => m.kind === 'video' || /\.(mp4|ts|mkv|mpg|mpeg|mov|m2ts)$/i.test(m.name));
    const fsel = h('select', { tid: 'vid-file' }, h('option', { value: '' }, vids.length ? T('Datei wählen …') : T('Keine Videodateien – hochladen')), vids.map(m => h('option', { value: m.path }, m.name)));
    fsel.value = src.file || ''; if (src.file && fsel.selectedIndex < 0) { fsel.append(h('option', { value: src.file }, src.file)); fsel.value = src.file; }
    fsel.addEventListener('change', () => { src.file = fsel.value; edited(`${ps}.file`); });
    params.push(field(T('Videodatei'), h('div.inrow', null, fsel, uploadBtn('video', 'video/*,.ts,.mkv,.mpg', 'vid-upload', async path => { src.file = path; edited(`${ps}.file`, true); })), { path: `${ps}.file`, wide: true, hint: T('MP4/TS/MKV … wird per ffmpeg transkodiert') }));
    params.push(field(T('Wiederholen'), toggle(`${ps}.loop`, T('Endlosschleife'), { tid: 'vid-loop' })));
  }
  if (src.kind === 'youtube') params.push(youtubeField(src, ps));
  if (src.kind === 'playlist') {
    const psel = h('select', { tid: 'vid-playlist' }, h('option', { value: '' }, T('Playlist wählen …')), S.playlists.map(pl => h('option', { value: pl.name }, pl.count != null ? `${pl.name} (${pl.count})` : pl.name)));
    psel.value = src.playlist || ''; psel.addEventListener('change', () => { src.playlist = psel.value; edited(`${ps}.playlist`); });
    params.push(field(T('Video-Playlist'), h('div.inrow', null, psel, h('a.btn', { href: '#/medien' }, icon('playlist'), T('Bearbeiten'))), { path: `${ps}.playlist`, wide: true, hint: T('Playlist mit Videodateien (Bereich Medien)') }));
  }
  return h('div.formgrid', null,
    card(T('Videoquelle'), 'tv', [
      h('div.srcgrid', { tid: 'vid-kind' }, kinds.map(([k, l, ic]) => h('button', { type: 'button', class: src.kind === k ? 'on' : null, tid: `vid-kind-${k}`, onclick: () => { src.kind = k; edited(`${ps}.kind`, true); } }, icon(ic), h('span', null, l)))),
      params.length ? h('div.grid2', null, params) : null,
    ], { tid: 'card-video-source' }),
    card(T('Videocodierung'), 'wave', [
      h('div.grid3', null,
        field('Codec', segmented(`${base}.video.codec`, Object.entries(STD.VIDEO.CODECS).map(([k, l]) => ({ v: k, label: l.split(' ')[0] })), { tid: 'vid-codec', onchange: () => { if (p.video.codec !== 'hevc') p.video.hdr = 'sdr'; if (p.video.codec === 'mpeg2') { p.video.encoder = 'cpu'; if (p.video.resolution === 'uhd') p.video.resolution = 'hd'; } fixVideoBr(p); } }), { path: `${base}.video.codec`, hint: T('MPEG-2 = maximal kompatibel, H.264 für HD, HEVC für UHD/HDR') }),
        field(T('Auflösung'), select(`${base}.video.resolution`, Object.entries(STD.VIDEO.RES).map(([k, l]) => ({ v: k, label: l })), { tid: 'vid-res', onchange: () => { if (p.video.resolution === 'uhd' && p.video.codec !== 'mpeg2' && nvencState()) p.video.encoder = 'nvenc'; fixVideoBr(p); } }), { path: `${base}.video.resolution` }),
        field(T('Bitrate (kbit/s)'), h('div.inrow', null, input(`${base}.video.bitrate_kbps`, { type: 'number', min: 300, max: 30000, step: 100, tid: 'vid-bitrate', mono: true, rerender: true }),
          (() => { const s = h('select', { tid: 'vid-bitrate-preset' }, h('option', { value: '' }, T('Vorschlag …')), brs.map(b => h('option', { value: b }, `${b}`)));
            s.addEventListener('change', () => { if (s.value) { p.video.bitrate_kbps = Number(s.value); edited(`${base}.video.bitrate_kbps`, true); } }); return s; })()),
          { path: `${base}.video.bitrate_kbps`, hint: v.codec === 'mpeg2' && v.resolution !== 'sd' ? T('MPEG-2 HD nur bei neueren Receivern') : '' })),
      h('div.grid2', null,
        field(T('Encoder'), segmented(`${base}.video.encoder`, Object.entries(STD.VIDEO.ENCODERS).map(([k, l]) => ({ v: k, label: k === 'cpu' ? 'CPU' : 'GPU (NVENC)', disabled: k === 'nvenc' && (v.codec === 'mpeg2' || nvencState() === false) })), { tid: 'vid-encoder' }),
          { path: `${base}.video.encoder`, hint: nvencHint() }),
        field(T('Dynamikumfang'), segmented(`${base}.video.hdr`, Object.entries(STD.VIDEO.HDR).map(([k, l]) => ({ v: k, label: k === 'sdr' ? 'SDR' : k === 'hdr10' ? 'HDR10' : 'HLG', title: l })), { tid: 'vid-hdr', onchange: val => { if (val !== 'sdr') { p.video.codec = 'hevc'; if (nvencState()) p.video.encoder = 'nvenc'; fixVideoBr(p); } } }),
          { path: `${base}.video.hdr`, hint: v.hdr === 'hdr10' ? T('HEVC Main10, PQ, BT.2020, Mastering-Metadaten (SEI)') : v.hdr === 'hlg' ? 'HEVC Main10, HLG (ARIB STD-B67), BT.2020' : 'BT.709, 8 bit' })),
      v.hdr !== 'sdr' && ['testpattern'].includes(v.source.kind) ? h('p.hint', null, icon('info'), T('Testbild in HDR: wird in BT.2020/10 bit gewandelt – für echte HDR-Prüfung eine HDR-Quelle (Datei/YouTube) verwenden.')) : null,
    ]));
}
function nvencState() { const d = S.state.devices?.nvenc ?? S.state.runner?.tools?.nvenc ?? S.state.mac?.tools?.nvenc; return d == null ? null : typeof d === 'object' ? !!(d.ok ?? d.available) : !!d; }
function nvencHint() { const d = S.state.devices?.nvenc; const st = nvencState(); return st === false ? T('Server meldet kein NVENC – CPU verwenden') : st ? T('NVENC verfügbar{gpu}; entlastet die CPU bei HD/UHD', { gpu: d && d.gpu ? ` (${d.gpu})` : '' }) : T('NVENC-Verfügbarkeit noch nicht gemeldet'); }
function fixVideoBr(p) { const l = STD.VIDEO.VIDEO_BR[p.video.codec]?.[p.video.resolution]; if (l && !l.includes(p.video.bitrate_kbps)) p.video.bitrate_kbps = l[1]; }

/** YouTube-URL mit Download-Fortschritt (Server: POST /api/youtube {url} → {job}; Zustand state.jobs[job] oder GET /api/youtube/<job>) */
export function youtubeField(src, ps) {
  const urlIn = input(`${ps}.url`, { tid: 'vid-yt-url', ph: 'https://www.youtube.com/watch?v=…' });
  const prog = h('div.progress.thin', { tid: 'vid-yt-progress' }, h('i'));
  const stat = h('small.muted', { tid: 'vid-yt-status' }, src.file ? T('geladen: {file}', { file: src.file }) : T('noch nicht geladen'));
  let timer = null;
  const show = j0 => {
    const j = { ...j0, status: { downloading: 'running', finished: 'done' }[j0.status] || j0.status };
    if (typeof j.speed === 'number' || j.speed_bps != null) { const bps = Number(j.speed ?? j.speed_bps); j.speed = bps > 1e6 ? `${(bps / 1048576).toFixed(1)} MiB/s` : `${Math.round(bps / 1024)} KiB/s`; }
    const pct = Math.max(0, Math.min(100, Number(j.percent) || 0));
    prog.firstChild.style.width = `${pct}%`; prog.classList.toggle('run', j.status === 'running' || j.status === 'queued');
    stat.textContent = j.status === 'done' ? T('geladen: {file}', { file: j.file }) + (j.title ? ` – ${j.title}` : '') : j.status === 'error' ? T('Fehler: {msg}', { msg: j.error || T('unbekannt') }) : `${j.status === 'queued' ? T('wartet') : T('lädt')} ${pct.toFixed(0)} %${j.speed ? ` · ${j.speed}` : ''}${j.eta_s != null ? ' · ' + T('noch {s} s', { s: Math.ceil(j.eta_s) }) : ''}`;
    stat.classList.toggle('bad', j.status === 'error');
    if (j.status === 'done' && j.file && src.file !== j.file) { src.file = j.file; src.title = j.title || ''; edited(`${ps}.file`); loadMedia(); }
  };
  const poll = async id => {
    clearInterval(timer);
    timer = setInterval(async () => {
      const j = S.state.jobs?.[id] || await api('GET', `/api/youtube/${encodeURIComponent(id)}`).catch(() => null);
      if (!j) return;
      show(j);
      const st = { downloading: 'running', finished: 'done' }[j.status] || j.status;
      if (st === 'done' || st === 'error') { clearInterval(timer); if (st === 'done') toast(T('YouTube-Video geladen'), 'ok'); }
    }, 700);
  };
  if (src.job) poll(src.job);
  const start = h('button.btn.primary', { type: 'button', tid: 'vid-yt-start', onclick: async () => {
    if (!/^https?:\/\//i.test(src.url || '')) return toast(T('YouTube-URL eingeben (https://…)'), 'error');
    try {
      const r = await api('POST', '/api/youtube', { url: src.url, quality: src.quality || 'best', cookies: src.cookies || 'none', cookies_file: src.cookies_file || undefined });
      src.job = r.job || r.id; src.file = ''; edited(`${ps}.url`);
      show({ status: 'queued', percent: 0 }); poll(src.job);
    } catch (e) { toast(T('Download fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000); }
  } }, icon('download'), T('Laden'));
  const ck = h('select', { tid: 'vid-yt-cookies' }, Object.entries(STD.VIDEO.COOKIES).map(([k, l]) => h('option', { value: k }, l)));
  ck.value = src.cookies || 'none';
  ck.addEventListener('change', () => { src.cookies = ck.value; edited(`${ps}.cookies`, true); });
  return field(T('YouTube-URL (yt-dlp → runtime/media/video/)'), h('div', null, h('div.inrow', null, urlIn, start), prog, stat,
    h('div.inrow.wrap.mt', null, h('label.inline', null, T('Cookies') + ' ', ck), src.cookies === 'file' ? input(`${ps}.cookies_file`, { tid: 'vid-yt-cookies-file', mono: true, ph: 'media/cookies.txt' }) : null),
    h('p.hint', { tid: 'vid-yt-cookie-hint' }, icon('info'), T('Bei „Sign in to confirm you’re not a bot“, Altersfreigabe oder Mitgliedervideos braucht yt-dlp Cookies (--cookies-from-browser bzw. cookies.txt). Nur eigene Konten verwenden, Nutzungsbedingungen und Urheberrecht beachten.'))),
    { path: `${ps}.url`, wide: true, hint: T('nach dem Laden wie eine Datei; Auflösung/Codec unten wählbar') });
}

function prgAudio(p, base) {
  const brs = STD.VIDEO.AUDIO_BR[p.audio.codec] || [192];
  return h('div.formgrid', null,
    card(T('Audiocodierung'), 'wave', h('div.grid2', null,
      field('Codec', segmented(`${base}.audio.codec`, Object.entries(STD.VIDEO.AUDIO).map(([k, l]) => ({ v: k, label: l.split(' (')[0] })), { tid: 'aud-codec', onchange: () => { const l = STD.VIDEO.AUDIO_BR[p.audio.codec]; if (!l.includes(p.audio.bitrate_kbps)) p.audio.bitrate_kbps = l[2]; } }), { path: `${base}.audio.codec`, hint: T('MP2 = Pflicht-Codec DVB; AC-3 über Deskriptor 0x6A') }),
      field(T('Bitrate (kbit/s)'), select(`${base}.audio.bitrate_kbps`, brs.map(b => ({ v: b, label: `${b} kbit/s` })), { num: true, tid: 'aud-bitrate' }), { path: `${base}.audio.bitrate_kbps` }))),
    p.type === 'tv' && ['file', 'youtube', 'playlist'].includes(p.video.source.kind)
      ? card(T('Tonquelle'), 'music', [
          h('div.inrow.wrap', null, (() => { const cb = h('input', { type: 'checkbox', role: 'switch', tid: 'aud-from-video' }); cb.checked = p.audio.source.kind === 'video';
            cb.addEventListener('change', () => { p.audio.source.kind = cb.checked ? 'video' : 'tone'; edited(`${base}.audio.source.kind`, true); });
            return h('label.switch', null, cb, h('span.track', null, h('i')), h('span.sw-label', null, T('Ton aus der Videodatei'))); })()),
          h('p.hint', null, icon('info'), T('SPEC: Tonquelle „tone“ ist auch bei Videodateien ein Sinus; für den Originalton „Ton aus der Videodatei“ wählen.')),
        ], { tid: 'card-prg-audio-video' }) : null,
    p.audio.source.kind === 'video' ? null : audioSourceCard(p.audio.source, `${base}.audio.source`, { key: p.key, title: p.type === 'radio' ? T('Audioquelle (Radio-Dienst)') : T('Ton (bei Testbild/ohne Tonspur)'), tid: 'card-prg-audio', prefix: 'aud' }));
}

function prgEit(p, base) {
  return h('div.formgrid', null, card(T('EIT – Jetzt/Danach (Gruppe present/following)'), 'text', [
    field(T('Titel (short_event_descriptor)'), input(`${base}.eit.title`, { tid: 'eit-title', maxlength: 96 }), { path: `${base}.eit.title`, extra: counter(() => [...(p.eit.title || '')].length, 96) }),
    field(T('Beschreibung'), textarea(`${base}.eit.description`, { tid: 'eit-desc', rows: 3 }), { wide: true }),
    field(T('Dauer (min)'), input(`${base}.eit.duration_min`, { type: 'number', min: 1, max: 1440, tid: 'eit-duration', mono: true }), { hint: T('Ereignis beginnt beim Start der Kette') }),
    h('p.hint', null, icon('info'), T('EIT-Texte sind live änderbar (Übernehmen ohne Neustart). Ohne EIT zeigen viele TVs „keine Information“.')),
  ], { badge: h('span.pill.live', null, 'live') }));
}

// ---------------------------------------------------------------- SI-Tabellen
export function pageSI() {
  const si = S.draft.dvb.si;
  const std = STD.standardOf(S.draft);
  const m = std === 'dvbt' ? S.draft.dvbt : std === 'dvbs' ? S.draft.dvbs : S.draft.dvbc;
  const delivery = std === 'dvbs'
    ? [['frequency', T('{hz} Hz (Transponder)', { hz: Math.round(m.transponder_mhz * 1e6) })], ['orbital_position', T('19.2E (Labor)')], ['polarization', m.polarization === 'H' ? 'horizontal' : 'vertical'], ['modulation_system', m.system === 'dvbs2' ? 'DVB-S2' : 'DVB-S'], ['modulation_type', m.modulation], ['roll_off', String(m.rolloff)], ['symbol_rate', T('{n} Symbole/s', { n: m.symbol_rate })], ['FEC_inner', m.fec]]
    : std === 'dvbt'
    ? [['centre_frequency', `${S.draft.rf.freq_hz} Hz`], ['bandwidth', `${m.bandwidth_mhz}MHz`], ['constellation', m.constellation], ['code_rate_HP_stream', m.code_rate], ['guard_interval', m.guard], ['transmission_mode', m.fft], ['hierarchy_information', '0']]
    : [['frequency', `${S.draft.rf.freq_hz} Hz`], ['FEC_outer', 'RS'], ['modulation', m.constellation], ['symbol_rate', T('{n} Symbole/s', { n: m.symbol_rate })], ['FEC_inner', 'none (15)']];
  return h('div.page-grid', null,
    h('div.col', null,
      card(T('Netzwerk & Transportstrom (NIT/SDT/PAT)'), 'id', [
        h('div.grid2', null,
          field(T('Netzwerk-ID (hex)'), input('dvb.si.network_id', { tid: 'si-network-id', mono: true, hex: 4 }), { path: 'dvb.si.network_id', hint: 'NIT actual' }),
          field(T('Netzwerkname'), input('dvb.si.network_name', { tid: 'si-network-name', maxlength: 40 }), { path: 'dvb.si.network_name', hint: 'network_name_descriptor 0x40' })),
        h('div.grid2', null,
          field(T('Original-Netzwerk-ID (hex)'), input('dvb.si.onid', { tid: 'si-onid', mono: true, hex: 4 }), { path: 'dvb.si.onid', hint: T('0xFF00–0xFFFF = privat/temporär (Labor)') }),
          field(T('Transportstrom-ID (hex)'), input('dvb.si.tsid', { tid: 'si-tsid', mono: true, hex: 4 }), { path: 'dvb.si.tsid', hint: 'PAT transport_stream_id' })),
        h('div.grid3', null,
          field('LCN', toggle('dvb.si.lcn', 'logical_channel_number (EACEM 0x83)', { tid: 'si-lcn' }), { hint: 'private_data_specifier 0x00000028' }),
          field('EIT', toggle('dvb.si.eit', T('Jetzt/Danach senden'), { tid: 'si-eit' })),
          field('TDT/TOT', toggle('dvb.si.tdt', T('Uhrzeit senden'), { tid: 'si-tdt' }), { hint: 'UTC + local_time_offset' })),
      ], { tid: 'card-si' }),
      card(T('Dienstliste (service_list_descriptor / LCN)'), 'table', h('div.tblwrap', null, h('table.tbl.compact', { tid: 'si-services' },
        h('thead', null, h('tr', null, h('th', null, 'SID'), h('th', null, T('Name')), h('th', null, T('Typ')), h('th.r', null, 'LCN'))),
        h('tbody', null, S.draft.dvb.programs.map(p => h('tr', null, h('td.mono', null, p.sid), h('td', null, p.name), h('td.mono', null, p.type === 'radio' ? '0x02' : p.video.resolution === 'sd' ? '0x01' : '0x19'), h('td.r.mono', null, si.lcn ? p.lcn : '–')))))))),
    h('div.col', null,
      card(std === 'dvbt' ? 'terrestrial_delivery_system_descriptor (0x5A)' : std === 'dvbs' ? 'satellite_delivery_system_descriptor (0x43)' : 'cable_delivery_system_descriptor (0x44)', 'antenna', [
        h('p.hint', null, icon('info'), T('Wird aus HF- und Modulationsparametern erzeugt (TSDuck NIT-XML); hier nur zur Kontrolle.')),
        h('table.cmp', null, h('tbody', null, delivery.map(([k, v]) => h('tr', null, h('td', null, k), h('td.mono', null, v))))),
      ], { tid: 'card-si-delivery' }),
      card(T('Tabellen im Transportstrom'), 'layers', h('ul.silist', null,
        ...[['PAT', 'PID 0x0000', true], ['PMT', T('{n} × (je Programm)', { n: S.draft.dvb.programs.length }), true], ['SDT actual', 'PID 0x0011, service_descriptor 0x48', true], ['NIT actual', 'PID 0x0010, Delivery + LCN', true],
          ['EIT p/f actual', 'PID 0x0012', si.eit], ['TDT/TOT', 'PID 0x0014', si.tdt]].map(([n, d, on]) => h('li', { class: on ? 'on' : 'off' }, icon(on ? 'check' : 'x'), h('b', null, n), h('small.muted', null, d)))))));
}
