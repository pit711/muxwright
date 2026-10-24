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
             "dvbs2": "/tmp/dablab_bin/dvbs2_mod",
             # neue Standards (untested): Modulatoren werden wie dvbs_mod nach /tmp/dablab_bin geladen (pc/device_files/<name>_iio)
             "atsc": "/tmp/dablab_bin/atsc_mod", "isdbt": "/tmp/dablab_bin/isdbt_mod", "dtmb": "/tmp/dablab_bin/dtmb_mod",
             "j83b": "/tmp/dablab_bin/dvbc_mod"}
UPLOADED = ("dvbs", "dvbs2", "atsc", "isdbt", "dtmb", "j83b")   # nicht in der Firmware: vor dem Start hochladen


def mod_key(std, cfg):
    """Schlüssel in MOD_PATHS / pc/device_files (<key>_mod_iio)."""
    if std == "dvbs" and is_dvbs2(cfg):
        return "dvbs2"
    if std == "dvbc" and str((cfg.get("dvbc") or {}).get("annex", "a")).lower() in ("b", "c"):
        return "j83b"                                   # erweiterter dvbc_mod (--annex b|c), nicht in der Firmware
    return std


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
    path = MOD_PATHS[mod_key(std, cfg)]
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
        annex = str(d.get("annex", "a")).lower()
        sps = 1 if annex in ("b", "c") else int(d.get("sps", 1))
        args += ["--qam", qam, "--sr", int(d.get("symbol_rate", 6900000)), "--sps", sps]
        if annex in ("b", "c"):
            args += ["--annex", annex]                    # J.83 B (US-Kabel) / J.83 C (Japan, RRC 0,13)
            if annex == "b":                              # Normsymbolrate je QAM (SCTE 07), nie die 6,9-MBd-Vorgabe
                i = args.index("--sr")
                args[i + 1] = 5056941 if qam == "64" else 5360537
        if sps == 1:
            if not fir:
                raise ValueError("dvbc_mod --sps 1 braucht eine RRC-FIR-Datei")
            args += ["--fir", fir]
        # Annex B: Coder auf zwei Threads (256-QAM nur so echtzeitfähig) → nicht an einen Kern binden
        opts = {"cpu": -1, "rt_prio": 50} if annex in ("b", "c") else {"cpu": 1, "rt_prio": 50}
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
    elif std == "atsc":
        opts = {"cpu": -1, "rt_prio": 50}                  # zwei Threads: Echtzeit nur mit beiden A9-Kernen (kein Pinning)
    elif std == "isdbt":
        d = cfg.get("isdbt") or {}
        two = int(d.get("layers", 1)) == 2
        cst = lambda v: str(v or "64-QAM").lower().replace("-", "")
        args += ["--mode", int(d.get("mode", 3)), "--guard", d.get("guard", "1/8"), "--layers", 2 if two else 1]
        for L in ("a", "b") if two else ("a",):
            ly = d.get("layer_" + L) or {}
            # Zeitverschachtelung im Dashboard als Mode-3-Wert (0/1/2/4); Mode 1 nutzt das Vierfache (0/4/8/16, ARIB STD-B31)
            ti = int(ly.get("ti", 2)) * (4 if int(d.get("mode", 3)) == 1 else 1)
            args += [f"--{L}-mod", cst(ly.get("constellation")), f"--{L}-rate", ly.get("code_rate", "3/4"), f"--{L}-ti", ti]
        if two and d.get("_layer_a_pids"):
            args += ["--layer-a-pids", ",".join(str(int(p)) for p in d["_layer_a_pids"])]
        args += ["--threads", 2, "--affinity", "--rt"]     # isdbt_mod verteilt Coder und IFFT selbst auf beide Kerne
        opts = {"cpu": -1, "rt_prio": 0}
    elif std == "dtmb":
        d = cfg.get("dtmb") or {}
        args += ["--carriers", int(d.get("carriers", 3780)), "--const", str(d.get("constellation", "64-QAM")).lower().replace("-", ""),
                 "--rate", d.get("code_rate", "0.6"), "--pn", int(d.get("header", 945)), "--ti", int(d.get("interleaver", 720))]
        opts = {"cpu": -1, "rt_prio": 50}
    else:
        raise ValueError(f"kein Gerätemodulator für {std}")
    if backoff is not None:
        args += ["--backoff", backoff]
    args += ["--atten", MUTE_DB]
    return cmd, [str(a) for a in args], opts
