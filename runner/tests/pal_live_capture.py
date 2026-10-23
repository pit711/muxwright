#!/usr/bin/env python3
"""Mitschnitt aus 'dablab_run.py live' (PAL, int16 I/Q) demodulieren: PNG + Kennwerte.
  python3 pal_live_capture.py --iq f.s16 --fs 7e6 --carrier -2.1e6 --skip-s 1.0 --png out.png [--system BG]"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pal import demod  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--iq", required=True)
ap.add_argument("--fs", type=float, default=7e6)
ap.add_argument("--carrier", type=float, default=-2.1e6)
ap.add_argument("--skip-s", type=float, default=1.0)
ap.add_argument("--seconds", type=float, default=0.16)
ap.add_argument("--png")
ap.add_argument("--system", default="BG")
a = ap.parse_args()
x = demod.load_iq(a.iq, "s16", skip=int(a.skip_s * a.fs), n=int(a.seconds * a.fs))
rep = demod.analyze(x, a.fs, a.carrier, a.system, png=a.png)
import numpy as np  # noqa: E402
rep["rms_dbfs"] = round(float(10 * np.log10(np.mean(np.abs(x) ** 2) + 1e-20)), 2)
rep["peak"] = round(float(np.max(np.abs(x))), 4)
print(json.dumps(rep, indent=1))
