// Einrichtung beim ersten Start: Gerät & Firmware prüfen, Betriebsart wählen (Labor / Amateurfunk), Rechtliches bestätigen.
// Ohne abgeschlossene Einrichtung lehnt der Server das Einschalten der HF ab (pc/server.py tx_permission). Texte: onboarding_i18n.js
import { h, icon, api, toast } from './ui.js';
import { S, refreshState, edited } from './core.js';
import * as STD from './standards.js';
import { LANGS, TXT, pickLang, saveLang, fmt } from './onboarding_i18n.js';
import { hwPanel, hwText } from './extras.js';
import { COMP } from './comp_i18n.js';
import { creditsPanel } from './credits.js';
import { setLang, T } from './i18n.js';
import { fwPanel } from './firmware.js';

const NEED = { lab: ['shielded', 'no_broadcast_radiation', 'terms', 'privacy', 'codecs'], ham: ['shielded', 'no_broadcast_radiation', 'terms', 'privacy', 'codecs', 'ham_license', 'ham_rules'] };

/** Gerätezustand aus /api/state als Prüfliste */
function checks(t) {
  const st = S.state || {}, pl = st.pluto || {}, dev = st.rf?.device || {}, rn = st.runner || st.mac || {};
  const fwOk = /dablab/.test(pl.fw || '');
  const fw = pl.fw || t.unknown;
  return [
    { k: 'pluto', ok: !!pl.connected, label: t.c_pluto, detail: pl.connected ? `${pl.model || 'ADALM-Pluto'} · ${pl.uri || ''}` : t.c_pluto_no },
    { k: 'fw', ok: fwOk, warn: pl.connected && !fwOk, label: t.c_fw, detail: !pl.connected ? t.c_fw_wait : fwOk ? fmt(t.c_fw_ok, { fw }) : fmt(t.c_fw_no, { fw }) },
    { k: 'agent', ok: dev.reachable !== false && fwOk, warn: fwOk && dev.reachable === false, label: t.c_agent, detail: dev.reachable === false ? t.c_agent_no : fwOk ? t.c_agent_ok : t.c_agent_fw },
    { k: 'runner', ok: !!rn.reachable, warn: !rn.reachable, label: t.c_runner, detail: rn.reachable ? t.c_runner_ok : t.c_runner_no },
  ];
}

/** Schritte: Willkommen, Gerät, PC-Leistung, Betriebsart, Rechtliches, Fertig */
/** Schritte: Willkommen, Gerät, PC-Leistung, Betriebsart, Rechtliches, Komponenten, Fertig */
const tx = lang => TXT[lang] || TXT.en;   // Einrichtungstexte; noch nicht übersetzte Sprachen → Englisch
const stepsOf = lang => { const s = tx(lang).steps; return [s[0], s[1], hwText(lang).step, s[2], s[3], (COMP[lang] || COMP.en).step, s[4]]; };
const acceptText = (t, lang, k) => k === 'codecs' ? (COMP[lang] || COMP.en).legal : t.accept[k];

export function onboardingNeeded() { return S.state?.onboarding && !S.state.onboarding.done; }

