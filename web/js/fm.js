// UKW: Bereiche „Sender“ (MPX, Audio, zweiter Träger) und „RDS“.

import { h, icon, toast } from './ui.js';
import * as D from './dab.js';
import * as STD from './standards.js';
import { S, field, input, textarea, select, toggle, segmented, card, counter, edited, sendLive, getPath, setPath, txRunning } from './core.js';
import { audioSourceCard } from './pages.js';
import { T } from './i18n.js';

// ---------------------------------------------------------------- Sender
export function pageFM() {
  const f = S.draft.fm;
  const audioEl = h('b.mono', { tid: 'fm-audio-dev' });
  const upd = () => { const a = STD.fmAudioDeviation(f); audioEl.textContent = `${D.fmtNum(a, 2)} kHz`; audioEl.classList.toggle('bad', a < 5); audioEl.classList.toggle('accent', a >= 5); };
  S.watchers.push(upd); upd();
  const mpxIn = input('fm.mpx_power_dbr', { type: 'number', min: -12, max: 12, step: 0.5, tid: 'fm-mpx-power', mono: true, ph: T('aus'), parse: v => v === '' ? null : Number(v), fmt: v => v == null ? '' : v });
  const mpx = card(T('Multiplex (MPX)'), 'wave', [
    h('div.bigread', null,
      h('div', null, h('small', null, T('Gesamthub (Spitze)')), h('b.mono', null, D.fmtNum(f.deviation_khz, 1), h('em', null, ' kHz'))),
      h('div', null, h('small', null, T('Audiohub = Gesamt − Pilot − RDS')), h('span.big', null, audioEl))),
    h('div.grid3', null,
      field(T('Gesamthub (kHz)'), input('fm.deviation_khz', { type: 'number', min: 5, max: 150, step: 0.5, tid: 'fm-deviation', mono: true }), { path: 'fm.deviation_khz', hint: T('Nennwert ±75 kHz; 120 % = 90 für Übermodulation · live') }),
      field(T('Pilothub (kHz)'), input('fm.pilot_khz', { type: 'number', min: 0, max: 10, step: 0.25, tid: 'fm-pilot', mono: true }), { path: 'fm.pilot_khz', hint: T('19 kHz; Nennwert 6,75 (9 %), 0 = kein Pilot · live') }),
      field(T('MPX-Leistung (dBr)'), mpxIn, { path: 'fm.mpx_power_dbr', hint: T('leer = keine Begrenzung; 0 dBr nach BS.412 · live') })),
    h('div.grid3', null,
      field('Preemphasis', segmented('fm.preemphasis_us', [{ v: 50, label: '50 µs' }, { v: 75, label: '75 µs' }, { v: 0, label: T('aus') }], { tid: 'fm-preemph' }), { path: 'fm.preemphasis_us', hint: T('Europa 50 µs, USA 75 µs') }),
      field(T('Betriebsart'), segmented('fm.stereo', [{ v: true, label: 'Stereo' }, { v: false, label: 'Mono' }], { tid: 'fm-stereo' }), { hint: T('Mono = kein Pilot, kein 38-kHz-Anteil') }),
      field(T('Audio-Gain (dB)'), input('fm.audio_gain_db', { type: 'number', min: -40, max: 20, step: 0.5, tid: 'fm-gain', mono: true }), { path: 'fm.audio_gain_db', hint: T('0 dBFS nach Preemphasis = 100 % Audiohub; 40 %-Testton: −8,3 dBFS · live') })),
    h('details.sub', { open: !f.limiter.enable }, h('summary', null, f.limiter.enable ? T('Spitzenbegrenzer (an)') : T('Spitzenbegrenzer (aus)')),
      h('div.grid4', null,
        field('', toggle('fm.limiter.enable', T('Limiter nach Preemphasis'), { tid: 'fm-limiter', rerender: true })),
        field(T('Schwelle (1,0 = 100 %)'), input('fm.limiter.threshold', { type: 'number', min: 0.1, max: 1.5, step: 0.05, tid: 'fm-limiter-threshold', mono: true }), { path: 'fm.limiter.threshold' }),
        field(T('Release (dB/s)'), input('fm.limiter.release_db_s', { type: 'number', min: 0.1, max: 60, step: 0.5, tid: 'fm-limiter-release', mono: true })),
        field('', toggle('fm.limiter.clip', T('hart clippen'), { tid: 'fm-limiter-clip' })))),
    h('p.hint', null, icon('info'), T('Hub, Pilot, MPX-Leistung und Gain sind live (ctl „fm“); Preemphasis, Stereo und Limiter brauchen „Übernehmen (Neustart)“.')),
  ], { tid: 'card-fm-mpx', badge: h('span.pill.live', null, T('Hub/Pilot/Gain live')) });

  const audio = audioSourceCard(f.audio.source, 'fm.audio.source', { key: 'fm', title: T('Audioquelle (48 kHz Stereo)'), tid: 'card-fm-source' });

  const sc = f.second_carrier;
  const second = card(T('Zweiter Träger (Nachbarkanal-/Fangverhältnis-Test)'), 'antenna', [
    h('div.inrow.wrap', null, toggle('fm.second_carrier.enable', T('zweiten Träger senden'), { tid: 'fm-second-enable', rerender: true }), h('span.grow'),
      sc.enable ? h('span.mono.muted', null, T('Störer bei {mhz} MHz', { mhz: D.fmtMHz(S.draft.rf.freq_hz + sc.offset_khz * 1e3) })) : null),
    sc.enable ? [
      h('div.grid3', null,
        field(T('Versatz (kHz)'), select('fm.second_carrier.offset_khz', [-400, -300, -200, -100, 0, 100, 200, 300, 400].map(v => ({ v, label: v === 0 ? T('0 (Gleichkanal, Fangverhältnis)') : `${v > 0 ? '+' : ''}${v}` })), { num: true, tid: 'fm-second-offset', rerender: false }), { path: 'fm.second_carrier.offset_khz', hint: T('IEC 60315-4 Zweisignal: ±100/±200/±300 kHz') }),
        field(T('Pegel gegen Nutzträger (dB)'), input('fm.second_carrier.level_db', { type: 'number', min: -60, max: 0, step: 1, tid: 'fm-second-level', mono: true }), { path: 'fm.second_carrier.level_db', hint: T('live; a1 + a2 = Vollaussteuerung − Backoff') }),
        field(T('Hub Störer (kHz)'), input('fm.second_carrier.deviation_khz', { type: 'number', min: 5, max: 150, step: 0.5, tid: 'fm-second-dev', mono: true }))),
      h('div.grid3', null,
        field(T('Betriebsart'), segmented('fm.second_carrier.stereo', [{ v: true, label: 'Stereo' }, { v: false, label: 'Mono' }], { tid: 'fm-second-stereo' })),
        field('Preemphasis', segmented('fm.second_carrier.preemphasis_us', [{ v: 50, label: '50 µs' }, { v: 75, label: '75 µs' }, { v: 0, label: T('aus') }], { tid: 'fm-second-preemph' })),
        field(T('Pilothub (kHz)'), input('fm.second_carrier.pilot_khz', { type: 'number', min: 0, max: 10, step: 0.25, tid: 'fm-second-pilot', mono: true }))),
      audioSourceCard(sc.audio.source, 'fm.second_carrier.audio.source', { title: T('Quelle des zweiten Trägers'), tid: 'card-fm-second-source', prefix: 'src2', kinds: ['tone', 'noise', 'pinknoise', 'file', 'silence'] }),
      h('div.grid3', null,
        field(T('RDS auf Störer'), toggle('fm.second_carrier.rds.enable', T('eigenes RDS'), { tid: 'fm-second-rds' })),
        field('PI (hex)', input('fm.second_carrier.rds.pi', { tid: 'fm-second-pi', mono: true, hex: 4 }), { path: 'fm.second_carrier.rds.pi' }),
        field('PS', input('fm.second_carrier.rds.ps', { tid: 'fm-second-ps', maxlength: 8 }), { hint: T('für AF-/EON-Umschalttests (14B eon_ta)') })),
    ] : h('p.muted.small', null, T('Aus. Für Nachbarkanalselektion, Fangverhältnis und AF-/EON-Umschaltung einschalten – beide Träger liegen im selben 4,096-MS/s-Strom.')),
  ], { tid: 'card-fm-second' });

  return h('div.page-grid', null, h('div.col', null, mpx, second), h('div.col', null, audio));
}

