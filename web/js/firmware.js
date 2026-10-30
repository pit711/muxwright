// Firmware-Flasher: Gerät wählen → ADALM-Pluto: Image prüfen → Sicherung → RAM-Test → Flashen;
// Fishball/PlutoSky 7010: SD-Karten-Image schreiben (Server: pc/flasher.py).
import { h, icon, api, toast } from './ui.js';
import { pickLang } from './onboarding_i18n.js';
import { FW } from './fw_i18n.js';

export const fwText = lang => ({ ...FW.en, ...(FW[lang] || {}) });

const BOARD_KEYS = { pluto: 'board_pluto', fishball7010: 'board_fishball' };

/** Flasher-Bedienfeld (Seite „Firmware“ und Einrichtungsschritt „Gerät“) */
export function fwPanel(lang, { compact = false } = {}) {
  const box = h('div.fw-panel', { tid: 'fw-panel' });
  const st = { data: null, confirm: false, sdConfirm: false, drive: '', poll: null };
  const post = async body => {
    try { await api('POST', '/api/firmware', body); }
    catch (e) { toast(e.message, 'error', 7000); }
    refresh();
  };
  const boardOf = d => d.board || (/tezuka/i.test(d.running_fw || '') ? 'fishball7010' : 'pluto');

  const jobView = (t, job) => {
    const phases = Object.entries(job.action === 'sd_write' ? t.sd_phases : t.phases);
    return h('div.fw-job', { tid: 'fw-job', class: job.running ? 'run' : job.result },
      h('ol.fw-phases', null, phases.map(([k, v]) => h('li', { class: k === job.phase ? 'on' : null }, v))),
      h('p.small', { tid: 'fw-job-result' }, job.running ? t.busy : job.result === 'ok' ? `${t.done} ✓` : `${t.failed}: ${job.error || ''}`),
      h('pre.onb-comp-log', null, (job.lines || []).slice(-10).join('\n')));
  };

  const boardPicker = (t, d, board) => h('div.inrow.wrap', { tid: 'fw-board' },
    h('b', null, t.board + ':'),
    ...Object.keys(d.boards || { pluto: 'dfu' }).map(b => h('label.onb-item.small', null,
      h('input', { type: 'radio', name: 'fw-board', tid: `fw-board-${b}`, checked: b === board || null, disabled: d.job?.running || null,
        onchange: () => post({ action: 'set_board', board: b }) }),
      h('span', null, t[BOARD_KEYS[b]] || b))));

  const plutoView = (t, d, job) => {
    const act = (action, img, label, opts = {}) => h('button.btn.sm' + (opts.primary ? '.primary' : opts.danger ? '.danger' : ''), {
      type: 'button', tid: `fw-${action}${img ? '-' + img.id : ''}`, disabled: job.running || opts.disabled || null,
      onclick: () => post({ action, image: img?.id, confirm: action === 'flash' ? st.confirm : undefined }) }, icon(opts.icon || 'chip'), label);
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
    return [
      compact ? null : h('p.muted', null, t.intro),
      h('h4', null, t.images), ...imgs,
      compact ? null : h('label.onb-item.small', null, cb, h('span', null, t.confirm)),
      h('div.inrow.wrap', null, act('backup', null, t.b_backup, { icon: 'save', disabled: !d.running_fw }),
        h('span.muted.small', null, t.stop_note)),
      job.action && job.action !== 'sd_write' ? jobView(t, job) : null,
      h('p.small.muted', null, t.dfu_hint, ' ', t.recovery)];
  };

  const sdView = (t, d, job, board) => {
    const im = d.sd_images?.[board];
    const drives = d.sd_drives || [];
    if (st.drive && !drives.some(x => x.drive === st.drive)) st.drive = '';
    if (!st.drive && drives.length === 1) st.drive = drives[0].drive;
    const sel = h('select', { tid: 'fw-sd-drive', disabled: job.running || null,
      onchange: e => { st.drive = e.target.value; draw(); } },
      h('option', { value: '' }, drives.length ? '—' : t.sd_none),
      ...drives.map(x => h('option', { value: x.drive, selected: x.drive === st.drive || null },
        `${x.drive} ${x.label || ''} · ${x.fs || '?'} · ${(x.size / 1073741824).toFixed(1)} GB`)));
    const cur = drives.find(x => x.drive === st.drive);
    const fatOk = cur && /^FAT(32)?$/i.test(cur.fs || '');
    const cb = h('input', { type: 'checkbox', tid: 'fw-sd-confirm' });
    cb.checked = st.sdConfirm;
    cb.addEventListener('change', () => { st.sdConfirm = cb.checked; draw(); });
    return [
      h('p.muted', null, t.sd_intro),
      h('p.small', { tid: 'fw-sd-warn' }, icon('info'), ' ', t.sd_warn),
      h('h4', null, t.images),
      im ? h('div.fw-img', { tid: 'fw-sd-img', class: im.ok ? 'ok' : 'bad' },
        h('div.onb-comp-head', null, icon(im.ok ? 'check' : 'x'), h('b', null, im.version || board), h('span.chip-sm', null, t.shipped)),
        h('p.small.muted', null, `${im.files.length} ${t.sd_files} · ${(im.size / 1048576).toFixed(1)} MiB · `,
          im.ok ? t.sd_ok : `${t.bad}: ${im.errors.join('; ')}`))
        : h('p.small', { tid: 'fw-sd-img' }, icon('x'), ' ', t.sd_missing),
      h('div.inrow.wrap', null, h('b', null, t.sd_drive + ':'), sel,
        h('button.btn.sm', { type: 'button', tid: 'fw-sd-rescan', disabled: job.running || null, onclick: refresh }, icon('refresh'), t.sd_rescan)),
      cur && !fatOk ? h('p.small', { tid: 'fw-sd-fs' }, icon('x'), ' ', t.sd_fat.replace('{fs}', cur.fs || '?')) : null,
      h('label.onb-item.small', null, cb, h('span', null, t.sd_confirm)),
      h('div.inrow.wrap', null, h('button.btn.sm.primary', { type: 'button', tid: 'fw-sd-write',
        disabled: job.running || !im?.ok || !fatOk || !st.sdConfirm || null,
        onclick: () => post({ action: 'sd_write', board, drive: st.drive, confirm: st.sdConfirm }) }, icon('save'), t.b_sd)),
      job.action === 'sd_write' ? jobView(t, job) : null,
      h('p.small.muted', null, t.sd_steps)];
  };

  const draw = () => {
    const t = fwText(lang), d = st.data;
    if (!d) { box.replaceChildren(h('p.muted', null, '…')); return; }
    const job = d.job || {}, board = boardOf(d);
    const sd = (d.boards || {})[board] === 'sd';
    box.replaceChildren(...[
      h('p', null, icon('chip'), ' ', h('b', null, t.running + ': '), h('span.mono', { tid: 'fw-running' }, d.running_fw || t.none)),
      boardPicker(t, d, board),
      ...(sd ? sdView(t, d, job, board) : plutoView(t, d, job)),
      !compact && d.history?.length ? h('details', null, h('summary', null, t.history),
        h('ul.small', { tid: 'fw-history' }, d.history.slice().reverse().map(x => h('li', null, `${x.at.replace('T', ' ')} · ${x.action} · ${x.image || ''}${x.drive ? ' → ' + x.drive : ''} · ${x.ok ? '✓' : '✗ ' + (x.error || '')}`)))) : null].filter(Boolean));
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
