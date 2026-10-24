// Gemeinsamer Zustand, Formular-Bindungen und Aktionen.

import { h, icon, api, toast, dialog, $$ } from './ui.js';
import { diffPaths, ATTEN_SAFE, ATTEN_MAX, channelHz, fmtDb } from './dab.js';
import { normalizeConfig, validateConfig, isLivePath, standardOf } from './standards.js';
import { T } from './i18n.js';

export const S = {
  state: {},          // letzter Serverzustand (tx, pluto, runner, sweep, check, monitor)
  server: null,       // normalisierte Serverkonfiguration
  serverRaw: '',
  draft: null,        // bearbeitete Konfiguration
  errors: [],
  page: 'hf',
  sel: 0,             // gewählter Dienst
  svcTab: 'general',
  reportTab: 'check',
  connected: false,
  log: [],
  media: [],
  presets: [],
  playlists: [],
  plSel: null,        // gewählte Playlist (Name)
  busy: {},
  watchers: [],
  render: () => {},
  liveUpdate: () => {},
  rfDragging: false,
  rfIntent: new Set(),   // HF-Livefelder, die der Nutzer per Preset/Testplan/Import bewusst ändern will
  rfPending: {},         // {feld: {v, until}} – vom Server bestätigte Live-Werte, Schutz vor überholten Schnappschüssen
  serverChangedWhileDirty: false,
};

// ---------- Pfadzugriff ----------
export function getPath(o, path) {
  return path.split('.').reduce((a, k) => (a == null ? undefined : a[k]), o);
}
export function setPath(o, path, v) {
  const ks = path.split('.'); const last = ks.pop();
  const t = ks.reduce((a, k) => (a[k] ??= /^\d+$/.test(k) ? [] : {}), o);
  t[last] = v;
}

// ---------- Persistenz (localStorage, fehlertolerant) ----------
export function load(key, def) {
  try { const v = localStorage.getItem('dablab.' + key); return v == null ? def : JSON.parse(v); } catch { return def; }
}
export function save(key, v) { try { localStorage.setItem('dablab.' + key, JSON.stringify(v)); } catch { /* egal */ } }

// ---------- Änderungen / Diff ----------
const RF_LIVE = ['channel', 'freq_hz', 'offset_hz', 'atten_db', 'enabled'];
export function changes() {
  if (!S.server || !S.draft) return { all: [], structural: [], live: [] };
  const all = diffPaths(S.server, S.draft).filter(p => !RF_LIVE.some(k => p === `rf.${k}` && !S.rfIntent.has(k)));
  const structural = all.filter(p => !isLivePath(p));
  return { all, structural, live: all.filter(p => isLivePath(p)) };
}
export const txRunning = () => ['running', 'starting'].includes(S.state?.tx?.state);
export const runner = () => S.state?.runner || S.state?.mac || {};

export function setServerConfig(cfg, { force = false } = {}) {
  const raw = JSON.stringify(cfg);
  // Abgelaufene Schutzfenster auflösen: dann gilt wieder der Serverstand
  const now0 = Date.now();
  for (const [k, p] of Object.entries(S.rfPending)) if (now0 > p.until) {
    delete S.rfPending[k];
    if (S.server && !force && cfg.rf && k in cfg.rf) { S.server.rf[k] = cfg.rf[k]; if (S.draft && !S.rfIntent.has(k) && !S.rfDragging) S.draft.rf[k] = cfg.rf[k]; }
  }
  if (!force && raw === S.serverRaw) return false;
  const wasClean = !S.draft || changes().all.length === 0;
  S.serverRaw = raw;
  S.server = normalizeConfig(cfg);
  // Frisch bestätigte Live-Werte dürfen von einem überholten Schnappschuss nicht zurückgesetzt werden
  const now = Date.now();
  for (const [k, p] of Object.entries(S.rfPending)) {
    if (now > p.until || JSON.stringify(S.server.rf[k]) === JSON.stringify(p.v)) delete S.rfPending[k];
    else S.server.rf[k] = p.v;
  }
  if (!S.draft || wasClean || force) {
    const keep = Object.fromEntries([...S.rfIntent].map(k => [k, S.draft?.rf?.[k]]));
    const before = S.draft;
    S.draft = structuredClone(S.server);
    if (!force) Object.assign(S.draft.rf, keep); else S.rfIntent.clear();
    S.serverChangedWhileDirty = false;
    // Nur Live-Werte geändert (z. B. DLS/RT aus der Playlist)? Dann nicht die ganze Seite neu aufbauen.
    if (before && !force) { const d = diffPaths(before, S.draft); if (d.length && d.every(p => isLivePath(p))) return false; }
    return true;               // Seite neu zeichnen
  }
  // Draft behält Bearbeitungen, aber die live gesteuerten HF-Werte folgen dem Server
  if (!S.rfDragging) for (const k of RF_LIVE) if (!S.rfIntent.has(k)) S.draft.rf[k] = S.server.rf[k];
  S.serverChangedWhileDirty = true;
  return false;
}

