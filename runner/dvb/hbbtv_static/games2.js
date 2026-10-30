/* Weitere Spiele fuer das Muxwright-HbbTV-Portal (ES5, eigene Umsetzungen, MIT). Spielfeld 760x470 (#game).
   Steuerung mit der Fernbedienung; gehaltene Tasten ueber Held (games.js). */
function stopTimer(g) { if (g.t) clearInterval(g.t); g.t = null; }
function loopOf(g, ms) { var me = g; stopTimer(g); g.t = setInterval(function () { me.step(); }, ms); }

/* ------------------------------------------------------------------ Steinbrecher: Ball, Schlaeger, Mauer */
var Bricks = {
  name: { de: 'Steinbrecher', en: 'Brick Breaker' }, desc: { de: 'Mauer abräumen', en: 'Clear the wall' },
  help: { de: '◄► Schläger  OK: Ball starten', en: '◄► paddle  OK: launch ball' },
  W: 500, H: 460,
  start: function (el) { this.el = el; this.ctx = canvasIn(el, this.W, this.H, 0, 5); if (this.ctx) { this.level = 1; this.score = 0; this.lives = 3; this.reset(); loopOf(this, 20); } },
  reset: function () {
    var r, c; this.b = [];
    for (r = 0; r < 5 + Math.min(this.level, 3); r++) for (c = 0; c < 10; c++) this.b.push({ x: 5 + c * 49, y: 50 + r * 20, a: true, r: r });
    this.px = this.W / 2 - 40; this.serve();
  },
  serve: function () { this.stuck = true; this.bx = this.px + 40; this.by = this.H - 40; this.vx = 3; this.vy = -4 - this.level * 0.5; },
  step: function () {
    if (Held.is(KEY.LEFT)) this.px = Math.max(0, this.px - 8);
    if (Held.is(KEY.RIGHT)) this.px = Math.min(this.W - 80, this.px + 8);
    if (this.stuck) { this.bx = this.px + 40; this.by = this.H - 40; return this.draw(); }
    if (this.over) return;
    this.bx += this.vx; this.by += this.vy;
    if (this.bx < 6 || this.bx > this.W - 6) this.vx = -this.vx;
    if (this.by < 6) this.vy = -this.vy;
    if (this.by > this.H - 34 && this.by < this.H - 22 && this.bx > this.px - 4 && this.bx < this.px + 84 && this.vy > 0) {
      this.vy = -this.vy; this.vx = ((this.bx - this.px - 40) / 40) * 6;
    }
    if (this.by > this.H) { this.lives--; if (this.lives <= 0) { this.over = true; this.draw(); return banner(this.ctx, this.W, this.H, T.over); } this.serve(); }
    var i, k, left = 0;
    for (i = 0; i < this.b.length; i++) {
      k = this.b[i]; if (!k.a) continue; left++;
      if (this.bx > k.x - 5 && this.bx < k.x + 49 && this.by > k.y - 5 && this.by < k.y + 23) { k.a = false; this.vy = -this.vy; this.score += 10 * (5 - Math.min(k.r, 4)); break; }
    }
    if (!left) { this.level++; this.reset(); }
    this.draw();
  },
  draw: function () {
    var c = this.ctx, i, k, col = ['#e53935', '#ff9100', '#ffd600', '#4caf50', '#2979ff', '#ab47bc', '#00bcd4', '#fff'];
    c.fillStyle = '#0d1a30'; c.fillRect(0, 0, this.W, this.H);
    for (i = 0; i < this.b.length; i++) { k = this.b[i]; if (k.a) { c.fillStyle = col[k.r % 8]; c.fillRect(k.x, k.y, 45, 16); } }
    c.fillStyle = '#e8eef7'; c.fillRect(this.px, this.H - 26, 80, 10);
    c.fillStyle = '#fff'; c.beginPath(); c.arc(this.bx, this.by, 6, 0, 7); c.fill();
    sidebar(this.el, [[T.score, this.score], [P.lang == 'de' ? 'Leben' : 'Lives', this.lives], ['Level', this.level], [T.best, best('bricks', this.score)]]);
  },
  key: function (k) { if (k == KEY.OK) { if (this.over) { this.level = 1; this.score = 0; this.lives = 3; this.over = false; this.reset(); } else this.stuck = false; } },
  stop: function () { stopTimer(this); }
};

