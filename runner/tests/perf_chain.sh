#!/bin/bash
# Kette in WSL/Mac selbst lesen (ohne Windows-Pipe): Zeit fuer N s Signal
cd "$(dirname "$0")/.."
CFG=${1:-examples/drei_dienste.json}; SEC=${2:-20}
B=$((SEC*16384000))
T0=$(date +%s.%N)
python3 dablab_run.py live --config "$CFG" 2>/tmp/perf_chain.err | head -c $B > /dev/null
T1=$(date +%s.%N)
python3 -c "el=$T1-$T0; print('{\"stage\":\"chain_local\",\"config\":\"$CFG\",\"signal_s\":$SEC,\"wall_s\":%.2f,\"realtime_factor\":%.2f}'%(el,$SEC/el))"
