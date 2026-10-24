// Startansicht „Regie“: eine Senderkachel je Standard mit Status, Kanal/Frequenz, Pegel, Programm/Jetzt läuft,
// Ein-Klick „Auf Sendung“.

import { h, icon, api, toast, dialog } from './ui.js';
import * as D from './dab.js';
import * as STD from './standards.js';
import { S, txRunning, rfUpdate, confirmAtten, applyConfig, refreshState, changes, nowPlaying, load, save, apiTxStart } from './core.js';
import { estDbm } from './pages.js';
import { APP_NAME } from './brand.js';
import { T } from './i18n.js';

const ICON = { dab: 'radio', fm: 'wave', dvbt: 'antenna', dvbc: 'layers', dvbs: 'star', pal: 'tv' };
const FIRST_PAGE = { dab: 'dienste', fm: 'sender', dvbt: 'modulation', dvbc: 'modulation', dvbs: 'modulation', pal: 'pal' };

const RANGE = { dab: [170e6, 1500e6], fm: [87.5e6, 108e6], dvbt: [40e6, 1000e6], dvbc: [40e6, 1000e6], dvbs: [430e6, 5850e6], pal: [45e6, 870e6] };
const plausible = (std, rf) => rf && rf.freq_hz >= RANGE[std][0] && rf.freq_hz <= RANGE[std][1] && !(std === 'fm' && rf.channel !== 'fm') && !(std === 'dvbs' && !['sat', 'ham'].includes(rf.channel));

/** Letzte HF-Einstellung je Standard merken (für inaktive Kacheln und den Wechsel); nur plausible Werte (kein Zwischenstand beim Umschalten) */
export function rememberRf() {
  if (!S.server || S.rfIntent?.size) return;
  const std = STD.standardOf(S.server);
  if (!plausible(std, S.server.rf)) return;
  const all = load('lastRf', {});
  const rf = S.server.rf;
  if (!all[std] || all[std].freq_hz !== rf.freq_hz || all[std].channel !== rf.channel) { all[std] = { channel: rf.channel, freq_hz: rf.freq_hz, offset_hz: rf.offset_hz || 0 }; save('lastRf', all); }
}
function rfFor(std) {
  if (S.server && STD.standardOf(S.server) === std) return S.server.rf;
  const last = load('lastRf', {})[std];
  if (plausible(std, last)) return last;
  const c = STD.setStandard(structuredClone(S.server || S.draft), std);
  return c.rf;
}

function tileInfo(std, cfg) {
  const rf = rfFor(std);
  const f = hz => `${D.fmtMHz(hz)} MHz`;
  const np = k => nowPlaying(k);
  if (std === 'dab') {
    const sv = cfg.services || [];
    const n = sv.map(s => np(s.key)).find(Boolean);
    return { ch: rf.channel === 'custom' ? T('frei') : rf.channel, freq: f(rf.freq_hz), params: `${T('{n} Dienste', { n: sv.length })} · ${D.capacity(cfg).used}/864 CU · Mode ${['I', 'II', 'III', 'IV'][(cfg.dab_mode || cfg.mode) - 1] || 'I'}`,
      prog: sv.slice(0, 3).map(s => s.label).join(' · ') || '–', np: n };
  }
  if (std === 'fm') {
    const r = cfg.fm.rds;
    return { ch: T('UKW'), freq: f(rf.freq_hz), params: `±${D.fmtNum(cfg.fm.deviation_khz, 0)} kHz · ${cfg.fm.stereo ? 'Stereo' : 'Mono'}${r.enable ? ` · RDS ${r.pi}` : ''}`, prog: r.enable ? `PS ${T('„{text}“', { text: r.ps })} · ${r.rt}` : T('RDS aus'), np: np('fm') };
  }
  if (std === 'pal') {
    const p = cfg.pal, c = STD.palCarriers({ ...cfg, rf });
    return { ch: rf.channel === 'custom' ? T('frei') : rf.channel, freq: T('Bild {f}', { f: f(c.vision) }), params: `${p.system === 'BG' ? 'B/G' : p.system} · ${T('Ton +{mhz} MHz', { mhz: STD.PAL_SYSTEMS[p.system]?.sound_mhz })} · ${p.live ? T('live') : T('Schleife 160 ms')}`,
      prog: `${p.image.kind === 'testpattern' ? STD.PAL_PATTERNS[p.image.pattern] || p.image.pattern : p.image.kind}${p.image.text ? ` · ${T('„{text}“', { text: p.image.text })}` : ''}` };
  }
  const c2 = { ...cfg, mode: std };
  const prgs = cfg.dvb.programs.map(p => p.name).join(' · ');
  if (std === 'dvbs' && cfg.dvbs?.use === 'ham') { const d = cfg.dvbs; return { ch: `DATV ${d.callsign || '–'}`, freq: f(Math.round(d.ham_freq_mhz * 1e6)), params: `${d.system === 'dvbs2' ? 'DVB-S2' : 'DVB-S'} ${d.modulation} ${d.fec} · ${d.symbol_rate / 1000} kS/s · ${(STD.netRate(c2) / 1e3).toFixed(0)} kbit/s · ${STD.DATV.BANDS[d.ham_band]?.label || ''}`, prog: prgs }; }
  if (std === 'dvbs') { const d = cfg.dvbs; return { ch: `${D.fmtNum(d.transponder_mhz, 0)} ${d.polarization}`, freq: T('ZF {f}', { f: f(STD.satIfHz(d)) }), params: `${d.system === 'dvbs2' ? 'DVB-S2' : 'DVB-S'} ${d.modulation} ${d.fec} · ${d.symbol_rate / 1000} kS/s · ${(STD.netRate(c2) / 1e6).toFixed(1)} Mbit/s`, prog: prgs }; }
  if (std === 'dvbt') { const m = cfg.dvbt; return { ch: rf.channel === 'custom' ? T('frei') : rf.channel, freq: f(rf.freq_hz), params: `${m.bandwidth_mhz} MHz ${m.fft} ${m.constellation} ${m.code_rate} GI ${m.guard} · ${(STD.netRate(c2) / 1e6).toFixed(1)} Mbit/s`, prog: prgs }; }
  const m = cfg.dvbc; return { ch: rf.channel === 'custom' ? T('frei') : rf.channel, freq: f(rf.freq_hz), params: `${m.constellation} · ${(m.symbol_rate / 1e6).toFixed(3)} MBd · ${(STD.netRate(c2) / 1e6).toFixed(1)} Mbit/s`, prog: prgs };
}

