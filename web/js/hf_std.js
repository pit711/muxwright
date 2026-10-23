// HF-Seite: Frequenzkarte für UKW/DVB und Ausgabeweg-Anzeige (alle Standards).

import { h, icon, toast } from './ui.js';
import * as D from './dab.js';
import * as STD from './standards.js';
import { S, field, input, select, card, rfUpdate, edited, txRunning } from './core.js';
import { lnbWarning } from './dvb.js';
import { T, LOCALE } from './i18n.js';
const PLUTO_MIN_MHZ = 70;   // AD9364-Untergrenze; Band-I-Kanäle E2–E4 (48–62 MHz) gehen nicht

const fmtMHz = hz => D.fmtMHz(hz);

/** Frequenzkarte für UKW (Direkteingabe, 100-kHz-Raster) oder DVB (Kanaltabellen + frei) */
export function freqCardStd(std, rf) {
  const offIn = h('input.mono', { type: 'number', tid: 'rf-offset', step: 10 });
  offIn.value = rf.offset_hz;
  offIn.addEventListener('change', () => rfUpdate({ offset_hz: Math.round(Number(offIn.value) || 0) }));
  const read = h('div.bigread', null,
    h('div', null, h('small', null, std === 'fm' ? T('Träger') : T('Kanal')), h('b.ch', { tid: 'rf-channel-read' }, std === 'fm' ? T('UKW') : rf.channel === 'custom' ? T('frei') : rf.channel)),
    h('div', null, h('small', null, T('Mittenfrequenz')), h('b.mono', null, fmtMHz(rf.freq_hz + (rf.offset_hz || 0)), h('em', null, ' MHz'))));

  if (std === 'fm') {
    const fIn = h('input.mono', { type: 'number', tid: 'fm-freq', step: 0.1, min: STD.FM_MIN_MHZ, max: STD.FM_MAX_MHZ });
    fIn.value = (rf.freq_hz / 1e6).toFixed(1);
    const setF = async mhz => {
      mhz = Math.round(mhz * 10) / 10;
      if (!(mhz >= STD.FM_MIN_MHZ && mhz <= STD.FM_MAX_MHZ)) return toast(T('UKW: 87,5–108,0 MHz'), 'error');
      if (Math.round(mhz * 1e6) === S.draft.rf.freq_hz && S.draft.rf.channel === 'fm') return;
      await rfUpdate({ channel: 'fm', freq_hz: Math.round(mhz * 1e6) }); S.render(true);
    };
    fIn.addEventListener('change', () => setF(Number(fIn.value)));
    fIn.addEventListener('keydown', e => { if (e.key === 'Enter') setF(Number(fIn.value)); });
    const stepBtn = d => h('button.btn.sm', { type: 'button', tid: `fm-freq-step-${d}`, onclick: () => setF(rf.freq_hz / 1e6 + d) }, `${d > 0 ? '+' : '−'}${String(Math.abs(d)).replace('.', ',')}`);
    const presets = [87.5, 88.0, 90.0, 95.0, 98.0, 100.0, 102.0, 104.0, 106.0, 107.9, 108.0];
    return card(T('Frequenz (Band II)'), 'antenna', [
      read,
      field(T('Trägerfrequenz (MHz)'), h('div.inrow', null, fIn, h('button.btn', { type: 'button', tid: 'fm-freq-apply', onclick: () => setF(Number(fIn.value)) }, T('Setzen'))),
        { path: 'rf.freq_hz', hint: T('87,5–108,0 MHz, 100-kHz-Raster (Europa); Feinverstimmung über „Frequenzversatz“') }),
      h('div.attrow.fmsteps', null, stepBtn(-1), stepBtn(-0.1), stepBtn(0.1), stepBtn(1)),
      h('div.chgrid', { tid: 'fm-presets' }, presets.map(m => h('button', { type: 'button', class: Math.abs(rf.freq_hz / 1e6 - m) < 0.01 ? 'on' : null, onclick: () => setF(m) }, m.toFixed(1)))),
      h('div.grid2', null,
        field(T('Frequenzversatz (Hz)'), offIn, { hint: T('AFC-/Verstimmungstest ±25/±50/±75 kHz, live') }),
        field(T('Abtastrate'), h('input.mono', { readOnly: true, value: `${(STD.requiredSampleRate(S.draft) / 1e6).toLocaleString(LOCALE, { minimumFractionDigits: 3 })} MS/s` }), { hint: T('IQ-Strom zum Pluto (fest)') })),
      h('div.grid2', null,
        field(T('LO-Offset (Hz)'), input('rf.lo_offset_hz', { type: 'number', tid: 'rf-lo-offset', step: 1000, mono: true }), { path: 'rf.lo_offset_hz', hint: T('gegen LO-Durchgriff · Neustart') }),
        field(T('Backoff (dB)'), input('rf.backoff_db', { type: 'number', min: 0, max: 30, step: 0.5, tid: 'rf-backoff', mono: true }), { path: 'rf.backoff_db', hint: T('FM: 3–6 dB reichen (konstante Hüllkurve) · Neustart') })),
    ], { tid: 'card-freq' });
  }

  if (std === 'dvbs') {
    const d = S.draft.dvbs;
    return card(T('Frequenz (Sat-ZF)'), 'antenna', [
      lnbWarning(),
      read,
      h('div.rx-grid', { tid: 'dvbs-hf-info' },
        h('div.rx-kv', null, h('small', null, T('Transponder')), h('b', null, `${D.fmtNum(d.transponder_mhz, 0)} MHz ${d.polarization}`)),
        h('div.rx-kv', null, h('small', null, 'LOF'), h('b', null, `${D.fmtNum(d.lof_mhz, 0)} MHz`)),
        h('div.rx-kv', null, h('small', null, '22 kHz'), h('b', null, d.tone_22khz ? T('an') : T('aus'))),
        h('div.rx-kv', null, h('small', null, T('Sat-ZF')), h('b', null, `${D.fmtNum(STD.satIfHz(d) / 1e6, 0)} MHz`))),
      h('p.hint', null, icon('info'), T('Transponder und LOF im Bereich „Modulation“; die Sendefrequenz ist die Sat-ZF (950–2150 MHz).')),
      field(T('Frequenzversatz (Hz)'), offIn, { hint: T('Toleranz-/AFC-Test, live') }),
      field(T('Backoff (dB)'), input('rf.backoff_db', { type: 'number', min: 0, max: 30, step: 0.5, tid: 'rf-backoff', mono: true }), { path: 'rf.backoff_db', hint: T('QPSK/8PSK: 4–6 dB · Neustart') }),
    ], { tid: 'card-freq' });
  }
  // DVB-T / DVB-C / PAL
  const list = STD.dvbChannels(std);
  const isPal = std === 'pal';
  const bands = [...new Set(list.map(c => c.band))];
  const chSel = h('select', { tid: 'dvb-channel' },
    bands.map(b => h('optgroup', { label: b }, list.filter(c => c.band === b).map(c => h('option', { value: c.id, disabled: c.mhz < PLUTO_MIN_MHZ || null },
      (isPal ? `${c.id} · ${T('Bild {f} MHz', { f: fmtMHz(c.mhz * 1e6) })} (${c.sys})` : `${c.id} · ${fmtMHz(c.mhz * 1e6)} MHz (${c.bw} MHz)`) + (c.mhz < PLUTO_MIN_MHZ ? T(' – unter 70 MHz, Pluto nicht möglich') : ''))))),
    h('option', { value: 'custom' }, T('Frei (eigene Frequenz)')));
  chSel.value = rf.channel;
  if (chSel.selectedIndex < 0) chSel.value = 'custom';
  chSel.addEventListener('change', async () => {
    if (chSel.value === 'custom') { S.draft.rf.channel = 'custom'; S.render(true); return; }
    const c = list.find(x => x.id === chSel.value);
    await rfUpdate({ channel: c.id, freq_hz: Math.round(c.mhz * 1e6) });
    if (std === 'dvbt' && c.bw !== Number(S.draft.dvbt.bandwidth_mhz)) toast(T('Hinweis: {ch} ist ein {bw}-MHz-Kanal, eingestellt sind {set} MHz (Bereich DVB-T).', { ch: c.id, bw: c.bw, set: S.draft.dvbt.bandwidth_mhz }), 'info', 6000);
    S.render(true);
  });
  const freqIn = h('input.mono', { type: 'number', tid: 'rf-freq', step: 0.001, min: PLUTO_MIN_MHZ, max: 1000 });
  freqIn.value = (rf.freq_hz / 1e6).toFixed(3);
  const apply = async () => {
    const hz = Math.round(Number(freqIn.value) * 1e6);
    if (!(hz >= PLUTO_MIN_MHZ * 1e6 && hz <= 1000e6)) return toast(isPal ? T('PAL: 70–1000 MHz (Pluto ab 70 MHz)') : T('DVB: 70–1000 MHz (Pluto ab 70 MHz)'), 'error');
    await rfUpdate({ channel: 'custom', freq_hz: hz }); S.render(true);
  };
  const uhf = list.filter(c => c.band === 'UHF');
  const palInfo = isPal ? (() => { const c = STD.palCarriers(S.draft); const ch = list.find(x => x.id === rf.channel);
    return h('div.rx-grid', { tid: 'pal-carriers' },
      h('div.rx-kv', null, h('small', null, T('Bildträger')), h('b', null, `${fmtMHz(c.vision)} MHz`)),
      h('div.rx-kv', null, h('small', null, T('Tonträger')), h('b', null, `${fmtMHz(c.sound)} MHz`)),
      ch ? h('div.rx-kv', null, h('small', null, T('Kanal')), h('b', null, `${ch.edge}–${ch.edge + ch.bw} MHz`)) : null); })() : null;
  return card(std === 'dvbt' ? T('Frequenz (Band III / UHF)') : isPal ? T('Frequenz (Bildträger)') : T('Frequenz (Kabel)'), 'antenna', [
    read, palInfo,
    h('div.grid2', null,
      field(T('Kanal'), chSel, { hint: std === 'dvbt' ? 'E5–E12 (7 MHz), K21–K69 (8 MHz)' : isPal ? T('Bildträger = Kanalgrenze + 1,25 MHz (E2–E12 B, K21–K69 G)') : T('K02–K69, S02–S41 (8-MHz-Raster), MHz = Kanalmitte; VHF-Teil ggf. +1 MHz → frei') }),
      rf.channel === 'custom' ? field(T('Freie Frequenz (MHz)'), h('div.inrow', null, freqIn, h('button.btn', { type: 'button', tid: 'rf-freq-apply', onclick: apply }, T('Setzen'))), { hint: T('70–1000 MHz (Pluto ab 70 MHz)') })
        : field(T('Frequenzversatz (Hz)'), offIn, { hint: T('Empfänger-Toleranztest, live') })),
    rf.channel === 'custom' ? field(T('Frequenzversatz (Hz)'), offIn, { hint: T('live') }) : null,
    h('details.quick', { open: true }, h('summary', null, T('UHF-Kanäle')), h('div.chgrid', { tid: 'dvb-chgrid' }, uhf.map(c =>
      h('button', { type: 'button', class: c.id === rf.channel ? 'on' : null, title: `${fmtMHz(c.mhz * 1e6)} MHz`,
        onclick: async () => { await rfUpdate({ channel: c.id, freq_hz: Math.round(c.mhz * 1e6) }); S.render(true); } }, c.id)))),
    h('div.grid2', null,
      field(T('LO-Offset (Hz)'), input('rf.lo_offset_hz', { type: 'number', tid: 'rf-lo-offset', step: 1000, mono: true }), { path: 'rf.lo_offset_hz', hint: T('gegen LO-Durchgriff · Neustart') }),
      field(T('Abtastrate'), h('input.mono', { readOnly: true, value: `${(STD.requiredSampleRate(S.draft) / 1e6).toLocaleString(LOCALE, { minimumFractionDigits: 3 })} MS/s` }), { hint: isPal ? T('Analog-Loop auf dem Pluto') : T('ergibt sich aus Bandbreite bzw. Symbolrate') })),
    field(T('Backoff (dB)'), input('rf.backoff_db', { type: 'number', min: 0, max: 30, step: 0.5, tid: 'rf-backoff', mono: true }), { path: 'rf.backoff_db', hint: isPal ? T('AM-Bild (Synchronspitze = Vollaussteuerung): 6 dB · Neustart') : T('OFDM/QAM: ≥ 10 dB (256-QAM empfindlich gegen Clipping) · Neustart') }),
  ], { tid: 'card-freq' });
}

