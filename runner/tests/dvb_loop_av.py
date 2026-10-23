#!/usr/bin/env python3
"""Bild-/Tonaussetzer in einem TS finden: dekodiert das Video (framemd5) und zaehlt Standbilder (gleiche
aufeinanderfolgende Bilder), Schwarzbilder (blackdetect) und Tonluecken (silencedetect). Fuer Quellen, deren Bilder
sich alle unterscheiden (ffmpeg testsrc mit Zaehler), ist jedes doppelte Bild ein Aussetzer.
  python3 dvb_loop_av.py f.ts"""
import json
import re
import subprocess
import sys

ts = sys.argv[1]
r = subprocess.run(["ffmpeg", "-v", "error", "-i", ts, "-map", "0:v:0", "-f", "framemd5", "-"], stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL)
h = [l.split(",")[-1].strip() for l in r.stdout.decode().splitlines() if l and not l.startswith("#")]
dups, runs, cur = 0, [], 1
for a, b in zip(h, h[1:]):
    if a == b:
        dups += 1
        cur += 1
    else:
        if cur > 1:
            runs.append(cur)
        cur = 1
r2 = subprocess.run(["ffmpeg", "-v", "info", "-i", ts, "-map", "0:v:0", "-vf", "blackdetect=d=0.03:pix_th=0.05", "-map", "0:a:0",
                     "-af", "silencedetect=noise=-50dB:d=0.03", "-f", "null", "-"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
err = r2.stderr.decode("utf-8", "replace")
black = [float(x) for x in re.findall(r"black_duration:([\d.]+)", err)]
sil = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", err)]
print(json.dumps({"frames": len(h), "duplicate_frames": dups, "freeze_runs": runs[:20], "black_s": black, "silence_s": sil}))
