#!/usr/bin/env python3
"""Vergleicht EINE (mod,rate,stage)-Kombination gegen gr-dtv (eigener Prozess, umgeht einen gr-dtv-Absturz bei
mehreren Flowgraphs je Prozess). Exitcode 0 = bitgleich.
  python3 s2_one.py qpsk 4/5 ldpc"""
import sys, subprocess, os
import numpy as np
from gnuradio import gr, blocks, dtv

RATE = {"1/4": dtv.C1_4, "1/3": dtv.C1_3, "2/5": dtv.C2_5, "1/2": dtv.C1_2, "3/5": dtv.C3_5, "2/3": dtv.C2_3,
        "3/4": dtv.C3_4, "4/5": dtv.C4_5, "5/6": dtv.C5_6, "8/9": dtv.C8_9, "9/10": dtv.C9_10}
RIDX = {"1/4":0,"1/3":1,"2/5":2,"1/2":3,"3/5":4,"2/3":5,"3/4":6,"4/5":7,"5/6":8,"8/9":9,"9/10":10}
MODI = {"qpsk":0,"8psk":1}
KBCH = [16008,21408,25728,32208,38688,43040,48408,51648,53840,57472,58192]
NBCH = [16200,21600,25920,32400,38880,43200,48600,51840,54000,57600,58320]
STAGE = os.path.join(os.path.dirname(__file__), "..", "dvbs2_stage")

modn, rn, stage = sys.argv[1], sys.argv[2], sys.argv[3]
ridx = RIDX[rn]; rate = RATE[rn]
dfl_bytes = (KBCH[ridx]-80)//8
ts = np.random.default_rng(100+ridx).integers(0,256,(dfl_bytes//188+4)*188,dtype=np.uint8); ts[::188]=0x47
ch=[blocks.vector_source_b(list(ts),False),
    dtv.dvb_bbheader_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate,dtv.RO_0_35,dtv.INPUTMODE_NORMAL,dtv.INBAND_OFF,168,4000000),
    dtv.dvb_bbscrambler_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate),
    dtv.dvb_bch_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate)]
if stage=="ldpc": ch.append(dtv.dvb_ldpc_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate,dtv.MOD_OTHER))
tb=gr.top_block(); sk=blocks.vector_sink_b(); tb.connect(*ch,sk); tb.run()
n = NBCH[ridx] if stage=="bch" else 64800
g=np.array(sk.data(),np.uint8)[:n]
m=np.frombuffer(subprocess.run([STAGE,"--mod",str(MODI[modn]),"--rate",str(ridx),"--stage",stage],
                               input=ts.tobytes(),capture_output=True).stdout,np.uint8)[:n]
ok = len(m)>=n and np.array_equal(g,m)
print(f"{modn} {rn} {stage}: diff={int(np.sum(g!=m[:n])) if len(m)>=n else 'len'} {'OK' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
