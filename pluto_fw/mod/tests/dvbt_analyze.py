#!/usr/bin/env python3
"""DVB-T-Signalanalyse (numpy) für Mitschnitte: Zeit-/Frequenzsynchronisation (Guard-Korrelation, Pilot-Suche),
Spiegelungstest, Symbolindex aus verstreuten Piloten, Kanalschätzung, MER, TPS-Decodierung (DBPSK, BCH-Prüfung).
  python3 dvbt_analyze.py --iq rx.iq [--fs 9142857.142857] [--mode 8k] [--guard 1/4] [--conj] [--scale12]
"""
import argparse, json, sys
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--iq", required=True); ap.add_argument("--fs", type=float, default=64e6 / 7)
ap.add_argument("--mode", default="8k"); ap.add_argument("--guard", default="1/4")
ap.add_argument("--conj", action="store_true"); ap.add_argument("--symbols", type=int, default=300)
ap.add_argument("--skip", type=int, default=100000)
a = ap.parse_args()
N = 8192 if a.mode == "8k" else 2048; K = 6817 if a.mode == "8k" else 1705; nrep = 4 if a.mode == "8k" else 1
G = N // int(a.guard.split("/")[1]); L = N + G
r = np.fromfile(a.iq, np.int16).astype(float)
x = (r[0::2] + 1j * r[1::2])[a.skip: a.skip + L * (a.symbols + 4)]
if a.conj: x = np.conj(x)
rep = {"file": a.iq, "conj": a.conj}

# --- PRBS / Pilotpositionen
wk = np.zeros(K, int); reg = 0x7FF
for k in range(K):
    wk[k] = reg & 1; nb = ((reg >> 2) ^ reg) & 1; reg = (reg >> 1) | (nb << 10)
cp2k = [0,48,54,87,141,156,192,201,255,279,282,333,432,450,483,525,531,618,636,714,759,765,780,804,873,888,918,939,942,969,984,1050,1101,1107,1110,1137,1140,1146,1206,1269,1323,1377,1491,1683,1704]
tps2k = [34,50,209,346,413,569,595,688,790,901,1073,1219,1262,1286,1469,1594,1687]
cps = np.array(sorted(set(c + 1704 * j for c in cp2k for j in range(nrep))))
tpsk = np.array(sorted(t + 1704 * j for t in tps2k for j in range(nrep)))

# --- 1. Zeit + Bruchteil-Frequenzversatz aus Guard-Korrelation (Mittel über viele Symbole)
c = x[N:] * np.conj(x[:-N])
cs = np.concatenate([[0], np.cumsum(c)]); g = cs[G:] - cs[:-G]
nsym = (len(g) - L) // L
acc = np.zeros(L, complex)
for s in range(nsym): acc += g[s * L: s * L + L]
t0 = int(np.argmax(np.abs(acc)))                 # Ende des Guard-Fensters → Symbolstart = t0 - G + 1 ... Nutzteil ab t0+1
eps = -np.angle(acc[t0]) / (2 * np.pi)           # Bruchteil-Versatz in Trägerabständen
rep["timing_metric"] = float(np.abs(acc[t0]) / np.mean(np.abs(acc)))
rep["cfo_frac_carriers"] = float(eps)
start = t0 - G + 1 + G                           # Beginn des Nutzteils des ersten ganzen Symbols
n = np.arange(len(x)); xc = x * np.exp(-2j * np.pi * eps * n / N)
# kleiner Vorlauf ins Guard (robust gegen Fensterung)
back = G // 8
syms = []
s0 = start - back
while s0 + N <= len(xc) and len(syms) < a.symbols:
    if s0 >= 0: syms.append(np.fft.fft(xc[s0: s0 + N]) * np.exp(2j * np.pi * np.arange(N) * (-back) / N))
    s0 += L
