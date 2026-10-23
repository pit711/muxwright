#!/usr/bin/env python3
"""Leser fuer den Durchsatztest unter Windows: `wsl ... live | python win_reader.py --seconds 30 [--paced]`.
Ohne --paced: so schnell wie moeglich lesen (zeigt die Maximalleistung der Kette).
Mit --paced: 16,384 MB/s (4,096 MS/s int16 I/Q), meldet Fehlbestand (Kette zu langsam)."""
import argparse, json, os, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--seconds", type=float, default=30)
ap.add_argument("--rate", type=int, default=4096000)
ap.add_argument("--paced", action="store_true")
a = ap.parse_args()
fd = sys.stdin.fileno()
if os.name == "nt":
    import msvcrt
    msvcrt.setmode(fd, os.O_BINARY)
bps = a.rate * 4
first = os.read(fd, 4)
t0 = time.perf_counter()
got, per = 0, {}
behind_max = 0
while True:
    el = time.perf_counter() - t0
    if el >= a.seconds:
        break
    if a.paced:
        want = int(bps * el) - got
        if want <= 0:
            time.sleep(0.001)
            continue
        behind_max = max(behind_max, want)
        n = min(want, 1 << 20)
    else:
        n = 1 << 20
    d = os.read(fd, n)
    if not d:
        break
    got += len(d)
    per[int(el)] = per.get(int(el), 0) + len(d)
el = time.perf_counter() - t0
secs = sorted(per)[1:-1]
r = {"mode": "paced" if a.paced else "unbegrenzt", "seconds": round(el, 2), "bytes": got,
     "rate_avg_sps": round(got / 4 / el), "rate_avg_MBps": round(got / el / 1e6, 2),
     "rate_min_sps": min(per[s] / 4 for s in secs) if secs else None,
     "rate_max_sps": max(per[s] / 4 for s in secs) if secs else None,
     "target_sps": a.rate, "realtime_factor": round(got / 4 / el / a.rate, 3)}
if a.paced:
    r["max_backlog_ms"] = round(behind_max / bps * 1000, 1)
print(json.dumps(r))
