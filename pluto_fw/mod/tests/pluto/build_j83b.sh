#!/bin/bash
# baut dvbc_mod (Annex A/B/C) für x86 (/opt/dablab/mod/x86j83b/dvbc_mod) und ARM statisch mit libiio (/opt/dablab/mod/armj83b);
# ARM-Binary → $1 (Standard: pc/device_files/j83b_mod_iio; der Server lädt es für Annex B/C als /tmp/dablab_bin/dvbc_mod)
# Die Bibliothek wird nur aus den für dvbc_mod nötigen Quellen gebaut (unabhängig von isdbt/atsc-Baustellen).
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
SRC=$ROOT/pluto_fw/mod; OUT=${1:-$ROOT/pc/device_files/j83b_mod_iio}
LIBS="lib/fec.c lib/dvbc.c lib/rrc.c lib/ts_input.c lib/output.c lib/util.c lib/j83b.c"
mkdir -p /opt/dablab/mod && cd /opt/dablab/mod && rm -rf x86j83b && cp -r $SRC x86j83b && cd x86j83b && make clean >/dev/null
make -j4 LIBSRC="$LIBS" dvbc_mod 2>&1 | grep -E "warning|error|undefined" || true
cd /opt/dablab/mod && rm -rf armj83b && cp -r $SRC armj83b && cd armj83b && make clean >/dev/null
make -j4 LIBSRC="$LIBS" CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include" \
     LDLIBS="-L/opt/dablab/mod/armsys/lib -liio -lm -lpthread" dvbc_mod 2>&1 | grep -E "warning|error|undefined" || true
cp dvbc_mod $OUT; ls -la $OUT | awk '{print $5, $9}'; file $OUT
