"""Measure DAC underruns at the transmitter caused by /api/monitor captures.

Runs against a running server (default http://127.0.0.1:8792). TX must be running, or pass
--start to start it (muted or at the given attenuation, never below 60 dB unless --atten says so
explicitly and the operator has cleared it).

    python test_monitor_underruns.py --base http://127.0.0.1:8792 --seconds 25 --repeat 2
    python test_monitor_underruns.py --start --atten 60 --seconds 25

Pass criterion: 0 underruns during every capture (plus a quiet reference window of the same length
without capture, to show the baseline). Exit code 0 = pass, 1 = underruns, 2 = setup problem.
"""
import argparse
import json
import sys
import time
import urllib.request


def call(base, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def tx(base):
    return call(base, "/api/state")["tx"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8792")
    ap.add_argument("--seconds", type=float, default=25)
    ap.add_argument("--rx-gain", type=float, default=50)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--start", action="store_true", help="TX starten, falls nicht aktiv")
    ap.add_argument("--atten", type=float, default=89.75, help="Dämpfung beim Start (Standard stumm)")
    ap.add_argument("--stop", action="store_true", help="TX am Ende stoppen (nur wenn hier gestartet)")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    st = call(base, "/api/state")
    if not st["pluto"]["connected"]:
        print("Pluto nicht verbunden")
        return 2
    started = False
    if st["tx"]["state"] != "running":
        if not a.start:
            print(f"TX läuft nicht (state={st['tx']['state']}); --start zum Starten")
            return 2
        call(base, "/api/rf", {"atten_db": max(a.atten, 0), "enabled": a.atten < 89.75})
        r = call(base, "/api/tx/start", {})
        print("tx/start:", r)
        started = True
        for _ in range(120):
            time.sleep(1)
            t = tx(base)
            if t["state"] == "running":
                break
            if t["state"] == "error":
                print("TX-Fehler:", t["error"])
                return 2
        time.sleep(5)
    results = []
    for i in range(a.repeat):
        t0 = tx(base)
        time.sleep(a.seconds)                       # quiet reference window
        t1 = tx(base)
        ref = t1["underruns"] - t0["underruns"]
        t2 = time.time()
        rep = call(base, "/api/monitor", {"seconds": a.seconds, "rx_gain_db": a.rx_gain})
        dt = time.time() - t2
        t3 = tx(base)
        during = t3["underruns"] - t1["underruns"]
        spec = rep.get("spectrum", {})
        row = {"run": i + 1, "ref_underruns": ref, "monitor_underruns": during,
               "monitor_reported": rep.get("tx_underruns_during"), "rx_path": rep.get("rx_path"),
               "capture_s": rep.get("capture_s"), "wall_s": round(dt, 1), "rate_ms": t3.get("rate_ms"),
               "src_late": t3.get("src_late"), "ctl_retries": t3.get("ctl_retries"),
               "in_band_db": spec.get("in_band_db"), "adjacent_db": spec.get("adjacent_db"),
               "ok": rep.get("ok"), "error": rep.get("error")}
        print(json.dumps(row))
        results.append(row)
    if started and a.stop:
        print("tx/stop:", call(base, "/api/tx/stop", {}).get("atten_db"))
    bad = [r for r in results if (r["monitor_underruns"] or 0) > 0 or not r["ok"]]
    total = sum(r["monitor_underruns"] or 0 for r in results)
    print(f"\nErgebnis: {len(results)} Mitschnitte à {a.seconds} s, Underruns währenddessen gesamt {total}, "
          f"Referenzfenster {sum(r['ref_underruns'] for r in results)}  ->  {'BESTANDEN' if not bad else 'NICHT BESTANDEN'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
