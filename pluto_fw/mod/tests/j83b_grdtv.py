#!/usr/bin/env python3
"""j83b_grdtv.py – gr-dtv (GNU Radio 3.10, „catv“-Blöcke von R. Economos) als unabhängiger Referenzsender für
dvbc_mod --annex b (ITU-T J.83 Annex B). Es gibt in gr-dtv keinen J.83B-Empfänger, nur den Sender.

  tx TS QAM CW OUTDIR      Kette wie examples/dtv/catv_tx_{64,256}qam.grc bis zum Trellis-Ausgang; schreibt je Stufe
                           eine Datei: 1_framing.u8 (Bytes), 2_rs.u8 (7-Bit-Symbole), 3_il.u8, 4_rand.u8, 5_fsync.u8,
                           6_trellis.u8 (QAM-Labels 0..M-1)
  map QAM LABELS OUT       Labels → dtv.dvbs2_modulator_bc (MOD_64QAM/MOD_256QAM, wie im gr-dtv-Beispiel) → complex64
GPL-3.0-or-later (nutzt gr-dtv zur Laufzeit)."""
import sys, os
from gnuradio import gr, blocks, dtv

CW = {0: (128, 1), 1: (128, 1), 2: (128, 2), 3: (64, 2), 4: (128, 3), 5: (32, 4), 6: (128, 4), 7: (16, 8), 8: (128, 5),
      9: (8, 16), 10: (128, 6), 12: (128, 7), 14: (128, 8)}


def tx(ts, qam, cw, outdir):
    os.makedirs(outdir, exist_ok=True)
    c = dtv.CATV_MOD_64QAM if qam == 64 else dtv.CATV_MOD_256QAM
    I, J = CW[cw]
    tb = gr.top_block()
    src = blocks.file_source(1, ts, False)
    fr = dtv.catv_transport_framing_enc_bb()
    p2u = blocks.packed_to_unpacked_bb(7, gr.GR_MSB_FIRST)
    rs = dtv.catv_reed_solomon_enc_bb()
    s2v = blocks.stream_to_vector(1, I)
    il = dtv.dvbt_convolutional_interleaver(1, I, J)
    rnd = dtv.catv_randomizer_bb(c)
    fs = dtv.catv_frame_sync_enc_bb(c, cw)
    tr = dtv.catv_trellis_enc_bb(c)
    tb.connect(src, fr, p2u, rs, s2v, il, rnd, fs, tr)
    for blk, name in ((fr, "1_framing"), (rs, "2_rs"), (il, "3_il"), (rnd, "4_rand"), (fs, "5_fsync"), (tr, "6_trellis")):
        tb.connect(blk, blocks.file_sink(1, os.path.join(outdir, name + ".u8")))
    tb.run()


def cmap(qam, labels, out):
    m = dtv.MOD_64QAM if qam == 64 else dtv.MOD_256QAM
    tb = gr.top_block()
    src = blocks.file_source(1, labels, False)
    mod = dtv.dvbs2_modulator_bc(dtv.FECFRAME_NORMAL, dtv.C1_4, m, dtv.INTERPOLATION_OFF)
    tb.connect(src, mod, blocks.file_sink(8, out))
    tb.run()


if __name__ == "__main__":
    a = sys.argv
    if a[1] == "tx":
        tx(a[2], int(a[3]), int(a[4]), a[5])
    elif a[1] == "map":
        cmap(int(a[2]), a[3], a[4])
    else:
        print(__doc__); sys.exit(2)
