#!/bin/bash
# baut ARM-Varianten (Fusion an/aus, Prefetch) des Selbsttests/Modulators für den Pluto-Mikrobenchmark
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
SRC=$ROOT/pluto_fw/mod
OUT=${OUT:-/tmp/dablab}
rm -f $OUT/st_* $OUT/mod_*
cd /opt/dablab/mod && rm -rf armv && mkdir armv && cp -r $SRC/. armv/ && cd armv
for v in "unfused:-DFFT_FUSED=0 -DFFT_PLD=64 -O3" "fused64:-DFFT_FUSED=1 -DFFT_PLD=64 -O3" "fused128:-DFFT_FUSED=1 -DFFT_PLD=128 -O3" "fused32:-DFFT_FUSED=1 -DFFT_PLD=32 -O3" "fusedO2:-DFFT_FUSED=1 -DFFT_PLD=64 -O2"; do
  name=${v%%:*}; flags=${v#*:}
  make clean >/dev/null
  nice -n 10 make -j4 CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="$flags -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard" dvbmod_selftest dvbt_mod 2>&1 | grep -E " error" || true
  cp dvbmod_selftest $OUT/st_$name; cp dvbt_mod $OUT/mod_$name
  echo built $name
done
# x86-Referenz-CRC
cd /opt/dablab/mod && cp -r $SRC/. src/ && cd src && make -j4 2>&1 | grep -E " error" || true
./dvbmod_selftest | grep -E "crc|SELFTEST"
