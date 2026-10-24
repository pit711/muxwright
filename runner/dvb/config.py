# -*- coding: utf-8 -*-
"""Konfigurationsschema fuer mode = "dvbt" | "dvbc": Normalisieren, Pruefen, abgeleitete Werte (_rate, PIDs, Bitraten)."""
import copy
import os
import re
from fractions import Fraction

from . import tables as T

VIDEO_SIZES = {"uhd": (3840, 2160), "2160p": (3840, 2160), "4k": (3840, 2160), "sd": (720, 576), "hd": (1920, 1080), "1080p": (1920, 1080), "720p": (1280, 720), "576p": (720, 576),
               "480p": (720, 480), "qsd": (352, 288)}
VIDEO_CODECS = ("mpeg2", "h264", "hevc")
ENCODERS = ("cpu", "nvenc")
AUDIO_CODECS = ("mp2", "aac", "ac3")
SOURCE_KINDS = ("testpattern", "file", "youtube", "slideshow", "playlist", "dshow", "gdigrab", "ddagrab", "ts")
NVENC_ONLY_SOURCES = ("dshow", "gdigrab", "ddagrab")
PATTERNS = ("testsrc2", "testsrc", "smptehdbars", "smptebars", "colorbars", "rgbtestsrc", "yuvtestsrc", "pal75bars",
            "pal100bars", "mandelbrot", "life")
AUDIO_GEN = ("sine", "speech", "sine+speech", "silence", "noise", "file")
OUTPUT_KINDS = ("stdout", "udp", "modulator", "file")
STANDARDS = ("dvbt", "dvbc", "dvbs", "atsc", "isdbt", "dtmb")
UNTESTED = ("atsc", "isdbt", "dtmb")      # dazu DVB-C Annex B/C (J.83 B/C)
ATSC_SAMPLE_RATE = 10762238               # atsc_mod: 1 Sample/Symbol (--print-rates), Pilot bei −2,69 MHz um den LO
SERVICE_TYPES = {"tv": 0x01, "radio": 0x02, "hdtv": 0x19, "aac_radio": 0x0A}


def _int(v, default=None):
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _norm_const(v, allowed):
    s = str(v or "").upper().replace("QAM-", "").replace("QAM", "").strip()
    if s in ("", "0"):
        return None
    if s == "QPSK" or s == "4":
        return "QPSK"
    c = s.rstrip("-") + "-QAM"
    return c if c in allowed else None


def _norm_frac(v, allowed):
    s = str(v or "").strip().replace(" ", "")
    return s if s in allowed else None


RES_ALIASES = {"hd720": "720p", "hd1080": "hd", "1080i": "hd", "576i": "sd"}
PATTERN_ALIASES = {"pm5544": "pal75bars", "color": "rgbtestsrc", "bars": "smptebars"}


def is_dashboard_format(raw):
    return bool((raw.get("dvb") or {}).get("programs")) or "fft" in (raw.get("dvbt") or {})


def from_dashboard(raw):
    """Dashboard-Format (web/js/standards.js: dvb.programs[], dvb.si, dvbt.fft) -> Runner-Format (services[], ts).
    Felder, die schon im Runner-Format stehen, haben Vorrang."""
    c = copy.deepcopy(raw)
    dv = c.get("dvb") or {}
    if c.get("dvbt") and "fft" in c["dvbt"] and "mode" not in c["dvbt"]:
        c["dvbt"]["mode"] = c["dvbt"]["fft"]
    si = dv.get("si") or {}
    if si and "ts" not in c:
        c["ts"] = {"network_id": si.get("network_id"), "network_name": si.get("network_name"),
                   "original_network_id": si.get("onid"), "transport_stream_id": si.get("tsid"),
                   "lcn": si.get("lcn", True), "eit": si.get("eit", True), "tdt": si.get("tdt", True)}
    # Dashboard-Konfigurationen tragen immer auch die DAB+-Dienste (services ohne DVB-"type"); die zählen hier nicht
    dvb_services = [s for s in (c.get("services") or []) if isinstance(s, dict) and s.get("type") in ("tv", "radio")]
    if dv.get("programs") and not dvb_services:
        svcs = []
        for p in dv["programs"]:
            v = dict(p.get("video") or {})
            vs = dict(v.pop("source", {}) or {})
            a = dict(p.get("audio") or {})
            asrc = dict(a.pop("source", {}) or {})
            src = {"kind": vs.get("kind") or "testpattern", "loop": vs.get("loop", True)}
            if p.get("type") == "radio":
                src["kind"] = "testpattern"
            if src["kind"] == "testpattern":
                pat = vs.get("pattern") or "smptebars"
                src["pattern"] = PATTERN_ALIASES.get(pat, pat)
            elif src["kind"] == "file":
                src["file"] = vs.get("file")
            elif src["kind"] == "youtube":
                if vs.get("file"):
                    src["kind"], src["file"] = "file", vs["file"]
                else:
                    src["url"] = vs.get("url")
            elif src["kind"] == "playlist":
                src["playlist"] = vs.get("playlist")
            elif src["kind"] == "slideshow":
                src["images"] = vs.get("images") or []
                src["interval_s"] = vs.get("interval_s", 5)
            # Tonquelle (Dashboard: audioSrc() wie bei DAB: tone/silence/noise/pinknoise/file/...)
            ak = asrc.get("kind") or ("source" if src["kind"] in ("file", "youtube", "playlist") else "tone")
            amap = {"tone": "sine", "sweep": "sine", "lr_test": "sine", "polarity": "sine", "pinknoise": "noise",
                    "video": "source"}
            src["audio"] = {"kind": amap.get(ak, ak), "freq_hz": asrc.get("freq_hz", 1000),
                            "level_dbfs": asrc.get("level_dbfs", -18), "file": asrc.get("file")}
            if asrc.get("text"):
                src["audio"]["text"] = asrc["text"]
            if src["audio"]["kind"] == "file" and not src["audio"]["file"]:
                src["audio"]["kind"] = "sine"
            if src["audio"]["kind"] == "playlist":
                src["audio"]["kind"] = "sine"
            e = dict(p.get("eit") or {})
            if "description" in e and "text" not in e:
                e["text"] = e.pop("description")
            res = v.get("resolution", "sd")
            svcs.append({"key": p.get("key"), "service_id": p.get("sid"), "name": p.get("name"),
                         "provider": p.get("provider"), "lcn": p.get("lcn"), "type": p.get("type", "tv"),
                         "visible": p.get("visible", True), "oneseg": bool(p.get("oneseg")),
                         "video": {"codec": v.get("codec", "mpeg2"), "size": RES_ALIASES.get(res, res),
                                   "bitrate_kbps": v.get("bitrate_kbps", "auto"), "fps": v.get("fps"),
                                   "aspect": v.get("aspect", "16:9"), "interlaced": v.get("interlaced", False),
                                   # Dashboard: encoder "cpu"/"nvenc", hdr "sdr"/"hdr10"/"hlg" (hier String, im Runner {"mode": …})
                                   "encoder": v.get("encoder", "cpu"),
                                   "hdr": ({"mode": "off"} if v.get("hdr") in (None, "", "sdr", "off")
                                           else {"mode": v["hdr"], **({"master_display": v["master_display"]} if v.get("master_display") else {}),
                                                 **({"max_cll": v["max_cll"]} if v.get("max_cll") else {}), **({"max_fall": v["max_fall"]} if v.get("max_fall") else {})}
                                           if isinstance(v.get("hdr"), str) else (v.get("hdr") or {"mode": "off"})),
                                   **({"preset": v["preset"]} if v.get("preset") else {}),
                                   **({"bit_depth": v["bit_depth"]} if v.get("bit_depth") else {})},
                         "audio": {"codec": a.get("codec", "mp2"), "bitrate_kbps": a.get("bitrate_kbps"),
                                   "channels": a.get("channels", 2), "samplerate": a.get("samplerate", 48000)},
                         "source": src, "eit": e})
        c["services"] = svcs
    if "output" not in c:
        path = (c.get("rf") or {}).get("path")
        if path == "pluto":
            c["output"] = {"kind": "udp", "host": (c.get("rf") or {}).get("pluto_host", "192.168.2.1"), "port": 1234}
        else:
            c["output"] = {"kind": "modulator"}
    return c


