# -*- coding: utf-8 -*-
"""GPU-Encoder (NVIDIA NVENC) fuer DVB-Dienste: Windows-ffmpeg aus WSL1 heraus.

WSL1 hat keinen GPU-Zugriff, kann aber Windows-Programme direkt starten (Interop). Der Runner startet je Dienst mit
video.encoder = "nvenc" ein Windows-ffmpeg (Standard C:\\ffmpeg\\bin\\ffmpeg.exe), das die Quelle selbst liest
(Datei/YouTube mit CUDA-Dekodierung, Testbild, dshow-Geraet, Bildschirm per gdigrab/ddagrab), auf 25 fps bringt und
mit hevc_nvenc bzw. h264_nvenc kodiert. Ausgabe: roher Elementarstrom (Annex B) auf stdout. Der Runner reicht ihn
ueber eine Pipe an den Mux-ffmpeg weiter (-c copy, Zeitstempel aus der festen Bildrate -> lueckenlos auch ueber
Schleifen), wahlweise mit eingefuegten HDR10-SEI (Mastering Display, Content Light Level). Der Ton kommt vom
Linux-Mux (aus derselben Datei bzw. Testton) oder bei dshow aus einem zweiten Windows-ffmpeg (PCM).

Schleife einer Datei: ffconcat-Liste mit derselben Datei, je Eintrag 'outpoint' = Videodauer. Beide Seiten (Bild auf
Windows, Ton in WSL) schneiden an derselben Stelle -> Bild und Ton bleiben ueber beliebig viele Durchlaeufe synchron.
"""
import json
import os
import re
import struct
import subprocess
import threading
import time

DEFAULT_EXE = "/mnt/c/ffmpeg/bin/ffmpeg.exe"
DEFAULT_PROBE = "/mnt/c/ffmpeg/bin/ffprobe.exe"
WIN_FONT = "C\\:/Windows/Fonts/arialbd.ttf"

# HDR10-Standardwerte (Sol-Levante-/BT.2020-P3-D65-Master, 1000 cd/m2)
DEFAULT_MASTER = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)"


# vom Nutzer in der Einrichtung installiertes ffmpeg (Muxwright-Ordner	oolsfmpeg), falls kein C:fmpeg vorhanden
LOCAL_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "ffmpeg", "bin", "ffmpeg.exe")


def win_exe():
    if os.environ.get("DABLAB_WIN_FFMPEG"):
        return os.environ["DABLAB_WIN_FFMPEG"]
    if not os.path.isfile(DEFAULT_EXE) and os.path.isfile(LOCAL_EXE):
        return os.path.normpath(LOCAL_EXE)
    return DEFAULT_EXE


def win_probe():
    if os.environ.get("DABLAB_WIN_FFPROBE"):
        return os.environ["DABLAB_WIN_FFPROBE"]
    loc = LOCAL_EXE.replace("ffmpeg.exe", "ffprobe.exe")
    if not os.path.isfile(DEFAULT_PROBE) and os.path.isfile(loc):
        return os.path.normpath(loc)
    return DEFAULT_PROBE


_DECODERS = None


