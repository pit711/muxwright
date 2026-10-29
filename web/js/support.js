// Bitte um Unterstützung: nach 10 Stunden Sendung am Stück eine kleine, nicht blockierende Karte unten rechts mit den
// Spendenlinks (pc/shop.py SPONSOR, über /api/shop). Höchstens einmal je Sendung; „Habe schon unterstützt“ = 180 Tage Ruhe.

import { h, icon, api } from './ui.js';
import { S, load, save } from './core.js';
import { T } from './i18n.js';

const AFTER_S = 10 * 3600;
const QUIET_DAYS = 180;
const FALLBACK = [{ name: 'Ko-fi', url: 'https://ko-fi.com/711it' }, { name: 'PayPal', url: 'https://paypal.me/711IT' }];
let links = null, shownFor = null;

async function sponsorLinks() {
  if (!links) {
    try { links = (await api('GET', '/api/shop'))?.sponsor; } catch { /* Fallback */ }
    if (!links?.length) links = FALLBACK;
  }
  return links;
}

function close(el) { el.classList.add('out'); setTimeout(() => el.remove(), 250); }

async function show(since, hours) {
  const sp = await sponsorLinks();
  if (document.querySelector('[data-testid=support-pop]')) return;
  const el = h('aside.support-pop', { tid: 'support-pop', role: 'dialog', 'aria-label': T('Muxwright unterstützen') },
    h('button.support-x', { type: 'button', tid: 'support-close', title: T('Später'), onclick: () => close(el) }, icon('x')),
    h('div.support-h', null, h('span.support-ic', null, '☕'), h('b', null, T('{h} Stunden auf Sendung – Zeit für einen Kaffee?', { h: hours }))),
    h('p', null, T('Muxwright läuft bei dir gerade ohne Unterbrechung. Die Software ist kostenlos und entsteht in meiner Freizeit. Wenn sie dir nützt, freue ich mich über eine kleine Spende – damit kann ich sie weiterentwickeln, neue Standards einbauen und Testhardware kaufen.')),
    h('div.support-btns', null, sp.map(s => h('a.btn.primary.sm', { href: s.url, target: '_blank', rel: 'noopener', tid: `support-${s.name.toLowerCase().replace(/\W/g, '')}`,
      onclick: () => setTimeout(() => close(el), 300) }, s.name))),
    h('div.support-foot', null,
      h('button.linklike', { type: 'button', tid: 'support-later', onclick: () => close(el) }, T('Später')),
      h('button.linklike', { type: 'button', tid: 'support-done', onclick: () => { save('supportQuietUntil', Date.now() + QUIET_DAYS * 864e5); close(el); } }, T('Habe schon unterstützt – danke!'))));
  document.body.append(el);
  shownFor = since;
  save('supportShownFor', since);
}

/** Aus liveUpdate (app.js): prüft die laufende Sendung */
export function checkSupport() {
  const tx = S.state?.tx;
  if (tx?.state !== 'running' || !tx.since) return;
  const since = Number(tx.since);
  const after = Number(load('supportAfterS', AFTER_S));          // für Tests verkürzbar (localStorage dablab.supportAfterS)
  const el = Date.now() / 1000 - since;
  if (el < after || shownFor === since || load('supportShownFor', null) === since) return;
  if (Date.now() < Number(load('supportQuietUntil', 0))) return;
  if (document.querySelector('dialog[open]')) return;             // nie über eine Sicherheitsabfrage legen
  show(since, Math.floor(el / 3600));
}
