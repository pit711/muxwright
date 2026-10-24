#!/usr/bin/env python3
"""j83b_rx.py – Loopback-Referenzempfänger für ITU-T J.83 Annex B (dvbc_mod --annex b), numpy, ohne gr-dtv.

IQ (int16 interleaved) → [sps 2: RRC-Matched-Filter, Abtastphase] → Symbole → optional Rauschen (Es/N0) und Drehung um
k·90° → Soft-Metrik je codiertem Bit (Abstand zum nächsten Punkt mit Bit 0/1) → zwei Viterbi-Decoder (16 Zustände,
G1 = 25, G2 = 37 oktal, Punktierung 4/5) → codierte Bits neu codieren, uncodierte Bits aus dem nächsten Punkt der Teilmenge
→ Präcoder rückrechnen → Trellisgruppen → Rahmensync (Unique Word) → Steuerwort → Entwürfelung → Deinterleaver →
RS(128,122)-Decoder → Bitstrom → MPEG-Paketsync über die Prüfsumme (Syndrom 47h) → TS; Vergleich mit dem Original-TS.

  j83b_rx.py IQ --qam 64|256 [--sps 1|2] [--sr SR] [--snr DB] [--rot K] [--ts-ref TS] [--ts-out TS] [--json F]
             [--max-frames N] [--rolloff A]
"""
import sys, json, argparse
import numpy as np
import j83b_ref as R


def rrc(alpha, sps, ntaps):
    h = np.zeros(ntaps)
    for i in range(ntaps):
        t = (i - (ntaps - 1) / 2) / sps
        if abs(t) < 1e-9:
            h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * t) - 1) < 1e-9:
            h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha)) + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else:
            h[i] = (np.sin(np.pi * t * (1 - alpha)) + 4 * alpha * t * np.cos(np.pi * t * (1 + alpha))) / (np.pi * t * (1 - (4 * alpha * t) ** 2))
    return h / np.sqrt(np.sum(h * h))


ORD64A = (10, 8, 7, 5, 4, 2, 1)      # Figur B.13: erstes A-Symbol MSB → LSB
ORD64B = (9, 6, 3, 0, 13, 12, 11)    # zweites A-Symbol


# ------------------------------------------------------------------ Viterbi (zwei Ströme parallel)
NS = np.arange(16)
PRED = np.stack([(NS >> 1) | (x << 3) for x in (0, 1)], 1)           # (16, 2)
BIN = NS & 1


def _outs(s, b):
    d1, d2, d3, d4 = s & 1, (s >> 1) & 1, (s >> 2) & 1, (s >> 3) & 1
    return b ^ d2 ^ d4, b ^ d1 ^ d2 ^ d3 ^ d4


G1 = np.zeros((16, 2), int); G2 = np.zeros((16, 2), int)
for _ns in range(16):
    for _x in range(2):
        G1[_ns, _x], G2[_ns, _x] = _outs(PRED[_ns, _x], _ns & 1)


def viterbi(m):
    """m: (ngroups, 5, 2 Ströme, 2 Bitwerte) Metriken je Symbol → Eingangsbits (ngroups*4, 2)"""
    ng = len(m)
    pm = np.zeros((2, 16))                    # Startzustand unbekannt (Mitschnitt beginnt mitten im Strom)
    dec = np.zeros((ng * 4, 2, 16), np.uint8)
    for g in range(ng):
        for t in range(4):
            c = pm[:, PRED]                                         # (2,16,2)
            if t < 3:
                c = c + m[g, t][:, G2]
            else:
                c = c + m[g, 3][:, G1] + m[g, 4][:, G2]
            k = np.argmin(c, 2)
            dec[g * 4 + t] = k
            pm = np.where(k == 0, c[:, :, 0], c[:, :, 1])
            pm -= pm.min(1, keepdims=True)
    out = np.zeros((ng * 4, 2), np.uint8)
    s = np.argmin(pm, 1)
    for n in range(ng * 4 - 1, -1, -1):
        out[n] = s & 1
        s = PRED[s, dec[n, [0, 1], s]]
    return out


