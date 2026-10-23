// Seiten des Dashboards.

import { h, icon, api, toast, dialog, download, stamp, debounce } from './ui.js';
import * as D from './dab.js';
import {
  S, getPath, setPath, field, input, textarea, select, toggle, segmented, card, counter, edited, refreshDecor,
  setAtten, setChannel, rfUpdate, sendLive, markRfIntent, upload, loadMedia, previewCache, applyConfig, errorsFor, save, load, txRunning, changes,
} from './core.js';
import { renderReport, slideSrc, protocolData, protocolHTML } from './report.js';
import { TESTPLANS, planCheck } from './testplans.js';
import * as STD from './standards.js';
import { freqCardStd, outputPathCard } from './hf_std.js';
import { nowPlayingBox } from './media.js';
import { T, LOCALE } from './i18n.js';
export { pageMedia } from './media.js';
export { uploadBtn };

const svcColor = i => `var(--c${i % 10})`;
const cal = () => S.draft.rf.cal_dbm_at_0db || {};
const calKey = rf => rf.channel === 'custom' || rf.channel === 'fm' ? `${Math.round(rf.freq_hz / 1e3) / 1e3}` : rf.channel;
export function estDbm(rf) {
  const c = (rf.cal_dbm_at_0db || {})[calKey(rf)];
  return c == null || c === '' ? null : Number(c) - Number(rf.atten_db);
}

// ============================================================ HF
export function pageHF() {
  const rf = S.draft.rf;
  const band = rf.channel === 'custom' ? 'custom' : D.isLBand(rf.channel) ? 'lband' : 'band3';

  // --- Frequenz
  const chSel = h('select', { tid: 'rf-channel' },
    h('optgroup', { label: 'Band III' }, D.CHANNELS.filter(c => c.band === 'Band III').map(c => h('option', { value: c.id }, `${c.id} · ${D.fmtMHz(c.mhz * 1e6)} MHz`))),
    h('optgroup', { label: 'L-Band' }, D.CHANNELS.filter(c => c.band === 'L-Band').map(c => h('option', { value: c.id }, `${c.id} · ${D.fmtMHz(c.mhz * 1e6)} MHz`))),
    h('option', { value: 'custom' }, T('Frei (eigene Frequenz)')));
  chSel.value = rf.channel;
  chSel.addEventListener('change', async () => {
    if (chSel.value === 'custom') { S.draft.rf.channel = 'custom'; S.render(true); return; }
    if (D.isLBand(chSel.value) && S.draft.mode === 1) toast(T('Hinweis: Für L-Band ist Mode II oder IV üblich (Bereich Modulation).'), 'info', 5000);
    await setChannel(chSel.value);
    S.render(true);
  });
  const freqIn = h('input.mono', { type: 'number', tid: 'rf-freq', step: 0.001, min: 70, max: 6000 });
  freqIn.value = (rf.freq_hz / 1e6).toFixed(3);
  const freqApply = h('button.btn', { type: 'button', tid: 'rf-freq-apply', onclick: async () => {
    const hz = Math.round(Number(freqIn.value) * 1e6);
    if (!(hz >= D.FREQ_MIN_HZ && hz <= D.FREQ_MAX_HZ)) return toast(T('Frequenz 70–6000 MHz'), 'error');
    await setChannel('custom', hz); S.render(true);
  } }, T('Setzen'));

  const quick = h('div.chgrid', { tid: 'rf-chgrid' },
    D.CHANNELS.filter(c => band === 'lband' ? c.band === 'L-Band' : c.band === 'Band III').map(c =>
      h('button', { type: 'button', class: c.id === rf.channel ? 'on' : null, title: `${D.fmtMHz(c.mhz * 1e6)} MHz`,
        onclick: async () => { await setChannel(c.id); S.render(true); } }, c.id)));

  const offIn = h('input.mono', { type: 'number', tid: 'rf-offset', step: 10 });
  offIn.value = rf.offset_hz;
  offIn.addEventListener('change', () => rfUpdate({ offset_hz: Math.round(Number(offIn.value) || 0) }));

  const freqCardDab = card(T('Frequenz'), 'antenna', [
    h('div.bigread', null,
      h('div', null, h('small', null, T('Kanal')), h('b.ch', { tid: 'rf-channel-read' }, rf.channel === 'custom' ? T('frei') : rf.channel)),
      h('div', null, h('small', null, T('Mittenfrequenz')), h('b.mono', null, D.fmtMHz(rf.freq_hz + (rf.offset_hz || 0)), h('em', null, ' MHz')))),
    h('div.grid2', null,
      field(T('Kanal'), chSel, { hint: band === 'lband' ? 'L-Band: Mode II/IV' : 'Band III: Mode I' }),
      rf.channel === 'custom'
        ? field(T('Freie Frequenz (MHz)'), h('div.inrow', null, freqIn, freqApply), { hint: '70 MHz – 6 GHz (AD9364-Hack)' })
        : field(T('Frequenzversatz (Hz)'), offIn, { hint: T('Empfänger-Toleranztest, live') })),
    rf.channel === 'custom' ? field(T('Frequenzversatz (Hz)'), offIn, { hint: T('live') }) : null,
    h('details.quick', { open: band !== 'custom' }, h('summary', null, band === 'lband' ? T('L-Band-Kanäle') : T('Band-III-Kanäle')), quick),
    h('div.grid2', null,
      field(T('LO-Offset (Hz)'), input('rf.lo_offset_hz', { type: 'number', tid: 'rf-lo-offset', step: 1000, mono: true }), { path: 'rf.lo_offset_hz', hint: T('Signal bleibt auf Sollfrequenz · Neustart') }),
      field(T('Abtastrate'), select('rf.sample_rate', D.SAMPLE_RATES.map(r => ({ v: r.v, label: r.label })), { num: true, tid: 'rf-samplerate' }),
        { path: 'rf.sample_rate', hint: D.SAMPLE_RATES.find(r => r.v === rf.sample_rate)?.warn || T('Neustart') })),
  ], { tid: 'card-freq' });

  // --- Pegel
  const range = h('input.atten', { type: 'range', min: 0, max: D.ATTEN_MAX, step: 0.25, tid: 'rf-atten' });
  range.value = rf.atten_db;
  const num = h('input.mono', { type: 'number', min: 0, max: D.ATTEN_MAX, step: 0.25, tid: 'rf-atten-input' });
  num.value = rf.atten_db;
  const readAtt = h('b.mono', { tid: 'rf-atten-read' });
  const readDbm = h('b.mono', { tid: 'rf-dbm-read' });
  const warn = h('div.dangerzone', null, icon('alert'), h('span', null, T('Unter 40 dB Dämpfung – nur abgeschirmt senden!')));
  const upd = () => {
    const v = Number(S.draft.rf.atten_db);
    if (!S.rfDragging) { range.value = v; if (document.activeElement !== num) num.value = v; }
    readAtt.textContent = D.fmtDb(S.rfDragging ? Number(range.value) : v);
    const e = estDbm({ ...S.draft.rf, atten_db: S.rfDragging ? Number(range.value) : v });
    readDbm.textContent = e == null ? '–' : `${D.fmtNum(e, 1)}`;
    warn.hidden = (S.rfDragging ? Number(range.value) : v) >= D.ATTEN_SAFE;
    range.style.setProperty('--p', `${(Number(range.value) / D.ATTEN_MAX) * 100}%`);
  };
  range.addEventListener('input', () => { S.rfDragging = true; upd(); });
  range.addEventListener('change', async () => { const v = Number(range.value); S.rfDragging = false; await setAtten(v); upd(); });
  num.addEventListener('change', async () => { await setAtten(Number(num.value)); upd(); });
  S.watchers.push(upd);
  const step = d => h('button.btn.sm', { type: 'button', tid: `rf-atten-step-${d}`, onclick: async () => { await setAtten(Number(S.draft.rf.atten_db) + d); upd(); } },
    `${d > 0 ? '+' : '−'}${String(Math.abs(d)).replace('.', ',')}`);
  const levelCard = card(T('Pegel'), 'gauge', [
    h('div.bigread', null,
      h('div', null, h('small', null, T('Dämpfung')), h('span.big', null, readAtt, h('em', null, ' dB'))),
      h('div', null, h('small', null, T('Geschätzte Leistung')), h('span.big', null, readDbm, h('em', null, ' dBm')))),
    h('div.rangewrap', null, range, h('div.scale', null, ['0', '20', '40', '60', '89,75'].map(s => h('span', null, s)))),
    warn,
    h('div.attrow', { title: T('Dämpfung in dB ändern (mehr Dämpfung = weniger Leistung)') }, step(-10), step(-1), step(-0.25), step(0.25), step(1), step(10), h('label.attnum', null, num, h('span', null, 'dB'))),
    estDbm(rf) == null ? h('p.hint', null, icon('info'), T('Kein Kalibrierwert für diesen Kanal – unten in der Kalibriertabelle eintragen, dann wird der Pegel in dBm angezeigt.')) : null,
    h('div.grid2', null,
      field('Backoff (dB)', input('rf.backoff_db', { type: 'number', min: 0, max: 30, step: 0.5, tid: 'rf-backoff', mono: true }), { path: 'rf.backoff_db', hint: T('RMS-Abstand zur Vollaussteuerung · Neustart') }),
      field('Crest-Factor-Reduction', toggle('rf.cfr', STD.standardOf(S.draft) === 'dab' ? 'CFR in odr-dabmod' : T('CFR im Modulator'), { tid: 'rf-cfr' }), { hint: STD.standardOf(S.draft) === 'fm' ? T('bei FM ohne Wirkung (konstante Hüllkurve)') : T('falls verfügbar · Neustart') })),
  ], { tid: 'card-level' });

  // --- Modulation
  const modCard = card(T('Modulation & TII'), 'wave', [
    field(T('Übertragungsmodus'), segmented('mode', D.MODES.map(m => ({ v: m.v, label: m.label.replace('Mode ', ''), title: m.hint })), { tid: 'rf-mode' }),
      { path: 'mode', hint: D.MODES.find(m => m.v === S.draft.mode)?.hint + ' · ' + T('Neustart') }),
    h('div.grid3', null,
      field('TII', toggle('tii.enable', T('senden'), { tid: 'tii-enable', rerender: true })),
      field('Comb', input('tii.comb', { type: 'number', min: 0, max: 23, tid: 'tii-comb', mono: true }), { path: 'tii', hint: '0–23' }),
      field('Pattern', input('tii.pattern', { type: 'number', min: 0, max: 69, tid: 'tii-pattern', mono: true }), { hint: '0–69' })),
    field('', toggle('tii.old_variant', T('alte TII-Variante (ältere Empfänger)'), { tid: 'tii-old' })),
  ], { tid: 'card-mod' });

  const std = STD.standardOf(S.draft);
  const freqCard = std === 'dab' ? freqCardDab : freqCardStd(std, rf);
  return h('div.page-grid.hf', null,
    h('div.col', null, levelCard, sweepCard()),
    h('div.col', null, freqCard, std === 'dab' ? modCard : null, outputPathCard(), calCard()));
}