export function openOnboarding() {
  if (document.querySelector('dialog.onb')) return;
  const prev = S.state?.onboarding || {};
  const st = { step: 0, lang: pickLang(), mode: prev.mode || 'lab',
    callsign: prev.callsign || S.draft?.dvbs?.callsign || '', accept: new Set(), busy: false };
  const mandatory = !prev.done;
  const dlg = h('dialog.modal.onb', { tid: 'onboarding' });
  dlg.addEventListener('cancel', e => { if (mandatory) e.preventDefault(); });

  const render = () => {
    const t = { ...tx(st.lang), steps: stepsOf(st.lang) }, ch = checks(t), last = t.steps.length - 1;
    const langSel = h('select', { tid: 'onb-lang', 'aria-label': t.lang }, Object.entries(LANGS).map(([k, n]) => h('option', { value: k }, n)));
    langSel.value = st.lang;
    langSel.addEventListener('change', () => setLang(langSel.value));   // gilt fürs ganze Dashboard (lädt neu)
    const head = h('div.onb-head', null,
      h('div.onb-title', null, h('h2', null, t.title), h('label.onb-lang', null, icon('flag'), langSel)),
      h('ol.onb-steps', null, t.steps.map((x, i) => h('li', { class: i === st.step ? 'on' : i < st.step ? 'done' : null }, h('span', null, String(i + 1)), x))));
    let body, canNext = true;
    let nextBtn = null;
    if (st.step === 0) {
      body = [h('h3', null, t.welcome_h), h('p', null, t.welcome_1), h('p', null, t.welcome_2), creditsPanel(st.lang)];
    } else if (st.step === 1) {
      body = [h('h3', null, t.dev_h),
        h('ul.onb-checks', { tid: 'onb-checks' }, ch.map(c => h('li', { class: c.ok ? 'ok' : c.warn ? 'warn' : 'bad', tid: `onb-check-${c.k}` },
          icon(c.ok ? 'check' : c.warn ? 'alert' : 'x'), h('div', null, h('b', null, c.label), h('small', null, c.detail))))),
        h('div.inrow', null, h('button.btn.ghost', { type: 'button', tid: 'onb-recheck', onclick: async () => { await refreshState().catch(() => {}); render(); } }, icon('refresh'), t.recheck),
          h('span.muted.small', null, t.dev_hint)),
        ch.find(c => c.k === 'pluto')?.ok && !ch.find(c => c.k === 'fw')?.ok ? fwPanel(st.lang, { compact: true }) : null];
    } else if (st.step === 2) {
      const ht = hwText(st.lang);
      body = [h('h3', null, ht.h), h('p.muted', null, ht.intro), hwPanel(st.lang, { compact: true })];
    } else if (st.step === 3) {
      const opt = (v, ic, title, d) => h('button.onb-mode', { type: 'button', class: st.mode === v ? 'on' : null, tid: `onb-mode-${v}`, 'aria-pressed': String(st.mode === v), onclick: () => { st.mode = v; render(); } },
        icon(ic), h('b', null, title), h('small', null, d));
      const cs = h('input.mono', { tid: 'onb-callsign', placeholder: 'DL1ABC', maxLength: 12, value: st.callsign, spellcheck: false, autocomplete: 'off' });
      cs.addEventListener('input', () => { st.callsign = cs.value.toUpperCase().trim(); cs.value = st.callsign; if (nextBtn) nextBtn.disabled = !STD.DATV.CALLSIGN_RE.test(st.callsign); });
      body = [h('h3', null, t.mode_h),
        h('div.onb-modes', null, opt('lab', 'shield', t.mode_lab, t.mode_lab_d), opt('ham', 'antenna', t.mode_ham, t.mode_ham_d)),
        st.mode === 'ham' ? h('label.field', null, h('span.lbl', null, t.callsign), cs, h('small.msg', null, t.callsign_hint)) : null];
      canNext = st.mode === 'lab' || STD.DATV.CALLSIGN_RE.test(st.callsign);
    } else if (st.step === 4) {
      const keys = NEED[st.mode];
      body = [h('h3', null, t.legal_h), h('p.muted.small', null, t.legal_hint),
        h('div.onb-legal', null, keys.map(k => {
          const cb = h('input', { type: 'checkbox', tid: `onb-accept-${k}` });
          cb.checked = st.accept.has(k);
          cb.addEventListener('change', () => { cb.checked ? st.accept.add(k) : st.accept.delete(k); if (nextBtn) nextBtn.disabled = !keys.every(x => st.accept.has(x)); });
          const [ti, tx] = acceptText(t, st.lang, k);
          return h('label.onb-item', null, cb, h('div', null, h('b', null, ti), h('p', null, tx)));
        }))];
      canNext = keys.every(x => st.accept.has(x));
    } else if (st.step === 5) {
      body = [componentsStep(st.lang, () => render())];
    } else {
      const fw = ch.find(c => c.k === 'fw');
      body = [h('h3', null, t.done_h),
        h('ul.onb-checks', null,
          h('li.ok', null, icon('check'), h('div', null, h('b', null, st.mode === 'ham' ? fmt(t.done_ham, { cs: st.callsign }) : t.done_lab), h('small', null, t.done_legal))),
          h('li', { class: fw.ok ? 'ok' : 'warn' }, icon(fw.ok ? 'check' : 'alert'), h('div', null, h('b', null, fw.ok ? t.done_fw_ok : t.done_fw_no), h('small', null, fw.ok ? t.done_fw_ok_d : t.done_fw_no_d)))),
        h('p.muted.small', null, t.done_hint), creditsPanel(st.lang)];
    }
    const back = h('button.btn.ghost', { type: 'button', tid: 'onb-back', disabled: st.step === 0 || st.busy, onclick: () => { st.step--; render(); } }, t.back);
    nextBtn = h('button.btn.primary', { type: 'button', tid: st.step === last ? 'onb-finish' : 'onb-next', disabled: !canNext || st.busy, onclick: next },
      st.step === last ? t.finish : t.next);
    const close = !mandatory ? h('button.btn.ghost', { type: 'button', tid: 'onb-close', onclick: () => dlg.close() }, t.close) : null;
    dlg.replaceChildren(head, h('div.onb-body', null, body), h('div.modal-actions', null, close, h('span.grow'), back, nextBtn));
  };

  async function next() {
    const t = tx(st.lang);
    if (st.step < stepsOf(st.lang).length - 1) { st.step++; if (st.step === 1) await refreshState().catch(() => {}); render(); return; }
    st.busy = true; render();
    try {
      const fw = (S.state?.pluto || {}).fw || null;
      await api('POST', '/api/onboarding', { mode: st.mode, callsign: st.mode === 'ham' ? st.callsign : '', accept: [...st.accept], lang: st.lang,
        firmware: { fw, ok: /dablab/.test(fw || '') } });
      await refreshState().catch(() => {});
      toast(t.saved, 'ok');
      dlg.close();
      if (st.mode === 'ham') enterHam(st.callsign);
      S.liveUpdate?.();
    } catch (e) {
      toast(t.save_err + e.message, 'error', 8000);
      st.busy = false; render();
    }
  }

  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  render();
  dlg.showModal();
}

