"""DAB-Lab PC server: web dashboard + HTTP API + Pluto TX streaming (see ../SPEC.md).

python server.py [--lan] [--port 8792] [--backend wsl|ssh] [--uri usb:] [--min-atten 0] [--no-nco]

Safety: the Pluto starts muted (-89.75 dB). Every error, tx/stop and server exit mutes it.
"""
import argparse
import atexit
import collections
import copy
import ctypes
import datetime as dt
import json
import mimetypes
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
from scipy.signal import fftconvolve, resample_poly, welch   # import early: a late import holds the GIL and starves TX

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pluto as plutomod  # noqa: E402
import rxproc  # noqa: E402
import playlists  # noqa: E402
import standards  # noqa: E402
import agent as agentmod  # noqa: E402
import device as devicemod  # noqa: E402
import hwcheck  # noqa: E402
import logi18n  # noqa: E402
import shop as shopmod  # noqa: E402
import setup as setupmod  # noqa: E402
import flasher as flashermod  # noqa: E402

ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")
RUNTIME = os.path.join(ROOT, "runtime")
MEDIA = os.path.join(RUNTIME, "media")
RXDIR = os.path.join(RUNTIME, "rx")
EXAMPLE_MEDIA = os.path.join(ROOT, "media")
PRESETS = os.path.join(HERE, "presets")
PLDIR = os.path.join(RUNTIME, "playlists")
VIDEO_SUB = "video"
CONFIG_FILE = os.path.join(RUNTIME, "pc_config.json")
DEVICE_MOD_FILE = os.path.join(RUNTIME, "device_mod.json")   # PID des zuletzt von uns gestarteten Gerätemodulators
ONBOARDING_FILE = os.path.join(RUNTIME, "onboarding.json")
# Einrichtung beim ersten Start: Version erhöhen, wenn sich Rechtstexte/Bestätigungen ändern (dann erneut zustimmen)
ONBOARDING_VERSION = 2      # 2: Bestätigung „codecs“ (Fremdkomponenten selbst installieren, § 11 Nr. 1 PatG)
ONBOARDING_ACCEPT = {"lab": ["shielded", "no_broadcast_radiation", "terms", "privacy", "codecs"],
                     "ham": ["shielded", "no_broadcast_radiation", "terms", "privacy", "codecs", "ham_license", "ham_rules"]}
CALLSIGN_RE = re.compile(r"^(?=.*\d)[A-Z0-9]{1,3}\d[A-Z0-9]{0,3}[A-Z](/[A-Z0-9]{1,4})?$")


def onboarding_load():
    try:
        with open(ONBOARDING_FILE, encoding="utf-8") as f:
            ob = json.load(f)
    except (OSError, ValueError):
        ob = {}
    ob["done"] = bool(ob.get("done")) and int(ob.get("version") or 0) >= ONBOARDING_VERSION
    ob["required_version"] = ONBOARDING_VERSION
    return ob


def onboarding_save(body):
    """-> (status, antwort). body: {mode: lab|ham, callsign?, accept: [schlüssel], firmware: {...}}"""
    if not isinstance(body, dict):
        return 400, {"ok": False, "errors": ["JSON-Objekt erwartet"]}
    mode = body.get("mode")
    if mode not in ONBOARDING_ACCEPT:
        return 400, {"ok": False, "errors": ["mode: lab oder ham"]}
    acc = set(body.get("accept") or [])
    miss = [k for k in ONBOARDING_ACCEPT[mode] if k not in acc]
    errs = [f"Bestätigung fehlt: {k}" for k in miss]
    cs = str(body.get("callsign") or "").strip().upper()
    if mode == "ham" and not CALLSIGN_RE.match(cs):
        errs.append("callsign: gültiges Rufzeichen nötig (z. B. DL1ABC)")
    if errs:
        return 400, {"ok": False, "errors": errs}
    ob = {"done": True, "version": ONBOARDING_VERSION, "mode": mode, "callsign": cs if mode == "ham" else "",
          "accept": sorted(acc), "firmware": body.get("firmware"),
          "lang": str(body.get("lang") or "de")[:5],
          "accepted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(RUNTIME, exist_ok=True)
    tmp = ONBOARDING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ob, f, indent=1, ensure_ascii=False)
    os.replace(tmp, ONBOARDING_FILE)
    log("info", f"Einrichtung abgeschlossen: Betriebsart {mode}{' ' + cs if cs else ''}")
    return 200, dict(onboarding_load(), ok=True)


