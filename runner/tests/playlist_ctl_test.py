#!/usr/bin/env python3
"""live mit Playlist: Echtzeit-Leser, ctl playlist next/jump/prev, now_playing aus stderr/Status mitschreiben."""
import json, os, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); RUNNER = os.path.join(os.path.dirname(HERE), "dablab_run.py")
cfg = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE), "examples", "playlist_test.json")
p = subprocess.Popen([sys.executable, RUNNER, "live", "--config", cfg], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
lines, sess = [], {}
def rd():
    for l in iter(p.stderr.readline, b""):
        s = l.decode("utf-8", "replace").strip(); lines.append((time.time(), s))
        if not sess and s.startswith('{"session"'): sess.update(json.loads(s))
threading.Thread(target=rd, daemon=True).start()
fd = p.stdout.fileno(); os.read(fd, 4); t0 = time.time(); got = 0; bps = 4096000 * 4
plan = [(3, {"action": "next"}), (6, {"action": "jump", "index": 0}), (9, {"action": "prev"})]
res = []
while time.time() - t0 < 13:
    el = time.time() - t0
    if plan and el >= plan[0][0]:
        _, a = plan.pop(0); a = dict(a, service="srv1")
        r = subprocess.run([sys.executable, RUNNER, "ctl", "--session", sess["session"], json.dumps({"playlist": a})], stdout=subprocess.PIPE)
        res.append({"t": round(el, 1), "req": a, "out": json.loads(r.stdout or b"{}")})
    want = int(bps * el) - got
    if want <= 0:
        time.sleep(0.002); continue
    d = os.read(fd, min(want, 262144)); got += len(d)
p.stdout.close(); p.wait(timeout=15)
np_ = [(round(t - t0, 1), json.loads(s)["now_playing"]) for t, s in lines if '"msg": "now_playing"' in s]
st = [json.loads(s).get("now_playing") for t, s in lines if '"msg": "Status"' in s]
print(json.dumps({"ctl": res, "now_playing_events": [(t, n["index"], n["title"], n["artist"]) for t, n in np_],
                  "status_now_playing": st, "rate_sps": round(got / 4 / (time.time() - t0)), "rc": p.returncode}, ensure_ascii=False, indent=1))
