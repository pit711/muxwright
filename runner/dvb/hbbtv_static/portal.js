/* Muxwright-HbbTV-Portal (ES5, ohne Bibliotheken; laeuft auf HbbTV 1.1 bis 2.x).
   Daten kommen aus window.P (von runner/dvb/hbbtv_portal.py in index.html eingebettet). */
var KEY = {
  RED: typeof VK_RED != 'undefined' ? VK_RED : 403, GREEN: typeof VK_GREEN != 'undefined' ? VK_GREEN : 404,
  YELLOW: typeof VK_YELLOW != 'undefined' ? VK_YELLOW : 405, BLUE: typeof VK_BLUE != 'undefined' ? VK_BLUE : 406,
  UP: typeof VK_UP != 'undefined' ? VK_UP : 38, DOWN: typeof VK_DOWN != 'undefined' ? VK_DOWN : 40,
  LEFT: typeof VK_LEFT != 'undefined' ? VK_LEFT : 37, RIGHT: typeof VK_RIGHT != 'undefined' ? VK_RIGHT : 39,
  OK: typeof VK_ENTER != 'undefined' ? VK_ENTER : 13, BACK: typeof VK_BACK != 'undefined' ? VK_BACK : 461,
  PLAY: typeof VK_PLAY != 'undefined' ? VK_PLAY : 415, PAUSE: typeof VK_PAUSE != 'undefined' ? VK_PAUSE : 19,
  STOP: typeof VK_STOP != 'undefined' ? VK_STOP : 413, FF: typeof VK_FAST_FWD != 'undefined' ? VK_FAST_FWD : 417,
  REW: typeof VK_REWIND != 'undefined' ? VK_REWIND : 412, PP: 402
};
var KS_HIDDEN = 0x1, KS_SHOWN = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x100, KS_VIDEO = KS_SHOWN | 0x20;

var TX = {
  de: { start: 'Start', weather: 'Wetter', radar: 'Regenradar', news: 'Nachrichten', epg: 'Programm', media: 'Mediathek',
    gallery: 'Bilder', games: 'Spiele', tools: 'Werkzeuge', info: 'Info', portal: 'Portal', now: 'Jetzt', next: 'Danach', here: 'läuft hier',
    feels: 'gefühlt', wind: 'Wind', hum: 'Feuchte', hours: 'Die nächsten Stunden', days: 'Die nächsten Tage', today: 'Heute',
    clock: 'Uhr', asof: 'Stand', src: 'Quelle', nav: '▲▼ Seite  ► öffnen  ◄ zurück', close: 'Rot: schließen',
    switchto: 'OK: umschalten', play: 'OK: abspielen', pick: 'OK: starten', open: 'OK: öffnen', cats: 'Zurück: Kategorien', endred: 'Rot: Ende', more: '◄► blättern', none: 'Keine Daten verfügbar',
    noweather: 'Wetterdaten zurzeit nicht verfügbar', score: 'Punkte', best: 'Rekord', lines: 'Reihen', moves: 'Züge',
    over: 'Spiel vorbei – OK: neu', won: 'Geschafft! – OK: neu', pause: 'Pause', loading: 'Video wird geladen …',
    vfail: 'Video lässt sich nicht abspielen', back: 'Zurück: Ende', radarnote: 'Niederschlag der letzten Minuten', tvcheck: 'Dieser Fernseher kann:',
    wd: ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'], mon: ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'] },
  en: { start: 'Home', weather: 'Weather', radar: 'Rain radar', news: 'News', epg: 'Programmes', media: 'Videos',
    gallery: 'Pictures', games: 'Games', tools: 'Tools', info: 'Info', portal: 'Portal', now: 'Now', next: 'Next', here: 'on this channel',
    feels: 'feels like', wind: 'Wind', hum: 'Humidity', hours: 'Next hours', days: 'Next days', today: 'Today',
    clock: '', asof: 'Updated', src: 'Source', nav: '▲▼ page  ► open  ◄ back', close: 'Red: close',
    switchto: 'OK: switch', play: 'OK: play', pick: 'OK: start', open: 'OK: open', cats: 'Back: categories', endred: 'Red: exit', more: '◄► browse', none: 'No data available',
    noweather: 'Weather data currently unavailable', score: 'Score', best: 'Best', lines: 'Lines', moves: 'Moves',
    over: 'Game over – OK: new', won: 'You made it! – OK: new', pause: 'Pause', loading: 'Loading video …',
    vfail: 'Video cannot be played', back: 'Back: exit', radarnote: 'Precipitation of the last minutes', tvcheck: 'This TV supports:',
    wd: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], mon: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] }
};
var T = TX[P.lang] || TX.en;

function $(id) { return document.getElementById(id); }
function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function pad(n) { return n < 10 ? '0' + n : '' + n; }
function vis(id, on) { var e = $(id); if (e) e.style.visibility = on ? 'visible' : 'hidden'; }
function setRect(e, l, t, w, h) { e.style.left = l + 'px'; e.style.top = t + 'px'; e.style.width = w + 'px'; e.style.height = h + 'px'; }
function store(k, v) { try { if (v === undefined) return window.localStorage.getItem('mx_' + k); window.localStorage.setItem('mx_' + k, v); } catch (e) {} return null; }

