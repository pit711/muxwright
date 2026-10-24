"""Multi-standard support for the PC server: mode dab | fm | dvbt | dvbc.

- channel tables from pc/channels.json (shared with the dashboard, served at /api/channels and /channels.json)
- standard_of(cfg): "dab" for mode missing / 1-4 / "dab", else the string
- validate_fm / validate_dvb: per-standard checks that produce rf.freq_hz
- media probing with ffprobe (duration, tags) for /api/media
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STANDARDS = ("dab", "fm", "dvbt", "dvbc", "dvbs", "pal", "atsc", "isdbt", "dtmb")
DVB = ("dvbt", "dvbc", "dvbs", "atsc", "isdbt", "dtmb")  # alle TS-Standards (Runner runner/dvb)
DEVICE_ONLY = ("dvbs", "pal", "atsc", "isdbt", "dtmb")
UNTESTED = ("atsc", "isdbt", "dtmb")                     # dazu DVB-C Annex B/C, NTSC-M/-J, PAL-M/-N, RBDS
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

with open(os.path.join(HERE, "channels.json"), encoding="utf-8") as _f:
    CHANNELS = json.load(_f)


def dab_channels():
    """{'6C': 185360000, ...} incl. L-band."""
    out = {k: int(round(v * 1e6)) for k, v in CHANNELS["dab"]["band3"].items()}
    out.update({k: int(round(v * 1e6)) for k, v in CHANNELS["dab"]["lband"].items()})
    return out


def dvb_channels(std):
    if std not in CHANNELS or "channels" not in CHANNELS[std]:
        return {}
    return {k: int(round(v["center_mhz"] * 1e6)) for k, v in CHANNELS[std]["channels"].items()}


def dvb_channel_norm(std, name):
    """'k21' / 'S2' / 'e5' -> key in channels.json or None."""
    if not name:
        return None
    n = str(name).strip().upper()
    tab = CHANNELS[std]["channels"]
    for cand in (n, (n[0] + "%02d" % int(n[1:])) if n[1:].isdigit() else n, (n[0] + "%d" % int(n[1:])) if n[1:].isdigit() else n):
        if cand in tab:
            return cand
    return None


def standard_of(cfg):
    m = cfg.get("mode", 1) if isinstance(cfg, dict) else 1
    if isinstance(m, str) and m.lower() in STANDARDS:
        return m.lower()
    return "dab"


def _num(v, name, lo, hi, errs, default=None):
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        errs.append(f"{name} ungültig")
        return default
    if not lo <= f <= hi:
        errs.append(f"{name} außerhalb {lo} … {hi}")
    return f


def _hex(v, bits):
    if isinstance(v, int):
        n = v
    elif isinstance(v, str) and re.fullmatch(r"(0x)?[0-9a-fA-F]+", v.strip()):
        n = int(v, 16)
    else:
        return None
    return n if 0 <= n < (1 << bits) else None


# --------------------------------------------------------------------------- FM
def validate_fm(c, errs, warns, validate_audio):
    """Checks c['fm'] (SPEC 'UKW'); sets c['rf']['freq_hz'], channel 'fm'. validate_audio(audio, prefix, errs)
    reuses the DAB audio-source checks (kind, file, playlist)."""
    fm = c.get("fm")
    if not isinstance(fm, dict):
        errs.append("fm fehlt (Objekt)")
        return
    t = CHANNELS["fm"]
    f = _num(fm.get("freq_mhz"), "fm.freq_mhz", t["min_mhz"], t["max_mhz"], errs)
    if f is None:
        errs.append("fm.freq_mhz fehlt")
    else:
        khz = round(f * 1000)
        region = str(fm.get("region") or "eu").lower()
        if region != "jp" and f < 87.5:
            errs.append(f"fm.freq_mhz {f}: unter 87,5 MHz nur mit fm.region = jp (Japan 76–95 MHz)")
        if region == "jp" and f > 95.0:
            warns.append(f"fm.freq_mhz {f}: über dem japanischen UKW-Band 76–95 MHz")
        if khz % t["raster_khz"]:
            warns.append(f"fm.freq_mhz {f} liegt nicht im {t['raster_khz']}-kHz-Raster")
        rf = c.setdefault("rf", {})
        rf["freq_hz"] = int(khz * 1000)
        rf["channel"] = "fm"
    dev = _num(fm.get("deviation_khz", 75), "fm.deviation_khz", 1, 150, errs, 75)
    pilot = _num(fm.get("pilot_khz", 6.75), "fm.pilot_khz", 0, 15, errs, 6.75)
    if fm.get("preemphasis_us", 50) not in (0, 50, 75):
        errs.append("fm.preemphasis_us: 50|75|0")
    for k in ("stereo",):
        if k in fm and not isinstance(fm[k], bool):
            errs.append(f"fm.{k}: true/false")
    _num(fm.get("audio_gain_db", 0), "fm.audio_gain_db", -40, 20, errs)
    if fm.get("mpx_power_dbr") is not None:
        _num(fm.get("mpx_power_dbr"), "fm.mpx_power_dbr", -10, 12, errs)
    lim = fm.get("limiter") or {}
    if lim and not isinstance(lim, dict):
        errs.append("fm.limiter: Objekt")
    audio = fm.get("audio") or {}
    if "source" in audio:
        validate_audio(audio, "fm.audio", errs)
    rds = fm.get("rds") or {}
    if rds.get("enable", True):
        if "pi" in rds and _hex(rds.get("pi"), 16) is None:
            errs.append("fm.rds.pi: 16-bit-Hex")
        if len(str(rds.get("ps", ""))) > 8:
            errs.append("fm.rds.ps: max. 8 Zeichen")
        if len(str(rds.get("rt", ""))) > 64:
            errs.append("fm.rds.rt: max. 64 Zeichen")
        pty = rds.get("pty", 0)
        if not (isinstance(pty, int) and 0 <= pty <= 31):
            errs.append("fm.rds.pty: 0–31")
        for k in ("tp", "ta", "ms", "ct"):
            if k in rds and not isinstance(rds[k], bool):
                errs.append(f"fm.rds.{k}: true/false")
        lvl = _num(rds.get("level_khz", 2.0), "fm.rds.level_khz", 0, 7.5, errs, 2.0)
        if rds.get("phase_deg", 0) not in (0, 90):
            errs.append("fm.rds.phase_deg: 0|90")
        if rds.get("af_method", "A") not in ("A", "B"):
            errs.append("fm.rds.af_method: A|B")
        for i, af in enumerate(rds.get("af") or []):
            _num(af, f"fm.rds.af[{i}]", t["min_mhz"], t["max_mhz"], errs)
        if rds.get("rtplus") and not isinstance(rds["rtplus"], list):
            errs.append("fm.rds.rtplus: Liste")
        if len(str(rds.get("ptyn", ""))) > 8:
            errs.append("fm.rds.ptyn: max. 8 Zeichen")
        if len(str(rds.get("lps", "")).encode("utf-8")) > 32:
            errs.append("fm.rds.lps: max. 32 Byte")
        for i, e in enumerate(rds.get("eon") or []):
            if not isinstance(e, dict) or _hex(e.get("pi"), 16) is None:
                errs.append(f"fm.rds.eon[{i}].pi: 16-bit-Hex")
        if dev is not None and pilot is not None and lvl is not None and dev - pilot - lvl < 10:
            errs.append("fm: Audiohub (deviation - pilot - rds.level) unter 10 kHz")
    sc = fm.get("second_carrier") or {}
    if sc.get("enable"):
        off = _num(sc.get("offset_khz", 200), "fm.second_carrier.offset_khz", -1800, 1800, errs, 200)
        if off is not None and abs(off) < 20:
            errs.append("fm.second_carrier.offset_khz: |df| 20…1800 kHz")
        _num(sc.get("level_db", -10), "fm.second_carrier.level_db", -60, 0, errs)
        if "audio" in sc and "source" in (sc.get("audio") or {}):
            validate_audio(sc["audio"], "fm.second_carrier.audio", errs)
    c.setdefault("rf", {}).setdefault("backoff_db", 3)


# --------------------------------------------------------------------------- DVB-T / DVB-C (SPEC "DVB-T / DVB-C – Runner-Schema")
RUNNER_DIR = os.path.join(os.path.dirname(HERE), "runner")
_dvbcfg = None
_dvbcfg_err = None


def runner_dvb():
    """Import runner/dvb/config.py (pure Python) so server and runner validate identically."""
    global _dvbcfg, _dvbcfg_err
    if _dvbcfg is None and _dvbcfg_err is None:
        import sys
        try:
            if RUNNER_DIR not in sys.path:
                sys.path.insert(0, RUNNER_DIR)
            from dvb import config as C, tables as T   # noqa: E402
            _dvbcfg = (C, T)
        except Exception as e:
            _dvbcfg_err = f"{type(e).__name__}: {e}"
    return _dvbcfg


# USB streaming budget: 7,15 MS/s measured, 4 bytes/sample -> keep 26-28 MB/s => max 7,0 MS/s
USB_MAX_SPS = 7_000_000
# DVB-S2 on the Pluto (dvbs2_mod, one A9 core incl. IIO output): highest symbol rate with real-time headroom
DVBS2_MAX_SR = {"QPSK": 22_000_000, "8PSK": 15_000_000}


def choose_path(c, std):
    """-> (path, reason). 'usb' = IQ via libiio from the PC, 'device' = modulator on the Pluto (TS via UDP).
    rf.path in the config: 'auto' (default) | 'usb' | 'device' (alias 'pluto')."""
    rf = c.get("rf") or {}
    want = str(rf.get("path") or "auto").lower()
    if want == "pluto":
        want = "device"
    if std in ("dab", "fm"):
        return "usb", f"{std}: IQ per USB"
    if std == "dvbs" and is_ham(c):
        sr = int(rf.get("sample_rate") or 0)
        return "usb", f"DATV: Modulator am PC, IQ per USB ({sr / 1e6:.3f} MS/s)"
    if std == "dvbs":
        if str((c.get("dvbs") or {}).get("system", "dvbs")).lower() == "dvbs2":
            return "device", "DVB-S2: dvbs2_mod auf dem Pluto (RRC im AD9361-FIR, Symbolrate × 2 am DAC)"
        return "device", "DVB-S: dvbs_mod auf dem Pluto (RRC im AD9361-FIR, Symbolrate × 2 am DAC)"
    if std == "pal":
        return "device", "PAL: zyklische Schleife auf dem Pluto (8 MS/s, iqplay)"
    if std == "atsc":
        return "device", "ATSC: atsc_mod auf dem Pluto (8-VSB, TS per UDP)"
    if std == "isdbt":
        return "device", "ISDB-T: isdbt_mod auf dem Pluto (OFDM 8,127 MS/s, TS per UDP)"
    if std == "dtmb":
        return "device", "DTMB: dtmb_mod auf dem Pluto (7,56 MS/s, TS per UDP)"
    if std == "dvbc" and str((c.get("dvbc") or {}).get("annex", "a")).lower() in ("b", "c"):
        return "device", "J.83 B/C: dvbc_mod --annex b|c auf dem Pluto (1 Sample/Symbol, RRC im AD9361-FIR)"
    sr = int(rf.get("sample_rate") or 0)
    fits = 0 < sr <= USB_MAX_SPS
    if std == "dvbc" and int((c.get("dvbc") or {}).get("sps", 2)) == 1:
        if want == "usb":
            return "usb", "DVB-C sps 1 braucht das RRC-FIR im AD9361 – nur im Gerätepfad"
        return "device", "DVB-C sps 1: dvbc_mod auf dem Pluto, RRC im AD9361-FIR (INT 2)"
    if want == "usb":
        return "usb", ("passt in USB" if fits else f"{sr / 1e6:.3f} MS/s über USB-Grenze {USB_MAX_SPS / 1e6} MS/s")
    if want == "device":
        return "device", "rf.path = device"
    return ("usb", f"{sr / 1e6:.3f} MS/s ≤ {USB_MAX_SPS / 1e6} MS/s → USB") if fits else         ("device", f"{sr / 1e6:.3f} MS/s > {USB_MAX_SPS / 1e6} MS/s → Modulator auf dem Pluto")


def validate_dvb(c, std, errs, warns):
    """Validate dvbt/dvbc with the runner's own normalizer (Runner format and dashboard format dvb.programs/dvb.si).
    The user config stays opaque (passed to the runner unchanged); derived values go to rf (channel, freq_hz,
    sample_rate) and c['_dvb'] (rate, budget, bandwidth, services)."""
    sec = c.get(std)
    if sec is not None and not isinstance(sec, dict):
        errs.append(f"{std}: Objekt erwartet")
        return
    mod = runner_dvb()
    rf = c.setdefault("rf", {})
    if std == "dvbs":
        pass
    elif rf.get("channel") in (None, "fm", "sat") or (rf.get("channel") and rf["channel"] in dab_channels()):
        # leftovers from another standard: take the channel from the standard section, else let the runner default
        rf.pop("channel", None)
        if not (sec or {}).get("freq_hz"):
            rf.pop("freq_hz", None)
    if (sec or {}).get("channel") and not rf.get("channel"):
        rf["channel"] = sec["channel"]
    if (sec or {}).get("freq_hz") and not rf.get("channel"):
        rf["channel"] = "custom"
        rf["freq_hz"] = sec["freq_hz"]
    if mod is None:
        errs.append(f"DVB-Prüfung nicht möglich (runner/dvb nicht ladbar: {_dvbcfg_err})")
        return
    C, T = mod
    import copy
    probe = copy.deepcopy({k: v for k, v in c.items() if not k.startswith("_")})
    probe["mode"] = std
    try:
        n, E, W = C.normalize(probe)
    except Exception as e:
        errs.append(f"DVB-Prüfung: {type(e).__name__}: {e}")
        return
    errs += [str(x) for x in E]
    warns += [str(x) for x in W]
    if E:
        return
    nrf = n.get("rf") or {}
    for k in ("channel", "freq_hz", "sample_rate"):
        if nrf.get(k) is not None:
            rf[k] = nrf[k]
    if str(rf.get("channel", "")).lower() == "custom":
        rf["channel"] = "custom"
    rf.setdefault("backoff_db", nrf.get("backoff_db", 12))
    budget = n.get("_budget") or {}
    d = n.get(std) or {}
    info = {"rate_bps": n.get("_rate_bps"), "rate_str": n.get("_rate_str"), "muxrate": n.get("_muxrate"),
            "budget": budget, "sample_rate": nrf.get("sample_rate"), "modulation": {k: v for k, v in d.items()},
            "services": [{"key": s.get("key"), "service_id": s.get("service_id"), "name": s.get("name"), "type": s.get("type"),
                          "lcn": s.get("lcn"), "video_kbps": (s.get("video") or {}).get("bitrate_kbps"),
                          "audio_kbps": (s.get("audio") or {}).get("bitrate_kbps"),
                          "source": (s.get("source") or {}).get("kind")} for s in n.get("services") or []]}
    if budget.get("total_bps") and n.get("_rate_bps"):
        info["overbooked"] = budget["total_bps"] > n["_rate_bps"]
        info["load_pct"] = round(100.0 * budget["total_bps"] / n["_rate_bps"], 1)
        if info["overbooked"]:
            warns.insert(0, f"Überbuchung: Programme + SI {budget['total_bps'] / 1e6:.2f} Mbit/s > Nutzbitrate "
                            f"{n['_rate_bps'] / 1e6:.3f} Mbit/s ({info['load_pct']} %) – Videobitraten senken oder Modulation ändern")
    c["_dvb"] = info


def is_ham(c):
    """DVB-S/S2 als Amateurfunk-DATV (direkte Sendefrequenz, Rufzeichen) statt Sat-ZF hinter einem LNB."""
    return str((c.get("dvbs") or {}).get("use", "sat")).lower() == "ham"


def dvbs_from_dashboard(c):
    """Dashboard DVB-S fields (web/js/standards.js: system, transponder_mhz, polarization H|V, lof_mhz, symbol_rate, fec,
    rolloff) -> runner fields (code_rate, frequency_hz, polarization, roll_off) and rf.freq_hz = Sat-ZF."""
    d = c.get("dvbs")
    if not isinstance(d, dict):
        return
    if d.get("fec") and not d.get("code_rate"):
        d["code_rate"] = d["fec"]
    if d.get("transponder_mhz") and not d.get("frequency_hz"):
        d["frequency_hz"] = int(round(float(d["transponder_mhz"]) * 1e6))
    pol = str(d.get("polarization", "")).upper()
    if pol in ("H", "V", "L", "R"):
        d["polarization"] = {"H": "horizontal", "V": "vertical", "L": "left", "R": "right"}[pol]
    if d.get("rolloff") and not d.get("roll_off"):
        d["roll_off"] = str(d["rolloff"])
    rf = c.setdefault("rf", {})
    if is_ham(c):
        if d.get("ham_freq_mhz"):
            rf["freq_hz"] = int(round(float(d["ham_freq_mhz"]) * 1e6))
        rf["channel"] = "ham"
        return
    if d.get("transponder_mhz") and d.get("lof_mhz") is not None and not rf.get("freq_hz_fixed"):
        rf["freq_hz"] = int(round((float(d["transponder_mhz"]) - float(d["lof_mhz"])) * 1e6))
        rf["channel"] = "sat"


def validate_dvbs(c, errs, warns):
    dvbs_from_dashboard(c)
    rf = c.setdefault("rf", {})
    if is_ham(c):
        validate_dvb(c, "dvbs", errs, warns)
        rf["channel"] = "ham"
        return
    if rf.get("channel") not in (None, "sat", "custom"):
        rf["channel"] = "sat"
    f = rf.get("freq_hz")
    if f and not 950e6 <= float(f) <= 2150e6:
        errs.append(f"rf.freq_hz {float(f) / 1e6:.1f} MHz: Sat-ZF muss 950–2150 MHz sein (Transponder − LOF)")
    validate_dvb(c, "dvbs", errs, warns)
    rf["channel"] = "sat"
    d = c.get("dvbs") or {}
    if int(d.get("sps", 1)) not in (1, 2):
        errs.append("dvbs.sps: 1 (RRC im AD9361-FIR) oder 2")
    sr = int(d.get("symbol_rate") or 22000000)
    rf["sample_rate"] = sr * (1 if int(d.get("sps", 1)) == 1 else 2)
    if str(d.get("system", "dvbs")).lower() == "dvbs2":
        # Pluto-A9, ein Kern (dvbs2_stage --bench, 23.10.): QPSK ≈ 28 MBd, 8PSK ≈ 19 MBd reine Kodierung; mit Ausgabe Reserve lassen
        lim = DVBS2_MAX_SR.get(str(d.get("modulation", "QPSK")).upper())
        if lim and sr > lim:
            warns.append(f"DVB-S2 {d.get('modulation')} {sr / 1e6:.1f} MBd: über {lim / 1e6:.0f} MBd schafft der Pluto-Modulator "
                         f"nicht sicher in Echtzeit (Unterläufe) – Symbolrate senken")


_palcli = None


def runner_pal():
    global _palcli
    if _palcli is None:
        import sys
        if RUNNER_DIR not in sys.path:
            sys.path.insert(0, RUNNER_DIR)
        from pal import cli as P   # noqa: E402
        _palcli = P
    return _palcli


def validate_pal(c, errs, warns):
    try:
        P = runner_pal()
    except Exception as e:
        errs.append(f"PAL-Prüfung nicht möglich (runner/pal nicht ladbar: {e})")
        return
    import copy
    rf = c.setdefault("rf", {})
    if rf.get("channel") in ("fm", "sat") or (rf.get("channel") and rf["channel"] in dab_channels()):
        rf.pop("channel", None)
        rf.pop("freq_hz", None)
    probe = copy.deepcopy({k: v for k, v in c.items() if not k.startswith("_")})
    probe["mode"] = "pal"
    try:
        n, E, W = P.normalize(probe)
    except Exception as e:
        errs.append(f"PAL-Prüfung: {type(e).__name__}: {e}")
        return
    errs += [str(x) for x in E]
    warns += [str(x) for x in W]
    if E:
        return
    nrf = n["rf"]
    for k in ("channel", "freq_hz", "lo_hz", "lo_hz_live", "sample_rate"):
        if nrf.get(k) is not None:
            rf[k] = nrf[k]
    if rf.get("channel"):
        rf["channel"] = str(rf["channel"]).upper() if str(rf["channel"]).lower() != "custom" else "custom"
    p = n["pal"]
    c["_pal"] = {"system": p["system"], "fs": p["fs"], "offset_hz": p["_offset_loop"], "lo_hz": nrf["lo_hz"],
                 "vision_hz": nrf["freq_hz"], "summary": P.summary(n)}


def dvb_rates(std):
    mod = runner_dvb()
    if not mod:
        return []
    if std == "dvbs":
        T = mod[1]
        rows = [{"system": "dvbs", "modulation": "QPSK", "symbol_rate": sr, "code_rate": cr,
                 "bitrate_bps": float(T.dvbs_useful_bitrate(cr, sr))}
                for sr in (22000000, 27500000, 30000000, 6000000) for cr in ("1/2", "2/3", "3/4", "5/6", "7/8")]
        rows += [{"system": "dvbs2", "modulation": m, "symbol_rate": sr, "code_rate": cr, "pilots": p,
                  "bitrate_bps": float(T.dvbs2_useful_bitrate(m, cr, sr, p))}
                 for m, srs in (("QPSK", (22000000, 6000000)), ("8PSK", (15000000, 6000000)))
                 for sr in srs for cr in T.DVBS2_MOD_RATES[m] for p in (True, False)]
        return rows
    return mod[1].rate_table(std)


# --------------------------------------------------------------------------- ffprobe media index
class MediaIndex:
    """Lazy duration/tag probe for media files; cache keyed by (name, size, mtime) in <runtime>/media_index.json."""
    AUDIO = ("mp3", "wav", "flac", "ogg", "oga", "opus", "m4a", "aac", "wma", "mp2", "aif", "aiff")
    VIDEO = ("mp4", "mkv", "ts", "m2ts", "mpg", "mpeg", "avi", "mov", "webm")
    IMAGE = ("jpg", "jpeg", "png", "gif", "webp")

    def __init__(self, media_dir, cache_file, wsl_distro="dablab", log=None):
        self.media = media_dir
        self.cache_file = cache_file
        self.distro = wsl_distro
        self.log = log or (lambda *a: None)
        self.cache = {}
        self.lock = threading.Lock()
        self._thread = None
        self._probe = None          # argv prefix or False
        try:
            with open(cache_file, encoding="utf-8") as f:
                self.cache = json.load(f)
        except Exception:
            pass

    @classmethod
    def kind_of(cls, name):
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        return "slide" if ext in cls.IMAGE else "audio" if ext in cls.AUDIO else "video" if ext in cls.VIDEO else "other"

    def probe_cmd(self):
        if self._probe is None:
            local = shutil.which("ffprobe") or next((p for p in (r"C:\ffmpeg\bin\ffprobe.exe",) if os.path.exists(p)), None)
            if local:
                self._probe = [local]
            elif shutil.which("wsl"):
                self._probe = ["wsl", "-d", self.distro, "-e", "ffprobe"]
            else:
                self._probe = False
        return self._probe

    def _wslpath(self, p):
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", os.path.abspath(p))
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/") if m else p

    def probe(self, path):
        cmd = self.probe_cmd()
        if not cmd:
            return {"error": "ffprobe nicht gefunden"}
        p = self._wslpath(path) if cmd[0] == "wsl" else path
        try:
            r = subprocess.run(cmd + ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", p],
                               capture_output=True, timeout=60, creationflags=NOWIN)
            j = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        except Exception as e:
            return {"error": str(e)[:200]}
        fmt = j.get("format") or {}
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        streams = j.get("streams") or []
        a = next((s for s in streams if s.get("codec_type") == "audio"), {})
        v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0), {})
        if not tags and a.get("tags"):
            tags = {k.lower(): val for k, val in a["tags"].items()}
        out = {"duration_s": round(float(fmt["duration"]), 2) if fmt.get("duration") else None,
               "bitrate_kbps": int(int(fmt["bit_rate"]) / 1000) if fmt.get("bit_rate") else None,
               "title": tags.get("title", ""), "artist": tags.get("artist", ""), "album": tags.get("album", ""),
               "codec": a.get("codec_name"), "samplerate": int(a["sample_rate"]) if a.get("sample_rate") else None,
               "channels": a.get("channels")}
        if v:
            out.update(video_codec=v.get("codec_name"), width=v.get("width"), height=v.get("height"))
        if r.returncode:
            out["error"] = r.stderr.decode("utf-8", "replace")[-200:]
        return out

    def key(self, name, st):
        return f"{name}|{st.st_size}|{int(st.st_mtime)}"

    def get(self, name, st):
        return self.cache.get(self.key(name, st))

    def forget(self, name):
        with self.lock:
            for k in [k for k in self.cache if k.startswith(name + "|")]:
                self.cache.pop(k, None)
            self._save()

    def _save(self):
        try:
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache, f)
            os.replace(tmp, self.cache_file)
        except Exception:
            pass

    def scan_async(self, names):
        """Probe missing entries in a background thread (one at a time)."""
        with self.lock:
            if self._thread and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._scan, args=(list(names),), daemon=True, name="ffprobe")
            self._thread.start()
            return True

    def _scan(self, names):
        for n in names:
            p = os.path.join(self.media, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            k = self.key(n, st)
            if k in self.cache:
                continue
            info = self.probe(p)
            info["t"] = time.time()
            with self.lock:
                self.cache[k] = info
                for old in [x for x in self.cache if x.startswith(n + "|") and x != k]:
                    self.cache.pop(old, None)
                self._save()
