"""Drei-Dienste-Ensemble auf 6C senden, Dämpfung wählbar (Standard 0 dB = Maximalleistung, nur im Labor!)."""
import json, sys, time, urllib.request
B = "http://127.0.0.1:8792"
ATTEN = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0


def api(p, d=None, m=None):
    r = urllib.request.Request(B + p, method=m or ("POST" if d is not None else "GET"),
                               data=json.dumps(d).encode() if d is not None else None,
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read() or b"null")


for _ in range(60):
    try:
        api("/api/state"); break
    except Exception:
        time.sleep(1)
cfg = json.load(open("runner/examples/drei_dienste.json", encoding="utf-8"))
cfg["rf"].update(channel="6C", freq_hz=185360000, atten_db=ATTEN, enabled=False)
api("/api/config", cfg, "PUT")
if api("/api/state")["tx"]["state"] != "running":
    api("/api/tx/start", {})
t = time.time()
while time.time() - t < 120 and api("/api/state")["tx"]["state"] not in ("running", "error"):
    time.sleep(1)
print("rf:", api("/api/rf", {"enabled": True, "atten_db": ATTEN})["applied"])
time.sleep(4)
st = api("/api/state"); p = st["pluto"]; tx = st["tx"]
print("PLUTO lo", p["lo_hz"], "atten", p["atten_db"], "muted", p["muted"], "TX", tx["state"], "underruns", tx.get("underruns"))
