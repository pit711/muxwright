#!/bin/bash
# Bitgenauigkeit dvbt2_mod gegen gr-dtv über eine Parameter-Matrix (je 2 T2-Rahmen, alle Stufen, IQ bis auf Rundung).
#   tests/dvbt2_grdtv_matrix.sh [BIN] [TS] [OUTDIR]
M="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN=${1:-/opt/dablab/mod/t2/dvbt2_mod}; TS=${2:-${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_rx/ref/orig_de.ts}; OUT=${3:-${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_cmp/matrix}
CASES=(
 "--fft 16k --ext 1 --const 256qam --rate 3/4 --guard 1/8 --pp 2 --l1-const 64qam"
 "--fft 8k --ext 0 --const 16qam --rate 1/2 --guard 1/16 --pp 4 --fec 16200 --l1-const 16qam"
 "--fft 2k --const qpsk --rate 3/5 --guard 1/4 --pp 1 --fec 16200 --l1-const qpsk"
 "--fft 4k --const 64qam --rate 4/5 --guard 1/32 --pp 7 --rotation 0"
 "--fft 1k --const 16qam --rate 5/6 --guard 1/8 --pp 3"
 "--fft 32k --ext 0 --const 256qam --rate 2/3 --guard 1/128 --pp 7"
 "--fft 32k --ext 1 --const qpsk --rate 1/2 --guard 1/8 --pp 2 --fec 16200"
 "--bw 7 --fft 8k --ext 1 --const 64qam --rate 2/3 --guard 19/128 --pp 8 --l1-const 64qam"
 "--fft 16k --ext 1 --const 64qam --rate 3/5 --guard 1/4 --pp 1"   # erwartet: IQ weicht ab (gr-dtv-Tippfehler CP 3636 statt 13636)
)
i=0
for c in "${CASES[@]}"; do
  i=$((i+1)); echo "=== Fall $i: $c"
  python3 $M/dvbt2_grdtv_compare.py $BIN $TS $OUT/c$i --frames 2 -- $c 2>&1 | grep -E "GLEICH|ABWEI|ERGEB|Rahmen . (P1|P2|Daten)|Parameter|rror" | sed 's/^/   /'
done
