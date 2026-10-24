#!/usr/bin/env python3
"""DTMB-LDPC (GB 20600-2006): systematische quasizyklische Generatormatrix aus der Prüfmatrix H ableiten.

Quelle von H: dtmb-sdr (https://github.com/ningzichun/dtmb-sdr, MIT), python/dtmb/data/dtmb_ldpc_rate{1,2,3}.alist,
dort aus GB 20600-2006 Anhang B erzeugt. Codewortordnung wie im Empfänger: [Parität (c·127) | Information (k·127)],
die ersten 5 Paritätsbits werden nicht gesendet (7493 → 7488).

Ergebnis: P^T = H_p^{-1}·H_u in Blöcken von 127×127; jeder Block muss zirkulant sein. Gespeichert wird je Block die
erste Spalte (Parität j, Information i): Bit r von g[i][j] = Paritätsbit (j·127 + r) für Informationsbit (i·127 + 0).
Wegen der Zirkulanz gilt für Informationsbit i·127+s: Paritätsbit j·127 + ((r + s) mod 127).
Ausgabe: lib/dtmb_ldpc_tables.h
Aufruf: gen_dtmb_ldpc.py ALIST_DIR [AUSGABE]
"""
import sys, os
import numpy as np

B = 127

def read_alist(path):
    with open(path) as f:
        tok = f.read().split()
    it = iter(tok)
    n, m = int(next(it)), int(next(it))
    next(it); next(it)
    colw = [int(next(it)) for _ in range(n)]
    roww = [int(next(it)) for _ in range(m)]
    maxc = max(colw)
    for c in range(n):
        for _ in range(maxc):
            next(it)
    maxr = max(roww)
    rows = []
    for r in range(m):
        vals = [int(next(it)) for _ in range(maxr)]
        rows.append([v - 1 for v in vals if v > 0])
    return n, m, rows

def solve(alist):
    n, m, rows = read_alist(alist)
    P = m                     # Paritätsbits (volle Länge)
    K = n - P
    assert P % B == 0 and K % B == 0, (n, m)
    W = (n + 63) // 64
    H = np.zeros((m, W), dtype=np.uint64)
    for r, vs in enumerate(rows):
        for v in vs:
            H[r, v >> 6] |= np.uint64(1) << np.uint64(v & 63)
    # Gauss-Jordan: Spalten 0..P-1 (Parität) zur Einheitsmatrix machen
    for col in range(P):
        w, b = col >> 6, np.uint64(1) << np.uint64(col & 63)
        colbits = (H[:, w] & b) != 0
        cand = np.nonzero(colbits[col:])[0]
        if cand.size == 0:
            raise SystemExit(f"{alist}: H_p singulär bei Spalte {col}")
        piv = col + cand[0]
        if piv != col:
            H[[col, piv]] = H[[piv, col]]
            colbits[[col, piv]] = colbits[[piv, col]]
        colbits[col] = False
        idx = np.nonzero(colbits)[0]
        if idx.size:
            H[idx] ^= H[col]
    # rechte Seite = P^T (P × K)
    bits = np.unpackbits(H.view(np.uint8), axis=1, bitorder='little')[:, :n]
    assert (bits[:, :P] == np.eye(P, dtype=np.uint8)).all()
    PT = bits[:, P:]
    c, k = P // B, K // B
    g = np.zeros((k, c, B), dtype=np.uint8)
    for j in range(c):
        for i in range(k):
            blk = PT[j*B:(j+1)*B, i*B:(i+1)*B]
            first = blk[:, 0]
            for s in range(B):
                if not (blk[:, s] == np.roll(first, s)).all():
                    raise SystemExit(f"{alist}: Block ({j},{i}) nicht zirkulant")
            g[i, j] = first
    # Gegenprobe mit dem Original-H: zufällige Codewörter
    rng = np.random.default_rng(1)
    for _ in range(3):
        u = rng.integers(0, 2, K, dtype=np.uint8)
        p = (PT.astype(np.int64) @ u) & 1
        cw = np.concatenate([p, u])
        for vs in rows:
            assert cw[vs].sum() % 2 == 0
    dens = g.mean()
    return k, c, g, dens

def main():
    d = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), '..', 'lib', 'dtmb_ldpc_tables.h')
    lines = ["/* dtmb_ldpc_tables.h – erzeugt von tools/gen_dtmb_ldpc.py, nicht von Hand ändern.",
             " * Systematische QC-Generatormatrix der DTMB-LDPC-Codes (GB 20600-2006), abgeleitet aus den Prüfmatrizen",
             " * (Anhang B) in dtmb-sdr python/dtmb/data/dtmb_ldpc_rate{1,2,3}.alist (MIT, github.com/ningzichun/dtmb-sdr).",
             " * DTMB_G_r[i][j]: 127-Bit-Spalte (4×32 Bit, Bit r = Wort r/32, Bit r%32) für Informationsblock i, Paritätsblock j."]
    # MIT-Hinweis der Ausgangsdaten mitführen (LICENSE im dtmb-sdr-Klon oberhalb des alist-Verzeichnisses)
    lic, up = None, os.path.abspath(d)
    for _ in range(5):
        if os.path.exists(os.path.join(up, 'LICENSE')):
            lic = open(os.path.join(up, 'LICENSE'), encoding='utf-8').read().strip()
            break
        up = os.path.dirname(up)
    if not lic:
        sys.exit('LICENSE von dtmb-sdr nicht gefunden – der MIT-Hinweis muss in die Tabelle')
    lines += [" *", " * Lizenz der Ausgangsdaten (dtmb-sdr):"] + [(" * " + l) if l else " *" for l in lic.splitlines()]
    lines[-1] += " */"
    lines += ["#ifndef DTMB_LDPC_TABLES_H", "#define DTMB_LDPC_TABLES_H", "#include <stdint.h>"]
    for rate in (1, 2, 3):
        k, c, g, dens = solve(os.path.join(d, f"dtmb_ldpc_rate{rate}.alist"))
        print(f"rate{rate}: k={k} c={c} Dichte={dens:.3f}", file=sys.stderr)
        lines.append(f"static const uint32_t DTMB_G_{rate}[{k}][{c}][4] = {{")
        for i in range(k):
            ent = []
            for j in range(c):
                w = [0, 0, 0, 0]
                for r in range(B):
                    if g[i, j, r]:
                        w[r >> 5] |= 1 << (r & 31)
                ent.append("{" + ",".join(f"0x{x:08x}" for x in w) + "}")
            lines.append("  {" + ",".join(ent) + "},")
        lines.append("};")
    lines.append("#endif")
    with open(out, 'w', newline='\n') as f:
        f.write("\n".join(lines) + "\n")
    print("geschrieben:", out, file=sys.stderr)

if __name__ == '__main__':
    main()
