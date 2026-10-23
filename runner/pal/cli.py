# -*- coding: utf-8 -*-
"""Befehle fuer mode = "pal": validate, render (Schleife fuer den zyklischen Puffer), check (Demod -> PNG + Pegel),
live (bewegtes Video, Strom-Encoder, mehrere Prozesse), channels."""
import copy
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import threading
import time

import numpy as np

from . import gen, demod
from .systems import SYSTEMS, channel_lookup, channel_table, default_offset

MAX_WORKERS = 3          # + Hauptprozess + ffmpeg <= 4 Kerne


# --------------------------------------------------------------------------------------------- Konfiguration
def normalize(raw):
    E, W = [], []
    c = copy.deepcopy(raw)
    p = c.setdefault("pal", {})
    p["system"] = str(p.get("system", "BG")).upper().replace("/", "")
    if p["system"] in ("B", "G"):
        p["system"] = "BG"
    if p["system"] in ("D", "K"):
        p["system"] = "DK"
    if p["system"] not in SYSTEMS:
        E.append("pal.system: BG, DK oder I")
        return c, E, W
    p["fs"] = int(p.get("fs") or 8000000)
    p["live_fs"] = int(p.get("live_fs") or 7000000)
    if p["fs"] not in (7000000, 8000000):
        E.append("pal.fs: 7000000 oder 8000000")
    if p["live_fs"] < 7000000:
        E.append("pal.live_fs: mindestens 7 MS/s (Ton bei +3,4 MHz im Basisband)")
    off = p.get("offset_hz", "auto")
    p["_offset_loop"] = default_offset(p["system"], p["fs"]) if off in (None, "auto") else float(off)
    p["_offset_live"] = default_offset(p["system"], p["live_fs"]) if off in (None, "auto") else float(off)
    for k in ("_offset_loop", "_offset_live"):
        if abs(p[k] / 25.0 - round(p[k] / 25.0)) > 1e-9:
            E.append("pal.offset_hz muss ein Vielfaches von 25 Hz sein")
    sysd = SYSTEMS[p["system"]]
    for fs, off_ in ((p["fs"], p["_offset_loop"]), (p["live_fs"], p["_offset_live"])):
        if sysd["sound_mhz"] * 1e6 - off_ + 80e3 > fs / 2 or -off_ - (sysd["vestige_mhz"] + 0.3) * 1e6 < -fs / 2:
            W.append("Norm %s passt bei %.1f MS/s und Versatz %.3f MHz nur knapp in das Basisband" % (p["system"], fs / 1e6, off_ / 1e6))
    img = p.setdefault("image", {})
    img.setdefault("kind", "testpattern")
    if img["kind"] not in ("testpattern", "file", "video", "youtube"):
        E.append("pal.image.kind: testpattern, file (Bild), video, youtube")
    img.setdefault("pattern", "pal75bars")
    img.setdefault("text", "DABLAB Test")
    img.setdefault("text2", None)
    img.setdefault("timestamp", True)
    snd = p.setdefault("sound", {})
    snd.setdefault("kind", "tone")
    if snd["kind"] not in ("tone", "silence", "off", "file", "source"):
        E.append("pal.sound.kind: tone, silence, off, file, source")
    snd.setdefault("freq_hz", 1000)
    snd.setdefault("deviation_hz", sysd["deviation_hz"])
    snd.setdefault("level_db", -13)
    snd.setdefault("preemphasis_us", 50)
    snd.setdefault("tone_level", 1.0)
    p["level"] = float(p.get("level", 0.9))
    if not (0.05 <= p["level"] <= 1.0):
        E.append("pal.level: 0,05 ... 1,0 (Spitze relativ zur Vollaussteuerung)")
    rf = c.setdefault("rf", {})
    ch = rf.get("channel")
    if ch and str(ch).lower() != "custom":
        v = channel_lookup(ch)
        if not v:
            E.append("rf.channel '%s' nicht in der PAL-Kanaltabelle" % ch)
        elif not rf.get("freq_hz"):
            rf["freq_hz"] = v
    rf["freq_hz"] = int(rf.get("freq_hz") or 591250000)
    rf["lo_hz"] = int(rf["freq_hz"] + p["_offset_loop"])            # Schleife (render)
    rf["lo_hz_live"] = int(rf["freq_hz"] + p["_offset_live"])       # live
    rf["sample_rate"] = p["fs"]
    rf.setdefault("atten_db", 60)
    rf.setdefault("enabled", False)
    if p["image"]["kind"] in ("file",) and not p["image"].get("file"):
        E.append("pal.image.file fehlt")
    return c, E, W


