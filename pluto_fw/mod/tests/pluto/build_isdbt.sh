#!/bin/bash
# baut isdbt_mod für ARM statisch mit libiio (Kopie unter /opt/dablab/mod/armisdbt, Quellbaum bleibt unberührt);
# ARM-Binary → $1 (Standard: pc/device_files/isdbt_mod_iio). x86: make isdbt_mod im Quellbaum.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
SRC=$ROOT/pluto_fw/mod; OUT=${1:-$ROOT/pc/device_files/isdbt_mod_iio}
mkdir -p /opt/dablab/mod && cd /opt/dablab/mod && rm -rf armisdbt && cp -r $SRC armisdbt && cd armisdbt && make clean >/dev/null
make -j2 CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include" \
     LDLIBS="-L/opt/dablab/mod/armsys/lib -liio -lm -lpthread" isdbt_mod 2>&1 | grep -E "warning|error|undefined" || true
cp isdbt_mod $OUT; ls -la $OUT | awk '{print $5, $9}'; file $OUT
