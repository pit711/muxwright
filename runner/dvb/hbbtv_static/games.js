/* Spiele fuer das Muxwright-HbbTV-Portal (ES5, Fernbedienung: Pfeile + OK). Eigene Umsetzung (MIT, wie Muxwright).
   Jedes Spiel: {name, desc, help, start(el), key(k), stop()}; Spielfeld 760x470 Pixel (#game). */
function canvasIn(el, w, h, x, y) {
  var c = document.createElement('canvas');
  if (!c.getContext) { el.innerHTML = '<div style="font-size:26px">Canvas not supported</div>'; return null; }
  c.width = w; c.height = h; c.style.position = 'absolute'; c.style.left = (x || 0) + 'px'; c.style.top = (y || 0) + 'px';
  el.appendChild(c);
  return c.getContext('2d');
}
function sidebar(el, rows) {
  var h = '';
  for (var i = 0; i < rows.length; i++) h += rows[i][0] + '<br/><b>' + rows[i][1] + '</b><br/>';
  var s = el.querySelector ? el.querySelector('.gs') : null;
  if (!s) { s = document.createElement('div'); s.className = 'gs'; el.appendChild(s); }
  s.innerHTML = h;
}
function best(id, v) {
  var b = parseInt(store('best_' + id) || '0', 10) || 0;
  if (v > b) { store('best_' + id, String(v)); b = v; }
  return b;
}
function banner(ctx, w, h, text) {
  ctx.fillStyle = 'rgba(0,0,0,0.7)'; ctx.fillRect(0, h / 2 - 34, w, 68);
  ctx.fillStyle = '#fff'; ctx.font = 'bold 26px Tiresias, Arial, sans-serif'; ctx.textAlign = 'center'; ctx.fillText(text, w / 2, h / 2 + 9);
}

function gname(g) { return typeof g.name == 'string' ? g.name : (g.name[P.lang] || g.name.en); }
/* Gehaltene Tasten (Paddel, Raumschiff): keydown setzt, keyup loescht; schickt die Fernbedienung kein keyup, gilt die
   Taste 300 ms nach der letzten Wiederholung als losgelassen */
var Held = { k: {}, t: {},
  down: function (k) { var me = this; me.k[k] = true; if (me.t[k]) clearTimeout(me.t[k]); me.t[k] = setTimeout(function () { me.k[k] = false; }, 300); },
  up: function (k) { this.k[k] = false; if (this.t[k]) clearTimeout(this.t[k]); },
  is: function (k) { return !!this.k[k]; }, clear: function () { this.k = {}; } };

/* ------------------------------------------------------------------ Snake */
var Snake = {
  name: 'Snake', desc: { de: 'Wachsen ohne anzustoßen', en: 'Grow without crashing' },
  help: { de: 'Pfeile: lenken  OK: neu/Pause', en: 'Arrows: steer  OK: new/pause' },
  C: 20, W: 25, H: 23,
  start: function (el) { this.el = el; this.ctx = canvasIn(el, this.W * this.C, this.H * this.C, 0, 0); if (this.ctx) this.reset(); },
  reset: function () {
    this.s = [{ x: 8, y: 11 }, { x: 7, y: 11 }, { x: 6, y: 11 }]; this.d = { x: 1, y: 0 }; this.nd = this.d;
    this.score = 0; this.over = false; this.paused = false; this.food(); this.loop(160);
  },
  loop: function (ms) { var me = this; if (this.t) clearInterval(this.t); this.ms = ms; this.t = setInterval(function () { me.step(); }, ms); },
  food: function () {
    var ok, f;
    do { f = { x: Math.floor(Math.random() * this.W), y: Math.floor(Math.random() * this.H) }; ok = true;
      for (var i = 0; i < this.s.length; i++) if (this.s[i].x == f.x && this.s[i].y == f.y) ok = false; } while (!ok);
    this.f = f;
  },
  step: function () {
    if (this.over || this.paused) return;
    this.d = this.nd;
    var h = { x: this.s[0].x + this.d.x, y: this.s[0].y + this.d.y }, i;
    if (h.x < 0 || h.y < 0 || h.x >= this.W || h.y >= this.H) return this.end();
    for (i = 0; i < this.s.length; i++) if (this.s[i].x == h.x && this.s[i].y == h.y) return this.end();
    this.s.unshift(h);
    if (h.x == this.f.x && h.y == this.f.y) { this.score += 10; this.food(); if (this.ms > 70 && this.score % 50 == 0) this.loop(this.ms - 15); }
    else this.s.pop();
    this.draw();
  },
  end: function () { this.over = true; this.draw(); banner(this.ctx, this.W * this.C, this.H * this.C, T.over); },
  draw: function () {
    var c = this.ctx, C = this.C, i;
    c.fillStyle = '#0d1a30'; c.fillRect(0, 0, this.W * C, this.H * C);
    c.fillStyle = '#e2231a'; c.fillRect(this.f.x * C + 3, this.f.y * C + 3, C - 6, C - 6);
    for (i = 0; i < this.s.length; i++) { c.fillStyle = i ? '#4caf50' : '#8bdc5c'; c.fillRect(this.s[i].x * C + 1, this.s[i].y * C + 1, C - 2, C - 2); }
    sidebar(this.el, [[T.score, this.score], [T.best, best('snake', this.score)]]);
  },
  key: function (k) {
    var d = this.d;
    if (k == KEY.UP && d.y != 1) this.nd = { x: 0, y: -1 };
    else if (k == KEY.DOWN && d.y != -1) this.nd = { x: 0, y: 1 };
    else if (k == KEY.LEFT && d.x != 1) this.nd = { x: -1, y: 0 };
    else if (k == KEY.RIGHT && d.x != -1) this.nd = { x: 1, y: 0 };
    else if (k == KEY.OK) { if (this.over) this.reset(); else { this.paused = !this.paused; if (this.paused) banner(this.ctx, this.W * this.C, this.H * this.C, T.pause); } }
  },
  stop: function () { if (this.t) clearInterval(this.t); this.t = null; }
};

