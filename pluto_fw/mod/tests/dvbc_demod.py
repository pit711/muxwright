#!/usr/bin/env python3
"""DVB-C-Prüfdemodulator (EN 300 429 Annex A) in numpy – unabhängig vom C-Code geschrieben.
IQ (int16 interleaved, sps Samples/Symbol) → RRC-Matched-Filter → Abtastphase → Entscheidung →
Differenzdecodierung → m-Tupel→Bytes → Faltungs-Deinterleaver (Sync-Suche) → RS(204,188)-Decoder (reedsolo)
→ Energie-Entwürfelung → TS. Vergleich mit dem Eingangs-TS (Bitfehler) und JSON-Bericht.

  python3 dvbc_demod.py --iq out.iq --qam 256 --sps 2 [--ts in.ts] [--sr 6.9e6] [--json r.json]
"""
import argparse, json, sys
import numpy as np
from reedsolo import RSCodec

# ---------------- Konstellationen (Quadrant 1) aus EN 300 429 Figur 7/8 ----------------
def q1_points(qam):
    g2 = {0: 1, 1: 3, 3: 5, 2: 7}
    g3 = {0: 1, 1: 3, 3: 5, 2: 7, 6: 9, 7: 11, 5: 13, 4: 15}
    pts = {}
    if qam == 16:
        for q in range(4): pts[q] = (1 + 2 * (q & 1), 1 + 2 * ((q >> 1) & 1))
    elif qam == 32:
        tI = [1, 3, 3, 5, 1, 3, 1, 5]; tQ = [1, 1, 5, 1, 3, 3, 5, 3]
        for q in range(8): pts[q] = (tI[q], tQ[q])
    elif qam == 64:
        for q in range(16): pts[q] = (g2[((q >> 2) & 1) << 1 | (q & 1)], g2[((q >> 3) & 1) << 1 | ((q >> 1) & 1)])
    elif qam == 128:
        tI = [1, 3, 1, 3, 7, 5, 7, 5, 7, 5, 7, 5, 9, 11, 9, 11, 1, 3, 1, 3, 7, 5, 7, 5, 1, 3, 1, 3, 9, 11, 9, 11]
        tQ = [1, 1, 3, 3, 1, 1, 3, 3, 9, 9, 11, 11, 1, 1, 3, 3, 7, 7, 5, 5, 7, 7, 5, 5, 9, 9, 11, 11, 7, 7, 5, 5]
        for q in range(32): pts[q] = (tI[q], tQ[q])
    else:
        for q in range(64):
            pts[q] = (g3[((q >> 4) & 1) << 2 | ((q >> 2) & 1) << 1 | (q & 1)], g3[((q >> 5) & 1) << 2 | ((q >> 3) & 1) << 1 | ((q >> 1) & 1)])
    return pts

def full_constellation(qam):
    m = int(np.log2(qam)); pts = q1_points(qam)
    sym = np.zeros(qam, dtype=complex)
    for w in range(qam):
        Ik, Qk = (w >> (m - 1)) & 1, (w >> (m - 2)) & 1
        x, y = pts[w & ((1 << (m - 2)) - 1)]
        if (Ik, Qk) == (0, 0): I, Q = x, y
        elif (Ik, Qk) == (1, 0): I, Q = -y, x
        elif (Ik, Qk) == (1, 1): I, Q = -x, -y
        else: I, Q = y, -x
        sym[w] = I + 1j * Q
    return sym

def rrc(alpha, sps, ntaps):
    h = np.zeros(ntaps)
    for i in range(ntaps):
        t = (i - (ntaps - 1) / 2) / sps
        if abs(t) < 1e-9: h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * t) - 1) < 1e-9:
            h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha)) + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else:
            h[i] = (np.sin(np.pi * t * (1 - alpha)) + 4 * alpha * t * np.cos(np.pi * t * (1 + alpha))) / (np.pi * t * (1 - (4 * alpha * t) ** 2))
    return h / np.sum(h) * sps

