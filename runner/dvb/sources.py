# -*- coding: utf-8 -*-
"""Videoquellen: Testbild, Datei, YouTube (yt-dlp), Diashow, Video-Playlist, Netzwerk-Stream (Feeder wie beim DAB-Teil).

build_source() liefert ffmpeg-Eingaenge und Filterketten je Dienst. Der Playlist-Feeder schreibt Rohvideo (yuv420p) und
PCM (s16le) in zwei FIFOs, die der Mux-ffmpeg als Eingaenge liest; Titelwechsel sind damit nahtlos und A/V bleibt
sample-genau synchron (nach jedem Titel wird die kuerzere Spur mit Schwarz bzw. Stille aufgefuellt)."""
import glob
import json
import os
import queue
import random
import re
import subprocess
import threading
import time

from . import si

FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                   "/System/Library/Fonts/Helvetica.ttc"]


def set_pipe_size(fd, size):
    """Linux: F_SETPIPE_SZ (1031). Groessere Pipes entkoppeln Feeder und Mux-ffmpeg."""
    try:
        import fcntl
        fcntl.fcntl(fd, 1031, int(size))
    except Exception:
        pass


def font_file():
    for f in FONT_CANDIDATES:
        if os.path.isfile(f):
            return f
    return None


def drawtext_escape(s):
    s = str(s)
    for ch in ("\\", "'", ":", "%", ",", "[", "]", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


def probe(host, path):
    """ffprobe: Streams und Dauer."""
    fp = host.tool("ffprobe", required=False)
    if not fp or not os.path.isfile(path):
        return {}
    try:
        r = subprocess.run([fp, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        j = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}
    out = {"has_video": False, "has_audio": False}
    for st in j.get("streams", []):
        if st.get("codec_type") == "video" and st.get("disposition", {}).get("attached_pic", 0) == 0:
            out["has_video"] = True
            out["width"], out["height"] = st.get("width"), st.get("height")
            try:
                if st.get("duration") not in (None, "N/A"):
                    out["video_duration_s"] = float(st["duration"])
                elif st.get("nb_frames") and st.get("avg_frame_rate", "0/0") != "0/0":
                    n, d = st["avg_frame_rate"].split("/")
                    out["video_duration_s"] = int(st["nb_frames"]) * float(d) / float(n)
            except Exception:
                pass
        elif st.get("codec_type") == "audio":
            out["has_audio"] = True
    f = j.get("format", {})
    if f.get("duration"):
        out["duration_s"] = round(float(f["duration"]), 2)
    tags = {k.lower(): v for k, v in (f.get("tags") or {}).items()}
    out["title"], out["artist"] = tags.get("title"), tags.get("artist")
    return out


# ---------------------------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------------------------
_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


def youtube_id(url_or_id):
    s = str(url_or_id or "").strip()
    if re.match(r"^[A-Za-z0-9_-]{11}$", s):
        return s
    m = _YT_ID.search(s)
    return m.group(1) if m else None


def video_dir(host):
    d = os.path.join(host.MEDIA, "video")
    os.makedirs(d, exist_ok=True)
    return d


def youtube_index(host):
    p = os.path.join(video_dir(host), "youtube_index.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def youtube_find(host, url_or_id):
    """Schon geladen? -> Eintrag aus dem Index oder Datei <id>.mp4."""
    vid = youtube_id(url_or_id)
    idx = youtube_index(host)
    if vid and vid in idx and os.path.isfile(os.path.join(video_dir(host), idx[vid]["file"])):
        return idx[vid]
    if vid:
        for ext in ("mp4", "mkv", "webm"):
            p = os.path.join(video_dir(host), "%s.%s" % (vid, ext))
            if os.path.isfile(p):
                return {"id": vid, "file": os.path.basename(p), "title": vid}
    return None


# --------------------------------------------------------------------------------------------- YouTube-Cookies
# Windows-yt-dlp (liest Browser-Cookies): DABLAB_WIN_YTDLP oder die übliche Python-/Conda-Installation des Nutzers
WIN_YTDLP = next(iter(sorted(glob.glob("/mnt/c/Users/*/miniconda3/Scripts/yt-dlp.exe")
                             + glob.glob("/mnt/c/Users/*/AppData/Local/Programs/Python/*/Scripts/yt-dlp.exe"))), "")
BROWSERS = ("edge", "firefox", "chrome", "brave", "opera", "vivaldi", "chromium")


def cookie_file(host):
    return os.path.join(host.CACHE, "youtube_cookies.txt")


def consent_file(host):
    return os.path.join(host.CACHE, "youtube_browser_consent.json")


def browser_consent(host):
    """Freigabe des Nutzers zum Lesen der Browser-Cookies (angelegt mit 'dablab_run.py media youtube-consent <browser>')."""
    try:
        with open(consent_file(host), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def export_browser_cookies(host, browser, url="https://www.youtube.com/", log=None):
    """Liest die YouTube-Cookies einmalig ueber das Windows-yt-dlp aus dem Browser (nur mit Freigabe) und schreibt sie
    als Netscape-Datei nach runtime/cache/youtube_cookies.txt. Das Linux-yt-dlp nutzt danach nur diese Datei."""
    c = browser_consent(host)
    if not c or c.get("browser") != browser:
        raise host.RunnerError("Keine Freigabe fuer Browser-Cookies (%s). Erst: dablab_run.py media youtube-consent %s" % (browser, browser))
    exe = os.environ.get("DABLAB_WIN_YTDLP", WIN_YTDLP)
    if not os.path.isfile(exe):
        raise host.RunnerError("Windows-yt-dlp fehlt (%s); unter Windows: pip install -U yt-dlp" % exe)
    from .nvenc import winpath
    out = cookie_file(host)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    r = subprocess.run([exe, "--cookies-from-browser", browser, "--cookies", winpath(out), "--skip-download",
                        "--no-warnings", "--quiet", "--playlist-items", "0", url], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=180)
    if not os.path.isfile(out):
        raise host.RunnerError("Cookie-Export fehlgeschlagen: %s" % r.stderr.decode("utf-8", "replace")[-400:])
    if log:
        log("info", "YouTube-Cookies aus %s exportiert -> %s" % (browser, out))
    return out


def youtube_download(host, url, max_height=1080, progress=None, log=None, force=False, cookies=None,
                     cookies_from_browser=None):
    """yt-dlp (Python-Modul) -> media/video/<id>.mp4. progress(dict) wird mit Fortschritt gerufen.
    Rueckgabe: {"id","file","title","duration_s","width","height","url"}."""
    try:
        import yt_dlp  # pip
    except ImportError:
        raise host.RunnerError("yt-dlp nicht installiert (pip install yt-dlp)")
    vd = video_dir(host)
    url = str(url).strip()
    vid = youtube_id(url)
    if vid and not url.startswith("http"):
        url = "https://www.youtube.com/watch?v=" + vid
    state = {"last": 0.0}

    def hook(d):
        st = d.get("status")
        now = time.time()
        if st == "downloading" and now - state["last"] < 0.5:
            return
        state["last"] = now
        tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        got = d.get("downloaded_bytes") or 0
        info = {"status": st, "downloaded_bytes": got, "total_bytes": tot,
                "percent": round(100.0 * got / tot, 1) if tot else None,
                "speed_bps": d.get("speed"), "eta_s": d.get("eta"), "file": os.path.basename(d.get("filename") or "")}
        if progress:
            progress(info)

    fmt = ("bv*[height<=%d][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=%d][ext=mp4]+ba[ext=m4a]/"
           "b[height<=%d][ext=mp4]/bv*[height<=%d]+ba/b[height<=%d]/b") % ((int(max_height),) * 5)
    opts = {"format": fmt, "merge_output_format": "mp4", "outtmpl": os.path.join(vd, "%(id)s.%(ext)s"),
            "noplaylist": True, "quiet": True, "no_warnings": True, "progress_hooks": [hook], "noprogress": True,
            "overwrites": bool(force), "continuedl": not force, "retries": 3,
            "postprocessor_args": {"merger": ["-movflags", "+faststart"]}}
    if log:
        opts["logger"] = _YtLogger(log)
    # Cookies: ausdruecklich angegebene Datei, sonst eine frueher exportierte (runtime/cache/youtube_cookies.txt);
    # Browser nur mit Freigabe (export_browser_cookies)
    if cookies_from_browser:
        cookies = export_browser_cookies(host, cookies_from_browser, url, log)
    if cookies:
        cp = host.media_path(cookies)
        if not os.path.isfile(cp):
            raise host.RunnerError("Cookie-Datei fehlt: %s" % cp)
        opts["cookiefile"] = cp
    elif os.path.isfile(cookie_file(host)):
        opts["cookiefile"] = cookie_file(host)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info.get("entries"):
        info = info["entries"][0]
    vid = info.get("id") or vid
    fn = None
    for ext in ("mp4", "mkv", "webm", "m4a"):
        if os.path.isfile(os.path.join(vd, "%s.%s" % (vid, ext))):
            fn = "%s.%s" % (vid, ext)
            break
    if not fn:
        rd = info.get("requested_downloads") or []
        if rd and rd[0].get("filepath"):
            fn = os.path.basename(rd[0]["filepath"])
    if not fn:
        raise host.RunnerError("yt-dlp: Zieldatei nicht gefunden (%s)" % vid)
    ent = {"id": vid, "file": fn, "title": info.get("title") or vid, "duration_s": info.get("duration"),
           "width": info.get("width"), "height": info.get("height"), "url": info.get("webpage_url") or url,
           "uploader": info.get("uploader"), "license": info.get("license"), "downloaded": time.time(),
           "bytes": os.path.getsize(os.path.join(vd, fn))}
    idx = youtube_index(host)
    idx[vid] = ent
    host.atomic_write(os.path.join(vd, "youtube_index.json"), json.dumps(idx, indent=1, ensure_ascii=False))
    return ent


class _YtLogger:
    def __init__(self, log):
        self.log = log

    def debug(self, msg):
        if msg.startswith("[download] Destination") or msg.startswith("[Merger]"):
            self.log("info", "yt-dlp: " + msg)

    def info(self, msg):
        self.log("info", "yt-dlp: " + msg)

    def warning(self, msg):
        self.log("warn", "yt-dlp: " + msg)

    def error(self, msg):
        self.log("error", "yt-dlp: " + msg)


# ---------------------------------------------------------------------------------------------
# ffmpeg-Eingaenge je Dienst
# ---------------------------------------------------------------------------------------------
def video_filter(svc, drawtext=None):
    w, h = svc["video"]["_wh"]
    fps = svc["video"]["fps"]
    f = ["scale=%d:%d:force_original_aspect_ratio=decrease:flags=bicubic" % (w, h),
         "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (w, h), "setsar=1", "fps=%s" % fps, "format=yuv420p"]
    if drawtext:
        f.append(drawtext)
    return ",".join(f)


def drawtext_filter(svc, text, timestamp=True):
    ff = font_file()
    if not ff:
        return None
    w, h = svc["video"]["_wh"]
    parts = [drawtext_escape(text)] if text else []
    if timestamp:
        parts.append("%{localtime\\:%X}")
        parts.append("%{pts\\:hms}")
    t = "  ".join(parts)
    return ("drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=%d:"
            "x=%d:y=%d" % (ff, t, max(16, h // 18), max(4, h // 120), w // 20, h // 16))


# ---------------------------------------------------------------------------------------------
# Overlay je TV-Programm: Logo (PNG/JPG mit Alpha) und Textzeile (live aenderbar, optional Lauftext)
# ---------------------------------------------------------------------------------------------
def _pos_xy(pos, m, w="w", h="h"):
    """Position (top-left … center, top/bottom) -> ffmpeg-Ausdruecke x, y mit Rand m (Pixel).
    W/H = Hauptbild, w/h = Logo bzw. Textbreite/-hoehe (drawtext: text_w/text_h)."""
    x = {"left": "%d" % m, "right": "W-%s-%d" % (w, m)}.get(pos.split("-")[-1], "(W-%s)/2" % w)
    y = {"top": "%d" % m, "bottom": "H-%s-%d" % (h, m)}.get(pos.split("-")[0], "(H-%s)/2" % h)
    return x, y


def overlay_text_clean(text, scroll=False):
    """Text fuer die Overlay-Datei: Steuerzeichen raus, Lauftext einzeilig, nie leer (drawtext braucht Inhalt)."""
    t = str(text or "").replace("\r", "")
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", t)
    if scroll:
        t = " ".join(t.split())
    t = t.strip("\n")
    return t[:500] or " "


def overlay_text_file(sdir, svc):
    return os.path.join(sdir, "overlay_%s.txt" % svc["key"])


def overlay_build(host, svc, sdir, log):
    """-> {"inputs": [[args]], "logo": Filter fuer den Logo-Eingang|None, "place": overlay=…|None, "text": drawtext=…|None}
    oder None ohne Overlay. Das Logo kommt als eigener Eingang (-loop 1), der Text aus einer Datei im Sitzungsordner
    (drawtext textfile + reload=1): ctl {"overlay": {...}} ersetzt sie atomar, ohne Neustart der Kette."""
    ov = svc.get("overlay") or {}
    lg, tx = ov.get("logo"), ov.get("text")
    if not lg and not tx:
        return None
    w, h = svc["video"]["_wh"]
    r = {"inputs": [], "logo": None, "place": None, "text": None}
    if lg:
        path = host.media_path(lg["file"])
        if not os.path.isfile(path):
            raise host.RunnerError("Overlay-Logo fehlt: %s" % lg["file"])
        r["inputs"].append(["-loop", "1", "-framerate", str(svc["video"]["fps"]), "-thread_queue_size", "16", "-i", path])
        lh = max(2, int(round(h * lg["size_pct"] / 100.0 / 2)) * 2)
        r["logo"] = "scale=-2:%d:flags=bicubic,format=rgba,colorchannelmixer=aa=%.3f" % (lh, lg["opacity"])
        x, y = _pos_xy(lg["position"], int(round(h * lg["margin_pct"] / 100.0)))
        r["place"] = "overlay=x=%s:y=%s:format=auto:eof_action=repeat" % (x, y)
    if tx:
        ff = font_file()
        if not ff:
            log("warn", "Overlay-Text: keine Schrift gefunden (DejaVu/Liberation), Text entfaellt")
        else:
            tf = overlay_text_file(sdir, svc)
            host.atomic_write(tf, overlay_text_clean(tx["text"], tx["scroll"]))
            fs = max(10, int(round(h * tx["size_pct"] / 100.0)))
            m = int(round(h * tx["margin_pct"] / 100.0))
            x, y = _pos_xy(tx["position"], m, "text_w", "text_h")
            if x.startswith("("):
                x = "max(%d\\,%s)" % (m, x)     # zu langer Text: links buendig statt beidseitig abgeschnitten
            if tx["scroll"]:
                # Lauftext: von rechts herein, links hinaus; speed_pct = Bildbreite je Sekunde in %
                x = "W-mod(t*%.3f\\,W+text_w)" % (w * tx["speed_pct"] / 100.0)
            f = ("drawtext=fontfile=%s:textfile=%s:reload=1:expansion=none:fontsize=%d:fontcolor=%s:x=%s:y=%s"
                 % (_lavfi_path(ff), _lavfi_path(tf), fs, tx["color"], x, y))
            if tx["box"]:
                f += ":box=1:boxcolor=%s:boxborderw=%d" % (tx["box_color"], max(2, fs // 4))
            r["text"] = f
    return r


def render_speech(host, work, key, text, with_sine, freq_hz, level_dbfs, log):
    """Sprachansage per ffmpeg-flite (Stimme slt) als WAV; optional leiser Sinus darunter. Wird in Schleife gespielt."""
    out = os.path.join(work, "speech_%s.wav" % key)
    ff = host.tool("ffmpeg")
    txt = str(text).replace("'", "").replace(":", " ").replace("\\", " ")
    cmd = [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "flite=text='%s':voice=slt" % txt]
    if with_sine:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=%s:sample_rate=48000" % freq_hz, "-filter_complex",
                "[0:a]apad=pad_dur=1.5,aresample=48000,volume=%sdB,aformat=channel_layouts=stereo[s];"
                "[1:a]volume=%sdB,aformat=channel_layouts=stereo[t];[s][t]amix=inputs=2:duration=first:normalize=0[a]"
                % (level_dbfs + 6, level_dbfs - 10), "-map", "[a]"]
    else:
        cmd += ["-af", "apad=pad_dur=1.5,aresample=48000,volume=%sdB,aformat=channel_layouts=stereo" % (level_dbfs + 6)]
    cmd += ["-ar", "48000", "-ac", "2", "-y", out]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
    if r.returncode != 0 or not os.path.isfile(out):
        log("warn", "Sprachansage (flite) fehlgeschlagen, nehme Sinus: %s" % r.stderr.decode("utf-8", "replace")[-200:])
        return None
    return out


def _lavfi_path(p):
    for ch in ("\\", ":", "'", ",", ";", "[", "]"):
        p = p.replace(ch, "\\" + ch)
    return p


def _amovie_loop(path, sr):
    """Tondatei endlos: amovie=loop=0 mit neu berechneten Zeitstempeln (asetpts=N/SR/TB) -> keine Spruenge
    an der Schleifenstelle (anders als -stream_loop, das die Dauer schaetzt)."""
    return (["-f", "lavfi", "-i", "amovie=%s:loop=0,aresample=%d,asetpts=N/SR/TB" % (_lavfi_path(path), sr)], "anull")


def audio_input(host, svc, aud, work, log):
    """Erzeugte Audioquelle -> (ffmpeg-Eingabeargumente, Audiofilter)."""
    kind = aud.get("kind", "sine")
    sr = svc["audio"]["samplerate"]
    lvl = float(aud.get("level_dbfs", -18))
    if kind == "file" and aud.get("file"):
        return _amovie_loop(host.media_path(aud["file"]), sr)
    if kind in ("speech", "sine+speech"):
        wav = render_speech(host, work, svc["key"], aud.get("text") or "DAB Lab test", kind == "sine+speech",
                            aud.get("freq_hz", 1000), lvl, log)
        if wav:
            return _amovie_loop(wav, sr)
        kind = "sine"
    if kind == "silence":
        return (["-f", "lavfi", "-i", "anullsrc=r=%d:cl=stereo" % sr], "anull")
    if kind == "noise":
        return (["-f", "lavfi", "-i", "anoisesrc=r=%d:c=pink:a=0.5" % sr], "volume=%sdB" % (lvl + 6))
    return (["-f", "lavfi", "-i", "sine=frequency=%s:sample_rate=%d" % (aud.get("freq_hz", 1000), sr)],
            "volume=%sdB,aformat=channel_layouts=stereo" % lvl)


def build_source(host, cfg, svc, work, sdir, log, render=False):
    """-> {"inputs": [[args...], ...], "vin": index|None, "ain": index, "vf": str, "af": str, "feeder": VideoFeeder|None,
            "info": {...}}  (Indizes relativ zur Liste "inputs")."""
    src = svc["source"]
    kind = src["kind"]
    is_tv = svc["type"] == "tv"
    inputs, vin, ain, vf, af, feeder, info = [], None, None, None, None, None, {"kind": kind}
    sr = svc["audio"]["samplerate"]
    if kind == "testpattern":
        if is_tv:
            w, h = svc["video"]["_wh"]
            inputs.append(["-f", "lavfi", "-i", "%s=size=%dx%d:rate=%s" % (src["pattern"], w, h, svc["video"]["fps"])])
            vin = 0
            vf = video_filter(svc, drawtext_filter(svc, src.get("text") or svc["name"], src.get("timestamp", True)))
        a_args, af = audio_input(host, svc, src["audio"], work, log)
        inputs.append(a_args)
        ain = len(inputs) - 1
    elif kind in ("file", "youtube"):
        if kind == "youtube":
            ent = youtube_find(host, src.get("url") or src.get("id"))
            if not ent:
                if render and not src.get("download", True):
                    raise host.RunnerError("YouTube-Video nicht geladen: %s" % (src.get("url") or src.get("id")))
                log("info", "YouTube-Download startet: %s" % (src.get("url") or src.get("id")))
                ent = youtube_download(host, src.get("url") or src.get("id"), src.get("max_height", 1080),
                                       progress=lambda p: log("info", "youtube", youtube=p), log=log)
            path = os.path.join(video_dir(host), ent["file"])
            info.update({"file": "media/video/" + ent["file"], "title": ent.get("title")})
        else:
            path = host.media_path(src["file"])
            if not os.path.isfile(path):
                raise host.RunnerError("Videodatei fehlt: %s" % src["file"])
            info["file"] = src["file"]
        pr = probe(host, path)
        info.update({k: pr.get(k) for k in ("duration_s", "width", "height", "has_audio", "has_video")})
        if src.get("loop", True) and src.get("loop_mode", "feeder") == "feeder" and not src.get("seek_s"):
            # Nahtlose Schleife: Feeder dekodiert die Datei Durchlauf fuer Durchlauf in Rohdaten-FIFOs; der Mux sieht
            # einen endlosen Strom mit festem Takt -> PTS/DTS/PCR laufen ohne Sprung weiter (kein -stream_loop).
            use_a = src["audio"]["kind"] == "source" and svc["audio"]["codec"] != "none"
            if use_a and not pr.get("has_audio", True):
                log("warn", "%s hat keine Tonspur, nehme Sinus" % os.path.basename(path))
                src["audio"]["kind"], use_a = "sine", False
            feeder = VideoFeeder(host, cfg, svc, work, sdir, log, audio=use_a, name="loop",
                                 items=[{"file": path, "title": info.get("title") or svc["eit"]["title"],
                                         "duration_s": pr.get("duration_s")}])
            feeder.fixed_eit = True
            info["loop_mode"] = "feeder"
            if is_tv:
                w, h = svc["video"]["_wh"]
                inputs.append(["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (w, h), "-r", str(svc["video"]["fps"]),
                               "-analyzeduration", "0", "-thread_queue_size", "64", "-i", feeder.fifo_v])
                vin = 0
                vf = video_filter(svc, drawtext_filter(svc, src.get("text"), src.get("timestamp", False))
                                  if (src.get("text") or src.get("timestamp")) else None)
            if use_a:
                inputs.append(["-f", "s16le", "-ar", str(sr), "-ac", "2", "-analyzeduration", "0", "-thread_queue_size", "256",
                               "-i", feeder.fifo_a])
                ain, af = len(inputs) - 1, "anull"
            else:
                a_args, af = audio_input(host, svc, src["audio"], work, log)
                inputs.append(a_args)
                ain = len(inputs) - 1
            return {"inputs": inputs, "vin": vin, "ain": ain, "vf": vf, "af": af, "feeder": feeder, "info": info}
        args = []
        if src.get("loop", True):
            args += ["-stream_loop", "-1"]
        if src.get("seek_s"):
            args += ["-ss", str(src["seek_s"])]
        inputs.append(args + ["-i", path])
        if is_tv:
            if not pr.get("has_video", True):
                raise host.RunnerError("%s enthaelt kein Video" % path)
            vin = 0
            vf = video_filter(svc, drawtext_filter(svc, src.get("text"), src.get("timestamp", False))
                              if (src.get("text") or src.get("timestamp")) else None)
        if src["audio"]["kind"] == "source" and pr.get("has_audio", True):
            ain, af = 0, "aresample=%d:async=1" % sr
        else:
            if src["audio"]["kind"] == "source":
                log("warn", "%s hat keine Tonspur, nehme Sinus" % os.path.basename(path))
                src["audio"]["kind"] = "sine"
            a_args, af = audio_input(host, svc, src["audio"], work, log)
            inputs.append(a_args)
            ain = len(inputs) - 1
    elif kind == "slideshow":
        imgs = [host.media_path(p) for p in src["images"]]
        missing = [p for p in imgs if not os.path.isfile(p)]
        if missing:
            raise host.RunnerError("Bilder fehlen: %s" % ", ".join(missing))
        lst = os.path.join(work, "slides_%s.txt" % svc["key"])
        with open(lst, "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            # Endlosschleife ohne -stream_loop: Liste fuer >= 48 h ausschreiben (concat-Zeitstempel bleiben stetig)
            reps = max(1, int(48 * 3600 / (src["interval_s"] * len(imgs))) + 1) if src.get("loop", True) else 1
            reps = min(reps, 200000 // max(1, len(imgs)))
            for _ in range(reps):
                for p in imgs:
                    f.write("file '%s'\nduration %.3f\n" % (p.replace("'", r"'\''"), src["interval_s"]))
            f.write("file '%s'\n" % imgs[-1].replace("'", r"'\''"))
        inputs.append(["-f", "concat", "-safe", "0", "-i", lst])
        vin = 0
        vf = video_filter(svc, drawtext_filter(svc, src.get("text") or svc["name"], src.get("timestamp", True))
                          if (src.get("text") or src.get("timestamp", True)) else None)
        a_args, af = audio_input(host, svc, src["audio"], work, log)
        inputs.append(a_args)
        ain = len(inputs) - 1
        info["images"] = len(imgs)
    elif kind == "folder":
        feeder = FolderFeeder(host, svc, work, log)
        w, h = svc["video"]["_wh"]
        # kleine Eingangswarteschlange: der Feeder schreibt jedes Bild erst, wenn der Mux es abholt -> geringe Latenz
        inputs.append(["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (w, h), "-r", str(svc["video"]["fps"]),
                       "-analyzeduration", "0", "-thread_queue_size", "2", "-i", feeder.fifo_v])
        vin = 0
        vf = video_filter(svc, drawtext_filter(svc, src.get("text"), src.get("timestamp", False))
                          if (src.get("text") or src.get("timestamp")) else None)
        a_args, af = audio_input(host, svc, src["audio"], work, log)
        inputs.append(a_args)
        ain = len(inputs) - 1
        info["folder"] = feeder.folder
    elif kind in ("playlist", "schedule"):
        use_a = svc["audio"]["codec"] != "none"
        if kind == "schedule":
            from .schedule import ScheduleFeeder
            feeder = ScheduleFeeder(host, cfg, svc, work, sdir, log, audio=use_a, render=render)
        else:
            feeder = VideoFeeder(host, cfg, svc, work, sdir, log, audio=use_a)
        w, h = svc["video"]["_wh"] if is_tv else (0, 0)
        if is_tv:
            inputs.append(["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (w, h), "-r", str(svc["video"]["fps"]),
                           "-analyzeduration", "0", "-thread_queue_size", "64", "-i", feeder.fifo_v])
            vin = 0
            vf = video_filter(svc, drawtext_filter(svc, src.get("text"), True) if src.get("text") else None)
        if use_a:
            inputs.append(["-f", "s16le", "-ar", str(sr), "-ac", "2", "-analyzeduration", "0", "-thread_queue_size", "256", "-i", feeder.fifo_a])
            ain = len(inputs) - 1
        af = "anull"
        if kind == "schedule":
            info["entries"] = feeder.n
        else:
            info["playlist"] = feeder.pl.get("name")
            info["items"] = feeder.n
    elif kind == "stream":
        # Live-Netzwerkquelle: Feeder liefert Rohbild/PCM mit festem Takt, auch wenn der Stream fehlt oder abreisst
        use_a = src["audio"]["kind"] == "source" and svc["audio"]["codec"] != "none"
        feeder = StreamFeeder(host, svc, work, sdir, log, audio=use_a)
        w, h = svc["video"]["_wh"]
        inputs.append(["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (w, h), "-r", str(svc["video"]["fps"]),
                       "-analyzeduration", "0", "-thread_queue_size", "64", "-i", feeder.fifo_v])
        vin = 0
        vf = video_filter(svc, drawtext_filter(svc, src.get("text"), src.get("timestamp", False))
                          if (src.get("text") or src.get("timestamp")) else None)
        if use_a:
            inputs.append(["-f", "s16le", "-ar", str(sr), "-ac", "2", "-analyzeduration", "0", "-thread_queue_size", "256",
                           "-i", feeder.fifo_a])
            ain, af = len(inputs) - 1, "anull"
        else:
            a_args, af = audio_input(host, svc, src["audio"], work, log)
            inputs.append(a_args)
            ain = len(inputs) - 1
        info.update({"stream": feeder.label, "mode": src.get("mode"), "frontend": "windows" if feeder.windows else "local"})
    return {"inputs": inputs, "vin": vin, "ain": ain, "vf": vf, "af": af, "feeder": feeder, "info": info}


# ---------------------------------------------------------------------------------------------
# Video-Playlist-Feeder
# ---------------------------------------------------------------------------------------------
class VideoFeeder:
    """Dekodiert Titel fuer Titel in Rohdaten-FIFOs (yuv420p + s16le), die der Mux-ffmpeg mit festem Takt liest.
    Dadurch laufen PTS/DTS/PCR ueber Titelwechsel und Schleifen hinweg lueckenlos weiter (der Mux sieht einen einzigen,
    endlosen Rohdatenstrom). Wird fuer Playlists und fuer geschleifte Dateien/YouTube-Videos verwendet.
    items: Eintragsliste statt Playlist-Datei; audio=False: Ton kommt nicht aus den Dateien (eigene Tonquelle)."""

    def __init__(self, host, cfg, svc, work, sdir, log, items=None, audio=True, name=None):
        self._init_io(host, cfg, svc, work, sdir, log, audio)
        if items is not None:
            self.pl = {"name": name or "loop", "items": [dict(it) for it in items]}
        else:
            self.pl = host.load_playlist(self.src["playlist"], probe=False)
        for it in self.pl["items"]:
            if not it.get("duration_s"):
                it["duration_s"] = probe(host, host.media_path(it["file"])).get("duration_s")
        self.n = len(self.pl["items"])
        if self.n == 0:
            raise host.RunnerError("Playlist '%s' ist leer" % self.src["playlist"])
        self.order = list(range(self.n))
        if self.src.get("shuffle"):
            random.shuffle(self.order)
        self.pos, self.cur = 0, None
        self.track_start = time.time()

    def _init_io(self, host, cfg, svc, work, sdir, log, audio):
        """Gemeinsamer Teil (auch ScheduleFeeder): FIFOs, Zaehler, Dateien im Sitzungsordner."""
        self.host, self.cfg, self.svc, self.log = host, cfg, svc, log
        self.src = svc["source"]
        self.key = svc["key"]
        self.sdir = sdir
        self.use_audio = bool(audio)
        self.proc = None
        self.is_tv = svc["type"] == "tv"
        self.w, self.h = svc["video"]["_wh"] if self.is_tv else (0, 0)
        self.fps = float(svc["video"]["fps"]) if self.is_tv else 25.0
        self.sr = int(svc["audio"]["samplerate"])
        self.frame_bytes = self.w * self.h * 3 // 2
        self.fifo_v = os.path.join(work, "pl_v_%s.yuv" % self.key)
        self.fifo_a = os.path.join(work, "pl_a_%s.pcm" % self.key)
        if self.is_tv:
            os.mkfifo(self.fifo_v)
        if self.use_audio:
            os.mkfifo(self.fifo_a)
        self.cmdfile = os.path.join(sdir, "playlist_%s.cmd" % self.key)
        self.npfile = os.path.join(sdir, "now_playing_%s.json" % self.key)
        self.eitfile = os.path.join(sdir, "eit", "eit_%s.xml" % self.key)
        self.frames = self.samples = 0          # gesamt, fuer A/V-Ausgleich
        self.frames_track = self.samples_track = 0
        # Grenze (absolute Bild-/Samplezahl): _drain gibt bis dorthin weiter, verwirft den Rest und setzt hit_v/hit_a
        self.lim_v = self.lim_a = None
        self.hit_v = self.hit_a = False
        self.stop_evt = threading.Event()
        self.fv = self.fa = None
        self.threads = []
        self.black = None
        self.eit_lock = threading.Lock()
        self.eit_override = {}

    # -- Zustand --
    def now_playing(self):
        it = self.pl["items"][self.cur] if self.cur is not None else {}
        return {"service": self.key, "index": self.cur, "title": it.get("title"), "artist": it.get("artist"),
                "file": it.get("file"), "elapsed_s": round(self.samples_track / float(self.sr), 1),
                "duration_s": it.get("duration_s"), "playlist": self.pl.get("name"), "position": self.pos}

    def _next_item(self, step=1):
        return self.pl["items"][self.order[(self.pos + step) % self.n]]

    def write_eit(self):
        if not self.cfg["ts"].get("eit", True) or self.cur is None:
            return
        if getattr(self, "fixed_eit", False):
            return  # Datei-Schleife: EIT kommt aus der Konfiguration (chain.write_tables), nicht je Durchlauf
        it = self.pl["items"][self.cur]
        nxt = self._next_item(1) if (self.src.get("loop", True) or self.pos + 1 < self.n) else None
        with self.eit_lock:
            o = self.eit_override
            pres = {"title": o.get("title") or it.get("title"), "text": o.get("text") or
                    (it.get("artist") and "%s - %s" % (it.get("artist"), it.get("title"))) or self.svc["eit"]["text"],
                    "start": int(self.track_start), "duration_s": it.get("duration_s") or 300}
            fol = ({"title": nxt.get("title"), "text": nxt.get("artist") or "", "duration_s": nxt.get("duration_s") or 300}
                   if nxt else None)
        try:
            os.makedirs(os.path.dirname(self.eitfile), exist_ok=True)
            si.write_eit(self.host, self.eitfile, self.cfg, self.svc, pres, fol)
        except Exception as e:
            self.log("warn", "EIT-Datei: %s" % e)

    def set_eit_override(self, title=None, text=None, clear=False):
        """ctl-EIT fuer die Playlist: gilt fuer alle folgenden Titel, bis {"eit":{"service":..,"clear":true}} kommt."""
        with self.eit_lock:
            if clear:
                self.eit_override = {}
            if title is not None:
                self.eit_override["title"] = title
            if text is not None:
                self.eit_override["text"] = text
        self.write_eit()

    def _cmd(self):
        try:
            with open(self.cmdfile) as f:
                c = json.load(f)
            os.unlink(self.cmdfile)
            return c
        except Exception:
            return None

    # -- Titel --
    def _start(self, idx):
        it = self.pl["items"][idx]
        self.cur = idx
        self.frames_track = self.samples_track = 0
        self.track_start = time.time()
        ff = self.host.tool("ffmpeg")
        path = self.host.media_path(it["file"])
        cmd = [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2", "-i", path]
        ra, wa = os.pipe()
        pass_fds = [wa]
        if self.is_tv:
            cmd += ["-map", "0:v:0", "-vf", video_filter(self.svc), "-an", "-f", "rawvideo", "pipe:1"]
        af = "aresample=%d:async=1" % self.sr
        if self.src.get("normalize", False):
            af = "loudnorm=I=-23:TP=-1.5:LRA=11," + af
        if self.use_audio:
            tlim = []
            if self.is_tv:
                # Ton exakt auf die Bildlaenge bringen (laengeren Ton abschneiden, kuerzeren mit Stille auffuellen):
                # so bleibt die Schleife/der Titelwechsel ohne Schwarzbild oder Standbild
                if "_vdur" not in it:
                    it["_vdur"] = probe(self.host, path).get("video_duration_s")
                if it.get("_vdur"):
                    af += ",apad"
                    tlim = ["-t", "%.6f" % it["_vdur"]]
            cmd += ["-map", "0:a:0?", "-vn", "-af", af, "-ac", "2", "-ar", str(self.sr)] + tlim + ["-f", "s16le", "pipe:%d" % wa]
        lf = open(os.path.join(self.sdir, "playlist_%s.log" % self.key), "ab")
        lf.write(("\n=== %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(cmd))).encode())
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if self.is_tv else subprocess.DEVNULL, stderr=lf,
                                     stdin=subprocess.DEVNULL, pass_fds=pass_fds)
        os.close(wa)
        lf.close()
        self.proc_a = os.fdopen(ra, "rb", 0)
        np_ = self.now_playing()
        try:
            self.host.atomic_write(self.npfile, json.dumps(np_, ensure_ascii=False))
        except Exception:
            pass
        self.log("info", "now_playing", now_playing=np_)
        self.write_eit()

    def kill(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=2)
            except Exception:
                pass

    # Entkopplung: Lesethreads leeren die Pipes des Titel-ffmpeg in begrenzte Warteschlangen, Schreibthreads fuellen
    # die FIFOs. Ohne diese Pufferung verklemmt die Kette: der Mux-ffmpeg liest beim Start ~0,7 s Audio voraus (Thread-
    # Queue 8 Pakete), stoppt dann das Audio-Lesen, bis Video nachkommt; der Titel-ffmpeg haengt aber an der vollen
    # Audio-Pipe und liefert kein Video mehr (64-KiB-Pipes).
    def _drain(self, rf, q, is_video):
        rem = b""
        unit = self.frame_bytes if is_video else 4
        chunk = unit if is_video else (1 << 16)
        first = True
        while not self.stop_evt.is_set():
            try:
                b = rf.read(1 << 18)
            except Exception:
                b = b""
            if not b:
                break
            if rem:
                b = rem + b
            n = len(b) // unit * unit
            rem = b[n:]
            b = b[:n]
            pos = 0
            while pos < len(b) and not self.stop_evt.is_set():
                part = b[pos:pos + chunk]
                pos += len(part)
                lim = self.lim_v if is_video else self.lim_a
                if lim is not None:
                    room = max(0, lim - (self.frames if is_video else self.samples))
                    if len(part) // unit >= room:
                        part = part[:room * unit]
                        if is_video:
                            self.hit_v = True
                        else:
                            self.hit_a = True
                    if not part:
                        continue        # hinter der Grenze: weiterlesen und verwerfen (Titel-ffmpeg nicht blockieren)
                while not self.stop_evt.is_set():
                    try:
                        q.put(part, timeout=0.5)
                        break
                    except queue.Full:
                        pass
                k = len(part) // unit
                if is_video:
                    self.frames += k
                    self.frames_track += k
                else:
                    self.samples += k
                    self.samples_track += k

    def _sink(self, q, wf):
        while not self.stop_evt.is_set():
            try:
                b = q.get(timeout=0.5)
            except queue.Empty:
                continue
            mv = memoryview(b)
            try:
                while mv:
                    n = wf.write(mv)
                    mv = mv[n:]
            except Exception:
                if not self.stop_evt.is_set():
                    self.log("warn", "feeder %s: FIFO-Schreibfehler" % self.key)
                self.stop_evt.set()
                return

    def _pad(self):
        """Nach einem Titel: kuerzere Spur auffuellen (Schwarzbild bzw. Stille), damit A/V exakt gleich lang bleiben."""
        if not self.is_tv or not self.use_audio:
            return
        tv, ta = self.frames / self.fps, self.samples / float(self.sr)
        if tv < ta:
            n = int(round((ta - tv) * self.fps))
            if self.black is None:
                self.black = bytes([16]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
            for _ in range(n):
                self.qv.put(self.black)
            self.frames += n
        elif ta < tv:
            n = int(round((tv - ta) * self.sr))
            self.qa.put(bytes(n * 4))
            self.samples += n

    def _apply(self, c):
        a = c.get("action")
        if a == "next":
            self.pos = (self.pos + 1) % self.n
        elif a == "prev":
            self.pos = (self.pos - 1) % self.n
        elif a == "jump":
            i = int(c.get("index", 0))
            if 0 <= i < self.n:
                self.pos = self.order.index(i)

    def _open_io(self):
        # FIFOs mit O_RDWR oeffnen: blockiert nicht. (ffmpeg oeffnet seine Eingaenge nacheinander und liest schon
        # aus dem ersten, bevor es den zweiten oeffnet - ein blockierendes open() beider FIFOs wuerde verklemmen.)
        if self.is_tv:
            self.fv = os.fdopen(os.open(self.fifo_v, os.O_RDWR), "wb", buffering=0)
        if self.use_audio:
            self.fa = os.fdopen(os.open(self.fifo_a, os.O_RDWR), "wb", buffering=0)
        # Hinweis: F_SETPIPE_SZ auf diesen FIFOs NICHT setzen - unter WSL1 liest ffmpeg danach nichts mehr
        # (gemessen: Feeder fuellt 4 MiB, Leser bleibt leer). 64 KiB reichen, Durchsatz > 1 GB/s.
        self.qv = queue.Queue(maxsize=max(25, int(round(self.fps * 2))))                  # ~2 s Video
        self.qa = queue.Queue(maxsize=8)                                                    # 8 x 64 KiB ~ 2,7 s Audio
        sinks = []
        if self.is_tv:
            sinks.append(threading.Thread(target=self._sink, args=(self.qv, self.fv), daemon=True))
        if self.use_audio:
            sinks.append(threading.Thread(target=self._sink, args=(self.qa, self.fa), daemon=True))
        for t in sinks:
            t.start()

    def _close_io(self):
        for f in (self.fv, self.fa):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass

    def run(self):
        try:
            self._open_io()
            self._start(self.order[self.pos])
            while not self.stop_evt.is_set():
                th = []
                if self.is_tv:
                    th.append(threading.Thread(target=self._drain, args=(self.proc.stdout, self.qv, True), daemon=True))
                if self.use_audio:
                    th.append(threading.Thread(target=self._drain, args=(self.proc_a, self.qa, False), daemon=True))
                for t in th:
                    t.start()
                cmd = None
                while any(t.is_alive() for t in th) and not self.stop_evt.is_set():
                    for t in th:
                        t.join(0.2)
                    cmd = self._cmd()
                    if cmd and cmd.get("action") == "eit":
                        self.set_eit_override(cmd.get("title"), cmd.get("text"), cmd.get("clear", False))
                        cmd = None
                    if cmd:
                        self.kill()
                        for t in th:
                            t.join(3)
                        break
                if self.stop_evt.is_set():
                    break
                self.proc.wait()
                try:
                    self.proc_a.close()
                except Exception:
                    pass
                if cmd:
                    self._apply(cmd)
                else:
                    self.pos += 1
                    if self.pos >= self.n:
                        self.pos = 0
                        if not self.src.get("loop", True):
                            self.log("info", "Playlist zu Ende (loop=false): Schwarzbild/Stille bis zum naechsten ctl")
                            self._pad()
                            if not self._idle():
                                break
                            continue
                        if self.src.get("shuffle"):
                            random.shuffle(self.order)
                self._pad()
                self._start(self.order[self.pos])
        except Exception as e:
            if not self.stop_evt.is_set():
                self.log("error", "Playlist-Feeder %s: %s" % (self.key, e))
        finally:
            self.kill()
            self._close_io()

    def _idle(self):
        """Nach dem Ende (loop=false): Schwarz und Stille, bis ein ctl kommt. True = weiter mit neuem Titel."""
        if self.black is None and self.is_tv:
            self.black = bytes([16]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
        spf = int(round(self.sr / self.fps))
        while not self.stop_evt.is_set():
            if self.is_tv:
                self.qv.put(self.black)
                self.frames += 1
                if self.use_audio:
                    self.qa.put(bytes(spf * 4))
                    self.samples += spf
            else:
                self.qa.put(bytes(self.sr // 10 * 4))
                self.samples += self.sr // 10
            c = self._cmd()
            if c:
                self._apply(c)
                self._start(self.order[self.pos])
                return True
        return False

    def start(self):
        t = threading.Thread(target=self.run, daemon=True, name="feeder-" + self.key)
        t.start()
        self.threads.append(t)

    def stop(self):
        self.stop_evt.set()
        self.kill()


# ---------------------------------------------------------------------------------------------
# Bildordner (Infoscreen): immer das neueste Bild eines Ordners
# ---------------------------------------------------------------------------------------------
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff")


def host_path(host, p):
    """Windows-Pfad aus dem Dashboard (C:\\Infoscreen) -> /mnt/c/Infoscreen; sonst wie media_path."""
    m = re.match(r"^([A-Za-z]):[\\/]?(.*)$", str(p or ""))
    if m and os.sep == "/":
        return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/"))
    return host.media_path(p)


class FolderFeeder:
    """Schreibt Rohbilder (yuv420p) in eine FIFO, die der Mux-ffmpeg mit fester Bildrate liest. Jedes Bild ist das zum
    Schreibzeitpunkt neueste Bild des Ordners (Aenderungszeit; dieselbe Datei darf auch ueberschrieben werden). Der
    Ordner wird alle poll_s Sekunden geprueft; ein neues Bild wird erst dekodiert, wenn es >= 0,15 s alt ist (halb
    geschriebene Dateien), Dekodierfehler -> naechster Versuch beim naechsten Durchlauf. Bis zum ersten Bild: schwarz."""
    SETTLE_S = 0.15

    def __init__(self, host, svc, work, log):
        self.host, self.svc, self.log = host, svc, log
        self.key = svc["key"]
        self.src = svc["source"]
        self.folder = host_path(host, self.src["folder"])
        self.poll_s = float(self.src.get("poll_s", 0.5))
        self.w, self.h = svc["video"]["_wh"]
        self.frame_bytes = self.w * self.h * 3 // 2
        self.frame = bytes([16]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
        self.cur = None              # (Pfad, mtime_ns, Groesse) des gesendeten Bildes
        self.cur_t = None            # Zeitpunkt, ab dem es gesendet wird
        self.failed = None
        self.warned = False
        self.fifo_v = os.path.join(work, "folder_v_%s.yuv" % self.key)
        os.mkfifo(self.fifo_v)
        self.stop_evt = threading.Event()
        self.threads = []

    def now_playing(self):
        return {"service": self.key, "title": os.path.basename(self.cur[0]) if self.cur else "", "file": self.cur[0] if self.cur else None,
                "folder": self.folder, "elapsed_s": round(time.time() - self.cur_t, 1) if self.cur_t else None}

    def _newest(self):
        best = None
        try:
            with os.scandir(self.folder) as it:
                for e in it:
                    if not e.name.lower().endswith(IMAGE_EXT) or e.name.startswith("."):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    if st.st_size and (best is None or st.st_mtime_ns > best[1]):
                        best = (e.path, st.st_mtime_ns, st.st_size)
        except OSError as e:
            if not self.warned:
                self.log("warn", "Bildordner %s: %s" % (self.folder, e))
                self.warned = True
            return None
        self.warned = False
        return best

    def _decode(self, path):
        w, h = self.w, self.h
        cmd = [self.host.tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-err_detect", "explode",
               "-i", path, "-frames:v", "1",
               "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease:flags=bicubic,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                      "format=yuv420p" % (w, h, w, h), "-f", "rawvideo", "pipe:1"]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        except Exception as e:
            return None, str(e)
        if r.returncode != 0 or len(r.stdout) != self.frame_bytes:
            return None, r.stderr.decode("utf-8", "replace").strip()[-200:] or "rc=%d" % r.returncode
        return r.stdout, None

    def _watch(self):
        while not self.stop_evt.is_set():
            n = self._newest()
            if n and n != self.cur and n != self.failed and time.time() - n[1] / 1e9 >= self.SETTLE_S:
                frame, err = self._decode(n[0])
                if frame:
                    self.frame, self.cur, self.cur_t, self.failed = frame, n, time.time(), None
                    self.log("info", "now_playing", now_playing=self.now_playing())
                else:
                    self.failed = n          # erst bei geaenderter Datei erneut versuchen
                    self.log("warn", "Bildordner: %s nicht lesbar: %s" % (os.path.basename(n[0]), err))
            self.stop_evt.wait(self.poll_s)

    def _write(self):
        try:
            # O_RDWR wie VideoFeeder: open() blockiert nicht, auch wenn der Mux die FIFO noch nicht geoeffnet hat
            with os.fdopen(os.open(self.fifo_v, os.O_RDWR), "wb", buffering=0) as f:
                while not self.stop_evt.is_set():
                    mv = memoryview(self.frame)
                    while mv:
                        mv = mv[f.write(mv):]
        except Exception as e:
            if not self.stop_evt.is_set():
                self.log("warn", "Bildordner-Feeder %s: %s" % (self.key, e))

    def start(self):
        for fn in (self._watch, self._write):
            t = threading.Thread(target=fn, daemon=True, name="folder-%s" % self.key)
            t.start()
            self.threads.append(t)

    def stop(self):
        self.stop_evt.set()


# ---------------------------------------------------------------------------------------------
# Netzwerk-Stream (SRT/UDP/RTP/RTMP/HLS, Pull oder Listen fuer OBS)
# ---------------------------------------------------------------------------------------------
_RE_IN_V = re.compile(r"Stream #0:\d+.*?: Video: (\w+).*?, (\d{2,5})x(\d{2,5})")
_RE_FPS = re.compile(r"([\d.]+) (?:fps|tbr)")
_RE_IN_A = re.compile(r"Stream #0:\d+.*?: Audio: (\w+).*?, (\d+) Hz, ([\w.()]+)")
_RE_CRED = re.compile(r"(//)[^/@]+@")


def in_wsl():
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def stream_url(src):
    """-> (Zusatzargumente vor -i, Eingangs-URL) des Front-End-ffmpeg."""
    if src.get("mode") == "listen":
        port = int(src["port"])
        if src["protocol"] == "rtmp":
            return ["-listen", "1"], "rtmp://0.0.0.0:%d/live/%s" % (port, src.get("key") or "live")
        return [], "srt://0.0.0.0:%d?mode=listener&transtype=live&latency=%d" % (port, int(src.get("latency_ms", 200)) * 1000)
    url = str(src["url"]).strip()
    scheme = url.split(":", 1)[0].lower()
    pre = []
    if scheme in ("udp", "rtp") and "fifo_size" not in url:
        # Ringpuffer im Empfangsthread: kurze Stockungen des Lesers verlieren keine Pakete
        url += ("&" if "?" in url else "?") + "fifo_size=1000000&overrun_nonfatal=1"
    if scheme == "srt" and "latency=" not in url:
        url += ("&" if "?" in url else "?") + "latency=%d" % (int(src.get("latency_ms", 200)) * 1000)
    if scheme in ("http", "https"):
        pre = ["-re"]      # HLS laedt Segmente sonst schneller als Echtzeit (der Jitterpuffer liefe ueber)
    return pre, url


def stream_label(src):
    if src.get("mode") == "listen":
        return "%s-Listener :%d" % (src["protocol"].upper(), int(src["port"]))
    return _RE_CRED.sub(r"\1***@", str(src.get("url") or ""))


class StreamFeeder:
    """Live-Netzwerkquelle mit festem Ausgabetakt. Ein Front-End-ffmpeg empfaengt den Stream (Pull: srt/udp/rtp/rtmp/
    http(s)/HLS; Listen: SRT- oder RTMP-Server fuer OBS) und remuxt ihn unveraendert (-c copy) als MPEG-TS; ein
    Dekoder-ffmpeg macht daraus Rohbild (yuv420p im Zielformat) und PCM. Unter WSL laeuft das Front-End als
    Windows-ffmpeg: Ports binden dann direkt unter Windows (aus dem LAN erreichbar, auch bei WSL2-NAT), Multicast kommt an.

    Der Taktgeber gibt Bild fuer Bild nach der Wanduhr aus (hoechstens LEAD_S voraus) und fuellt die FIFOs des Mux-ffmpeg
    lueckenlos: Live-Bilder aus einem kleinen Jitterpuffer, bei Aussetzern bis hold_s das letzte Bild, danach die
    Ersatztafel (bzw. Testbild/letztes Bild/Schwarz) und Stille. Die Verbindung wird automatisch neu aufgebaut."""
    LEAD_S = 1.0          # Vorlauf vor der Wanduhr (genug fuer Encoder-Lookahead und muxdelay)
    CONNECT_S = 20.0      # Pull: ohne Eingangsdaten nach so vielen Sekunden neu verbinden

    def __init__(self, host, svc, work, sdir, log, audio=True):
        self.host, self.svc, self.log, self.sdir = host, svc, log, sdir
        self.key = svc["key"]
        self.src = svc["source"]
        self.use_audio = bool(audio)
        self.w, self.h = svc["video"]["_wh"]
        self.fps = float(svc["video"]["fps"])
        self.sr = int(svc["audio"]["samplerate"])
        self.frame_bytes = self.w * self.h * 3 // 2
        self.hold_s = float(self.src.get("hold_s", 1.0))
        self.stall_s = float(self.src.get("stall_s", 5.0))
        self.pre_n = max(1, int(round(float(self.src.get("buffer_ms", 300)) / 1000.0 * self.fps)))
        self.max_n = self.pre_n + int(round(self.fps))                   # darueber: aelteste Bilder verwerfen
        self.fallback = self.src.get("fallback", "slate")
        self.fifo_v = os.path.join(work, "st_v_%s.yuv" % self.key)
        self.fifo_a = os.path.join(work, "st_a_%s.pcm" % self.key)
        os.mkfifo(self.fifo_v)
        if self.use_audio:
            os.mkfifo(self.fifo_a)
        fe = self.src.get("frontend", "auto")
        from . import nvenc
        self.windows = fe == "windows" or (fe == "auto" and in_wsl() and nvenc.available())
        self.fe_exe = nvenc.win_exe() if self.windows else host.tool("ffmpeg")
        self.label = stream_label(self.src)
        self.lock = threading.Lock()
        self.vbuf = []
        self.abuf = bytearray()
        self.last = None              # letztes Live-Bild
        self.last_rx = 0.0            # Zeit des letzten empfangenen Bildes
        self.live = False             # Live-Bilder werden ausgegeben (Jitterpuffer war gefuellt)
        self.state = "waiting"        # waiting | live | lost
        self.since = time.time()
        self.conns = 0                # Verbindungen, die Bilder geliefert haben
        self.dropouts = 0
        self.rx = self.dropped = self.repeated = self.fill = 0
        self.k = 0
        self.vinfo = self.ainfo = None
        self.procs = []
        self.stop_evt = threading.Event()
        self.threads = []
        self.slate = self._render_fallback()

    # -- Ersatzbild --
    def _render_fallback(self):
        black = bytes([16]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
        if self.fallback == "black":
            return black
        w, h = self.w, self.h
        src = ("smptehdbars=s=%dx%d" if self.fallback == "testpattern" else "color=c=0x1b2230:s=%dx%d") % (w, h)
        ff = font_file()
        vf = []
        if ff:
            t1 = drawtext_escape(self.src.get("fallback_text") or "Kein Signal")
            t2 = drawtext_escape(self.svc["name"])
            box = ":box=1:boxcolor=black@0.6:boxborderw=%d" % max(6, h // 60) if self.fallback == "testpattern" else ""
            vf += ["drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=white:x=(w-tw)/2:y=h/2-th%s" % (ff, t1, h // 9, box),
                   "drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=0xb0b8c8:x=(w-tw)/2:y=h/2+%d%s" % (ff, t2, h // 22, h // 20, box)]
        cmd = [self.host.tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", src,
               "-frames:v", "1", "-vf", ",".join(vf + ["format=yuv420p"]), "-f", "rawvideo", "pipe:1"]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if len(r.stdout) == self.frame_bytes:
                return r.stdout
            self.log("warn", "Ersatzbild: %s" % r.stderr.decode("utf-8", "replace")[-200:])
        except Exception as e:
            self.log("warn", "Ersatzbild: %s" % e)
        return black

    # -- Zustand --
    def now_playing(self):
        st = {"state": self.state, "mode": self.src.get("mode", "pull"), "url": self.label, "since": int(self.since),
              "frontend": "windows" if self.windows else "local", "video": self.vinfo, "audio": self.ainfo,
              "connections": self.conns, "reconnects": max(0, self.conns - 1), "dropouts": self.dropouts,
              "frames_rx": self.rx, "frames_dropped": self.dropped, "frames_repeated": self.repeated,
              "frames_fallback": self.fill}
        return {"service": self.key, "title": self.label, "elapsed_s": round(time.time() - self.since, 1), "stream": st}

    def _set_state(self, s):
        if s == self.state:
            return
        if self.state == "live":
            self.dropouts += 1
        self.state, self.since = s, time.time()
        self.log("info" if s == "live" else "warn",
                 "Stream %s: %s" % (self.key, {"live": "Live-Bild", "lost": "Signal fehlt, Ersatzbild",
                                               "waiting": "warte auf Stream"}[s]), now_playing=self.now_playing())

    # -- Empfang --
    def _fe_cmd(self):
        pre, url = stream_url(self.src)
        return ([self.fe_exe, "-hide_banner", "-loglevel", "info", "-nostats", "-fflags", "+genpts+discardcorrupt"] + pre
                + ["-i", url, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "mpegts", "pipe:1"])

    def _fe_stderr(self, p, lf, hdr, ev):
        """stderr des Front-End: Eingangsformat (Codec, Aufloesung) bis zur Zeile 'Output #0' auswerten."""
        inp = False
        try:
            for raw in iter(p.stderr.readline, b""):
                lf.write(raw)
                s = raw.decode("utf-8", "replace").rstrip()
                if s.startswith("Input #0"):
                    inp = True
                elif s.startswith("Output #0") or s.startswith("Stream mapping"):
                    inp = False
                    ev.set()
                elif inp and hdr.get("v") is None and ": Video: " in s:
                    m, f = _RE_IN_V.search(s), _RE_FPS.search(s)
                    hdr["v"] = ("%s %sx%s" % m.groups() if m else s.split(": Video: ", 1)[1][:40]) + \
                        (" %s fps" % f.group(1) if f else "")
                elif inp and hdr.get("a") is None and ": Audio: " in s:
                    m = _RE_IN_A.search(s)
                    hdr["a"] = "%s %s Hz %s" % m.groups() if m else s.split(": Audio: ", 1)[1][:40]
        except Exception:
            pass
        finally:
            ev.set()

    def _read_video(self, f):
        while not self.stop_evt.is_set():
            b = f.read(self.frame_bytes)
            if not b or len(b) < self.frame_bytes:
                break
            with self.lock:
                self.vbuf.append(b)
                if len(self.vbuf) > self.max_n:          # Quelle schneller als die Wanduhr: aeltestes Bild weg
                    del self.vbuf[0]
                    self.dropped += 1
                self.rx += 1
                self.last_rx = time.time()

    def _read_audio(self, f):
        cap = int(self.max_n / self.fps * self.sr) * 4
        with f:
            while not self.stop_evt.is_set():
                b = f.read(1 << 14)
                if not b:
                    break
                with self.lock:
                    self.abuf += b
                    self._trim(cap)

    def _trim(self, keep):
        """Tonpuffer vorne auf hoechstens keep Byte kuerzen (nur ganze Stereo-Samples, sonst verrutscht der Takt)."""
        n = len(self.abuf) - keep
        if n > 0:
            del self.abuf[:n + (-n) % 4]

    def _kill(self, p, windows=False):
        if p is None or p.poll() is not None:
            return
        if windows:
            try:
                p.stdin.write(b"q")      # Windows-ffmpeg sauber beenden (gibt den Port frei)
                p.stdin.flush()
                p.wait(timeout=2)
                return
            except Exception:
                pass
        try:
            p.kill()
            p.wait(timeout=3)
        except Exception:
            pass

    def _connect_once(self):
        """Eine Verbindung: Front-End starten, auf Eingangsdaten warten, Dekoder anhaengen, bis Abbruch/Stillstand.
        -> True, wenn Bilder kamen."""
        listen = self.src.get("mode") == "listen"
        lf = open(os.path.join(self.sdir, "stream_%s.log" % self.key), "ab")
        cmd = self._fe_cmd()
        lf.write(("\n=== %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(cmd))).encode())
        lf.flush()
        fe = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.procs = [fe]
        hdr, ev = {}, threading.Event()
        threading.Thread(target=self._fe_stderr, args=(fe, lf, hdr, ev), daemon=True).start()
        t0 = time.time()
        dec = None
        try:
            while not ev.wait(0.5):
                if self.stop_evt.is_set() or fe.poll() is not None:
                    return False
                if not listen and time.time() - t0 > self.CONNECT_S:
                    lf.write(b"--- keine Eingangsdaten, neuer Versuch\n")
                    return False
            if self.stop_evt.is_set() or fe.poll() is not None:
                return False
            has_v, has_a = bool(hdr.get("v")), bool(hdr.get("a")) and self.use_audio
            self.vinfo, self.ainfo = hdr.get("v"), hdr.get("a")
            if not has_v and not has_a:
                return False
            dcmd = [self.host.tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
                    "-probesize", "2M", "-analyzeduration", "1500000", "-fflags", "+discardcorrupt",
                    "-f", "mpegts", "-i", "pipe:0"]
            ra = wa = None
            if has_v:
                dcmd += ["-map", "0:v:0", "-vf", video_filter(self.svc), "-an", "-f", "rawvideo", "pipe:1"]
            if has_a:
                ra, wa = os.pipe()
                dcmd += ["-map", "0:a:0", "-vn", "-af", "aresample=%d:async=1000" % self.sr, "-ac", "2", "-ar", str(self.sr),
                         "-f", "s16le", "pipe:%d" % wa]
            lf.write(("=== Dekoder: %s\n" % " ".join(dcmd)).encode())
            lf.flush()
            dec = subprocess.Popen(dcmd, stdin=fe.stdout, stdout=subprocess.PIPE if has_v else subprocess.DEVNULL,
                                   stderr=lf, pass_fds=(wa,) if wa is not None else ())
            fe.stdout.close()
            self.procs = [fe, dec]
            readers = []
            if has_v:
                readers.append(threading.Thread(target=self._read_video, args=(dec.stdout,), daemon=True))
            if has_a:
                os.close(wa)
                readers.append(threading.Thread(target=self._read_audio, args=(os.fdopen(ra, "rb", 0),), daemon=True))
            for t in readers:
                t.start()
            self.log("info", "Stream %s: Eingang %s%s" % (self.key, self.vinfo or "ohne Bild",
                                                          ", Ton %s" % self.ainfo if self.ainfo else ", ohne Ton"))
            t1, counted = time.time(), False
            while any(t.is_alive() for t in readers) and not self.stop_evt.is_set():
                self.stop_evt.wait(0.5)
                if self.last_rx > t1 and not counted:
                    counted = True
                    self.conns += 1
                ref = self.last_rx if self.last_rx > t1 else t1
                if has_v and time.time() - ref > (self.stall_s if counted else max(self.stall_s, 10.0)):
                    lf.write(b"--- keine Bilder mehr, neu verbinden\n")
                    break
            return counted
        finally:
            self._kill(dec)
            self._kill(fe, self.windows)
            try:
                lf.close()
            except Exception:
                pass

    def _conn_loop(self):
        while not self.stop_evt.is_set():
            try:
                ok = self._connect_once()
            except Exception as e:
                ok = False
                self.log("warn", "Stream %s: %s" % (self.key, e))
            # Listener sofort wieder oeffnen; Pull nach kurzer Pause (nicht im Sekundentakt anklopfen)
            self.stop_evt.wait(0.3 if self.src.get("mode") == "listen" else (1.0 if ok else 3.0))

    # -- Taktgeber --
    def _next(self):
        """-> (Bild, PCM) fuer den naechsten Ausgabeschritt (ein Bild, dazu genau die passende Zahl Samples)."""
        now = time.time()
        with self.lock:
            if not self.live and len(self.vbuf) >= self.pre_n and now - self.last_rx < 1.0:
                self.live = True
                # Ton auf die Laenge des Bildpuffers kuerzen: beide Spuren beginnen damit etwa gleichzeitig
                self._trim(int(len(self.vbuf) / self.fps * self.sr) * 4)
            frame = None
            if self.live and self.vbuf:
                frame = self.vbuf.pop(0)
                self.last = frame
            elif self.live and now - self.last_rx < self.hold_s:
                frame = self.last                       # kurzer Aussetzer: letztes Bild halten
                self.repeated += 1
            elif self.live:
                self.live = False                       # Signal weg: Ersatzbild, Jitterpuffer neu fuellen
            self.k += 1
            need = (int(self.k * self.sr / self.fps) - int((self.k - 1) * self.sr / self.fps)) * 4
            if self.live and len(self.abuf) >= 4:
                take = min(need, len(self.abuf) // 4 * 4)
                pcm = bytes(self.abuf[:take]) + bytes(need - take)     # zu wenig Ton: mit Stille auffuellen
                del self.abuf[:take]
            else:
                pcm = bytes(need)
                if not self.live:
                    self._trim(int(self.pre_n / self.fps * self.sr) * 4)
        if frame is None:
            self.fill += 1
            frame = self.last if (self.fallback == "last" and self.last is not None) else self.slate
        self._set_state("live" if self.live else ("lost" if self.conns else "waiting"))
        return frame, pcm

    def _clock(self):
        try:
            self.fv = os.fdopen(os.open(self.fifo_v, os.O_RDWR), "wb", buffering=0)
            self.fa = os.fdopen(os.open(self.fifo_a, os.O_RDWR), "wb", buffering=0) if self.use_audio else None
            qv, qa = queue.Queue(maxsize=max(4, int(self.fps))), queue.Queue(maxsize=max(4, int(self.fps)))
            for q, f in ((qv, self.fv), (qa, self.fa)):
                if f is not None:
                    threading.Thread(target=VideoFeeder._sink, args=(self, q, f), daemon=True).start()
            t0, self.k = time.time(), 0
            while not self.stop_evt.is_set():
                wait = t0 + self.k / self.fps - self.LEAD_S - time.time()
                if wait > 0 and self.stop_evt.wait(wait):
                    break
                frame, pcm = self._next()
                for q, b in ((qv, frame), (qa, pcm if self.use_audio else None)):
                    while b is not None and not self.stop_evt.is_set():
                        try:
                            q.put(b, timeout=0.5)
                            break
                        except queue.Full:
                            pass
        except Exception as e:
            if not self.stop_evt.is_set():
                self.log("error", "Stream-Feeder %s: %s" % (self.key, e))

    def start(self):
        self.log("info", "Stream %s: %s, Front-End %s" % (self.key, self.label, "Windows-ffmpeg" if self.windows else "ffmpeg"),
                 now_playing=self.now_playing())
        for fn in (self._clock, self._conn_loop):
            t = threading.Thread(target=fn, daemon=True, name="stream-%s" % self.key)
            t.start()
            self.threads.append(t)

    def stop(self):
        self.stop_evt.set()
        procs = list(self.procs)
        for i, p in enumerate(procs):
            self._kill(p, self.windows and i == 0)