/* ------------------------------------------------------------------ Wettersymbole (wie hbbtv_app.py) */
var WMO_KIND = { 0: 'sun', 1: 'sun', 2: 'part', 3: 'cloud', 45: 'fog', 48: 'fog', 71: 'snow', 73: 'snow', 75: 'snow', 77: 'snow',
  85: 'snow', 86: 'snow', 95: 'storm', 96: 'storm', 99: 'storm' };
function wkind(c) { c = +c; if (WMO_KIND[c]) return WMO_KIND[c]; return (c >= 51 && c <= 82) ? 'rain' : 'cloud'; }
function icon(code, s, night) {
  var k = wkind(code), h = [];
  function d(cls, l, t, w, hh, bg) { h.push('<div class="' + cls + '" style="left:' + Math.round(l) + 'px;top:' + Math.round(t) + 'px;width:' + Math.round(w) + 'px;height:' + Math.round(hh) + 'px' + (bg ? ';background:' + bg : '') + '"></div>'); }
  if (k == 'sun' || k == 'part') { var dd = s * (k == 'sun' ? 0.62 : 0.5), o = k == 'sun' ? (s - dd) / 2 : s * 0.06; d('ic-sun', o, o, dd, dd, night ? '#cfd8e6' : '#ffb300'); }
  if (k != 'sun') {
    var top = s * (k == 'part' ? 0.30 : 0.18), col = (k == 'rain' || k == 'snow' || k == 'storm') ? '#9aa7b8' : '#e3e8ef';
    d('ic-cl', s * 0.10, top + s * 0.18, s * 0.80, s * 0.30, col); d('ic-cl', s * 0.28, top, s * 0.40, s * 0.40, col);
  }
  var i;
  if (k == 'rain') for (i = 0; i < 3; i++) d('ic-dr', s * (0.25 + 0.2 * i), s * 0.72, Math.max(2, s * 0.06), s * 0.18);
  else if (k == 'snow') for (i = 0; i < 3; i++) d('ic-sn', s * (0.24 + 0.2 * i), s * 0.74, s * 0.11, s * 0.11);
  else if (k == 'storm') d('ic-st', s * 0.44, s * 0.64, s * 0.1, s * 0.3);
  else if (k == 'fog') for (i = 0; i < 2; i++) d('ic-fg', s * 0.12, s * (0.70 + 0.13 * i), s * 0.76, Math.max(3, s * 0.07));
  return '<div class="ic" style="width:' + s + 'px;height:' + s + 'px">' + h.join('') + '</div>';
}

/* ------------------------------------------------------------------ Zustand */
var app = null, vb = null, av = null;
var shown = false, focus = 'menu', cur = 0, reloadDue = false, hintOn = true, game = null, playing = null, tool = null;
var PAGES = [];
var LIVE = { l: 96 + 4, t: 36 + 480, w: 256, h: 144 };

function keyset(m) { try { if (app && app.privateData && app.privateData.keyset) app.privateData.keyset.setValue(m); } catch (e) {} }

function videoFull(full) {
  if (!vb) return;
  try { if (full) setRect(vb, 0, 0, 1280, 720); else setRect(vb, LIVE.l, LIVE.t, LIVE.w, LIVE.h); } catch (e) {}
}

function show(on) {
  shown = on;
  vis('portal', on); vis('live', on && P.live); vis('livel', on && P.live); vis('hint', !on && hintOn);
  videoFull(!on || !P.live);
  keyset(on ? KS_SHOWN : KS_HIDDEN);
  if (on) { focus = 'menu'; render(); }
  else { stopGame(); if (reloadDue && !tool) document.location.reload(); }
}

/* ------------------------------------------------------------------ Seitenrahmen */
function render() {
  var h = '', i;
  trace('page ' + PAGES[cur].id + ' ' + focus + (focus == 'content' && PAGES[cur].id == 'games' ? ' ' + gsel.sel : focus == 'content' && PAGES[cur].id == 'tools' ? ' ' + tcat + '/' + (tcat < 0 ? csel.sel : tsel.sel) : ''));
  for (i = 0; i < PAGES.length; i++)
    h += '<div class="mi' + (i == cur ? ' on' + (focus == 'menu' ? ' f' : '') : '') + '" style="top:' + (8 + i * 38) + 'px"><span class="n">' + (i + 1) + '</span>' + esc(PAGES[i].label) + '</div>';
  $('menu').innerHTML = h;
  var p = PAGES[cur];
  $('ptitle').innerHTML = esc(p.title || p.label);
  $('pbody').innerHTML = '';
  p.render($('pbody'), focus == 'content');
  $('pfoot').innerHTML = esc(focus == 'content' && p.hint ? p.hint() : T.nav) + '<span class="r">' + esc(p.src ? p.src + '  ·  ' : '') + esc(T.close) + '</span>';
}
function go(i) { if (i >= 0 && i < PAGES.length) { stopGame(); cur = i; focus = 'menu'; render(); } }
function pageId(id) { for (var i = 0; i < PAGES.length; i++) if (PAGES[i].id == id) return i; return -1; }

