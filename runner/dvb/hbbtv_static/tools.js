/* Werkzeugkasten fuer das Muxwright-HbbTV-Portal (ES5, eigene Umsetzung, MIT).
   Werkzeug: {name, desc, help, full (Vollbild), start(el), key(k) -> true wenn verarbeitet, keypress(ch) (USB-Tastatur), stop()}.
   Rot beendet jedes Werkzeug (portal.js), Zurueck ebenfalls, sofern das Werkzeug die Taste nicht selbst braucht. */
function tx(de, en) { return P.lang == 'de' ? de : en; }
function h2(s) { return '<div style="font-size:26px;font-weight:bold;margin-bottom:8px">' + s + '</div>'; }
function fmt(n, d) { if (!isFinite(n)) return '–'; var a = Math.abs(n); if (a !== 0 && (a >= 1e9 || a < 1e-4)) return n.toExponential(d || 4).replace('.', P.lang == 'de' ? ',' : '.'); var s = (Math.round(n * Math.pow(10, d == null ? 4 : d)) / Math.pow(10, d == null ? 4 : d)).toString(); return P.lang == 'de' ? s.replace('.', ',') : s; }
var ACX = null;
function actx() { if (!ACX) { try { ACX = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { ACX = null; } } return ACX; }
function beep(f, ms, vol) { var a = actx(); if (!a) return; var o = a.createOscillator(), g = a.createGain(); o.frequency.value = f; g.gain.value = vol || 0.2; o.connect(g); g.connect(a.destination); o.start(0); o.stop(a.currentTime + ms / 1000); }
function lanBase() { return P.ssh_url || null; }
function xhr(method, url, body, cb, timeout) {
  var x = new XMLHttpRequest(), done = false;
  x.open(method, url, true);
  if (timeout) x.timeout = timeout;
  x.onreadystatechange = function () {
    if (x.readyState != 4 || done) return; done = true;
    var j = null; try { j = JSON.parse(x.responseText); } catch (e) {}
    cb(x.status, j, x);
  };
  x.ontimeout = function () { if (!done) { done = true; cb(0, null, x); } };
  try { x.send(body ? (typeof body == 'string' ? body : JSON.stringify(body)) : null); } catch (e) { if (!done) { done = true; cb(0, null, x); } }
}
/* Zahleneingabe mit der Fernbedienung: Ziffern, Gruen = Komma, Gelb = loeschen, Blau = Vorzeichen */
function NumIn(v) { this.s = v == null ? '' : String(v); this.fresh = true; }
NumIn.prototype.key = function (k) {
  // die erste Ziffer (oder das Komma) nach dem Anwaehlen ersetzt den Vorgabewert, statt angehaengt zu werden
  if ((k >= 48 && k <= 57) || k == KEY.GREEN) { if (this.fresh) this.s = ''; this.fresh = false; }
  if (k >= 48 && k <= 57) { if (this.s.length < 12) this.s += String.fromCharCode(k); return true; }
  if (k == KEY.GREEN) { if (this.s.indexOf('.') < 0) this.s += this.s ? '.' : '0.'; return true; }
  if (k == KEY.YELLOW) { this.fresh = false; this.s = this.s.slice(0, -1); return true; }
  if (k == KEY.BLUE) { this.fresh = false; this.s = this.s.charAt(0) == '-' ? this.s.slice(1) : '-' + this.s; return true; }
  return false;
};
NumIn.prototype.val = function () { var v = parseFloat(this.s); return isNaN(v) ? 0 : v; };
NumIn.prototype.txt = function () { return (this.s || '0').replace('.', P.lang == 'de' ? ',' : '.'); };
var NUMHELP = tx('Ziffern  Grün: Komma  Gelb: löschen  Blau: ±', 'Digits  Green: point  Yellow: delete  Blue: ±');

/* ------------------------------------------------------------------ Bildschirmtastatur (QWERTZ) */
var OSK = {
  L: {
    lo: [['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'ß', 'BS'], ['q', 'w', 'e', 'r', 't', 'z', 'u', 'i', 'o', 'p', 'ü', '+'],
      ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ö', 'ä', '#'], ['SHIFT', 'y', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '-', 'ENTER'],
      ['CTRL', 'ESC', 'TAB', 'SPACE', 'LEFT', 'UP', 'DOWN', 'RIGHT', 'SYM']],
    up: [['!', '"', '§', '$', '%', '&', '/', '(', ')', '=', '?', 'BS'], ['Q', 'W', 'E', 'R', 'T', 'Z', 'U', 'I', 'O', 'P', 'Ü', '*'],
      ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'Ö', 'Ä', "'"], ['SHIFT', 'Y', 'X', 'C', 'V', 'B', 'N', 'M', ';', ':', '_', 'ENTER'],
      ['CTRL', 'ESC', 'TAB', 'SPACE', 'LEFT', 'UP', 'DOWN', 'RIGHT', 'SYM']],
    sym: [['@', '€', '{', '}', '[', ']', '\\', '|', '~', '^', '`', 'BS'], ['<', '>', '$', '%', '&', '*', '(', ')', '=', '?', '!', '/'],
      [':', ';', '"', "'", '#', '+', '-', '_', '.', ',', '°', 'µ'], ['SHIFT', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'ENTER'],
      ['CTRL', 'ESC', 'TAB', 'SPACE', 'LEFT', 'UP', 'DOWN', 'RIGHT', 'SYM']]
  },
  LBL: { BS: '⌫', SHIFT: '⇧', ENTER: '↵', CTRL: 'Strg', ESC: 'Esc', TAB: 'Tab', SPACE: '␣', LEFT: '←', UP: '↑', DOWN: '↓', RIGHT: '→', SYM: '@#' },
  vis: false,
  open: function (cb, keep) { this.cb = cb; this.layer = 'lo'; this.ctrl = false; if (!keep) { this.r = 1; this.c = 0; } this.vis = true; this.render(); },
  close: function () { this.vis = false; var e = $('osk'); if (e) e.style.visibility = 'hidden'; },
  render: function () {
    var e = $('osk'), rows = this.L[this.layer], h = '', r, c, k, w, x;
    for (r = 0; r < rows.length; r++) {
      x = 0;
      for (c = 0; c < rows[r].length; c++) {
        k = rows[r][c]; w = k == 'SPACE' ? 260 : r == 4 ? 88 : 84;
        var on = r == this.r && c == this.c, act = (k == 'SHIFT' && this.layer == 'up') || (k == 'CTRL' && this.ctrl) || (k == 'SYM' && this.layer == 'sym');
        h += '<div style="position:absolute;left:' + x + 'px;top:' + (r * 50) + 'px;width:' + (w - 6) + 'px;height:44px;line-height:44px;text-align:center;font-size:24px;border-radius:7px;' +
          'background:' + (on ? '#e8eef7' : act ? '#2e7d32' : '#24406e') + ';color:' + (on ? '#0a1222' : '#fff') + '">' + esc(this.LBL[k] || k) + '</div>';
        x += w;
      }
    }
    e.innerHTML = h + '<div style="position:absolute;left:0;top:252px;font-size:17px;color:#9fb3d1">' + esc(tx('OK: Taste  Zurück: ⌫  Blau: Tastatur zu', 'OK: key  Back: ⌫  Blue: close keyboard')) + '</div>';
    e.style.visibility = 'visible';
  },
  key: function (k) {
    var rows = this.L[this.layer];
    if (k == KEY.UP) { this.r = (this.r + rows.length - 1) % rows.length; this.c = Math.min(this.c, rows[this.r].length - 1); }
    else if (k == KEY.DOWN) { this.r = (this.r + 1) % rows.length; this.c = Math.min(this.c, rows[this.r].length - 1); }
    else if (k == KEY.LEFT) this.c = (this.c + rows[this.r].length - 1) % rows[this.r].length;
    else if (k == KEY.RIGHT) this.c = (this.c + 1) % rows[this.r].length;
    else if (k == KEY.BACK) this.emit('BS');
    else if (k == KEY.BLUE) { this.close(); if (this.cb) this.cb('CLOSE'); return true; }
    else if (k >= 48 && k <= 57) this.emit(String.fromCharCode(k));
    else if (k == KEY.OK) {
      var v = rows[this.r][this.c];
      if (v == 'SHIFT') this.layer = this.layer == 'up' ? 'lo' : 'up';
      else if (v == 'SYM') this.layer = this.layer == 'sym' ? 'lo' : 'sym';
      else if (v == 'CTRL') this.ctrl = !this.ctrl;
      else { this.emit(v == 'SPACE' ? ' ' : v); if (this.layer == 'up' && v.length == 1) this.layer = 'lo'; }
    } else return false;
    this.render();
    return true;
  },
  emit: function (v) { var c = this.ctrl; this.ctrl = false; if (this.cb) this.cb(v, c); }
};

/* ------------------------------------------------------------------ VT100/xterm-Teilmenge fuer das Terminal */
function Term(cols, rows) { this.cols = cols; this.rows = rows; this.reset(); }
Term.prototype.reset = function () {
  this.buf = []; for (var r = 0; r < this.rows; r++) this.buf.push(this.blank());
  this.x = 0; this.y = 0; this.attr = 7; this.top = 0; this.bot = this.rows - 1; this.st = 0; this.par = ''; this.cur = true; this.dirty = true; this.saved = [0, 0];
};
Term.prototype.blank = function () { var l = []; for (var c = 0; c < this.cols; c++) l.push([' ', 7]); return l; };
Term.prototype.scroll = function (n) { while (n-- > 0) { this.buf.splice(this.top, 1); this.buf.splice(this.bot, 0, this.blank()); } };
Term.prototype.rscroll = function (n) { while (n-- > 0) { this.buf.splice(this.bot, 1); this.buf.splice(this.top, 0, this.blank()); } };
Term.prototype.nl = function () { if (this.y == this.bot) this.scroll(1); else if (this.y < this.rows - 1) this.y++; };
Term.prototype.write = function (s) {
  var i, ch, code;
  for (i = 0; i < s.length; i++) {
    ch = s.charAt(i); code = s.charCodeAt(i);
    if (this.st == 0) {
      if (code == 27) { this.st = 1; continue; }
      if (code == 13) { this.x = 0; continue; }
      if (code == 10 || code == 11 || code == 12) { this.nl(); continue; }
      if (code == 8) { if (this.x > 0) this.x--; continue; }
      if (code == 9) { this.x = Math.min(this.cols - 1, (this.x + 8) & ~7); continue; }
      if (code == 7 || code < 32) continue;
      if (this.x >= this.cols) { this.x = 0; this.nl(); }
      this.buf[this.y][this.x] = [ch, this.attr]; this.x++;
    } else if (this.st == 1) {
      if (ch == '[') { this.st = 2; this.par = ''; }
      else if (ch == ']') this.st = 3;
      else if (ch == '(' || ch == ')') this.st = 4;
      else if (ch == '7') { this.saved = [this.x, this.y]; this.st = 0; }
      else if (ch == '8') { this.x = this.saved[0]; this.y = this.saved[1]; this.st = 0; }
      else if (ch == 'M') { if (this.y == this.top) this.rscroll(1); else if (this.y > 0) this.y--; this.st = 0; }
      else if (ch == 'D') { this.nl(); this.st = 0; }
      else if (ch == 'c') { this.reset(); }
      else this.st = 0;
    } else if (this.st == 2) {
      if ((code >= 48 && code <= 63)) this.par += ch;
      else if (code >= 32 && code <= 47) this.par += ch;
      else { this.csi(ch); this.st = 0; }
    } else if (this.st == 3) { if (code == 7) this.st = 0; else if (code == 27) this.st = 5; }
    else if (this.st == 5) { this.st = 0; }
    else if (this.st == 4) { this.st = 0; }
  }
  this.dirty = true;
};
Term.prototype.csi = function (f) {
  var priv = this.par.charAt(0) == '?', p = (priv ? this.par.slice(1) : this.par).split(';'), n = parseInt(p[0], 10), m = parseInt(p[1], 10), i, r, c;
  var N = isNaN(n) || n == 0 ? 1 : n;
  switch (f) {
    case 'A': this.y = Math.max(this.top > this.y ? 0 : this.top, this.y - N); break;
    case 'B': case 'e': this.y = Math.min(this.rows - 1, this.y + N); break;
    case 'C': case 'a': this.x = Math.min(this.cols - 1, this.x + N); break;
    case 'D': this.x = Math.max(0, this.x - N); break;
    case 'E': this.x = 0; this.y = Math.min(this.rows - 1, this.y + N); break;
    case 'F': this.x = 0; this.y = Math.max(0, this.y - N); break;
    case 'G': case '`': this.x = Math.min(this.cols - 1, N - 1); break;
    case 'd': this.y = Math.min(this.rows - 1, N - 1); break;
    case 'H': case 'f': this.y = Math.min(this.rows - 1, (isNaN(n) || !n ? 1 : n) - 1); this.x = Math.min(this.cols - 1, (isNaN(m) || !m ? 1 : m) - 1); break;
    case 'J':
      if (n == 2 || n == 3) { for (r = 0; r < this.rows; r++) this.buf[r] = this.blank(); }
      else if (n == 1) { for (r = 0; r < this.y; r++) this.buf[r] = this.blank(); for (c = 0; c <= this.x; c++) this.buf[this.y][c] = [' ', 7]; }
      else { for (c = this.x; c < this.cols; c++) this.buf[this.y][c] = [' ', 7]; for (r = this.y + 1; r < this.rows; r++) this.buf[r] = this.blank(); }
      break;
    case 'K':
      if (n == 2) this.buf[this.y] = this.blank();
      else if (n == 1) { for (c = 0; c <= this.x && c < this.cols; c++) this.buf[this.y][c] = [' ', 7]; }
      else { for (c = this.x; c < this.cols; c++) this.buf[this.y][c] = [' ', 7]; }
      break;
    case 'X': for (c = this.x; c < Math.min(this.cols, this.x + N); c++) this.buf[this.y][c] = [' ', 7]; break;
    case 'P': this.buf[this.y].splice(this.x, N); while (this.buf[this.y].length < this.cols) this.buf[this.y].push([' ', 7]); break;
    case '@': for (i = 0; i < N; i++) this.buf[this.y].splice(this.x, 0, [' ', 7]); this.buf[this.y].length = this.cols; break;
    case 'L': if (this.y >= this.top && this.y <= this.bot) for (i = 0; i < N; i++) { this.buf.splice(this.bot, 1); this.buf.splice(this.y, 0, this.blank()); } break;
    case 'M': if (this.y >= this.top && this.y <= this.bot) for (i = 0; i < N; i++) { this.buf.splice(this.y, 1); this.buf.splice(this.bot, 0, this.blank()); } break;
    case 'S': this.scroll(N); break;
    case 'T': this.rscroll(N); break;
    case 'r': this.top = (isNaN(n) || !n ? 1 : n) - 1; this.bot = (isNaN(m) || !m ? this.rows : m) - 1; this.x = 0; this.y = 0; break;
    case 's': this.saved = [this.x, this.y]; break;
    case 'u': this.x = this.saved[0]; this.y = this.saved[1]; break;
    case 'h': case 'l':
      if (priv && (n == 25)) this.cur = f == 'h';
      if (priv && (n == 1049 || n == 47 || n == 1047)) { for (r = 0; r < this.rows; r++) this.buf[r] = this.blank(); this.x = 0; this.y = 0; }
      break;
    case 'm':
      for (i = 0; i < p.length; i++) {
        var v = parseInt(p[i], 10); if (isNaN(v)) v = 0;
        if (v == 0) this.attr = 7; else if (v == 1) this.attr |= 0x800; else if (v == 7) this.attr |= 0x1000; else if (v == 27) this.attr &= ~0x1000; else if (v == 22) this.attr &= ~0x800;
        else if (v >= 30 && v <= 37) this.attr = (this.attr & ~0xF) | (v - 30); else if (v >= 90 && v <= 97) this.attr = (this.attr & ~0xF) | (v - 82);
        else if (v == 39) this.attr = (this.attr & ~0xF) | 7;
        else if (v >= 40 && v <= 47) this.attr = (this.attr & ~0xF0) | ((v - 40 + 1) << 4); else if (v >= 100 && v <= 107) this.attr = (this.attr & ~0xF0) | ((v - 92 + 1) << 4);
        else if (v == 49) this.attr &= ~0xF0;
        else if ((v == 38 || v == 48) && p[i + 1] == '5') i += 2; else if ((v == 38 || v == 48) && p[i + 1] == '2') i += 4;
      }
      break;
  }
};
var TCOL = ['#000', '#cd3131', '#0dbc79', '#e5e510', '#2472c8', '#bc3fbc', '#11a8cd', '#e5e5e5', '#666', '#f14c4c', '#23d18b', '#f5f543', '#3b8eea', '#d670d6', '#29b8db', '#fff'];
Term.prototype.html = function () {
  var out = [], r, c, cell, a, run, fg, bg, h, cur;
  for (r = 0; r < this.rows; r++) {
    h = ''; run = ''; a = -1;
    for (c = 0; c < this.cols; c++) {
      cell = this.buf[r][c]; cur = this.cur && r == this.y && c == this.x;
      var at = cur ? (cell[1] ^ 0x1000) : cell[1];
      if (at != a) {
        if (run) h += '<span style="' + this.sty(a) + '">' + esc(run) + '</span>';
        run = ''; a = at;
      }
      run += cell[0];
    }
    if (run) h += '<span style="' + this.sty(a) + '">' + esc(run) + '</span>';
    out.push('<div class="tl">' + h + '</div>');
  }
  return out.join('');
};
Term.prototype.sty = function (a) {
  var fg = (a & 0xF) + ((a & 0x800) && (a & 0xF) < 8 ? 8 : 0), bg = (a >> 4) & 0xF;
  var F = TCOL[fg], B = bg ? TCOL[bg - 1] : 'transparent';
  if (a & 0x1000) { var t = F; F = B == 'transparent' ? '#000' : B; B = t; }
  return 'color:' + F + ';background:' + B;
};

/* ------------------------------------------------------------------ 1 Terminal (SSH ueber den Muxwright-PC) */
var TermTool = {
  name: 'Terminal', desc: { de: 'SSH über den PC', en: 'SSH via the PC' }, full: true,
  help: { de: '', en: '' },
  start: function (el) {
    this.el = el; this.f = { host: store('ssh_host') || '', port: store('ssh_port') || '22', user: store('ssh_user') || '', pass: '' }; this.sel = 0; this.state = lanBase() ? 'form' : 'nolan'; trace('ssh state ' + this.state);
    this.msg = ''; this.out = ''; this.sendT = null; this.draw();
  },
  fields: ['host', 'port', 'user', 'pass'],
  draw: function () {
    var h = '', i, k, lbl = { host: tx('Rechner (Name oder IP)', 'Host (name or IP)'), port: 'Port', user: tx('Benutzer', 'User'), pass: tx('Passwort', 'Password') };
    if (this.state == 'term') return this.drawTerm();
    h += '<div style="position:absolute;left:96px;top:40px;width:1088px">' + h2(tx('Terminal – SSH über den Muxwright-PC', 'Terminal – SSH via the Muxwright PC'));
    if (this.state == 'nolan') { this.el.innerHTML = h + '<div style="font-size:24px">' + tx('Nur möglich, wenn Muxwright im LAN-Modus läuft und das SSH-Terminal in den Portal-Einstellungen erlaubt ist.', 'Only available when Muxwright runs in LAN mode and the SSH terminal is enabled in the portal settings.') + '</div></div>'; return; }
    for (i = 0; i < 4; i++) {
      k = this.fields[i];
      h += '<div style="margin:10px 0;padding:10px 16px;border-radius:8px;font-size:26px;background:' + (this.sel == i && this.state == 'form' ? '#e8eef7;color:#0a1222' : '#1b2a44') + '">' +
        '<span style="display:inline-block;width:330px;opacity:0.7">' + lbl[k] + '</span>' + esc(k == 'pass' ? this.f.pass.replace(/./g, '•') : this.f[k]) + (OSK.vis && this.sel == i ? '▌' : '') + '</div>';
    }
    if (this.state == 'pin') h += '<div style="margin-top:16px;font-size:26px;color:#ffd600">' + tx('PIN eingeben (steht im Muxwright-Protokoll am PC): ', 'Enter PIN (shown in the Muxwright log on the PC): ') + esc(this.pin.s) + '▌</div>';
    h += '<div style="margin-top:16px;font-size:22px;color:#9fb3d1">' + esc(this.msg || tx('▲▼ Feld  OK: bearbeiten  Grün: verbinden  Rot: zurück', '▲▼ field  OK: edit  Green: connect  Red: back')) + '</div></div>';
    this.el.innerHTML = h;
  },
  drawTerm: function () {
    if (!this.t.dirty && this.el.firstChild) return;
    this.t.dirty = false;
    this.el.innerHTML = '<div class="term">' + this.t.html() + '</div><div style="position:absolute;left:64px;top:690px;font-size:16px;color:#9fb3d1">' +
      esc(tx('Pfeile/OK/Ziffern an den Rechner · Blau: Tastatur · Gelb: Strg+C · Grün: Tab · Zurück: ⌫ · Rot: trennen', 'Arrows/OK/digits to host · Blue: keyboard · Yellow: Ctrl+C · Green: Tab · Back: ⌫ · Red: disconnect')) + '</div>';
  },
  edit: function () {
    var me = this, k = this.fields[this.sel];
    OSK.open(function (v) {
      if (v == 'CLOSE' || v == 'ENTER') { OSK.close(); me.draw(); return; }
      if (v == 'BS') me.f[k] = me.f[k].slice(0, -1); else if (v.length == 1) me.f[k] += v;
      me.draw();
    });
    this.draw();
  },
  connect: function () {
    var me = this, base = lanBase();
    store('ssh_host', this.f.host); store('ssh_port', this.f.port); store('ssh_user', this.f.user);
    this.msg = tx('Verbinde …', 'Connecting …'); this.draw();
    trace('ssh open ' + this.f.user + '@' + this.f.host + ':' + this.f.port + (this.pin ? ' pin' : ''));   // ohne Passwort
    xhr('POST', base + 'open', { token: store('ssh_token') || '', pin: this.pin ? this.pin.s : '', host: this.f.host, port: parseInt(this.f.port, 10) || 22,
      user: this.f.user, password: this.f.pass, cols: 80, rows: 24 }, function (st, j) {
      trace('ssh ' + st + ' ' + (j ? (j.ok ? 'ok' : j.need_pin ? 'need_pin' : '') + ' ' + (j.error || '') : 'no json'));
      if (j && j.token) store('ssh_token', j.token);
      if (j && j.need_pin) { me.state = 'pin'; me.pin = new NumIn(''); me.msg = j.error || tx('PIN am Muxwright-PC: ', 'PIN on the Muxwright PC: ') + 'http://127.0.0.1:' + ((/:(\d+)\//.exec(lanBase()) || [0, 8792])[1]) + '/hbbtv/ssh-pin'; return me.draw(); }
      if (!j || !j.ok) { me.state = 'form'; me.msg = '✘ ' + ((j && j.error) || tx('Keine Antwort vom PC', 'No answer from the PC') + ' (' + st + ')'); return me.draw(); }
      store('ssh_token', j.token); me.f.pass = ''; me.sid = j.sid; me.o = 0; me.state = 'term'; me.t = new Term(80, 24);
      if (j.fingerprint) me.t.write('\x1b[90m' + tx('Rechnerschlüssel ', 'Host key ') + j.fingerprint + '\x1b[0m\r\n');
      me.poll(); me.draw();
    }, 20000);
  },
  poll: function () {
    var me = this;
    if (this.state != 'term') return;
    xhr('GET', lanBase() + 'read?sid=' + this.sid + '&t=' + encodeURIComponent(store('ssh_token') || '') + '&o=' + this.o, null, function (st, j) {
      if (me.state != 'term') return;
      if (j && j.ok) { if (j.data) me.t.write(j.data); me.o = j.o; me.drawTerm(); if (j.closed) { me.t.write('\r\n\x1b[93m[' + tx('Verbindung beendet', 'Connection closed') + ']\x1b[0m'); me.drawTerm(); me.state = 'closed'; return; } me.poll(); }
      else setTimeout(function () { me.poll(); }, 1500);
    }, 30000);
  },
  send: function (s) {
    var me = this;
    this.out += s;
    if (this.sendT) return;
    this.sendT = setTimeout(function () {
      var d = me.out; me.out = ''; me.sendT = null;
      xhr('POST', lanBase() + 'write', { sid: me.sid, t: store('ssh_token') || '', d: d }, function () {});
    }, 30);
  },
  oskOut: function (v, ctrl) {
    var map = { ENTER: '\r', BS: '\x7f', TAB: '\t', ESC: '\x1b', UP: '\x1b[A', DOWN: '\x1b[B', RIGHT: '\x1b[C', LEFT: '\x1b[D' };
    if (v == 'CLOSE') return;
    if (map[v]) return this.send(map[v]);
    if (ctrl && v.length == 1) { var c = v.toUpperCase().charCodeAt(0); if (c >= 64 && c <= 95) return this.send(String.fromCharCode(c - 64)); }
    this.send(v);
  },
  key: function (k) {
    var me = this;
    if (this.state == 'term' || this.state == 'closed') {
      if (this.state == 'closed') { if (k == KEY.OK || k == KEY.BACK) { this.state = 'form'; this.msg = ''; this.draw(); } return true; }
      if (OSK.vis) return OSK.key(k);
      var seq = { 38: '\x1b[A', 40: '\x1b[B', 39: '\x1b[C', 37: '\x1b[D' };
      if (k == KEY.UP || k == KEY.DOWN || k == KEY.LEFT || k == KEY.RIGHT) this.send(seq[k == KEY.UP ? 38 : k == KEY.DOWN ? 40 : k == KEY.RIGHT ? 39 : 37]);
      else if (k == KEY.OK) this.send('\r');
      else if (k == KEY.BACK || k == 8) this.send('\x7f');
      else if (k == 27) this.send('\x1b');
      else if (k == 9 || k == KEY.GREEN) this.send('\t');
      else if (k == KEY.YELLOW) this.send('\x03');
      else if (k == KEY.BLUE) OSK.open(function (v, c) { me.oskOut(v, c); });
      else if (k >= 48 && k <= 57) this.send(String.fromCharCode(k));
      return true;
    }
    if (this.state == 'nolan') return false;
    if (OSK.vis) return OSK.key(k);
    if (this.state == 'pin') {
      if (this.pin.key(k)) { this.draw(); return true; }
      if (k == KEY.OK || k == KEY.GREEN) { this.connect(); return true; }
      if (k == KEY.BACK) { this.state = 'form'; this.draw(); return true; }
      return true;
    }
    if (k == KEY.UP) this.sel = (this.sel + 3) % 4;
    else if (k == KEY.DOWN) this.sel = (this.sel + 1) % 4;
    else if (k == KEY.OK) return this.edit(), true;
    else if (k == KEY.GREEN) return this.connect(), true;
    else if (k >= 48 && k <= 57 && this.fields[this.sel] == 'port') this.f.port = (this.f.port + String.fromCharCode(k)).slice(-5);
    else return false;
    this.draw(); return true;
  },
  keypress: function (ch) {
    if (this.state == 'term') return this.send(ch);
    if (this.state == 'form') { var k = this.fields[this.sel]; this.f[k] += ch; this.draw(); }
  },
  stop: function () {
    if (this.state == 'term' && this.sid) xhr('POST', lanBase() + 'close', { sid: this.sid, t: store('ssh_token') || '' }, function () {});
    this.state = 'off'; OSK.close();
  }
};

/* ------------------------------------------------------------------ 2 Notizen */
var Notes = {
  name: { de: 'Notizen', en: 'Notes' }, desc: { de: '5 Zettel, bleiben gespeichert', en: '5 notes, kept on the TV' },
  help: { de: '▲▼ Zettel  OK: schreiben  Gelb: leeren', en: '▲▼ note  OK: write  Yellow: clear' },
  start: function (el) { this.el = el; this.sel = 0; this.draw(); },
  get: function (i) { return store('note' + i) || ''; },
  set: function (i, v) { store('note' + i, v); },
  draw: function () {
    var h = '', i, t;
    for (i = 0; i < 5; i++) { t = this.get(i);
      h += '<div class="ell" style="position:absolute;left:0;top:' + (i * 50) + 'px;width:220px;height:42px;line-height:42px;padding-left:12px;border-radius:8px;font-size:21px;background:' + (i == this.sel ? '#e8eef7;color:#0a1222' : '#1b2a44') + '">' + (i + 1) + '. ' + esc(t.split('\n')[0] || tx('(leer)', '(empty)')) + '</div>'; }
    t = this.get(this.sel);
    h += '<div style="position:absolute;left:250px;top:0;width:490px;height:' + (OSK.vis ? 200 : 450) + 'px;overflow:hidden;background:#fffbe6;color:#222;border-radius:8px;padding:10px;font-size:22px;line-height:30px;white-space:pre-wrap;word-wrap:break-word">' + esc(t) + (OSK.vis || this.typing ? '▌' : '') + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) {
    var me = this;
    if (OSK.vis) return OSK.key(k);
    if (k == KEY.UP) this.sel = (this.sel + 4) % 5;
    else if (k == KEY.DOWN) this.sel = (this.sel + 1) % 5;
    else if (k == KEY.YELLOW) this.set(this.sel, '');
    else if (k == KEY.OK) { OSK.open(function (v) {
      var t = me.get(me.sel);
      if (v == 'CLOSE') return me.draw();
      if (v == 'BS') t = t.slice(0, -1); else if (v == 'ENTER') t += '\n'; else if (v == 'TAB') t += '  '; else if (v.length == 1) t += v;
      me.set(me.sel, t.slice(0, 2000)); me.draw();
    }); }
    else return false;
    this.draw(); return true;
  },
  keypress: function (ch) { this.typing = true; this.set(this.sel, (this.get(this.sel) + ch).slice(0, 2000)); this.draw(); },
  stop: function () { OSK.close(); }
};

/* ------------------------------------------------------------------ 3 Rechner */
var Calc = {
  name: { de: 'Rechner', en: 'Calculator' }, desc: { de: 'Grundrechenarten', en: 'Basic arithmetic' },
  help: { de: 'Pfeile + OK oder Zifferntasten  Grün: =', en: 'Arrows + OK or digit keys  Green: =' },
  K: [['C', '⌫', '%', '÷'], ['7', '8', '9', '×'], ['4', '5', '6', '−'], ['1', '2', '3', '+'], ['±', '0', ',', '=']],
  start: function (el) { this.el = el; this.r = 4; this.c = 1; this.cur = '0'; this.acc = null; this.op = null; this.fresh = true; this.hist = ''; this.draw(); },
  num: function () { return parseFloat(this.cur.replace(',', '.')); },
  apply: function () {
    if (this.op == null || this.acc == null) return;
    var a = this.acc, b = this.num(), r = this.op == '+' ? a + b : this.op == '−' ? a - b : this.op == '×' ? a * b : b === 0 ? NaN : a / b;
    this.hist = fmt(a, 10) + ' ' + this.op + ' ' + fmt(b, 10) + ' =';
    this.cur = isFinite(r) ? fmt(r, 10).replace(/,/g, ',') : 'Error'; this.acc = null; this.op = null; this.fresh = true;
  },
  press: function (v) {
    if (/[0-9]/.test(v)) { this.cur = this.fresh || this.cur == '0' ? v : (this.cur + v).slice(0, 14); this.fresh = false; }
    else if (v == ',') { if (this.fresh) { this.cur = '0,'; this.fresh = false; } else if (this.cur.indexOf(',') < 0) this.cur += ','; }
    else if (v == 'C') { this.cur = '0'; this.acc = null; this.op = null; this.fresh = true; this.hist = ''; }
    else if (v == '⌫') { this.cur = this.cur.length > 1 ? this.cur.slice(0, -1) : '0'; }
    else if (v == '±') { this.cur = this.cur.charAt(0) == '-' ? this.cur.slice(1) : '-' + this.cur; }
    else if (v == '%') { this.cur = fmt(this.num() / 100 * (this.acc != null ? this.acc : 1), 10); }
    else if (v == '=') this.apply();
    else { if (this.op && !this.fresh) this.apply(); this.acc = this.num(); this.op = v; this.fresh = true; this.hist = fmt(this.acc, 10) + ' ' + v; }
    if (this.cur.indexOf('.') >= 0 && P.lang == 'de') this.cur = this.cur.replace('.', ',');
  },
  draw: function () {
    var h = '<div style="position:absolute;left:150px;top:0;width:440px;height:96px;background:#0a1222;border-radius:10px;text-align:right;padding:6px 14px;box-sizing:border-box">' +
      '<div style="font-size:20px;color:#9fb3d1;height:26px">' + esc(this.hist) + '</div><div style="font-size:48px">' + esc(this.cur) + '</div></div>', r, c, v;
    for (r = 0; r < 5; r++) for (c = 0; c < 4; c++) { v = this.K[r][c];
      h += '<div style="position:absolute;left:' + (150 + c * 112) + 'px;top:' + (110 + r * 72) + 'px;width:104px;height:64px;line-height:64px;text-align:center;font-size:30px;border-radius:10px;background:' +
        (r == this.r && c == this.c ? '#e8eef7;color:#0a1222' : c == 3 || v == '=' ? '#2e5aa8' : '#1b2a44') + '">' + v + '</div>'; }
    this.el.innerHTML = h;
  },
  key: function (k) {
    if (k == KEY.UP) this.r = (this.r + 4) % 5; else if (k == KEY.DOWN) this.r = (this.r + 1) % 5;
    else if (k == KEY.LEFT) this.c = (this.c + 3) % 4; else if (k == KEY.RIGHT) this.c = (this.c + 1) % 4;
    else if (k == KEY.OK) this.press(this.K[this.r][this.c]);
    else if (k >= 48 && k <= 57) this.press(String.fromCharCode(k));
    else if (k == KEY.GREEN) this.press('=');
    else if (k == KEY.YELLOW) this.press('⌫');
    else return false;
    this.draw(); return true;
  },
  keypress: function (ch) { var m = { '*': '×', '/': '÷', '-': '−', '+': '+', '.': ',', ',': ',', '=': '=', '%': '%' }; if (/[0-9]/.test(ch)) this.press(ch); else if (m[ch]) this.press(m[ch]); this.draw(); },
  stop: function () {}
};

/* ------------------------------------------------------------------ 4 Stoppuhr */
function hms(ms, cs) { var s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60; return (h ? h + ':' + pad(m) : m) + ':' + pad(s % 60) + (cs ? (P.lang == 'de' ? ',' : '.') + pad(Math.floor(ms / 10) % 100) : ''); }
var Stopwatch = {
  name: { de: 'Stoppuhr', en: 'Stopwatch' }, desc: { de: 'mit Rundenzeiten', en: 'with lap times' },
  help: { de: 'OK: Start/Stopp  Grün: Runde  Gelb: zurücksetzen', en: 'OK: start/stop  Green: lap  Yellow: reset' },
  start: function (el) { var me = this; this.el = el; if (!this.laps) { this.acc = 0; this.t0 = 0; this.laps = []; } this.iv = setInterval(function () { me.draw(); }, 50); this.draw(); },
  now: function () { return this.acc + (this.t0 ? new Date().getTime() - this.t0 : 0); },
  draw: function () {
    var h = '<div style="text-align:center;font-size:120px;line-height:160px;font-family:monospace">' + hms(this.now(), true) + '</div>', i;
    for (i = this.laps.length - 1; i >= Math.max(0, this.laps.length - 6); i--) h += '<div style="text-align:center;font-size:26px;color:#cfe0ff">' + tx('Runde ', 'Lap ') + (i + 1) + ':  ' + hms(this.laps[i], true) + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) {
    var t = new Date().getTime();
    if (k == KEY.OK) { if (this.t0) { this.acc += t - this.t0; this.t0 = 0; } else this.t0 = t; }
    else if (k == KEY.GREEN) { if (this.t0) this.laps.push(this.now()); }
    else if (k == KEY.YELLOW) { this.acc = 0; this.t0 = 0; this.laps = []; }
    else return false;
    this.draw(); return true;
  },
  stop: function () { clearInterval(this.iv); }
};

/* ------------------------------------------------------------------ 5 Timer (Kuechenwecker) */
var Timer = {
  name: { de: 'Timer', en: 'Timer' }, desc: { de: 'Countdown mit Alarm', en: 'Countdown with alarm' },
  help: { de: 'Ziffern: mm:ss  OK: Start/Pause  Grün: +1 min  Gelb: zurück', en: 'Digits: mm:ss  OK: start/pause  Green: +1 min  Yellow: reset' },
  start: function (el) { var me = this; this.el = el; if (this.left == null) { this.digits = '500'; this.left = 0; this.end = 0; } this.iv = setInterval(function () { me.tick(); }, 200); this.draw(); },
  setMs: function () { var d = ('0000' + this.digits).slice(-4); return (parseInt(d.slice(0, 2), 10) * 60 + parseInt(d.slice(2), 10)) * 1000; },
  rem: function () { return this.end ? Math.max(0, this.end - new Date().getTime()) : this.left || this.setMs(); },
  tick: function () {
    if (this.end && this.rem() <= 0) { this.end = 0; this.left = 0; this.alarm = 20; }
    if (this.alarm) { this.alarm--; if (this.alarm % 2) beep(880, 150, 0.3); }
    this.draw();
  },
  draw: function () {
    var r = this.rem(), flash = this.alarm && this.alarm % 2;
    this.el.innerHTML = '<div style="text-align:center;font-size:150px;line-height:220px;font-family:monospace;' + (flash ? 'color:#ff6b6b' : '') + '">' + hms(r + 999 * (this.end ? 1 : 0), false) + '</div>' +
      '<div style="text-align:center;font-size:26px;color:#9fb3d1">' + (this.end ? tx('läuft', 'running') : this.left ? tx('Pause', 'paused') : tx('bereit – Zeit mit den Zifferntasten eingeben', 'ready – enter time with the digit keys')) + '</div>';
  },
  key: function (k) {
    if (k >= 48 && k <= 57 && !this.end) { this.digits = (this.digits + String.fromCharCode(k)).replace(/^0+/, '').slice(-4); this.left = 0; }
    else if (k == KEY.OK) { if (this.end) { this.left = this.rem(); this.end = 0; } else { var r = this.left || this.setMs(); if (r > 0) this.end = new Date().getTime() + r; } this.alarm = 0; }
    else if (k == KEY.GREEN) { if (this.end) this.end += 60000; else { this.left = (this.left || this.setMs()) + 60000; } }
    else if (k == KEY.YELLOW) { this.end = 0; this.left = 0; this.alarm = 0; this.digits = '500'; }
    else return false;
    this.draw(); return true;
  },
  stop: function () { clearInterval(this.iv); }
};

/* ------------------------------------------------------------------ 6 Weltuhr */
var CITIES = [['Berlin', 'Europe/Berlin', 1, 'eu'], ['London', 'Europe/London', 0, 'eu'], ['New York', 'America/New_York', -5, 'us'], ['Los Angeles', 'America/Los_Angeles', -8, 'us'],
  ['São Paulo', 'America/Sao_Paulo', -3, ''], ['Moskau|Moscow', 'Europe/Moscow', 3, ''], ['Dubai', 'Asia/Dubai', 4, ''], ['Neu-Delhi|New Delhi', 'Asia/Kolkata', 5.5, ''],
  ['Bangkok', 'Asia/Bangkok', 7, ''], ['Peking|Beijing', 'Asia/Shanghai', 8, ''], ['Tokio|Tokyo', 'Asia/Tokyo', 9, ''], ['Sydney', 'Australia/Sydney', 10, 'au']];
function lastSun(y, m) { var d = new Date(Date.UTC(y, m + 1, 0)); d.setUTCDate(d.getUTCDate() - d.getUTCDay()); return d; }
function nthSun(y, m, n) { var d = new Date(Date.UTC(y, m, 1)); d.setUTCDate(1 + (7 - d.getUTCDay()) % 7 + (n - 1) * 7); return d; }
function cityTime(c, now) {
  try {
    var f = new Intl.DateTimeFormat('en-GB', { timeZone: c[1], hour: '2-digit', minute: '2-digit', weekday: 'short', hour12: false });
    var p = f.format(now); return { t: p.slice(-5), wd: p.slice(0, 3), exact: true };
  } catch (e) {}
  var y = now.getUTCFullYear(), off = c[2], dst = false;
  if (c[3] == 'eu') { var a = lastSun(y, 2), b = lastSun(y, 9); a.setUTCHours(1); b.setUTCHours(1); dst = now >= a && now < b; }
  else if (c[3] == 'us') { var a2 = nthSun(y, 2, 2), b2 = nthSun(y, 10, 1); a2.setUTCHours(10); b2.setUTCHours(9); dst = now >= a2 && now < b2; }
  else if (c[3] == 'au') { var a3 = nthSun(y, 9, 1), b3 = nthSun(y, 3, 1); dst = now >= a3 || now < b3; }
  var d = new Date(now.getTime() + (off + (dst ? 1 : 0)) * 3600000);
  return { t: pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()), wd: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()], exact: false };
}
var WorldClock = {
  name: { de: 'Weltuhr', en: 'World clock' }, desc: { de: '12 Städte', en: '12 cities' }, help: { de: 'Nur Anzeige', en: 'Display only' },
  start: function (el) { var me = this; this.el = el; this.iv = setInterval(function () { me.draw(); }, 5000); this.draw(); },
  draw: function () {
    var h = '', i, c, t, now = new Date(), WD = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }, hr;
    for (i = 0; i < CITIES.length; i++) {
      c = CITIES[i]; t = cityTime(c, now); hr = parseInt(t.t, 10);
      h += '<div style="position:absolute;left:' + ((i % 3) * 254) + 'px;top:' + (Math.floor(i / 3) * 116) + 'px;width:244px;height:106px;border-radius:10px;background:' + (hr >= 7 && hr < 19 ? '#1f4c8a' : '#141d33') + '">' +
        '<div style="padding:8px 14px;font-size:21px;color:#cfe0ff">' + esc(c[0].split('|')[P.lang == 'de' ? 0 : 1] || c[0]) + '</div>' +
        '<div style="padding:0 14px;font-size:44px">' + t.t + ' <span style="font-size:20px;color:#9fb3d1">' + T.wd[WD[t.wd]] + (t.exact ? '' : ' ≈') + '</span></div></div>';
    }
    this.el.innerHTML = h;
  },
  key: function () { return false; },
  stop: function () { clearInterval(this.iv); }
};

/* ------------------------------------------------------------------ 7 Kalender mit Feiertagen (DE) */
function easter(y) { var a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3),
  h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451), mo = Math.floor((h + l - 7 * m + 114) / 31), da = (h + l - 7 * m + 114) % 31 + 1;
  return new Date(y, mo - 1, da); }
