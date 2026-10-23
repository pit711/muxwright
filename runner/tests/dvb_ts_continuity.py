#!/usr/bin/env python3
"""Zeitstempel-Kontinuitaet eines Transportstroms: je Elementarstrom DTS/PTS-Schritte (ffprobe -show_packets),
dazu PCR-Regression (Rate, Residuen) aus dvb/check.py. Fuer den Nachweis nahtloser Schleifen.

  python3 dvb_ts_continuity.py f.ts [--rate R] [--json out.json]
Ergebnis: je Strom Anzahl Pakete, Sollschritt (Median), Zahl/Groesse der Abweichungen, groesster Sprung, Luecken in s."""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap = argparse.ArgumentParser()
ap.add_argument("ts")
ap.add_argument("--rate", type=float)
ap.add_argument("--json")
a = ap.parse_args()

r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_packets", "-show_entries",
                    "packet=stream_index,pts,dts,duration,flags:stream=index,codec_type,codec_name,id", a.ts],
                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
j = json.loads(r.stdout or b"{}")
streams = {s["index"]: s for s in j.get("streams", [])}
per = {}
for p in j.get("packets", []):
    per.setdefault(p["stream_index"], []).append(p)
out = {"file": a.ts, "streams": []}
for idx, pk in sorted(per.items()):
    st = streams.get(idx, {})
    key = "dts" if all("dts" in p for p in pk) else "pts"
    ts = [int(p[key]) for p in pk if key in p]
    if len(ts) < 3:
        continue
    d = [b - a_ for a_, b in zip(ts, ts[1:])]
    ds = sorted(d)
    med = ds[len(ds) // 2]
    dev = [(i, x) for i, x in enumerate(d) if x != med]
    # Audio-Encoder liefern teils wechselnde Paketdauern (z. B. AAC 1024/48000 = 1920 Ticks) -> Abweichung gegen die
    # gemeldete Paketdauer pruefen
    dur = [int(p["duration"]) for p in pk if p.get("duration") not in (None, "N/A")]
    bad = [(i, x) for i, x in dev if not dur or abs(x - (dur[i] if i < len(dur) else med)) > 1]
    big = max((abs(x - med) for _, x in dev), default=0)
    out["streams"].append({"index": idx, "pid": st.get("id"), "type": st.get("codec_type"), "codec": st.get("codec_name"),
                           "key": key, "packets": len(ts), "step_ticks": med, "deviations": len(dev),
                           "real_deviations": len(bad), "max_jump_ticks": big, "max_jump_ms": round(big / 90.0, 3),
                           "span_s": round((ts[-1] - ts[0]) / 90000.0, 3), "backwards": sum(1 for x in d if x <= 0),
                           "first_bad": [(i, x) for i, x in bad[:5]]})
if a.rate:
    try:
        from dvb import check as CK
        out["pcr"] = CK.pcr_jitter(a.ts, a.rate)
    except Exception as e:
        out["pcr"] = {"error": str(e)}
print(json.dumps(out, indent=1))
if a.json:
    with open(a.json, "w") as f:
        json.dump(out, f, indent=1)
