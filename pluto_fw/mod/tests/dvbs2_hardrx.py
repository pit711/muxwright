#!/usr/bin/env python3
"""DVB-S2-Kontrollempfänger mit harter Entscheidung (ohne LDPC-Decoder) für Mitschnitte mit hohem SNR (Loopback, Datei).
BCH und LDPC sind systematisch: Die ersten Kbch Bits eines FECFRAME sind das BBFRAME. Bei fehlerfreien harten Entscheidungen
ergibt sich daraus der TS; BBHEADER-CRC-8 und die CRC-8 jedes User-Pakets (ersetzt das Sync-Byte) zeigen Bitfehler an.
Kette: Abtastphase → PLHEADER-Suche (differentiell, frequenzrobust) → Frequenz/Phase aus den Headern → PL-Descrambling
→ Pilots entfernen → Phasennachführung (M-te Potenz je Slot) → Demapping → Bit-Deinterleaver → BB-Descrambling → TS.
Nur Normal-FECFRAME, QPSK/8PSK, CCM/SIS-TS (wie dvbs2_mod). Port der Tabellen aus lib/dvbs2.c.
  python3 dvbs2_hardrx.py --iq x.iq --sps 2 --mod qpsk --rate 3/4 --pilots 1 [--rolloff 0.35] -o out.ts
  python3 dvbs2_hardrx.py --iq rx.iq --sps 1 --rx [--up 8] ...      (Funk-Mitschnitt, Matched Filter im AD9361-RX-FIR)"""
import argparse, json, sys
import numpy as np

RATES = ["1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10"]
KBCH = [16008, 21408, 25728, 32208, 38688, 43040, 48408, 51648, 53840, 57472, 58192]
MODS = {"qpsk": 2, "8psk": 3}
SOF = 0x18D2E82
PLSCR = 0x719D83C953422DFA
G = [0x55555555, 0x33333333, 0x0F0F0F0F, 0x00FF00FF, 0x0000FFFF, 0xFFFFFFFF]


def modcod(mod, ridx):
    if mod == "qpsk":
        return 1 + ridx
    return 12 + ridx - 4 - (1 if ridx > 7 else 0)      # 8PSK 3/5 … 9/10 = 12 … 17 (kein 4/5)


def plheader(code7):
    """90 π/2-BPSK-Symbole (Betrag 1) wie build_pl()."""
    in6, b7 = code7 >> 1, code7 & 1
    y = 0
    for r in range(6):
        if (in6 >> (5 - r)) & 1:
            y ^= G[r]
    bits = [(SOF >> (25 - i)) & 1 for i in range(26)]
    for i in range(32):
        yi = (y >> (31 - i)) & 1
        bits += [yi, yi ^ b7]
    for i in range(64):
        bits[26 + i] ^= (PLSCR >> (63 - i)) & 1
    h = np.empty(90, complex)
    for i in range(0, 90, 2):
        u, v = 1 - 2 * bits[i], 1 - 2 * bits[i + 1]
        h[i] = u * (1 + 1j) / np.sqrt(2)
        h[i + 1] = v * (-1 + 1j) / np.sqrt(2)
    return h


def gold_rn(L, gold):
    P = (1 << 18) - 1
    x = np.empty(P, np.uint8); yv = np.empty(P, np.uint8)
    xs, ys = 1, 0x3FFFF
    for i in range(P):
        x[i] = xs & 1; yv[i] = ys & 1
        xb = (xs ^ (xs >> 7)) & 1; yb = (ys ^ (ys >> 5) ^ (ys >> 7) ^ (ys >> 10)) & 1
        xs = (xs >> 1) | (xb << 17); ys = (ys >> 1) | (yb << 17)
    i = np.arange(L); j = (i + 131072) % P
    z0 = x[(i + gold) % P] ^ yv[i]; z1 = x[(j + gold) % P] ^ yv[j]
    return (2 * z1 + z0).astype(np.int64)