function holidays(y) {
  var e = easter(y), r = {}, add = function (d, n) { r[d.getMonth() + '-' + d.getDate()] = n; }, off = function (n) { return new Date(e.getFullYear(), e.getMonth(), e.getDate() + n); };
  add(new Date(y, 0, 1), 'Neujahr'); add(off(-2), 'Karfreitag'); add(off(1), 'Ostermontag'); add(new Date(y, 4, 1), 'Tag der Arbeit'); add(off(39), 'Christi Himmelfahrt');
  add(off(50), 'Pfingstmontag'); add(new Date(y, 9, 3), 'Tag der Deutschen Einheit'); add(new Date(y, 11, 25), '1. Weihnachtstag'); add(new Date(y, 11, 26), '2. Weihnachtstag');
  return r;
}
function isoWeek(d) { var t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())), n = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - n); var y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1)); return Math.ceil(((t - y0) / 86400000 + 1) / 7); }
var Calendar = {
  name: { de: 'Kalender', en: 'Calendar' }, desc: { de: 'mit Feiertagen', en: 'with holidays' },
  help: { de: '◄► Monat  ▲▼ Jahr  Grün: heute', en: '◄► month  ▲▼ year  Green: today' },
  start: function (el) { this.el = el; var d = new Date(); this.y = d.getFullYear(); this.m = d.getMonth(); this.draw(); },
  draw: function () {
    var d = new Date(this.y, this.m, 1), first = (d.getDay() + 6) % 7, days = new Date(this.y, this.m + 1, 0).getDate(), today = new Date(), hol = P.lang == 'de' ? holidays(this.y) : {}, h, i, r, c, n, wd = P.lang == 'de' ? ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'] : ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
    var MON = P.lang == 'de' ? ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'] : ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    h = '<div style="font-size:28px;font-weight:bold;margin-bottom:6px">' + MON[this.m] + ' ' + this.y + '</div>';
    for (c = 0; c < 7; c++) h += '<div style="position:absolute;left:' + (60 + c * 66) + 'px;top:44px;width:60px;text-align:center;color:#9fb3d1;font-size:20px">' + wd[c] + '</div>';
    var list = [];
    for (i = 0; i < 42; i++) {
      n = i - first + 1; r = Math.floor(i / 7); c = i % 7;
      if (c == 0 && n <= days && n + 6 >= 1) h += '<div style="position:absolute;left:0;top:' + (76 + r * 60) + 'px;width:50px;text-align:center;color:#6f86a8;font-size:17px;line-height:52px">' + isoWeek(new Date(this.y, this.m, Math.max(1, n))) + '</div>';
      if (n < 1 || n > days) continue;
      var key = this.m + '-' + n, isT = today.getFullYear() == this.y && today.getMonth() == this.m && today.getDate() == n;
      if (hol[key]) list.push(n + '. ' + hol[key]);
      h += '<div style="position:absolute;left:' + (60 + c * 66) + 'px;top:' + (76 + r * 60) + 'px;width:60px;height:52px;line-height:52px;text-align:center;font-size:24px;border-radius:8px;background:' +
        (isT ? '#e8eef7;color:#0a1222' : hol[key] ? '#7a2a2a' : c >= 5 ? '#24365a' : '#1b2a44') + '">' + n + '</div>';
    }
    h += '<div style="position:absolute;left:540px;top:44px;width:220px;font-size:19px;line-height:28px;color:#ffcfcf">' + list.map(esc).join('<br/>') + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) {
    if (k == KEY.LEFT) { this.m--; if (this.m < 0) { this.m = 11; this.y--; } }
    else if (k == KEY.RIGHT) { this.m++; if (this.m > 11) { this.m = 0; this.y++; } }
    else if (k == KEY.UP) this.y--; else if (k == KEY.DOWN) this.y++;
    else if (k == KEY.GREEN) { var d = new Date(); this.y = d.getFullYear(); this.m = d.getMonth(); }
    else return false;
    this.draw(); return true;
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ 8 Einheiten */
var UNITS = [
  [{ de: 'Länge', en: 'Length' }, [['mm', 0.001], ['cm', 0.01], ['m', 1], ['km', 1000], ['in', 0.0254], ['ft', 0.3048], ['yd', 0.9144], ['mi', 1609.344], ['sm/nmi', 1852]]],
  [{ de: 'Masse', en: 'Mass' }, [['g', 0.001], ['kg', 1], ['t', 1000], ['oz', 0.028349523], ['lb', 0.45359237]]],
  [{ de: 'Temperatur', en: 'Temperature' }, [['°C', 'c'], ['°F', 'f'], ['K', 'k']]],
  [{ de: 'Volumen', en: 'Volume' }, [['ml', 0.001], ['l', 1], ['m³', 1000], ['gal (US)', 3.785411784], ['fl oz (US)', 0.0295735296], ['Tasse/cup', 0.2365882365]]],
  [{ de: 'Geschwindigkeit', en: 'Speed' }, [['m/s', 1], ['km/h', 1 / 3.6], ['mph', 0.44704], ['kn', 0.514444444]]],
  [{ de: 'Fläche', en: 'Area' }, [['m²', 1], ['ha', 10000], ['km²', 1e6], ['ft²', 0.09290304], ['acre', 4046.8564224]]],
  [{ de: 'Daten', en: 'Data' }, [['B', 1], ['kB', 1e3], ['MB', 1e6], ['GB', 1e9], ['TB', 1e12], ['KiB', 1024], ['MiB', 1048576], ['GiB', 1073741824]]],
  [{ de: 'Energie', en: 'Energy' }, [['J', 1], ['kJ', 1000], ['Wh', 3600], ['kWh', 3.6e6], ['kcal', 4184]]],
  [{ de: 'Leistung', en: 'Power' }, [['W', 1], ['kW', 1000], ['PS', 735.49875], ['hp', 745.699872]]],
  [{ de: 'Druck', en: 'Pressure' }, [['Pa', 1], ['hPa', 100], ['bar', 1e5], ['psi', 6894.757293], ['atm', 101325]]]];
function toBase(cat, v, u) { if (cat != 2) return v * UNITS[cat][1][u][1]; var k = UNITS[2][1][u][1]; return k == 'c' ? v : k == 'f' ? (v - 32) * 5 / 9 : v - 273.15; }
function fromBase(cat, v, u) { if (cat != 2) return v / UNITS[cat][1][u][1]; var k = UNITS[2][1][u][1]; return k == 'c' ? v : k == 'f' ? v * 9 / 5 + 32 : v + 273.15; }
var Units = {
  name: { de: 'Einheiten', en: 'Units' }, desc: { de: '10 Größen umrechnen', en: 'Convert 10 quantities' },
  help: { de: '▲▼ Größe  ◄► Ausgangseinheit  ' + NUMHELP, en: '▲▼ quantity  ◄► source unit  ' + NUMHELP },
  start: function (el) { this.el = el; this.cat = 0; this.u = 2; this.n = new NumIn('1'); this.draw(); },
  draw: function () {
    var U = UNITS[this.cat][1], base = toBase(this.cat, this.n.val(), this.u), h, i;
    h = '<div style="font-size:24px;color:#9fb3d1">' + esc(UNITS[this.cat][0][P.lang] || UNITS[this.cat][0].en) + ' (' + (this.cat + 1) + '/' + UNITS.length + ')</div>' +
      '<div style="font-size:52px;margin:6px 0 14px 0">' + esc(this.n.txt()) + ' <span style="color:#7ec8ff">' + esc(U[this.u][0]) + '</span></div>';
    for (i = 0; i < U.length; i++) h += '<div style="font-size:24px;line-height:36px;' + (i == this.u ? 'color:#7ec8ff' : '') + '"><span style="display:inline-block;width:220px;color:#9fb3d1">' + esc(U[i][0]) + '</span>' + fmt(fromBase(this.cat, base, i), 6) + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) {
    if (this.n.key(k)) { this.draw(); return true; }
    if (k == KEY.UP) { this.cat = (this.cat + UNITS.length - 1) % UNITS.length; this.u = 0; }
    else if (k == KEY.DOWN) { this.cat = (this.cat + 1) % UNITS.length; this.u = 0; }
    else if (k == KEY.LEFT) this.u = (this.u + UNITS[this.cat][1].length - 1) % UNITS[this.cat][1].length;
    else if (k == KEY.RIGHT) this.u = (this.u + 1) % UNITS[this.cat][1].length;
    else return false;
    this.draw(); return true;
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ 9 Waehrungen (EZB-Referenzkurse, vom PC ins Karussell) */
var Currency = {
  name: { de: 'Währungen', en: 'Currencies' }, desc: { de: 'EZB-Referenzkurse', en: 'ECB reference rates' },
  help: { de: '◄► Ausgangswährung  ' + NUMHELP, en: '◄► source currency  ' + NUMHELP },
  SHOW: ['EUR', 'USD', 'GBP', 'CHF', 'JPY', 'PLN', 'CZK', 'SEK', 'DKK', 'NOK', 'HUF', 'TRY', 'CNY', 'THB', 'AUD', 'CAD'],
  start: function (el) { this.el = el; this.cur = 0; this.n = new NumIn('100'); this.draw(); },
  rate: function (c) { return c == 'EUR' ? 1 : (P.fx && P.fx.rates[c]) || null; },
  draw: function () {
    if (!P.fx) { this.el.innerHTML = h2(tx('Keine Kurse', 'No rates')) + '<div style="font-size:22px">' + tx('Der PC konnte die EZB-Kurse nicht abrufen.', 'The PC could not fetch the ECB rates.') + '</div>'; return; }
    var src = this.SHOW[this.cur], eur = this.n.val() / this.rate(src), h, i, c, list = [];
    h = '<div style="font-size:52px;margin:0 0 12px 0">' + esc(this.n.txt()) + ' <span style="color:#7ec8ff">' + src + '</span></div>';
    for (i = 0; i < this.SHOW.length; i++) { c = this.SHOW[i]; if (c == src || !this.rate(c)) continue; list.push(c); }
    for (i = 0; i < list.length; i++) h += '<div style="position:absolute;left:' + ((i % 3) * 254) + 'px;top:' + (80 + Math.floor(i / 3) * 62) + 'px;width:244px;font-size:24px"><span style="color:#9fb3d1">' + list[i] + '</span> ' + fmt(eur * this.rate(list[i]), 2) + '</div>';
    h += '<div style="position:absolute;left:0;top:420px;font-size:17px;color:#9fb3d1">' + tx('Referenzkurse der EZB vom ', 'ECB reference rates of ') + esc(P.fx.date) + tx(' – unverbindlich', ' – for information only') + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) {
    if (this.n.key(k)) { this.draw(); return true; }
    if (k == KEY.LEFT) this.cur = (this.cur + this.SHOW.length - 1) % this.SHOW.length;
    else if (k == KEY.RIGHT) this.cur = (this.cur + 1) % this.SHOW.length;
    else return false;
    this.draw(); return true;
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ 10 HF-Rechner */
function uhfChannel(mhz) { if (mhz >= 470 && mhz <= 862) return 'K' + Math.round((mhz - 474) / 8 + 21); if (mhz >= 174 && mhz <= 230) return 'K' + Math.round((mhz - 177.5) / 7 + 5); return ''; }
var RFCalc = {
  name: { de: 'HF-Rechner', en: 'RF calculator' }, desc: { de: 'λ, Dipol, dBm ↔ Watt', en: 'λ, dipole, dBm ↔ watts' },
  help: { de: '▲▼ Eingabe  ' + NUMHELP, en: '▲▼ input  ' + NUMHELP },
  start: function (el) { this.el = el; this.sel = 0; this.f = new NumIn(P.rf && P.rf.freq_mhz ? String(P.rf.freq_mhz) : '474'); this.p = new NumIn('0'); this.draw(); },
  draw: function () {
    var f = this.f.val(), l = 299.792458 / f, dbm = this.p.val(), w = Math.pow(10, dbm / 10) / 1000, h;
    var row = function (a, b) { return '<div style="font-size:24px;line-height:36px"><span style="display:inline-block;width:330px;color:#9fb3d1">' + a + '</span>' + b + '</div>'; };
    h = '<div style="padding:8px 14px;border-radius:8px;font-size:30px;background:' + (this.sel == 0 ? '#e8eef7;color:#0a1222' : '#1b2a44') + '">' + tx('Frequenz', 'Frequency') + ': ' + esc(this.f.txt()) + ' MHz</div>';
    h += row(tx('Wellenlänge λ', 'Wavelength λ'), fmt(l * 100, 2) + ' cm') + row('λ/2 ' + tx('(Dipol, VF 0,95)', '(dipole, VF 0.95)'), fmt(l * 50 * 0.95, 2) + ' cm') +
      row('λ/4 ' + tx('(Stab, VF 0,95)', '(monopole, VF 0.95)'), fmt(l * 25 * 0.95, 2) + ' cm') + row(tx('Kanal', 'Channel'), uhfChannel(f) || '–');
    h += '<div style="margin-top:14px;padding:8px 14px;border-radius:8px;font-size:30px;background:' + (this.sel == 1 ? '#e8eef7;color:#0a1222' : '#1b2a44') + '">' + tx('Leistung', 'Power') + ': ' + esc(this.p.txt()) + ' dBm</div>';
    h += row(tx('Watt', 'Watts'), w >= 1 ? fmt(w, 3) + ' W' : w >= 0.001 ? fmt(w * 1000, 3) + ' mW' : fmt(w * 1e6, 3) + ' µW') +
      row(tx('Spannung an 50 Ω', 'Voltage at 50 Ω'), fmt(Math.sqrt(w * 50) * 1000, 3) + ' mV') + row('dBµV (50 Ω)', fmt(dbm + 107, 1));
    this.el.innerHTML = h;
  },
  key: function (k) {
    if ((this.sel ? this.p : this.f).key(k)) { this.draw(); return true; }
    if (k == KEY.UP || k == KEY.DOWN) { this.sel = 1 - this.sel; this.draw(); return true; }
    return false;
  },
  stop: function () {}
};

/* ------------------------------------------------------------------ 11 Kanaltabelle */
var Channels = {
  name: { de: 'Kanaltabelle', en: 'Channel table' }, desc: { de: 'VHF/UHF-Frequenzen', en: 'VHF/UHF frequencies' },
  help: { de: '▲▼ blättern', en: '▲▼ scroll' },
  start: function (el) { this.el = el; this.list = []; var i; for (i = 5; i <= 12; i++) this.list.push(['K' + i, 177.5 + (i - 5) * 7, 7]); for (i = 21; i <= 48; i++) this.list.push(['K' + i, 474 + (i - 21) * 8, 8]);
    this.top = 0; for (i = 0; i < this.list.length; i++) if (P.rf && this.list[i][0] == P.rf.channel) this.top = Math.max(0, i - 5); this.draw(); },
  draw: function () {
    var h = '<div style="font-size:20px;color:#9fb3d1;margin-bottom:4px">' + tx('Kanal · Mitte · Bereich (MHz)', 'Channel · centre · range (MHz)') + '</div>', i, c;
    for (i = this.top; i < Math.min(this.list.length, this.top + 12); i++) { c = this.list[i];
      h += '<div style="font-size:24px;line-height:35px;padding-left:10px;border-radius:6px;' + (P.rf && c[0] == P.rf.channel ? 'background:#2e7d32' : '') + '"><span style="display:inline-block;width:110px">' + c[0] + '</span>' +
        '<span style="display:inline-block;width:160px">' + fmt(c[1], 1) + '</span><span style="color:#9fb3d1">' + fmt(c[1] - c[2] / 2, 1) + ' – ' + fmt(c[1] + c[2] / 2, 1) + '</span>' +
        (P.rf && c[0] == P.rf.channel ? '  ● ' + tx('dieser Sender', 'this transmitter') : '') + '</div>'; }
    this.el.innerHTML = h;
  },
  key: function (k) { if (k == KEY.DOWN) this.top = Math.min(this.list.length - 12, this.top + 6); else if (k == KEY.UP) this.top = Math.max(0, this.top - 6); else return false; this.draw(); return true; },
  stop: function () {}
};

/* ------------------------------------------------------------------ 12 Testbilder (Vollbild) */
var TestCards = {
  name: { de: 'Testbilder', en: 'Test patterns' }, desc: { de: 'Overscan, Farben, Schärfe', en: 'Overscan, colours, sharpness' }, full: true,
  help: { de: '', en: '' },
  N: [{ de: 'Sicherer Bereich / Overscan', en: 'Safe area / overscan' }, { de: 'Farbbalken 75 %', en: 'Colour bars 75 %' }, { de: 'Graustufen', en: 'Grey steps' },
    { de: 'Schwarzwert', en: 'Black level' }, { de: 'Weißwert', en: 'White level' }, { de: 'Schärfe', en: 'Sharpness' }, { de: 'Rot', en: 'Red' }, { de: 'Grün', en: 'Green' },
    { de: 'Blau', en: 'Blue' }, { de: 'Weiß', en: 'White' }, { de: 'Schwarz', en: 'Black' }],
  start: function (el) { this.el = el; this.i = 0; this.el.innerHTML = '<canvas id="tc" width="1280" height="720" style="position:absolute;left:0;top:0"></canvas><div id="tcl" style="position:absolute;left:96px;bottom:40px;padding:8px 14px;background:rgba(0,0,0,0.7);font-size:22px;border-radius:8px"></div>'; this.draw(); },
  draw: function () {
    var c = $('tc').getContext('2d'), W = 1280, H = 720, i, x, me = this;
    c.fillStyle = '#000'; c.fillRect(0, 0, W, H);
    switch (this.i) {
      case 0:
        c.fillStyle = '#333'; c.fillRect(0, 0, W, H); c.strokeStyle = '#fff'; c.lineWidth = 1;
        for (x = 0; x <= W; x += 40) { c.beginPath(); c.moveTo(x + 0.5, 0); c.lineTo(x + 0.5, H); c.stroke(); }
        for (x = 0; x <= H; x += 40) { c.beginPath(); c.moveTo(0, x + 0.5); c.lineTo(W, x + 0.5); c.stroke(); }
        c.lineWidth = 4; c.strokeStyle = '#ff4040'; c.strokeRect(2, 2, W - 4, H - 4);
        c.strokeStyle = '#ffd600'; c.strokeRect(W * 0.035, H * 0.035, W * 0.93, H * 0.93);
        c.strokeStyle = '#40ff40'; c.strokeRect(W * 0.05, H * 0.05, W * 0.9, H * 0.9);
        c.strokeStyle = '#40a0ff'; c.strokeRect(W * 0.1, H * 0.1, W * 0.8, H * 0.8);
        c.beginPath(); c.arc(W / 2, H / 2, H * 0.4, 0, 7); c.strokeStyle = '#fff'; c.stroke();
        c.fillStyle = '#fff'; c.font = '20px Arial';
        c.fillText(tx('Rand 0 % (rot) – Action 3,5 % (gelb) – 5 % (grün) – Titel 10 % (blau)', 'Edge 0 % (red) – action 3.5 % (yellow) – 5 % (green) – title 10 % (blue)'), W * 0.12, H * 0.14);
        c.fillText(tx('Sichtbare Linien = was der Fernseher zeigt. Kreis rund = Seitenverhältnis stimmt.', 'Visible lines = what the TV shows. Round circle = correct aspect ratio.'), W * 0.12, H * 0.18);
        break;
      case 1: var bars = ['#bfbfbf', '#bfbf00', '#00bfbf', '#00bf00', '#bf00bf', '#bf0000', '#0000bf', '#000000']; for (i = 0; i < 8; i++) { c.fillStyle = bars[i]; c.fillRect(i * W / 8, 0, W / 8 + 1, H); } break;
      case 2: for (i = 0; i < 11; i++) { var g = Math.round(i * 25.5); c.fillStyle = 'rgb(' + g + ',' + g + ',' + g + ')'; c.fillRect(i * W / 11, 0, W / 11 + 1, H); c.fillStyle = g > 128 ? '#000' : '#fff'; c.font = '24px Arial'; c.fillText(i * 10 + '%', i * W / 11 + 30, H / 2); } break;
      case 3: c.fillStyle = '#000'; c.fillRect(0, 0, W, H); var lv = [2, 4, 6, 8, 10]; for (i = 0; i < lv.length; i++) { var v = Math.round(lv[i] * 2.55); c.fillStyle = 'rgb(' + v + ',' + v + ',' + v + ')'; c.fillRect(200 + i * 180, 200, 140, 320); }
        c.fillStyle = '#555'; c.font = '20px Arial'; c.fillText(tx('2–10 % über Schwarz: Helligkeit so einstellen, dass 2 % gerade noch sichtbar ist', '2–10 % above black: set brightness so 2 % is just visible'), 200, 580); break;
      case 4: c.fillStyle = '#fff'; c.fillRect(0, 0, W, H); var lw = [90, 92, 94, 96, 98]; for (i = 0; i < lw.length; i++) { var v2 = Math.round(lw[i] * 2.55); c.fillStyle = 'rgb(' + v2 + ',' + v2 + ',' + v2 + ')'; c.fillRect(200 + i * 180, 200, 140, 320); }
        c.fillStyle = '#999'; c.font = '20px Arial'; c.fillText(tx('90–98 % Weiß: Kontrast so einstellen, dass alle Felder unterscheidbar sind', '90–98 % white: set contrast so all patches are distinguishable'), 200, 580); break;
      case 5: for (x = 0; x < 640; x += 2) { c.fillStyle = '#fff'; c.fillRect(x, 0, 1, H / 2); } for (x = 640; x < W; x += 4) { c.fillStyle = '#fff'; c.fillRect(x, 0, 2, H / 2); }
        for (i = 0; i < 64; i++) for (x = 0; x < 128; x++) if ((i + x) % 2 == 0) { c.fillStyle = '#fff'; c.fillRect(x * 10, H / 2 + i * 5, 5, 5); } break;
      default: c.fillStyle = ['#f00', '#0f0', '#00f', '#fff', '#000'][this.i - 6]; c.fillRect(0, 0, W, H);
    }
    var l = $('tcl'); l.innerHTML = esc((this.N[this.i][P.lang] || this.N[this.i].en) + '  (' + (this.i + 1) + '/' + this.N.length + ')  ◄►  ' + tx('Rot: Ende', 'Red: exit')); l.style.visibility = 'visible';
    if (this.lt) clearTimeout(this.lt); this.lt = setTimeout(function () { if ($('tcl')) $('tcl').style.visibility = 'hidden'; }, 3000);
  },
  key: function (k) { if (k == KEY.LEFT) this.i = (this.i + this.N.length - 1) % this.N.length; else if (k == KEY.RIGHT || k == KEY.OK) this.i = (this.i + 1) % this.N.length; else return false; this.draw(); return true; },
  stop: function () { if (this.lt) clearTimeout(this.lt); }
};

/* ------------------------------------------------------------------ 13 Tongenerator */
var ToneGen = {
  name: { de: 'Tongenerator', en: 'Tone generator' }, desc: { de: 'Sinus, Rauschen, Sweep', en: 'Sine, noise, sweep' },
  help: { de: 'OK: an/aus  ◄► Frequenz  ▲▼ Signal  Grün: Kanal  Ziffern: Hz', en: 'OK: on/off  ◄► frequency  ▲▼ signal  Green: channel  Digits: Hz' },
  W: ['sine', 'square', 'triangle', 'sawtooth', 'noise', 'sweep'],
  start: function (el) { this.el = el; this.f = 1000; this.w = 0; this.ch = 0; this.on = false; this.typed = ''; broadcastPause(true); this.draw(); },
  play: function () {
    var a = actx(); if (!a) return;
    this.halt();
    var g = a.createGain(); g.gain.value = 0.25;
    var out = g;
    if (a.createStereoPanner) { var p = a.createStereoPanner(); p.pan.value = [0, -1, 1][this.ch]; g.connect(p); out = p; }
    out.connect(a.destination);
    var w = this.W[this.w], src;
    if (w == 'noise') { var b = a.createBuffer(1, a.sampleRate * 2, a.sampleRate), d = b.getChannelData(0), i; for (i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; src = a.createBufferSource(); src.buffer = b; src.loop = true; }
    else { src = a.createOscillator(); src.type = w == 'sweep' ? 'sine' : w; src.frequency.value = this.f;
      if (w == 'sweep') { src.frequency.setValueAtTime(20, a.currentTime); src.frequency.exponentialRampToValueAtTime(20000, a.currentTime + 20); } }
    src.connect(g); src.start(0); this.src = src; this.g = g; this.t0 = a.currentTime;
  },
  halt: function () { if (this.src) { try { this.src.stop(0); } catch (e) {} this.src = null; } },
  draw: function () {
    var names = { sine: tx('Sinus', 'Sine'), square: tx('Rechteck', 'Square'), triangle: tx('Dreieck', 'Triangle'), sawtooth: tx('Sägezahn', 'Sawtooth'), noise: tx('Weißes Rauschen', 'White noise'), sweep: 'Sweep 20 Hz – 20 kHz (20 s)' };
    this.el.innerHTML = '<div style="text-align:center;font-size:96px;line-height:140px;color:' + (this.on ? '#7ddc7d' : '#fff') + '">' + (this.typed ? this.typed + '▌' : this.f + ' Hz') + '</div>' +
      '<div style="text-align:center;font-size:28px;line-height:44px">' + names[this.W[this.w]] + '<br/>' + [tx('beide Kanäle', 'both channels'), tx('nur links', 'left only'), tx('nur rechts', 'right only')][this.ch] +
      '<br/><span style="color:' + (this.on ? '#7ddc7d' : '#9fb3d1') + '">' + (this.on ? tx('● läuft – Fernsehton angehalten', '● playing – TV sound paused') : tx('aus', 'off')) + '</span></div>';
  },
  key: function (k) {
    if (k >= 48 && k <= 57) { this.typed = (this.typed + String.fromCharCode(k)).slice(0, 5); this.draw(); return true; }
    if (k == KEY.OK && this.typed) { this.f = Math.max(10, Math.min(20000, parseInt(this.typed, 10))); this.typed = ''; if (this.on) this.play(); this.draw(); return true; }
    if (k == KEY.OK) { this.on = !this.on; if (this.on) this.play(); else this.halt(); }
    else if (k == KEY.LEFT || k == KEY.RIGHT) { this.f = Math.max(10, Math.min(20000, Math.round(this.f * Math.pow(2, (k == KEY.RIGHT ? 1 : -1) / 6)))); if (this.on && this.src && this.src.frequency) this.src.frequency.value = this.f; }
    else if (k == KEY.UP || k == KEY.DOWN) { this.w = (this.w + (k == KEY.DOWN ? 1 : this.W.length - 1)) % this.W.length; if (this.on) this.play(); }
    else if (k == KEY.GREEN) { this.ch = (this.ch + 1) % 3; if (this.on) this.play(); }
    else return false;
    this.draw(); return true;
  },
  stop: function () { this.halt(); broadcastPause(false); }
};

/* ------------------------------------------------------------------ 14 Tastentest */
var KeyTest = {
  name: { de: 'Tastentest', en: 'Key test' }, desc: { de: 'Fernbedienung prüfen', en: 'Check the remote' },
  help: { de: 'Jede Taste wird angezeigt · Rot: Ende', en: 'Every key is shown · Red: exit' },
  start: function (el) { this.el = el; this.log = []; this.draw(); },
  NAMES: function () { var n = {}, k; for (k in KEY) n[KEY[k]] = k; n[8] = 'BACKSPACE'; n[27] = 'ESC'; n[9] = 'TAB'; for (k = 48; k <= 57; k++) n[k] = 'DIGIT ' + (k - 48); return n; },
  draw: function () {
    var h = '<div style="font-size:22px;color:#9fb3d1;margin-bottom:8px">' + tx('Taste drücken …', 'Press a key …') + '</div>', i, e;
    for (i = 0; i < this.log.length; i++) { e = this.log[i]; h += '<div style="font-size:' + (i ? 24 : 44) + 'px;line-height:' + (i ? 34 : 60) + 'px;' + (i ? 'color:#cfe0ff' : '') + '">' + esc(e) + '</div>'; }
    this.el.innerHTML = h;
  },
  key: function (k) { var n = this.NAMES(); this.log.unshift('keyCode ' + k + '  ' + (n[k] || '')); this.log.length = Math.min(this.log.length, 11); this.draw(); return true; },
  keypress: function (ch) { this.log.unshift('key "' + ch + '"'); this.log.length = Math.min(this.log.length, 11); this.draw(); },
  stop: function () {}
};

/* ------------------------------------------------------------------ 15 Senderinfo */
var TxInfo = {
  name: { de: 'Senderinfo', en: 'Transmitter info' }, desc: { de: 'Multiplex & Fernseher', en: 'Multiplex & TV' }, help: { de: 'Nur Anzeige', en: 'Display only' },
  start: function (el) {
    var c = caps(), m = P.mux || {}, h = '', i, s, yn = function (v) { return v ? '<span style="color:#7ddc7d">✔</span>' : '<span style="color:#ff6b6b">✘</span>'; };
    var row = function (a, b) { return '<div style="font-size:21px;line-height:30px"><span style="display:inline-block;width:230px;color:#9fb3d1">' + a + '</span>' + b + '</div>'; };
    h += row(tx('Standard', 'Standard'), esc((m.std || '').toUpperCase() + ' · ' + (m.mod || ''))) + row(tx('Kanal', 'Channel'), esc((P.rf && P.rf.channel || '') + ' · ' + fmt(P.rf ? P.rf.freq_mhz : 0, 3) + ' MHz')) +
      row(tx('Nutzbitrate', 'Net bit rate'), fmt(m.rate_mbps || 0, 2) + ' Mbit/s') + row('ONID / TSID', esc(m.ids || ''));
    for (i = 0; i < (m.services || []).length && i < 5; i++) { s = m.services[i]; h += row((i ? '' : tx('Programme', 'Services')), esc(s.name + ' · SID ' + s.sid + ' · ' + s.info)); }
    h += '<div style="margin-top:10px;font-size:21px">' + tx('Fernseher: ', 'TV: ') + 'WebAssembly ' + yn(c.wasm) + '  Canvas ' + yn(c.canvas) + '  WebGL ' + yn(c.webgl) + '  localStorage ' + yn(c.ls) + '  Video ' + yn(c.video) + '  ' + c.w + '×' + c.h + '</div>' +
      '<div class="ell dim" style="font-size:16px;line-height:24px;width:760px">' + esc(c.ua) + '</div>';
    el.innerHTML = h;
  },
  key: function () { return false; }, stop: function () {}
};

/* ------------------------------------------------------------------ 16 LAN-Test */
var LanTest = {
  name: { de: 'LAN-Test', en: 'LAN test' }, desc: { de: 'Ping & Tempo zum PC', en: 'Ping & speed to the PC' },
  help: { de: 'OK: messen', en: 'OK: measure' },
  start: function (el) { this.el = el; this.res = null; this.busy = false; this.draw(); },
  draw: function () {
    var h = h2(tx('Verbindung zum Muxwright-PC', 'Connection to the Muxwright PC'));
    if (!lanBase()) h += '<div style="font-size:22px">' + tx('Nur wenn Muxwright im LAN-Modus läuft.', 'Only when Muxwright runs in LAN mode.') + '</div>';
    else if (this.busy) h += '<div style="font-size:28px">' + esc(this.busy) + '</div>';
    else if (this.res) h += '<div style="font-size:40px;line-height:64px">Ping ' + (this.res.ping != null ? fmt(this.res.ping, 0) + ' ms' : '–') + '<br/>' + tx('Tempo ', 'Speed ') + (this.res.mbps != null ? fmt(this.res.mbps, 1) + ' Mbit/s' : '–') + '</div>' +
      '<div style="font-size:20px;color:#9fb3d1">' + tx('Mediathek-Videos brauchen etwa 3–8 Mbit/s.', 'Library videos need about 3–8 Mbit/s.') + '</div>';
    else h += '<div style="font-size:22px">' + tx('OK drücken, um zu messen.', 'Press OK to measure.') + '</div>';
    this.el.innerHTML = h;
  },
  run: function () {
    var me = this, base = lanBase(), n = 0, sum = 0, t0;
    this.busy = 'Ping …'; this.res = {}; this.draw();
    function ping() {
      if (n == 8) { me.res.ping = sum / 8; return speed(); }
      t0 = new Date().getTime();
      xhr('GET', base + 'ping?x=' + t0, null, function (st) { if (st != 200) { me.busy = false; me.res.ping = null; return me.draw(); } sum += new Date().getTime() - t0; n++; ping(); }, 5000);
    }
    function speed() {
      me.busy = tx('Tempo …', 'Speed …'); me.draw();
      var t1 = new Date().getTime();
      xhr('GET', base + 'speed?n=8000000&x=' + t1, null, function (st, j, x) {
        var dt = (new Date().getTime() - t1) / 1000, len = x && x.responseText ? x.responseText.length : 0;
        me.res.mbps = st == 200 && len ? len * 8 / dt / 1e6 : null; me.busy = false; me.draw();
      }, 60000);
    }
    ping();
  },
  key: function (k) { if (k == KEY.OK && !this.busy && lanBase()) { this.run(); return true; } return false; },
  stop: function () {}
};

/* ------------------------------------------------------------------ 17 Wuerfel & Zufall */
var Dice = {
  name: { de: 'Würfel & Zufall', en: 'Dice & random' }, desc: { de: 'Würfel, Münze, Zahl', en: 'Dice, coin, number' },
  help: { de: '◄► Art  ▲▼ Anzahl  Ziffern: Höchstzahl  OK: los', en: '◄► mode  ▲▼ count  Digits: maximum  OK: go' },
  start: function (el) { this.el = el; this.mode = 0; this.n = 2; this.max = new NumIn('100'); this.res = []; this.draw(); },
  face: function (v, x) {
    var P2 = { 1: [[1, 1]], 2: [[0, 0], [2, 2]], 3: [[0, 0], [1, 1], [2, 2]], 4: [[0, 0], [0, 2], [2, 0], [2, 2]], 5: [[0, 0], [0, 2], [1, 1], [2, 0], [2, 2]], 6: [[0, 0], [0, 1], [0, 2], [2, 0], [2, 1], [2, 2]] }, h = '', i;
    for (i = 0; i < P2[v].length; i++) h += '<div style="position:absolute;left:' + (20 + P2[v][i][1] * 36) + 'px;top:' + (20 + P2[v][i][0] * 36) + 'px;width:24px;height:24px;border-radius:12px;background:#111"></div>';
    return '<div style="position:absolute;left:' + x + 'px;top:120px;width:112px;height:112px;border-radius:18px;background:#fff">' + h + '</div>';
  },
  draw: function () {
    var M = [tx('Würfel', 'Dice'), tx('Münze', 'Coin'), tx('Zahl', 'Number'), tx('Ja / Nein', 'Yes / No')], h = '<div style="font-size:28px">◄ ' + M[this.mode] + ' ►' + (this.mode == 0 ? '  ×' + this.n : this.mode == 2 ? '  1 – ' + esc(this.max.txt()) : '') + '</div>', i, sum = 0;
    if (this.mode == 0) { for (i = 0; i < this.res.length; i++) { h += this.face(this.res[i], (760 - this.res.length * 126) / 2 + i * 126); sum += this.res[i]; } if (this.res.length > 1) h += '<div style="position:absolute;left:0;top:260px;width:760px;text-align:center;font-size:30px">Σ ' + sum + '</div>'; }
    else if (this.res.length) h += '<div style="position:absolute;left:0;top:120px;width:760px;text-align:center;font-size:110px">' + esc(String(this.res[0])) + '</div>';
    this.el.innerHTML = h;
  },
  roll: function () {
    var me = this, k = 0, i;
    var one = function () { var r = []; if (me.mode == 0) for (i = 0; i < me.n; i++) r.push(1 + Math.floor(Math.random() * 6));
      else if (me.mode == 1) r.push(Math.random() < 0.5 ? tx('Kopf', 'Heads') : tx('Zahl', 'Tails')); else if (me.mode == 2) r.push(1 + Math.floor(Math.random() * Math.max(1, Math.floor(me.max.val()))));
      else r.push(Math.random() < 0.5 ? tx('Ja', 'Yes') : tx('Nein', 'No')); return r; };
    if (this.iv) clearInterval(this.iv);
    this.iv = setInterval(function () { me.res = one(); me.draw(); beep(300 + Math.random() * 400, 30, 0.08); if (++k > 12) { clearInterval(me.iv); me.iv = null; } }, 70);
  },
  key: function (k) {
    if (this.mode == 2 && this.max.key(k)) { this.draw(); return true; }
    if (k == KEY.LEFT) this.mode = (this.mode + 3) % 4; else if (k == KEY.RIGHT) this.mode = (this.mode + 1) % 4;
    else if (k == KEY.UP && this.mode == 0) this.n = Math.min(5, this.n + 1); else if (k == KEY.DOWN && this.mode == 0) this.n = Math.max(1, this.n - 1);
    else if (k == KEY.OK) return this.roll(), true;
    else return false;
    this.res = []; this.draw(); return true;
  },
  stop: function () { if (this.iv) clearInterval(this.iv); }
};

/* ------------------------------------------------------------------ 18 Laufschrift (Vollbild) */
var Ticker = {
  name: { de: 'Laufschrift', en: 'Scrolling text' }, desc: { de: 'Großer Text im Vollbild', en: 'Big full-screen text' }, full: true,
  help: { de: '', en: '' },
  COL: [['#fff', '#000'], ['#ffd600', '#000'], ['#000', '#fff'], ['#fff', '#c62828'], ['#7ddc7d', '#000'], ['#fff', '#1565c0']],
  start: function (el) { this.el = el; this.text = store('ticker') || tx('Willkommen bei Muxwright TV', 'Welcome to Muxwright TV'); this.col = 0; this.speed = 4; this.run = false; this.draw(); },
  draw: function () {
    var me = this, c = this.COL[this.col];
    if (this.raf) { clearInterval(this.raf); this.raf = null; }
    if (!this.run) {
      this.el.innerHTML = '<div style="position:absolute;left:96px;top:60px;width:1088px">' + h2(tx('Laufschrift', 'Scrolling text')) + '<div style="font-size:30px;padding:14px;border-radius:8px;background:#1b2a44">' + esc(this.text) + (OSK.vis ? '▌' : '') + '</div>' +
        '<div style="margin-top:14px;font-size:22px;color:#9fb3d1">' + tx('OK: Text bearbeiten  Grün: starten  ▲▼ Farbe  ◄► Tempo ', 'OK: edit text  Green: start  ▲▼ colour  ◄► speed ') + this.speed + '</div>' +
        '<div style="margin-top:14px;font-size:40px;padding:10px;color:' + c[0] + ';background:' + c[1] + '">' + esc(this.text.slice(0, 30)) + '</div></div>';
      return;
    }
    this.el.innerHTML = '<div style="position:absolute;left:0;top:0;width:1280px;height:720px;background:' + c[1] + ';overflow:hidden"><div id="tk" style="position:absolute;top:220px;white-space:nowrap;font-size:240px;font-weight:bold;color:' + c[0] + '">' + esc(this.text) + '</div></div>';
    var e = $('tk'), x = 1280;
    this.raf = setInterval(function () { x -= me.speed * 2; if (x < -e.offsetWidth) x = 1280; e.style.left = x + 'px'; }, 20);
  },
  key: function (k) {
    var me = this;
    if (OSK.vis) return OSK.key(k);
    if (this.run) { if (k == KEY.OK || k == KEY.BACK || k == KEY.GREEN) { this.run = false; this.draw(); return true; } if (k == KEY.LEFT || k == KEY.RIGHT) { this.speed = Math.max(1, Math.min(10, this.speed + (k == KEY.RIGHT ? 1 : -1))); return true; } return true; }
    if (k == KEY.OK) { OSK.open(function (v) { if (v == 'CLOSE' || v == 'ENTER') { OSK.close(); store('ticker', me.text); return me.draw(); } if (v == 'BS') me.text = me.text.slice(0, -1); else if (v.length == 1) me.text = (me.text + v).slice(0, 200); me.draw(); }); }
    else if (k == KEY.GREEN) { this.run = true; }
    else if (k == KEY.UP || k == KEY.DOWN) this.col = (this.col + (k == KEY.DOWN ? 1 : this.COL.length - 1)) % this.COL.length;
    else if (k == KEY.LEFT || k == KEY.RIGHT) this.speed = Math.max(1, Math.min(10, this.speed + (k == KEY.RIGHT ? 1 : -1)));
    else return false;
    this.draw(); return true;
  },
  keypress: function (ch) { if (!this.run) { this.text = (this.text + ch).slice(0, 200); store('ticker', this.text); this.draw(); } },
  stop: function () { if (this.raf) clearInterval(this.raf); OSK.close(); }
};

/* ------------------------------------------------------------------ 19 Morse */
var MORSE = { a: '.-', b: '-...', c: '-.-.', d: '-..', e: '.', f: '..-.', g: '--.', h: '....', i: '..', j: '.---', k: '-.-', l: '.-..', m: '--', n: '-.', o: '---', p: '.--.', q: '--.-', r: '.-.', s: '...', t: '-',
  u: '..-', v: '...-', w: '.--', x: '-..-', y: '-.--', z: '--..', '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-', '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
  'ä': '.-.-', 'ö': '---.', 'ü': '..--', '.': '.-.-.-', ',': '--..--', '?': '..--..', '/': '-..-.', '=': '-...-', '@': '.--.-.' };
var Morse = {
  name: 'Morse', desc: { de: 'Text als Morsecode', en: 'Text as Morse code' }, help: { de: 'OK: Text  Grün: abspielen  ◄► Tempo', en: 'OK: text  Green: play  ◄► speed' },
  start: function (el) { this.el = el; this.text = store('morse') || 'CQ DE MUXWRIGHT'; this.wpm = 15; this.lit = false; this.draw(); },
  code: function () { var s = this.text.toLowerCase(), r = [], i; for (i = 0; i < s.length; i++) r.push(s.charAt(i) == ' ' ? '/' : (MORSE[s.charAt(i)] || '')); return r.join(' '); },
  draw: function () {
    this.el.innerHTML = '<div style="font-size:30px;padding:12px;border-radius:8px;background:#1b2a44">' + esc(this.text) + (OSK.vis ? '▌' : '') + '</div>' +
      '<div style="margin-top:14px;font-size:30px;letter-spacing:3px;word-wrap:break-word;color:#ffd600">' + esc(this.code()) + '</div>' +
      '<div style="position:absolute;left:330px;top:300px;width:100px;height:100px;border-radius:50px;background:' + (this.lit ? '#ffd600;box-shadow:0 0 40px #ffd600' : '#333') + '"></div>' +
      '<div style="position:absolute;left:0;top:420px;font-size:22px;color:#9fb3d1">' + this.wpm + ' ' + tx('Wörter/min, 600 Hz', 'words/min, 600 Hz') + '</div>';
  },
  play: function () {
    var a = actx(), me = this, dot = 1.2 / this.wpm, t = a ? a.currentTime + 0.1 : 0, c = this.code(), i, seq = [], tt = 0;
    for (i = 0; i < c.length; i++) { var ch = c.charAt(i);
      if (ch == '.' || ch == '-') { var d = ch == '.' ? dot : 3 * dot; seq.push([tt, d]); tt += d + dot; } else if (ch == ' ') tt += 2 * dot; else if (ch == '/') tt += 4 * dot; }
    if (a) { var o = a.createOscillator(), g = a.createGain(); o.frequency.value = 600; g.gain.value = 0; o.connect(g); g.connect(a.destination);
      for (i = 0; i < seq.length; i++) { g.gain.setValueAtTime(0.25, t + seq[i][0]); g.gain.setValueAtTime(0, t + seq[i][0] + seq[i][1]); }
      o.start(t); o.stop(t + tt + 0.2); }
    if (this.timers) for (i = 0; i < this.timers.length; i++) clearTimeout(this.timers[i]);
    this.timers = [];
    for (i = 0; i < seq.length; i++) (function (s) {
      me.timers.push(setTimeout(function () { me.lit = true; me.draw(); }, (s[0] + 0.1) * 1000));
      me.timers.push(setTimeout(function () { me.lit = false; me.draw(); }, (s[0] + s[1] + 0.1) * 1000));
    })(seq[i]);
  },
  key: function (k) {
    var me = this;
    if (OSK.vis) return OSK.key(k);
    if (k == KEY.OK) OSK.open(function (v) { if (v == 'CLOSE' || v == 'ENTER') { OSK.close(); store('morse', me.text); return me.draw(); } if (v == 'BS') me.text = me.text.slice(0, -1); else if (v.length == 1) me.text = (me.text + v).slice(0, 120); me.draw(); });
    else if (k == KEY.GREEN) this.play();
    else if (k == KEY.LEFT || k == KEY.RIGHT) this.wpm = Math.max(5, Math.min(30, this.wpm + (k == KEY.RIGHT ? 1 : -1)));
    else return false;
    this.draw(); return true;
  },
  keypress: function (ch) { this.text = (this.text + ch).slice(0, 120); store('morse', this.text); this.draw(); },
  stop: function () { var i; if (this.timers) for (i = 0; i < this.timers.length; i++) clearTimeout(this.timers[i]); OSK.close(); }
};

/* ------------------------------------------------------------------ 20 Kaminfeuer (Vollbild, Stimmung) */
var Fire = {
  name: { de: 'Kaminfeuer', en: 'Fireplace' }, desc: { de: 'Gemütliches Vollbild', en: 'Cosy full screen' }, full: true,
  help: { de: '', en: '' },
  W: 160, H: 90,
  start: function (el) {
    var me = this, i;
    el.innerHTML = '<canvas id="fire" width="' + this.W + '" height="' + this.H + '" style="position:absolute;left:0;top:0;width:1280px;height:720px"></canvas>' +
      '<div id="firel" style="position:absolute;left:96px;bottom:40px;font-size:20px;color:#a66;">' + esc(tx('Grün: Knistern an/aus · Rot: Ende', 'Green: crackle on/off · Red: exit')) + '</div>';
    this.c = $('fire').getContext('2d'); this.img = this.c.createImageData(this.W, this.H); this.p = new Uint8Array(this.W * this.H);
    this.pal = []; for (i = 0; i < 256; i++) { var r = Math.min(255, i * 3), g = Math.max(0, Math.min(255, (i - 85) * 3)), b = Math.max(0, Math.min(255, (i - 170) * 3)); this.pal.push([r, g, b]); }
    this.sound = false; this.iv = setInterval(function () { me.step(); }, 40);
    setTimeout(function () { var l = $('firel'); if (l) l.style.visibility = 'hidden'; }, 4000);
  },
  step: function () {
    var W = this.W, H = this.H, p = this.p, x, y, i, d = this.img.data, v;
    // Glut: Herdstellen wandern langsam, dazwischen dunklere Bereiche -> einzelne Flammenzungen
    this.ph = (this.ph || 0) + 0.03;
    for (x = 0; x < W; x++) { var hot = 0.5 + 0.5 * Math.sin(x * 0.11 + this.ph) * Math.sin(x * 0.037 - this.ph * 0.7);
      p[(H - 1) * W + x] = Math.random() < 0.35 + hot * 0.6 ? 255 : Math.floor(Math.random() * 90); }
    for (y = 0; y < H - 1; y++) for (x = 0; x < W; x++) {
      var s = (y + 1) * W + x, l = s - 1 < (y + 1) * W ? s : s - 1, r = s + 1 >= (y + 2) * W ? s : s + 1, b2 = Math.min(H - 1, y + 2) * W + x;
      v = (p[l] + p[s] + p[r] + p[b2]) / 4.12 - Math.random() * 2.4;
      p[y * W + x] = v > 0 ? v : 0;
    }
    for (i = 0; i < W * H; i++) { var c = this.pal[p[i]]; d[i * 4] = c[0]; d[i * 4 + 1] = c[1]; d[i * 4 + 2] = c[2]; d[i * 4 + 3] = 255; }
    this.c.putImageData(this.img, 0, 0);
    if (this.sound && Math.random() < 0.25) { var a = actx(); if (a) { var n = a.createBuffer(1, 400, a.sampleRate), dd = n.getChannelData(0), k; for (k = 0; k < 400; k++) dd[k] = (Math.random() * 2 - 1) * Math.exp(-k / 60);
      var sN = a.createBufferSource(), g = a.createGain(); g.gain.value = 0.15 + Math.random() * 0.25; sN.buffer = n; sN.connect(g); g.connect(a.destination); sN.start(0); } }
  },
  key: function (k) { if (k == KEY.GREEN) { this.sound = !this.sound; if (this.sound) broadcastPause(true); else broadcastPause(false); return true; } return false; },
  stop: function () { clearInterval(this.iv); if (this.sound) broadcastPause(false); }
};

var TOOLS = [TermTool, Notes, Calc, Stopwatch, Timer, WorldClock, Calendar, Units, Currency, RFCalc, Channels, TestCards, ToneGen, KeyTest, TxInfo, LanTest, Dice, Ticker, Morse, Fire];
