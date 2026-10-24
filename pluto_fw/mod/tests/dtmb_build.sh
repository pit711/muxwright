#!/bin/bash
# baut dtmb_mod (x86, WSL) ohne den restlichen Baum: $1 = Zielverzeichnis (Standard /opt/dablab/mod/dtmb)
set -e
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; OUT=${1:-/opt/dablab/mod/dtmb}
mkdir -p $OUT
gcc -O3 -march=native -ffp-contract=off -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-unused-function -D_GNU_SOURCE -o $OUT/dtmb_mod \
    $M/dtmb_mod.c $M/lib/dtmb.c $M/lib/ts_input.c $M/lib/output.c $M/lib/util.c -lm -lpthread
echo $OUT/dtmb_mod
