#!/bin/bash
# baut atsc_mod für x86 (im Quellbaum) und ARM statisch mit libiio (Kopie unter /opt/dablab/mod/armatsc);
# ARM-Binary → $1 (Standard: pc/device_files/atsc_mod_iio)
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
SRC=$ROOT/pluto_fw/mod; OUT=${1:-$ROOT/pc/device_files/atsc_mod_iio}
cd $SRC && make -j2 atsc_mod 2>&1 | grep -E "warning|error|undefined" || true
mkdir -p /opt/dablab/mod && cd /opt/dablab/mod && rm -rf armatsc && cp -r $SRC armatsc && cd armatsc && make clean >/dev/null
make -j2 CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include" \
     LDLIBS="-L/opt/dablab/mod/armsys/lib -liio -lm -lpthread" atsc_mod 2>&1 | grep -E "warning|error|undefined" || true
cp atsc_mod $OUT; ls -la $OUT | awk '{print $5, $9}'; file $OUT
