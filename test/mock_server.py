"""Mock des DAB-Lab-PC-Servers (API laut SPEC.md) für die Dashboard-Entwicklung.

Start:  python test/mock_server.py [--port 8793] [--fast]
Liefert web/ unter /, simuliert Sendekette, Pluto, Signalerzeugung, SSE, Prüfberichte, Presets, Uploads.
Keine Hardware, kein Senden.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import struct
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
MEDIA = Path(tempfile.gettempdir()) / "dablab_mock_media"
ATT_MAX = 89.75

CHANNELS = {
    "5A": 174.928, "5B": 176.640, "5C": 178.352, "5D": 180.064, "6A": 181.936, "6B": 183.648, "6C": 185.360, "6D": 187.072,
    "7A": 188.928, "7B": 190.640, "7C": 192.352, "7D": 194.064, "8A": 195.936, "8B": 197.648, "8C": 199.360, "8D": 201.072,
    "9A": 202.928, "9B": 204.640, "9C": 206.352, "9D": 208.064, "10A": 209.936, "10N": 210.096, "10B": 211.648, "10C": 213.360,
    "10D": 215.072, "11A": 216.928, "11N": 217.088, "11B": 218.640, "11C": 220.352, "11D": 222.064, "12A": 223.936,
    "12N": 224.096, "12B": 225.648, "12C": 227.360, "12D": 229.072, "13A": 230.784, "13B": 232.496, "13C": 234.208,
    "13D": 235.776, "13E": 237.488, "13F": 239.200,
}
for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVW"):
    CHANNELS["L" + c] = round(1452.960 + i * 1.712, 3)


# ------------------------------------------------------------------ Beispielmedien
def png(w: int, h: int, c1, c2, stripes=0) -> bytes:
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            t = (x + y) / (w + h)
            r, g, b = (int(c1[k] * (1 - t) + c2[k] * t) for k in range(3))
            if stripes and (x // stripes) % 2 == 0 and y > h * 0.62:
                r, g, b = 255 - r // 3, 255 - g // 3, 255 - b // 3
            row += bytes((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def make_media():
    MEDIA.mkdir(parents=True, exist_ok=True)
    samples = {
        "slide1.png": (320, 240, (37, 99, 235), (13, 148, 136), 20),
        "slide2.png": (320, 240, (219, 39, 119), (234, 88, 12), 32),
        "slide3.png": (320, 240, (79, 70, 229), (8, 145, 178), 0),
        "logo.png": (128, 128, (29, 78, 216), (14, 165, 233), 0),
    }
    for name, (w, h, a, b, s) in samples.items():
        p = MEDIA / name
        if not p.exists():
            p.write_bytes(png(w, h, a, b, s))
    for name in DEMO_AUDIO:
        p = MEDIA / name
        if not p.exists():
            p.write_bytes(b"ID3mock")


# Beispielmetadaten (Dauer/Tags) für die Medienbibliothek
DEMO_AUDIO = {
    "song.mp3": (214.0, "Laborsong", "DAB-Lab Band"), "sprache.wav": (38.5, "Ansage", ""),
    "jingle.ogg": (7.2, "Jingle", "DAB-Lab"), "podcast.opus": (1865.0, "Podcast Folge 1", "Redaktion"),
    "klassik.flac": (412.4, "Sinfonie Nr. 5", "Orchester"), "nachrichten.m4a": (180.0, "Nachrichten", "Studio"),
}
AUDIO_EXT = (".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".aac", ".wma", ".mp2")


def media_meta(f: Path):
    d = DEMO_AUDIO.get(f.name)
    if d:
        return {"duration_s": d[0], "title": d[1], "artist": d[2]}
    # Upload: Dauer aus der Größe „schätzen“ (128 kbit/s)
    return {"duration_s": round(max(1.0, f.stat().st_size / 16000), 1), "title": "", "artist": ""}


def media_item(f: Path):
    kind = "slide" if f.suffix.lower() in (".png", ".jpg", ".jpeg") else "audio" if f.suffix.lower() in AUDIO_EXT else "other"
    it = {"name": f.name, "file": f"media/{f.name}", "size": f.stat().st_size, "kind": kind, "mtime": f.stat().st_mtime,
          "format": f.suffix.lstrip(".").lower()}
    if kind == "audio":
        it.update(media_meta(f))
    return it


def playlist_item(file: str, title="", artist="", duration=None):
    f = MEDIA / Path(file).name
    meta = media_meta(f) if f.exists() else {"duration_s": None, "title": "", "artist": ""}
    return {"file": file if file.startswith("media/") else f"media/{file}", "title": title or meta["title"],
            "artist": artist or meta["artist"], "duration_s": duration if duration is not None else meta["duration_s"]}


def parse_m3u(text: str):
    items, title, artist, dur = [], "", "", None
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    if lines and lines[0].lower() == "[playlist]":           # PLS
        files, titles, lens = {}, {}, {}
        for ln in lines[1:]:
            if "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip().lower()
            if k.startswith("file"):
                files[k[4:]] = v.strip()
            elif k.startswith("title"):
                titles[k[5:]] = v.strip()
            elif k.startswith("length"):
                lens[k[6:]] = v.strip()
        for n in sorted(files, key=lambda x: int(x) if x.isdigit() else 0):
            d = lens.get(n)
            items.append(playlist_item(files[n], titles.get(n, ""), "", float(d) if d and float(d) > 0 else None))
        return items
    for ln in lines:
        if not ln:
            continue
        if ln.upper().startswith("#EXTINF:"):
            rest = ln[8:]
            d, _, name = rest.partition(",")
            try:
                dur = float(d.split()[0]) if float(d.split()[0]) > 0 else None
            except ValueError:
                dur = None
            if " - " in name:
                artist, title = (x.strip() for x in name.split(" - ", 1))
            else:
                title, artist = name.strip(), ""
        elif ln.startswith("#"):
            continue
        else:
            items.append(playlist_item(ln, title, artist, dur))
            title, artist, dur = "", "", None
    return items


def parse_multipart(ctype, data):
    """multipart/form-data → [(dateiname, bytes)] für Teile mit filename."""
    import email.parser
    import email.policy
    head = ("Content-Type: " + ctype + 2 * (chr(13) + chr(10))).encode()
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + data)
    out = []
    for part in msg.iter_parts():
        fn = part.get_filename()
        if fn:
            out.append((fn, part.get_payload(decode=True)))
    return out


def pl_out(pl):
    """Playlist wie der echte Server: items mit exists-Flag."""
    return {"name": pl["name"], "items": [dict(it, exists=(MEDIA / Path(it["file"]).name).exists()) for it in pl["items"]]}


def to_m3u(pl):
    out = ["#EXTM3U"]
    for it in pl.get("items", []):
        d = it.get("duration_s")
        label = " - ".join(x for x in (it.get("artist"), it.get("title")) if x) or Path(it["file"]).name
        out.append(f"#EXTINF:{int(round(d)) if d else -1},{label}")
        out.append(it["file"].replace("media/", "", 1))
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ Zustand
def service(key, sid, label, short, codec="he-aac-v2", br=88, typ="dabplus", prot=("EEP_A", 3), **kw):
    s = {
        "key": key, "sid": sid, "ecc": None, "label": label, "shortlabel": short, "pty": kw.get("pty", 10), "pty_dynamic": False,
        "language": "0x08", "type": typ,
        "audio": {"source": {"kind": kw.get("src", "tone"), "file": kw.get("file", ""), "freq_hz": 1000, "level_dbfs": -18,
                             "left_hz": 1000, "right_hz": 400, "sweep_from_hz": 20, "sweep_to_hz": 20000, "sweep_s": 10, "loop": True,
                             "playlist": kw.get("playlist", ""), "shuffle": False, "crossfade_s": 0, "normalize": True, "dls_from_tags": True},
                  "codec": codec, "samplerate": 48000, "channels": kw.get("ch", 2), "bitrate": br, "mp2_mode": "j", "gain_db": 0,
                  "bandwidth_hz": 0},
        "protection": {"profile": prot[0], "level": prot[1]},
        "pad": {"enable": True, "bytes": 58,
                "dls": {"enable": True, "text": kw.get("dls", f"{label} – DAB-Lab"),
                        "dlplus": {"enable": False, "artist": "", "title": "", "album": "", "station": "", "program": "", "url": ""},
                        "rotate": [], "rotate_s": 10, "charset": "utf8", "remove_label": False},
                "sls": {"enable": bool(kw.get("slides")), "slides": kw.get("slides", []), "interval_s": 10, "max_bytes": 50000,
                        "raw": False, "categories": False}},
        "data": {"kind": "spi", "bitrate": 16, "packet_address": 1, "files": []},
        "spi": {"logo": kw.get("logo", "")},
        "announcements": {"supported": kw.get("asu", []), "clusters": kw.get("clusters", [])},
        "linking": {"fm_pi": None, "fm_freqs_mhz": [], "other_ensembles": []},
    }
    return s


def default_config():
    return {
        "name": "Standard", "mode": 1,
        "ensemble": {"eid": "0x4FFF", "ecc": "0xE0", "label": "DABLAB Labor", "shortlabel": "DABLAB", "charset": "ebu",
                     "international_table": 1, "lto": "auto", "datetime": True, "alarm_flag": False, "reconfig_counter": "hash"},
        "services": [
            service("srv1", "0xD001", "DABLAB Pop", "Pop", "he-aac-v2", 88, slides=["media/slide1.png", "media/slide2.png"],
                    logo="media/logo.png", asu=["Traffic", "News"], clusters=[1], src="playlist", playlist="Demo",
                    dls="Jetzt: Testmusik – DAB-Lab"),
            service("srv2", "0xD002", "Testton 1 kHz", "Ton1k", "aac-lc", 128, pty=0),
            service("srv3", "0xD003", "Info MP2", "InfoMP2", "mp2", 128, "dab", ("UEP", 3), pty=3, asu=["Traffic"], clusters=[1]),
        ],
        "clusters": [{"id": 1, "flags": ["Traffic"], "service": "srv3", "active": False}],
        "tii": {"enable": False, "comb": 1, "pattern": 11, "old_variant": False},
        "rf": {"channel": "6C", "freq_hz": 185360000, "offset_hz": 0, "lo_offset_hz": 0, "atten_db": 60, "sample_rate": 4096000,
               "backoff_db": 12, "cfr": False, "enabled": False, "cal_dbm_at_0db": {"6C": 7.5, "5C": 7.8, "100.6": 8.1, "K21": 6.2, "S21": 6.9}},
        "fm": default_fm(), "dvbt": {"bandwidth_mhz": 8, "fft": "8k", "constellation": "64-QAM", "code_rate": "2/3", "guard": "1/32", "cell_id": 0, "hierarchy": "none"},
        "dvbc": {"symbol_rate": 6900000, "constellation": "256-QAM", "sps": 2}, "dvb": default_dvb(), "pal": default_pal(),
        "dvbs": {"system": "dvbs", "transponder_mhz": 11300, "polarization": "H", "lof_mhz": 9750, "tone_22khz": False, "symbol_rate": 6000000, "fec": "3/4", "modulation": "QPSK", "rolloff": 0.35, "pilots": False},
    }


def audio_src(**kw):
    d = {"kind": "tone", "file": "", "freq_hz": 1000, "level_dbfs": -18, "left_hz": 1000, "right_hz": 400, "sweep_from_hz": 20, "sweep_to_hz": 15000,
         "sweep_s": 10, "loop": True, "playlist": "", "shuffle": False, "crossfade_s": 0, "normalize": True, "dls_from_tags": True}
    d.update(kw)
    return d


def default_fm():
    return {"freq_mhz": 100.6, "deviation_khz": 75, "pilot_khz": 6.75, "preemphasis_us": 50, "stereo": True,
            "limiter": {"enable": True, "threshold": 1.0, "release_db_s": 3, "clip": True}, "mpx_power_dbr": None, "audio_gain_db": 0,
            "audio": {"source": audio_src(kind="playlist", playlist="Demo")},
            "rds": {"enable": True, "pi": "0xD3C4", "ps": "DABLAB", "rt": "DAB-Lab UKW-Testsender – RDS aktiv", "rtplus": [], "pty": 10, "tp": True, "ta": False, "ms": True,
                    "di": {"stereo": True, "artificial_head": False, "compressed": False, "dynamic_pty": False},
                    "af": [93.3, 99.9], "af_method": "A", "af_regional": [], "ecc": "0xE0", "lic": None, "pin": None, "ct": True, "utc_offset_halfhours": "auto", "ct_latency_s": 0,
                    "ptyn": "", "lps": "", "eon": [], "level_khz": 2.0, "phase_deg": 0, "group_version": "A", "rt_version": "A",
                    "rates": {"1a": 60, "3a": 20, "10a": 10, "11a": 30, "14a": 8, "15a": 20}, "rt_from_tags": True},
            "second_carrier": {"enable": False, "offset_khz": 200, "level_db": -10, "deviation_khz": 75, "stereo": True, "preemphasis_us": 50, "pilot_khz": 6.75,
                               "audio": {"source": audio_src(kind="noise")}, "rds": {"enable": False, "pi": "0xD3C5", "ps": "ZWEITER"}}}


def program(n, name=None, lcn=None, typ="tv", vcodec="mpeg2", res="sd", vbr=3500, acodec="mp2", abr=192):
    return {"key": f"prg{n}", "name": name or f"DAB-Lab TV {n}", "provider": "DAB-Lab", "sid": f"0x{0x1000 + n:04X}", "lcn": lcn or n, "type": typ,
            "video": {"source": {"kind": "testpattern", "pattern": "smptebars", "file": "", "url": "", "playlist": "", "loop": True}, "codec": vcodec, "resolution": res, "bitrate_kbps": vbr},
            "audio": {"codec": acodec, "bitrate_kbps": abr, "source": audio_src()},
            "eit": {"title": f"Testsendung {n}", "description": "DAB-Lab Laborsender – Prüfprogramm", "duration_min": 60}}


def default_dvb():
    return {"programs": [program(1), program(2, "DAB-Lab Zwei HD", 5, vcodec="h264", res="hd", vbr=6000, acodec="aac", abr=128), program(3, "DAB-Lab Radio", 99, typ="radio")],
            "si": {"network_id": "0xFF16", "network_name": "DAB-Lab Netz", "onid": "0xFF16", "tsid": "0x0016", "lcn": True, "eit": True, "tdt": True, "private_data": "eacem"}}


def default_pal():
    return {"system": "BG", "fs": 8000000, "live_fs": 7000000, "live": False, "offset_hz": "auto", "level": 0.9,
            "image": {"kind": "testpattern", "pattern": "pal75bars", "file": "", "url": "", "text": "DABLAB Test", "text2": "", "timestamp": True},
            "sound": {"kind": "tone", "freq_hz": 1000, "deviation_hz": 50000, "level_db": -13, "preemphasis_us": 50, "tone_level": 1.0, "file": ""}}


PAL_SOUND_MHZ = {"BG": 5.5, "I": 6.0, "DK": 6.5}


def pal_image_png(cfg):
    """Demoduliertes Bild (Simulation): 75-%-Farbbalken mit dunklem Textband oben/unten, 384×288."""
    w, hgt = 384, 288
    bars = [(191, 191, 191), (191, 191, 0), (0, 191, 191), (0, 191, 0), (191, 0, 191), (191, 0, 0), (0, 0, 191), (0, 0, 0)]
    pat = cfg["pal"].get("image", {}).get("pattern")
    rows = []
    for y in range(hgt):
        row = bytearray([0])
        for x in range(w):
            if pat == "grey":
                v = (x * 8 // w) * 255 // 7
                c = (v, v, v)
            else:
                c = bars[x * 8 // w]
            if y < 30 or y > hgt - 30:
                c = (20, 20, 20) if (x // 6 + y // 6) % 7 else (230, 230, 230)
            n = random.randint(-6, 6)
            row += bytes(max(0, min(255, k + n)) for k in c)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    import base64
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, hgt, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(data).decode()


def build_pal_report(kind):
    """PAL-Bericht wie runner/pal (check): Pegel relativ zur Synchronspitze, Ton, Korrelation, PNG."""
    cfg = ST["config"]
    p = cfg["pal"]
    running = ST["tx"]["state"] == "running" and cfg["rf"]["enabled"]
    if kind == "monitor" and not running:
        return {"ok": False, "lock": False, "error": "Kein Bildträger – Sender aus oder stumm"}
    v = cfg["rf"]["freq_hz"]
    off = PAL_SOUND_MHZ.get(p["system"], 5.5) * 1e6
    err = random.uniform(-300, 300) if kind == "monitor" else random.uniform(-20, 20)
    snd = p["sound"]
    has_snd = snd["kind"] not in ("off", "silence")
    tone = round(round(snd["freq_hz"] / 6.25) * 6.25, 2)
    fs = p["live_fs"] if p.get("live") else p["fs"]
    auto = {"DK": 2675000, "I": 2175000}.get(p["system"], 2100000 if fs == 7000000 else 2250000)
    offset = auto if p["offset_hz"] == "auto" else int(p["offset_hz"])
    return {"ok": True, "lock": True, "mode": "pal", "vision_carrier_hz": round(v + err), "sound_carrier_hz": round(v + off + err + random.uniform(-50, 50)),
            "sound_offset_hz": round(off + random.uniform(-50, 50)), "lo_hz": v + offset, "sample_rate": fs,
            "sound_deviation_hz": round(snd["deviation_hz"] * random.uniform(0.96, 1.04)) if has_snd else 0,
            "sound_freq_hz": tone if snd["kind"] == "tone" else None,
            "sound_vision_db": round(snd["level_db"] + random.uniform(-0.4, 0.4), 2) if snd["kind"] != "off" else None,
            "levels": {"sync_rel": 1.0, "blanking_rel": round(0.73 + random.uniform(-0.008, 0.008), 3), "black_rel": round(0.70 + random.uniform(-0.008, 0.008), 3),
                       "white_rel": round(0.10 + random.uniform(-0.006, 0.006), 3), "chroma_rel": round(0.21 + random.uniform(-0.01, 0.01), 3)},
            "line_freq_hz": round(15625 + random.uniform(-0.2, 0.2), 2), "color_burst": True, "correlation": round(random.uniform(0.975, 0.995), 3),
            "loop_point_ok": True, "clipping": 0, "snr_db": round(random.uniform(46, 52) if kind == "check" else random.uniform(34, 42), 1),
            "image": pal_image_png(cfg), "image_info": f"Verfahren fft, 384×288, Schleife {fs / 1e6:.0f} MS/s", "mismatches": []}


def standard_of(cfg):
    m = cfg.get("mode")
    return m if m in ("fm", "dvbt", "dvbc", "dvbs", "pal", "atsc", "isdbt", "dtmb", "dvbt2") else "dab"


BITS = {"QPSK": 2, "16-QAM": 4, "32-QAM": 5, "64-QAM": 6, "128-QAM": 7, "256-QAM": 8}


def net_rate(cfg):
    std = standard_of(cfg)
    if std == "dvbt":
        m = cfg["dvbt"]
        a, b = (int(x) for x in m["code_rate"].split("/"))
        ga, gb = (int(x) for x in m["guard"].split("/"))
        tu = 8192 * 7 / (8 * float(m["bandwidth_mhz"])) * 1e-6
        return 6048 * BITS[m["constellation"]] * a / b * 188 / 204 / (tu * (1 + ga / gb))
    if std == "dvbc":
        m = cfg["dvbc"]
        if m.get("annex") == "b":
            return {"64-QAM": 26970352, "256-QAM": 38810701}.get(m["constellation"], 0)
        return m["symbol_rate"] * BITS[m["constellation"]] * 188 / 204
    if std == "atsc":
        return 19392658.46
    if std == "isdbt":
        m = cfg["isdbt"]
        fr = lambda s: (lambda a, b: a / b)(*(int(x) for x in s.split("/")))  # noqa: E731
        lay = lambda segs, ly: 96e6 / 252 * segs * BITS[ly["constellation"]] * fr(ly["code_rate"]) * 188 / 204 / (1 + fr(m["guard"]))  # noqa: E731
        two = int(m.get("layers", 1)) == 2
        return lay(1 if two else 13, m["layer_a"]) + (lay(12, m["layer_b"]) if two else 0)
    if std == "dtmb":
        m = cfg["dtmb"]
        b = {"4-QAM-NR": 1, "4-QAM": 2, "16-QAM": 4, "32-QAM": 5, "64-QAM": 6}[m["constellation"]]
        return 7560000 / (3780 + int(m["header"])) * 3744 * b / 7488 * {"0.4": 3008, "0.6": 4512, "0.8": 6016}[str(m["code_rate"])]
    if std == "dvbt2":
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runner"))
        from dvb import tables as T2             # gleiche Rechnung wie Runner und dvbt2_mod --print-rates
        m = cfg["dvbt2"]
        try:
            return float(T2.dvbt2_useful_bitrate(int(m["bandwidth_mhz"]), m["fft"], m.get("ext"), m["constellation"], m["code_rate"], m["guard"], int(m["pp"])))
        except (KeyError, IndexError, ValueError):
            return 0.0
    if std == "dvbs":
        m = cfg["dvbs"]
        a, b = (int(x) for x in m["fec"].split("/"))
        if m["system"] == "dvbs2":
            return m["symbol_rate"] * (3 if m["modulation"] == "8PSK" else 2) * a / b * (0.9636 if m["pilots"] else 0.9888) * 0.993
        return m["symbol_rate"] * 2 * a / b * 188 / 204
    return 0.0


def rf_path(cfg):
    std = standard_of(cfg)
    if std in ("pal", "atsc", "isdbt", "dtmb", "dvbt2") or (std == "dvbc" and cfg["dvbc"].get("annex") == "b"):
        return "device"
    if std == "dvbt" and float(cfg["dvbt"]["bandwidth_mhz"]) >= 7:
        return "device"
    if std == "dvbc" and cfg["dvbc"].get("sps", 2) * cfg["dvbc"]["symbol_rate"] > 7150000:
        return "device"
    if std == "dvbs" and 2 * cfg["dvbs"]["symbol_rate"] > 7150000:
        return "device"
    return "usb"


STRUCT_IGNORE = {"name"}
LOCK = threading.RLock()
ST = {
    "config": default_config(),
    "tx": {"state": "idle", "since": None, "session": None, "underruns": 0, "samples_sent": 0, "rate_ms": None, "buffer_ms": None, "now_playing": {}},
    "pluto": {"connected": True, "uri": "usb:1.5.5", "fw": "v0.31-dablab.20261023-b3", "temp_c": 41.5, "lo_hz": 185360000, "atten_db": ATT_MAX},
    "mac": {"reachable": True, "host": "wsl", "tools": {"odr-audioenc": {"ok": True, "version": "3.6.0"}, "odr-dabmux": {"ok": True, "version": "5.1.0"},
                                                         "odr-dabmod": {"ok": True, "version": "3.0.0"}, "welle-cli": {"ok": True}}},
    "log": [],
    "check": None, "monitor": None,
    "sweep": None,
    # Einrichtung (wie pc/server.py); Tests starten eingerichtet, --onboarding startet ohne
    "onboarding": {"done": True, "version": 1, "required_version": 1, "mode": "lab", "callsign": "", "accept": [],
                   "accepted_at": "2026-10-23T12:00:00+00:00"},
}
ONB_ACCEPT = {"lab": ["shielded", "no_broadcast_radiation", "terms", "privacy", "codecs"],
              "ham": ["shielded", "no_broadcast_radiation", "terms", "privacy", "codecs", "ham_license", "ham_rules"]}
# simulierte Komponenten (wie pc/setup.py); ytdlp/win_ffmpeg fehlen, damit der Test eine Installation auslösen kann
SETUP = {"installed": {"wsl_distro": True, "base": True, "ffmpeg": True, "tsduck": True, "odr": True, "welle": True, "modulators": True,
                       "ytdlp": False, "win_ffmpeg": False},
         "job": {"running": False, "key": None, "lines": [], "result": None, "error": None, "finished": None}}
SETUP_META = [("wsl_distro", False, []), ("base", False, ["wsl_distro"]), ("ffmpeg", False, ["wsl_distro"]), ("tsduck", False, ["base"]),
              ("odr", False, ["base"]), ("welle", False, ["base"]), ("modulators", False, ["base"]), ("ytdlp", True, ["base"]), ("win_ffmpeg", True, [])]


# simulierter Firmware-Flasher (wie pc/flasher.py)
FWM = {"tested": False, "history": [], "job": {"running": False, "action": None, "phase": None, "lines": [], "result": None, "error": None}}
FW_IMG = {"id": "76a35113aa000000", "kind": "shipped", "name": "pluto_fw\\firmware\\pluto.frm", "version": "v0.31-dablab.20261023-b3",
          "size": 10128723, "sha256": "76a35113" + "0" * 56, "ok": True, "errors": []}


def fw_status():
    img = dict(FW_IMG, ram_tested={"at": "2026-10-23T21:00:00", "fw": FW_IMG["version"]} if FWM["tested"] else None)
    return {"images": [img], "job": dict(FWM["job"]), "running_fw": "v0.31-dablab.20261023-b3", "history": FWM["history"][-10:],
            "user_dir": "runtime\\firmware"}


def fw_job(action):
    FWM["job"] = {"running": True, "action": action, "phase": "release", "lines": ["Pluto freigegeben"], "result": None, "error": None}
    for ph in ("dfu_mode", "download", "boot", "verify"):
        time.sleep(0.15)
        FWM["job"]["phase"] = ph
        FWM["job"]["lines"].append(ph)
    if action == "ram_test":
        FWM["tested"] = True
    FWM["history"].append({"at": "2026-10-23T21:00:00", "action": action, "image": FW_IMG["name"], "ok": True, "error": None})
    FWM["job"].update(running=False, result="ok", phase="done")


def setup_status():
    comps = [{"key": k, "license": "Testlizenz", "source": "https://example.org/" + k, "optional": o, "needs": n,
              "installed": SETUP["installed"][k], "accepted": SETUP["installed"][k]} for k, o, n in SETUP_META]
    return {"wsl": {"installed": True, "distro": "dablab", "distro_ok": True}, "components": comps, "ubuntu": "24.04",
            "complete": all(c["installed"] for c in comps if not c["optional"]), "job": dict(SETUP["job"]),
            "all_running": SETUP.get("all_running", False)}


def setup_all(via):
    SETUP["all_running"] = True
    try:
        todo = [k for k, o, _ in SETUP_META if not o and not SETUP["installed"][k]]
        for i, k in enumerate(todo):
            setup_job(k)
            SETUP["job"].update(queue=todo[i + 1:], via=via)
    finally:
        SETUP["all_running"] = False


def setup_job(key):
    SETUP["job"] = {"running": True, "key": key, "lines": [f"@@STEP installiere {key}"], "result": None, "error": None, "finished": None,
                    "via": SETUP.get("via")}
    time.sleep(1.2 if not FAST else 0.6)
    SETUP["installed"][key] = True
    SETUP["job"].update(running=False, result="ok", finished=time.time(), lines=SETUP["job"]["lines"] + [f"@@OK {key}"])
def _preset_std(std, **rf):
    c = default_config()
    c["mode"] = std
    c["name"] = {"fm": "UKW Standard", "dvbt": "DVB-T Standard", "dvbc": "DVB-C Standard", "pal": "PAL Standard", "dvbs": "DVB-S Standard"}[std]
    c["rf"].update(rf)
    if std == "fm":
        c["fm"]["freq_mhz"] = round(rf.get("freq_hz", 100600000) / 1e6, 3)
        c["rf"]["backoff_db"] = 3
    return c


PRESETS = {"Standard": default_config(), "UKW Standard": _preset_std("fm", channel="fm", freq_hz=100600000),
           "DVB-T Standard": _preset_std("dvbt", channel="K21", freq_hz=474000000), "DVB-C Standard": _preset_std("dvbc", channel="S21", freq_hz=306000000),
           "PAL Standard": _preset_std("pal", channel="K36", freq_hz=591250000), "DVB-S Standard": _preset_std("dvbs", channel="sat", freq_hz=1550000000)}
PLAYLISTS: dict[str, dict] = {}
NOW: dict[str, dict] = {}        # service key → Wiedergabezustand (nur Simulation)


def init_playlists():
    PLAYLISTS["Demo"] = {"name": "Demo", "items": [playlist_item(n) for n in ("song.mp3", "jingle.ogg", "klassik.flac", "nachrichten.m4a")]}
    PLAYLISTS["Sprache"] = {"name": "Sprache", "items": [playlist_item("sprache.wav"), playlist_item("podcast.opus")]}


def tick_playlists(dt: float):
    """Simuliert now_playing je Playlist-Dienst (Titel laufen durch, Endlos)."""
    cfg = ST["config"]
    active = set()
    sources = [(s["key"], s.get("audio", {}).get("source", {}), s) for s in cfg["services"]]
    if standard_of(cfg) == "fm":
        sources = [("fm", cfg["fm"]["audio"]["source"], None)]
    for key, src, s in sources:
        if src.get("kind") != "playlist":
            continue
        pl = PLAYLISTS.get(src.get("playlist") or "")
        if not pl or not pl["items"]:
            continue
        active.add(key)
        st = NOW.setdefault(key, {"index": 0, "elapsed": 0.0})
        st["elapsed"] += dt * (30 if FAST else 1)
        it = pl["items"][st["index"] % len(pl["items"])]
        dur = it.get("duration_s") or 180
        if st["elapsed"] >= dur:
            st["elapsed"] = 0.0
            st["index"] = (st["index"] + 1) % len(pl["items"])
            if st["index"] == 0 and not src.get("loop", True):
                st["elapsed"] = dur
            it = pl["items"][st["index"]]
            log(f"{key}: Titel {st['index'] + 1} „{it.get('title') or it['file']}“", src="playlist")
            if s is not None and src.get("dls_from_tags", True):
                s["pad"]["dls"]["text"] = " – ".join(x for x in (it.get("artist"), it.get("title")) if x) or it["file"]
            if s is None and cfg["fm"]["rds"].get("rt_from_tags"):
                cfg["fm"]["rds"]["rtplus"] = [{"type": "ITEM.ARTIST", "text": it.get("artist", "")}, {"type": "ITEM.TITLE", "text": it.get("title", "")}]
                cfg["fm"]["rds"]["rt"] = " - ".join(x for x in (it.get("artist"), it.get("title")) if x) or it["file"]
    for k in list(NOW):
        if k not in active:
            NOW.pop(k)
    ST["tx"]["now_playing"] = {k: now_entry(k) for k in NOW}


def now_entry(key):
    cfg = ST["config"]
    s = next((x for x in cfg["services"] if x["key"] == key), None)
    st = NOW.get(key)
    src = cfg["fm"]["audio"]["source"] if key == "fm" else (s["audio"]["source"] if s else None)
    if not src or not st:
        return None
    pl = PLAYLISTS.get(src.get("playlist") or "")
    if not pl or not pl["items"]:
        return None
    i = st["index"] % len(pl["items"])
    it = pl["items"][i]
    return {"service": key, "index": i, "count": len(pl["items"]), "playlist": pl["name"], "file": it["file"],
            "title": it.get("title") or Path(it["file"]).name, "artist": it.get("artist", ""),
            "elapsed_s": round(st["elapsed"], 1), "duration_s": it.get("duration_s")}


def playlist_ctl(d):
    key, action = d.get("service"), d.get("action")
    s = next((x for x in ST["config"]["services"] if x["key"] == key), None)
    src = ST["config"]["fm"]["audio"]["source"] if key == "fm" else (s["audio"]["source"] if s else None)
    if not src or src.get("kind") != "playlist":
        return 404, {"ok": False, "error": "Dienst hat keine Playlist-Quelle"}
    pl = PLAYLISTS.get(src.get("playlist") or "")
    if not pl or not pl["items"]:
        return 409, {"ok": False, "error": "Playlist leer oder unbekannt"}
    st = NOW.setdefault(key, {"index": 0, "elapsed": 0.0})
    n = len(pl["items"])
    if action == "next":
        st["index"] = (st["index"] + 1) % n
    elif action == "prev":
        st["index"] = (st["index"] - 1) % n
    elif action == "jump":
        st["index"] = int(d.get("index", 0)) % n
    else:
        return 400, {"ok": False, "error": "action: next|prev|jump"}
    st["elapsed"] = 0.0
    ST["tx"]["now_playing"] = {k: now_entry(k) for k in NOW}
    return 200, {"ok": True, "now_playing": now_entry(key)}
FAST = False
SWEEP_STOP = threading.Event()
OUTAGE_UNTIL = 0.0   # Testhilfe: simulierter Serverausfall
JOBS: dict[str, dict] = {}   # YouTube-Downloads (Simulation)
DEVICES = {"video": ["USB3.0 Capture (Elgato Cam Link 4K)", "HD Pro Webcam C920"], "audio": ["Digitale Audioschnittstelle (Cam Link 4K)", "Mikrofon (HD Pro Webcam C920)"],
           "screens": ["Bildschirm 1 (2560x1440)", "Bildschirm 2 (1920x1080)"], "nvenc": {"ok": True, "gpu": "NVIDIA GeForce RTX 3060", "sessions": 5, "hevc_main10": True}}


def youtube_worker(job_id, url):
    total = 1.5 if FAST else 12.0
    t0 = time.time()
    while True:
        time.sleep(0.2)
        pct = min(100.0, (time.time() - t0) / total * 100)
        with LOCK:
            j = JOBS[job_id]
            j.update(status="running", percent=round(pct, 1), speed=round(random.uniform(2.5, 6.0) * 1048576), eta_s=round(max(0.0, total - (time.time() - t0)), 1))
        if pct >= 100:
            break
    name = f"video/yt_{job_id}.mp4"
    (MEDIA / "video").mkdir(exist_ok=True)
    (MEDIA / "video" / f"yt_{job_id}.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42mock")
    with LOCK:
        JOBS[job_id].update(status="done", percent=100.0, file=f"media/{name}", title="Beispielvideo (yt-dlp)", duration_s=212.0, eta_s=0)
    log(f"YouTube-Download fertig: media/{name}", src="yt-dlp")


def log(msg, lvl="info", src="server"):
    with LOCK:
        ST["log"].append({"t": round(time.time(), 3), "lvl": lvl, "msg": msg, "src": src})
        del ST["log"][:-200]


def apply_rf_to_pluto():
    rf = ST["config"]["rf"]
    running = ST["tx"]["state"] == "running"
    ST["pluto"]["lo_hz"] = rf["freq_hz"] + rf.get("offset_hz", 0)
    ST["pluto"]["atten_db"] = rf["atten_db"] if (running and rf["enabled"]) else ATT_MAX


def validate(cfg):
    errs = []
    try:
        rf = cfg["rf"]
        if not (0 <= float(rf["atten_db"]) <= ATT_MAX):
            errs.append({"path": "rf.atten_db", "msg": "Dämpfung außerhalb 0–89,75 dB"})
        en = cfg["ensemble"]
        if len(en["label"]) > 16:
            errs.append({"path": "ensemble.label", "msg": "Ensemble-Label > 16 Zeichen"})
        for i, s in enumerate(cfg["services"]):
            if len(s["label"]) > 16 or len(s["shortlabel"]) > 8:
                errs.append({"path": f"services.{i}.label", "msg": f"Dienst {s['key']}: Label zu lang"})
    except Exception as e:  # noqa: BLE001
        errs.append({"path": "", "msg": f"Schema: {e}"})
    return errs


def structure(cfg):
    c = copy.deepcopy(cfg)
    for k in ("channel", "freq_hz", "offset_hz", "atten_db", "enabled", "cal_dbm_at_0db"):
        c["rf"].pop(k, None)
    c.pop("name", None)
    c["ensemble"].pop("label", None)
    c["ensemble"].pop("shortlabel", None)
    for s in c["services"]:
        s.pop("label", None)
        s.pop("shortlabel", None)
        s["pad"]["dls"].pop("text", None)
    for cl in c["clusters"]:
        cl.pop("active", None)
    return c


def build_report(kind):
    cfg = ST["config"]
    en = cfg["ensemble"]
    codec = {"aac-lc": "AAC-LC", "he-aac": "HE-AAC", "he-aac-v2": "HE-AACv2", "mp2": "MP2"}
    services = []
    for i, s in enumerate(cfg["services"]):
        p = s["protection"]
        prot = f"UEP {p['level']}" if p["profile"] == "UEP" else f"EEP {p['level']}-{'A' if p['profile'] == 'EEP_A' else 'B'}"
        audio = s["audio"]
        r = {"sid": s["sid"].lower(), "label": s["label"], "shortlabel": s["shortlabel"], "pty": s["pty"],
             "language": int(s["language"], 16), "codec": codec.get(audio["codec"], audio["codec"]),
             "bitrate": audio["bitrate"] if s["type"] != "data" else s["data"]["bitrate"], "protection": prot,
             "samplerate": audio["samplerate"], "channels": audio["channels"],
             "dls": s["pad"]["dls"]["text"] if s["pad"]["enable"] and s["pad"]["dls"]["enable"] else "",
             "dlplus": [], "slides": [], "audio_level_db": [round(-18 + random.uniform(-1, 1), 1), round(-18 + random.uniform(-1, 1), 1)],
             "errors": {"frame": 0, "rs": 0, "aac": 0}}
        if s["pad"]["sls"]["enable"]:
            r["slides"] = [{"file": f, "w": 320, "h": 240} for f in s["pad"]["sls"]["slides"]]
        if s["pad"]["dls"]["dlplus"]["enable"]:
            d = s["pad"]["dls"]["dlplus"]
            r["dlplus"] = [{"type": "ITEM.ARTIST", "text": d["artist"]}, {"type": "ITEM.TITLE", "text": d["title"]}]
        # absichtliche Abweichungen zur Demonstration des Soll/Ist-Vergleichs
        if i == 1:
            r["shortlabel"] = s["shortlabel"][:-1] + "X" if s["shortlabel"] else "X"
        if kind == "monitor" and i == 0:
            r["errors"] = {"frame": 0, "rs": 3, "aac": 1}
        services.append(r)
    return {
        "ok": True, "sync": True, "time": time.time(),
        "snr_db": round(random.uniform(22, 26) if kind == "check" else random.uniform(14, 19), 1),
        "freq_corr_hz": round(random.uniform(-3, 3) if kind == "check" else random.uniform(-120, 120), 1),
        "ensemble": {"eid": en["eid"].lower(), "label": en["label"], "shortlabel": en["shortlabel"], "ecc": en["ecc"].lower(),
                     "lto": 2 if en["lto"] == "auto" else en["lto"], "datetime": "2026-10-22T12:00:00Z" if en["datetime"] else None},
        "services": services,
        "tii": [{"comb": cfg["tii"]["comb"], "pattern": cfg["tii"]["pattern"]}] if cfg["tii"]["enable"] else [],
        "announcements": [c["id"] for c in cfg["clusters"] if c["active"]],
        "raw": {"mux.json": {"note": "mock"}},
    }


def build_fm_report(kind):
    """UKW-Bericht im SPEC-Format: measured / rds / expected / fields / mismatches / second_carrier."""
    cfg = ST["config"]
    f = cfg["fm"]
    r = f["rds"]
    running = ST["tx"]["state"] == "running" and cfg["rf"]["enabled"]
    if kind == "monitor" and not running:
        return {"ok": False, "mode": "fm", "sync": False, "error": "Kein Träger – Sender aus oder stumm", "measured": {"carrier": False}, "rds": {}, "expected": {}, "fields": [], "mismatches": []}
    pilot = f["pilot_khz"] if f["stereo"] else 0.0
    rdsk = r["level_khz"] if r["enable"] else 0.0
    dev = f["deviation_khz"] * random.uniform(0.985, 1.015)
    measured = {"carrier": True, "deviation_peak_khz": round(dev, 2), "deviation_audio_khz": round(dev - pilot - rdsk, 2), "deviation_rms_khz": round(dev * 0.32, 2),
                "pilot_khz": round(pilot * random.uniform(0.98, 1.02), 2), "pilot_freq_hz": round(19000 + random.uniform(-0.5, 0.5), 2),
                "pilot_phase_deg": round(r["phase_deg"] + random.uniform(-3, 3), 1), "rds_khz": round(rdsk * random.uniform(0.97, 1.03), 2), "carrier_38k_khz": round(random.uniform(0.05, 0.3), 2),
                "mpx_power_dbr": round((f["mpx_power_dbr"] if f["mpx_power_dbr"] is not None else 1.8) + random.uniform(-0.4, 0.4), 2),
                "stereo_separation_db": round(random.uniform(48, 56), 1) if f["stereo"] else None, "sinad_db": round(random.uniform(52, 60) if kind == "check" else random.uniform(38, 48), 1),
                "thd_pct": round(random.uniform(0.05, 0.3), 2), "spurs_dbc": round(random.uniform(-75, -65), 1), "freq_corr_hz": round(random.uniform(-40, 40) if kind == "monitor" else random.uniform(-2, 2), 1)}
    expected = {"deviation_peak_khz": f["deviation_khz"], "pilot_khz": pilot, "pilot_phase_deg": r["phase_deg"], "rds_khz": rdsk, "mpx_power_dbr": f["mpx_power_dbr"], "stereo": f["stereo"]}
    fields = [
        {"name": "Spitzenhub (kHz)", "soll": f["deviation_khz"], "ist": measured["deviation_peak_khz"], "ok": abs(measured["deviation_peak_khz"] - f["deviation_khz"]) <= f["deviation_khz"] * 0.05},
        {"name": "Pilothub (kHz)", "soll": pilot, "ist": measured["pilot_khz"], "ok": abs(measured["pilot_khz"] - pilot) <= 0.3},
        {"name": "Pilotfrequenz (Hz)", "soll": 19000, "ist": measured["pilot_freq_hz"], "ok": abs(measured["pilot_freq_hz"] - 19000) <= 2},
        {"name": "57-kHz-Phase (°)", "soll": r["phase_deg"], "ist": measured["pilot_phase_deg"], "ok": abs(measured["pilot_phase_deg"] - r["phase_deg"]) <= 10},
        {"name": "RDS-Hub (kHz)", "soll": rdsk, "ist": measured["rds_khz"], "ok": abs(measured["rds_khz"] - rdsk) <= 0.2},
        {"name": "MPX-Leistung (dBr)", "soll": f["mpx_power_dbr"], "ist": measured["mpx_power_dbr"], "ok": f["mpx_power_dbr"] is None or abs(measured["mpx_power_dbr"] - f["mpx_power_dbr"]) <= 1},
        {"name": "Stereo", "soll": f["stereo"], "ist": measured["pilot_khz"] > 1, "ok": f["stereo"] == (measured["pilot_khz"] > 1)},
    ]
    rds = {}
    if r["enable"]:
        rtplus = list(r["rtplus"])
        rds = {"pi": r["pi"].lower(), "ps": r["ps"], "rt": r["rt"], "rtplus": [{"type": t.get("type"), "text": t.get("text")} for t in rtplus], "pty": r["pty"], "pty_name": "Pop Music",
               "tp": r["tp"], "ta": r["ta"], "ms": r["ms"], "di": dict(r["di"]), "af": list(r["af"]), "af_method": r["af_method"], "ecc": r["ecc"].lower() if r["ecc"] else None, "country": "DE",
               "ct": "2026-10-23T01:00:00+02:00" if r["ct"] else None, "ptyn": r["ptyn"] or None, "lps": r["lps"] or None,
               "eon": [{"pi": e["pi"].lower(), "ps": e["ps"], "pty": e["pty"], "af": e.get("af", []), "tp": e["tp"], "ta": e["ta"]} for e in r["eon"]],
               "group_rate": round(11.4 + random.uniform(-0.1, 0.1), 2), "bler_pct": 0.0 if kind == "check" else round(random.uniform(0, 1.5), 2),
               "groups": {"0A": 456, "1A": 114, "2A": 171, "4A": 2, "14A": 114 if r["eon"] else 0}}
        ist_ps = rds["ps"] if len(r["ps"]) <= 7 else r["ps"][:7] + "?"     # absichtliche Abweichung bei 8 Zeichen (Demo)
        fields += [
            {"name": "RDS PI", "soll": r["pi"], "ist": rds["pi"], "ok": True},
            {"name": "RDS PS", "soll": r["ps"], "ist": ist_ps, "ok": ist_ps == r["ps"]},
            {"name": "RDS RT", "soll": r["rt"], "ist": r["rt"], "ok": True},
            {"name": "RDS PTY", "soll": r["pty"], "ist": r["pty"], "ok": True},
            {"name": "RDS TP/TA", "soll": f"{int(r['tp'])}/{int(r['ta'])}", "ist": f"{int(r['tp'])}/{int(r['ta'])}", "ok": True},
            {"name": "RDS AF", "soll": r["af"], "ist": r["af"], "ok": True},
            {"name": "RDS CT", "soll": r["ct"], "ist": bool(r["ct"]), "ok": True},
        ]
        if r["eon"]:
            fields.append({"name": "RDS EON", "soll": len(r["eon"]), "ist": len(r["eon"]), "ok": True})
        if rtplus:
            fields.append({"name": "RDS RT+", "soll": [t.get("type") for t in rtplus], "ist": [t.get("type") for t in rtplus], "ok": True})
    mism = [{"name": x["name"], "soll": x["soll"], "ist": x["ist"]} for x in fields if x["ok"] is False]
    rep = {"ok": not mism, "mode": "fm", "sync": True, "measured": measured, "rds": rds, "expected": expected, "fields": fields, "mismatches": mism,
           "snr_db": measured["sinad_db"], "freq_corr_hz": measured["freq_corr_hz"]}
    sc = f["second_carrier"]
    if sc["enable"]:
        rep["second_carrier"] = {"offset_khz": sc["offset_khz"], "level_db": round(sc["level_db"] + random.uniform(-0.3, 0.3), 1), "deviation_peak_khz": round(sc["deviation_khz"] * random.uniform(0.98, 1.02), 1),
                                 "pi": sc["rds"]["pi"].lower() if sc["rds"]["enable"] else None, "ps": sc["rds"]["ps"] if sc["rds"]["enable"] else None}
    return rep


def build_dvb_report(kind):
    cfg = ST["config"]
    std = standard_of(cfg)
    m = cfg.get(std) or cfg["dvbc"]
    si = cfg["dvb"]["si"]
    running = ST["tx"]["state"] == "running" and cfg["rf"]["enabled"]
    if kind == "monitor" and not running:
        return {"ok": False, "lock": False, "error": "Kein Lock – Sender aus oder stumm", "services": [], "tables": {}}
    net = net_rate(cfg)
    if std == "dvbs":
        mod = {"system": "DVB-S2" if m["system"] == "dvbs2" else "DVB-S", "transponder_mhz": m["transponder_mhz"], "polarization": m["polarization"],
               "symbol_rate": m["symbol_rate"], "modulation_type": m["modulation"], "fec": m["fec"], "roll_off": m["rolloff"], "frequency_hz": int(m["transponder_mhz"] * 1e6)}
    else:
        mod = ({"bandwidth_mhz": m["bandwidth_mhz"], "fft": m["fft"], "constellation": m["constellation"], "code_rate": m["code_rate"], "guard": m["guard"], "cell_id": m["cell_id"]}
               if std == "dvbt" else {"symbol_rate": m["symbol_rate"], "constellation": m["constellation"]} if std == "dvbc" else dict(m))
        mod["frequency_hz"] = cfg["rf"]["freq_hz"]
    services = []
    for i, p in enumerate(cfg["dvb"]["programs"]):
        res = {"sd": "720x576i", "hd": "1920x1080p", "hd720": "1280x720p", "uhd": "3840x2160p"}[p["video"]["resolution"]]
        svc = {"sid": p["sid"].lower(), "name": p["name"], "provider": p["provider"], "type": "radio" if p["type"] == "radio" else ("tv_hd" if p["video"]["resolution"] != "sd" else "tv"),
               "lcn": p["lcn"] if si["lcn"] else None, "video_codec": None if p["type"] == "radio" else {"mpeg2": "MPEG-2", "h264": "H.264", "hevc": "HEVC"}[p["video"]["codec"]],
               "hdr": p["video"].get("hdr", "sdr") if p["type"] != "radio" else None,
               "resolution": None if p["type"] == "radio" else res, "audio_codec": {"mp2": "MP2", "aac": "AAC", "ac3": "AC-3"}[p["audio"]["codec"]],
               "video_bitrate_kbps": None if p["type"] == "radio" else p["video"]["bitrate_kbps"], "audio_bitrate_kbps": p["audio"]["bitrate_kbps"],
               "eit_title": p["eit"]["title"] if si["eit"] else None, "eit_description": p["eit"]["description"] if si["eit"] else None,
               "pids": {"pmt": 0x100 + i, "video": 0x200 + i, "audio": 0x300 + i, "pcr": 0x200 + i}}
        if i == 1:
            svc["name"] = svc["name"] + " "          # absichtlich: Leerzeichen (wird getrimmt) – kein Fehler
            svc["eit_title"] = (svc["eit_title"] or "") + "!"   # absichtliche Abweichung
        services.append(svc)
    streams = []
    for p in cfg["dvb"]["programs"]:
        if p["type"] != "radio":
            w, hgt = {"sd": (720, 576), "hd": (1920, 1080), "hd720": (1280, 720), "uhd": (3840, 2160)}[p["video"]["resolution"]]
            streams.append({"type": "video", "codec": {"mpeg2": "mpeg2video", "h264": "h264", "hevc": "hevc"}[p["video"]["codec"]], "color_transfer": {"hdr10": "smpte2084", "hlg": "arib-std-b67"}.get(p["video"].get("hdr"), "bt709"), "width": w, "height": hgt, "fps": "25", "bitrate": p["video"]["bitrate_kbps"] * 1000})
        streams.append({"type": "audio", "codec": {"mp2": "mp2", "aac": "aac", "ac3": "ac3"}[p["audio"]["codec"]], "samplerate": 48000, "channels": 2, "bitrate": p["audio"]["bitrate_kbps"] * 1000})
    return {"ok": True, "lock": True, "mer_db": round(random.uniform(34, 38) if kind == "check" else random.uniform(24, 31), 1),
            "snr_db": round(random.uniform(30, 36), 1), "ber_pre": 0.0 if kind == "check" else round(random.uniform(1e-6, 8e-5), 7), "ber_post": 0.0,
            "freq_corr_hz": round(random.uniform(-500, 500) if kind == "monitor" else 0, 1), "bitrate_bps": round(net * random.uniform(0.999, 1.001)),
            "pcr_jitter_ns_max": round(random.uniform(40, 320)), "continuity_errors": 0, "modulation": mod,
            "tables": {"pat": True, "pmt": len(cfg["dvb"]["programs"]), "sdt": True, "nit": True, "eit": bool(si["eit"]), "tdt": bool(si["tdt"])},
            "nit": {"network_id": si["network_id"].lower(), "network_name": si["network_name"], "onid": si["onid"].lower(), "tsid": si["tsid"].lower(), "delivery": mod},
            "lcn": {p["sid"].lower(): p["lcn"] for p in cfg["dvb"]["programs"]} if si["lcn"] else {},
            "services": services, "ffprobe": {"ok": True, "streams": streams}}


def build_any_report(kind):
    std = standard_of(ST["config"])
    if std == "fm":
        return build_fm_report(kind)
    if std in ("dvbt", "dvbc", "atsc", "isdbt", "dtmb", "dvbt2"):
        return build_dvb_report(kind)
    if std == "pal":
        return build_pal_report(kind)
    return build_report(kind)


def build_monitor(d):
    """Kontrollempfang wie pc/server.py: äußerer Mitschnitt-Bericht mit Spektrum, Dekodierung unter `decode`."""
    import math
    cfg = ST["config"]
    rf = cfg["rf"]
    gain = float(d.get("rx_gain_db", 30))
    secs = float(d.get("seconds", 8))
    running = ST["tx"]["state"] == "running" and rf["enabled"]
    fs = 4096000
    n = 512
    f = [round(-fs / 2 + (i + 0.5) * fs / n) for i in range(n)]
    level = (-70 + (60 - rf["atten_db"]) * 0.5 + (gain - 30) * 0.3) if running else -96
    noise = -96 + (gain - 30) * 0.3
    psd = []
    for x in f:
        v = noise + random.uniform(-1.5, 1.5)
        if running and abs(x) < 768000:
            v = level + random.uniform(-1.2, 1.2) - (abs(x) / 768000) ** 8 * 3
        elif running and abs(x) < 900000:
            v = noise + (level - noise) * (900000 - abs(x)) / 132000 * 0.6 + random.uniform(-1, 1)
        psd.append(round(v, 1))
    inb = round(level if running else noise, 2)
    adj = round(noise + 2, 2)
    clip = round(max(0.0, (gain - 55) * 0.045) if running else 0.0, 4)
    spec = {"in_band_db": inb, "adjacent_db": adj, "delta_db": round(inb - adj, 2), "band_hz": [5000.0, 760000.0],
            "adjacent_hz": [900000.0, 1720319.58], "rms_dbfs": round(-30 + (gain - 30) * 0.5, 2), "clip_pct": clip,
            "psd_db": psd, "psd_f_hz": f}
    std = standard_of(cfg)
    if running and std == "dab":
        dec = build_report("monitor")
        dec.pop("time", None)
        dec["fic_crc_errors"] = random.randint(0, 20)
        dec["ensemble"]["lto_halfhours"] = 4
        dec["ensemble"]["lto"] = 2.0
        dec["decode_seconds"] = round(secs * 1.6, 1)
    elif running:
        dec = build_any_report("monitor")
    else:
        dec = {"ok": False, "sync": False, "snr_db": None, "freq_corr_hz": None, "fic_crc_errors": None, "ensemble": {},
               "services": [], "tii": [], "announcements": [], "error": "Kein Sync – kein Signal (Sender aus oder stumm)"}
    return {"ok": True, "t": time.time(), "seconds": secs, "rx_gain_db": gain, "rx_lo_hz": rf["freq_hz"], "center_hz": rf["freq_hz"],
            "channel": rf["channel"], "fs_capture": fs, "fs_file": 2048000, "capture_s": round(secs + 3.3, 2),
            "file": f"runtime/rx/mon_{time.strftime('%Y%m%d_%H%M%S')}.cf32.iq", "tx_running": ST["tx"]["state"] == "running",
            "rx_path": "proc:usb:1.5.5", "tx_underruns_during": 0 if running else None, "spectrum": spec, "testgen_corr": None,
            "decode": dec}


# ------------------------------------------------------------------ Simulation
def ticker():
    last = time.time()
    while True:
        time.sleep(0.25)
        now = time.time()
        dt = now - last
        last = now
        with LOCK:
            tx = ST["tx"]
            if tx["state"] == "starting" and now - tx["since"] > (0.4 if FAST else 1.5):
                tx["state"] = "running"
                log("Kette läuft – IQ-Strom 4,096 MS/s zum Pluto", src="tx")
                apply_rf_to_pluto()
            if tx["state"] == "running":
                rate = ST["config"]["rf"]["sample_rate"] / 1e6
                tx["samples_sent"] += int(rate * 1e6 * dt)
                tx["rate_ms"] = round(rate * random.uniform(0.995, 1.004), 3)
                tx["buffer_ms"] = round(random.uniform(180, 260))
                if random.random() < 0.004:
                    tx["underruns"] += 1
                    log("Underrun: Pluto-Puffer leer (Erzeugung zu langsam)", "warn", "pluto")
                if random.random() < 0.02:
                    log(random.choice(["odr-dabmux: FIG-Karussell ok", "odr-audioenc srv1: 88 kbit/s, Puffer 3/8",
                                       "odr-padenc srv1: Slide 1 gesendet", "odr-dabmod: Ausgabe 4096000 S/s"]), src="runner")
                ST["pluto"]["temp_c"] = round(min(62, ST["pluto"]["temp_c"] + 0.01), 1)
                tick_playlists(dt)
            else:
                ST["pluto"]["temp_c"] = round(max(38, ST["pluto"]["temp_c"] - 0.01), 1)
                NOW.clear()
                ST["tx"]["now_playing"] = {}


def sweep_worker(p):
    frm, to, step, dwell = float(p["from_db"]), float(p["to_db"]), float(p["step_db"]), float(p["dwell_s"])
    if FAST:
        dwell = min(dwell, 0.3)
    n = int(abs(to - frm) // step) + 1
    d = 1 if to >= frm else -1
    SWEEP_STOP.clear()
    log(f"Pegelrampe {frm}→{to} dB, {n} Stufen", src="sweep")
    for i in range(n):
        att = round(frm + d * i * step, 2)
        with LOCK:
            ST["config"]["rf"]["atten_db"] = att
            apply_rf_to_pluto()
            ST["sweep"] = {"running": True, "from_db": frm, "to_db": to, "step_db": step, "dwell_s": dwell, "index": i + 1,
                           "total": n, "current_db": att, "started": ST["sweep"]["started"] if ST["sweep"] else time.time(),
                           "eta_s": (n - i) * dwell}
        if SWEEP_STOP.wait(dwell):
            log("Pegelrampe abgebrochen", "warn", "sweep")
            break
    with LOCK:
        if ST["sweep"]:
            ST["sweep"]["running"] = False
            ST["sweep"]["eta_s"] = 0
    log("Pegelrampe beendet", src="sweep")


# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DABLabMock/1.0"

    def log_message(self, fmt, *args):  # ruhig
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def jbody(self):
        b = self.body()
        return json.loads(b) if b else {}

    def state(self):
        with LOCK:
            s = {k: copy.deepcopy(ST[k]) for k in ("config", "tx", "pluto", "mac", "log", "check", "monitor", "sweep", "onboarding")}
        s["runner"] = s["mac"]
        path = rf_path(s["config"])
        s["devices"] = DEVICES
        s["rf"] = {"path": path, "path_label": "pluto" if path == "device" else "usb", "sample_rate": s["config"]["rf"].get("sample_rate"), "pal_live": False}
        with LOCK:
            s["jobs"] = copy.deepcopy(JOBS)
        return s

    # -------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p.startswith("/api/") and time.time() < OUTAGE_UNTIL:
            return self.send_json({"error": "simulierter Ausfall"}, 503)
        if p == "/api/state":
            return self.send_json(self.state())
        if p == "/api/onboarding":
            return self.send_json(ST["onboarding"])
        if p == "/api/netinfo":
            return self.send_json({"ok": True, "ips": ["192.168.1.50", "10.0.0.7"], "host": "MOCK-PC"})
        if p == "/api/firmware":
            return self.send_json(fw_status())
        if p == "/api/setup":
            return self.send_json(setup_status())
        if p == "/api/hwcheck":
            bench = "bench=1" in (u.query or "")
            return self.send_json({"gpu": [{"name": "NVIDIA GeForce GTX 1080", "driver": "580.88", "nvenc_gen": "Pascal", "rank": 3, "vendor": "NVIDIA"}],
                                   "nvenc": {"name": "NVIDIA GeForce GTX 1080", "driver": "580.88", "nvenc_gen": "Pascal", "rank": 3, "vendor": "NVIDIA"},
                                   "cpu": {"name": "AMD Ryzen 5 3600 6-Core Processor", "cores": 6, "threads": 12, "mhz": 3600}, "ram_gb": 16, "ffmpeg": True,
                                   "caps": [{"k": k, "status": "ok", "how": "nvenc" if k not in ("dab_fm", "sd") else "cpu"} for k in ("dab_fm", "sd", "hd", "uhd", "hdr10", "datv", "multi")],
                                   "recommend": [{"k": "gpu", "why": "reserve"}], "weak": False,
                                   "bench": {"x264_1080p_fps": 247.3, "nvenc_hevc_2160p_fps": 85.8, "nvenc_hevc10_2160p_fps": 86.2} if bench else None})
        if p == "/api/shop":
            mk = lambda i, g, n: {"id": i, "group": g, "name": n, "note": "", "amazon": f"https://www.amazon.de/s?k={i}&tag=test-21",
                                  "ebay": f"https://www.ebay.de/sch/i.html?_nkw={i}&campid=1", "ali": ""}
            return self.send_json({"market": "de", "available": True, "slots": [mk("pluto", "radio", "ADALM-PLUTO"), mk("satf", "adapter", "SMA → F"),
                                   mk("gpu_rtx", "pc", "RTX 4060 Ti"), mk("qo100_pa", "ham", "2,4-GHz-PA")],
                                   "sponsor": [{"name": "Ko-fi", "url": "https://ko-fi.com/711it"}, {"name": "PayPal", "url": "https://paypal.me/711IT"}]})
        if p == "/api/logs":
            ents = [{"t": time.time() - 60, "lvl": "info", "src": "pc", "msg": "TX-Strom läuft", "text": "TX-Strom läuft", "translated": True, "lvl_text": "Info"},
                    {"t": time.time() - 30, "lvl": "warn", "src": "pc", "msg": "Unbekannte Meldung", "text": "Unbekannte Meldung", "translated": False, "lvl_text": "Warnung"}]
            return self.send_json({"files": [{"name": "muxwright-2026-10-23.log", "size": 2048}], "file": "muxwright-2026-10-23.log", "entries": ents, "total": 2})
        if p == "/api/events":
            return self.sse()
        if p == "/api/preview":            # Live-Vorschau: der Mock sendet keinen Transportstrom -> keine Bilder
            return self.send_json({"ok": True, "session": None, "items": []})
        if p == "/api/media":
            files = [media_item(f) for f in sorted(MEDIA.iterdir()) if f.is_file()]
            vd = MEDIA / "video"
            if vd.is_dir():
                files += [dict(media_item(f), file=f"media/video/{f.name}", kind="video", duration_s=212.0) for f in sorted(vd.iterdir()) if f.is_file()]
            return self.send_json({"ok": True, "media": files})
        if p == "/api/playlists":
            summ = []
            for k, v in sorted(PLAYLISTS.items()):
                durs = [i.get("duration_s") for i in v["items"]]
                summ.append({"name": k, "count": len(v["items"]), "duration_s": round(sum(d for d in durs if d), 1) if any(durs) else None,
                             "missing": sum(1 for i in v["items"] if not (MEDIA / Path(i["file"]).name).exists())})
            return self.send_json({"ok": True, "playlists": summ})
        if p.startswith("/api/playlists/"):
            parts = p.split("/")
            name = parts[3]
            if name not in PLAYLISTS:
                return self.send_json({"ok": False, "error": "Playlist nicht gefunden"}, 404)
            if len(parts) > 4 and parts[4] == "export":
                body = to_m3u(PLAYLISTS[name]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpegurl; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}.m3u8"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return self.send_json(dict(pl_out(PLAYLISTS[name]), ok=True))
        if p == "/api/presets":
            return self.send_json([{"name": k, "modified": time.time()} for k in sorted(PRESETS)])
        if p == "/api/devices":
            return self.send_json(dict(DEVICES, ok=True))
        if p.startswith("/api/youtube/"):
            j = JOBS.get(p.split("/", 3)[3])
            return self.send_json(j if j else {"ok": False, "error": "Job unbekannt"}, 200 if j else 404)
        if p.startswith("/media/video/"):
            f = MEDIA / "video" / Path(p).name
            if f.exists():
                return self.file(f)
            return self.send_json({"error": "not found"}, 404)
        if p.startswith("/api/presets/"):
            name = p.split("/", 3)[3]
            if name not in PRESETS:
                return self.send_json({"error": "Preset nicht gefunden"}, 404)
            return self.send_json(PRESETS[name])
        if p.startswith("/media/"):
            f = MEDIA / Path(p).name
            if f.exists():
                return self.file(f)
            return self.send_json({"error": "not found"}, 404)
        if p == "/":
            p = "/index.html"
        f = (WEB / p.lstrip("/")).resolve()
        if WEB.resolve() in f.parents and f.is_file():
            return self.file(f)
        self.send_json({"error": "not found"}, 404)

    def file(self, f: Path):
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".json": "application/json",
                 ".md": "text/markdown; charset=utf-8"}
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(f.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            while time.time() >= OUTAGE_UNTIL:
                s = self.state()
                s["log"] = s["log"][-50:]
                self.wfile.write(f"event: state\ndata: {json.dumps(s, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        self.close_connection = True

    # -------------------------------------------------- PUT/POST/DELETE
    def do_PUT(self):
        p = unquote(urlparse(self.path).path)
        if p == "/api/config":
            cfg = self.jbody()
            errs = validate(cfg)
            if errs:
                return self.send_json({"errors": errs}, 400)
            with LOCK:
                old = ST["config"]
                restart = ST["tx"]["state"] == "running" and structure(old) != structure(cfg)
                # Sicherheit: Dämpfung/enabled nicht über config ändern
                cfg["rf"]["atten_db"] = old["rf"]["atten_db"] if cfg["rf"].get("atten_db") is None else cfg["rf"]["atten_db"]
                ST["config"] = cfg
                apply_rf_to_pluto()
            log(f"Konfiguration „{cfg.get('name')}“ gesetzt" + (" – Neustart nötig" if restart else ""))
            return self.send_json({"ok": True, "restart_required": restart})
        if p.startswith("/api/presets/"):
            name = p.split("/", 3)[3]
            PRESETS[name] = self.jbody()
            log(f"Preset „{name}“ gespeichert")
            return self.send_json({"ok": True})
        if p.startswith("/api/playlists/"):
            name = p.split("/", 3)[3]
            d = self.jbody() or {}
            items = [playlist_item(it["file"] if isinstance(it, dict) else it, (it.get("title", "") if isinstance(it, dict) else ""),
                                   (it.get("artist", "") if isinstance(it, dict) else ""), (it.get("duration_s") if isinstance(it, dict) else None))
                     for it in d.get("items", [])]
            with LOCK:
                PLAYLISTS[name] = {"name": name, "items": items}
                for st in NOW.values():
                    st["index"] = 0 if not items else st["index"] % len(items)
            log(f"Playlist „{name}“ gespeichert ({len(items)} Titel)")
            return self.send_json(dict(pl_out(PLAYLISTS[name]), ok=True))
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        p = unquote(urlparse(self.path).path)
        if p.startswith("/api/presets/"):
            name = p.split("/", 3)[3]
            PRESETS.pop(name, None)
            log(f"Preset „{name}“ gelöscht")
            return self.send_json({"ok": True})
        if p.startswith("/api/playlists/"):
            name = p.split("/", 3)[3]
            if PLAYLISTS.pop(name, None) is None:
                return self.send_json({"ok": False, "error": "Playlist nicht gefunden"}, 404)
            log(f"Playlist „{name}“ gelöscht")
            return self.send_json({"ok": True})
        if p.startswith("/api/media/"):
            f = MEDIA / Path(p.split("/", 3)[3]).name
            if not f.exists():
                return self.send_json({"ok": False, "error": "Datei nicht gefunden"}, 404)
            f.unlink()
            log(f"Medium {f.name} gelöscht")
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p == "/api/onboarding":
            d = self.jbody()
            mode = d.get("mode")
            if mode not in ONB_ACCEPT:
                return self.send_json({"ok": False, "errors": ["mode: lab oder ham"]}, 400)
            miss = [k for k in ONB_ACCEPT[mode] if k not in (d.get("accept") or [])]
            cs = str(d.get("callsign") or "").upper()
            if miss or (mode == "ham" and not cs):
                return self.send_json({"ok": False, "errors": [f"Bestätigung fehlt: {k}" for k in miss] + (["callsign"] if mode == "ham" and not cs else [])}, 400)
            with LOCK:
                ST["onboarding"] = {"done": True, "version": 1, "required_version": 1, "mode": mode, "callsign": cs if mode == "ham" else "",
                                    "accept": sorted(d.get("accept") or []), "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}
            log(f"Einrichtung abgeschlossen: {mode} {cs}".strip())
            return self.send_json(dict(ST["onboarding"], ok=True))
        if p == "/api/firmware":
            d = self.jbody()
            act = d.get("action")
            if act not in ("ram_test", "flash", "backup"):
                return self.send_json({"ok": False, "errors": ["unbekannte Aktion"]}, 400)
            if act == "flash" and not FWM["tested"]:
                return self.send_json({"ok": False, "errors": ["Erst den RAM-Test bestehen"]}, 409)
            if act == "flash" and d.get("confirm") is not True:
                return self.send_json({"ok": False, "errors": ["Flashen muss bestätigt werden"]}, 400)
            if FWM["job"]["running"]:
                return self.send_json({"ok": False, "errors": ["läuft"]}, 409)
            threading.Thread(target=fw_job, args=(act,), daemon=True).start()
            time.sleep(0.05)
            return self.send_json({"ok": True})
        if p == "/api/setup/install_all":
            d = self.jbody()
            if d.get("accepted") is not True:
                return self.send_json({"ok": False, "errors": ["Lizenzen müssen bestätigt werden"]}, 400)
            if SETUP["job"]["running"] or SETUP.get("all_running"):
                return self.send_json({"ok": False, "errors": ["Installation läuft bereits"]}, 409)
            SETUP["via"] = "dashboard"
            threading.Thread(target=setup_all, args=("dashboard",), daemon=True).start()
            time.sleep(0.05)
            return self.send_json({"ok": True})
        if p == "/api/setup/install":
            d = self.jbody()
            k = d.get("component")
            if k not in SETUP["installed"]:
                return self.send_json({"ok": False, "errors": ["unbekannte Komponente"]}, 400)
            if d.get("accepted") is not True:
                return self.send_json({"ok": False, "errors": ["Lizenz/Quelle der Komponente muss bestätigt werden"]}, 400)
            if SETUP["job"]["running"]:
                return self.send_json({"ok": False, "errors": ["läuft noch"]}, 409)
            threading.Thread(target=setup_job, args=(k,), daemon=True).start()
            time.sleep(0.05)
            return self.send_json({"ok": True})
        if p == "/api/onboarding/reset":
            self.body()
            with LOCK:
                ST["onboarding"] = {"done": False, "version": 0, "required_version": 1}
                ST["config"]["rf"]["enabled"] = False
                apply_rf_to_pluto()
            return self.send_json(dict(ST["onboarding"], ok=True))
        if p == "/api/tx/start":
            self.body()
            with LOCK:
                if ST["tx"]["state"] in ("running", "starting"):
                    return self.send_json({"ok": True, "already": True})
                ST["tx"].update({"state": "starting", "since": time.time(), "session": f"s{int(time.time())}", "underruns": 0,
                                 "samples_sent": 0})
            log("Starte Kette: ffmpeg → odr-audioenc → odr-dabmux → odr-dabmod", src="tx")
            return self.send_json({"ok": True})
        if p == "/api/tx/stop":
            self.body()
            SWEEP_STOP.set()
            with LOCK:
                ST["tx"].update({"state": "idle", "since": None, "session": None, "rate_ms": None, "buffer_ms": None})
                ST["config"]["rf"]["enabled"] = False
                apply_rf_to_pluto()
            log("Kette gestoppt, Pluto −89,75 dB", "warn", "tx")
            return self.send_json({"ok": True})
        if p == "/api/rf":
            d = self.jbody()
            if d.get("enabled") is True and not ST["onboarding"].get("done"):
                return self.send_json({"ok": False, "errors": ["Einrichtung nicht abgeschlossen – erst Firmware-Prüfung und Rechtliches bestätigen"]}, 403)
            with LOCK:
                rf = ST["config"]["rf"]
                if "atten_db" in d:
                    a = float(d["atten_db"])
                    if not (0 <= a <= ATT_MAX):
                        return self.send_json({"error": "Dämpfung außerhalb 0–89,75 dB"}, 400)
                    rf["atten_db"] = a
                if "channel" in d:
                    if d["channel"] not in CHANNELS and d.get("freq_hz") is None:
                        return self.send_json({"error": "Unbekannter Kanal (freq_hz nötig)"}, 400)
                    rf["channel"] = d["channel"]
                    rf["freq_hz"] = int(d.get("freq_hz") or round(CHANNELS[d["channel"]] * 1e6))
                elif "freq_hz" in d:
                    rf["freq_hz"] = int(d["freq_hz"])
                for k in ("offset_hz", "enabled"):
                    if k in d:
                        rf[k] = d[k]
                apply_rf_to_pluto()
                newrf = {k: rf[k] for k in ("channel", "freq_hz", "offset_hz", "atten_db", "enabled")}
            log("HF: " + ", ".join(f"{k}={v}" for k, v in d.items()), src="rf")
            return self.send_json({"ok": True, "rf": newrf, "applied": {"atten_db": ST["pluto"]["atten_db"]}})
        if p == "/api/live":
            d = self.jbody()
            with LOCK:
                if "announcement" in d:
                    a = d["announcement"]
                    for c in ST["config"]["clusters"]:
                        if c["id"] == a["cluster"]:
                            c["active"] = bool(a["active"])
                if "dls" in d:
                    for s in ST["config"]["services"]:
                        if s["key"] == d["dls"]["service"]:
                            s["pad"]["dls"]["text"] = d["dls"]["text"]
                if "rds" in d:
                    rr = ST["config"]["fm"]["rds"]
                    for k, v in d["rds"].items():
                        if k == "rt_auto":
                            rr["rt_from_tags"] = bool(v)
                        elif k == "eon_ta":
                            for e in rr["eon"]:
                                if str(e.get("pi", "")).lower() == str(v.get("pi", "")).lower():
                                    e["ta"] = bool(v.get("active"))
                        elif k in rr:
                            rr[k] = v
                if "fm" in d:
                    for k, v in d["fm"].items():
                        if k == "rds_level_khz":
                            ST["config"]["fm"]["rds"]["level_khz"] = v
                        elif k in ("audio_gain_db", "deviation_khz", "pilot_khz", "mpx_power_dbr"):
                            ST["config"]["fm"][k] = v
                if "pal" in d:
                    for k in ("text", "text2"):
                        if k in d["pal"]:
                            ST["config"]["pal"]["image"][k] = d["pal"][k]
                if "overlay" in d:
                    for pr in (ST["config"].get("dvb") or {}).get("programs") or []:
                        if pr.get("key") == d["overlay"].get("service") and (pr.get("overlay") or {}).get("text"):
                            pr["overlay"]["text"]["text"] = d["overlay"].get("text", "")
                if "second_carrier" in d:
                    for k, v in d["second_carrier"].items():
                        if k in ("level_db", "enable"):
                            ST["config"]["fm"]["second_carrier"][k] = v
                if "playlist" in d:
                    if ST["tx"]["state"] != "running":
                        return self.send_json({"ok": False, "error": "keine Live-Sitzung (TX aus)"}, 409)
                    code, res = playlist_ctl(d["playlist"])
                    log("Live: " + json.dumps(d, ensure_ascii=False), src="ctl")
                    return self.send_json(res, code)
            log("Live: " + json.dumps(d, ensure_ascii=False), src="ctl")
            return self.send_json({"ok": True})
        if p.startswith("/api/playlists/") and p.endswith("/import"):
            name = p.split("/")[3]
            text = self.body().decode("utf-8", "replace")
            items = parse_m3u(text)
            if not items:
                return self.send_json({"ok": False, "error": "Keine Titel in der Datei (M3U/M3U8/PLS erwartet)"}, 400)
            missing = [it["file"] for it in items if not (MEDIA / Path(it["file"]).name).exists()]
            with LOCK:
                PLAYLISTS[name] = {"name": name, "items": items}
            log(f"Playlist „{name}“ importiert: {len(items)} Titel" + (f", {len(missing)} Dateien fehlen" if missing else ""))
            return self.send_json(dict(pl_out(PLAYLISTS[name]), ok=True, warnings=[f"{m}: Datei fehlt" for m in missing]))
        if p == "/api/upload":
            q = parse_qs(u.query)
            kind = q.get("kind", ["audio"])[0]
            data = self.body()
            ctype = self.headers.get("Content-Type", "")
            files = parse_multipart(ctype, data) if ctype.lower().startswith("multipart/form-data") else                 [(Path(q.get("name", ["upload.bin"])[0]).name, data)]
            out, errs = [], []
            for name, blob in files:
                name = Path(name.replace("\\", "/")).name
                if kind == "video":
                    (MEDIA / "video").mkdir(exist_ok=True)
                    (MEDIA / "video" / name).write_bytes(blob)
                    out.append({"file": f"media/video/{name}", "bytes": len(blob), "kind": kind})
                    continue
                if kind == "audio" and not name.lower().endswith(AUDIO_EXT):
                    errs.append(f"{name}: kein Audioformat")
                    continue
                (MEDIA / name).write_bytes(blob)
                out.append({"file": f"media/{name}", "bytes": len(blob), "kind": kind})
                log(f"Upload {kind}: media/{name} ({len(blob)} Byte)")
            res = {"ok": bool(out), "files": out, "errors": errs}
            if out:
                res.update(out[0])
            plname = q.get("playlist", [None])[0]
            if plname and out and kind == "audio":
                with LOCK:
                    pl = PLAYLISTS.setdefault(plname, {"name": plname, "items": []})
                    pl["items"] += [playlist_item(o["file"]) for o in out]
                    res["playlist"] = pl_out(pl)
            return self.send_json(res, 200 if out else 400)
        if p in ("/api/check", "/api/monitor"):
            d = self.jbody()
            kind = "check" if p.endswith("check") else "monitor"
            log(f"{'Offline-Prüfung' if kind == 'check' else 'Kontrollempfang'} gestartet ({d})", src=kind)
            time.sleep(0.5 if FAST else 2.0)
            with LOCK:
                if kind == "monitor":
                    rep = build_monitor(d)
                else:
                    rep = build_any_report(kind)
                    rep["t"] = time.time()
                ST[kind] = rep
            log(f"Bericht fertig: SNR {rep.get('snr_db')} dB", src=kind)
            return self.send_json(rep)
        if p == "/api/sweep":
            d = self.jbody()
            with LOCK:
                if ST["tx"]["state"] != "running":
                    return self.send_json({"error": "Kette läuft nicht"}, 409)
                ST["sweep"] = None
            threading.Thread(target=sweep_worker, args=(d,), daemon=True).start()
            return self.send_json({"ok": True})
        if p == "/api/youtube":
            d = self.jbody()
            url = str(d.get("url", ""))
            if not url.startswith("http"):
                return self.send_json({"ok": False, "error": "URL fehlt"}, 400)
            job_id = f"j{int(time.time() * 1000) % 10000000}"
            with LOCK:
                JOBS[job_id] = {"id": job_id, "url": url, "status": "queued", "percent": 0.0, "file": None, "title": None, "t": time.time()}
            threading.Thread(target=youtube_worker, args=(job_id, url), daemon=True).start()
            log(f"YouTube-Download gestartet: {url}", src="yt-dlp")
            return self.send_json({"ok": True, "job": job_id})
        if p == "/api/_mock/outage":
            global OUTAGE_UNTIL
            OUTAGE_UNTIL = time.time() + float(self.jbody().get("seconds", 3))
            return self.send_json({"ok": True})
        if p == "/api/sweep/stop":
            self.body()
            SWEEP_STOP.set()
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)


def main():
    global FAST
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--fast", action="store_true", help="kurze Wartezeiten (Tests)")
    ap.add_argument("--onboarding", action="store_true", help="ohne abgeschlossene Einrichtung starten")
    ap.add_argument("--no-components", action="store_true", help="ohne Pflichtkomponenten starten (Linux-Umgebung vorhanden)")
    ap.add_argument("--installer-setup", action="store_true", help="wie vom Installer gestartet: Komponenten laufen sofort an")
    a = ap.parse_args()
    FAST = a.fast
    if a.onboarding:
        ST["onboarding"] = {"done": False, "version": 0, "required_version": 1}
    if a.no_components or a.installer_setup:
        for k, o, _ in SETUP_META:
            if k != "wsl_distro":
                SETUP["installed"][k] = False
    if a.installer_setup:
        SETUP["via"] = "installer"
        threading.Thread(target=setup_all, args=("installer",), daemon=True).start()
    make_media()
    init_playlists()
    log("Mock-Server gestartet (keine Hardware)")
    threading.Thread(target=ticker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    print(f"DAB-Lab-Mock auf http://127.0.0.1:{a.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
