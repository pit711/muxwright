# -*- coding: utf-8 -*-
"""Prozesskette: ffmpeg (Mux, PAT/PMT, muxrate) -> tsp (SDT/NIT/EIT/TDT/TOT, PCR, Regelung) -> Ausgabe."""
import glob
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import si, sources, nvenc
from . import tables as T

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

NICE = 10
THREADS = 4
MOD_DIR_ENV = "DABLAB_DVB_MOD_DIR"


def _nice():
    try:
        os.nice(NICE)
    except Exception:
        pass


def find_modulator(host, std):
    """Pfad zum lokalen Modulator (pluto_fw/mod/dvbt_mod bzw. dvbc_mod), falls vorhanden."""
    name = "%s_mod" % std
    cands = []
    if os.environ.get(MOD_DIR_ENV):
        cands.append(os.path.join(os.environ[MOD_DIR_ENV], name))
    base = os.path.abspath(os.path.join(host.SCRIPT_DIR, "..", "pluto_fw", "mod"))
    # x86-Build in WSL (/opt/dablab/mod/src, siehe pluto_fw/mod/README.md), dann der Quellordner (falls dort gebaut)
    cands += ["/opt/dablab/mod/src/" + name, os.path.join(base, name)]
    w = shutil.which(name)
    if w:
        cands.append(w)
    for c in cands:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def modulator_cmd(host, cfg):
    """Kommandozeile des Modulators: Vorlage aus output.modulator_cmd (Liste mit Platzhaltern) oder Standard.
    Schnittstelle (Pipe): TS 188 Byte auf stdin, IQ int16 LE interleaved auf stdout mit rf.sample_rate."""
    std = cfg["mode"]
    d = cfg[std]
    s2 = std == "dvbs" and d.get("system") == "dvbs2"
    subst = {"standard": std, "ts_bitrate": cfg["_rate_str"], "muxrate": str(cfg["_muxrate"]),
             "sample_rate": str(cfg["rf"].get("sample_rate")), "freq_hz": str(cfg["rf"]["freq_hz"]),
             "backoff_db": str(cfg["rf"].get("backoff_db", 12))}
    if std == "dvbt":
        subst.update({"mode": d["mode"], "constellation": d["constellation"], "code_rate": d["code_rate"],
                      "guard": d["guard"], "bandwidth_mhz": str(d["bandwidth_mhz"])})
    elif std == "dvbs":
        subst.update({"system": d.get("system", "dvbs"), "modulation": d.get("modulation", "QPSK"), "code_rate": d["code_rate"],
                      "symbol_rate": str(d["symbol_rate"]), "roll_off": d["roll_off"], "pilots": "1" if d.get("pilots") else "0"})
    else:
        subst.update({"constellation": d["constellation"], "symbol_rate": str(d["symbol_rate"])})
    tmpl = cfg["output"].get("modulator_cmd")
    if tmpl:
        if isinstance(tmpl, str):
            tmpl = tmpl.split()
        return [str(x).format(**subst) for x in tmpl]
    name = "dvbs2" if s2 else std
    exe = find_modulator(host, name)
    if not exe:
        raise host.RunnerError("Kein Modulator gefunden (/opt/dablab/mod/src/%s_mod, pluto_fw/mod/%s_mod oder %s); "
                               "output.modulator_cmd setzen" % (name, name, MOD_DIR_ENV))
    # CLI laut pluto_fw/mod/README.md: TS auf stdin (-i -), IQ int16 LE interleaved auf stdout (-o -)
    cmd = [exe, "-i", "-", "-o", "-"]
    if std == "dvbt":
        const = {"QPSK": "qpsk", "16-QAM": "16qam", "64-QAM": "64qam"}[d["constellation"]]
        cmd += ["--mode", d["mode"], "--const", const, "--rate", d["code_rate"], "--guard", d["guard"],
                "--bw", str(d["bandwidth_mhz"]), "--backoff", str(cfg["rf"].get("backoff_db", 12))]
        if d.get("cell_id"):
            cmd += ["--cell-id", str(int(d["cell_id"]))]
    elif std == "dvbs":
        # am PC immer 2 Samples/Symbol (RRC im Programm); 1 Sample/Symbol braucht das AD9361-FIR (nur Gerätepfad)
        cmd += ["--sr", str(d["symbol_rate"]), "--rate", d["code_rate"], "--sps", str(d.get("sps") or 2),
                "--backoff", str(cfg["rf"].get("backoff_db", 10))]
        if s2:
            cmd += ["--mod", d["modulation"].lower(), "--pilots", "1" if d.get("pilots") else "0", "--rolloff", d["roll_off"]]
    else:
        cmd += ["--qam", d["constellation"].split("-")[0], "--sr", str(d["symbol_rate"]),
                "--sps", str(d.get("sps", 2)), "--backoff", str(cfg["rf"].get("backoff_db", 10))]
    return cmd


