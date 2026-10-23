#!/usr/bin/env python3
"""PAD-Varianten pruefen (DLS/SLS je Codec und PAD-Laenge) per check."""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); RUNNER = os.path.join(os.path.dirname(HERE), "dablab_run.py")
V = [("heaacv2_58_dls", "he-aac-v2", 58, False), ("heaacv2_58_dls_sls", "he-aac-v2", 58, True),
     ("aaclc_58_dls_sls", "aac-lc", 58, True), ("aaclc_16_dls", "aac-lc", 16, False), ("heaacv2_16_dls_sls", "he-aac-v2", 16, True),
     ("mp2_58_dls_sls", "mp2", 58, True)]
SRC = {"kind": os.environ.get("SRC", "tone"), "level_dbfs": -20}
only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
for name, codec, pb, sls in V:
    if only and name not in only:
        continue
    s = {"key": "a", "sid": "0xD201", "label": "PAD Test", "shortlabel": "PAD", "type": "dab" if codec == "mp2" else "dabplus",
         "audio": {"source": SRC, "codec": codec, "bitrate": int(os.environ.get("BR", 88)) if codec != "mp2" else 128, "samplerate": 48000, "channels": 2},
         "protection": {"profile": "EEP_A", "level": 3},
         "pad": {"enable": True, "bytes": pb, "dls": {"enable": True, "text": "PAD %s" % name}, "sls": {"enable": sls, "slides": ["media/slide1.jpg"]}}}
    cfg = {"name": name, "mode": 1, "ensemble": {"eid": "0x4FFF", "ecc": "0xE0", "label": "PADTEST", "shortlabel": "PAD"}, "services": [s],
           "rf": {"sample_rate": 4096000, "backoff_db": 12}}
    fd, p = tempfile.mkstemp(suffix=".json"); os.write(fd, json.dumps(cfg).encode()); os.close(fd)
    r = json.loads(subprocess.run([sys.executable, RUNNER, "check", "--config", p, "--seconds", "15", "--wait", "30"], stdout=subprocess.PIPE).stdout)
    sv = (r.get("services") or [{}])[0]
    print(name, "ok=%s dls=%r slides=%s err=%s" % (r.get("ok"), sv.get("dls"), [(x["w"], x["bytes"]) for x in sv.get("slides", [])], sv.get("errors")))
    os.unlink(p)
