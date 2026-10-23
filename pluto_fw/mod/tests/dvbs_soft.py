#!/usr/bin/env python3
"""IQ (int16) → weiche QPSK-Entscheidungen int8 für dvbs_rx. Datei (sps 2, bekannte Lage) oder Funk-Mitschnitt
(--rx: 1 Sample/Symbol nach AD9361-RX-FIR als Matched Filter, FFT-Überabtastung, Abtastphase, Träger per 4. Potenz).
  python3 dvbs_soft.py --iq x.iq --sps 2 -o soft.s8 [--rx --up 8] [--max 1000000]"""
import argparse, json
import numpy as np
ap = argparse.ArgumentParser()
ap.add_argument("--iq", required=True); ap.add_argument("-o", required=True); ap.add_argument("--sps", type=int, default=2)
ap.add_argument("--rx", action="store_true"); ap.add_argument("--up", type=int, default=8); ap.add_argument("--max", type=int, default=1000000)
ap.add_argument("--taps", type=int, default=64); ap.add_argument("--skip", type=int, default=0)
a = ap.parse_args()
r = np.fromfile(a.iq, np.int16).astype(float); x = (r[0::2] + 1j * r[1::2])[a.skip:]
x = x[: (a.max + 64) * a.sps]
def rrc(alpha, sps, n):
    t = (np.arange(n) - (n - 1) / 2) / sps; h = np.zeros(n)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-9: h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * ti) - 1) < 1e-9: h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha)) + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else: h[i] = (np.sin(np.pi * ti * (1 - alpha)) + 4 * alpha * ti * np.cos(np.pi * ti * (1 + alpha))) / (np.pi * ti * (1 - (4 * alpha * ti) ** 2))
    return h / h.sum()
rep = {}
if not a.rx:
    y = np.convolve(x, rrc(0.35, a.sps, a.taps), mode="full")[a.taps // 2:]
    best = max(range(a.sps), key=lambda p: np.mean(np.abs(y[p::a.sps][:20000])))
    s = y[best::a.sps]
else:
    x = x - x.mean(); n = len(x) - len(x) % 2; X = np.fft.fft(x[:n]); nu = n * a.up // a.sps
    U = np.zeros(nu, complex); h = n // 2; U[:h] = X[:h]; U[-h:] = X[-h:]; y = np.fft.ifft(U); sp = a.up // a.sps * a.sps
    best = max(range(a.up), key=lambda p: np.mean(np.abs(np.real(y[p::a.up][:40000] * np.exp(-1j * np.angle(np.mean((y[p::a.up][:40000]) ** 4)) / 4)) ** 2 - 0)))
    # Abtastphase: minimale Streuung der 4.-Potenz-Normierten Punkte
    def spread(p):
        z = y[p::a.up][:40000]; z = z / np.sqrt(np.mean(np.abs(z) ** 2)); ph = np.angle(np.mean(z ** 4)) / 4 + np.pi / 4
        z = z * np.exp(-1j * ph); return np.mean((np.abs(z.real) - np.mean(np.abs(z.real))) ** 2 + (np.abs(z.imag) - np.mean(np.abs(z.imag))) ** 2)
    best = min(range(a.up), key=spread); s = y[best::a.up]
    # Träger: Frequenz per FFT der 4. Potenz, Phase blockweise
    p4 = (s / (np.abs(s) + 1e-9)) ** 4; F = np.fft.fft(p4, 1 << int(np.ceil(np.log2(len(s))))); k = np.argmax(np.abs(F))
    fq = np.fft.fftfreq(len(F))[k] / 4; s = s * np.exp(-2j * np.pi * fq * np.arange(len(s)))
    B = 500; out = np.empty_like(s); prev = None
    for i in range(0, len(s), B):
        blk = s[i:i + B]; ph = np.angle(np.sum((blk / (np.abs(blk) + 1e-9)) ** 4)) / 4
        if prev is not None: ph = prev + ((ph - prev + np.pi / 4) % (np.pi / 2) - np.pi / 4)
        prev = ph; out[i:i + B] = blk * np.exp(-1j * (ph + np.pi / 4))
    s = out; rep["carrier_offset_symbols"] = float(fq)
s = s[: a.max]; s = s / np.sqrt(np.mean(np.abs(s) ** 2)) * np.sqrt(2)
d = np.sign(s.real) + 1j * np.sign(s.imag)
rep["mer_db"] = float(10 * np.log10(np.mean(np.abs(d) ** 2) / np.mean(np.abs(s - d) ** 2))); rep["phase"] = int(best); rep["symbols"] = len(s)
soft = np.empty(2 * len(s), np.int8)
soft[0::2] = np.clip(np.round(s.real * 40), -127, 127); soft[1::2] = np.clip(np.round(s.imag * 40), -127, 127)
soft.tofile(a.o); print(json.dumps(rep))
