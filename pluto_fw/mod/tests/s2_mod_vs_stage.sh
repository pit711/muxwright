#!/bin/bash
# dvbs2_mod (Datei-Ausgabe, sps 1) muss je MODCOD dieselben Samples liefern wie dvbs2_stage --stage frame (gr-dtv-geprüft)
cd /opt/dablab/mod/x86s2; T=/tmp/s2mv; mkdir -p $T; fail=0
head -c $((188*4000)) /dev/urandom | python3 -c "import sys;d=bytearray(sys.stdin.buffer.read());d[::188]=b'\x47'*(len(d)//188);sys.stdout.buffer.write(d)" > $T/in.ts
for c in "qpsk 1/2 0 1" "qpsk 3/4 1 1" "8psk 5/6 1 1" "8psk 9/10 0 0" "16apsk 3/4 1 1" "32apsk 8/9 1 0"; do
  set -- $c; M=$1; R=$2; P=$3; S=0
  A=$(./dvbs2_mod -i $T/in.ts -o $T/mod.iq --mod $M --rate $R --pilots $P --frames 3 2>&1 | head -1 | python3 -c "import sys,json;print(json.loads(sys.stdin.readline())['A'])")
  MI=$(python3 -c "print({'qpsk':0,'8psk':1,'16apsk':2,'32apsk':3}['$M'])")
  RI=$(python3 -c "print(['1/4','1/3','2/5','1/2','3/5','2/3','3/4','4/5','5/6','8/9','9/10'].index('$R'))")
  ./dvbs2_stage --mod $MI --rate $RI --pilots $P --stage frame --frames 3 --A $A < $T/in.ts > $T/st.iq
  if cmp -s $T/mod.iq $T/st.iq; then echo "$M $R pilots=$P A=$A: identisch ($(stat -c%s $T/mod.iq) B)"; else echo "$M $R pilots=$P: ABWEICHUNG"; fail=1; fi
done
[ $fail = 0 ] && echo "MOD=STAGE OK" || echo "MOD=STAGE FEHLER"
