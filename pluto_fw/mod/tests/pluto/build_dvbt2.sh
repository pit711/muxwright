#!/bin/bash
# baut dvbt2_mod für ARM statisch mit libiio (Kopie unter /opt/dablab/mod/armdvbt2, nur die benötigten Quellen);
# ARM-Binary → $1 (Standard: pc/device_files/dvbt2_mod_iio). x86: tests/dvbt2_build.sh bzw. make dvbt2_mod.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
SRC=$ROOT/pluto_fw/mod; OUT=${1:-$ROOT/pc/device_files/dvbt2_mod_iio}
D=/opt/dablab/mod/armdvbt2; rm -rf $D; mkdir -p $D/lib
cp $SRC/dvbt2_mod.c $D/; cp $SRC/lib/{dvbt2.c,dvbt2.h,dvbt2_fft.c,dvbt2_fft.h,dvbt2_tables.h,dvbs2_tables.h,dvbmod.h,fft16.c,ts_input.c,output.c,util.c} $D/lib/
cd $D
arm-linux-gnueabihf-gcc -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -std=gnu11 -Wall -Wextra -Wno-unused-parameter \
    -Wno-unused-function -Wno-unused-const-variable -D_GNU_SOURCE -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include -static -o dvbt2_mod \
    dvbt2_mod.c lib/dvbt2.c lib/dvbt2_fft.c lib/fft16.c lib/ts_input.c lib/output.c lib/util.c -L/opt/dablab/mod/armsys/lib -liio -lm -lpthread 2>&1 \
    | grep -E "warning|error|undefined" | grep -v "getaddrinfo\|dlopen\|gethostbyname" || true
cp dvbt2_mod $OUT; ls -la $OUT | awk '{print $5, $9}'; file $OUT