// ---------------------------------------------------------------- Schritt „Komponenten“: Nutzer installiert Fremdsoftware selbst
const compState = { data: null, accept: new Set(), poll: null };
function componentsStep(lang, rerender) {
  const c = COMP[lang] || COMP.en;
  const box = h('div.onb-comp', { tid: 'onb-components' }, h('h3', null, c.h), h('p', null, c.intro),
    h('p.alert.small', null, icon('info'), c.patents), h('p.muted.small', null, c.skip + ' ' + c.duration));
  const list = h('div.onb-comp-list');
  box.append(list);
  const draw = () => {
    const d = compState.data;
    if (!d) { list.replaceChildren(h('p.muted', null, '…')); return; }
    const inst = Object.fromEntries(d.components.map(x => [x.key, x.installed]));
    const job = d.job || {};
    const rows = d.components.map(x => {
      const depsOk = (x.needs || []).every(n => inst[n]);
      const cb = h('input', { type: 'checkbox', tid: `comp-accept-${x.key}`, disabled: x.installed || null });
      cb.checked = x.installed || compState.accept.has(x.key);
      cb.addEventListener('change', () => { cb.checked ? compState.accept.add(x.key) : compState.accept.delete(x.key); draw(); });
      const running = job.running && job.key === x.key;
      const btn = h('button.btn.sm' + (x.installed ? '.ghost' : '.primary'), { type: 'button', tid: `comp-install-${x.key}`,
        disabled: x.installed || running || job.running || !depsOk || !compState.accept.has(x.key) || null,
        onclick: async () => {
          try { await api('POST', '/api/setup/install', { component: x.key, accepted: true }); } catch (e) { toast(e.message, 'error', 6000); }
          refresh();
        } }, running ? c.installing : x.installed ? c.installed : c.install);
      const failed = !job.running && job.key === x.key && job.result === 'fail';
      return h('div.onb-comp-item', { class: x.installed ? 'ok' : failed ? 'bad' : null, tid: `comp-${x.key}` },
        h('div.onb-comp-head', null, icon(x.installed ? 'check' : failed ? 'x' : 'download'),
          h('b', null, c.names[x.key] || x.key), h('span.chip-sm', null, x.optional ? c.optional : c.required), h('span.grow'), btn),
        h('p.small', null, c.desc[x.key] || ''),
        h('p.small.muted', null, `${c.license}: ${x.license} · ${c.source}: `,
          /^https?:/.test(x.source) ? h('a', { href: x.source, target: '_blank', rel: 'noopener' }, x.source) : x.source,
          !depsOk && !x.installed ? ` · ${c.needs}: ${(x.needs || []).filter(n => !inst[n]).map(n => c.names[n] || n).join(', ')}` : ''),
        x.installed ? null : h('label.onb-item.small', null, cb, h('span', null, c.accept)),
        failed ? h('p.alert.small', null, icon('alert'), `${c.failed}: ${job.error || ''}`) : null,
        running || failed ? h('pre.onb-comp-log', { tid: `comp-log-${x.key}` }, (job.lines || []).slice(-8).join('\n')) : null);
    });
    const req = d.components.filter(x => !x.optional);
    list.replaceChildren(...[
      d.wsl && !d.wsl.installed ? h('p.alert', { tid: 'comp-wsl-missing' }, icon('alert'), c.wsl_missing) : null,
      ...rows,
      h('div.inrow', null, h('button.btn.ghost.sm', { type: 'button', tid: 'comp-recheck', onclick: () => refresh(true) }, icon('refresh'), c.recheck),
        req.every(x => x.installed) ? h('span.small', { tid: 'comp-all-done' }, icon('check'), ' ', c.all_done) : null)].filter(Boolean));
  };
  const refresh = async (fresh = false) => {
    try { compState.data = await api('GET', '/api/setup' + (fresh ? '?fresh=1' : '')); } catch (e) { toast(e.message, 'error'); }
    if (!box.isConnected && compState.data) return;
    draw();
    clearTimeout(compState.poll);
    if (compState.data?.job?.running) compState.poll = setTimeout(() => refresh(), 1500);
    else if (compState.data?.job?.finished && compState._lastFinished !== compState.data.job.finished) {
      compState._lastFinished = compState.data.job.finished; refresh(true);
    }
  };
  draw();
  refresh(true);
  return box;
}

