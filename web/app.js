// PlutoSDR-Regie – Einstieg: Kopfzeile, Navigation, Zustand/SSE. (Name zentral in js/brand.js)

import { pageHardware, pageLogFile } from './js/extras.js';
import { openOnboarding, onboardingNeeded } from './js/onboarding.js';
import { h, icon, $, $$, toast, api } from './js/ui.js';
import * as D from './js/dab.js';
import { S, refreshState, ingestState, mergeLog, refreshDecor, updateApplyBar, applyConfig, setAutoApply, discard, txStart, txStop, emergencyStop, edited,
  rfUpdate, confirmAtten, txRunning, runner, loadMedia, load, save, changes, setServerConfig } from './js/core.js';
import { pageHF, pageEnsemble, pageServices, pageAnnouncements, pageCapacity, pageCheck, pagePresets, pageLog, pageMedia, estDbm, sweepNow } from './js/pages.js';
import { pageFM, pageRDS } from './js/fm.js';
import { pageModulation, pagePrograms, pageSI, tsBudgetBar } from './js/dvb.js';
import * as STD from './js/standards.js';
import { pagePAL } from './js/pal.js';
import { pageRegie, rememberRf } from './js/regie.js';
import { APP_NAME, APP_SUB, APP_VERSION } from './js/brand.js';
import { pageFirmware } from './js/firmware.js';
import { pageSettings } from './js/settings.js';
import { T, translateDom, LOCALE } from './js/i18n.js';
import { loadPlaylists, nowPlaying, playlistCtl } from './js/core.js';

const ALL_PAGES = [
  { id: 'regie', title: T('Regie'), sub: T('Alle Sender auf einen Blick'), icon: 'grid', render: pageRegie, err: () => false },
  { id: 'hf', title: T('HF'), sub: T('Kanal, Pegel, Ausgabeweg'), icon: 'sliders', render: pageHF, err: p => /^(rf|tii|mode)/.test(p) },
  { id: 'ensemble', std: 'dab', title: T('Ensemble'), sub: T('Kennung, Labels, Zeit'), icon: 'antenna', render: pageEnsemble, err: p => p.startsWith('ensemble') },
  { id: 'dienste', std: 'dab', title: T('Dienste'), sub: 'Audio, PAD, Slideshow', icon: 'radio', render: pageServices, err: p => p.startsWith('services') },
  { id: 'durchsagen', std: 'dab', title: T('Durchsagen'), sub: T('Cluster, Alarm'), icon: 'bell', render: pageAnnouncements, err: p => p.startsWith('clusters') },
  { id: 'kapazitaet', std: 'dab', title: T('Kapazität'), sub: '864 CU', icon: 'pie', render: pageCapacity, err: p => p === 'capacity' },
  { id: 'sender', std: 'fm', title: T('Sender'), sub: T('MPX, Audio, 2. Träger'), icon: 'wave', render: pageFM, err: p => /^fm\.(?!rds)/.test(p) },
  { id: 'rds', std: 'fm', title: 'RDS', sub: 'PI, PS, RT, AF, EON', icon: 'text', render: pageRDS, err: p => p.startsWith('fm.rds') },
  { id: 'modulation', std: 'dvb', title: T('Modulation'), sub: T('Bandbreite, QAM, Coderate'), icon: 'wave', render: pageModulation, err: p => /^(dvb[tcs]|atsc|isdbt|dtmb)\./.test(p) },
  { id: 'programme', std: 'dvb', title: T('Programme'), sub: T('Transportstrom, Video, Audio'), icon: 'tv', render: pagePrograms, err: p => p.startsWith('dvb.programs') || p === 'tsbudget' },
  { id: 'si', std: 'dvb', title: T('SI-Tabellen'), sub: 'NIT, SDT, LCN, EIT', icon: 'layers', render: pageSI, err: p => p.startsWith('dvb.si') },
  { id: 'pal', std: 'pal', title: T('Bild & Ton'), sub: T('Testbild, Text, Ton'), icon: 'tv', render: pagePAL, err: p => p.startsWith('pal.') },
  { id: 'medien', title: T('Medien & Playlists'), short: T('Medien'), sub: T('Bibliothek, Playlists'), icon: 'playlist', render: pageMedia, err: () => false },
  { id: 'pruefen', title: T('Prüfen'), sub: T('Offline & Kontrollempfang'), icon: 'scan', render: pageCheck, err: () => false },
  { id: 'presets', title: T('Presets & Testpläne'), short: T('Presets'), sub: T('Vorlagen, Protokoll'), icon: 'checklist', render: pagePresets, err: () => false },
  { id: 'log', title: T('Log'), sub: T('Live-Ereignisse'), icon: 'list', render: pageLog, err: () => false },
  { id: 'logdatei', title: T('Logdatei'), sub: T('je Tag, mehrsprachig'), icon: 'file', render: pageLogFile, err: () => false },
  { id: 'einstellungen', title: T('Einstellungen'), sub: T('Sprache, Darstellung'), icon: 'flag', render: () => pageSettings({ applyTheme }), err: () => false },
  { id: 'firmware', title: T('Firmware'), sub: T('Prüfen, RAM-Test, Flashen'), icon: 'upload', render: pageFirmware, err: () => false },
  { id: 'hardware', title: T('Benötigte Hardware'), short: T('Hardware'), sub: T('PC-Leistung, Shops, Unterstützen'), icon: 'chip', render: pageHardware, err: () => false },
];
const stdKey = std => STD.isDvb(std) ? 'dvb' : std;
function pages() { const k = stdKey(STD.standardOf(S.draft || {})); return ALL_PAGES.filter(p => !p.std || p.std === k); }