/* ------------------------------------------------------------------ Seiten */
function pStart(el) {
  var d = new Date(), h = '<div style="position:absolute;left:0;top:0;font-size:88px;line-height:96px">' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + '</div>' +
    '<div class="dim" style="position:absolute;left:4px;top:98px;font-size:26px">' + T.wd[d.getDay()] + ', ' + d.getDate() + '. ' + T.mon[d.getMonth()] + ' ' + d.getFullYear() + '</div>';
  var w = P.weather;
  if (w) h += '<div style="position:absolute;left:420px;top:4px;width:340px">' + icon(w.now.code, 96, w.now.night) +
    '<div style="position:absolute;left:112px;top:0;font-size:64px;line-height:76px">' + w.now.t + '°</div>' +
    '<div class="dim" style="position:absolute;left:116px;top:76px;font-size:22px">' + esc(w.place) + ' · ' + esc(w.now.cond) + '</div></div>';
  var me = null, i;
  for (i = 0; i < P.epg.length; i++) if (P.epg[i].here) me = P.epg[i];
  if (me && me.now) h += '<div style="position:absolute;left:0;top:160px;width:760px"><span class="dim" style="font-size:22px">' + T.now + ' ' + esc(me.now.start) + '</span>' +
    '<div class="ell" style="font-size:30px;font-weight:bold">' + esc(me.now.title) + '</div>' +
    (me.next ? '<div class="ell dim" style="font-size:22px;margin-top:4px">' + T.next + ' ' + esc(me.next.start) + ' ' + esc(me.next.title) + '</div>' : '') + '</div>';
  if (P.news && P.news.items.length) {
    h += '<div class="dim" style="position:absolute;left:0;top:282px;font-size:22px">' + T.news + '</div>';
    for (i = 0; i < Math.min(4, P.news.items.length); i++)
      h += '<div class="ell" style="position:absolute;left:0;top:' + (316 + i * 38) + 'px;width:760px;font-size:24px">· ' + esc(P.news.items[i].title) + '</div>';
  }
  el.innerHTML = h;
}

function pWeather(el) {
  var w = P.weather;
  if (!w) { el.innerHTML = '<div style="font-size:28px">' + T.noweather + '</div>'; return; }
  var n = w.now, h = '<div style="position:absolute;left:0;top:0">' + icon(n.code, 110, n.night) + '</div>' +
    '<div style="position:absolute;left:130px;top:-6px;font-size:84px;line-height:100px">' + n.t + '°</div>' +
    '<div style="position:absolute;left:134px;top:96px;font-size:26px;color:#cfe0ff">' + esc(n.cond) + '</div>' +
    '<div style="position:absolute;left:420px;top:8px;font-size:24px;line-height:38px"><span class="dim">' + T.feels + '</span> ' + n.feels + '°<br/>' +
    '<span class="dim">' + T.wind + '</span> ' + n.wind + ' km/h ' + esc(n.dir) + '<br/><span class="dim">' + T.hum + '</span> ' + n.hum + ' %</div>';
  var i;
  h += '<div class="dim" style="position:absolute;left:0;top:150px;font-size:22px">' + T.hours + '</div>';
  for (i = 0; i < w.hours.length && i < 6; i++)
    h += '<div class="cell" style="left:' + (i * 127) + 'px;top:182px;width:127px"><div class="t">' + esc(w.hours[i].h) + ' ' + T.clock + '</div>' + icon(w.hours[i].code, 44) + '<div class="v">' + w.hours[i].t + '°</div></div>';
  h += '<div class="dim" style="position:absolute;left:0;top:312px;font-size:22px">' + T.days + '</div>';
  for (i = 0; i < w.days.length && i < 6; i++) {
    var dy = w.days[i];
    h += '<div class="cell" style="left:' + (i * 127) + 'px;top:344px;width:127px"><div class="t">' + esc(i == 0 ? T.today : T.wd[dy.wd]) + '</div>' + icon(dy.code, 44) +
      '<div class="v" style="font-size:24px">' + dy.max + '° / ' + dy.min + '°</div></div>';
  }
  el.innerHTML = h;
}

function pRadar(el) {
  var r = P.radar;
  el.innerHTML = '<img src="' + r.img + '" style="position:absolute;left:0;top:-14px;width:' + r.w + 'px;height:' + r.h + 'px" alt=""/>' +
    '<div style="position:absolute;left:' + (r.w + 30) + 'px;top:0;width:' + (730 - r.w) + 'px;font-size:22px;line-height:32px">' +
    '<div style="font-size:26px;font-weight:bold">' + esc(r.time) + ' ' + T.clock + '</div><div class="dim">' + T.radarnote + '</div>' +
    (P.weather ? '<div style="margin-top:24px">' + icon(P.weather.now.code, 64, P.weather.now.night) + '<div style="font-size:40px">' + P.weather.now.t + '° ' + esc(P.weather.place) + '</div></div>' : '') + '</div>';
}