def prbs_sequence():
    reg = 0x4A80; seq = np.zeros(8 * 188, dtype=np.uint8)
    for p in range(8):
        for i in range(188):
            if p == 0 and i == 0: continue
            b = 0
            for _ in range(8):
                out = ((reg >> 1) ^ reg) & 1; reg = (reg >> 1) | (out << 14); b = (b << 1) | out
            seq[p * 188 + i] = 0 if i == 0 else b
    return seq

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iq", required=True); ap.add_argument("--qam", type=int, default=256)
    ap.add_argument("--sps", type=int, default=2); ap.add_argument("--ts"); ap.add_argument("--json")
    ap.add_argument("--sr", type=float, default=6.9e6); ap.add_argument("--taps", type=int, default=128)
    ap.add_argument("--rolloff", type=float, default=0.15, help="RRC-Roll-off (Annex A 0,15, Annex C 0,13)")
    ap.add_argument("--max-symbols", type=int, default=400000)
    ap.add_argument("--rx", action="store_true", help="Funk-Mitschnitt: DC-Abzug, Träger-Frequenz/Phase per 4. Potenz nachführen")
    ap.add_argument("--dump-ts", help="dekodierten TS schreiben")
    ap.add_argument("--no-mf", action="store_true", help="Matched Filter schon im Empfänger (z. B. AD9361-RX-FIR RRC)")
    ap.add_argument("--up", type=int, default=0, help="Eingang mit --sps Samples/Symbol per FFT-Matched-Filter auf UP Samples/Symbol bringen")
    a = ap.parse_args()
    rep = {"qam": a.qam, "sps": a.sps}
    raw = np.fromfile(a.iq, dtype=np.int16)
    x = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
    rep["samples"] = int(len(x)); rep["rms_dbfs"] = float(20 * np.log10(np.sqrt(np.mean(np.abs(x) ** 2) / 2) / 32768))
    rep["papr_db"] = float(10 * np.log10(np.max(np.abs(x) ** 2) / np.mean(np.abs(x) ** 2)))
    # Spektrum: Schulter bei ±(0,6…0,8)·SR relativ zur Bandmitte (nur sps=2 sinnvoll)
    if a.sps == 2:
        n = min(len(x), 1 << 18); seg = x[:n] * np.hanning(n)
        P = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2; f = np.fft.fftshift(np.fft.fftfreq(n, 1 / (a.sr * a.sps)))
        inband = P[np.abs(f) < 0.4 * a.sr].mean(); sh = P[(np.abs(f) > 0.65 * a.sr) & (np.abs(f) < 0.9 * a.sr)].mean()
        rep["shoulder_db"] = float(10 * np.log10(sh / inband))
    if a.rx: x = x - np.mean(x)
    if a.up:
        # Matched Filter (RRC) im Frequenzbereich + Überabtastung durch Nullauffüllung
        nfft = len(x) - len(x) % 2; Xf = np.fft.fft(x[:nfft]); fr = np.fft.fftfreq(nfft, 1.0 / a.sps)   # in Symbolraten
        al = a.rolloff; af = np.abs(fr)
        Hm = np.where(af <= (1 - al) / 2, 1.0, np.where(af >= (1 + al) / 2, 0.0, np.sqrt(np.clip(0.5 * (1 + np.cos(np.pi / al * (af - (1 - al) / 2))), 0, 1))))
        if not a.no_mf: Xf = Xf * Hm
        n_up = nfft * a.up // a.sps; Xu = np.zeros(n_up, complex)
        h = nfft // 2; Xu[:h] = Xf[:h]; Xu[-h:] = Xf[-h:]
        x = np.fft.ifft(Xu) * (n_up / nfft); a.sps = a.up; a.taps = 0
    # Matched filter
    if a.taps == 0:
        y = x
    elif a.sps >= 2:
        h = rrc(a.rolloff, a.sps, a.taps)
        y = np.convolve(x, h / a.sps, mode="full")[len(h) // 2: len(h) // 2 + len(x)]
    else:
        y = x
    const = full_constellation(a.qam); m = int(np.log2(a.qam))
    # Abtastphase: die mit der kleinsten Entscheidungsabweichung
    def carrier(s):
        """4.-Potenz-Schätzung: Frequenz (FFT-Spitze) + blockweise Phase (π/2-Mehrdeutigkeit löst die Differenzcodierung)."""
        if not a.rx: return s
        n = len(s); p4 = (s / (np.abs(s) + 1e-9)) ** 4
        F = np.fft.fft(p4, 1 << int(np.ceil(np.log2(n)))); k = np.argmax(np.abs(F)); fq = np.fft.fftfreq(len(F))[k] / 4
        s = s * np.exp(-2j * np.pi * fq * np.arange(n))
        B = 1000; out = np.empty_like(s); prev = None
        for i in range(0, n, B):
            blk = s[i:i + B]; ph = np.angle(np.sum((blk / (np.abs(blk) + 1e-9)) ** 4)) / 4
            if prev is not None:   # Sprünge um π/2 vermeiden
                ph = prev + ((ph - prev + np.pi / 4) % (np.pi / 2) - np.pi / 4)
            prev = ph; out[i:i + B] = blk * np.exp(-1j * (ph + np.pi / 4))   # 4. Potenz von QAM liegt bei π → Ausrichtung
        return out
    best = None
    for ph in range(a.sps):
        s = carrier(y[ph::a.sps][: min(a.max_symbols, len(y) // a.sps)])
        scale = np.sqrt(np.mean(np.abs(const) ** 2) / np.mean(np.abs(s[1000:]) ** 2)) if len(s) > 2000 else 1
        sn = s * scale
        d = np.abs(sn[:, None] - const[None, :]).argmin(axis=1)
        err = np.mean(np.abs(sn - const[d]) ** 2) / np.mean(np.abs(const) ** 2)
        if best is None or err < best[0]: best = (err, ph, sn, d)
    err, ph, sn, dec = best
    if a.rx:
        # entscheidungsgesteuerter LMS-Entzerrer (symbolweise, 21 Taps), Start mit Einheitsimpuls
        L = 21; w = np.zeros(L, complex); w[L // 2] = 1.0; mu = 2e-4
        z = np.concatenate([np.zeros(L // 2), sn, np.zeros(L // 2)])
        out = np.empty(len(sn), complex)
        ntrain = min(len(sn), 300000)
        for it in range(2):                       # zwei Durchläufe über die Trainingsstrecke
            for n in range(ntrain):
                v = z[n:n + L][::-1]; yv = np.dot(w, v)
                d = const[np.argmin(np.abs(yv - const))]
                w += mu * (d - yv) * np.conj(v)
            mu *= 0.3
        # Anwenden (Faltung) auf alles
        out = np.convolve(z, w, mode="valid")[: len(sn)]
        sn = out
        dec = np.abs(sn[:, None] - const[None, :]).argmin(axis=1)
        err = np.mean(np.abs(sn - const[dec]) ** 2) / np.mean(np.abs(const) ** 2)
        rep["equalizer_taps"] = L
    rep["timing_phase"] = int(ph); rep["mer_db"] = float(-10 * np.log10(err + 1e-12))
    # Differenzdecodierung: Quadrantwinkel 0..3 (Q1=0, Q2=1, Q3=2, Q4=3)
    Ik = (dec >> (m - 1)) & 1; Qk = (dec >> (m - 2)) & 1
    ang = np.where((Ik == 0) & (Qk == 0), 0, np.where((Ik == 1) & (Qk == 0), 1, np.where((Ik == 1) & (Qk == 1), 2, 3)))
    # Startzustand der Differenzcodierung (I,Q)=(0,0) = Quadrant 1 annehmen; falls das erste Symbol
    # fehlt, wird der Bitversatz 0..7 in der Sync-Suche mitgesucht
    ang0 = np.concatenate([[0], ang]); rot = (ang0[1:] - ang0[:-1]) % 4
    AB = np.array([0, 2, 3, 1])[rot]           # 0→00, 1→10, 2→11, 3→01  (als 2-Bit-Zahl A<<1|B)
    words = (AB << (m - 2)) | (dec & ((1 << (m - 2)) - 1))
    bits_all = ((words[:, None] >> np.arange(m - 1, -1, -1)[None, :]) & 1).astype(np.uint8).reshape(-1)
    best_off, best_hits, best_bit = 0, -1, 0
    for bo in range(8):
        bits = bits_all[bo:]; nb = len(bits) // 8 * 8
        bb = np.packbits(bits[:nb])
        for off in range(204):
            idx = off + 204 * np.arange(min(64, (len(bb) - off) // 204))
            hits = int(np.sum((bb[idx] == 0x47) | (bb[idx] == 0xB8)))
            if hits > best_hits: best_hits, best_off, best_bit = hits, off, bo
    bits = bits_all[best_bit:]; nb = len(bits) // 8 * 8; bytes_ = np.packbits(bits[:nb])
    rep["sync_offset"] = int(best_off); rep["sync_bit_offset"] = int(best_bit); rep["sync_hits_of_64"] = int(best_hits)
    inter = bytes_[best_off:]; npk = len(inter) // 204; inter = inter[: npk * 204].reshape(npk, 204)
    # Deinterleaver: deint[p][i] = inter[p + (i mod 12)][i]
    nout = npk - 11
    deint = np.zeros((nout, 204), dtype=np.uint8)
    for i in range(204):
        j = i % 12
        deint[:, i] = inter[j: j + nout, i]
    # RS-Decoder
    rsc = RSCodec(16, nsize=255, fcr=0, prim=0x11d)
    ok, corrected, failed = 0, 0, 0; ts_pk = []
    for p in range(nout):
        try:
            msg, full, errata = rsc.decode(bytes(deint[p].tobytes()))
            ts_pk.append(np.frombuffer(bytes(msg), dtype=np.uint8)); ok += 1; corrected += len(errata)
        except Exception:
            failed += 1; ts_pk.append(deint[p, :188].copy())
    rep["rs_packets"] = nout; rep["rs_ok"] = ok; rep["rs_corrected_bytes"] = corrected; rep["rs_failed"] = failed
    ts = np.array(ts_pk, dtype=np.uint8)
    # Entwürfelung: 0xB8-Paket als Gruppenstart
    seq = prbs_sequence().reshape(8, 188)
    starts = np.where(ts[:, 0] == 0xB8)[0]
    rep["b8_markers"] = int(len(starts))
    if len(starts) == 0: rep["ok"] = False; print(json.dumps(rep, indent=1)); return 1
    g0 = int(starts[0]); n8 = (len(ts) - g0) // 8 * 8
    out = ts[g0: g0 + n8].copy()
    for p in range(n8): out[p, 1:] ^= seq[p % 8, 1:]
    out[:, 0] = 0x47
    out_bytes = out.reshape(-1)
    if a.dump_ts: out_bytes.tofile(a.dump_ts)
    rep["ts_packets_out"] = int(n8)
    if a.ts:
        ref = np.fromfile(a.ts, dtype=np.uint8); ref = ref[: len(ref) // 188 * 188].reshape(-1, 188)
        # Ausrichtung: erstes dekodiertes Paket im Referenz-TS suchen (Nutzlast-Pakete)
        key = out[0]; cand = np.where((ref == key).all(axis=1))[0]
        if len(cand) == 0:
            for k in range(1, min(16, n8)):
                cand = np.where((ref == out[k]).all(axis=1))[0]
                if len(cand): key = out[k]; cand = cand - k; break
        if len(cand) == 0: rep["ok"] = False; rep["note"] = "kein Alignment zum Referenz-TS"
        else:
            r0 = int(cand[0]); ncmp = min(n8, len(ref) - r0)
            ncmp = max(0, ncmp)
            diff = np.unpackbits(out[:ncmp] ^ ref[r0: r0 + ncmp]).sum()
            rep["compared_packets"] = int(ncmp); rep["bit_errors"] = int(diff); rep["ref_offset"] = r0
            rep["ok"] = bool(diff == 0 and ncmp > 0)
    else:
        rep["ok"] = bool(failed == 0)
    print(json.dumps(rep, indent=1))
    if a.json: json.dump(rep, open(a.json, "w"), indent=1)
    return 0 if rep.get("ok") else 1

if __name__ == "__main__":
    sys.exit(main())
