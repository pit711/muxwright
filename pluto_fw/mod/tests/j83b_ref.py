#!/usr/bin/env python3
"""j83b_ref.py – eigene numpy-Referenz für ITU-T J.83 Annex B (SCTE 07), direkt aus dem Normtext (J.83 12/2007, B.4–B.5):
Sender-Stufen (zum Stufenvergleich mit gr-dtv und dvbc_mod) und Bausteine für den Loopback-Empfänger j83b_rx.py
(RS-Decoder, Prüfsummen-Syndrom, Deinterleaver, Randomizer-Folge, Konstellation).

  stages TS QAM CW OUTDIR       Sender-Stufen wie j83b_grdtv.py tx (gleiche Dateinamen) – Referenz aus der Norm
Labels C5..C0 (64-QAM) bzw. C7..C0 (256-QAM) wie Figur B.18/B.19."""
import sys, json, argparse, functools
import numpy as np

# ------------------------------------------------------------------ GF(128), α^7 = α^3 + 1
EXP = [0] * 254
LOG = [0] * 128
_x = 1
for _i in range(127):
    EXP[_i] = EXP[_i + 127] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x80:
        _x ^= 0x89


def gmul(a, b):
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def gpow(e):
    return EXP[e % 127]


def ginv(a):
    return EXP[(127 - LOG[a]) % 127]


# g(x) = Π_{i=1..5} (x + α^i)
GEN = [1]
for _i in range(1, 6):
    r = [0] * (len(GEN) + 1)
    for j, c in enumerate(GEN):
        r[j] ^= c
        r[j + 1] ^= gmul(c, gpow(_i))
    GEN = r  # GEN[0] = x^5-Koeffizient


def rs_encode(m):
    """122 Symbole (m121 zuerst) → 128 Symbole: m121..m0 r4..r0 c_"""
    reg = [0] * 5
    for s in m:
        fb = s ^ reg[0]
        for j in range(4):
            reg[j] = reg[j + 1] ^ gmul(fb, GEN[j + 1])
        reg[4] = gmul(fb, GEN[5])
    cw = list(m) + reg
    # c_ = c(α^6), c(x) = Σ cw[k] x^(126-k)
    acc = 0
    for s in cw:
        acc = gmul(acc, gpow(6)) ^ s
    return cw + [acc]


def _bm_correct(cw, S):
    """Berlekamp-Massey + Chien + Forney auf den Syndromen S = [S_1..S_n] (Wurzeln α^1..α^n) für die 127 Symbole cw.
    Liefert (korrigiertes cw, Anzahl) oder (None, -1)."""
    ns = len(S)
    L, Lam, B, m, b = 0, [1], [1], 1, 1
    for n in range(ns):
        d = S[n]
        for i in range(1, L + 1):
            if i < len(Lam):
                d ^= gmul(Lam[i], S[n - i])
        if d == 0:
            m += 1
            continue
        coef = gmul(d, ginv(b))
        T = Lam[:]
        Bs = [0] * m + B
        if len(Bs) > len(Lam):
            Lam = Lam + [0] * (len(Bs) - len(Lam))
        for i, c in enumerate(Bs):
            Lam[i] ^= gmul(coef, c)
        if 2 * L <= n:
            L, B, b, m = n + 1 - L, T, d, 1
        else:
            m += 1
    if 2 * L > ns:
        return None, -1
    pos = [k for k in range(127)
           if not functools.reduce(lambda v, ic: v ^ (gmul(ic[1], gpow(-(126 - k) * ic[0])) if ic[1] else 0), enumerate(Lam), 0)]
    if len(pos) != L:
        return None, -1
    Om = [0] * ns                      # Ω(x) = S(x)Λ(x) mod x^n, S(x) = Σ S_{i+1} x^i
    for i in range(ns):
        for j, c in enumerate(Lam):
            if i - j >= 0 and c:
                Om[i] ^= gmul(c, S[i - j])
    cw = list(cw)
    for k in pos:
        Xi = ginv(gpow(126 - k))
        num = 0
        for i, c in enumerate(Om):
            if c:
                num ^= gmul(c, gpow(LOG[Xi] * i))
        den = 0
        for i in range(1, len(Lam), 2):
            if Lam[i]:
                den ^= gmul(Lam[i], gpow(LOG[Xi] * (i - 1)))
        if den == 0:
            return None, -1
        cw[k] ^= gmul(num, ginv(den))  # Forney mit fcr = 1: e = Ω(X⁻¹)/Λ'(X⁻¹)
    return cw, len(pos)