/** Ausgabeweg: USB-Streaming (PC) oder Modulator/zyklischer Puffer auf dem Pluto – aus `state.rf.path`, mit Soll aus den Parametern */
export function outputPathCard() {
  const box = h('div', null, pathBody());
  let last = pathKey();
  (S.npWatchers ||= []).push(() => { const k = pathKey(); if (k !== last) { last = k; box.replaceChildren(pathBody()); } });
  return card(T('Ausgabeweg'), 'chip', box, { tid: 'card-path' });
}
function pathKey() { return `${STD.normPath(S.state.rf)}|${JSON.stringify(STD.requiredPath(S.draft))}`; }
function pathBody() {
  const need = STD.requiredPath(S.draft);
  const actual = STD.normPath(S.state.rf) ?? STD.normPath({ path: S.state.tx?.rf_path ?? S.state.pluto?.path });
  const name = p => p === 'pluto' ? T('Modulator auf dem Pluto (Firmware)') : p === 'usb' ? 'USB-Streaming (PC)' : T('unbekannt');
  const mismatch = actual && actual !== need.path;
  const el = h('div.pathinfo', { tid: 'rf-path' },
    h('div.rx-grid', null,
      h('div.rx-kv' + (actual ? (mismatch ? '.bad' : '.ok') : ''), null, h('small', null, T('Aktueller Weg')), h('b', { tid: 'rf-path-actual' }, name(actual))),
      h('div.rx-kv', null, h('small', null, T('Nötig für diese Parameter')), h('b', { tid: 'rf-path-required' }, name(need.path))),
      h('div.rx-kv', null, h('small', null, T('Abtastrate')), h('b', null, `${(need.sps / 1e6).toLocaleString(LOCALE, { minimumFractionDigits: 3 })} MS/s`))),
    h('p.hint', null, icon('info'), need.reason),
    mismatch ? h('p.alert', { tid: 'rf-path-warn' }, icon('alert'), `${T('Der Server meldet „{actual}“, die Parameter brauchen aber „{need}“.', { actual: name(actual), need: name(need.path) })} ${need.path === 'pluto' ? T('Pluto-Firmware mit Modulator nötig, TS geht per UDP an 192.168.2.1.') : T('Streaming per USB genügt.')}`) : null,
    !actual ? h('p.muted.small', null, T('Der Server meldet den Ausgabeweg noch nicht (`rf.path`); Anzeige folgt, sobald er ihn liefert.')) : null);
  return el;
}
