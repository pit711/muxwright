#!/usr/bin/env python3
"""DTMB-Spektrum, Pegel und FIR-Gegenprobe.
Aufruf (WSL): dtmb_spectrum.py DTMB_MOD WORKDIR [PNG] [-- dtmb_mod-Optionen]
  * erzeugt 0,5 s Signal mit --sps 2 (15,12 MS/s, SRRC in Software) und mit --sps 1 (7,56 MS/s) + --fir-out,
  * FIR-Gegenprobe: sps-1-Ausgabe ×2 hochgetastet und mit den Taps der AD9361-FIR-Datei gefaltet (wie der TX-FIR mit
    INT 2) muss bis auf Rundung der sps-2-Ausgabe entsprechen,
  * Spektrum (Welch, 8192 Punkte, Hann) relativ zur mittleren Leistungsdichte im Nutzband |f| < 3,5 MHz:
    −3/−20/−40-dB-Breite, Pegel an der Kanalkante ±4,0 MHz, ab ±4,1/4,2/4,5/5 MHz (Maximum), 99-%-Bandbreite,
  * RMS (dBFS), PAPR (99,99 % und Maximum), Anteil gesättigter Samples.
Ergebnis als JSON auf stdout (und tests/results/dtmb_spectrum*.json)."""
import sys, os, json, subprocess
import numpy as np
from scipy.signal import welch

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
extra = []
if '--' in args:
    i = args.index('--'); extra = args[i + 1:]; args = args[:i]
mod, work = args[0], args[1]
png = args[2] if len(args) > 2 else None
os.makedirs(work, exist_ok=True)
fs2 = 15.12e6
f2, f1, fir = (os.path.join(work, n) for n in ('spec_sps2.ci16', 'spec_sps1.ci16', 'spec.fir'))
base = [mod, '-i', 'prbs', '--seconds', '0.5', '--quiet'] + extra
subprocess.run(base + ['-o', f2, '--sps', '2'], check=True)
subprocess.run(base + ['-o', f1, '--sps', '1'], check=True)
subprocess.run([mod, '--fir-out', fir] + extra, check=True, capture_output=True)
x2 = np.fromfile(f2, np.int16).astype(np.float64); x2 = x2[0::2] + 1j * x2[1::2]
x1 = np.fromfile(f1, np.int16).astype(np.float64); x1 = x1[0::2] + 1j * x1[1::2]
taps = np.array([int(l.split(',')[0]) for l in open(fir) if l[:1] in '-0123456789'], float)
# FIR-Gegenprobe (Nachbildung TX-FIR INT 2: Nullen einfügen, falten, /32768)
u = np.zeros(2 * len(x1), complex); u[0::2] = x1
y = np.convolve(u, taps)[: len(u)] / 32768.0
n = min(len(y), len(x2))
err = np.abs(y[:n] - x2[:n])
fir_check = dict(taps=int(len(taps)), max_abs_err_lsb=float(err.max()), rms_err_lsb=float(np.sqrt(np.mean(err ** 2))),
                 snr_db=float(10 * np.log10(np.mean(np.abs(x2[:n]) ** 2) / np.mean(err ** 2))))
# Spektrum
f, P = welch(x2, fs=fs2, nperseg=8192, window='hann', detrend=False, return_onesided=False, scaling='density')
order = np.argsort(f); f, P = f[order], P[order]
ref = P[np.abs(f) < 3.5e6].mean()
D = 10 * np.log10(P / ref)
def width(th):
    m = np.nonzero(D >= th)[0]
    return float(f[m[-1]] - f[m[0]])
def at(fr): return float(np.interp(fr, f, D))
def maxbeyond(fr): return float(D[np.abs(f) >= fr].max())
cum = np.cumsum(P) / P.sum()
occ = float(f[np.searchsorted(cum, 0.995)] - f[np.searchsorted(cum, 0.005)])
pw = np.abs(x2) ** 2
rms = np.sqrt(pw.mean())
res = dict(options=' '.join(extra), sample_rate=fs2,
           bw_3db_hz=width(-3), bw_20db_hz=width(-20), bw_40db_hz=width(-40), occupied_99_hz=occ,
           level_dbc={'+-3.78MHz': round((at(3.78e6) + at(-3.78e6)) / 2, 1), '+-3.969MHz': round((at(3.969e6) + at(-3.969e6)) / 2, 1),
                      '+-4.0MHz': round((at(4.0e6) + at(-4.0e6)) / 2, 1)},
           max_beyond_dbc={'4.1MHz': round(maxbeyond(4.1e6), 1), '4.2MHz': round(maxbeyond(4.2e6), 1),
                           '4.5MHz': round(maxbeyond(4.5e6), 1), '5MHz': round(maxbeyond(5e6), 1)},
           rms_dbfs=float(20 * np.log10(rms / 32767)), papr_9999_db=float(10 * np.log10(np.quantile(pw, 0.9999) / pw.mean())),
           papr_max_db=float(10 * np.log10(pw.max() / pw.mean())),
           saturated_fraction=float(np.mean((np.abs(np.fromfile(f2, np.int16)) >= 32767))), fir_check=fir_check)
print(json.dumps(res))
tag = '_'.join(a.lstrip('-') for a in extra) or 'default'
os.makedirs(os.path.join(HERE, 'results'), exist_ok=True)
json.dump(res, open(os.path.join(HERE, 'results', f'dtmb_spectrum_{tag}.json'), 'w'), indent=1)
if png:
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(f / 1e6, D, lw=0.7)
    for v in (-4, 4): ax.axvline(v, color='r', ls='--', lw=0.6)
    for v in (-3.969, 3.969): ax.axvline(v, color='g', ls=':', lw=0.6)
    ax.set_xlim(-7.56, 7.56); ax.set_ylim(-90, 5); ax.grid(alpha=0.3)
    ax.set_xlabel('Frequenz relativ zum LO (MHz)'); ax.set_ylabel('dB relativ Nutzband')
    ax.set_title(f'dtmb_mod {" ".join(extra) or "(Standard)"} – SRRC α=0,05, {len(taps)} Taps, 15,12 MS/s')
    fig.tight_layout(); fig.savefig(png, dpi=110)
for p_ in (f1, f2):
    os.remove(p_)
