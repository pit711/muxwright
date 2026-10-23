// Seiten „Benötigte Hardware“ (PC-Bewertung, Partnerlinks, Unterstützen) und „Logdatei“ (je Tag, mehrsprachig).
// Der Baustein hwPanel() wird auch in der Einrichtung genutzt.
import { h, icon, api, toast } from './ui.js';
import { load, save } from './core.js';
import { HW } from './hw_i18n.js';
import { pickLang, fmt } from './onboarding_i18n.js';

const REL = 'sponsored nofollow noopener';
const GROUP_ORDER = ['radio', 'adapter', 'cable', 'safety', 'pc', 'ham'];
export const hwText = lang => HW[lang] || HW.en;

// Shop-Region wie auf der Website: Europa (amazon.de + eBay-Seite des Landes) bzw. USA/Kanada (amazon.com + ebay.com/.ca)
const EBAY_EU = ['de', 'at', 'ch', 'fr', 'it', 'es', 'nl', 'be', 'pl', 'ie', 'uk'], EBAY_NA = ['us', 'ca'];
const EBAY_HOST = { de: 'ebay.de', at: 'ebay.at', ch: 'ebay.ch', fr: 'ebay.fr', it: 'ebay.it', es: 'ebay.es', nl: 'ebay.nl', be: 'ebay.be',
  pl: 'ebay.pl', ie: 'ebay.ie', uk: 'ebay.co.uk', us: 'ebay.com', ca: 'ebay.ca' };
const COUNTRY_EBAY = { DE: 'de', AT: 'at', CH: 'ch', LI: 'ch', LU: 'de', FR: 'fr', MC: 'fr', IT: 'it', SM: 'it', ES: 'es', NL: 'nl', BE: 'be',
  PL: 'pl', IE: 'ie', GB: 'uk', US: 'us', CA: 'ca' };
function guessEbay(lang) {
  for (const l of navigator.languages || [navigator.language || '']) {
    const m = /^[a-z]{2,3}-([A-Z]{2})/i.exec(l || '');
    if (m && COUNTRY_EBAY[m[1].toUpperCase()]) return COUNTRY_EBAY[m[1].toUpperCase()];
  }
  return { de: 'de', fr: 'fr', it: 'it', es: 'es', nl: 'nl', pl: 'pl' }[lang]
    || (['pt', 'cs', 'sv', 'uk', 'ru', 'tr'].includes(lang) ? 'de' : 'us');
}

/** PC-Bewertung: lädt /api/hwcheck, zeigt Hardware, Fähigkeiten und Empfehlungen; Leistungstest auf Knopfdruck */
export function hwPanel(lang, { compact = false, onShop = null } = {}) {
  const t = hwText(lang);
  const box = h('div.hwpanel', { tid: 'hw-panel' }, h('p.muted', null, t.loading));
  let data = null, busy = false;
  const draw = () => {
    if (!data) return;
    const nv = data.nvenc;
    const rows = [
      [t.gpu, nv ? `${nv.name} · ${t.gen} ${nv.nvenc_gen} · ${t.driver} ${nv.driver}` : (data.gpu?.[0]?.name ? `${data.gpu[0].name} – ${t.none_gpu}` : t.none_gpu)],
      [t.cpu, `${data.cpu?.name || '?'} · ${data.cpu?.cores || '?'}C/${data.cpu?.threads || '?'}T`],
      [t.ram, `${data.ram_gb || '?'} GB`],
    ];
    const b = data.bench;
    const benchTxt = b ? [b.x264_1080p_fps != null ? `x264 1080p: ${b.x264_1080p_fps} fps` : null,
      b.nvenc_hevc_2160p_fps != null ? `NVENC HEVC 2160p: ${b.nvenc_hevc_2160p_fps} fps` : null,
      b.nvenc_hevc10_2160p_fps != null ? `HDR10 (Main10): ${b.nvenc_hevc10_2160p_fps} fps` : null].filter(Boolean).join(' · ') : '';
    const benchBtn = h('button.btn.ghost.sm', { type: 'button', tid: 'hw-bench', disabled: busy || !data.ffmpeg, onclick: async () => {
      busy = true; draw();
      try { data = await api('GET', '/api/hwcheck?bench=1'); } catch (e) { toast(e.message, 'error'); }
      busy = false; draw();
    } }, icon('gauge'), busy ? t.bench_run : t.bench);
    box.replaceChildren(...[
      h('dl.hw-kv', null, rows.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])),
      h('ul.hw-caps', { tid: 'hw-caps' }, (data.caps || []).map(c => h('li', { class: c.status, tid: `hw-cap-${c.k}` },
        icon(c.status === 'ok' ? 'check' : c.status === 'warn' ? 'alert' : 'x'),
        h('span', null, t.caps[c.k] || c.k), h('small', null, `${t.st[c.status]} · ${t.via[c.how] || c.how}`)))),
      data.weak ? h('p.alert', { tid: 'hw-weak' }, icon('alert'), t.weak) : null,
      (data.recommend || []).length ? h('ul.hw-rec', { tid: 'hw-rec' }, data.recommend.map(r => h('li', null, icon('info'), t.rec[r.why] || r.why))) : null,
      h('div.inrow.wrap', null, benchBtn, benchTxt ? h('span.small.mono', { tid: 'hw-bench-res' }, `${t.bench_res}: ${benchTxt}`) : null,
        benchTxt ? h('span.muted.small', null, t.bench_note) : null, h('span.grow'),
        onShop && (data.recommend || []).length ? h('button.btn.sm', { type: 'button', tid: 'hw-shop', onclick: onShop }, icon('link'), t.rec_link) : null)].filter(Boolean));
  };
  api('GET', '/api/hwcheck').then(d => { data = d; draw(); }).catch(e => box.replaceChildren(h('p.alert', null, icon('alert'), e.message)));
  if (compact) box.classList.add('compact');
  return box;
}