async function waitRunning(ms = 12000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { await refreshState(); } catch { /* weiter */ }
    if (S.state.tx?.state === 'running') return true;
    if (S.state.tx?.state === 'error') return false;
    await new Promise(r => setTimeout(r, 300));
  }
  return false;
}

/** Ein Klick: Standard umschalten (falls nötig), Kette starten, Senden einschalten */
export async function goOnAir(std) {
  const active = STD.standardOf(S.server || S.draft);
  if (std !== active) {
    const pending = changes().all.length;
    const ok = await dialog({ title: T('Auf Sendung: {std}', { std: STD.stdName(std) }), tid: 'confirm-onair', ok: T('Umschalten & senden'),
      body: [h('p', null, T('Der Sender wechselt von {from} auf {to}. Die Kette wird neu gestartet; der Pluto bleibt bis zum Einschalten stumm.', { from: STD.stdName(active), to: STD.stdName(std) })),
        pending ? h('p.alert', null, icon('alert'), T('{n} nicht übernommene Änderung(en) im Entwurf werden verworfen.', { n: pending })) : null,
        std === 'dvbs' && S.draft?.dvbs?.use === 'ham' ? h('p.alert', null, icon('alert'), T('Amateurfunk-DATV: nur mit Zulassung, Rufzeichen und passendem Band senden.'))
          : std === 'dvbs' ? h('p.alert', null, icon('alert'), T('Sat-Eingang führt 13/18 V – DC-Block bzw. LNB-Speisung am Receiver aus!')) : null] });
    if (!ok) return false;
    S.draft = STD.normalizeConfig(structuredClone(S.server));
    S.rfIntent.clear();
    STD.setStandard(S.draft, std);
    const last = load('lastRf', {})[std];
    if (plausible(std, last) && std !== 'dvbs') Object.assign(S.draft.rf, { channel: last.channel, freq_hz: last.freq_hz });
    for (const k of ['channel', 'freq_hz', 'offset_hz']) if (JSON.stringify(S.draft.rf[k]) !== JSON.stringify(S.server.rf[k])) S.rfIntent.add(k);
    const errs = STD.validateConfig(S.draft);
    if (errs.length) { toast(`${STD.stdName(std)}: ${errs[0].msg}`, 'error', 7000); location.hash = `#/${FIRST_PAGE[std]}`; S.render(true); return false; }
    if (txRunning()) { try { await api('POST', '/api/tx/stop'); } catch { /* weiter */ } try { await refreshState(); } catch { /* weiter */ } S.state.tx = { ...(S.state.tx || {}), state: 'idle' }; }
    if (!(await applyConfig({ quiet: true, noRestart: true }))) return false;
  }
  if (!txRunning()) {
    try { await apiTxStart(); } catch (e) { toast(T('Start fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000); return false; }
    if (!(await waitRunning())) { toast(T('Kette läuft nicht – siehe Log'), 'error', 6000); return false; }
  }
  if (!(await confirmAtten(S.server.rf.atten_db, D.ATTEN_MAX))) return false;
  const ok = await rfUpdate({ enabled: true }, { silent: true });
  if (ok) toast(T('{std} ist auf Sendung', { std: STD.stdName(std) }), 'ok');
  S.render(true);
  return ok;
}

export function pageRegie() {
  const cfg = S.server || S.draft;
  const active = STD.standardOf(cfg);
  const running = txRunning();
  const phase = S.state.tx?.state === 'starting' ? 'starting' : running ? (cfg.rf.enabled ? ((S.state.pluto?.atten_db ?? 0) >= 89 ? 'muted' : 'onair') : 'ready') : S.state.tx?.state === 'error' ? 'error' : 'off';
  const tiles = STD.STANDARDS.map(st => {
    const isActive = st.id === active;
    const info = tileInfo(st.id, cfg);
    const status = isActive ? phase : 'idle';
    const statusText = { onair: 'ON AIR', ready: T('STUMM'), muted: T('AN, STUMM'), starting: T('STARTET …'), error: T('FEHLER'), off: T('AUS'), idle: T('BEREIT') }[status];
    const rf = rfFor(st.id);
    const est = isActive ? estDbm(cfg.rf) : null;
    const np = info.np;
    return h('section.tile.' + status + (isActive ? '.active' : ''), { tid: `regie-tile-${st.id}` },
      h('header', null,
        h('span.tile-ic', null, icon(ICON[st.id])),
        h('div.tile-name', null, h('b', null, st.name), h('small', null, st.long)),
        h('span.tile-status', { tid: `regie-status-${st.id}` }, h('i'), statusText)),
      h('div.tile-freq', null, h('b.mono', null, info.ch), h('span.mono', null, info.freq)),
      h('div.tile-params.mono', null, info.params),
      h('div.tile-level', null,
        isActive ? [h('span', null, icon('gauge'), h('b.mono', null, `${D.fmtDb(cfg.rf.atten_db)} dB`)), est != null ? h('span.mono', null, `≈ ${D.fmtNum(est, 1)} dBm`) : null,
          h('span.muted', null, STD.requiredPath(cfg).path === 'pluto' ? T('Modulator auf dem Pluto') : 'USB-Streaming')]
          : h('span.muted', null, T('inaktiv – Ein Klick schaltet um'))),
      h('div.tile-prog', null, h('small', null, T('Programm')), h('span', { title: info.prog }, info.prog)),
      np ? h('div.tile-np', { tid: `regie-np-${st.id}` }, icon('music'), h('b', null, np.title || ''), np.artist ? h('span', null, ` – ${np.artist}`) : null,
        h('i', null, h('b', { style: { width: np.duration_s ? `${Math.min(100, np.elapsed_s / np.duration_s * 100)}%` : '0' } }))) : null,
      h('footer', null,
        isActive && status === 'onair'
          ? h('button.btn.lg.mute', { type: 'button', tid: `regie-mute-${st.id}`, onclick: async () => { await rfUpdate({ enabled: false }); S.render(true); } }, icon('mute'), T('Stumm schalten'))
          : h('button.btn.lg.onair', { type: 'button', tid: `regie-onair-${st.id}`, disabled: status === 'starting', onclick: () => goOnAir(st.id) }, icon('power'), T('Auf Sendung')),
        h('button.btn.ghost', { type: 'button', tid: `regie-edit-${st.id}`, onclick: () => {
          if (STD.standardOf(S.draft) !== st.id) { STD.setStandard(S.draft, st.id); const last = load('lastRf', {})[st.id]; if (plausible(st.id, last) && st.id !== 'dvbs') Object.assign(S.draft.rf, { channel: last.channel, freq_hz: last.freq_hz }); for (const k of ['channel', 'freq_hz']) if (S.draft.rf[k] !== S.server?.rf[k]) S.rfIntent.add(k); }
          location.hash = `#/${FIRST_PAGE[st.id]}`;
        } }, icon('sliders'), T('Konfigurieren'))));
  });
  return h('div.page-stack.regie', null,
    firmwareNotice(),
    h('div.regie-head', null,
      h('div', null, h('h2', null, APP_NAME), h('p.muted', null, T('Ein Sender, sechs Standards. Kachel wählen, „Auf Sendung“ – die Kette startet, der Pluto schaltet nach der Sicherheitsabfrage ein.'))),
      h('span.grow')),   // Not-Aus steht einmal fest in der Kopfzeile (auf allen Seiten und Bildschirmgrößen)
    h('div.tiles', { tid: 'regie-tiles' }, tiles));
}

/** Hinweis, wenn auf dem Pluto keine Muxwright-Firmware (dablab-Build mit Geräte-Agent) läuft */
function firmwareNotice() {
  const pl = S.state?.pluto || {}, dev = S.state?.rf?.device || {};
  if (!pl.connected) return null;
  const ours = /dablab/.test(pl.fw || '') && dev.reachable !== false;
  if (ours) return null;
  return h('div.alert', { tid: 'fw-notice' }, icon('alert'), h('div', null,
    h('b', null, dev.reachable === false ? T('Muxwright-Firmware nicht erkannt (Pluto-FW {fw}, Geräte-Agent nicht erreichbar).', { fw: pl.fw || T('unbekannt') }) : T('Muxwright-Firmware nicht erkannt (Pluto-FW {fw}).', { fw: pl.fw || T('unbekannt') })),
    h('p', null, T('DAB+ und UKW laufen per USB auch mit der Werksfirmware. DVB-T, DVB-C, DVB-S/S2 und PAL brauchen den Modulator auf dem Pluto: pluto.frm auf das Laufwerk „PlutoSDR“ kopieren und auswerfen (vorher optional im RAM testen). Zurück zur Werksfirmware geht genauso.'))));
}
