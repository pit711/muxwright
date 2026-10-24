#!/usr/bin/env python3
"""atsc_grdtv.py – gr-dtv (GNU Radio 3.10) als Referenz für atsc_mod.

  tx  TS SYM_OUT                 gr-dtv-Sender bis zum Feldsync-Mux → Symbolindizes 0..7 (uint8, 832/Segment)
  ref SYM_IN TAPS IQ_OUT         Symbolindizes → +1,25 Pilot → Drehung −fs/4 → FIR (Taps als Textdatei) → complex64
  rx  IQ_IN TS_OUT [RATE] [SPS] [SCALE]  gr-dtv-ATSC-Empfänger (dtv.atsc_rx): int16-IQ → TS
GPL-3.0-or-later (nutzt gr-dtv zur Laufzeit)."""
import sys, math
from gnuradio import gr, blocks, dtv, filter as grfilter

SR = 4.5e6 / 286 * 684


def tx(ts, sym_out):
    tb = gr.top_block()
    src = blocks.file_source(1, ts, False)
    chain = [src, dtv.atsc_pad(), dtv.atsc_randomizer(), dtv.atsc_rs_encoder(), dtv.atsc_interleaver(),
             dtv.atsc_trellis_encoder(), dtv.atsc_field_sync_mux(), blocks.vector_to_stream(1, 1024),
             blocks.keep_m_in_n(1, 832, 1024, 4), blocks.file_sink(1, sym_out)]
    tb.connect(*chain)
    tb.run()


def ref(sym_in, taps_file, iq_out):
    taps = [float(x) for x in open(taps_file).read().split()]
    tb = gr.top_block()
    src = blocks.file_source(1, sym_in, False)
    tofl = blocks.uchar_to_float()
    lev = blocks.multiply_const_ff(2.0)
    off = blocks.add_const_ff(-7.0 + 1.25)
    f2c = blocks.float_to_complex()
    # Drehung −fs/4 exakt als periodische Folge 1, −j, −1, +j (rotator_cc driftet in float über Mio. Samples)
    lo = blocks.vector_source_c([1, -1j, -1, 1j], True)
    rot = blocks.multiply_cc()
    fir = grfilter.fir_filter_ccf(1, taps)
    tb.connect(src, tofl, lev, off, f2c, (rot, 0))
    tb.connect(lo, (rot, 1))
    tb.connect(rot, fir, blocks.file_sink(8, iq_out))
    tb.run()


def rx(iq_in, ts_out, rate=SR, sps=1.1, scale=2600.0):
    tb = gr.top_block()
    src = blocks.file_source(2, iq_in, False)
    s2c = blocks.interleaved_short_to_complex(False, False, scale)   # RMS ≈ 4 (AGC-Referenz von atsc_rx)
    rxb = dtv.atsc_rx(rate, sps)
    tb.connect(src, s2c, rxb, blocks.file_sink(1, ts_out))
    tb.run()


if __name__ == "__main__":
    m = sys.argv[1]
    if m == "tx": tx(sys.argv[2], sys.argv[3])
    elif m == "ref": ref(sys.argv[2], sys.argv[3], sys.argv[4])
    elif m == "rx": rx(sys.argv[2], sys.argv[3], *(float(x) for x in sys.argv[4:7]))
    else: print(__doc__); sys.exit(2)
