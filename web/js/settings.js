// Seite „Einstellungen“: Sprache (gilt für das ganze Dashboard) und Darstellung.
import { h, icon } from './ui.js';
import { LANG, LANGS, T, setLang } from './i18n.js';
import { load, save } from './core.js';

export function pageSettings({ applyTheme } = {}) {
  const sel = h('select', { tid: 'settings-lang', 'aria-label': T('Sprache') }, Object.entries(LANGS).map(([k, n]) => h('option', { value: k }, n)));
  sel.value = LANG;
  sel.addEventListener('change', () => setLang(sel.value));
  const themes = [['system', T('wie System'), 'auto'], ['light', T('hell'), 'sun'], ['dark', T('dunkel'), 'moon']];
  const seg = h('div.seg', { role: 'group', 'aria-label': T('Darstellung') });
  const drawSeg = () => seg.replaceChildren(...themes.map(([k, label, ic]) => h('button', {
    type: 'button', tid: `settings-theme-${k}`, class: load('theme', 'system') === k ? 'on' : null,
    onclick: () => { save('theme', k); applyTheme?.(k); drawSeg(); } }, icon(ic), ' ', label)));
  drawSeg();
  return h('div.page-stack', { tid: 'page-einstellungen' },
    h('section.card', null, h('h3', null, icon('flag'), ' ', T('Sprache')),
      h('p.muted', null, T('Gilt für das ganze Dashboard, die Einrichtung und die Logdatei. Die Seite lädt dabei neu.')),
      h('label.inrow', null, sel)),
    h('section.card', null, h('h3', null, icon('sun'), ' ', T('Darstellung')), seg));
}
