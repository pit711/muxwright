#!/bin/bash
# baut dtmb_mod für ARM statisch mit libiio (Kopie unter /opt/dablab/mod/armdtmb, nur die benötigten Quellen);
# ARM-Binary → $1 (Standard: pc/device_files/dtmb_mod_iio). x86: tests/dtmb_build.sh bzw. make dtmb_mod.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
SRC=$ROOT/pluto_fw/mod; OUT=${1:-$ROOT/pc/device_files/dtmb_mod_iio}
D=/opt/dablab/mod/armdtmb; rm -rf $D; mkdir -p $D/lib
cp $SRC/dtmb_mod.c $D/; cp $SRC/lib/{dtmb.c,dtmb.h,dtmb_ldpc_tables.h,dvbmod.h,ts_input.c,output.c,util.c} $D/lib/
cd $D
arm-linux-gnueabihf-gcc -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -std=gnu11 -Wall -Wextra -Wno-unused-parameter \
    -Wno-unused-function -D_GNU_SOURCE -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include -static -o dtmb_mod \
    dtmb_mod.c lib/dtmb.c lib/ts_input.c lib/output.c lib/util.c -L/opt/dablab/mod/armsys/lib -liio -lm -lpthread 2>&1 \
    | grep -E "warning|error|undefined" | grep -v "getaddrinfo\|dlopen\|gethostbyname" || true
cp dtmb_mod $OUT; ls -la $OUT | awk '{print $5, $9}'; file $OUT
