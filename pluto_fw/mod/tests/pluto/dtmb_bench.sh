#!/bin/bash
# dtmb_mod auf dem Pluto: Selbsttest, Bitgleichheit ARM vs. x86 (Prüfsummen tests/dtmb_md5.sh) und Echtzeitfaktor je
# Konfiguration. NUR CPU: Ausgabe -o /dev/null, NIE iio: (der Pluto sendet nicht). Aufruf aus WSL:
#   tests/pluto/dtmb_bench.sh [SEKUNDEN]      (vorher tests/pluto/build_dtmb.sh und tests/dtmb_build.sh)
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; ROOT="$(cd $M/../../.. && pwd)"; P=$M/pluto/pl.sh
SECS=${1:-5}
bash $M/dtmb_md5.sh /opt/dablab/mod/dtmb/dtmb_mod > /tmp/dtmb_md5_x86.txt
bash $P push $ROOT/pc/device_files/dtmb_mod_iio $M/dtmb_md5.sh >/dev/null
bash $P run "cd /tmp; ./dtmb_mod_iio --selftest; sh dtmb_md5.sh ./dtmb_mod_iio" > /tmp/dtmb_arm.txt
cat /tmp/dtmb_arm.txt
tail -5 /tmp/dtmb_arm.txt | diff -q - /tmp/dtmb_md5_x86.txt >/dev/null && echo "ARM = x86 (5 Prüfsummen)" || echo "ARM != x86!"
CASES=(
 "--carriers 3780 --const 64qam --rate 0.6 --pn 945 --ti 720"
 "--carriers 1 --const 16qam --rate 0.8 --pn 420 --ti 720"
 "--carriers 3780 --const 64qam --rate 0.8 --pn 420 --ti 720"
 "--carriers 3780 --const 32qam --rate 0.8 --pn 420 --ti 720"
 "--carriers 3780 --const 16qam --rate 0.8 --pn 420 --ti 240"
 "--carriers 3780 --const 4qamnr --rate 0.8 --pn 595 --ti 720"
 "--carriers 3780 --const 4qam --rate 0.4 --pn 945 --ti 240"
 "--carriers 1 --const 64qam --rate 0.8 --pn 420 --ti 720"
)
for a in "${CASES[@]}"; do
  echo "== $a"
  bash $P run "cd /tmp; ./dtmb_mod_iio -i prbs -o /dev/null --sps 1 --seconds $SECS --profile --quiet $a 2>&1 | tail -1"
done
