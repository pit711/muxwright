#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DAB-Lab Runner: ffmpeg -> odr-audioenc (+odr-padenc) -> FIFO -> odr-dabmux -> odr-dabmod -> stdout (IQ).

Befehle (siehe SPEC.md / README.md):
  live     --config c.json [--shift-hz N]
  render   --config c.json --seconds N --out f [--rate 2048000] [--format cf32|s16]
  check    --config c.json [--seconds 20] [--wait 35]
  decode   --iq f.cf32.iq [--seconds 20] [--config c.json]
  ctl      --session <id> '<json>'
  selftest [--quick]
  iqconv   --in f.s16 --in-rate 4096000 --out g.cf32.iq      (Hilfsbefehl: 2:1 dezimieren, s16 -> cf32)
  mkmedia                                                     (Hilfsbefehl: Beispielmedien erzeugen)
  validate --config c.json                                    (nur pruefen, CU-Bilanz)

Nur Python-Standardbibliothek; numpy wird genutzt, wenn vorhanden (Pegelmessung, iqconv, Frequenzverschiebung).
Laeuft unter macOS und Linux (auch WSL1). Umgebungsvariablen:
  DABLAB_BIN      Werkzeugverzeichnis (odr-*, welle-cli). Standard: Verzeichnis von odr-dabmux im PATH.
  DABLAB_RUNTIME  Laufzeitverzeichnis (media/, sessions/, cache/). Standard: Skriptordner, falls er "runtime" heisst,
                  sonst <Skriptordner>/../runtime.
  DABLAB_TMP      Ordner fuer FIFOs (muss ein echtes Unix-Dateisystem sein, nicht /mnt/c). Standard: /tmp.
"""
import errno
import glob
import hashlib
import io
import json
import math
import os
import platform
import random
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib

try:
    import numpy as np  # optional
except Exception:  # pragma: no cover
    np = None

VERSION = "1.0"
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

# --------------------------------------------------------------------------------------------
# Pfade und Werkzeuge
# --------------------------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_runtime():
    if os.environ.get("DABLAB_RUNTIME"):
        return os.path.abspath(os.path.expanduser(os.environ["DABLAB_RUNTIME"]))
    if os.path.basename(SCRIPT_DIR) == "runtime":
        return SCRIPT_DIR
    return os.path.abspath(os.path.join(SCRIPT_DIR, "..", "runtime"))


RUNTIME = _default_runtime()
MEDIA = os.path.join(RUNTIME, "media")
SESSIONS = os.path.join(RUNTIME, "sessions")
PLAYLISTS = os.path.join(RUNTIME, "playlists")
CACHE = os.path.join(RUNTIME, "cache")
TMPROOT = os.environ.get("DABLAB_TMP") or ("/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir())

_EXTRA_PATHS = ["/opt/dablab/bin", os.path.expanduser("~/dablab/prefix/bin"), "/opt/homebrew/bin",
                "/usr/local/bin", "/usr/bin", "/bin"]


def _bin_dir():
    if os.environ.get("DABLAB_BIN"):
        return os.path.abspath(os.path.expanduser(os.environ["DABLAB_BIN"]))
    p = shutil.which("odr-dabmux")
    if p:
        return os.path.dirname(os.path.realpath(p))
    for d in _EXTRA_PATHS:
        if os.path.isfile(os.path.join(d, "odr-dabmux")):
            return d
    return ""


BIN = _bin_dir()


def _sanitize_path():
    """PATH neu ordnen: DABLAB_BIN zuerst, dann System. Unter WSL haengt Windows seinen PATH an (/mnt/c/...),
    dort darf kein Windows-ffmpeg o. ae. gefunden werden -> /mnt/-Eintraege entfernen."""
    old = [p for p in os.environ.get("PATH", "").split(":") if p and not p.startswith("/mnt/")]
    new = []
    for p in ([BIN] if BIN else []) + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] + old:
        if p not in new and os.path.isdir(p):
            new.append(p)
    os.environ["PATH"] = ":".join(new)


_sanitize_path()
_EXTRA_PATHS = [p for p in _EXTRA_PATHS if not p.startswith("/mnt/")]


def tool(name, required=True):
    cands = []
    if BIN:
        cands.append(os.path.join(BIN, name))
    p = shutil.which(name)
    if p:
        cands.append(p)
    cands += [os.path.join(d, name) for d in _EXTRA_PATHS]
    for c in cands:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    if required:
        raise RunnerError("Werkzeug nicht gefunden: %s (DABLAB_BIN=%s)" % (name, BIN or "-"))
    return None


def magick_cmd():
    m = tool("magick", required=False)
    if m:
        return [m]
    c = tool("convert", required=False)
    return [c] if c else None


def media_path(p):
    """Pfad aus der Konfiguration -> absolut (relativ zu RUNTIME, 'media/x' oder 'x')."""
    if not p:
        return p
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    for base in (RUNTIME, MEDIA):
        q = os.path.join(base, p)
        if os.path.exists(q):
            return q
    return os.path.join(RUNTIME, p)


class RunnerError(Exception):
    pass


# --------------------------------------------------------------------------------------------
# Protokollierung (stderr JSON-Zeilen)
# --------------------------------------------------------------------------------------------
_log_lock = threading.Lock()
_log_file = None


def log(lvl, msg, **kw):
    d = {"t": round(time.time(), 3), "lvl": lvl, "msg": msg}
    d.update(kw)
    line = json.dumps(d, ensure_ascii=False)
    with _log_lock:
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        if _log_file:
            try:
                _log_file.write(line + "\n")
                _log_file.flush()
            except Exception:
                pass


def emit_json(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------------------------
# Tabellen
# --------------------------------------------------------------------------------------------
CHANNELS = {
    "5A": 174.928, "5B": 176.640, "5C": 178.352, "5D": 180.064, "6A": 181.936, "6B": 183.648, "6C": 185.360,
    "6D": 187.072, "7A": 188.928, "7B": 190.640, "7C": 192.352, "7D": 194.064, "8A": 195.936, "8B": 197.648,
    "8C": 199.360, "8D": 201.072, "9A": 202.928, "9B": 204.640, "9C": 206.352, "9D": 208.064, "10A": 209.936,
    "10N": 210.096, "10B": 211.648, "10C": 213.360, "10D": 215.072, "11A": 216.928, "11N": 217.088,
    "11B": 218.640, "11C": 220.352, "11D": 222.064, "12A": 223.936, "12N": 224.096, "12B": 225.648,
    "12C": 227.360, "12D": 229.072, "13A": 230.784, "13B": 232.496, "13C": 234.208, "13D": 235.776,
    "13E": 237.488, "13F": 239.200,
}
for _i, _c in enumerate("ABCDEFGHIJKLMNOP"):
    CHANNELS["L" + _c] = round(1452.960 + 1.712 * _i, 3)

# UEP: (Bitrate, Schutzstufe 1..5) -> CU   (EN 300 401 Tabelle 6, wie ODR-DabMux MuxElements.cpp)
_UEP_ROWS = [
    (32, [5, 4, 3, 2, 1], [16, 21, 24, 29, 35]),
    (48, [5, 4, 3, 2, 1], [24, 29, 35, 42, 52]),
    (56, [5, 4, 3, 2], [29, 35, 42, 52]),
    (64, [5, 4, 3, 2, 1], [32, 42, 48, 58, 70]),
    (80, [5, 4, 3, 2, 1], [40, 52, 58, 70, 84]),
    (96, [5, 4, 3, 2, 1], [48, 58, 70, 84, 104]),
    (112, [5, 4, 3, 2], [58, 70, 84, 104]),
    (128, [5, 4, 3, 2, 1], [64, 84, 96, 116, 140]),
    (160, [5, 4, 3, 2, 1], [80, 104, 116, 140, 168]),
    (192, [5, 4, 3, 2, 1], [96, 116, 140, 168, 208]),
    (224, [5, 4, 3, 2, 1], [116, 140, 168, 208, 232]),
    (256, [5, 4, 3, 2, 1], [128, 168, 192, 232, 280]),
    (320, [5, 4, 2], [160, 208, 280]),
    (384, [5, 3, 1], [192, 280, 416]),
]
UEP_TABLE = {}
for _br, _lv, _cu in _UEP_ROWS:
    for _l, _c in zip(_lv, _cu):
        UEP_TABLE[(_br, _l)] = _c

MP2_RATES_48 = [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384]
MP2_RATES_24 = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]

ANN_FLAGS = ["Alarm", "Traffic", "Travel", "Warning", "News", "Weather", "Event", "Special",
             "ProgrammeInfo", "Sports", "Finance"]
ANN_ALIASES = {"rad_info": "ProgrammeInfo", "radinfo": "ProgrammeInfo", "programmeinfo": "ProgrammeInfo",
               "programme_info": "ProgrammeInfo"}

DLPLUS_TYPES = {0: "DUMMY", 1: "ITEM.TITLE", 2: "ITEM.ALBUM", 3: "ITEM.TRACKNUMBER", 4: "ITEM.ARTIST",
                5: "ITEM.COMPOSITION", 6: "ITEM.MOVEMENT", 7: "ITEM.CONDUCTOR", 8: "ITEM.COMPOSER",
                9: "ITEM.BAND", 10: "ITEM.COMMENT", 11: "ITEM.GENRE", 12: "INFO.NEWS", 13: "INFO.NEWS.LOCAL",
                14: "INFO.STOCKMARKET", 15: "INFO.SPORT", 16: "INFO.LOTTERY", 17: "INFO.HOROSCOPE",
                18: "INFO.DAILY_DIVERSION", 19: "INFO.HEALTH", 20: "INFO.EVENT", 21: "INFO.SCENE",
                22: "INFO.CINEMA", 23: "INFO.STUPIDITY_MACHINE", 24: "INFO.DATE_TIME", 25: "INFO.WEATHER",
                26: "INFO.TRAFFIC", 27: "INFO.ALARM", 28: "INFO.ADVERTISEMENT", 29: "INFO.URL",
                30: "INFO.OTHER", 31: "STATIONNAME.SHORT", 32: "STATIONNAME.LONG", 33: "PROGRAMME.NOW",
                34: "PROGRAMME.NEXT", 35: "PROGRAMME.PART", 36: "PROGRAMME.HOST",
                37: "PROGRAMME.EDITORIAL_STAFF", 38: "PROGRAMME.FREQUENCY", 39: "PROGRAMME.HOMEPAGE",
                40: "PROGRAMME.SUBCHANNEL", 41: "PHONE.HOTLINE", 42: "PHONE.STUDIO", 43: "PHONE.OTHER",
                44: "SMS.STUDIO", 45: "SMS.OTHER", 46: "EMAIL.HOTLINE", 47: "EMAIL.STUDIO", 48: "EMAIL.OTHER",
                49: "MMS.OTHER", 50: "CHAT", 51: "CHAT.CENTER", 52: "VOTE.QUESTION", 53: "VOTE.CENTRE",
                59: "PRIVATE.1", 60: "PRIVATE.2", 61: "PRIVATE.3", 62: "DESCRIPTOR.PLACE",
                63: "DESCRIPTOR.APPOINTMENT", 64: "DESCRIPTOR.IDENTIFIER", 65: "DESCRIPTOR.PURCHASE",
                66: "DESCRIPTOR.GET_DATA"}
DLPLUS_FIELDS = [("artist", 4), ("title", 1), ("album", 2), ("station", 32), ("program", 33), ("url", 29)]

# Modus -> Rahmendauer (Transmission Frame) in s; ETI-Rahmen sind immer 24 ms
MODE_TF = {1: 0.096, 2: 0.024, 3: 0.024, 4: 0.048}


# --------------------------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------------------------
def hexint(v, default=None):
    if v is None or v == "":
        return default
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def crc16(data):
    """CRC-16-CCITT wie in EN 300 401 (Init 0xFFFF, Ergebnis invertiert)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


def free_port(preferred=None):
    if preferred:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", preferred))
            s.close()
            return preferred
        except OSError:
            s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def set_pipe_size(fd, size=16384):
    """Linux: Pipe/FIFO-Puffer verkleinern (weniger Latenz). macOS: nicht moeglich, ignoriert."""
    if IS_LINUX:
        try:
            import fcntl
            fcntl.fcntl(fd, 1031, size)  # F_SETPIPE_SZ
        except Exception:
            pass


def mux_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def image_size(path):
    """(w,h) fuer JPEG/PNG ohne Fremdbibliotheken."""
    try:
        with open(path, "rb") as f:
            d = f.read()
    except Exception:
        return None, None
    return image_size_bytes(d)


