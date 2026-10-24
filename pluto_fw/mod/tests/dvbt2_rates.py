#!/usr/bin/env python3
"""Nutzrate dvbt2_mod --print-rates gegen eine unabhängige Rechnung nach EN 302 755 (eigene Umsetzung in Python, Werte aus
Tab. 6, 46–51, 65, 67 und Abschn. 7.3.1.2/8.3.1/8.3.2 abgetippt). TS-Rate in HEM: N_FEC·(Kbch−80)·188/187 / T_F.
  python3 dvbt2_rates.py BIN"""
import json, math, subprocess, sys

BIN = sys.argv[1]
T = {"1.7": 71 / 131, "5": 7 / 40, "6": 7 / 48, "7": 1 / 8, "8": 7 / 64, "10": 7 / 80}          # µs, Tab. 65
KBCH = {(64800, "1/2"): 32208, (64800, "3/5"): 38688, (64800, "2/3"): 43040, (64800, "3/4"): 48408, (64800, "4/5"): 51648,
        (64800, "5/6"): 53840, (16200, "1/2"): 7032, (16200, "3/5"): 9552, (16200, "2/3"): 10632, (16200, "3/4"): 11712,
        (16200, "4/5"): 12432, (16200, "5/6"): 13152}                                              # Tab. 6
ETA = {"qpsk": 2, "16qam": 4, "64qam": 6, "256qam": 8}
NP2 = {1: 16, 2: 8, 4: 4, 8: 2, 16: 1, 32: 1}; CP2 = {1: 558, 2: 1118, 4: 2236, 8: 4472, 16: 8944, 32: 22432}   # Tab. 51, 47
CDATA = {  # Tab. 48 (ohne TR): (fft, ext) → PP1..PP8
    (1, 0): [764, 768, 798, 804, 818, 0, 0, 0], (2, 0): [1522, 1532, 1596, 1602, 1632, 0, 1646, 0],
    (4, 0): [3084, 3092, 3228, 3234, 3298, 0, 3328, 0],
    (8, 0): [6208, 6214, 6494, 6498, 6634, 0, 6698, 6698], (8, 1): [6296, 6298, 6584, 6588, 6728, 0, 6788, 6788],
    (16, 0): [12418, 12436, 12988, 13002, 13272, 13288, 13416, 13406], (16, 1): [12678, 12698, 13262, 13276, 13552, 13568, 13698, 13688],
    (32, 0): [0, 24886, 0, 26022, 0, 26592, 26836, 26812], (32, 1): [0, 25412, 0, 26572, 0, 27152, 27404, 27376]}
CFC = {  # Tab. 50
    (1, 0): [402, 654, 490, 707, 544, 0, 0, 0], (2, 0): [804, 1309, 980, 1415, 1088, 0, 1396, 0], (4, 0): [1609, 2619, 1961, 2831, 2177, 0, 2792, 0],
    (8, 0): [3218, 5238, 3922, 5662, 4354, 0, 5585, 0], (8, 1): [3264, 5312, 3978, 5742, 4416, 0, 5664, 0],
    (16, 0): [6437, 10476, 7845, 11324, 8709, 11801, 11170, 0], (16, 1): [6573, 10697, 8011, 11563, 8893, 12051, 11406, 0],
    (32, 0): [0, 20952, 0, 22649, 0, 23603, 0, 0], (32, 1): [0, 21395, 0, 23127, 0, 24102, 0, 0]}
FC = {  # Tab. 64 (SISO): (fft, GI) → Pilotmuster mit Frame-Closing-Symbol
    32: {"1/32": [6], "1/16": [4], "19/256": [4], "1/8": [2], "19/128": [2]},
    16: {"1/32": [7, 6], "1/16": [4, 5], "19/256": [4, 5], "1/8": [2, 3], "19/128": [2, 3], "1/4": [1]},
    8: {"1/32": [7], "1/16": [4, 5], "19/256": [4, 5], "1/8": [2, 3], "19/128": [2, 3], "1/4": [1]},
    4: {"1/32": [7], "1/16": [4, 5], "1/8": [2, 3], "1/4": [1]}, 2: {"1/32": [7], "1/16": [4, 5], "1/8": [2, 3], "1/4": [1]},
    1: {"1/16": [4, 5], "1/8": [2, 3], "1/4": [1]}}