class DvbChain:
    def __init__(self, host, cfg, session_id, log, render=False):
        self.host, self.cfg, self.sid, self.log, self.render = host, cfg, session_id, log, render
        self.sdir = os.path.join(host.SESSIONS, session_id)
        os.makedirs(os.path.join(self.sdir, "eit"), exist_ok=True)
        self.work = tempfile.mkdtemp(prefix="dvb-%s-" % session_id[-6:], dir=host.TMPROOT)
        self.procs = []
        self.feeders = {}
        self.stop_evt = threading.Event()
        self.threads = []
        self.out_bytes = 0
        self.out_t0 = None
        self.rate_win = []
        self.level = None
        self.versions = {"nit": 1, "sdt": 1}
        self.names = {}
        self.ffmpeg = self.tsp = self.mod = self.preview = None
        self.out_fd = None
        self.relay_thread = None
        self.win_procs = []          # Windows-Prozesse (NVENC, dshow-Ton)
        self.win_started = []        # (Name, Startzeit, info) - Wachhund: erste Daten innerhalb von 30 s
        self.pass_fds = []           # Pipes vom NVENC-Relais in den Mux-ffmpeg
        self.close_after = []

    # ------------------------------------------------------------------ Prozesse
    def spawn(self, name, cmd, stdin=None, stdout=None, pass_fds=()):
        logpath = os.path.join(self.sdir, name + ".log")
        lf = open(logpath, "ab")
        lf.write(("\n=== %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(cmd))).encode())
        lf.flush()
        p = subprocess.Popen(cmd, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                             stdout=stdout if stdout is not None else lf, stderr=subprocess.PIPE, cwd=self.work,
                             close_fds=True, pass_fds=pass_fds, preexec_fn=_nice)
        t = threading.Thread(target=self._pump, args=(name, p, lf), daemon=True)
        t.start()
        self.threads.append(t)
        self.procs.append((name, p))
        return p

    def _pump(self, name, p, lf):
        n = 0
        try:
            for line in iter(p.stderr.readline, b""):
                lf.write(line)
                lf.flush()
                s = line.decode("utf-8", "replace").strip()
                if not s or self.stop_evt.is_set():
                    continue
                low = s.lower()
                if name == "tsp" and "count:" in low:
                    self._tsp_count(s)
                    continue
                if any(k in low for k in ("error", "fail", "invalid", "cannot", "no such", "overflow", "underflow",
                                          "non-monotonic", "dts < pcr", "too many packets", "unknown")) and n < 60:
                    n += 1
                    self.log("warn", "%s: %s" % (name, s[:300]))
        except Exception:
            pass
        finally:
            try:
                lf.close()
            except Exception:
                pass

    _COUNT_RE = re.compile(r"total:\s*([\d,]+)\s*packets")

    def _tsp_count(self, s):
        # "* count: <Zeit>, counted: N packets, R b/s, total: N packets, R b/s" (tsp -P count --interval)
        m = self._COUNT_RE.search(s)
        if m:
            self.out_bytes = int(m.group(1).replace(",", "")) * 188
        m = re.search(r"counted:\s*[\d,]+\s*packets,\s*([\d,]+)\s*b/s", s)
        if m and self.out_bytes > 0:
            self.udp_rate = int(m.group(1).replace(",", ""))

    # ------------------------------------------------------------------ Aufbau
    def write_tables(self):
        c = self.cfg
        if T.is_psip(c):
            si.write_psip(self.host, self.sdir, c)
            return
        self.host.atomic_write(os.path.join(self.sdir, "nit.xml"), si.nit_xml(c, self.versions["nit"]))
        self.host.atomic_write(os.path.join(self.sdir, "sdt.xml"), si.sdt_xml(c, self.versions["sdt"], self.names))
        self.host.atomic_write(os.path.join(self.sdir, "time.xml"), si.time_xml(c))
        for s in c["services"]:
            # Playlist: Startwert aus der Konfiguration, der Feeder ueberschreibt ihn beim ersten Titel (neue Version)
            e = s["eit"]
            fol = {"title": e["next_title"], "text": e.get("next_text") or ""} if e.get("next_title") else None
            si.write_eit(self.host, os.path.join(self.sdir, "eit", "eit_%s.xml" % s["key"]), c, s,
                         {"title": e["title"], "text": e["text"]}, fol)

    def ffmpeg_cmd(self, seconds=None):
        c = self.cfg
        ff = self.host.tool("ffmpeg")
        cmd = [ff, "-nostdin", "-hide_banner", "-loglevel", "warning", "-nostats", "-threads", str(THREADS)]
        inputs, fc, maps, codec_opts, streamids, programs = [], [], [], [], [], []
        base = 0
        out_idx = 0
        vi = ai = 0  # Zaehler je Typ fuer Stream-Spezifizierer v:N / a:N
        for i, s in enumerate(c["services"]):
            v = s.get("video") or {}
            use_nv = s["type"] == "tv" and v.get("encoder") in ("nvenc", "amf")
            use_ts = s["source"]["kind"] == "ts"
            if use_nv:
                src = nvenc.build(self, s)
                src["feeder"] = None
                self.pass_fds += src["pass_fds"]
                self.close_after += src["close_after_spawn"]
                self.log("info", "%s-Encoder gestartet (%s)" % ("AMF" if v["encoder"] == "amf" else "NVENC", s["key"]), nvenc=src["info"])
            elif use_ts:
                # fertiger Transportstrom: Programm unveraendert uebernehmen (Video/Ton kopiert), endlos
                path = self.host.media_path(s["source"]["file"])
                src = {"inputs": [(["-stream_loop", "-1"] if s["source"].get("loop", True) else []) + ["-i", path]],
                       "vin": 0, "ain": 0, "feeder": None, "info": {"kind": "ts", "file": path,
                                                                    "program": s["source"]["program"]}}
            else:
                src = sources.build_source(self.host, c, s, self.work, self.sdir, self.log, render=self.render)
            s["_source_info"] = src["info"]
            if src["feeder"]:
                self.feeders[s["key"]] = src["feeder"]
            for a in src["inputs"]:
                inputs += a
            st = []
            if s["type"] == "tv" and (use_nv or use_ts):
                if use_ts:
                    maps += ["-map", "%d:p:%d:v:0" % (base, s["source"]["program"])]
                else:
                    maps += ["-map", "%d:v:0" % (base + src["vin"])]
                codec_opts += ["-c:v:%d" % vi, "copy"]
                streamids += ["-streamid", "%d:%d" % (out_idx, s["_vpid"])]
                st.append(out_idx)
                out_idx += 1
                vi += 1
            elif s["type"] == "tv":
                fc.append("[%d:v:0]%s[v%d]" % (base + src["vin"], src["vf"], i))
                maps += ["-map", "[v%d]" % i]
                v = s["video"]
                br = int(float(v["bitrate_kbps"]) * 1000)
                sp = "v:%d" % vi
                low = s["source"]["kind"] == "folder"    # Infoscreen: ohne B-Bilder/Lookahead (geringe Latenz)
                o = ["-c:" + sp]
                if v["codec"] == "mpeg2":
                    o += ["mpeg2video", "-b:" + sp, str(br), "-maxrate:" + sp, str(int(br * 1.15)),
                          "-bufsize:" + sp, str(9781248 if v["_hd"] else 1835008), "-g:" + sp, str(v["gop"]),
                          "-bf:" + sp, "0" if low else "2"]
                    if v.get("interlaced"):
                        o += ["-flags:" + sp, "+ilme+ildct", "-top:" + sp, "1", "-alternate_scan:" + sp, "1"]
                elif v["codec"] == "hevc":
                    o += ["libx265", "-preset:" + sp, v.get("preset", "veryfast"), "-profile:" + sp, "main",
                          "-b:" + sp, str(br), "-tag:" + sp, "hvc1",
                          "-x265-params:" + sp, "pools=%d:vbv-maxrate=%d:vbv-bufsize=%d:keyint=%d:min-keyint=%d:scenecut=0:"
                          "repeat-headers=1:aud=1:hrd=1:log-level=error" % (THREADS, br // 1000, br // 2000, v["gop"], v["gop"])
                          + (":bframes=0:rc-lookahead=0:frame-threads=1" if low else "")]
                else:
                    o += ["libx264", "-preset:" + sp, v.get("preset", "veryfast"),
                          "-profile:" + sp, "high" if v["_hd"] else "main", "-level:" + sp, "4.0" if v["_hd"] else "3.0",
                          "-b:" + sp, str(br), "-maxrate:" + sp, str(int(br * 1.1)), "-bufsize:" + sp, str(br * 2),
                          "-g:" + sp, str(v["gop"]), "-keyint_min:" + sp, str(v["gop"]), "-sc_threshold:" + sp, "0",
                          "-x264-params:" + sp, "nal-hrd=vbr:force-cfr=1:aud=1:threads=%d" % THREADS
                          + (":bframes=0:rc-lookahead=0:sync-lookahead=0:sliced-threads=1" if low else "")]
                o += ["-aspect:" + sp, v.get("aspect", "16:9"), "-r:" + sp, str(v["fps"])]
                codec_opts += o
                streamids += ["-streamid", "%d:%d" % (out_idx, s["_vpid"])]
                st.append(out_idx)
                out_idx += 1
                vi += 1
            a = s["audio"]
            sp = "a:%d" % ai
            if a["codec"] != "none":     # "none": Programm ohne Tonspur, eine erzeugte Tonquelle bleibt ungenutzt
                if use_ts:
                    maps += ["-map", "%d:p:%d:a:0" % (base, s["source"]["program"])]
                    o = ["-c:" + sp, "copy", "-metadata:s:%d" % out_idx, "language=%s" % a.get("language", "deu")]
                else:
                    fc.append("[%d:a:0]%s[a%d]" % (base + src["ain"], src["af"] or "anull", i))
                    maps += ["-map", "[a%d]" % i]
                    o = ["-c:" + sp, {"mp2": "mp2", "aac": "aac", "ac3": "ac3"}[a["codec"]], "-b:" + sp, "%dk" % a["bitrate_kbps"],
                         "-ac:" + sp, str(a["channels"]), "-ar:" + sp, str(a["samplerate"]),
                         "-metadata:s:%d" % out_idx, "language=%s" % a.get("language", "deu")]
                codec_opts += o
                streamids += ["-streamid", "%d:%d" % (out_idx, s["_apid"])]
                st.append(out_idx)
                out_idx += 1
                ai += 1
            prog = "title=%s:program_num=%d" % (s["name"].replace(":", " ").replace("=", " "), s["service_id"])
            for k in st:
                prog += ":st=%d" % k
            programs += ["-program", prog, "-metadata:p:%d" % i, "service_provider=%s" % s["provider"],
                         "-metadata:p:%d" % i, "service_name=%s" % s["name"]]
            base += len(src["inputs"])
        ts = c["ts"]
        cmd += inputs + (["-filter_complex", ";".join(fc)] if fc else []) + maps + codec_opts + streamids + programs
        if seconds:
            cmd += ["-t", "%.3f" % float(seconds)]
        # DATV (Nutzrate < 2 Mbit/s): PAT/PMT nicht zu jedem Bild, sondern alle 0,4 s, sonst frisst die SI ~ 75 kbit/s
        small = c["_rate_bps"] < 2e6
        # Infoscreen (Bildordner): Mux-Vorlauf 0,2 s statt 0,5 s
        delay = "0.2" if any(s["source"]["kind"] == "folder" for s in c["services"]) else "0.5"
        cmd += ["-mpegts_transport_stream_id", str(ts["transport_stream_id"]),
                "-mpegts_original_network_id", str(ts["original_network_id"]),
                "-mpegts_pmt_start_pid", str(ts["pmt_start_pid"]), "-mpegts_start_pid", str(ts["start_pid"]),
                "-mpegts_flags", self._mpegts_flags(small), "-muxrate", str(c["_muxrate"]),
                "-pcr_period", str(ts.get("pcr_period_ms", 30)), "-pat_period", "0.4" if small else "0.1",
                "-sdt_period", "1" if small else "0.25", "-nit_period", "1" if small else "0.25", "-tables_version", str(ts.get("tables_version", 1) % 32),
                "-max_delay", str(int(float(delay) * 1e6)), "-muxpreload", delay, "-muxdelay", delay,
                "-f", "mpegts", "pipe:1"]
        return cmd

    def _mpegts_flags(self, small):
        c = self.cfg
        if T.is_psip(c):                 # ATSC-Systemschicht: AC-3 als stream_type 0x81, keine NIT
            return "+pat_pmt_at_frames"
        f = "+nit+system_b" if small else "+nit+pat_pmt_at_frames+system_b"
        if c["mode"] == "isdbt" and c["isdbt"].get("region") != "jp":
            f += "+latm"                 # ISDB-Tb: AAC in LATM/LOAS (stream_type 0x11); Japan: ADTS
        return f

    def _stt_loop(self):
        while not self.stop_evt.wait(1.0):
            try:
                si.write_stt(self.sdir)
            except OSError:
                pass

    def _tsp_buffer(self):
        """tsp puffert sonst 16 MB (bei 25 Mbit/s ~ 5 s). Mit Bildordner-Quelle (Infoscreen): ~ 0,4 s Transportstrom."""
        if not any(s["source"]["kind"] == "folder" for s in self.cfg["services"]):
            return []
        return ["--buffer-size-mb", "%.3f" % max(0.05, self.cfg["_rate_bps"] * 0.4 / 8 / 1e6)]

    def tsp_cmd(self, output, regulate):
        c, ts = self.cfg, self.cfg["ts"]
        tsp = self.host.tool("tsp")
        R = c["_rate_str"]
        if T.is_psip(c):
            # ffmpeg schreibt immer eine SDT: durch Nullpakete ersetzen; PSIP auf der Basis-PID 0x1FFB (A/65: MGT <= 150 ms,
            # VCT <= 400 ms, STT 1 s)
            cmd = [tsp, "--bitrate", R] + self._tsp_buffer() + ["-I", "file", "-P", "filter", "--negate", "--pid", "17", "--stuffing",
                   "-P", "inject", "--pid", "8187", "--bitrate", "40000", "--stuffing", "--poll-files",
                   os.path.join(self.sdir, "mgt.bin") + "=100", os.path.join(self.sdir, "vct.bin") + "=300",
                   os.path.join(self.sdir, "stt.bin") + "=1000",
                   "-P", "pcradjust", "--bitrate", R]
            if regulate:
                cmd += ["-P", "regulate", "--bitrate", R, "--packet-burst", "16"]
            if c["output"]["kind"] == "udp" and not self.render:
                cmd += ["-P", "count", "--interval", str(max(1000, int(c["_rate_bps"] / 8 / 188 * 5)))]
            return cmd + output
        remap = ["%d=%d" % (s["_pmt_pid"], s["_pmt_pid_out"]) for s in c["services"] if s.get("_pmt_pid_out")]
        cmd = [tsp, "--bitrate", R] + self._tsp_buffer() + ["-I", "file"] + (["-P", "remap"] + remap if remap else []) + [
               "-P", "inject", "--pid", "16", "--replace", "--stuffing", "--poll-files", os.path.join(self.sdir, "nit.xml"),
               "-P", "inject", "--pid", "17", "--replace", "--stuffing", "--poll-files", os.path.join(self.sdir, "sdt.xml")]
        if ts.get("tdt", True):
            cmd += ["-P", "inject", "--pid", "20", "--bitrate", str(ts.get("tdt_bitrate", 2000)), "--stuffing",
                    os.path.join(self.sdir, "time.xml"), "-P", "timeref", "--system"]
        if ts.get("eit", True):
            # EIT p/f: je Dienst eine Datei, komplett neu geladen bei Aenderung (ctl, Titelwechsel der Playlist).
            # (eitinject wurde verworfen: es haelt geladene Ereignisse im EPG fest und uebernahm geaenderte Titel
            # eines laufenden Ereignisses nicht.) Wiederholrate: alle Abschnitte mehrmals je Sekunde.
            cmd += ["-P", "inject", "--pid", "18", "--bitrate", str(ts.get("eit_bitrate", 30000)), "--stuffing",
                    "--poll-files"] + [os.path.join(self.sdir, "eit", "eit_%s.bin" % s["key"]) for s in c["services"]]
        cmd += ["-P", "pcradjust", "--bitrate", R]
        if regulate:
            cmd += ["-P", "regulate", "--bitrate", R, "--packet-burst", "16"]
        if c["output"]["kind"] == "udp" and not self.render:
            # nach regulate: zaehlt, was wirklich hinausgeht (Rate je 5-s-Intervall in der Statuszeile)
            cmd += ["-P", "count", "--interval", str(max(1000, int(c["_rate_bps"] / 8 / 188 * 5)))]
        cmd += output
        return cmd

    # ------------------------------------------------------------------ Start
    def start(self, out_fd=None, seconds=None, out_file=None, iq=False):
        """out_fd: Dateideskriptor fuer stdout-Ausgabe (TS oder IQ). seconds/out_file: render."""
        c = self.cfg
        kind = c["output"]["kind"]
        self.write_tables()
        if T.is_psip(c):
            t = threading.Thread(target=self._stt_loop, daemon=True, name="stt")
            t.start()
            self.threads.append(t)
        ffcmd = self.ffmpeg_cmd(seconds)  # baut auch Quellen/Feeder
        for f in self.feeders.values():
            f.start()
        self.ffmpeg = self.spawn("ffmpeg", ffcmd, stdout=subprocess.PIPE, pass_fds=tuple(self.pass_fds))
        for fd in self.close_after:
            try:
                os.close(fd)
            except OSError:
                pass
        regulate = (kind == "udp" or (kind == "file" and c["output"].get("regulate"))) and not self.render
        if out_file:
            out = ["-O", "file", out_file]
        elif kind == "udp" and not self.render and c["output"].get("via") == "stdout":
            # WSL2 (NAT) stellt UDP an das USB-Netz des Pluto nicht zu: geregelter TS auf stdout, der Server (Windows)
            # schickt ihn per UDP weiter (pc/server.py _udp_relay)
            out = ["-O", "file"]
        elif kind == "udp" and not self.render:
            o = c["output"]
            out = ["-O", "ip", "%s:%d" % (o["host"], o["port"]), "--packet-burst", str(o["burst"]), "--enforce-burst"]
            if o.get("rtp"):
                out.append("--rtp")
            if o.get("ttl"):
                out += ["--ttl", str(o["ttl"])]
        elif kind == "file" and not self.render:
            out = ["-O", "file", self.host.media_path(c["output"]["file"] or "out.ts")]
        else:
            out = ["-O", "file"]  # stdout
        tcmd = self.tsp_cmd(out, regulate)
        need_relay = out == ["-O", "file"]
        tsp_in = self.ffmpeg.stdout
        if self._preview_wanted():
            tsp_in = self._start_preview()
        self.tsp = self.spawn("tsp", tcmd, stdin=tsp_in,
                              stdout=subprocess.PIPE if need_relay else None)
        if tsp_in is self.ffmpeg.stdout:
            self.ffmpeg.stdout.close()
        else:
            os.close(tsp_in)
        src_pipe = self.tsp.stdout if need_relay else None
        if need_relay and (kind == "modulator" or iq):
            mcmd = modulator_cmd(self.host, c)
            self.mod = self.spawn("modulator", mcmd, stdin=self.tsp.stdout, stdout=subprocess.PIPE)
            self.tsp.stdout.close()
            src_pipe = self.mod.stdout
            self.log("info", "Modulator: %s" % " ".join(mcmd))
        if src_pipe is not None:
            self.out_fd = out_fd if out_fd is not None else sys.stdout.fileno()
            t = threading.Thread(target=self._relay, args=(src_pipe, bool(self.mod)), daemon=True, name="relay")
            t.start()
            self.threads.append(t)
            self.relay_thread = t
        self.out_t0 = time.time()
        self.cmds = {"ffmpeg": ffcmd, "tsp": tcmd}
        return self.cmds

    # ------------------------------------------------------------------ Vorschau
    # Abgriff zwischen Mux-ffmpeg und tsp: Video ist dort schon so kodiert, wie es gesendet wird. Ein zweites ffmpeg
    # dekodiert nur Keyframes (-skip_frame nokey, ~1-2 Bilder/s je Programm) und schreibt je TV-Dienst
    # preview/<key>.jpg (atomar). Die Vorschau bremst nie die Sendekette: ist ihre Warteschlange voll, fallen Pakete weg.
    PREVIEW_H = 270

    def _preview_wanted(self):
        return (not self.render and self.cfg["ts"].get("preview", True)
                and any(s["type"] == "tv" for s in self.cfg["services"]))

    def _start_preview(self):
        """-> Lese-Ende der Pipe fuer tsp; startet Tee-Thread und Vorschau-ffmpeg."""
        pdir = os.path.join(self.sdir, "preview")
        os.makedirs(pdir, exist_ok=True)
        for f in glob.glob(os.path.join(pdir, "*.jpg")):
            try:
                os.remove(f)
            except OSError:
                pass
        cmd = [self.host.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostats", "-threads", "2",
               "-skip_frame", "nokey", "-probesize", "4M", "-analyzeduration", "3M", "-f", "mpegts", "-i", "pipe:0"]
        for s in self.cfg["services"]:
            if s["type"] != "tv":
                continue
            cmd += ["-map", "0:p:%d:v:0?" % s["service_id"], "-vf", "scale=-2:%d" % self.PREVIEW_H, "-q:v", "6",
                    "-f", "image2", "-update", "1", "-atomic_writing", "1", os.path.join(pdir, "%s.jpg" % s["key"])]
        self.preview = self.spawn("preview", cmd, stdin=subprocess.PIPE)
        self.preview_q = queue.Queue(maxsize=64)
        rfd, wfd = os.pipe()
        for target, args, name in ((self._tee, (self.ffmpeg.stdout, wfd), "tee"), (self._preview_feed, (), "preview")):
            t = threading.Thread(target=target, args=args, daemon=True, name=name)
            t.start()
            self.threads.append(t)
        return rfd

    def _tee(self, src, wfd):
        q = self.preview_q
        rest = b""
        try:
            with os.fdopen(wfd, "wb", 0) as wf:
                while True:
                    b = src.read(1 << 16)
                    if not b:
                        break
                    wf.write(b)
                    # nur ganze 188-Byte-Pakete an die Vorschau, sonst verliert der Demuxer nach einem Verwurf den Takt
                    b = rest + b
                    n = len(b) - len(b) % 188
                    rest = b[n:]
                    try:
                        q.put_nowait(b[:n])
                    except queue.Full:
                        pass
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                src.close()
            except Exception:
                pass
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def _preview_feed(self):
        p = self.preview
        try:
            while not self.stop_evt.is_set():
                b = self.preview_q.get()
                if b is None:
                    break
                p.stdin.write(b)
        except (BrokenPipeError, OSError, ValueError):
            if not self.stop_evt.is_set():
                self.log("warn", "Vorschau beendet (rc=%s), Sendung laeuft weiter" % p.poll())
        finally:
            try:
                p.stdin.close()
            except Exception:
                pass

    def _relay(self, pipe, is_iq):
        fd = self.out_fd
        try:
            sources.set_pipe_size(pipe.fileno(), 4 << 20)
        except Exception:
            pass
        try:
            while not self.stop_evt.is_set():
                b = pipe.read(1 << 16)
                if not b:
                    break
                mv = memoryview(b)
                while mv:
                    try:
                        k = os.write(fd, mv)
                    except BlockingIOError:
                        time.sleep(0.002)
                        continue
                    except (BrokenPipeError, OSError):
                        self.stop_evt.set()
                        self.stop_why = "stdout geschlossen"
                        return
                    mv = mv[k:]
                self.out_bytes += len(b)
                if is_iq and np is not None and (self.out_bytes // len(b)) % 64 == 0:
                    a = np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(a * a) * 2)) if a.size else 0.0
                    self.level = {"rms_dbfs": round(20 * __import__("math").log10(max(rms, 1e-9)), 2),
                                  "peak": round(float(np.max(np.abs(a))), 4)}
        except Exception as e:
            if not self.stop_evt.is_set():
                self.log("error", "Relais: %s" % e)
        finally:
            self.stop_evt.set()

    # ------------------------------------------------------------------ Status / Ende
    def status(self):
        now = time.time()
        self.rate_win.append((now, self.out_bytes))
        self.rate_win = [(t, b) for (t, b) in self.rate_win if now - t <= 10.5]
        rate = None
        if len(self.rate_win) >= 2:
            (t0, b0), (t1, b1) = self.rate_win[0], self.rate_win[-1]
            if t1 > t0:
                rate = (b1 - b0) * 8.0 / (t1 - t0)
        if getattr(self, "udp_rate", None):
            rate = self.udp_rate  # tsp -P count: Rate des letzten 5-s-Intervalls (UDP-Ausgabe)
        tgt = self.cfg["_rate_bps"] if not self.mod else float(self.cfg["rf"]["sample_rate"]) * 32
        d = {"uptime_s": round(now - (self.out_t0 or now), 1), "out_bytes": self.out_bytes,
             "out_rate_bps": round(rate) if rate else None, "target_bps": tgt, "ts_rate_bps": self.cfg["_rate_bps"],
             "procs": {n: p.poll() for n, p in self.procs}}
        if self.level:
            d["level"] = self.level
        if self.feeders:
            d["now_playing"] = [f.now_playing() for f in self.feeders.values()]
        return d

    def alive(self):
        for n, t0, info in self.win_started:
            if not info.get("first_data_s") and time.time() - t0 > 30:
                return False, "%s liefert nach 30 s keine Daten (Quelle/Geraet pruefen, Log %s)" % (n, n + ".log")
        for n, p in self.win_procs:
            if p.poll() is not None and not self.render:
                return False, "%s (Windows) beendet (rc=%s)" % (n, p.returncode)
        for n, p in self.procs:
            if p.poll() is not None and n in ("ffmpeg", "tsp", "modulator"):
                return False, "%s beendet (rc=%s)" % (n, p.returncode)
        return True, None

    def write_session(self, extra=None):
        c = self.cfg
        d = {"session": self.sid, "standard": c["mode"], "pid": os.getpid(), "started": time.time(), "work": self.work,
             "running": True, "config_name": c.get("name"), "rate_bps": c["_rate_bps"], "output": c["output"]["kind"],
             "services": {s["key"]: {"service_id": s["service_id"], "name": s["name"], "provider": s["provider"],
                                     "lcn": s["lcn"], "playlist": s["source"]["kind"] == "playlist",
                                     "eit_file": os.path.join(self.sdir, "eit", "eit_%s.xml" % s["key"]),
                                     "pmt_pid": s.get("_pmt_pid_out", s["_pmt_pid"]), "video_pid": s["_vpid"], "audio_pid": s["_apid"]}
                          for s in c["services"]},
             "versions": self.versions, "names": self.names}
        if extra:
            d.update(extra)
        self.host.atomic_write(os.path.join(self.sdir, "session.json"), json.dumps(d, indent=1, ensure_ascii=False))
        with open(os.path.join(self.sdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in c.items() if not k.startswith("_") or k in ("_rate_bps", "_muxrate")}, f,
                      indent=1, ensure_ascii=False, default=str)

    def stop(self):
        self.stop_evt.set()
        nvenc.stop_all(self)
        for f in self.feeders.values():
            f.stop()
        for name, p in self.procs:
            if p.poll() is None:
                try:
                    p.send_signal(signal.SIGTERM)
                except Exception:
                    pass
        t_end = time.time() + 3
        for name, p in self.procs:
            try:
                p.wait(timeout=max(0.1, t_end - time.time()))
            except Exception:
                try:
                    p.kill()
                    p.wait(timeout=2)
                except Exception:
                    pass
        try:
            sp = os.path.join(self.sdir, "session.json")
            with open(sp) as f:
                d = json.load(f)
            d["running"] = False
            d["stopped"] = time.time()
            self.host.atomic_write(sp, json.dumps(d, indent=1, ensure_ascii=False))
        except Exception:
            pass
        shutil.rmtree(self.work, ignore_errors=True)