/* ------------------------------------------------------------------ Tetris */
var Blocks = {
  name: { de: 'Blöcke', en: 'Blocks' }, desc: { de: 'Reihen füllen', en: 'Fill the rows' },
  help: { de: '◄► schieben  ▲ drehen  ▼ schneller  OK: fallen lassen', en: '◄► move  ▲ rotate  ▼ faster  OK: drop' },
  C: 22, W: 10, H: 20,
  SH: [[[0, 1], [1, 1], [2, 1], [3, 1]], [[0, 0], [1, 0], [0, 1], [1, 1]], [[1, 0], [0, 1], [1, 1], [2, 1]], [[0, 0], [0, 1], [1, 1], [2, 1]],
    [[2, 0], [0, 1], [1, 1], [2, 1]], [[1, 0], [2, 0], [0, 1], [1, 1]], [[0, 0], [1, 0], [1, 1], [2, 1]]],
  COL: ['#00bcd4', '#ffd600', '#ab47bc', '#2979ff', '#ff9100', '#4caf50', '#e53935'],
  start: function (el) { this.el = el; this.ctx = canvasIn(el, this.W * this.C, this.H * this.C, 140, 13); if (this.ctx) this.reset(); },
  reset: function () {
    var x, y; this.b = [];
    for (y = 0; y < this.H; y++) { this.b.push([]); for (x = 0; x < this.W; x++) this.b[y].push(-1); }
    this.score = 0; this.lines = 0; this.over = false; this.nxt = Math.floor(Math.random() * 7); this.spawn(); this.loop(600);
  },
  loop: function (ms) { var me = this; if (this.t) clearInterval(this.t); this.ms = ms; this.t = setInterval(function () { me.fall(); }, ms); },
  spawn: function () {
    this.p = { t: this.nxt, c: this.SH[this.nxt].slice(0), x: 3, y: 0 }; this.nxt = Math.floor(Math.random() * 7);
    if (!this.fits(this.p.c, this.p.x, this.p.y)) { this.over = true; this.draw(); banner(this.ctx, this.W * this.C, this.H * this.C, T.over); }
  },
  fits: function (c, ox, oy) {
    for (var i = 0; i < c.length; i++) { var x = c[i][0] + ox, y = c[i][1] + oy;
      if (x < 0 || x >= this.W || y >= this.H || (y >= 0 && this.b[y][x] >= 0)) return false; }
    return true;
  },
  rot: function () {
    if (this.p.t == 1) return;
    var c = [], i; for (i = 0; i < 4; i++) c.push([2 - this.p.c[i][1], this.p.c[i][0]]);
    if (this.fits(c, this.p.x, this.p.y)) this.p.c = c; else if (this.fits(c, this.p.x - 1, this.p.y)) { this.p.c = c; this.p.x--; } else if (this.fits(c, this.p.x + 1, this.p.y)) { this.p.c = c; this.p.x++; }
  },
  fall: function () {
    if (this.over) return;
    if (this.fits(this.p.c, this.p.x, this.p.y + 1)) { this.p.y++; this.draw(); return; }
    var i, y, x, n = 0;
    for (i = 0; i < 4; i++) if (this.p.c[i][1] + this.p.y >= 0) this.b[this.p.c[i][1] + this.p.y][this.p.c[i][0] + this.p.x] = this.p.t;
    for (y = this.H - 1; y >= 0; y--) {
      var full = true; for (x = 0; x < this.W; x++) if (this.b[y][x] < 0) full = false;
      if (full) { this.b.splice(y, 1); var r = []; for (x = 0; x < this.W; x++) r.push(-1); this.b.unshift(r); y++; n++; }
    }
    if (n) { this.lines += n; this.score += [0, 40, 100, 300, 1200][n]; if (this.ms > 150) this.loop(Math.max(150, 600 - Math.floor(this.lines / 5) * 50)); }
    this.spawn(); if (!this.over) this.draw();
  },
  draw: function () {
    var c = this.ctx, C = this.C, x, y, i;
    c.fillStyle = '#0d1a30'; c.fillRect(0, 0, this.W * C, this.H * C);
    for (y = 0; y < this.H; y++) for (x = 0; x < this.W; x++) if (this.b[y][x] >= 0) { c.fillStyle = this.COL[this.b[y][x]]; c.fillRect(x * C + 1, y * C + 1, C - 2, C - 2); }
    c.fillStyle = this.COL[this.p.t];
    for (i = 0; i < 4; i++) c.fillRect((this.p.c[i][0] + this.p.x) * C + 1, (this.p.c[i][1] + this.p.y) * C + 1, C - 2, C - 2);
    sidebar(this.el, [[T.score, this.score], [T.lines, this.lines], [T.best, best('blocks', this.score)]]);
  },
  key: function (k) {
    if (this.over) { if (k == KEY.OK) this.reset(); return; }
    if (k == KEY.LEFT && this.fits(this.p.c, this.p.x - 1, this.p.y)) this.p.x--;
    else if (k == KEY.RIGHT && this.fits(this.p.c, this.p.x + 1, this.p.y)) this.p.x++;
    else if (k == KEY.UP) this.rot();
    else if (k == KEY.DOWN) { this.fall(); return; }
    else if (k == KEY.OK) { while (this.fits(this.p.c, this.p.x, this.p.y + 1)) { this.p.y++; this.score += 2; } this.fall(); return; }
    this.draw();
  },
  stop: function () { if (this.t) clearInterval(this.t); this.t = null; }
};

