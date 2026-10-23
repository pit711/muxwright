#!/usr/bin/env python3
"""Vergleicht die fertigen PLFRAME-Symbole des eigenen dvbs2_mod mit gr-dtv (interleaver→modulator→physical),
also Bit-Interleaver, QPSK/8PSK-Mapping, PLHEADER, Pilots und PL-Scrambling. Ein Prozess je Lauf.
  python3 s2_symbols.py qpsk 3/4 [--pilots] [--short]"""
import sys, subprocess, os, argparse
import numpy as np
from gnuradio import gr, blocks, dtv

RATE = {"1/4": dtv.C1_4,"1/3": dtv.C1_3,"2/5": dtv.C2_5,"1/2": dtv.C1_2,"3/5": dtv.C3_5,"2/3": dtv.C2_3,
        "3/4": dtv.C3_4,"4/5": dtv.C4_5,"5/6": dtv.C5_6,"8/9": dtv.C8_9,"9/10": dtv.C9_10}
RIDX = {"1/4":0,"1/3":1,"2/5":2,"1/2":3,"3/5":4,"2/3":5,"3/4":6,"4/5":7,"5/6":8,"8/9":9,"9/10":10}
MODG = {"qpsk": dtv.MOD_QPSK, "8psk": dtv.MOD_8PSK}; MODI = {"qpsk":0,"8psk":1}
KBCH = [16008,21408,25728,32208,38688,43040,48408,51648,53840,57472,58192]

ap=argparse.ArgumentParser(); ap.add_argument("mod"); ap.add_argument("rate")
ap.add_argument("--pilots",action="store_true"); a=ap.parse_args()
ridx=RIDX[a.rate]; rate=RATE[a.rate]; modg=MODG[a.mod]
dfl_bytes=(KBCH[ridx]-80)//8
nfrm=4
ts=np.random.default_rng(55+ridx).integers(0,256,(dfl_bytes//188+2)*nfrm*188,dtype=np.uint8); ts[::188]=0x47
pil = dtv.PILOTS_ON if a.pilots else dtv.PILOTS_OFF
ch=[blocks.vector_source_b(list(ts),False),
    dtv.dvb_bbheader_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate,dtv.RO_0_35,dtv.INPUTMODE_NORMAL,dtv.INBAND_OFF,168,4000000),
    dtv.dvb_bbscrambler_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate),
    dtv.dvb_bch_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate),
    dtv.dvb_ldpc_bb(dtv.STANDARD_DVBS2,dtv.FECFRAME_NORMAL,rate,modg),
    dtv.dvbs2_interleaver_bb(dtv.FECFRAME_NORMAL,rate,modg),
    dtv.dvbs2_modulator_bc(dtv.FECFRAME_NORMAL,rate,modg,dtv.INTERPOLATION_OFF),
    dtv.dvbs2_physical_cc(dtv.FECFRAME_NORMAL,rate,modg,pil,0)]
import math
# Ausgabe nach dem Physical-Block auf ~2 PLFRAMEs begrenzen
hd=blocks.head(gr.sizeof_gr_complex, 200000)
tb=gr.top_block(); sk=blocks.vector_sink_c(); tb.connect(*ch,hd,sk); tb.run()
g=np.array(sk.data(),dtype=np.complex64)
# gr-dtv 3.10: dvbs2_physical_cc liefert 2 Samples/Symbol (Nullen dazwischen, fürs RRC-Filter) → Nullen entfernen
if len(g)>1 and np.max(np.abs(g[1::2]))<1e-6: g=g[0::2]
# eigener Encoder: dvbs2_stage --stage frame gibt int16 I/Q eines PLFRAME (A=8000)
A=8000
raw=subprocess.run([os.path.join(os.path.dirname(__file__),"..","dvbs2_stage"),"--mod",str(MODI[a.mod]),
                    "--rate",str(ridx),"--stage","frame","--pilots",str(1 if a.pilots else 0),"--A",str(A)],
                   input=ts.tobytes(),capture_output=True).stdout
m=np.frombuffer(raw,dtype=np.int16).astype(np.float64); mc=(m[0::2]+1j*m[1::2])/A
Lp=len(mc)
# gr: suche das Frame im Strom, das mit dem PLHEADER unseres Frames übereinstimmt (0/1 Frame-Versatz)
best=None
for off in range(0, max(1,len(g)-Lp+1), Lp if Lp>0 else 1):
    seg=g[off:off+Lp]
    if len(seg)<Lp: break
    s0=np.sqrt(np.mean(np.abs(seg)**2)/ (np.mean(np.abs(mc)**2)+1e-20))
    e=np.mean(np.abs(seg-mc*s0)**2)/(np.mean(np.abs(seg)**2)+1e-20)
    if best is None or e<best[0]: best=(e,off)
off=best[1] if best else 0
g=g[off:off+Lp]
n=min(len(g),len(mc))
if n==0: print("leer"); sys.exit(1)
# gr normiert auf Einheitsenergie; skaliere mc auf dieselbe Leistung
sc=np.sqrt(np.mean(np.abs(g[:n])**2)/np.mean(np.abs(mc[:n])**2))
err=np.abs(g[:n]-mc[:n]*sc)
snr=10*np.log10(np.mean(np.abs(g[:n])**2)/(np.mean(err**2)+1e-20))
if os.environ.get("S2DIAG"):
    bad=np.nonzero(err>0.05)[0]
    print("abweichend:",len(bad),"von",n,"erste",bad[:12],"letzte",bad[-6:])
    for i in bad[:6]: print(i, np.round(g[i],3), np.round(mc[i]*sc,3), "Winkel gr/mine", round(np.angle(g[i],True)), round(np.angle(mc[i],True)))
print(f"{a.mod} {a.rate} pilots={a.pilots}: gr={len(g)} mine={len(mc)} SNR={snr:.1f} dB {'OK' if snr>50 and len(g)==len(mc) else 'FAIL'}")
sys.exit(0 if (snr>50 and len(g)==len(mc)) else 1)
