#!/usr/bin/env python3
"""atsc_iq_check.py IQ SYM PNG [--taps 81 --kaiser 4] [--json OUT]
1) Sample-Vergleich: atsc_mod-IQ (int16) gegen gr-dtv-Referenzkette (Symbole + Pilot → rotator −π/2 →
   fir_filter_ccf mit denselben Q15-Taps) → SNR in dB (Rest = Festkomma-Rundung).
2) Spektrum (Welch, 4096 Punkte): Pilotfrequenz/-pegel, −3/−20/−40-dB-Bandbreite, Schulter, PAPR, Clipping → PNG + JSON."""
import sys, os, json, math, argparse, tempfile
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("iq"); ap.add_argument("sym"); ap.add_argument("png")
ap.add_argument("--taps", type=int, default=81); ap.add_argument("--kaiser", type=float, default=4.0)
ap.add_argument("--json"); ap.add_argument("--max-samples", type=int, default=4_000_000)
a = ap.parse_args()
SR = 4.5e6 / 286 * 684

# --- Taps wie lib/atsc.c
N, beta, al = a.taps, a.kaiser, 0.1152
h = np.zeros(N)
for i in range(N):
    t = (i - (N - 1) / 2) / 2
    if abs(t) < 1e-9: x = 1 - al + 4 * al / math.pi
    elif abs(abs(4 * al * t) - 1) < 1e-9:
        x = al / math.sqrt(2) * ((1 + 2 / math.pi) * math.sin(math.pi / (4 * al)) + (1 - 2 / math.pi) * math.cos(math.pi / (4 * al)))
    else: x = (math.sin(math.pi * t * (1 - al)) + 4 * al * t * math.cos(math.pi * t * (1 + al))) / (math.pi * t * (1 - (4 * al * t) ** 2))
    if beta > 0: x *= np.i0(beta * math.sqrt(max(0.0, 1 - (2 * i / (N - 1) - 1) ** 2))) / np.i0(beta)
    h[i] = x
hq = np.round(h * 32767 / np.abs(h).max()) / 32768

iq = np.fromfile(a.iq, np.int16)[: 2 * a.max_samples].astype(np.float64)
z = iq[0::2] + 1j * iq[1::2]
res = {"samples": int(len(z))}

# --- 1) Referenz über gr-dtv/GNU Radio
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import atsc_grdtv
tmp = tempfile.mkdtemp()
np.fromfile(a.sym, np.uint8)[: len(z)].tofile(os.path.join(tmp, "s.u8"))
np.savetxt(os.path.join(tmp, "taps.txt"), hq)
atsc_grdtv.ref(os.path.join(tmp, "s.u8"), os.path.join(tmp, "taps.txt"), os.path.join(tmp, "ref.c64"))
ref = np.fromfile(os.path.join(tmp, "ref.c64"), np.complex64).astype(np.complex128)
n = min(len(ref), len(z)); skip = N
g = np.vdot(ref[skip:n], z[skip:n]) / np.vdot(ref[skip:n], ref[skip:n])
err = z[skip:n] - g * ref[skip:n]
res["ref_gain"] = float(abs(g)); res["ref_phase_deg"] = float(np.degrees(np.angle(g)))
res["snr_vs_grdtv_ref_db"] = float(10 * np.log10(np.mean(abs(g * ref[skip:n]) ** 2) / np.mean(abs(err) ** 2)))

# --- 2) Pegel und Spektrum
p = np.abs(z) ** 2
res["rms_dbfs"] = float(10 * np.log10(p.mean() / 32768 ** 2))
res["papr_db"] = float(10 * np.log10(p.max() / p.mean()))
res["papr_99_99_db"] = float(10 * np.log10(np.quantile(p, 0.9999) / p.mean()))
res["clipped_samples"] = int(((np.abs(iq) >= 32767)).sum())
nf = 4096
win = np.blackman(nf); segs = len(z) // nf
S = np.zeros(nf)
for k in range(segs - 1):
    S += np.abs(np.fft.fft(z[k * nf:(k + 1) * nf] * win)) ** 2
S = np.fft.fftshift(S); f = np.fft.fftshift(np.fft.fftfreq(nf, 1 / SR))
Sd = 10 * np.log10(S / S.max())
band = np.abs(f) < 2.5e6
ref_lvl = np.median(Sd[band])                       # Plateau
ip = np.argmax(Sd); res["pilot_hz"] = float(f[ip]); res["pilot_above_plateau_db"] = float(Sd[ip] - ref_lvl)
res["pilot_above_lower_edge_hz"] = float(f[ip] + 3e6)
for thr in (3, 20, 40):
    sel = np.nonzero(Sd - ref_lvl > -thr)[0]
    res[f"bw_{thr}db_hz"] = float(f[sel[-1]] - f[sel[0]])
res["level_at_pm3.25MHz_dbc"] = float(max(Sd[np.argmin(abs(f + 3.25e6))], Sd[np.argmin(abs(f - 3.25e6))]) - ref_lvl)
res["max_outside_pm3.5MHz_dbc"] = float(Sd[np.abs(f) > 3.5e6].max() - ref_lvl)

import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(f / 1e6, Sd - ref_lvl, lw=0.8)
ax.axvline(-3, color="gray", ls="--", lw=0.7); ax.axvline(3, color="gray", ls="--", lw=0.7)
ax.annotate(f"Pilot {f[ip]/1e6:+.4f} MHz\n({(f[ip]+3e6)/1e3:.0f} kHz über Kanalunterkante)", (f[ip] / 1e6, Sd[ip] - ref_lvl),
            xytext=(-1.5, 15), arrowprops=dict(arrowstyle="->"))
ax.set_xlim(-SR / 2e6, SR / 2e6); ax.set_ylim(-100, 25); ax.grid(alpha=0.3)
ax.set_xlabel("Frequenz relativ zum LO (Kanalmitte) / MHz"); ax.set_ylabel("dB relativ zum Plateau")
ax.set_title(f"atsc_mod 8-VSB, fs = {SR/1e6:.6f} MS/s, {N} Taps Kaiser {beta:g}, -3 dB-BW {res['bw_3db_hz']/1e6:.3f} MHz")
fig.tight_layout(); fig.savefig(a.png, dpi=110)
print(json.dumps(res))
if a.json: open(a.json, "w").write(json.dumps(res, indent=1))