def logs_api(name="", lang="de", level=""):
    """Liste der Logdateien bzw. Inhalt einer Datei, Meldungen in der gewünschten Sprache (logi18n)."""
    files = sorted((n for n in os.listdir(LOGDIR) if n.startswith("muxwright-") and n.endswith(".log")), reverse=True) \
        if os.path.isdir(LOGDIR) else []
    out = {"files": [{"name": n, "size": os.path.getsize(os.path.join(LOGDIR, n))} for n in files], "lang": lang, "dir": LOGDIR}
    if not name:
        name = files[0] if files else ""
    if name not in files:
        return dict(out, file=None, entries=[])
    entries = []
    with open(os.path.join(LOGDIR, name), encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if level and e.get("lvl") != level:
                continue
            txt, ok = logi18n.translate(str(e.get("msg", "")), lang)
            e["text"] = txt
            e["translated"] = ok
            e["lvl_text"] = logi18n.LEVELS.get(e.get("lvl"), {}).get(lang, e.get("lvl"))
            entries.append(e)
    return dict(out, file=name, entries=entries[-5000:], total=len(entries))


def tx_permission(cfg):
    """-> None (Senden erlaubt) oder Grund. Gilt für das Einschalten der HF, nicht für das Starten der Kette (stumm)."""
    ob = onboarding_load()
    if not ob["done"]:
        return "Einrichtung nicht abgeschlossen – erst Firmware-Prüfung und Rechtliches bestätigen"
    if standards.standard_of(cfg) == "dvbs" and standards.is_ham(cfg):
        if ob.get("mode") != "ham":
            return "Amateurfunk-Betrieb ist nicht freigegeben – in der Einrichtung „Amateurfunk“ mit Rufzeichen bestätigen"
        cs = str((cfg.get("dvbs") or {}).get("callsign") or "").upper().split("/")[0]
        if cs != str(ob.get("callsign") or "").split("/")[0]:
            return f"DATV-Rufzeichen {cs or '–'} stimmt nicht mit dem bestätigten Rufzeichen {ob.get('callsign')} überein"
    return None
TESTGEN = os.path.join(HERE, "testgen.py")
WIN_RUNNER = os.path.join(ROOT, "runner", "dablab_run.py")
MUTE_DB = plutomod.MUTE_DB

# ============================================================================ logging
LOG = collections.deque(maxlen=200)
_log_lock = threading.Lock()


LOGDIR = os.path.join(RUNTIME, "logs")
LOG_KEEP_DAYS = 30


def _log_file(e):
    """Logdatei je Tag (JSON-Zeilen) unter runtime/logs/muxwright-JJJJ-MM-TT.log; Dateien älter als 30 Tage weg."""
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.localtime(e["t"]))
        fn = os.path.join(LOGDIR, f"muxwright-{day}.log")
        new = not os.path.exists(fn)
        with open(fn, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        if new:
            cut = time.time() - LOG_KEEP_DAYS * 86400
            for n in os.listdir(LOGDIR):
                q = os.path.join(LOGDIR, n)
                if n.startswith("muxwright-") and n.endswith(".log") and os.path.getmtime(q) < cut:
                    os.remove(q)
    except OSError:
        pass


def log(lvl, msg, src="pc"):
    e = {"t": round(time.time(), 3), "lvl": lvl, "src": src, "msg": str(msg)}
    with _log_lock:
        LOG.append(e)
        _log_file(e)
    try:
        print(f"{time.strftime('%H:%M:%S')} {lvl:5s} [{src}] {msg}", flush=True)
    except Exception:
        pass


# ============================================================================ channel table (pc/channels.json)
CHANNELS = standards.dab_channels()

# UEP table (ODR-DabMux MuxElements: BitRateTable / ProtectionLevelTable(0-based) / Sub_Channel_SizeTable)
_UEP_BR = [32] * 5 + [48] * 5 + [56] * 4 + [64] * 5 + [80] * 5 + [96] * 5 + [112] * 4 + [128] * 5 + \
    [160] * 5 + [192] * 5 + [224] * 5 + [256] * 5 + [320] * 3 + [384] * 3
_UEP_PL = [4, 3, 2, 1, 0] * 2 + [4, 3, 2, 1] + [4, 3, 2, 1, 0] * 3 + [4, 3, 2, 1] + [4, 3, 2, 1, 0] * 5 + \
    [4, 3, 1] + [4, 2, 0]
_UEP_CU = [16, 21, 24, 29, 35, 24, 29, 35, 42, 52, 29, 35, 42, 52, 32, 42, 48, 58, 70, 40, 52, 58, 70, 84,
           48, 58, 70, 84, 104, 58, 70, 84, 104, 64, 84, 96, 116, 140, 80, 104, 116, 140, 168, 96, 116, 140,
           168, 208, 116, 140, 168, 208, 232, 128, 168, 192, 232, 280, 160, 208, 280, 192, 280, 416]
UEP = {(b, p + 1): c for b, p, c in zip(_UEP_BR, _UEP_PL, _UEP_CU)}
MP2_RATES = {32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384}

DEFAULT_CONFIG = {
    "name": "Standard", "mode": 1,
    "ensemble": {"eid": "0x4FFF", "ecc": "0xE0", "label": "DABLAB", "shortlabel": "DABLAB",
                 "charset": "ebu", "international_table": 1, "lto": "auto", "datetime": True,
                 "alarm_flag": False, "reconfig_counter": "hash"},
    "services": [{
        "key": "srv1", "sid": "0xD001", "ecc": None, "label": "Testton 1 kHz", "shortlabel": "Ton1k",
        "pty": 0, "pty_dynamic": False, "language": "0x08", "type": "dabplus",
        "audio": {"source": {"kind": "tone", "freq_hz": 1000, "level_dbfs": -18, "loop": True},
                  "codec": "he-aac-v2", "samplerate": 48000, "channels": 2, "bitrate": 88,
                  "mp2_mode": "j", "gain_db": 0, "bandwidth_hz": 0},
        "protection": {"profile": "EEP_A", "level": 3},
        "pad": {"enable": True, "bytes": 58,
                "dls": {"enable": True, "text": "Hallo DAB+",
                        "dlplus": {"enable": False, "artist": "", "title": "", "album": "", "station": "",
                                   "program": "", "url": ""},
                        "rotate": [], "rotate_s": 10, "charset": "utf8", "remove_label": False},
                "sls": {"enable": False, "slides": [], "interval_s": 10, "max_bytes": 50000,
                        "raw": False, "categories": False}},
        "announcements": {"supported": [], "clusters": []},
        "linking": {"fm_pi": None, "fm_freqs_mhz": [], "other_ensembles": []}}],
    "clusters": [],
    "tii": {"enable": False, "comb": 1, "pattern": 11, "old_variant": False},
    "rf": {"channel": "6C", "freq_hz": 185360000, "offset_hz": 0, "lo_offset_hz": 0, "atten_db": 60,
           "sample_rate": 4096000, "backoff_db": 12, "cfr": False, "enabled": False, "cal_dbm_at_0db": {}},
}
PLSTORE = playlists.Store(PLDIR, MEDIA)
FM_DEFAULT_CONFIG = {
    "name": "UKW Standard", "mode": "fm",
    "fm": {"freq_mhz": 100.0, "deviation_khz": 75, "pilot_khz": 6.75, "preemphasis_us": 50, "stereo": True,
           "limiter": {"enable": True, "threshold": 1.0, "release_db_s": 3, "clip": True}, "mpx_power_dbr": None,
           "audio_gain_db": 0,
           "audio": {"source": {"kind": "tone", "freq_hz": 1000, "level_dbfs": -8.3, "loop": True}},
           "rds": {"enable": True, "pi": "0xD3C4", "ps": "DABLAB", "rt": "DAB-Lab UKW-Testsender", "rtplus": [],
                   "pty": 10, "tp": True, "ta": False, "ms": True,
                   "di": {"stereo": True, "artificial_head": False, "compressed": False, "dynamic_pty": False},
                   "af": [], "af_method": "A", "af_regional": [], "ecc": "0xE0", "lic": None, "pin": None,
                   "ct": True, "utc_offset_halfhours": "auto", "ct_latency_s": 0, "ptyn": "", "lps": "",
                   "eon": [], "level_khz": 2.0, "phase_deg": 0, "group_version": "A", "rt_version": "A",
                   "rates": {"1a": 60, "3a": 20, "10a": 10, "11a": 30, "14a": 8, "15a": 20}, "rt_from_tags": True},
           "second_carrier": {"enable": False, "offset_khz": 200, "level_db": -10, "deviation_khz": 75, "stereo": True,
                              "preemphasis_us": 50, "pilot_khz": 6.75,
                              "audio": {"source": {"kind": "tone", "freq_hz": 400, "level_dbfs": -8.3}},
                              "rds": {"pi": "0xD3C5", "ps": "ZWEITER"}}},
    "rf": {"channel": "fm", "freq_hz": 100000000, "offset_hz": 0, "lo_offset_hz": 0, "atten_db": 60,
           "sample_rate": 4096000, "backoff_db": 3, "cfr": False, "enabled": False, "cal_dbm_at_0db": {}},
}
ANN_TYPES = ["Alarm", "Traffic", "Travel", "Warning", "News", "Weather", "Event", "Special", "Rad_info",
             "Sports", "Finance"]


# ============================================================================ validation
def _hex(v, bits):
    if isinstance(v, int):
        n = v
    elif isinstance(v, str) and re.fullmatch(r"(0x)?[0-9a-fA-F]+", v.strip()):
        n = int(v, 16)
    else:
        return None
    return n if 0 <= n < (1 << bits) else None


def _is_subseq(short, label):
    it = iter(label)
    return all(c in it for c in short)


def subchannel_cu(svc):
    t = svc.get("type")
    prot = svc.get("protection") or {}
    prof, lvl = prot.get("profile", "EEP_A"), int(prot.get("level", 3))
    br = int((svc.get("data") or {}).get("bitrate", 0)) if t == "data" else int((svc.get("audio") or {}).get("bitrate", 0))
    if prof == "UEP":
        return UEP.get((br, lvl))
    if prof == "EEP_A":
        return {1: 12, 2: 8, 3: 6, 4: 4}.get(lvl, 0) * br // 8
    if prof == "EEP_B":
        return {1: 27, 2: 21, 3: 18, 4: 15}.get(lvl, 0) * br // 32
    return None


def validate_rf(rf, errs, base=None, std="dab", freq_ok=True):
    """freq_ok=False: the standard's own check already reported a frequency problem; don't repeat it."""
    out = dict(base or DEFAULT_CONFIG["rf"])
    out.update(rf or {})
    ch = str(out.get("channel", "custom")).upper()
    table = CHANNELS if std == "dab" else standards.dvb_channels(std) if std in ("dvbt", "dvbc") else {}
    if std in ("dvbt", "dvbc") and ch != "CUSTOM":
        ch = standards.dvb_channel_norm(std, ch) or ch
    if not freq_ok:
        pass
    elif std in ("dvbs", "pal"):                          # channel/frequency checked by the standard's own validator
        try:
            f = int(out.get("freq_hz"))
            if not 70e6 <= f <= 6e9:
                errs.append("rf.freq_hz außerhalb 70 MHz … 6 GHz")
            out["freq_hz"] = f
        except Exception:
            errs.append("rf.freq_hz fehlt/ungültig")
    elif std == "fm":
        out["channel"] = "fm"
        t = standards.CHANNELS["fm"]
        try:
            f = int(out.get("freq_hz"))
            if not t["min_mhz"] * 1e6 <= f <= t["max_mhz"] * 1e6:
                errs.append(f"rf.freq_hz außerhalb UKW-Band {t['min_mhz']}–{t['max_mhz']} MHz")
            out["freq_hz"] = f
        except Exception:
            errs.append("rf.freq_hz fehlt/ungültig (aus fm.freq_mhz)")
    elif ch == "CUSTOM":
        out["channel"] = "custom"
        try:
            f = int(out.get("freq_hz"))
            if not 70e6 <= f <= 6e9:
                errs.append("rf.freq_hz außerhalb 70 MHz … 6 GHz")
            out["freq_hz"] = f
        except Exception:
            errs.append("rf.freq_hz fehlt/ungültig")
    elif ch in table:
        out["channel"] = ch
        out["freq_hz"] = table[ch]
    else:
        errs.append(f"rf.channel '{out.get('channel')}' unbekannt ({std})")
    for k, lo, hi in (("offset_hz", -500000, 500000), ("lo_offset_hz", -1000000, 1000000),
                      ("atten_db", 0, 89.75), ("backoff_db", 0, 40)):
        try:
            v = float(out.get(k, 0))
            if not lo <= v <= hi:
                errs.append(f"rf.{k} außerhalb {lo} … {hi}")
            out[k] = int(v) if k.endswith("_hz") else v
        except Exception:
            errs.append(f"rf.{k} ungültig")
    try:
        sr = 4096000 if std == "fm" else int(out.get("sample_rate", 4096000))   # UKW-Sender (runner/fm) läuft fest mit 4,096 MS/s
        hi = 7000000 if std in ("dab", "fm") else 61440000
        if not 2100000 <= sr <= hi:
            errs.append(f"rf.sample_rate muss 2,1 … {hi / 1e6:g} MS/s sein (Standard 4096000)")
        out["sample_rate"] = sr
    except Exception:
        errs.append("rf.sample_rate ungültig")
    for k in ("enabled", "cfr"):
        if not isinstance(out.get(k, False), bool):
            errs.append(f"rf.{k} muss true/false sein")
    if not isinstance(out.get("cal_dbm_at_0db", {}), dict):
        errs.append("rf.cal_dbm_at_0db muss ein Objekt sein")
    return out


AUDIO_KINDS = ("file", "tone", "silence", "noise", "pinknoise", "sweep", "lr_test", "polarity", "playlist")


def validate_audio_source(a, prefix, errs):
    src = (a.get("source") or {}).get("kind", "tone")
    if src not in AUDIO_KINDS:
        errs.append(f"{prefix}.source.kind unbekannt")
    if src == "file" and not (a.get("source") or {}).get("file"):
        errs.append(f"{prefix}.source.file fehlt")
    if src == "playlist":
        playlists.validate_source(a.get("source") or {}, PLSTORE, errs, f"{prefix}.source")


def validate_config(cfg):
    """-> (config, errors). Warnings (non-fatal) land in config['_warnings']."""
    errs, warns = [], []
    if not isinstance(cfg, dict):
        return None, ["Konfiguration muss ein JSON-Objekt sein"]
    c = copy.deepcopy(cfg)
    if not str(c.get("name", "")).strip():
        errs.append("name fehlt")
    std = standards.standard_of(c)
    m = c.get("mode", 1)
    if isinstance(m, str) and m.lower() not in standards.STANDARDS:
        errs.append("mode: dab (bzw. 1–4) | fm | dvbt | dvbc")
        m = 1
    if std != "dab":
        n0 = len(errs)
        if std == "fm":
            standards.validate_fm(c, errs, warns, validate_audio_source)
        elif std == "dvbs":
            standards.validate_dvbs(c, errs, warns)
        elif std == "pal":
            standards.validate_pal(c, errs, warns)
        else:
            standards.validate_dvb(c, std, errs, warns)
        c["rf"] = validate_rf(c.get("rf"), errs, std=std, freq_ok=len(errs) == n0)
        path, why = standards.choose_path(c, std)
        if path == "usb" and std == "dvbc" and int((c.get("dvbc") or {}).get("sps", 2)) == 1:
            errs.append("rf.path usb: DVB-C mit sps 1 braucht das RRC-FIR im AD9361 – sps 2 wählen oder rf.path auto/device")
        if path == "usb" and std in standards.DVB and int(c["rf"].get("sample_rate") or 0) > standards.USB_MAX_SPS:
            errs.append(f"rf.path usb: {why} – rf.path auf auto/device stellen")
        if str((c.get("rf") or {}).get("path") or "auto").lower() not in ("auto", "usb", "device", "pluto"):
            errs.append("rf.path: auto | usb | device")
        c["_path"], c["_path_reason"] = path, why
        c["_cu"] = None
        c["_warnings"] = warns
        c["_standard"] = std
        return c, errs
    if m not in (1, 2, 3, 4, "dab"):
        errs.append("mode muss 1–4 sein (DAB-Übertragungsmodus)")
    e = c.get("ensemble")
    if not isinstance(e, dict):
        errs.append("ensemble fehlt")
        e = {}
    if _hex(e.get("eid"), 16) is None:
        errs.append("ensemble.eid: 16-bit-Hex erwartet")
    if _hex(e.get("ecc"), 8) is None:
        errs.append("ensemble.ecc: 8-bit-Hex erwartet")
    lab, sl = str(e.get("label", "")), str(e.get("shortlabel", ""))
    if not 1 <= len(lab) <= 16:
        errs.append("ensemble.label: 1–16 Zeichen")
    if len(sl) > 8 or not _is_subseq(sl, lab):
        errs.append("ensemble.shortlabel: max. 8 Zeichen, Auswahl aus dem Label (Reihenfolge)")
    if e.get("charset", "ebu") not in ("ebu", "utf8"):
        errs.append("ensemble.charset: ebu|utf8")
    lto = e.get("lto", "auto")
    if lto != "auto" and not (isinstance(lto, int) and -24 <= lto <= 24):
        errs.append("ensemble.lto: auto oder -24…24")
    rc = e.get("reconfig_counter", "hash")
    if rc not in ("hash", "off") and not (isinstance(rc, int) and 0 <= rc <= 1023):
        errs.append("ensemble.reconfig_counter: hash|off|0…1023")
    svcs = c.get("services")
    if not isinstance(svcs, list) or not svcs:
        errs.append("services: mindestens ein Dienst")
        svcs = []
    keys, sids, total_cu = set(), set(), 0
    for i, s in enumerate(svcs):
        p = f"services[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{p}: Objekt erwartet")
            continue
        k = str(s.get("key", ""))
        if not re.fullmatch(r"[a-z0-9_]+", k):
            errs.append(f"{p}.key: [a-z0-9_]+")
        if k in keys:
            errs.append(f"{p}.key '{k}' doppelt")
        keys.add(k)
        t = s.get("type")
        if t not in ("dabplus", "dab", "data"):
            errs.append(f"{p}.type: dabplus|dab|data")
        sid = _hex(s.get("sid"), 32 if t == "data" else 16)
        if sid is None:
            errs.append(f"{p}.sid: {'32' if t == 'data' else '16'}-bit-Hex erwartet")
        elif sid in sids:
            errs.append(f"{p}.sid doppelt")
        sids.add(sid)
        lab, sl = str(s.get("label", "")), str(s.get("shortlabel", ""))
        if not 1 <= len(lab) <= 16:
            errs.append(f"{p}.label: 1–16 Zeichen")
        if len(sl) > 8 or not _is_subseq(sl, lab):
            errs.append(f"{p}.shortlabel: max. 8 Zeichen, Auswahl aus dem Label")
        if not (isinstance(s.get("pty", 0), int) and 0 <= s.get("pty", 0) <= 31):
            errs.append(f"{p}.pty: 0–31")
        if _hex(s.get("language", "0x08"), 8) is None:
            errs.append(f"{p}.language: 8-bit-Hex")
        if s.get("ecc") not in (None, "") and _hex(s.get("ecc"), 8) is None:
            errs.append(f"{p}.ecc: 8-bit-Hex oder null")
        prot = s.get("protection") or {}
        prof, lvl = prot.get("profile", "EEP_A"), prot.get("level", 3)
        if t in ("dabplus", "dab"):
            a = s.get("audio") or {}
            codec, br = a.get("codec"), a.get("bitrate")
            if not isinstance(br, int):
                errs.append(f"{p}.audio.bitrate fehlt")
                br = 0
            if t == "dab":
                if codec != "mp2":
                    errs.append(f"{p}: type dab braucht codec mp2")
                if br not in MP2_RATES:
                    errs.append(f"{p}.audio.bitrate: MP2 {sorted(MP2_RATES)}")
                if a.get("samplerate", 48000) not in (48000, 24000):
                    errs.append(f"{p}.audio.samplerate: MP2 48000|24000")
                if a.get("mp2_mode", "j") not in ("s", "j", "d", "m"):
                    errs.append(f"{p}.audio.mp2_mode: s|j|d|m")
            else:
                if codec not in ("he-aac-v2", "he-aac", "aac-lc"):
                    errs.append(f"{p}.audio.codec: he-aac-v2|he-aac|aac-lc")
                if not (8 <= br <= 192 and br % 8 == 0):
                    errs.append(f"{p}.audio.bitrate: DAB+ 8…192, Vielfaches von 8")
                if a.get("samplerate", 48000) not in (48000, 32000):
                    errs.append(f"{p}.audio.samplerate: DAB+ 48000|32000")
                if codec == "he-aac-v2" and a.get("channels", 2) != 2:
                    errs.append(f"{p}: HE-AAC v2 (PS) braucht 2 Kanäle")
            if a.get("channels", 2) not in (1, 2):
                errs.append(f"{p}.audio.channels: 1|2")
            validate_audio_source(a, f"{p}.audio", errs)
            pad = s.get("pad") or {}
            if pad.get("enable") and not (isinstance(pad.get("bytes", 58), int) and 0 <= pad.get("bytes", 58) <= 255):
                errs.append(f"{p}.pad.bytes: 0–255")
            dls = pad.get("dls") or {}
            if len(str(dls.get("text", "")).encode("utf-8")) > 128:
                errs.append(f"{p}.pad.dls.text: max. 128 Byte")
        elif t == "data":
            d = s.get("data") or {}
            if d.get("kind") not in ("spi", "slideshow_packet", "tpeg", "journaline", "raw"):
                errs.append(f"{p}.data.kind unbekannt")
            br = d.get("bitrate", 0)
            if not (isinstance(br, int) and 8 <= br <= 384 and br % 8 == 0):
                errs.append(f"{p}.data.bitrate: 8…384, Vielfaches von 8")
            if prof == "UEP":
                errs.append(f"{p}: UEP nur für MP2-Audio")
        if prof not in ("EEP_A", "EEP_B", "UEP"):
            errs.append(f"{p}.protection.profile: EEP_A|EEP_B|UEP")
        elif prof == "UEP":
            if t != "dab":
                errs.append(f"{p}: UEP nur bei type dab (MP2)")
            elif (br, lvl) not in UEP:
                errs.append(f"{p}: UEP {lvl} bei {br} kbit/s nicht definiert")
        elif lvl not in (1, 2, 3, 4):
            errs.append(f"{p}.protection.level: 1–4")
        elif prof == "EEP_B" and isinstance(br, int) and br % 32:
            errs.append(f"{p}: EEP-B braucht Bitrate als Vielfaches von 32")
        cu = subchannel_cu(s)
        if cu:
            total_cu += cu
        ann = (s.get("announcements") or {}).get("supported", [])
        bad = [x for x in ann if x not in ANN_TYPES]
        if bad:
            errs.append(f"{p}.announcements.supported: unbekannt {bad}")
    if total_cu > 864:
        errs.append(f"Kapazität überschritten: {total_cu} CU > 864")
    for i, cl in enumerate(c.get("clusters") or []):
        if not (isinstance(cl.get("id"), int) and 1 <= cl["id"] <= 255):
            errs.append(f"clusters[{i}].id: 1–255")
        if cl.get("service") and cl["service"] not in keys:
            errs.append(f"clusters[{i}].service '{cl['service']}' unbekannt")
    tii = c.get("tii") or {}
    if tii.get("enable"):
        if not (0 <= int(tii.get("comb", 0)) <= 23 and 0 <= int(tii.get("pattern", 0)) <= 69):
            errs.append("tii: comb 0–23, pattern 0–69")
    c["rf"] = validate_rf(c.get("rf"), errs)
    c["_cu"] = total_cu
    c["_warnings"] = warns
    c["_standard"] = "dab"
    c["_path"], c["_path_reason"] = "usb", "dab: IQ per USB"
    return c, errs


# ============================================================================ runner backends
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEWGRP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def win2wsl(p):
    p = os.path.abspath(p)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if not m:
        return p.replace("\\", "/")
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")