/* ------------------------------------------------------------------ Tennis: Schlaeger gegen den Fernseher */
var Tennis = {
  name: { de: 'Tennis', en: 'Paddle Duel' }, desc: { de: 'Gegen den Fernseher', en: 'Against the TV' },
  help: { de: '▲▼ Schläger  OK: Aufschlag', en: '▲▼ paddle  OK: serve' },
  W: 500, H: 460,
  start: function (el) { this.el = el; this.ctx = canvasIn(el, this.W, this.H, 0, 5); if (this.ctx) { this.me = 0; this.cpu = 0; this.py = this.cy = this.H / 2 - 40; this.serve(); loopOf(this, 20); } },
  serve: function () { this.wait = true; this.bx = this.W / 2; this.by = this.H / 2; this.vx = (Math.random() < 0.5 ? -1 : 1) * 4; this.vy = (Math.random() - 0.5) * 6; },
  step: function () {
    if (Held.is(KEY.UP)) this.py = Math.max(0, this.py - 8);
    if (Held.is(KEY.DOWN)) this.py = Math.min(this.H - 80, this.py + 8);
    var target = this.by - 40, sp = 4.2 + (this.me - this.cpu) * 0.3;
    this.cy += Math.max(-sp, Math.min(sp, target - this.cy));
    if (!this.wait && !this.over) {
      this.bx += this.vx; this.by += this.vy;
      if (this.by < 6 || this.by > this.H - 6) this.vy = -this.vy;
      if (this.bx < 30 && this.bx > 14 && this.by > this.py - 6 && this.by < this.py + 86 && this.vx < 0) { this.vx = -this.vx * 1.05; this.vy = ((this.by - this.py - 40) / 40) * 6; }
      if (this.bx > this.W - 30 && this.bx < this.W - 14 && this.by > this.cy - 6 && this.by < this.cy + 86 && this.vx > 0) { this.vx = -this.vx * 1.05; this.vy = ((this.by - this.cy - 40) / 40) * 6; }
      if (this.bx < 0) { this.cpu++; this.serve(); }
      if (this.bx > this.W) { this.me++; this.serve(); }
      if (this.me >= 7 || this.cpu >= 7) this.over = true;
    }
    var c = this.ctx, i;
    c.fillStyle = '#0d3b2a'; c.fillRect(0, 0, this.W, this.H);
    c.fillStyle = 'rgba(255,255,255,0.4)'; for (i = 0; i < this.H; i += 24) c.fillRect(this.W / 2 - 2, i, 4, 14);
    c.fillStyle = '#fff'; c.fillRect(16, this.py, 12, 80); c.fillRect(this.W - 28, this.cy, 12, 80);
    c.beginPath(); c.arc(this.bx, this.by, 7, 0, 7); c.fill();
    c.font = 'bold 40px Arial'; c.textAlign = 'center'; c.fillText(this.me, this.W / 2 - 50, 50); c.fillText(this.cpu, this.W / 2 + 50, 50);
    if (this.over) banner(c, this.W, this.H, this.me > this.cpu ? T.won : T.over);
    sidebar(this.el, [[P.lang == 'de' ? 'Du' : 'You', this.me], ['TV', this.cpu], [P.lang == 'de' ? 'Ziel' : 'Goal', 7]]);
  },
  key: function (k) { if (k == KEY.OK) { if (this.over) { this.me = this.cpu = 0; this.over = false; this.serve(); } else this.wait = false; } },
  stop: function () { stopTimer(this); }
};

