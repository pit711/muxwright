#!/bin/bash
# atsc_test.sh – Gesamtprüfung atsc_mod (WSL, GNU Radio 3.10 gr-dtv):
#  1) Symbolstrom (bis Feldsync-Mux) gegen gr-dtv-Sender, 2) IQ gegen gr-dtv-Referenzkette (Drehung + FIR, gleiche Taps),
#  3) Loopback atsc_mod → IQ → dtv.atsc_rx → TS, paketweise mit dem Original verglichen (ffmpeg-TS 20 s + Zufalls-TS),
#  4) Spektrum-PNG und Kennwerte. Ergebnisse: tests/results/atsc_*.json, atsc_spectrum.png
set -e
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; R=$M/tests/results; W=${WORK:-/tmp/atsc}; mkdir -p $R $W
cd $M && make -j2 atsc_mod >/dev/null
cd $W
[ -f test.ts ] || ffmpeg -y -loglevel error -f lavfi -i testsrc=size=1280x720:rate=30000/1001 -f lavfi -i sine=frequency=1000:sample_rate=48000 \
   -t 20 -c:v mpeg2video -b:v 15M -maxrate 15M -bufsize 4M -c:a ac3 -b:a 384k -f mpegts -muxrate 19392658 -mpegts_service_id 1 test.ts
[ -f rand.ts ] || python3 -c "
import numpy as np; r=np.random.default_rng(1); p=r.integers(0,256,(31200,188),dtype=np.uint8)
p[:,0]=0x47; p[:,1]=0x01; p[:,2]=0x00; p[:,3]=0x10|(np.arange(31200)%16); p.tofile('rand.ts')"
q() { grep -v buffer_double || true; }
# 1) Symbole
python3 $M/tests/atsc_grdtv.py tx test.ts gr.sym 2>&1 | q
$M/atsc_mod -i test.ts -o test.iq --dump-sym my.sym --quiet 2>/dev/null
python3 - <<PY | tee $R/atsc_symbols_vs_grdtv.json
import numpy as np, json
a=np.fromfile("gr.sym",np.uint8); b=np.fromfile("my.sym",np.uint8); n=min(len(a),len(b))
print(json.dumps({"gr_symbols":int(len(a)),"atsc_mod_symbols":int(len(b)),"compared":int(n),"differing":int((a[:n]!=b[:n]).sum()),"segments":int(n//832),"fields":int(n//832//313)}))
PY
# 2)+4) IQ-Referenz und Spektrum
python3 $M/tests/atsc_iq_check.py test.iq my.sym $R/atsc_spectrum.png --json $R/atsc_iq_spectrum.json 2>&1 | q
# 3) Loopback
for ts in test rand; do
  [ $ts = rand ] && $M/atsc_mod -i rand.ts -o rand.iq --quiet 2>/dev/null
  timeout 1200 python3 $M/tests/atsc_grdtv.py rx $ts.iq rx_$ts.ts 2>&1 | q
  python3 $M/tests/atsc_ts_compare.py $ts.ts rx_$ts.ts | tee $R/atsc_loopback_$ts.json
done
