"""DAB-Streaming-Nachweis: tx_6c_max.py 60 (60 dB Dämpfung) starten, 60 s Zustand beobachten, dann TX stoppen."""
import json, subprocess, sys, time, urllib.request
B = "http://127.0.0.1:8792"


def api(p, d=None, m=None):
    r = urllib.request.Request(B + p, method=m or ("POST" if d is not None else "GET"),
                               data=json.dumps(d).encode() if d is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read() or b"null")


root = sys.argv[1] if len(sys.argv) > 1 else "."
print(subprocess.run([sys.executable, "test/tx_6c_max.py", "60"], cwd=root, capture_output=True, text=True, timeout=300).stdout.strip())
t0 = time.time(); rows = []
while time.time() - t0 < 60:
    st = api("/api/state"); tx = st["tx"]; p = st["pluto"]
    rows.append((round(time.time() - t0, 1), tx["state"], tx.get("underruns"), tx.get("samples_sent"), tx.get("rate_ms"), tx.get("buffer_ms"), p.get("fw"), p.get("atten_db"), p.get("temp_c")))
    time.sleep(5)
for r in rows: print("t=%5.1fs state=%s underruns=%s samples=%s rate_ms=%s buffer_ms=%s fw=%s atten=%s temp=%s" % r)
first, last = rows[0], rows[-1]
dt = last[0] - first[0]; ds = (last[3] or 0) - (first[3] or 0)
print(f"ERGEBNIS: {dt:.0f} s beobachtet, Underruns Anfang={first[2]} Ende={last[2]}, mittlere Rate {ds/dt/1e6 if dt else 0:.4f} MS/s")
print("stop:", api("/api/tx/stop", {}))
time.sleep(2); p = api("/api/state")["pluto"]; print("nach stop: atten", p.get("atten_db"), "muted", p.get("muted"))