def _json_from(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    i = text.find("{")
    while i >= 0:
        try:
            return json.loads(text[i:])
        except Exception:
            i = text.find("{", i + 1)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return None


class Backend:
    """Where `dablab_run.py` runs. Paths given to the runner come from rpath()."""
    name = "?"

    def available(self):
        raise NotImplementedError

    def rpath(self, local):              # local runtime file -> path for the runner
        raise NotImplementedError

    def put(self, local):               # make a local runtime file visible to the runner
        pass

    def argv(self, args):
        raise NotImplementedError

    def live_argv(self, cfg_local):
        raise NotImplementedError

    def kill(self, pid):
        pass

    def run(self, args, timeout=120):
        r = subprocess.run(self.argv(args), capture_output=True, timeout=timeout, creationflags=NOWIN)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    def env(self):
        return None

    def popen(self, args):
        """Streaming runner call (jobs): stdout/stderr pipes."""
        return subprocess.Popen(self.argv(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env(), creationflags=NOWIN | NEWGRP)

    def describe(self):
        return {}


class LocalBackend(Backend):
    """Runner as a local Python script (`--backend local --runner path`), runtime = local folder. Used for tests."""
    name = "local"

    def __init__(self, runner):
        self.runner = os.path.abspath(runner)

    def available(self):
        return os.path.isfile(self.runner)

    def rpath(self, local):
        return os.path.abspath(local)

    def env(self):
        e = dict(os.environ)
        e["DABLAB_RUNTIME"] = RUNTIME
        return e

    def argv(self, args):
        return [sys.executable, "-u", self.runner] + [str(a) for a in args]

    def run(self, args, timeout=120):
        r = subprocess.run(self.argv(args), capture_output=True, timeout=timeout, env=self.env(), creationflags=NOWIN)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    def live_argv(self, cfg_local):
        return self.argv(["live", "--config", os.path.abspath(cfg_local)]), self.env()

    def kill(self, pid):
        pass                                    # local process: terminate() in _teardown

    def describe(self):
        return {"backend": "local", "runner_path": self.runner, "runtime": RUNTIME}


class WslBackend(Backend):
    name = "wsl"

    def __init__(self, distro="dablab", runner=WIN_RUNNER, python="python3"):
        self.distro = distro
        self.runner_win = runner
        self.python = python
        self.runtime = win2wsl(RUNTIME)
        self._distro_ok = None
        self._distro_t = 0

    def _env(self):
        env = dict(os.environ)
        env["DABLAB_RUNTIME"] = self.runtime
        w = env.get("WSLENV", "")
        env["WSLENV"] = (w + ":" if w else "") + "DABLAB_RUNTIME"
        return env

    def distro_ok(self):
        if self._distro_ok is None or time.time() - self._distro_t > 30:
            try:
                r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=10, creationflags=NOWIN)
                names = r.stdout.decode("utf-16-le", "ignore").replace("\x00", "").split()
                self._distro_ok = self.distro in names
            except Exception:
                self._distro_ok = False
            self._distro_t = time.time()
        return self._distro_ok

    def available(self):
        return os.path.isfile(self.runner_win) and self.distro_ok()

    def rpath(self, local):
        return win2wsl(local)

    def argv(self, args):
        return ["wsl", "-d", self.distro, "-e", self.python, win2wsl(self.runner_win)] + [str(a) for a in args]

    def run(self, args, timeout=120):
        r = subprocess.run(self.argv(args), capture_output=True, timeout=timeout, env=self._env(),
                           creationflags=NOWIN)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    def env(self):
        return self._env()

    def live_argv(self, cfg_local):
        sh = (f"export DABLAB_RUNTIME={shlex.quote(self.runtime)}; echo pc_pid=$$ >&2; "
              f"exec {self.python} {shlex.quote(win2wsl(self.runner_win))} live --config {shlex.quote(win2wsl(cfg_local))}")
        return ["wsl", "-d", self.distro, "-e", "sh", "-c", sh], self._env()

    def kill(self, pid):
        subprocess.run(["wsl", "-d", self.distro, "-e", "kill", "-TERM", str(pid)], capture_output=True,
                       timeout=10, creationflags=NOWIN)

    def describe(self):
        return {"backend": "wsl", "distro": self.distro, "runner_path": self.runner_win,
                "runtime": self.runtime, "distro_ok": self._distro_ok}


class SshBackend(Backend):
    """Fallback: Mac via ssh; runtime = ~/dablab/runtime (files are copied there)."""
    name = "ssh"
    OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3"]

    def __init__(self, host="", python="python3"):
        self.host = host
        self.python = python
        self.ssh = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
        self._avail = None
        self._t = 0

    def _ssh(self, cmd):
        return [self.ssh] + self.OPTS + [self.host, cmd]

    def available(self):
        if self._avail is None or time.time() - self._t > 30:
            try:
                r = subprocess.run(self._ssh("test -f ~/dablab/runtime/dablab_run.py && echo YES || echo NO"),
                                   capture_output=True, timeout=15, creationflags=NOWIN)
                self._avail = b"YES" in r.stdout
            except Exception:
                self._avail = False
            self._t = time.time()
        return self._avail

    def rpath(self, local):
        return os.path.relpath(local, RUNTIME).replace("\\", "/")   # relative to ~/dablab/runtime (cwd)

    def put(self, local):
        rel = self.rpath(local)
        d = os.path.dirname(rel) or "."
        cmd = f"mkdir -p ~/dablab/runtime/{shlex.quote(d)} && cat > ~/dablab/runtime/{shlex.quote(rel)}"
        with open(local, "rb") as f:
            r = subprocess.run(self._ssh(cmd), stdin=f, capture_output=True, timeout=600, creationflags=NOWIN)
        if r.returncode:
            raise RuntimeError(f"Kopie zum Mac fehlgeschlagen: {r.stderr.decode('utf-8', 'replace')[:300]}")

    def argv(self, args):
        q = " ".join(shlex.quote(str(a)) for a in args)
        return self._ssh(f"cd ~/dablab/runtime && {self.python} dablab_run.py {q}")

    def live_argv(self, cfg_local):
        self.put(cfg_local)
        cmd = (f"cd ~/dablab/runtime && echo pc_pid=$$ >&2 && exec {self.python} dablab_run.py live "
               f"--config {shlex.quote(self.rpath(cfg_local))}")
        return self._ssh(cmd), None

    def kill(self, pid):
        subprocess.run(self._ssh(f"kill -TERM {int(pid)}"), capture_output=True, timeout=10, creationflags=NOWIN)

    def describe(self):
        return {"backend": "ssh", "host": self.host, "runtime": "~/dablab/runtime"}


# ============================================================================ application
class App:
    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        if args.backend == "local":
            self.backend = LocalBackend(args.runner or WIN_RUNNER)
        else:
            self.backend = WslBackend(args.wsl_distro) if args.backend == "wsl" else SshBackend(args.ssh_host)
        self.agent = agentmod.Agent(args.agent) if args.agent and args.agent.lower() != "off" else None
        self.agent_status = None        # last /api/status of dablab-agent (None = unreachable / off)
        self.agent_t = 0
        self.device = None              # active device-path session {cmd,args,port,pid,started,unf0}
        self.jobs = {}                  # youtube jobs
        self._job_n = 0
        self.pluto = plutomod.Pluto(args.uri, log=lambda lvl, m: log(lvl, m, "pluto"))
        self.cfg = self._load_config()
        self.stream = None
        self.proc = None
        self.proc_pid = None
        self.source = None              # "runner" | "testgen"
        self.session = None
        self.tx_state = "idle"
        self.tx_since = time.time()
        self.tx_error = None
        self.last_stats = {}
        self.gain_applied = None        # hardwaregain we set last (None = unknown)
        self.check = None
        self.monitor = None
        self.monitor_lock = threading.Lock()
        self.sweep = {"running": False}
        self.sweep_stop = threading.Event()
        self.runner_ok = None
        self.runner_installed = None
        self.selftest = None
        self._selftest_t = 0
        self.closing = False
        self.nco = not args.no_nco
        self.user_stopped = True
        self.media_index = standards.MediaIndex(MEDIA, os.path.join(RUNTIME, "media_index.json"), args.wsl_distro,
                                                log=lambda l, m: log(l, m, "media"))
        self.runner_status = None
        self.now_playing = {}
        self._status_logged = 0
        self.restarts = 0
        self.restart_times = []

    # ---------------------------------------------------------------- config
    def _load_config(self):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                c, errs = validate_config(json.load(f))
            if not errs:
                c["rf"]["enabled"] = False          # always start muted
                return c
            log("warn", f"gespeicherte Konfiguration ungültig ({errs[:3]}), nehme Standard")
        except FileNotFoundError:
            pass
        except Exception as e:
            log("warn", f"Konfiguration nicht lesbar: {e}")
        c, _ = validate_config(DEFAULT_CONFIG)
        return c

    def save_config(self):
        os.makedirs(RUNTIME, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.runner_config(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)

    def runner_config(self):
        """Config as the runner reads it: for DVB the output follows the chosen path
        (usb -> modulator on the PC, IQ on stdout; device -> TS via UDP to the Pluto)."""
        c = self.public_cfg()
        if self.standard == "dvbs":
            standards.dvbs_from_dashboard(c)
        if self.standard in standards.DVB:
            path = self.path()
            c.setdefault("rf", {})["path"] = c["rf"].get("path") or "auto"
            if path == "device":
                c["output"] = {"kind": "udp", "host": self.args.device_host, "port": int(self.args.device_udp_port),
                               "burst": 7, "rtp": False}
            else:
                c["output"] = {"kind": "modulator"}
        return c

    def path(self):
        return self.cfg.get("_path") or "usb"

    def foreign_mod(self):
        """True if the agent reports a modulator on the device that this server did not start."""
        st = self.agent_status or {}
        return bool((st.get("mod") or {}).get("running")) and self.device is None

    def orphan_mod(self):
        """Command line if the device modulator was started by Muxwright itself in an earlier session (server restarted
        or closed while transmitting): same PID as remembered in DEVICE_MOD_FILE at start, or our upload folders.
        Such a leftover is taken over without force; modulators started by hand stay foreign."""
        m = (self.agent_status or {}).get("mod") or {}
        cmd = str(m.get("cmd") or "")
        mine = self.remembered_mod()
        if m.get("pid") and m.get("pid") == mine.get("pid") and cmd.startswith(str(mine.get("cmd") or "\0")):
            return cmd
        return cmd if ("/tmp/dablab_files/" in cmd or "/tmp/dablab_bin/" in cmd) else ""

    @staticmethod
    def remembered_mod():
        try:
            with open(DEVICE_MOD_FILE, encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def remember_device_mod(self, pid, cmd):
        try:
            with open(DEVICE_MOD_FILE, "w", encoding="utf-8") as f:
                json.dump({"pid": pid, "cmd": cmd, "ts": int(time.time())}, f)
        except OSError as e:
            log("warn", f"Merkdatei Gerätemodulator nicht geschrieben: {e}")

    def public_cfg(self):
        return {k: v for k, v in self.cfg.items() if not k.startswith("_")}

    @property
    def standard(self):
        return self.cfg.get("_standard") or standards.standard_of(self.cfg)

    # ---------------------------------------------------------------- RF
    def rf_plan(self, rf):
        """-> (tx_lo_hz, nco_hz, signal_center_hz)"""
        f, off, lo_off = rf["freq_hz"], rf.get("offset_hz", 0), rf.get("lo_offset_hz", 0)
        if self.standard in standards.DVB:              # wide signal: no NCO shift
            return f + off, 0, f + off
        if self.standard == "pal":                      # loop: vision carrier at -offset in baseband
            lo = rf.get("lo_hz") or (f + 2250000)
            return lo + off, 0, f + off
        if self.nco:
            return f - lo_off, off + lo_off, f + off
        return f + off, 0, f + off

    def running(self):
        if self.device is not None:
            return self.tx_state == "running"
        return self.stream is not None and self.stream.state == "running"

    def sync_gain(self, force=False):
        """Apply atten if TX runs and rf.enabled, otherwise mute."""
        if self.device is not None:
            return self._sync_gain_device(force)
        if self.foreign_mod():
            return                                   # someone else's modulator owns the TX path: hands off
        if not self.pluto.connected:
            self.gain_applied = None
            return
        rf = self.cfg["rf"]
        if rf.get("enabled") and self.running() and not self.closing:
            att = max(float(rf["atten_db"]), float(self.args.min_atten))
            want = plutomod.clamp_gain(att)
        else:
            want = MUTE_DB
        if force or self.gain_applied != want:
            try:
                if want == MUTE_DB:
                    self.pluto.mute()
                else:
                    self.pluto.set_atten(-want)
                self.gain_applied = want
            except Exception as e:
                log("error", f"Dämpfung setzen: {e}")
                self.safe_mute()

    def _sync_gain_device(self, force=False):
        rf = self.cfg["rf"]
        if rf.get("enabled") and self.tx_state == "running" and not self.closing:
            want = plutomod.clamp_gain(max(float(rf["atten_db"]), float(self.args.min_atten)))
        else:
            want = MUTE_DB
        if force or self.gain_applied != want:
            try:
                self.agent.rf(atten_db=-want)
                self.gain_applied = want
            except Exception as e:
                log("error", f"Dämpfung (Gerät) setzen: {e}")
                self._agent_mute()

    def _agent_mute(self):
        try:
            if self.agent is not None:
                self.agent.mute()
                self.gain_applied = MUTE_DB
        except Exception as e:
            log("error", f"Gerät stummschalten fehlgeschlagen: {e}")

    def apply_rf(self):
        rf = self.cfg["rf"]
        lo, nco, center = self.rf_plan(rf)
        out = {"tx_lo_hz": lo, "nco_hz": nco, "center_hz": center, "path": self.path()}
        if self.device is not None:
            try:
                r = self.agent.rf(lo_hz=int(lo))
                out["tx_lo_readback"] = r.get("lo_hz")
            except Exception as e:
                log("error", f"LO (Gerät): {e}")
                self._agent_mute()
                raise
            self.sync_gain()
            out["atten_readback"] = -self.gain_applied if self.gain_applied is not None else None
            return out
        if self.foreign_mod():
            out["warning"] = "Modulator auf dem Gerät läuft (nicht von diesem Server) – HF-Werte nur gespeichert"
            return out
        if not self.nco and rf.get("lo_offset_hz"):
            out["warning"] = "NCO abgeschaltet (--no-nco): lo_offset_hz wird ignoriert"
        if self.stream is not None:
            out["nco_hz"] = self.stream.set_nco(nco)
        if self.pluto.connected:
            try:
                out["tx_lo_readback"] = self.pluto.set_tx_lo(lo)
            except Exception as e:
                log("error", f"LO setzen: {e}")
                self.safe_mute()
                raise
        self.sync_gain()
        if self.pluto.connected:
            out["atten_readback"] = -self.pluto.read_gain()
        return out

    def safe_mute(self):
        if self.device is not None:
            self._agent_mute()
            return
        if self.foreign_mod():
            return
        try:
            if self.pluto.connected:
                self.pluto.mute()
                self.gain_applied = MUTE_DB
        except Exception as e:
            log("error", f"Stummschalten fehlgeschlagen: {e}")

    # ---------------------------------------------------------------- TX
    def tx_start(self, auto=False, force=False):
        if not auto:
            self.user_stopped = False
        path = self.path()
        with self.lock:
            if self.stream is not None or self.device is not None or self.tx_state == "starting":
                return 409, {"ok": False, "error": "TX läuft bereits"}
            if self.agent is not None and (time.time() - self.agent_t > 3):
                self._poll_agent()
            if self.foreign_mod() and not force and self.orphan_mod():
                log("warn", f"Modulator einer früheren Muxwright-Sitzung läuft noch – wird übernommen ({self.orphan_mod()[:80]})")
                force = True
            if self.foreign_mod() and not force:
                m = (self.agent_status or {}).get("mod") or {}
                return 409, {"ok": False, "code": "foreign_mod", "cmd": m.get("cmd"),
                             "error": f"Auf dem Pluto läuft ein fremder Modulator ({m.get('cmd')}); "
                                      "mit {\"force\":true} beenden und übernehmen"}
            if path == "device":
                if self.agent is None or self.agent_status is None:
                    return 503, {"ok": False, "error": "dablab-agent auf dem Pluto nicht erreichbar (rf.path device)"}
            elif not self.pluto.connected and not self.pluto.connect():
                return 503, {"ok": False, "error": "Pluto nicht verbunden"}
            self.tx_state, self.tx_since, self.tx_error = "starting", time.time(), None
            self.session = None
        worker = self._tx_start_device if path == "device" else self._tx_start_worker
        self.start_thread = threading.Thread(target=worker, args=(force,), daemon=True, name="tx-start")
        self.start_thread.start()
        return 200, {"ok": True, "state": "starting", "path": path}

    def _tx_start_device(self, force=False):
        """Device path: modulator on the Pluto via dablab-agent.
        DVB-T/C/S: runner TS chain (output udp) -> dvbt_mod/dvbc_mod/dvbs_mod (UDP input, field-tested arguments).
        PAL: runner pal render -> loop file -> agent upload -> agent cmd "pal" (iqplay, cyclic DMA buffer)."""
        std = self.standard
        try:
            if std not in standards.DVB and std != "pal":
                raise RuntimeError(f"rf.path device gibt es nur für DVB-T/C/S und PAL (Standard {std})")
            if self.args.source == "testgen":
                raise RuntimeError("rf.path device braucht den Runner, nicht den Testgenerator")
            if not self.backend.available():
                raise RuntimeError(f"Runner ({self.backend.name}) nicht verfügbar")
            if self.pluto.connected and not self.pluto.passive:
                self.safe_mute()
            dev = self.devssh()
            if dev is not None:
                stray = dev.stray_modulators()
                if stray:
                    mine = str(self.remembered_mod().get("pid") or "")
                    ours = all("/tmp/dablab_files/" in s or "/tmp/dablab_bin/" in s or (mine and s.split()[0] == mine)
                               for s in stray)
                    if ours and not force:
                        log("warn", f"Modulatorprozess einer früheren Muxwright-Sitzung wird beendet ({stray[0][:80]})")
                    if not force and not ours:
                        raise RuntimeError(f"Auf dem Pluto laufen fremde Modulatorprozesse ({stray[0][:80]}); mit force übernehmen")
                    dev.kill_stray()
            st = self.agent.status()
            if (st.get("mod") or {}).get("running"):
                log("warn", f"Gerät: laufender Modulator wird beendet ({(st.get('mod') or {}).get('cmd')})")
                self.agent.mod_stop()
            rf = self.cfg["rf"]
            lo, _, _ = self.rf_plan(rf)
            self.agent.rf(lo_hz=int(lo), atten_db=agentmod.MUTE_DB)
            self.gain_applied = MUTE_DB
            if std in ("dvbt", "pal"):
                # no FIR in these modes: switch the shared RX/TX FIR off now (while muted, before the DMA starts),
                # otherwise a leftover RX FIR (e.g. RRC from DVB-S) spoils the control receiver and toggling it later
                # would reprogram the BBPLL under a running TX
                try:
                    self.agent.fir_enable(False)
                except Exception as e:
                    log("warn", f"Gerät: FIR aus: {e}")
            if std == "pal":
                self._start_pal(lo)
                return
            fir, rfbw = None, None
            if std == "dvbt":
                rfbw = int((self.cfg.get("dvbt") or {}).get("bandwidth_mhz") or 8) * 1000000
            elif std == "dvbc":
                d = self.cfg.get("dvbc") or {}
                rfbw = 8000000
                if int(d.get("sps", 1)) == 1:
                    fir = dev.ensure_fir("dvbc", int(d.get("symbol_rate", 6900000)), 0.15, 128, self.args.wsl_distro)
            elif std == "dvbs":
                d = self.cfg.get("dvbs") or {}
                sr = int(d.get("symbol_rate", 22000000))
                alpha = agentmod.dvbs_alpha(self.cfg)
                rfbw = int(min(40e6, max(2e6, sr * (1 + alpha))))
                if int(d.get("sps", 1)) == 1:
                    fir = dev.ensure_fir("dvbs", sr, alpha, 64, self.args.wsl_distro)
                if not self.args.device_mod_dir:
                    key = "dvbs2" if agentmod.is_dvbs2(self.cfg) else "dvbs"
                    dev.ensure(os.path.join(devicemod.FILES, f"{key}_mod_iio"), agentmod.MOD_PATHS[key], "0755")
            cmd, margs, opts = agentmod.modulator_command(std, self.runner_config(), self.args.device_udp_port,
                                                         self.args.device_mod_dir, lo_hz=lo, window=self.args.device_window,
                                                         fir=fir, rfbw_hz=rfbw)
            r = self.agent.mod_start(cmd, margs, cpu=opts["cpu"], rt_prio=opts["rt_prio"])
            if fir and dev is not None:
                # the modulator loads the RRC taps for TX and RX but only enables the TX FIR; enable the RX FIR too
                # (matched filter for the control receiver). Done now, still muted and before 'running', because it
                # rebuilds the clock chain (DAC 55,2 -> 110,4 MHz, TX rate unchanged); the start-up underflow is cleared.
                time.sleep(2.0)
                try:
                    out = dev.sh(devicemod._PHY + "echo 1 > $PHY/in_voltage_filter_fir_en; cat $PHY/in_voltage_filter_fir_en; "
                                 "cat $PHY/tx_path_rates")
                    log("info", f"Gerät: RX-FIR (Matched Filter) ein – {' '.join(out.split())[:160]}")
                except Exception as e:
                    log("warn", f"Gerät: RX-FIR nicht aktiviert: {e}")
            self.device = {"cmd": cmd, "args": margs, "port": int(self.args.device_udp_port), "pid": r.get("pid"),
                           "started": time.time(), "unf0": None, "host": self.args.device_host, "standard": std,
                           "fir": fir}
            self.remember_device_mod(r.get("pid"), cmd)
            log("info", f"Gerät: {cmd} {' '.join(margs)} (pid {r.get('pid')})")
            self.save_config()
            argv, env = self.backend.live_argv(CONFIG_FILE)
            self.source = "runner"
            log("info", f"TS-Kette → udp://{self.args.device_host}:{self.args.device_udp_port}: {' '.join(argv)[:200]}")
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=1 << 16, env=env,
                                         creationflags=NOWIN | NEWGRP)
            self.proc_pid = None
            threading.Thread(target=self._stderr_reader, args=(self.proc,), daemon=True, name="tx-stderr").start()
            threading.Thread(target=self._drain, args=(self.proc,), daemon=True, name="tx-stdout").start()
        except Exception as e:
            log("error", f"TX-Start (Gerät): {e}")
            self.tx_error = str(e)
            self._teardown("error")

    def devssh(self):
        """SSH helper for the Pluto (deploy files, stray processes, RX capture); None with --no-device-ssh."""
        if self.args.no_device_ssh:
            return None
        if getattr(self, "_dev", None) is None:
            self._dev = devicemod.Device(self.args.device_host if self.args.device_host not in ("127.0.0.1", "localhost")
                                         else "192.168.2.1", log=lambda l, m: log(l, m, "gerät"))
        return self._dev

    def _start_pal(self, lo):
        """Render the PAL loop (cached by config), upload it to the agent and start it cyclic."""
        import hashlib
        p = self.cfg.get("pal") or {}
        key = json.dumps({"pal": p, "freq": self.cfg["rf"].get("freq_hz")}, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha1(key.encode()).hexdigest()[:12]
        os.makedirs(os.path.join(RUNTIME, "pal"), exist_ok=True)
        local = os.path.join(RUNTIME, "pal", f"pal_{h}.iq")
        info_f = local + ".json"
        if not (os.path.isfile(local) and os.path.isfile(info_f)):
            self.save_config()
            log("info", "PAL: rendere Schleife (runner pal render)")
            rc, out, err = self.backend.run(["render", "--config", self.backend.rpath(CONFIG_FILE),
                                             "--out", self.backend.rpath(local), "--format", "s16"], timeout=300)
            j = _json_from(out) or {}
            if rc or not j.get("ok") or not os.path.isfile(local):
                raise RuntimeError(f"PAL-render fehlgeschlagen: {j.get('errors') or err[-300:]}")
            with open(info_f, "w", encoding="utf-8") as f:
                json.dump(j, f)
        with open(info_f, encoding="utf-8") as f:
            j = json.load(f)
        name = f"pal_{h}.iq"
        st = self.agent.status()
        with open(local, "rb") as f:
            blob = f.read()
        t0 = time.time()
        try:
            self.agent.upload(name, blob, timeout=300)
        except agentmod.AgentError as e:
            if "404" not in str(e) or self.devssh() is None:
                raise
            self.devssh().ensure(local, f"/tmp/dablab_files/{name}")        # older agent without /api/upload: SSH
        log("info", f"PAL: Schleife {len(blob) / 1e6:.1f} MB hochgeladen ({time.time() - t0:.1f} s), LO {lo} Hz")
        rate = int(j.get("sample_rate") or 8000000)
        try:
            ver = tuple(int(x) for x in str(st.get("agent", "0")).split(".")[:2])
        except ValueError:
            ver = (0, 0)
        if ver >= (0, 6):
            r = self.agent.start_cyclic(name, lo, rate, min(rate, 8000000))
        else:
            # agent < 0.6 has no "pal" command: start iqplay directly (cyclic, whole file = one DMA buffer, muted)
            nsamp = len(blob) // 4
            r = self.agent.mod_start("/usr/bin/iqplay", ["-r", rate, "-f", f"/tmp/dablab_files/{name}", "-c", "-b", nsamp,
                                                         "-l", int(lo), "-g", agentmod.MUTE_DB, "-w", min(rate, 8000000), "-q"],
                                     cpu=-1, rt_prio=0)
            # iqplay of firmware b3 has no -N: ad9361_set_bb_rate() loads and enables a FIR (cuts the sound carrier at
            # +3,25 MHz). Switch it off again while still muted; 8 MS/s is valid without FIR, the cyclic DMA keeps running.
            time.sleep(1.5)
            try:
                rr = self.agent.fir_enable(False)
                log("info", f"PAL: FIR aus (iqplay ohne -N) – Rate {rr.get('rate_hz')} Hz, fir_en {rr.get('fir_en')}")
            except Exception as e:
                log("warn", f"PAL: FIR aus: {e}")
        self.remember_device_mod(r.get("pid"), "/usr/bin/iqplay")
        self.device = {"cmd": "pal", "args": [name], "file": name, "pid": r.get("pid"), "started": time.time(),
                       "unf0": None, "host": self.args.device_host, "standard": "pal", "loop": j.get("summary")}
        self.source = "runner"

    @staticmethod
    def _drain(proc):
        try:
            while proc.stdout.read(65536):
                pass
        except Exception:
            pass

    def _device_tick(self):
        """Called from the loop while the device path runs: agent status -> tx stats, failure detection."""
        d = self.device
        st = self.agent_status
        if d is None or d.get("stopping") or d.get("failed") or self.tx_state == "stopping":
            return
        if st is None:
            if time.time() - self.agent_t > 6:
                self._device_fail("dablab-agent antwortet nicht")
            return
        mod, dac = st.get("mod") or {}, st.get("dac") or {}
        unf = int(dac.get("underflows") or 0)
        if not mod.get("running"):
            if time.time() - d["started"] > 2:
                tail = ""
                try:
                    tail = self.agent.mod_log()[-300:]
                except Exception:
                    pass
                self._device_fail(f"Modulator beendet (exit {mod.get('exit')}) {tail}".strip())
            return
        if self.proc is not None and self.proc.poll() is not None:
            self._device_fail(f"TS-Kette beendet (rc {self.proc.returncode})")
            return
        rs = self.runner_status or {}
        no_chain = self.proc is None
        if self.tx_state == "starting" and (rs.get("out_rate_bps") or time.time() - d["started"] > (1.5 if no_chain else 8)):
            self.agent.underflow_clear()                 # first buffer always sets the sticky bit
            d["unf0"] = 0
            self.tx_state, self.tx_since = "running", time.time()
            log("info", "TX läuft (Modulator auf dem Pluto)")
            return
        if self.tx_state == "starting":
            return
        if d["unf0"] is None:
            d["unf0"] = unf
        und = max(0, unf - d["unf0"])
        if und > self.last_stats.get("underruns", 0) and (und <= 10 or und % 100 == 0):
            log("warn", f"Gerät: DAC-Underflow-Abtastungen {und}")
        tgt, cur = rs.get("target_bps") or 0, rs.get("out_rate_bps")
        if tgt and cur is not None and time.time() - self.tx_since > 25:
            if cur < 0.6 * tgt:                       # Kette liefert dauerhaft deutlich zu wenig: Encoder zu langsam
                d["starved"] = d.get("starved", 0) + 1
                if d["starved"] == 5:
                    log("error", f"TS-Kette liefert nur {cur / 1e6:.1f} von {tgt / 1e6:.1f} Mbit/s – Encoder zu langsam, das Bild bricht ab. "
                                 "GPU (NVENC) wählen oder Auflösung/Bitrate senken.")
            else:
                d["starved"] = 0
        net = (st.get("udp") or {})
        self.last_stats = {"underruns": und, "starved": d.get("starved", 0) >= 5, "rate_ms": round((dac.get("rate_hz") or 0) / 1e6, 6) or None,
                           "rate_avg_ms": None, "samples_sent": None,
                           "ts_rate_bps": rs.get("out_rate_bps"), "target_bps": rs.get("target_bps"),
                           "udp_rcvbuf_err": net.get("rcvbuf_err"), "cpu_pct": st.get("cpu_pct"),
                           "device_temp_c": st.get("temp_c"), "mod_since_s": mod.get("since_s")}

    def _device_fail(self, msg):
        d = self.device
        if d is None or d.get("failed") or d.get("stopping"):
            return
        d["failed"] = True
        log("error", f"Gerät: {msg}")
        self.tx_error = msg
        threading.Thread(target=self._fail_worker, daemon=True, name="tx-fail").start()

    def _poll_agent(self):
        if self.agent is None:
            self.agent_status = None
            return None
        try:
            self.agent_status = self.agent.status(timeout=1.5)
            self.agent_t = time.time()
        except Exception:
            self.agent_status = None
        return self.agent_status

    def _tx_start_worker(self, force=False):
        try:
            if force and self.foreign_mod():
                log("warn", "Fremder Modulator auf dem Gerät wird beendet (force)")
                self.agent.mod_stop()
                self._poll_agent()
                if self.pluto.passive:
                    self.pluto.drop("(übernehme TX-Pfad)")
                    self.pluto.connect()
            self.safe_mute()
            fs = int(self.cfg["rf"]["sample_rate"])
            self.pluto.set_rate(fs)
            self.save_config()
            if self.args.source != "testgen" and self.runner_ok is None:
                self._check_backend()
            if self.args.source == "runner":
                use_runner = self.backend.available()      # forced: installed is enough, selftest may fail
            else:
                use_runner = self.args.source == "auto" and bool(self.runner_ok)
            if self.args.source == "runner" and not use_runner:
                raise RuntimeError(f"Runner ({self.backend.name}) nicht verfügbar (--source runner)")
            if use_runner:
                argv, env = self.backend.live_argv(CONFIG_FILE)
                self.source = "runner"
            else:
                argv, env = [sys.executable, "-u", TESTGEN, "live", "--config", CONFIG_FILE], None
                self.source = "testgen"
            log("info", f"Quelle: {self.source} ({self.backend.name}) → {' '.join(argv)[:200]}")
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=1 << 20, env=env,
                                         creationflags=NOWIN | NEWGRP)
            self.proc_pid = None
            threading.Thread(target=self._stderr_reader, args=(self.proc,), daemon=True, name="tx-stderr").start()
            lo, nco, _ = self.rf_plan(self.cfg["rf"])
            half_bw = {"dvbt": 0.5e6 * int((self.cfg.get("dvbt") or {}).get("bandwidth_mhz") or 8) * 0.95,
                       "dvbc": 0.575 * int((self.cfg.get("dvbc") or {}).get("symbol_rate") or 6900000)}.get(self.standard, 0.85e6)
            st = plutomod.TxStream(self.pluto, self.proc, fs, log=lambda l, m: log(l, m, "tx"), nco_hz=nco, half_bw=half_bw,
                                   nsamp=self.args.nsamp, kbufs=self.args.kbufs,
                                   qchunks=max(4, (1 << 21) // self.args.nsamp), prefill=max(2, (1 << 20) // self.args.nsamp),
                                   on_fail=self._on_stream_fail)
            self.pluto.set_tx_lo(lo)
            self.stream = st
            st.set_nco(nco)
            st.start()
        except Exception as e:
            log("error", f"TX-Start: {e}")
            self.tx_error = str(e)
            self._teardown("error")

    def _stderr_reader(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            m = re.match(r"pc_pid=(\d+)", line)
            if m:
                self.proc_pid = int(m.group(1))
                continue
            try:
                j = json.loads(line)
            except Exception:
                log("info", line[:300], "runner")
                continue
            if isinstance(j, dict) and "session" in j:
                self.session = j["session"]
                log("info", f"Sitzung {self.session}", "runner")
            elif isinstance(j, dict):
                extra = {k: v for k, v in j.items() if k not in ("t", "lvl", "msg")}
                if "now_playing" in j:
                    self._ingest_now_playing(j["now_playing"], j.get("service"))
                    if j.get("msg") in ("now_playing", "Titel", "track"):
                        log("info", f"Jetzt läuft: {json.dumps(j['now_playing'], ensure_ascii=False)[:200]}", "runner")
                        continue
                if str(j.get("msg", "")).lower() == "status":   # periodic (5 s): keep in state, log once a minute
                    self.runner_status = extra
                    if time.time() - self._status_logged < 60:
                        continue
                    self._status_logged = time.time()
                msg = str(j.get("msg", line))
                if extra:
                    msg += " " + json.dumps(extra, ensure_ascii=False)
                log(j.get("lvl", "info"), msg[:500], "runner")

    def _ingest_now_playing(self, np_, service=None):
        """Runner formats accepted: {service:..}, {svc: {...}, ...}, [ {service:..}, ... ]. Stored per service key."""
        items = []
        if isinstance(np_, list):
            items = [x for x in np_ if isinstance(x, dict)]
        elif isinstance(np_, dict):
            if "title" in np_ or "index" in np_ or "file" in np_ or "service" in np_:
                items = [np_]
            else:
                items = [dict(v, service=k) for k, v in np_.items() if isinstance(v, dict)]
        now = time.time()
        for it in items:
            svc = it.get("service") or service
            if not svc:
                continue
            rec = {"service": svc, "index": it.get("index"), "title": it.get("title", ""), "artist": it.get("artist", ""),
                   "file": it.get("file"), "elapsed_s": it.get("elapsed_s"), "duration_s": it.get("duration_s"),
                   "count": it.get("count"), "playlist": it.get("playlist"), "t": now}
            for k in ("album", "shuffle", "loop", "paused"):
                if k in it:
                    rec[k] = it[k]
            self.now_playing[svc] = rec

    def _on_stream_fail(self, msg):
        self.tx_error = msg
        threading.Thread(target=self._fail_worker, daemon=True, name="tx-fail").start()

    def _fail_worker(self):
        if self.tx_state in ("stopping",):
            return
        self._teardown("error")
        if self.closing or self.user_stopped or not self.args.auto_restart:
            return
        now = time.time()
        self.restart_times = [t for t in self.restart_times if now - t < 600] + [now]
        if len(self.restart_times) > 5:
            log("error", "TX: zu viele Neustarts in 10 min, bleibe gestoppt (Pluto stumm)")
            return
        time.sleep(2)
        if self.user_stopped or self.closing:
            return
        self.restarts += 1
        log("warn", f"TX: automatischer Neustart #{self.restarts}")
        self.tx_start(auto=True)

    def _teardown(self, final):
        with self.lock:
            self.tx_state = "stopping"                      # gain logic now only mutes; device tick stands still
            if self.device is not None:
                self.device["stopping"] = True
                try:
                    r = self.agent.mod_stop()               # agent mutes on stop
                    log("info", f"Gerät: Modulator gestoppt (exit {r.get('exit')})")
                except Exception as e:
                    log("error", f"Gerät: mod/stop fehlgeschlagen: {e}")
                    self._agent_mute()
                self.gain_applied = MUTE_DB
            self.safe_mute()
            st, proc, pid = self.stream, self.proc, self.proc_pid
            if st is not None:
                st.stop_ev.set()
            if proc is not None and pid and self.source == "runner":
                try:
                    self.backend.kill(pid)
                except Exception as e:
                    log("warn", f"Runner-Kill: {e}")
            if proc is not None and self.source != "runner":
                proc.terminate()
            if proc is not None:
                try:
                    proc.wait(3)
                except Exception:
                    pass
                if proc.poll() is None:
                    proc.kill()
            if st is not None:
                st.stop()
                self.last_stats = st.stats()
            self.stream = self.proc = self.proc_pid = None
            self.device = None
            self.now_playing = {}
            self.safe_mute()
            self.tx_state = final
            self.tx_since = time.time()
            log("info" if final == "idle" else "error",
                "TX gestoppt, Pluto stumm (-89,75 dB)" if final == "idle" else f"TX-Fehler, Pluto stumm: {self.tx_error}")

    def hold(self, on):
        """Firmware-Flasher: Pluto freigeben (on=True) bzw. wieder übernehmen."""
        if on:
            self.fw_hold = True
            self.tx_stop()
            try:
                self.pluto.close()
            except Exception:
                pass
            log("info", "Pluto für den Firmware-Flasher freigegeben")
        else:
            self.fw_hold = False
            log("info", "Firmware-Flasher fertig, Pluto wird wieder verbunden")

    def tx_stop(self):
        self.user_stopped = True
        self.sweep_stop.set()
        # Stop während des Starts (z. B. schnell die nächste Karte „Auf Sendung“): erst den Start abwarten, sonst startet
        # der Hintergrund-Thread danach noch den Modulator auf dem Pluto und niemand beendet ihn mehr.
        t = getattr(self, "start_thread", None)
        if t is not None and t.is_alive() and t is not threading.current_thread():
            log("info", "TX-Stopp: warte auf den laufenden Start")
            t.join(timeout=90)
        if self.stream is None and self.proc is None and self.device is None:
            self.safe_mute()
            self.tx_state = "idle"
            return {"ok": True, "state": "idle", "atten_db": self._atten_readback()}
        self._teardown("idle")
        return {"ok": True, "state": "idle", "atten_db": self._atten_readback(), "stats": self.last_stats}

    def _atten_readback(self):
        if self.path() == "device" and self.agent_status is not None:
            try:
                return -float((self._poll_agent() or {}).get("dac", {}).get("gain_db"))
            except Exception:
                return None
        try:
            return -self.pluto.read_gain() if self.pluto.connected else None
        except Exception:
            return None

    # ---------------------------------------------------------------- background loop
    def loop(self):
        last_poll = last_conn = last_mac = 0
        while not self.closing:
            now = time.time()
            try:
                if self.agent is not None and now - self.agent_t > (1.0 if self.device is not None else 2.0):
                    self._poll_agent()
                    if self.agent_status is None:
                        self.agent_t = now                    # retry later, not on every tick
                if self.device is not None:
                    self._device_tick()
                if getattr(self, "fw_hold", False):
                    time.sleep(0.5)
                    continue
                if not self.pluto.connected:
                    if now - last_conn > 5:
                        last_conn = now
                        passive = self.foreign_mod() or self.device is not None
                        if self.pluto.connect(passive=passive):
                            if not passive:
                                self.gain_applied = MUTE_DB
                                try:
                                    lo, _, _ = self.rf_plan(self.cfg["rf"])
                                    self.pluto.set_tx_lo(lo)
                                except Exception as e:
                                    log("warn", f"LO: {e}")
                    if self.stream is not None:
                        self.tx_error = "Pluto getrennt"
                        self._teardown("error")
                elif now - last_poll > 2:
                    last_poll = now
                    if not self.pluto.poll():
                        self.gain_applied = None
                    else:
                        # safety: readback must match what we want
                        want = self.gain_applied
                        rb = self.pluto.info.get("atten_db")
                        hands_off = self.device is not None or self.foreign_mod() or self.pluto.passive
                        if not hands_off and want is not None and rb is not None and abs(-rb - want) > 0.3:
                            log("warn", f"Dämpfung extern verändert ({rb} dB), setze erneut")
                            self.sync_gain(force=True)
                if self.stream is not None:
                    if self.stream.state == "running" and self.tx_state != "running":
                        self.tx_state, self.tx_since = "running", time.time()
                    self.last_stats = self.stream.stats()
                self.sync_gain()
                if now - last_mac > 30:
                    last_mac = now
                    threading.Thread(target=self._check_backend, daemon=True).start()
            except Exception as e:
                log("error", f"Hintergrund: {e}")
                self.safe_mute()
            time.sleep(0.5)

    def _check_backend(self):
        """runner_ok = runner installed AND `selftest` says ok (re-tested every 30 s until ok, then every 5 min)."""
        if self.args.source == "testgen":
            self.runner_installed = self.runner_ok = False
            return
        try:
            avail = self.backend.available()
            ok = False
            if avail:
                # selftest renders a mux (CPU load) -> never while TX runs; after ok only every 10 min when idle
                busy = self.stream is not None or self.tx_state == "starting"
                if (self.selftest or {}).get("ok"):
                    retest = not busy and time.time() - self._selftest_t > 600
                else:
                    retest = not busy or self.selftest is None
                if retest:
                    rc, out, err = self.backend.run(["selftest"], timeout=90)
                    self.selftest = _json_from(out) or {"ok": False, "rc": rc, "stdout": out[-500:], "stderr": err[-500:]}
                    self._selftest_t = time.time()
                ok = bool((self.selftest or {}).get("ok"))
            else:
                self.selftest = None
            if ok != self.runner_ok:
                why = "" if ok else (" (selftest: " + str((self.selftest or {}).get("error", "fehlt"))[:120] + ")" if avail else " (nicht installiert)")
                log("info", f"Runner ({self.backend.name}) {'bereit' if ok else 'nicht bereit → Testgenerator'}{why}")
            self.runner_installed = avail
            self.runner_ok = ok
        except Exception as e:
            self.runner_ok = False
            log("warn", f"Backend-Prüfung: {e}")

    # ---------------------------------------------------------------- state
    def state(self):
        st = self.last_stats or {}
        tx = {"state": self.tx_state, "since": self.tx_since, "session": self.session, "source": self.source,
              "underruns": st.get("underruns", 0), "samples_sent": st.get("samples_sent", 0),
              "rate_ms": st.get("rate_ms"), "rate_avg_ms": st.get("rate_avg_ms"),
              "buffer_ms": st.get("buffer_ms"), "latency_ms": st.get("latency_ms"),
              "queue_ms": st.get("queue_ms"), "kernel_ms": st.get("kernel_ms"),
              "src_stalls": st.get("src_stalls", 0), "src_late": st.get("src_late", 0), "nco_hz": st.get("nco_hz"),
              "error": self.tx_error, "restarts": self.restarts, "ctl_retries": self.pluto.ctl_retries,
              "runner_status": self.runner_status if self.source == "runner" else None,
              "now_playing": self.now_playing if (self.source == "runner" and self.stream is not None) else {}}
        pi = dict(self.pluto.info)
        lo, nco, center = self.rf_plan(self.cfg["rf"])
        pi.update(center_hz=center, nco_enabled=self.nco, min_atten_db=self.args.min_atten,
                  muted=(self.gain_applied == MUTE_DB) if self.gain_applied is not None else None)
        mac = {"reachable": self.runner_installed, "runner": self.runner_ok, "runner_installed": self.runner_installed,
               "tools": self.selftest, "source_mode": self.args.source}
        mac.update(self.backend.describe())
        with _log_lock:
            lg = list(LOG)
        path = self.path()
        pi["path"] = path
        ast = self.agent_status or {}
        dev = {"agent": self.args.agent if self.agent else None, "reachable": self.agent_status is not None,
               "fw": ast.get("fw"), "temp_c": ast.get("temp_c"), "cpu_pct": ast.get("cpu_pct"), "load": ast.get("load"),
               "mod": ast.get("mod"), "dac": ast.get("dac"), "udp": ast.get("udp"),
               "ours": self.device is not None, "foreign": self.foreign_mod(),
               "session": {k: v for k, v in (self.device or {}).items() if k != "unf0"} or None}
        rfst = {"path": path, "path_label": "pluto" if path == "device" else "usb",
                "path_reason": self.cfg.get("_path_reason"), "device": dev, "standard": self.standard,
                "lo_hz": lo, "center_hz": center, "nco_hz": nco, "sample_rate": self.cfg["rf"].get("sample_rate"),
                "warnings": self.cfg.get("_warnings", [])}
        for k in ("ts_rate_bps", "target_bps", "udp_rcvbuf_err", "cpu_pct", "device_temp_c"):
            if k in st:
                tx[k] = st[k]
        tx["path"] = path
        jobs = {k: dict(v) for k, v in self.jobs.items()}
        return {"config": self.public_cfg(), "cu": self.cfg.get("_cu"), "standard": self.standard, "rf": rfst,
                "dvb": self.cfg.get("_dvb"), "jobs": jobs,
                "tx": tx, "pluto": pi, "mac": mac,
                "log": lg, "check": self.check, "monitor": self.monitor, "sweep": self.sweep,
                "onboarding": onboarding_load(),
                "server": {"t": time.time(), "lan": self.args.lan, "port": self.args.port, "pid": os.getpid(),
                           "runtime": RUNTIME, "rx_uri": self.args.rx_uri, "rx_mode": self.args.rx_mode}}

    # ---------------------------------------------------------------- config API
    def put_config(self, body):
        c, errs = validate_config(body)
        if errs:
            return 400, {"ok": False, "errors": errs}
        with self.lock:
            old = self.public_cfg()
            self.cfg = c
            self.save_config()
            restart = False
            if self.stream is not None:
                o = {k: v for k, v in old.items() if k != "rf"}
                n = {k: v for k, v in self.public_cfg().items() if k != "rf"}
                restart = o != n or old["rf"].get("sample_rate") != c["rf"].get("sample_rate") or \
                    old["rf"].get("backoff_db") != c["rf"].get("backoff_db") or old["rf"].get("cfr") != c["rf"].get("cfr")
            rf = self.apply_rf()
        return 200, {"ok": True, "restart_required": restart, "rf": rf, "cu": c["_cu"], "standard": c["_standard"],
                     "warnings": c.get("_warnings", [])}

    def post_rf(self, body):
        if not isinstance(body, dict):
            return 400, {"ok": False, "errors": ["JSON-Objekt erwartet"]}
        allowed = {"channel", "freq_hz", "offset_hz", "lo_offset_hz", "atten_db", "enabled"}
        unknown = set(body) - allowed
        errs = [f"unbekanntes Feld {k}" for k in sorted(unknown)]
        upd = {k: v for k, v in body.items() if k in allowed}
        std = self.standard
        if "freq_hz" in upd and "channel" not in upd:
            upd["channel"] = "custom"
        newrf = validate_rf(upd, errs, base=self.cfg["rf"], std=std)
        if errs:
            return 400, {"ok": False, "errors": errs}
        if upd.get("enabled") is True:
            why = tx_permission(self.public_cfg())
            if why:
                log("warn", f"Senden abgelehnt: {why}")
                return 403, {"ok": False, "errors": [why], "onboarding": onboarding_load()}
        restart = False
        with self.lock:
            if std in standards.DVB or std == "pal":
                # channel/frequency feed NIT + sample rate + path: re-validate the whole config
                cand = self.public_cfg()
                cand["rf"] = dict(newrf)
                sec = cand.setdefault(std, {})
                if std == "dvbs" and isinstance(sec, dict):
                    if sec.get("lof_mhz") is not None:
                        sec["transponder_mhz"] = round(newrf["freq_hz"] / 1e6 + float(sec["lof_mhz"]), 3)
                    sec.pop("frequency_hz", None)
                elif std == "pal":
                    for k in ("lo_hz", "lo_hz_live", "sample_rate"):
                        cand["rf"].pop(k, None)
                    if newrf["channel"] != "custom" and "channel" in upd:
                        cand["rf"].pop("freq_hz", None)
                elif isinstance(sec, dict):
                    sec["channel"] = newrf["channel"]
                    if newrf["channel"] == "custom":
                        sec["freq_hz"] = newrf["freq_hz"]
                    else:
                        sec.pop("freq_hz", None)
                        cand["rf"].pop("freq_hz", None)
                c, e2 = validate_config(cand)
                if e2:
                    return 400, {"ok": False, "errors": e2}
                old_path, old_sr = self.path(), self.cfg["rf"].get("sample_rate")
                self.cfg = c
                newrf = c["rf"]
                restart = (self.stream is not None or self.device is not None) and                     (c["_path"] != old_path or c["rf"].get("sample_rate") != old_sr or (std != "pal" and ("channel" in upd or "freq_hz" in upd)))
            else:
                self.cfg["rf"] = newrf
                if std == "fm":                               # keep fm.freq_mhz in sync (the runner reads it)
                    self.cfg.setdefault("fm", {})["freq_mhz"] = round(newrf["freq_hz"] / 1e6, 3)
            self.save_config()
            try:
                r = self.apply_rf()
            except Exception as e:
                return 500, {"ok": False, "error": str(e)}
        log("info", f"RF: {newrf['channel']} {newrf['freq_hz'] / 1e6:.3f} MHz, Versatz {newrf['offset_hz']} Hz, "
                    f"Dämpfung {newrf['atten_db']} dB, {'an' if newrf['enabled'] else 'stumm'}")
        return 200, {"ok": True, "rf": newrf, "applied": r, "restart_required": restart,
                     "note": "NIT/Delivery-Deskriptor ändert sich erst nach Neustart der TS-Kette" if restart else None}

    # ---------------------------------------------------------------- runner commands
    LIVE_KEYS = {"dab": {"dls", "slide", "announcement", "label", "playlist", "pty"},
                 "fm": {"rds", "fm", "second_carrier", "playlist", "dls", "pty", "label"},
                 "dvbt": {"eit", "name", "lcn", "playlist", "status"}, "dvbc": {"eit", "name", "lcn", "playlist", "status"},
                 "dvbs": {"eit", "name", "lcn", "playlist", "status"}, "pal": set()}
    RDS_KEYS = {"ps", "rt", "rtplus", "ta", "tp", "pty", "ms", "ct", "ptyn", "af", "af_method", "af_regional", "lps", "eon", "eon_ta",
                "rt_auto"}   # wie runner/fm/fm_tx.py (live)
    FM_KEYS = {"audio_gain_db", "mute", "deviation_khz", "pilot_khz", "rds_level_khz", "mpx_power_dbr"}

    def live_ctl(self, body):
        if not isinstance(body, dict) or not body:
            return 400, {"ok": False, "errors": ["JSON-Objekt mit Befehl erwartet"]}
        std = self.standard
        bad = sorted(set(body) - self.LIVE_KEYS.get(std, set()))
        if bad:
            return 400, {"ok": False, "errors": [f"Live-Schlüssel {bad} nicht für Standard {std}"]}
        if "rds" in body:
            r = body["rds"]
            errs = []
            if not isinstance(r, dict) or not r:
                errs.append("rds: Objekt mit Feldern")
            else:
                errs += [f"rds.{k} unbekannt" for k in r if k not in self.RDS_KEYS]
                if "ps" in r and len(str(r["ps"])) > 8:
                    errs.append("rds.ps: max. 8 Zeichen")
                if "rt" in r and len(str(r["rt"])) > 64:
                    errs.append("rds.rt: max. 64 Zeichen")
                if "pty" in r and not (isinstance(r["pty"], int) and 0 <= r["pty"] <= 31):
                    errs.append("rds.pty: 0–31")
                for k in ("ta", "tp", "ms", "ct", "rt_auto"):
                    if k in r and not isinstance(r[k], bool):
                        errs.append(f"rds.{k}: true/false")
                if "eon_ta" in r and not (isinstance(r["eon_ta"], dict) and "pi" in r["eon_ta"]):
                    errs.append("rds.eon_ta: {pi, active}")
            if errs:
                return 400, {"ok": False, "errors": errs}
        if "fm" in body:
            r = body["fm"]
            if not isinstance(r, dict) or set(r) - self.FM_KEYS:
                return 400, {"ok": False, "errors": [f"fm: erlaubt {sorted(self.FM_KEYS)}"]}
        if isinstance(body, dict) and "playlist" in body:
            pc = body["playlist"] or {}
            errs = []
            if pc.get("action") not in ("next", "prev", "jump", "pause", "resume"):
                errs.append("playlist.action: next|prev|jump")
            if pc.get("action") == "jump" and not isinstance(pc.get("index"), int):
                errs.append("playlist.index: Ganzzahl")
            if not pc.get("service"):
                errs.append("playlist.service fehlt")
            if errs:
                return 400, {"ok": False, "errors": errs}
        if self.source != "runner" or not self.session:
            return 409, {"ok": False, "error": "keine Runner-Live-Sitzung (TX aus oder Testgenerator)"}
        try:
            rc, out, err = self.backend.run(["ctl", "--session", self.session, json.dumps(body, ensure_ascii=False)],
                                            timeout=30)
        except Exception as e:
            return 502, {"ok": False, "error": str(e)}
        j = _json_from(out)
        if j is None:
            return 502, {"ok": False, "error": f"rc={rc}", "stderr": err[-800:], "stdout": out[-800:]}
        log("info", f"live: {json.dumps(body, ensure_ascii=False)[:200]} → {json.dumps(j)[:120]}")
        return 200, j

    def run_check(self, body):
        if not self.backend.available():
            return 503, {"ok": False, "error": f"Runner ({self.backend.name}) nicht installiert"}
        try:
            secs = int(float((body or {}).get("seconds", 20)))
        except (TypeError, ValueError):
            secs = -1
        if not 2 <= secs <= 600:                      # sonst Timeout-Überlauf („timestamp out of range“) bzw. leerer Lauf
            return 400, {"ok": False, "errors": ["seconds: 2–600"]}
        self.save_config()
        log("info", f"Prüfung startet ({secs} s)")
        try:
            self.backend.put(CONFIG_FILE)
            rc, out, err = self.backend.run(["check", "--config", self.backend.rpath(CONFIG_FILE),
                                             "--seconds", secs], timeout=secs * 10 + 120)
        except Exception as e:
            return 502, {"ok": False, "error": str(e)}
        j = _json_from(out) or {"ok": False, "rc": rc, "stderr": err[-2000:], "stdout": out[-2000:]}
        j.setdefault("t", time.time())
        j.setdefault("mode", self.standard)
        self.check = j
        summary = f"sync={j.get('sync')}" if "sync" in j else ", ".join(
            f"{k}={v}" for k, v in (j.get("measured") or {}).items() if isinstance(v, (int, float, str)))[:160]
        mm = j.get("mismatches")
        log("info" if j.get("ok") else "warn", f"Prüfung ({j.get('mode')}): ok={j.get('ok')} {summary}"
            + (f" Abweichungen: {len(mm)}" if mm else ""))
        return 200, j

    # ---------------------------------------------------------------- RX monitor
    def run_monitor(self, body):
        body = body or {}
        secs = float(body.get("seconds", 8))
        gain = float(body.get("rx_gain_db", 30))
        if not 0.5 <= secs <= 30:
            return 400, {"ok": False, "errors": ["seconds: 0,5–30"]}
        if self.foreign_mod():
            return 409, {"ok": False, "error": "Auf dem Pluto läuft ein fremder Modulator – kein Kontrollempfang"}
        if self.device is not None or self.path() == "device":
            return self.run_monitor_device(body)
        if not self.pluto.connected:
            return 503, {"ok": False, "error": "Pluto nicht verbunden"}
        if not self.monitor_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "Mitschnitt läuft bereits"}
        try:
            rf = self.cfg["rf"]
            _, _, center = self.rf_plan(rf)
            if self.stream is None:
                self.pluto.set_rate(int(rf["sample_rate"]))
            un0 = self.stream.underruns if self.stream else None
            corr_backoff = float(rf.get("backoff_db", 12)) if (self.source == "testgen" and self.stream is not None) else None
            os.makedirs(RXDIR, exist_ok=True)
            name = "mon_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".cf32.iq"
            path = os.path.join(RXDIR, name)
            how = "proc:" + self.args.rx_uri
            if self.args.rx_mode == "inproc":
                how = "inproc:" + self.pluto.uri
                res = self._monitor_inproc(secs, gain, center, path, corr_backoff)
            else:
                try:
                    # separate process + own context over ip: -> no GIL/USB contention with the TX stream
                    res = rxproc.capture(self.args.rx_uri, secs, gain, center, path, corr_backoff=corr_backoff)
                except Exception as e:
                    if self.args.rx_mode == "proc":
                        raise
                    log("warn", f"RX-Prozess über {self.args.rx_uri} fehlgeschlagen ({e}), nehme In-Prozess-Mitschnitt")
                    how = "inproc:" + self.pluto.uri
                    res = self._monitor_inproc(secs, gain, center, path, corr_backoff)
            un1 = self.stream.underruns if self.stream else None
            fs, spec, corr = res["fs_capture"], res["spectrum"], res.get("testgen_corr")
            in_db, adj_db = spec["in_band_db"], spec["adjacent_db"]
            t_cap = res["capture_s"]
            self.pluto.info["rx_lo_hz"] = res.get("rx_lo_hz")
            os.makedirs(RXDIR, exist_ok=True)
            name = "mon_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".cf32.iq"
            old = sorted(n for n in os.listdir(RXDIR) if n.startswith("mon_") and n.endswith(".cf32.iq"))
            for n in old[:-5]:
                for q in (n, n + ".report"):
                    q = os.path.join(RXDIR, q)
                    try:
                        shutil.rmtree(q) if os.path.isdir(q) else os.remove(q)
                    except Exception:
                        pass
            rep = {"ok": True, "t": time.time(), "seconds": secs, "rx_gain_db": gain,
                   "rx_lo_hz": res.get("rx_lo_hz"), "center_hz": center,
                   "channel": rf["channel"], "fs_capture": fs, "fs_file": int(round(fs / 2)),
                   "capture_s": round(t_cap, 2), "file": os.path.relpath(path, ROOT).replace("\\", "/"),
                   "tx_running": self.stream is not None, "rx_path": how,
                   "tx_underruns_during": (un1 - un0) if un0 is not None else None, "spectrum": spec,
                   "testgen_corr": corr}
            if self.backend.available():
                try:
                    self.backend.put(path)
                    args = ["decode", "--iq", self.backend.rpath(path), "--seconds", int(secs)]
                    try:                                    # Soll/Ist-Vergleich, FM: --mode fm (runner/README.md, UKW)
                        self.save_config()
                        self.backend.put(CONFIG_FILE)
                        args += ["--config", self.backend.rpath(CONFIG_FILE)]
                    except Exception:
                        pass
                    if self.standard == "fm":
                        args += ["--mode", "fm"]
                    rc, out, err = self.backend.run(args, timeout=int(secs) * 10 + 120)
                    rep["decode"] = _json_from(out) or {"ok": False, "rc": rc, "stderr": err[-2000:]}
                except Exception as e:
                    rep["decode"] = {"ok": False, "error": str(e)}
            else:
                rep["decode"] = {"ok": False, "skipped": True,
                                 "error": f"Runner ({self.backend.name}) nicht installiert – nur Spektrum"}
            rep["standard"] = self.standard
            self.monitor = {k: v for k, v in rep.items()}
            log("info", f"Monitor: Band {in_db:.1f} dB, daneben {adj_db:.1f} dB, Abstand {in_db - adj_db:.1f} dB, "
                        f"RMS {spec['rms_dbfs']} dBFS")
            return 200, rep
        except Exception as e:
            log("error", f"Monitor: {e}")
            return 500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-1500:]}
        finally:
            self.monitor_lock.release()

    def run_monitor_device(self, body):
        """Kontrollempfang im Gerätepfad: iio_readdev on the Pluto (RX LO = TX LO, RX rate = TX rate, TX untouched),
        transfer via SSH, spectrum + standard-specific decoder (rxdecode.py)."""
        import rxdecode
        body = body or {}
        std = self.standard
        secs = float(body.get("seconds", 0) or 0)
        gain = float(body.get("rx_gain_db", 50))
        dev = self.devssh()
        if dev is None:
            return 409, {"ok": False, "error": "Geräte-RX braucht SSH zum Pluto (--no-device-ssh gesetzt)"}
        if not self.monitor_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "Mitschnitt läuft bereits"}
        try:
            st = self._poll_agent() or {}
            rx_rate = int(((st.get("dac") or {}).get("rx_rate_hz")) or self.cfg["rf"].get("sample_rate") or 4096000)
            lo, _, center = self.rf_plan(self.cfg["rf"])
            # gr-dtv needs >1 s of DVB-T before its deinterleaver/RS chain delivers packets (4,8 M samples gave 0 packets)
            # ffmpeg repeats the SDT every 0,5 s: DVB-C/S captures cover >= 0,6 s so the service names are inside
            default_n = {"dvbt": 12_000_000, "dvbc": 6_000_000, "dvbs": 13_500_000, "pal": 4_000_000}.get(std, 2_000_000)
            n = int(secs * rx_rate) if secs > 0 else default_n
            n = max(200_000, min(n, 16_000_000))
            un0 = self.last_stats.get("underruns", 0)
            bw_default = {"dvbt": int((self.cfg.get("dvbt") or {}).get("bandwidth_mhz") or 8) * 1000000, "pal": 8000000,
                          "dvbc": 8000000,
                          "dvbs": int(min(40e6, int((self.cfg.get("dvbs") or {}).get("symbol_rate") or 22000000) * 1.35))}.get(std)
            blob, info = dev.rx_capture(lo, n, gain, rx_bw_hz=body.get("rx_bw_hz", bw_default))
            os.makedirs(RXDIR, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(RXDIR, f"mon_{stamp}_{std}.iq")
            with open(path, "wb") as f:
                f.write(blob)
            raw = np.frombuffer(blob, "<i2").astype(np.float32)
            x = ((raw[0::2] + 1j * raw[1::2]) / 2048.0).astype(np.complex64)
            fsr = info.get("rx_rate_hz") or rx_rate
            if std == "dvbt":
                bwm = int((self.cfg.get("dvbt") or {}).get("bandwidth_mhz") or 8)
                bands = ((50e3, 3.7e6 * bwm / 8), (4.15e6 * bwm / 8, 0.49 * fsr))
            elif std in ("dvbc", "dvbs"):
                srx = int((self.cfg.get(std) or {}).get("symbol_rate") or fsr)
                bands = ((50e3, 0.4 * srx), (0.62 * srx, 0.49 * fsr) if 0.62 * srx < 0.49 * fsr else None)
            elif std == "pal":
                bands = ((2.0e6, 2.5e6), (3.6e6, 3.9e6))        # around the vision carrier (-2,25 MHz) vs. empty edge
            else:
                bands = ((5e3, 760e3), (900e3, None))
            spec = rxproc.spectrum_metrics(x[:min(len(x), 1 << 21)], fsr, bands[0], bands[1])
            clip = float(np.mean(np.abs(raw) >= 2047) * 100)
            del x, raw
            t0 = time.time()
            png = os.path.join(RXDIR, f"mon_{stamp}_{std}.png") if std == "pal" else None
            cfg = self.runner_config()
            try:
                dec = rxdecode.decode(std, path, cfg, info.get("rx_rate_hz") or rx_rate, self.args.wsl_distro, png=png,
                                      rx_fir=info.get("rx_fir", 1))
            except Exception as e:
                dec = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            dec["decode_s"] = round(time.time() - t0, 1)
            self._poll_agent()
            un1 = self.last_stats.get("underruns", 0)
            rep = {"ok": True, "t": time.time(), "standard": std, "path": "device", "rx_path": "device:iio_readdev",
                   "rx_gain_db": gain, "rx_lo_hz": info.get("rx_lo_hz"), "center_hz": center, "tx_lo_hz": lo,
                   "fs_capture": info.get("rx_rate_hz") or rx_rate, "samples": n, "capture": info,
                   "file": os.path.relpath(path, ROOT).replace("\\", "/"), "clip_pct": round(clip, 4),
                   "tx_running": self.device is not None, "tx_underruns_during": (un1 - un0) if self.device else None,
                   "spectrum": spec, "decode": dec}
            if png and dec.get("png"):
                rep["png"] = os.path.relpath(png, ROOT).replace("\\", "/")
                dec["png"] = rep["png"]          # das Dashboard liest decode.png – relativ, nicht als Laufwerkspfad
            old = sorted(f for f in os.listdir(RXDIR) if f.startswith("mon_") and f.endswith(".iq"))
            for fn in old[:-6]:
                for q in [fn] + [fn[:-3] + ext for ext in (".ts", ".norm.ts", ".conj.ts", ".dvbc.json", ".png")]:
                    try:
                        os.remove(os.path.join(RXDIR, q))
                    except OSError:
                        pass
            self.monitor = rep
            log("info" if dec.get("ok") else "warn",
                f"Monitor ({std}, Gerät): lock={dec.get('lock')} Dienste={[s.get('name') for s in dec.get('services') or []]} "
                f"{'; '.join(dec.get('mismatches') or [])[:160]}")
            return 200, rep
        except Exception as e:
            log("error", f"Monitor (Gerät): {e}")
            return 500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-1500:]}
        finally:
            self.monitor_lock.release()

    def _monitor_inproc(self, secs, gain, center, path, corr_backoff):
        """Old path: capture in this process on the server's own context (costs TX underruns)."""
        t0 = time.time()
        x, fs = self.pluto.rx_capture(secs, gain, center, nbuf=rxproc.RX_NBUF)
        t_cap = time.time() - t0
        spec = rxproc.spectrum_metrics(x, fs)
        y = resample_poly(x, 1, 2).astype(np.complex64)
        del x
        corr = None
        if corr_backoff is not None:
            try:
                corr = rxproc.testgen_correlation(y, fs, corr_backoff)
            except Exception as e:
                corr = {"error": str(e)}
        y.tofile(path)
        return {"fs_capture": fs, "capture_s": round(t_cap, 2), "rx_lo_hz": self.pluto.info.get("rx_lo_hz"),
                "spectrum": spec, "testgen_corr": corr}

    # ---------------------------------------------------------------- sweep
    def start_sweep(self, body):
        body = body or {}
        try:
            a, b = float(body.get("from_db", 40)), float(body.get("to_db", 89))
            step, dwell = abs(float(body.get("step_db", 1))), float(body.get("dwell_s", 5))
        except Exception:
            return 400, {"ok": False, "errors": ["Zahlen erwartet"]}
        errs = []
        if not (0 <= a <= 89.75 and 0 <= b <= 89.75):
            errs.append("from_db/to_db: 0–89,75")
        if not 0.25 <= step <= 30:
            errs.append("step_db: 0,25–30")
        if not 0.2 <= dwell <= 3600:
            errs.append("dwell_s: 0,2–3600")
        if errs:
            return 400, {"ok": False, "errors": errs}
        if self.sweep.get("running"):
            return 409, {"ok": False, "error": "Sweep läuft bereits"}
        n = int(abs(b - a) / step) + 1
        vals = [round(a + (step if b >= a else -step) * i, 2) for i in range(n)]
        self.sweep_stop.clear()
        self.sweep = {"running": True, "from_db": a, "to_db": b, "step_db": step, "dwell_s": dwell,
                      "index": 0, "count": n, "atten_db": None, "started": time.time()}
        threading.Thread(target=self._sweep_worker, args=(vals, dwell), daemon=True, name="sweep").start()
        return 200, {"ok": True, "steps": n, "duration_s": n * dwell}

    def _sweep_worker(self, vals, dwell):
        orig = self.cfg["rf"]["atten_db"]
        log("info", f"Sweep {vals[0]} → {vals[-1]} dB, {len(vals)} Stufen à {dwell} s")
        try:
            for i, v in enumerate(vals):
                if self.sweep_stop.is_set():
                    break
                with self.lock:
                    self.cfg["rf"]["atten_db"] = v
                    self.apply_rf()
                self.sweep.update(index=i + 1, atten_db=v)
                if self.sweep_stop.wait(dwell):
                    break
        except Exception as e:
            log("error", f"Sweep: {e}")
            self.safe_mute()
        finally:
            with self.lock:
                self.cfg["rf"]["atten_db"] = orig
                try:
                    self.apply_rf()
                except Exception:
                    self.safe_mute()
                self.save_config()
            self.sweep["running"] = False
            self.sweep["finished"] = time.time()
            log("info", f"Sweep beendet, Dämpfung zurück auf {orig} dB")

    # ---------------------------------------------------------------- media / presets
    # ---------------------------------------------------------------- jobs (YouTube)
    YT_STATUS = {"downloading": "running", "started": "running", "running": "running", "merging": "running",
                 "postprocessing": "running", "finished": "done", "done": "done", "cached": "done",
                 "error": "error", "queued": "queued"}

    def youtube_start(self, body):
        body = body or {}
        url = str(body.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            return 400, {"ok": False, "errors": ["url: http(s)-Adresse erwartet"]}
        mh = body.get("max_height", body.get("quality"))
        try:
            mh = int(re.sub(r"[^0-9]", "", str(mh))) if mh not in (None, "", "best") else 1080
        except ValueError:
            mh = 1080
        mh = max(144, min(2160, mh))
        if not self.backend.available():
            return 503, {"ok": False, "error": f"Runner ({self.backend.name}) nicht verfügbar"}
        for j in self.jobs.values():
            if j["url"] == url and j["status"] in ("queued", "running"):
                return 200, {"ok": True, "job": j["job"], "existing": True}
        self._job_n += 1
        jid = f"yt{int(time.time())}-{self._job_n}"
        self.jobs[jid] = {"job": jid, "kind": "youtube", "url": url, "max_height": mh, "status": "queued",
                          "percent": 0.0, "speed": None, "eta_s": None, "downloaded_bytes": None, "total_bytes": None,
                          "file": None, "title": None, "error": None, "t_start": time.time(), "t_end": None}
        for k in sorted(self.jobs, key=lambda k: self.jobs[k]["t_start"])[:-20]:
            if self.jobs[k]["status"] not in ("queued", "running"):
                self.jobs.pop(k, None)
        args = ["media", "youtube", url, "--max-height", mh] + (["--force"] if body.get("force") else [])
        threading.Thread(target=self._youtube_worker, args=(jid, args), daemon=True, name="youtube").start()
        log("info", f"YouTube-Job {jid}: {url} (≤ {mh}p)")
        return 200, {"ok": True, "job": jid}

    @staticmethod
    def _media_rel(f):
        if not f:
            return f
        f = str(f).replace("\\", "/")
        i = f.rfind("/media/")
        if i >= 0:
            return f[i + 1:]
        return f if f.startswith("media/") else "media/video/" + f.rsplit("/", 1)[-1]

    def _youtube_worker(self, jid, args):
        job = self.jobs[jid]
        try:
            proc = self.backend.popen(args)
        except Exception as e:
            job.update(status="error", error=str(e), t_end=time.time())
            return
        job["status"] = "running"
        out_chunks = []
        t_out = threading.Thread(target=lambda: out_chunks.append(proc.stdout.read()), daemon=True)
        t_out.start()
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            try:
                j = json.loads(line)
            except Exception:
                continue
            y = j.get("youtube") if isinstance(j, dict) else None
            if isinstance(y, dict):
                st = self.YT_STATUS.get(str(y.get("status", "")).lower(), "running")
                upd = {"percent": y.get("percent", job["percent"]), "speed": y.get("speed_bps", y.get("speed")),
                       "eta_s": y.get("eta_s"), "downloaded_bytes": y.get("downloaded_bytes"),
                       "total_bytes": y.get("total_bytes")}
                if y.get("file"):
                    upd["file"] = self._media_rel(y["file"])
                if y.get("title"):
                    upd["title"] = y["title"]
                job.update({k: v for k, v in upd.items() if v is not None})
                job["status"] = "running" if st == "done" else st      # done only after the final stdout result
            elif isinstance(j, dict) and j.get("lvl") == "error":
                job["error"] = j.get("msg")
        proc.wait()
        t_out.join(5)
        res = _json_from((out_chunks[0] if out_chunks else b"").decode("utf-8", "replace")) or {}
        if proc.returncode == 0 and res.get("ok", True) and (res.get("file") or job.get("file")):
            job.update(status="done", percent=100.0, eta_s=0, file=self._media_rel(res.get("file") or job["file"]),
                       title=res.get("title") or job.get("title"), duration_s=res.get("duration_s"),
                       width=res.get("width"), height=res.get("height"), cached=res.get("cached"),
                       license=res.get("license"), id=res.get("id"))
            log("info", f"YouTube-Job {jid} fertig: {job['file']} ({job.get('title')})")
        else:
            job.update(status="error", error=res.get("error") or job.get("error") or f"rc {proc.returncode}")
            log("warn", f"YouTube-Job {jid} fehlgeschlagen: {job['error']}")
        job["t_end"] = time.time()

    @staticmethod
    def _safe_name(name):
        name = os.path.basename(str(name).replace("\\", "/"))
        return re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")

    def upload(self, qs, data, ctype=""):
        """Body = raw bytes (name=.. in the query) or multipart/form-data with one or more files.
        ?playlist=<name> appends uploaded audio files to that playlist (created if missing)."""
        kind = (qs.get("kind") or ["audio"])[0]
        if kind not in ("audio", "slide", "logo", "video"):
            return 400, {"ok": False, "errors": ["kind: audio|slide|logo|video"]}
        target = os.path.join(MEDIA, VIDEO_SUB) if kind == "video" else MEDIA
        prefix = "media/video/" if kind == "video" else "media/"
        files = []
        if ctype.lower().startswith("multipart/form-data"):
            try:
                files = parse_multipart(ctype, data)
            except Exception as e:
                return 400, {"ok": False, "errors": [f"multipart: {e}"]}
        else:
            names = qs.get("name") or []
            if len(names) != 1:
                return 400, {"ok": False, "errors": ["name fehlt (ein name= je Rohdaten-Upload, mehrere Dateien per multipart)"]}
            files = [(names[0], data)]
        if not files:
            return 400, {"ok": False, "errors": ["keine Datei"]}
        os.makedirs(target, exist_ok=True)
        out, errs = [], []
        for name, blob in files:
            name = self._safe_name(name)
            if not name:
                errs.append("Dateiname fehlt")
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if kind == "audio" and ext not in playlists.AUDIO_EXT:
                errs.append(f"{name}: kein Audioformat")
                continue
            if kind == "video" and ext not in standards.MediaIndex.VIDEO:
                errs.append(f"{name}: kein Videoformat ({', '.join(standards.MediaIndex.VIDEO)})")
                continue
            path = os.path.join(target, name)
            with open(path, "wb") as f:
                f.write(blob)
            item = {"file": prefix + name, "bytes": len(blob), "kind": kind}
            if self.backend.name == "ssh":
                try:
                    self.backend.put(path)
                    item["remote"] = True
                except Exception as e:
                    item["remote"] = False
                    item["warning"] = str(e)
            out.append(item)
            log("info", f"Upload {kind}: {prefix}{name} ({len(blob)} B)")
        res = {"ok": bool(out), "files": out, "errors": errs}
        if out:                                           # backwards compatible single-file fields
            res.update(out[0])
        plname = (qs.get("playlist") or [None])[0]
        if plname and out and kind == "audio":
            try:
                pl = PLSTORE.load(plname) if PLSTORE.exists(plname) else {"name": plname, "items": []}
                pl["items"] += [{"file": o["file"]} for o in out]
                res["playlist"] = PLSTORE.save(plname, pl)
            except Exception as e:
                res["errors"].append(f"Playlist: {e}")
        return (200 if out else 400), res

    def media_list(self):
        out, missing = [], []
        names = []
        if os.path.isdir(MEDIA):
            names = sorted(os.listdir(MEDIA))
            vd = os.path.join(MEDIA, VIDEO_SUB)
            if os.path.isdir(vd):
                names += [VIDEO_SUB + "/" + n for n in sorted(os.listdir(vd))
                          if not n.endswith((".json", ".part", ".ytdl", ".tmp"))]
            for n in names:
                p = os.path.join(MEDIA, n)
                if os.path.isfile(p):
                    st = os.stat(p)
                    kind = standards.MediaIndex.kind_of(n)
                    item = {"name": n, "file": "media/" + n, "size": st.st_size, "kind": kind, "mtime": st.st_mtime}
                    if kind in ("audio", "video"):
                        info = self.media_index.get(n, st)
                        if info is None:
                            missing.append(n)
                            item["probed"] = False
                        else:
                            item.update({k: v for k, v in info.items() if k != "t"})
                            item["probed"] = True
                    out.append(item)
        if missing:
            self.media_index.scan_async(missing)
        return {"ok": True, "media": out, "probing": len(missing), "ffprobe": bool(self.media_index.probe_cmd())}

    def media_delete(self, name):
        name = unquote(name).replace("\\", "/")
        sub = ""
        if name.startswith(VIDEO_SUB + "/"):
            sub, name = VIDEO_SUB + "/", name[len(VIDEO_SUB) + 1:]
        name = sub + self._safe_name(name)
        p = os.path.join(MEDIA, name)
        if not name or not os.path.isfile(p):
            return 404, {"ok": False, "error": "Datei nicht gefunden"}
        used = []
        for pl in PLSTORE.names():
            try:
                if any(i["file"] == "media/" + name for i in PLSTORE.load(pl)["items"]):
                    used.append(pl)
            except Exception:
                pass
        in_cfg = ("media/" + name) in json.dumps(self.public_cfg(), ensure_ascii=False)
        os.remove(p)
        self.media_index.forget(name)
        log("info", f"Medium gelöscht: media/{name}" + (f" (in Playlists {used})" if used else ""))
        return 200, {"ok": True, "file": "media/" + name, "playlists": used, "in_config": in_cfg}


def parse_multipart(ctype, data):
    """Minimal multipart/form-data parser -> [(filename, bytes)] for parts that carry a filename."""
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        raise ValueError("boundary fehlt")
    b = b"--" + m.group(1).encode()
    files = []
    for part in data.split(b)[1:]:
        if part[:2] == b"--":
            break
        part = part[2:] if part[:2] == b"\r\n" else part
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        if body.endswith(b"\r\n"):
            body = body[:-2]
        hm = re.search(rb'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]*)"?', head, re.I)
        if hm:
            fn = unquote(hm.group(1).decode("utf-8", "replace"))
            files.append((fn, body))
    return files


def _preset_path(name):
    name = unquote(name)
    if not re.fullmatch(r"[\w .()+-]{1,64}", name) or name.startswith("."):
        return None
    return os.path.join(PRESETS, name + ".json")


# ============================================================================ HTTP
APP = None
SETUP = None
FLASHER = None


class Handler(BaseHTTPRequestHandler):
    server_version = "DABLab/1.0"

    def log_message(self, fmt, *a):
        pass

    # ---------------------------------------------------------------- helpers
    LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
    _lan_hosts = None

    @classmethod
    def allowed_hosts(cls):
        """localhost, im LAN-Modus zusätzlich Rechnername und alle eigenen IP-Adressen dieses PCs (einmal ermittelt)."""
        if not APP.args.lan:
            return cls.LOCAL_HOSTS
        if cls._lan_hosts is None:
            hs = set(cls.LOCAL_HOSTS)
            try:
                name = socket.gethostname()
                hs |= {name.lower(), socket.getfqdn().lower()}
                for fam, _, _, _, sa in socket.getaddrinfo(name, None):
                    hs.add(sa[0] if fam == socket.AF_INET else f"[{sa[0]}]")
            except OSError:
                pass
            try:                                      # Adresse der Standardroute (falls der Name nicht alle IPs liefert)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
                    u.connect(("10.255.255.255", 1))
                    hs.add(u.getsockname()[0])
            except OSError:
                pass
            cls._lan_hosts = hs
        return cls._lan_hosts

    def _foreign_request(self, write):
        """True (und 403 gesendet), wenn die Anfrage nicht vom eigenen Dashboard stammt:
        - Host muss localhost sein (außer --lan) – schützt gegen DNS-Rebinding fremder Seiten,
        - schreibende Anfragen: Origin (falls vorhanden) muss genau dieser Host sein, Sec-Fetch-Site nicht „cross-site“ –
          sonst könnte jede im Browser geöffnete Webseite z. B. /api/tx/start auslösen (CSRF)."""
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        bad = None
        if hostname not in self.allowed_hosts():   # auch mit --lan nur eigene Namen/IPs (DNS-Rebinding)
            bad = f"Host {host!r}"
        elif write:
            origin = (self.headers.get("Origin") or "").strip().lower()
            if origin and origin != "null" and urlparse(origin).netloc != host:
                bad = f"Origin {origin!r}"
            elif origin == "null" or self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
                bad = "cross-site"
        if bad:
            log("warn", f"Anfrage abgelehnt (fremde Herkunft: {bad}) {self.command} {self.path[:80]}")
            self._send(403, {"ok": False, "error": "Anfrage von fremder Herkunft abgelehnt"})
            return True
        return False

    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self, limit=512 * 1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("zu groß")
        data = b""
        while len(data) < n:
            chunk = self.rfile.read(min(1 << 20, n - len(data)))
            if not chunk:
                break
            data += chunk
        return data

    def _json(self):
        raw = self._body(8 * 1024 * 1024)
        if not raw.strip():
            return {}
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            txt = raw.decode("cp1252")
        return json.loads(txt)

    # ---------------------------------------------------------------- dispatch
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self._foreign_request(write=False):
            return
        try:
            u = urlparse(self.path)
            p = u.path
            if p == "/api/state":
                return self._send(200, APP.state())
            if p == "/api/onboarding":
                return self._send(200, onboarding_load())
            if p == "/api/logs":
                q = parse_qs(u.query)
                return self._send(200, logs_api(q.get("file", [""])[0], q.get("lang", ["de"])[0], q.get("level", [""])[0]))
            if p == "/api/hwcheck":
                q = parse_qs(u.query)
                return self._send(200, hwcheck.detect(bench_run=q.get("bench", ["0"])[0] == "1"))
            if p == "/api/firmware":
                return self._send(200, FLASHER.status())
            if p == "/api/setup":
                return self._send(200, SETUP.status(fresh=parse_qs(u.query).get("fresh", ["0"])[0] == "1"))
            if p == "/api/shop":
                q = parse_qs(u.query)
                return self._send(200, shopmod.shop(q.get("market", ["de"])[0], q.get("lang", ["de"])[0], q.get("ebay", [""])[0]))
            if p == "/api/events":
                return self._sse()
            if p == "/api/media":
                return self._send(200, APP.media_list())
            if p == "/api/channels" or p == "/channels.json":
                return self._send(200, standards.CHANNELS)
            if p == "/api/jobs":
                return self._send(200, {"ok": True, "jobs": APP.jobs})
            if p.startswith("/api/youtube/"):
                j = APP.jobs.get(unquote(p[len("/api/youtube/"):]))
                return self._send(200, dict(j, ok=True)) if j else self._send(404, {"ok": False, "error": "Job unbekannt"})
            if p == "/api/dvb/rates":
                std = (parse_qs(u.query).get("standard") or [APP.standard])[0]
                if std not in ("dvbt", "dvbc"):
                    return self._send(400, {"ok": False, "errors": ["standard: dvbt|dvbc"]})
                return self._send(200, {"ok": True, "standard": std, "rates": standards.dvb_rates(std)})
            if p == "/api/standards":
                return self._send(200, {"ok": True, "standards": list(standards.STANDARDS), "current": APP.standard,
                                        "live_keys": {k: sorted(v) for k, v in APP.LIVE_KEYS.items()}})
            if p == "/api/playlists":
                return self._send(200, {"ok": True, "playlists": PLSTORE.summary()})
            if p.startswith("/api/playlists/"):
                rest = unquote(p[len("/api/playlists/"):])
                name, _, sub = rest.partition("/")
                if not PLSTORE.exists(name):
                    return self._send(404, {"ok": False, "error": "Playlist nicht gefunden"})
                pl = PLSTORE.load(name)
                if sub == "export":
                    body = PLSTORE.export_m3u8(pl).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/x-mpegurl; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{name}.m3u8"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                if sub:
                    return self._send(404, {"ok": False, "error": "unbekannter Pfad"})
                return self._send(200, dict(pl, ok=True))
            if p == "/api/presets":
                os.makedirs(PRESETS, exist_ok=True)
                names = sorted(n[:-5] for n in os.listdir(PRESETS) if n.endswith(".json"))
                return self._send(200, {"ok": True, "presets": names})
            if p.startswith("/api/presets/"):
                fp = _preset_path(p[len("/api/presets/"):])
                if not fp or not os.path.isfile(fp):
                    return self._send(404, {"ok": False, "error": "Preset nicht gefunden"})
                with open(fp, encoding="utf-8") as f:
                    return self._send(200, json.load(f))
            if p.startswith("/api/"):
                return self._send(404, {"ok": False, "error": "unbekannter Pfad"})
            if p.startswith("/media/"):
                return self._static(MEDIA, p[len("/media/"):])
            return self._static(WEB, p.lstrip("/") or "index.html")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            log("error", f"GET {self.path}: {e}")
            try:
                self._send(500, {"ok": False, "error": str(e)})
            except Exception:
                pass

    def do_PUT(self):
        if self._foreign_request(write=True):
            return
        try:
            p = urlparse(self.path).path
            if p == "/api/config":
                code, res = APP.put_config(self._json())
                return self._send(code, res)
            if p.startswith("/api/playlists/"):
                name = unquote(p[len("/api/playlists/"):])
                try:
                    pl = PLSTORE.save(name, self._json())
                except playlists.PlaylistError as e:
                    return self._send(400, {"ok": False, "errors": str(e).split("; ")})
                log("info", f"Playlist '{name}' gespeichert ({len(pl['items'])} Titel)")
                return self._send(200, dict(pl, ok=True))
            if p.startswith("/api/presets/"):
                fp = _preset_path(p[len("/api/presets/"):])
                if not fp:
                    return self._send(400, {"ok": False, "errors": ["Presetname ungültig"]})
                c, errs = validate_config(self._json())
                if errs:
                    return self._send(400, {"ok": False, "errors": errs})
                c.pop("_cu", None)
                c["rf"]["enabled"] = False
                os.makedirs(PRESETS, exist_ok=True)
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(c, f, indent=2, ensure_ascii=False)
                return self._send(200, {"ok": True})
            return self._send(404, {"ok": False, "error": "unbekannter Pfad"})
        except json.JSONDecodeError as e:
            self._send(400, {"ok": False, "errors": [f"JSON: {e}"]})
        except Exception as e:
            log("error", f"PUT {self.path}: {e}")
            self._send(500, {"ok": False, "error": str(e)})

    def do_DELETE(self):
        if self._foreign_request(write=True):
            return
        p = urlparse(self.path).path
        if p.startswith("/api/media/"):
            return self._send(*APP.media_delete(p[len("/api/media/"):]))
        if p.startswith("/api/playlists/"):
            try:
                ok = PLSTORE.delete(unquote(p[len("/api/playlists/"):]))
            except playlists.PlaylistError as e:
                return self._send(400, {"ok": False, "errors": [str(e)]})
            return self._send(200 if ok else 404, {"ok": ok})
        if p.startswith("/api/presets/"):
            fp = _preset_path(p[len("/api/presets/"):])
            if fp and os.path.isfile(fp):
                os.remove(fp)
                return self._send(200, {"ok": True})
            return self._send(404, {"ok": False, "error": "Preset nicht gefunden"})
        self._send(404, {"ok": False, "error": "unbekannter Pfad"})

    def do_POST(self):
        if self._foreign_request(write=True):
            return
        try:
            u = urlparse(self.path)
            p = u.path
            if p == "/api/upload":
                return self._send(*APP.upload(parse_qs(u.query), self._body(), self.headers.get("Content-Type", "")))
            if p.startswith("/api/playlists/") and p.endswith("/import"):
                name = unquote(p[len("/api/playlists/"):-len("/import")])
                raw = self._body(16 * 1024 * 1024)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252", "replace")
                try:
                    pl, errs = PLSTORE.parse_import(text, name)
                    if not pl["items"]:
                        return self._send(400, {"ok": False, "errors": errs or ["keine Einträge gefunden"]})
                    pl = PLSTORE.save(name, pl)
                except playlists.PlaylistError as e:
                    return self._send(400, {"ok": False, "errors": [str(e)]})
                log("info", f"Playlist '{name}' importiert ({len(pl['items'])} Titel)")
                return self._send(200, dict(pl, ok=True, warnings=errs))
            body = self._json()
            if p == "/api/tx/start":
                return self._send(*APP.tx_start(force=bool((body or {}).get("force"))))
            if p == "/api/onboarding":
                return self._send(*onboarding_save(body))
            if p == "/api/firmware":
                b = body or {}
                return self._send(*FLASHER.start(str(b.get("action") or ""), b.get("image"), confirm=b.get("confirm") is True))
            if p == "/api/setup/install_all":
                if (body or {}).get("accepted") is not True:
                    return self._send(400, {"ok": False, "errors": ["Lizenzen müssen bestätigt werden (accepted: true)"]})
                return self._send(*SETUP.install_all(via="dashboard"))
            if p == "/api/setup/install":
                b = body or {}
                return self._send(*SETUP.install(str(b.get("component") or ""), accepted=b.get("accepted") is True))
            if p == "/api/onboarding/reset":
                try:
                    os.remove(ONBOARDING_FILE)
                except OSError:
                    pass
                APP.safe_mute()
                log("info", "Einrichtung zurückgesetzt – Pluto stumm")
                return self._send(200, dict(onboarding_load(), ok=True))
            if p == "/api/youtube":
                return self._send(*APP.youtube_start(body))
            if p == "/api/tx/stop":
                return self._send(200, APP.tx_stop())
            if p == "/api/rf":
                return self._send(*APP.post_rf(body))
            if p == "/api/live":
                return self._send(*APP.live_ctl(body))
            if p == "/api/check":
                return self._send(*APP.run_check(body))
            if p == "/api/monitor":
                return self._send(*APP.run_monitor(body))
            if p == "/api/sweep":
                return self._send(*APP.start_sweep(body))
            if p == "/api/sweep/stop":
                APP.sweep_stop.set()
                return self._send(200, {"ok": True})
            if p == "/api/config":           # convenience alias
                return self._send(*APP.put_config(body))
            return self._send(404, {"ok": False, "error": "unbekannter Pfad"})
        except json.JSONDecodeError as e:
            self._send(400, {"ok": False, "errors": [f"JSON: {e}"]})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            log("error", f"POST {self.path}: {e}")
            APP.safe_mute()
            try:
                self._send(500, {"ok": False, "error": str(e)})
            except Exception:
                pass

    # ---------------------------------------------------------------- SSE / static
    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            while not APP.closing:
                data = json.dumps(APP.state(), ensure_ascii=False)
                self.wfile.write(b"event: state\ndata: " + data.encode("utf-8") + b"\n\n")
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _static(self, base, rel):
        rel = unquote(rel)
        full = os.path.abspath(os.path.join(base, rel))
        if not full.startswith(os.path.abspath(base)) or not os.path.isfile(full):
            if base == WEB and rel == "index.html":
                return self._send(200, b"<!doctype html><meta charset=utf-8><title>DAB-Lab</title>"
                                       b"<p>Dashboard (web/index.html) fehlt noch. API: <a href=/api/state>/api/state</a>",
                                  "text/html; charset=utf-8")
            return self._send(404, {"ok": False, "error": "nicht gefunden"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)


# ============================================================================ main
def main():
    ap = argparse.ArgumentParser(description="DAB-Lab PC-Server")
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--lan", action="store_true", help="zusätzlich im LAN erreichbar (0.0.0.0)")
    ap.add_argument("--uri", default=os.environ.get("DABLAB_PLUTO_URI", "usb:"))
    ap.add_argument("--backend", choices=["wsl", "ssh", "local"], default=os.environ.get("DABLAB_BACKEND", "wsl"))
    ap.add_argument("--runner", default=None, help="nur --backend local: Pfad zu dablab_run.py (oder Testattrappe)")
    ap.add_argument("--agent", default=os.environ.get("DABLAB_AGENT", "http://192.168.2.1:8080"),
                    help="dablab-agent auf dem Pluto (rf.path device); 'off' = aus")
    ap.add_argument("--device-host", default=os.environ.get("DABLAB_DEVICE_HOST", "192.168.2.1"),
                    help="UDP-Ziel des Transportstroms im Gerätepfad")
    ap.add_argument("--device-udp-port", type=int, default=1234)
    ap.add_argument("--device-mod-dir", default=os.environ.get("DABLAB_DEVICE_MOD_DIR", ""),
                    help="Verzeichnis von dvbt_mod/dvbc_mod auf dem Gerät (leer = PATH /usr/bin, z. B. /tmp)")
    ap.add_argument("--device-rt-prio", type=int, default=50)
    ap.add_argument("--device-window", type=int, default=32, help="dvbt_mod --window (8k: 32, 2k: max. 8)")
    ap.add_argument("--no-device-ssh", action="store_true", help="kein SSH zum Pluto (keine Dateiverteilung, kein Geräte-RX)")
    # Installierte Version (Inno-Setup, unins000.exe im Programmordner): eigene Distro in {app}\wsl\fs, damit sie nicht die
    # Entwicklungs-Distro "dablab" mitbenutzt (und der Deinstaller nur ihre eigene entfernt). Nicht "muxwright" nennen:
    # WSL verwaltet den Startmenü-Ordner mit dem Namen der Distro und löscht dort beim Start fremde Verknüpfungen.
    installed = os.path.exists(os.path.join(ROOT, "unins000.exe"))
    ap.add_argument("--wsl-distro", default=os.environ.get("DABLAB_WSL_DISTRO", "muxwright-linux" if installed else "dablab"))
    ap.add_argument("--ssh-host", default=os.environ.get("DABLAB_SSH_HOST", ""), help="nur --backend ssh: user@host des Runner-Rechners")
    ap.add_argument("--min-atten", type=float, default=float(os.environ.get("DABLAB_MIN_ATTEN", "0")),
                    help="Untergrenze der Dämpfung in dB (Sicherheitsklemme, Standard 0)")
    ap.add_argument("--source", choices=["auto", "runner", "testgen"], default=os.environ.get("DABLAB_SOURCE", "auto"),
                    help="IQ-Quelle: auto = Runner wenn vorhanden, sonst Testgenerator")
    ap.add_argument("--rx-uri", default=os.environ.get("DABLAB_RX_URI", "ip:192.168.2.1"),
                    help="libiio-URI für den RX-Mitschnitt im Kindprozess (Standard RNDIS ip:)")
    ap.add_argument("--rx-mode", choices=["auto", "proc", "inproc"], default="auto",
                    help="auto: Kindprozess, bei Fehler In-Prozess; proc: nur Kindprozess; inproc: alter Weg")
    ap.add_argument("--runtime", default=os.environ.get("DABLAB_RUNTIME_DIR"),
                    help="anderer runtime-Ordner (Standard dablab/runtime; für Testinstanzen)")
    ap.add_argument("--no-priority", action="store_true", help="Prozess-/Threadpriorität nicht anheben")
    ap.add_argument("--no-nco", action="store_true", help="Versatz über LO statt NCO, lo_offset_hz ignorieren")
    ap.add_argument("--setup-components", action="store_true",
                    help="alle Pflichtkomponenten im Hintergrund einrichten (vom Windows-Installer, Lizenzen dort bestätigt)")
    ap.add_argument("--no-auto-restart", dest="auto_restart", action="store_false",
                    help="TX nach Pluto-/Quellenfehler nicht automatisch neu starten")
    ap.add_argument("--nsamp", type=int, default=1 << 17, help="TX-Puffergröße in Samples")
    ap.add_argument("--kbufs", type=int, default=8, help="Anzahl Kernel-Puffer")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args.min_atten = max(0.0, min(89.75, args.min_atten))
    if args.runtime:
        global RUNTIME, MEDIA, RXDIR, CONFIG_FILE, PLDIR, PLSTORE, ONBOARDING_FILE, LOGDIR, DEVICE_MOD_FILE
        RUNTIME = os.path.abspath(args.runtime)
        MEDIA, RXDIR, PLDIR = (os.path.join(RUNTIME, d) for d in ("media", "rx", "playlists"))
        CONFIG_FILE = os.path.join(RUNTIME, "pc_config.json")
        ONBOARDING_FILE = os.path.join(RUNTIME, "onboarding.json")
        DEVICE_MOD_FILE = os.path.join(RUNTIME, "device_mod.json")
        LOGDIR = os.path.join(RUNTIME, "logs")
        PLSTORE = playlists.Store(PLDIR, MEDIA)

    for d in (RUNTIME, MEDIA, RXDIR, PRESETS, PLDIR):
        os.makedirs(d, exist_ok=True)
    if os.path.isdir(EXAMPLE_MEDIA):
        for n in os.listdir(EXAMPLE_MEDIA):
            s, t = os.path.join(EXAMPLE_MEDIA, n), os.path.join(MEDIA, n)
            if os.path.isfile(s) and not os.path.exists(t):
                shutil.copy2(s, t)
    std = os.path.join(PRESETS, "Standard.json")
    if not os.path.exists(std):
        with open(std, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    fmp = os.path.join(PRESETS, "UKW Standard.json")
    if not os.path.exists(fmp):
        with open(fmp, "w", encoding="utf-8") as f:
            json.dump(FM_DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)

    if not args.no_priority and plutomod.set_process_priority(0x8000):
        log("info", "Prozesspriorität ABOVE_NORMAL, TX-Thread HIGHEST")
    global APP
    global SETUP
    SETUP = setupmod.Setup(distro=args.wsl_distro, runtime=RUNTIME, log=log,
                           distro_dir=os.environ.get("DABLAB_WSL_DIR") or None)
    APP = App(args)
    if args.setup_components:
        SETUP.install_all(via="installer")
    global FLASHER
    FLASHER = flashermod.Flasher(ROOT, RUNTIME, app=APP, device_host=args.device_host, log=log,
                                 agent_url=args.agent,
                                 fw_now=lambda: APP.pluto.info.get("fw"))
    log("info", f"DAB-Lab Server, Backend {args.backend}, Pluto {args.uri}, Mindestdämpfung {args.min_atten} dB, "
                f"NCO {'an' if APP.nco else 'aus'}")
    if APP.agent is not None and APP._poll_agent() is not None:
        m = APP.agent_status.get("mod") or {}
        log("info", f"dablab-agent erreichbar ({APP.agent_status.get('fw')})" +
            (f" – Modulator läuft: {m.get('cmd')} → USB-Verbindung passiv, Pluto wird nicht angefasst" if m.get("running") else ""))
    passive = APP.foreign_mod()
    if APP.pluto.connect(passive=passive):
        if not passive:
            APP.gain_applied = MUTE_DB
            try:
                APP.pluto.set_tx_lo(APP.rf_plan(APP.cfg["rf"])[0])
            except Exception as e:
                log("warn", f"LO: {e}")
    else:
        log("warn", f"Pluto nicht verbunden: {APP.pluto.info.get('error')}")

    done = threading.Event()

    def shutdown(*_):
        if done.is_set():
            return
        done.set()
        APP.closing = True
        APP.sweep_stop.set()
        try:
            APP._teardown("idle") if APP.stream is not None or APP.proc is not None else APP.safe_mute()
        except Exception:
            APP.safe_mute()
        APP.safe_mute()
        log("info", "Server beendet, Pluto stumm")

    atexit.register(shutdown)
    if os.name == "nt":       # console window closed / logoff: mute first
        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def _ctrl(ev):
            shutdown()
            return 0
        APP._ctrl_handler = HandlerRoutine(_ctrl)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(APP._ctrl_handler, True)
    if hasattr(signal, "SIGBREAK"):
        def _brk(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK, _brk)

    threading.Thread(target=APP.loop, daemon=True, name="poll").start()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer((host, args.port), Handler)
    log("info", f"HTTP http://{'127.0.0.1' if not args.lan else host}:{args.port}/" + ("  (LAN offen!)" if args.lan else ""))
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
        httpd.server_close()
        APP.pluto.close()
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
