# -*- coding: utf-8 -*-
"""Pruefung eines Transportstroms: tsanalyze, continuity/pcrverify, PCR-Jitter (eigene Regression), tstables (Soll/Ist),
ffprobe/ffmpeg-Decodertest je Programm."""
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


from . import tables as T

_NAMES = {"EACEM": 0x28, "NORDIG": 0x29, "SES": 0x01}


def _num(v, default=None):
    """TSDuck-XML-Werte: Zahl, Hex oder Name (z. B. private_data_specifier='EACEM')."""
    if v is None:
        return default
    s = str(v).strip().replace(",", "")
    try:
        return int(s, 0)
    except ValueError:
        pass
    return _NAMES.get(s.upper(), s)


def _run(cmd, timeout=300, stdin=None):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, stdin=stdin)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------------------------------------
def tsanalyze(host, path):
    rc, out, err = _run([host.tool("tsanalyze"), "--json", path], timeout=600)
    try:
        return json.loads(out)
    except Exception:
        return {"error": err[-500:]}


def continuity_pcr(host, path, jitter_max_ticks=14):
    """tsp -P continuity -P pcrverify: zaehlt gemeldete Fehler (stderr)."""
    rc, out, err = _run([host.tool("tsp"), "-I", "file", path, "-P", "continuity", "-P", "pcrverify",
                         "--jitter-max", str(jitter_max_ticks), "-O", "drop"], timeout=600)
    cc = sum(1 for l in err.splitlines() if "continuity" in l.lower() and ("error" in l.lower() or "discontinuity" in l.lower()))
    pj = sum(1 for l in err.splitlines() if "pcrverify" in l.lower() and "jitter" in l.lower())
    return {"continuity_errors": cc, "pcrverify_jitter_reports": pj, "jitter_max_ticks": jitter_max_ticks,
            "log_tail": err.strip().splitlines()[-5:]}


