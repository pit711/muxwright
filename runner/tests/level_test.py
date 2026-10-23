#!/usr/bin/env python3
"""Pegel/Crest/Clips der s16-Ausgabe fuer Backoff- und CFR-Varianten (vollstaendige Auswertung mit numpy)."""
import json, os, subprocess, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(os.path.dirname(HERE), "dablab_run.py")
base = json.load(open(os.path.join(os.path.dirname(HERE), "examples", "minimal_ton.json")))
variants = [("backoff 12, CFR aus", {"backoff_db": 12}), ("backoff 9, CFR aus", {"backoff_db": 9}),
            ("backoff 15, CFR aus", {"backoff_db": 15}),
            ("backoff 12, CFR an (Standard)", {"backoff_db": 12, "cfr": True}),
            ("backoff 12, CFR papr 8 dB, error_clip 1.0", {"backoff_db": 12, "cfr": True, "cfr_papr_db": 8, "cfr_error_clip": 1.0}),
            ("backoff 9, CFR papr 6 dB, error_clip 1.0", {"backoff_db": 9, "cfr": True, "cfr_papr_db": 6, "cfr_error_clip": 1.0})]
res = []
for name, rf in variants:
    c = json.loads(json.dumps(base)); c["rf"].update(rf)
    fd, cp = tempfile.mkstemp(suffix=".json"); os.write(fd, json.dumps(c).encode()); os.close(fd)
    out = "/tmp/lvl.s16"
    subprocess.run([sys.executable, RUNNER, "render", "--config", cp, "--seconds", "5", "--rate", "4096000",
                    "--format", "s16", "--out", out], stdout=subprocess.DEVNULL)
    x = np.fromfile(out, dtype="<i2").astype(np.float32)
    p = np.hypot(x[0::2], x[1::2]); rms = np.sqrt(np.mean(p ** 2))
    db = lambda v: round(float(20 * np.log10(v / 32767)), 2)
    res.append({"variant": name, "rms_dbfs": db(rms), "peak_dbfs": db(p.max()), "crest_db": round(db(p.max()) - db(rms), 2),
                "p99_99_over_rms_db": round(float(20 * np.log10(np.percentile(p, 99.99) / rms)), 2),
                "clipped_values": int(np.count_nonzero((x >= 32767) | (x <= -32768))), "samples": int(len(p))})
    os.unlink(cp); os.unlink(out)
print(json.dumps(res, indent=1, ensure_ascii=False))