var news = { sel: 0 };
function pNews(el, f) {
  var it = P.news.items, n = 7, top = Math.floor(news.sel / n) * n, h = '', i;
  for (i = top; i < Math.min(top + n, it.length); i++)
    h += '<div class="row ell' + (i == news.sel && f ? ' sel f' : '') + '" style="top:' + ((i - top) * 36) + 'px;height:34px;font-size:23px;line-height:34px;padding-left:8px;width:752px">' +
      '<span class="dim">' + esc(it[i].time) + '</span>  ' + esc(it[i].title) + '</div>';
  var s = it[news.sel] || {};
  h += '<div style="position:absolute;left:0;top:262px;width:760px;height:204px;overflow:hidden;border-top:2px solid #24406e;padding-top:12px">' +
    '<div style="font-size:26px;font-weight:bold;line-height:32px">' + esc(s.title) + '</div><div style="font-size:22px;line-height:30px;margin-top:6px;color:#dbe6f6">' + esc(s.text) + '</div></div>';
  el.innerHTML = h;
}
function kNews(k) {
  var n = P.news.items.length;
  if (k == KEY.DOWN) news.sel = Math.min(n - 1, news.sel + 1);
  else if (k == KEY.UP) { if (news.sel == 0) return false; news.sel--; }
  else if (k == KEY.RIGHT) news.sel = Math.min(n - 1, news.sel + 7);
  else return false;
  render(); return true;
}

var epg = { sel: 0 };
function pEpg(el, f) {
  var h = '', i, e, n = Math.min(P.epg.length, 6), top = Math.max(0, Math.min(epg.sel - 2, P.epg.length - n));
  for (i = top; i < top + n; i++) {
    e = P.epg[i];
    h += '<div class="row' + (i == epg.sel && f ? ' sel f' : '') + '" style="top:' + ((i - top) * 78) + 'px;height:74px;padding-left:10px;width:750px">' +
      '<div class="ell" style="font-size:24px;font-weight:bold;line-height:34px">' + esc(e.name) + (e.here ? '  <span class="dim" style="font-weight:normal;font-size:20px">● ' + T.here + '</span>' : '') + '</div>' +
      '<div class="ell dim" style="font-size:20px;line-height:26px">' + (e.now ? T.now + ' ' + esc(e.now.start) + '  ' + esc(e.now.title) : '') + (e.next ? '   ·   ' + T.next + ' ' + esc(e.next.start) + '  ' + esc(e.next.title) : '') + '</div></div>';
  }
  el.innerHTML = h || '<div style="font-size:26px">' + T.none + '</div>';
}
function kEpg(k) {
  if (k == KEY.DOWN) epg.sel = Math.min(P.epg.length - 1, epg.sel + 1);
  else if (k == KEY.UP) { if (epg.sel == 0) return false; epg.sel--; }
  else if (k == KEY.OK) { tune(P.epg[epg.sel]); return true; }
  else return false;
  render(); return true;
}
function tune(e) {
  if (!vb || !e || e.here) return;
  try {
    var list = vb.getChannelConfig().channelList, ch = null, i, c;
    for (i = 0; i < list.length; i++) { c = list.item ? list.item(i) : list[i]; if (c && c.sid == e.sid && c.onid == P.onid && c.tsid == P.tsid) { ch = c; break; } }
    if (!ch) ch = vb.createChannelObject(P.idtype, P.onid, P.tsid, e.sid);
    if (ch) vb.setChannel(ch, false);
  } catch (ex) {}
}

var gal = { sel: 0, timer: null };
function pGallery(el) {
  var g = P.gallery[gal.sel];
  el.innerHTML = '<div style="position:absolute;left:0;top:0;width:760px;height:428px;text-align:center"><img src="' + g.img + '" style="width:' + g.w + 'px;height:' + g.h + 'px" alt=""/></div>' +
    '<div class="ell" style="position:absolute;left:0;top:436px;width:640px;font-size:24px">' + esc(g.title) + '</div>' +
    '<div class="dim" style="position:absolute;right:0;top:436px;font-size:22px">' + (gal.sel + 1) + ' / ' + P.gallery.length + '</div>';
  if (gal.timer) clearTimeout(gal.timer);
  gal.timer = setTimeout(function () { if (shown && PAGES[cur].id == 'gallery' && !game) { gal.sel = (gal.sel + 1) % P.gallery.length; render(); } }, 8000);
}
function kGallery(k) {
  var n = P.gallery.length;
  if (k == KEY.RIGHT || k == KEY.DOWN) gal.sel = (gal.sel + 1) % n;
  else if (k == KEY.LEFT || k == KEY.UP) { if (k == KEY.LEFT && gal.sel == 0) return false; gal.sel = (gal.sel + n - 1) % n; }
  else return false;
  render(); return true;
}

var med = { sel: 0 };
function pMedia(el, f) {
  var it = P.media.items, h = '', i, x, y, per = 6, top = Math.floor(med.sel / per) * per;
  for (i = top; i < Math.min(top + per, it.length); i++) {
    x = ((i - top) % 3) * 256; y = Math.floor((i - top) / 3) * 214;
    h += '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:248px;height:206px;border-radius:10px' + (i == med.sel && f ? ';background:#e8eef7;color:#0a1222' : '') + '">' +
      '<div class="thumb" style="left:4px;top:4px">' + (it[i].thumb ? '<img src="' + it[i].thumb + '" alt=""/>' : '') + '</div>' +
      '<div class="ell" style="position:absolute;left:8px;top:144px;width:232px;font-size:21px;line-height:28px">' + esc(it[i].title) + '</div>' +
      '<div style="position:absolute;left:8px;top:172px;font-size:18px;opacity:0.7">' + esc(it[i].dur || '') + '</div></div>';
  }
  el.innerHTML = h;
}
function kMedia(k) {
  var n = P.media.items.length;
  if (k == KEY.RIGHT) med.sel = Math.min(n - 1, med.sel + 1);
  else if (k == KEY.LEFT) { if (med.sel % 3 == 0) return false; med.sel--; }
  else if (k == KEY.DOWN) med.sel = Math.min(n - 1, med.sel + 3);
  else if (k == KEY.UP) { if (med.sel < 3) return false; med.sel -= 3; }
  else if (k == KEY.OK) { playVideo(P.media.items[med.sel]); return true; }
  else return false;
  render(); return true;
}

