// Firmware-Flasher: mitgelieferte Muxwright-Firmware prüfen → Sicherung → RAM-Test → Flashen (Server: pc/flasher.py).
import { h, icon, api, toast } from './ui.js';
import { pickLang } from './onboarding_i18n.js';
import { FW } from './fw_i18n.js';

export const fwText = lang => FW[lang] || FW.en;

/** Flasher-Bedienfeld (Seite „Firmware“ und Einrichtungsschritt „Gerät“) */
export function fwPanel(lang, { compact = false } = {}) {
  const box = h('div.fw-panel', { tid: 'fw-panel' });
  const st = { data: null, confirm: false, poll: null };
  const draw = () => {
    const t = fwText(lang), d = st.data;
    if (!d) { box.replaceChildren(h('p.muted', null, '…')); return; }
    const job = d.job || {};
    const act = (action, img, label, opts = {}) => h('button.btn.sm' + (opts.primary ? '.primary' : opts.danger ? '.danger' : ''), {
      type: 'button', tid: `fw-${action}${img ? '-' + img.id : ''}`, disabled: job.running || opts.disabled || null,
      onclick: async () => {
        try { await api('POST', '/api/firmware', { action, image: img?.id, confirm: action === 'flash' ? st.confirm : undefined }); }
        catch (e) { toast(e.message, 'error', 7000); }
        refresh();
      } }, icon(opts.icon || 'chip'), label);
    const imgs = d.images.map(img => {
      const tested = !!img.ram_tested || img.kind === 'backup';
      return h('div.fw-img', { tid: `fw-img-${img.kind}`, class: img.ok ? 'ok' : 'bad' },
        h('div.onb-comp-head', null, icon(img.ok ? 'check' : 'x'), h('b', null, img.version || img.name),
          h('span.chip-sm', null, img.kind === 'backup' ? t.backup : t.shipped), h('span.grow'),
          img.ok ? act('ram_test', img, t.b_ram, { icon: 'play' }) : null,
          img.ok && !compact ? act('flash', img, t.b_flash, { danger: true, icon: 'upload', disabled: !tested || !st.confirm }) : null),
        h('p.small.muted', null, `${img.name} · ${(img.size / 1048576).toFixed(1)} MiB · SHA-256 ${img.sha256.slice(0, 12)}… · `,
          img.ok ? t.ok : `${t.bad}: ${img.errors.join('; ')}`),
        h('p.small', { tid: `fw-tested-${img.kind}` }, icon(tested ? 'check' : 'info'), ' ', tested ? `${t.tested}${img.ram_tested ? ' · ' + img.ram_tested.at.replace('T', ' ') : ''}` : t.untested));
    });
    const cb = h('input', { type: 'checkbox', tid: 'fw-confirm' });
    cb.checked = st.confirm;
    cb.addEventListener('change', () => { st.confirm = cb.checked; draw(); });
    const phases = Object.entries(t.phases);
    box.replaceChildren(...[
      h('p', null, icon('chip'), ' ', h('b', null, t.running + ': '), h('span.mono', { tid: 'fw-running' }, d.running_fw || t.none)),
      compact ? null : h('p.muted', null, t.intro),
      h('h4', null, t.images), ...imgs,
      compact ? null : h('label.onb-item.small', null, cb, h('span', null, t.confirm)),
      h('div.inrow.wrap', null, act('backup', null, t.b_backup, { icon: 'save', disabled: !d.running_fw }),
        h('span.muted.small', null, t.stop_note)),
      job.action ? h('div.fw-job', { tid: 'fw-job', class: job.running ? 'run' : job.result },
        h('ol.fw-phases', null, phases.map(([k, v]) => h('li', { class: k === job.phase ? 'on' : null }, v))),
        h('p.small', { tid: 'fw-job-result' }, job.running ? t.busy : job.result === 'ok' ? `${t.done} ✓` : `${t.failed}: ${job.error || ''}`),
        h('pre.onb-comp-log', null, (job.lines || []).slice(-10).join('\n'))) : null,
      h('p.small.muted', null, t.dfu_hint, ' ', t.recovery),
      !compact && d.history?.length ? h('details', null, h('summary', null, t.history),
        h('ul.small', { tid: 'fw-history' }, d.history.slice().reverse().map(x => h('li', null, `${x.at.replace('T', ' ')} · ${x.action} · ${x.image || ''} · ${x.ok ? '✓' : '✗ ' + (x.error || '')}`)))) : null].filter(Boolean));
  };
  const refresh = async () => {
    try { st.data = await api('GET', '/api/firmware'); } catch (e) { toast(e.message, 'error'); }
    draw();
    clearTimeout(st.poll);
    if (st.data?.job?.running && box.isConnected !== false) st.poll = setTimeout(refresh, 1500);
  };
  draw();
  refresh();
  return box;
}

export function pageFirmware() {
  const lang = pickLang(), t = fwText(lang);
  return h('div.page-stack', { tid: 'page-firmware' }, h('section.card', null, h('h3', null, icon('chip'), ' ', t.h), fwPanel(lang)));
}