/** HF-Livefelder (Kanal, Frequenz, Versatz, Dämpfung, Senden) */
export { RF_LIVE };
/** Markiert HF-Livefelder eines geladenen Presets/Testplans als Absicht (werden beim Übernehmen per /api/rf gesetzt) */
export function markRfIntent(cfg) {
  S.rfIntent.clear();
  for (const k of ['channel', 'freq_hz', 'offset_hz']) if (JSON.stringify(cfg.rf[k]) !== JSON.stringify(S.server?.rf[k])) S.rfIntent.add(k);
  for (const k of ['atten_db', 'enabled']) cfg.rf[k] = S.server?.rf[k] ?? cfg.rf[k];   // Sicherheit: nie aus Dateien
  return cfg;
}

// ---------- Validierung & Anzeige ----------
export function edited(path, rerender = false) {
  S.errors = validateConfig(S.draft);
  if (rerender) S.render(true);
  else refreshDecor();
}
export function refreshDecor() {
  S.errors = validateConfig(S.draft);
  updateFieldErrors();
  S.watchers.forEach(f => { try { f(); } catch (e) { console.warn(e); } });
  updateApplyBar();
  S.liveUpdate();
}

export function errorsFor(prefix) {
  return S.errors.filter(e => e.path === prefix || e.path.startsWith(prefix + '.'));
}
function updateFieldErrors() {
  for (const f of $$('.field[data-path]')) {
    const p = f.dataset.path;
    const errs = S.errors.filter(e => e.path === p || (f.dataset.deep && e.path.startsWith(p + '.')));
    f.classList.toggle('invalid', errs.length > 0);
    const msg = f.querySelector(':scope > .msg');
    if (msg) { if (errs.length) msg.textContent = errs[0].msg; else if (f._hintNode) msg.replaceChildren(f._hintNode); else msg.textContent = f.dataset.hint || ''; }
  }
}

// ---------- Automatisch übernehmen ----------
// Gültige Änderungen werden nach einer kurzen Ruhepause selbst übernommen; Strukturänderungen starten die Kette ohne Rückfrage neu.
// Abschaltbar in der Übernehmen-Leiste (localStorage dablab.autoApply).
const AUTO_DELAY_LIVE = 800, AUTO_DELAY_RESTART = 2000;
let autoTimer = null, autoSig = null, autoFailedSig = null;
export const autoApplyOn = () => load('autoApply', true) !== false;
export function setAutoApply(on) { save('autoApply', !!on); updateApplyBar(); }
function scheduleAutoApply(ch, errs, restart) {
  if (!autoApplyOn() || errs || !ch.all.length || S.busy.apply || S.serverChangedWhileDirty) {
    clearTimeout(autoTimer); autoTimer = null; autoSig = null; return;
  }
  // Statusabfragen rufen updateApplyBar ständig auf: Zeitgeber nur bei echter Änderung des Entwurfs neu starten
  const sig = JSON.stringify(S.draft);
  if (sig === autoFailedSig) return;                     // abgelehnter Entwurf: erst nach weiterer Änderung erneut
  if (autoTimer && sig === autoSig) return;
  clearTimeout(autoTimer); autoSig = sig;
  autoTimer = setTimeout(() => {
    autoTimer = null; autoSig = null;
    if (S.rfDragging || S.busy.apply) { updateApplyBar(); return; }
    applyConfig({ auto: true });
  }, restart ? AUTO_DELAY_RESTART : AUTO_DELAY_LIVE);
}

