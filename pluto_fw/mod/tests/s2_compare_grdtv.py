#!/usr/bin/env python3
"""Bitgenauer Vergleich des eigenen DVB-S2-Encoders (dvbs2_stage, aus dvbs2.c) mit gr-dtv (GPL-3.0, nur als Prüfwerkzeug).
Vergleicht BBFRAME (nach Scrambling), FECFRAME nach BCH und nach LDPC für QPSK/8PSK über mehrere Coderaten.
  python3 s2_compare_grdtv.py [--mod qpsk|8psk] [--rate 3/4 ...]"""
import argparse, subprocess, sys, os
import numpy as np
from gnuradio import gr, blocks, dtv

RATE = {"1/4": dtv.C1_4, "1/3": dtv.C1_3, "2/5": dtv.C2_5, "1/2": dtv.C1_2, "3/5": dtv.C3_5, "2/3": dtv.C2_3,
        "3/4": dtv.C3_4, "4/5": dtv.C4_5, "5/6": dtv.C5_6, "8/9": dtv.C8_9, "9/10": dtv.C9_10}
RIDX = {"1/4":0,"1/3":1,"2/5":2,"1/2":3,"3/5":4,"2/3":5,"3/4":6,"4/5":7,"5/6":8,"8/9":9,"9/10":10}
MOD = {"qpsk": (dtv.MOD_QPSK, 0), "8psk": (dtv.MOD_8PSK, 1)}
STAGE = os.path.join(os.path.dirname(__file__), "..", "dvbs2_stage")

def grdtv_bits(payload, modc, rate, upto):
    """upto: 'bb' (nach Scrambler), 'bch', 'ldpc'. Liefert Bits (0/1) eines FECFRAME."""
    tb = gr.top_block()
    src = blocks.vector_source_b(list(payload), False)
    bbh = dtv.dvb_bbheader_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate, dtv.RO_0_35, dtv.INPUTMODE_NORMAL, dtv.INBAND_OFF, 168, 4000000)
    bbs = dtv.dvb_bbscrambler_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate)
    chain = [src, bbh, bbs]
    if upto in ("bch", "ldpc"):
        chain.append(dtv.dvb_bch_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate))
    if upto == "ldpc":
        chain.append(dtv.dvb_ldpc_bb(dtv.STANDARD_DVBS2, dtv.FECFRAME_NORMAL, rate, dtv.MOD_OTHER))
    snk = blocks.vector_sink_b()
    tb.connect(*chain, snk)
    tb.run()
    return np.array(snk.data(), dtype=np.uint8)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", default="qpsk,8psk")
    ap.add_argument("--rate", default="1/4,1/2,2/3,3/4,5/6,8/9,9/10")
    a = ap.parse_args()
    allok = True
    for modn in a.mod.split(","):
        modc, modi = MOD[modn]
        for rn in a.rate.split(","):
            if modn == "8psk" and rn in ("1/4","1/3","2/5","1/2"): continue
            if modn == "qpsk" and rn == "9/10": pass
            ridx = RIDX[rn]
            # Nutzlast: eine DFL (Kbch-80) – gr-dtv liest ganze TS-Pakete, wir geben genug Bytes
            kbch = {0:16008,1:21408,2:25728,3:32208,4:38688,5:43040,6:48408,7:51648,8:53840,9:57472,10:58192}[ridx]
            dfl_bytes = (kbch - 80)//8
            npkt = dfl_bytes//188 + 4
            rng = np.random.default_rng(1234 + ridx)
            ts = rng.integers(0,256,npkt*188,dtype=np.uint8); ts[::188] = 0x47
            payload_bytes = ts.tobytes()
            for stage in ("bch","ldpc"):
                gb = grdtv_bits(ts, modc, RATE[rn], stage)
                # unser Encoder: dvbs2_stage schreibt FECFRAME-Bits gepackt (MSB zuerst) nach stdout
                p = subprocess.run([STAGE, "--mod", str(modi), "--rate", str(ridx), "--stage", stage],
                                   input=payload_bytes, capture_output=True)
                mb = np.unpackbits(np.frombuffer(p.stdout, dtype=np.uint8))
                n = {"bch": {0:16200,1:21600,2:25920,3:32400,4:38880,5:43200,6:48600,7:51840,8:54000,9:57600,10:58320}[ridx],
                     "ldpc": 64800}[stage]
                g1 = gb[:n]; m1 = mb[:n]
                ok = len(m1)>=n and np.array_equal(g1, m1[:n])
                diff = -1 if not (len(m1)>=n) else int(np.sum(g1 != m1[:n]))
                print(f"{modn:5s} {rn:4s} {stage:4s}: len gr={len(gb)} mine={len(mb)} diff={diff} {'OK' if ok else 'FAIL'}")
                allok &= ok
    print("ALL OK" if allok else "MISMATCH")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