// ---------------------------------------------------------------- Benötigte Hardware
export function pageHardware() {
  let lang = pickLang();
  let ebay = load('shopEbay', guessEbay(lang));
  let market = load('shopMarket', EBAY_NA.includes(ebay) ? 'us' : 'de');
  const root = h('div.page-stack.hwpage', { tid: 'page-hardware' });
  const render = async () => {
    const t = hwText(lang);
    let shop = null;
    try { shop = await api('GET', `/api/shop?market=${market}&lang=${lang}&ebay=${ebay}`); } catch (e) { toast(e.message, 'error'); }
    const mk = (m, label) => h('button', { type: 'button', class: market === m ? 'on' : null, tid: `shop-market-${m}`, onclick: () => {
      market = m; save('shopMarket', m);
      const fit = m === 'us' ? EBAY_NA : EBAY_EU;
      if (!fit.includes(ebay)) { const g = guessEbay(lang); ebay = fit.includes(g) ? g : fit[0]; save('shopEbay', ebay); }
      render(); } }, label);
    const esel = h('select', { tid: 'shop-ebay', 'aria-label': 'eBay' }, (market === 'us' ? EBAY_NA : EBAY_EU).map(k => h('option', { value: k }, EBAY_HOST[k])));
    esel.value = shop?.ebay_site || ebay;
    esel.addEventListener('change', () => { ebay = esel.value; save('shopEbay', ebay); render(); });
    const ad = () => h('span.mini-ad', null, t.ad);
    const btn = (href, label, cls) => href ? h('a.btn.sm.' + cls, { href, target: '_blank', rel: REL }, label) : null;   // „Anzeige“ einmal je Gruppe
    const groups = {};
    for (const s of shop?.slots || []) (groups[s.group] = groups[s.group] || []).push(s);
    const shopEls = GROUP_ORDER.filter(g => groups[g]).map(g => h('section.card.shopgrp', { tid: `shop-group-${g}`, id: `shop-${g}` },
      h('h3', null, t.groups[g] || g, ' ', ad()),
      h('div.shoplist', null, groups[g].map(s => h('div.shopitem', { tid: `shop-${s.id}` },
        h('div', null, h('b', null, s.name), s.note ? h('p.muted.small', null, s.note) : null),
        h('div.inrow.wrap', null, btn(s.amazon, t.amazon, 'amz'), btn(s.ebay, t.ebay, 'ebay'), btn(s.ali, t.ali, 'ali')))))));
    root.replaceChildren(
      h('div.toolbar', null, h('h2', null, t.shop_h), h('span.grow'), 
        h('div.seg', { role: 'group', 'aria-label': t.market }, mk('de', 'EU · amazon.de'), mk('us', 'US · CA · amazon.com')), esel),
      h('section.card', { tid: 'card-pc' }, h('h3', null, icon('chip'), ' ', t.h), h('p.muted', null, t.intro),
        hwPanel(lang, { onShop: () => document.getElementById('shop-pc')?.scrollIntoView({ behavior: 'smooth' }) })),
      h('p.muted.small', { tid: 'shop-adnote' }, h('span.mini-ad', null, t.ad), ' ', t.shop_intro),
      ...(shopEls.length ? shopEls : [h('p.muted', null, '–')]),
      h('section.card.sponsor', { tid: 'card-sponsor' }, h('h3', null, icon('star'), ' ', t.sponsor_h), h('p', null, t.sponsor_t),
        h('div.inrow.wrap', null, (shop?.sponsor || []).map(sp => h('a.btn.primary.sm', { href: sp.url, target: '_blank', rel: 'noopener', tid: `sponsor-${sp.name.toLowerCase().replace(/\W/g, '')}` }, sp.name)))));
  };
  render();
  return root;
}