def pcr_jitter(path, rate_bps, pids=None, max_packets=4000000):
    """PCR-Regression: Steigung -> gemessene Bitrate, Residuen -> Jitter. Rueckgabe je PCR-PID."""
    if np is None:
        return {"error": "numpy fehlt"}
    size = os.path.getsize(path)
    n = min(size // 188, max_packets)
    a = np.fromfile(path, dtype=np.uint8, count=n * 188).reshape(n, 188)
    sync_ok = int(np.sum(a[:, 0] == 0x47))
    pid = ((a[:, 1].astype(np.uint16) & 0x1F) << 8) | a[:, 2]
    afc = (a[:, 3] >> 4) & 3
    has_af = (afc & 2) != 0
    aflen = a[:, 4]
    pcr_flag = has_af & (aflen >= 7) & ((a[:, 5] & 0x10) != 0)
    idx = np.nonzero(pcr_flag)[0]
    res = {"packets": n, "sync_ok": sync_ok, "pids": {}}
    for p in np.unique(pid[idx]):
        if pids and int(p) not in pids:
            continue
        ii = idx[pid[idx] == p]
        if len(ii) < 10:
            continue
        b = a[ii, 6:12].astype(np.int64)
        base = (b[:, 0] << 25) | (b[:, 1] << 17) | (b[:, 2] << 9) | (b[:, 3] << 1) | (b[:, 4] >> 7)
        ext = ((b[:, 4] & 1) << 8) | b[:, 5]
        pcr = base * 300 + ext
        # Wrap (2^33 * 300) ausgleichen
        d = np.diff(pcr)
        wrap = np.where(d < -(1 << 40), (1 << 33) * 300, 0)
        pcr = pcr + np.concatenate([[0], np.cumsum(wrap)])
        x = ii.astype(np.float64)
        y = pcr.astype(np.float64)
        slope, inter = np.polyfit(x, y, 1)      # Ticks je Paket
        resid = y - (slope * x + inter)
        measured = 188 * 8 * 27e6 / slope
        exp_slope = 188 * 8 * 27e6 / float(rate_bps)
        resid_fixed = y - y[0] - (x - x[0]) * exp_slope
        res["pids"][int(p)] = {
            "pcr_count": int(len(ii)), "measured_bps": round(measured, 1),
            "deviation_ppm": round((measured / float(rate_bps) - 1) * 1e6, 2),
            "jitter_max_ns": round(float(np.max(np.abs(resid))) / 27.0 * 1000, 1),
            "jitter_rms_ns": round(float(np.sqrt(np.mean(resid ** 2))) / 27.0 * 1000, 1),
            "drift_vs_target_ns_per_s": round(float((resid_fixed[-1] - resid_fixed[0]) / 27.0 * 1000 /
                                                    max(1e-9, (x[-1] - x[0]) * 188 * 8 / float(rate_bps))), 1),
            "interval_max_ms": round(float(np.max(np.diff(y))) / 27000.0, 1)}
    return res


# ---------------------------------------------------------------------------------------------
def tstables(host, path, max_tables=60, extra_pids=()):
    out = path + ".tables.xml"
    cmd = [host.tool("tstables"), "--pid", "0", "--pid", "16", "--pid", "17", "--pid", "18", "--pid", "20"]
    for p in extra_pids:
        cmd += ["--pid", str(int(p))]
    rc, o, e = _run(cmd + ["--max-tables", str(max_tables + 10 * len(extra_pids)), "--xml-output", out, path], timeout=600)
    tabs = {"PAT": [], "PMT": [], "NIT": [], "SDT": [], "EIT": [], "TDT": [], "TOT": [], "other": []}
    try:
        root = ET.parse(out).getroot()
    except Exception as ex:
        return {"error": "tstables: %s %s" % (ex, e[-300:])}, tabs
    seen = set()
    for t in root:
        key = (t.tag, json.dumps(t.attrib, sort_keys=True), ET.tostring(t)[:4000])
        if key in seen:
            continue
        seen.add(key)
        tabs.setdefault(t.tag, tabs["other"]).append(t)
    return None, tabs


def parse_tables(tabs):
    """TSDuck-XML -> kompakte Ist-Werte."""
    r = {"pat": None, "nit": None, "sdt": None, "eit": [], "tdt": bool(tabs["TDT"]), "tot": None, "pmt": {}}
    for pm in tabs["PMT"]:
        sid = _num(pm.get("service_id"))
        if sid in r["pmt"]:
            continue
        r["pmt"][sid] = {"pcr_pid": _num(pm.get("PCR_PID")), "version": _num(pm.get("version")),
                         "components": [{"pid": _num(c.get("elementary_PID")), "stream_type": _num(c.get("stream_type")),
                                         "descriptors": [d.tag for d in c]} for c in pm.findall("component")]}
    if tabs["PAT"]:
        p = tabs["PAT"][0]
        r["pat"] = {"transport_stream_id": int(p.get("transport_stream_id", "0"), 0),
                    "nit_pid": int(p.get("network_PID", "0"), 0) if p.get("network_PID") else None,
                    "programs": {int(s.get("service_id"), 0): int(s.get("program_map_PID"), 0) for s in p.findall("service")}}
    if tabs["NIT"]:
        n = tabs["NIT"][0]
        d = {"network_id": int(n.get("network_id", "0"), 0), "actual": n.get("actual"), "version": int(n.get("version", "0")),
             "network_name": None, "transport_streams": []}
        nn = n.find("network_name_descriptor")
        if nn is not None:
            d["network_name"] = nn.get("network_name")
        for tsx in n.findall("transport_stream"):
            t = {"transport_stream_id": int(tsx.get("transport_stream_id"), 0),
                 "original_network_id": int(tsx.get("original_network_id"), 0), "delivery": None,
                 "service_list": {}, "lcn": {}, "private_data_specifier": None}
            td = tsx.find("terrestrial_delivery_system_descriptor")
            cd = tsx.find("cable_delivery_system_descriptor")
            sd = tsx.find("satellite_delivery_system_descriptor")
            if sd is not None:
                t["delivery"] = {"kind": "satellite", "frequency": _num(sd.get("frequency")),
                                 "orbital_position": sd.get("orbital_position"), "west_east": sd.get("west_east_flag"),
                                 "polarization": sd.get("polarization"), "modulation_system": sd.get("modulation_system"),
                                 "modulation_type": sd.get("modulation_type"), "symbol_rate": _num(sd.get("symbol_rate")),
                                 "FEC_inner": sd.get("FEC_inner"), "roll_off": sd.get("roll_off")}
            if td is not None:
                t["delivery"] = {"kind": "terrestrial", "centre_frequency": int(td.get("centre_frequency", "0").replace(",", "")),
                                 "bandwidth": td.get("bandwidth"), "constellation": td.get("constellation"),
                                 "code_rate_HP": td.get("code_rate_HP_stream"), "guard_interval": td.get("guard_interval"),
                                 "transmission_mode": td.get("transmission_mode")}
            if cd is not None:
                t["delivery"] = {"kind": "cable", "frequency": int(cd.get("frequency", "0").replace(",", "")),
                                 "modulation": cd.get("modulation"), "symbol_rate": int(cd.get("symbol_rate", "0").replace(",", "")),
                                 "FEC_outer": cd.get("FEC_outer"), "FEC_inner": cd.get("FEC_inner")}
            sl = tsx.find("service_list_descriptor")
            if sl is not None:
                t["service_list"] = {_num(s.get("service_id")): _num(s.get("service_type")) for s in sl.findall("service")}
            pds = tsx.find("private_data_specifier_descriptor")
            if pds is not None:
                t["private_data_specifier"] = _num(pds.get("private_data_specifier"))
            lc = tsx.find("logical_channel_number_descriptor")
            if lc is None:
                lc = tsx.find("eacem_logical_channel_number_descriptor")
            if lc is not None:
                t["lcn"] = {_num(s.get("service_id")): _num(s.get("logical_channel_number")) for s in lc.findall("service")}
            d["transport_streams"].append(t)
        r["nit"] = d
    if tabs["SDT"]:
        s = tabs["SDT"][0]
        d = {"transport_stream_id": int(s.get("transport_stream_id"), 0), "original_network_id": int(s.get("original_network_id"), 0),
             "version": int(s.get("version", "0")), "services": {}}
        for sv in s.findall("service"):
            sd = sv.find("service_descriptor")
            d["services"][int(sv.get("service_id"), 0)] = {
                "name": sd.get("service_name") if sd is not None else None,
                "provider": sd.get("service_provider_name") if sd is not None else None,
                "type": _num(sd.get("service_type")) if sd is not None else None,
                "eit_pf": sv.get("EIT_present_following"), "eit_schedule": sv.get("EIT_schedule"),
                "running_status": sv.get("running_status")}
        r["sdt"] = d
    for e in tabs["EIT"]:
        evs = []
        for ev in e.findall("event"):
            sed = ev.find("short_event_descriptor")
            evs.append({"event_id": int(ev.get("event_id"), 0), "start": ev.get("start_time"), "duration": ev.get("duration"),
                        "running": ev.get("running_status"),
                        "name": (sed.findtext("event_name") if sed is not None else None),
                        "text": (sed.findtext("text") if sed is not None else None)})
        r["eit"].append({"service_id": int(e.get("service_id"), 0), "type": e.get("type"), "actual": e.get("actual"), "events": evs})
    if tabs["TOT"]:
        t = tabs["TOT"][0]
        lt = t.find("local_time_offset_descriptor/region")
        r["tot"] = {"utc_time": t.get("UTC_time"), "country": lt.get("country_code") if lt is not None else None,
                    "offset": lt.get("local_time_offset") if lt is not None else None}
    if tabs["TDT"]:
        r["tdt"] = {"utc_time": tabs["TDT"][0].get("UTC_time")}
    return r


# ---------------------------------------------------------------------------------------------
def ffprobe_programs(host, path):
    fp = host.tool("ffprobe", required=False)
    if not fp:
        return []
    rc, out, err = _run([fp, "-v", "error", "-print_format", "json", "-show_programs", "-show_streams", path], timeout=120)
    try:
        j = json.loads(out)
    except Exception:
        return []
    progs = []
    for p in j.get("programs", []):
        progs.append({"program_id": p.get("program_id"), "pmt_pid": p.get("pmt_pid"), "pcr_pid": p.get("pcr_pid"),
                      "tags": p.get("tags", {}),
                      "streams": [{"pid": int(s.get("id", "0"), 0) if s.get("id") else None, "type": s.get("codec_type"),
                                   "codec": s.get("codec_name"), "profile": s.get("profile"),
                                   "size": ("%sx%s" % (s.get("width"), s.get("height"))) if s.get("width") else None,
                                   "fps": s.get("r_frame_rate"), "sample_rate": s.get("sample_rate"),
                                   "channels": s.get("channels"), "language": (s.get("tags") or {}).get("language")}
                                  for s in p.get("streams", [])]})
    return progs


def decode_test(host, path, service_id, seconds=4, has_video=True):
    """ffmpeg dekodiert ein Programm in den Null-Muxer; Rueckgabe: Frames/Zeit, Fehlerzeilen."""
    ff = host.tool("ffmpeg")
    cmd = [ff, "-nostdin", "-v", "error", "-threads", "2", "-t", str(seconds), "-i", path, "-map", "0:p:%d" % service_id,
           "-progress", "pipe:1", "-f", "null", "-"]
    rc, out, err = _run(cmd, timeout=180)
    frames = 0
    out_us = 0
    for l in out.splitlines():
        if l.startswith("frame="):
            try:
                frames = int(l.split("=")[1])
            except ValueError:
                pass
        if l.startswith("out_time_us="):
            try:
                out_us = int(l.split("=")[1])
            except ValueError:
                pass
    errs = [l for l in err.splitlines() if l.strip()]
    return {"rc": rc, "video_frames": frames, "decoded_s": round(out_us / 1e6, 2), "errors": len(errs),
            "error_sample": errs[:3]}


# ---------------------------------------------------------------------------------------------
def compare(cfg, rep):
    """Soll/Ist -> Liste von Abweichungen."""
    M = []
    ts = cfg["ts"]
    std = cfg["mode"]
    R = cfg["_rate_bps"]
    b = rep.get("bitrate") or {}
    if b.get("measured_bps"):
        dev = abs(b["measured_bps"] / R - 1)
        if dev > 1e-4:
            M.append("Bitrate %.1f b/s weicht %.4f %% von %.1f ab" % (b["measured_bps"], dev * 100, R))
    pj = rep.get("pcr", {})
    for pid, v in (pj.get("pids") or {}).items():
        if v["jitter_max_ns"] > 500:
            M.append("PCR-Jitter PID %s: max %.0f ns > 500 ns" % (pid, v["jitter_max_ns"]))
    if rep.get("continuity", {}).get("continuity_errors"):
        M.append("Continuity-Fehler: %d" % rep["continuity"]["continuity_errors"])
    t = rep.get("tables") or {}
    pat = t.get("pat")
    if not pat:
        M.append("PAT fehlt")
    else:
        if pat["transport_stream_id"] != ts["transport_stream_id"]:
            M.append("PAT TSID 0x%04X statt 0x%04X" % (pat["transport_stream_id"], ts["transport_stream_id"]))
        for s in cfg["services"]:
            if s["service_id"] not in pat["programs"]:
                M.append("PAT: Programm %d fehlt" % s["service_id"])
        if pat.get("nit_pid") not in (16, None):
            M.append("PAT verweist auf NIT-PID %s" % pat.get("nit_pid"))
    psip = T.is_psip(cfg)
    sdt = t.get("sdt")
    if psip:
        pass                              # ATSC: virtuelle Kanaele in der TVCT/CVCT statt SDT
    elif not sdt:
        M.append("SDT fehlt")
    else:
        if sdt["original_network_id"] != ts["original_network_id"]:
            M.append("SDT ONID 0x%04X statt 0x%04X" % (sdt["original_network_id"], ts["original_network_id"]))
        for s in cfg["services"]:
            e = sdt["services"].get(s["service_id"])
            if not e:
                M.append("SDT: Dienst %d fehlt" % s["service_id"])
                continue
            if e["name"] != s["name"]:
                M.append("SDT: Name '%s' statt '%s'" % (e["name"], s["name"]))
            if e["provider"] != s["provider"]:
                M.append("SDT: Provider '%s' statt '%s'" % (e["provider"], s["provider"]))
            if e["type"] != s["_service_type"]:
                M.append("SDT: service_type 0x%02X statt 0x%02X (%s)" % (e["type"] or 0, s["_service_type"], s["name"]))
            if ts.get("eit", True) and e["eit_pf"] != "true":
                M.append("SDT: EIT_present_following nicht gesetzt (%s)" % s["name"])
    nit = t.get("nit")
    if psip:
        pass
    elif not nit:
        M.append("NIT fehlt")
    else:
        if nit["network_id"] != ts["network_id"]:
            M.append("NIT network_id 0x%04X statt 0x%04X" % (nit["network_id"], ts["network_id"]))
        if nit.get("network_name") != ts["network_name"]:
            M.append("NIT Netzname '%s' statt '%s'" % (nit.get("network_name"), ts["network_name"]))
        tss = [x for x in nit["transport_streams"] if x["transport_stream_id"] == ts["transport_stream_id"]]
        if not tss:
            M.append("NIT: eigener TS 0x%04X fehlt" % ts["transport_stream_id"])
        else:
            x = tss[0]
            d = x.get("delivery")
            if not d and std not in ("isdbt", "dtmb"):       # ISDB_terrestrial_delivery_system_descriptor zerlegt die Analyse nicht
                M.append("NIT: Delivery-System-Descriptor fehlt")
            elif std == "dvbt":
                dd = cfg["dvbt"]
                want = {"kind": "terrestrial", "centre_frequency": int(cfg["rf"]["freq_hz"]), "bandwidth": "%dMHz" % dd["bandwidth_mhz"],
                        "constellation": dd["constellation"], "code_rate_HP": dd["code_rate"], "guard_interval": dd["guard"],
                        "transmission_mode": dd["mode"]}
                for k, v in want.items():
                    if d.get(k) != v:
                        M.append("NIT terrestrial %s = %r statt %r" % (k, d.get(k), v))
            elif std in ("isdbt", "dtmb") or not d:
                pass                      # ISDB-/DTMB-Delivery: von tsanalyze nicht zerlegt bzw. nicht vorhanden
            elif std == "dvbs":
                dd = cfg["dvbs"]
                want = {"kind": "satellite", "frequency": int(dd["frequency_hz"]), "symbol_rate": int(dd["symbol_rate"]),
                        "FEC_inner": dd["code_rate"], "polarization": dd["polarization"], "modulation_type": dd.get("modulation", "QPSK"),
                        "modulation_system": "DVB-S2" if dd.get("system") == "dvbs2" else "DVB-S"}
                if dd.get("system") == "dvbs2":
                    want["FEC_inner"] = T.DVBS2_NIT_FEC[dd["code_rate"]]
                    want["roll_off"] = dd["roll_off"]
                for k, v in want.items():
                    if d.get(k) != v:
                        M.append("NIT satellite %s = %r statt %r" % (k, d.get(k), v))
            else:
                dd = cfg["dvbc"]
                want = {"kind": "cable", "frequency": int(cfg["rf"]["freq_hz"]), "modulation": dd["constellation"],
                        "symbol_rate": int(dd["symbol_rate"]), "FEC_outer": "RS", "FEC_inner": "none"}
                for k, v in want.items():
                    if d.get(k) != v:
                        M.append("NIT cable %s = %r statt %r" % (k, d.get(k), v))
            for s in cfg["services"]:
                if x["service_list"].get(s["service_id"]) != s["_service_type"]:
                    M.append("NIT service_list: Dienst %d Typ %r statt 0x%02X" % (s["service_id"], x["service_list"].get(s["service_id"]),
                                                                                s["_service_type"]))
                if ts.get("lcn", True):
                    if x.get("private_data_specifier") != 0x28:
                        M.append("NIT: private_data_specifier EACEM (0x28) fehlt")
                    if x["lcn"].get(s["service_id"]) != s["lcn"]:
                        M.append("NIT LCN Dienst %d: %r statt %d" % (s["service_id"], x["lcn"].get(s["service_id"]), s["lcn"]))
    ST_V = {"mpeg2": (0x02,), "h264": (0x1B,), "hevc": (0x24,)}
    ST_A = {"mp2": (0x03, 0x04), "aac": (0x0F, 0x11), "ac3": (0x06, 0x81)}
    for s in cfg["services"]:
        pm = (t.get("pmt") or {}).get(s["service_id"])
        if not pm:
            M.append("PMT fuer Dienst %d fehlt" % s["service_id"])
            continue
        if pm.get("pcr_pid") != s["_pcr_pid"]:
            M.append("PMT Dienst %d: PCR-PID %s statt %d" % (s["service_id"], pm.get("pcr_pid"), s["_pcr_pid"]))
        types = {c["pid"]: c["stream_type"] for c in pm["components"]}
        if s["type"] == "tv" and types.get(s["_vpid"]) not in ST_V[s["video"]["codec"]]:
            M.append("PMT Dienst %d: Video-Streamtyp %s statt %s" % (s["service_id"], types.get(s["_vpid"]), ST_V[s["video"]["codec"]]))
        if types.get(s["_apid"]) not in ST_A[s["audio"]["codec"]]:
            M.append("PMT Dienst %d: Audio-Streamtyp %s statt %s" % (s["service_id"], types.get(s["_apid"]), ST_A[s["audio"]["codec"]]))
    if ts.get("eit", True):
        eits = {e["service_id"]: e for e in t.get("eit", []) if e.get("actual") == "true"}
        for s in cfg["services"]:
            e = eits.get(s["service_id"])
            if not e or not e["events"]:
                M.append("EIT p/f fuer Dienst %d fehlt" % s["service_id"])
            elif s["source"]["kind"] != "playlist":
                names = [ev["name"] for ev in e["events"]]
                if s["eit"]["title"] not in names:
                    M.append("EIT Dienst %d: Titel %r nicht in %r" % (s["service_id"], s["eit"]["title"], names))
    if ts.get("tdt", True):
        if not t.get("tdt"):
            M.append("TDT fehlt")
        if not t.get("tot"):
            M.append("TOT fehlt")
    for s in rep.get("services", []):
        if s.get("decode", {}).get("rc", 0) != 0:
            M.append("Decodertest Dienst %d fehlgeschlagen" % s["service_id"])
        elif s.get("type") == "tv" and s.get("decode", {}).get("video_frames", 0) < 10:
            M.append("Decodertest Dienst %d: nur %d Videobilder" % (s["service_id"], s.get("decode", {}).get("video_frames", 0)))
        elif s.get("type") == "radio" and s.get("decode", {}).get("decoded_s", 0) < 1:
            M.append("Decodertest Dienst %d: kein Ton dekodiert" % s["service_id"])
        if s.get("decode", {}).get("errors"):
            M.append("Decoder meldet %d Fehlerzeilen bei Dienst %d" % (s["decode"]["errors"], s["service_id"]))
        if s.get("stream_check"):
            M += s["stream_check"]
    return M


def analyze_file(host, cfg, path, log=None, decode_seconds=4):
    """Komplette Pruefung einer TS-Datei gegen die Konfiguration -> Bericht."""
    R = cfg["_rate_bps"]
    rep = {"file": path, "bytes": os.path.getsize(path), "packets": os.path.getsize(path) // 188, "target_bps": R}
    an = tsanalyze(host, path)
    rep["tsanalyze"] = _compact_analyze(an)
    pj = pcr_jitter(path, R)
    rep["pcr"] = pj
    meas = None
    pids = pj.get("pids") or {}
    if pids:
        # gewichtetes Mittel ueber alle PCR-PIDs
        meas = sum(v["measured_bps"] * v["pcr_count"] for v in pids.values()) / sum(v["pcr_count"] for v in pids.values())
    an_rate = None
    try:
        an_rate = float(an["ts"]["bitrate"])
    except Exception:
        pass
    null = None
    try:
        for p in an.get("pids", []):
            if int(p.get("id", -1)) == 8191:
                pk = p.get("packets", 0)
                null = (pk.get("total", 0) if isinstance(pk, dict) else pk) / float(rep["packets"])
    except Exception:
        pass
    rep["bitrate"] = {"target_bps": R, "measured_bps": round(meas, 1) if meas else None,
                      "deviation_pct": round((meas / R - 1) * 100, 5) if meas else None,
                      "tsanalyze_bps": an_rate, "null_share": round(null, 4) if null is not None else None,
                      "max_jitter_ns": max([v["jitter_max_ns"] for v in pids.values()] or [None])}
    rep["continuity"] = continuity_pcr(host, path)
    err, tabs = tstables(host, path, extra_pids=[s["_pmt_pid"] for s in cfg["services"]])
    rep["tables"] = parse_tables(tabs) if not err else {"error": err}
    rep["table_counts"] = {k: len(v) for k, v in tabs.items()}
    progs = {p["program_id"]: p for p in ffprobe_programs(host, path)}
    svcs = []
    for s in cfg["services"]:
        p = progs.get(s["service_id"])
        d = {"key": s["key"], "service_id": s["service_id"], "name": s["name"], "type": s["type"], "lcn": s["lcn"],
             "ffprobe": p, "stream_check": []}
        if not p:
            d["stream_check"].append("ffprobe: Programm %d nicht gefunden" % s["service_id"])
        else:
            sts = p["streams"]
            v = [x for x in sts if x["type"] == "video"]
            a = [x for x in sts if x["type"] == "audio"]
            if s["type"] == "tv":
                want = {"mpeg2": "mpeg2video", "h264": "h264", "hevc": "hevc"}[s["video"]["codec"]]
                if not v:
                    d["stream_check"].append("Programm %d ohne Video" % s["service_id"])
                elif v[0]["codec"] != want:
                    d["stream_check"].append("Programm %d Video %s statt %s" % (s["service_id"], v[0]["codec"], want))
                elif v[0]["size"] != "%dx%d" % s["video"]["_wh"]:
                    d["stream_check"].append("Programm %d Groesse %s statt %dx%d" % ((s["service_id"], v[0]["size"]) + s["video"]["_wh"]))
            want_a = {"mp2": "mp2", "aac": "aac", "ac3": "ac3"}[s["audio"]["codec"]]
            if not a:
                d["stream_check"].append("Programm %d ohne Ton" % s["service_id"])
            elif a[0]["codec"].replace("aac_latm", "aac") != want_a:
                d["stream_check"].append("Programm %d Audio %s statt %s" % (s["service_id"], a[0]["codec"], want_a))
            if log:
                log("info", "Decodertest Dienst %d (%s)" % (s["service_id"], s["name"]))
            d["decode"] = decode_test(host, path, s["service_id"], decode_seconds, s["type"] == "tv")
        svcs.append(d)
    rep["services"] = svcs
    rep["mismatches"] = compare(cfg, rep)
    rep["ok"] = not rep["mismatches"]
    dashboard_fields(cfg, rep)
    return rep


def _hx(v):
    return "0x%04X" % v if isinstance(v, int) else v


def dashboard_fields(cfg, rep):
    """Zusatzfelder im Format von web/js/report_std.js (compareDVB): lock, bitrate_bps, pcr_jitter_ns_max,
    continuity_errors, modulation, nit{network_id,network_name,onid,tsid,delivery}, lcn{sid:lcn},
    services[].{sid,name,provider,type,lcn,video_codec,resolution,audio_codec,..,eit_title,pids}, ffprobe{ok,streams}."""
    t = rep.get("tables") or {}
    b = rep.get("bitrate") or {}
    rep["lock"] = True            # TS-Pruefung ohne HF: kein Demodulator im Spiel
    rep["t"] = __import__("time").time()
    rep["bitrate_bps"] = b.get("measured_bps")
    rep["pcr_jitter_ns_max"] = b.get("max_jitter_ns")
    rep["continuity_errors"] = (rep.get("continuity") or {}).get("continuity_errors")
    nit = t.get("nit") or {}
    own = [x for x in nit.get("transport_streams", []) if x["transport_stream_id"] == cfg["ts"]["transport_stream_id"]]
    own = own[0] if own else (nit.get("transport_streams") or [{}])[0] if nit.get("transport_streams") else {}
    deliv = dict(own.get("delivery") or {})
    if deliv.get("kind") == "terrestrial":
        deliv.update({"bandwidth_mhz": float(str(deliv.get("bandwidth", "0")).replace("MHz", "") or 0),
                      "code_rate": deliv.get("code_rate_HP"), "guard": deliv.get("guard_interval"),
                      "fft": deliv.get("transmission_mode"), "frequency_hz": deliv.get("centre_frequency")})
    elif deliv.get("kind") == "satellite":
        deliv.update({"constellation": deliv.get("modulation_type"), "code_rate": deliv.get("FEC_inner"),
                      "frequency_hz": deliv.get("frequency")})
    elif deliv.get("kind") == "cable":
        deliv.update({"constellation": deliv.get("modulation"), "frequency_hz": deliv.get("frequency")})
    rep["modulation"] = deliv
    rep["nit"] = {"network_id": _hx(nit.get("network_id")), "network_name": nit.get("network_name"),
                  "onid": _hx(own.get("original_network_id")), "tsid": _hx(own.get("transport_stream_id")),
                  "version": nit.get("version"), "delivery": deliv,
                  "private_data_specifier": own.get("private_data_specifier")}
    rep["tsid"] = _hx((t.get("pat") or {}).get("transport_stream_id"))
    rep["lcn"] = {_hx(k): v for k, v in (own.get("lcn") or {}).items()}
    sdt = (t.get("sdt") or {}).get("services", {})
    eits = {e["service_id"]: e for e in t.get("eit", []) if e.get("actual") == "true"}
    all_streams = []
    for s in rep.get("services", []):
        sid = s["service_id"]
        sd = sdt.get(sid) or {}
        cs = next((c for c in cfg["services"] if c["service_id"] == sid), None)
        fp = s.get("ffprobe") or {}
        sts = fp.get("streams", [])
        v = next((x for x in sts if x["type"] == "video"), None)
        a = next((x for x in sts if x["type"] == "audio"), None)
        ev = (eits.get(sid) or {}).get("events") or []
        pres = next((e for e in ev if e.get("running") == "running"), ev[0] if ev else {})
        s.update({"sid": _hx(sid), "name": sd.get("name", s.get("name")), "provider": sd.get("provider"),
                  "type": "radio" if sd.get("type") in (0x02, 0x0A) else ("tv" if sd.get("type") is not None else s.get("type")),
                  "service_type": sd.get("type"), "lcn": rep["lcn"].get(_hx(sid)),
                  "video_codec": v["codec"] if v else None, "resolution": v["size"] if v else None,
                  "audio_codec": a["codec"] if a else None,
                  "video_bitrate_kbps": cs["video"].get("bitrate_kbps") if cs and cs["type"] == "tv" else None,
                  "audio_bitrate_kbps": cs["audio"]["bitrate_kbps"] if cs else None,
                  "eit_title": pres.get("name"), "eit_description": pres.get("text"),
                  "pids": {"pmt": fp.get("pmt_pid"), "pcr": fp.get("pcr_pid"),
                           **{x["type"]: x["pid"] for x in sts if x.get("pid") is not None}}})
        for x in sts:
            w, h = (x["size"].split("x") + [None])[:2] if x.get("size") else (None, None)
            all_streams.append({"sid": _hx(sid), "pid": x.get("pid"), "type": x["type"], "codec": x["codec"],
                                "width": int(w) if w else None, "height": int(h) if h else None, "fps": x.get("fps") if w else None,
                                "samplerate": int(x["sample_rate"]) if x.get("sample_rate") else None,
                                "channels": x.get("channels")})
    rep["ffprobe"] = {"ok": all((s.get("decode") or {}).get("rc", 1) == 0 and not (s.get("decode") or {}).get("errors")
                                for s in rep.get("services", [])) and bool(rep.get("services")),
                      "streams": all_streams}


def _compact_analyze(an):
    if not isinstance(an, dict) or "ts" not in an:
        return an
    ts = an.get("ts", {})
    out = {"bitrate": ts.get("bitrate"), "pcr_bitrate": ts.get("pcr-bitrate"), "packets": ts.get("packets", {}),
           "services_count": ts.get("services", {}).get("total") if isinstance(ts.get("services"), dict) else None,
           "services": [], "pids": []}
    for s in an.get("services", []):
        out["services"].append({"id": s.get("id"), "name": s.get("name"), "provider": s.get("provider"),
                                "type": s.get("type"), "type_name": s.get("type-name"), "pmt_pid": s.get("pmt-pid"),
                                "pcr_pid": s.get("pcr-pid"), "bitrate": s.get("bitrate"), "pids": s.get("pids")})
    for p in an.get("pids", []):
        out["pids"].append({"id": p.get("id"), "description": p.get("description"), "bitrate": p.get("bitrate"),
                            "packets": p.get("packets", {}).get("total") if isinstance(p.get("packets"), dict) else p.get("packets"),
                            "discontinuities": p.get("packets", {}).get("discontinuities") if isinstance(p.get("packets"), dict) else None,
                            "pcr": p.get("pcr", {}).get("count") if isinstance(p.get("pcr"), dict) else None})
    return out
