#!/bin/bash
cd /opt/dablab/mod/sat
python3 /opt/dablab/mod/src/tests/dvbs_soft.py --iq ${OUT:-/tmp/dablab}/rx_s.iq --sps 1 --rx --up 8 -o rx.s8 --max 1500000 --skip 200000
/opt/dablab/mod/src/dvbs_rx --rate 1/2 -i rx.s8 -o rx.ts --max 1500000
python3 - <<'PY'
import numpy as np
rx=np.fromfile("rx.ts",np.uint8).reshape(-1,188); ref=np.fromfile("sat.ts",np.uint8).reshape(-1,188)
m={p.tobytes() for p in ref}
miss=sum(1 for p in rx if p.tobytes() not in m); print("TS-Pakete", len(rx), "nicht im gesendeten TS:", miss)
PY
tsanalyze rx.ts 2>/dev/null | grep -E "Service name" | head -1
