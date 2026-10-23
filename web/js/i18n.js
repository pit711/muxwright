// Sprache des Dashboards. Texte stehen im Code auf Deutsch in T('…'); die Wörterbücher in js/i18n/*.js liefern
// en/fr/es/it/nl/pl. Fehlt eine Übersetzung, bleibt der deutsche Text stehen. Umschalten: Seite „Einstellungen“.
import { LANGS, RTL, TXT, pickLang, saveLang } from './onboarding_i18n.js';
import { HW } from './hw_i18n.js';
import { COMP } from './comp_i18n.js';
import { FW } from './fw_i18n.js';
import { CREDITS_T } from './credits_i18n.js';
import A from './i18n/a.js';
import B from './i18n/b.js';
import C from './i18n/c.js';

export { LANGS };
export const LANG = pickLang();
// Weitere Sprachen: je Sprache eine Datei js/i18n/lang/<code>.js (alle Bereiche), nur die gewählte wird geladen
const EXTRA = ['pt', 'cs', 'sv', 'ru', 'uk', 'tr', 'ar', 'hi', 'zh', 'ja', 'ko', 'id', 'vi'];
let X = {};
if (EXTRA.includes(LANG)) {
  try {
    const m = await import(`./i18n/lang/${LANG}.js`);
    X = m.default || {};
    // Einrichtung, Hardware, Komponenten, Flasher, Danke: gleiche Struktur wie der englische Block
    if (m.TXT) TXT[LANG] = m.TXT;
    if (m.HW) HW[LANG] = m.HW;
    if (m.COMP) COMP[LANG] = m.COMP;
    if (m.FW) FW[LANG] = m.FW;
    if (m.CREDITS) CREDITS_T[LANG] = m.CREDITS;
  } catch { X = {}; }
}
const DICT = LANG === 'de' ? {} : Object.assign({}, A[LANG], B[LANG], C[LANG], X);
document.documentElement.lang = LANG;
/** Zahlen-/Datumsformat der Sprache (arabisch mit lateinischen Ziffern – technische Werte) */
export const LOCALE = { de: 'de-DE', en: 'en-GB', fr: 'fr-FR', es: 'es-ES', pt: 'pt-BR', it: 'it-IT', nl: 'nl-NL', pl: 'pl-PL', cs: 'cs-CZ',
  sv: 'sv-SE', ru: 'ru-RU', uk: 'uk-UA', tr: 'tr-TR', ar: 'ar-u-nu-latn', hi: 'hi-IN-u-nu-latn', zh: 'zh-CN', ja: 'ja-JP', ko: 'ko-KR',
  id: 'id-ID', vi: 'vi-VN' }[LANG] || 'en-GB';
document.documentElement.dir = RTL.has(LANG) ? 'rtl' : 'ltr';

/** Übersetzt einen deutschen Text; {name}-Platzhalter werden aus vars ersetzt. */
export function T(de, vars) {
  let s = (LANG !== 'de' && DICT[de]) || de;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
}

/** Statische Texte in index.html: data-i18n (Text), data-i18n-title (title-Attribut). */
export function translateDom(root = document) {
  if (LANG === 'de') return;
  root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = T(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = T(el.dataset.i18nTitle); });
}

/** Sprache wechseln: speichern und neu laden, damit alle Module die neue Sprache verwenden. */
export function setLang(l) {
  if (!LANGS[l] || l === LANG) return;
  saveLang(l);
  location.reload();
}