def normalize(raw):
    """-> (cfg, errors, warnings). cfg enthaelt abgeleitete Felder mit fuehrendem Unterstrich.
    Akzeptiert das Runner-Format (services[], ts) und das Dashboard-Format (dvb.programs[], dvb.si)."""
    E, W = [], []
    cfg = from_dashboard(raw) if is_dashboard_format(raw) else copy.deepcopy(raw)
    std = str(cfg.get("mode", "")).lower()
    if std not in STANDARDS:
        E.append("mode muss %s sein" % ", ".join("'%s'" % s for s in STANDARDS))
        return cfg, E, W
    cfg["mode"] = std
    cfg.setdefault("name", {"dvbt": "DVB-T", "dvbc": "DVB-C", "dvbs": "DVB-S", "atsc": "ATSC", "isdbt": "ISDB-T", "dtmb": "DTMB"}[std])
    if std in UNTESTED:
        W.append("%s ist ungetestet (untested)" % cfg["name"])

    # -- Modulation / Rate --
    if std == "dvbt":
        d = cfg.setdefault("dvbt", {})
        d["mode"] = str(d.get("mode", "8k")).lower()
        d["constellation"] = _norm_const(d.get("constellation", "64-QAM"), T.DVBT_BITS)
        d["code_rate"] = _norm_frac(d.get("code_rate", "2/3"), T.DVBT_CODE_RATES)
        d["guard"] = _norm_frac(d.get("guard", "1/32"), T.DVBT_GUARDS)
        d["bandwidth_mhz"] = _int(d.get("bandwidth_mhz"), 0)
        d["cell_id"] = _int(d.get("cell_id"), 0)
        d.pop("fft", None)
        if d["mode"] not in T.DVBT_MODES:
            E.append("dvbt.mode: 2k oder 8k")
        if not d["constellation"]:
            E.append("dvbt.constellation: QPSK, 16-QAM, 64-QAM")
        if not d["code_rate"]:
            E.append("dvbt.code_rate: 1/2, 2/3, 3/4, 5/6, 7/8")
        if not d["guard"]:
            E.append("dvbt.guard: 1/4, 1/8, 1/16, 1/32")
    elif std == "dvbs":
        d = cfg.setdefault("dvbs", {})
        d["symbol_rate"] = _int(d.get("symbol_rate"), 22000000)
        d["use"] = "ham" if str(d.get("use", "sat")).lower() == "ham" else "sat"
        d["system"] = "dvbs2" if str(d.get("system", "dvbs")).lower().replace("-", "") == "dvbs2" else "dvbs"
        d["frequency_hz"] = _int(d.get("frequency_hz"), 11038000000)     # Downlink fuer die NIT (Ku-Band)
        d.setdefault("orbital_position", "19.2")
        d["west_east"] = str(d.get("west_east", "east")).lower()
        d["polarization"] = str(d.get("polarization", "vertical")).lower()
        ro = str(d.get("roll_off", d.get("rolloff", "0.35")))
        d["roll_off"] = {"0.2": "0.20", "0.25": "0.25", "0.35": "0.35"}.get(ro.rstrip("0") if "." in ro else ro, ro)
        d.pop("rolloff", None)
        if d["system"] == "dvbs2":
            d["modulation"] = str(d.get("modulation", "QPSK")).upper()
            d["pilots"] = bool(d.get("pilots", True))
            cr = str(d.get("code_rate", "3/4")).strip()
            d["code_rate"] = cr if cr in T.DVBS2_MOD_RATES.get(d["modulation"], []) else None
            if d["modulation"] not in T.DVBS2_BITS:
                E.append("dvbs.modulation: DVB-S2 QPSK oder 8PSK")
            elif not d["code_rate"]:
                E.append("dvbs.code_rate: DVB-S2 %s: %s" % (d["modulation"], ", ".join(T.DVBS2_MOD_RATES[d["modulation"]])))
            if d["roll_off"] not in T.DVBS2_ROLLOFFS:
                E.append("dvbs.roll_off: 0.35, 0.25 oder 0.20")
        else:
            d["modulation"] = "QPSK"
            d["code_rate"] = _norm_frac(d.get("code_rate", "3/4"), T.DVBS_CODE_RATES)
            if d["roll_off"] != "0.35":
                E.append("dvbs.roll_off: DVB-S nur 0.35")
            if not d["code_rate"]:
                E.append("dvbs.code_rate: 1/2, 2/3, 3/4, 5/6, 7/8")
        if d["use"] == "ham":
            d["callsign"] = str(d.get("callsign") or "").strip().upper()
            if not re.match(T.CALLSIGN_RE, d["callsign"]):
                E.append("dvbs.callsign: Rufzeichen fehlt oder ungültig (z. B. DL1ABC) – Pflicht im Amateurfunk")
            if not (T.HAM_SR_MIN <= d["symbol_rate"] <= T.HAM_SR_MAX):
                E.append("dvbs.symbol_rate: DATV %d kS/s … %.1f MS/s" % (T.HAM_SR_MIN // 1000, T.HAM_SR_MAX / 1e6))
            d["sps"] = T.ham_sps(d["symbol_rate"])
        elif not (1000000 <= d["symbol_rate"] <= 45000000):
            E.append("dvbs.symbol_rate: 1..45 MBd")
        if d["polarization"] not in ("horizontal", "vertical", "left", "right"):
            E.append("dvbs.polarization: horizontal, vertical, left, right")
    elif std == "atsc":
        d = cfg.setdefault("atsc", {})
        d["modulation"] = "8-VSB"
        d["symbol_rate"] = float(T.ATSC_SYMBOL_RATE)
    elif std == "dtmb":
        d = cfg.setdefault("dtmb", {})
        d["carriers"] = _int(d.get("carriers"), 3780)
        d["constellation"] = str(d.get("constellation", "64-QAM")).upper().replace("QAM-", "QAM").replace("4QAM", "4-QAM").replace("16QAM", "16-QAM") \
            .replace("32QAM", "32-QAM").replace("64QAM", "64-QAM")
        d["code_rate"] = str(d.get("code_rate", "0.6"))
        d["header"] = _int(d.get("header"), 945)
        d["interleaver"] = _int(d.get("interleaver"), 720)
        if d["carriers"] not in (1, 3780):
            E.append("dtmb.carriers: 3780 (Mehrtraeger) oder 1 (Eintraeger)")
        if d["constellation"] not in T.DTMB_BITS:
            E.append("dtmb.constellation: %s" % ", ".join(T.DTMB_BITS))
        elif d["code_rate"] not in T.DTMB_ALLOWED[d["constellation"]]:
            E.append("dtmb.code_rate: %s erlaubt %s" % (d["constellation"], ", ".join(T.DTMB_ALLOWED[d["constellation"]])))
        if d["header"] not in T.DTMB_HEADERS:
            E.append("dtmb.header: PN 420, 595 oder 945")
        if d["interleaver"] not in (240, 720):
            E.append("dtmb.interleaver: 240 oder 720")
    elif std == "isdbt":
        d = cfg.setdefault("isdbt", {})
        d["region"] = "jp" if str(d.get("region", "br")).lower() in ("jp", "japan") else "br"
        d["mode"] = _int(str(d.get("mode", 3)).lower().replace("mode", ""), 3)
        d["guard"] = _norm_frac(d.get("guard", "1/8"), T.DVBT_GUARDS)
        d["layers"] = _int(d.get("layers"), 1)
        d["area_code"] = _int(d.get("area_code"), 0)
        d["remote_key"] = _int(d.get("remote_key"), 1)
        if d["mode"] not in (1, 2, 3):
            E.append("isdbt.mode: 1, 2 oder 3")
        elif d["mode"] == 2 and (cfg.get("output") or {}).get("kind") not in ("file", "stdout"):
            E.append("isdbt.mode 2: auf dem Pluto nicht echtzeitfähig (4096-Punkt-IFFT) – Mode 1 oder 3 wählen")
        if not d["guard"]:
            E.append("isdbt.guard: 1/4, 1/8, 1/16, 1/32")
        if d["layers"] not in (1, 2):
            E.append("isdbt.layers: 1 (13 Segmente) oder 2 (A = 1seg, B = 12 Segmente)")
        if not 1 <= d["remote_key"] <= 12:
            E.append("isdbt.remote_key: 1..12 (virtuelle Kanalnummer)")
        for L, segs, dc, dr in (("a", 1 if d["layers"] == 2 else 13, "QPSK" if d["layers"] == 2 else "64-QAM", "2/3" if d["layers"] == 2 else "3/4"),
                                ("b", 12, "64-QAM", "3/4")):
            if L == "b" and d["layers"] == 1:
                d.pop("layer_b", None)
                continue
            ly = d.setdefault("layer_" + L, {})
            ly["segments"] = segs
            ly["constellation"] = _norm_const(ly.get("constellation", dc), T.ISDBT_BITS)
            ly["code_rate"] = _norm_frac(ly.get("code_rate", dr), T.DVBT_CODE_RATES)
            ly["ti"] = _int(ly.get("ti"), 2)
            if not ly["constellation"]:
                E.append("isdbt.layer_%s.constellation: QPSK, 16-QAM, 64-QAM" % L)
            if not ly["code_rate"]:
                E.append("isdbt.layer_%s.code_rate: 1/2, 2/3, 3/4, 5/6, 7/8" % L)
            if ly["ti"] not in T.ISDBT_TI:
                E.append("isdbt.layer_%s.ti: 0, 1, 2, 4" % L)
    else:
        d = cfg.setdefault("dvbc", {})
        an = str(d.get("annex", "a")).lower()
        d["annex"] = "b" if an in ("b", "j83b", "us") else "c" if an in ("c", "j83c", "jp") else "a"
        if d["annex"] == "c":
            d.setdefault("symbol_rate", T.J83C_SR)
        if d["annex"] == "b":                    # J.83 B: Symbolrate fest je QAM, RRC 0,18 (64) / 0,12 (256)
            d["constellation"] = _norm_const(d.get("constellation", "256-QAM"), T.DVBC_BITS)
            if d["constellation"] not in T.J83B:
                E.append("dvbc.constellation: J.83 Annex B nur 64-QAM oder 256-QAM")
                d["constellation"] = "256-QAM"
            d["symbol_rate"] = T.J83B[d["constellation"]][0]
            d["sps"] = _int(d.get("sps"), 2)
        else:
            d["constellation"] = _norm_const(d.get("constellation", "256-QAM"), T.DVBC_BITS)
            d["symbol_rate"] = _int(d.get("symbol_rate"), 6900000)
            d["sps"] = _int(d.get("sps"), 2)
        if d["sps"] not in (1, 2):
            E.append("dvbc.sps: 1 oder 2")
        if not d["constellation"]:
            E.append("dvbc.constellation: 16-QAM ... 256-QAM")
        if not (1000000 <= d["symbol_rate"] <= 7200000):
            E.append("dvbc.symbol_rate: 1..7,2 MBd")

    # -- HF / Kanal --
    rf = cfg.setdefault("rf", {})
    ch = rf.get("channel") or d.get("channel")
    tab = "j83b" if (std == "dvbc" and d.get("annex") == "b") else "isdbt_jp" if (std == "isdbt" and d.get("region") == "jp") else \
        "dtmb" if (std == "dvbc" and str(ch or "").upper().startswith("DS")) else std
    look = T.channel_lookup(tab, ch) if ch and str(ch).lower() != "custom" and std != "dvbs" else None
    if ch and str(ch).lower() != "custom" and not look and std != "dvbs":
        E.append("rf.channel '%s' nicht in der %s-Kanaltabelle" % (ch, {"dvbt": "DVB-T", "dvbc": "DVB-C", "atsc": "US-", "isdbt": "US-/Brasilien-",
                                                                         "j83b": "US-Kabel-", "dtmb": "China-", "isdbt_jp": "Japan-"}.get(tab, tab)))
    if look:
        rf["channel"] = str(ch).upper()
        rf["freq_hz"] = look[0]            # Kanal ist maßgeblich (feine Verschiebung über rf.offset_hz)
        if std == "dvbt" and not d["bandwidth_mhz"]:
            d["bandwidth_mhz"] = look[1]
    if std == "dvbt":
        if not d["bandwidth_mhz"]:
            d["bandwidth_mhz"] = 8
        if d["bandwidth_mhz"] not in T.DVBT_BANDWIDTHS:
            E.append("dvbt.bandwidth_mhz: 5, 6, 7 oder 8")
    rf["freq_hz"] = _int(rf.get("freq_hz"), 0)
    if std == "dvbs" and d.get("use") == "ham" and not E:
        ro = float(d["roll_off"])
        half = d["symbol_rate"] * (1 + ro) / 2
        band, why = T.ham_band_of(rf["freq_hz"], half, d.get("ham_band"))
        if not band:
            E.append("rf.freq_hz %.3f MHz ± %.0f kHz %s" % (rf["freq_hz"] / 1e6, half / 1e3, why))
        else:
            d["ham_band"] = band
            # NIT: bei QO-100 die Downlink-Frequenz (so stimmt der Empfänger ab), sonst die Sendefrequenz
            d["frequency_hz"] = int(rf["freq_hz"] + (T.QO100_DOWNLINK_OFFSET if band == "qo100" else 0))
            names = " ".join(str(s.get("name", "")) + " " + str(s.get("provider", "")) for s in cfg.get("services") or [])
            if d.get("callsign") and d["callsign"].split("/")[0] not in names.upper():
                E.append("services: Rufzeichen %s muss im Dienst- oder Anbieternamen stehen (Stationskennung)" % d["callsign"])
    if not rf["freq_hz"]:
        rf["freq_hz"] = {"dvbt": 474000000, "dvbc": 113000000, "dvbs": 1288000000, "atsc": 473000000,
                         "isdbt": 473142857, "dtmb": 474000000}[std]
        if std != "dvbs":
            W.append("rf.freq_hz fehlt, NIT bekommt %d Hz" % rf["freq_hz"])
    rf.setdefault("atten_db", 60)
    rf.setdefault("enabled", False)
    rf.setdefault("backoff_db", 12)
    if E:
        return cfg, E, W
    if std == "dvbt":
        rate = T.dvbt_useful_bitrate(d["constellation"], d["code_rate"], d["guard"], d["bandwidth_mhz"])
        rf["sample_rate"] = int(round(T.dvbt_sample_rate(d["bandwidth_mhz"])))
    elif std == "dvbs":
        rate = (T.dvbs2_useful_bitrate(d["modulation"], d["code_rate"], d["symbol_rate"], d["pilots"])
                if d["system"] == "dvbs2" else T.dvbs_useful_bitrate(d["code_rate"], d["symbol_rate"]))
        rf["sample_rate"] = d["symbol_rate"]          # Pluto-Modulator dvbs_mod/dvbs2_mod --sps 1 (+ FIR 2x)
        if d.get("use") == "ham":
            rf["sample_rate"] = d["sps"] * d["symbol_rate"]   # DATV: Modulator am PC, IQ per USB (RRC im Programm)
        elif (cfg.get("output") or {}).get("kind") == "modulator":
            rf["sample_rate"] = 2 * d["symbol_rate"]  # lokaler Modulator am PC: --sps 2 (RRC im Programm)
    elif std == "dtmb":
        rate = T.dtmb_useful_bitrate(d["constellation"], d["code_rate"], d["header"])
        rf["sample_rate"] = int(d.get("sample_rate") or 2 * T.DTMB_SR)
    elif std == "atsc":
        rate = T.atsc_useful_bitrate()
        rf["sample_rate"] = int(d.get("sample_rate") or ATSC_SAMPLE_RATE)
    elif std == "isdbt":
        d["_rate_a"] = T.isdbt_layer_bitrate(d["layer_a"]["segments"], d["layer_a"]["constellation"], d["layer_a"]["code_rate"], d["guard"])
        d["_rate_b"] = (T.isdbt_layer_bitrate(12, d["layer_b"]["constellation"], d["layer_b"]["code_rate"], d["guard"])
                        if d["layers"] == 2 else 0)
        rate = d["_rate_a"] + d["_rate_b"]
        rf["sample_rate"] = int(round(T.ISDBT_SAMPLE_RATE))
    elif d.get("annex") == "b":
        rate = T.j83b_useful_bitrate(d["constellation"])
        rf["sample_rate"] = d["sps"] * d["symbol_rate"]
    else:
        rate = T.dvbc_useful_bitrate(d["constellation"], d["symbol_rate"])
        rf["sample_rate"] = d["sps"] * d["symbol_rate"]
    cfg["_rate"] = rate                      # Fraction, exakt
    cfg["_rate_bps"] = float(rate)
    cfg["_muxrate"] = int(round(rate))       # ffmpeg -muxrate (ganzzahlig)
    cfg["_rate_str"] = T.bitrate_str(rate, 1)

    # -- TS / Netz --
    ts = cfg.setdefault("ts", {})
    ts["transport_stream_id"] = _int(ts.get("transport_stream_id"), 0x0101)
    ts["original_network_id"] = _int(ts.get("original_network_id"), 0xFF01)
    ts["network_id"] = _int(ts.get("network_id"), ts["original_network_id"])
    ts.setdefault("network_name", "DABLAB")
    ts["pmt_start_pid"] = _int(ts.get("pmt_start_pid"), 0x1000)
    ts["start_pid"] = _int(ts.get("start_pid"), 0x0100)
    ts.setdefault("lcn", True)
    ts.setdefault("eit", True)
    ts.setdefault("tdt", True)
    if std == "isdbt":
        # SBTVD: network_id = ONID = TSID (eine Kennung je Sender), keine EACEM-LCN (Kanalnummer = remote_control_key_id)
        ts["original_network_id"] = ts["network_id"] = ts["transport_stream_id"] = _int((raw.get("ts") or {}).get("network_id"), ts["transport_stream_id"]) & 0xFFFF
        ts["lcn"] = False
        ts.setdefault("country", "JPN" if d.get("region") == "jp" else "BRA")
    if std == "dtmb":
        ts["lcn"] = False
        ts.setdefault("country", "CHN")
    if T.is_psip(cfg):
        ts["lcn"] = ts["eit"] = ts["tdt"] = False            # PSIP: TVCT/CVCT + STT statt NIT/SDT/EIT/TDT
        ts.setdefault("major_channel", _int(re.sub(r"\D", "", str(rf.get("channel") or "")) or 0, 0) or 1)
    ts.setdefault("country", "DEU")
    ts.setdefault("utc_offset_min", "auto")
    small = cfg["_rate_bps"] < 2e6          # DATV mit kleinen Symbolraten: SI sparsam wiederholen
    ts.setdefault("eit_bitrate", 3000 if small else 30000)
    ts.setdefault("tdt_bitrate", 1000 if small else 2000)
    ts.setdefault("pcr_period_ms", 30)
    ts.setdefault("tables_version", 1)

    # -- Dienste --
    region60 = std in ("atsc", "isdbt") or T.is_psip(cfg) or (std == "dvbc" and cfg["dvbc"].get("annex") == "c")
    svcs = cfg.get("services") or []
    if not svcs:
        E.append("services: mindestens ein Dienst")
    keys, sids, lcns = set(), set(), set()
    for i, s in enumerate(svcs):
        s.setdefault("key", "svc%d" % (i + 1))
        if not re.match(r"^[a-z0-9_]+$", str(s["key"])):
            E.append("services[%d].key: nur [a-z0-9_]" % i)
        if s["key"] in keys:
            E.append("services[%d].key doppelt: %s" % (i, s["key"]))
        keys.add(s["key"])
        s["service_id"] = _int(s.get("service_id"), i + 1)
        if not (1 <= s["service_id"] <= 0xFFFF):
            E.append("services[%d].service_id: 1..65535" % i)
        if s["service_id"] in sids:
            E.append("services[%d].service_id doppelt" % i)
        sids.add(s["service_id"])
        s.setdefault("name", "LAB %s %d" % ("TV", i + 1))
        s.setdefault("provider", "DABLAB")
        s["type"] = str(s.get("type", "tv")).lower()
        if s["type"] not in ("tv", "radio"):
            E.append("services[%d].type: tv oder radio" % i)
        s["lcn"] = _int(s.get("lcn"), i + 1)
        if s["lcn"] in lcns:
            W.append("services[%d].lcn doppelt" % i)
        lcns.add(s["lcn"])
        s.setdefault("visible", True)
        # Video
        v = s.setdefault("video", {})
        if s["type"] == "tv":
            v["codec"] = str(v.get("codec", "mpeg2")).lower().replace("mpeg-2", "mpeg2").replace("h.264", "h264") \
                .replace("avc", "h264").replace("h.265", "hevc").replace("h265", "hevc")
            if v["codec"] not in VIDEO_CODECS:
                E.append("services[%d].video.codec: mpeg2, h264 oder hevc" % i)
            v["encoder"] = str(v.get("encoder", "cpu")).lower()
            if v["encoder"] not in ENCODERS:
                E.append("services[%d].video.encoder: cpu oder nvenc" % i)
            if v["encoder"] == "nvenc" and v["codec"] == "mpeg2":
                E.append("services[%d]: NVENC kann kein MPEG-2 (h264 oder hevc)" % i)
            hd = v.setdefault("hdr", {"mode": "off"})
            if isinstance(hd, str):
                hd = v["hdr"] = {"mode": hd}
            hd["mode"] = str(hd.get("mode", "off")).lower()
            if hd["mode"] not in ("off", "hdr10", "hlg"):
                E.append("services[%d].video.hdr.mode: off, hdr10, hlg" % i)
            if hd["mode"] != "off" and v["codec"] != "hevc":
                E.append("services[%d]: HDR nur mit HEVC (Main10)" % i)
            if hd["mode"] != "off" and v["encoder"] != "nvenc":
                E.append("services[%d]: HDR ist nur fuer video.encoder = nvenc umgesetzt" % i)
            if str(v.get("size", "sd")).lower() in ("uhd", "2160p", "4k") and v["encoder"] == "cpu" and (os.cpu_count() or 0) < 16:
                E.append("services[%d]: UHD mit CPU-Encoder nicht in Echtzeit (%d Threads) - video.encoder nvenc oder Aufloesung hd"
                         % (i, os.cpu_count() or 0))
            size = str(v.get("size", "sd")).lower()
            if size in ("sd", "576p") and region60:
                v["_wh"] = (720, 480)
            elif size in VIDEO_SIZES:
                v["_wh"] = VIDEO_SIZES[size]
            elif re.match(r"^\d+x\d+$", size):
                v["_wh"] = tuple(int(x) for x in size.split("x"))
            else:
                E.append("services[%d].video.size: sd, hd, 720p, uhd oder WxH" % i)
                v["_wh"] = (720, 576)
            v["size"] = size
            v["fps"] = v.get("fps") or (29.97 if region60 else 25)
            if region60 and float(v["fps"]) == 25:
                W.append("services[%d]: 25 fps in einer 60-Hz-Norm – Empfaenger erwarten 29,97/59,94 fps" % i)
            v.setdefault("aspect", "16:9")
            v.setdefault("gop", 12 if v["codec"] == "mpeg2" else 25)
            v.setdefault("preset", "veryfast")
            v.setdefault("bitrate_kbps", "auto")
            v.setdefault("interlaced", False)
            v["_hd"] = v["_wh"][1] > 576
            if v["codec"] == "mpeg2" and v["_hd"] and not region60:
                W.append("services[%d]: MPEG-2 HD ist unueblich (MP@HL), Receiver koennen das meist, brauchen aber viel Bitrate" % i)
        # Audio
        a = s.setdefault("audio", {})
        a["codec"] = str(a.get("codec", "mp2")).lower().replace("mpeg", "mp2").replace("a52", "ac3")
        if a["codec"] not in AUDIO_CODECS:
            E.append("services[%d].audio.codec: mp2, aac oder ac3" % i)
        a["bitrate_kbps"] = _int(a.get("bitrate_kbps"), 192 if a["codec"] != "aac" else 128)
        a["channels"] = _int(a.get("channels"), 2)
        a["samplerate"] = _int(a.get("samplerate"), 48000)
        a.setdefault("language", "deu")
        if a["codec"] == "mp2" and a["samplerate"] not in (32000, 44100, 48000):
            E.append("services[%d].audio.samplerate fuer MP2: 32/44,1/48 kHz" % i)
        # Quelle
        src = s.setdefault("source", {"kind": "testpattern"})
        k = str(src.get("kind", "testpattern")).lower()
        src["kind"] = {"test": "testpattern", "testbild": "testpattern", "video": "file", "bilder": "slideshow",
                       "images": "slideshow", "yt": "youtube"}.get(k, k)
        if src["kind"] not in SOURCE_KINDS:
            E.append("services[%d].source.kind: %s" % (i, ", ".join(SOURCE_KINDS)))
        if src["kind"] in NVENC_ONLY_SOURCES and v.get("encoder") != "nvenc":
            E.append("services[%d]: Quelle %s laeuft auf Windows und braucht video.encoder = nvenc" % (i, src["kind"]))
        if src["kind"] == "dshow" and not src.get("device"):
            E.append("services[%d].source.device fehlt (dablab_run.py media dshow)" % i)
        if src["kind"] == "ts":
            if not src.get("file"):
                E.append("services[%d].source.file fehlt (fertiger Transportstrom)" % i)
            src["program"] = _int(src.get("program"), s["service_id"])
        if src["kind"] == "testpattern":
            src.setdefault("pattern", "testsrc2" if not (s["type"] == "tv" and v.get("_hd")) else "smptehdbars")
            if src["pattern"] not in PATTERNS:
                E.append("services[%d].source.pattern: %s" % (i, ", ".join(PATTERNS)))
            src.setdefault("text", "")
            src.setdefault("timestamp", True)
        if src["kind"] == "file" and not src.get("file"):
            E.append("services[%d].source.file fehlt" % i)
        if src["kind"] == "youtube" and not (src.get("url") or src.get("id")):
            E.append("services[%d].source.url fehlt (YouTube)" % i)
        if src["kind"] == "slideshow":
            if not src.get("images"):
                E.append("services[%d].source.images fehlt" % i)
            src["interval_s"] = float(src.get("interval_s") or 5)
        if src["kind"] == "playlist" and not src.get("playlist"):
            E.append("services[%d].source.playlist fehlt" % i)
        src.setdefault("loop", True)
        aud = src.setdefault("audio", "sine" if src["kind"] in ("testpattern", "slideshow") else "source")
        if isinstance(aud, str):
            src["audio"] = {"kind": aud}
        src["audio"].setdefault("kind", "sine")
        src["audio"].setdefault("freq_hz", 1000)
        src["audio"].setdefault("level_dbfs", -18)
        src["audio"].setdefault("text", "This is the DAB Lab test transmitter, programme %s." % s["name"])
        if src["audio"]["kind"] not in AUDIO_GEN + ("source",):
            E.append("services[%d].source.audio.kind: %s, source" % (i, ", ".join(AUDIO_GEN)))
        if src["kind"] in ("testpattern", "slideshow") and src["audio"]["kind"] == "source":
            src["audio"]["kind"] = "sine"
        # EIT
        e = s.setdefault("eit", {})
        e.setdefault("title", s["name"])
        e.setdefault("text", "DAB-Lab Testsendung")
        e.setdefault("duration_min", 60)
        e.setdefault("language", "deu")
        e.setdefault("next_title", None)
        e.setdefault("next_text", "")
        if std == "atsc" and s["type"] == "tv" and v.get("codec") != "mpeg2":
            W.append("services[%d]: ATSC 1.0 sieht MPEG-2-Video vor (A/53); %s verstehen viele Empfaenger nicht" % (i, v.get("codec")))
        if T.is_psip(cfg) and a["codec"] != "ac3":
            W.append("services[%d]: ATSC/US-Kabel erwartet AC-3-Ton (A/52)" % i)
        jp = std == "isdbt" and cfg["isdbt"]["region"] == "jp"
        if std == "isdbt" and not jp and s["type"] == "tv" and v.get("codec") != "h264":
            W.append("services[%d]: ISDB-Tb sieht H.264 vor (ABNT NBR 15602)" % i)
        if jp and s["type"] == "tv" and v.get("codec") != "mpeg2" and not s.get("oneseg"):
            W.append("services[%d]: ISDB-T Japan sieht MPEG-2-Video vor (ARIB STD-B32)" % i)
        if std == "isdbt" and a["codec"] != "aac":
            W.append("services[%d]: ISDB-Tb sieht AAC (LATM/LOAS) vor" % i)
        s["oneseg"] = bool(s.get("oneseg")) and std == "isdbt" and cfg["isdbt"]["layers"] == 2
        if std == "isdbt":
            # ABNT NBR 15603: service_id = network_id(11 Bit) << 5 | Diensttyp << 3 | Nummer (0 TV, 3 1seg/partial reception)
            s["service_id"] = ((ts["network_id"] & 0x7FF) << 5) | ((3 if s["oneseg"] else 0) << 3) | (i & 7)
        s["_service_type"] = (0xC0 if s["oneseg"] else 0x01 if (std == "isdbt" and s["type"] == "tv") else
                              0x1F if (s["type"] == "tv" and v.get("codec") == "hevc") else
                              0x19 if (s["type"] == "tv" and v.get("codec") == "h264" and v.get("_hd")) else
                              0x01 if s["type"] == "tv" else (0x0A if a["codec"] == "aac" else 0x02))
        if s.get("service_type") is not None:
            s["_service_type"] = _int(s["service_type"])
        # PIDs
        s["_pmt_pid"] = ts["pmt_start_pid"] + i
        if s.get("oneseg"):
            # ARIB TR-B14 / ABNT NBR 15603: PMT des 1seg-Dienstes auf 0x1FC8–0x1FCF (Empfänger suchen dort); tsp remap setzt sie um
            s["_pmt_pid_out"] = 0x1FC8 + sum(1 for x in svcs[:i] if x.get("oneseg"))
        s["_vpid"] = ts["start_pid"] + 16 * i
        s["_apid"] = ts["start_pid"] + 16 * i + 1
        s["_pcr_pid"] = s["_vpid"] if s["type"] == "tv" else s["_apid"]

    # -- Ausgabe --
    out = cfg.setdefault("output", {})
    out["kind"] = str(out.get("kind", "stdout")).lower()
    if out["kind"] not in OUTPUT_KINDS:
        E.append("output.kind: %s" % ", ".join(OUTPUT_KINDS))
    out.setdefault("host", "192.168.2.1")
    out["port"] = _int(out.get("port"), 1234)
    out["burst"] = _int(out.get("burst"), 7)
    out.setdefault("rtp", False)
    out.setdefault("ttl", None)
    out.setdefault("regulate", out["kind"] in ("udp", "file"))
    out.setdefault("modulator_cmd", None)
    out.setdefault("file", None)
    if E:
        return cfg, E, W

    # -- Bitratenbudget --
    _budget(cfg, W)
    return cfg, E, W


def _budget(cfg, W):
    """Videobitraten 'auto' aus der Nutzbitrate ableiten; Summe pruefen.
    Reserve: SI-Tabellen + Nullpakete ~ 4 %, PES/TS-Overhead Video ~ 3 %, Audio-TS-Overhead ~ 8 %."""
    R = cfg["_rate_bps"]
    # PAT/PMT je Bild + NIT/SDT (ffmpeg, per tsp ersetzt): bei großen Raten pauschal 250 kbit/s, bei DATV-Raten ~ 8 %
    si = (250000 if R >= 4e6 else max(25000, 0.08 * R)) + cfg["ts"]["eit_bitrate"] + cfg["ts"]["tdt_bitrate"]
    audio = sum(s["audio"]["bitrate_kbps"] * 1000 * 1.08 for s in cfg["services"])
    avail = R * 0.96 - si - audio
    autos = [s for s in cfg["services"] if s["type"] == "tv" and str(s["video"]["bitrate_kbps"]).lower() == "auto"]
    fixed = sum(float(s["video"]["bitrate_kbps"]) * 1000 for s in cfg["services"]
                if s["type"] == "tv" and str(s["video"]["bitrate_kbps"]).lower() != "auto")
    avail -= fixed * 1.03
    if autos:
        weights = []
        for s in autos:
            w, h = s["video"]["_wh"]
            px = w * h
            cap = 40000 if px > 4000000 else (15000 if s["video"]["codec"] == "mpeg2" else 20000) if px > 500000 else \
                (9000 if s["video"]["codec"] == "mpeg2" else 6000)
            weights.append((s, px ** 0.6, cap * 1000))
        tot = sum(w for _, w, _ in weights)
        for s, w, cap in weights:
            br = avail / 1.03 * w / tot if tot else 0
            br = max(min(br, cap), 300000)
            s["video"]["bitrate_kbps"] = int(br / 1000)
    d = cfg.get("isdbt") or {}
    if cfg["mode"] == "isdbt" and d.get("layers") == 2:
        # Schicht A (1seg) traegt nur die oneseg-Dienste: deren Videorate aus der Schicht-A-Rate (PAT/PMT/NIT ~ 30 kb/s)
        ra = float(d["_rate_a"]) * 0.92 - 30000
        for s in cfg["services"]:
            if s.get("oneseg") and s["type"] == "tv":
                s["video"]["bitrate_kbps"] = int(max(100000, ra - s["audio"]["bitrate_kbps"] * 1080) / 1000)
                s["video"]["_wh"] = (320, 240)
                s["video"]["size"] = "320x240"
    total = si + audio + sum(float(s["video"]["bitrate_kbps"]) * 1000 * 1.03 for s in cfg["services"]
                             if s["type"] == "tv")
    cfg["_budget"] = {"rate_bps": R, "si_bps": si, "audio_bps": round(audio), "video_bps":
                      round(sum(float(s["video"]["bitrate_kbps"]) * 1000 for s in cfg["services"] if s["type"] == "tv")),
                      "total_bps": round(total), "null_share": round(1 - total / R, 4)}
    if total > R:
        W.append("Bitratensumme %.0f kb/s ueberschreitet die Nutzbitrate %.0f kb/s - ffmpeg wird den Mux ueberlaufen lassen"
                 % (total / 1000, R / 1000))
    for s in cfg["services"]:
        if s["type"] == "tv" and float(s["video"]["bitrate_kbps"]) < (150 if cfg["_rate_bps"] < 2e6 else 800):
            W.append("services[%s]: Videobitrate %s kb/s ist sehr niedrig" % (s["key"], s["video"]["bitrate_kbps"]))


def summary(cfg):
    std = cfg["mode"]
    d = cfg[std]
    mod = ("%s %s %s GI %s %d MHz" % (d["mode"], d["constellation"], d["code_rate"], d["guard"], d["bandwidth_mhz"])
           if std == "dvbt" else ("%s%s %s %.3f MBd%s" % ("DVB-S2 " if d.get("system") == "dvbs2" else "", d.get("modulation", "QPSK"),
                                                              d["code_rate"], d["symbol_rate"] / 1e6,
                                                              (" α %s%s" % (d["roll_off"], " Pilots" if d.get("pilots") else ""))
                                                              if d.get("system") == "dvbs2" else "")
                                                             + (" · DATV %s" % d.get("callsign", "") if d.get("use") == "ham" else "")) if std == "dvbs"
           else "8-VSB 10,762 MBd" if std == "atsc"
           else ("%s %s %s PN%d TI %d" % ("C=3780" if d["carriers"] == 3780 else "C=1", d["constellation"], d["code_rate"], d["header"], d["interleaver"])) if std == "dtmb"
           else ("Mode %d GI %s %s" % (d["mode"], d["guard"], " + ".join("%s: %d Seg. %s %s" % (L.upper(), d["layer_" + L]["segments"],
                                                                                                d["layer_" + L]["constellation"], d["layer_" + L]["code_rate"])
                                                                         for L in ("a", "b") if d.get("layer_" + L)))) if std == "isdbt"
           else "%s%s %.3f MBd" % ("J.83 B " if d.get("annex") == "b" else "", d["constellation"], d["symbol_rate"] / 1e6))
    return {"standard": std, "modulation": mod, "useful_bitrate_bps": cfg["_rate_bps"], "muxrate": cfg["_muxrate"],
            "freq_hz": cfg["rf"]["freq_hz"], "channel": cfg["rf"].get("channel"),
            "sample_rate": cfg["rf"].get("sample_rate"), "budget": cfg.get("_budget"),
            "services": [{"key": s["key"], "service_id": s["service_id"], "name": s["name"], "lcn": s["lcn"],
                          "type": s["type"], "video": (s["video"].get("codec"), s["video"].get("size"),
                                                       s["video"].get("bitrate_kbps")) if s["type"] == "tv" else None,
                          "audio": (s["audio"]["codec"], s["audio"]["bitrate_kbps"]), "source": s["source"]["kind"],
                          "pmt_pid": s.get("_pmt_pid_out", s["_pmt_pid"]), "video_pid": s["_vpid"], "audio_pid": s["_apid"]}
                         for s in cfg["services"]]}
