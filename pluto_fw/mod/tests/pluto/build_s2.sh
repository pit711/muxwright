#!/bin/bash
# baut dvbs2_mod/dvbs2_stage für x86 (/opt/dablab/mod/x86s2) und ARM mit libiio (/opt/dablab/mod/arms2), Ausgabe der ARM-Binaries nach $1
set -e
SRC=/mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/mod; OUT=${1:-/opt/dablab/mod/out}; mkdir -p $OUT
cd /opt/dablab/mod && rm -rf x86s2 && cp -r $SRC x86s2 && cd x86s2 && make -j12 dvbs2_mod dvbs2_stage dvbs_mod 2>&1 | grep -E "warning|error|undefined" || true
cd /opt/dablab/mod && rm -rf arms2 && cp -r $SRC arms2 && cd arms2 && make clean >/dev/null
make -j12 CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include" \
     LDLIBS="-L/opt/dablab/mod/armsys/lib -liio -lm -lpthread" dvbs2_mod dvbs2_stage dvbs_mod 2>&1 | grep -E "warning|error|undefined" || true
cp dvbs2_mod dvbs2_stage dvbs_mod $OUT/; ls -la $OUT/dvbs2_mod $OUT/dvbs2_stage | awk '{print $5, $9}'
