#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UKW-Testsender (FM-Stereo + RDS) fuer den DAB-Lab-Runner: mode == "fm".

Wird von dablab_run.py aufgerufen (dispatch(args, host)); `host` ist das Runner-Modul, aus dem Audioquellen
(ffmpeg_cmd, PlaylistFeeder), Protokollierung (log, emit_json), Pfade (SESSIONS, RUNTIME, TMPROOT), tool(),
media_path(), atomic_write(), level_stats(), new_session_id(), set_pipe_size() und halfband_taps() wiederverwendet werden.

Signalfluss: Audio 48 kHz s16le (ffmpeg/Playlist) -> dsp.FmCarrier (Preemphasis, Limiter, 15-kHz-TP, MPX 228 kHz mit
Pilot/38 kHz/RDS 57 kHz, x1024/57, FM) [+ zweiter Traeger bei +-df] -> int16 I/Q 4,096 MS/s -> stdout.
"""
import errno
import glob
import json
import math
import os
import select
import signal
import subprocess
import sys
import threading
import time

import numpy as np

from . import dsp
from . import rds as rdsmod

FM_MIN_MHZ, FM_MAX_MHZ = 87.5, 108.0
SOURCE_KINDS = ("file", "tone", "silence", "noise", "pinknoise", "sweep", "lr_test", "polarity", "playlist")


class FmError(Exception):
    pass


# --------------------------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------------------------
def _hexint(v, default=None):
    if v is None or v == "":
        return default
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _norm_audio(a, E, W, ctx):
    a = dict(a or {})
    src = dict(a.get("source") or {"kind": "tone", "freq_hz": 1000, "level_dbfs": -6})
    src.setdefault("kind", "tone")
    if src["kind"] not in SOURCE_KINDS:
        E.append("%s.source.kind unbekannt: %r" % (ctx, src["kind"]))
    if src["kind"] == "file" and not src.get("file"):
        E.append("%s.source.file fehlt" % ctx)
    if src["kind"] == "playlist" and not src.get("playlist"):
        E.append("%s.source.playlist fehlt" % ctx)
    src.setdefault("level_dbfs", -6)
    src.setdefault("loop", True)
    a["source"] = src
    a["channels"] = 2
    a["samplerate"] = 48000
    a.setdefault("gain_db", 0)
    return a


def _norm_rds(r, E, W, ctx, tuned_mhz):
    r = dict(r or {})
    r.setdefault("enable", True)
    pi = _hexint(r.get("pi", "0xD3C4"), 0xD3C4)
    if not 0 < pi <= 0xFFFF:
        E.append("%s.pi ungueltig" % ctx)
    r["pi"] = pi
    r["ps"] = str(r.get("ps", "DABLAB"))
    if len(r["ps"]) > 8:
        W.append("%s.ps laenger als 8 Zeichen, wird gekuerzt" % ctx)
        r["ps"] = r["ps"][:8]
    r["rt"] = str(r.get("rt", ""))
    if len(r["rt"]) > 64:
        W.append("%s.rt laenger als 64 Zeichen, wird gekuerzt" % ctx)
    r["rtplus"] = list(r.get("rtplus") or [])
    for t in r["rtplus"]:
        try:
            rdsmod.rtplus_type(t.get("type", 0))
        except ValueError as e:
            E.append("%s.rtplus: %s" % (ctx, e))
    r["pty"] = int(r.get("pty", 0) or 0)
    if not 0 <= r["pty"] <= 31:
        E.append("%s.pty muss 0..31 sein" % ctx)
    for k, d in (("tp", True), ("ta", False), ("ms", True), ("ct", True)):
        r[k] = bool(r.get(k, d))
    r.setdefault("di", {"stereo": True, "artificial_head": False, "compressed": False, "dynamic_pty": False})
    afs = []
    for f in r.get("af") or []:
        try:
            f = float(f)
        except (TypeError, ValueError):
            E.append("%s.af enthaelt keinen Zahlenwert: %r" % (ctx, f))
            continue
        if rdsmod.af_code(f) is None:
            E.append("%s.af %.1f MHz ausserhalb 87,6..107,9" % (ctx, f))
        afs.append(round(f, 1))
    r["af"] = afs
    r["af_method"] = str(r.get("af_method", "A")).upper()
    if r["af_method"] not in ("A", "B"):
        E.append("%s.af_method muss A oder B sein" % ctx)
    r["af_regional"] = [round(float(f), 1) for f in (r.get("af_regional") or [])]
    r["tuned_mhz"] = tuned_mhz
    r["ecc"] = _hexint(r.get("ecc", "0xE0"), None) if r.get("ecc", "0xE0") not in (None, "", False) else None
    r["lic"] = _hexint(r.get("lic"), None) if r.get("lic") not in (None, "") else None
    r["pin"] = r.get("pin") or 0
    r.setdefault("utc_offset_halfhours", "auto")
    r["ct_latency_s"] = float(r.get("ct_latency_s", 0) or 0)
    r["ptyn"] = str(r.get("ptyn", "") or "")[:8]
    r["lps"] = str(r.get("lps", "") or "")
    eon = []
    for e in r.get("eon") or []:
        e = dict(e)
        if _hexint(e.get("pi"), None) is None:
            E.append("%s.eon: pi fehlt" % ctx)
            continue
        e["pi"] = "0x%04X" % _hexint(e["pi"])
        eon.append(e)
    r["eon"] = eon
    r["level_khz"] = float(r.get("level_khz", 2.0))
    if not 0 <= r["level_khz"] <= 7.5:
        E.append("%s.level_khz muss 0..7,5 sein (EN 50067: 1,0..7,5)" % ctx)
    r["phase_deg"] = int(r.get("phase_deg", 0) or 0)
    if r["phase_deg"] not in (0, 90):
        E.append("%s.phase_deg muss 0 oder 90 sein" % ctx)
    r["group_version"] = str(r.get("group_version", "A")).upper()
    r["rt_version"] = str(r.get("rt_version", "A")).upper()
    r.setdefault("rt_from_tags", True)
    r.setdefault("rates", {})
    r.setdefault("oda", [])
    return r


def _norm_carrier(p, E, W, ctx, tuned_mhz, defaults=None):
    p = dict(p or {})
    d = defaults or {}
    p["deviation_khz"] = float(p.get("deviation_khz", d.get("deviation_khz", 75.0)))
    if not 1.0 <= p["deviation_khz"] <= 150.0:
        E.append("%s.deviation_khz muss 1..150 sein" % ctx)
    p["stereo"] = bool(p.get("stereo", d.get("stereo", True)))
    p["pilot_khz"] = float(p.get("pilot_khz", d.get("pilot_khz", 6.75)))
    if not 0 <= p["pilot_khz"] <= 15:
        E.append("%s.pilot_khz muss 0..15 sein" % ctx)
    pe = p.get("preemphasis_us", d.get("preemphasis_us", 50))
    pe = 0 if pe in (None, False, "off", "aus") else float(pe)
    if pe not in (0, 50, 75):
        W.append("%s.preemphasis_us %s ist kein Normwert (0/50/75)" % (ctx, pe))
    p["preemphasis_us"] = pe
    lim = p.get("limiter", d.get("limiter", True))
    if isinstance(lim, bool):
        lim = {"enable": lim}
    lim = dict(lim or {})
    lim.setdefault("enable", True)
    lim.setdefault("threshold", 1.0)
    lim.setdefault("release_db_s", 3.0)
    lim.setdefault("clip", True)
    p["limiter"] = lim
    p["mpx_power_dbr"] = None if p.get("mpx_power_dbr", d.get("mpx_power_dbr")) in (None, "", False) \
        else float(p.get("mpx_power_dbr", d.get("mpx_power_dbr")))
    p["audio_gain_db"] = float(p.get("audio_gain_db", 0) or 0)
    p["audio_deviation_khz"] = p.get("audio_deviation_khz", "auto")
    p["rds"] = _norm_rds(p.get("rds", d.get("rds")), E, W, ctx + ".rds", tuned_mhz)
    p["audio"] = _norm_audio(p.get("audio", d.get("audio")), E, W, ctx + ".audio")
    return p


def normalize_fm(cfg):
    """Gibt (cfg_norm, errors, warnings) zurueck. rf.freq_hz wird aus fm.freq_mhz gesetzt."""
    E, W = [], []
    c = json.loads(json.dumps(cfg))
    c.setdefault("name", "UKW")
    c["mode"] = "fm"
    fm = c.setdefault("fm", {})
    try:
        f = float(fm.get("freq_mhz", 100.0))
    except (TypeError, ValueError):
        E.append("fm.freq_mhz ungueltig")
        f = 100.0
    if not FM_MIN_MHZ <= f <= FM_MAX_MHZ:
        E.append("fm.freq_mhz muss 87,5..108,0 sein (ist %s)" % f)
    if abs(f * 10 - round(f * 10)) > 1e-6:
        W.append("fm.freq_mhz %.3f liegt nicht im 100-kHz-Raster" % f)
    fm["freq_mhz"] = f
    main = _norm_carrier(fm, E, W, "fm", f)
    fm.update(main)
    sc = dict(fm.get("second_carrier") or {})
    sc.setdefault("enable", False)
    sc["offset_khz"] = float(sc.get("offset_khz", 200.0))
    if sc["enable"] and not 20 <= abs(sc["offset_khz"]) <= 1800:
        E.append("fm.second_carrier.offset_khz: |df| muss 20..1800 kHz sein")
    sc["level_db"] = float(sc.get("level_db", -20.0))
    if sc["level_db"] > 10:
        E.append("fm.second_carrier.level_db hoechstens +10 dB")
    if sc["enable"]:
        f2 = round(f + sc["offset_khz"] / 1000.0, 3)
        sc2 = _norm_carrier(sc, E, W, "fm.second_carrier", f2,
                            defaults={"rds": {"pi": "0xD3C5", "ps": "ZWEITER", "rt": "Zweiter Traeger"},
                                      "audio": {"source": {"kind": "tone", "freq_hz": 400, "level_dbfs": -6}},
                                      "deviation_khz": fm["deviation_khz"], "pilot_khz": fm["pilot_khz"],
                                      "preemphasis_us": fm["preemphasis_us"], "stereo": fm["stereo"]})
        sc.update(sc2)
        sc["freq_mhz"] = f2
    fm["second_carrier"] = sc
    rf = c.setdefault("rf", {})
    rf["freq_hz"] = int(round(f * 1e6))
    rf["channel"] = "fm"
    rf.setdefault("offset_hz", 0)
    rf.setdefault("lo_offset_hz", 0)
    rf.setdefault("sample_rate", 4096000)
    if int(rf["sample_rate"]) != 4096000:
        E.append("rf.sample_rate muss fuer UKW 4096000 sein")
    rf.setdefault("backoff_db", 3.0)
    rf.setdefault("atten_db", 60)
    rf.setdefault("enabled", False)
    try:
        if not 0 <= float(rf["atten_db"]) <= 89.75:
            E.append("rf.atten_db muss 0..89,75 sein")
    except (TypeError, ValueError):
        E.append("rf.atten_db ungueltig")
    c["_capacity"] = {"used_cu": 0, "note": "UKW: keine CU-Bilanz"}
    return c, E, W


# --------------------------------------------------------------------------------------------
# Audioquelle (ffmpeg / Playlist aus dem Runner)
# --------------------------------------------------------------------------------------------
class AudioSource:
    """Liefert Bloecke float32 (n, 2) in [-1, 1] mit 48 kHz. Nutzt host.ffmpeg_cmd bzw. host.PlaylistFeeder."""

    def __init__(self, host, audio_cfg, sess, key="fm"):
        self.host, self.a, self.sess, self.key = host, audio_cfg, sess, key
        self.proc = None
        self.feeder = None
        self.fd = None
        self.eof = False
        self.buf = b""
        src = audio_cfg["source"]
        if src.get("kind") == "playlist":
            r, w = os.pipe()
            host.set_pipe_size(w, 65536)
            pipe = os.fdopen(w, "wb", buffering=0)
            svc = {"key": key, "audio": {"source": src, "samplerate": 48000, "channels": 2}, "pad": {"enable": False}}
            self.feeder = host.PlaylistFeeder(sess, svc, pipe)
            th = threading.Thread(target=self.feeder.run, daemon=True)
            th.start()
            self.fd = r
        else:
            cmd = host.ffmpeg_cmd({"source": src, "channels": 2}, 48000)
            lf = open(os.path.join(sess.sdir, "ffmpeg_%s.log" % key), "ab")
            lf.write(("\n=== %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(cmd))).encode())
            lf.flush()
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf, stdin=subprocess.DEVNULL, close_fds=True)
            lf.close()
            host.set_pipe_size(self.proc.stdout.fileno(), 65536)
            self.fd = self.proc.stdout.fileno()
            sess.procs.append(("ffmpeg_%s" % key, self.proc))

    def read(self, n_frames):
        need = n_frames * 4
        while len(self.buf) < need and not self.eof:
            try:
                d = os.read(self.fd, need - len(self.buf))
            except InterruptedError:
                continue
            except OSError:
                d = b""
            if not d:
                self.eof = True
                self.host.log("warn", "Audioquelle %s beendet, sende Stille" % self.key)
                break
            self.buf += d
        if len(self.buf) < need:
            self.buf += bytes(need - len(self.buf))
        d, self.buf = self.buf[:need], self.buf[need:]
        x = np.frombuffer(d, dtype="<i2").astype(np.float32).reshape(-1, 2) / 32768.0
        return x

    def now_playing(self):
        return self.feeder.now_playing() if self.feeder else None

    def close(self):
        if self.feeder:
            self.feeder.kill()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        try:
            os.close(self.fd)
        except Exception:
            pass


# --------------------------------------------------------------------------------------------
# Sitzung (Zustand, ctl-Dateien)
# --------------------------------------------------------------------------------------------
class FmSession:
    def __init__(self, host, cfg, sid, render=False):
        self.host, self.cfg, self.sid, self.render = host, cfg, sid, render
        self.sdir = os.path.join(host.SESSIONS, sid)
        os.makedirs(self.sdir, exist_ok=True)
        self.stop_evt = threading.Event()
        self.procs = []
        self.feeders = {}
        self.sources = []
        self.carriers = []
        self.encoders = []
        self.ctl_seq = 0
        self.rt_manual = False

    def write_session(self, extra=None):
        d = {"session": self.sid, "pid": os.getpid(), "started": time.time(), "mode": "fm", "running": True,
             "config_name": self.cfg.get("name"), "freq_mhz": self.cfg["fm"]["freq_mhz"],
             "playlist": self.cfg["fm"]["audio"]["source"].get("kind") == "playlist",
             "services": {"fm": {"playlist": self.cfg["fm"]["audio"]["source"].get("kind") == "playlist",
                                 "pad": False, "type": "fm"}},
             "rds": self.encoders[0].snapshot() if self.encoders and self.encoders[0] else None, "render": self.render}
        if extra:
            d.update(extra)
        self.host.atomic_write(os.path.join(self.sdir, "session.json"), json.dumps(d, indent=1, ensure_ascii=False))
        with open(os.path.join(self.sdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=1, ensure_ascii=False)

    def build(self, shift_hz=0.0, immediate_ct=False):
        fm = self.cfg["fm"]
        t0 = time.time() + fm["rds"].get("ct_latency_s", 0.0)
        enc = rdsmod.RdsEncoder(fm["rds"], log=self.host.log, immediate_ct=immediate_ct) if fm["rds"]["enable"] else None
        self.encoders.append(enc)
        c1 = dsp.FmCarrier(fm, enc, freq_offset_hz=float(shift_hz or 0), t0_utc=t0)
        self.carriers.append(c1)
        self.sources.append(AudioSource(self.host, fm["audio"], self, "fm"))
        if self.sources[0].feeder:
            self.feeders["fm"] = self.sources[0].feeder
        sc = fm.get("second_carrier") or {}
        if sc.get("enable"):
            enc2 = rdsmod.RdsEncoder(sc["rds"], log=self.host.log, immediate_ct=immediate_ct) if sc["rds"]["enable"] else None
            self.encoders.append(enc2)
            c2 = dsp.FmCarrier(sc, enc2, freq_offset_hz=sc["offset_khz"] * 1000.0 + float(shift_hz or 0), t0_utc=t0)
            self.carriers.append(c2)
            self.sources.append(AudioSource(self.host, sc["audio"], self, "fm2"))
            if self.sources[1].feeder:
                self.feeders["fm2"] = self.sources[1].feeder
        back = float(self.cfg["rf"].get("backoff_db", 3.0))
        total = 32767.0 * 10 ** (-back / 20.0)
        if len(self.carriers) > 1:
            r = 10 ** (sc["level_db"] / 20.0)
            self.amps = [total / (1 + r), total * r / (1 + r)]
            if sc.get("solo"):            # nur fuer die Pruefung: Zweittraeger allein (Nutztraeger stumm)
                self.amps = [0.0, total]
        else:
            self.amps = [total]
        return self

    def amplitudes_dbfs(self):
        return [round(20 * math.log10(a / 32767.0), 2) if a > 0 else None for a in self.amps]

    def next_block(self):
        cs = []
        for src, car in zip(self.sources, self.carriers):
            audio = src.read(dsp.N_AUDIO)
            cs.append(car.iq_block(audio))
        if len(cs) == 1:
            return dsp.interleave_int16(cs[0][0], cs[0][1], self.amps[0])
        return dsp.interleave_int16(cs[0][0], cs[0][1], self.amps[0], cs[1][0], cs[1][1], self.amps[1])

    # -- Laufzeitaenderungen --
    def poll_ctl(self):
        files = sorted(glob.glob(os.path.join(self.sdir, "ctl_*.json")))
        for fp in files:
            try:
                with open(fp, encoding="utf-8") as f:
                    req = json.load(f)
                os.unlink(fp)
            except Exception:
                continue
            res = {"ok": True, "done": [], "t": time.time()}
            try:
                self.apply_ctl(req, res)
            except Exception as e:
                res = {"ok": False, "error": str(e), "done": res.get("done"), "t": time.time()}
            ack = fp.replace("ctl_", "ack_")
            try:
                self.host.atomic_write(ack, json.dumps(res, ensure_ascii=False))
            except Exception:
                pass
            self.host.log("info", "ctl angewendet", ctl=req, result=res)
            try:
                self.write_session()
            except Exception:
                pass

    def apply_ctl(self, req, res):
        enc = self.encoders[0] if self.encoders else None
        if "rds" in req:
            d = dict(req["rds"])
            target = int(d.pop("carrier", 0) or 0)
            e = self.encoders[target] if target < len(self.encoders) else None
            if e is None:
                raise FmError("RDS auf Traeger %d nicht aktiv" % target)
            upd = {}
            for k in ("ps", "rt", "rtplus", "ta", "tp", "pty", "ms", "ct", "ptyn", "af", "af_method", "af_regional",
                      "lps", "eon", "di", "pin", "ecc", "lic", "eon_ta", "rt_version", "group_version"):
                if k in d:
                    upd[k] = d[k]
            if "rt" in upd and target == 0:
                self.rt_manual = not bool(d.get("rt_auto", False))
            if d.get("rt_auto") and target == 0:
                self.rt_manual = False
            if "pi" in d:
                upd["pi"] = _hexint(d["pi"])
            if "rtplus" in upd and "rtplus_new_item" in d:
                upd["rtplus_new_item"] = bool(d["rtplus_new_item"])
            if "ps" in upd and len(str(upd["ps"])) > 8:
                upd["ps"] = str(upd["ps"])[:8]
            e.update(upd)
            res["done"].append("rds " + ",".join(sorted(upd)))
            res["rds"] = e.snapshot()
        if "fm" in req:
            d = req["fm"]
            car = self.carriers[int(d.get("carrier", 0) or 0)]
            if "audio_gain_db" in d:
                car.gain = 10 ** (float(d["audio_gain_db"]) / 20.0)
            if "mute" in d:
                car.mute_audio = bool(d["mute"])
            if "deviation_khz" in d:
                car.dev = float(d["deviation_khz"])
                car.audio_dev = max(0.0, car.dev - car.pilot - car.rds_level)
            if "pilot_khz" in d and car.stereo:
                car.pilot = float(d["pilot_khz"])
                car.audio_dev = max(0.0, car.dev - car.pilot - car.rds_level)
            if "rds_level_khz" in d and car.rds_on:
                car.rds_level = float(d["rds_level_khz"])
                car.audio_dev = max(0.0, car.dev - car.pilot - car.rds_level)
            if "mpx_power_dbr" in d:
                car.mpx_limit = None if d["mpx_power_dbr"] in (None, "", False) else float(d["mpx_power_dbr"])
                if car.mpx_limit is None:
                    car.mpx_gain = 1.0
            res["done"].append("fm " + ",".join(sorted(k for k in d if k != "carrier")))
            res["carrier"] = car.snapshot()
        if "second_carrier" in req and len(self.carriers) > 1:
            d = req["second_carrier"]
            if "level_db" in d:
                total = sum(self.amps)
                r = 10 ** (float(d["level_db"]) / 20.0)
                self.amps = [total / (1 + r), total * r / (1 + r)]
            if "enable" in d and not d["enable"]:
                self.amps = [sum(self.amps), 0.0]
            res["done"].append("second_carrier")
            res["amplitudes_dbfs"] = self.amplitudes_dbfs()
        if "playlist" in req:
            d = req["playlist"]
            key = d.get("service", "fm")
            fe = self.feeders.get(key)
            if not fe:
                raise FmError("keine Playlist auf %s" % key)
            if d.get("action") not in ("next", "prev", "jump"):
                raise FmError("playlist.action muss next|prev|jump sein")
            self.host.atomic_write(fe.cmdfile, json.dumps({"action": d["action"], "index": d.get("index"), "t": time.time()}))
            res["done"].append("playlist %s" % d["action"])
        if not res["done"]:
            raise FmError("nichts zu tun (erlaubt: rds, fm, second_carrier, playlist)")

    def track_tags(self):
        """RT/RT+ aus den Playlist-Tags beim Titelwechsel (wenn rt_from_tags und kein manuelles RT)."""
        src = self.sources[0]
        if not src.feeder or not self.encoders or self.encoders[0] is None or self.rt_manual:
            return
        if not self.cfg["fm"]["rds"].get("rt_from_tags", True):
            return
        npw = src.feeder.now_playing()
        idx = npw.get("index")
        if idx is None or idx == getattr(self, "_last_idx", None):
            return
        self._last_idx = idx
        artist, title = (npw.get("artist") or "").strip(), (npw.get("title") or "").strip()
        text = ("%s - %s" % (artist, title)) if artist else title
        tags = []
        if artist:
            tags.append({"type": "ITEM.ARTIST", "text": artist})
        if title:
            tags.append({"type": "ITEM.TITLE", "text": title})
        self.encoders[0].update({"rt": text, "rtplus": tags, "rtplus_new_item": True})
        self.host.log("info", "RDS RT aus Tags", rt=text, rtplus=tags)

    def status(self):
        d = {"carriers": [c.snapshot() for c in self.carriers], "amplitudes_dbfs": self.amplitudes_dbfs()}
        if self.feeders:
            d["now_playing"] = [f.now_playing() for f in self.feeders.values()]
        return d

    def stop(self):
        self.stop_evt.set()
        for s in self.sources:
            s.close()
        for name, p in self.procs:
            if p.poll() is None:
                try:
                    p.kill()
                    p.wait(timeout=2)
                except Exception:
                    pass
        try:
            with open(os.path.join(self.sdir, "session.json")) as f:
                d = json.load(f)
            d["running"] = False
            d["stopped"] = time.time()
            self.host.atomic_write(os.path.join(self.sdir, "session.json"), json.dumps(d, indent=1, ensure_ascii=False))
        except Exception:
            pass


def write_all(fd, data, stop_evt):
    """Nicht blockierend mit select schreiben (wie relay() im Runner), damit SIGTERM auch bei stehendem Leser wirkt.
    Rueckgabe: None = fertig, sonst Grund fuer das Ende."""
    mv = memoryview(data)
    while mv:
        try:
            _, wr, _ = select.select([], [fd], [], 0.5)
        except InterruptedError:
            wr = []
        if stop_evt.is_set():
            return "stop"
        if not wr:
            continue
        try:
            k = os.write(fd, mv)
        except BlockingIOError:
            continue
        except BrokenPipeError:
            return "stdout geschlossen (EPIPE)"
        except OSError as e:
            if e.errno in (errno.EPIPE, errno.EBADF):
                return "stdout geschlossen"
            if e.errno in (errno.EINTR, errno.EAGAIN):
                continue
            raise
        mv = mv[k:]
    return None


# --------------------------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------------------------
def cmd_live(args, host, cfg, W):
    sid = host.new_session_id()
    sys.stderr.write(json.dumps({"session": sid}) + "\n")
    sys.stderr.flush()
    for w in W:
        host.log("warn", w)
    sess = FmSession(host, cfg, sid)
    host._log_file = open(os.path.join(sess.sdir, "runner.log"), "a", encoding="utf-8")
    stop = {"why": None}

    def on_sig(signum, frame):
        stop["why"] = "signal %d" % signum
        sess.stop_evt.set()

    for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sg, on_sig)
    rc = 0
    try:
        sess.build(shift_hz=getattr(args, "shift_hz", 0) or 0)
        sess.write_session({"rate": dsp.FS_OUT})
        fm = cfg["fm"]
        host.log("info", "UKW-Kette gestartet: %.1f MHz, Hub %.1f kHz, %s, Pilot %.2f kHz, RDS %s, Preemphasis %s us" %
                 (fm["freq_mhz"], fm["deviation_khz"], "Stereo" if fm["stereo"] else "Mono", fm["pilot_khz"] if fm["stereo"] else 0,
                  ("%.1f kHz, PI 0x%04X, PS '%s'" % (fm["rds"]["level_khz"], fm["rds"]["pi"], fm["rds"]["ps"])) if fm["rds"]["enable"] else "aus",
                  fm["preemphasis_us"]), session=sid, amplitudes_dbfs=sess.amplitudes_dbfs(),
                 second_carrier=({"offset_khz": fm["second_carrier"]["offset_khz"], "level_db": fm["second_carrier"]["level_db"]}
                                 if fm["second_carrier"].get("enable") else None))
        out_fd = sys.stdout.fileno()
        try:
            os.set_blocking(out_fd, False)
        except OSError:
            pass
        host.set_pipe_size(out_fd, 1 << 20)
        t0 = time.time()
        cpu0 = time.process_time()
        last_rep, last_bytes, total = t0, 0, 0
        stat_acc = []
        nblk = 0
        while not sess.stop_evt.is_set():
            data = sess.next_block()
            why = write_all(out_fd, data, sess.stop_evt)
            if why:
                stop["why"] = stop["why"] or why
                break
            total += len(data)
            nblk += 1
            if nblk % 16 == 0 and len(stat_acc) < 4:
                stat_acc.append(data)
            if nblk % 4 == 0:
                sess.poll_ctl()
                sess.track_tags()
            now = time.time()
            if now - last_rep >= 5:
                st = host.level_stats(b"".join(stat_acc)) if stat_acc else {}
                stat_acc = []
                cpu1 = time.process_time()
                host.log("info", "Status", samples=total // 4, seconds=round(now - t0, 1),
                         rate_sps=round((total - last_bytes) / 4.0 / (now - last_rep)),
                         rate_avg_sps=round(total / 4.0 / (now - t0)), level=st,
                         cpu_pct=round(100.0 * (cpu1 - cpu0) / max(1e-6, now - last_rep), 1), **sess.status())
                cpu0 = cpu1
                last_rep, last_bytes = now, total
    except (FmError, host.RunnerError) as e:
        host.log("error", str(e))
        rc = 2
    except Exception as e:
        import traceback
        host.log("error", "Ausnahme: %r" % e, trace=traceback.format_exc()[-1500:])
        rc = 1
    finally:
        sess.stop()
        host.log("info", "beendet (%s)" % (stop["why"] or "Ende"))
    return rc


# --------------------------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------------------------
def render(host, cfg, seconds, out, rate=4096000, fmt="s16", immediate_ct=True, shift_hz=0.0):
    rate = int(rate)
    if rate not in (4096000, 2048000):
        raise FmError("render: --rate muss 4096000 oder 2048000 sein")
    sid = host.new_session_id() + "-r"
    sess = FmSession(host, cfg, sid, render=True)
    t0 = time.time()
    try:
        sess.build(shift_hz=shift_hz, immediate_ct=immediate_ct)
        sess.write_session({"rate": rate})
        dec = None
        if rate == 2048000:
            dec = dsp.Resampler(1, 2, np.array(host.halfband_taps(63)), channels=1)
        nblk = int(math.ceil(seconds * dsp.FS_OUT / dsp.N_OUT))
        written = 0
        stats_parts = []
        with open(out, "wb") as fo:
            for i in range(nblk):
                data = sess.next_block()
                if i % 8 == 0 and len(stats_parts) < 32:
                    stats_parts.append(data)
                if fmt == "cf32" or dec is not None:
                    x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
                    c = x[0::2] + 1j * x[1::2]
                    if dec is not None:
                        c = dec.process(c.astype(np.complex128)).astype(np.complex64)
                    y = np.empty(2 * len(c), np.float32)
                    y[0::2], y[1::2] = c.real, c.imag
                    if fmt == "cf32":
                        fo.write(y.astype("<f4").tobytes())
                    else:
                        fo.write(np.clip(np.round(y * 32768.0), -32768, 32767).astype("<i2").tobytes())
                    written += len(c)
                else:
                    fo.write(data)
                    written += len(data) // 4
                if i % 4 == 3:
                    sess.track_tags()
        st = host.level_stats(b"".join(stats_parts)) if stats_parts else {}
        res = {"ok": True, "out": os.path.abspath(out), "format": fmt, "rate": rate, "seconds": seconds,
               "samples": written, "bytes": os.path.getsize(out), "elapsed_s": round(time.time() - t0, 2),
               "realtime_factor": round(written / float(rate) / max(1e-6, time.time() - t0), 2),
               "level_s16": st, "session": sid, "amplitudes_dbfs": sess.amplitudes_dbfs(),
               "carriers": [c.snapshot() for c in sess.carriers],
               "rds": [e.snapshot() if e else None for e in sess.encoders]}
        return res
    finally:
        sess.stop()


def cmd_render(args, host, cfg, W):
    res = render(host, cfg, float(args.seconds), args.out, int(args.rate), args.format,
                 shift_hz=getattr(args, "shift_hz", 0) or 0)
    res["warnings"] = W
    host.emit_json(res)
    return 0 if res["ok"] else 1


# --------------------------------------------------------------------------------------------
# check / decode
# --------------------------------------------------------------------------------------------
def cmd_check(args, host, cfg, W):
    from . import fm_check
    import random
    seconds = float(args.seconds)
    os.makedirs(os.path.join(host.RUNTIME, "checks"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(host.TMPROOT, "dablab-fmcheck-%s-%04x.s16" % (stamp, random.randint(0, 0xFFFF)))
    t0 = time.time()
    r = render(host, cfg, seconds, out, 4096000, "s16")
    sc_on = bool(cfg["fm"].get("second_carrier", {}).get("enable"))
    rep = fm_check.analyze(out, 4096000, "s16", cfg=cfg, seconds=seconds, host=host, rds_sent=r.get("rds"),
                           interference=sc_on, ct_expected=True)
    rep["render"] = {k: r[k] for k in ("seconds", "samples", "elapsed_s", "realtime_factor", "level_s16", "carriers",
                                       "amplitudes_dbfs")}
    if sc_on:
        # Zweittraeger allein rendern und pruefen (im Summensignal ueberlagern sich die Traeger physikalisch)
        out2 = out.replace(".s16", "-solo.s16")
        cfg2 = json.loads(json.dumps(cfg))
        cfg2["fm"]["second_carrier"]["solo"] = True
        try:
            r2 = render(host, cfg2, min(seconds, 12.0), out2, 4096000, "s16")
            rep["second_carrier_solo"] = fm_check.analyze(out2, 4096000, "s16", cfg=cfg2, seconds=min(seconds, 12.0), host=host,
                                                          shift_hz=cfg["fm"]["second_carrier"]["offset_khz"] * 1000.0,
                                                          which=1, ct_expected=True)
            rep["second_carrier_solo"]["render"] = {k: r2[k] for k in ("seconds", "elapsed_s", "realtime_factor")}
            rep["mismatches"] += ["Zweittraeger solo: " + m for m in rep["second_carrier_solo"].get("mismatches", [])]
            rep["ok"] = not rep["mismatches"]
        except Exception as e:
            rep["second_carrier_solo"] = {"ok": False, "error": repr(e)}
        finally:
            if not getattr(args, "keep", False):
                try:
                    os.unlink(out2)
                except Exception:
                    pass
    rep["warnings"] = W
    rep["elapsed_s"] = round(time.time() - t0, 1)
    repdir = os.path.join(host.RUNTIME, "checks")
    try:
        with open(os.path.join(repdir, "fmcheck-%s.json" % stamp), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, ensure_ascii=False)
    except Exception:
        pass
    if getattr(args, "keep", False):
        rep["iq_file"] = out
    else:
        try:
            os.unlink(out)
        except Exception:
            pass
    host.emit_json(rep)
    return 0 if rep.get("ok") else 1


def cmd_decode(args, host, cfg):
    from . import fm_check
    path = os.path.abspath(args.iq)
    fmt = getattr(args, "format", None)
    rate = getattr(args, "rate", None)
    if not fmt:
        fmt = "s16" if path.endswith(".s16") or path.endswith(".s16.iq") else "cf32"
    if not rate:
        rate = 4096000 if fmt == "s16" else 2048000
    rep = fm_check.analyze(path, int(rate), fmt, cfg=cfg, seconds=float(args.seconds or 20), host=host,
                           shift_hz=float(getattr(args, "shift_hz", 0) or 0))
    host.emit_json(rep)
    return 0 if rep.get("ok") else 1


# --------------------------------------------------------------------------------------------
# ctl
# --------------------------------------------------------------------------------------------
def cmd_ctl(args, host, sess, sdir):
    try:
        req = json.loads(args.json)
    except Exception as e:
        host.emit_json({"ok": False, "error": "JSON ungueltig: %s" % e})
        return 2
    if not sess.get("running") or not host._pid_alive(sess.get("pid")):
        host.emit_json({"ok": False, "error": "Sitzung laeuft nicht mehr"})
        return 2
    # Kompatibilitaet zu DAB-Schluesseln: dls -> rds.rt (+ RT+ aus dlplus), pty -> rds.pty, label -> rds.ps
    if "dls" in req:
        d = req.pop("dls")
        r = req.setdefault("rds", {})
        r["rt"] = d.get("text", "")
        dlp = d.get("dlplus") or {}
        tags = [{"type": "ITEM.ARTIST", "text": dlp["artist"]} for _ in [0] if dlp.get("artist")] + \
               [{"type": "ITEM.TITLE", "text": dlp["title"]} for _ in [0] if dlp.get("title")]
        if tags:
            r["rtplus"] = tags
    if "pty" in req and isinstance(req["pty"], dict):
        req.setdefault("rds", {})["pty"] = int(req.pop("pty")["pty"])
    if "label" in req:
        req.setdefault("rds", {})["ps"] = str(req.pop("label").get("label", ""))[:8]
    name = "ctl_%d_%06d.json" % (int(time.time() * 1000), os.getpid() % 1000000)
    fp = os.path.join(sdir, name)
    host.atomic_write(fp, json.dumps(req, ensure_ascii=False))
    ack = fp.replace("ctl_", "ack_")
    deadline = time.time() + 4.0
    res = None
    while time.time() < deadline:
        if os.path.exists(ack):
            try:
                with open(ack, encoding="utf-8") as f:
                    res = json.load(f)
                os.unlink(ack)
            except Exception:
                res = None
            break
        time.sleep(0.05)
    if res is None:
        res = {"ok": True, "pending": True, "note": "Befehl abgelegt, Prozess hat noch nicht bestaetigt"}
    host.emit_json(res)
    return 0 if res.get("ok") else 1


# --------------------------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------------------------
def selftest(host, quick=False):
    import scipy
    from . import fm_check
    R = {"ok": True, "numpy": np.__version__, "scipy": scipy.__version__}
    p = host.tool("redsea", required=False)
    R["redsea"] = {"path": p, "version": host._ver([p, "--version"]) if p else None}
    R["rds"] = rdsmod.selftest()
    R["dsp"] = dsp.selftest()
    R["ok"] = bool(R["rds"]["ok"] and R["dsp"]["ok"] and p)
    if not quick:
        # kurze interne Schleife: 4 s Signal erzeugen -> demodulieren -> Pilot/Hub/RDS messen
        try:
            cfg, E, W = normalize_fm({"fm": {"freq_mhz": 100.0, "audio": {"source": {"kind": "tone", "freq_hz": 1000, "level_dbfs": -0.4}},
                                             "rds": {"pi": "0xD3C4", "ps": "SELFTEST", "rt": "Selbsttest"}}})
            enc = rdsmod.RdsEncoder(cfg["fm"]["rds"], immediate_ct=True)
            car = dsp.FmCarrier(cfg["fm"], enc, t0_utc=time.time())
            n = int(4.0 * dsp.FS_AUDIO / dsp.N_AUDIO)
            t = np.arange(dsp.N_AUDIO) / dsp.FS_AUDIO
            chunks = []
            for i in range(n):
                tone = (10 ** (-0.4 / 20.0)) * np.sin(2 * math.pi * 1000 * (t + i * dsp.N_AUDIO / dsp.FS_AUDIO))
                c, s = car.iq_block(np.stack([tone, tone], axis=1))
                chunks.append((c + 1j * s).astype(np.complex64))
            iq = np.concatenate(chunks)
            m = fm_check.measure_iq(iq, dsp.FS_OUT, cfg=cfg, host=host, want_rds=bool(p))
            R["loop"] = {k: m.get(k) for k in ("deviation", "pilot", "rds", "audio", "redsea") if k in m}
            ok = abs(m["pilot"]["level_khz"] - 6.75) / 6.75 < 0.05 and abs(m["deviation"]["total_peak_khz"] - 75) / 75 < 0.03
            if p:
                ok = ok and m.get("redsea", {}).get("pi") == "0xD3C4" and m["redsea"].get("ps") == "SELFTEST"
            R["loop"]["ok"] = ok
            R["ok"] = R["ok"] and ok
        except Exception as e:
            import traceback
            R["loop"] = {"ok": False, "error": repr(e), "trace": traceback.format_exc()[-1200:]}
            R["ok"] = False
    return R


# --------------------------------------------------------------------------------------------
# Einstieg aus dablab_run.py
# --------------------------------------------------------------------------------------------
def dispatch(args, host, cfg_raw):
    cfg, E, W = normalize_fm(cfg_raw)
    if args.cmd == "validate":
        host.emit_json({"ok": not E, "errors": E, "warnings": W, "mode": "fm", "rf": cfg["rf"]})
        return 0 if not E else 2
    if E:
        if args.cmd == "live":
            sys.stderr.write(json.dumps({"session": "invalid"}) + "\n")
            for e in E:
                host.log("error", e)
            host.log("error", "Konfiguration ungueltig (%d Fehler)" % len(E), errors=E)
        else:
            host.emit_json({"ok": False, "errors": E, "warnings": W})
        return 2
    if args.cmd == "live":
        return cmd_live(args, host, cfg, W)
    if args.cmd == "render":
        return cmd_render(args, host, cfg, W)
    if args.cmd == "check":
        return cmd_check(args, host, cfg, W)
    if args.cmd == "decode":
        return cmd_decode(args, host, cfg)
    raise FmError("Befehl %s nicht fuer mode fm" % args.cmd)
