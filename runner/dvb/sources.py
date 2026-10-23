# -*- coding: utf-8 -*-
"""Videoquellen: Testbild, Datei, YouTube (yt-dlp), Diashow, Video-Playlist (Feeder wie beim DAB-Teil).

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
            use_a = src["audio"]["kind"] == "source"
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
    elif kind == "playlist":
        feeder = VideoFeeder(host, cfg, svc, work, sdir, log)
        w, h = svc["video"]["_wh"] if is_tv else (0, 0)
        if is_tv:
            inputs.append(["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (w, h), "-r", str(svc["video"]["fps"]),
                           "-analyzeduration", "0", "-thread_queue_size", "64", "-i", feeder.fifo_v])
            vin = 0
            vf = video_filter(svc, drawtext_filter(svc, src.get("text"), True) if src.get("text") else None)
        inputs.append(["-f", "s16le", "-ar", str(sr), "-ac", "2", "-analyzeduration", "0", "-thread_queue_size", "256", "-i", feeder.fifo_a])
        ain = len(inputs) - 1
        af = "anull"
        info["playlist"] = feeder.pl.get("name")
        info["items"] = feeder.n
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
        self.host, self.cfg, self.svc, self.log = host, cfg, svc, log
        self.src = svc["source"]
        self.key = svc["key"]
        self.sdir = sdir
        self.use_audio = bool(audio)
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
        self.pos, self.cur, self.proc = 0, None, None
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
        self.track_start = time.time()
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

    def run(self):
        try:
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
            for f in (self.fv, self.fa):
                try:
                    if f is not None:
                        f.close()
                except Exception:
                    pass

    def _idle(self):
        """Nach dem Ende (loop=false): Schwarz und Stille, bis ein ctl kommt. True = weiter mit neuem Titel."""
        if self.black is None and self.is_tv:
            self.black = bytes([16]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
        spf = int(round(self.sr / self.fps))
        while not self.stop_evt.is_set():
            if self.is_tv:
                self.qv.put(self.black)
                self.frames += 1
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