/* Mediathek: A/V-Control-Objekt (HbbTV 1.x, MP4 ueber HTTP), Sendung vorher anhalten, danach wieder binden */
function osd(text, right, sec) {
  $('osd').innerHTML = '<div class="t ell">' + esc(text) + '</div><div class="r">' + esc(right || '') + '</div>';
  vis('osd', true);
  if (osd.t) clearTimeout(osd.t);
  if (sec) osd.t = setTimeout(function () { vis('osd', false); }, sec * 1000);
}
function fmtMs(ms) { var s = Math.floor((ms || 0) / 1000); return Math.floor(s / 60) + ':' + pad(s % 60); }
function playVideo(it) {
  if (playing) return;
  playing = it;
  vis('portal', false); vis('live', false); vis('livel', false);
  try { if (vb) vb.stop(); } catch (e) {}
  var o = document.createElement('object');
  o.type = 'video/mp4'; o.id = 'av'; o.data = it.url;
  document.body.appendChild(o);
  av = o;
  keyset(KS_VIDEO);
  osd(T.loading, '', 0);
  o.onPlayStateChange = function () {
    var s = o.playState;
    if (s == 1) osd(it.title, T.back, 4);
    else if (s == 5) stopVideo();
    else if (s == 6) { osd(T.vfail, '', 3); setTimeout(stopVideo, 3000); }
  };
  try { o.play(1); } catch (e) { osd(T.vfail, '', 3); setTimeout(stopVideo, 3000); }
}
function stopVideo() {
  if (!playing) return;
  try { av.stop(); } catch (e) {}
  try { av.parentNode.removeChild(av); } catch (e) {}
  av = null; playing = null; vis('osd', false);
  try { if (vb) vb.bindToCurrentChannel(); } catch (e) {}
  show(true);
}
function kVideo(k) {
  var s = av ? av.playState : 0;
  if (k == KEY.BACK || k == KEY.STOP || k == KEY.RED) stopVideo();
  else if (k == KEY.OK || k == KEY.PP || k == KEY.PAUSE || k == KEY.PLAY) {
    try { if (s == 2 && k != KEY.PAUSE) { av.play(1); osd(playing.title, fmtMs(av.playPosition) + ' / ' + fmtMs(av.playTime), 3); } else if (k != KEY.PLAY) { av.play(0); osd(T.pause, fmtMs(av.playPosition) + ' / ' + fmtMs(av.playTime), 0); } } catch (e) {}
  } else if (k == KEY.RIGHT || k == KEY.FF || k == KEY.LEFT || k == KEY.REW) {
    try { var d = (k == KEY.RIGHT || k == KEY.FF) ? 30000 : -30000; av.seek(Math.max(0, av.playPosition + d)); osd(playing.title, fmtMs(av.playPosition + d) + ' / ' + fmtMs(av.playTime), 3); } catch (e) {}
  } else osd(playing.title, av ? fmtMs(av.playPosition) + ' / ' + fmtMs(av.playTime) : '', 3);
  return true;
}

