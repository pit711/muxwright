"""Client for dablab-agent on the Pluto (pluto_fw/agent/API.md, HTTP/JSON on 192.168.2.1:8080).

Used for rf.path = "device": the modulator (dvbt_mod/dvbc_mod) runs on the Pluto, gets the TS via UDP,
and the agent starts/stops it, sets LO/attenuation and reports DAC underflows and CPU load.
"""
import json
import urllib.error
import urllib.request

MUTE_DB = 89.75


class AgentError(Exception):
    pass


class Agent:
    def __init__(self, base="http://192.168.2.1:8080", timeout=3.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _call(self, path, body=None, timeout=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                txt = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise AgentError(f"{path}: HTTP {e.code} {e.read()[:200]!r}")
        except Exception as e:
            raise AgentError(f"{path}: {e}")
        try:
            j = json.loads(txt or "{}")
        except Exception:
            raise AgentError(f"{path}: keine JSON-Antwort")
        if isinstance(j, dict) and j.get("ok") is False:
            raise AgentError(f"{path}: {j.get('error', j)}")
        return j

    # ------------------------------------------------------------------ API
    def status(self, timeout=None):
        return self._call("/api/status", timeout=timeout)

    def reachable(self, timeout=1.0):
        try:
            return self.status(timeout=timeout)
        except AgentError:
            return None

    def mod_start(self, cmd, args, cpu=-1, rt_prio=50, cwd="/tmp", atten_db=None):
        body = {"cmd": cmd, "args": [str(a) for a in args], "cpu": int(cpu), "rt_prio": int(rt_prio), "cwd": cwd}
        if atten_db is not None:
            body["atten_db"] = max(0.0, min(MUTE_DB, float(atten_db)))
        return self._call("/api/mod/start", body, timeout=10)

    def mod_stop(self):
        return self._call("/api/mod/stop", {}, timeout=8)

    def mod_log(self):
        return self._call("/api/mod/log").get("log", "")

    def rf(self, **kw):
        """lo_hz, atten_db (clamped 0..89.75), rf_bw_hz, mute ..."""
        if "atten_db" in kw:
            kw["atten_db"] = max(0.0, min(MUTE_DB, float(kw["atten_db"])))
        return self._call("/api/rf", kw)

    def mute(self):
        return self.rf(atten_db=MUTE_DB)

    def fir_enable(self, on):
        """in_out_voltage_filter_fir_en (RX and TX FIR together) without changing the rate."""
        return self._call("/api/rate", {"fir_en": 1 if on else 0})

    def underflow_clear(self):
        return self._call("/api/underflow/clear", {})

    def upload(self, name, data, timeout=120):
        """POST /api/upload?name=NAME (raw) -> stored in /tmp/dablab_files/NAME on the device."""
        import urllib.parse
        req = urllib.request.Request(self.base + "/api/upload?name=" + urllib.parse.quote(name), data=data, method="POST",
                                     headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read() or b"{}")
        except Exception as e:
            raise AgentError(f"/api/upload: {e}")
        if not j.get("ok"):
            raise AgentError(f"/api/upload: {j}")
        return j

    def start_cyclic(self, file, lo_hz, rate_hz, rf_bw_hz, seconds=0):
        """PAL / any cyclic int16 IQ loop via iqplay -N -c (TX FIR off, whole file = one DMA buffer). Starts muted."""
        return self._call("/api/mod/start", {"cmd": "pal", "file": file, "lo_hz": int(lo_hz), "rate_hz": int(rate_hz),
                                             "rf_bw_hz": int(rf_bw_hz), "atten_db": MUTE_DB, "seconds": int(seconds)},
                          timeout=30)


MOD_PATHS = {"dvbt": "/usr/bin/dvbt_mod", "dvbc": "/usr/bin/dvbc_mod", "dvbs": "/tmp/dablab_bin/dvbs_mod",
             "dvbs2": "/tmp/dablab_bin/dvbs2_mod"}


def is_dvbs2(cfg):
    return str((cfg.get("dvbs") or {}).get("system", "dvbs")).lower() == "dvbs2"


def dvbs_alpha(cfg):
    """RRC roll-off of the DVB-S/S2 config as float (DVB-S always 0.35)."""
    return float((cfg.get("dvbs") or {}).get("roll_off") or (cfg.get("dvbs") or {}).get("rolloff") or 0.35) if is_dvbs2(cfg) else 0.35


def modulator_command(std, cfg, udp_port, mod_dir="", lo_hz=None, window=32, udp_buffer=None, fir=None, rfbw_hz=None):
    """-> (cmd, args, opts) for the on-device modulator, as field-tested with the LG TV (pluto_fw/mod/tests/k56/*.sh):
      DVB-T: dvbt_mod -i udp --udp-buffer 20000 -o iio: --lo --rfbw --mode --const --rate --guard --bw --window 32 --affinity --rt --backoff
             (no --fir -> dvbt_mod switches the AD9361 TX FIR off)
      DVB-C: dvbc_mod ... --qam --sr --sps 1 --fir rrc.fir (RRC α 0,15, INT 2) | --sps 2 (RRC in software)
      DVB-S: dvbs_mod ... --sr --rate --sps 1 --fir rrc22.fir (RRC α 0,35, INT 2)
      DVB-S2: dvbs2_mod ... --sr --mod --rate --pilots --rolloff --sps 1 --fir (RRC α = roll-off, INT 2)
    The modulator starts muted (--atten 89.75); the server raises the level via the agent once it runs.
    opts: cpu / rt_prio for /api/mod/start."""
    rf = cfg.get("rf") or {}
    path = MOD_PATHS["dvbs2" if std == "dvbs" and is_dvbs2(cfg) else std]
    cmd = (mod_dir.rstrip("/") + "/" + path.rsplit("/", 1)[1]) if mod_dir else path
    buf = udp_buffer or (30000 if std == "dvbs" else 20000)
    args = ["-i", f"udp://0.0.0.0:{int(udp_port)}", "--udp-buffer", buf, "-o", "iio:"]
    if lo_hz:
        args += ["--lo", int(lo_hz)]
    if rfbw_hz:
        args += ["--rfbw", int(rfbw_hz)]
    backoff = rf.get("backoff_db")
    opts = {"cpu": -1, "rt_prio": 0}
    if std == "dvbt":
        d = cfg.get("dvbt") or {}
        const = str(d.get("constellation", "64-QAM")).upper().replace("-", "").replace(" ", "").lower()
        if const not in ("qpsk", "16qam", "64qam"):
            const = "64qam"
        mode = str(d.get("mode") or d.get("fft") or "8k").lower()
        args += ["--mode", mode, "--const", const, "--rate", d.get("code_rate", "2/3"), "--guard", d.get("guard", "1/32"),
                 "--bw", int(d.get("bandwidth_mhz") or 8)]
        if window is not None:
            args += ["--window", int(window if mode == "8k" else min(window, 8))]
        args += ["--affinity", "--rt"]                     # dvbt_mod pins/raises its own two threads
        if d.get("cell_id"):
            args += ["--cell-id", int(d["cell_id"])]
        if fir:
            args += ["--fir", fir]
    elif std == "dvbc":
        d = cfg.get("dvbc") or {}
        qam = "".join(ch for ch in str(d.get("constellation", "256-QAM")) if ch.isdigit()) or "256"
        sps = int(d.get("sps", 1))
        args += ["--qam", qam, "--sr", int(d.get("symbol_rate", 6900000)), "--sps", sps]
        if sps == 1:
            if not fir:
                raise ValueError("dvbc_mod --sps 1 braucht eine RRC-FIR-Datei")
            args += ["--fir", fir]
        opts = {"cpu": 1, "rt_prio": 50}
    elif std == "dvbs":
        d = cfg.get("dvbs") or {}
        sps = int(d.get("sps", 1))
        args += ["--sr", int(d.get("symbol_rate", 22000000)), "--rate", d.get("code_rate") or d.get("fec") or "3/4", "--sps", sps]
        if is_dvbs2(cfg):
            args += ["--mod", str(d.get("modulation", "QPSK")).lower(), "--pilots", 1 if d.get("pilots", True) else 0,
                     "--rolloff", "%.2f" % dvbs_alpha(cfg)]
        if sps == 1:
            if not fir:
                raise ValueError(f"{path.rsplit('/', 1)[1]} --sps 1 braucht eine RRC-FIR-Datei")
            args += ["--fir", fir]
        opts = {"cpu": 1, "rt_prio": 50}
    else:
        raise ValueError(f"kein Gerätemodulator für {std}")
    if backoff is not None:
        args += ["--backoff", backoff]
    args += ["--atten", MUTE_DB]
    return cmd, [str(a) for a in args], opts
