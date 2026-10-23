#!/bin/bash
# Alle MODCODs (mit/ohne Pilots) von s2_symbols.py; Aufruf in der Build-Kopie (dvbs2_stage eine Ebene höher)
cd "$(dirname "$0")"
fail=0
for mr in qpsk:1/4 qpsk:1/3 qpsk:2/5 qpsk:1/2 qpsk:3/5 qpsk:2/3 qpsk:3/4 qpsk:4/5 qpsk:5/6 qpsk:8/9 qpsk:9/10 \
          8psk:3/5 8psk:2/3 8psk:3/4 8psk:5/6 8psk:8/9 8psk:9/10; do
  for p in "" --pilots; do
    timeout 300 python3 s2_symbols.py ${mr%%:*} ${mr#*:} $p 2>&1 | tail -1 | tee /tmp/s2l
    grep -q OK /tmp/s2l || fail=1
  done
done
[ $fail = 0 ] && echo "SYMBOLS ALL OK" || echo "SYMBOLS MISMATCH"
