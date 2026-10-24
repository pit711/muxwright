"""Gerätekette ohne Senden: Server (eigene Instanz, eigener runtime-Ordner, Mindestdämpfung 60 dB) → Runner (WSL) →
UDP → Modulator auf dem Pluto, für die neuen Standards von Beta 0.3 und DVB-T2. Der Sender bleibt AUS (rf.enabled false, −89,75 dB):
geprüft werden nur Kettenstart, Ankunft der TS-Pakete am Modulator (UDP-Zähler, aufgefüllte Nullpakete) und DAC-Unterläufe.

  python test/e2e_device_world.py [--runtime DIR] [--port 8811] [--seconds 25] [nur STANDARD …]

Abbruch, sobald die Dämpfung am Pluto nicht −89,75 dB ist (Sicherheitsregel: nie ungefragt senden).
"""
import argparse, json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AGENT = "http://192.168.2.1:8080"


def http(url, data=None, timeout=30, method=None):
    req = urllib.request.Request(url, method=method or ("POST" if data is not None else "GET"),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", "Origin": url.split("/api")[0]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def prog(codec, audio, **kw):
    return dict({"key": "prg1", "name": "LAB 1", "provider": "LAB", "sid": "0x0001", "lcn": 1, "type": "tv",
                 "video": {"source": {"kind": "testpattern", "pattern": "smptebars"}, "codec": codec, "resolution": "sd",
                           "bitrate_kbps": 3000, "encoder": "cpu", "hdr": "sdr"},
                 "audio": {"codec": audio, "bitrate_kbps": 192, "source": {"kind": "tone"}}, "eit": {"title": "T", "description": "D"}}, **kw)


CASES = {
    "atsc": ({"mode": "atsc", "atsc": {}}, {"channel": "A14", "freq_hz": 473000000}, [prog("mpeg2", "ac3")]),
    "isdbt": ({"mode": "isdbt", "isdbt": {"region": "br", "mode": 3, "guard": "1/8", "layers": 2,
                                          "layer_a": {"constellation": "QPSK", "code_rate": "2/3", "ti": 4},
                                          "layer_b": {"constellation": "64-QAM", "code_rate": "3/4", "ti": 2}}},
              {"channel": "A14", "freq_hz": 473142857}, [prog("h264", "aac"), prog("h264", "aac", key="prg2", sid="0x0002", lcn=2, oneseg=True)]),
    "dtmb": ({"mode": "dtmb", "dtmb": {"carriers": 3780, "constellation": "64-QAM", "code_rate": "0.6", "header": 945, "interleaver": 720}},
             {"channel": "DS13", "freq_hz": 474000000}, [prog("h264", "mp2")]),
    "j83b": ({"mode": "dvbc", "dvbc": {"annex": "b", "constellation": "256-QAM", "symbol_rate": 5360537, "sps": 1}},
             {"channel": "C80", "freq_hz": 561000000}, [prog("mpeg2", "ac3")]),
    "dvbc": ({"mode": "dvbc", "dvbc": {"annex": "a", "constellation": "256-QAM", "symbol_rate": 6900000, "sps": 1}},
             {"channel": "S21", "freq_hz": 306000000}, [prog("mpeg2", "mp2")]),
    "dvbt2": ({"mode": "dvbt2", "dvbt2": {"preset": "de", "bandwidth_mhz": 8, "fft": "32k", "ext": True, "constellation": "64-QAM",
                                          "code_rate": "2/3", "guard": "19/256", "pp": 4, "rotation": True}},
              {"channel": "K21", "freq_hz": 474000000}, [prog("hevc", "aac")]),
    "dvbt2_leicht": ({"mode": "dvbt2", "dvbt2": {"preset": "leicht", "bandwidth_mhz": 8, "fft": "8k", "ext": True, "constellation": "64-QAM",
                                                 "code_rate": "3/5", "guard": "1/8", "pp": 2, "rotation": True}},
                     {"channel": "K21", "freq_hz": 474000000}, [prog("hevc", "aac")]),
    # Bestandsstandards mit Gerätemodulator (gleicher UDP-Weg)
    "dvbt": ({"mode": "dvbt", "dvbt": {"bandwidth_mhz": 8, "fft": "8k", "constellation": "64-QAM", "code_rate": "2/3", "guard": "1/32", "cell_id": 0}},
             {"channel": "K21", "freq_hz": 474000000}, [prog("mpeg2", "mp2")]),
    "dvbs2": ({"mode": "dvbs", "dvbs": {"use": "sat", "system": "dvbs2", "transponder_mhz": 11038, "polarization": "V", "lof_mhz": 9750,
                                        "tone_22khz": False, "symbol_rate": 22000000, "fec": "3/4", "modulation": "QPSK", "rolloff": 0.35, "pilots": True}},
              {"channel": "sat", "freq_hz": 1288000000}, [prog("h264", "aac")]),
}


def last_stats():
    raw = http(AGENT + "/api/mod/log").get("log") or ""
    lines = raw.splitlines() if isinstance(raw, str) else raw
    return next((json.loads(x) for x in reversed(lines) if isinstance(x, str) and x.startswith("{") and '"rt_factor"' in x), {})


def stuffed_of(st):
    """Füllpakete wegen fehlender Eingangspakete (isdbt_mod ersetzt Nullpakete absichtlich je Schicht → 'q_*'/fill)."""
    return st.get("stuffed", 0) if "stuffed" in st else 0


def gain():
    return float(http(AGENT + "/api/status")["dac"]["gain_db"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=os.path.join(tempfile.gettempdir(), "mx_e2e_rt"))
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--seconds", type=float, default=25)
    ap.add_argument("only", nargs="*")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    if gain() > -89.7:
        sys.exit("Pluto nicht stumm – Abbruch")
    import socket
    with socket.socket() as so:                     # Port muss frei sein – sonst antwortet ein fremder (Mock-)Server
        if so.connect_ex(("127.0.0.1", a.port)) == 0:
            sys.exit(f"Port {a.port} ist belegt – anderen Port wählen")
    os.makedirs(a.runtime, exist_ok=True)
    log = open(Path(a.runtime) / "server.log", "w", encoding="utf-8")
    srv = subprocess.Popen([sys.executable, str(ROOT / "pc" / "server.py"), "--port", str(a.port), "--runtime", a.runtime, "--min-atten", "60"],
                           stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    results, ok = {}, True
    try:
        for _ in range(120):
            try:
                http(base + "/api/state")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        st = http(base + "/api/state")
        cfg = st["config"]
        for std, (sec, rf, progs) in CASES.items():
            if a.only and std not in a.only:
                continue
            c = json.loads(json.dumps(cfg))
            c.update({k: v for k, v in sec.items()})
            c["rf"].update(rf)
            c["rf"].update({"enabled": False, "atten_db": 89.75, "path": "auto"})
            c["dvb"] = {"programs": progs, "si": {"network_id": "0x04D2", "network_name": "LAB", "onid": "0x04D2", "tsid": "0x04D2",
                                                 "lcn": True, "eit": True, "tdt": True}}
            r = http(base + "/api/config", c, timeout=120, method="PUT")
            if not r.get("ok", True) or r.get("errors"):
                results[std] = {"ok": False, "errors": r.get("errors")}
                ok = False
                continue
            http(AGENT + "/api/underflow/clear", {})
            u0 = http(AGENT + "/api/status")["udp"]
            r = http(base + "/api/tx/start", {}, timeout=180)
            t0, state, worst, mid = time.time(), None, -89.75, None
            while time.time() - t0 < a.seconds + 60:
                s = http(base + "/api/state")
                state = (s.get("tx") or {}).get("state")
                g = gain()
                worst = max(worst, g)
                if g > -89.7:
                    http(base + "/api/tx/stop", {})
                    sys.exit(f"{std}: Dämpfung {g} dB – Abbruch")
                if state == "running" and time.time() - t0 > a.seconds - 10 and mid is None:
                    mid = last_stats()                  # Stand 10 s vor Ende: Füllpakete im eingeschwungenen Zustand zählen
                if state == "running" and time.time() - t0 > a.seconds:
                    break
                if state == "error":
                    break
                time.sleep(1)
            dev = http(AGENT + "/api/status")
            mlog = http(AGENT + "/api/mod/log")
            http(base + "/api/tx/stop", {})
            raw = mlog.get("lines") or mlog.get("log") or []
            lines = raw.splitlines() if isinstance(raw, str) else [x for x in raw if isinstance(x, str)]
            last = next((json.loads(x) for x in reversed(lines) if x.startswith("{") and '"rt_factor"' in x), {})
            res = {"state": state, "mod_running": dev["mod"]["running"], "underflows": dev["dac"]["underflows"],
                   "udp_in": dev["udp"]["in"] - u0["in"], "rcvbuf_err": dev["udp"]["rcvbuf_err"] - u0["rcvbuf_err"],
                   "rt_factor": last.get("rt_factor"), "ts_in": last.get("ts_in"), "stuffed": last.get("stuffed", last.get("stuffed_b")),
                   "dropped": last.get("dropped"), "max_gain_db": worst, "error": (s.get("tx") or {}).get("error")}
            m = mid or {}
            d_in = (last.get("ts_in") or 0) - (m.get("ts_in") or 0)
            d_st = stuffed_of(last) - stuffed_of(m)
            res["steady_ts_in"], res["steady_stuffed"] = d_in, d_st
            res["ok"] = (state == "running" and res["mod_running"] and res["underflows"] == 0 and res["udp_in"] > 1000
                         and d_in > 0 and d_st <= 0.01 * d_in)
            ok &= res["ok"]
            results[std] = res
            print(std, json.dumps(res, ensure_ascii=False), flush=True)
            time.sleep(3)
    finally:
        try:
            http(base + "/api/tx/stop", {})
        except Exception:  # noqa: BLE001
            pass
        srv.terminate()
        print("Pluto-Dämpfung am Ende:", gain())
    print("OK" if ok else "FEHLER", json.dumps(results, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
