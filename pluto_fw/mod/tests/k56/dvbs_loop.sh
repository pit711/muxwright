#!/bin/bash
cd /opt/dablab/mod/testwork
for r in 1/2 3/4 7/8; do
  /opt/dablab/mod/src/dvbs_mod -i test_in.ts --sr 22000000 --rate $r --sps 2 --packets 4000 -o s.iq --quiet
  echo "== rate $r: $(python3 /opt/dablab/mod/src/tests/dvbs_soft.py --iq s.iq --sps 2 -o s.s8 --max 900000)"
  /opt/dablab/mod/src/dvbs_rx --rate $r -i s.s8 -o s.ts --max 900000
  python3 /opt/dablab/mod/tscmp.py s.ts test_in.ts | tail -1
done
echo "== Benchmark x86 (1 sps, 1/2, ohne Ausgabe)"
/opt/dablab/mod/src/dvbs_mod --bench 200000 --sr 22000000 --rate 1/2 --sps 1 --quiet 2>&1 | tail -1