// ---------------------------------------------------------------- Theme
const THEMES = ['system', 'light', 'dark'];
function applyTheme(t) {
  if (t === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  const b = $('[data-testid=theme-toggle]');
  if (b) {
    b.replaceChildren(icon(t === 'light' ? 'sun' : t === 'dark' ? 'moon' : 'auto'));
    b.title = T('Darstellung: {theme} (klicken zum Wechseln)', { theme: { system: T('wie System'), light: T('hell'), dark: T('dunkel') }[t] });
    b.dataset.theme = t;
  }
}

// ---------------------------------------------------------------- Rendering
let lastNavStd = null;
function renderNav() {
  const nav = $('#nav');
  const std = STD.standardOf(S.draft || {});
  nav.replaceChildren(...pages().flatMap((p, i, arr) => {
    const a = h('a.navitem', { href: `#/${p.id}`, tid: `nav-${p.id}`, class: S.page === p.id ? 'on' : null, 'aria-current': S.page === p.id ? 'page' : null },
      icon(p.icon), h('span.nv-t', null, h('b', null, p.short || p.title), h('small', null, p.sub)), h('span.nv-badge', { 'data-nav-badge': p.id }));
    const first = p.std && (i === 0 || !arr[i - 1].std), last = p.std && (i === arr.length - 1 || !arr[i + 1].std);
    return [first ? h('div.navsec', { tid: 'nav-std-label' }, STD.stdName(std)) : null, a, last ? h('div.navsec', null, T('Gemeinsam')) : null].filter(Boolean);
  }), h('div.navcap', { tid: 'nav-capacity' }));
  lastNavStd = std;
  updateNavBadges();
}
function updateNavBadges() {
  for (const p of pages()) {
    const el = $(`[data-nav-badge="${p.id}"]`); if (!el) continue;
    const n = S.errors.filter(e => p.err(e.path)).length;
    el.textContent = n ? String(n) : (p.id === 'dienste' ? String(S.draft?.services.length ?? '') : '');
    el.className = 'nv-badge' + (n ? ' err' : '');
  }
  const nc = $('.navcap');
  const std = STD.standardOf(S.draft || {});
  if (nc && S.draft && STD.isDvb(std)) { nc.replaceChildren(h('div.nc-row', null, h('small', null, T('Transportstrom'))), tsBudgetBar({ tid: 'nav-ts' })); }
  else if (nc && S.draft && std !== 'dab') nc.replaceChildren();
  else if (nc && S.draft) {
    const cap = D.capacity(S.draft);
    nc.replaceChildren(h('div.nc-row', null, h('small', null, T('Kapazität')), h('small.mono' + (cap.over ? '.bad' : ''), null, `${cap.used}/${D.TOTAL_CU} CU`)),
      h('div.capbar.thin' + (cap.over ? '.over' : ''), null, h('i', { style: { width: `${Math.min(100, cap.used / D.TOTAL_CU * 100)}%` } })));
  }
}

let renderQueued = false;
function render(keepFocus = false) {
  if (!S.draft) return;
  const main = $('#main');
  if (STD.standardOf(S.draft) !== lastNavStd) renderNav();
  if (!pages().some(p => p.id === S.page)) { S.page = 'regie'; history.replaceState(null, '', '#/regie'); }
  const page = pages().find(p => p.id === S.page) || pages()[0];
  const active = document.activeElement;
  const focusTid = keepFocus && active && main.contains(active) ? active.dataset?.testid : null;
  const scroll = window.scrollY;
  S.watchers = [];
  S.npWatchers = [];
  S.logDraw = null;
  const title = h('div.pagehead', null, h('h1', null, page.title), h('p', null, page.sub));
  let content;
  try { content = page.render(); }
  catch (e) { console.error(e); content = h('p.alert', null, T('Darstellungsfehler: {msg}', { msg: e.message })); }
  main.replaceChildren(title, content);
  main.dataset.page = page.id;
  document.title = `${page.title} · ${APP_NAME}`;
  $$('.navitem').forEach(a => { const on = a.getAttribute('href') === `#/${page.id}`; a.classList.toggle('on', on); on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'); });
  refreshDecor();
  updateNavBadges();
  if (keepFocus) {
    window.scrollTo(0, scroll);
    if (focusTid) $(`[data-testid="${focusTid}"]`, main)?.focus({ preventScroll: true });
  }
}
S.render = (keep = true) => render(keep);

function route() {
  const id = (location.hash.match(/^#\/([\w-]+)/) || [])[1] || 'regie';
  S.page = pages().some(p => p.id === id) ? id : 'regie';
  save('page', S.page);
  render(false);
  window.scrollTo(0, 0);
  $(`[data-testid=nav-${S.page}]`)?.scrollIntoView({ block: 'nearest', inline: 'center' });
}

// ---------------------------------------------------------------- Kopfzeile
function txPhase() {
  const st = S.state.tx?.state || 'idle';
  if (st === 'running') {
    if (!S.server?.rf.enabled) return 'ready';
    const pa = S.state.pluto?.atten_db;
    return S.state.pluto?.connected !== false && pa != null && pa >= 89 ? 'muted' : 'onair';
  }
  if (st === 'starting') return 'starting';
  if (st === 'error') return 'error';
  return 'off';
}
const PHASE_TEXT = { off: T('AUS'), starting: T('STARTET …'), ready: T('BEREIT'), onair: 'ON AIR', muted: T('AN, STUMM'), error: T('FEHLER') };

let lastSamples = null;
/** Fehlende Werkzeuge aus dem Selbsttest (selftest-JSON oder einfache Werkzeugliste) */
function missingTools(t) {
  if (!t || typeof t !== 'object') return [];
  if (Array.isArray(t.checks)) return t.checks.filter(c => c && c.ok === false).map(c => c.name);
  const src = t.tools && typeof t.tools === 'object' ? t.tools : t;
  return Object.entries(src).filter(([, v]) => v === false || (v && typeof v === 'object' && v.ok === false)).map(([k]) => k);
}
function updateHeader() {
  const rf = S.server?.rf || S.draft?.rf; if (!rf) return;
  const tx = S.state.tx || {};
  const pl = S.state.pluto || {};
  const rn = runner();
  const phase = txPhase();
  document.body.dataset.phase = phase;
  const box = $('#txstatus');
  box.className = 'txstatus ' + phase;
  $('.tx-state', box).textContent = PHASE_TEXT[phase];
  $('.tx-sub', box).textContent = phase === 'muted' ? T('Senden an, aber Pluto auf {db} dB – kein Signal', { db: D.fmtDb(S.state.pluto.atten_db) }) : phase === 'ready' ? T('stumm (−89,75 dB)') : phase === 'off' ? T('Kette gestoppt') : phase === 'starting' ? T('Kette startet') : phase === 'error' ? (tx.error || T('siehe Log')) : T('sendet');
  const est = estDbm(rf);
  const stdS = STD.standardOf(S.server || S.draft);
  const stdInfo = stdS === 'fm' ? `±${D.fmtNum((S.server || S.draft).fm.deviation_khz, 0)} kHz${(S.server || S.draft).fm.rds.enable ? ' · RDS' : ''}`
    : stdS === 'dvbs' ? `${(S.server || S.draft).dvbs.transponder_mhz} ${(S.server || S.draft).dvbs.polarization} ${(S.server || S.draft).dvbs.symbol_rate / 1000} kS/s ${(S.server || S.draft).dvbs.fec}`
    : stdS === 'pal' ? `${(S.server || S.draft).pal.system === 'BG' ? 'B/G' : (S.server || S.draft).pal.system} · ${T('Ton {mhz}', { mhz: D.fmtMHz(STD.palCarriers(S.server || S.draft).sound) })}`
    : stdS === 'dvbt' ? `${(S.server || S.draft).dvbt.bandwidth_mhz} MHz ${(S.server || S.draft).dvbt.constellation} ${(S.server || S.draft).dvbt.code_rate}`
    : stdS === 'dvbc' ? `${((S.server || S.draft).dvbc.symbol_rate / 1e6).toLocaleString(LOCALE, { minimumFractionDigits: 3 })} MBd ${(S.server || S.draft).dvbc.constellation}` : null;
  $('.tx-details', box).replaceChildren(
    h('span', null, h('b', { tid: 'hdr-std' }, STD.stdName(stdS)), ' ', h('b', null, stdS === 'fm' || rf.channel === 'fm' ? '' : rf.channel === 'custom' ? T('frei') : rf.channel === 'sat' ? T('ZF') : rf.channel)),
    h('span.mono', null, D.fmtMHz(rf.freq_hz + (rf.offset_hz || 0)), ' MHz'),
    ...(stdInfo ? [h('span.mono.muted', { tid: 'hdr-std-info' }, stdInfo)] : []),
    h('span.mono', { tid: 'hdr-atten' }, D.fmtDb(rf.atten_db), ' dB'),
    h('span.mono' + (est == null ? '.muted' : '.dbm'), { tid: 'hdr-dbm', title: est == null ? T('kein Kalibrierwert für diesen Kanal') : T('geschätzt aus Kalibrierwert') }, est == null ? '– dBm' : `≈ ${D.fmtNum(est, 1)} dBm`));

  // Standard-Umschalter (zeigt den Entwurf; abweichend vom Server → Punkt)
  const stdD = STD.standardOf(S.draft);
  for (const b of $$('.stdswitch button')) { b.classList.toggle('on', b.dataset.std === stdD); b.setAttribute('aria-checked', String(b.dataset.std === stdD)); b.classList.toggle('pending', b.dataset.std === stdD && stdD !== stdS); }
  // Steuerung
  const running = txRunning();
  const en = $('[data-testid=rf-enable]');
  const sending = !!rf.enabled && running;
  if (!S.busy.send) en.checked = sending;
  en.disabled = !!S.busy.send;
  en.closest('.switch').classList.toggle('on', en.checked);
  $('.rfsw-text').textContent = S.busy.send ? (en.checked ? T('Startet …') : T('Stoppt …')) : sending ? T('Senden an') : T('Senden aus');

  // Status-Chips
  const chip = (sel, kind, value, title) => { const c = $(sel); c.className = 'chip-st ' + kind; $('b', c).textContent = value; if (title) c.title = title; };
  chip('[data-testid=st-pluto]', pl.connected ? 'ok' : 'bad', pl.connected ? (pl.temp_c != null ? `${D.fmtNum(pl.temp_c, 0)} °C` : T('verbunden')) : T('getrennt'),
    pl.connected ? `Pluto ${pl.uri || ''} · FW ${pl.fw || '?'} · LO ${pl.lo_hz ? D.fmtMHz(pl.lo_hz) + ' MHz' : '–'} · ${T('Dämpfung')} ${pl.atten_db != null ? D.fmtDb(pl.atten_db) + ' dB' : '–'}` : T('Pluto nicht verbunden'));
  const tools = missingTools(rn.tools);
  chip('[data-testid=st-runner]', rn.reachable ? (tools.length ? 'warn' : 'ok') : 'bad', rn.reachable ? (tools.length ? T('{n} fehlt', { n: tools.length }) : T('bereit')) : T('nicht erreichbar'),
    rn.reachable ? (tools.length ? T('Signalerzeugung (WSL) erreichbar · fehlend: {tools}', { tools: tools.join(', ') }) : T('Signalerzeugung (WSL) erreichbar')) : T('Signalerzeugung (WSL) nicht erreichbar'));
  chip('[data-testid=st-underruns]', tx.underruns ? 'warn' : running ? 'ok' : 'na', tx.underruns == null ? '–' : D.fmtNum(tx.underruns), T('Underruns seit Start'));
  let rate = tx.rate_ms;
  if (rate == null && tx.samples_sent != null) {
    const now = performance.now();
    if (lastSamples) { const dt = (now - lastSamples.t) / 1000; if (dt > 0.2) rate = (tx.samples_sent - lastSamples.n) / dt / 1e6; }
    lastSamples = { t: now, n: tx.samples_sent };
  }
  chip('[data-testid=st-rate]', running ? 'ok' : 'na', running && rate != null ? `${D.fmtNum(rate, 2)} MS/s` : '–', T('Datenrate zum Pluto'));
  const buf = tx.buffer_ms;
  chip('[data-testid=st-buffer]', !running || buf == null ? 'na' : buf < 50 ? 'warn' : 'ok', running && buf != null ? `${D.fmtNum(buf, 0)} ms` : '–', T('Puffer'));
  const up = $('[data-testid=st-uptime]');
  if (running && tx.since) {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - (tx.since > 1e12 ? tx.since / 1000 : tx.since)));
    up.hidden = false; $('b', up).textContent = `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  } else up.hidden = true;

  updateHeaderNP();
  const conn = $('[data-testid=conn]');
  conn.className = 'conn ' + (S.connected ? 'ok' : 'bad');
  conn.title = S.connected ? T('Live-Verbindung zum Server aktiv') : T('Verbindung unterbrochen – verbinde neu …');
  $('span', conn).textContent = S.connected ? T('live') : T('getrennt');
  document.body.classList.toggle('offline', !S.connected);
}

/** Kompakte Jetzt-läuft-Zeile in der Kopfzeile (erster Playlist-Dienst mit Meldung) */
let hdrNpKey = null;
function updateHeaderNP() {
  const el = $('#hdr-np'); if (!el) return;
  const svcs = (S.server?.services || []).filter(s => s.audio?.source?.kind === 'playlist');
  const s = svcs.find(x => nowPlaying(x.key)) || null;
  const np = s ? nowPlaying(s.key) : null;
  if (!np || !txRunning()) { el.hidden = true; hdrNpKey = null; return; }
  el.hidden = false;
  const d = Number(np.duration_s) || 0, e = Number(np.elapsed_s) || 0;
  const title = np.title || (np.file || '').split('/').pop() || T('Titel {n}', { n: (np.index ?? 0) + 1 });
  if (hdrNpKey !== s.key) {
    hdrNpKey = s.key;
    el.replaceChildren(
      h('button', { type: 'button', tid: 'hdr-np-prev', title: T('Vorheriger Titel'), onclick: () => playlistCtl(s.key, 'prev') }, icon('prev')),
      icon('music'), h('b', { tid: 'hdr-np-title' }), h('span.np-h-artist'), h('i', null, h('b')), h('span.np-h-pos.mono'),
      h('button', { type: 'button', tid: 'hdr-np-next', title: T('Nächster Titel'), onclick: () => playlistCtl(s.key, 'next') }, icon('next')));
  }
  $('[data-testid=hdr-np-title]', el).textContent = title;
  $('.np-h-artist', el).textContent = np.artist ? `– ${np.artist}` : '';
  $('i > b', el).style.width = d ? `${Math.min(100, e / d * 100)}%` : '0';
  $('.np-h-pos', el).textContent = d ? `${D.fmtDur(e)}/${D.fmtDur(d)}` : D.fmtDur(e);
  el.title = `${s.label}: ${title}${np.artist ? ' – ' + np.artist : ''}`;
}

let regieKey = '';
function liveUpdate() {
  updateHeader();
  rememberRf();
  if (S.page === 'regie' && S.server) {
    const np = S.state.tx?.now_playing ? JSON.stringify(Object.values(S.state.tx.now_playing).map(x => x && [x.title, Math.floor((x.elapsed_s || 0) / 5)])) : '';
    const k = [S.state.tx?.state, S.server.mode, S.server.rf.enabled, S.server.rf.atten_db, S.server.rf.freq_hz, S.state.pluto?.atten_db, np].join('|');
    if (k !== regieKey) { regieKey = k; if (!document.querySelector('dialog[open]')) render(true); }
  }
  (S.npWatchers || []).forEach(f => { try { f(); } catch (e) { console.warn(e); } });
  updateNavBadges();
  S.sweepUpd?.();
  S.logDraw?.();
}
S.liveUpdate = liveUpdate;

// ---------------------------------------------------------------- SSE
let es = null, retry = 0, retryTimer = null;
function connectSSE() {
  clearTimeout(retryTimer);
  if (es) es.close();
  es = new EventSource('/api/events');
  const handler = ev => {
    let d; try { d = JSON.parse(ev.data); } catch { if (ev.type === 'log') { mergeLog(ev.data); liveUpdate(); } return; }
    if (!S.connected) { S.connected = true; }
    if (ev.type === 'log' || (d && d.msg !== undefined && d.lvl !== undefined && !d.tx)) mergeLog(Array.isArray(d) ? d : [d]);
    else if (ev.type === 'check' || ev.type === 'monitor') S.state[ev.type] = d;
    else {
      const payload = d && d.state && typeof d.state === 'object' && d.state.tx ? d.state : d;
      if (Array.isArray(payload.log_new)) mergeLog(payload.log_new);
      const redraw = ingestState(payload);
      if (redraw) render(true);
    }
    liveUpdate();
    if (!S.page || S.page === 'log') S.logDraw?.();
  };
  es.onmessage = handler;
  for (const t of ['state', 'log', 'tx', 'status', 'sweep', 'check', 'monitor', 'config']) es.addEventListener(t, handler);
  es.onopen = () => {
    const was = S.connected; S.connected = true; retry = 0;
    if (!was) { refreshState().then(r => { liveUpdate(); }).catch(() => {}); }
    liveUpdate();
  };
  es.onerror = () => {
    if (S.connected) toast(T('Verbindung zum Server unterbrochen – verbinde neu …'), 'error', 4000);
    S.connected = false; liveUpdate();
    if (es.readyState === EventSource.CLOSED) {
      retry = Math.min(retry + 1, 3);
      retryTimer = setTimeout(connectSSE, 500 * 2 ** retry);
    }
  };
}

// Fallback-Abfrage, falls SSE länger hängt
setInterval(() => {
  // Server wieder erreichbar? Dann SSE sofort neu aufbauen\n  if (!S.connected) refreshState().then(() => { liveUpdate(); if (!S.connected) connectSSE(); }).catch(() => {});
}, 5000);
setInterval(() => { if (txRunning()) updateHeader(); }, 1000);

// ---------------------------------------------------------------- Start
async function init() {
  translateDom();
  applyTheme(load('theme', 'system'));
  $('.brand-t b').textContent = APP_NAME; $('.brand-t small').textContent = `${APP_VERSION} · ${APP_SUB.replace(/\bUKW\b/, T('UKW'))}`; document.title = APP_NAME;
  const tb = $('#topbar');
  new ResizeObserver(() => document.documentElement.style.setProperty('--tbh', tb.offsetHeight + 'px')).observe(tb);
  $('[data-testid=theme-toggle]').addEventListener('click', () => {
    const t = THEMES[(THEMES.indexOf(load('theme', 'system')) + 1) % 3]; save('theme', t); applyTheme(t);
  });
  $('[data-testid=estop]').addEventListener('click', emergencyStop);
  $('.stdswitch').replaceChildren(...STD.STANDARDS.map(st => h('button', { type: 'button', role: 'radio', tid: `std-${st.id}`, dataset: { std: st.id }, title: T('{std} – Wechsel braucht „Übernehmen (Neustart)“', { std: st.long }), onclick: () => {
    if (STD.standardOf(S.draft) === st.id) return;
    STD.setStandard(S.draft, st.id); S.sel = 0; S.prgSel = 0;
    for (const k of ['channel', 'freq_hz', 'offset_hz']) if (JSON.stringify(S.draft.rf[k]) !== JSON.stringify(S.server?.rf[k])) S.rfIntent.add(k);   // Startkanal des Standards wird beim Übernehmen live gesetzt
    edited('mode', true);
    if (!pages().some(p => p.id === S.page)) location.hash = '#/hf';
    if (st.id === 'dvbs') toast(T('Achtung: Sat-Eingang führt 13/18 V – DC-Block bzw. LNB-Speisung am Receiver aus!'), 'error', 8000);
    toast(T('Standard {std} gewählt – aktiv nach „Übernehmen (Neustart)“', { std: st.name }), 'info', 4000);
  } }, st.name)));
  // Ein Schalter für alles: an = Kette starten (falls nötig) und nach der Sicherheitsabfrage Pluto einschalten,
  // aus = Pluto stumm und Kette stoppen
  $('[data-testid=rf-enable]').addEventListener('change', async e => {
    const on = e.target.checked;
    if (on && onboardingNeeded()) { e.target.checked = false; openOnboarding(); return; }
    S.busy.send = true; updateHeader();
    try {
      if (on) {
        if (!(await confirmAtten(S.server.rf.atten_db, D.ATTEN_MAX))) { e.target.checked = false; return; }
        if (!txRunning()) {
          await txStart();
          await refreshState().catch(() => {});
          for (let i = 0; i < 180 && !['running', 'error', 'idle'].includes(S.state?.tx?.state); i++) { await new Promise(r => setTimeout(r, 500)); await refreshState(); }
          if (S.state?.tx?.state !== 'running') { e.target.checked = false; toast(T('Kette nicht gestartet – Senden bleibt aus.'), 'error', 6000); return; }
        }
        await rfUpdate({ enabled: true });
      } else {
        await rfUpdate({ enabled: false });
        await txStop();
      }
    } finally { S.busy.send = false; updateHeader(); }
  });
  $('[data-testid=onb-open]').addEventListener('click', () => openOnboarding({ force: true }));
  $('[data-testid=apply]').addEventListener('click', () => applyConfig());
  $('[data-testid=apply-auto]').addEventListener('change', e => setAutoApply(e.target.checked));
  $('[data-testid=apply-discard]').addEventListener('click', discard);
  $('[data-testid=apply-reload]').addEventListener('click', () => { setServerConfig(JSON.parse(S.serverRaw), { force: true }); render(true); });
  $('[data-testid=apply-errors]').addEventListener('click', () => $('#applybar').classList.toggle('showerr'));
  window.addEventListener('hashchange', route);
  window.addEventListener('keydown', e => {
    // Not-Aus per Tastatur: Strg+Leertaste oder Pause
    if ((e.ctrlKey && e.code === 'Space') || e.code === 'Pause') { e.preventDefault(); emergencyStop(); }
    if (e.ctrlKey && e.key === 's') { e.preventDefault(); if (changes().all.length) applyConfig(); }
  });

  try {
    await refreshState();
    S.connected = true;
  } catch (e) {
    toast(T('Server nicht erreichbar: {msg}', { msg: e.message }), 'error', 8000);
    setServerConfig(D.defaultConfig());
  }
  if (!S.draft) setServerConfig(D.defaultConfig());
  Promise.all([loadMedia(), loadPlaylists()]).then(() => { if (S.page === 'dienste') render(true); });
  renderNav();
  if (!location.hash) history.replaceState(null, '', '#/regie');
  route();
  liveUpdate();
  connectSSE();
  document.body.classList.add('ready');
  if (onboardingNeeded()) openOnboarding();
}
init();
