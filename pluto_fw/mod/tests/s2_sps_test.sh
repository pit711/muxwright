#!/bin/bash
# DATV-Symbolraten: dvbs2_mod --sps 2/4/8 -> dvbs2_hardrx (TS bitgleich zurück?)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"   # Repo-Wurzel (dablab/)
M=/opt/dablab/mod/x86s2; T=/tmp/s2sps; mkdir -p $T; H=$ROOT/pluto_fw/mod/tests/dvbs2_hardrx.py
head -c $((188*3000)) /dev/urandom | python3 -c "import sys;d=bytearray(sys.stdin.buffer.read());d[::188]=b'\x47'*(len(d)//188);sys.stdout.buffer.write(d)" > $T/in.ts
fail=0
for sps in 2 4 8; do for c in "qpsk 2/3 0 0.35" "qpsk 3/4 1 0.25" "8psk 5/6 0 0.20"; do set -- $c
  $M/dvbs2_mod -i $T/in.ts -o $T/tx.iq --mod $1 --rate $2 --pilots $3 --rolloff $4 --sps $sps --sr 333000 --quiet 2>/dev/null
  R=$(python3 $H --iq $T/tx.iq --sps $sps --mod $1 --rate $2 --pilots $3 --rolloff $4 --taps $((32*sps)) -o $T/rx.ts)
  OK=$(python3 -c "a=open('$T/in.ts','rb').read();b=open('$T/rx.ts','rb').read();i=a.find(b[:752]);print('identisch' if i>=0 and a[i:i+len(b)]==b and len(b)>18800 else 'ABWEICHUNG')")
  echo "sps $sps $1 $2 p$3 ro$4: $OK $(echo $R | python3 -c 'import sys,json;r=json.loads(sys.stdin.read());print("mer",r["mer_db"],"pakete",r["ts_packets"])')"
  [ "$OK" = identisch ] || fail=1
done; done
[ $fail = 0 ] && echo "SPS OK" || echo "SPS FEHLER"
