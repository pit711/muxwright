#!/usr/bin/env python3
"""Echtzeit-Leser fuer den DVB-Runner (stdout = TS): liest mit exakt der Zielrate, misst Rueckstand/Stau, schreibt
wahlweise mit. Aufruf: dablab_run.py live ... | dvb_reader.py --rate <b/s> --seconds N [--out f.ts]"""
import argparse
import json
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--rate", type=float, required=True)
ap.add_argument("--seconds", type=float, default=30)
ap.add_argument("--out")
ap.add_argument("--unpaced", action="store_true", help="so schnell wie moeglich lesen (Durchsatzmessung)")
a = ap.parse_args()
fd = sys.stdin.fileno()
out = open(a.out, "wb") if a.out else None
t0 = time.time()
total = 0
stalls = 0
max_lag = 0.0
chunk = 188 * 64
while True:
    now = time.time()
    if now - t0 >= a.seconds:
        break
    if not a.unpaced:
        due = t0 + total * 8.0 / a.rate
        if due > now:
            time.sleep(min(0.05, due - now))
            continue
        max_lag = max(max_lag, now - due)
    b = os.read(fd, chunk)
    if not b:
        break
    total += len(b)
    if out:
        out.write(b)
el = time.time() - t0
if out:
    out.close()
print(json.dumps({"bytes": total, "seconds": round(el, 2), "rate_bps": round(total * 8 / el), "target_bps": a.rate,
                  "max_lag_s": round(max_lag, 3), "ratio_realtime": round(total * 8 / el / a.rate, 3)}))
