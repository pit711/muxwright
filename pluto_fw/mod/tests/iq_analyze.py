#!/usr/bin/env python3
"""Spektrum-/Pegelanalyse einer IQ-Datei (int16 interleaved): RMS, PAPR, Clipping, Schulterabstand und
DVB-T-Spektrummaske (EN 300 744 Tabelle 19 „critical“ / Figur 13 nicht-kritisch, 4-kHz-Messbandbreite).
  python3 iq_analyze.py --iq out.iq --rate 9142857.142857 --bw 8 [--dvbc-sr 6.9e6] [--json r.json]
"""
import argparse, json, sys
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--iq", required=True); ap.add_argument("--rate", type=float, required=True)
ap.add_argument("--bw", type=float, default=0, help="DVB-T Kanalbandbreite MHz (0 = keine Maske)")
ap.add_argument("--dvbc-sr", type=float, default=0, help="DVB-C Symbolrate (Schulter bei 0,65..0,9 SR)")
ap.add_argument("--json"); ap.add_argument("--skip", type=int, default=0, help="Samples am Anfang überspringen")
a = ap.parse_args()
raw = np.fromfile(a.iq, dtype=np.int16)[2 * a.skip:]
x = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
rep = {"samples": int(len(x)), "rate": a.rate}
p = np.abs(x) ** 2
rep["rms_dbfs"] = float(10 * np.log10(np.mean(p) / 2 / 32768 ** 2))
rep["papr_db"] = float(10 * np.log10(p.max() / p.mean()))
rep["papr_99_99_db"] = float(10 * np.log10(np.percentile(p, 99.99) / p.mean()))
rep["clipped_samples"] = int(np.sum((np.abs(raw) >= 32767)))
# Welch-PSD mit 4-kHz-Auflösung (nfft = rate/4000)
nfft = int(2 ** np.ceil(np.log2(a.rate / 4000)))
nseg = len(x) // nfft
if nseg < 1: print("zu kurz"); sys.exit(1)
w = np.hanning(nfft); acc = np.zeros(nfft)
for s in range(min(nseg, 64)):
    acc += np.abs(np.fft.fft(x[s * nfft:(s + 1) * nfft] * w)) ** 2
psd = np.fft.fftshift(acc / min(nseg, 64)); f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / a.rate))
total = psd.sum()
rel = 10 * np.log10(psd / total + 1e-30)          # Pegel je 4-kHz-Bin relativ zur Gesamtleistung
rep["psd_resolution_hz"] = float(a.rate / nfft)
if a.bw > 0:
    half = 3.805e6 * a.bw / 8                       # Nutzband ±3,805 MHz (8 MHz)
    inb = rel[np.abs(f) < 0.9 * half].mean()
    rep["inband_level_db_4khz"] = float(inb)
    pts = {}
    for off in [3.9, 4.2, 5.0, 6.0]:
        fo = off * 1e6 * a.bw / 8
        sel = (np.abs(np.abs(f) - fo) < a.rate / nfft)
        if sel.any(): pts[f"{off}MHz"] = float(rel[sel].max())
    rep["mask_points_db"] = pts
    # Masken (8 MHz, relativ zur Gesamtleistung): nicht-kritisch (Fig. 13 Gesamtbild) -32.8@3.9, -73@4.2, -85@6, -110@12; kritisch -32.8@3.8, -83@4.2, -95@6, -120@12
    nc = {3.9: -32.8, 4.2: -73.0, 6.0: -85.0}; cr = {3.9: -32.8, 4.2: -83.0, 6.0: -95.0}
    rep["mask_noncritical_ok"] = all(pts.get(f"{k}MHz", -200) <= v + 0.5 for k, v in nc.items())
    rep["mask_critical_ok"] = all(pts.get(f"{k}MHz", -200) <= v + 0.5 for k, v in cr.items())
    sh = rel[(np.abs(f) > 1.05 * half) & (np.abs(f) < 1.25 * half)].mean()
    rep["shoulder_db"] = float(sh - inb)
if a.dvbc_sr > 0:
    inb = rel[np.abs(f) < 0.4 * a.dvbc_sr].mean(); sh = rel[(np.abs(f) > 0.65 * a.dvbc_sr) & (np.abs(f) < 0.9 * a.dvbc_sr)].mean()
    rep["shoulder_db"] = float(sh - inb)
print(json.dumps(rep, indent=1))
if a.json: json.dump(rep, open(a.json, "w"), indent=1)