export function updateApplyBar() {
  const bar = document.getElementById('applybar');
  if (!bar || !S.draft) return;
  const ch = changes();
  const errs = S.errors.length;
  const running = txRunning();
  const restart = running && ch.structural.length > 0;
  const auto = autoApplyOn();
  const tg = bar.querySelector('[data-testid=apply-auto]');
  if (tg) tg.checked = auto;
  bar.hidden = ch.all.length === 0 && !S.serverChangedWhileDirty;
  document.body.classList.toggle('has-applybar', !bar.hidden);
  bar.classList.toggle('restart', restart);
  bar.classList.toggle('haserr', errs > 0);
  const btn = bar.querySelector('[data-testid=apply]');
  btn.disabled = errs > 0 || ch.all.length === 0;
  btn.querySelector('span').textContent = restart ? T('Übernehmen (Neustart)') : running ? T('Live übernehmen') : T('Übernehmen');
  bar.querySelector('.ab-count').textContent = ch.all.length === 1 ? T('1 Änderung') : T('{n} Änderungen', { n: ch.all.length });
  bar.querySelector('.ab-kind').textContent = errs ? T('{n} Fehler – bitte korrigieren', { n: errs }) :
    auto && !S.serverChangedWhileDirty ? (S.busy.apply ? T('wird übernommen …') : restart ? T('wird gleich automatisch übernommen – Kette startet neu') : T('wird gleich automatisch übernommen')) :
    restart ? T('Strukturänderung: Kette startet neu') : running ? T('ohne Neustart anwendbar') : T('Sender aus – wird gespeichert');
  bar.querySelector('.ab-server').hidden = !S.serverChangedWhileDirty;
  const list = bar.querySelector('.ab-errlist');
  list.replaceChildren(...S.errors.slice(0, 8).map(e => h('li', null, e.msg)));
  scheduleAutoApply(ch, errs, restart);
}

// ---------- Formular-Bausteine ----------
export function field(label, control, o = {}) {
  const f = h('div.field' + (o.wide ? '.wide' : '') + (o.cls ? '.' + o.cls : ''), null,
    label || o.extra ? h('label.lbl', null, label, o.extra || null) : null, control, h('small.msg', null, o.hint || ''));
  if (o.path) f.dataset.path = o.path;
  if (o.deep) f.dataset.deep = '1';
  if (o.hint instanceof Node) { f._hintNode = o.hint; f.dataset.hint = o.hint.textContent; }   // Hinweis mit Elementen (Flaggen, „untested“)
  else if (o.hint) f.dataset.hint = o.hint;
  return f;
}

export function input(path, o = {}) {
  const v = getPath(S.draft, path);
  const el = h('input' + (o.mono ? '.mono' : ''), {
    type: o.type || 'text', tid: o.tid, placeholder: o.ph, min: o.min, max: o.max, step: o.step,
    maxLength: o.maxlength, spellcheck: false, autocomplete: 'off', inputMode: o.inputmode, disabled: o.disabled || null,
  });
  el.value = o.fmt ? o.fmt(v) : (v ?? '');
  el.dataset.path = path;
  el.addEventListener('input', () => {
    let val = el.value;
    if (o.type === 'number') val = el.value === '' ? null : Number(el.value);
    if (o.parse) val = o.parse(el.value);
    setPath(S.draft, path, val);
    o.oninput?.(val);
    edited(path, o.rerender);
  });
  if (o.hex) el.addEventListener('blur', () => {
    const s = el.value.trim().replace(/^0x/i, '');
    if (/^[0-9a-f]+$/i.test(s) && s.length <= o.hex) {
      el.value = '0x' + s.toUpperCase().padStart(o.hex, '0');
      setPath(S.draft, path, el.value); edited(path);
    }
  });
  return el;
}

export function textarea(path, o = {}) {
  const v = getPath(S.draft, path);
  const el = h('textarea', { tid: o.tid, rows: o.rows || 3, placeholder: o.ph, spellcheck: false });
  el.value = o.fmt ? o.fmt(v) : (v ?? '');
  el.addEventListener('input', () => {
    setPath(S.draft, path, o.parse ? o.parse(el.value) : el.value);
    edited(path);
  });
  return el;
}