def params(c, live=False):
    p = c["pal"]
    return {"system": p["system"], "fs": p["live_fs"] if live else p["fs"],
            "offset_hz": p["_offset_live"] if live else p["_offset_loop"], "level": p["level"], "sound": p["sound"]}


def summary(c):
    p, rf = c["pal"], c["rf"]
    return {"standard": "pal", "system": p["system"], "vision_hz": rf["freq_hz"], "channel": rf.get("channel"),
            "loop": {"fs": p["fs"], "lo_hz": rf["lo_hz"], "offset_hz": p["_offset_loop"], "samples": int(p["fs"] * gen.LOOP_S)},
            "live": {"fs": p["live_fs"], "lo_hz": rf["lo_hz_live"], "offset_hz": p["_offset_live"]},
            "sound_hz": rf["freq_hz"] + SYSTEMS[p["system"]]["sound_mhz"] * 1e6, "image": p["image"], "sound": p["sound"]}


def prepare(host, path):
    return normalize(host.load_config(path))


# --------------------------------------------------------------------------------------------- render / check
def _still_rgb(host, c):
    img = dict(c["pal"]["image"])
    if img["kind"] in ("video", "youtube"):
        img["kind"] = "testpattern"          # Schleife = Standbild; erstes Bild eines Videos waere auch moeglich
    return gen.ffmpeg_image(host, img)


def stream_loop(host, c, rgb):
    """Schleife ueber den Strom-Encoder (4 Vollbilder, periodisch) - fuer den Vergleich mit dem FFT-Verfahren."""
    p = params(c)
    enc = gen.StreamEncoder(p)
    Y, Pb, Pr = _rgb_to_ypbpr(rgb, enc.act_len)
    vid = np.concatenate([enc.frame(Y, Pb, Pr, k) for k in range(gen.FRAMES)])
    snd = gen.sound_loop(p, len(vid), p["fs"])
    s = vid + snd
    s = s / np.max(np.abs(s)) * p["level"]
    return s, gen.spectrum_report(s, p, p["fs"])


def _rgb_to_ypbpr(rgb, act_len):
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    Pb = (B - Y) / 1.772
    Pr = (R - Y) / 1.402
    xs = np.linspace(0, rgb.shape[1] - 1, act_len)
    rs = lambda M: np.array([np.interp(xs, np.arange(rgb.shape[1]), r) for r in M]).astype(np.float32)
    return rs(Y), _chroma_lp(rs(Pb)), _chroma_lp(rs(Pr))


def _chroma_lp(M):
    """Farbdifferenz auf ca. 1,3 MHz begrenzen: zweimal gleitender Mittelwert ueber 8 Samples (bei 2*fs)."""
    from scipy.ndimage import uniform_filter1d
    return uniform_filter1d(uniform_filter1d(M, 9, axis=1, mode="nearest"), 9, axis=1, mode="nearest")


