#!/bin/bash
# Prüfsummen der dtmb_mod-Ausgabe für eine feste Fallliste (Regression / ARM-x86-Vergleich): $1 = Binary
B=${1:-/opt/dablab/mod/dtmb/dtmb_mod}
for a in "--carriers 3780 --const 64qam --rate 0.6 --pn 945 --ti 720" "--carriers 3780 --const 4qamnr --rate 0.8 --pn 595 --ti 240" \
         "--carriers 3780 --const 32qam --rate 0.8 --pn 420 --ti 720 --pn-const" "--carriers 1 --const 16qam --rate 0.8 --pn 420 --ti 720" \
         "--carriers 3780 --const 16qam --rate 0.4 --pn 420 --ti 240 --backoff 20"; do
  echo "$($B -i prbs:3000 -o - --sps 1 --quiet $a 2>/dev/null | md5sum | cut -c1-32)  $a"
done
