#!/bin/bash
# Durchsatz der Einzelstufen (WSL/Mac): dabmod allein, Kette nach /dev/null
cd "$(dirname "$0")/.."
W=$(mktemp -d /tmp/perf.XXXX)
python3 - "$W" <<'PY'
import sys, json, os
sys.path.insert(0, ".")
import dablab_run as d
w = sys.argv[1]
mux = os.path.join(w, "c.mux")
open(mux, "w").write("general {\n dabmode 1\n nbframes 1250\n}\nremotecontrol {\n telnetport 0\n}\n"
  "ensemble {\n id 0x4FFF\n ecc 0xE0\n label \"P\"\n shortlabel \"P\"\n}\nservices {\n s1 {\n id 0xD0FF\n label \"P\"\n }\n}\n"
  "subchannels {\n p1 {\n type data\n bitrate 576\n id 1\n protection 3\n inputproto prbs\n inputuri \"prbs://\"\n }\n}\n"
  "components {\n c1 {\n service s1\n subchannel p1\n }\n}\noutputs {\n o \"file://%s/c.eti?type=raw\"\n}\n" % w)
import subprocess; subprocess.run([d.tool("odr-dabmux"), mux], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for rate in (2048000, 4096000):
    for fir in (1, 0):
        ini = os.path.join(w, "m.ini")
        d.write_mod_ini({"mode": 1, "tii": {"enable": False, "comb": 1, "pattern": 1}, "rf": {"cfr": False}}, ini, rate, 1.0, out="/dev/null")
        if not fir:
            t = open(ini).read().replace("[firfilter]\nenabled=1", "[firfilter]\nenabled=0"); open(ini, "w").write(t)
        import time; t0 = time.time()
        subprocess.run([d.tool("odr-dabmod"), ini], stdin=open(os.path.join(w, "c.eti"), "rb"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        el = time.time() - t0
        print(json.dumps({"stage": "odr-dabmod", "rate": rate, "fir": fir, "signal_s": 30.0, "wall_s": round(el, 2), "realtime_factor": round(30.0 / el, 2)}))
PY
rm -rf "$W"