def expected(bw, fftk, ext, mod, rate, gi, pp, fec, l1, ldata=None):
    n, (a, b) = fftk * 1024, map(int, gi.split("/"))
    guard = n * a // b
    Tu = T[bw]
    ts = (n + guard) * Tu
    lfmax = int((250000 - 2048 * Tu) // ts)
    if fftk == 32 and lfmax % 2: lfmax -= 1
    np2 = NP2[fftk]
    ld = ldata or lfmax - np2
    fc = pp in FC[fftk].get(gi, [])
    cd = CDATA[(fftk, ext)][pp - 1]
    ctot = np2 * CP2[fftk] + ((ld - 1) * cd + CFC[(fftk, ext)][pp - 1] if fc else ld * cd)
    # L1-post (7.3.1.2): 318 Infobits + CRC; L1-pre 1840 Zellen
    eta1 = {"bpsk": 1, "qpsk": 2, "16qam": 4, "64qam": 6}[l1]
    ks = 318 + 32
    npt = max((np2 * eta1 if np2 > 1 else 2 * eta1) - 1, 6 * (7032 - ks) // 5)
    npost_t = ks + 168 + 9000 - npt
    m = 2 * eta1 if np2 == 1 else eta1 * np2
    npost = math.ceil(npost_t / m) * m
    dl1 = 1840 + npost // eta1
    ncells = fec // ETA[mod]
    nfec = min((ctot - dl1) // ncells, 1023)
    tf = (2048 + (np2 + ld) * (n + guard)) * Tu * 1e-6
    return nfec * (KBCH[(fec, rate)] - 80) * 188 / 187 / tf, nfec, ld, tf


CASES = [("8", 32, 1, "64qam", "2/3", "19/256", 4, 64800, "bpsk"),   # DE
         ("8", 8, 1, "64qam", "3/5", "1/8", 2, 64800, "bpsk"),       # Leicht
         ("8", 32, 1, "256qam", "2/3", "1/128", 7, 64800, "bpsk"),   # UK-Profil (Vergleichswert ~40,2 Mbit/s)
         ("8", 16, 1, "256qam", "3/4", "1/8", 2, 64800, "64qam"),
         ("8", 8, 0, "16qam", "1/2", "1/16", 4, 16200, "16qam"),
         ("8", 2, 0, "qpsk", "3/5", "1/4", 1, 16200, "qpsk"),
         ("7", 8, 1, "64qam", "2/3", "19/128", 8, 64800, "64qam"),
         ("6", 32, 1, "64qam", "3/4", "1/16", 4, 64800, "bpsk"),
         ("1.7", 1, 0, "qpsk", "1/2", "1/16", 4, 16200, "bpsk")]
ok = True
for bw, fk, ext, mod, rate, gi, pp, fec, l1 in CASES:
    args = ["--bw", bw, "--fft", f"{fk}k", "--ext", str(ext), "--const", mod, "--rate", rate, "--guard", gi, "--pp", str(pp),
            "--fec", str(fec), "--l1-const", l1]
    r = json.loads(subprocess.run([BIN, "--print-rates"] + args, capture_output=True, text=True, check=True).stdout)
    e, nfec, ld, tf = expected(bw, fk, ext, mod, rate, gi, pp, fec, l1)
    good = abs(r["ts_bps"] - e) < 1e-3 and r["nfec"] == nfec and r["ldata"] == ld
    ok &= good
    print(f"{bw:>3} MHz {fk:2d}K{'E' if ext else ' '} {mod:6s} {rate} GI {gi:6s} PP{pp} {fec} L1 {l1:5s}: Ldata {r['ldata']:4d} "
          f"NFEC {r['nfec']:4d} TF {r['frame_s']*1e3:7.3f} ms  dvbt2_mod {r['ts_bps']/1e6:8.4f} Mbit/s  Norm-Rechnung {e/1e6:8.4f}  "
          f"{'OK' if good else 'ABWEICHUNG'}")
print("ALLE RATEN GLEICH" if ok else "ABWEICHUNGEN")
sys.exit(0 if ok else 1)
