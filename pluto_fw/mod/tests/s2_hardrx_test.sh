#!/bin/bash
# dvbs2_mod → Datei → dvbs2_hardrx: TS muss bitgleich zurückkommen (sps 2), dazu nachgebildeter Funkweg (sps 1, CFO, Phase, Rauschen)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"   # Repo-Wurzel (dablab/)
M=/opt/dablab/mod/x86s2; T=/tmp/s2hr; mkdir -p $T; H=$ROOT/pluto_fw/mod/tests/dvbs2_hardrx.py
python3 - <<'P'
import numpy as np
rng=np.random.default_rng(7); n=6000
d=rng.integers(0,256,(n,188),dtype=np.uint8); d[:,0]=0x47; d[:,1]&=0x1f; d[:,3]=0x10|(np.arange(n)%16)
d.tofile("/tmp/s2hr/in.ts")
P
fail=0
for c in "qpsk 3/4 1" "qpsk 1/4 0" "qpsk 9/10 1" "8psk 3/5 1" "8psk 5/6 0" "8psk 9/10 1"; do
  set -- $c
  $M/dvbs2_mod -i $T/in.ts -o $T/tx.iq --mod $1 --rate $2 --pilots $3 --sps 2 --sr 1000000 --quiet 2>/dev/null
  R=$(python3 $H --iq $T/tx.iq --sps 2 --mod $1 --rate $2 --pilots $3 -o $T/rx.ts)
  CMP=$(python3 -c "
a=open('$T/in.ts','rb').read(); b=open('$T/rx.ts','rb').read()
i=a.find(b[:188*4]); print('identisch' if i>=0 and a[i:i+len(b)]==b and len(b)>188*100 else 'ABWEICHUNG', len(b)//188)")
  echo "$1 $2 p$3 sps2: $CMP $(echo $R | python3 -c 'import sys,json;r=json.loads(sys.stdin.read());print("ok=%s mer=%s crcerr=%s pls=%s"%(r["ok"],r["mer_db"],r["up_crc_errors"],r["pls_ok"]))')"
  case "$CMP" in identisch*) ;; *) fail=1;; esac
  # Funkweg: Matched Filter, 1 Sample/Symbol mit Versatz, CFO 2e-5 Zyklen/Symbol, Phase 1 rad, SNR 25 dB
  python3 - $T/tx.iq $T/rx1.iq $1 <<'P'
import sys, numpy as np
sys.path.insert(0, "$ROOT/pluto_fw/mod/tests"); import dvbs2_hardrx as H
r=np.fromfile(sys.argv[1],np.int16).astype(float); x=r[0::2]+1j*r[1::2]
y=np.convolve(x,H.rrc(0.35,2,64),mode="full")[32:]; y=y[1::2]
n=np.arange(len(y)); y=y*np.exp(1j*(1.0+2*np.pi*2e-5*n)); p=np.mean(np.abs(y)**2)
snr=25 if sys.argv[3]=="qpsk" else 28
y=y+np.sqrt(p/10**(snr/10)/2)*(np.random.default_rng(1).standard_normal(len(y))+1j*np.random.default_rng(2).standard_normal(len(y)))
o=np.empty(2*len(y),np.int16); s=8000/np.sqrt(p); o[0::2]=np.round(y.real*s); o[1::2]=np.round(y.imag*s); o.tofile(sys.argv[2])
P
  R=$(python3 $H --iq $T/rx1.iq --sps 1 --rx --mod $1 --rate $2 --pilots $3 -o $T/rx1.ts)
  echo "$1 $2 p$3 funk: $(echo $R | python3 -c 'import sys,json;r=json.loads(sys.stdin.read());print("ok=%s mer=%s frames=%s pakete=%s crcerr=%s cfo=%.2e"%(r["ok"],r["mer_db"],r["frames"],r["ts_packets"],r["up_crc_errors"],r["cfo_cycles_per_symbol"]))')"
  echo "$R" | grep -q '"ok": true' || fail=1
done
[ $fail = 0 ] && echo "HARDRX OK" || echo "HARDRX FEHLER"
