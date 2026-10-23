#!/bin/bash
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
OUT=${OUT:-/tmp/dablab}
bash $OUT/fixcrlf.sh >/dev/null
sed -i 's/\r$//' $ROOT/pluto_fw/mod/tests/k56/make_sat_ts.sh
bash $ROOT/pluto_fw/mod/tests/k56/make_sat_ts.sh
/opt/dablab/mod/src/rrc_fir_gen --alpha 0.35 --sr 22000000 --taps 64 -o /opt/dablab/mod/sat/rrc22.fir
cp /opt/dablab/mod/sat/sat.ts /opt/dablab/mod/sat/rrc22.fir $OUT/
cd /opt/dablab/mod && rm -rf armiio && cp -r $ROOT/pluto_fw/mod armiio && cd armiio && make clean >/dev/null
nice -n 10 make -j4 CC=arm-linux-gnueabihf-gcc STATIC=1 CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -DHAVE_LIBIIO -I/opt/dablab/mod/armsys/include" LDLIBS="-L/opt/dablab/mod/armsys/lib -liio -lm -lpthread" dvbs_mod 2>&1 | grep -E " error|undefined" || true
cp dvbs_mod $OUT/dvbs_mod_iio; ls -la $OUT/dvbs_mod_iio | awk '{print $5}'
