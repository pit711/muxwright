#!/usr/bin/env python3
"""Vergleicht die IQ-Ausgabe von dvbt_mod (int16 interleaved) mit der gr-dtv-Referenz (complex64):
Skalierung per Least-Squares, Fehler-SNR gesamt und je OFDM-Symbol, Sample-Versatz-Suche.
  python3 iq_compare.py --mine out.iq --ref ref.cf32 --symlen 8448 [--json r.json]
"""
import argparse, json, sys
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--mine", required=True); ap.add_argument("--ref", required=True)
ap.add_argument("--symlen", type=int, default=8448); ap.add_argument("--json")
a = ap.parse_args()
m16 = np.fromfile(a.mine, dtype=np.int16); mine = m16[0::2].astype(np.float64) + 1j * m16[1::2].astype(np.float64)
ref = np.fromfile(a.ref, dtype=np.complex64).astype(np.complex128)
n = min(len(mine), len(ref)); rep = {"samples_mine": len(mine), "samples_ref": len(ref)}
# Versatz-Suche über Kreuzkorrelation der ersten 3 Symbole
L = min(3 * a.symlen, n)
best = (0, -1)
for off in range(-16, 17):
    x = mine[max(0, off): max(0, off) + L - 32]; y = ref[max(0, -off): max(0, -off) + L - 32]
    c = abs(np.vdot(y, x)) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-12)
    if c > best[1]: best = (off, c)
off, corr = best; rep["offset"] = off; rep["corr_first3sym"] = float(corr)
x = mine[max(0, off): n]; y = ref[max(0, -off): n]; n = min(len(x), len(y)); x = x[:n]; y = y[:n]
scale = np.vdot(y, x) / np.vdot(y, y)          # komplexer Skalierungsfaktor (inkl. Phase)
rep["scale_abs"] = float(abs(scale)); rep["scale_phase_deg"] = float(np.degrees(np.angle(scale)))
err = x - scale * y
rep["snr_db_total"] = float(10 * np.log10(np.sum(np.abs(x) ** 2) / (np.sum(np.abs(err) ** 2) + 1e-12)))
nsym = n // a.symlen
per = []
for s in range(nsym):
    xs = x[s * a.symlen:(s + 1) * a.symlen]; es = err[s * a.symlen:(s + 1) * a.symlen]
    per.append(10 * np.log10(np.sum(np.abs(xs) ** 2) / (np.sum(np.abs(es) ** 2) + 1e-12)))
per = np.array(per)
rep["symbols"] = int(nsym); rep["snr_db_min_symbol"] = float(per.min()) if nsym else None
rep["snr_db_median_symbol"] = float(np.median(per)) if nsym else None
rep["bad_symbols_below_40db"] = [int(i) for i in np.where(per < 40)[0][:20]]
rep["ok"] = bool(nsym > 0 and per.min() > 45)
print(json.dumps(rep, indent=1))
if a.json: json.dump(rep, open(a.json, "w"), indent=1)
sys.exit(0 if rep["ok"] else 1)