// --- Pegelassistent (Rampe)
function sweepCard() {
  const p = load('sweepParams', { from_db: 40, to_db: 89, step_db: 1, dwell_s: 5 });
  const numIn = (k, tid, o = {}) => {
    const el = h('input.mono', { type: 'number', tid, step: o.step || 1, min: 0, max: o.max ?? D.ATTEN_MAX });
    el.value = p[k];
    el.addEventListener('change', () => { p[k] = Number(el.value); save('sweepParams', p); updInfo(); });
    return el;
  };
  const info = h('p.hint');
  const updInfo = () => {
    const n = Math.floor(Math.abs(p.to_db - p.from_db) / (p.step_db || 1)) + 1;
    info.replaceChildren(icon('info'), T('{n} Stufen · Dauer ca. {min} min. Dämpfung steigt → Pegel sinkt.', { n, min: String(Math.round(n * p.dwell_s / 60 * 10) / 10) }));
  };
  updInfo();
  const prog = h('div.progress', { tid: 'sweep-progress' }, h('i'));
  const status = h('div.sweepstat', { tid: 'sweep-status' });
  const startBtn = h('button.btn.primary', { type: 'button', tid: 'sweep-start', onclick: async () => {
    if (!txRunning()) return toast(T('Zuerst oben „Senden“ einschalten.'), 'info');
    if (Math.min(p.from_db, p.to_db) < D.ATTEN_SAFE && !(await dialog({ title: T('Rampe unter 40 dB'), danger: true, ok: T('Trotzdem starten'), tid: 'confirm-sweep',
      body: h('p', null, T('Die Rampe führt unter 40 dB Dämpfung. Nur bei abgeschirmtem Aufbau fortfahren.')) }))) return;
    try {
      const r = await api('POST', '/api/sweep', p);
      S.localSweep = { ...p, started: Date.now() / 1000, running: true };
      if (r && r.sweep) S.state.sweep = r.sweep;
      toast(T('Pegelrampe gestartet'), 'ok');
    } catch (e) { toast(T('Rampe fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
    S.liveUpdate();
  } }, icon('play'), T('Rampe starten'));
  const stopBtn = h('button.btn', { type: 'button', tid: 'sweep-stop', onclick: async () => {
    try { await api('POST', '/api/sweep/stop'); toast(T('Rampe abgebrochen'), 'info'); } catch (e) { toast(e.message, 'error'); }
    if (S.localSweep) S.localSweep.running = false;
    S.liveUpdate();
  } }, icon('stop'), T('Abbrechen'));
  const markBtn = h('button.btn.accent', { type: 'button', tid: 'sweep-mark', onclick: () => {
    const sw = sweepNow();
    const att = sw?.current_db ?? S.server.rf.atten_db;
    const rf = { ...S.server.rf, atten_db: att };
    const marks = load('marks', []);
    marks.unshift({ time: Date.now(), channel: rf.channel, freq_hz: rf.freq_hz, atten_db: att, dbm: estDbm(rf), service: S.server.services[0]?.label });
    save('marks', marks.slice(0, 100));
    toast(T('Schwelle markiert: {db} dB', { db: D.fmtDb(att) }), 'ok');
    renderMarks();
  } }, icon('flag'), T('Schwelle markieren'));
  const marksBox = h('div.marks');
  const renderMarks = () => {
    const marks = load('marks', []);
    marksBox.replaceChildren(marks.length ? h('table.tbl.compact', { tid: 'sweep-marks' },
      h('thead', null, h('tr', null, h('th', null, T('Zeit')), h('th', null, T('Kanal')), h('th.r', null, T('Dämpfung')), h('th.r', null, T('Pegel')), h('th', null, ''))),
      h('tbody', null, marks.slice(0, 8).map((m, i) => h('tr', null,
        h('td', null, new Date(m.time).toLocaleTimeString(LOCALE)), h('td', null, m.channel),
        h('td.r.mono', null, `${D.fmtDb(m.atten_db)} dB`), h('td.r.mono', null, m.dbm == null ? '–' : `${D.fmtNum(m.dbm, 1)} dBm`),
        h('td.r', null, h('button.icon-btn', { type: 'button', title: T('Löschen'), onclick: () => { const a = load('marks', []); a.splice(i, 1); save('marks', a); renderMarks(); } }, icon('x'))))))) :
      h('p.muted.small', null, T('Noch keine Schwellen markiert.')));
  };
  renderMarks();
  const upd = () => {
    const sw = sweepNow();
    const running = !!sw?.running;
    startBtn.disabled = running; stopBtn.disabled = !running; markBtn.disabled = !txRunning();
    const pct = sw ? Math.max(0, Math.min(100, sw.progress * 100)) : 0;
    prog.firstChild.style.width = pct + '%';
    prog.classList.toggle('run', running);
    if (!sw) { status.replaceChildren(h('span.muted', null, T('Bereit.'))); return; }
    const rf = { ...S.draft.rf, atten_db: sw.current_db };
    const e = estDbm(rf);
    status.replaceChildren(...[
      h('span', null, T(running ? 'Läuft · Stufe {i}/{n}' : 'Beendet · Stufe {i}/{n}', { i: sw.index ?? '–', n: sw.total ?? '–' })),
      h('b.mono', null, `${D.fmtDb(sw.current_db ?? 0)} dB`), e != null ? h('b.mono.accent', null, `${D.fmtNum(e, 1)} dBm`) : null,
      running && sw.eta != null ? h('span.muted', null, T('noch {s} s', { s: Math.ceil(sw.eta) })) : null].filter(Boolean));
  };
  S.watchers.push(upd); S.sweepUpd = upd;
  setTimeout(upd);
  return card(T('Pegelassistent · Empfindlichkeitsrampe'), 'ramp', [
    h('div.grid4', null,
      field(T('Von (dB)'), numIn('from_db', 'sweep-from', { step: 0.25 })), field(T('Bis (dB)'), numIn('to_db', 'sweep-to', { step: 0.25 })),
      field(T('Schritt (dB)'), numIn('step_db', 'sweep-step', { step: 0.25, max: 20 })), field(T('Verweilzeit (s)'), numIn('dwell_s', 'sweep-dwell', { max: 600 }))),
    info, prog, status,
    h('div.inrow.wrap', null, startBtn, stopBtn, h('span.grow'), markBtn),
    marksBox,
  ], { tid: 'card-sweep' });
}

/** Rampenzustand: vom Server (state.sweep) oder lokal geschätzt */
export function sweepNow() {
  const s = S.state.sweep;
  if (s && (s.running || s.current_db != null || s.atten_db != null)) {
    const total = s.total ?? s.count ?? (Math.floor(Math.abs(s.to_db - s.from_db) / (s.step_db || 1)) + 1);
    const index = s.index ?? s.step ?? null;
    const cur = s.current_db ?? s.atten_db ?? s.from_db;
    const progress = s.progress ?? (index != null && total ? index / total : 0);
    const eta = s.eta_s ?? (s.running && total && index != null ? (total - index) * (s.dwell_s || 0) : null);
    return { running: !!s.running, current_db: cur, index, total, progress, eta };
  }
  const l = S.localSweep;
  if (!l) return null;
  const n = Math.floor(Math.abs(l.to_db - l.from_db) / (l.step_db || 1)) + 1;
  const el = Date.now() / 1000 - l.started;
  const i = Math.min(n, Math.floor(el / l.dwell_s) + 1);
  const running = l.running && el < n * l.dwell_s;
  const dir = l.to_db >= l.from_db ? 1 : -1;
  return { running, current_db: l.from_db + dir * (i - 1) * l.step_db, index: i, total: n, progress: Math.min(1, el / (n * l.dwell_s)), eta: n * l.dwell_s - el };
}

function calCard() {
  const rf = S.draft.rf;
  const c = cal();
  const hzOf = k => D.channelHz(k) || STD.dvbChannelHz('dvbt', k) || STD.dvbChannelHz('dvbc', k) || Number(k) * 1e6 || 0;
  const rows = Object.entries(c).sort((a, b) => hzOf(a[0]) - hzOf(b[0]));
  const std = STD.standardOf(S.draft);
  const chList = std === 'dab' ? D.CHANNELS.map(x => x.id) : std === 'fm' ? [calKey(rf)] : STD.dvbChannels(std).map(x => x.id);
  const chSel = h('select', { tid: 'cal-channel' }, chList.map(id => h('option', { value: id }, id)));
  chSel.value = rf.channel === 'custom' || std === 'fm' ? calKey(rf) : rf.channel;
  if (chSel.selectedIndex < 0) { chSel.append(h('option', { value: calKey(rf) }, calKey(rf))); chSel.value = calKey(rf); }
  const val = h('input.mono', { type: 'number', step: 0.1, tid: 'cal-value', placeholder: T('z. B. 7,5') });
  const add = () => {
    if (val.value === '') return toast(T('Messwert in dBm eintragen'), 'error');
    S.draft.rf.cal_dbm_at_0db = { ...c, [chSel.value]: Number(val.value) };
    edited('rf.cal_dbm_at_0db', true);
  };
  return card(T('Kalibriertabelle'), 'table', [
    h('p.hint', null, T('Gemessene Ausgangsleistung bei 0 dB Dämpfung (Leistungsmesser/Spektrumanalysator, Kanalleistung 1,536 MHz). Daraus wird der Pegel in dBm geschätzt.')),
    rows.length ? h('table.tbl', { tid: 'cal-table' },
      h('thead', null, h('tr', null, h('th', null, T('Kanal')), h('th.r', null, 'MHz'), h('th.r', null, 'dBm @ 0 dB'), h('th.r', null, T('jetzt')), h('th', null, ''))),
      h('tbody', null, rows.map(([k, v]) => h('tr', { class: k === calKey(rf) ? 'cur' : null },
        h('td', null, k), h('td.r.mono', null, D.channelHz(k) ? D.fmtMHz(D.channelHz(k)) : STD.dvbChannelHz('dvbt', k) || STD.dvbChannelHz('dvbc', k) ? D.fmtMHz(STD.dvbChannelHz('dvbt', k) || STD.dvbChannelHz('dvbc', k)) : k),
        h('td.r.mono', null, D.fmtNum(v, 1)), h('td.r.mono', null, `${D.fmtNum(Number(v) - rf.atten_db, 1)} dBm`),
        h('td.r', null, h('button.icon-btn', { type: 'button', title: T('Entfernen'), onclick: () => {
          const n = { ...c }; delete n[k]; S.draft.rf.cal_dbm_at_0db = n; edited('rf.cal_dbm_at_0db', true);
        } }, icon('trash'))))))) : h('p.muted.small', null, T('Noch keine Kalibrierwerte.')),
    h('div.inrow.wrap', null, chSel, val, h('button.btn', { type: 'button', tid: 'cal-add', onclick: add }, icon('plus'), T('Eintragen'))),
  ], { tid: 'card-cal' });
}

// ============================================================ Ensemble
function labelEditor(base, o) {
  const charset = () => S.draft.ensemble.charset;
  const lab = input(`${base}.label`, { tid: `${o.tid}-label`, oninput: () => chipsUpd() });
  const short = input(`${base}.shortlabel`, { tid: `${o.tid}-shortlabel`, maxlength: 8, oninput: () => chipsUpd() });
  const chips = h('div.charchips', { tid: `${o.tid}-shortchips`, title: T('Zeichen anklicken, um das Kurzlabel zu bilden') });
  const lcd = h('div.lcd', null, h('div.l1'), h('div.l2'));
  const chipsUpd = () => {
    const L = [...(getPath(S.draft, `${base}.label`) || '')];
    const mask = D.shortlabelMask(L.join(''), getPath(S.draft, `${base}.shortlabel`)) || [];
    chips.replaceChildren(...L.map((ch, i) => h('button', { type: 'button', class: mask.includes(i) ? 'on' : null, onclick: () => {
      let m = new Set(mask);
      if (m.has(i)) m.delete(i); else { if (m.size >= 8) return toast(T('Kurzlabel: maximal 8 Zeichen'), 'error'); m.add(i); }
      const s = [...m].sort((a, b) => a - b).map(j => L[j]).join('');
      setPath(S.draft, `${base}.shortlabel`, s); short.value = s; edited(`${base}.shortlabel`); chipsUpd();
    } }, ch === ' ' ? '␣' : ch)));
    lcd.firstChild.textContent = (getPath(S.draft, `${base}.label`) || '').padEnd(16, ' ').slice(0, 24);
    lcd.lastChild.textContent = getPath(S.draft, `${base}.shortlabel`) || '';
  };
  chipsUpd();
  return [
    h('div.grid2', null,
      field(T('Label'), lab, { path: `${base}.label`, extra: counter(() => D.labelLen(getPath(S.draft, `${base}.label`), charset()), 16, charset() === 'utf8' ? ' Byte' : ''), hint: charset() === 'utf8' ? T('max. 16 Byte (UTF-8)') : T('max. 16 Zeichen (EBU Latin)') }),
      field(T('Kurzlabel'), short, { path: `${base}.shortlabel`, extra: counter(() => [...(getPath(S.draft, `${base}.shortlabel`) || '')].length, 8), hint: T('Auswahl aus den Label-Zeichen') })),
    field(T('Kurzlabel-Auswahl (FIG 1 Zeichenmaske)'), chips, { wide: true }),
    o.lcd ? h('div.lcdwrap', null, lcd, h('small.muted', null, T('Vorschau Display (16 / 8 Zeichen)'))) : null,
  ];
}

export function pageEnsemble() {
  const e = S.draft.ensemble;
  const country = () => D.COUNTRIES.filter(c => c.ecc.toUpperCase() === (S.draft.ensemble.ecc || '').toUpperCase()
    && c.cids.includes(((S.draft.ensemble.eid || '').replace(/^0x/i, '')[0] || '').toUpperCase()));
  const ctry = h('span.hint-inline');
  const updC = () => { const c = country(); ctry.textContent = c.length ? T('ECC + Länderkennung entsprechen: {list}', { list: c.map(x => x.name).join(' / ') }) : T('Kein Land passt zu ECC + Länderkennung (1. Ziffer der EId) – für Labortests zulässig, Empfänger zeigen ggf. kein Land.'); ctry.classList.toggle('warn', !c.length); };
  S.watchers.push(updC); updC();
  const eccOpts = [...new Map(D.COUNTRIES.map(c => [c.ecc + c.name, { v: c.ecc, label: T('{name} · {ecc} (Kennung {ids})', { name: c.name, ecc: c.ecc.replace('0x', ''), ids: c.cids.join('/') }) }])).values()];
  const eccSel = h('select', { tid: 'ens-country' }, h('option', { value: '' }, T('Land wählen …')), eccOpts.map((o, i) => h('option', { value: i }, o.label)));
  { const m = D.COUNTRIES.findIndex(c => c.ecc.toUpperCase() === (e.ecc || '').toUpperCase() && c.cids.includes((e.eid || '').replace(/^0x/i, '')[0]?.toUpperCase())); if (m >= 0) eccSel.value = String(m); }
  eccSel.addEventListener('change', () => {
    const c = D.COUNTRIES[Number(eccSel.value)]; if (!c) return;
    S.draft.ensemble.ecc = c.ecc;
    const eid = (S.draft.ensemble.eid || '0x0FFF').replace(/^0x/i, '').padStart(4, '0');
    if (!c.cids.includes(eid[0].toUpperCase())) S.draft.ensemble.eid = '0x' + c.cids[0] + eid.slice(1).toUpperCase();
    edited('ensemble.ecc', true);
  });
  const ltoOpts = [{ v: 'auto', label: D.ltoLabel('auto') }, ...Array.from({ length: 49 }, (_, i) => ({ v: i - 24, label: D.ltoLabel(i - 24) }))];
  const rcMode = ['hash', 'off'].includes(e.reconfig_counter) ? e.reconfig_counter : 'fix';

  return h('div.page-grid', null,
    h('div.col', null,
      card(T('Kennung'), 'id', [
        h('div.grid2', null,
          field('EId (hex)', input('ensemble.eid', { tid: 'ens-eid', mono: true, hex: 4, ph: '0x4FFF' }), { path: 'ensemble.eid', hint: T('1. Ziffer = Länderkennung') }),
          field('ECC (hex)', input('ensemble.ecc', { tid: 'ens-ecc', mono: true, hex: 2, ph: '0xE0' }), { path: 'ensemble.ecc' })),
        field(T('Land (setzt ECC und Länderkennung)'), eccSel),
        h('p.ctry', null, icon('info'), ctry),
      ], { tid: 'card-ens-id' }),
      card(T('Zeit & Signalisierung'), 'clock', [
        h('div.grid2', null,
          field(T('Ortszeitversatz (LTO, FIG 0/9)'), select('ensemble.lto', ltoOpts, { tid: 'ens-lto', parse: v => v === 'auto' ? 'auto' : Number(v), rerender: false })),
          field(T('Datum/Uhrzeit (FIG 0/10)'), toggle('ensemble.datetime', T('senden'), { tid: 'ens-datetime' }))),
        h('div.grid2', null,
          field(T('Alarm-Flag (AL, FIG 0/0)'), toggle('ensemble.alarm_flag', T('gesetzt'), { tid: 'ens-alarm', cls: 'red' }), { hint: T('erlaubt Alarm-Cluster 0xFF') }),
          field(T('Int. Zeichentabelle'), input('ensemble.international_table', { type: 'number', min: 1, max: 1, tid: 'ens-inttable', mono: true }), { hint: T('FIG 0/0 · Standard 1') })),
        h('div.grid2', null,
          field('Reconfig-Counter', h('div.inrow', null,
            (() => { const s = h('select', { tid: 'ens-rc-mode' }, h('option', { value: 'hash' }, T('aus Konfig-Hash')), h('option', { value: 'off' }, T('aus')), h('option', { value: 'fix' }, T('fester Wert')));
              s.value = rcMode; s.addEventListener('change', () => { S.draft.ensemble.reconfig_counter = s.value === 'fix' ? 0 : s.value; edited('ensemble.reconfig_counter', true); }); return s; })(),
            rcMode === 'fix' ? input('ensemble.reconfig_counter', { type: 'number', min: 0, max: 1023, tid: 'ens-rc', mono: true }) : null),
            { path: 'ensemble.reconfig_counter', hint: '0–1023, FIG 0/7' })),
      ], { tid: 'card-ens-time' })),
    h('div.col', null,
      card(T('Labels'), 'type', [
        field(T('Zeichensatz (FIG 1 / FIG 2)'), segmented('ensemble.charset', [{ v: 'ebu', label: 'EBU Latin' }, { v: 'utf8', label: 'UTF-8' }], { tid: 'ens-charset' }),
          { hint: T('gilt für Ensemble- und Dienstlabels') }),
        ...labelEditor('ensemble', { tid: 'ens', lcd: true }),
      ], { tid: 'card-ens-label', badge: h('span.pill.live', null, T('live')) })));
}

// ============================================================ Dienste
const SVC_TABS = [
  ['general', T('Allgemein'), 'id'], ['audio', T('Audio & Schutz'), 'wave'], ['source', T('Quelle'), 'music'],
  ['pad', 'DLS', 'text'], ['sls', T('Slideshow & Logo'), 'image'], ['ann', T('Durchsagen'), 'bell'], ['link', T('Linking'), 'link'],
];

export function pageServices() {
  const svcs = S.draft.services;
  if (S.sel >= svcs.length) S.sel = Math.max(0, svcs.length - 1);
  const cap = D.capacity(S.draft);
  const list = h('div.svclist', { tid: 'svc-list' },
    svcs.map((s, i) => {
      const cu = cap.items[i].cu;
      const errs = errorsFor(`services.${i}`).length;
      return h('button.svcitem', { type: 'button', class: i === S.sel ? 'on' : null, tid: `svc-item-${i}`, onclick: () => { S.sel = i; S.render(); } },
        h('i.dot', { style: { background: svcColor(i) } }),
        h('div.si-main', null, h('b', null, s.label || T('(ohne Label)')), h('small.mono', null, `${s.sid} · ${s.type === 'data' ? T('Daten') : D.CODECS[s.audio.codec]?.short} ${D.serviceBitrate(s)} · ${D.protLabel(s.protection)}`)),
        h('span.si-cu' + (cu == null ? '.bad' : ''), null, cu == null ? '!' : `${cu}`, h('small', null, ' CU')),
        errs ? h('span.errdot', { title: T('{n} Fehler', { n: errs }) }, errs) : null);
    }));
  const s = svcs[S.sel];
  const addBtn = h('button.btn.primary', { type: 'button', tid: 'svc-add', onclick: () => addService() }, icon('plus'), T('Dienst hinzufügen'));
  const aside = h('aside.svcside', null,
    h('div.svcside-h', null, h('h3', null, T('Dienste'), ' ', h('span.muted', null, `(${svcs.length})`)), addBtn),
    capMini(cap), list);
  if (!s) return h('div.svc-layout', null, aside, h('div.empty', null, icon('radio', 'big'), h('p', null, T('Noch keine Dienste. Lege einen an.'))));
  const base = `services.${S.sel}`;
  const tabs = SVC_TABS.filter(([k]) => s.type === 'data' ? ['general', 'audio', 'sls', 'ann', 'link'].includes(k) : true);
  if (!tabs.some(([k]) => k === S.svcTab)) S.svcTab = 'general';
  const tabBar = h('div.subtabs', { role: 'tablist' }, tabs.map(([k, label, ic]) =>
    h('button', { type: 'button', role: 'tab', class: k === S.svcTab ? 'on' : null, tid: `svc-tab-${k}`, 'aria-selected': String(k === S.svcTab),
      onclick: () => { S.svcTab = k; S.render(); } }, icon(ic), h('span', null, k === 'audio' && s.type === 'data' ? T('Daten & Schutz') : label),
      errorsIn(k, base) ? h('span.errdot', null, '!') : null)));
  const body = { general: svcGeneral, audio: svcAudio, source: svcSource, pad: svcPad, sls: svcSls, ann: svcAnn, link: svcLink }[S.svcTab](s, base);
  const head = h('div.svchead', null,
    h('i.dot.lg', { style: { background: svcColor(S.sel) } }),
    h('div', null, h('h2', null, s.label || T('(ohne Label)')), h('small.mono.muted', null, `${s.key} · SId ${s.sid} · ${cap.items[S.sel].cu ?? '?'} CU`)),
    h('span.grow'),
    h('button.btn.ghost', { type: 'button', tid: 'svc-dup', title: T('Duplizieren'), onclick: () => addService(structuredClone(s)) }, icon('copy'), h('span.hide-sm', null, T('Duplizieren'))),
    h('button.btn.ghost.danger-text', { type: 'button', tid: 'svc-del', title: T('Löschen'), onclick: async () => {
      if (!(await dialog({ title: T('Dienst löschen?'), body: h('p', null, T('„{label}“ ({sid}) wird aus der Konfiguration entfernt.', { label: s.label, sid: s.sid })), ok: T('Löschen'), danger: true, tid: 'confirm-del' }))) return;
      S.draft.services.splice(S.sel, 1);
      S.draft.clusters = S.draft.clusters.filter(c => c.service !== s.key);
      S.sel = Math.max(0, S.sel - 1); edited('services', true);
    } }, icon('trash'), h('span.hide-sm', null, T('Löschen'))));
  return h('div.svc-layout', null, aside, h('div.svcedit', { tid: 'svc-editor' }, head, tabBar, h('div.svcbody', null, body)));
}

function errorsIn(tab, base) {
  const map = { general: ['key', 'sid', 'ecc', 'label', 'shortlabel'], audio: ['audio.codec', 'audio.samplerate', 'audio.channels', 'audio.bitrate', 'protection'],
    source: ['audio.source'], pad: ['pad.bytes', 'pad.dls'], sls: ['pad.sls'], ann: ['announcements'], link: ['linking'] }[tab] || [];
  return S.errors.some(e => map.some(m => e.path.startsWith(`${base}.${m}`)));
}

function addService(tpl) {
  const svcs = S.draft.services;
  let n = svcs.length + 1;
  while (svcs.some(x => x.key === `srv${n}`)) n++;
  const s = tpl ? tpl : D.newService(`srv${n}`, n);
  s.key = `srv${n}`;
  let sid = D.parseHex(s.sid) || 0xD000;
  const used = new Set(svcs.map(x => D.parseHex(x.sid)));
  while (used.has(sid)) sid++;
  s.sid = s.type === 'data' ? D.hex(sid, 8) : D.hex(sid, 4);
  if (tpl) { s.label = [...`${s.label} Kopie`].slice(0, 16).join('').trim(); if (!D.shortlabelMask(s.label, s.shortlabel)) s.shortlabel = [...s.label.replace(/\s/g, '')].slice(0, 8).join(''); }
  else { const c = D.capacity(S.draft); if (c.free < 48) toast(T('Achtung: nur noch {n} CU frei', { n: c.free }), 'error'); }
  svcs.push(s);
  S.sel = svcs.length - 1; S.svcTab = 'general';
  edited('services', true);
  toast(tpl ? T('Dienst dupliziert') : T('Dienst hinzugefügt'), 'ok', 1800);
}

function capMini(cap) {
  const bar = h('div.capbar.mini', { tid: 'cap-mini' }, cap.items.map((it, i) => it.cu ? h('i', { style: { width: `${Math.min(100, it.cu / D.TOTAL_CU * 100)}%`, background: svcColor(i) }, title: `${it.label}: ${it.cu} CU` }) : null));
  return h('div.capmini' + (cap.over ? '.over' : ''), null, bar, h('small', null, h('b', null, cap.used), T(' / {total} CU belegt · ', { total: D.TOTAL_CU }), cap.over ? h('b.bad', null, T('{n} CU zu viel', { n: -cap.free })) : T('{n} frei', { n: cap.free })));
}

function svcGeneral(s, base) {
  const isData = s.type === 'data';
  return h('div.formgrid', null,
    card(T('Kennung'), null, [
      h('div.grid3', null,
        field('SId (hex)', input(`${base}.sid`, { tid: 'svc-sid', mono: true, hex: isData ? 8 : 4 }), { path: `${base}.sid`, hint: isData ? T('32 bit für Daten') : T('16 bit, 1. Ziffer = Land') }),
        field(T('Schlüssel'), input(`${base}.key`, { tid: 'svc-key', mono: true, parse: v => v.toLowerCase() }), { path: `${base}.key`, hint: T('intern, a–z 0–9 _') }),
        field(T('ECC (abweichend)'), input(`${base}.ecc`, { tid: 'svc-ecc', mono: true, ph: S.draft.ensemble.ecc, parse: v => v.trim() ? v.trim() : null }), { path: `${base}.ecc`, hint: T('leer = Ensemble') })),
      field(T('Typ'), segmented(`${base}.type`, [{ v: 'dabplus', label: 'DAB+' }, { v: 'dab', label: 'DAB (MP2)' }, { v: 'data', label: T('Daten') }], { tid: 'svc-type', onchange: v => fixType(s, v) }), { path: `${base}.type` }),
    ]),
    card(T('Labels'), null, labelEditor(base, { tid: 'svc' }), { badge: h('span.pill.live', null, T('live')) }),
    card(T('Programm'), null, h('div.grid2', null,
      field(T('Programmart (PTy)'), select(`${base}.pty`, D.PTY.map((n, i) => ({ v: i, label: `${i} · ${n}` })), { num: true, tid: 'svc-pty', rerender: false })),
      field(T('Sprache (FIG 0/5)'), select(`${base}.language`, D.LANGUAGES.map(l => ({ v: l.v, label: `${l.name} (${l.v})` })), { tid: 'svc-lang', rerender: false })),
      field(T('Dynamische PTy'), toggle(`${base}.pty_dynamic`, T('PTy kann sich ändern'), { tid: 'svc-ptydyn' })))));
}

function fixType(s, t) {
  if (t === 'dab') { s.audio.codec = 'mp2'; if (!D.samplerates('dab', 'mp2').includes(s.audio.samplerate)) s.audio.samplerate = 48000; if (!D.bitrates('dab', 'mp2', s.audio.samplerate, s.audio.channels).includes(s.audio.bitrate)) s.audio.bitrate = 128; s.protection = { profile: 'UEP', level: 3 }; }
  else if (t === 'dabplus') { if (s.audio.codec === 'mp2') s.audio.codec = 'he-aac-v2'; s.audio.channels = 2; if (![48000, 32000].includes(s.audio.samplerate)) s.audio.samplerate = 48000; s.audio.bitrate = Math.min(192, Math.max(8, Math.round(s.audio.bitrate / 8) * 8)); if (s.protection.profile === 'UEP') s.protection = { profile: 'EEP_A', level: 3 }; }
  else { s.sid = D.hex(D.parseHex(s.sid) || 0xE0D001, 8); if (s.protection.profile === 'UEP') s.protection = { profile: 'EEP_A', level: 3 }; }
  fixProtection(s);
}
function fixProtection(s) {
  const opts = D.protections(s.type, s.audio.codec, D.serviceBitrate(s));
  if (!opts.some(o => o.profile === s.protection.profile && o.level === Number(s.protection.level))) {
    const p = opts.find(o => o.profile === s.protection.profile && o.level === 3) || opts.find(o => o.label === 'EEP 3-A') || opts[0];
    if (p) s.protection = { profile: p.profile, level: p.level };
  }
}

function protSelect(s, base) {
  const opts = D.protections(s.type, s.audio.codec, D.serviceBitrate(s));
  const sel = h('select', { tid: 'svc-protection' },
    ['UEP', 'EEP_A', 'EEP_B'].map(pr => {
      const g = opts.filter(o => o.profile === pr);
      return g.length ? h('optgroup', { label: pr === 'UEP' ? T('UEP (nur MP2, EN 300 401 Tab. 6)') : pr === 'EEP_A' ? 'EEP-A' : T('EEP-B (Bitrate Vielfaches von 32)') },
        g.map(o => h('option', { value: `${o.profile}:${o.level}` }, `${o.label} · ${o.cu} CU`))) : null;
    }));
  sel.value = `${s.protection.profile}:${s.protection.level}`;
  if (sel.selectedIndex < 0) { sel.prepend(h('option', { value: '' }, T('{prot} (ungültig)', { prot: D.protLabel(s.protection) }))); sel.value = ''; }
  sel.addEventListener('change', () => { const [p, l] = sel.value.split(':'); s.protection = { profile: p, level: Number(l) }; edited(`${base}.protection`, true); });
  return sel;
}

function svcAudio(s, base) {
  const cap = D.capacity(S.draft);
  const cu = D.serviceCU(s);
  const protInfo = h('div.cuinfo', null,
    h('div', null, h('small', null, T('Belegung')), h('b', null, cu ?? '–', h('em', null, ' CU'))),
    h('div', null, h('small', null, 'Ensemble'), h('b' + (cap.over ? '.bad' : ''), null, cap.used, h('em', null, ` / ${D.TOTAL_CU}`))),
    h('div', null, h('small', null, T('Frei')), h('b' + (cap.over ? '.bad' : ''), null, cap.free)));
  const protCard = card(T('Fehlerschutz'), 'shield', [
    field(T('Profil & Stufe'), protSelect(s, base), { path: `${base}.protection`, hint: s.type === 'dab' ? T('UEP 1 = stärkster Schutz, 5 = schwächster') : T('1 = stärkster Schutz, 4 = schwächster') }),
    protInfo,
    cap.over ? h('p.alert', null, icon('alert'), T('Ensemble um {n} CU überbucht – Bitrate senken oder schwächeren Schutz wählen.', { n: -cap.free })) : null,
  ]);
  if (s.type === 'data') {
    return h('div.formgrid', null, card(T('Paketdaten'), 'data', h('div.grid2', null,
      field(T('Art'), select(`${base}.data.kind`, [{ v: 'spi', label: 'SPI / EPG' }, { v: 'slideshow_packet', label: T('Slideshow (Paketmodus)') }, { v: 'tpeg', label: 'TPEG' }, { v: 'journaline', label: 'Journaline' }, { v: 'raw', label: T('Rohdaten') }], { tid: 'svc-data-kind' })),
      field(T('Bitrate (kbit/s)'), select(`${base}.data.bitrate`, D.bitrates('data').map(b => ({ v: b, label: `${b} kbit/s` })), { num: true, tid: 'svc-data-bitrate', onchange: () => fixProtection(s) }), { path: `${base}.data.bitrate` }),
      field(T('Paketadresse'), input(`${base}.data.packet_address`, { type: 'number', min: 1, max: 1023, tid: 'svc-data-addr', mono: true })),
      field(T('Dateien'), textarea(`${base}.data.files`, { rows: 2, fmt: v => (v || []).join('\n'), parse: v => v.split('\n').map(x => x.trim()).filter(Boolean), tid: 'svc-data-files' }), { hint: T('eine Datei je Zeile (media/…)') }))), protCard);
  }
  const a = s.audio;
  const codecOpts = Object.entries(D.CODECS).filter(([, c]) => c.type === s.type).map(([v, c]) => ({ v, label: c.name }));
  const brs = D.bitrates(s.type, a.codec, a.samplerate, a.channels);
  const brHint = D.bitrateHint(s.type, a.codec, a.bitrate, a.channels);
  const after = () => { if (!D.channelOptions(a.codec).includes(a.channels)) a.channels = 2; if (!D.samplerates(s.type, a.codec).includes(a.samplerate)) a.samplerate = 48000;
    const b = D.bitrates(s.type, a.codec, a.samplerate, a.channels); if (!b.includes(a.bitrate)) a.bitrate = b.reduce((p, c) => Math.abs(c - a.bitrate) < Math.abs(p - a.bitrate) ? c : p, b[0]); fixProtection(s); };
  return h('div.formgrid', null,
    card('Codec', 'wave', [
      h('div.grid2', null,
        field('Codec', select(`${base}.audio.codec`, codecOpts, { tid: 'svc-codec', onchange: after }), { path: `${base}.audio.codec` }),
        field(T('Abtastrate'), select(`${base}.audio.samplerate`, D.samplerates(s.type, a.codec).map(r => ({ v: r, label: `${r / 1000} kHz${a.codec.startsWith('he') ? T(' (SBR intern {k} kHz)', { k: r / 2000 }) : ''}` })), { num: true, tid: 'svc-samplerate', onchange: after }), { path: `${base}.audio.samplerate` }),
        field(T('Kanäle'), segmented(`${base}.audio.channels`, D.channelOptions(a.codec).map(c => ({ v: c, label: c === 2 ? 'Stereo' : 'Mono' })), { tid: 'svc-channels', onchange: after }), { path: `${base}.audio.channels`, hint: a.codec === 'he-aac-v2' ? T('HE-AAC v2 (PS) ist immer Stereo') : '' }),
        field(T('Bitrate'), select(`${base}.audio.bitrate`, brs.map(b => ({ v: b, label: `${b} kbit/s` })), { num: true, tid: 'svc-bitrate', onchange: () => fixProtection(s) }), { path: `${base}.audio.bitrate`, hint: brHint || (a.codec === 'mp2' ? 'ISO 11172-3 / 13818-3' : T('8–192 kbit/s, Vielfache von 8')) }),
        a.codec === 'mp2' ? field(T('MP2-Modus'), select(`${base}.audio.mp2_mode`, D.MP2_MODES.filter(m => a.channels === 1 ? m.v === 'm' : m.v !== 'm').map(m => ({ v: m.v, label: m.name })), { tid: 'svc-mp2mode' })) : null,
      ),
      h('div.grid2', null,
        field(T('Audio-Gain (dB)'), input(`${base}.audio.gain_db`, { type: 'number', step: 0.5, min: -40, max: 20, tid: 'svc-gain', mono: true })),
        field(T('Bandbreite (Hz)'), input(`${base}.audio.bandwidth_hz`, { type: 'number', step: 500, min: 0, max: 24000, tid: 'svc-bw', mono: true }), { hint: T('0 = Encoder-Standard') })),
    ]), protCard);
}

const SOURCES = [
  ['file', T('Datei'), 'music'], ['playlist', T('Playlist'), 'playlist'], ['tone', T('Ton'), 'wave'], ['noise', T('Rauschen'), 'noise'], ['pinknoise', T('Rosa Rauschen'), 'noise'],
  ['sweep', T('Sweep'), 'ramp'], ['lr_test', T('L/R-Test'), 'lr'], ['polarity', T('Polarität'), 'polarity'], ['silence', T('Stille'), 'mute'],
];
function svcSource(s, base) { return h('div.formgrid', null, audioSourceCard(s.audio.source, `${base}.audio.source`, { key: s.key })); }

/** Generische Audioquelle (DAB-Dienst, UKW-Träger, DVB-Programmton). o: {key, title, tid, prefix, kinds} */
export function audioSourceCard(src, p, o = {}) {
  const tp = o.prefix || 'src';
  const params = [];
  if (src.kind === 'file') {
    const audios = S.media.filter(m => m.kind === 'audio');
    const fsel = h('select', { tid: `${tp}-file` }, h('option', { value: '' }, audios.length ? T('Datei wählen …') : T('Keine Audiodateien – hochladen')), audios.map(m => h('option', { value: m.path }, m.name)));
    fsel.value = src.file || '';
    if (src.file && fsel.selectedIndex < 0) { fsel.append(h('option', { value: src.file }, src.file)); fsel.value = src.file; }
    fsel.addEventListener('change', () => { src.file = fsel.value; edited(`${p}.file`); });
    params.push(field(T('Audiodatei'), h('div.inrow', null, fsel, uploadBtn('audio', 'audio/*', 'src-upload', async path => { src.file = path; edited(`${p}.file`, true); })), { path: `${p}.file`, wide: true, hint: T('MP3/WAV/FLAC… wird per ffmpeg eingespielt') }));
    params.push(field(T('Wiederholen'), toggle(`${p}.loop`, T('Endlosschleife'), { tid: `${tp}-loop` })));
  }
  let plExtra = null;
  if (src.kind === 'playlist') {
    const psel = h('select', { tid: `${tp}-playlist` }, h('option', { value: '' }, S.playlists.length ? T('Playlist wählen …') : T('Keine Playlists – unter „Medien & Playlists“ anlegen')),
      S.playlists.map(pl => h('option', { value: pl.name }, pl.count != null ? T('{name} ({n} Titel)', { name: pl.name, n: pl.count }) : pl.name)));
    psel.value = src.playlist || '';
    if (src.playlist && psel.selectedIndex < 0) { psel.append(h('option', { value: src.playlist }, T('{name} (fehlt)', { name: src.playlist }))); psel.value = src.playlist; }
    psel.addEventListener('change', () => { src.playlist = psel.value; edited(`${p}.playlist`, true); });
    params.push(field(T('Playlist'), h('div.inrow', null, psel, h('a.btn', { href: '#/medien', tid: `${tp}-playlist-edit`, title: T('Playlists bearbeiten') }, icon('playlist'), T('Bearbeiten'))),
      { path: `${p}.playlist`, wide: true, hint: T('Titel für Titel nahtlos in dieselbe PCM-Pipe (48 kHz)') }));
    params.push(field(T('Reihenfolge'), toggle(`${p}.shuffle`, T('Zufall'), { tid: `${tp}-shuffle` })));
    params.push(field(T('Wiederholen'), toggle(`${p}.loop`, T('Endlos'), { tid: `${tp}-loop` })));
    params.push(field(T('Lautheitsangleich'), toggle(`${p}.normalize`, 'EBU R128 (−23 LUFS)', { tid: `${tp}-normalize` }), { hint: 'ffmpeg loudnorm' }));
    params.push(field('Crossfade (s)', input(`${p}.crossfade_s`, { type: 'number', min: 0, max: 30, step: 0.5, tid: `${tp}-crossfade`, mono: true }), { path: `${p}.crossfade_s`, hint: T('0 = hart') }));
    params.push(field(T('Titel als DLS'), toggle(`${p}.dls_from_tags`, T('Titel/Interpret aus Tags'), { tid: `${tp}-dls-tags` }), { hint: T('DLS + DL Plus bei Titelwechsel') }));
    plExtra = o.key ? h('div.np-wrap', null, h('label.lbl', null, T('Jetzt läuft')), nowPlayingBox({ key: o.key }, { tid: tp === 'src' ? undefined : `np-${tp}` })) : null;
  }
  if (['tone', 'noise', 'pinknoise', 'sweep', 'lr_test', 'polarity'].includes(src.kind))
    params.push(field(T('Pegel (dBFS)'), input(`${p}.level_dbfs`, { type: 'number', min: -60, max: 0, step: 1, tid: `${tp}-level`, mono: true }), { hint: T('Referenz: −18 dBFS') }));
  if (['tone', 'polarity'].includes(src.kind))
    params.push(field(T('Frequenz (Hz)'), input(`${p}.freq_hz`, { type: 'number', min: 10, max: 20000, tid: `${tp}-freq`, mono: true })));
  if (src.kind === 'lr_test') {
    params.push(field(T('Links (Hz)'), input(`${p}.left_hz`, { type: 'number', min: 10, max: 20000, tid: `${tp}-left`, mono: true })));
    params.push(field(T('Rechts (Hz)'), input(`${p}.right_hz`, { type: 'number', min: 10, max: 20000, tid: `${tp}-right`, mono: true })));
  }
  if (src.kind === 'sweep') {
    params.push(field(T('Von (Hz)'), input(`${p}.sweep_from_hz`, { type: 'number', min: 10, max: 24000, tid: `${tp}-sfrom`, mono: true })));
    params.push(field(T('Bis (Hz)'), input(`${p}.sweep_to_hz`, { type: 'number', min: 10, max: 24000, tid: `${tp}-sto`, mono: true })));
    params.push(field(T('Dauer (s)'), input(`${p}.sweep_s`, { type: 'number', min: 1, max: 600, tid: `${tp}-sdur`, mono: true })));
  }
  const desc = { file: T('Beliebige Audiodatei vom Server.'), playlist: T('Mehrere Titel nacheinander, nahtlos; Titel und Interpret können automatisch als DLS gesendet werden.'), tone: T('Sinuston auf beiden Kanälen – Referenz für Pegel und Klirrfaktor.'), noise: T('Weißes Rauschen – Codec-Stresstest.'),
    pinknoise: T('Rosa Rauschen (−3 dB/Oktave) – Frequenzgang mit Analysator.'), sweep: T('Logarithmischer Gleitton – Frequenzgang des Codecs/Empfängers.'),
    lr_test: T('Unterschiedliche Töne links/rechts – Kanalzuordnung prüfen.'), polarity: T('Asymmetrischer Impuls/Ton – Polarität (Phase) prüfen.'), silence: T('Digitale Stille – Rauschabstand des Empfängers.') }[src.kind];
  const kinds = o.kinds ? SOURCES.filter(([k]) => o.kinds.includes(k)) : SOURCES;
  return card(o.title || T('Audioquelle'), 'music', [
      h('div.srcgrid', { tid: `${tp}-kind` }, kinds.map(([k, l, ic]) => h('button', { type: 'button', class: src.kind === k ? 'on' : null, tid: `${tp}-kind-${k}`,
        onclick: () => { src.kind = k; edited(`${p}.kind`, true); } }, icon(ic), h('span', null, l)))),
      h('p.hint', null, icon('info'), desc),
      params.length ? h('div.grid3', null, params) : null,
      plExtra,
    ], { tid: o.tid || 'card-source' });
}

function uploadBtn(kind, accept, tid, onDone, multiple = false) {
  const fi = h('input', { type: 'file', accept, hidden: true, multiple, tid: tid + '-input' });
  fi.addEventListener('change', async () => {
    for (const f of fi.files) {
      try { const path = await upload(f, kind); toast(T('{name} hochgeladen', { name: f.name }), 'ok', 2000); await onDone(path, f); }
      catch (e) { toast(T('Upload fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
    }
    fi.value = '';
  });
  return h('span', null, fi, h('button.btn', { type: 'button', tid, onclick: () => fi.click() }, icon('upload'), T('Hochladen')));
}

function svcPad(s, base) {
  const dls = s.pad.dls;
  const txt = textarea(`${base}.pad.dls.text`, { tid: 'dls-text', rows: 3 });
  const sendBtn = h('button.btn.primary', { type: 'button', tid: 'dls-send', onclick: async () => {
    if (!txtOk()) return toast(T('DLS-Text zu lang (max. 128 Zeichen)'), 'error');
    const payload = { dls: { service: s.key, text: dls.text } };
    if (dls.dlplus.enable) payload.dls.dlplus = dls.dlplus;
    const ok = await sendLive(payload, T('DLS gesendet'));
    if (ok && S.server) { const ss = S.server.services.find(x => x.key === s.key); if (ss) ss.pad.dls.text = dls.text; refreshDecor(); }
  } }, icon('send'), T('Live senden'));
  const txtOk = () => [...(dls.text || '')].length <= 128;
  const dlp = dls.dlplus;
  const dlpFields = [['artist', T('Interpret (ITEM.ARTIST)')], ['title', T('Titel (ITEM.TITLE)')], ['album', T('Album (ITEM.ALBUM)')], ['station', T('Sender (STATIONNAME.LONG)')], ['program', T('Sendung (PROGRAMME.NOW)')], ['url', 'URL (PROGRAMME.HOMEPAGE)']];
  return h('div.formgrid', null,
    card(T('Programmbegleitende Daten (PAD)'), 'data', h('div.grid2', null,
      field('PAD', toggle(`${base}.pad.enable`, T('aktiv'), { tid: 'pad-enable', rerender: true })),
      field(T('X-PAD-Länge (Byte)'), input(`${base}.pad.bytes`, { type: 'number', min: 0, max: 255, tid: 'pad-bytes', mono: true }), { path: `${base}.pad.bytes`, hint: T('typisch 16–58; Slideshow braucht ≥ 26') }))),
    card('Dynamic Label (DLS)', 'text', [
      h('div.inrow.wrap', null, toggle(`${base}.pad.dls.enable`, T('DLS senden'), { tid: 'dls-enable' }), h('span.grow'),
        h('label.inline', null, T('Zeichensatz'), ' ', select(`${base}.pad.dls.charset`, [{ v: 'utf8', label: 'UTF-8' }, { v: 'ebu', label: 'EBU Latin' }], { tid: 'dls-charset', rerender: false }))),
      field(T('Text'), txt, { path: `${base}.pad.dls.text`, wide: true, extra: counter(() => [...(dls.text || '')].length, 128) }),
      h('div.inrow.wrap', null, sendBtn, h('span.muted.small', null, T('sendet sofort über die laufende Kette (ohne Neustart)')), h('span.grow'),
        toggle(`${base}.pad.dls.remove_label`, T('Label vor Text entfernen'), { tid: 'dls-rmlabel' })),
      h('details.sub', { open: dlp.enable }, h('summary', null, 'DL Plus (Tags)'),
        field('', toggle(`${base}.pad.dls.dlplus.enable`, T('DL Plus senden'), { tid: 'dlplus-enable' })),
        h('div.grid2', null, dlpFields.map(([k, l]) => field(l, input(`${base}.pad.dls.dlplus.${k}`, { tid: `dlplus-${k}` }))))),
      h('details.sub', { open: (dls.rotate || []).length > 0 }, h('summary', null, T('Rotation')),
        h('div.grid2', null,
          field(T('Texte (je Zeile einer)'), textarea(`${base}.pad.dls.rotate`, { rows: 4, tid: 'dls-rotate', fmt: v => (v || []).join('\n'), parse: v => v.split('\n').filter(x => x.trim()) }), { hint: T('wechseln zyklisch mit dem Haupttext') }),
          field(T('Intervall (s)'), input(`${base}.pad.dls.rotate_s`, { type: 'number', min: 2, max: 600, tid: 'dls-rotate-s', mono: true })))),
    ], { tid: 'card-dls', badge: h('span.pill.live', null, T('live')) }));
}

function svcSls(s, base) {
  const sls = s.pad.sls;
  const thumbs = h('div.thumbs', { tid: 'sls-thumbs' }, sls.slides.length ? sls.slides.map((path, i) => {
    const src = previewCache.get(path) || slideSrc(path);
    return h('figure.thumb', null,
      h('img', { src, alt: path, loading: 'lazy', onerror: e => e.target.classList.add('broken') }),
      h('figcaption', null, path.split('/').pop()),
      h('div.thumb-act', null,
        h('button.icon-btn', { type: 'button', title: T('Jetzt senden'), tid: `sls-send-${i}`, onclick: () => sendLive({ slide: { service: s.key, file: path } }, T('Slide gesendet')) }, icon('send')),
        h('button.icon-btn', { type: 'button', title: T('Nach vorn'), disabled: i === 0, onclick: () => { sls.slides.splice(i - 1, 0, sls.slides.splice(i, 1)[0]); edited(`${base}.pad.sls.slides`, true); } }, icon('left')),
        h('button.icon-btn', { type: 'button', title: T('Entfernen'), tid: `sls-del-${i}`, onclick: () => { sls.slides.splice(i, 1); edited(`${base}.pad.sls.slides`, true); } }, icon('trash'))));
  }) : h('p.muted.small', null, T('Noch keine Bilder. Empfohlen: 320×240 JPEG/PNG, < 50 kB.')));
  const imgs = S.media.filter(m => m.kind === 'image' && !sls.slides.includes(m.path));
  const pick = h('select', { tid: 'sls-pick' }, h('option', { value: '' }, imgs.length ? T('Vorhandenes Bild hinzufügen …') : T('keine weiteren Bilder')), imgs.map(m => h('option', { value: m.path }, m.name)));
  pick.addEventListener('change', () => { if (pick.value) { sls.slides.push(pick.value); edited(`${base}.pad.sls.slides`, true); } });
  const logo = s.spi.logo;
  return h('div.formgrid', null,
    card('MOT-Slideshow', 'image', [
      h('div.inrow.wrap', null, toggle(`${base}.pad.sls.enable`, T('Slideshow senden'), { tid: 'sls-enable' }), h('span.grow'),
        uploadBtn('slide', 'image/jpeg,image/png', 'sls-upload', async path => { sls.slides.push(path); edited(`${base}.pad.sls.slides`, true); }, true), pick),
      s.pad.sls.enable && s.pad.bytes < 26 ? h('p.alert', null, icon('alert'), T('X-PAD {n} Byte ist für Slideshow sehr klein – Bilder brauchen sehr lange.', { n: s.pad.bytes })) : null,
      thumbs,
      h('div.grid4', null,
        field(T('Intervall (s)'), input(`${base}.pad.sls.interval_s`, { type: 'number', min: 1, max: 600, tid: 'sls-interval', mono: true })),
        field(T('Max. Größe (Byte)'), input(`${base}.pad.sls.max_bytes`, { type: 'number', min: 1000, max: 500000, step: 1000, tid: 'sls-maxbytes', mono: true }), { hint: T('größere werden neu komprimiert') }),
        field(T('Rohdaten'), toggle(`${base}.pad.sls.raw`, T('nicht umrechnen'), { tid: 'sls-raw' })),
        field(T('Kategorien'), toggle(`${base}.pad.sls.categories`, 'CatSLS', { tid: 'sls-cat' }))),
    ], { tid: 'card-sls' }),
    card(T('Senderlogo (SPI)'), 'star', [
      h('div.logo-row', null,
        h('div.logo-prev', null, logo ? h('img', { src: previewCache.get(logo) || slideSrc(logo), alt: 'Logo', onerror: e => e.target.classList.add('broken') }) : icon('image', 'big')),
        h('div', null,
          h('p.hint', null, T('Logo für Senderliste/Thumbnails (ETSI TS 102 818 / TS 102 371). Quadratisch, z. B. 128×128 PNG.')),
          h('div.inrow.wrap', null, uploadBtn('logo', 'image/png,image/jpeg', 'logo-upload', async path => { s.spi.logo = path; edited(`${base}.spi.logo`, true); }),
            logo ? h('button.btn.ghost', { type: 'button', onclick: () => { s.spi.logo = ''; edited(`${base}.spi.logo`, true); } }, T('Entfernen')) : null),
          logo ? h('small.mono.muted', null, logo) : null)),
    ]));
}

function svcAnn(s, base) {
  const sup = s.announcements.supported;
  const clusters = S.draft.clusters;
  return h('div.formgrid', null,
    card(T('Unterstützte Durchsagen (ASu, FIG 0/18)'), 'bell', [
      h('div.chipgrid', { tid: 'asu' }, D.ANNOUNCEMENTS.map(a => {
        const cb = h('input', { type: 'checkbox', tid: `asu-${a.v}` }); cb.checked = sup.includes(a.v);
        cb.addEventListener('change', () => { if (cb.checked) sup.push(a.v); else sup.splice(sup.indexOf(a.v), 1); edited(`${base}.announcements`); });
        return h('label.chip' + (a.v === 'Alarm' ? '.red' : ''), null, cb, h('span', null, a.name));
      })),
      h('div.inrow', null, h('button.btn.sm', { type: 'button', onclick: () => { s.announcements.supported = D.ANNOUNCEMENTS.map(a => a.v); edited(base, true); } }, T('Alle')),
        h('button.btn.sm', { type: 'button', onclick: () => { s.announcements.supported = []; edited(base, true); } }, T('Keine'))),
    ]),
    card(T('Cluster-Zugehörigkeit'), 'layers', clusters.length ? h('div.chipgrid', null, clusters.map(c => {
      const cb = h('input', { type: 'checkbox', tid: `asu-cluster-${c.id}` }); cb.checked = s.announcements.clusters.includes(c.id);
      cb.addEventListener('change', () => { const a = s.announcements.clusters; if (cb.checked) a.push(c.id); else a.splice(a.indexOf(c.id), 1); edited(`${base}.announcements`); });
      return h('label.chip' + (c.id === 255 ? '.red' : ''), null, cb, h('span', null, c.id === 255 ? T('Alarm (0xFF)') : `Cluster ${c.id}`));
    })) : h('p.muted.small', null, T('Noch keine Cluster – im Bereich „Durchsagen“ anlegen.'))));
}

function svcLink(s, base) {
  const lk = s.linking;
  const oe = lk.other_ensembles;
  return h('div.formgrid', null,
    card(T('UKW-Verknüpfung (FIG 0/21, 0/6)'), 'link', h('div.grid2', null,
      field(T('UKW-PI (hex)'), input(`${base}.linking.fm_pi`, { tid: 'link-pi', mono: true, hex: 4, ph: T('z. B. 0xD3C1'), parse: v => v.trim() || null }), { path: `${base}.linking.fm_pi` }),
      field(T('UKW-Frequenzen (MHz)'), input(`${base}.linking.fm_freqs_mhz`, { tid: 'link-freqs', mono: true, ph: '87,6; 99,1', fmt: v => (v || []).map(x => String(x).replace('.', ',')).join('; '),
        parse: v => v.split(/[;\s]+/).map(x => x.trim().replace(',', '.')).filter(Boolean).map(Number) }), { path: `${base}.linking.fm_freqs_mhz`, hint: T('durch Semikolon getrennt') }))),
    card(T('Andere Ensembles (FIG 0/24)'), 'antenna', [
      oe.length ? h('table.tbl', null, h('thead', null, h('tr', null, h('th', null, 'EId'), h('th', null, T('Kanal')), h('th', null, ''))),
        h('tbody', null, oe.map((o, i) => h('tr', null,
          h('td', null, input(`${base}.linking.other_ensembles.${i}.eid`, { mono: true, hex: 4, tid: `link-oe-eid-${i}` })),
          h('td', null, select(`${base}.linking.other_ensembles.${i}.channel`, D.CHANNELS.map(c => ({ v: c.id, label: c.id })), { rerender: false })),
          h('td.r', null, h('button.icon-btn', { type: 'button', onclick: () => { oe.splice(i, 1); edited(base, true); } }, icon('trash'))))))) : h('p.muted.small', null, T('Keine Verweise.')),
      h('button.btn.sm', { type: 'button', tid: 'link-oe-add', onclick: () => { oe.push({ eid: '0x10BC', channel: '11D' }); edited(base, true); } }, icon('plus'), T('Ensemble hinzufügen')),
    ]));
}

// ============================================================ Durchsagen
export function pageAnnouncements() {
  const cl = S.draft.clusters;
  const live = h('div.anngrid', { tid: 'ann-live' }, cl.length ? cl.map((c, i) => annCard(c, i)) : h('div.empty', null, icon('bell', 'big'), h('p', null, T('Keine Durchsage-Cluster angelegt.'))));
  const addCl = alarm => {
    const id = alarm ? 255 : (() => { let n = 1; while (cl.some(c => c.id === n)) n++; return n; })();
    if (alarm && cl.some(c => c.id === 255)) return toast(T('Alarm-Cluster existiert bereits'), 'error');
    const svc = S.draft.services[0]?.key;
    cl.push({ id, flags: alarm ? ['Alarm'] : ['Traffic'], service: svc, active: false });
    if (alarm && !S.draft.ensemble.alarm_flag) toast(T('Hinweis: AL-Flag im Ensemble ist aus – Alarm wird ignoriert.'), 'info', 5000);
    edited('clusters', true);
  };
  return h('div.page-stack', null,
    h('div.toolbar', null,
      h('p.hint.grow', null, icon('info'), T('Die großen Schalter wirken sofort (live). Neue oder geänderte Cluster brauchen „Übernehmen“.')),
      h('button.btn', { type: 'button', tid: 'cluster-add', onclick: () => addCl(false) }, icon('plus'), 'Cluster'),
      h('button.btn.danger-ghost', { type: 'button', tid: 'cluster-add-alarm', onclick: () => addCl(true) }, icon('alert'), T('Alarm-Cluster 0xFF'))),
    !S.draft.ensemble.alarm_flag && cl.some(c => c.id === 255) ? h('p.alert', null, icon('alert'), T('Alarm-Cluster vorhanden, aber AL-Flag im Ensemble ist nicht gesetzt.'),
      h('button.btn.sm', { type: 'button', onclick: () => { S.draft.ensemble.alarm_flag = true; edited('ensemble.alarm_flag', true); } }, T('AL-Flag setzen'))) : null,
    live);
}

function annCard(c, i) {
  const base = `clusters.${i}`;
  const alarm = c.id === 255;
  const saved = S.server?.clusters.find(x => x.id === c.id);
  const sw = h('input', { type: 'checkbox', tid: `ann-switch-${c.id}`, role: 'switch', disabled: !saved });
  sw.checked = !!c.active;
  sw.addEventListener('change', async () => {
    const ok = await sendLive({ announcement: { cluster: c.id, active: sw.checked } }, T(sw.checked ? 'Durchsage {id} aktiv' : 'Durchsage {id} beendet', { id: alarm ? 'ALARM' : c.id }));
    if (!ok) { sw.checked = !sw.checked; return; }
    c.active = sw.checked; if (saved) saved.active = sw.checked;
    S.render(true);
  });
  const svcOpts = S.draft.services.map(s => ({ v: s.key, label: `${s.label} (${s.sid})` }));
  const target = S.draft.services.find(s => s.key === c.service);
  const missing = target ? c.flags.filter(f => !target.announcements.supported.includes(f)) : [];
  return h('section.anncard' + (alarm ? '.alarm' : '') + (c.active ? '.active' : ''), { tid: `cluster-${c.id}` },
    h('header', null,
      h('div', null, h('small', null, alarm ? T('Alarm-Cluster') : 'Cluster'), h('h3.mono', null, alarm ? '0xFF' : String(c.id))),
      h('button.icon-btn', { type: 'button', title: T('Cluster löschen'), tid: `cluster-del-${c.id}`, onclick: () => { S.draft.clusters.splice(i, 1); edited('clusters', true); } }, icon('trash'))),
    h('label.bigswitch', null, sw, h('span.track', null, h('i')), h('span.bs-text', null, h('b', null, c.active ? T('Durchsage aktiv') : T('Durchsage aus')),
      h('small', null, saved ? T('wirkt sofort') : T('erst übernehmen')))),
    alarm ? null : field('Cluster-ID', input(`${base}.id`, { type: 'number', min: 1, max: 254, tid: `cluster-id-${c.id}`, mono: true }), { path: `${base}.id` }),
    field(T('Durchsage-Dienst'), select(`${base}.service`, svcOpts, { tid: `cluster-svc-${c.id}` }), { path: `${base}.service`,
      hint: missing.length ? T('Achtung: Dienst meldet {list} nicht als ASu', { list: missing.map(D.annName).join(', ') }) : '' }),
    h('div.chipgrid.small', null, D.ANNOUNCEMENTS.map(a => {
      const cb = h('input', { type: 'checkbox' }); cb.checked = c.flags.includes(a.v);
      cb.addEventListener('change', () => { if (cb.checked) c.flags.push(a.v); else c.flags.splice(c.flags.indexOf(a.v), 1); edited(`${base}.flags`, true); });
      return h('label.chip' + (a.v === 'Alarm' ? '.red' : ''), null, cb, h('span', null, a.name));
    })));
}

// ============================================================ Kapazität
export function pageCapacity() {
  const cap = D.capacity(S.draft);
  const pct = x => `${(x / Math.max(D.TOTAL_CU, cap.used) * 100).toFixed(3)}%`;
  const bar = h('div.capbar.big' + (cap.over ? '.over' : ''), { tid: 'cap-bar' },
    cap.items.map((it, i) => it.cu ? h('i', { style: { width: pct(it.cu), background: svcColor(i) }, title: `${it.label}: ${it.cu} CU` }, it.cu > 40 ? h('span', null, it.label) : null) : null),
    cap.over ? h('b.limit', { style: { left: pct(D.TOTAL_CU) } }) : null);
  const alt = it => {
    const s = it.svc;
    const opts = D.protections(s.type, s.audio?.codec, D.serviceBitrate(s));
    const cheaper = opts.filter(o => o.cu < (it.cu || Infinity)).sort((a, b) => b.cu - a.cu)[0];
    return cheaper ? `${cheaper.label}: ${cheaper.cu} CU (−${it.cu - cheaper.cu})` : '–';
  };
  return h('div.page-stack', null,
    card(T('Ensemble-Kapazität'), 'pie', [
      h('div.capnums', null,
        h('div', null, h('small', null, T('Belegt')), h('b' + (cap.over ? '.bad' : ''), { tid: 'cap-used' }, cap.used), h('em', null, ' CU')),
        h('div', null, h('small', null, T('Frei')), h('b' + (cap.over ? '.bad' : ''), null, cap.free), h('em', null, ' CU')),
        h('div', null, h('small', null, T('Auslastung')), h('b', null, `${Math.round(cap.used / D.TOTAL_CU * 100)}`), h('em', null, ' %')),
        h('div', null, h('small', null, T('Dienste')), h('b', null, cap.items.length))),
      bar,
      h('div.capscale', null, h('span', null, '0'), h('span', null, '216'), h('span', null, '432'), h('span', null, '648'), h('span', null, '864 CU')),
      cap.over ? h('div.alert.big', { tid: 'cap-over' }, icon('alert'), h('div', null,
        h('b', null, T('Überbucht um {n} CU – Übernehmen ist gesperrt.', { n: -cap.free })),
        h('p', null, T('Ein DAB-Ensemble hat 864 Capacity Units (CU) im Main Service Channel. Abhilfe: Bitrate senken, schwächeren Fehlerschutz wählen (z. B. EEP 3-A statt 2-A, EEP-B statt EEP-A) oder einen Dienst entfernen. Die Spalte „Sparen“ zeigt die nächstgünstigere Schutzstufe.')))) : null,
      cap.invalid.length ? h('p.alert', null, icon('alert'), T('Ungültige Schutz/Bitrate-Kombination bei: {list}', { list: cap.invalid.map(i => i.label).join(', ') })) : null,
    ], { tid: 'card-capacity' }),
    card(T('Belegung je Dienst'), 'table', h('div.tblwrap', null, h('table.tbl', { tid: 'cap-table' },
      h('thead', null, h('tr', null, h('th', null, ''), h('th', null, T('Dienst')), h('th', null, T('Typ')), h('th.r', null, 'kbit/s'), h('th', null, T('Schutz')), h('th.r', null, 'CU'), h('th.r', null, T('Anteil')), h('th.hide-sm', null, T('Sparen')))),
      h('tbody', null, cap.items.map((it, i) => h('tr', { class: it.cu == null ? 'bad' : null, onclick: () => { S.sel = i; location.hash = '#/dienste'; } },
        h('td', null, h('i.dot', { style: { background: svcColor(i) } })),
        h('td', null, it.label), h('td', null, it.svc.type === 'data' ? T('Daten') : D.CODECS[it.svc.audio.codec]?.short),
        h('td.r.mono', null, D.serviceBitrate(it.svc)), h('td', null, D.protLabel(it.svc.protection)),
        h('td.r.mono', null, it.cu ?? T('ungültig')), h('td.r.mono', null, it.cu ? `${(it.cu / D.TOTAL_CU * 100).toFixed(1)} %` : '–'),
        h('td.hide-sm.muted', null, alt(it))))),
      h('tfoot', null, h('tr', null, h('td'), h('td', null, T('Summe')), h('td'), h('td.r.mono', null, cap.items.reduce((a, b) => a + D.serviceBitrate(b.svc), 0)), h('td'),
        h('td.r.mono', null, h('b', null, cap.used)), h('td.r.mono', null, `${(cap.used / D.TOTAL_CU * 100).toFixed(1)} %`), h('td.hide-sm')))))),
    card(T('Formeln'), 'info', h('div.formula', null,
      h('p', null, h('b', null, 'EEP-A'), T(' (Bitrate 8n kbit/s): 1-A = 12n, 2-A = 8n, 3-A = 6n, 4-A = 4n CU')),
      h('p', null, h('b', null, 'EEP-B'), T(' (Bitrate 32n kbit/s): 1-B = 27n, 2-B = 21n, 3-B = 18n, 4-B = 15n CU')),
      h('p', null, h('b', null, 'UEP'), T(' (nur MP2): Tabelle 6 aus EN 300 401, z. B. 128 kbit/s UEP 3 = 96 CU, 192 kbit/s UEP 3 = 140 CU')))));
}

// ============================================================ Prüfen
export function pageCheck() {
  const p = load('checkParams', { seconds: 20, mon_seconds: 8, rx_gain_db: 30 });
  const num = (k, tid, min, max) => { const el = h('input.mono', { type: 'number', min, max, tid }); el.value = p[k]; el.addEventListener('change', () => { p[k] = Number(el.value); save('checkParams', p); }); return el; };
  const run = async (kind) => {
    if (kind === 'monitor' && !txRunning()) { if (!(await dialog({ title: T('Kette läuft nicht'), body: h('p', null, T('Für den Kontrollempfang muss der Sender laufen und eingeschaltet sein. Trotzdem versuchen?')), ok: T('Trotzdem'), tid: 'confirm-monitor' }))) return; }
    S.busy[kind] = true; S.reportTab = kind; S.render();
    try {
      const body = kind === 'check' ? { seconds: p.seconds } : { seconds: p.mon_seconds, rx_gain_db: p.rx_gain_db };
      const r = await api('POST', kind === 'check' ? '/api/check' : '/api/monitor', body);
      S.state[kind] = r?.report || r;
      toast(kind === 'check' ? T('Offline-Prüfung fertig') : T('Kontrollempfang fertig'), 'ok');
    } catch (e) { toast(T(kind === 'check' ? 'Prüfung fehlgeschlagen: {msg}' : 'Kontrollempfang fehlgeschlagen: {msg}', { msg: e.message }), 'error', 8000); }
    finally { S.busy[kind] = false; S.render(); }
  };
  const dirty = changes().all.length > 0;
  const btn = (kind, tid, label, ic) => h('button.btn.primary.lg', { type: 'button', tid, disabled: !!S.busy[kind], onclick: () => run(kind) },
    S.busy[kind] ? h('span.spinner') : icon(ic), S.busy[kind] ? T('läuft …') : label);
  const rep = S.state[S.reportTab];
  return h('div.page-stack', null,
    h('div.grid2.checkactions', null,
      card(T('Offline-Prüfung'), 'scan', [
        h('p.hint', null, { dab: T('Rendert die gespeicherte Konfiguration und dekodiert sie mit welle-cli – ohne HF. Prüft Multiplex, Labels, Codecs, PAD.'), fm: T('Rendert MPX/IQ und misst Hub, Pilot, 57-kHz-Phase, MPX-Leistung, Stereotrennung; RDS per redsea – ohne HF.'), dvbt: T('Erzeugt den Transportstrom und prüft Tabellen, Dienste, LCN, NIT, Bitrate, PCR und Decodierbarkeit (tsanalyze/ffprobe) – ohne HF.'), dvbc: T('Erzeugt den Transportstrom und prüft Tabellen, Dienste, LCN, NIT, Bitrate, PCR und Decodierbarkeit (tsanalyze/ffprobe) – ohne HF.'), pal: T('Rendert das PAL-Signal und demoduliert es: Bild-/Tonträger, Hüllkurvenpegel, Ton-Hub und ein Standbild als PNG – ohne HF.') }[STD.standardOf(S.draft)]),
        h('div.inrow', null, h('label.inline', null, T('Dauer') + ' ', num('seconds', 'check-seconds', 5, 120), ' s'), h('span.grow'), btn('check', 'check-run', T('Prüfen'), 'scan')),
      ]),
      card(T('Kontrollempfang'), 'antenna', [
        h('p.hint', null, { dab: T('Mitschnitt am Pluto-RX auf der Sendefrequenz (Luft/Kabel), anschließend Dekodierung. Liefert SNR und Fehlerzähler.'), fm: T('Mitschnitt am Pluto-RX, FM-Demodulation: Hub, Pilot, RDS (redsea) und Audio aus der Luft bzw. vom Kabel.'), dvbt: T('Mitschnitt am Pluto-RX (Einzelpuffer), gr-dtv-Demodulation: Lock, MER, BER, Transportstromanalyse.'), dvbc: T('Mitschnitt am Pluto-RX, QAM-Demodulation: Lock, MER, BER, Transportstromanalyse.'), pal: T('Mitschnitt am Pluto-RX, AM-/FM-Demodulation: Träger, Pegel, Bild (PNG) aus der Luft bzw. vom Kabel.') }[STD.standardOf(S.draft)]),
        h('div.inrow.wrap', null, h('label.inline', null, T('Dauer') + ' ', num('mon_seconds', 'monitor-seconds', 2, 60), ' s'), h('label.inline', null, 'RX-Gain ', num('rx_gain_db', 'monitor-gain', 0, 70), ' dB'), h('span.grow'), btn('monitor', 'monitor-run', T('Empfangen'), 'antenna')),
      ])),
    dirty ? h('p.alert', null, icon('info'), T('Es gibt nicht übernommene Änderungen. Geprüft wird die gespeicherte Konfiguration; der Soll/Ist-Vergleich nutzt die gespeicherte (Server-)Konfiguration.')) : null,
    h('div.subtabs', { role: 'tablist' },
      [['check', T('Offline-Prüfung')], ['monitor', T('Kontrollempfang')]].map(([k, l]) => h('button', { type: 'button', role: 'tab', tid: `report-tab-${k}`, class: S.reportTab === k ? 'on' : null, onclick: () => { S.reportTab = k; S.render(); } }, l,
        S.state[k] ? h('span.tabdot.' + (S.state[k].ok && S.state[k].sync ? 'ok' : 'bad')) : null))),
    S.busy[S.reportTab] ? h('div.empty', null, h('span.spinner.big'), h('p', null, T('Bericht wird erstellt …'))) : renderReport(rep, S.server || S.draft, S.reportTab));
}

// ============================================================ Presets & Testpläne
export function pagePresets() {
  const presetList = h('div.presetlist', { tid: 'preset-list' });
  const loadPresets = async () => {
    try {
      const r = await api('GET', '/api/presets');
      const arr = Array.isArray(r) ? r : (r?.presets || []);
      S.presets = arr.map(x => typeof x === 'string' ? { name: x.replace(/\.json$/, '') } : { ...x, name: (x.name || '').replace(/\.json$/, '') });
    } catch (e) { S.presets = []; toast(T('Presets nicht ladbar: {msg}', { msg: e.message }), 'error'); }
    renderList();
  };
  const loadPreset = async name => {
    try {
      const cfg = await api('GET', `/api/presets/${encodeURIComponent(name)}`);
      const c = STD.normalizeConfig(cfg?.config || cfg);
      markRfIntent(c);           // Dämpfung/Senden bleiben; Kanal/Frequenz/Versatz werden beim Übernehmen live gesetzt
      S.draft = c; S.sel = 0;
      edited('name', true);
      toast(T('Preset „{name}“ geladen – zum Aktivieren übernehmen', { name }), 'ok', 4000);
    } catch (e) { toast(T('Laden fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
  };
  const renderList = () => {
    sel.replaceChildren(h('option', { value: '' }, S.presets.length ? T('Preset wählen …') : T('keine Presets')), ...S.presets.map(p => h('option', { value: p.name }, p.name)));
    presetList.replaceChildren(...(S.presets.length ? S.presets.map(p => h('div.presetrow', { tid: `preset-row-${p.name}` },
      icon('file'), h('div.grow', null, h('b', null, p.name), p.modified || p.mtime ? h('small.muted', null, ' · ', stamp(new Date((p.modified || p.mtime) * ((p.modified || p.mtime) < 1e12 ? 1000 : 1)))) : null),
      h('button.btn.sm', { type: 'button', tid: `preset-load-${p.name}`, onclick: () => loadPreset(p.name) }, T('Laden')),
      h('button.icon-btn', { type: 'button', title: T('Löschen'), tid: `preset-del-${p.name}`, onclick: async () => {
        if (!(await dialog({ title: T('Preset löschen?'), body: h('p', null, T('„{name}“ wird endgültig gelöscht.', { name: p.name })), ok: T('Löschen'), danger: true, tid: 'confirm-preset-del' }))) return;
        try { await api('DELETE', `/api/presets/${encodeURIComponent(p.name)}`); toast(T('Preset gelöscht'), 'ok'); loadPresets(); } catch (e) { toast(e.message, 'error'); }
      } }, icon('trash')))) : [h('p.muted.small', null, T('Noch keine Presets gespeichert.'))]));
  };
  const sel = h('select', { tid: 'preset-select' });
  const nameIn = input('name', { tid: 'preset-name', ph: T('Name des Presets') });
  const saveBtn = h('button.btn.primary', { type: 'button', tid: 'preset-save', onclick: async () => {
    const name = (S.draft.name || '').trim();
    if (!/^[\w\-äöüÄÖÜß .]{1,60}$/.test(name)) return toast(T('Name: 1–60 Zeichen (Buchstaben, Ziffern, Leerzeichen, - _ .)'), 'error');
    if (S.presets.some(p => p.name === name) && !(await dialog({ title: T('Überschreiben?'), body: h('p', null, T('Preset „{name}“ existiert bereits.', { name })), ok: T('Überschreiben'), tid: 'confirm-overwrite' }))) return;
    try { await api('PUT', `/api/presets/${encodeURIComponent(name)}`, S.draft); toast(T('Preset „{name}“ gespeichert', { name }), 'ok'); loadPresets(); } catch (e) { toast(T('Speichern fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
  } }, icon('save'), T('Speichern'));
  const imp = h('input', { type: 'file', accept: '.json,application/json', hidden: true, tid: 'preset-import-input' });
  imp.addEventListener('change', async () => {
    const f = imp.files[0]; if (!f) return;
    try { const c = STD.normalizeConfig(JSON.parse(await f.text())); markRfIntent(c); S.draft = c; edited('name', true); toast(T('Konfiguration importiert'), 'ok'); }
    catch (e) { toast(T('Import fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
    imp.value = '';
  });
  setTimeout(loadPresets);

  return h('div.page-grid.presets', null,
    h('div.col', null,
      card(T('Presets'), 'folder', [
        h('div.inrow', null, sel, h('button.btn', { type: 'button', tid: 'preset-load', onclick: () => sel.value ? loadPreset(sel.value) : toast(T('Preset wählen'), 'error') }, T('Laden'))),
        presetList,
        h('hr'),
        field(T('Aktuelle Konfiguration speichern als'), h('div.inrow', null, nameIn, saveBtn), { hint: T('Dämpfung/Sendezustand werden beim Laden nicht übernommen (Sicherheit).') }),
        h('div.inrow.wrap', null,
          h('button.btn.ghost', { type: 'button', tid: 'config-export', onclick: () => download(`${S.draft.name || 'config'}.json`, JSON.stringify(S.draft, null, 2)) }, icon('download'), T('Export JSON')),
          imp, h('button.btn.ghost', { type: 'button', tid: 'config-import', onclick: () => imp.click() }, icon('upload'), T('Import JSON'))),
      ], { tid: 'card-presets' }),
      protocolCard()),
    h('div.col', null, plansCard()));
}

function planState() { return load('plan', { id: null, results: {}, notes: '' }); }

function plansCard() {
  const ps = planState();
  const plan = TESTPLANS.find(p => p.id === ps.id);
  if (!plan) {
    const std = STD.standardOf(S.draft);
    return card(T('Testpläne · {std}', { std: STD.stdName(std) }), 'checklist', h('div.plangrid', { tid: 'plan-list' }, TESTPLANS.filter(p => (p.standard || 'dab') === std || p.standard === 'all').map(p => h('button.plancard', { type: 'button', tid: `plan-${p.id}`,
      onclick: () => { save('plan', { id: p.id, results: {}, notes: ps.notes || '' }); S.render(); } },
      h('span.plan-ic', null, icon(p.icon)), h('b', null, p.title), h('small', null, p.desc), h('span.plan-n', null, T(p.steps.length > 1 ? '{n} Schritte' : '{n} Schritt', { n: p.steps.length }))))), { tid: 'card-plans' });
  }
  const done = plan.steps.filter((_, i) => ps.results[i]?.result).length;
  return card(T('Testplan: {title}', { title: plan.title }), plan.icon, [
    h('p.muted', null, plan.desc),
    h('div.progress.thin', null, h('i', { style: { width: `${done / plan.steps.length * 100}%` } })),
    h('ol.steps', { tid: 'plan-steps' }, plan.steps.map((st, i) => {
      const r = ps.results[i] || {};
      const cfg = st.config(structuredClone(S.server || S.draft));
      const setRes = res => { const s = planState(); s.results[i] = { ...(s.results[i] || {}), result: res, title: st.title, time: Date.now() }; save('plan', s); S.render(); };
      const note = h('input', { type: 'text', placeholder: T('Bemerkung / Messwert'), tid: `step-note-${i}` }); note.value = r.note || '';
      note.addEventListener('change', () => { const s = planState(); s.results[i] = { ...(s.results[i] || {}), note: note.value, title: st.title }; save('plan', s); });
      return h('li.step' + (r.result ? '.' + r.result : ''), { tid: `step-${i}` },
        h('div.step-h', null, h('span.step-n', null, i + 1), h('b', null, st.title), h('span.grow'), h('small.mono.muted', null, planCheck(cfg))),
        h('p', null, st.instruction), h('p.expect', null, icon('target'), h('span', null, h('b', null, T('Erwartung:') + ' '), st.expect)),
        h('div.inrow.wrap', null,
          h('button.btn.sm', { type: 'button', tid: `step-load-${i}`, onclick: () => {
            const c = st.config(structuredClone(S.server || S.draft));
            markRfIntent(c);
            c.name = `${plan.title} · ${st.title}`.slice(0, 60);
            S.draft = STD.normalizeConfig(c); S.sel = 0; edited('services', true);
            toast(T('Schritt geladen – „Übernehmen“ aktiviert ihn'), 'ok', 3500);
          } }, icon('download'), T('Konfiguration laden')),
          st.action?.type === 'sweep' ? h('button.btn.sm', { type: 'button', onclick: () => { save('sweepParams', st.action.params); location.hash = '#/hf'; } }, icon('ramp'), T('Rampe vorbereiten')) : null,
          h('span.grow'),
          h('div.seg.sm', null,
            h('button', { type: 'button', class: r.result === 'pass' ? 'on ok' : null, tid: `step-pass-${i}`, onclick: () => setRes('pass') }, icon('check'), 'OK'),
            h('button', { type: 'button', class: r.result === 'fail' ? 'on bad' : null, tid: `step-fail-${i}`, onclick: () => setRes('fail') }, icon('x'), T('Fehler')),
            h('button', { type: 'button', class: r.result === 'skip' ? 'on' : null, onclick: () => setRes('skip') }, '–'))),
        note);
    })),
  ], { tid: 'card-plan', actions: h('button.btn.ghost.sm', { type: 'button', tid: 'plan-close', onclick: () => { const s = planState(); s.id = null; save('plan', s); S.render(); } }, icon('left'), T('Alle Pläne')) });
}

function protocolCard() {
  const ps = planState();
  const notes = h('textarea', { rows: 3, tid: 'protocol-notes', placeholder: T('Prüfling, Seriennummer, Firmware, Messaufbau (Dämpfungsglieder, Kabel) …') });
  notes.value = ps.notes || '';
  notes.addEventListener('change', () => { const s = planState(); s.notes = notes.value; save('plan', s); });
  const build = () => {
    const s = planState();
    const plan = TESTPLANS.find(p => p.id === s.id);
    const data = protocolData({ cfg: S.server || S.draft, state: S.state, plan, results: plan ? plan.steps.map((st, i) => ({ title: st.title, ...(s.results[i] || {}) })) : [], notes: s.notes });
    data.sensitivity_marks = load('marks', []);
    return data;
  };
  return card(T('Messprotokoll'), 'print', [
    h('p.hint', null, T('Enthält Zeitstempel, gespeicherte Konfiguration, Kapazität, Testplan-Ergebnisse, letzte Prüf- und Empfangsberichte mit Soll/Ist-Vergleich.')),
    field(T('Bemerkungen'), notes, { wide: true }),
    h('div.inrow.wrap', null,
      h('button.btn', { type: 'button', tid: 'protocol-json', onclick: () => download(`messprotokoll-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`, JSON.stringify(build(), null, 2)) }, icon('download'), 'JSON'),
      h('button.btn.primary', { type: 'button', tid: 'protocol-html', onclick: async () => {
        const css = await fetch('protocol.css').then(r => r.text()).catch(() => '');
        const sprite = document.getElementById('sprite')?.outerHTML || '';
        const html = protocolHTML(build(), css, sprite);
        const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
        const w = window.open(url, '_blank');
        if (!w) download('messprotokoll.html', html, 'text/html');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      } }, icon('print'), T('Druckansicht (HTML)'))),
  ], { tid: 'card-protocol' });
}

// ============================================================ Log
export function pageLog() {
  const f = load('logFilter', { info: true, warn: true, error: true, q: '', follow: true });
  const listEl = h('div.loglist', { tid: 'log-list' });
  const q = h('input', { type: 'search', placeholder: T('Filter (Text) …'), tid: 'log-filter' }); q.value = f.q;
  const lv = l => { const cb = h('input', { type: 'checkbox', tid: `log-lvl-${l}` }); cb.checked = f[l]; cb.addEventListener('change', () => { f[l] = cb.checked; save('logFilter', f); draw(); }); return h('label.chip.lvl-' + l, null, cb, h('span', null, { info: 'Info', warn: T('Warnung'), error: T('Fehler') }[l])); };
  const follow = h('input', { type: 'checkbox', tid: 'log-follow' }); follow.checked = f.follow;
  follow.addEventListener('change', () => { f.follow = follow.checked; save('logFilter', f); });
  q.addEventListener('input', debounce(() => { f.q = q.value; save('logFilter', f); draw(); }, 150));
  let drawn = -1;
  const draw = () => {
    const needle = f.q.toLowerCase();
    const rows = S.log.filter(l => (f[l.lvl] ?? true) && (!needle || `${l.msg} ${l.src}`.toLowerCase().includes(needle))).slice(-800);
    listEl.replaceChildren(...rows.map(l => h('div.logline.' + l.lvl, null,
      h('span.lt.mono', null, l.t ? new Date(l.t * (l.t < 1e12 ? 1000 : 1)).toLocaleTimeString(LOCALE) : ''),
      h('span.ll', null, l.lvl), l.src ? h('span.ls', null, l.src) : null, h('span.lm', null, l.msg))));
    if (!rows.length) listEl.append(h('p.muted.small.pad', null, S.log.length ? T('Keine Zeilen passen zum Filter.') : T('Noch keine Logzeilen.')));
    if (f.follow) listEl.scrollTop = listEl.scrollHeight;
    count.textContent = `${rows.length} / ${S.log.length}`;
    drawn = S.log.length;
  };
  const count = h('small.muted');
  S.watchers.push(() => { if (S.log.length !== drawn) draw(); });
  S.logDraw = () => { if (S.log.length !== drawn) draw(); };
  setTimeout(draw);
  return h('div.page-stack.logpage', null,
    h('div.toolbar', null, q, lv('info'), lv('warn'), lv('error'),
      h('label.chip', null, follow, h('span', null, T('Mitlaufen'))), h('span.grow'), count,
      h('button.btn.ghost.sm', { type: 'button', tid: 'log-clear', onclick: () => { S.log.length = 0; draw(); } }, icon('trash'), T('Leeren')),
      h('button.btn.ghost.sm', { type: 'button', tid: 'log-export', onclick: () => download('dablab-log.txt', S.log.map(l => `${l.t ?? ''}\t${l.lvl}\t${l.src}\t${l.msg}`).join('\n'), 'text/plain') }, icon('download'), T('Export'))),
    listEl);
}