var gsel = { sel: 0 };
function gameList() {
  var l = GAMES.slice(0);
  var lan = /^https?:/i.test(P.doom_url || '');
  if (P.doom_url) l.push({ name: 'Freedoom', ext: P.doom_url, desc: lan ? { de: 'Freedoom · über LAN', en: 'Freedoom · via LAN' } : { de: 'Freedoom · aus dem Sendesignal', en: 'Freedoom · from the broadcast' } });
  return l;
}
function launch(url) {
  osd(P.lang == 'de' ? 'Doom wird gestartet …' : 'Starting Doom …', '', 0);
  // aus dem Karussell (relativ): einfacher Seitenwechsel innerhalb der Anwendung, zurueck mit Rot
  if (!/^https?:/i.test(url)) { document.location.href = url + (P.report_url ? '#log=' + encodeURIComponent(P.report_url.replace(/report$/, 'log')) : ''); return; }
  // aus dem LAN: eigene Anwendung starten (ersetzt das Portal; die AIT startet es danach wieder);
  // createApplication liefert null statt eines Fehlers, wenn das Geraet nicht will -> dann normaler Seitenwechsel
  try { if (app && app.createApplication && app.createApplication(url, false)) return; } catch (e) {}
  document.location.href = url;
}
/* Kachelraster fuer Spiele (4 Spalten) und Werkzeuge (5 Spalten) */
function tiles(el, list, sel, f, cols, st) {
  // blaetterbar: es sind nur so viele Reihen sichtbar, wie in den Inhaltsbereich passen; die Auswahl bleibt sichtbar
  var h = '', i, w = Math.floor(760 / cols), hh = cols == 4 ? 150 : 112, vis = cols == 4 ? 3 : 4, rows = Math.ceil(list.length / cols), r = Math.floor(sel / cols);
  st = st || {}; st.top = Math.max(0, Math.min(st.top || 0, rows - vis)); if (r < st.top) st.top = r; if (r >= st.top + vis) st.top = r - vis + 1;
  for (i = st.top * cols; i < Math.min(list.length, (st.top + vis) * cols); i++)
    h += '<div class="tile' + (i == sel ? ' on' + (f ? ' f' : '') : '') + '" style="left:' + ((i % cols) * w) + 'px;top:' + ((Math.floor(i / cols) - st.top) * (hh + 8)) + 'px;width:' + (w - 8) + 'px;height:' + hh + 'px">' +
      '<div class="g" style="margin-top:' + (cols == 4 ? 34 : 16) + 'px;font-size:' + (cols == 4 ? 26 : 20) + 'px;line-height:' + (cols == 4 ? 32 : 24) + 'px">' + esc(gname(list[i])) + '</div><div class="d" style="font-size:' + (cols == 4 ? 18 : 15) + 'px;line-height:' + (cols == 4 ? 22 : 18) + 'px;margin:6px 6px 0 6px">' + esc(list[i].desc[P.lang] || list[i].desc.en) + '</div></div>';
  if (rows > vis) h += '<div style="position:absolute;right:0;top:-44px;font-size:18px;color:#9fb3d1">' + (sel + 1) + ' / ' + list.length + (st.top + vis < rows ? '  ▼' : '') + '</div>';
  el.innerHTML = h;
}
function gridKey(k, st, n, cols) {
  if (k == KEY.RIGHT) st.sel = Math.min(n - 1, st.sel + 1);
  else if (k == KEY.LEFT) { if (st.sel % cols == 0) return false; st.sel--; }
  else if (k == KEY.DOWN) st.sel = Math.min(n - 1, st.sel + cols);
  else if (k == KEY.UP) { if (st.sel < cols) return false; st.sel -= cols; }
  else return null;
  return true;
}
function pGames(el, f) { if (!game) tiles(el, gameList(), gsel.sel, f, 4, gsel); }
function kGames(k) {
  if (game) {
    if (k == KEY.BACK) { stopGame(); render(); return true; }
    game.key(k); return true;
  }
  var GAMES = gameList(), r = gridKey(k, gsel, GAMES.length, 4);
  if (r === false) return false;
  if (r) { render(); return true; }
  if (k == KEY.OK && GAMES[gsel.sel].ext) { launch(GAMES[gsel.sel].ext); return true; }
  if (k == KEY.OK) {
    $('pbody').innerHTML = '<div id="game"></div>';
    $('ptitle').innerHTML = esc(gname(GAMES[gsel.sel]));
    $('pfoot').innerHTML = esc(GAMES[gsel.sel].help[P.lang] || GAMES[gsel.sel].help.en) + '<span class="r">' + esc(T.back) + '</span>';
    Held.clear(); game = GAMES[gsel.sel]; trace('game ' + gname(game)); game.start($('game')); return true;
  }
  return false;
}
var tsel = { sel: 0 }, csel = { sel: 0 }, tcat = -1, CATS = null;
/* Werkzeuge in Kategorien: erst die Kategorie waehlen, dann das Werkzeug; Zurueck/◄ fuehrt zu den Kategorien */
function toolCats() {
  if (CATS) return CATS;
  var seen = [], rest = [], i, j, c;
  CATS = [];
  for (i = 0; i < (window.TOOL_CATS || []).length; i++) {
    c = TOOL_CATS[i]; var l = [];
    for (j = 0; j < c.tools.length; j++) if (c.tools[j] && TOOLS.indexOf(c.tools[j]) >= 0) { l.push(c.tools[j]); seen.push(c.tools[j]); }
    if (l.length) CATS.push({ name: c.name, tools: l });
  }
  for (i = 0; i < TOOLS.length; i++) if (seen.indexOf(TOOLS[i]) < 0) rest.push(TOOLS[i]);
  if (rest.length) CATS.push({ name: { de: 'Weitere', en: 'More' }, tools: rest });
  for (i = 0; i < CATS.length; i++) CATS[i].desc = { de: CATS[i].tools.length + ' Werkzeuge', en: CATS[i].tools.length + ' tools' };
  return CATS;
}
function pTools(el, f) {
  if (tool) return;
  var C = toolCats();
  if (tcat < 0) { tiles(el, C, csel.sel, f, 4, csel); return; }
  $('ptitle').innerHTML = esc(T.tools + '  ›  ' + gname(C[tcat]));
  tiles(el, C[tcat].tools, tsel.sel, f, 5, tsel);
}
function kTools(k) {
  var C = toolCats(), r;
  if (tcat < 0) {
    r = gridKey(k, csel, C.length, 4);
    if (r === false) return false;
    if (r) { render(); return true; }
    if (k == KEY.OK) { tcat = csel.sel; tsel = { sel: 0 }; render(); return true; }
    return false;
  }
  var L = C[tcat].tools;
  r = gridKey(k, tsel, L.length, 5);
  if (r === false || k == KEY.BACK) { tcat = -1; render(); return true; }
  if (r) { render(); return true; }
  if (k == KEY.OK) { startTool(L[tsel.sel]); return true; }
  return false;
}
function startTool(t) {
  tool = t;
  trace('tool ' + gname(t));
  if (t.full) {
    vis('portal', false); vis('live', false); vis('livel', false); videoFull(true); $('full').innerHTML = ''; vis('full', true); t.start($('full'));
    fullHint(true); return;
  }
  $('pbody').innerHTML = '<div id="tool" style="position:absolute;left:0;top:0;width:760px;height:470px"></div>';
  $('ptitle').innerHTML = esc(gname(t));
  $('pfoot').innerHTML = esc((t.help[P.lang] || t.help.en || '')) + '<span class="r">' + esc(T.back) + '</span>';
  t.start($('tool'));
}
/* Vollbild-Werkzeuge haben keine Fusszeile: kurz einblenden, wie man sie beendet */
function fullHint(on) {
  var h = $('fhint');
  if (!h) { h = document.createElement('div'); h.id = 'fhint'; document.body.appendChild(h);
    h.style.cssText = 'position:absolute;right:60px;top:40px;padding:10px 18px;border-radius:8px;background:rgba(0,0,0,0.7);color:#fff;font-size:22px;z-index:99'; }
  h.innerHTML = esc((tool && tool.help && (tool.help[P.lang] || tool.help.en) ? (tool.help[P.lang] || tool.help.en) + '  ·  ' : '') + T.endred);
  h.style.visibility = on ? 'visible' : 'hidden';
  clearTimeout(fullHint.t);
  if (on) fullHint.t = setTimeout(function () { h.style.visibility = 'hidden'; }, 5000);
}
function stopTool() {
  if (!tool) return;
  fullHint(false);
  var t = tool; tool = null;
  try { t.stop(); } catch (e) {}
  if (typeof OSK != 'undefined') OSK.close();
  if (t.full) { vis('full', false); $('full').innerHTML = ''; show(true); focus = 'content'; } // zurueck ins Werkzeugraster
  render();
}
/* Programmton/-bild anhalten (Tongenerator, Knistern) und wieder aufnehmen */
var bcPaused = 0;
function broadcastPause(on) {
  bcPaused += on ? 1 : -1;
  try { if (on && bcPaused == 1) vb.stop(); else if (!on && bcPaused == 0) vb.bindToCurrentChannel(); } catch (e) {}
}
function stopGame() { if (game) { try { game.stop(); } catch (e) {} game = null; } }

