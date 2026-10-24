#!/bin/bash
# baut dvbt2_mod (x86, WSL) ohne den restlichen Baum: $1 = Zielverzeichnis (Standard /opt/dablab/mod/t2)
set -e
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; OUT=${1:-/opt/dablab/mod/t2}
mkdir -p $OUT
gcc -O3 -march=native -ffp-contract=off -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-unused-function -Wno-unused-const-variable \
    -D_GNU_SOURCE -o $OUT/dvbt2_mod $M/dvbt2_mod.c $M/lib/dvbt2.c $M/lib/dvbt2_fft.c $M/lib/fft16.c $M/lib/ts_input.c $M/lib/output.c $M/lib/util.c -lm -lpthread
echo $OUT/dvbt2_mod