def rs_decode(blk):
    """128 Symbole → (122 Daten, korrigierte Symbole oder -1). Erweiterter Code (t = 3): Syndrome S_i = c(α^i), i = 1..5
    aus den ersten 127 Symbolen, S_6 = c(α^6) + c_. Ist c_ selbst falsch, passt das nicht zusammen → zweiter Versuch mit
    S_1..S_5 allein (t = 2 für die 127 Symbole, c_ zählt als dritter Fehler)."""
    cw = list(blk[:127])
    S = []
    for i in range(1, 7):
        acc = 0
        for s in cw:
            acc = gmul(acc, gpow(i)) ^ s
        S.append(acc)
    S[5] ^= blk[127]
    if not any(S):
        return cw[:122], 0
    c, n = _bm_correct(cw, S)
    if n >= 0:
        return c[:122], n
    if not any(S[:5]):
        return cw[:122], 1                 # nur c_ falsch
    c, n = _bm_correct(cw, S[:5])
    if n >= 0:
        return c[:122], n + 1
    return cw[:122], -1


# ------------------------------------------------------------------ MPEG-Paritäts-Prüfsumme (B.4, Figur B.4/B.5/B.6)
C_HEX = ("b0f3 857f 97a5 0ddb eba0 caa3 58c1 2da9 a7ee 67b2 1039 2627 5688 a47c 05c7 78b3 61e7 0aff 2f4a 1bb7 d741 9546 "
         "b182 5b53 4fdc cf64 2072 4c4e ad11 48f8 0b8e f166 c3ce 15fe 5e94 376f ae83 2a8d 6304 b6a6 9fb9 9ec8 40e4 989d "
         "5a22 91f0 171d e2cd 879c 2bfc bd28 6edf 5d06 551a c609 6d4d 3f73 3d90 81c9 313a b445 23e0 2e3b c59b 0f38 57f9 "
         "7a50 ddbe ba0c aa35 8c12 da9a 7ee6 7b21 0392 6275 688a 47c0 5c77 8b36 1e70 aff2 f4a1 bb7d 7419 546b 1825 b534 "
         "fdcc f642 0724 c4ea d114 8f")
CVEC = np.array([int(b) for b in "".join(bin(int(h, 16))[2:].zfill(4 * len(h)) for h in C_HEX.split())] + [1], np.uint8)
assert len(CVEC) == 1497
# P (1504 × 8): Spalte k = C um k nach unten verschoben; Zeile i → 8-Bit-Wert (Spalte 0 = MSB)
PROW = np.zeros(1504, np.int64)
for _k in range(8):
    PROW[_k:_k + 1497] |= CVEC.astype(np.int64) << (7 - _k)


def syndrome(bits1504):
    nz = np.nonzero(bits1504)[0]
    return int(np.bitwise_xor.reduce(PROW[nz])) if len(nz) else 0


W7 = np.array([64, 32, 16, 8, 4, 2, 1], np.uint8)


def to7(bits):
    n = len(bits) // 7 * 7
    return (np.asarray(bits[:n], np.uint8).reshape(-1, 7) * W7).sum(1).astype(np.uint8)


def checksum(payload187):
    bits = np.unpackbits(np.frombuffer(bytes(payload187), np.uint8))
    s = syndrome(np.concatenate([bits, np.zeros(8, np.uint8)]))
    # Prüfbits c (8) so, dass s ^ Σ c_j PROW[1496+j] = 47h – Dreieckssystem (vorwärts)
    want = s ^ 0x47
    c = 0
    for j in range(8):
        # Beitrag von Bit j: PROW[1496+j]; Bit (7-j) im Ergebnis hängt nur von Bits 0..j ab (C[1496] = 1)
        if (want >> (7 - j)) & 1:
            c |= 1 << (7 - j)
            want ^= int(PROW[1496 + j])
    assert want == 0
    return c