// ---------------------------------------------------------------- Logdatei
export function pageLogFile() {
  let lang = pickLang(), file = '', level = '', q = '';
  const root = h('div.page-stack.logfile', { tid: 'page-logdatei' });
  const render = async () => {
    const t = hwText(lang);
    let d = { files: [], entries: [] };
    try { d = await api('GET', `/api/logs?lang=${lang}&file=${encodeURIComponent(file)}&level=${level}`); } catch (e) { toast(e.message, 'error'); }
    file = d.file || '';
    const fsel = h('select', { tid: 'logf-file' }, d.files.map(f => h('option', { value: f.name }, `${f.name.replace(/^muxwright-|\.log$/g, '')} (${Math.round(f.size / 1024)} kB)`)));
    fsel.value = file;
    fsel.addEventListener('change', () => { file = fsel.value; render(); });
    const lsel = h('select', { tid: 'logf-level' }, [['', t.log_all], ['info', 'Info'], ['warn', '⚠'], ['error', '✖']].map(([v, n]) => h('option', { value: v }, n)));
    lsel.value = level;
    lsel.addEventListener('change', () => { level = lsel.value; render(); });
    const qi = h('input', { type: 'search', placeholder: t.log_filter, tid: 'logf-filter', value: q });
    const list = h('div.loglist', { tid: 'logf-list' });
    const draw = () => {
      const n = q.toLowerCase();
      const rows = d.entries.filter(e => !n || `${e.text} ${e.src}`.toLowerCase().includes(n));
      list.replaceChildren(...rows.map(e => h('div.logline.' + e.lvl, null,
        h('span.lt.mono', null, new Date(e.t * 1000).toLocaleTimeString(lang)),
        h('span.ll', null, e.lvl_text || e.lvl), e.src ? h('span.ls', null, e.src) : null,
        h('span.lm', null, e.text, !e.translated && lang !== 'de' ? h('em.orig', null, ` (${t.log_orig})`) : null))));
      if (!rows.length) list.append(h('p.muted.small.pad', null, t.log_none));
      cnt.textContent = fmt(t.log_count, { n: rows.length });
    };
    qi.addEventListener('input', () => { q = qi.value; draw(); });
    const cnt = h('small.muted', { tid: 'logf-count' });
    const dl = h('button.btn.ghost.sm', { type: 'button', tid: 'logf-download', onclick: () => {
      const txt = d.entries.map(e => `${new Date(e.t * 1000).toISOString()}\t${e.lvl_text || e.lvl}\t${e.src || ''}\t${e.text}`).join('\n');
      const a = h('a', { href: URL.createObjectURL(new Blob([txt], { type: 'text/plain;charset=utf-8' })), download: (file || 'muxwright.log').replace(/\.log$/, `-${lang}.txt`) });
      document.body.append(a); a.click(); a.remove();
    } }, icon('download'), t.log_download);
    root.replaceChildren(
      h('div.toolbar', null, h('h2', null, t.log_h), h('span.grow'), cnt),
      h('p.muted.small', null, t.log_intro),
      h('div.toolbar', null, h('label.inrow', null, t.log_file, fsel), 
        h('label.inrow', null, t.log_level, lsel), qi, h('span.grow'),
        h('button.btn.ghost.sm', { type: 'button', tid: 'logf-reload', onclick: render }, icon('refresh'), t.log_reload), dl),
      list);
    draw();
  };
  render();
  return root;
}
