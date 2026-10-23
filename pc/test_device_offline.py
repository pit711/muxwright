"""Hardware-free tests for DVB (validation, rf.path), the device path via dablab-agent (mocked), YouTube jobs and
video upload. Never touches the Pluto, the real agent or port 8792:
  --uri ip:127.0.0.1 (does not connect), --agent <mock>, --device-host 127.0.0.1, --backend local (fake runner),
  own --runtime folder, free port.

    python test_device_offline.py [--verbose]
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tests_fake"))
from mock_agent import MockAgent  # noqa: E402

FAILS = []


def check(name, cond, info=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(info)[:400]}" if info and not cond else ""))
    if not cond:
        FAILS.append(name)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, agent, extra=()):
        self.rt = tempfile.mkdtemp(prefix="dablab_dev_")
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.p = subprocess.Popen(
            [sys.executable, "-u", os.path.join(HERE, "server.py"), "--port", str(self.port), "--uri", "ip:127.0.0.1",
             "--backend", "local", "--runner", os.path.join(HERE, "tests_fake", "fake_runner.py"),
             "--agent", agent.url, "--device-host", "127.0.0.1", "--device-udp-port", str(agent.udp_port),
             "--runtime", self.rt, "--no-priority", *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        for _ in range(60):
            time.sleep(0.25)
            try:
                st = self.get("/api/state")[1]
                if st.get("server", {}).get("pid") != self.p.pid:
                    raise SystemExit("fremder Server auf dem Port")
                break
            except (urllib.error.URLError, ConnectionError):
                continue

    def req(self, path, body=None, method=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                   headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(r, timeout=30) as h:
                return h.status, json.loads(h.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def get(self, path):
        return self.req(path)

    def wait(self, pred, timeout=15, step=0.25):
        t = time.time()
        while time.time() - t < timeout:
            st = self.get("/api/state")[1]
            if pred(st):
                return st
            time.sleep(step)
        return self.get("/api/state")[1]

    def close(self):
        try:
            self.req("/api/tx/stop", {})
        except Exception:
            pass
        self.p.terminate()
        try:
            out = self.p.communicate(timeout=10)[0].decode("utf-8", "replace")
        except Exception:
            self.p.kill()
            out = ""
        if "--verbose" in sys.argv:
            print(out)
        shutil.rmtree(self.rt, ignore_errors=True)


SVC = [{"key": "tv1", "service_id": "0x0001", "name": "LAB TV 1", "provider": "DABLAB", "lcn": 1, "type": "tv",
        "video": {"codec": "mpeg2", "size": "sd", "bitrate_kbps": "auto"}, "audio": {"codec": "mp2", "bitrate_kbps": 192},
        "source": {"kind": "testpattern", "pattern": "smptebars"}}]
DVBT8 = {"name": "DVB-T K21", "mode": "dvbt",
         "dvbt": {"mode": "8k", "constellation": "64-QAM", "code_rate": "2/3", "guard": "1/32", "bandwidth_mhz": 8},
         "ts": {"network_name": "DABLAB"}, "services": SVC, "rf": {"channel": "K21", "atten_db": 60, "enabled": False}}


def device_tests():
    ag = MockAgent().start()
    sv = Server(ag)
    try:
        st = sv.get("/api/state")[1]
        check("server up, agent reachable via mock", st["rf"]["device"]["reachable"] and not st["pluto"]["connected"], st["rf"])
        s, r = sv.req("/api/config", DVBT8, "PUT")
        check("DVB-T 8 MHz accepted", s == 200 and r["standard"] == "dvbt", r)
        st = sv.get("/api/state")[1]
        check("DVB-T 8 MHz -> rf.path device", st["rf"]["path"] == "device" and st["rf"]["path_label"] == "pluto"
              and st["rf"]["sample_rate"] == 9142857, st["rf"])
        check("dvb budget in state", abs(st["dvb"]["rate_bps"] - 24128342.25) < 1 and st["dvb"]["overbooked"] is False, st.get("dvb"))
        writes_before = [p for p in ag.paths("POST")]
        check("no agent writes while idle", writes_before == [], writes_before)
        s, r = sv.req("/api/tx/start", {})
        check("tx/start device -> 200", s == 200 and r["path"] == "device", r)
        st = sv.wait(lambda s: s["tx"]["state"] == "running", 15)
        check("device TX running", st["tx"]["state"] == "running" and st["tx"]["path"] == "device", st["tx"])
        starts = ag.bodies("/api/mod/start")
        a = starts[0]["args"] if starts else []
        check("mod/start dvbt_mod with udp/affinity/rt/window",
              starts and starts[0]["cmd"] == "dvbt_mod" and a[:2] == ["-i", f"udp://0.0.0.0:{ag.udp_port}"]
              and "--affinity" in a and "--rt" in a and a[a.index("--window") + 1] == "32"
              and a[a.index("--const") + 1] == "64qam" and a[a.index("--bw") + 1] == "8"
              and a[a.index("--lo") + 1] == "474000000" and a[-2:] == ["--atten", "89.75"], starts)
        rfb = ag.bodies("/api/rf")
        check("agent rf before start: LO, muted, bandwidth", rfb and rfb[0].get("lo_hz") == 474000000
              and rfb[0].get("atten_db") == 89.75 and rfb[0].get("rf_bw_hz") == 8000000, rfb)
        check("underflow counter cleared after start", "/api/underflow/clear" in ag.paths("POST"))
        time.sleep(1.5)
        check("TS datagrams reach the modulator port", ag.udp_bytes > 50000, ag.udp_bytes)
        lc = json.load(open(os.path.join(sv.rt, "fake_runner_last_config.json"), encoding="utf-8"))
        check("runner config: output udp to device host/port", lc["output"] == {"kind": "udp", "host": "127.0.0.1",
              "port": ag.udp_port, "burst": 7, "rtp": False}, lc.get("output"))
        st = sv.wait(lambda s: s["tx"].get("ts_rate_bps"), 5)
        check("tx.ts_rate_bps from runner status", (st["tx"].get("ts_rate_bps") or 0) > 0, st["tx"])
        check("muted while rf.enabled false", ag.state["gain_db"] == -89.75)
        s, r = sv.req("/api/rf", {"enabled": True, "atten_db": 60})
        time.sleep(0.6)
        check("rf.enabled -> agent atten 60", s == 200 and ag.state["gain_db"] == -60.0, (r, ag.state["gain_db"]))
        s, r = sv.req("/api/rf", {"atten_db": 75})
        check("atten live via agent", ag.state["gain_db"] == -75.0, ag.state["gain_db"])
        s, r = sv.req("/api/rf", {"channel": "K22"})
        check("channel live: LO via agent + restart_required (NIT)", s == 200 and ag.state["lo_hz"] == 482000000
              and r["restart_required"] is True, (r, ag.state["lo_hz"]))
        ag.state["underflows"] = 3
        st = sv.wait(lambda s: s["tx"]["underruns"] == 3, 5)
        check("agent underflows -> tx.underruns", st["tx"]["underruns"] == 3, st["tx"])
        check("device status in state", st["rf"]["device"]["ours"] and st["rf"]["device"]["cpu_pct"]["cpu1"] == 70.0, st["rf"]["device"])
        s, r = sv.req("/api/monitor", {"seconds": 1})
        check("monitor refused in device path", s == 409, r)
        s, r = sv.req("/api/tx/stop", {})
        check("tx/stop -> agent mod/stop, muted", s == 200 and not ag.state["mod"]["running"] and ag.state["gain_db"] == -89.75, r)
        n0 = ag.udp_bytes
        time.sleep(1.0)
        check("TS chain stopped (no more datagrams)", ag.udp_bytes - n0 < 20000, ag.udp_bytes - n0)
        # modulator crash -> error -> automatic restart
        ag.fail_mod_after = 2.5
        sv.req("/api/rf", {"enabled": False})
        sv.req("/api/tx/start", {})
        st = sv.wait(lambda s: s["tx"]["restarts"] >= 1, 20)
        check("modulator exit -> auto restart", st["tx"]["restarts"] >= 1 and len(ag.bodies("/api/mod/start")) >= 3, st["tx"])
        ag.fail_mod_after = None
        sv.req("/api/tx/stop", {})
        # usb path for DVB-T 6 MHz
        cfg6 = json.loads(json.dumps(DVBT8))
        cfg6["dvbt"]["bandwidth_mhz"] = 6
        s, r = sv.req("/api/config", cfg6, "PUT")
        st = sv.get("/api/state")[1]
        check("DVB-T 6 MHz -> rf.path usb", s == 200 and st["rf"]["path"] == "usb" and st["rf"]["sample_rate"] == 6857143, st["rf"])
        pc = json.load(open(os.path.join(sv.rt, "pc_config.json"), encoding="utf-8"))
        check("runner config usb: output modulator", pc["output"] == {"kind": "modulator"}, pc.get("output"))
        s, r = sv.req("/api/tx/start", {})
        check("usb path without Pluto -> 503", s == 503, r)
        cfgc = {"name": "C", "mode": "dvbc", "dvbc": {"constellation": "256-QAM", "symbol_rate": 6900000, "sps": 2},
                "services": SVC, "rf": {"channel": "S21", "path": "usb"}}
        s, r = sv.req("/api/config", cfgc, "PUT")
        check("DVB-C 6,9 MBd forced usb -> 400", s == 400 and "USB" in json.dumps(r), r)
        cfgc["rf"]["path"] = "auto"
        s, r = sv.req("/api/config", cfgc, "PUT")
        st = sv.get("/api/state")[1]
        check("DVB-C 6,9 MBd -> device, S21 306 MHz", s == 200 and st["rf"]["path"] == "device"
              and st["config"]["rf"]["freq_hz"] == 306000000, st["rf"])
        big = json.loads(json.dumps(DVBT8))
        big["dvbt"].update(constellation="16-QAM", code_rate="1/2", guard="1/4")
        big["services"] = [dict(SVC[0], key=f"tv{i}", service_id=f"0x000{i}", video={"codec": "mpeg2", "size": "sd", "bitrate_kbps": 6000})
                           for i in range(1, 4)]
        s, r = sv.req("/api/config", big, "PUT")
        check("overbooking -> warning", s == 200 and r["warnings"] and r["warnings"][0].startswith("Überbuchung"), r.get("warnings"))
        s, r = sv.get("/api/dvb/rates?standard=dvbc")
        check("dvb rates table", s == 200 and any(abs(x["bitrate_bps"] - 50870588.2) < 1 for x in r["rates"]), str(r)[:200])
        s, r = sv.get("/api/channels")
        check("channels.json from SPEC tables", r["dvbt"]["channels"]["E5"]["center_mhz"] == 177.5
              and r["dvbc"]["channels"]["S02"]["center_mhz"] == 113.0 and "K12" not in r["dvbc"]["channels"])
        # YouTube jobs
        s, r = sv.req("/api/youtube", {"url": "https://youtu.be/xyz", "quality": "720p"})
        jid = r.get("job")
        check("youtube job created", s == 200 and jid, r)
        seen = set()
        t = time.time()
        j = {}
        while time.time() - t < 15:
            j = sv.get(f"/api/youtube/{jid}")[1]
            seen.add(j.get("status"))
            if j.get("status") in ("done", "error"):
                break
            time.sleep(0.1)
        check("youtube job running -> done, speed/percent mapped", j.get("status") == "done" and "running" in seen
              and j.get("speed") == 5000000 and j.get("percent") == 100.0 and j.get("file") == "media/video/abc123.mp4"
              and j.get("title") == "Testvideo" and j.get("height") == 480, (seen, j))
        st = sv.get("/api/state")[1]
        check("state.jobs has the job", st["jobs"].get(jid, {}).get("status") == "done", list(st["jobs"]))
        s, r = sv.req("/api/youtube", {"url": "https://youtu.be/fail"})
        jid2 = r["job"]
        j = sv.wait(lambda s: s["jobs"].get(jid2, {}).get("status") in ("done", "error"), 10)["jobs"][jid2]
        check("youtube failure -> error", j["status"] == "error" and "nicht verfügbar" in (j["error"] or ""), j)
        s, r = sv.req("/api/youtube", {"url": "ftp://x"})
        check("youtube bad url -> 400", s == 400)
        # video upload
        body = (b"--VB\r\nContent-Disposition: form-data; name=\"f\"; filename=\"clip one.mp4\"\r\n\r\n" + b"\x00" * 2000 +
                b"\r\n--VB\r\nContent-Disposition: form-data; name=\"f\"; filename=\"bad.mp3\"\r\n\r\nxx\r\n--VB--\r\n")
        s, r = sv.req("/api/upload?kind=video", raw=body, ctype="multipart/form-data; boundary=VB")
        check("video upload -> media/video", s == 200 and r["file"] == "media/video/clip one.mp4"
              and os.path.isfile(os.path.join(sv.rt, "media", "video", "clip one.mp4")) and r["errors"], r)
        s, r = sv.get("/api/media")
        names = {m["name"]: m["kind"] for m in r["media"]}
        check("media list includes videos", names.get("video/clip one.mp4") == "video" and names.get("video/abc123.mp4") == "video", names)
        s, r = sv.req("/api/media/video%2Fclip%20one.mp4", method="DELETE")
        check("delete video", s == 200 and not os.path.exists(os.path.join(sv.rt, "media", "video", "clip one.mp4")), r)
    finally:
        sv.close()
        ag.stop()


def foreign_tests():
    """A modulator that this server did not start is running (e.g. live TV): server must not touch the Pluto."""
    ag = MockAgent(mod_running=True, mod_cmd="dvbt_mod -i udp://:1234 (K57)").start()
    sv = Server(ag)
    try:
        st = sv.wait(lambda s: s["rf"]["device"]["foreign"], 5)
        check("foreign modulator detected", st["rf"]["device"]["foreign"] and not st["rf"]["device"]["ours"], st["rf"]["device"])
        s, r = sv.req("/api/rf", {"atten_db": 70, "enabled": True})
        check("rf with foreign modulator: stored only", s == 200 and "fremd" in json.dumps(r) or "nicht von diesem Server" in json.dumps(r, ensure_ascii=False), r)
        s, r = sv.req("/api/tx/start", {})
        check("tx/start refused while foreign modulator runs", s == 409 and "fremder Modulator" in r.get("error", ""), r)
        s, r = sv.req("/api/tx/stop", {})
        time.sleep(1.0)
        posts = ag.paths("POST")
        check("no write call to the agent (live TV untouched)", posts == [], posts)
        check("foreign modulator still running", ag.state["mod"]["running"] and ag.state["gain_db"] == -89.75 or ag.state["mod"]["running"])
    finally:
        sv.p.terminate()
        try:
            sv.p.communicate(timeout=10)
        except Exception:
            sv.p.kill()
        shutil.rmtree(sv.rt, ignore_errors=True)
        posts = ag.paths("POST")
        check("server shutdown did not touch the foreign modulator", posts == [] and ag.state["mod"]["running"], posts)
        ag.stop()


if __name__ == "__main__":
    device_tests()
    foreign_tests()
    print(f"\n{len(FAILS)} Fehler" + (": " + ", ".join(FAILS) if FAILS else ""))
    sys.exit(1 if FAILS else 0)