def cmd_render(args, host):
    c, E, W = prepare(host, args.config)
    if E:
        host.emit_json({"ok": False, "errors": E, "warnings": W})
        return 2
    t0 = time.time()
    rgb = _still_rgb(host, c)
    if getattr(args, "method", "fft") == "stream":
        s, rep = stream_loop(host, c, rgb)
    else:
        s, rep = gen.loop_iq(params(c), rgb)
    fmt = "cf32" if args.format == "cf32" else "s16"
    gen.to_int16(s, args.out, fmt)
    out = {"ok": True, "out": os.path.abspath(args.out), "format": fmt, "bytes": os.path.getsize(args.out),
           "samples": len(s), "cyclic": True, "loop_s": len(s) / c["pal"]["fs"], "sample_rate": c["pal"]["fs"],
           "lo_hz": c["rf"]["lo_hz"], "vision_hz": c["rf"]["freq_hz"], "elapsed_s": round(time.time() - t0, 2),
           "spectrum": rep, "warnings": W, "summary": summary(c),
           "hint": "zyklischer Puffer: LO = lo_hz, sampling_frequency = sample_rate, Datei einmal laden (cyclic=True)"}
    host.emit_json(out)
    return 0


def cmd_check(args, host):
    c, E, W = prepare(host, args.config)
    if E:
        host.emit_json({"ok": False, "errors": E, "warnings": W})
        return 2
    sid = host.new_session_id()
    cdir = os.path.join(host.RUNTIME, "checks")
    os.makedirs(cdir, exist_ok=True)
    t0 = time.time()
    p = c["pal"]
    rgb = _still_rgb(host, c)
    ref = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    methods = ["fft", "stream"] if getattr(args, "method", "both") == "both" else [args.method]
    rep = {"ok": True, "standard": "pal", "session": sid, "summary": summary(c), "warnings": W, "methods": {}, "mismatches": []}
    for mth in methods:
        s, spec = (gen.loop_iq(params(c), rgb) if mth == "fft" else stream_loop(host, c, rgb))
        iq = gen.to_int16(s)                      # wie gesendet: int16
        x = (iq[0::2].astype(np.float64) + 1j * iq[1::2]) / 32768.0
        xx = np.concatenate([x, x])               # zwei Schleifendurchlaeufe -> Schleifenstelle mitten im Bild
        png = os.path.join(cdir, "pal_%s_%s.png" % (sid, mth))
        d = demod.analyze(xx[len(x) // 2: len(x) // 2 + len(x)], p["fs"], -p["_offset_loop"], p["system"], png=png,
                          ref_luma=ref, ffmpeg=host.tool("ffmpeg"))
        # Schleifenstelle: Sprung zwischen letztem und erstem Sample im Vergleich zu normalen Nachbarschritten
        steps = np.abs(np.diff(x))
        jump = float(np.abs(x[0] - x[-1]))
        d["loop_seam_step"] = round(jump, 5)
        d["loop_seam_percentile"] = round(float(np.mean(steps < jump) * 100), 3)   # Rang unter allen Schritten
        d["max_step"] = round(float(steps.max()), 5)
        d["spectrum"] = spec
        d["clipped_samples"] = int(np.sum(np.abs(iq) >= 32767))
        rep["methods"][mth] = d
        M = rep["mismatches"]
        tag = "[%s] " % mth
        if abs(d["line_rate_hz"] - 15625) > 1:
            M.append(tag + "Zeilenfrequenz %.1f Hz" % d["line_rate_hz"])
        if abs(d["blank_rel"] - 0.73) > 0.04:
            M.append(tag + "Austastpegel %.3f statt 0,73" % d["blank_rel"])
        if abs(d["white_rel"] - 0.10) > 0.05:
            M.append(tag + "Weisspegel %.3f statt 0,10" % d["white_rel"])
        if d.get("luma_correlation", 0) < 0.95:
            M.append(tag + "Bild weicht ab (Korrelation %.3f)" % d.get("luma_correlation", 0))
        if p["sound"]["kind"] == "tone":
            if abs(d.get("audio_tone_hz", 0) - p["sound"]["freq_hz"]) > 15:
                M.append(tag + "Tonfrequenz %.1f statt %s Hz" % (d.get("audio_tone_hz", 0), p["sound"]["freq_hz"]))
            exp_dev = p["sound"]["deviation_hz"] * abs(1 + 1j * 2 * np.pi * p["sound"]["freq_hz"] * p["sound"]["preemphasis_us"] * 1e-6)
            if abs(d.get("deviation_peak_hz", 0) / exp_dev - 1) > 0.1:
                M.append(tag + "Hub %.0f Hz statt %.0f Hz" % (d.get("deviation_peak_hz", 0), exp_dev))
        if p["sound"]["kind"] != "off" and abs(d.get("sound_rel_peak_db", -99) - p["sound"]["level_db"]) > 1.5:
            M.append(tag + "Tontraeger %.1f dB (bezogen auf Synchronspitze) statt %s dB" % (d.get("sound_rel_peak_db", -99), p["sound"]["level_db"]))
        if jump > d["max_step"] * 1.0001:
            M.append(tag + "Schleifenstelle springt (%.4f, groesster Schritt sonst %.4f)" % (jump, d["max_step"]))
        if d["clipped_samples"]:
            M.append(tag + "%d geclippte Samples" % d["clipped_samples"])
    rep["ok"] = not rep["mismatches"]
    rep["elapsed_s"] = round(time.time() - t0, 1)
    rp = os.path.join(cdir, "pal_%s.json" % sid)
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False, default=float)
    rep["report_file"] = rp
    host.emit_json(rep)
    return 0 if rep["ok"] else 1


