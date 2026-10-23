#!/usr/bin/env python3
"""Diagnose zu s2_symbols.py: vergleicht PLHEADER und Datensymbole getrennt, prüft Konjugation/Drehung/Versatz."""
import sys, os, subprocess
import numpy as np
from gnuradio import gr, blocks, dtv
mod = sys.argv[1] if len(sys.argv) > 1 else "qpsk"
modg = {"qpsk": dtv.MOD_QPSK, "8psk": dtv.MOD_8PSK}[mod]; modi = {"qpsk": 0, "8psk": 1}[mod]
rate = dtv.C3_4; ridx = 6; dfl = (48408 - 80) // 8
ts = np.random.default_rng(1).integers(0, 256, (dfl // 188 + 2) * 4 * 188, dtype=np.uint8); ts[::188] = 0x47
ch = [blocks.vector_source_b(list(ts), False),
      dtv.dvb_bbheader_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate, dtv.RO_0_35, dtv.INPUTMODE_NORMAL, dtv.INBAND_OFF, 168, 4000000),
      dtv.dvb_bbscrambler_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate),
      dtv.dvb_bch_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate),
      dtv.dvb_ldpc_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate, modg),
      dtv.dvbs2_interleaver_bb(dtv.FECFRAME_NORMAL, rate, modg),
      dtv.dvbs2_modulator_bc(dtv.FECFRAME_NORMAL, rate, modg, dtv.INTERPOLATION_OFF),
      dtv.dvbs2_physical_cc(dtv.FECFRAME_NORMAL, rate, modg, dtv.PILOTS_OFF, 0)]
hd = blocks.head(gr.sizeof_gr_complex, 100000); tb = gr.top_block(); sk = blocks.vector_sink_c(); tb.connect(*ch, hd, sk); tb.run()
g = np.array(sk.data(), dtype=np.complex64)
raw = subprocess.run([os.path.join(os.path.dirname(__file__), "..", "dvbs2_stage"), "--mod", str(modi), "--rate", str(ridx),
                      "--stage", "frame", "--frames", "2", "--A", "8000"], input=ts.tobytes(), capture_output=True).stdout
m = np.frombuffer(raw, dtype=np.int16).astype(np.float64); m = (m[0::2] + 1j * m[1::2]) / 8000
L = len(m) // 2
print("len g", len(g), "mine", len(m), "L", L)
np.set_printoptions(precision=2, suppress=True, linewidth=160)
print("gr   hdr", g[:8]); print("mine hdr", m[:8])
print("gr   dat", g[90:98]); print("mine dat", m[90:98])
def corr(a, b): return np.abs(np.vdot(a, b)) / np.sqrt(np.vdot(a, a).real * np.vdot(b, b).real)
for name, x in (("plain", m), ("conj", np.conj(m))):
    print(name, "hdr", round(corr(g[:90], x[:90]), 3), "data", round(corr(g[90:L], x[90:L]), 3),
          "frame2 data", round(corr(g[L + 90:2 * L], x[L + 90:2 * L]), 3), "g2 vs m1", round(corr(g[L + 90:2 * L], x[90:L]), 3))
