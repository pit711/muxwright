#!/usr/bin/env python3
"""Uebernahme-Pruefung: runner/pal/gen.loop_iq gegen das Original pluto_fw/mod/tools/pal_bg_gen.py mit demselben Bild.
Erwartet: identische int16-Ausgabe (bis auf Rundung)."""
import importlib.util
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from pal import gen  # noqa: E402

orig = os.path.abspath(os.path.join(HERE, "..", "..", "pluto_fw", "mod", "tools", "pal_bg_gen.py"))
spec = importlib.util.spec_from_file_location("pal_bg_gen", orig)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
out = os.path.join(tempfile.gettempdir(), "pal_orig.iq")
subprocess.run([sys.executable, orig, "-o", out, "--text", "DABLAB Test"], check=True, stdout=subprocess.DEVNULL)
rgb = mod.make_image("DABLAB Test")
p = {"system": "BG", "fs": 8e6, "offset_hz": 2.25e6, "level": 0.9,
     "sound": {"kind": "tone", "freq_hz": 1000, "deviation_hz": 50e3, "level_db": -13.0, "preemphasis_us": 50}}
s, rep = gen.loop_iq(p, rgb)
a = gen.to_int16(s).astype(np.int32)
b = np.fromfile(out, np.int16).astype(np.int32)
d = np.abs(a - b)
print({"samples": len(a) // 2, "identical_pct": round(100.0 * np.mean(d == 0), 3), "max_abs_diff_lsb": int(d.max()),
       "orig": orig})