def cmd_validate(args, host):
    c, E, W = prepare(host, args.config)
    host.emit_json({"ok": not E, "errors": E, "warnings": W, "summary": summary(c) if not E else None})
    return 0 if not E else 2


# --------------------------------------------------------------------------------------------- live
_ENC = None


def _winit(p):
    global _ENC
    # Signal-Handler des Hauptprozesses nicht erben: sonst beendet pool.terminate() (SIGTERM) die Worker nicht
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
    try:
        os.nice(10)
    except Exception:
        pass
    _ENC = gen.StreamEncoder(p)


def _wframe(job):
    k, raw = job
    a = _ENC.act_len
    f = np.frombuffer(raw, np.uint8).reshape(3, 576, a).astype(np.float32)
    Y = (f[0] - 16.0) / 219.0
    Pb = _chroma_lp((f[1] - 128.0) / 224.0)
    Pr = _chroma_lp((f[2] - 128.0) / 224.0)
    return _ENC.frame(Y, Pb, Pr, k)


def _source_cmd(host, c, act_len, sr, afd):
    """ffmpeg: Bild (yuv444p planar, act_len x 576, 25 fps) auf stdout, Ton (s16le mono sr) auf Pipe afd."""
    img, snd = c["pal"]["image"], c["pal"]["sound"]
    ff = host.tool("ffmpeg")
    inputs, fc = [], []
    if img["kind"] in ("video", "youtube", "file"):
        path = host.media_path(img.get("file")) if img.get("file") else None
        if img["kind"] == "youtube" and not path:
            from dvb import sources as dsrc
            ent = dsrc.youtube_find(host, img.get("url")) or dsrc.youtube_download(host, img.get("url"), 576)
            path = os.path.join(dsrc.video_dir(host), ent["file"])
        if img["kind"] == "file":
            inputs += ["-loop", "1", "-framerate", "25", "-i", path]
        else:
            inputs += ["-stream_loop", "-1", "-i", path]
    else:
        inputs += ["-f", "lavfi", "-i", "%s=size=720x576:rate=25" % img.get("pattern", "pal75bars")]
    vf = ["scale=720:576:force_original_aspect_ratio=decrease", "pad=720:576:(ow-iw)/2:(oh-ih)/2", "fps=25", "setsar=1"]
    font = gen._font()
    if font and (img.get("text") or img.get("timestamp")):
        t = gen._esc(img.get("text") or "")
        if img.get("timestamp"):
            t += "  %{localtime\\:%X}  %{n}"
        vf.append("drawtext=fontfile=%s:text='%s':fontcolor=white:fontsize=34:box=1:boxcolor=black@0.6:boxborderw=6:x=(w-text_w)/2:y=h-80"
                  % (font, t))
    vf += ["scale=%d:576:flags=bicubic" % act_len, "format=yuv444p"]
    a_in = None
    if snd["kind"] == "source" and img["kind"] in ("video", "youtube"):
        a_in = "0:a:0?"
    elif snd["kind"] == "file" and snd.get("file"):
        inputs += ["-stream_loop", "-1", "-i", host.media_path(snd["file"])]
        a_in = "1:a:0"
    else:
        f0 = float(snd.get("freq_hz", 1000)) if snd["kind"] == "tone" else 0
        # Vollaussteuerung = Nennhub (ffmpeg "sine" hat nur 1/8 Amplitude, deshalb aevalsrc)
        inputs += ["-f", "lavfi", "-i", ("aevalsrc=sin(2*PI*%s*t):s=%d:c=mono" % (f0, sr)) if f0 else "anullsrc=r=%d:cl=mono" % sr]
        a_in = "1:a:0"
    cmd = [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1"] + inputs + \
          ["-map", "0:v:0", "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "yuv444p", "pipe:1",
           "-map", a_in, "-ac", "1", "-ar", str(sr), "-af", "aresample=async=1,volume=%s" %
           (float(snd.get("tone_level", 1.0)) * (1.0 if snd["kind"] != "tone" else 1.0)), "-f", "s16le", "pipe:%d" % afd]
    return cmd


