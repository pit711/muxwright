#!/usr/bin/env python3
"""Prüfdemodulator für pal_bg_gen.py-Dateien oder Pluto-RX-Mitschnitte: Hüllkurve um den Bildträger →
Zeilenraster (Sync-Suche) → Graustufenbild (PNG über ffmpeg) + Kennwerte (Zeilenfrequenz, Sync-Tiefe, Tonträger).
  python3 pal_check.py --iq pal_k36.iq --fs 8e6 --carrier -2.25e6 --png bild.png [--cs16|--cf32]
"""
import argparse, json, subprocess
import numpy as np
ap = argparse.ArgumentParser()
ap.add_argument("--iq", required=True); ap.add_argument("--fs", type=float, default=8e6)
ap.add_argument("--carrier", type=float, default=-2.25e6); ap.add_argument("--png")
ap.add_argument("--cf32", action="store_true"); ap.add_argument("--skip", type=int, default=0)
a = ap.parse_args()
if a.cf32: x = np.fromfile(a.iq, np.complex64).astype(complex)
else:
    r = np.fromfile(a.iq, np.int16).astype(float); x = r[0::2] + 1j * r[1::2]
x = x[a.skip: a.skip + int(a.fs * 0.16)]
n = len(x); t = np.arange(n) / a.fs
X = np.fft.fft(x * np.exp(-2j * np.pi * a.carrier * t)); f = np.fft.fftfreq(n, 1 / a.fs)
# Spektrum-Kennwerte
P = np.abs(X) ** 2
def lvl(f0, bw):
    m = np.abs(f - f0) < bw; return 10 * np.log10(P[m].sum() + 1e-30)
rep = {"carrier_offset_hz": a.carrier, "sound_rel_db": lvl(5.5e6, 150e3) - lvl(0, 20e3) if a.fs > 2 * (abs(a.carrier) + 5.6e6) or a.carrier < 0 else None}
# Feinfrequenz des Bildträgers (für RX-Mitschnitt)
m = np.abs(f) < 50e3; k = np.argmax(P * m); rep["vision_carrier_fine_hz"] = float(a.carrier + f[k])
X[np.abs(f) > 1.0e6] = 0       # Hüllkurve nur aus dem DSB-Bereich (±1 MHz) → robust
env = np.abs(np.fft.ifft(X * (np.abs(f) <= 1.0e6)))
spl = int(round(64e-6 * a.fs))
# Zeilensync: Maximum der Hüllkurve (Synchronboden) periodisch alle spl Samples
prof = env[: (n // spl) * spl].reshape(-1, spl)
phase = int(np.argmax(np.convolve(prof.mean(0), np.ones(int(4.7e-6 * a.fs)), "same")))
start = (phase - int(2.35e-6 * a.fs)) % spl
e = np.roll(env, -start)[: (n // spl) * spl].reshape(-1, spl)
lines = e.shape[0]
synctip = np.percentile(e[:, : int(4e-6 * a.fs)], 50)
blank = np.median(e[:, int(5e-6 * a.fs): int(5.5e-6 * a.fs)])
white = np.percentile(e, 1)
rep.update({"lines": int(lines), "sync_tip": float(synctip), "blank_rel": float(blank / synctip), "white_rel": float(white / synctip)})
# Bild aus den aktiven Zeilen (erste 625 Zeilen), negativ moduliert → hell = kleine Hüllkurve
img = e[:625, int(10.5e-6 * a.fs): int(62.5e-6 * a.fs)]
img = np.clip((blank - img) / (blank - white + 1e-9), 0, 1)
if a.png:
    h, w = img.shape
    raw = (img * 255).astype(np.uint8).tobytes()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-i", "-",
                    "-vf", "scale=768:576", a.png], input=raw, check=True)
print(json.dumps(rep, indent=1))