/* ------------------------------------------------------------------ Minen */
var Mines = {
  name: { de: 'Minen', en: 'Mines' }, desc: { de: 'Felder aufdecken', en: 'Reveal the field' },
  help: { de: 'Pfeile: wählen  OK: aufdecken  Grün: Fahne', en: 'Arrows: choose  OK: reveal  Green: flag' },
  N: 10, M: 14,
  start: function (el) { this.el = el; this.reset(); },
  reset: function () { var i; this.f = []; for (i = 0; i < this.N * this.N; i++) this.f.push({ m: false, o: false, fl: false, n: 0 }); this.cur = 0; this.first = true; this.over = false; this.won = false; this.t0 = 0; this.draw(); },
  plant: function (safe) {
    var n = 0, i, x, y, dx, dy, N = this.N;
    while (n < this.M) { i = Math.floor(Math.random() * N * N); if (i == safe || this.f[i].m) continue; this.f[i].m = true; n++; }
    for (i = 0; i < N * N; i++) { x = i % N; y = Math.floor(i / N); n = 0;
      for (dy = -1; dy <= 1; dy++) for (dx = -1; dx <= 1; dx++) if ((dx || dy) && x + dx >= 0 && x + dx < N && y + dy >= 0 && y + dy < N && this.f[(y + dy) * N + x + dx].m) n++;
      this.f[i].n = n; }
  },
  open: function (i) {
    var s = [i], N = this.N, c, x, y, dx, dy, j;
    while (s.length) {
      c = s.pop(); if (this.f[c].o || this.f[c].fl) continue; this.f[c].o = true;
      if (this.f[c].n || this.f[c].m) continue;
      x = c % N; y = Math.floor(c / N);
      for (dy = -1; dy <= 1; dy++) for (dx = -1; dx <= 1; dx++) { j = (y + dy) * N + x + dx; if (x + dx >= 0 && x + dx < N && y + dy >= 0 && y + dy < N) s.push(j); }
    }
  },
  draw: function () {
    var h = '', i, f, x, y, col = ['', '#59a8ff', '#4caf50', '#e53935', '#ab47bc', '#ff9100', '#00bcd4', '#fff', '#9e9e9e'], left = 0;
    for (i = 0; i < this.N * this.N; i++) {
      f = this.f[i]; x = 30 + (i % this.N) * 44; y = 4 + Math.floor(i / this.N) * 44;
      if (f.fl) left++;
      h += '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:40px;height:40px;line-height:40px;text-align:center;font-weight:bold;font-size:24px;border-radius:5px;' +
        'background:' + (f.o ? (f.m ? '#e53935' : '#1b2a44') : '#3a5a8c') + ';color:' + (col[f.n] || '#fff') + (i == this.cur ? ';box-shadow:0 0 0 3px #fff' : '') + '">' +
        (f.o ? (f.m ? '✹' : (f.n || '')) : (f.fl ? '<span style="color:#ffd600">⚑</span>' : '')) + '</div>';
    }
    if (this.over || this.won) h += '<div style="position:absolute;left:30px;top:190px;width:436px;height:68px;background:rgba(0,0,0,0.8);text-align:center;font-size:26px;line-height:68px;font-weight:bold">' + (this.won ? T.won : T.over) + '</div>';
    this.el.innerHTML = h;
    sidebar(this.el, [[P.lang == 'de' ? 'Minen' : 'Mines', this.M - left], [P.lang == 'de' ? 'Zeit' : 'Time', this.t0 ? Math.floor((new Date().getTime() - this.t0) / 1000) + ' s' : '0 s']]);
  },
  key: function (k) {
    var N = this.N, i;
    if (this.over || this.won) { if (k == KEY.OK) this.reset(); return; }
    if (k == KEY.LEFT && this.cur % N) this.cur--;
    else if (k == KEY.RIGHT && this.cur % N < N - 1) this.cur++;
    else if (k == KEY.UP && this.cur >= N) this.cur -= N;
    else if (k == KEY.DOWN && this.cur < N * (N - 1)) this.cur += N;
    else if (k == KEY.GREEN || k == 48) { if (!this.f[this.cur].o) this.f[this.cur].fl = !this.f[this.cur].fl; }
    else if (k == KEY.OK) {
      if (this.first) { this.plant(this.cur); this.first = false; this.t0 = new Date().getTime(); }
      if (this.f[this.cur].fl) return;
      if (this.f[this.cur].m) { this.over = true; for (i = 0; i < N * N; i++) if (this.f[i].m) this.f[i].o = true; }
      else { this.open(this.cur); var c = 0; for (i = 0; i < N * N; i++) if (!this.f[i].o) c++; if (c == this.M) this.won = true; }
    }
    this.draw();
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ Sudoku (eindeutige Loesung, per Loeser geprueft) */
var Sudoku = {
  name: 'Sudoku', desc: { de: 'Zahlen 1–9', en: 'Numbers 1–9' },
  help: { de: 'Pfeile: Feld  1–9: Zahl  0: löschen  Grün: neu', en: 'Arrows: cell  1–9: number  0: clear  Green: new' },
  start: function (el) { this.el = el; this.newGame(); },
  solve: function (g, limit) {
    var i, v, ok, n = 0, self = this;
    for (i = 0; i < 81; i++) if (!g[i]) break;
    if (i == 81) return 1;
    for (v = 1; v <= 9 && n < limit; v++) {
      if (self.can(g, i, v)) { g[i] = v; n += self.solve(g, limit - n); g[i] = 0; }
    }
    return n;
  },
  can: function (g, i, v) {
    var r = Math.floor(i / 9), c = i % 9, br = r - r % 3, bc = c - c % 3, k;
    for (k = 0; k < 9; k++) if (g[r * 9 + k] == v || g[k * 9 + c] == v || g[(br + Math.floor(k / 3)) * 9 + bc + k % 3] == v) return false;
    return true;
  },
  newGame: function () {
    var base = [], i, r, c, perm = [1, 2, 3, 4, 5, 6, 7, 8, 9], j, t, order = [];
    for (i = 8; i > 0; i--) { j = Math.floor(Math.random() * (i + 1)); t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
    for (r = 0; r < 9; r++) for (c = 0; c < 9; c++) base.push(perm[(r * 3 + Math.floor(r / 3) + c) % 9]);
    // Zeilen innerhalb der Baender und Spalten innerhalb der Stapel mischen (bleibt gueltig)
    for (t = 0; t < 20; t++) {
      var b = Math.floor(Math.random() * 3) * 3, a1 = b + Math.floor(Math.random() * 3), a2 = b + Math.floor(Math.random() * 3), k, x;
      for (k = 0; k < 9; k++) { x = base[a1 * 9 + k]; base[a1 * 9 + k] = base[a2 * 9 + k]; base[a2 * 9 + k] = x; }
      for (k = 0; k < 9; k++) { x = base[k * 9 + a1]; base[k * 9 + a1] = base[k * 9 + a2]; base[k * 9 + a2] = x; }
    }
    this.sol = base.slice(0);
    var g = base.slice(0);
    for (i = 0; i < 81; i++) order.push(i);
    for (i = 80; i > 0; i--) { j = Math.floor(Math.random() * (i + 1)); t = order[i]; order[i] = order[j]; order[j] = t; }
    var removed = 0;
    for (i = 0; i < 81 && removed < 48; i++) { t = g[order[i]]; g[order[i]] = 0; if (this.solve(g.slice(0), 2) != 1) g[order[i]] = t; else removed++; }
    this.fixed = g.map(function (v) { return v > 0; }); this.g = g; this.cur = 0; this.done = false; this.draw();
  },
  draw: function () {
    var h = '', i, r, c, v, bad;
    for (i = 0; i < 81; i++) {
      r = Math.floor(i / 9); c = i % 9; v = this.g[i];
      bad = v && !this.fixed[i] && v != this.sol[i];
      h += '<div style="position:absolute;left:' + (40 + c * 48 + Math.floor(c / 3) * 6) + 'px;top:' + (r * 48 + Math.floor(r / 3) * 6) + 'px;width:44px;height:44px;line-height:44px;text-align:center;font-size:28px;' +
        'background:' + (i == this.cur ? '#e8eef7' : '#1b2a44') + ';color:' + (i == this.cur ? '#0a1222' : bad ? '#ff6b6b' : this.fixed[i] ? '#fff' : '#7ec8ff') + (this.fixed[i] ? ';font-weight:bold' : '') + '">' + (v || '') + '</div>';
    }
    if (this.done) h += '<div style="position:absolute;left:40px;top:200px;width:444px;height:68px;background:rgba(0,0,0,0.8);text-align:center;font-size:26px;line-height:68px;font-weight:bold">' + T.won + '</div>';
    this.el.innerHTML = h;
    var open = 0; for (i = 0; i < 81; i++) if (!this.g[i]) open++;
    sidebar(this.el, [[P.lang == 'de' ? 'Offen' : 'Open', open]]);
  },
  key: function (k) {
    if (k == KEY.GREEN || (this.done && k == KEY.OK)) return this.newGame();
    if (k == KEY.LEFT && this.cur % 9) this.cur--;
    else if (k == KEY.RIGHT && this.cur % 9 < 8) this.cur++;
    else if (k == KEY.UP && this.cur >= 9) this.cur -= 9;
    else if (k == KEY.DOWN && this.cur < 72) this.cur += 9;
    else if (k >= 48 && k <= 57 && !this.fixed[this.cur]) {
      this.g[this.cur] = k - 48;
      var i, full = true; for (i = 0; i < 81; i++) if (this.g[i] != this.sol[i]) full = false;
      this.done = full;
    }
    this.draw();
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ Vier in einer Reihe gegen den Fernseher */
var Four = {
  name: { de: 'Vier in einer Reihe', en: 'Four in a Row' }, desc: { de: 'Gegen den Fernseher', en: 'Against the TV' },
  help: { de: '◄► Spalte  OK: Stein einwerfen', en: '◄► column  OK: drop disc' },
  start: function (el) { this.el = el; this.reset(); },
  reset: function () { var i; this.b = []; for (i = 0; i < 42; i++) this.b.push(0); this.col = 3; this.win = 0; this.busy = false; this.draw(); },
  drop: function (b, c, p) { for (var r = 5; r >= 0; r--) if (!b[r * 7 + c]) { b[r * 7 + c] = p; return r; } return -1; },
  wins: function (b, p) {
    var r, c, d, k, D = [[0, 1], [1, 0], [1, 1], [1, -1]];
    for (r = 0; r < 6; r++) for (c = 0; c < 7; c++) for (d = 0; d < 4; d++) {
      for (k = 0; k < 4; k++) { var rr = r + D[d][0] * k, cc = c + D[d][1] * k; if (rr < 0 || rr > 5 || cc < 0 || cc > 6 || b[rr * 7 + cc] != p) break; }
      if (k == 4) return true;
    }
    return false;
  },
  score: function (b) {   // Bewertung fuer den Fernseher (2) gegen den Spieler (1): offene Zweier/Dreier, Mitte
    var s = 0, r, c, d, k, D = [[0, 1], [1, 0], [1, 1], [1, -1]], a, m;
    for (r = 0; r < 6; r++) if (b[r * 7 + 3] == 2) s += 3; else if (b[r * 7 + 3] == 1) s -= 3;
    for (r = 0; r < 6; r++) for (c = 0; c < 7; c++) for (d = 0; d < 4; d++) {
      a = 0; m = 0;
      for (k = 0; k < 4; k++) { var rr = r + D[d][0] * k, cc = c + D[d][1] * k; if (rr < 0 || rr > 5 || cc < 0 || cc > 6) { a = -1; break; } if (b[rr * 7 + cc] == 2) a++; else if (b[rr * 7 + cc] == 1) m++; }
      if (a < 0 || (a && m)) continue;
      s += [0, 1, 5, 50, 1000][a] - [0, 1, 6, 80, 1000][m];
    }
    return s;
  },
  minimax: function (b, depth, max, al, be) {
    if (this.wins(b, 2)) return 100000 + depth;
    if (this.wins(b, 1)) return -100000 - depth;
    if (!depth) return this.score(b);
    var order = [3, 2, 4, 1, 5, 0, 6], i, c, r, v, best = max ? -1e9 : 1e9, any = false;
    for (i = 0; i < 7; i++) {
      c = order[i]; r = this.drop(b, c, max ? 2 : 1); if (r < 0) continue; any = true;
      v = this.minimax(b, depth - 1, !max, al, be); b[r * 7 + c] = 0;
      if (max) { if (v > best) best = v; if (v > al) al = v; } else { if (v < best) best = v; if (v < be) be = v; }
      if (be <= al) break;
    }
    return any ? best : 0;
  },
  cpu: function () {
    var order = [3, 2, 4, 1, 5, 0, 6], i, c, r, v, best = -1e10, bc = 3;
    for (i = 0; i < 7; i++) { c = order[i]; r = this.drop(this.b, c, 2); if (r < 0) continue; v = this.minimax(this.b, 4, false, -1e9, 1e9); this.b[r * 7 + c] = 0; if (v > best) { best = v; bc = c; } }
    this.drop(this.b, bc, 2);
    if (this.wins(this.b, 2)) this.win = 2; else if (this.full()) this.win = 3;
  },
  full: function () { for (var c = 0; c < 7; c++) if (!this.b[c]) return false; return true; },
  draw: function () {
    var h = '<div style="position:absolute;left:40px;top:60px;width:448px;height:388px;background:#2457b3;border-radius:14px"></div>', i, r, c;
    h += '<div style="position:absolute;left:' + (52 + this.col * 62) + 'px;top:4px;width:50px;height:50px;border-radius:25px;background:#ffd600"></div>';
    for (i = 0; i < 42; i++) { r = Math.floor(i / 7); c = i % 7;
      h += '<div style="position:absolute;left:' + (52 + c * 62) + 'px;top:' + (72 + r * 62) + 'px;width:52px;height:52px;border-radius:26px;background:' + ['#0d1a30', '#ffd600', '#e53935'][this.b[i]] + '"></div>'; }
    if (this.win) h += '<div style="position:absolute;left:40px;top:220px;width:448px;height:68px;background:rgba(0,0,0,0.8);text-align:center;font-size:26px;line-height:68px;font-weight:bold">' +
      (this.win == 1 ? T.won : this.win == 2 ? (P.lang == 'de' ? 'Der Fernseher gewinnt – OK: neu' : 'The TV wins – OK: new') : (P.lang == 'de' ? 'Unentschieden – OK: neu' : 'Draw – OK: new')) + '</div>';
    this.el.innerHTML = h;
    sidebar(this.el, [[P.lang == 'de' ? 'Du: gelb' : 'You: yellow', ''], [P.lang == 'de' ? 'TV: rot' : 'TV: red', '']]);
  },
  key: function (k) {
    var me = this;
    if (this.win) { if (k == KEY.OK) this.reset(); return; }
    if (this.busy) return;
    if (k == KEY.LEFT && this.col > 0) this.col--;
    else if (k == KEY.RIGHT && this.col < 6) this.col++;
    else if (k == KEY.OK && this.drop(this.b, this.col, 1) >= 0) {
      if (this.wins(this.b, 1)) this.win = 1; else if (this.full()) this.win = 3;
      else { this.busy = true; this.draw(); return setTimeout(function () { me.cpu(); me.busy = false; me.draw(); }, 200); }
    }
    this.draw();
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ Sternenabwehr: Weltraum-Shooter (eigene Grafik) */
var Stars = {
  name: { de: 'Sternenabwehr', en: 'Star Defender' }, desc: { de: 'Raumschiff gegen Schwärme', en: 'Ship versus swarms' },
  help: { de: '◄► fliegen  OK: schießen', en: '◄► fly  OK: fire' },
  W: 500, H: 460,
  start: function (el) { this.el = el; this.ctx = canvasIn(el, this.W, this.H, 0, 5); if (this.ctx) { this.score = 0; this.lives = 3; this.wave = 1; this.bg = []; for (var i = 0; i < 60; i++) this.bg.push([Math.random() * this.W, Math.random() * this.H, Math.random() * 2 + 0.5]); this.reset(); loopOf(this, 25); } },
  reset: function () {
    var r, c; this.e = []; this.shots = []; this.bombs = []; this.x = this.W / 2; this.dir = 1; this.cool = 0;
    for (r = 0; r < 4; r++) for (c = 0; c < 8; c++) this.e.push({ x: 40 + c * 52, y: 40 + r * 40, a: true, k: r });
  },
  step: function () {
    var i, j, e, s, alive = 0, edge = false, sp = 0.6 + this.wave * 0.25;
    if (this.over) return;
    if (Held.is(KEY.LEFT)) this.x = Math.max(20, this.x - 6);
    if (Held.is(KEY.RIGHT)) this.x = Math.min(this.W - 20, this.x + 6);
    if (this.cool) this.cool--;
    for (i = 0; i < this.e.length; i++) { e = this.e[i]; if (!e.a) continue; alive++; e.x += this.dir * sp; if (e.x < 20 || e.x > this.W - 20) edge = true;
      if (Math.random() < 0.002 * this.wave) this.bombs.push([e.x, e.y + 10]); if (e.y > this.H - 60) { this.lives = 0; } }
    if (edge) { this.dir = -this.dir; for (i = 0; i < this.e.length; i++) this.e[i].y += 12; }
    for (i = this.shots.length - 1; i >= 0; i--) { s = this.shots[i]; s[1] -= 9; if (s[1] < 0) { this.shots.splice(i, 1); continue; }
      for (j = 0; j < this.e.length; j++) { e = this.e[j]; if (e.a && Math.abs(e.x - s[0]) < 18 && Math.abs(e.y - s[1]) < 14) { e.a = false; this.shots.splice(i, 1); this.score += 10 * (4 - e.k); break; } } }
    for (i = this.bombs.length - 1; i >= 0; i--) { s = this.bombs[i]; s[1] += 4; if (s[1] > this.H) { this.bombs.splice(i, 1); continue; }
      if (Math.abs(s[0] - this.x) < 18 && s[1] > this.H - 40) { this.bombs.splice(i, 1); this.lives--; } }
    if (this.lives <= 0) { this.over = true; this.draw(); return banner(this.ctx, this.W, this.H, T.over); }
    if (!alive) { this.wave++; this.reset(); }
    this.draw();
  },
  draw: function () {
    var c = this.ctx, i, e, col = ['#ff6b6b', '#ffb300', '#4caf50', '#59a8ff'];
    c.fillStyle = '#05070f'; c.fillRect(0, 0, this.W, this.H);
    c.fillStyle = '#9fb3d1'; for (i = 0; i < this.bg.length; i++) { this.bg[i][1] = (this.bg[i][1] + this.bg[i][2] * 0.5) % this.H; c.fillRect(this.bg[i][0], this.bg[i][1], this.bg[i][2], this.bg[i][2]); }
    for (i = 0; i < this.e.length; i++) { e = this.e[i]; if (!e.a) continue; c.fillStyle = col[e.k];
      c.beginPath(); c.moveTo(e.x - 16, e.y - 6); c.lineTo(e.x + 16, e.y - 6); c.lineTo(e.x + 8, e.y + 8); c.lineTo(e.x - 8, e.y + 8); c.closePath(); c.fill();
      c.fillStyle = '#05070f'; c.fillRect(e.x - 7, e.y - 3, 4, 4); c.fillRect(e.x + 3, e.y - 3, 4, 4); }
    c.fillStyle = '#e8eef7'; c.beginPath(); c.moveTo(this.x, this.H - 44); c.lineTo(this.x + 18, this.H - 16); c.lineTo(this.x - 18, this.H - 16); c.closePath(); c.fill();
    c.fillStyle = '#59a8ff'; c.fillRect(this.x - 3, this.H - 32, 6, 8);
    c.fillStyle = '#fff'; for (i = 0; i < this.shots.length; i++) c.fillRect(this.shots[i][0] - 2, this.shots[i][1], 4, 12);
    c.fillStyle = '#ff6b6b'; for (i = 0; i < this.bombs.length; i++) c.fillRect(this.bombs[i][0] - 2, this.bombs[i][1], 4, 10);
    sidebar(this.el, [[T.score, this.score], [P.lang == 'de' ? 'Leben' : 'Lives', this.lives], [P.lang == 'de' ? 'Welle' : 'Wave', this.wave], [T.best, best('stars', this.score)]]);
  },
  key: function (k) {
    if (this.over) { if (k == KEY.OK) { this.score = 0; this.lives = 3; this.wave = 1; this.over = false; this.reset(); } return; }
    if (k == KEY.OK && !this.cool && this.shots.length < 3) { this.shots.push([this.x, this.H - 48]); this.cool = 6; }
  },
  stop: function () { stopTimer(this); }
};

GAMES.push(Bricks, Tennis, Mines, Sudoku, Four, Stars);