// ---------------------------------------------------------------- RDS
const AF_PRESETS = [87.6, 89.0, 91.4, 93.3, 95.0, 97.5, 99.9, 101.3, 103.7, 105.5, 107.1];

export function pageRDS() {
  const r = S.draft.fm.rds;
  const liveBtn = (tid, label, mkPayload) => h('button.btn.primary.sm', { type: 'button', tid, onclick: async () => {
    if (!txRunning()) return toast(T('Kette läuft nicht – Wert wird beim Start gesendet.'), 'info');
    const payload = mkPayload();
    if (await sendLive({ rds: payload }, T('{label} gesendet', { label }))) { if (S.server) Object.assign(S.server.fm.rds, structuredClone(payload)); edited('fm.rds'); }
  } }, icon('send'), label);

  const ctry = h('span.hint-inline');
  const updC = () => { const c = STD.rdsCountry(r.pi, r.ecc); ctry.textContent = c.length ? T('Länderkennung passt: {names}', { names: c.map(x => x.name).join(' / ') }) : T('Länderkennung (1. Ziffer) passt nicht zur ECC'); ctry.classList.toggle('warn', !c.length); };
  S.watchers.push(updC); updC();

  const ident = card(T('Kennung (Gruppe 1A)'), 'id', [
    h('div.grid3', null,
      field('PI (hex)', input('fm.rds.pi', { tid: 'rds-pi', mono: true, hex: 4, ph: '0xD3C4' }), { path: 'fm.rds.pi', hint: T('1. Ziffer Land (D/1 = DE), 2. Ziffer Bereich, 3.–4. Programm') }),
      field('ECC (hex)', input('fm.rds.ecc', { tid: 'rds-ecc', mono: true, hex: 2, ph: '0xE0' }), { path: 'fm.rds.ecc' }),
      field(T('RDS senden'), toggle('fm.rds.enable', T('RDS aktiv'), { tid: 'rds-enable', rerender: true }))),
    h('p.ctry', null, icon('info'), ctry),
    h('div.grid3', null,
      field(T('PTY (RDS Europa)'), select('fm.rds.pty', STD.RDS_PTY.map((n, i) => ({ v: i, label: `${i} · ${n}` })), { num: true, tid: 'rds-pty', rerender: false }), { path: 'fm.rds.pty', hint: 'live' }),
      field('PTYN (8)', input('fm.rds.ptyn', { tid: 'rds-ptyn', maxlength: 8 }), { path: 'fm.rds.ptyn', extra: counter(() => [...(r.ptyn || '')].length, 8), hint: T('Gruppe 10A · live') }),
      field(T('PIN (TT:HH:MM)'), input('fm.rds.pin', { tid: 'rds-pin', mono: true, ph: T('leer = aus'), parse: v => v.trim() || null }), { path: 'fm.rds.pin', hint: T('Programmkennung Gruppe 1A') })),
    h('div.grid3', null,
      field(T('RDS-Hub (kHz)'), input('fm.rds.level_khz', { type: 'number', min: 0, max: 7.5, step: 0.1, tid: 'rds-level', mono: true }), { path: 'fm.rds.level_khz', hint: T('Nennwert 2,0; zulässig 1,0–7,5 · live') }),
      field(T('Phase zum Pilot'), select('fm.rds.phase_deg', [{ v: 0, label: '0°' }, { v: 90, label: '90°' }], { num: true, tid: 'rds-phase', rerender: false }), { path: 'fm.rds.phase_deg', hint: T('phasenstarr zum 3. Pilot-Oberton') }),
      field(T('Gruppenversion'), h('div.inrow', null, h('label.inline', null, '0A/0B ', select('fm.rds.group_version', [{ v: 'A', label: 'A' }, { v: 'B', label: 'B' }], { tid: 'rds-group-version', rerender: false })),
        h('label.inline', null, 'RT 2A/2B ', select('fm.rds.rt_version', [{ v: 'A', label: 'A' }, { v: 'B', label: 'B' }], { tid: 'rds-rt-version', rerender: false }))))),
    h('details.sub', null, h('summary', null, T('Gruppenraten (Sondergruppe alle n Gruppen)')),
      h('div.grid3', null, Object.keys(r.rates).map(g => field(g.toUpperCase(), input(`fm.rds.rates.${g}`, { type: 'number', min: 1, max: 1000, tid: `rds-rate-${g}`, mono: true }))))),
  ], { tid: 'card-rds-ident' });

  const flags = card(T('Flags & Uhrzeit'), 'clock', [
    h('div.chipgrid', null,
      ...[['tp', T('TP (Verkehrsprogramm)')], ['ta', T('TA (Durchsage läuft)')], ['ms', T('MS = Musik')], ['ct', T('CT (Gruppe 4A, 1×/min)')]].map(([k, l]) => {
        const cb = h('input', { type: 'checkbox', tid: `rds-${k}` }); cb.checked = !!r[k];
        cb.addEventListener('change', () => { r[k] = cb.checked; edited(`fm.rds.${k}`, k === 'ta' || k === 'tp'); });
        return h('label.chip' + (k === 'ta' ? '.red' : ''), null, cb, h('span', null, l));
      }),
      ...[['stereo', 'DI Stereo'], ['artificial_head', T('DI Kunstkopf')], ['compressed', T('DI komprimiert')], ['dynamic_pty', T('DI dynam. PTY')]].map(([k, l]) => {
        const cb = h('input', { type: 'checkbox', tid: `rds-di-${k.replace('_', '-')}` }); cb.checked = !!r.di[k];
        cb.addEventListener('change', () => { r.di[k] = cb.checked; edited(`fm.rds.di.${k}`); });
        return h('label.chip', null, cb, h('span', null, l));
      })),
    h('div.inrow.wrap', null,
      h('button.btn' + (r.ta ? '.danger' : '.accent'), { type: 'button', tid: 'rds-ta-live', disabled: !r.tp, onclick: async () => {
        const ta = !(S.server?.fm.rds.ta ?? r.ta);
        if (await sendLive({ rds: { ta } }, ta ? T('TA ein – Durchsage aktiv') : T('TA aus'))) { r.ta = ta; if (S.server) S.server.fm.rds.ta = ta; edited('fm.rds.ta', true); }
      } }, icon('bell'), r.ta ? T('Durchsage beenden (TA aus)') : T('Durchsage starten (TA ein)')),
      h('span.muted.small', null, r.tp ? T('wirkt sofort über die laufende Kette') : T('TA erfordert TP'))),
    h('div.grid2', null,
      field(T('UTC-Versatz (½ h)'), select('fm.rds.utc_offset_halfhours', [{ v: 'auto', label: T('automatisch (Systemzeit)') }, ...Array.from({ length: 49 }, (_, i) => ({ v: i - 24, label: D.ltoLabel(i - 24) }))], { tid: 'rds-utc', parse: v => v === 'auto' ? 'auto' : Number(v), rerender: false }), { hint: T('Gruppe 4A') }),
      field(T('CT-Latenzausgleich (s)'), input('fm.rds.ct_latency_s', { type: 'number', min: -5, max: 5, step: 0.1, tid: 'rds-ct-latency', mono: true }), { hint: T('Minutenkante ±0,1 s nach Gruppenende') })),
  ], { tid: 'card-rds-flags' });

  const rtp = (type, label, tid) => {
    const el = h('input', { type: 'text', tid, maxLength: 64, disabled: r.rt_from_tags && ['ITEM.ARTIST', 'ITEM.TITLE'].includes(type) });
    el.value = STD.rtplusGet(r, type);
    el.addEventListener('input', () => { STD.rtplusSet(r, type, el.value); edited('fm.rds.rtplus'); });
    return field(label, el);
  };
  const ps = card(T('Programmname (PS) & Radiotext (RT)'), 'text', [
    field(T('PS (8 Zeichen)'), h('div.inrow', null, input('fm.rds.ps', { tid: 'rds-ps', maxlength: 8 }), liveBtn('rds-ps-send', T('PS senden'), () => ({ ps: r.ps }))),
      { path: 'fm.rds.ps', extra: counter(() => [...(r.ps || '')].length, 8), hint: T('Gruppe 0A/0B, 4 Segmente; Zeichensatz Annex E (Umlaute erlaubt)') }),
    field(T('Radiotext (64 Zeichen)'), h('div.inrow', null, textarea('fm.rds.rt', { tid: 'rds-rt', rows: 2 }), liveBtn('rds-rt-send', T('RT senden'), () => ({ rt: r.rt }))),
      { path: 'fm.rds.rt', wide: true, extra: counter(() => [...(r.rt || '')].length, 64), hint: T('Gruppe 2A (16 Segmente) bzw. 2B (32 Zeichen); A/B-Flag kippt beim Textwechsel') }),
    h('div.inrow.wrap', null, toggle('fm.rds.rt_from_tags', T('RT und RT+ (Titel/Interpret) aus der Playlist'), { tid: 'rtplus-auto', rerender: true }), h('span.muted.small', null, T('bei Quelle „Playlist“: bei Titelwechsel · live (rt_auto)'))),
    h('details.sub', { open: (r.rtplus || []).length > 0 }, h('summary', null, T('RT+ (Gruppe 3A/11A, AID 0x4BD7)')),
      h('div.grid3', null,
        rtp('ITEM.ARTIST', 'ITEM.ARTIST', 'rtplus-artist'), rtp('ITEM.TITLE', 'ITEM.TITLE', 'rtplus-title'), rtp('ITEM.ALBUM', 'ITEM.ALBUM', 'rtplus-album'),
        rtp('STATIONNAME.LONG', 'STATIONNAME.LONG', 'rtplus-station'), rtp('PROGRAMME.NOW', 'PROGRAMME.NOW', 'rtplus-program'), rtp('PROGRAMME.HOMEPAGE', 'PROGRAMME.HOMEPAGE', 'rtplus-url')),
      h('div.inrow.wrap', null, liveBtn('rtplus-send', T('RT+ senden'), () => ({ rtplus: r.rtplus })), h('span.muted.small', null, T('{n} Tag(s) · maximal 2 je 11A-Gruppe, Rest rotiert', { n: (r.rtplus || []).length })))),
    field(T('LPS – langer Programmname (UTF-8, 32 Byte, Gruppe 15A)'), input('fm.rds.lps', { tid: 'rds-lps' }), { path: 'fm.rds.lps', extra: counter(() => new TextEncoder().encode(r.lps || '').length, 32, ' B'), hint: T('nur neuere Empfänger (RDS2-Ära) · live') }),
  ], { tid: 'card-rds-text', badge: h('span.pill.live', null, 'PS/RT/RT+/TA live') });

  // AF-Liste
  const afChips = h('div.chipgrid', { tid: 'rds-af-list' });
  const afIn = h('input.mono', { type: 'number', step: 0.1, min: STD.FM_MIN_MHZ, max: STD.FM_MAX_MHZ, tid: 'rds-af-input', placeholder: 'MHz' });
  const drawAf = () => afChips.replaceChildren(...(r.af.length ? r.af.map((a, i) => h('span.chip.on', null, h('span.mono', null, a.toFixed(1)), h('button.chipx', { type: 'button', title: T('Entfernen'), tid: `rds-af-del-${i}`, onclick: () => { r.af.splice(i, 1); edited('fm.rds.af'); drawAf(); } }, icon('x')))) : [h('span.muted.small', null, T('Keine AF – Empfänger sendet „No AF“ (0xE0).'))]));
  const addAf = v => { v = Math.round(Number(v) * 10) / 10; if (!(v >= STD.FM_MIN_MHZ && v <= STD.FM_MAX_MHZ)) return toast(T('AF: 87,5–108,0 MHz'), 'error'); if (r.af.length >= 25) return toast(T('maximal 25 AF'), 'error'); if (!r.af.includes(v)) r.af.push(v); r.af.sort((a, b) => a - b); edited('fm.rds.af'); drawAf(); afIn.value = ''; };
  afIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addAf(afIn.value); } });
  drawAf();
  const af = card(T('Alternativfrequenzen (AF, Gruppe 0A)'), 'link', [
    h('div.inrow.wrap', null, field(T('Methode'), segmented('fm.rds.af_method', [{ v: 'A', label: T('A (Liste)') }, { v: 'B', label: T('B (Paare)') }], { tid: 'rds-af-method', rerender: true })),
      r.af_method === 'B' ? field(T('Regionale AF (Methode B)'), input('fm.rds.af_regional', { tid: 'rds-af-regional', mono: true, ph: '89,0; 91,4', fmt: v => (v || []).map(x => String(x).replace('.', ',')).join('; '), parse: v => v.split(/[;\s]+/).map(x => Number(x.replace(',', '.'))).filter(x => x > 0) }), { hint: T('Paare mit der eigenen Frequenz') }) : null),
    afChips,
    h('div.inrow.wrap', null, afIn, h('button.btn.sm', { type: 'button', tid: 'rds-af-add', onclick: () => addAf(afIn.value) }, icon('plus'), T('Hinzufügen')),
      h('button.btn.sm.ghost', { type: 'button', tid: 'rds-af-self', onclick: () => addAf(S.draft.rf.freq_hz / 1e6) }, T('eigene Frequenz')),
      S.draft.fm.second_carrier.enable ? h('button.btn.sm.ghost', { type: 'button', onclick: () => addAf((S.draft.rf.freq_hz + S.draft.fm.second_carrier.offset_khz * 1e3) / 1e6) }, T('zweiter Träger')) : null,
      h('span.grow'), liveBtn('rds-af-send', T('AF senden'), () => ({ af: r.af, af_method: r.af_method }))),
    h('div.chgrid', null, AF_PRESETS.map(m => h('button', { type: 'button', class: r.af.includes(m) ? 'on' : null, onclick: () => addAf(m) }, m.toFixed(1)))),
  ], { tid: 'card-rds-af' });

  // EON
  const eon = card(T('EON – verknüpfte Programme (Gruppe 14A/14B)'), 'layers', [
    r.eon.length ? h('div.tblwrap', null, h('table.tbl', { tid: 'rds-eon-table' },
      h('thead', null, h('tr', null, h('th', null, 'PI'), h('th', null, 'PS'), h('th', null, 'PTY'), h('th', null, 'AF (MHz)'), h('th', null, 'TP'), h('th', null, 'TA'), h('th', null, 'Linkage'), h('th', null, ''))),
      h('tbody', null, r.eon.map((e, i) => h('tr', null,
        h('td', null, input(`fm.rds.eon.${i}.pi`, { mono: true, hex: 4, tid: `eon-pi-${i}` })),
        h('td', null, input(`fm.rds.eon.${i}.ps`, { maxlength: 8, tid: `eon-ps-${i}` })),
        h('td', null, select(`fm.rds.eon.${i}.pty`, STD.RDS_PTY.map((n, k) => ({ v: k, label: `${k} · ${n}` })), { num: true, rerender: false })),
        h('td', null, input(`fm.rds.eon.${i}.af`, { mono: true, ph: '93,3; 95,0', fmt: v => (v || []).map(x => String(x).replace('.', ',')).join('; '), parse: v => v.split(/[;\s]+/).map(x => Number(x.replace(',', '.'))).filter(x => x > 0) })),
        h('td', null, toggle(`fm.rds.eon.${i}.tp`, '')), h('td', null, toggle(`fm.rds.eon.${i}.ta`, '', { tid: `eon-ta-${i}` })),
        h('td', null, input(`fm.rds.eon.${i}.linkage`, { type: 'number', min: 0, max: 4095, mono: true })),
        h('td.r', null, h('div.inrow.nowrap', null,
          h('button.btn.sm', { type: 'button', title: T('14B: TA dieses Senders live schalten'), tid: `eon-ta-live-${i}`, onclick: async () => {
            const active = !e.ta;
            if (await sendLive({ rds: { eon_ta: { pi: e.pi, active } } }, active ? T('EON-TA {ps} ein', { ps: e.ps }) : T('EON-TA {ps} aus', { ps: e.ps }))) { e.ta = active; if (S.server?.fm.rds.eon[i]) S.server.fm.rds.eon[i].ta = active; edited(`fm.rds.eon.${i}.ta`, true); }
          } }, icon('bell')),
          h('button.icon-btn', { type: 'button', onclick: () => { r.eon.splice(i, 1); edited('fm.rds.eon', true); } }, icon('trash')))))))))
      : h('p.muted.small', null, T('Keine EON-Einträge. Für TA-Umschaltung auf einen Zweitsender wird der zweite Träger gebraucht (14B per eon_ta).')),
    h('div.inrow.wrap', null,
      h('button.btn.sm', { type: 'button', tid: 'rds-eon-add', onclick: () => { r.eon.push({ pi: S.draft.fm.second_carrier.rds.pi || '0xD3C5', ps: S.draft.fm.second_carrier.rds.ps || 'ZWEITER', pty: 3, tp: true, ta: false, af: [], mapped_af: {}, pin: null, linkage: 0 }); edited('fm.rds.eon', true); } }, icon('plus'), T('EON-Eintrag')),
      r.eon.length ? liveBtn('rds-eon-send', T('EON senden'), () => ({ eon: r.eon })) : null),
  ], { tid: 'card-rds-eon' });

  return h('div.page-grid', null, h('div.col', null, ident, ps, eon), h('div.col', null, flags, af));
}