def constellation(mod):
    if mod == "qpsk":
        return np.array([((1 - 2 * ((w >> 1) & 1)) + 1j * (1 - 2 * (w & 1))) / np.sqrt(2) for w in range(4)])
    deg = [45, 0, 180, 225, 90, 315, 135, 270]
    return np.exp(1j * np.radians(deg))


def rrc(alpha, sps, n):
    t = (np.arange(n) - (n - 1) / 2) / sps; h = np.zeros(n)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-9: h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * ti) - 1) < 1e-9: h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha)) + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else: h[i] = (np.sin(np.pi * ti * (1 - alpha)) + 4 * alpha * ti * np.cos(np.pi * ti * (1 + alpha))) / (np.pi * ti * (1 - (4 * alpha * ti) ** 2))
    return h / h.sum()


def xcorr(a, b):
    """|Σ a[k+n]·conj(b[n])| für alle k (FFT)."""
    n = len(a) + len(b); nf = 1 << int(np.ceil(np.log2(n)))
    c = np.fft.ifft(np.fft.fft(a, nf) * np.conj(np.fft.fft(b, nf)))
    return c[: len(a) - len(b) + 1]


def crc8_table():
    t = []
    for v in range(256):
        r = v
        for _ in range(8):
            r = ((r << 1) ^ 0xD5) & 0xFF if r & 0x80 else (r << 1) & 0xFF
        t.append(r)
    return t