// ---------------------------------------------------------------- Betriebsart Amateurfunk: Dashboard auf DVB-S2-DATV umstellen
/** Wie Standardwechsel + Schalter „Amateurfunk (DATV)“ auf der DVB-S-Seite; aktiv nach „Übernehmen (Neustart)“. */
function enterHam(callsign) {
  const d = S.draft;
  if (!d) return;
  if (STD.standardOf(d) !== 'dvbs') { STD.setStandard(d, 'dvbs'); S.sel = 0; S.prgSel = 0; }
  const v = d.dvbs;
  v.use = 'ham'; v.callsign = callsign; v.system = 'dvbs2'; v.pilots = false;
  if (!STD.DATV.BANDS[v.ham_band]) v.ham_band = 'qo100';
  if (!v.ham_freq_mhz) v.ham_freq_mhz = STD.DATV.BANDS[v.ham_band].def;
  if (!(v.symbol_rate <= STD.DATV.SR_MAX)) v.symbol_rate = 333000;
  if (!STD.DVBS.FEC_S2[v.modulation]?.includes(v.fec)) v.fec = '2/3';
  d.rf.channel = 'ham'; d.rf.freq_hz = Math.round(Number(v.ham_freq_mhz) * 1e6);
  // DATV: bei 333 kS/s passt nur ein Programm in den Transportstrom → erstes TV-Programm behalten
  if (d.dvb?.programs?.length > 1) { d.dvb.programs = [d.dvb.programs.find(p => p.type === 'tv') || d.dvb.programs[0]]; S.prgSel = 0; }
  STD.fitDatvPrograms(d);
  for (const k of ['channel', 'freq_hz', 'offset_hz']) if (JSON.stringify(d.rf[k]) !== JSON.stringify(S.server?.rf[k])) S.rfIntent.add(k);
  edited('mode', true);
  location.hash = '#/modulation';
  toast(T('Amateurfunk-DATV ({band}) mit Rufzeichen {cs} eingestellt – aktiv nach „Übernehmen (Neustart)“',
    { band: STD.DATV.BANDS[v.ham_band].label, cs: callsign }), 'info', 7000);
}