S = np.array(syms); ns = len(S)
# --- 2. ganzzahliger Versatz über kontinuierliche Piloten (Leistung)
pw = np.mean(np.abs(S) ** 2, axis=0)
best = None
for off in range(-200, 201):
    b = (cps - K // 2 + off) % N
    v = pw[b].mean()
    if best is None or v > best[1]: best = (off, v)
ioff = best[0]; rep["cfo_int_carriers"] = int(ioff)
rep["cfo_total_hz"] = float((ioff + eps) * a.fs / N)
rep["cp_boost_ratio"] = float(best[1] / np.median(pw))
kb = (np.arange(K) - K // 2 + ioff) % N            # Bin je Träger k
Y = S[:, kb]                                       # ns × K
# --- 3. Symbolindex mod 4 aus verstreuten Piloten
sp_score = np.zeros((ns, 4))
for l in range(ns):
    for m in range(4):
        idx = np.arange(3 * m, K, 12)
        sp_score[l, m] = np.mean(np.abs(Y[l, idx]) ** 2)
mod4 = np.argmax(sp_score, axis=1)
consistent = np.mean((mod4[1:] - mod4[:-1]) % 4 == 1)
rep["scattered_pilot_sequence_consistency"] = float(consistent)
# --- 4. Kanalschätzung aus verstreuten + kontinuierlichen Piloten (Vorzeichen aus w_k), MER der Datenzellen
pil_val = (1 - 2 * wk) * 4 / 3
mers, phases = [], []
H_all = np.zeros_like(Y)
for l in range(ns):
    m = mod4[l]
    pk = np.union1d(np.arange(3 * m, K, 12), cps)
    h = Y[l, pk] / pil_val[pk]
    H = np.interp(np.arange(K), pk, h.real) + 1j * np.interp(np.arange(K), pk, h.imag)
    H_all[l] = H
Z = Y / H_all
# Datenzellen = alle Träger außer Piloten/TPS
mer_l = []
qam = np.array([a + 1j * b for a in (-3, -1, 1, 3) for b in (-3, -1, 1, 3)]) / np.sqrt(10)
for l in range(ns):
    m = mod4[l]
    mask = np.ones(K, bool); mask[np.arange(3 * m, K, 12)] = False; mask[cps] = False; mask[tpsk] = False
    z = Z[l, mask]
    d = qam[np.argmin(np.abs(z[:, None] - qam[None, :]), axis=1)]
    mer_l.append(10 * np.log10(np.mean(np.abs(d) ** 2) / np.mean(np.abs(z - d) ** 2)))
rep["mer_db_median"] = float(np.median(mer_l)); rep["mer_db_min"] = float(np.min(mer_l))
# --- 5. TPS: DBPSK zwischen aufeinanderfolgenden Symbolen, Mehrheitsentscheid über alle TPS-Träger
tp = np.sum(Z[1:, tpsk] * np.conj(Z[:-1, tpsk]), axis=1)
bits = (tp.real < 0).astype(int)                   # 1 = Vorzeichenwechsel
sync1 = [int(c) for c in "0011010111101110"]; sync2 = [1 - b for b in sync1]
found = []
for i in range(len(bits) - 67):
    w = list(bits[i: i + 16])
    if w == sync1 or w == sync2: found.append(i)
rep["tps_sync_positions"] = found[:6]
if found:
    i = found[0]; s = [0] + list(bits[i: i + 67])  # s0..s67 (s0 unbekannt)
    def val(a0, n): return int("".join(map(str, s[a0: a0 + n])), 2)
    rep["tps"] = {"length_ind": format(val(17, 6), "06b"), "frame": val(23, 2), "constellation": ["QPSK", "16QAM", "64QAM", "res"][val(25, 2)],
                  "hierarchy": val(27, 3), "cr_hp": ["1/2", "2/3", "3/4", "5/6", "7/8", "?", "?", "?"][val(30, 3)],
                  "cr_lp": ["1/2", "2/3", "3/4", "5/6", "7/8", "?", "?", "?"][val(33, 3)], "guard": ["1/32", "1/16", "1/8", "1/4"][val(36, 2)],
                  "mode": ["2k", "8k", "4k", "res"][val(38, 2)]}
    # BCH-Prüfung
    reg = 0
    for b in s[1:54]:
        fb = (b ^ (reg >> 13)) & 1; reg = ((reg << 1) & 0x3FFF) ^ (0x0377 if fb else 0)
    rep["tps"]["bch_ok"] = (reg == val(54, 14))
print(json.dumps(rep, indent=1))