export function select(path, options, o = {}) {
  const cur = getPath(S.draft, path);
  const el = h('select', { tid: o.tid },
    options.map(opt => opt.group
      ? h('optgroup', { label: opt.group }, opt.items.map(x => h('option', { value: String(x.v), disabled: x.disabled }, x.label)))
      : h('option', { value: String(opt.v), disabled: opt.disabled }, opt.label)));
  el.value = String(cur);
  if (el.selectedIndex < 0 && o.missing !== false) {
    el.prepend(h('option', { value: String(cur) }, T('{v} (ungültig)', { v: cur })));
    el.value = String(cur);
  }
  el.addEventListener('change', () => {
    let v = el.value;
    if (o.num) v = Number(v);
    if (o.parse) v = o.parse(v);
    setPath(S.draft, path, v);
    o.onchange?.(v);
    edited(path, o.rerender !== false);
  });
  return el;
}

export function toggle(path, label, o = {}) {
  const cb = h('input', { type: 'checkbox', tid: o.tid, role: 'switch' });
  cb.checked = !!getPath(S.draft, path);
  cb.addEventListener('change', () => {
    setPath(S.draft, path, cb.checked);
    o.onchange?.(cb.checked);
    edited(path, o.rerender);
  });
  return h('label.switch' + (o.cls ? '.' + o.cls : ''), { title: o.title }, cb, h('span.track', null, h('i')), h('span.sw-label', null, label));
}

export function segmented(path, options, o = {}) {
  const cur = getPath(S.draft, path);
  const wrap = h('div.seg', { role: 'radiogroup', tid: o.tid });
  for (const opt of options) {
    const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(String(cur) === String(opt.v)), class: String(cur) === String(opt.v) ? 'on' : null,
      tid: o.tid ? `${o.tid}-${opt.v}` : null, disabled: opt.disabled, title: opt.title },
      opt.icon ? icon(opt.icon) : null, opt.label);
    b.addEventListener('click', () => {
      setPath(S.draft, path, opt.v);
      o.onchange?.(opt.v);
      edited(path, o.rerender !== false);
    });
    wrap.append(b);
  }
  return wrap;
}

export function card(title, ic, body, o = {}) {
  return h('section.card' + (o.cls ? '.' + o.cls : ''), { tid: o.tid },
    title ? h('header.card-h', null, h('h3', null, ic ? icon(ic) : null, title), o.badge || null, o.actions ? h('div.card-actions', null, o.actions) : null) : null,
    h('div.card-b', null, body));
}

export function counter(get, max, unit = '') {
  const el = h('span.counter');
  const upd = () => { const n = get(); el.textContent = `${n}/${max}${unit}`; el.classList.toggle('over', n > max); el.classList.toggle('full', n === max); };
  upd(); S.watchers.push(upd);
  return el;
}

// ---------- Aktionen ----------
export async function refreshState() {
  const st = await api('GET', '/api/state');
  if (ingestState(st)) S.render(true);      // Entwurf wurde ersetzt → Seite neu binden (sonst zeigen Felder auf einen alten Entwurf)
  return st;
}

export function ingestState(d) {
  if (!d || typeof d !== 'object') return;
  for (const k of ['tx', 'pluto', 'mac', 'runner', 'sweep', 'check', 'monitor', 'rf', 'jobs', 'devices', 'onboarding']) if (k in d) S.state[k] = d[k];
  if (d.log) mergeLog(d.log);
  let redraw = false;
  if (d.config) redraw = setServerConfig(d.config);
  return redraw;
}

const logKey = l => `${l.t ?? ''}|${l.lvl ?? ''}|${l.msg ?? ''}`;
export function normLog(l) {
  if (typeof l === 'string') { try { l = JSON.parse(l); } catch { l = { msg: l }; } }
  return { t: l.t ?? l.time ?? null, lvl: (l.lvl || l.level || 'info').toLowerCase(), msg: l.msg ?? l.message ?? JSON.stringify(l), src: l.src || l.source || '' };
}
export function mergeLog(lines) {
  if (!Array.isArray(lines)) lines = [lines];
  const inc = lines.map(normLog);
  if (!inc.length) return;
  const lastKey = S.log.length ? logKey(S.log[S.log.length - 1]) : null;
  let start = 0;
  if (lastKey) {
    let idx = -1;
    for (let i = inc.length - 1; i >= 0; i--) if (logKey(inc[i]) === lastKey) { idx = i; break; }
    if (idx >= 0) start = idx + 1;
    else if (inc.length > 1 && S.log.length && inc.every(l => S.log.some(x => logKey(x) === logKey(l)))) start = inc.length;
  }
  for (let i = start; i < inc.length; i++) S.log.push(inc[i]);
  if (S.log.length > 3000) S.log.splice(0, S.log.length - 3000);
}