def bb_scrambler(kbch):
    out = np.zeros(kbch, np.uint8); reg = 0x4A80
    for i in range(kbch):
        o = ((reg >> 1) ^ reg) & 1; out[i] = o; reg = (reg >> 1) | (o << 14)
    return np.packbits(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iq", required=True); ap.add_argument("-o", required=True)
    ap.add_argument("--sps", type=int, default=2); ap.add_argument("--rx", action="store_true"); ap.add_argument("--up", type=int, default=8)
    ap.add_argument("--mod", default="qpsk"); ap.add_argument("--rate", default="3/4"); ap.add_argument("--pilots", type=int, default=1)
    ap.add_argument("--rolloff", type=float, default=0.35); ap.add_argument("--gold", type=int, default=0); ap.add_argument("--taps", type=int, default=64)
    ap.add_argument("--max", type=int, default=3000000, help="max. Symbole"); ap.add_argument("--skip", type=int, default=0, help="Samples überspringen")
    a = ap.parse_args()
    mod = a.mod.lower(); ridx = RATES.index(a.rate); bits = MODS[mod]
    if mod == "8psk" and (ridx < 4 or ridx == 7):
        sys.exit("8PSK: 3/5, 2/3, 3/4, 5/6, 8/9, 9/10")
    kbch = KBCH[ridx]; ns = 64800 // bits; nslots = ns // 90
    npil = (nslots - 1) // 16 if a.pilots else 0
    plsym = 90 + ns + 36 * npil
    rep = {"modcod": f"{mod.upper()} {a.rate}{' pilots' if a.pilots else ''}", "plframe_symbols": plsym}

    raw = np.fromfile(a.iq, np.int16, count=2 * (a.skip + (a.max + 2 * plsym) * a.sps)).astype(float)
    x = (raw[0::2] + 1j * raw[1::2])[a.skip:]
    # -- Symbolfolge
    if not a.rx:
        y = np.convolve(x, rrc(a.rolloff, a.sps, a.taps), mode="full")[a.taps // 2:]
        s = max((y[p::a.sps] for p in range(a.sps)), key=lambda z: np.mean(np.abs(z[:40000])))
    else:
        x = x - x.mean(); n = len(x) - len(x) % 2; X = np.fft.fft(x[:n]); nu = n * a.up // a.sps
        U = np.zeros(nu, complex); h = n // 2; U[:h] = X[:h]; U[-h:] = X[-h:]; y = np.fft.ifft(U)
        def spread(p):      # PSK: konstanter Betrag im Abtastzeitpunkt
            z = np.abs(y[p::a.up][:60000]); return np.var(z) / np.mean(z) ** 2
        best = min(range(a.up), key=spread); s = y[best::a.up]; rep["phase"] = int(best)
    s = s[: a.max + 2 * plsym]
    s = s / np.sqrt(np.mean(np.abs(s) ** 2))

    # -- Rahmensuche: differentielle Korrelation mit dem PLHEADER (unempfindlich gegen Frequenzablage)
    code7 = (modcod(mod, ridx) << 2) | (1 if a.pilots else 0)
    hdr = plheader(code7)
    d = s[1:] * np.conj(s[:-1]); dh = hdr[1:] * np.conj(hdr[:-1])
    c = np.abs(xcorr(d, dh)) / 89
    if len(c) < plsym:
        sys.exit("Mitschnitt kürzer als ein PLFRAME")
    k0 = int(np.argmax(c[:plsym])); starts = []
    k = k0
    tail = 24          # Rahmen am Mitschnittende brauchen noch das Ausschwingen des RRC-Filters
    while k + plsym + tail <= len(s):
        lo, hi = max(0, k - 4), min(len(c), k + 5)
        k = lo + int(np.argmax(c[lo:hi])) if hi > lo else k
        if c[k] > 0.5 and k + plsym + tail <= len(s):
            starts.append(k)
        k += plsym
    rep["sync_metric"] = round(float(c[k0]), 3)
    if not starts:
        rep.update(ok=False, error="kein PLHEADER gefunden"); print(json.dumps(rep)); return 1
    # -- Frequenzablage: Phasendrehung zwischen aufeinanderfolgenden Headern (grob aus dem Header selbst)
    ph = [np.angle(np.sum(s[k:k + 90] * np.conj(hdr))) for k in starts]
    f_in = np.angle(np.sum(d[starts[0]:starts[0] + 89] * np.conj(dh))) / (2 * np.pi)
    if len(starts) > 1:
        steps = np.diff(starts); dph = np.diff(ph)
        pred = 2 * np.pi * f_in * steps
        dph = dph - 2 * np.pi * np.round((dph - pred) / (2 * np.pi))
        f = float(np.sum(dph) / np.sum(steps) / (2 * np.pi))
    else:
        f = float(f_in)
    s = s * np.exp(-2j * np.pi * f * np.arange(len(s)))
    rep["cfo_cycles_per_symbol"] = f
    # MODCOD aus dem ersten Header bestimmen (alle 128 PLS-Codes)
    k = starts[0]; p0 = np.exp(-1j * np.angle(np.sum(s[k:k + 90] * np.conj(hdr))))
    best = max(range(128), key=lambda cd: np.real(np.sum(s[k + 26:k + 90] * p0 * np.conj(plheader(cd)[26:]))))
    rep["pls_detected"] = {"modcod": best >> 2, "short": bool(best & 2), "pilots": bool(best & 1)}
    rep["pls_ok"] = best == code7

    # -- Rahmen dekodieren
    L = plsym - 90
    rn = gold_rn(L, a.gold)
    descr = (-1j) ** rn                      # Scrambling dreht um j^Rn
    data_idx, pil_idx, p = [], [], 0
    for slot in range(nslots):
        data_idx.extend(range(p, p + 90)); p += 90
        if a.pilots and (slot + 1) % 16 == 0 and slot + 1 < nslots:
            pil_idx.extend(range(p, p + 36)); p += 36
    data_idx = np.array(data_idx); pil_idx = np.array(pil_idx, int)
    const = constellation(mod); M = 4 if mod == "qpsk" else 8
    ref = 0 if mod == "8psk" else np.pi / 4          # QPSK-Punkte bei ±45°, 8PSK-Raster enthält 0°

    def slot_phases(k):
        hp = np.exp(-1j * np.angle(np.sum(s[k:k + 90] * np.conj(hdr))))
        zd = (s[k + 90:k + plsym] * hp * descr)[data_idx]
        out, prev = [], 0.0
        for b0 in range(0, ns, 90):
            est = np.angle(np.sum(zd[b0:b0 + 90] ** M * np.exp(-1j * M * ref))) / M
            prev = prev + ((est - prev + np.pi / M) % (2 * np.pi / M) - np.pi / M); out.append(prev)
        return np.array(out)
    # Feinschätzung der Frequenz: Phasensteigung über die Slots (Abstand 90 bzw. 126 Symbole, keine Mehrdeutigkeit)
    pos = data_idx[::90] + 45.0
    for _ in range(2):
        sl = np.mean([np.polyfit(pos, slot_phases(k), 1)[0] for k in starts[:8]])
        s = s * np.exp(-1j * sl * np.arange(len(s))); f += float(sl / (2 * np.pi))
    rep["cfo_cycles_per_symbol"] = f
    rows = 64800 // bits; order = [2, 1, 0] if (mod == "8psk" and ridx == 4) else list(range(bits))
    scr = bb_scrambler(kbch); T = crc8_table()
    def crc8(b):
        r = 0
        for v in b: r = T[r ^ v]
        return r
    ts = bytearray(); up_stream = bytearray(); aligned = False
    nfr = hdr_ok = 0; evm_num = evm_den = 0.0; up_crc_err = up_n = 0; prev_up = None
    for k in starts:
        hp = np.exp(-1j * np.angle(np.sum(s[k:k + 90] * np.conj(hdr))))
        z = s[k + 90:k + plsym] * hp * descr
        if len(pil_idx):
            evm_num += np.sum(np.abs(z[pil_idx] - (1 + 1j) / np.sqrt(2)) ** 2); evm_den += len(pil_idx)
        zd = z[data_idx]
        prev = 0.0
        for b0 in range(0, ns, 90):        # Phasennachführung je Slot (M-te Potenz, an den Header-Bezug gebunden)
            blk = zd[b0:b0 + 90]
            est = (np.angle(np.sum(blk ** M * np.exp(-1j * M * ref)))) / M
            est = prev + ((est - prev + np.pi / M) % (2 * np.pi / M) - np.pi / M)
            prev = est; zd[b0:b0 + 90] = blk * np.exp(-1j * est)
        dist = np.abs(zd[:, None] - const[None, :]); lab = np.argmin(dist, axis=1)
        evm_num += np.sum(np.abs(zd - const[lab]) ** 2); evm_den += ns
        cw = np.zeros(64800, np.uint8)
        if mod == "qpsk":                  # QPSK ohne Bit-Interleaver
            cw[0::2] = lab >> 1; cw[1::2] = lab & 1
        else:
            for ci in range(bits):
                cw[order[ci] * rows:(order[ci] + 1) * rows] = (lab >> (bits - 1 - ci)) & 1
        bb = np.packbits(cw[:kbch]) ^ scr
        nfr += 1
        if crc8(bb[:9]) != bb[9] or bb[6] != 0x47:
            aligned = False; up_stream = bytearray(); continue
        hdr_ok += 1
        dfl = (int(bb[4]) << 8) | int(bb[5]); syncd = (int(bb[7]) << 8) | int(bb[8])
        df = bytes(bb[10:10 + dfl // 8])
        if not aligned:
            if syncd == 0xFFFF or syncd % 8:
                continue
            up_stream = bytearray(df[syncd // 8:]); aligned = True; prev_up = None
        else:
            up_stream += df
        while len(up_stream) >= 188:
            up = bytes(up_stream[:188]); del up_stream[:188]
            if prev_up is not None:
                up_n += 1
                if crc8(prev_up[1:]) != up[0]:
                    up_crc_err += 1
            prev_up = up
            ts += b"\x47" + up[1:]
    open(a.o, "wb").write(ts)
    mer = 10 * np.log10(evm_den / evm_num) if evm_num > 0 else 99.0
    rep.update(frames=nfr, bbheader_ok=hdr_ok, ts_packets=len(ts) // 188, up_crc_checked=up_n, up_crc_errors=up_crc_err,
               mer_db=round(float(mer), 1), symbols=int(len(s)))
    rep["ok"] = bool(nfr and hdr_ok == nfr and up_n and up_crc_err == 0 and rep["pls_ok"])
    print(json.dumps(rep))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