def win_has_decoder(name):
    global _DECODERS
    if _DECODERS is None:
        try:
            r = subprocess.run([win_exe(), "-hide_banner", "-decoders"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
            _DECODERS = r.stdout.decode("utf-8", "replace")
        except Exception:
            _DECODERS = ""
    return (" %s " % name) in _DECODERS


def available():
    return os.path.isfile(win_exe()) and os.access(win_exe(), os.X_OK)


def winpath(p):
    """Linux-Pfad -> Windows-Pfad. /mnt/g/x -> G:\\x ; sonstige (z. B. /tmp, /opt) -> \\\\wsl$\\<Distribution>\\..."""
    p = os.path.abspath(p)
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", p)
    if m:
        return m.group(1).upper() + ":" + (m.group(2) or "/").replace("/", "\\")
    distro = os.environ.get("WSL_DISTRO_NAME", "dablab")
    return "\\\\wsl$\\" + distro + p.replace("/", "\\")


# --------------------------------------------------------------------------------------------- HDR10-SEI
def _parse_master(s):
    """'G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)' (x265-Schreibweise, Einheiten 0,00002 bzw. 0,0001 cd/m2)."""
    d = {k: tuple(int(v) for v in re.search(r"%s\((\d+),(\d+)\)" % k, s).groups()) for k in ("G", "B", "R", "WP", "L")}
    return d


class _BitWriter:
    def __init__(self):
        self.bits = []

    def u(self, n, v):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def bytes(self):
        b = self.bits + [1] + [0] * ((8 - (len(self.bits) + 1) % 8) % 8)    # rbsp_trailing_bits
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def _ep(rbsp):
    """Emulation prevention (00 00 0x -> 00 00 03 0x)."""
    out, z = bytearray(), 0
    for b in rbsp:
        if z >= 2 and b <= 3:
            out.append(3)
            z = 0
        out.append(b)
        z = z + 1 if b == 0 else 0
    return bytes(out)


def hdr10_sei_nal(master=DEFAULT_MASTER, max_cll=1000, max_fall=400):
    """HEVC-Prefix-SEI-NAL (Typ 39) mit mastering_display_colour_volume (137) und content_light_level_info (144)."""
    m = _parse_master(master)
    mdcv = b"".join(struct.pack(">HH", *m[k]) for k in ("G", "B", "R")) + struct.pack(">HH", *m["WP"]) + \
        struct.pack(">II", m["L"][0], m["L"][1])
    cll = struct.pack(">HH", int(max_cll), int(max_fall))
    payload = bytes([137, len(mdcv)]) + mdcv + bytes([144, len(cll)]) + cll
    rbsp = payload + b"\x80"                                         # rbsp_trailing_bits
    return b"\x00\x00\x00\x01" + bytes([39 << 1, 1]) + _ep(rbsp)


class SeiInjector:
    """Fuegt in einen HEVC-Annex-B-Strom vor die erste VCL-NAL jedes Zugriffs mit Parametersaetzen (VPS/SPS/PPS, also
    jedes IDR) die SEI ein. NAL-Grenzen = 3-Byte-Startcodes; die letzte, noch unvollstaendige NAL bleibt im Puffer."""

    def __init__(self, sei):
        self.sei = sei
        self.buf = b""
        self.need = False
        self.count = 0

    def feed(self, data):
        b = self.buf + data
        starts = []
        i = b.find(b"\x00\x00\x01")
        while i >= 0:
            starts.append(i)
            i = b.find(b"\x00\x00\x01", i + 3)
        if len(starts) < 2:
            self.buf = b
            return b""
        out = [b[:starts[0]]]
        for k in range(len(starts) - 1):
            s0 = starts[k]
            if s0 + 3 >= len(b):
                break
            t = (b[s0 + 3] >> 1) & 0x3F
            if t in (32, 33, 34):
                self.need = True
            elif t < 32 and self.need:
                out.append(self.sei)
                self.count += 1
                self.need = False
            elif t == 35:
                self.need = False
            out.append(b[s0:starts[k + 1]])
        self.buf = b[starts[-1]:]
        return b"".join(out)

    def flush(self):
        b, self.buf = self.buf, b""
        return b


# --------------------------------------------------------------------------------------------- Quelle / Kommando
def probe_video(host, path):
    """Videodauer (s) und HDR-Metadaten der Quelle (Linux-ffprobe)."""
    fp = host.tool("ffprobe", required=False)
    out = {"duration": None, "hdr_side_data": False, "transfer": None, "pix_fmt": None, "width": None, "height": None,
           "codec": None}
    if not fp:
        return out
    try:
        r = subprocess.run([fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,duration,color_transfer,pix_fmt,width,height:format=duration", "-of", "json", path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        j = json.loads(r.stdout or b"{}")
        st = (j.get("streams") or [{}])[0]
        d = st.get("duration") or (j.get("format") or {}).get("duration")
        out.update({"duration": float(d) if d else None, "transfer": st.get("color_transfer"), "pix_fmt": st.get("pix_fmt"),
                    "codec": st.get("codec_name"),
                    "width": st.get("width"), "height": st.get("height")})
        r = subprocess.run([fp, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1", "-show_frames",
                            "-show_entries", "frame=side_data_list", "-of", "json", path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        out["hdr_side_data"] = b"Mastering display" in r.stdout
    except Exception:
        pass
    return out


def write_concat(host, path, dur, list_path, windows):
    """ffconcat mit derselben Datei, je Eintrag outpoint = Videodauer (>= 48 h Gesamtdauer)."""
    n = max(2, int(48 * 3600 / max(1.0, dur)) + 1)
    p = winpath(path).replace("\\", "/") if windows else path
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for _ in range(n):
            f.write("file '%s'\noutpoint %.6f\n" % (p.replace("'", r"'\''"), dur))
    return list_path


def encoder_args(svc):
    v = svc["video"]
    hd = v.get("hdr") or {}
    hevc = v["codec"] == "hevc"
    br = int(float(v["bitrate_kbps"]) * 1000)
    fps = int(v["fps"])
    buf = str(int(br * float(v.get("vbv_s", 0.5))))
    if v.get("encoder") == "amf":
        # AMD AMF (ungetestet: keine AMD-Karte im Labor). CBR mit Fuellbits, IDR je Sekunde, SPS/PPS(/VPS) vor jedem IDR
        a = ["-c:v", "hevc_amf" if hevc else "h264_amf", "-usage", "transcoding", "-quality", v.get("amf_quality", "balanced"),
             "-rc", "cbr", "-b:v", str(br), "-maxrate", str(br), "-bufsize", buf, "-enforce_hrd", "1", "-filler_data", "1",
             "-g", str(fps), "-forced_idr", "1", "-bf", "0", "-aud", "1",
             "-force_key_frames", "expr:gte(t,n_forced*1)"]
        if hevc:
            a += ["-header_insertion_mode", "idr"]
    else:
        a = ["-c:v", "hevc_nvenc" if hevc else "h264_nvenc", "-preset", v.get("nv_preset", "p4"), "-tune", "hq", "-rc", "cbr",
             "-b:v", str(br), "-maxrate", str(br), "-bufsize", buf, "-g", str(fps),
             "-keyint_min", str(fps), "-forced-idr", "1", "-bf", "0", "-aud", "1", "-spatial-aq", "1",
             "-force_key_frames", "expr:gte(t,n_forced*1)"]
    ten = v["codec"] == "hevc" and (v.get("bit_depth", 8) == 10 or hd.get("mode") in ("hdr10", "hlg"))
    if v["codec"] == "hevc":
        a += ["-profile:v", "main10" if ten else "main", "-pix_fmt", "p010le" if ten else "yuv420p"]
    else:
        a += ["-profile:v", "high", "-pix_fmt", "yuv420p"]
    if hd.get("mode") in ("hdr10", "hlg"):
        trc = "smpte2084" if hd["mode"] == "hdr10" else "arib-std-b67"
        a += ["-color_primaries", "bt2020", "-color_trc", trc, "-colorspace", "bt2020nc", "-color_range", "tv",
              "-bsf:v", "hevc_metadata=colour_primaries=9:transfer_characteristics=%d:matrix_coefficients=9:video_full_range_flag=0"
              % (16 if hd["mode"] == "hdr10" else 18)]
    else:
        a += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
              "-bsf:v", "%s_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0"
              % ("hevc" if v["codec"] == "hevc" else "h264")]
    return a


def build(chain, svc):
    """Startet den Windows-Encoder (NVENC oder AMF) fuer einen Dienst. -> dict(inputs=[...], vin, ain, af, pass_fds=[...], info)."""
    host, cfg, log = chain.host, chain.cfg, chain.log
    v, src = svc["video"], svc["source"]
    enc = v.get("encoder", "nvenc")
    label = "AMF" if enc == "amf" else "NVENC"
    if not available():
        raise host.RunnerError("%s: Windows-ffmpeg nicht gefunden (%s, DABLAB_WIN_FFMPEG)" % (label, win_exe()))
    w, h = v["_wh"]
    fps = int(v["fps"])
    kind = src["kind"]
    cdir = os.path.join(host.CACHE, "nvenc")
    os.makedirs(cdir, exist_ok=True)
    win_in, vf = [], ["fps=%d" % fps, "scale=%d:%d:force_original_aspect_ratio=decrease:flags=bicubic" % (w, h),
                      "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (w, h), "setsar=1"]
    info = {"encoder": enc, "kind": kind}
    audio = None                                 # (linux_inputs, af)
    probe = {}
    if kind in ("file", "youtube"):
        if kind == "youtube":
            from . import sources as S
            ent = S.youtube_find(host, src.get("url") or src.get("id")) or \
                S.youtube_download(host, src.get("url") or src.get("id"), src.get("max_height", 2160),
                                   progress=lambda p: log("info", "youtube", youtube=p), log=log,
                                   cookies=src.get("cookies"), cookies_from_browser=src.get("cookies_from_browser"))
            path = os.path.join(S.video_dir(host), ent["file"])
        else:
            path = host.media_path(src["file"])
        if not os.path.isfile(path):
            raise host.RunnerError("Videodatei fehlt: %s" % path)
        probe = probe_video(host, path)
        info.update({"file": path, "duration_s": probe["duration"], "source_hdr_side_data": probe["hdr_side_data"]})
        # GPU-Dekodierung nur fuer Formate, die die GPU kann (Pascal: H.264, HEVC 8/10 bit, VP9, MPEG-2); AV1 per libdav1d.
        # NVIDIA: CUDA; AMD: D3D11VA (Bilder werden automatisch in den Hauptspeicher kopiert)
        hw = "d3d11va" if enc == "amf" else "cuda"
        dec = ["-hwaccel", hw] if probe.get("codec") in ("h264", "hevc", "vp9", "mpeg2video", "vc1", "mpeg4") else []
        if probe.get("codec") == "av1":
            if not win_has_decoder("libdav1d"):
                raise host.RunnerError("%s: %s ist AV1 - die GPU dekodiert kein AV1 und das Windows-ffmpeg hat kein "
                                       "libdav1d. Quelle vorher umwandeln (z. B. ffmpeg -i x.mp4 -c:v libx264 -crf 16 y.mp4) "
                                       "oder ein ffmpeg-full-Build verwenden." % (label, os.path.basename(path)))
            dec = ["-c:v", "libdav1d"]
        info["decoder"] = hw if dec[:1] == ["-hwaccel"] else (dec[1] if dec else "software")
        if src.get("loop", True) and probe["duration"]:
            lw = write_concat(host, path, probe["duration"], os.path.join(cdir, "%s_%s_win.ffconcat" % (chain.sid, svc["key"])), True)
            win_in = dec + ["-f", "concat", "-safe", "0", "-i", winpath(lw)]
            if src["audio"]["kind"] == "source":
                ll = write_concat(host, path, probe["duration"], os.path.join(chain.work, "loop_%s.ffconcat" % svc["key"]), False)
                audio = (["-f", "concat", "-safe", "0", "-i", ll], "aresample=%d:async=1" % svc["audio"]["samplerate"])
            info["loop"] = "concat-outpoint"
        else:
            win_in = dec + ["-i", winpath(path)]
            if src["audio"]["kind"] == "source":
                audio = (["-i", path], "aresample=%d:async=1" % svc["audio"]["samplerate"])
    elif kind == "testpattern":
        win_in = ["-f", "lavfi", "-i", "%s=size=%dx%d:rate=%d" % (src.get("pattern", "testsrc2"), w, h, fps)]
        txt = (src.get("text") or svc["name"]).replace(":", "\\:").replace("'", "")
        vf.append("drawtext=fontfile='%s':text='%s  %%{localtime\\:%%X}  %%{pts\\:hms}':fontsize=%d:fontcolor=white:"
                  "box=1:boxcolor=black@0.6:x=w/20:y=h/16" % (WIN_FONT, txt, max(16, h // 18)))
    elif kind == "dshow":
        win_in = ["-f", "dshow", "-rtbufsize", "512M"]
        if src.get("dshow_options"):
            win_in += src["dshow_options"]
        win_in += ["-i", "video=%s" % src["device"]]
        info["device"] = src["device"]
    elif kind in ("gdigrab", "screen"):
        win_in = ["-f", "gdigrab", "-framerate", str(fps)]
        if src.get("region"):
            x, y, ww, hh = src["region"]
            win_in += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", "%dx%d" % (ww, hh)]
        win_in += ["-i", src.get("window") and ("title=" + src["window"]) or "desktop"]
    elif kind == "ddagrab":
        win_in = ["-f", "lavfi", "-i", "ddagrab=output_idx=%d:framerate=%d" % (int(src.get("output", 0)), fps)]
        vf.insert(0, "hwdownload,format=bgra")
    else:
        raise host.RunnerError("%s: Quelle %s nicht unterstuetzt (file, youtube, testpattern, dshow, gdigrab, ddagrab)" % (label, kind))
    hd = v.get("hdr") or {}
    ten = v["codec"] == "hevc" and (v.get("bit_depth", 8) == 10 or hd.get("mode") in ("hdr10", "hlg"))
    vf.append("format=%s" % ("p010le" if ten else "yuv420p"))
    exe = win_exe()
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostats", "-threads", str(v.get("win_threads", 4)),
           "-filter_threads", "2"] + win_in + ["-map", "0:v:0", "-vf", ",".join(vf), "-an"] + encoder_args(svc) + \
        ["-f", "hevc" if v["codec"] == "hevc" else "h264", "pipe:1"]
    # HDR10-SEI: NVENC uebernimmt Mastering-Display/CLL aus der Quelle (nachgewiesen); fehlen sie (SDR-Quelle als
    # HDR10-Testsignal), fuegt der Runner sie selbst ein. AMF: nicht nachgewiesen -> immer einfuegen
    inject = None
    if hd.get("mode") == "hdr10":
        want = hd.get("inject_sei", "auto")
        if want == "always" or (want == "auto" and (enc == "amf" or not probe.get("hdr_side_data"))):
            inject = SeiInjector(hdr10_sei_nal(hd.get("master_display", DEFAULT_MASTER), hd.get("max_cll", 1000),
                                               hd.get("max_fall", 400)))
        info["sei"] = "eingefuegt (Runner)" if inject else "aus der Quelle (%s)" % label
    lf = open(os.path.join(chain.sdir, "%s_%s.log" % (enc, svc["key"])), "ab")
    lf.write(("\n=== %s\n" % " ".join(cmd)).encode())
    lf.flush()
    t_start = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=lf)
    lf.close()
    chain.win_procs.append(("%s_%s" % (enc, svc["key"]), p))
    rfd, wfd = os.pipe()
    chain.win_started.append(("%s_%s" % (enc, svc["key"]), time.time(), info))

    def relay():
        try:
            with os.fdopen(wfd, "wb", 0) as wf:
                while not chain.stop_evt.is_set():
                    b = p.stdout.read(1 << 16)
                    if not b:
                        break
                    if not info.get("first_data_s"):
                        info["first_data_s"] = round(time.time() - t_start, 2)
                    if inject:
                        b = inject.feed(b)
                    if b:
                        wf.write(b)
                if inject:
                    wf.write(inject.flush())
        except Exception as e:
            if not chain.stop_evt.is_set() and not (chain.ffmpeg is not None and chain.ffmpeg.poll() is not None):
                log("warn", "%s-Relais %s: %s" % (label, svc["key"], e))
        finally:
            info["sei_count"] = inject.count if inject else None

    t = threading.Thread(target=relay, daemon=True, name="nvenc-" + svc["key"])
    t.start()
    chain.threads.append(t)
    inputs = [["-f", "hevc" if v["codec"] == "hevc" else "h264", "-framerate", str(fps), "-fflags", "+genpts",
               "-probesize", "32M", "-thread_queue_size", "1024", "-i", "pipe:%d" % rfd]]
    vin = 0
    pass_fds = [rfd]
    # Ton
    from . import sources as S
    if svc["audio"]["codec"] == "none":
        info["cmd"] = " ".join(cmd)
        return {"inputs": inputs, "vin": vin, "ain": None, "af": None, "pass_fds": pass_fds, "info": info,
                "close_after_spawn": [rfd]}
    if audio is None and src["audio"]["kind"] == "dshow":
        # Ton von einem DirectShow-Geraet: zweites Windows-ffmpeg -> PCM s16le 48 kHz stereo
        acmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "dshow", "-audio_buffer_size", "50", "-i",
                "audio=%s" % src["audio"]["device"], "-ac", "2", "-ar", "48000", "-f", "s16le", "pipe:1"]
        ap = subprocess.Popen(acmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        chain.win_procs.append(("dshow_audio_" + svc["key"], ap))
        inputs.append(["-f", "s16le", "-ar", "48000", "-ac", "2", "-thread_queue_size", "512", "-i", "pipe:%d" % ap.stdout.fileno()])
        pass_fds.append(ap.stdout.fileno())
        af = "aresample=%d:async=1000" % svc["audio"]["samplerate"]
    elif audio is not None:
        inputs.append(audio[0])
        af = audio[1]
    else:
        aud = dict(src["audio"])
        if aud["kind"] in ("source", "dshow"):
            aud["kind"] = "sine"
        a_args, af = S.audio_input(host, svc, aud, chain.work, log)
        inputs.append(a_args)
    info["cmd"] = " ".join(cmd)
    return {"inputs": inputs, "vin": vin, "ain": len(inputs) - 1, "af": af, "pass_fds": pass_fds, "info": info,
            "close_after_spawn": [rfd]}


def stop_all(chain):
    for name, p in getattr(chain, "win_procs", []):
        try:
            if p.poll() is None and p.stdin:
                p.stdin.write(b"q")
                p.stdin.flush()
        except Exception:
            pass
    for name, p in getattr(chain, "win_procs", []):
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=2)
            except Exception:
                pass


def list_dshow():
    """DirectShow-Geraete (Video/Audio) ueber das Windows-ffmpeg."""
    if not available():
        return {"error": "Windows-ffmpeg fehlt"}
    r = subprocess.run([win_exe(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    out = {"video": [], "audio": []}
    for line in r.stderr.decode("utf-8", "replace").splitlines():
        m = re.search(r'"(.+)" \((video|audio|none)\)', line)
        if m:
            out["audio" if m.group(2) == "audio" else "video"].append(m.group(1))
    return out


def gpu_info():
    try:
        r = subprocess.run(["/mnt/c/Windows/System32/nvidia-smi.exe", "--query-gpu=name,driver_version,encoder.stats.sessionCount",
                            "--format=csv,noheader"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        return r.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return None