/* ------------------------------------------------------------------ 2048 */
var G2048 = {
  name: '2048', desc: { de: 'Zahlen verschmelzen', en: 'Merge the numbers' },
  help: { de: 'Pfeile: schieben  OK: neu', en: 'Arrows: slide  OK: new' },
  COL: { 2: '#eee4da', 4: '#ede0c8', 8: '#f2b179', 16: '#f59563', 32: '#f67c5f', 64: '#f65e3b', 128: '#edcf72', 256: '#edcc61', 512: '#edc850', 1024: '#edc53f', 2048: '#edc22e' },
  start: function (el) { this.el = el; this.reset(); },
  reset: function () { this.g = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]; this.score = 0; this.over = false; this.won = false; this.add(); this.add(); this.draw(); },
  add: function () {
    var e = [], x, y; for (y = 0; y < 4; y++) for (x = 0; x < 4; x++) if (!this.g[y][x]) e.push([x, y]);
    if (!e.length) return; var p = e[Math.floor(Math.random() * e.length)]; this.g[p[1]][p[0]] = Math.random() < 0.9 ? 2 : 4;
  },
  line: function (a) {
    var v = [], i, out = []; for (i = 0; i < 4; i++) if (a[i]) v.push(a[i]);
    for (i = 0; i < v.length; i++) { if (v[i] == v[i + 1]) { out.push(v[i] * 2); this.score += v[i] * 2; if (v[i] * 2 == 2048) this.won = true; i++; } else out.push(v[i]); }
    while (out.length < 4) out.push(0); return out;
  },
  move: function (dir) {
    var moved = false, i, j, a, r;
    for (i = 0; i < 4; i++) {
      a = []; for (j = 0; j < 4; j++) a.push(dir == 0 ? this.g[i][j] : dir == 1 ? this.g[i][3 - j] : dir == 2 ? this.g[j][i] : this.g[3 - j][i]);
      r = this.line(a);
      for (j = 0; j < 4; j++) {
        var x = dir == 0 ? j : dir == 1 ? 3 - j : i, y = dir == 0 || dir == 1 ? i : dir == 2 ? j : 3 - j;
        if (this.g[y][x] != r[j]) moved = true; this.g[y][x] = r[j];
      }
    }
    if (moved) { this.add(); if (!this.canMove()) this.over = true; }
    this.draw();
  },
  canMove: function () {
    for (var y = 0; y < 4; y++) for (var x = 0; x < 4; x++) {
      if (!this.g[y][x]) return true;
      if (x < 3 && this.g[y][x] == this.g[y][x + 1]) return true;
      if (y < 3 && this.g[y][x] == this.g[y + 1][x]) return true;
    }
    return false;
  },
  draw: function () {
    var h = '<div style="position:absolute;left:60px;top:15px;width:440px;height:440px;background:#3a4f70;border-radius:12px"></div>', x, y, v;
    for (y = 0; y < 4; y++) for (x = 0; x < 4; x++) {
      v = this.g[y][x];
      h += '<div class="t2048" style="left:' + (72 + x * 106) + 'px;top:' + (27 + y * 106) + 'px;background:' + (v ? (this.COL[v] || '#3c3a32') : '#4d6386') +
        ';color:' + (v > 4 ? '#fff' : '#3c3a32') + ';font-size:' + (v >= 1024 ? 32 : v >= 128 ? 40 : 48) + 'px">' + (v || '') + '</div>';
    }
    if (this.over || this.won) h += '<div style="position:absolute;left:60px;top:200px;width:440px;height:68px;background:rgba(0,0,0,0.75);text-align:center;font-size:26px;line-height:68px;font-weight:bold">' + (this.won ? T.won : T.over) + '</div>';
    this.el.innerHTML = h;
    sidebar(this.el, [[T.score, this.score], [T.best, best('2048', this.score)]]);
  },
  key: function (k) {
    if (k == KEY.OK) { this.reset(); return; }
    if (this.over || this.won) return;
    if (k == KEY.LEFT) this.move(0); else if (k == KEY.RIGHT) this.move(1); else if (k == KEY.UP) this.move(2); else if (k == KEY.DOWN) this.move(3);
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ Memory */
var Memory = {
  name: { de: 'Paare', en: 'Pairs' }, desc: { de: 'Gleiche Karten finden', en: 'Find matching cards' },
  help: { de: 'Pfeile: wählen  OK: aufdecken', en: 'Arrows: choose  OK: flip' },
  SYM: [['#e53935', 999], ['#ffd600', 0], ['#4caf50', 999], ['#2979ff', 0], ['#ab47bc', 999], ['#ff9100', 0], ['#00bcd4', 999], ['#ffffff', 0]],
  start: function (el) { this.el = el; this.reset(); },
  reset: function () {
    var d = [], i, j, t;
    for (i = 0; i < 8; i++) { d.push(i); d.push(i); }
    for (i = d.length - 1; i > 0; i--) { j = Math.floor(Math.random() * (i + 1)); t = d[i]; d[i] = d[j]; d[j] = t; }
    this.d = d; this.open = []; this.done = {}; this.cur = 0; this.moves = 0; this.lock = false; this.draw();
  },
  draw: function () {
    var h = '', i, x, y, up;
    for (i = 0; i < 16; i++) {
      x = 40 + (i % 4) * 116; y = 8 + Math.floor(i / 4) * 116; up = this.done[i] || this.open[0] === i || this.open[1] === i;
      var s = this.SYM[this.d[i]];
      h += '<div class="card' + (i == this.cur ? ' c' : '') + '" style="left:' + x + 'px;top:' + y + 'px;' + (up ? 'background:#e8eef7' : '') + (this.done[i] ? ';opacity:0.45' : '') + '">' +
        (up ? '<div class="s" style="background:' + s[0] + ';border-radius:' + s[1] + 'px' + (s[0] == '#ffffff' ? ';border:3px solid #24406e;width:54px;height:54px' : '') + '"></div>' : '') + '</div>';
    }
    var n = 0; for (i in this.done) n++;
    if (n == 16) h += '<div style="position:absolute;left:40px;top:200px;width:460px;height:68px;background:rgba(0,0,0,0.75);text-align:center;font-size:26px;line-height:68px;font-weight:bold">' + T.won + '</div>';
    this.el.innerHTML = h;
    sidebar(this.el, [[T.moves, this.moves]]);
  },
  key: function (k) {
    var n = 0, i; for (i in this.done) n++;
    if (n == 16) { if (k == KEY.OK) this.reset(); return; }
    if (k == KEY.LEFT && this.cur % 4) this.cur--;
    else if (k == KEY.RIGHT && this.cur % 4 < 3) this.cur++;
    else if (k == KEY.UP && this.cur >= 4) this.cur -= 4;
    else if (k == KEY.DOWN && this.cur < 12) this.cur += 4;
    else if (k == KEY.OK && !this.lock && !this.done[this.cur] && this.open[0] !== this.cur) {
      this.open.push(this.cur);
      if (this.open.length == 2) {
        this.moves++;
        var a = this.open[0], b = this.open[1], me = this;
        if (this.d[a] == this.d[b]) { this.done[a] = this.done[b] = true; this.open = []; }
        else { this.lock = true; this.t = setTimeout(function () { me.open = []; me.lock = false; me.draw(); }, 900); }
      }
    }
    this.draw();
  },
  stop: function () { if (this.t) clearTimeout(this.t); }
};

var GAMES = [Snake, Blocks, G2048, Memory];
