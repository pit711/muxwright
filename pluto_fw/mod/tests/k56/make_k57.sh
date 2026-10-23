#!/bin/bash
set -e
cd /opt/dablab/mod/k56
sed 's/centre_frequency="754000000"/centre_frequency="762000000"/; s/version="1"/version="2"/' nit.xml > nit57.xml
grep -o 'centre_frequency="[0-9]*"' nit57.xml
tsp -I file raw.ts -P inject --pid 16 --bitrate 15000 --stuffing nit57.xml -P continuity -O file k57.ts 2>&1 | tail -2
ls -la k57.ts
tstables --pid 16 --max-tables 1 k57.ts 2>/dev/null | grep -E "Centre|Version"
tsanalyze k57.ts | grep -E "Service name|Bitrate: .*13," | head -3
cp k57.ts nit57.xml ${OUT:-/tmp/dablab}/
