#!/usr/bin/env python3
"""Spektrum einer isdbt_mod-IQ-Datei (int16 LE I/Q, 512/63 MS/s) als PNG + JSON-Kennwerte.
Aufruf: isdbt_spectrum.py IQ PNG [TITEL]"""
import json, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fs = 512e6 / 63
iq = np.fromfile(sys.argv[1], np.int16).astype(np.float32)
x = (iq[0::2] + 1j * iq[1::2])[: int(fs * 2)]
nfft = 8192
win = np.hanning(nfft)
nseg = (len(x) - nfft) // (nfft // 2)
P = np.zeros(nfft)
for k in range(nseg):
    s = x[k * nfft // 2: k * nfft // 2 + nfft] * win
    P += np.abs(np.fft.fftshift(np.fft.fft(s))) ** 2
P /= nseg
f = (np.arange(nfft) - nfft // 2) * fs / nfft
ref = np.median(P[np.abs(f) < 2.5e6])
db = 10 * np.log10(P / ref + 1e-30)
occ_edge = 5617 * fs / 8192 / 2          # 5,572 MHz belegte Bandbreite
def lvl(lo, hi): m = (np.abs(f) >= lo) & (np.abs(f) <= hi); return float(np.max(db[m]))
res = {
    'samples': len(x), 'rms': float(np.sqrt(np.mean(np.abs(x) ** 2))), 'peak': float(np.max(np.abs(iq))),
    'papr_db': float(10 * np.log10(np.max(np.abs(x) ** 2) / np.mean(np.abs(x) ** 2))),
    'occupied_mhz': 2 * occ_edge / 1e6,
    'shoulder_dbc_3.0_3.1MHz': lvl(3.0e6, 3.1e6), 'max_dbc_3.2_3.5MHz': lvl(3.2e6, 3.5e6), 'max_dbc_3.5_4MHz': lvl(3.5e6, 4.0e6),
}
bw = f[db > -3]
res['bw_3db_mhz'] = float((bw.max() - bw.min()) / 1e6)
plt.figure(figsize=(10, 5))
plt.plot(f / 1e6, db, lw=0.6)
for e in (-3, 3): plt.axvline(e, color='r', ls='--', lw=0.8)
for e in (-occ_edge / 1e6, occ_edge / 1e6): plt.axvline(e, color='g', ls=':', lw=0.8)
plt.ylim(-90, 5); plt.xlim(-fs / 2e6, fs / 2e6); plt.grid(alpha=0.3)
plt.xlabel('Frequenz relativ zur Kanalmitte [MHz]'); plt.ylabel('PSD [dB rel. Kanalmitte]')
plt.title(sys.argv[3] if len(sys.argv) > 3 else 'isdbt_mod')
plt.text(-3.95, -84, 'rot: 6-MHz-Kanalgrenzen, grün: belegte Bandbreite 5,572 MHz (5617 Träger)', fontsize=8)
plt.tight_layout(); plt.savefig(sys.argv[2], dpi=110)
print(json.dumps(res))