function pInfo(el) {
  var c = caps(), yn = function (v) { return v ? '<span style="color:#7ddc7d">✔</span>' : '<span style="color:#ff6b6b">✘</span>'; };
  el.innerHTML = '<div style="font-size:30px;font-weight:bold">' + esc(P.info.title) + '</div>' +
    '<div style="font-size:23px;line-height:32px;margin-top:12px;white-space:pre-wrap;height:150px;overflow:hidden">' + esc(P.info.text) + '</div>' +
    '<div style="position:absolute;left:0;top:214px;width:760px;font-size:20px;line-height:28px">' +
    '<div class="dim" style="font-size:22px;margin-bottom:4px">' + esc(T.tvcheck) + '</div>' +
    'WebAssembly ' + yn(c.wasm) + '   Canvas ' + yn(c.canvas) + '   WebGL ' + yn(c.webgl) + '   localStorage ' + yn(c.ls) + '   HTML5 video ' + yn(c.video) +
    '<div class="dim ell" style="font-size:17px;line-height:24px;margin-top:6px">' + esc(c.ua) + '</div></div>' +
    '<div class="dim" style="position:absolute;left:0;top:420px;font-size:19px;line-height:26px">' + esc(P.tech) + '</div>';
}

/* Faehigkeiten des Fernsehers (fuer Spiele wie Doom: WebAssembly) - Meldung an den Muxwright-PC, falls im LAN */
function caps() {
  if (caps.r) return caps.r;
  var r = { ua: navigator.userAgent || '', w: screen.width, h: screen.height, wasm: false, canvas: false, webgl: false, ls: false, video: false };
  try { r.wasm = typeof WebAssembly == 'object' && WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0])); } catch (e) {}
  try { var cv = document.createElement('canvas'); r.canvas = !!(cv.getContext && cv.getContext('2d')); r.webgl = !!(cv.getContext && (cv.getContext('webgl') || cv.getContext('experimental-webgl'))); } catch (e) {}
  try { window.localStorage.setItem('mx_t', '1'); r.ls = window.localStorage.getItem('mx_t') == '1'; } catch (e) {}
  try { var v = document.createElement('video'); r.video = !!(v.canPlayType && v.canPlayType('video/mp4; codecs="avc1.4d401f, mp4a.40.2"')); } catch (e) {}
  caps.r = r;
  return r;
}
/* Diagnose ueber den Rueckkanal (nur LAN-Modus): Seite, Spiel, Werkzeug und Skriptfehler an den Muxwright-PC */
function trace(m) {
  if (!P.report_url) return;
  try { new Image().src = P.report_url.replace(/report$/, 'log') + '?m=' + encodeURIComponent(m) + '&t=' + new Date().getTime(); } catch (e) {}
}
window.onerror = function (msg, src, line) { trace('error ' + msg + ' @' + String(src || '').replace(/^.*\//, '') + ':' + line); };

function report() {
  if (!P.report_url) return;
  var c = caps(), q = [];
  for (var k in c) q.push(k + '=' + encodeURIComponent(c[k] === true ? 1 : c[k] === false ? 0 : c[k]));
  try { new Image().src = P.report_url + '?' + q.join('&') + '&t=' + new Date().getTime(); } catch (e) {}
}

function buildPages() {
  PAGES.push({ id: 'start', label: T.start, title: P.title, render: pStart });
  if (P.weather || P.weather_on) PAGES.push({ id: 'weather', label: T.weather, title: T.weather + (P.weather ? ' – ' + P.weather.place : ''), render: pWeather, src: P.weather ? P.weather.src : '' });
  if (P.radar) PAGES.push({ id: 'radar', label: T.radar, render: pRadar, src: P.radar.src });
  if (P.news && P.news.items.length) PAGES.push({ id: 'news', label: T.news, render: pNews, key: kNews, src: T.src + ': ' + P.news.src, hint: function () { return '▲▼ ' + T.more; } });
  if (P.epg.length) PAGES.push({ id: 'epg', label: T.epg, render: pEpg, key: kEpg, hint: function () { return '▲▼  ' + T.switchto; } });
  if (P.media && P.media.items.length) PAGES.push({ id: 'media', label: T.media, render: pMedia, key: kMedia, hint: function () { return '◄►▲▼  ' + T.play; } });
  if (P.gallery.length) PAGES.push({ id: 'gallery', label: T.gallery, render: pGallery, key: kGallery, hint: function () { return T.more; } });
  if (P.games) PAGES.push({ id: 'games', label: T.games, render: pGames, key: kGames, hint: function () { return '◄►▲▼  ' + T.pick; } });
  if (P.tools) PAGES.push({ id: 'tools', label: T.tools, render: pTools, key: kTools, hint: function () { return '◄►▲▼  ' + (tcat < 0 ? T.open : T.pick + '  ·  ' + T.cats); } });
  if (P.info) PAGES.push({ id: 'info', label: T.info, render: pInfo });
}

/* ------------------------------------------------------------------ Tasten */
function onKey(k) {
  if (playing) return kVideo(k);
  if (tool) {
    if (k == KEY.RED) { stopTool(); return true; }
    if (tool.key(k)) return true;
    if (k == KEY.BACK) { stopTool(); return true; }
    return true;
  }
  if (!shown) { if (k == KEY.RED) { hintOn = false; show(true); return true; } return false; }
  if (k == KEY.RED) { show(false); return true; }
  var p = PAGES[cur];
  if (focus == 'content') {
    if (p.key && p.key(k)) return true;
    if (k == KEY.LEFT || k == KEY.BACK || k == KEY.UP) { stopGame(); focus = 'menu'; render(); return true; }
    if (k == KEY.GREEN || k == KEY.YELLOW || k == KEY.BLUE || (k >= 49 && k <= 57)) { focus = 'menu'; } else return true;
  }
  if (k == KEY.UP) go((cur + PAGES.length - 1) % PAGES.length);
  else if (k == KEY.DOWN) go((cur + 1) % PAGES.length);
  else if (k == KEY.RIGHT || k == KEY.OK) { if (p.key) { focus = 'content'; render(); } }
  else if (k == KEY.BACK) show(false);
  else if (k >= 49 && k <= 57) go(k - 49);
  else if (k == 48) go(9);
  else if (k == KEY.GREEN) go(pageId('epg'));
  else if (k == KEY.YELLOW) go(pageId('news'));
  else if (k == KEY.BLUE) go(pageId('weather'));
  else return false;
  return true;
}
document.onkeydown = function (ev) {
  ev = ev || window.event;
  if (game) Held.down(ev.keyCode);
  if (tool && tool.keypress && ev.key && ev.key.length == 1 && !ev.ctrlKey) { tool.keypress(ev.key); if (ev.preventDefault) ev.preventDefault(); return false; }
  if (onKey(ev.keyCode)) { if (ev.preventDefault) ev.preventDefault(); return false; }
  return true;
};
document.onkeyup = function (ev) { ev = ev || window.event; Held.up(ev.keyCode); };

/* ------------------------------------------------------------------ Start */
function tick() {
  var d = new Date();
  $('clock').innerHTML = T.wd[d.getDay()] + ' ' + d.getDate() + '.' + (d.getMonth() + 1) + '.  ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}
function init() {
  try { app = $('appmgr').getOwnerApplication(document); app.show(); } catch (e) {}
  vb = $('vb');
  try { vb.bindToCurrentChannel(); } catch (e) {}
  buildPages();
  $('htitle').innerHTML = esc(P.title);
  tick(); setInterval(tick, 10000);
  show(document.location.hash == '#open');
  setTimeout(function () { hintOn = false; if (!shown) vis('hint', false); }, 15000);
  setTimeout(function () { reloadDue = true; if (!shown && !playing) document.location.reload(); }, P.refresh_ms);
  setTimeout(report, 3000);
}
