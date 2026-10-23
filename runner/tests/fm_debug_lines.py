#!/usr/bin/env python3
"""Diagnose: Spektrallinien im Blockraster (12,5 Hz) im demodulierten L+R-Signal eines s16-Mitschnitts."""
import sys, math, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np
from scipy import signal
from fm import fm_check

path = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
mpx = fm_check.demod_file(path, 4096000, "s16", secs)
fs = 256000.0
h = signal.firwin(1023, 15800.0, window=("kaiser", 8.0), fs=fs)
m = signal.fftconvolve(mpx, h, mode="same")[20000:-20000]
n = len(m)
w = np.hanning(n)
P = np.abs(np.fft.rfft(m * w)) ** 2
f = np.fft.rfftfreq(n, 1.0 / fs)
res = f[1] - f[0]
tone_i = int(np.argmax(P[(f > 20)]))
peak = P.max()
print("Aufloesung %.3f Hz, Dauer %.1f s" % (res, n / fs))
rows = []
for k in range(1, 1200):
    fc = k * 12.5
    i = int(round(fc / res))
    if i + 40 >= len(P):
        break
    line = P[i - 1:i + 2].max()
    floor = np.median(np.concatenate([P[i - 40:i - 5], P[i + 5:i + 40]]))
    rows.append((10 * math.log10(line / floor), fc, 10 * math.log10(line / peak)))
rows.sort(reverse=True)
print("Top-Linien im 12,5-Hz-Raster: (dB ueber Boden, Hz, dBc)")
for r in rows[:15]:
    print("  %.1f dB  %.1f Hz  %.1f dBc" % r)
# staerkste Linien insgesamt ausser Ton
idx = np.argsort(P)[::-1]
print("Staerkste Linien insgesamt (Hz, dBc):")
shown = 0
last = -100
for i in idx:
    if abs(f[i] - last) < 2:
        continue
    print("  %.2f Hz  %.1f dBc" % (f[i], 10 * math.log10(P[i] / peak)))
    last = f[i]
    shown += 1
    if shown >= 12:
        break