def framing(ts):
    """188er-Pakete → je Paket 187 Nutzbytes + Prüfsumme (ersetzt das Sync-Byte des Folgepakets)."""
    out = bytearray()
    for p in range(len(ts) // 188):
        pl = ts[p * 188 + 1:(p + 1) * 188]
        out += pl + bytes([checksum(pl)])
    return bytes(out)


# ------------------------------------------------------------------ Rahmen, Interleaver, Randomizer
CW_IJ = {0: (128, 1), 1: (128, 1), 2: (128, 2), 3: (64, 2), 4: (128, 3), 5: (32, 4), 6: (128, 4), 7: (16, 8), 8: (128, 5),
         9: (8, 16), 10: (128, 6), 12: (128, 7), 14: (128, 8)}
NBLK = {64: 60, 256: 88}


def interleave(sym, I, J):
    """Faltungsinterleaver, Zweig i mit i·J Zellen (Start mit Nullen), Kommutator ab Zweig 0 bei Symbol 0."""
    sym = np.asarray(sym, np.uint8)
    n = len(sym) - len(sym) % I
    out = np.zeros(n, np.uint8)
    x = sym[:n].reshape(-1, I)
    o = out.reshape(-1, I)
    for i in range(I):
        d = i * J
        col = np.concatenate([np.zeros(d, np.uint8), x[:, i]])[:len(x)]
        o[:, i] = col
    return out


def deinterleave(sym, I, J):
    sym = np.asarray(sym, np.uint8)
    n = len(sym) - len(sym) % I
    x = sym[:n].reshape(-1, I)
    out = np.zeros_like(x)
    for i in range(I):
        d = (I - 1 - i) * J
        out[:, i] = np.concatenate([np.zeros(d, np.uint8), x[:, i]])[:len(x)]
    return out.reshape(-1), (I - 1) * J  # Gesamtverzögerung in Kommutatorzyklen


def rand_seq(n):
    """Randomizer-Folge (Figur B.11): Register r0 → (+) → r1 → r2, Ausgang r2, Rückführung α^3·r2 → r0, r2 → (+)."""
    r0 = r1 = r2 = 0x7F
    a3 = gpow(3)
    out = np.zeros(n, np.uint8)
    for k in range(n):
        out[k] = r2
        r0, r1, r2 = gmul(a3, r2), r0 ^ r2, r1
    return out


def trailer(qam, cw):
    if qam == 64:
        bits = "1110101" "0101100" "0001101" "1101100" + format(cw, "04b") + "0" * 10
    else:
        bits = format(0x71E84DD4, "032b") + format(cw, "04b") + "0000"
    return np.array([int(b) for b in bits], np.uint8)


# ------------------------------------------------------------------ Trellis (B.5.5)
def bcc_out(state, b):
    """state = letzte 4 Eingangsbits (Bit0 = jüngstes). G1 = 1+D²+D⁴, G2 = 1+D+D²+D³+D⁴."""
    d1, d2, d3, d4 = state & 1, (state >> 1) & 1, (state >> 2) & 1, (state >> 3) & 1
    g1 = b ^ d2 ^ d4
    g2 = b ^ d1 ^ d2 ^ d3 ^ d4
    return g1, g2, ((state << 1) | b) & 15


def bcc4(state, bits):
    """4 Eingangsbits → 5 Ausgangsbits (Punktierung P1 = 0001, P2 = 1111; je Takt G1 vor G2)."""
    out = []
    for k, b in enumerate(bits):
        g1, g2, state = bcc_out(state, b)
        if k == 3:
            out.append(g1)
        out.append(g2)
    return out, state


class Trellis:
    def __init__(self, qam):
        self.qam = qam
        self.px = self.py = 0
        self.su = self.sv = 0

    def precode(self, W, Z):
        X, Y = [], []
        for w, z in zip(W, Z):
            a = self.px ^ self.py
            x = w ^ self.px ^ (z & a)
            y = z ^ w ^ self.py ^ (z & a)
            X.append(x); Y.append(y)
            self.px, self.py = x, y
        return X, Y

    def group(self, A, B):
        """A, B: Listen der Bits A0.., B0.. (14 bzw. 19 Stück); liefert 5 Labels."""
        if self.qam == 64:
            W = [A[0], A[3], A[6], A[9]]; Z = [B[0], B[3], B[6], B[9]]
        else:
            W = [A[0], A[4], A[8], A[12]]; Z = [B[0], B[4], B[8], B[12]]
        X, Y = self.precode(W, Z)
        U, self.su = bcc4(self.su, X)
        V, self.sv = bcc4(self.sv, Y)
        labs = []
        for k in range(5):
            if self.qam == 64:
                c5 = A[3 * k + 2] if k < 4 else A[13]; c4 = A[3 * k + 1] if k < 4 else A[12]
                c2 = B[3 * k + 2] if k < 4 else B[13]; c1 = B[3 * k + 1] if k < 4 else B[12]
                labs.append(c5 << 5 | c4 << 4 | U[k] << 3 | c2 << 2 | c1 << 1 | V[k])
            else:
                if k < 4:
                    a = [A[4 * k + 1], A[4 * k + 2], A[4 * k + 3]]; b = [B[4 * k + 1], B[4 * k + 2], B[4 * k + 3]]
                else:
                    a = [A[16], A[17], A[18]]; b = [B[16], B[17], B[18]]
                labs.append(a[2] << 7 | a[1] << 6 | a[0] << 5 | U[k] << 4 | b[2] << 3 | b[1] << 2 | b[0] << 1 | V[k])
        return labs


# Bitreihenfolgen der Trellis-Gruppen
ORDER64 = ["A10", "A8", "A7", "A5", "A4", "A2", "A1", "A9", "A6", "A3", "A0", "A13", "A12", "A11",
           "B10", "B8", "B7", "B5", "B4", "B2", "B1", "B9", "B6", "B3", "B0", "B13", "B12", "B11"]
ORDER256 = ("A0 B0 A1 A2 A3 B1 B2 B3 A4 B4 A5 A6 A7 B5 B6 B7 A8 B8 A9 A10 A11 B9 B10 B11 A12 B12 A13 A14 A15 B13 B14 B15 "
            "A16 A17 A18 B16 B17 B18").split()
SYNC256 = ("S0 S1 A1 A2 A3 B1 B2 B3 S2 S3 A5 A6 A7 B5 B6 B7 S4 S5 A9 A10 A11 B9 B10 B11 S6 S7 A13 A14 A15 B13 B14 B15 "
           "A16 A17 A18 B16 B17 B18").split()


def fill(order, bits, sbits=None):
    A = [0] * 19; B = [0] * 19; it = iter(bits)
    for name in order:
        if name[0] == "S":
            v = sbits[int(name[1:])]
            (A if int(name[1:]) % 2 == 0 else B)[(int(name[1:]) // 2) * 4] = v
        else:
            (A if name[0] == "A" else B)[int(name[1:])] = next(it)
    return A, B


def trellis_encode(qam, frames_bits):
    """frames_bits: Liste je Rahmen (Datenbits nach Randomizer, Trailer-Bits). → Labels"""
    T = Trellis(qam); labs = []
    if qam == 64:
        stream = np.concatenate([np.concatenate([d, t]) for d, t in frames_bits])
        for g in range(len(stream) // 28):
            A, B = fill(ORDER64, stream[g * 28:(g + 1) * 28])
            labs += T.group(A, B)
    else:
        for d, t in frames_bits:
            pos = 0
            for g in range(2076):
                if g < 2071:
                    A, B = fill(ORDER256, d[pos:pos + 38]); pos += 38
                else:
                    s = t[(g - 2071) * 8:(g - 2070) * 8]
                    A, B = fill(SYNC256, d[pos:pos + 30], s); pos += 30
                labs += T.group(A, B)
            assert pos == len(d)
    return np.array(labs, np.uint8)


def stages(ts_file, qam, cw, outdir):
    import os
    os.makedirs(outdir, exist_ok=True)
    ts = open(ts_file, "rb").read()
    I, J = CW_IJ[cw]
    fr = framing(ts); open(os.path.join(outdir, "1_framing.u8"), "wb").write(fr)
    bits = np.unpackbits(np.frombuffer(fr, np.uint8))
    sy = to7(bits)
    nb = len(sy) // 122
    rs = np.array([s for b in range(nb) for s in rs_encode(list(sy[b * 122:(b + 1) * 122]))], np.uint8)
    rs.tofile(os.path.join(outdir, "2_rs.u8"))
    il = interleave(rs, I, J); il.tofile(os.path.join(outdir, "3_il.u8"))
    N = NBLK[qam] * 128
    nf = len(il) // N
    rnd = il[:nf * N].copy()
    rs_ = rand_seq(N)
    for f in range(nf):
        rnd[f * N:(f + 1) * N] ^= rs_
    rnd.tofile(os.path.join(outdir, "4_rand.u8"))
    tr = trailer(qam, cw)
    frames = []
    fsb = []
    for f in range(nf):
        d = np.unpackbits(rnd[f * N:(f + 1) * N, None], axis=1)[:, 1:].reshape(-1)
        frames.append((d, tr)); fsb += [d, tr]
    np.concatenate(fsb).astype(np.uint8).tofile(os.path.join(outdir, "5_fsync.u8"))
    trellis_encode(qam, frames).tofile(os.path.join(outdir, "6_trellis.u8"))




# ------------------------------------------------------------------ Konstellation (Figur B.18/B.19, aus der Norm-PDF)
CONST = {
    64: np.array([1,1,1,-3,-3,1,-3,-3,-1,-1,3,-1,-1,3,3,3,5,1,1,-7,-3,5,-7,-3,-1,-5,7,-1,-5,3,3,7,1,5,5,-3,-7,1,-3,-7,-5,-1,3,-5,-1,7,7,3,5,5,5,-7,-7,5,-7,-7,-5,-5,7,-5,-5,7,7,7]) + 1j * np.array([1,-1,-3,-1,1,3,-3,3,1,-1,1,3,-3,-1,-3,3,1,-5,-7,-1,5,3,-3,7,5,-1,1,7,-3,-5,-7,3,5,-1,-3,-5,1,7,-7,3,1,-5,5,3,-7,-1,-3,7,5,-5,-7,-5,5,7,-7,7,5,-5,5,7,-7,-5,-7,7]),
    256: np.array([1,1,-3,1,1,5,-7,1,1,9,-11,1,1,13,-15,1,-1,-1,-1,3,-5,-1,-1,7,-9,-1,-1,11,-13,-1,-1,15,1,-3,-3,-3,5,-3,-3,-7,9,-3,-3,-11,13,-3,-3,-15,3,-1,3,3,3,-5,7,3,3,-9,11,3,3,-13,15,3,5,1,-3,5,5,5,-7,5,5,9,-11,5,5,13,-15,5,-1,-5,-5,3,-5,-5,-5,7,-9,-5,-5,11,-13,-5,-5,15,1,-7,-7,-3,5,-7,-7,-7,9,-7,-7,-11,13,-7,-7,-15,7,-1,3,7,7,-5,7,7,7,-9,11,7,7,-13,15,7,9,1,-3,9,9,5,-7,9,9,9,-11,9,9,13,-15,9,-1,-9,-9,3,-5,-9,-9,7,-9,-9,-9,11,-13,-9,-9,15,1,-11,-11,-3,5,-11,-11,-7,9,-11,-11,-11,13,-11,-11,-15,11,-1,3,11,11,-5,7,11,11,-9,11,11,11,-13,15,11,13,1,-3,13,13,5,-7,13,13,9,-11,13,13,13,-15,13,-1,-13,-13,3,-5,-13,-13,7,-9,-13,-13,11,-13,-13,-13,15,1,-15,-15,-3,5,-15,-15,-7,9,-15,-15,-11,13,-15,-15,-15,15,-1,3,15,15,-5,7,15,15,-9,11,15,15,-13,15,15]) + 1j * np.array([1,-1,1,3,5,-1,1,7,9,-1,1,11,13,-1,1,15,1,-1,-3,-1,1,-5,-7,-1,1,-9,-11,-1,1,-13,-15,-1,-3,-1,-3,3,-3,-5,-7,3,-3,-9,-11,3,-3,-13,-15,3,1,3,-3,3,5,3,-3,7,9,3,-3,11,13,3,-3,15,1,-5,5,3,5,-5,5,7,9,-5,5,11,13,-5,5,15,5,-1,-3,-5,5,-5,-7,-5,5,-9,-11,-5,5,-13,-15,-5,-7,-1,-3,7,-7,-5,-7,7,-7,-9,-11,7,-7,-13,-15,7,1,7,-7,3,5,7,-7,7,9,7,-7,11,13,7,-7,15,1,-9,9,3,5,-9,9,7,9,-9,9,11,13,-9,9,15,9,-1,-3,-9,9,-5,-7,-9,9,-9,-11,-9,9,-13,-15,-9,-11,-1,-3,11,-11,-5,-7,11,-11,-9,-11,11,-11,-13,-15,11,1,11,-11,3,5,11,-11,7,9,11,-11,11,13,11,-11,15,1,-13,13,3,5,-13,13,7,9,-13,13,11,13,-13,13,15,13,-1,-3,-13,13,-5,-7,-13,13,-9,-11,-13,13,-13,-15,-13,-15,-1,-3,15,-15,-5,-7,15,-15,-9,-11,15,-15,-13,-15,15,1,15,-15,3,5,15,-15,7,9,15,-15,11,13,15,-15,15]),
}


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "stages":
        stages(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    else:
        print(__doc__); sys.exit(2)