def bcc_reencode(bits):
    """Eingangsbits (n*4) → 5 Ausgangsbits je Gruppe wie der Sender"""
    st = 0; out = []
    for g in range(len(bits) // 4):
        o, st = R.bcc4(st, [int(b) for b in bits[g * 4:g * 4 + 4]])
        out += o
    return np.array(out, np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iq"); ap.add_argument("--qam", type=int, required=True)
    ap.add_argument("--sps", type=int, default=2); ap.add_argument("--sr", type=float, default=0)
    ap.add_argument("--rolloff", type=float, default=0); ap.add_argument("--taps", type=int, default=128)
    ap.add_argument("--snr", type=float, default=0, help="Es/N0 in dB (Rauschen auf den Symbolen nach dem Matched Filter)")
    ap.add_argument("--rot", type=int, default=0, help="Drehung um k·90° (Phasenmehrdeutigkeit des Trägers)")
    ap.add_argument("--ts-ref"); ap.add_argument("--ts-out"); ap.add_argument("--json")
    ap.add_argument("--max-frames", type=int, default=0); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--dump-frames", help="Rahmendaten (7-Bit-Symbole, noch verwürfelt) schreiben (Test)")
    a = ap.parse_args()
    qam = a.qam; P = R.CONST[qam]; nb = 6 if qam == 64 else 8
    cu = 3 if qam == 64 else 4                      # Bitposition U (C3 bzw. C4), V = C0
    sr = a.sr or (5056941.0 if qam == 64 else 5360537.0)
    alpha = a.rolloff or (0.18 if qam == 64 else 0.12)
    rep = {"qam": qam, "sps": a.sps, "symbol_rate": sr, "snr_db": a.snr or None, "rot90": a.rot}

    x = np.fromfile(a.iq, np.int16).astype(np.float64)
    x = x[0::2] + 1j * x[1::2]
    if a.sps == 2:
        h = rrc(alpha, 2, a.taps)
        y = np.convolve(x, h)
        best = None
        for ph in range(2):
            s = y[ph::2][200:20200]
            s = s / np.sqrt(np.mean(np.abs(s) ** 2)) * np.sqrt(np.mean(np.abs(P) ** 2))
            d = np.min(np.abs(s[:, None] - P[None, :]), 1)
            evm = np.sqrt(np.mean(d ** 2) / np.mean(np.abs(P) ** 2))
            if best is None or evm < best[0]:
                best = (evm, ph)
        sym = y[best[1]::2]
    else:
        sym = x
    # Skalierung auf die Konstellation (eingeschwungener Teil)
    sym = sym / np.sqrt(np.mean(np.abs(sym[200:-200]) ** 2)) * np.sqrt(np.mean(np.abs(P) ** 2))
    d = np.min(np.abs(sym[200:20200, None] - P[None, :]), 1)
    rep["mer_db_rx"] = float(10 * np.log10(np.mean(np.abs(P) ** 2) / np.mean(d ** 2)))
    if a.snr:
        rng = np.random.default_rng(a.seed)
        es = np.mean(np.abs(P) ** 2); n0 = es / 10 ** (a.snr / 10)
        sym = sym + np.sqrt(n0 / 2) * (rng.standard_normal(len(sym)) + 1j * rng.standard_normal(len(sym)))
    if a.rot:
        sym = sym * (1j ** a.rot)
    if a.max_frames:
        per = 9607.5 if qam == 64 else 10380
        sym = sym[:int(per * (a.max_frames + 2))]

    # harte Labelentscheidung (nur für die Statistik)
    def nearest(s, mask=None):
        out = np.zeros(len(s), np.int64)
        for i in range(0, len(s), 20000):
            dd = np.abs(s[i:i + 20000, None] - P[None, :]) ** 2
            if mask is not None:
                dd = dd + mask[i:i + 20000]
            out[i:i + 20000] = np.argmin(dd, 1)
        return out

    lab_hard = nearest(sym)
    rep["mer_db_dec"] = float(10 * np.log10(np.mean(np.abs(P) ** 2) / np.mean(np.abs(sym - P[lab_hard]) ** 2)))

    # Metriken je Symbol: min. Abstand für U = 0/1 und V = 0/1
    labs = np.arange(qam)
    ub = (labs >> cu) & 1; vb = labs & 1
    met = np.zeros((len(sym), 2, 2))
    for i in range(0, len(sym), 20000):
        dd = np.abs(sym[i:i + 20000, None] - P[None, :]) ** 2
        for b in (0, 1):
            met[i:i + 20000, 0, b] = dd[:, ub == b].min(1)
            met[i:i + 20000, 1, b] = dd[:, vb == b].min(1)

    def decode_phase(ph, nmax):
        n = (len(sym) - ph) // 5
        if nmax: n = min(n, nmax)
        m = met[ph:ph + 5 * n].reshape(n, 5, 2, 2)
        XY = viterbi(m)
        U = bcc_reencode(XY[:, 0]); V = bcc_reencode(XY[:, 1])
        ss = sym[ph:ph + 5 * n]
        # Teilmengen-Entscheidung: nur Punkte mit passendem U/V
        mask = np.where((ub[None, :] == U[:, None]) & (vb[None, :] == V[:, None]), 0.0, 1e9)
        lab = nearest(ss, mask)
        # Präcoder rückrechnen: Z = X ^ Y ^ a, W = X ^ X' ^ Z·a  (a = X' ^ Y')
        X = XY[:, 0].astype(int); Y = XY[:, 1].astype(int)
        Xp = np.concatenate([[0], X[:-1]]); Yp = np.concatenate([[0], Y[:-1]])
        aa = Xp ^ Yp
        Z = X ^ Y ^ aa
        W = X ^ Xp ^ (Z & aa)
        return lab.reshape(n, 5), W.reshape(n, 4), Z.reshape(n, 4), int((lab != lab_hard[ph:ph + 5 * n]).sum())

    def groups_to_symbols64(lab, W, Z):
        """Labels + W/Z → 7-Bit-Symbole (4 je Gruppe) nach Figur B.13"""
        n = len(lab)
        A = np.zeros((n, 14), np.uint8); B = np.zeros((n, 14), np.uint8)
        for k in range(5):
            c5 = (lab[:, k] >> 5) & 1; c4 = (lab[:, k] >> 4) & 1; c2 = (lab[:, k] >> 2) & 1; c1 = (lab[:, k] >> 1) & 1
            if k < 4:
                A[:, 3 * k + 2] = c5; A[:, 3 * k + 1] = c4; B[:, 3 * k + 2] = c2; B[:, 3 * k + 1] = c1
            else:
                A[:, 13] = c5; A[:, 12] = c4; B[:, 13] = c2; B[:, 12] = c1
        for t, idx in enumerate((0, 3, 6, 9)):
            A[:, idx] = W[:, t]; B[:, idx] = Z[:, t]
        out = np.zeros((n, 4), np.uint8)
        for j, (src, order) in enumerate(((A, ORD64A), (A, ORD64B), (B, ORD64A), (B, ORD64B))):
            v = np.zeros(n, np.int64)
            for b in order:
                v = (v << 1) | src[:, b]
            out[:, j] = v
        return out.reshape(-1)

    def groups_AB256(lab, W, Z):
        n = len(lab)
        A = np.zeros((n, 19), np.uint8); B = np.zeros((n, 19), np.uint8)
        for k in range(5):
            l = lab[:, k]
            base = 4 * k + 1 if k < 4 else 16
            for j in range(3):
                A[:, base + j] = (l >> (5 + j)) & 1
                B[:, base + j] = (l >> (1 + j)) & 1
        for t in range(4):
            A[:, 4 * t] = W[:, t]; B[:, 4 * t] = Z[:, t]
        return A, B

    # Gruppenphase suchen (5 Möglichkeiten): Phase, in der der Rahmensync auftaucht
    probe = 3 * (2076 if qam == 256 else 1922) + 10
    found = None
    for ph in range(5):
        lab, W, Z, _ = decode_phase(ph, probe)
        if qam == 64:
            s7 = groups_to_symbols64(lab, W, Z)
            hits = [i for i in range(len(s7) - 4) if s7[i] == 0x75 and s7[i + 1] == 0x2c and s7[i + 2] == 0x0d and s7[i + 3] == 0x6c]
        else:
            sb = np.zeros(len(W), np.int64)
            for t in range(4):
                sb = (sb << 2) | (W[:, t] << 1) | Z[:, t]
            hits = [i for i in range(len(sb) - 4) if list(sb[i:i + 4]) == [0x71, 0xe8, 0x4d, 0xd4]]
        if len(hits) >= 2:
            found = (ph, hits); break
    if not found:
        rep["error"] = "kein Rahmensync"
        print(json.dumps(rep)); sys.exit(1)
    ph, hits = found
    rep["group_phase"] = ph
    lab, W, Z, ndiff = decode_phase(ph, 0)
    rep["trellis_corrected_symbols"] = ndiff     # Symbole, deren Label der Viterbi gegenüber der harten Entscheidung änderte

    # Rahmen zerlegen
    N = R.NBLK[qam] * 128
    frames = []
    if qam == 64:
        s7 = groups_to_symbols64(lab, W, Z)
        pos = [i for i in range(len(s7) - 6) if s7[i] == 0x75 and s7[i + 1] == 0x2c and s7[i + 2] == 0x0d and s7[i + 3] == 0x6c]
        rep["frame_period_7bit_symbols"] = sorted(set(np.diff(pos).tolist()))
        rep["frame_period_qam_symbols"] = [d * 7 / 28 * 5 for d in rep["frame_period_7bit_symbols"]]
        cws = []
        for p in pos:
            if p + 6 + N <= len(s7):
                cws.append(int(s7[p + 4]) >> 3)
                frames.append(s7[p + 6:p + 6 + N])
        rep["trailer_reserved_zero"] = bool(all((int(s7[p + 4]) & 7) == 0 and s7[p + 5] == 0 for p in pos))
    else:
        A, B = groups_AB256(lab, W, Z)
        sb = np.zeros(len(W), np.int64)
        for t in range(4):
            sb = (sb << 2) | (W[:, t] << 1) | Z[:, t]
        pos = [i for i in range(len(sb) - 5) if list(sb[i:i + 4]) == [0x71, 0xe8, 0x4d, 0xd4]]
        rep["frame_period_groups"] = sorted(set(np.diff(pos).tolist()))
        rep["frame_period_qam_symbols"] = [d * 5 for d in rep["frame_period_groups"]]
        cws = [int(sb[p + 4]) >> 4 for p in pos]
        rep["trailer_reserved_zero"] = bool(all((int(sb[p + 4]) & 15) == 0 for p in pos))
        o_ns = [(c[0], int(c[1:])) for c in R.ORDER256]
        o_sy = [(c[0], int(c[1:])) for c in R.SYNC256 if c[0] != "S"]
        for p in pos:
            g0 = p + 5          # erste Gruppe des folgenden Rahmens
            if g0 + 2076 > len(lab):
                break
            bits = []
            for g in range(g0, g0 + 2076):
                for c, i in (o_ns if g < g0 + 2071 else o_sy):
                    bits.append(A[g, i] if c == "A" else B[g, i])
            frames.append(R.to7(np.array(bits, np.uint8)))
    rep["frames_found"] = len(pos)
    rep["control_words"] = sorted(set(cws))
    cw = max(set(cws), key=cws.count)
    I, J = R.CW_IJ[cw]
    rep["I"], rep["J"] = I, J
    rnd = R.rand_seq(N)
    if a.dump_frames:
        np.concatenate(frames).astype(np.uint8).tofile(a.dump_frames)
    data = np.concatenate([f ^ rnd for f in frames])
    de, dly = R.deinterleave(data, I, J)
    skip = dly * I                                  # Einschwingen des Deinterleavers (Vielfaches von 128)
    blocks = de[skip:]
    nblk = len(blocks) // 128
    corr = []; fail = 0; info = []
    for b in range(nblk):
        d, c = R.rs_decode([int(v) for v in blocks[b * 128:(b + 1) * 128]])
        if c < 0:
            fail += 1
        else:
            corr.append(c)
        info += d
    rep["rs_blocks"] = nblk; rep["rs_failed"] = fail; rep["rs_corrected_symbols"] = int(sum(corr))
    bits = np.unpackbits(np.array(info, np.uint8)[:, None], axis=1)[:, 1:].reshape(-1)
    # MPEG-Sync: Bitversatz o mit Syndrom 47h in zwei aufeinanderfolgenden Fenstern
    o0 = None
    for o in range(0, 1504 * 2):
        if o + 2 * 1504 > len(bits): break
        if R.syndrome(bits[o:o + 1504]) == 0x47 and R.syndrome(bits[o + 1504:o + 3008]) == 0x47:
            o0 = o; break
    if o0 is None:
        rep["error"] = "kein MPEG-Sync"
        print(json.dumps(rep)); sys.exit(1)
    npk = (len(bits) - o0) // 1504
    w = bits[o0:o0 + npk * 1504].reshape(npk, 1504)
    syn = np.array([R.syndrome(r) for r in w])
    pk = np.packbits(w, axis=1)                       # 188 Byte: 187 Nutzbytes + Prüfsumme
    ts = np.concatenate([np.full((npk, 1), 0x47, np.uint8), pk[:, :187]], 1)
    rep["ts_packets"] = int(npk); rep["checksum_errors"] = int((syn != 0x47).sum())
    if a.ts_out:
        ts.tofile(a.ts_out)
    if a.ts_ref:
        ref = np.fromfile(a.ts_ref, np.uint8)
        ref = ref[:len(ref) // 188 * 188].reshape(-1, 188)
        # Lage des ersten decodierten Pakets im Original
        key = ts[0].tobytes(); start = None
        for i in range(len(ref)):
            if ref[i].tobytes() == key:
                start = i; break
        if start is None:
            rep["ref_match"] = False
        else:
            n = min(npk, len(ref) - start)
            bad = np.any(ts[:n] != ref[start:start + n], 1)
            rep["ref_first_packet"] = start; rep["ref_compared"] = int(n); rep["ref_packet_errors"] = int(bad.sum())
            rep["ref_bit_errors"] = int(np.unpackbits(ts[:n] ^ ref[start:start + n]).sum())
    # Nutzrate aus der Rahmenstruktur
    per = rep["frame_period_qam_symbols"][0] if len(rep["frame_period_qam_symbols"]) == 1 else None
    if qam == 64 and len(rep["frame_period_qam_symbols"]) <= 2:
        per = float(np.mean(np.diff(pos))) * 7 / 28 * 5
    if per:
        rep["qam_symbols_per_frame"] = per
        rep["ts_bps_from_frame"] = sr * R.NBLK[qam] * 122 * 7 / per
    s = json.dumps(rep)
    print(s)
    if a.json:
        open(a.json, "w").write(s + "\n")


if __name__ == "__main__":
    main()