export async function rfUpdate(patch, { silent = false } = {}) {
  // Entwurf steht auf einem anderen Standard als der Server (Wechsel noch nicht übernommen): Kanal/Frequenz nur im
  // Entwurf vormerken – sie gehen mit dem Standardwechsel an den Server, sonst prüft er sie gegen den alten Standard
  const geo = ['channel', 'freq_hz', 'offset_hz'];
  if (S.server && standardOf(S.draft) !== standardOf(S.server) && Object.keys(patch).every(k => geo.includes(k))) {
    Object.assign(S.draft.rf, patch);
    for (const k of Object.keys(patch)) S.rfIntent.add(k);
    refreshDecor();
    return true;
  }
  try {
    const r = await api('POST', '/api/rf', patch);
    const applied = { ...patch };
    if (r && r.rf && typeof r.rf === 'object') for (const k of Object.keys(patch)) if (k in r.rf) applied[k] = r.rf[k];
    Object.assign(S.draft.rf, applied);
    if (S.server) Object.assign(S.server.rf, applied);
    if ('freq_hz' in applied) for (const c of [S.draft, S.server]) if (c && c.fm && (c.mode === 'fm')) c.fm.freq_mhz = Math.round(applied.freq_hz / 1e3) / 1e3;
    for (const k of Object.keys(applied)) { S.rfPending[k] = { v: applied[k], until: Date.now() + 4000 }; S.rfIntent.delete(k); }
    if (r && r.config) setServerConfig(r.config);
    if (!silent) toast(rfMsg(applied), 'ok', 2000);
    S.liveUpdate();
    refreshState().then(() => S.liveUpdate()).catch(() => {});   // serverRaw auf den bestätigten Stand bringen
    return true;
  } catch (e) {
    toast(T('HF-Änderung fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000);
    Object.assign(S.draft.rf, Object.fromEntries(Object.keys(patch).map(k => [k, S.server?.rf[k]])));
    S.liveUpdate();
    return false;
  }
}
function rfMsg(p) {
  if ('enabled' in p) return p.enabled ? T('Sender eingeschaltet') : T('Sender stumm geschaltet');
  if ('atten_db' in p) return T('Dämpfung {db} dB', { db: fmtDb(p.atten_db) });
  if ('channel' in p) return T('Kanal {ch}', { ch: p.channel });
  if ('offset_hz' in p) return T('Versatz {hz} Hz', { hz: p.offset_hz });
  return T('HF aktualisiert');
}

/** Sicherheitsabfrage beim Unterschreiten von 40 dB Dämpfung */
export async function confirmAtten(newDb, oldDb) {
  if (newDb >= ATTEN_SAFE || (oldDb != null && oldDb < ATTEN_SAFE && newDb >= oldDb)) return true;
  return dialog({
    title: T('Hohe Sendeleistung'), danger: true, tid: 'confirm-atten', ok: T('{db} dB setzen', { db: fmtDb(newDb) }),
    body: [h('p', null, T('Die Dämpfung soll auf {db} dB sinken (unter {safe} dB).', { db: fmtDb(newDb), safe: ATTEN_SAFE })),
      h('p', null, h('strong', null, T('Nur senden, wenn der Ausgang abgeschirmt ist: ')), T('Messkammer, Kabel mit Dämpfungsglied oder Dummy-Load. Abstrahlung über eine Antenne ist ohne Frequenzzuteilung nicht zulässig.'))],
  });
}

let attenBusy = false;
export async function setAtten(db) {
  if (attenBusy || !isFinite(db)) return false;
  db = Math.round(Math.min(ATTEN_MAX, Math.max(0, db)) * 4) / 4;
  const old = S.server?.rf.atten_db;
  if (db === old) return true;
  attenBusy = true;
  try {
    if (!(await confirmAtten(db, old))) { S.draft.rf.atten_db = old; S.liveUpdate(); return false; }
    return await rfUpdate({ atten_db: db });
  } finally { attenBusy = false; }
}

export async function setChannel(ch, freq) {
  const hz = ch === 'custom' || freq != null ? freq : channelHz(ch);
  return rfUpdate({ channel: ch, freq_hz: hz });
}

/** /api/tx/start; läuft auf dem Pluto ein Sender, den dieser Server nicht gestartet hat (409 foreign_mod),
 *  fragt das Dashboard, ob er beendet und übernommen werden soll (sonst käme der Nutzer nicht weiter). */
export async function apiTxStart() {
  try { return await api('POST', '/api/tx/start'); }
  catch (e) {
    if (e.status !== 409 || e.data?.code !== 'foreign_mod') throw e;
    const ok = await dialog({ title: T('Pluto ist belegt'), tid: 'confirm-takeover', ok: T('Beenden und übernehmen'), danger: true,
      body: [h('p', null, T('Auf dem Pluto läuft ein Sender, den Muxwright nicht selbst gestartet hat (z. B. aus einer früheren Sitzung oder von Hand):')),
        h('pre.small', null, e.data.cmd || ''), h('p', null, T('Soll Muxwright ihn beenden und den Pluto übernehmen?'))] });
    if (!ok) throw new Error(T('Abgebrochen – der andere Sender läuft weiter.'));
    return api('POST', '/api/tx/start', { force: true });
  }
}

export async function txStart() {
  const ch = changes();
  if (S.errors.length) { toast(T('Konfiguration enthält Fehler – Start nicht möglich.'), 'error'); return; }
  if (ch.all.length) { if (!(await applyConfig({ quiet: true, noRestart: true }))) return; }
  try { S.busy.start = true; S.liveUpdate(); await apiTxStart(); toast(T('Kette wird gestartet (Pluto stumm)'), 'ok'); }
  catch (e) { toast(T('Start fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000); }
  finally { S.busy.start = false; refreshState().then(() => S.liveUpdate()).catch(() => {}); }
}
export async function txStop() {
  try { await api('POST', '/api/tx/stop'); toast(T('Kette gestoppt'), 'ok'); }
  catch (e) { toast(T('Stopp fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000); }
  refreshState().then(() => S.liveUpdate()).catch(() => {});
}
export async function emergencyStop() {
  const calls = [api('POST', '/api/tx/stop'), api('POST', '/api/sweep/stop').catch(() => null)];
  try { await calls[0]; toast(T('NOT-AUS ausgeführt – Kette gestoppt, Pluto −89,75 dB'), 'error', 6000); }
  catch (e) {
    // Zweiter Versuch: wenigstens stumm schalten
    try { await api('POST', '/api/rf', { enabled: false, atten_db: ATTEN_MAX }); toast(T('NOT-AUS: Stopp fehlgeschlagen, Pluto stumm geschaltet'), 'error', 8000); }
    catch { toast(T('NOT-AUS fehlgeschlagen: Server nicht erreichbar! Pluto abziehen.'), 'error', 15000); }
  }
  await Promise.allSettled(calls);
  refreshState().then(() => S.liveUpdate()).catch(() => {});
}

export async function sendLive(payload, okMsg) {
  try { await api('POST', '/api/live', payload); if (okMsg) toast(okMsg, 'ok', 2200); return true; }
  catch (e) { toast(T('Live-Änderung fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000); return false; }
}

export async function applyConfig({ quiet = false, noRestart = false, auto = false } = {}) {
  clearTimeout(autoTimer); autoTimer = null; autoSig = null;
  S.errors = validateConfig(S.draft);
  if (S.errors.length) { toast(T('{n} Fehler in der Konfiguration', { n: S.errors.length }), 'error'); updateApplyBar(); return false; }
  const ch = changes();
  const running = txRunning();
  if (running && ch.structural.length && !noRestart && !auto) {
    const ok = await dialog({ title: T('Übernehmen mit Neustart'), tid: 'confirm-restart', ok: T('Übernehmen (Neustart)'),
      body: [h('p', null, T('Diese Änderungen betreffen die Struktur des Multiplex. Die Kette wird neu gestartet; der Empfänger verliert kurz das Signal.')),
        h('ul.mono.small', null, ch.structural.slice(0, 12).map(p => h('li', null, p)), ch.structural.length > 12 ? h('li', null, '…') : null)] });
    if (!ok) return false;
  }
  const cfg = structuredClone(S.draft);
  const intent = Object.fromEntries([...S.rfIntent].map(k => [k, cfg.rf[k]]));
  try {
    S.busy.apply = true; updateApplyBar();
    // HF-Livewerte (Kanal, Frequenz, Versatz, Dämpfung, Senden) nie aus dem Entwurf, sondern aktuell vom Server
    let live = S.server?.rf || cfg.rf;
    try { const st = await api('GET', '/api/state'); if (st?.config?.rf) live = st.config.rf; } catch { /* Fallback: letzter bekannter Serverstand */ }
    for (const k of RF_LIVE) cfg.rf[k] = k in intent ? intent[k] : live[k];
    if (cfg.mode === 'fm' && cfg.fm) cfg.fm.freq_mhz = Math.round(cfg.rf.freq_hz / 1e3) / 1e3;   // SPEC: fm.freq_mhz spiegelt rf.freq_hz
    const r = await api('PUT', '/api/config', cfg);
    const prev = S.server;
    setServerConfig(cfg, { force: true });
    if (Object.keys(intent).length) {
      const patch = { ...intent };
      if ('channel' in patch && !('freq_hz' in patch)) patch.freq_hz = cfg.rf.freq_hz;
      await rfUpdate(patch);
    }
    if (running && !noRestart && (ch.structural.length || r?.restart_required)) {
      await api('POST', '/api/tx/stop');
      await apiTxStart();
      toast(auto ? T('Automatisch übernommen – Kette neu gestartet') : T('Übernommen – Kette neu gestartet'), 'ok');
    } else {
      if (running) await pushLiveChanges(prev, cfg, ch.live);
      if (!quiet) toast(running ? T('Live übernommen') : T('Konfiguration gespeichert'), 'ok');
    }
    S.render(true);
    return true;
  } catch (e) {
    const list = e.data?.errors;
    if (auto) autoFailedSig = JSON.stringify(S.draft);
    toast(T('Übernehmen fehlgeschlagen: {msg}', { msg: e.message }), 'error', 8000);
    if (Array.isArray(list)) S.errors = list.map(x => typeof x === 'string' ? { path: 'server', msg: x } : { path: x.path || 'server', msg: x.msg || x.message || JSON.stringify(x) });
    updateApplyBar();
    return false;
  } finally { S.busy.apply = false; }
}

async function pushLiveChanges(prev, cfg, paths) {
  const done = new Set();
  if (cfg.mode === 'pal') {
    if (paths.some(p => /^pal\.image\.(text|text2)$/.test(p))) await sendLive({ pal: { text: cfg.pal.image.text, text2: cfg.pal.image.text2 } });
    return;
  }
  // UKW: gebündelt nach ctl-Schlüsseln (SPEC: rds / fm / second_carrier)
  if (typeof cfg.mode === 'string' && cfg.mode === 'fm') {
    const rds = {}, fm = {}, sc = {};
    for (const p of paths) {
      let m;
      if ((m = p.match(/^fm\.rds\.(ps|rt|rtplus|ta|tp|pty|ms|ct|ptyn|af|af_method|lps|eon|rt_from_tags|level_khz)(?:\.|$)/))) {
        const k = m[1];
        if (k === 'level_khz') fm.rds_level_khz = cfg.fm.rds.level_khz;
        else if (k === 'rt_from_tags') rds.rt_auto = cfg.fm.rds.rt_from_tags;
        else rds[k] = cfg.fm.rds[k];
      } else if ((m = p.match(/^fm\.(deviation_khz|pilot_khz|mpx_power_dbr|audio_gain_db)$/))) fm[m[1]] = cfg.fm[m[1]];
      else if ((m = p.match(/^fm\.second_carrier\.(level_db|enable)$/))) sc[m[1]] = cfg.fm.second_carrier[m[1]];
    }
    if (Object.keys(rds).length) await sendLive({ rds });
    if (Object.keys(fm).length) await sendLive({ fm });
    if (Object.keys(sc).length) await sendLive({ second_carrier: sc });
    return;
  }
  for (const p of paths) {
    let m;
    if ((m = p.match(/^ensemble\.(label|shortlabel)$/)) && !done.has('ens')) {
      done.add('ens'); await sendLive({ label: { ensemble: true, label: cfg.ensemble.label, shortlabel: cfg.ensemble.shortlabel } });
    } else if ((m = p.match(/^services\.(\d+)\.(label|shortlabel)$/)) && !done.has('l' + m[1])) {
      done.add('l' + m[1]); const s = cfg.services[m[1]];
      await sendLive({ label: { service: s.key, label: s.label, shortlabel: s.shortlabel } });
    } else if ((m = p.match(/^services\.(\d+)\.pad\.dls\.text$/))) {
      const s = cfg.services[m[1]]; await sendLive({ dls: { service: s.key, text: s.pad.dls.text } });
    }
  }
}

export async function discard() {
  S.rfIntent.clear();
  S.draft = structuredClone(S.server);
  S.serverChangedWhileDirty = false;
  S.errors = validateConfig(S.draft);
  S.render(true);
  toast(T('Änderungen verworfen'), 'info', 2000);
}

let mediaSeq = 0;
export async function loadMedia() {
  const seq = ++mediaSeq;
  try {
    const r = await api('GET', '/api/media');
    if (seq !== mediaSeq) return S.media;             // überholte Antwort ignorieren
    const arr = Array.isArray(r) ? r : (r?.files || r?.media || []);
    S.media = arr.map(x => {
      const o = typeof x === 'string' ? { name: x.split('/').pop(), path: x.includes('/') ? x : `media/${x}` } : { ...x };
      o.name = o.name || (o.path || o.file || '').split('/').pop();
      o.path = o.path || o.file || `media/${o.name}`;
      o.duration_s = o.duration_s ?? o.duration ?? null;
      o.format = o.format || o.codec || (o.name.includes('.') ? o.name.split('.').pop().toLowerCase() : '');
      o.kind = /\.(jpe?g|png|gif|webp|svg)$/i.test(o.name) || ['slide', 'logo', 'image'].includes(o.kind) ? 'image'
        : /\.(mp3|mp2|wav|flac|ogg|oga|m4a|aac|opus|wma)$/i.test(o.name) || o.kind === 'audio' ? 'audio'
        : /\.(mp4|ts|m2ts|mkv|mpg|mpeg|mov|webm|avi)$/i.test(o.name) || o.kind === 'video' ? 'video' : 'other';
      return o;
    });
  } catch { S.media = []; }
  return S.media;
}

export async function upload(file, kind) {
  const name = file.name.replace(/[^\w.\-äöüÄÖÜß]+/g, '_');
  const r = await api('POST', `/api/upload?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`, file);
  const path = r?.path || r?.file || `media/${name}`;
  previewCache.set(path, URL.createObjectURL(file));
  await loadMedia();
  return path;
}
export const previewCache = new Map();

// ---------- Playlists & Jetzt läuft ----------
let plSeq = 0;
export async function loadPlaylists() {
  const seq = ++plSeq;
  try {
    const r = await api('GET', '/api/playlists');
    if (seq !== plSeq) return S.playlists;            // überholte Antwort ignorieren
    const arr = Array.isArray(r) ? r : (r?.playlists || []);
    S.playlists = arr.map(x => typeof x === 'string' ? { name: x.replace(/\.json$/, ''), count: null, missing: 0 } : { ...x, name: (x.name || '').replace(/\.json$/, ''), count: x.count ?? (x.items ? x.items.length : null), missing: x.missing || 0 });
  } catch { S.playlists = []; }
  return S.playlists;
}

/** Jetzt-läuft-Eintrag eines Dienstes (tx.now_playing als Objekt je Dienst, Liste oder Einzelobjekt) */
export function nowPlaying(key) {
  const np = S.state.tx?.now_playing;
  if (!np || typeof np !== 'object') return null;
  if (Array.isArray(np)) return (key ? np.find(x => x && x.service === key) : np[0]) || null;
  if ('service' in np || 'title' in np) return !key || np.service === key ? np : null;
  const v = key ? np[key] : Object.values(np)[0];
  return v && typeof v === 'object' ? v : null;
}
export function playlistCtl(service, action, index) {
  const p = { service, action };
  if (index != null) p.index = index;
  return sendLive({ playlist: p }, { next: T('Nächster Titel'), prev: T('Vorheriger Titel'), jump: T('Titel {n}', { n: index + 1 }) }[action]);
}
