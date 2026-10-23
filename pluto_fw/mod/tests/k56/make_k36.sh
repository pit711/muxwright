#!/bin/bash
set -e
cd /opt/dablab/mod/k56
sed "s/centre_frequency=\"754000000\"/centre_frequency=\"594000000\"/; s/version=\"1\"/version=\"3\"/" nit.xml > nit36.xml
grep -o 'centre_frequency="[0-9]*"' nit36.xml
tsp -I file raw.ts -P inject --pid 16 --bitrate 15000 --stuffing nit36.xml -P continuity -O file k36.ts 2>&1 | tail -2
ls -la k36.ts
tstables --pid 16 --max-tables 1 k36.ts 2>/dev/null | grep -E "Centre|Version"
tsanalyze k36.ts | grep -E "Service name|Bitrate: .*13," | head -3
cp k36.ts nit36.xml ${OUT:-/tmp/dablab}/