def cmd_live(args, host):
    sid = host.new_session_id()
    sys.stderr.write(json.dumps({"session": sid}) + "\n")
    sys.stderr.flush()
    c, E, W = prepare(host, args.config)
    if E:
        for e in E:
            host.log("error", e)
        return 2
    for w in W:
        host.log("warn", w)
    p = params(c, live=True)
    sdir = os.path.join(host.SESSIONS, sid)
    os.makedirs(sdir, exist_ok=True)
    host.atomic_write(os.path.join(sdir, "session.json"), json.dumps(
        {"session": sid, "standard": "pal", "pid": os.getpid(), "running": True, "started": time.time(),
         "sample_rate": p["fs"], "lo_hz": c["rf"]["lo_hz_live"], "vision_hz": c["rf"]["freq_hz"]}, indent=1))
    host.log("info", "PAL live: %s, %d S/s, LO %d Hz (Bildtraeger %d Hz + %d Hz)" %
             (p["system"], p["fs"], c["rf"]["lo_hz_live"], c["rf"]["freq_hz"], p["offset_hz"]),
             sample_rate=p["fs"], lo_hz=c["rf"]["lo_hz_live"], summary=summary(c))
    enc0 = gen.StreamEncoder(p)
    act_len = enc0.act_len
    sr = 48000
    snd = gen.SoundFM(p, p["fs"], sr)
    ra, wa = os.pipe()
    cmd = _source_cmd(host, c, act_len, sr, wa)
    lf = open(os.path.join(sdir, "ffmpeg.log"), "ab")
    lf.write((" ".join(cmd) + "\n").encode())
    src = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf, stdin=subprocess.DEVNULL, pass_fds=[wa],
                           preexec_fn=lambda: os.nice(10))
    os.close(wa)
    afile = os.fdopen(ra, "rb", 0)
    stop = {"why": None}

    def on_sig(signum, frame):
        stop["why"] = "signal %d" % signum

    for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sg, on_sig)
    nworkers = max(1, min(MAX_WORKERS, int(getattr(args, "workers", 0) or MAX_WORKERS)))
    pool = mp.get_context("fork").Pool(nworkers, initializer=_winit, initargs=(p,))
    fb = 3 * 576 * act_len
    n_out = int(round(p["fs"] * 0.04))
    scale = p["level"] / (1.12 + snd.amp)            # Spitze Video ~1,1 (Synchronboden + Restseitenband-Ueberschwinger)
    out_fd = sys.stdout.fileno()
    t0 = time.time()
    frames = 0
    clips = 0
    last = t0
    abuf = bytearray()
    rc = 0

    def reader():
        k = 0
        while stop["why"] is None:
            raw = src.stdout.read(fb)
            if not raw or len(raw) < fb:
                return
            yield (k, raw)
            k += 1

    try:
        for vid in pool.imap(_wframe, reader(), chunksize=1):
            need = snd.samples_needed(n_out) * 2
            while len(abuf) < need:
                b = afile.read(max(4096, need - len(abuf)))
                if not b:
                    break
                abuf += b
            a = np.frombuffer(bytes(abuf[:need]), np.int16).astype(np.float64) / 32768.0
            del abuf[:need]
            if len(a) < need // 2:
                a = np.pad(a, (0, need // 2 - len(a)))
            s = (vid + snd.block(a, n_out)) * scale
            iq = np.empty(2 * n_out, np.int16)
            re_, im_ = np.round(s.real * 32767), np.round(s.imag * 32767)
            clips += int(np.sum(np.abs(re_) > 32767) + np.sum(np.abs(im_) > 32767))
            iq[0::2] = np.clip(re_, -32768, 32767)
            iq[1::2] = np.clip(im_, -32768, 32767)
            mv = memoryview(iq.tobytes())
            try:
                while mv:
                    k = os.write(out_fd, mv)
                    mv = mv[k:]
            except (BrokenPipeError, OSError):
                stop["why"] = "stdout geschlossen"
                break
            frames += 1
            now = time.time()
            if now - last >= 5:
                last = now
                el = now - t0
                host.log("info", "status", frames=frames, uptime_s=round(el, 1), fps=round(frames / el, 2),
                         realtime=round(frames * 0.04 / el, 3), out_bytes=frames * n_out * 4,
                         level={"rms_dbfs": round(float(20 * np.log10(np.sqrt(np.mean(np.abs(s) ** 2)) + 1e-12)), 2),
                                "peak": round(float(np.max(np.abs(s))), 4), "clipped": clips})
            if stop["why"]:
                break
        else:
            if src.poll() not in (None, 0):
                stop["why"] = "ffmpeg beendet (rc=%s)" % src.returncode
                rc = 1
    finally:
        el = time.time() - t0
        host.log("info", "Beende PAL live: %s" % (stop["why"] or "Ende"), frames=frames,
                 realtime=round(frames * 0.04 / max(el, 1e-9), 3))
        stop["why"] = stop["why"] or "Ende"
        try:
            src.kill()                 # zuerst die Quelle: der Pool-Zufuehrer blockiert sonst im read()
            src.wait(2)
        except Exception:
            pass
        # (src.stdout nicht schliessen: der Zufuehrer-Thread des Pools liest gerade daraus -> close() wuerde auf
        # dessen Puffer-Sperre warten. Nach kill() liefert read() EOF und der Zufuehrer endet.)
        pool.terminate()
        try:
            d = json.load(open(os.path.join(sdir, "session.json")))
            d["running"] = False
            host.atomic_write(os.path.join(sdir, "session.json"), json.dumps(d, indent=1))
        except Exception:
            pass
    return rc


def cmd_channels(args, host):
    host.emit_json({"ok": True, "standard": "pal", "channels": channel_table(),
                    "systems": {k: v for k, v in SYSTEMS.items()}})
    return 0


def run(args, host):
    fn = {"render": cmd_render, "check": cmd_check, "live": cmd_live, "validate": cmd_validate}.get(args.cmd)
    if not fn:
        host.emit_json({"ok": False, "error": "Befehl %s fuer PAL nicht verfuegbar" % args.cmd})
        return 2
    return fn(args, host)
