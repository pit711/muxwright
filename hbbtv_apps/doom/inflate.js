/* Rohes DEFLATE (RFC 1951) entpacken, ES5, fuer alte TV-Browser. inflate(Uint8Array, Ausgabelaenge) -> Uint8Array.
   Eigene Umsetzung fuer Muxwright (MIT). Kanonische Huffman-Codes, bitweise dekodiert (klein statt schnell). */
var inflate = (function () {
  var LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
  var LEXT = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  var DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
    4097, 6145, 8193, 12289, 16385, 24577];
  var DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
  var ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

  // Tabelle: Anzahl Codes je Laenge + Symbole sortiert nach (Laenge, Symbolwert)
  function table(lens, n) {
    var cnt = new Uint16Array(16), start = new Uint16Array(16), sym = new Uint16Array(n), i;
    for (i = 0; i < n; i++) cnt[lens[i]]++;
    cnt[0] = 0;
    for (i = 1; i < 16; i++) start[i] = start[i - 1] + cnt[i - 1];
    for (i = 0; i < n; i++) if (lens[i]) sym[start[lens[i]]++] = i;
    return { cnt: cnt, sym: sym };
  }
  var fixedL, fixedD;
  (function () {
    var l = new Uint8Array(288), d = new Uint8Array(30), i;
    for (i = 0; i < 144; i++) l[i] = 8;
    for (; i < 256; i++) l[i] = 9;
    for (; i < 280; i++) l[i] = 7;
    for (; i < 288; i++) l[i] = 8;
    for (i = 0; i < 30; i++) d[i] = 5;
    fixedL = table(l, 288); fixedD = table(d, 30);
  })();

  return function (src, outLen) {
    var out = new Uint8Array(outLen), op = 0, ip = 0, buf = 0, cnt = 0;
    function bits(n) {
      while (cnt < n) { if (ip >= src.length) throw new Error('inflate: Daten zu kurz'); buf |= src[ip++] << cnt; cnt += 8; }
      var v = buf & ((1 << n) - 1); buf >>>= n; cnt -= n; return v;
    }
    function sym(t) {
      // kanonischer Code: Bit fuer Bit, je Laenge pruefen, ob der Code im Bereich dieser Laenge liegt
      var code = 0, first = 0, idx = 0, len, c;
      for (len = 1; len < 16; len++) {
        code |= bits(1);
        c = t.cnt[len];
        if (code < first + c) return t.sym[idx + code - first];
        idx += c; first = (first + c) << 1; code <<= 1;
      }
      throw new Error('inflate: ungueltiger Code');
    }
    function block(lt, dt) {
      var s, len, dist, i;
      for (;;) {
        s = sym(lt);
        if (s < 256) { out[op++] = s; continue; }
        if (s == 256) return;
        s -= 257;
        len = LBASE[s] + bits(LEXT[s]);
        s = sym(dt);
        dist = DBASE[s] + bits(DEXT[s]);
        for (i = 0; i < len; i++, op++) out[op] = out[op - dist];
      }
    }
    var last, type, n, i, hlit, hdist, hclen, lens, cl, ct, s, prev, rep;
    do {
      last = bits(1); type = bits(2);
      if (type == 0) {
        buf = 0; cnt = 0;                                       // auf Bytegrenze
        n = src[ip] | (src[ip + 1] << 8); ip += 4;
        out.set(src.subarray(ip, ip + n), op); ip += n; op += n;
      } else if (type == 1) {
        block(fixedL, fixedD);
      } else if (type == 2) {
        hlit = bits(5) + 257; hdist = bits(5) + 1; hclen = bits(4) + 4;
        cl = new Uint8Array(19);
        for (i = 0; i < hclen; i++) cl[ORDER[i]] = bits(3);
        ct = table(cl, 19);
        lens = new Uint8Array(hlit + hdist);
        for (i = 0; i < hlit + hdist;) {
          s = sym(ct);
          if (s < 16) { lens[i++] = s; continue; }
          prev = 0;
          if (s == 16) { prev = lens[i - 1]; rep = 3 + bits(2); }
          else if (s == 17) rep = 3 + bits(3);
          else rep = 11 + bits(7);
          while (rep--) lens[i++] = prev;
        }
        block(table(lens.subarray(0, hlit), hlit), table(lens.subarray(hlit), hdist));
      } else throw new Error('inflate: Blocktyp 3');
    } while (!last);
    return op == outLen ? out : out.subarray(0, op);
  };
})();
if (typeof module != 'undefined') module.exports = inflate;
