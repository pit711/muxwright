#!/bin/bash
# dvbc_mod (Annex B/C, pc/device_files/j83b_mod_iio) auf dem Pluto: Bitgleichheit ARM vs. x86 (md5) und Durchsatz
# NUR CPU: Ausgabe -o /dev/null bzw. -o - | cat > /dev/null, NIE -o iio: (der Pluto sendet nicht). Aus WSL starten.
# X86=… (Standard /opt/dablab/mod/x86j83b/dvbc_mod aus build_j83b.sh), SECS=… Messdauer je Fall
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"; P=$ROOT/pluto_fw/mod/tests/pluto
X86=${X86:-/opt/dablab/mod/x86j83b/dvbc_mod}; SECS=${SECS:-10}
cp $ROOT/pc/device_files/j83b_mod_iio /tmp/j83b_mod_iio && bash $P/pl.sh push /tmp/j83b_mod_iio >/dev/null
CASES=("--annex b --qam 64 --sps 1" "--annex b --qam 256 --sps 1" "--annex b --qam 64 --sps 2" "--annex b --qam 256 --sps 2"
       "--annex c --qam 256 --sps 1" "--annex c --qam 64 --sps 2" "--annex a --qam 256 --sps 1"
       "--annex b --qam 256 --sps 1 --threads 1" "--annex b --qam 64 --sps 1 --threads 1")
for c in "${CASES[@]}"; do
  x=$($X86 $c -i prbs:3000 -o - --quiet 2>/dev/null | md5sum | cut -c1-32)
  a=$(bash $P/pl.sh run "/tmp/j83b_mod_iio $c -i prbs:3000 -o - --quiet 2>/dev/null | md5sum" | cut -c1-32)
  echo "md5 $c x86=$x arm=$a $([ "$x" = "$a" ] && echo gleich || echo VERSCHIEDEN)"
done
for c in "${CASES[@]}"; do
  echo -n "devnull $c: "; bash $P/pl.sh run "/tmp/j83b_mod_iio $c -i prbs -o /dev/null --seconds $SECS 2>&1 | tail -1"
done
for c in "--annex b --qam 64 --sps 1" "--annex b --qam 256 --sps 1"; do
  echo -n "pipe $c: "; bash $P/pl.sh run "/tmp/j83b_mod_iio $c -i prbs -o - --seconds $SECS 2>/tmp/j83b_err | cat >/dev/null; tail -1 /tmp/j83b_err"
done
bash $P/pl.sh run "rm -f /tmp/j83b_mod_iio /tmp/j83b_err"
