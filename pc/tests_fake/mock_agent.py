"""Mock of dablab-agent (pluto_fw/agent/API.md) for hardware-free tests. Records every call.

    m = MockAgent(); m.start(); ... m.calls; m.state; m.stop()
Also binds a UDP port to count the TS datagrams the server's chain sends (udp_port).
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockAgent:
    def __init__(self, mod_running=False, mod_cmd="dvbt_mod --udp 5000"):
        self.calls = []
        self.lock = threading.Lock()
        self.state = {"lo_hz": 0, "gain_db": -89.75, "underflows": 0, "rf_bw_hz": 0,
                      "mod": {"running": mod_running, "pid": 812 if mod_running else None,
                              "cmd": mod_cmd if mod_running else None, "since_s": 0.0, "exit": -1},
                      "mod_started": time.time()}
        self.udp_bytes = 0
        self.fail_mod_after = None           # seconds -> modulator "exits"
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._handle(None)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                self._handle(body)

            def _handle(self, body):
                p = self.path
                with srv.lock:
                    srv.calls.append((self.command, p, body))
                    st = srv.state
                    m = st["mod"]
                    if m["running"] and srv.fail_mod_after and time.time() - st["mod_started"] > srv.fail_mod_after:
                        m.update(running=False, exit=139)
                    if p == "/api/status":
                        m["since_s"] = round(time.time() - st["mod_started"], 1) if m["running"] else 0
                        return self._reply({"agent": "0.3-mock", "fw": "device-fw mock", "uptime_s": 1, "load": [0.5, 0.4, 0.3],
                                            "cpu_pct": {"cpu0": 40.0, "cpu1": 70.0, "all": 55.0}, "temp_c": 44.0,
                                            "dac": {"underflows": st["underflows"], "unf_bit": 0, "rate_hz": 9142857,
                                                    "lo_hz": st["lo_hz"], "gain_db": st["gain_db"], "rf_bw_hz": st["rf_bw_hz"]},
                                            "mod": dict(m), "udp": {"in": srv.udp_bytes // 1316, "rcvbuf_err": 0}})
                    if p == "/api/mod/start":
                        st.update(gain_db=-89.75, underflows=1, mod_started=time.time())   # bit set once at start
                        m.update(running=True, pid=900 + len(srv.calls), cmd=body["cmd"] + " " + " ".join(body.get("args", [])),
                                 exit=-1, cpu=body.get("cpu"), rt_prio=body.get("rt_prio"))
                        return self._reply({"ok": True, "pid": m["pid"], "log": "/tmp/dablab-mod.log"})
                    if p == "/api/mod/stop":
                        was = m["running"]
                        m.update(running=False, exit=0 if was else -1)
                        st["gain_db"] = -89.75
                        return self._reply({"ok": True, "exit": 0})
                    if p == "/api/mod/log":
                        return self._reply({"ok": True, "log": "mock log\nsegfault"})
                    if p == "/api/rf":
                        if "lo_hz" in body:
                            st["lo_hz"] = int(body["lo_hz"])
                        if "atten_db" in body:
                            st["gain_db"] = -max(0.0, min(89.75, float(body["atten_db"])))
                        if "rf_bw_hz" in body:
                            st["rf_bw_hz"] = int(body["rf_bw_hz"])
                        return self._reply({"ok": True, "lo_hz": st["lo_hz"], "gain_db": st["gain_db"], "rf_bw_hz": st["rf_bw_hz"]})
                    if p == "/api/underflow/clear":
                        st["underflows"] = 0
                        return self._reply({"ok": True})
                    self.send_response(404)
                    self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(0.5)
        self.udp_port = self.udp.getsockname()[1]
        self._stop = threading.Event()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def _udp_loop(self):
        while not self._stop.is_set():
            try:
                d, _ = self.udp.recvfrom(65536)
                self.udp_bytes += len(d)
            except socket.timeout:
                pass
            except OSError:
                break

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._udp_loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        self.httpd.shutdown()
        self.udp.close()

    def paths(self, method=None):
        with self.lock:
            return [p for m, p, _ in self.calls if method is None or m == method]

    def bodies(self, path):
        with self.lock:
            return [b for _, p, b in self.calls if p == path]