def image_size_bytes(d):
    if d[:8] == b"\x89PNG\r\n\x1a\n" and len(d) >= 24:
        return struct.unpack(">II", d[16:24])
    if d[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(d):
            if d[i] != 0xFF:
                i += 1
                continue
            m = d[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", d[i + 5:i + 9])
                return w, h
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            ln = struct.unpack(">H", d[i + 2:i + 4])[0]
            i += 2 + ln
    return None, None


def atomic_write(path, text):
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------------
# Konfiguration: Normalisieren + Pruefen
# --------------------------------------------------------------------------------------------
def load_config(path):
    path = os.path.expanduser(path)
    if not os.path.isabs(path) and not os.path.exists(path):
        alt = os.path.join(RUNTIME, path)
        if os.path.exists(alt):
            path = alt
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    # jsonc-Kommentare zulassen (nur ganze //-Zeilen)
    txt = "\n".join(l for l in txt.splitlines() if not l.strip().startswith("//"))
    return json.loads(txt)


def short_ok(label, short):
    """ODR verlangt: Kurzlabel = Auswahl aus Zeichen des Labels (Reihenfolge erhalten), max. 8 Zeichen."""
    i = 0
    for ch in short:
        j = label.find(ch, i)
        if j < 0:
            return False
        i = j + 1
    return True


def eep_cu(profile, level, br):
    if profile == "EEP_A":
        return [12, 8, 6, 4][level - 1] * br // 8
    return [27, 21, 18, 15][level - 1] * br // 32


def normalize(cfg):
    """Gibt (cfg_norm, errors, warnings, capacity) zurueck."""
    E, W = [], []
    c = json.loads(json.dumps(cfg))  # tiefe Kopie
    c.setdefault("name", "unbenannt")
    try:
        c["mode"] = int(c.get("mode", 1))
    except Exception:
        E.append("mode ungueltig")
        c["mode"] = 1
    if c["mode"] not in (1, 2, 3, 4):
        E.append("mode muss 1..4 sein (ist %s)" % c["mode"])
    ens = c.setdefault("ensemble", {})
    ens.setdefault("eid", "0x4FFF")
    ens.setdefault("ecc", "0xE0")
    ens.setdefault("label", "DABLAB")
    ens.setdefault("shortlabel", ens["label"][:8])
    ens.setdefault("charset", "ebu")
    ens.setdefault("international_table", 1)
    ens.setdefault("lto", "auto")
    ens.setdefault("datetime", True)
    ens.setdefault("alarm_flag", False)
    ens.setdefault("reconfig_counter", "hash")
    try:
        eid = hexint(ens["eid"])
        assert 0 < eid <= 0xFFFF
    except Exception:
        E.append("ensemble.eid ungueltig: %r" % ens.get("eid"))
    try:
        assert 0 <= hexint(ens["ecc"]) <= 0xFF
    except Exception:
        E.append("ensemble.ecc ungueltig: %r" % ens.get("ecc"))
    if len(ens["label"]) > 16:
        E.append("ensemble.label laenger als 16 Zeichen")
    if len(ens["shortlabel"]) > 8 or not short_ok(ens["label"], ens["shortlabel"]):
        E.append("ensemble.shortlabel '%s' muss max. 8 Zeichen aus dem Label (in Reihenfolge) sein" % ens["shortlabel"])
    if ens["lto"] != "auto":
        try:
            v = int(ens["lto"])
            if not -24 <= v <= 24:
                E.append("ensemble.lto ausserhalb -24..24 (halbe Stunden)")
        except Exception:
            E.append("ensemble.lto muss 'auto' oder Zahl sein")
    if not ens.get("datetime", True):
        W.append("ensemble.datetime=false wird von ODR-DabMux nicht unterstuetzt: FIG 0/10 wird immer gesendet")
    rc = ens["reconfig_counter"]
    if rc not in ("hash", "off"):
        try:
            assert 0 <= int(rc) <= 1023
        except Exception:
            E.append("ensemble.reconfig_counter muss hash|off|0..1023 sein")

    services = c.setdefault("services", [])
    if not services:
        E.append("keine Dienste konfiguriert")
    keys, sids = set(), set()
    total_cu = 0
    cap = []
    subch_id = 0
    for i, s in enumerate(services):
        key = s.get("key") or "srv%d" % (i + 1)
        s["key"] = key
        if not re.match(r"^[a-z0-9_]+$", key):
            E.append("services[%d].key '%s' ungueltig ([a-z0-9_])" % (i, key))
        if key in keys:
            E.append("services: key '%s' doppelt" % key)
        keys.add(key)
        s.setdefault("type", "dabplus")
        st = s["type"]
        if st not in ("dabplus", "dab", "data"):
            E.append("%s: type muss dabplus|dab|data sein" % key)
        try:
            sid = hexint(s.get("sid"))
            if sid is None:
                sid = (0xD001 + i) if st != "data" else (0xE0D00001 + i)
            s["sid"] = "0x%04X" % sid if sid <= 0xFFFF else "0x%08X" % sid
            if st != "data" and sid > 0xFFFF:
                E.append("%s: Audio-SId muss 16 bit sein" % key)
            if st == "data" and sid <= 0xFFFF:
                W.append("%s: Datendienst mit 16-bit-SId, empfohlen ist 32 bit (ECC+Land+Nummer)" % key)
            if sid in sids:
                E.append("%s: SId %s doppelt" % (key, s["sid"]))
            sids.add(sid)
        except Exception:
            E.append("%s: sid ungueltig %r" % (key, s.get("sid")))
        s.setdefault("label", key)
        s.setdefault("shortlabel", s["label"][:8])
        if len(s["label"]) > 16:
            E.append("%s: label laenger als 16 Zeichen" % key)
        if len(s["shortlabel"]) > 8 or not short_ok(s["label"], s["shortlabel"]):
            E.append("%s: shortlabel '%s' muss max. 8 Zeichen aus dem Label (in Reihenfolge) sein" % (key, s["shortlabel"]))
        s.setdefault("pty", 0)
        if not 0 <= int(s["pty"]) <= 31:
            E.append("%s: pty 0..31" % key)
        s.setdefault("pty_dynamic", False)
        s.setdefault("language", "0x08")
        s.setdefault("ecc", None)
        prot = s.setdefault("protection", {})
        prot.setdefault("profile", "UEP" if st == "dab" else "EEP_A")
        prot.setdefault("level", 3)
        prot["profile"] = str(prot["profile"]).upper().replace("-", "_")
        if prot["profile"] == "EEP_A" or prot["profile"] == "EEPA":
            prot["profile"] = "EEP_A"
        if prot["profile"] == "EEPB":
            prot["profile"] = "EEP_B"
        lvl = int(prot["level"])
        prot["level"] = lvl
        subch_id += 1
        s["_subch"] = subch_id
        if st in ("dabplus", "dab"):
            a = s.setdefault("audio", {})
            src = a.setdefault("source", {"kind": "tone"})
            src.setdefault("kind", "tone")
            if src["kind"] not in ("file", "tone", "silence", "noise", "pinknoise", "sweep", "lr_test", "polarity",
                                   "playlist"):
                E.append("%s: audio.source.kind '%s' unbekannt" % (key, src["kind"]))
            if src["kind"] == "playlist":
                try:
                    pl = load_playlist(src.get("playlist", ""))
                    if not pl["items"]:
                        E.append("%s: Playlist '%s' ist leer" % (key, src.get("playlist")))
                    for it in pl["items"]:
                        if not os.path.isfile(media_path(it["file"])):
                            E.append("%s: Playlist '%s': Datei fehlt: %s" % (key, src.get("playlist"), it["file"]))
                except RunnerError as e:
                    E.append("%s: %s" % (key, e))
                if float(src.get("crossfade_s") or 0) > 0:
                    W.append("%s: crossfade_s wird nicht unterstuetzt, Titel laufen nahtlos hintereinander" % key)
            if src["kind"] == "file":
                fp = media_path(src.get("file", ""))
                if not fp or not os.path.isfile(fp):
                    E.append("%s: Audiodatei fehlt: %s" % (key, src.get("file")))
            a.setdefault("codec", "mp2" if st == "dab" else "he-aac-v2")
            a.setdefault("samplerate", 48000)
            a.setdefault("channels", 2)
            a.setdefault("bitrate", 128 if st == "dab" else 88)
            a.setdefault("mp2_mode", "j" if a["channels"] == 2 else "m")
            a.setdefault("gain_db", 0)
            a.setdefault("bandwidth_hz", 0)
            br, sr, ch = int(a["bitrate"]), int(a["samplerate"]), int(a["channels"])
            a["bitrate"], a["samplerate"], a["channels"] = br, sr, ch
            if ch not in (1, 2):
                E.append("%s: channels 1|2" % key)
            if st == "dabplus":
                if a["codec"] not in ("he-aac-v2", "he-aac", "aac-lc"):
                    E.append("%s: codec fuer DAB+ muss he-aac-v2|he-aac|aac-lc sein" % key)
                if sr not in (48000, 32000):
                    E.append("%s: DAB+ samplerate 48000|32000" % key)
                if br < 8 or br > 192 or br % 8:
                    E.append("%s: DAB+ bitrate 8..192, Vielfaches von 8" % key)
                if a["codec"] == "he-aac-v2" and ch != 2:
                    E.append("%s: HE-AACv2 (PS) braucht channels=2" % key)
                if prot["profile"] == "UEP":
                    E.append("%s: UEP nur fuer DAB (MP2)" % key)
            else:
                if a["codec"] != "mp2":
                    E.append("%s: type dab verlangt codec mp2" % key)
                if sr not in (48000, 24000):
                    E.append("%s: MP2 samplerate 48000|24000" % key)
                allowed = MP2_RATES_48 if sr == 48000 else MP2_RATES_24
                if br not in allowed:
                    E.append("%s: MP2 bitrate %d bei %d Hz nicht erlaubt (%s)" % (key, br, sr, allowed))
                if a["mp2_mode"] not in ("s", "j", "d", "m"):
                    E.append("%s: mp2_mode s|j|d|m" % key)
                if ch == 1 and a["mp2_mode"] != "m":
                    W.append("%s: mono -> mp2_mode m" % key)
                    a["mp2_mode"] = "m"
                if ch == 2 and a["mp2_mode"] == "m":
                    E.append("%s: mp2_mode m verlangt channels=1" % key)
            sub_br = br
        else:
            d = s.setdefault("data", {})
            d.setdefault("kind", "slideshow_packet")
            d.setdefault("bitrate", 16)
            d.setdefault("packet_address", 1)
            d.setdefault("files", [])
            sub_br = int(d["bitrate"])
            if sub_br % 8 or sub_br < 8:
                E.append("%s: data.bitrate Vielfaches von 8" % key)
            if d["kind"] not in ("spi", "slideshow_packet", "tpeg", "journaline", "raw"):
                E.append("%s: data.kind unbekannt" % key)
            if not 1 <= int(d["packet_address"]) <= 1023:
                E.append("%s: packet_address 1..1023" % key)
            for fn in d["files"]:
                if not os.path.isfile(media_path(fn)):
                    E.append("%s: Datei fehlt: %s" % (key, fn))
            if d["kind"] in ("tpeg", "journaline") and not d["files"]:
                E.append("%s: %s braucht eine fertige Paketstrom-Datei in data.files (kein Encoder vorhanden)" % (key, d["kind"]))
            if prot["profile"] == "UEP":
                E.append("%s: UEP nur fuer DAB (MP2)" % key)
        # Kapazitaet
        cu = None
        if prot["profile"] in ("EEP_A", "EEP_B"):
            if not 1 <= lvl <= 4:
                E.append("%s: EEP-Stufe 1..4" % key)
            elif prot["profile"] == "EEP_B" and sub_br % 32:
                E.append("%s: EEP-B verlangt Bitrate als Vielfaches von 32 (ist %d)" % (key, sub_br))
            else:
                cu = eep_cu(prot["profile"], lvl, sub_br)
        elif prot["profile"] == "UEP":
            cu = UEP_TABLE.get((sub_br, lvl))
            if cu is None:
                E.append("%s: UEP %d bei %d kbit/s nicht definiert (Tab. 6)" % (key, lvl, sub_br))
        else:
            E.append("%s: protection.profile EEP_A|EEP_B|UEP" % key)
        if cu is not None:
            total_cu += cu
            cap.append({"service": key, "bitrate": sub_br,
                        "protection": prot_name(prot["profile"], lvl), "cu": cu})
        # PAD
        pad = s.setdefault("pad", {"enable": False})
        pad.setdefault("enable", False)
        if st == "data":
            pad["enable"] = False
        pad.setdefault("bytes", 58)
        dls = pad.setdefault("dls", {})
        dls.setdefault("enable", True)
        dls.setdefault("text", s["label"])
        dls.setdefault("dlplus", {"enable": False})
        dls.setdefault("rotate", [])
        dls.setdefault("rotate_s", 10)
        dls.setdefault("charset", "ebu")
        dls.setdefault("remove_label", False)
        sls = pad.setdefault("sls", {})
        sls.setdefault("enable", False)
        sls.setdefault("slides", [])
        sls.setdefault("interval_s", 10)
        sls.setdefault("max_bytes", 50000)
        sls.setdefault("raw", False)
        sls.setdefault("categories", False)
        if pad["enable"]:
            pb = int(pad["bytes"])
            if not (pb == 6 or 8 <= pb <= 196):
                E.append("%s: pad.bytes 6 oder 8..196 (ODR-PadEnc)" % key)
            for fn in sls["slides"]:
                if not os.path.isfile(media_path(fn)):
                    E.append("%s: Slide fehlt: %s" % (key, fn))
            if sls.get("categories"):
                W.append("%s: sls.categories (CatSLS) wird von ODR-PadEnc nicht unterstuetzt" % key)
            if len(dls.get("text", "").encode("utf-8")) > 128:
                W.append("%s: DLS-Text > 128 Byte wird gekuerzt" % key)
        spi = s.get("spi") or {}
        if spi.get("logo"):
            if not os.path.isfile(media_path(spi["logo"])):
                E.append("%s: spi.logo fehlt: %s" % (key, spi["logo"]))
            else:
                W.append("%s: spi.logo: Logos werden nur in einem SPI-Datendienst (type data, kind spi) "
                         "uebertragen; ein SI-XML (TS 102 371 binaer) fehlt, siehe README" % key)
        ann = s.setdefault("announcements", {"supported": [], "clusters": []})
        ann.setdefault("supported", [])
        ann.setdefault("clusters", [])
        ann["supported"] = [norm_flag(f, E, key) for f in ann["supported"]]
        lk = s.setdefault("linking", {})
        lk.setdefault("fm_pi", None)
        lk.setdefault("fm_freqs_mhz", [])
        lk.setdefault("other_ensembles", [])
    c["_capacity"] = {"used_cu": total_cu, "max_cu": 864, "free_cu": 864 - total_cu, "subchannels": cap}
    if total_cu > 864:
        E.append("Kapazitaet ueberschritten: %d CU belegt, erlaubt sind 864 CU (%s)" %
                 (total_cu, ", ".join("%s=%d" % (x["service"], x["cu"]) for x in cap)))
    # Cluster
    cl = c.setdefault("clusters", [])
    ids = set()
    for k in cl:
        try:
            cid = hexint(k.get("id"))
            k["id"] = cid
        except Exception:
            E.append("clusters: id ungueltig")
            continue
        if not 1 <= cid <= 255:
            E.append("clusters: id %s ausserhalb 1..255" % cid)
        if cid in ids:
            E.append("clusters: id %d doppelt" % cid)
        ids.add(cid)
        k["flags"] = [norm_flag(f, E, "cluster %d" % cid) for f in k.get("flags", [])]
        if not k["flags"]:
            E.append("cluster %d: mindestens ein flag noetig" % cid)
        k.setdefault("active", False)
        sv = [s for s in services if s["key"] == k.get("service")]
        if not sv:
            E.append("cluster %d: service '%s' unbekannt" % (cid, k.get("service")))
        elif sv[0]["type"] == "data":
            E.append("cluster %d: service muss Audiodienst sein" % cid)
    if ens.get("alarm_flag") and 255 not in ids:
        aud = [s for s in services if s["type"] != "data"]
        if aud:
            cl.append({"id": 255, "flags": ["Alarm"], "service": aud[0]["key"], "active": False, "_auto": True})
            W.append("alarm_flag: Alarm-Cluster 0xFF (inaktiv) automatisch angelegt -> AL-Flag in FIG 0/0")
    if not ens.get("alarm_flag") and 255 in ids:
        W.append("Cluster 0xFF vorhanden: ODR-DabMux setzt das AL-Flag in FIG 0/0 automatisch")
        ens["alarm_flag"] = True
    tii = c.setdefault("tii", {})
    tii.setdefault("enable", False)
    tii.setdefault("comb", 1)
    tii.setdefault("pattern", 11)
    tii.setdefault("old_variant", False)
    if tii["enable"]:
        if not 0 <= int(tii["comb"]) <= 23:
            E.append("tii.comb 0..23")
        if not 0 <= int(tii["pattern"]) <= 69:
            E.append("tii.pattern 0..69")
    rf = c.setdefault("rf", {})
    rf.setdefault("channel", "6C")
    if rf["channel"] != "custom":
        if rf["channel"] not in CHANNELS:
            E.append("rf.channel '%s' unbekannt" % rf["channel"])
        else:
            rf.setdefault("freq_hz", int(round(CHANNELS[rf["channel"]] * 1e6)))
    rf.setdefault("freq_hz", 185360000)
    rf.setdefault("sample_rate", 4096000)
    rf.setdefault("backoff_db", 12)
    rf.setdefault("cfr", False)
    try:
        sr = int(rf["sample_rate"])
        if sr % 1000 or not 2048000 <= sr <= 20000000:
            E.append("rf.sample_rate 2048000..20000000")
    except Exception:
        E.append("rf.sample_rate ungueltig")
    if not 0 <= float(rf["backoff_db"]) <= 40:
        E.append("rf.backoff_db 0..40")
    if float(rf["backoff_db"]) < 9:
        W.append("rf.backoff_db < 9 dB: OFDM-Spitzen (Crest ~12 dB) werden in int16 abgeschnitten")
    if c["mode"] in (2, 3, 4) and float(rf.get("freq_hz", 0)) < 1e9 and c["mode"] != 1:
        W.append("Modus %d in Band III ist ungewoehnlich (Band III: Modus I)" % c["mode"])
    return c, E, W


def norm_flag(f, E, ctx):
    f0 = str(f)
    if f0 in ANN_FLAGS:
        return f0
    low = f0.lower()
    if low in ANN_ALIASES:
        return ANN_ALIASES[low]
    for x in ANN_FLAGS:
        if x.lower() == low:
            return x
    E.append("%s: Durchsage-Flag '%s' unbekannt" % (ctx, f0))
    return f0


def prot_name(profile, level):
    if profile == "UEP":
        return "UEP %d" % level
    return "EEP %d-%s" % (level, "A" if profile == "EEP_A" else "B")


# --------------------------------------------------------------------------------------------
# DLS-Dateien (ODR-PadEnc-Format mit DL Plus)
# --------------------------------------------------------------------------------------------
def build_dls(text, dlplus=None):
    """Liefert Dateiinhalt fuer odr-padenc -t (mit DL-Plus-Parameterblock)."""
    text = (text or "").replace("\r", " ").replace("\n", " ")
    tags = []
    if dlplus and dlplus.get("enable"):
        for fld, ct in DLPLUS_FIELDS:
            val = (dlplus.get(fld) or "").strip()
            if not val or len(tags) >= 4:
                continue
            pos = text.find(val)
            if pos < 0:
                text = (text + " - " + val) if text else val
                pos = text.find(val)
            if pos > 127 or len(val) > 128:
                continue
            tags.append((ct, pos, min(len(val), 128) - 1))
    if len(text.encode("utf-8")) > 128:
        b = text.encode("utf-8")[:128]
        text = b.decode("utf-8", "ignore")
    if not (dlplus and dlplus.get("enable")):
        return text + "\n"
    out = ["##### parameters { #####", "DL_PLUS=1",
           "DL_PLUS_ITEM_TOGGLE=%d" % (1 if dlplus.get("item_toggle") else 0),
           "DL_PLUS_ITEM_RUNNING=%d" % (0 if dlplus.get("item_running") is False else 1)]
    for ct, st, ln in tags:
        out.append("DL_PLUS_TAG=%d %d %d" % (ct, st, ln))
    out.append("##### parameters } #####")
    out.append(text)
    return "\n".join(out) + "\n"


def decode_dlplus(hexstr, dls):
    if not hexstr:
        return None
    try:
        b = bytes.fromhex(hexstr)
    except ValueError:
        return []
    if not b or (b[0] >> 4) != 0:
        return [{"raw": hexstr}]
    nt = (b[0] & 3) + 1
    res = []
    for i in range(nt):
        o = 1 + 3 * i
        if o + 2 >= len(b):
            break
        ct, st, ln = b[o] & 0x7F, b[o + 1] & 0x7F, b[o + 2] & 0x7F
        res.append({"code": ct, "type": DLPLUS_TYPES.get(ct, "?"), "start": st, "length": ln + 1,
                    "text": (dls or "")[st:st + ln + 1] if ct else ""})
    return {"item_toggle": bool(b[0] & 8), "item_running": bool(b[0] & 4), "tags": res}


# --------------------------------------------------------------------------------------------
# MOT im Paketmodus (EN 300 401 5.3.2/5.3.3, EN 301 234)
# --------------------------------------------------------------------------------------------
MOT_TYPES = {".jpg": (2, 1), ".jpeg": (2, 1), ".png": (2, 3), ".gif": (2, 0), ".bmp": (2, 2),
             ".xml": (7, 0), ".txt": (1, 0), ".html": (1, 2), ".htm": (1, 2)}


def mot_param(pid, data):
    n = len(data)
    if n == 0:
        return bytes([pid & 0x3F])
    if n == 1:
        return bytes([0x40 | pid]) + data
    if n == 4:
        return bytes([0x80 | pid]) + data
    hdr = bytes([0xC0 | pid])
    if n <= 127:
        hdr += bytes([n])
    else:
        hdr += bytes([0x80 | (n >> 8), n & 0xFF])
    return hdr + data


def mot_header(body_size, ctype, csub, params):
    ext = b"".join(params)
    hsize = 7 + len(ext)
    v = (body_size << 28) | (hsize << 15) | (ctype << 9) | csub
    return v.to_bytes(7, "big") + ext


class DataGroupEncoder:
    def __init__(self):
        self.cont = {}

    def dg(self, dgtype, tid, seg_no, last, seg_data, repetition=0):
        ci = self.cont.get(dgtype, 0)
        self.cont[dgtype] = (ci + 1) & 0xF
        h = bytes([0x70 | (dgtype & 0xF), (ci << 4) | (repetition & 0xF)])  # CRC+Seg+UA, keine Extension
        h += (((1 if last else 0) << 15) | (seg_no & 0x7FFF)).to_bytes(2, "big")
        h += bytes([0x10 | 2]) + tid.to_bytes(2, "big")  # TransportId-Flag, LI=2
        seg = (len(seg_data) & 0x1FFF).to_bytes(2, "big") + seg_data  # RepCount 0
        d = h + seg
        return d + crc16(d).to_bytes(2, "big")

    def segments(self, dgtype, tid, data, segsize=4096):
        out = []
        n = max(1, (len(data) + segsize - 1) // segsize)
        for i in range(n):
            out.append(self.dg(dgtype, tid, i, i == n - 1, data[i * segsize:(i + 1) * segsize]))
        return out


class PacketEncoder:
    SIZES = (24, 48, 72, 96)

    def __init__(self, address, max_size=96):
        self.addr = address
        self.ci = 0
        self.max_size = max_size

    def packets(self, dg):
        out = []
        pos = 0
        while pos < len(dg):
            rest = len(dg) - pos
            size = next((s for s in self.SIZES if s - 5 >= rest and s <= self.max_size), self.max_size)
            n = min(rest, size - 5)
            first = pos == 0
            last = pos + n >= len(dg)
            hdr = bytes([(self.SIZES.index(size) << 6) | (self.ci << 4) | (0x08 if first else 0) |
                         (0x04 if last else 0) | ((self.addr >> 8) & 3), self.addr & 0xFF, n & 0x7F])
            body = hdr + dg[pos:pos + n] + bytes(size - 5 - n)
            out.append(body + crc16(body).to_bytes(2, "big"))
            self.ci = (self.ci + 1) & 3
            pos += n
        return out


def mot_objects_from_files(files, kind):
    objs = []
    for fn in files:
        p = media_path(fn)
        with open(p, "rb") as f:
            body = f.read()
        ext = os.path.splitext(p)[1].lower()
        ct, cs = MOT_TYPES.get(ext, (5, 0))
        objs.append((os.path.basename(p), body, ct, cs))
    return objs


def build_packet_stream(svc, out_path):
    """Erzeugt die Paketstrom-Datei fuer einen Paketmodus-Datendienst. Rueckgabe: Info-Dict."""
    d = svc["data"]
    kind = d["kind"]
    addr = int(d["packet_address"])
    files = list(d.get("files") or [])
    if kind in ("tpeg", "journaline"):
        shutil.copyfile(media_path(files[0]), out_path)
        return {"kind": kind, "mode": "passthrough", "bytes": os.path.getsize(out_path)}
    if kind == "spi" and svc.get("spi", {}).get("logo"):
        files = files + [svc["spi"]["logo"]]
    if kind == "slideshow_packet" and not files:
        files = [os.path.join(MEDIA, "slide1.jpg")]
    if not files:
        raise RunnerError("%s: keine Dateien fuer Datendienst" % svc["key"])
    objs = mot_objects_from_files(files, kind)
    dge = DataGroupEncoder()
    # Ein Paket darf nicht ueber einen 24-ms-Rahmen hinausgehen: Groesse <= Bitrate*3 Byte (16 kbit/s -> 48 Byte)
    maxsz = max([x for x in PacketEncoder.SIZES if x <= int(d.get("bitrate", 16)) * 3] or [24])
    pe = PacketEncoder(addr, maxsz)
    out = []
    tid0 = random.randint(1, 0x7000)
    if kind in ("slideshow_packet", "raw") and kind != "spi":
        # MOT Header-Modus: je Objekt Header (DG 3) + Body (DG 4)
        for k, (name, body, ct, cs) in enumerate(objs):
            tid = tid0 + k
            params = [mot_param(0x0C, bytes([0x00]) + name.encode("latin-1", "replace")[:60])]
            if kind == "slideshow_packet":
                params.append(mot_param(0x05, b"\x00\x00\x00\x00"))  # TriggerTime = jetzt
            hdr = mot_header(len(body), ct, cs, params)
            for g in dge.segments(3, tid, hdr):
                out += pe.packets(g)
            for g in dge.segments(4, tid, body):
                out += pe.packets(g)
        mode = "header"
    else:
        # MOT Directory-Modus (SPI-Logos u. a.): Verzeichnis (DG 6) + Bodies (DG 4)
        entries = b""
        bodies = []
        for k, (name, body, ct, cs) in enumerate(objs):
            tid = tid0 + 1 + k
            params = [mot_param(0x0C, bytes([0x00]) + name.encode("latin-1", "replace")[:60])]
            hdr = mot_header(len(body), ct, cs, params)
            entries += tid.to_bytes(2, "big") + hdr
            bodies.append((tid, body))
        segsize = 4096
        dir_ext = b""
        dlen = 13 + len(dir_ext) + len(entries)
        directory = ((dlen & 0x3FFFFFFF).to_bytes(4, "big") + len(objs).to_bytes(2, "big") +
                     (100).to_bytes(3, "big") + (segsize & 0x1FFF).to_bytes(2, "big") +
                     len(dir_ext).to_bytes(2, "big") + dir_ext + entries)
        for g in dge.segments(6, tid0, directory):
            out += pe.packets(g)
        for tid, body in bodies:
            for g in dge.segments(4, tid, body, segsize):
                out += pe.packets(g)
        mode = "directory"
    with open(out_path, "wb") as f:
        f.write(b"".join(out))
    return {"kind": kind, "mode": mode, "objects": [o[0] for o in objs], "packets": len(out),
            "bytes": sum(len(p) for p in out), "address": addr}


def dabplus_pad_stats(data, bitrate):
    """Wertet einen fehlerfreien DAB+-Unterkanal (aus dem ETI) aus: Superframes, AU-CRCs, Anteil der AUs mit X-PAD,
    DLS-Segmente (X-PAD-Anwendungstyp 2/3) und MOT-Anteil. Dient als senderseitiger Nachweis des PAD-Flusses."""
    s = bitrate // 8
    sf = 120 * s
    best = None
    for off in range(0, 5 * 3 * bitrate, 3 * bitrate):
        res = {"superframes": 0, "aus": 0, "au_crc_ok": 0, "aus_with_xpad": 0, "xpad_bytes": 0, "dls_ci": 0, "mot_ci": 0}
        pos = off
        while pos + sf <= len(data):
            a = data[pos:pos + 110 * s]
            pos += sf
            b2 = a[2]
            dac, sbr = (b2 >> 6) & 1, (b2 >> 5) & 1
            nau = {(0, 1): 1 * 2, (1, 1): 3, (0, 0): 4, (1, 0): 6}[(dac, sbr)]
            hl = 3 + ((nau - 1) * 12 + 7) // 8 if nau > 1 else 3
            starts = [hl]
            bits = int.from_bytes(a[3:3 + ((nau - 1) * 12 + 7) // 8], "big") if nau > 1 else 0
            nb = ((nau - 1) * 12 + 7) // 8 * 8
            for k in range(nau - 1):
                starts.append((bits >> (nb - 12 * (k + 1))) & 0xFFF)
            starts.append(110 * s)
            res["superframes"] += 1
            for k in range(nau):
                au = a[starts[k]:starts[k + 1]]
                if len(au) < 3:
                    continue
                res["aus"] += 1
                if crc16(au[:-2]) != int.from_bytes(au[-2:], "big"):
                    continue
                res["au_crc_ok"] += 1
                # DSE am AU-Anfang: id(3)=4, tag(4), align(1), count(8)[, esc(8)]
                if au[0] >> 5 == 4:
                    cnt = au[1]
                    o = 2
                    if cnt == 255:
                        cnt += au[2]
                        o = 3
                    pad = au[o:o + cnt]
                    if len(pad) >= 2:
                        fpad = pad[-2:]
                        if (fpad[0] >> 4) & 3 and len(pad) > 2:  # X-PAD vorhanden
                            res["aus_with_xpad"] += 1
                            res["xpad_bytes"] += len(pad) - 2
                            # CI-Liste (variable X-PAD) liegt rueckwaerts vor dem F-PAD
                            if fpad[1] & 2:
                                ci = pad[-3]
                                at = ci & 0x1F
                                if at in (2, 3):
                                    res["dls_ci"] += 1
                                elif at in (12, 13):
                                    res["mot_ci"] += 1
        if best is None or res["au_crc_ok"] > best["au_crc_ok"]:
            best = res
    return best


def parse_packet_stream(data, address=None):
    """Prueft Paket-CRCs, setzt MSC-Datengruppen zusammen, prueft deren CRCs und zaehlt vollstaendige
    MOT-Objekte (Header+Body bzw. Verzeichnis+Bodies). Fuer selftest und ETI-Pruefung der Datendienste."""
    pos, bad, dgs, cur, npk, npad = 0, 0, [], {}, 0, 0
    bodies = {}
    while pos + 3 <= len(data):
        size = PacketEncoder.SIZES[data[pos] >> 6]
        p = data[pos:pos + size]
        pos += size
        if len(p) < size:
            break
        if crc16(p[:-2]) != int.from_bytes(p[-2:], "big"):
            bad += 1
            continue
        addr = ((p[0] & 3) << 8) | p[1]
        if addr == 0:
            npad += 1
            continue
        if address is not None and addr != address:
            continue
        npk += 1
        first, last, n = bool(p[0] & 8), bool(p[0] & 4), p[2] & 0x7F
        if first:
            cur[addr] = b""
        if addr not in cur:
            continue
        cur[addr] = cur.get(addr, b"") + p[3:3 + n]
        if last:
            dg = cur.pop(addr)
            ok = crc16(dg[:-2]) == int.from_bytes(dg[-2:], "big")
            d = {"type": dg[0] & 0xF, "crc_ok": ok, "len": len(dg)}
            if ok and len(dg) > 9:
                last_seg, segno = bool(dg[2] & 0x80), ((dg[2] & 0x7F) << 8) | dg[3]
                tid = int.from_bytes(dg[5:7], "big")
                seg = dg[9:-2]
                key = (d["type"], tid)
                b = bodies.setdefault(key, {})
                b[segno] = seg
                if last_seg:
                    b["last"] = segno
            dgs.append(d)
    objs = 0
    for (ty, tid), b in bodies.items():
        if ty == 4 and "last" in b and all(i in b for i in range(b["last"] + 1)):
            objs += 1
    return {"packet_crc_errors": bad, "packets": npk, "padding": npad, "datagroups": dgs, "objects": objs}


# --------------------------------------------------------------------------------------------
# Mux- und Mod-Konfiguration erzeugen
# --------------------------------------------------------------------------------------------
def write_mux(cfg, inputs, path, telnet_port, nbframes=0):
    ens = cfg["ensemble"]
    L = []
    # Modus III hat 4 FIBs je CIF; der "classic"-FIC-Scheduler von ODR-DabMux 5.5.1 kann nur 3 -> "priority"
    # Mit Service-Linking/FI/OE ebenfalls "priority": beim "classic"-Scheduler kommen FIG 0/6, 0/21, 0/24 (Rate E)
    # erst nach ~2 min erstmals, "priority" sendet sie gleich zu Beginn und dann zyklisch.
    has_link = any((s.get("linking") or {}).get(k) for s in cfg["services"]
                   for k in ("fm_pi", "fm_freqs_mhz", "other_ensembles"))
    sched = cfg.get("fic_scheduler") or ("priority" if (cfg["mode"] == 3 or has_link) else "classic")
    L.append("general {\n    dabmode %d\n    nbframes %d\n    syslog false\n    tist false\n    managementport 0\n"
             "    fic-scheduler %s\n}" % (cfg["mode"], nbframes, sched))
    L.append("remotecontrol {\n    telnetport %d\n}" % telnet_port)
    e = ["    id 0x%04X" % hexint(ens["eid"]), "    ecc 0x%02X" % hexint(ens["ecc"])]
    if ens["lto"] == "auto":
        e.append("    local-time-offset auto")
    else:
        e.append("    local-time-offset %s" % (int(ens["lto"]) / 2.0))
    e.append("    international-table %d" % int(ens.get("international_table", 1)))
    rc = ens.get("reconfig_counter", "hash")
    if rc == "hash":
        e.append("    reconfig-counter hash")
    elif rc != "off":
        e.append("    reconfig-counter %d" % int(rc))
    e.append("    label %s" % mux_str(ens["label"]))
    e.append("    shortlabel %s" % mux_str(ens["shortlabel"]))
    if ens.get("charset") == "utf8":
        e.append("    fig2_label %s" % mux_str(ens["label"]))
    if cfg["clusters"]:
        a = ["    announcements {"]
        for k in cfg["clusters"]:
            svc = [s for s in cfg["services"] if s["key"] == k["service"]][0]
            a.append("        ann_%d {\n            cluster %d\n            flags {\n%s\n            }\n"
                     "            subchannel sub_%s\n        }" %
                     (k["id"], k["id"], "\n".join("                %s true" % f for f in k["flags"]), svc["key"]))
        a.append("    }")
        e += a
    L.append("ensemble {\n" + "\n".join(e) + "\n}")
    # Dienste
    S = []
    for s in cfg["services"]:
        x = ["        id %s" % s["sid"], "        label %s" % mux_str(s["label"]),
             "        shortlabel %s" % mux_str(s["shortlabel"]),
             "        pty %d" % int(s["pty"]), "        pty-sd %s" % ("dynamic" if s.get("pty_dynamic") else "static"),
             "        language %s" % s.get("language", "0")]
        if s.get("ecc"):
            x.append("        ecc 0x%02X" % hexint(s["ecc"]))
        if ens.get("charset") == "utf8":
            x.append("        fig2_label %s" % mux_str(s["label"]))
        ann = s.get("announcements") or {}
        sup = ann.get("supported") or []
        clus = [int(hexint(c)) for c in (ann.get("clusters") or []) if int(hexint(c)) != 255]
        if sup or clus:
            x.append("        announcements {")
            for f in ANN_FLAGS:
                x.append("            %s %s" % (f, "true" if f in sup else "false"))
            if clus:
                x.append("            clusters %s" % mux_str(",".join(str(c) for c in clus)))
            x.append("        }")
        S.append("    srv_%s {\n%s\n    }" % (s["key"], "\n".join(x)))
    L.append("services {\n" + "\n".join(S) + "\n}")
    # Unterkanaele
    U = []
    for s in cfg["services"]:
        p = s["protection"]
        if s["type"] == "dabplus":
            t, br = "dabplus", s["audio"]["bitrate"]
        elif s["type"] == "dab":
            t, br = "audio", s["audio"]["bitrate"]
        else:
            t, br = "packet", int(s["data"]["bitrate"])
        x = ["        type %s" % t, "        bitrate %d" % br, "        id %d" % s["_subch"],
             "        protection-profile %s" % p["profile"], "        protection %d" % p["level"],
             "        inputproto file", "        inputuri %s" % mux_str(inputs[s["key"]])]
        if t == "packet":
            x.append("        load_entire_file true")
        else:
            x.append("        nonblock false")
        U.append("    sub_%s {\n%s\n    }" % (s["key"], "\n".join(x)))
    L.append("subchannels {\n" + "\n".join(U) + "\n}")
    # Komponenten
    C = []
    for s in cfg["services"]:
        x = ["        service srv_%s" % s["key"], "        subchannel sub_%s" % s["key"]]
        uas = []
        if s["type"] == "data":
            kind = s["data"]["kind"]
            x.append("        type %d" % (60 if kind in ("spi", "slideshow_packet", "raw") else 0))
            x.append("        address %d" % int(s["data"]["packet_address"]))
            x.append("        datagroup true")
            uas = {"spi": ["spi"], "slideshow_packet": ["slideshow"], "journaline": ["journaline"]}.get(kind, [])
        elif s["pad"]["enable"] and s["pad"]["sls"]["enable"]:
            uas = ["slideshow"]
        if uas:
            x.append("        user-applications {\n%s\n        }" %
                     "\n".join("            userapp %s" % mux_str(u) for u in uas))
        C.append("    comp_%s {\n%s\n    }" % (s["key"], "\n".join(x)))
    L.append("components {\n" + "\n".join(C) + "\n}")
    # Service-Linking (FIG 0/6), FI (FIG 0/21), OE (FIG 0/24)
    LS, FI, OS = [], [], []
    for s in cfg["services"]:
        lk = s.get("linking") or {}
        sid = hexint(s["sid"])
        if lk.get("fm_pi"):
            pi = hexint(lk["fm_pi"])
            LS.append("    set_%s {\n        lsn 0x%03X\n        hard true\n        active true\n"
                      "        international false\n        keyservice srv_%s\n        list {\n"
                      "            fm_%s {\n                type fm\n                id 0x%04X\n            }\n"
                      "        }\n    }" % (s["key"], sid & 0xFFF, s["key"], s["key"], pi))
            if lk.get("fm_freqs_mhz"):
                FI.append("    fi_fm_%s {\n        oe false\n        range_modulation fm\n        pi_code 0x%04X\n"
                          "        continuity true\n        frequencies %s\n    }" %
                          (s["key"], pi, mux_str(" ".join("%.3f" % (float(f) + 0.004) for f in lk["fm_freqs_mhz"]))))
                # +0,004 MHz: ODR-DabMux schneidet (f-87,5)*10 ab statt zu runden (87,6 -> 87,5 ohne Zuschlag)
        if lk.get("other_ensembles") and s["type"] != "data":
            oes = [hexint(ens["eid"])] + [hexint(x) for x in lk["other_ensembles"]]
            OS.append("    oe_%s {\n        id %s\n        other_ensembles %s\n    }" %
                      (s["key"], s["sid"], mux_str(",".join("0x%04X" % x for x in oes))))
    if any((s.get("linking") or {}).get("fm_freqs_mhz") or (s.get("linking") or {}).get("other_ensembles")
           for s in cfg["services"]):
        # eigene Ensemble-Frequenz ebenfalls signalisieren
        f_mhz = float(cfg["rf"]["freq_hz"]) / 1e6
        FI.insert(0, "    fi_dab_own {\n        oe false\n        range_modulation dab\n        eid 0x%04X\n"
                     "        continuity true\n        frequencies {\n            f1 {\n"
                     "                signal_mode_1 %s\n                adjacent true\n"
                     "                frequency %.3f\n            }\n        }\n    }" %
                  (hexint(ens["eid"]), "true" if cfg["mode"] == 1 else "false", f_mhz))
    if LS:
        L.append("linking {\n" + "\n".join(LS) + "\n}")
    if FI:
        L.append("frequency_information {\n" + "\n".join(FI) + "\n}")
    if OS:
        L.append("other-services {\n" + "\n".join(OS) + "\n}")
    L.append('outputs {\n    stdout "fifo:///dev/stdout?type=raw"\n}')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def write_mod_ini(cfg, path, rate, gain, out="/dev/stdout", telnet_port=0, cfr=None):
    tii = cfg["tii"]
    cfr = cfg["rf"].get("cfr") if cfr is None else cfr
    clip = cfr_clip(cfg) if cfr else 0
    ini = """[remotecontrol]
telnet={tel}
telnetport={port}
zmqctrl=0

[log]
syslog=0
filelog=0

[input]
transport=file
source=/dev/stdin
loop=0

[modulator]
mode={mode}
gainmode=var
digital_gain={gain:.6f}
rate={rate}

[cfr]
enabled={cfr}
clip={clip:.1f}
error_clip={eclip:.3f}

[firfilter]
enabled=1

[output]
output=file

[fileoutput]
filename={out}
format=s16

[tii]
enable={tii_en}
comb={comb}
pattern={pattern}
old_variant={old}
""".format(tel=1 if telnet_port else 0, port=telnet_port or 0, mode=cfg["mode"], gain=gain, rate=rate,
           cfr=1 if cfr else 0, clip=clip, eclip=float((cfg.get("rf") or {}).get("cfr_error_clip", 0.1)), out=out, tii_en=1 if tii["enable"] else 0, comb=int(tii["comb"]),
           pattern=int(tii["pattern"]), old=1 if tii.get("old_variant") else 0)
    with open(path, "w") as f:
        f.write(ini)


CARRIERS = {1: 1536, 2: 384, 3: 192, 4: 768}


def cfr_clip(cfg):
    """CFR-Schwelle im internen dabmod-Massstab (Ausgang der unnormierten IFFT, RMS = sqrt(Traegerzahl)).
    Standard: Spitzen auf (backoff_db - 0,5 dB) ueber RMS begrenzen, damit int16 nicht clippt.
    rf.cfr_clip (absolut) oder rf.cfr_papr_db (Ziel-PAPR) ueberschreiben das."""
    if cfg["rf"].get("cfr_clip"):
        return float(cfg["rf"]["cfr_clip"])
    papr = float(cfg["rf"].get("cfr_papr_db") or (float(cfg["rf"].get("backoff_db", 12)) - 0.5))
    return math.sqrt(CARRIERS.get(cfg["mode"], 1536)) * 10 ** (papr / 20.0)


# --------------------------------------------------------------------------------------------
# Pegelkalibrierung (digital_gain fuer gewuenschten Backoff)
# --------------------------------------------------------------------------------------------
def calib_key(mode, rate):
    st = os.stat(tool("odr-dabmod"))
    return "%s-%s-m%d-r%d-%d" % (platform.system(), platform.machine(), mode, rate, int(st.st_mtime))


def calibrate(mode, rate):
    """Misst RMS (dBFS, komplex) der s16-Ausgabe bei digital_gain=1. Ergebnis wird gecacht."""
    os.makedirs(CACHE, exist_ok=True)
    cpath = os.path.join(CACHE, "calib.json")
    try:
        with open(cpath) as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    key = calib_key(mode, rate)
    if key in cache:
        return cache[key]
    work = tempfile.mkdtemp(prefix="dablab-cal-", dir=TMPROOT)
    try:
        mux = os.path.join(work, "c.mux")
        with open(mux, "w") as f:
            f.write("general {\n dabmode %d\n nbframes 120\n syslog false\n tist false\n fic-scheduler priority\n}\n"
                    "remotecontrol {\n telnetport 0\n}\n"
                    "ensemble {\n id 0x4FFF\n ecc 0xE0\n label \"CAL\"\n shortlabel \"CAL\"\n}\n"
                    "services {\n s1 {\n id 0xD0FF\n label \"CAL\"\n }\n}\n"
                    "subchannels {\n p1 {\n type data\n bitrate 576\n id 1\n protection 3\n inputproto prbs\n"
                    " inputuri \"prbs://\"\n }\n}\n"
                    "components {\n c1 {\n service s1\n subchannel p1\n }\n}\n"
                    "outputs {\n o \"file://%s/c.eti?type=raw\"\n}\n" % (mode, work))
        subprocess.run([tool("odr-dabmux"), mux], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, check=True)
        dummy = {"mode": mode, "tii": {"enable": False, "comb": 1, "pattern": 1}, "rf": {"cfr": False}}
        ini = os.path.join(work, "c.ini")
        write_mod_ini(dummy, ini, rate, 1.0, out=os.path.join(work, "c.s16"))
        with open(os.path.join(work, "c.eti"), "rb") as fin:
            subprocess.run([tool("odr-dabmod"), ini], stdin=fin, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120, check=True)
        with open(os.path.join(work, "c.s16"), "rb") as f:
            raw = f.read()
        st = level_stats(raw[len(raw) // 4:])
        res = {"rms_dbfs_at_gain1": st["rms_dbfs"], "peak_dbfs_at_gain1": st["peak_dbfs"]}
        cache[key] = res
        with open(cpath, "w") as f:
            json.dump(cache, f, indent=1)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def gain_for(cfg, rate):
    cal = calibrate(cfg["mode"], rate)
    target = -float(cfg["rf"]["backoff_db"])
    g = 10 ** ((target - cal["rms_dbfs_at_gain1"]) / 20.0)
    return g, cal, target


def level_stats(raw):
    """RMS/Spitze (dBFS bezogen auf 32767) und Clip-Zaehler fuer int16-IQ-Bytes."""
    n = len(raw) // 4 * 4
    if n == 0:
        return {"rms_dbfs": None, "peak_dbfs": None, "clips": 0, "samples": 0}
    if np is not None:
        x = np.frombuffer(raw[:n], dtype="<i2").astype(np.float32)
        p2 = x[0::2] ** 2 + x[1::2] ** 2
        rms = math.sqrt(float(p2.mean()))
        pk = math.sqrt(float(p2.max()))
        clips = int(np.count_nonzero((x >= 32767) | (x <= -32768)))
        ns = len(p2)
    else:
        import array
        a = array.array("h")
        a.frombytes(raw[:n])
        if sys.byteorder != "little":
            a.byteswap()
        step = max(1, len(a) // 200000) * 2
        acc, pk2, clips, ns = 0.0, 0.0, 0, 0
        for i in range(0, len(a) - 1, step):
            v = a[i] * a[i] + a[i + 1] * a[i + 1]
            acc += v
            pk2 = max(pk2, v)
            if a[i] in (32767, -32768) or a[i + 1] in (32767, -32768):
                clips += 1
            ns += 1
        rms, pk = math.sqrt(acc / max(ns, 1)), math.sqrt(pk2)
    db = lambda v: round(20 * math.log10(max(v, 1e-9) / 32767.0), 2)
    return {"rms_dbfs": db(rms), "peak_dbfs": db(pk), "crest_db": round(db(pk) - db(rms), 2),
            "clips": clips, "samples": ns}


# --------------------------------------------------------------------------------------------
# Audioquellen (ffmpeg)
# --------------------------------------------------------------------------------------------
def ffmpeg_cmd(a, rate):
    src = a["source"]
    kind = src.get("kind", "tone")
    lvl = float(src.get("level_dbfs", -18))
    A = 10 ** (lvl / 20.0)
    ch = int(a["channels"])
    ff = [tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error"]
    lav = None
    if kind == "file":
        p = media_path(src["file"])
        if src.get("loop", True):
            ff += ["-stream_loop", "-1", "-i", p]
            af = None
        else:
            ff += ["-i", p]
            af = "apad"
        if lvl != -18 and src.get("normalize"):
            af = (af + "," if af else "") + "volume=%.2fdB" % 0
        if af:
            ff += ["-af", af]
    else:
        f = float(src.get("freq_hz", 1000))
        if kind == "tone":
            e = "%.6f*sin(2*PI*%g*t)" % (A, f)
            lav = "aevalsrc=exprs='%s|%s':s=%d" % (e, e, rate)
        elif kind == "silence":
            lav = "anullsrc=r=%d:cl=stereo" % rate
        elif kind in ("noise", "pinknoise"):
            amp = min(1.0, A * math.sqrt(3.0))
            lav = "anoisesrc=r=%d:color=%s:amplitude=%.6f,pan=stereo|c0=c0|c1=c0" % (
                rate, "pink" if kind == "pinknoise" else "white", amp)
        elif kind == "sweep":
            f0 = float(src.get("sweep_from_hz", 20))
            f1 = float(src.get("sweep_to_hz", min(20000, rate / 2 - 100)))
            T = float(src.get("sweep_s", 10))
            k = f1 / f0
            e = "%.6f*sin(2*PI*%g*%g/log(%g)*(pow(%g,mod(t,%g)/%g)-1))" % (A, f0, T, k, k, T, T)
            lav = "aevalsrc=exprs='%s|%s':s=%d" % (e, e, rate)
        elif kind == "lr_test":
            fl = float(src.get("left_hz", 1000))
            fr = float(src.get("right_hz", 400))
            lav = "aevalsrc=exprs='%.6f*sin(2*PI*%g*t)|%.6f*sin(2*PI*%g*t)':s=%d" % (A, fl, A, fr, rate)
        elif kind == "polarity":
            # gleichanteilfreier asymmetrischer Puls (positiv kurz/hoch, negativ lang/flach), beide Kanaele gleichphasig
            e = "if(lt(mod(t*%g,1),0.1),%.6f,%.6f)" % (f if f < 500 else 100, A, -A / 9)
            lav = "aevalsrc=exprs='%s|%s':s=%d" % (e, e, rate)
        ff += ["-f", "lavfi", "-i", lav]
    ff += ["-vn", "-ac", str(ch), "-ar", str(rate), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    return ff


def audioenc_cmd(s, out_path, pad_ident):
    a = s["audio"]
    cmd = [tool("odr-audioenc"), "-f", "raw", "-i", "-", "-r", str(a["samplerate"]), "-c", str(a["channels"]),
           "-b", str(a["bitrate"])]
    if s["type"] == "dab":
        cmd += ["-a", "--dabmode=%s" % a["mp2_mode"]]
    else:
        cmd += {"aac-lc": ["--aaclc"], "he-aac": ["--sbr"], "he-aac-v2": ["--ps"]}[a["codec"]]
        if int(a.get("bandwidth_hz") or 0) > 0:
            cmd += ["-B", str(int(a["bandwidth_hz"]))]
    if float(a.get("gain_db") or 0) != 0:
        cmd += ["-g", str(float(a["gain_db"]))]
    if pad_ident:
        cmd += ["-p", str(int(s["pad"]["bytes"])), "-P", pad_ident]
    cmd += ["-o", out_path]
    return cmd


def padenc_cmd(s, pad_ident, dls_file, slide_dir, render):
    pad = s["pad"]
    dls, sls = pad["dls"], pad["sls"]
    cmd = [tool("odr-padenc"), "-o", pad_ident]
    if dls.get("enable", True):
        cmd += ["-t", dls_file]
        if dls.get("charset") == "utf8":
            cmd += ["-C", "-c", "15"]
        if dls.get("remove_label"):
            cmd += ["-r"]
        cmd += ["-L", "150" if render else "1200"]
    if sls.get("enable"):
        # -s 0 verdraengt in ODR-PadEnc das DLS vollstaendig (EncodeSlide() kehrt vorzeitig zurueck) -> mind. 1 s
        cmd += ["-d", slide_dir, "-s", "1" if render else str(max(1, int(sls.get("interval_s", 10)))),
                "-m", str(int(sls.get("max_bytes", 50000)))]
        if sls.get("raw"):
            cmd += ["-R"]
    return cmd


# --------------------------------------------------------------------------------------------
# Kette (live/render)
# --------------------------------------------------------------------------------------------
# --------------------------------------------------------------------------------------------
# Playlist (Audioquelle "playlist", SPEC-Ergaenzung 23.10.)
# --------------------------------------------------------------------------------------------
def _ffprobe_tags(path):
    fp = tool("ffprobe", required=False)
    if not fp:
        return {}
    try:
        r = subprocess.run([fp, "-v", "quiet", "-print_format", "json", "-show_format", path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        f = json.loads(r.stdout.decode("utf-8", "replace") or "{}").get("format", {})
        tags = {k.lower(): v for k, v in (f.get("tags") or {}).items()}
        return {"title": tags.get("title"), "artist": tags.get("artist") or tags.get("album_artist"),
                "album": tags.get("album"), "duration_s": round(float(f["duration"]), 2) if f.get("duration") else None}
    except Exception:
        return {}


def playlist_path(name):
    if not re.match(r"^[A-Za-z0-9_.\- ]+$", name or ""):
        raise RunnerError("Playlist-Name ungueltig: %r" % name)
    return os.path.join(PLAYLISTS, name + ".json")


def load_playlist(name, probe=False):
    p = playlist_path(name)
    if not os.path.isfile(p):
        raise RunnerError("Playlist '%s' nicht gefunden (%s)" % (name, p))
    with open(p, encoding="utf-8") as f:
        pl = json.load(f)
    pl.setdefault("name", name)
    items = []
    for it in pl.get("items", []):
        if isinstance(it, str):
            it = {"file": it}
        it = dict(it)
        if probe and (not it.get("title") or not it.get("artist") or not it.get("duration_s")):
            tg = _ffprobe_tags(media_path(it["file"]))
            for k in ("title", "artist", "album", "duration_s"):
                if not it.get(k) and tg.get(k):
                    it[k] = tg[k]
        if not it.get("title"):
            it["title"] = os.path.splitext(os.path.basename(it["file"]))[0]
        items.append(it)
    pl["items"] = items
    return pl


class PlaylistFeeder:
    """Spielt die Titel einer Playlist nacheinander (ffmpeg je Titel -> s16le) in dieselbe PCM-Pipe des Encoders.
    Nahtlos und sample-genau, weil alles in einen Datenstrom geschrieben wird. Bei Titelwechsel: DLS + DL Plus."""

    def __init__(self, chain, svc, pipe):
        self.chain, self.svc, self.pipe = chain, svc, pipe
        self.src = svc["audio"]["source"]
        self.pl = load_playlist(self.src["playlist"], probe=True)
        self.n = len(self.pl["items"])
        self.order = list(range(self.n))
        if self.src.get("shuffle"):
            random.shuffle(self.order)
        self.pos = 0
        self.proc = None
        self.cur = None
        self.bytes_track = 0
        self.key = svc["key"]
        self.cmdfile = os.path.join(chain.sdir, "playlist_%s.cmd" % self.key)
        self.npfile = os.path.join(chain.sdir, "now_playing_%s.json" % self.key)
        self.rate = int(svc["audio"]["samplerate"])
        self.ch = int(svc["audio"]["channels"])

    def now_playing(self):
        it = self.pl["items"][self.cur] if self.cur is not None else {}
        el = self.bytes_track / float(self.rate * self.ch * 2)
        return {"service": self.key, "index": self.cur, "title": it.get("title"), "artist": it.get("artist"),
                "file": it.get("file"), "elapsed_s": round(el, 1), "duration_s": it.get("duration_s"),
                "playlist": self.pl.get("name"), "position": self.pos}

    def _cmd(self):
        try:
            with open(self.cmdfile) as f:
                c = json.load(f)
            os.unlink(self.cmdfile)
            return c
        except Exception:
            return None

    def _start(self, idx):
        it = self.pl["items"][idx]
        self.cur = idx
        self.bytes_track = 0
        af = []
        if self.src.get("normalize", True):
            af.append("loudnorm=I=-23:TP=-1.5:LRA=11")
        af.append("aresample=%d" % self.rate)
        cmd = [tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", media_path(it["file"]),
               "-vn", "-af", ",".join(af), "-ac", str(self.ch), "-ar", str(self.rate), "-f", "s16le",
               "-acodec", "pcm_s16le", "pipe:1"]
        lf = open(os.path.join(self.chain.sdir, "playlist_%s.log" % self.key), "ab")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf, stdin=subprocess.DEVNULL)
        lf.close()
        np_ = self.now_playing()
        try:
            atomic_write(self.npfile, json.dumps(np_, ensure_ascii=False))
        except Exception:
            pass
        log("info", "now_playing", now_playing=np_)
        manual = os.path.join(self.chain.sdir, "dls_%s.manual" % self.key)
        if self.src.get("dls_from_tags", True) and self.svc["pad"]["enable"] and not os.path.exists(manual):
            artist, title = (it.get("artist") or "").strip(), (it.get("title") or "").strip()
            text = "%s - %s" % (artist, title) if artist else title
            atomic_write(os.path.join(self.chain.sdir, "dls_%s.txt" % self.key),
                         build_dls(text, {"enable": True, "artist": artist, "title": title}))

    def kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass

    def _write(self, data):
        mv = memoryview(data)
        while mv:
            k = os.write(self.pipe.fileno(), mv)
            mv = mv[k:]

    def run(self):
        silence = bytes(self.rate * self.ch * 2 // 10)
        try:
            self._start(self.order[self.pos])
            while not self.chain.stop_evt.is_set():
                c = self._cmd()
                if c:
                    self.kill()
                    a = c.get("action")
                    if a == "next":
                        self.pos = (self.pos + 1) % self.n
                    elif a == "prev":
                        self.pos = (self.pos - 1) % self.n
                    elif a == "jump":
                        idx = int(c.get("index") or 0) % self.n
                        self.pos = self.order.index(idx)
                    self._start(self.order[self.pos])
                    continue
                if self.proc is None:  # Ende der Playlist ohne loop: Stille
                    self._write(silence)
                    continue
                d = self.proc.stdout.read(32768)
                if d:
                    self._write(d)
                    self.bytes_track += len(d)
                    continue
                self.proc.wait()
                self.pos += 1
                if self.pos >= self.n:
                    if not self.src.get("loop", True):
                        self.proc, self.cur = None, None
                        log("info", "Playlist zu Ende (loop=false), sende Stille", service=self.key)
                        continue
                    self.pos = 0
                    if self.src.get("shuffle"):
                        random.shuffle(self.order)
                self._start(self.order[self.pos])
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            self.kill()
            try:
                self.pipe.close()
            except Exception:
                pass


def parse_m3u(text):
    items, meta = [], {}
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            body = line[8:]
            dur, _, rest = body.partition(",")
            try:
                v = float(dur.split()[0])
                meta["duration_s"] = v if v > 0 else None
            except Exception:
                pass
            if " - " in rest:
                meta["artist"], meta["title"] = [x.strip() for x in rest.split(" - ", 1)]
            elif rest.strip():
                meta["title"] = rest.strip()
            continue
        if line.startswith("#"):
            continue
        items.append(dict(meta, file=_import_path(line)))
        meta = {}
    return items


def parse_pls(text):
    ent = {}
    for line in text.splitlines():
        m = re.match(r"^(File|Title|Length)(\d+)=(.*)$", line.strip(), re.I)
        if m:
            ent.setdefault(int(m.group(2)), {})[m.group(1).lower()] = m.group(3).strip()
    items = []
    for k in sorted(ent):
        e = ent[k]
        if "file" not in e:
            continue
        it = {"file": _import_path(e["file"])}
        if e.get("title"):
            if " - " in e["title"]:
                it["artist"], it["title"] = [x.strip() for x in e["title"].split(" - ", 1)]
            else:
                it["title"] = e["title"]
        try:
            if float(e.get("length", -1)) > 0:
                it["duration_s"] = float(e["length"])
        except ValueError:
            pass
        items.append(it)
    return items


def _import_path(p):
    """Pfade aus M3U/PLS: relativ -> relativ zu media/ (gespeichert als 'media/...'), absolut bleibt."""
    p = p.replace("\\", "/")
    if p.startswith("file://"):
        p = p[7:]
    if os.path.isabs(p) or re.match(r"^[A-Za-z]:/", p):
        return p
    return p if p.startswith("media/") else "media/" + p


def export_m3u(pl):
    out = ["#EXTM3U", "#PLAYLIST:%s" % pl.get("name", "")]
    for it in pl["items"]:
        dur = int(round(it.get("duration_s") or -1))
        name = ("%s - %s" % (it["artist"], it["title"])) if it.get("artist") else it.get("title", "")
        out.append("#EXTINF:%d,%s" % (dur, name))
        f = it["file"]
        out.append(f[6:] if f.startswith("media/") else f)
    return "\n".join(out) + "\n"


def cmd_playlist(args):
    os.makedirs(PLAYLISTS, exist_ok=True)
    if args.action == "list":
        names = sorted(os.path.splitext(f)[0] for f in os.listdir(PLAYLISTS) if f.endswith(".json"))
        emit_json({"ok": True, "playlists": names, "dir": PLAYLISTS})
        return 0
    if args.action == "import":
        if not args.args:
            raise RunnerError("playlist import <datei.m3u|.m3u8|.pls> [name]")
        src = args.args[0]
        name = args.args[1] if len(args.args) > 1 else os.path.splitext(os.path.basename(src))[0]
        with open(src, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        is_pls = src.lower().endswith(".pls") or text.lstrip().lower().startswith("[playlist]")
        items = parse_pls(text) if is_pls else parse_m3u(text)
        pl = {"name": name, "items": items}
        with open(playlist_path(name), "w", encoding="utf-8") as f:
            json.dump(pl, f, indent=1, ensure_ascii=False)
        missing = [it["file"] for it in items if not os.path.isfile(media_path(it["file"]))]
        emit_json({"ok": True, "name": name, "items": len(items), "missing_files": missing, "file": playlist_path(name)})
        return 0
    if args.action == "export":
        if not args.args:
            raise RunnerError("playlist export <name> [datei.m3u8]")
        pl = load_playlist(args.args[0], probe=True)
        txt = export_m3u(pl)
        if len(args.args) > 1:
            with open(args.args[1], "w", encoding="utf-8") as f:
                f.write(txt)
            emit_json({"ok": True, "out": args.args[1], "items": len(pl["items"])})
        else:
            sys.stdout.write(txt)
        return 0
    if args.action == "show":
        emit_json({"ok": True, "playlist": load_playlist(args.args[0], probe=True)})
        return 0
    return 2


class Chain:
    def __init__(self, cfg, session_id, render=False):
        self.cfg = cfg
        self.sid = session_id
        self.render = render
        self.procs = []  # (name, Popen)
        self.fifos = []
        self.sdir = os.path.join(SESSIONS, session_id)
        os.makedirs(self.sdir, exist_ok=True)
        self.work = tempfile.mkdtemp(prefix="dablab-%s-" % session_id[-6:], dir=TMPROOT)
        self.stop_evt = threading.Event()
        self.threads = []
        self.pad_idents = []
        self.mux_port = None
        self.mod_port = None
        self.log_threads = []
        self.feeders = {}
        self.fifo_fds = []

    # -- Prozesse --
    def spawn(self, name, cmd, stdin=None, stdout=None, cwd=None):
        logpath = os.path.join(self.sdir, name + ".log")
        lf = open(logpath, "ab")
        lf.write(("\n=== %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(cmd))).encode())
        lf.flush()
        p = subprocess.Popen(cmd, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                             stdout=stdout if stdout is not None else lf, stderr=subprocess.PIPE,
                             cwd=cwd or self.work, close_fds=True)
        t = threading.Thread(target=self._pump_stderr, args=(name, p, lf), daemon=True)
        t.start()
        self.log_threads.append(t)
        self.procs.append((name, p))
        return p

    _ERR_RE = re.compile(r"(error|fail|cannot|can't|could not|exception|abort|invalid|not reachable)", re.I)
    _IGN_RE = re.compile(r"(Interrupted system call|exits|PAD socket poll error|Terminating|End of input)", re.I)

    def _pump_stderr(self, name, p, lf):
        n_fw = 0
        try:
            for line in iter(p.stderr.readline, b""):
                lf.write(line)
                lf.flush()
                s = line.decode("utf-8", "replace").strip()
                if s and self._ERR_RE.search(s) and not self._IGN_RE.search(s) and n_fw < 50 \
                        and not self.stop_evt.is_set():
                    n_fw += 1
                    log("warn", "%s: %s" % (name, s[:300]))
        except Exception:
            pass
        finally:
            try:
                lf.close()
            except Exception:
                pass

    def mkfifo(self, name, size=None):
        p = os.path.join(self.work, name)
        os.mkfifo(p, 0o600)
        self.fifos.append(p)
        if IS_LINUX and size:
            # Linux: FIFO-Puffer verkleinern (Standard 64 KiB = mehrere Sekunden bei niedriger Bitrate -> Latenz
            # fuer ctl/now_playing). Dazu ein eigenes O_RDWR-fd offen halten, damit die Groesse bestehen bleibt.
            try:
                fd = os.open(p, os.O_RDWR | os.O_NONBLOCK)
                set_pipe_size(fd, size)
                self.fifo_fds.append(fd)
            except OSError:
                pass
        return p

    # -- Aufbau --
    def start(self, rate, gain, out_fd=None):
        cfg = self.cfg
        inputs = {}
        self.info = {"services": {}}
        # 1) PAD-Encoder und Datendateien
        for s in cfg["services"]:
            key = s["key"]
            if s["type"] == "data":
                pk = os.path.join(self.sdir, "data_%s.pkt" % key)
                self.info["services"][key] = build_packet_stream(s, pk)
                inputs[key] = pk
                continue
            pad_ident = None
            if s["pad"]["enable"]:
                pad_ident = "dablab_%s_%s" % (self.sid.replace("-", "")[-10:], key)
                self.pad_idents.append(pad_ident)
                dls_file = os.path.join(self.sdir, "dls_%s.txt" % key)
                d = s["pad"]["dls"]
                atomic_write(dls_file, build_dls(d.get("text", ""), d.get("dlplus")))
                slide_dir = os.path.join(self.sdir, "slides_%s" % key)
                os.makedirs(slide_dir, exist_ok=True)
                for old in glob.glob(os.path.join(slide_dir, "*")):
                    os.remove(old)
                if s["pad"]["sls"]["enable"]:
                    slides = s["pad"]["sls"]["slides"] or [os.path.join(MEDIA, "slide1.jpg")]
                    for i, fn in enumerate(slides):
                        src = media_path(fn)
                        if os.path.isfile(src):
                            shutil.copyfile(src, os.path.join(slide_dir, "%02d_%s" % (i, os.path.basename(src))))
                self.spawn("padenc_%s" % key, padenc_cmd(s, pad_ident, dls_file, slide_dir, self.render))
            # ca. 0,25 s Audio im FIFO (mind. eine Seite = 4096 Byte)
            fifo = self.mkfifo("in_%s.%s" % (key, "mp2" if s["type"] == "dab" else "dabp"),
                               size=max(4096, int(s["audio"]["bitrate"]) * 125 // 4))
            inputs[key] = fifo
            s["_pad_ident"] = pad_ident
        if self.pad_idents:
            time.sleep(0.4)  # PadEnc-Sockets anlegen lassen
        # 2) Audioketten
        for s in cfg["services"]:
            if s["type"] == "data":
                continue
            key = s["key"]
            if s["audio"]["source"].get("kind") == "playlist":
                ae = self.spawn("audioenc_%s" % key, audioenc_cmd(s, inputs[key], s.get("_pad_ident")),
                                stdin=subprocess.PIPE)
                set_pipe_size(ae.stdin.fileno(), 16384)
                fe = PlaylistFeeder(self, s, ae.stdin)
                self.feeders[key] = fe
                th = threading.Thread(target=fe.run, daemon=True)
                th.start()
                self.threads.append(th)
                continue
            ff = self.spawn("ffmpeg_%s" % key, ffmpeg_cmd(s["audio"], s["audio"]["samplerate"]),
                            stdout=subprocess.PIPE)
            set_pipe_size(ff.stdout.fileno(), 65536)
            self.spawn("audioenc_%s" % key, audioenc_cmd(s, inputs[key], s.get("_pad_ident")), stdin=ff.stdout)
            ff.stdout.close()
        # 3) Mux -> Mod
        self.mux_port = free_port(12721)
        muxfile = os.path.join(self.sdir, "dablab.mux")
        write_mux(cfg, inputs, muxfile, self.mux_port)
        r_mux, w_mux = os.pipe()
        set_pipe_size(w_mux, 65536)
        mux = self.spawn("dabmux", [tool("odr-dabmux"), muxfile], stdout=w_mux)
        os.close(w_mux)
        self.mod_port = free_port(12722 if self.mux_port == 12721 else None)
        ini = os.path.join(self.sdir, "dabmod.ini")
        write_mod_ini(cfg, ini, rate, gain, telnet_port=self.mod_port)
        if out_fd is None:
            r_out, w_out = os.pipe()
        else:
            r_out, w_out = None, out_fd
        if self.render:
            # ETI-Abgriff: FIC des gesendeten Multiplex mitschneiden (FIG 0/6, 0/21, 0/24 kommen nur alle 2 min)
            r_mod, w_tap = os.pipe()
            t = threading.Thread(target=self._eti_tap, args=(r_mux, w_tap), daemon=True)
            t.start()
            self.threads.append(t)
            mod = self.spawn("dabmod", [tool("odr-dabmod"), ini], stdin=r_mod, stdout=w_out)
            os.close(r_mod)
        else:
            mod = self.spawn("dabmod", [tool("odr-dabmod"), ini], stdin=r_mux, stdout=w_out)
            os.close(r_mux)
        if r_out is not None:
            os.close(w_out)
        self.out_fd = r_out
        return r_out

    def _eti_tap(self, r_fd, w_fd):
        self.eti_fic = bytearray()
        self.eti_subch = {s["_subch"]: bytearray() for s in self.cfg["services"]
                          if s["type"] == "data" or (s["type"] == "dabplus" and s["pad"]["enable"])}
        self.eti_frames = 0
        buf = b""
        try:
            while True:
                d = os.read(r_fd, 65536)
                if not d:
                    break
                mv = memoryview(d)
                while mv:
                    k = os.write(w_fd, mv)
                    mv = mv[k:]
                buf += d
                while len(buf) >= 6144:
                    fr, buf = buf[:6144], buf[6144:]
                    self.eti_frames += 1
                    if len(self.eti_fic) < 4 * 1024 * 1024:
                        ficf, nst, mid = fr[5] >> 7, fr[5] & 0x7F, (fr[6] >> 3) & 3
                        ficl = (128 if mid == 3 else 96) if ficf else 0
                        o = 8 + 4 * nst + 4
                        self.eti_fic += fr[o:o + ficl]
                        # MST: Paket-Unterkanaele mitschneiden (Pruefung der Datendienste auf ETI-Ebene)
                        if self.eti_subch:
                            q = o + ficl
                            for k in range(nst):
                                sc = fr[8 + 4 * k:12 + 4 * k]
                                scid = sc[0] >> 2
                                stl = ((sc[2] & 3) << 8) | sc[3]
                                n = stl * 8
                                if scid in self.eti_subch and len(self.eti_subch[scid]) < 2 * 1024 * 1024:
                                    self.eti_subch[scid] += fr[q:q + n]
                                q += n
        except OSError:
            pass
        finally:
            for f in (r_fd, w_fd):
                try:
                    os.close(f)
                except OSError:
                    pass

    def write_session(self, extra=None):
        d = {"session": self.sid, "pid": os.getpid(), "started": time.time(), "work": self.work,
             "mux_telnet": self.mux_port, "mod_telnet": self.mod_port,
             "services": {s["key"]: {"sid": s["sid"], "type": s["type"],
                                     "dls_file": os.path.join(self.sdir, "dls_%s.txt" % s["key"]),
                                     "slide_dir": os.path.join(self.sdir, "slides_%s" % s["key"]),
                                     "pad": bool(s.get("pad", {}).get("enable")),
                                     "playlist": (s.get("audio") or {}).get("source", {}).get("kind") == "playlist",
                                     "dlplus": (s.get("pad", {}).get("dls", {}).get("dlplus") or {})}
                          for s in self.cfg["services"]},
             "clusters": [k["id"] for k in self.cfg["clusters"]], "running": True}
        if extra:
            d.update(extra)
        atomic_write(os.path.join(self.sdir, "session.json"), json.dumps(d, indent=1))
        with open(os.path.join(self.sdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=1, ensure_ascii=False)

    def start_rotation(self):
        for s in self.cfg["services"]:
            if s["type"] == "data" or not s["pad"]["enable"]:
                continue
            d = s["pad"]["dls"]
            texts = [t for t in (d.get("rotate") or []) if t]
            if not texts:
                continue
            t = threading.Thread(target=self._rotate, args=(s, texts, max(2.0, float(d.get("rotate_s", 10)))),
                                 daemon=True)
            t.start()
            self.threads.append(t)

    def _rotate(self, s, texts, period):
        key = s["key"]
        dls_file = os.path.join(self.sdir, "dls_%s.txt" % key)
        manual = os.path.join(self.sdir, "dls_%s.manual" % key)
        seq = [s["pad"]["dls"].get("text", "")] + texts
        i = 0
        while not self.stop_evt.wait(period):
            if os.path.exists(manual):
                continue
            i = (i + 1) % len(seq)
            dlp = s["pad"]["dls"].get("dlplus") if i == 0 else None
            atomic_write(dls_file, build_dls(seq[i], dlp))

    def alive(self):
        return [(n, p.poll()) for n, p in self.procs]

    def stop(self):
        self.stop_evt.set()
        for f in self.feeders.values():
            f.kill()
        # von hinten nach vorn: mod, mux, encoder, ffmpeg, padenc
        for name, p in reversed(self.procs):
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        deadline = time.time() + 3
        for name, p in self.procs:
            try:
                p.wait(timeout=max(0.05, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                    p.wait(timeout=2)
                except Exception:
                    pass
        for t in self.log_threads:
            t.join(timeout=1)
        for fd in self.fifo_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        for f in self.fifos:
            try:
                os.unlink(f)
            except Exception:
                pass
        for ident in self.pad_idents:
            for suf in (".padenc", ".audioenc", ""):
                try:
                    os.unlink("/tmp/" + ident + suf)
                except Exception:
                    pass
        shutil.rmtree(self.work, ignore_errors=True)
        try:
            with open(os.path.join(self.sdir, "session.json")) as f:
                d = json.load(f)
            d["running"] = False
            d["stopped"] = time.time()
            atomic_write(os.path.join(self.sdir, "session.json"), json.dumps(d, indent=1))
        except Exception:
            pass


def new_session_id():
    prune_dirs()
    return time.strftime("%Y%m%d-%H%M%S") + "-%04x" % random.randint(0, 0xFFFF)


def prune_dirs(keep_sessions=40, keep_checks=20):
    """Alte Sitzungs- und Pruefordner entfernen (laufende Sitzungen bleiben)."""
    try:
        ds = sorted(glob.glob(os.path.join(SESSIONS, "2*")))
        for d in ds[:-keep_sessions]:
            try:
                with open(os.path.join(d, "session.json")) as f:
                    s = json.load(f)
                if s.get("running") and _pid_alive(s.get("pid")):
                    continue
            except Exception:
                pass
            shutil.rmtree(d, ignore_errors=True)
        cs = sorted(glob.glob(os.path.join(RUNTIME, "checks", "check-*.report")))
        for d in cs[:-keep_checks]:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def prepare(cfg_path):
    cfg_raw = load_config(cfg_path)
    cfg, E, W = normalize(cfg_raw)
    return cfg, E, W


def ensure_media():
    """Legt fehlende Standard-Beispielmedien an (slide1.jpg, slide2.jpg, logo.png)."""
    os.makedirs(MEDIA, exist_ok=True)
    made = []
    specs = [("slide1.jpg", "navy", "DABLAB\nSlide 1", "320x240"), ("slide2.jpg", "darkgreen", "DABLAB\nSlide 2", "320x240"),
             ("logo.png", "darkred", "LAB", "128x128"), ("logo_320x240.png", "darkred", "DABLAB", "320x240")]
    mg = magick_cmd()
    font = None
    for fp in ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
               "/usr/share/fonts/dejavu/DejaVuSans.ttf"]:
        if os.path.isfile(fp):
            font = fp
            break
    for name, color, text, size in specs:
        p = os.path.join(MEDIA, name)
        if os.path.isfile(p):
            continue
        ok = False
        if mg:
            cmd = mg + ["-size", size, "xc:" + color]
            if font:
                cmd += ["-font", font, "-fill", "white", "-pointsize", "36" if size == "320x240" else "40",
                        "-gravity", "center", "-annotate", "0", text]
            cmd += ["-quality", "80", p]
            ok = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not ok:
            w, h = size.split("x")
            subprocess.run([tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=%s:s=%sx%s" % (color, w, h), "-frames:v", "1", p],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.isfile(p):
            made.append(name)
    return made


# --------------------------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------------------------
def cmd_live(args):
    global _log_file
    sid = new_session_id()
    sys.stderr.write(json.dumps({"session": sid}) + "\n")
    sys.stderr.flush()
    try:
        cfg, E, W = prepare(args.config)
    except Exception as e:
        log("error", "Konfiguration nicht lesbar: %s" % e)
        return 2
    for w in W:
        log("warn", w)
    if E:
        for e in E:
            log("error", e)
        log("error", "Konfiguration ungueltig (%d Fehler)" % len(E), errors=E)
        return 2
    rate = int(cfg["rf"]["sample_rate"])
    ensure_media()
    chain = Chain(cfg, sid)
    _log_file = open(os.path.join(chain.sdir, "runner.log"), "a", encoding="utf-8")
    stop = {"why": None}

    def on_sig(signum, frame):
        stop["why"] = "signal %d" % signum
        chain.stop_evt.set()

    for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sg, on_sig)
    rc = 0
    try:
        gain, cal, target = gain_for(cfg, rate)
        log("info", "Pegel: Ziel-RMS %.1f dBFS, digital_gain %.4f (Kalibrierung %.2f dBFS bei 1.0)" %
            (target, gain, cal["rms_dbfs_at_gain1"]))
        log("info", "Kapazitaet: %d/864 CU" % cfg["_capacity"]["used_cu"], capacity=cfg["_capacity"])
        r_out = chain.start(rate, gain)
        chain.write_session({"rate": rate, "gain": gain, "config_name": cfg.get("name")})
        chain.start_rotation()
        log("info", "Kette gestartet: %d Dienst(e), %d Hz s16, Modus %d" % (len(cfg["services"]), rate, cfg["mode"]),
            session=sid, mux_telnet=chain.mux_port, mod_telnet=chain.mod_port)
        try:
            os.set_blocking(sys.stdout.fileno(), False)
        except OSError:
            pass
        rc = relay(chain, r_out, sys.stdout.fileno(), rate, stop, shift_hz=getattr(args, "shift_hz", 0) or 0)
    except RunnerError as e:
        log("error", str(e))
        rc = 2
    except Exception as e:
        log("error", "Ausnahme: %r" % e)
        rc = 1
    finally:
        chain.stop()
        log("info", "beendet (%s), alle Kindprozesse beendet" % (stop["why"] or "Ende"),
            procs=[{"name": n, "rc": c} for n, c in chain.alive()])
    return rc


def relay(chain, r_fd, w_fd, rate, stop, limit_bytes=None, sink=None, shift_hz=0):
    """Kopiert dabmod-Ausgabe nach w_fd (blockierend). Misst Rate und Pegel. Rueckgabe Exitcode."""
    bufsz = 65536
    total = 0
    t0 = time.time()
    last_rep = t0
    last_bytes = 0
    stat_acc = []
    phase = 0
    nshift = None
    if shift_hz and np is not None:
        w = 2 * math.pi * shift_hz / rate
    else:
        w = 0.0
    try:
        while not chain.stop_evt.is_set():
            r, _, _ = select.select([r_fd], [], [], 0.5)
            if not r:
                dead = [(n, c) for n, c in chain.alive() if c is not None]
                if dead:
                    log("error", "Prozess beendet: %s" % dead)
                    return 3
                continue
            want = bufsz if limit_bytes is None else min(bufsz, limit_bytes - total)
            data = os.read(r_fd, want)
            if not data:
                stop["why"] = stop["why"] or "dabmod-Ausgabe EOF"
                dead = [(n, c) for n, c in chain.alive() if c is not None]
                log("warn" if limit_bytes is None else "info", "dabmod-Ausgabe beendet", procs=dead)
                return 3 if limit_bytes is None else 0
            if w:
                x = np.frombuffer(data[:len(data) // 4 * 4], dtype="<i2").astype(np.float32)
                c = x[0::2] + 1j * x[1::2]
                n = np.arange(len(c))
                c = c * np.exp(1j * (phase + w * n))
                phase = (phase + w * len(c)) % (2 * math.pi)
                y = np.empty(2 * len(c), np.float32)
                y[0::2], y[1::2] = c.real, c.imag
                data = np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()
            if sink is not None:
                sink(data)
            else:
                mv = memoryview(data)
                while mv:
                    # nicht blockierend schreiben, damit SIGTERM auch bei haengendem Leser wirkt
                    try:
                        _, wr, _ = select.select([], [w_fd], [], 0.5)
                    except InterruptedError:
                        wr = []
                    if chain.stop_evt.is_set():
                        return 0
                    if not wr:
                        continue
                    try:
                        k = os.write(w_fd, mv)
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        stop["why"] = "stdout geschlossen (EPIPE)"
                        return 0
                    except OSError as e:
                        if e.errno in (errno.EPIPE, errno.EBADF):
                            stop["why"] = "stdout geschlossen"
                            return 0
                        if e.errno in (errno.EINTR, errno.EAGAIN):
                            continue
                        raise
                    mv = mv[k:]
            total += len(data)
            if len(stat_acc) < 4 and (total // len(data)) % 16 == 0:
                stat_acc.append(data)
            now = time.time()
            if now - last_rep >= 5 and limit_bytes is None:
                st = level_stats(b"".join(stat_acc)) if stat_acc else {}
                stat_acc = []
                inst = (total - last_bytes) / 4.0 / (now - last_rep)
                extra = {}
                if chain.feeders:
                    extra["now_playing"] = [f.now_playing() for f in chain.feeders.values()]
                log("info", "Status", samples=total // 4, seconds=round(now - t0, 1),
                    rate_sps=round(inst), rate_avg_sps=round(total / 4.0 / (now - t0)), level=st, **extra)
                last_rep, last_bytes = now, total
            if limit_bytes is not None and total >= limit_bytes:
                return 0
    finally:
        chain.total_bytes = total
    return 0


# --------------------------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------------------------
def render(cfg, seconds, out, rate=2048000, fmt="cf32", quiet=False, speed=None):
    speed = float(speed or os.environ.get("DABLAB_RENDER_SPEED") or 3.0)
    sid = new_session_id() + "-r"
    ensure_media()
    chain = Chain(cfg, sid, render=True)
    gain, cal, target = gain_for(cfg, rate)
    nsamp = int(round(seconds * rate))
    # Einschwingen: Zeitinterleaver/Encoder-Vorlauf (~0,5 s) verwerfen
    skip = int(0.5 * rate) * 4
    limit = nsamp * 4 + skip
    t0 = time.time()
    stats_parts = []
    state = {"written": 0, "skipped": 0}
    fout = open(out, "wb")

    t_start = [None]

    def sink(data):
        # Tempo begrenzen: PadEnc arbeitet mit Wanduhr-Intervallen (DLS-Einfuegung), bei >10x Echtzeit kaemen
        # DLS/Slides nicht mehr zuverlaessig in den PAD. Standard 3x Echtzeit (DABLAB_RENDER_SPEED).
        if t_start[0] is None:
            t_start[0] = time.time()
        ahead = (state["written"] + state["skipped"]) / 4.0 / rate / speed - (time.time() - t_start[0])
        if ahead > 0.005:
            time.sleep(ahead)
        if state["skipped"] < skip:
            k = min(len(data), skip - state["skipped"])
            state["skipped"] += k
            data = data[k:]
            if not data:
                return
        if len(stats_parts) < 64 and random.random() < 0.05:
            stats_parts.append(data)
        if fmt == "cf32":
            if np is not None:
                x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
                fout.write(x.astype("<f4").tobytes())
            else:
                import array
                a = array.array("h")
                a.frombytes(data)
                fout.write(array.array("f", [v / 32768.0 for v in a]).tobytes())
        else:
            fout.write(data)
        state["written"] += len(data)

    stop = {"why": None}
    rc = 0
    try:
        r_out = chain.start(rate, gain)
        chain.write_session({"rate": rate, "render": True})
        rc = relay(chain, r_out, None, rate, stop, limit_bytes=limit, sink=sink)
    finally:
        chain.stop()
        fout.close()
    st = level_stats(b"".join(stats_parts)) if stats_parts else {}
    tx = None
    if getattr(chain, "eti_fic", None):
        f = parse_fic(bytes(chain.eti_fic))
        tx = {"eti_frames": chain.eti_frames, "fibs": f["fibs"], "crc_errors": f["crc_errors"],
              "figs_seen": dict(sorted(f["figs_seen"].items())), "al_flag": f["al_flag"], "lto_halfhours": f["lto"],
              "ecc": f["ecc"], "labels": f["labels"], "ensemble_label": f["ensemble_label"], "pty": f["pty"],
              "asu": f["asu"], "asw": {str(k): v for k, v in f["asw"].items()}, "fig0_6": f["linking"],
              "fig0_21": f["fi"], "fig0_24": f["oe"], "user_apps": f["user_apps"],
              "packet_components": f["packet_components"],
              "subchannels": {str(k): v for k, v in sorted(f["subchannels"].items())}}
    pad_tx = {}
    for s in cfg["services"]:
        if s["type"] == "dabplus" and s["pad"]["enable"] and getattr(chain, "eti_subch", None):
            try:
                pad_tx[s["key"]] = dabplus_pad_stats(bytes(chain.eti_subch.get(s["_subch"], b"")), s["audio"]["bitrate"])
            except Exception as e:
                pad_tx[s["key"]] = {"error": repr(e)}
    for s in cfg["services"]:
        if s["type"] == "data" and getattr(chain, "eti_subch", None) and s["key"] in chain.info.get("services", {}):
            pr = parse_packet_stream(bytes(chain.eti_subch.get(s["_subch"], b"")),
                                     address=int(s["data"]["packet_address"]))
            dgs = pr["datagroups"]
            chain.info["services"][s["key"]]["eti_check"] = {
                "packet_crc_errors": pr["packet_crc_errors"], "packets_ok": pr["packets"],
                "padding_packets": pr["padding"], "datagroups": len(dgs),
                "datagroup_crc_ok": sum(1 for d in dgs if d["crc_ok"]),
                "datagroup_types": sorted(set(d["type"] for d in dgs)), "mot_objects": pr["objects"]}
    res = {"ok": rc == 0 and state["written"] >= nsamp * 4, "out": os.path.abspath(out), "format": fmt,
           "tx_fic": tx, "tx_pad": pad_tx,
           "rate": rate, "seconds": seconds, "samples": state["written"] // 4,
           "bytes": os.path.getsize(out), "elapsed_s": round(time.time() - t0, 2),
           "digital_gain": round(gain, 5), "target_rms_dbfs": target, "level_s16": st,
           "session": sid, "capacity": cfg["_capacity"], "data_services": chain.info.get("services", {})}
    if rc:
        res["error"] = "Kette vorzeitig beendet, siehe %s" % chain.sdir
    return res


def cmd_render(args):
    cfg, E, W = prepare(args.config)
    if E:
        emit_json({"ok": False, "errors": E, "warnings": W})
        return 2
    res = render(cfg, float(args.seconds), args.out, int(args.rate), args.format)
    res["warnings"] = W
    emit_json(res)
    return 0 if res["ok"] else 1


# --------------------------------------------------------------------------------------------
# FIC-Parser (FIG 0/x, 1/x) fuer den Pruefbericht
# --------------------------------------------------------------------------------------------
EBU_HI = {0x80: "á", 0x81: "à", 0x82: "é", 0x83: "è", 0x84: "í", 0x85: "ì", 0x86: "ó", 0x87: "ò", 0x88: "ú",
          0x89: "ù", 0x8A: "Ñ", 0x8B: "Ç", 0x8C: "Ş", 0x8D: "ß", 0x8E: "¡", 0x8F: "Ĳ", 0x90: "â", 0x91: "ä",
          0x92: "ê", 0x93: "ë", 0x94: "î", 0x95: "ï", 0x96: "ô", 0x97: "ö", 0x98: "û", 0x99: "ü", 0x9A: "ñ",
          0x9B: "ç", 0x9C: "ş", 0x9D: "ğ", 0x9E: "ı", 0x9F: "ĳ", 0xC0: "Á", 0xC1: "À", 0xC2: "É", 0xC3: "È",
          0xC4: "Í", 0xC5: "Ì", 0xC6: "Ó", 0xC7: "Ò", 0xC8: "Ú", 0xC9: "Ù", 0xCA: "Ř", 0xCB: "Č", 0xCC: "Š",
          0xCD: "Ž", 0xCE: "Đ", 0xCF: "Ŀ", 0xD0: "Â", 0xD1: "Ä", 0xD2: "Ê", 0xD3: "Ë", 0xD4: "Î", 0xD5: "Ï",
          0xD6: "Ô", 0xD7: "Ö", 0xD8: "Û", 0xD9: "Ü", 0xDA: "ř", 0xDB: "č", 0xDC: "š", 0xDD: "ž", 0xDE: "đ",
          0xDF: "ŀ"}


def ebu_decode(b):
    return "".join(chr(c) if 0x20 <= c < 0x7F else EBU_HI.get(c, "?") for c in b)


def parse_fic(blob):
    """Wertet FIBs (je 32 Byte inkl. CRC) aus und sammelt die FIG-Inhalte."""
    R = {"fibs": 0, "crc_errors": 0, "figs_seen": {}, "al_flag": None, "cif_count": None, "subchannels": {},
         "services": {}, "packet_components": [], "languages": {}, "lto": None, "ecc": None, "inter_table": None,
         "datetime": None, "user_apps": {}, "pty": {}, "asu": {}, "asw": {}, "linking": {}, "fi": {}, "oe": {},
         "labels": {}, "ensemble_label": None, "eid": None, "charsets": set()}
    for k in range(0, len(blob) - 31, 32):
        fib = blob[k:k + 32]
        R["fibs"] += 1
        if crc16(fib[:30]) != int.from_bytes(fib[30:32], "big"):
            R["crc_errors"] += 1
            continue
        i = 0
        while i < 30:
            h = fib[i]
            if h == 0xFF:
                break
            ftype, flen = h >> 5, h & 0x1F
            d = fib[i + 1:i + 1 + flen]
            i += 1 + flen
            if not d:
                continue
            try:
                if ftype == 0:
                    _fig0(R, d)
                elif ftype == 1:
                    _fig1(R, d)
                elif ftype == 2:
                    R["figs_seen"]["2/%d" % (d[0] & 7)] = R["figs_seen"].get("2/%d" % (d[0] & 7), 0) + 1
            except Exception:
                pass
    R["charsets"] = sorted(R["charsets"])
    return R


def _fig0(R, d):
    cn, oe, pd, ext = d[0] >> 7, (d[0] >> 6) & 1, (d[0] >> 5) & 1, d[0] & 0x1F
    key = "0/%d" % ext
    R["figs_seen"][key] = R["figs_seen"].get(key, 0) + 1
    b = d[1:]
    if ext == 0 and len(b) >= 4:
        R["eid"] = "0x%04x" % int.from_bytes(b[0:2], "big")
        R["al_flag"] = bool(b[2] & 0x20)
    elif ext == 1:
        j = 0
        while j + 3 <= len(b):
            sc = b[j] >> 2
            start = ((b[j] & 3) << 8) | b[j + 1]
            if b[j + 2] & 0x80:
                opt, lvl = (b[j + 2] >> 4) & 7, (b[j + 2] >> 2) & 3
                size = ((b[j + 2] & 3) << 8) | b[j + 3]
                R["subchannels"][sc] = {"start": start, "cu": size,
                                        "protection": "EEP %d-%s" % (lvl + 1, "A" if opt == 0 else "B")}
                j += 4
            else:
                idx = b[j + 2] & 0x3F
                lvl = [4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 2, 1, 4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 2,
                       1, 4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 2, 1, 0, 4, 3, 1, 4, 2, 0][idx]
                cu = [16, 21, 24, 29, 35, 24, 29, 35, 42, 52, 29, 35, 42, 52, 32, 42, 48, 58, 70, 40, 52, 58, 70, 84,
                      48, 58, 70, 84, 104, 58, 70, 84, 104, 64, 84, 96, 116, 140, 80, 104, 116, 140, 168, 96, 116, 140,
                      168, 208, 116, 140, 168, 208, 232, 128, 168, 192, 232, 280, 160, 208, 280, 192, 280, 416][idx]
                R["subchannels"][sc] = {"start": start, "cu": cu, "protection": "UEP %d" % (lvl + 1), "uep_index": idx}
                j += 3
    elif ext == 2:
        j = 0
        while j < len(b):
            n = 4 if pd else 2
            sid = int.from_bytes(b[j:j + n], "big")
            j += n
            nsc = b[j] & 0xF
            j += 1
            comps = []
            for _ in range(nsc):
                tm = b[j] >> 6
                if tm == 0:
                    comps.append({"tmid": 0, "ascty": b[j] & 0x3F, "subch": b[j + 1] >> 2, "ps": bool(b[j + 1] & 2)})
                elif tm == 3:
                    comps.append({"tmid": 3, "scid": ((b[j] & 0x3F) << 6) | (b[j + 1] >> 2), "ps": bool(b[j + 1] & 2)})
                else:
                    comps.append({"tmid": tm, "dscty": b[j] & 0x3F, "subch": b[j + 1] >> 2})
                j += 2
            R["services"]["0x%04x" % sid if not pd else "0x%08x" % sid] = comps
    elif ext == 3:
        j = 0
        while j + 5 <= len(b):
            scid = (b[j] << 4) | (b[j + 1] >> 4)
            scca = b[j + 1] & 1
            dg = bool(b[j + 2] & 0x80)
            dscty = b[j + 2] & 0x3F
            subch = b[j + 3] >> 2
            addr = ((b[j + 3] & 3) << 8) | b[j + 4]
            ent = {"scid": scid, "datagroups": not dg, "dscty": dscty, "subch": subch, "address": addr}
            if ent not in R["packet_components"]:
                R["packet_components"].append(ent)
            j += 7 if scca else 5
    elif ext == 5:
        j = 0
        while j + 2 <= len(b):
            if b[j] & 0x80:
                j += 3
                continue
            sc = b[j] & 0x3F
            R["languages"][sc] = b[j + 1]
            j += 2
    elif ext == 9 and len(b) >= 3:
        lto = b[0] & 0x1F
        if b[0] & 0x20:
            lto = -lto
        R["lto"] = lto
        R["ecc"] = "0x%02x" % b[1]
        R["inter_table"] = b[2]
    elif ext == 10 and len(b) >= 4:
        mjd = ((b[0] & 0x7F) << 10) | (b[1] << 2) | (b[2] >> 6)
        utc_long = (b[2] >> 3) & 1
        hours = ((b[2] & 7) << 2) | (b[3] >> 6)
        minutes = b[3] & 0x3F
        sec = (b[4] >> 2) if utc_long and len(b) >= 6 else 0
        y, m, dd = mjd_to_date(mjd)
        R["datetime"] = "%04d-%02d-%02dT%02d:%02d:%02dZ" % (y, m, dd, hours, minutes, sec)
    elif ext == 13:
        j = 0
        while j < len(b):
            n = 4 if pd else 2
            sid = int.from_bytes(b[j:j + n], "big")
            j += n
            scids, nua = b[j] >> 4, b[j] & 0xF
            j += 1
            uas = []
            for _ in range(nua):
                ua = (b[j] << 3) | (b[j + 1] >> 5)
                ln = b[j + 1] & 0x1F
                uas.append({"type": "0x%x" % ua, "name": {2: "MOT Slideshow", 7: "SPI", 0x44A: "Journaline",
                                                         3: "BWS", 4: "TPEG"}.get(ua, "?"),
                            "data": b[j + 2:j + 2 + ln].hex()})
                j += 2 + ln
            R["user_apps"]["%s/%d" % (("0x%08x" if pd else "0x%04x") % sid, scids)] = uas
    elif ext == 17:
        j = 0
        while j + 4 <= len(b):
            sid = int.from_bytes(b[j:j + 2], "big")
            R["pty"]["0x%04x" % sid] = {"pty": b[j + 3] & 0x1F, "dynamic": bool(b[j + 2] & 0x80)}
            j += 4
    elif ext == 18:
        j = 0
        while j + 5 <= len(b):
            sid = int.from_bytes(b[j:j + 2], "big")
            asu = int.from_bytes(b[j + 2:j + 4], "big")
            n = b[j + 4] & 0x1F
            R["asu"]["0x%04x" % sid] = {"flags": ann_bits(asu), "asu": "0x%04x" % asu,
                                        "clusters": list(b[j + 5:j + 5 + n])}
            j += 5 + n
    elif ext == 19:
        j = 0
        while j + 4 <= len(b):
            cid = b[j]
            asw = int.from_bytes(b[j + 1:j + 3], "big")
            new, reg, subch = bool(b[j + 3] & 0x80), b[j + 3] & 0x40, b[j + 3] & 0x3F
            prev = R["asw"].get(cid, {"count": 0, "active_seen": 0})
            R["asw"][cid] = {"flags": ann_bits(asw), "asw": "0x%04x" % asw, "new": new, "subch": subch,
                             "count": prev["count"] + 1, "active_seen": prev["active_seen"] + (1 if asw else 0)}
            j += 5 if reg else 4
    elif ext == 6:
        j = 0
        while j + 2 <= len(b):
            idlist, la, sh, ils = b[j] >> 7, (b[j] >> 6) & 1, (b[j] >> 5) & 1, (b[j] >> 4) & 1
            lsn = ((b[j] & 0xF) << 8) | b[j + 1]
            j += 2
            ent = {"lsn": "0x%03x" % lsn, "active": bool(la), "hard": bool(sh), "international": bool(ils), "ids": []}
            if idlist:
                idlq, nid = (b[j] >> 5) & 3, b[j] & 0xF
                j += 1
                ent["idlq"] = {0: "DAB SId", 1: "RDS PI", 2: "rfu", 3: "DRM/AMSS"}[idlq]
                for _ in range(nid):
                    if pd:
                        ent["ids"].append("0x%08x" % int.from_bytes(b[j:j + 4], "big"))
                        j += 4
                    elif ils:
                        ent["ids"].append("ecc 0x%02x id 0x%04x" % (b[j], int.from_bytes(b[j + 1:j + 3], "big")))
                        j += 3
                    else:
                        ent["ids"].append("0x%04x" % int.from_bytes(b[j:j + 2], "big"))
                        j += 2
            R["linking"][ent["lsn"] + ("/oe" if oe else "")] = ent
    elif ext == 21:
        j = 0
        while j + 2 <= len(b):
            ln = b[j + 1] & 0x1F
            body = b[j + 2:j + 2 + ln]
            j += 2 + ln
            q = 0
            while q + 3 <= len(body):
                ident = int.from_bytes(body[q:q + 2], "big")
                rm, cont, lfl = body[q + 2] >> 4, (body[q + 2] >> 3) & 1, body[q + 2] & 7
                fl = body[q + 3:q + 3 + lfl]
                q += 3 + lfl
                freqs = []
                if rm == 0:
                    for z in range(0, len(fl) - 2, 3):
                        freqs.append(round((((fl[z] & 7) << 16) | (fl[z + 1] << 8) | fl[z + 2]) * 16e3 / 1e6, 3))
                elif rm == 8:
                    freqs = [round(87.5 + 0.1 * v, 1) for v in fl]
                else:
                    freqs = [fl.hex()]
                R["fi"]["0x%04x/%s%s" % (ident, {0: "DAB", 8: "FM", 6: "DRM", 14: "AMSS"}.get(rm, str(rm)),
                                         "/oe" if oe else "")] = {"freqs_mhz": freqs, "continuity": bool(cont)}
    elif ext == 24:
        j = 0
        while j < len(b):
            n = 4 if pd else 2
            sid = int.from_bytes(b[j:j + n], "big")
            j += n
            ne = b[j] & 0xF
            j += 1
            eids = ["0x%04x" % int.from_bytes(b[j + 2 * z:j + 2 * z + 2], "big") for z in range(ne)]
            j += 2 * ne
            R["oe"][("0x%08x" if pd else "0x%04x") % sid + ("/oe" if oe else "")] = eids


def _fig1(R, d):
    cs, oe, ext = d[0] >> 4, (d[0] >> 3) & 1, d[0] & 7
    R["figs_seen"]["1/%d" % ext] = R["figs_seen"].get("1/%d" % ext, 0) + 1
    R["charsets"].add(cs)
    b = d[1:]
    if ext == 0 and len(b) >= 20:
        lab = b[2:18]
        fl = int.from_bytes(b[18:20], "big")
        R["ensemble_label"] = {"label": ebu_decode(lab).rstrip(), "short": short_from_flags(lab, fl), "charset": cs}
    elif ext in (1, 5) and len(b) >= (18 if ext == 1 else 20):
        n = 2 if ext == 1 else 4
        sid = int.from_bytes(b[0:n], "big")
        lab = b[n:n + 16]
        fl = int.from_bytes(b[n + 16:n + 18], "big")
        R["labels"][("0x%04x" if n == 2 else "0x%08x") % sid] = {"label": ebu_decode(lab).rstrip(),
                                                                   "short": short_from_flags(lab, fl), "charset": cs}


def short_from_flags(lab, fl):
    return ebu_decode(bytes(lab[i] for i in range(16) if fl & (0x8000 >> i))).strip()


def ann_bits(v):
    return [ANN_FLAGS[i] for i in range(11) if v & (1 << i)]


def mjd_to_date(mjd):
    yp = int((mjd - 15078.2) / 365.25)
    mp = int((mjd - 14956.1 - int(yp * 365.25)) / 30.6001)
    day = mjd - 14956 - int(yp * 365.25) - int(mp * 30.6001)
    k = 1 if mp in (14, 15) else 0
    return 1900 + yp + k, mp - 1 - k * 12, day


# --------------------------------------------------------------------------------------------
# welle-cli Dekodierung
# --------------------------------------------------------------------------------------------
def http_get(port, path, timeout=3):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def read_fic_stream(port, seconds=2.0):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(b"GET /fic HTTP/1.0\r\nHost: localhost\r\n\r\n")
    data = b""
    t_end = time.time() + seconds
    s.settimeout(0.3)
    while time.time() < t_end:
        try:
            c = s.recv(65536)
            if not c:
                break
            data += c
        except socket.timeout:
            if b"\r\n\r\n" in data and len(data) > 2000:
                break
    s.close()
    k = data.find(b"\r\n\r\n")
    return data[k + 4:] if k >= 0 else b""


def decode_iq(path, wait_s=35.0, expect=None, outdir=None, max_slides=4, mode=1):
    """Startet welle-cli -D -w <port> -f <datei.cf32.iq> und baut den Pruefbericht."""
    if not path.endswith(".cf32.iq"):
        raise RunnerError("decode erwartet eine Datei *.cf32.iq (2,048 MS/s complex float32)")
    port = free_port()
    outdir = outdir or os.path.join(os.path.dirname(os.path.abspath(path)), os.path.basename(path) + ".report")
    os.makedirs(outdir, exist_ok=True)
    logp = open(os.path.join(outdir, "welle.log"), "wb")
    env = dict(os.environ, WELLE_DAB_MODE=str(int(mode)))  # nur mit patches/patch_welle.py wirksam
    p = subprocess.Popen([tool("welle-cli"), "-D", "-w", str(port), "-f", os.path.abspath(path)],
                         stdout=logp, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=outdir, env=env)
    mux = None
    t0 = time.time()
    slides = {}
    fic_state = {"data": b"", "stop": False}
    tii_seen = {}
    err_base = {}
    dls_seen = {}
    dls_hist = {}

    def fic_reader():
        # /fic liefert alle FIBs (welle puffert nur ~6 s), daher von Anfang an mitlesen
        while not fic_state["stop"]:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
            except OSError:
                time.sleep(0.2)
                continue
            try:
                s.sendall(b"GET /fic HTTP/1.0\r\nHost: localhost\r\n\r\n")
                s.settimeout(0.5)
                hdr = b""
                body = b""
                while not fic_state["stop"]:
                    try:
                        c = s.recv(65536)
                    except socket.timeout:
                        continue
                    if not c:
                        break
                    if hdr is not None:
                        hdr += c
                        k = hdr.find(b"\r\n\r\n")
                        if k >= 0:
                            body += hdr[k + 4:]
                            hdr = None
                    else:
                        body += c
                    if len(body) > 8 * 1024 * 1024:
                        break
                fic_state["data"] += body[:len(body) // 32 * 32]
            except OSError:
                pass
            finally:
                s.close()
            if fic_state["data"]:
                break
            time.sleep(0.2)

    fth = threading.Thread(target=fic_reader, daemon=True)
    fth.start()
    try:
        while time.time() - t0 < wait_s:
            time.sleep(1.0)
            if p.poll() is not None:
                break
            try:
                raw, _ = http_get(port, "/mux.json")
                mux = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            # Fehlerzaehler-Basis 4 s nach Start (Synchronisationsphase nicht mitzaehlen)
            if not err_base and time.time() - t0 >= 4 and mux.get("services"):
                for s in mux["services"]:
                    err_base[s.get("sid")] = dict(s.get("errorcounters") or {})
            # Slides abholen
            for s in mux.get("services", []):
                sid = s.get("sid")
                if (s.get("mot") or {}).get("time"):
                    try:
                        img, ctype = http_get(port, "/slide/%s" % sid)
                        h = hashlib.md5(img).hexdigest()[:10]
                        lst = slides.setdefault(sid, {})
                        if h not in lst and len(lst) < max_slides:
                            ext = ".png" if "png" in ctype else ".jpg"
                            fn = os.path.join(outdir, "slide_%s_%d%s" % (sid, len(lst) + 1, ext))
                            with open(fn, "wb") as f:
                                f.write(img)
                            w, hh = image_size_bytes(img)
                            lst[h] = {"file": fn, "w": w, "h": hh, "bytes": len(img), "type": ctype}
                    except Exception:
                        pass
            # DLS/DL Plus merken: welle setzt sie bei jedem Datei-Neustart zurueck
            for s in mux.get("services", []):
                dd = s.get("dls") or {}
                if dd.get("label"):
                    h = dls_hist.setdefault(s.get("sid"), [])
                    if not h or h[-1]["text"] != dd["label"]:
                        h.append({"text": dd["label"], "t_s": round(time.time() - t0, 1)})
                    dls_seen[s.get("sid")] = {"label": dd.get("label"), "dlplus": dd.get("dlplus") or
                                              (dls_seen.get(s.get("sid")) or {}).get("dlplus", "")}
                elif dd.get("dlplus") and s.get("sid") in dls_seen:
                    dls_seen[s.get("sid")]["dlplus"] = dd["dlplus"]
            for ti in mux.get("tii") or []:
                k = (ti.get("comb"), ti.get("pattern"))
                if k not in tii_seen:
                    tii_seen[k] = dict(ti, first_seen_s=round(time.time() - t0, 1))
            if expect and _expect_met(mux, expect, slides, tii_seen, dls_seen) and time.time() - t0 > 9:
                break
        time.sleep(1.0)
        fic_state["stop"] = True
        fth.join(timeout=3)
        fic = fic_state["data"]
    finally:
        fic_state["stop"] = True
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=3)
            except Exception:
                pass
        logp.close()
    figs = parse_fic(fic)
    if mux is not None:
        mux["_tii_seen"] = list(tii_seen.values())
        mux["_err_base"] = err_base
        mux["_dls_hist"] = dls_hist
        for s in mux.get("services", []):
            seen = dls_seen.get(s.get("sid"))
            dd = s.setdefault("dls", {})
            if seen and not dd.get("label"):
                dd["label"] = seen["label"]
                dd["seen_earlier"] = True
            if seen and not dd.get("dlplus"):
                dd["dlplus"] = seen.get("dlplus", "")
    rep = build_report(mux, figs, slides, time.time() - t0)
    with open(os.path.join(outdir, "mux.json"), "w", encoding="utf-8") as f:
        json.dump(mux, f, indent=1, ensure_ascii=False)
    rep["report_dir"] = outdir
    # welle-cli spielt die Datei in einer Schleife ab; jeder Neustart kostet 1-2 RS/Frame-Fehler je Dienst
    dur = os.path.getsize(path) / 8.0 / 2048000
    rep["iq_seconds"] = round(dur, 2)
    rep["file_loops"] = int(rep.get("decode_seconds", 0) // dur) if dur > 0 else 0
    return rep


def _expect_met(mux, expect, slides, tii_seen=None, dls_seen=None):
    svcs = {s.get("sid", "").lower(): s for s in mux.get("services", [])}
    for e in expect:
        if "tii" in e:
            if tuple(e["tii"]) not in (tii_seen or {}):
                return False
            continue
        s = svcs.get(e["sid"].lower())
        if not s:
            return False
        seen = (dls_seen or {}).get(s.get("sid")) or {}
        if e.get("dls") and not ((s.get("dls") or {}).get("label") or seen.get("label")):
            return False
        if e.get("dlplus") and not ((s.get("dls") or {}).get("dlplus") or seen.get("dlplus")):
            return False
        if e.get("slide") and not slides.get(s.get("sid")):
            return False
        if e.get("label") and not ((s.get("label") or {}).get("label")):
            return False
    return True


CODEC_RE = [("HE-AACv2", re.compile(r"HE-?AAC ?v2|PS", re.I)), ("HE-AAC", re.compile(r"HE-?AAC|SBR", re.I)),
            ("AAC-LC", re.compile(r"AAC", re.I)), ("MP2", re.compile(r"MP2|MPEG|Layer", re.I))]


def build_report(mux, figs, slides, elapsed):
    if not mux:
        return {"ok": False, "sync": False, "error": "welle-cli lieferte kein /mux.json (keine Synchronisation?)",
                "fic": _fig_summary(figs)}
    ens = mux.get("ensemble", {})
    el = ens.get("label") or {}
    dem = mux.get("demodulator", {})
    ut = mux.get("utctime") or {}
    services = []
    for s in mux.get("services", []):
        sid = s.get("sid", "")
        lab = s.get("label") or {}
        comps = s.get("components") or []
        c0 = comps[0] if comps else {}
        sub = c0.get("subchannel") or {}
        mode = s.get("mode") or ""
        codec = None
        if c0.get("ascty") == "DAB":
            codec = "MP2"
        elif mode and mode != "invalid":
            for name, rx in CODEC_RE:
                if rx.search(mode):
                    codec = name
                    break
        elif c0.get("ascty") == "DAB+":
            codec = "DAB+"
        al = (s.get("audiolevel") or {}) if c0.get("transportmode") == "audio" else {}

        def dbfs(v):
            try:
                return round(20 * math.log10(max(float(v), 1e-3) / 32767.0), 1)
            except Exception:
                return None
        dls = (s.get("dls") or {}).get("label") or ""
        _dlp = decode_dlplus((s.get("dls") or {}).get("dlplus"), dls)
        ec = s.get("errorcounters") or {}
        sidk = sid.lower()
        sidk_fig = "0x%04x" % int(sid, 16) if sid else ""
        pty = figs["pty"].get(sidk_fig, {})
        fcomps = figs["services"].get(sidk_fig) or []
        fsub = next((c.get("subch") for c in fcomps if c.get("tmid") == 0), None)
        flang = figs["languages"].get(fsub) if fsub is not None else None
        ch = None
        if mode:
            ch = 1 if re.search(r"mono", mode, re.I) else (2 if re.search(r"stereo|ps|v2|joint|dual", mode, re.I) else None)
        services.append({
            "sid": sidk, "label": (lab.get("label") or "").rstrip(), "shortlabel": (lab.get("shortlabel") or "").strip(),
            "fig2label": lab.get("fig2label") or "",
            "pty": s.get("programType"), "pty_name": s.get("ptystring"), "pty_dynamic": pty.get("dynamic"),
            "language": "0x%02x" % flang if flang is not None else s.get("language"),
            "language_name": s.get("languagestring") or None,
            "transport": c0.get("transportmode"), "ascty": c0.get("ascty"),
            "codec": codec, "mode": mode,
            "bitrate": sub.get("bitrate"), "protection": sub.get("protection"),
            "subchannel": fsub if fsub is not None else sub.get("subchannelid"),
            "cu": sub.get("cu"), "startaddr": sub.get("startaddr"),
            "samplerate": s.get("samplerate") or None, "channels": ch,
            "dls_history": (mux.get("_dls_hist") or {}).get(sid, []),
            "dls": dls, "dlplus": (_dlp.get("tags") if isinstance(_dlp, dict) else []) if _dlp else [],
            "dlplus_item": ({"toggle": _dlp.get("item_toggle"), "running": _dlp.get("item_running")}
                            if isinstance(_dlp, dict) and "tags" in _dlp else None),
            "slides": list((slides.get(sid) or {}).values()),
            "audio_level_db": [dbfs(al.get("left")), dbfs(al.get("right"))] if al else None,
            "errors": {"frame": ec.get("frameerrors"), "rs": ec.get("rserrors"), "aac": ec.get("aacerrors"),
                       "xpad": bool((s.get("xpaderror") or {}).get("haserror"))},
            "errors_after_sync": _err_delta(ec, (mux.get("_err_base") or {}).get(sid)),
            "announcements": figs["asu"].get(sidk_fig),
            "user_apps": [u for k, v in figs["user_apps"].items() if k.startswith(sidk_fig + "/") or k.startswith(sidk + "/")
                          for u in v],
        })
    sync = bool(ens.get("id") and ens.get("id") != "0x0000") or bool(services)
    lto = figs.get("lto")
    rep = {
        "ok": sync and bool(services), "sync": sync, "snr_db": dem.get("snr"),
        "freq_corr_hz": dem.get("frequencycorrection"),
        "fic_crc_errors": (dem.get("fic") or {}).get("numcrcerrors"),
        "ensemble": {"eid": ens.get("id"), "label": (el.get("label") or "").rstrip(),
                     "shortlabel": (el.get("shortlabel") or "").strip(), "ecc": ens.get("ecc"),
                     "lto": (lto / 2.0) if lto is not None else ut.get("lto"), "lto_halfhours": lto,
                     "datetime": figs.get("datetime") or ("%04d-%02d-%02dT%02d:%02dZ" % (
                         ut.get("year", 0), ut.get("month", 0), ut.get("day", 0), ut.get("hour", 0),
                         ut.get("minutes", 0)) if ut.get("year") else None),
                     "al_flag": figs.get("al_flag"), "international_table": figs.get("inter_table"),
                     "label_charsets": figs.get("charsets")},
        "services": services,
        "tii": mux.get("_tii_seen") or mux.get("tii") or [],
        "announcements": [{"cluster": cid, **v} for cid, v in sorted(figs["asw"].items())],
        "linking": {"fig0_6": figs["linking"], "fig0_21": figs["fi"], "fig0_24": figs["oe"]},
        "packet_components": figs["packet_components"],
        "fic": _fig_summary(figs),
        "decode_seconds": round(elapsed, 1),
        "raw": {"mux.json": mux},
    }
    return rep


def _err_delta(ec, base):
    if base is None:
        return None
    return {"frame": (ec.get("frameerrors") or 0) - (base.get("frameerrors") or 0),
            "rs": (ec.get("rserrors") or 0) - (base.get("rserrors") or 0),
            "aac": (ec.get("aacerrors") or 0) - (base.get("aacerrors") or 0)}


def _fig_summary(figs):
    return {"fibs": figs["fibs"], "crc_errors": figs["crc_errors"],
            "figs_seen": dict(sorted(figs["figs_seen"].items())),
            "subchannels": {str(k): v for k, v in sorted(figs["subchannels"].items())}}


def expectations(cfg):
    exp = []
    for s in cfg["services"]:
        if s["type"] == "data":
            continue
        e = {"sid": "0x%04x" % hexint(s["sid"]), "key": s["key"], "label": s["label"]}
        if s["pad"]["enable"] and s["pad"]["dls"].get("enable", True):
            e["dls"] = True
            if (s["pad"]["dls"].get("dlplus") or {}).get("enable"):
                e["dlplus"] = True
        if s["pad"]["enable"] and s["pad"]["sls"].get("enable"):
            e["slide"] = True
        exp.append(e)
    if cfg.get("tii", {}).get("enable") and not cfg["tii"].get("old_variant"):  # welle kennt nur die neue Variante
        exp.append({"tii": (int(cfg["tii"]["comb"]), int(cfg["tii"]["pattern"]))})
    return exp


def compare(cfg, rep):
    """Soll/Ist-Vergleich, liefert Liste von Abweichungen."""
    M = []
    ens = cfg["ensemble"]
    re_ = rep.get("ensemble", {})
    if (re_.get("label") or "") != ens["label"]:
        M.append("Ensemble-Label: soll '%s', ist '%s'" % (ens["label"], re_.get("label")))
    if (re_.get("eid") or "").lower() != "0x%04x" % hexint(ens["eid"]):
        M.append("EId: soll 0x%04x, ist %s" % (hexint(ens["eid"]), re_.get("eid")))
    svcs = {s["sid"]: s for s in rep.get("services", [])}
    tii = cfg.get("tii") or {}
    if tii.get("enable") and not tii.get("old_variant"):
        if not any(t.get("comb") == int(tii["comb"]) and t.get("pattern") == int(tii["pattern"]) for t in rep.get("tii", [])):
            M.append("TII %s/%s nicht erkannt" % (tii["comb"], tii["pattern"]))
    for s in cfg["services"]:
        sid = ("0x%04x" if hexint(s["sid"]) <= 0xFFFF else "0x%08x") % hexint(s["sid"])
        r = svcs.get(sid)
        if s["type"] == "data":
            pc = [p for p in rep.get("packet_components", []) if p["address"] == int(s["data"]["packet_address"])]
            if not pc:
                M.append("%s: Paketkomponente (Adresse %s) nicht in FIG 0/3" % (s["key"], s["data"]["packet_address"]))
            continue
        if not r:
            M.append("%s: Dienst %s nicht empfangen" % (s["key"], sid))
            continue
        if r["label"] != s["label"]:
            M.append("%s: Label soll '%s', ist '%s'" % (s["key"], s["label"], r["label"]))
        if r["bitrate"] and int(r["bitrate"]) != int(s["audio"]["bitrate"]):
            M.append("%s: Bitrate soll %s, ist %s" % (s["key"], s["audio"]["bitrate"], r["bitrate"]))
        if s["pad"]["enable"] and s["pad"]["dls"].get("enable", True) and not r["dls"]:
            M.append("%s: kein DLS empfangen" % s["key"])
        if s["pad"]["enable"] and (s["pad"]["dls"].get("dlplus") or {}).get("enable") and not r["dlplus"]:
            M.append("%s: kein DL Plus empfangen" % s["key"])
        if s["pad"]["enable"] and s["pad"]["sls"].get("enable") and not r["slides"]:
            M.append("%s: kein Slide empfangen" % s["key"])
        e = r.get("errors_after_sync") or r.get("errors") or {}
        tol = 3 * int(rep.get("file_loops") or 0) + 2
        if (e.get("aac") or 0) > tol or (e.get("rs") or 0) > tol or (e.get("frame") or 0) > tol:
            M.append("%s: Fehlerzaehler frame=%s rs=%s aac=%s" % (s["key"], e.get("frame"), e.get("rs"), e.get("aac")))
    return M


def compare_tx(cfg, tx):
    """Soll/Ist fuer die gesendete FIC (aus dem ETI-Abgriff)."""
    if not tx:
        return ["kein ETI-FIC-Mitschnitt"]
    M = []
    if tx.get("crc_errors"):
        M.append("ETI-FIC: %d CRC-Fehler" % tx["crc_errors"])
    if bool(tx.get("al_flag")) != bool(cfg["ensemble"].get("alarm_flag")):
        M.append("AL-Flag: soll %s, gesendet %s" % (cfg["ensemble"].get("alarm_flag"), tx.get("al_flag")))
    for s in cfg["services"]:
        sid = hexint(s["sid"])
        sk = "0x%04x" % sid
        lk = s.get("linking") or {}
        if lk.get("fm_pi"):
            lsn = "0x%03x" % (sid & 0xFFF)
            if lsn not in tx["fig0_6"]:
                M.append("%s: FIG 0/6 Linkage-Set %s fehlt" % (s["key"], lsn))
            if lk.get("fm_freqs_mhz") and not any(k.endswith("/FM") and k.startswith("0x%04x" % hexint(lk["fm_pi"]))
                                                 for k in tx["fig0_21"]):
                M.append("%s: FIG 0/21 FM-Frequenzen fehlen" % s["key"])
        if lk.get("other_ensembles") and s["type"] != "data" and sk not in tx["fig0_24"]:
            M.append("%s: FIG 0/24 fehlt" % s["key"])
        sup = (s.get("announcements") or {}).get("supported") or []
        if sup and s["type"] != "data":
            a = tx["asu"].get(sk)
            if not a or sorted(a["flags"]) != sorted(sup):
                M.append("%s: FIG 0/18 ASu soll %s, gesendet %s" % (s["key"], sup, a))
        if s["type"] == "data":
            if not any(p["address"] == int(s["data"]["packet_address"]) for p in tx["packet_components"]):
                M.append("%s: FIG 0/3 Paketkomponente fehlt" % s["key"])
    return M


def cmd_check(args):
    cfg, E, W = prepare(args.config)
    if E:
        emit_json({"ok": False, "errors": E, "warnings": W, "capacity": cfg.get("_capacity")})
        return 2
    seconds = float(args.seconds)
    os.makedirs(os.path.join(RUNTIME, "checks"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # IQ-Datei auf schnellem lokalem Dateisystem (WSL: /mnt/g ist langsam), Bericht + Slides im Runtime-Ordner
    out = os.path.join(TMPROOT, "dablab-check-%s-%04x.cf32.iq" % (stamp, random.randint(0, 0xFFFF)))
    repdir = os.path.join(RUNTIME, "checks", "check-%s.report" % stamp)
    r = render(cfg, seconds, out, 2048000, "cf32")
    if not r["ok"]:
        emit_json({"ok": False, "error": r.get("error", "render fehlgeschlagen"), "render": r, "warnings": W})
        return 1
    rep = decode_iq(out, wait_s=float(args.wait), expect=expectations(cfg), mode=cfg["mode"], outdir=repdir)
    rep["mismatches"] = compare(cfg, rep)
    rep["ok"] = bool(rep.get("ok")) and not rep["mismatches"]
    rep["render"] = {k: r[k] for k in ("seconds", "samples", "elapsed_s", "digital_gain", "target_rms_dbfs",
                                       "level_s16", "data_services")}
    rep["tx_fic"] = r.get("tx_fic")
    rep["mismatches"] += compare_tx(cfg, r.get("tx_fic"))
    for k, ds in (r.get("data_services") or {}).items():
        ec = ds.get("eti_check")
        if ec is not None and ds.get("mode") != "passthrough" and (ec["packet_crc_errors"] or not ec["mot_objects"]):
            rep["mismatches"].append("%s: Paketstrom im ETI unvollstaendig (%s)" % (k, ec))
    rep["ok"] = bool(rep.get("sync")) and not rep["mismatches"]
    rep["capacity"] = cfg["_capacity"]
    rep["warnings"] = W
    if not args.keep:
        try:
            os.unlink(out)
        except Exception:
            pass
    else:
        rep["iq_file"] = out
    emit_json(rep)
    return 0 if rep["ok"] else 1


def cmd_decode(args):
    expect = None
    cfg = None
    if args.config:
        cfg, E, W = prepare(args.config)
        expect = expectations(cfg)
    mode = args.mode or (cfg["mode"] if cfg else 1)
    rep = decode_iq(os.path.abspath(args.iq), wait_s=float(args.seconds) + 15 if args.seconds else 35, expect=expect,
                    mode=mode)
    if cfg:
        rep["mismatches"] = compare(cfg, rep)
        rep["ok"] = bool(rep.get("ok")) and not rep["mismatches"]
    emit_json(rep)
    return 0 if rep.get("ok") else 1


# --------------------------------------------------------------------------------------------
# ctl (Fernsteuerung einer laufenden live-Sitzung)
# --------------------------------------------------------------------------------------------
def odr_rc(port, cmds, timeout=2.0):
    """Sendet Befehle an die ODR-Telnet-Fernsteuerung, gibt Antworten zurueck."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.settimeout(0.4)
    out = []

    def drain():
        buf = b""
        t_end = time.time() + timeout
        while time.time() < t_end:
            try:
                c = s.recv(4096)
                if not c:
                    break
                buf += c
                if buf.rstrip().endswith(b">"):
                    break
            except socket.timeout:
                if buf:
                    break
        return buf.decode("utf-8", "replace")

    drain()
    for c in cmds:
        s.sendall((c + "\n").encode("utf-8"))
        out.append(drain().replace(">", "").strip())
    try:
        s.sendall(b"quit\n")
    except Exception:
        pass
    s.close()
    return out


def cmd_ctl(args):
    sdir = os.path.join(SESSIONS, args.session)
    try:
        with open(os.path.join(sdir, "session.json")) as f:
            sess = json.load(f)
    except Exception:
        emit_json({"ok": False, "error": "Sitzung %s unbekannt" % args.session})
        return 2
    if sess.get("mode") == "fm":  # UKW-Sitzung: Kommandodatei fuer fm_tx (siehe fm/fm_tx.py)
        from fm import fm_tx
        return fm_tx.cmd_ctl(args, sys.modules[__name__], sess, sdir)
    try:
        req = json.loads(args.json)
    except Exception as e:
        emit_json({"ok": False, "error": "JSON ungueltig: %s" % e})
        return 2
    if not sess.get("running") or not _pid_alive(sess.get("pid")):
        emit_json({"ok": False, "error": "Sitzung laeuft nicht mehr"})
        return 2
    res = {"ok": True, "done": []}
    try:
        if "dls" in req:
            d = req["dls"]
            key = d.get("service")
            sv = sess["services"].get(key)
            if not sv or not sv.get("pad"):
                raise RunnerError("Dienst %s ohne PAD/DLS" % key)
            dlp = d.get("dlplus")
            if dlp is None and sv.get("dlplus", {}).get("enable") and d.get("keep_dlplus"):
                dlp = sv["dlplus"]
            if isinstance(dlp, dict):
                dlp = dict(dlp)
                dlp.setdefault("enable", True)
            atomic_write(sv["dls_file"], build_dls(d.get("text", ""), dlp))
            manual = os.path.join(sdir, "dls_%s.manual" % key)
            if d.get("rotate", False):
                if os.path.exists(manual):
                    os.unlink(manual)
            else:
                open(manual, "w").close()
            res["done"].append("dls %s" % key)
        if "slide" in req:
            d = req["slide"]
            key = d.get("service")
            sv = sess["services"].get(key)
            if not sv or not sv.get("pad"):
                raise RunnerError("Dienst %s ohne PAD" % key)
            src = media_path(d["file"])
            if not os.path.isfile(src):
                raise RunnerError("Slide-Datei fehlt: %s" % d["file"])
            sd = sv["slide_dir"]
            os.makedirs(sd, exist_ok=True)
            if d.get("mode", "replace") == "replace":
                for old in glob.glob(os.path.join(sd, "*")):
                    os.remove(old)
            dst = os.path.join(sd, "%d_%s" % (int(time.time()), os.path.basename(src)))
            shutil.copyfile(src, dst + ".part")
            os.replace(dst + ".part", dst)
            res["done"].append("slide %s" % key)
        if "announcement" in req:
            d = req["announcement"]
            cid = int(hexint(d.get("cluster")))
            if cid not in sess.get("clusters", []):
                raise RunnerError("Cluster %d nicht konfiguriert (vorhanden: %s)" % (cid, sess.get("clusters")))
            r = odr_rc(sess["mux_telnet"], ["set ann_%d active %d" % (cid, 1 if d.get("active") else 0),
                                            "get ann_%d active" % cid])
            res["rc"] = r
            if "ok" not in r[0].lower() and r[0].strip() not in ("", "ok"):
                raise RunnerError("DabMux: %s" % r[0])
            res["done"].append("announcement %d %s" % (cid, bool(d.get("active"))))
        if "label" in req:
            d = req["label"]
            key = d.get("service")
            if key not in sess["services"]:
                raise RunnerError("Dienst %s unbekannt" % key)
            lab, sh = d.get("label", ""), d.get("shortlabel") or d.get("label", "")[:8]
            if "," in lab or "," in sh:
                raise RunnerError("Komma in Labels nicht erlaubt (ODR-Fernsteuerung)")
            r = odr_rc(sess["mux_telnet"], ["set srv_%s label %s,%s" % (key, lab, sh)])
            res["rc"] = r
            if r and r[0] and "ok" not in r[0].lower():
                raise RunnerError("DabMux: %s" % r[0])
            res["done"].append("label %s" % key)
        if "pty" in req:
            d = req["pty"]
            r = odr_rc(sess["mux_telnet"], ["set srv_%s pty %d" % (d["service"], int(d["pty"]))])
            res["rc"] = r
            res["done"].append("pty %s" % d["service"])
        if "tii" in req:
            d = req["tii"]
            cmds = []
            for k in ("enable", "comb", "pattern", "old_variant"):
                if k in d:
                    cmds.append("set tii %s %d" % (k, int(d[k])))
            res["rc"] = odr_rc(sess["mod_telnet"], cmds)
            res["done"].append("tii")
        if "gain" in req:
            res["rc"] = odr_rc(sess["mod_telnet"], ["set gain digital %.6f" % float(req["gain"]["digital"])])
            res["done"].append("gain")
        if "playlist" in req:
            d = req["playlist"]
            key = d.get("service")
            sv = sess["services"].get(key)
            if not sv or not sv.get("playlist"):
                raise RunnerError("Dienst %s spielt keine Playlist" % key)
            if d.get("action") not in ("next", "prev", "jump"):
                raise RunnerError("playlist.action muss next|prev|jump sein")
            cmdf = os.path.join(sdir, "playlist_%s.cmd" % key)
            atomic_write(cmdf, json.dumps({"action": d["action"], "index": d.get("index"), "t": time.time()}))
            res["done"].append("playlist %s %s" % (key, d["action"]))
            npf = os.path.join(sdir, "now_playing_%s.json" % key)
            try:
                with open(npf) as f:
                    res["now_playing_before"] = json.load(f)
            except Exception:
                pass
        if "raw" in req:  # Expertenzugang: {"raw":{"target":"mux|mod","cmd":"show srv_x"}}
            d = req["raw"]
            res["rc"] = odr_rc(sess["mux_telnet" if d.get("target", "mux") == "mux" else "mod_telnet"], [d["cmd"]])
        if not res["done"] and "raw" not in req:
            raise RunnerError("nichts zu tun (erlaubt: dls, slide, announcement, label, pty, tii, gain, playlist, raw)")
    except (RunnerError, OSError, KeyError, ValueError) as e:
        res = {"ok": False, "error": str(e), "done": res.get("done")}
    emit_json(res)
    return 0 if res["ok"] else 1


# --------------------------------------------------------------------------------------------
# iqconv: s16 (4,096 MS/s) -> cf32 (2,048 MS/s)
# --------------------------------------------------------------------------------------------
def halfband_taps(n=63):
    taps = []
    m = (n - 1) / 2
    for i in range(n):
        k = i - m
        h = 0.5 if k == 0 else math.sin(math.pi * k / 2) / (math.pi * k)
        w = 0.42 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) + 0.08 * math.cos(4 * math.pi * i / (n - 1))
        taps.append(h * w)
    s = sum(taps)
    return [t / s for t in taps]


def cmd_iqconv(args):
    if np is None:
        emit_json({"ok": False, "error": "iqconv braucht numpy"})
        return 2
    rate_in = int(args.in_rate)
    dec = max(1, rate_in // 2048000)
    taps = np.array(halfband_taps(63 if dec == 2 else 127), dtype=np.float32) if dec > 1 else None
    if dec > 2:
        # allgemeiner Tiefpass fuer dec>2
        n = 127
        k = np.arange(n) - (n - 1) / 2
        taps = (np.sinc(k / dec) / dec * np.blackman(n)).astype(np.float32)
        taps /= taps.sum()
    fin = open(args.inp, "rb")
    fout = open(args.out, "wb")
    hist = np.zeros(len(taps) - 1 if taps is not None else 0, np.complex64)
    nin = nout = 0
    off = 0
    while True:
        b = fin.read(1 << 22)
        if not b:
            break
        x = np.frombuffer(b[:len(b) // 4 * 4], dtype="<i2").astype(np.float32) / 32768.0
        c = (x[0::2] + 1j * x[1::2]).astype(np.complex64)
        nin += len(c)
        if dec > 1:
            buf = np.concatenate([hist, c])
            y = np.convolve(buf, taps, mode="valid")
            hist = buf[-(len(taps) - 1):]
            n_y = len(y)
            y = y[off::dec]
            off = (off - n_y) % dec
            c = y.astype(np.complex64)
        out = np.empty(2 * len(c), np.float32)
        out[0::2], out[1::2] = c.real, c.imag
        fout.write(out.astype("<f4").tobytes())
        nout += len(c)
    fin.close()
    fout.close()
    emit_json({"ok": True, "in_samples": nin, "out_samples": nout, "decimation": dec, "out": args.out})
    return 0


# --------------------------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------------------------
def _ver(cmd, pat=None):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        out = r.stdout.decode("utf-8", "replace")
        if pat:
            m = re.search(pat, out)
            return m.group(1) if m else out.strip().splitlines()[0][:80]
        return out.strip().splitlines()[0][:100]
    except Exception as e:
        return "FEHLER: %s" % e


def cmd_selftest(args):
    R = {"ok": True, "version": VERSION, "platform": "%s %s" % (platform.system(), platform.machine()),
         "python": sys.version.split()[0], "numpy": np.__version__ if np is not None else None,
         "bin": BIN, "runtime": RUNTIME, "tmp": TMPROOT, "tools": {}, "checks": []}

    def chk(name, ok, **kw):
        d = {"name": name, "ok": bool(ok)}
        d.update(kw)
        R["checks"].append(d)
        if not ok:
            R["ok"] = False

    vers = {"odr-audioenc": ([], r"ODR-AudioEnc (v[\d.]+\S*)"), "odr-padenc": (["--version"], r"(v[\d.]+\S*)"),
            "odr-dabmux": (["-h"], r"ODR-DabMux (v[\d.]+\S*)"), "odr-dabmod": (["-h"], r"ODR-DabMod version (v[\d.]+\S*)"),
            "welle-cli": (["-v"], r"(v[\d.]+\S*)"), "ffmpeg": (["-version"], r"ffmpeg version (\S+)")}
    for name, (a, pat) in vers.items():
        p = tool(name, required=False)
        R["tools"][name] = {"path": p, "version": _ver([p] + a, pat) if p else None}
        chk("tool " + name, p is not None)
    mg = magick_cmd()
    R["tools"]["magick"] = {"path": mg[0] if mg else None}
    # Patches
    # Verzeichnisse / FIFO / Unix-Socket
    for d in (RUNTIME, MEDIA, SESSIONS, CACHE):
        os.makedirs(d, exist_ok=True)
    try:
        w = tempfile.mkdtemp(prefix="dablab-st-", dir=TMPROOT)
        os.mkfifo(os.path.join(w, "f"))
        us = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        us.bind("/tmp/dablab_selftest_%d.sock" % os.getpid())
        us.close()
        os.unlink("/tmp/dablab_selftest_%d.sock" % os.getpid())
        shutil.rmtree(w)
        chk("FIFO + Unix-Datagramm-Socket in %s und /tmp" % TMPROOT, True)
    except Exception as e:
        chk("FIFO + Unix-Datagramm-Socket", False, error=str(e))
    made = ensure_media()
    chk("Beispielmedien", os.path.isfile(os.path.join(MEDIA, "slide1.jpg")), created=made)
    # Kapazitaetsrechnung
    t = {"services": [{"key": "a", "type": "dabplus", "audio": {"bitrate": 96}, "protection": {"profile": "EEP_A", "level": 3}},
                      {"key": "b", "type": "dab", "audio": {"bitrate": 128}, "protection": {"profile": "UEP", "level": 3}},
                      {"key": "c", "type": "dabplus", "audio": {"bitrate": 64}, "protection": {"profile": "EEP_B", "level": 2}}]}
    c, E, W = normalize(t)
    chk("CU-Rechnung (72+96+42=210)", c["_capacity"]["used_cu"] == 210, cu=c["_capacity"]["used_cu"], errors=E)
    over = {"services": [{"key": "s%d" % i, "type": "dabplus", "audio": {"bitrate": 192},
                          "protection": {"profile": "EEP_A", "level": 1}} for i in range(4)]}
    c2, E2, _ = normalize(over)
    chk("Kapazitaetsueberschreitung wird gemeldet", any("Kapazit" in e for e in E2), errors=E2[-1:] if E2 else [])
    # MOT-Paketencoder Rundlauf
    try:
        w = tempfile.mkdtemp(prefix="dablab-st-", dir=TMPROOT)
        pk = os.path.join(w, "p.pkt")
        info = build_packet_stream({"key": "t", "data": {"kind": "slideshow_packet", "packet_address": 5,
                                                         "files": [os.path.join(MEDIA, "slide1.jpg")]}}, pk)
        with open(pk, "rb") as f:
            pr = parse_packet_stream(f.read())
        shutil.rmtree(w)
        ok = pr["packet_crc_errors"] == 0 and pr["datagroups"] and all(d["crc_ok"] for d in pr["datagroups"])
        chk("MOT-Paketmodus Encoder->Decoder", ok, packets=info["packets"],
            datagroups=len(pr["datagroups"]), types=sorted(set(d["type"] for d in pr["datagroups"])))
    except Exception as e:
        chk("MOT-Paketmodus Encoder->Decoder", False, error=str(e))
    # DLS/DL Plus-Datei
    txt = build_dls("Kuenstler - Titel", {"enable": True, "artist": "Kuenstler", "title": "Titel"})
    chk("DL-Plus-Datei", "DL_PLUS_TAG=4 0 8" in txt and "DL_PLUS_TAG=1 12 4" in txt)
    # FIFO-Teil-Reads in DabMux (Patch patches/patch_dabmux.py) + Pegelkalibrierung
    if not args.quick and all(R["tools"][n]["path"] for n in ("odr-audioenc", "odr-dabmux", "odr-dabmod", "ffmpeg")):
        _ok, _info = _fifo_test()
        chk("DabMux liest FIFO mit Teil-Reads (patches/patch_dabmux.py)", _ok, **_info)
        for mode in (1, 2, 3, 4):
            for rate in (2048000, 4096000):
                try:
                    cal = calibrate(mode, rate)
                    chk("Pegelkalibrierung Modus %d %d Hz" % (mode, rate), cal["rms_dbfs_at_gain1"] < 0, **cal)
                except Exception as e:
                    chk("Pegelkalibrierung Modus %d %d Hz" % (mode, rate), False, error=str(e))
    # UKW-Teil (fm/): RDS-Bitebene, DSP-Blockkontinuitaet, redsea, interne Schleife
    try:
        from fm import fm_tx
        R["fm"] = fm_tx.selftest(sys.modules[__name__], quick=args.quick)
    except Exception as e:
        R["fm"] = {"ok": False, "error": repr(e)}
    chk("UKW (fm/): RDS, DSP, redsea, Schleife", R["fm"].get("ok"))
    emit_json(R)
    return 0 if R["ok"] else 1


def _fifo_test():
    """Schreibt einen kleinen DAB+-Strom in Haeppchen in eine FIFO; DabMux darf nicht 'rewind'-Fehler melden."""
    w = tempfile.mkdtemp(prefix="dablab-ff-", dir=TMPROOT)
    try:
        dabp = os.path.join(w, "a.dabp")
        ff = subprocess.Popen([tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                               "sine=f=1000:d=3:r=48000", "-ac", "2", "-f", "s16le", "pipe:1"], stdout=subprocess.PIPE)
        subprocess.run([tool("odr-audioenc"), "-f", "raw", "-i", "-", "-b", "48", "-o", dabp], stdin=ff.stdout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        ff.wait()
        with open(dabp, "rb") as f:
            data = f.read()
        fifo = os.path.join(w, "in.dabp")
        os.mkfifo(fifo)
        mux = os.path.join(w, "t.mux")
        with open(mux, "w") as f:
            f.write("general {\n dabmode 1\n nbframes 100\n}\nremotecontrol {\n telnetport 0\n}\n"
                    "ensemble {\n id 0x4FFF\n ecc 0xE0\n label \"T\"\n shortlabel \"T\"\n}\n"
                    "services {\n s {\n id 0xD001\n label \"T\"\n }\n}\n"
                    "subchannels {\n a {\n type dabplus\n bitrate 48\n id 1\n protection 3\n inputproto file\n"
                    " inputuri \"%s\"\n }\n}\ncomponents {\n c {\n service s\n subchannel a\n }\n}\n"
                    "outputs {\n o \"file://%s/t.eti?type=raw\"\n}\n" % (fifo, w))
        mp = subprocess.Popen([tool("odr-dabmux"), mux], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def writer():
            try:
                with open(fifo, "wb", buffering=0) as fo:
                    for i in range(0, len(data), 333):
                        fo.write(data[i:i + 333])
                        time.sleep(0.001)
            except Exception:
                pass
        th = threading.Thread(target=writer, daemon=True)
        th.start()
        try:
            _, err = mp.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            mp.kill()
            _, err = mp.communicate()
        err = err.decode("utf-8", "replace")
        bad = [l for l in err.splitlines() if re.search(r"rewind|Not enough data|Can't read", l)]
        eti = os.path.getsize(os.path.join(w, "t.eti")) if os.path.exists(os.path.join(w, "t.eti")) else 0
        return (not bad and eti >= 100 * 6144), {"eti_bytes": eti, "errors": bad[:3]}
    finally:
        shutil.rmtree(w, ignore_errors=True)


def cmd_validate(args):
    cfg, E, W = prepare(args.config)
    emit_json({"ok": not E, "errors": E, "warnings": W, "capacity": cfg.get("_capacity")})
    return 0 if not E else 2


def cmd_mkmedia(args):
    emit_json({"ok": True, "media": MEDIA, "created": ensure_media()})
    return 0


# --------------------------------------------------------------------------------------------
# UKW (mode == "fm"): Delegation an das Paket fm/ (fm_tx.py), die DAB-Logik bleibt unberuehrt
# --------------------------------------------------------------------------------------------
def _fm_dispatch(args):
    """Gibt einen Exitcode zurueck, wenn der Befehl fuer eine UKW-Konfiguration/-Sitzung ist, sonst None."""
    cfg_raw = None
    if args.cmd in ("live", "render", "check", "validate", "decode") and getattr(args, "config", None):
        try:
            cfg_raw = load_config(args.config)
        except Exception:
            return None
        if str(cfg_raw.get("mode", "")).lower() != "fm":
            cfg_raw = None
    if args.cmd == "decode":
        is_fm = cfg_raw is not None or str(args.mode).lower() == "fm"
        if not is_fm and not getattr(args, "config", None):
            try:
                from fm import fm_check
                fmt = args.format or ("s16" if args.iq.endswith(".s16") else "cf32")
                rate = args.rate or (4096000 if fmt == "s16" else 2048000)
                is_fm = fm_check.looks_like_fm(os.path.abspath(args.iq), rate, fmt)
            except Exception:
                is_fm = False
        if not is_fm:
            args.mode = int(args.mode) if str(args.mode).isdigit() else 0
            return None
    elif cfg_raw is None:
        return None
    from fm import fm_tx
    return fm_tx.dispatch(args, sys.modules[__name__], cfg_raw)


# --------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="dablab_run.py", description="DAB-Lab Runner")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("live")
    p.add_argument("--config", required=True)
    p.add_argument("--shift-hz", type=float, default=0, dest="shift_hz",
                   help="digitale Frequenzverschiebung (numpy), z. B. gegen LO-Durchgriff")
    p.add_argument("--workers", type=int, default=0, help="PAL live: Encoder-Prozesse (Standard 3)")
    p = sub.add_parser("render")
    p.add_argument("--config", required=True)
    p.add_argument("--seconds", type=float, default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--rate", type=int, default=2048000)
    p.add_argument("--method", choices=["fft", "stream"], default="fft", help="PAL: Verfahren fuer die Schleife")
    p.add_argument("--format", choices=["cf32", "s16", "ts"], default="cf32",
                   help="DAB: cf32|s16 (IQ). DVB: ts (Standard, Transportstrom) oder s16 (IQ ueber den Modulator)")
    p = sub.add_parser("check")
    p.add_argument("--config", required=True)
    p.add_argument("--method", choices=["both", "fft", "stream"], default="both", help="PAL: geprueftes Verfahren")
    p.add_argument("--seconds", type=float, default=20)
    p.add_argument("--wait", type=float, default=40)
    p.add_argument("--keep", action="store_true")
    p = sub.add_parser("decode")
    p.add_argument("--iq", required=True)
    p.add_argument("--mode", default="0", help="DAB-Modus 1-4 (Standard: aus --config, sonst 1) oder 'fm' (UKW)")
    p.add_argument("--seconds", type=float, default=20)
    p.add_argument("--config")
    p.add_argument("--rate", type=int, default=0, help="nur UKW: Abtastrate des Mitschnitts (Standard: cf32 2048000, s16 4096000)")
    p.add_argument("--format", choices=["cf32", "s16"], default=None, help="nur UKW: Format des Mitschnitts (Standard: aus Dateiname)")
    p.add_argument("--shift-hz", type=float, default=0, dest="shift_hz", help="nur UKW: Traegerversatz im Mitschnitt")
    p = sub.add_parser("ctl")
    p.add_argument("--session", required=True)
    p.add_argument("json")
    p = sub.add_parser("selftest")
    p.add_argument("--quick", action="store_true")
    p = sub.add_parser("iqconv")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--in-rate", dest="in_rate", type=int, default=4096000)
    p.add_argument("--out", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--config", required=True)
    sub.add_parser("mkmedia")
    p = sub.add_parser("playlist")
    p.add_argument("action", choices=["list", "show", "import", "export"])
    p.add_argument("args", nargs="*")
    p = sub.add_parser("media", help="DVB-Medien: youtube <url> | list | info <datei> | channels [dvbt|dvbc] | rates | tools")
    p.add_argument("action", choices=["youtube", "youtube-consent", "list", "info", "channels", "rates", "tools", "dshow", "gpu"])
    p.add_argument("args", nargs="*")
    p.add_argument("--max-height", dest="max_height", type=int, default=1080)
    p.add_argument("--force", action="store_true")
    p.add_argument("--cookies", help="YouTube: Netscape-Cookie-Datei")
    p.add_argument("--cookies-from-browser", dest="cookies_from_browser", choices=["edge", "firefox", "chrome", "brave", "opera",
                                                                                 "vivaldi", "chromium"],
                   help="YouTube: Cookies einmalig aus dem Windows-Browser exportieren (nur nach media youtube-consent)")
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    for d in (RUNTIME, SESSIONS):
        os.makedirs(d, exist_ok=True)
    # --- Delegation an das DVB-T/DVB-C-Paket (runner/dvb/) ---
    dvb_sess = None
    is_dvb = args.cmd == "media"
    if args.cmd in ("live", "render", "check", "validate"):
        try:
            is_dvb = str(load_config(args.config).get("mode", "")).lower() in ("dvbt", "dvbc", "dvbs")
        except Exception:
            is_dvb = False
    elif args.cmd == "ctl":
        try:
            with open(os.path.join(SESSIONS, args.session, "session.json")) as f:
                dvb_sess = json.load(f)
            is_dvb = dvb_sess.get("standard") in ("dvbt", "dvbc", "dvbs")
        except Exception:
            is_dvb = False
    # --- Delegation an das PAL-Paket (runner/pal/) ---
    if args.cmd in ("live", "render", "check", "validate"):
        try:
            is_pal = str(load_config(args.config).get("mode", "")).lower() == "pal"
        except Exception:
            is_pal = False
        if is_pal:
            from pal import cli as pal_cli
            try:
                return pal_cli.run(args, sys.modules[__name__])
            except RunnerError as e:
                if args.cmd == "live":
                    log("error", str(e))
                else:
                    emit_json({"ok": False, "error": str(e)})
                return 2
    if args.cmd == "render" and args.seconds is None and not is_dvb:
        emit_json({"ok": False, "error": "render: --seconds fehlt"})
        return 2
    if is_dvb:
        from dvb import cli as dvb_cli
        try:
            return dvb_cli.run(args, sys.modules[__name__], dvb_sess)
        except RunnerError as e:
            if args.cmd == "live":
                log("error", str(e))
            else:
                emit_json({"ok": False, "error": str(e)})
            return 2
    fn = {"live": cmd_live, "render": cmd_render, "check": cmd_check, "decode": cmd_decode, "ctl": cmd_ctl,
          "selftest": cmd_selftest, "iqconv": cmd_iqconv, "validate": cmd_validate, "mkmedia": cmd_mkmedia,
          "playlist": cmd_playlist}[args.cmd]
    try:
        rc = _fm_dispatch(args)
        if rc is not None:
            return rc
        return fn(args)
    except RunnerError as e:
        if args.cmd == "live":
            log("error", str(e))
        else:
            emit_json({"ok": False, "error": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
