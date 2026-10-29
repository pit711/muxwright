# -*- coding: utf-8 -*-
"""Befehle fuer mode = dvbt/dvbc: live, render, check, validate, ctl, media. Aufruf aus dablab_run.main()."""
import json
import os
import signal
import sys
import threading
import time

from . import config as C
from . import tables as T
from . import si, sources
from .chain import DvbChain


def is_dvb_config(raw):
    return str((raw or {}).get("mode", "")).lower() in C.STANDARDS


def prepare(host, path):
    raw = host.load_config(path)
    return C.normalize(raw)


def _log_cfg(host, cfg, W):
    for w in W:
        host.log("warn", w)
    host.log("info", "DVB-Konfiguration", summary=C.summary(cfg))


# ---------------------------------------------------------------------------------------------
def cmd_validate(args, host):
    cfg, E, W = prepare(host, args.config)
    host.emit_json({"ok": not E, "errors": E, "warnings": W, "summary": C.summary(cfg) if not E else None})
    return 0 if not E else 2


def cmd_live(args, host):
    sid = host.new_session_id()
    sys.stderr.write(json.dumps({"session": sid}) + "\n")
    sys.stderr.flush()
    try:
        cfg, E, W = prepare(host, args.config)
    except Exception as e:
        host.log("error", "Konfiguration nicht lesbar: %s" % e)
        return 2
    if E:
        for e in E:
            host.log("error", e)
        host.log("error", "Konfiguration ungueltig (%d Fehler)" % len(E), errors=E)
        return 2
    chain = DvbChain(host, cfg, sid, host.log)
    host._log_file = open(os.path.join(chain.sdir, "runner.log"), "a", encoding="utf-8")
    _log_cfg(host, cfg, W)
    stop = {"why": None}

    def on_sig(signum, frame):
        # nur einmal: ein zweites Signal (z. B. timeout an Prozess und Gruppe) traefe den Handler mitten in Event.set()
        # und wartete dann ewig auf dessen Lock
        if stop["why"]:
            return
        stop["why"] = "signal %d" % signum
        chain.stop_evt.set()

    for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sg, on_sig)
    rc = 0
    try:
        chain.start(out_fd=sys.stdout.fileno())
        chain.write_session()
        host.log("info", "Kette gestartet: %s, %d Dienst(e), Nutzbitrate %s b/s, Ausgabe %s" %
                 (cfg["mode"].upper(), len(cfg["services"]), cfg["_rate_str"], cfg["output"]["kind"] +
                  ("" if cfg["output"]["kind"] != "udp" else " %s:%d" % (cfg["output"]["host"], cfg["output"]["port"]))),
                 session=sid, ffmpeg=" ".join(chain.cmds["ffmpeg"]), tsp=" ".join(chain.cmds["tsp"]))
        last = time.time()
        while not chain.stop_evt.wait(0.5):
            ok, why = chain.alive()
            if not ok:
                stop["why"] = why
                host.log("error", why)
                rc = 1
                break
            if time.time() - last >= 5:
                last = time.time()
                host.log("info", "status", **chain.status())
    finally:
        why = stop["why"] or getattr(chain, "stop_why", None) or "Ende"
        host.log("info", "Beende Kette: %s" % why, **chain.status())
        chain.stop()
    return rc


def cmd_render(args, host):
    if args.seconds is None:
        host.emit_json({"ok": False, "error": "render: --seconds fehlt"})
        return 2
    cfg, E, W = prepare(host, args.config)
    if E:
        host.emit_json({"ok": False, "errors": E, "warnings": W})
        return 2
    sid = host.new_session_id()
    out = os.path.abspath(args.out)
    iq = getattr(args, "format", "ts") == "s16" or (cfg["output"]["kind"] == "modulator" and getattr(args, "format", "ts") != "ts")
    chain = DvbChain(host, cfg, sid, host.log, render=True)
    _log_cfg(host, cfg, W)
    t0 = time.time()
    rc = 0
    try:
        if iq:
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            chain.start(out_fd=fd, seconds=args.seconds, iq=True)
        else:
            chain.start(seconds=args.seconds, out_file=out)
        chain.write_session({"render": True, "out": out, "seconds": args.seconds})
        deadline = time.time() + float(args.seconds) * 4 + 120
        last = time.time()
        while not chain.stop_evt.is_set():
            if time.time() - last >= 5:
                last = time.time()
                host.log("info", "status", **chain.status())
            if chain.ffmpeg.poll() is not None:
                # ffmpeg fertig -> tsp liest EOF und endet
                try:
                    chain.tsp.wait(timeout=60)
                except Exception:
                    pass
                if chain.mod is not None:
                    try:
                        chain.mod.wait(timeout=60)
                    except Exception:
                        pass
                break
            if time.time() > deadline:
                host.log("error", "render: Zeitueberschreitung")
                rc = 1
                break
            time.sleep(0.2)
            ok, why = chain.alive()
            if not ok and chain.ffmpeg.poll() is None and ("keine Daten" in why or "Windows" not in why):
                host.log("error", why)
                rc = 1
                break
        # Relais zu Ende lesen (IQ vom Modulator bzw. TS)
        if chain.relay_thread is not None:
            chain.relay_thread.join(60)
    finally:
        chain.stop()
        if iq:
            try:
                os.close(fd)
            except Exception:
                pass
    size = os.path.getsize(out) if os.path.exists(out) else 0
    rep = {"ok": rc == 0 and size > 0, "out": out, "bytes": size, "packets": size // 188 if not iq else None,
           "format": "s16" if iq else "ts", "seconds": args.seconds, "elapsed_s": round(time.time() - t0, 1),
           "ts_seconds": round(size * 8 / cfg["_rate_bps"], 2) if not iq else None,
           "target_bps": cfg["_rate_bps"], "muxrate": cfg["_muxrate"], "session": sid, "warnings": W,
           "summary": C.summary(cfg), "ffmpeg_rc": chain.ffmpeg.returncode if chain.ffmpeg else None,
           "tsp_rc": chain.tsp.returncode if chain.tsp else None}
    host.emit_json(rep)
    return 0 if rep["ok"] else 1


def cmd_check(args, host):
    from . import check as CK
    cfg, E, W = prepare(host, args.config)
    if E:
        host.emit_json({"ok": False, "errors": E, "warnings": W})
        return 2
    sid = host.new_session_id()
    cdir = os.path.join(host.RUNTIME, "checks")
    os.makedirs(cdir, exist_ok=True)
    tsfile = os.path.join(host.TMPROOT, "dvbcheck-%s.ts" % sid)
    chain = DvbChain(host, cfg, sid, host.log, render=True)
    _log_cfg(host, cfg, W)
    t0 = time.time()
    try:
        chain.start(seconds=args.seconds, out_file=tsfile)
        chain.write_session({"check": True, "out": tsfile, "seconds": args.seconds})
        deadline = time.time() + float(args.seconds) * 4 + 120
        while chain.ffmpeg.poll() is None and time.time() < deadline:
            time.sleep(0.2)
            ok, why = chain.alive()
            if not ok and "keine Daten" in (why or ""):
                host.log("error", why)
                break
        if chain.ffmpeg.poll() is not None:
            try:
                chain.tsp.wait(timeout=60)
            except Exception:
                pass
    finally:
        chain.stop()
    if not os.path.exists(tsfile) or os.path.getsize(tsfile) < 188 * 100:
        host.emit_json({"ok": False, "error": "render lieferte keinen Transportstrom", "session": sid,
                        "logs": os.path.join(host.SESSIONS, sid)})
        return 1
    host.log("info", "TS gerendert: %d Byte in %.1f s, Analyse laeuft" % (os.path.getsize(tsfile), time.time() - t0))
    rep = CK.analyze_file(host, cfg, tsfile, log=host.log, decode_seconds=min(6, max(2, args.seconds / 3)))
    rep.update({"standard": cfg["mode"], "session": sid, "seconds": args.seconds, "warnings": W, "summary": C.summary(cfg),
                "elapsed_s": round(time.time() - t0, 1)})
    rp = os.path.join(cdir, "dvb_%s.json" % sid)
    try:
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, ensure_ascii=False, default=str)
        rep["report_file"] = rp
    except Exception:
        pass
    if not getattr(args, "keep", False):
        for p in (tsfile, tsfile + ".tables.xml"):
            try:
                os.unlink(p)
            except OSError:
                pass
    else:
        rep["kept"] = tsfile
    host.emit_json(rep)
    return 0 if rep["ok"] else 1


# ---------------------------------------------------------------------------------------------
def cmd_ctl(args, host, sess):
    sdir = os.path.join(host.SESSIONS, args.session)
    try:
        req = json.loads(args.json)
    except Exception as e:
        host.emit_json({"ok": False, "error": "JSON ungueltig: %s" % e})
        return 2
    if not sess.get("running") or not host._pid_alive(sess.get("pid")):
        host.emit_json({"ok": False, "error": "Sitzung laeuft nicht mehr"})
        return 2
    with open(os.path.join(sdir, "config.json"), encoding="utf-8") as f:
        raw = json.load(f)
    cfg, E, W = C.normalize({k: v for k, v in raw.items() if not k.startswith("_")})
    if E:
        host.emit_json({"ok": False, "error": "Sitzungskonfiguration ungueltig", "errors": E})
        return 2
    svcs = {s["key"]: s for s in cfg["services"]}
    versions = sess.get("versions") or {"nit": 1, "sdt": 1}
    names = sess.get("names") or {}
    res = {"ok": True, "done": []}

    def svc_of(d):
        k = d.get("service")
        if k not in svcs:
            raise host.RunnerError("Dienst '%s' unbekannt" % k)
        return svcs[k]

    try:
        if "eit" in req:
            d = req["eit"]
            s = svc_of(d)
            ss = sess["services"][s["key"]]
            if ss.get("playlist") or ss.get("schedule"):
                # Feeder schreibt die EIT (Text gilt bis "clear" bzw. beim Zeitplan bis zum naechsten Eintrag)
                host.atomic_write(os.path.join(sdir, "playlist_%s.cmd" % s["key"]),
                                  json.dumps({"action": "eit", "title": d.get("title"), "text": d.get("text"),
                                              "clear": bool(d.get("clear"))}))
            else:
                # Start = jetzt: eitinject uebernimmt ein Ereignis mit gleicher ID nur, wenn sich Start/Dauer aendern
                pres = {"title": d.get("title") or s["eit"]["title"], "text": d.get("text") if d.get("text") is not None
                        else s["eit"]["text"], "start": int(time.time()),
                        "duration_s": float(d.get("duration_min") or s["eit"]["duration_min"]) * 60}
                fol = ({"title": d.get("next_title"), "text": d.get("next_text") or ""} if d.get("next_title") else
                       ({"title": s["eit"]["next_title"], "text": s["eit"].get("next_text") or ""} if s["eit"].get("next_title") else None))
                res["eit_version"] = si.write_eit(host, sess["services"][s["key"]]["eit_file"], cfg, s, pres, fol)
            res["done"].append("eit")
        if "name" in req:
            d = req["name"]
            s = svc_of(d)
            n = dict(names.get(s["key"], {}))
            if d.get("name"):
                n["name"] = str(d["name"])[:48]
            if d.get("provider"):
                n["provider"] = str(d["provider"])[:48]
            names[s["key"]] = n
            versions["sdt"] = (versions.get("sdt", 1) + 1) % 32
            host.atomic_write(os.path.join(sdir, "sdt.xml"), si.sdt_xml(cfg, versions["sdt"], names))
            res["done"].append("name")
            res["sdt_version"] = versions["sdt"]
        if "overlay" in req:
            # Overlay-Text: drawtext liest die Datei mit reload=1 bei jedem Bild neu -> atomar ersetzen
            d = req["overlay"]
            s = svc_of(d)
            tx = (s.get("overlay") or {}).get("text")
            tf = os.path.join(sdir, "overlay_%s.txt" % s["key"])
            if not tx or not os.path.isfile(tf):
                raise host.RunnerError("Dienst '%s' hat keinen Overlay-Text (Einschalten braucht einen Neustart)" % s["key"])
            host.atomic_write(tf, sources.overlay_text_clean(d.get("text"), tx["scroll"]))
            res["done"].append("overlay")
        if "lcn" in req:
            d = req["lcn"]
            s = svc_of(d)
            s["lcn"] = int(d["lcn"])
            raw_s = [x for x in raw["services"] if x.get("key") == s["key"]]
            if raw_s:
                raw_s[0]["lcn"] = s["lcn"]
            versions["nit"] = (versions.get("nit", 1) + 1) % 32
            host.atomic_write(os.path.join(sdir, "nit.xml"), si.nit_xml(cfg, versions["nit"]))
            host.atomic_write(os.path.join(sdir, "config.json"), json.dumps(raw, indent=1, ensure_ascii=False, default=str))
            res["done"].append("lcn")
        if "playlist" in req:
            d = req["playlist"]
            s = svc_of(d)
            if not sess["services"][s["key"]].get("playlist"):
                raise host.RunnerError("Dienst '%s' hat keine Playlist" % s["key"])
            cmd = {"action": d.get("action", "next")}
            if "index" in d:
                cmd["index"] = int(d["index"])
            npf = os.path.join(sdir, "now_playing_%s.json" % s["key"])
            try:
                with open(npf) as f:
                    res["now_playing_before"] = json.load(f)
            except Exception:
                pass
            host.atomic_write(os.path.join(sdir, "playlist_%s.cmd" % s["key"]), json.dumps(cmd))
            res["done"].append("playlist")
        if req.get("status"):
            np_ = []
            for k in svcs:
                try:
                    with open(os.path.join(sdir, "now_playing_%s.json" % k)) as f:
                        np_.append(json.load(f))
                except Exception:
                    pass
            res["now_playing"] = np_
            res["done"].append("status")
        sess["versions"], sess["names"] = versions, names
        host.atomic_write(os.path.join(sdir, "session.json"), json.dumps(sess, indent=1, ensure_ascii=False))
    except host.RunnerError as e:
        res = {"ok": False, "error": str(e), "done": res["done"]}
    except Exception as e:
        res = {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "done": res["done"]}
    host.emit_json(res)
    return 0 if res["ok"] else 2


# ---------------------------------------------------------------------------------------------
def cmd_media(args, host):
    act = args.action
    if act == "youtube":
        if not args.args:
            host.emit_json({"ok": False, "error": "media youtube <url> [--max-height N]"})
            return 2
        url = args.args[0]
        mh = getattr(args, "max_height", None) or 1080
        have = sources.youtube_find(host, url) if not getattr(args, "force", False) else None
        if have:
            host.emit_json({"ok": True, "cached": True, "file": "media/video/" + have["file"], **have})
            return 0
        t0 = time.time()

        def prog(p):
            host.log("info", "youtube", youtube=p)

        try:
            ent = sources.youtube_download(host, url, mh, progress=prog, log=host.log, force=getattr(args, "force", False),
                                           cookies=getattr(args, "cookies", None),
                                           cookies_from_browser=getattr(args, "cookies_from_browser", None))
        except Exception as e:
            host.emit_json({"ok": False, "error": "yt-dlp: %s" % str(e)[:500]})
            return 1
        pr = sources.probe(host, os.path.join(sources.video_dir(host), ent["file"]))
        host.emit_json({"ok": True, "cached": False, "file": "media/video/" + ent["file"], "elapsed_s": round(time.time() - t0, 1),
                        "probe": pr, **ent})
        return 0
    if act == "youtube-consent":
        # Freigabe durch den Nutzer: erst danach darf der Runner Cookies aus diesem Browser lesen
        br = (args.args[0] if args.args else "").lower()
        if br in ("off", "revoke", "aus"):
            for p in (sources.consent_file(host), sources.cookie_file(host)):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            host.emit_json({"ok": True, "consent": None, "msg": "Freigabe und Cookie-Datei geloescht"})
            return 0
        if br not in sources.BROWSERS:
            host.emit_json({"ok": False, "error": "media youtube-consent <%s|off>" % "|".join(sources.BROWSERS)})
            return 2
        d = {"browser": br, "granted": time.time(), "by": os.environ.get("USER") or "user",
             "note": "Freigabe zum einmaligen Export der YouTube-Cookies aus dem Browser (Windows-yt-dlp)"}
        os.makedirs(host.CACHE, exist_ok=True)
        host.atomic_write(sources.consent_file(host), json.dumps(d, indent=1))
        host.emit_json({"ok": True, "consent": d, "file": sources.consent_file(host)})
        return 0
    if act == "dshow":
        from . import nvenc
        host.emit_json({"ok": nvenc.available(), "devices": nvenc.list_dshow(), "ffmpeg": nvenc.win_exe()})
        return 0
    if act == "gpu":
        from . import nvenc
        host.emit_json({"ok": nvenc.available(), "gpu": nvenc.gpu_info(), "ffmpeg": nvenc.win_exe()})
        return 0
    if act == "list":
        vd = sources.video_dir(host)
        idx = sources.youtube_index(host)
        files = []
        for fn in sorted(os.listdir(vd)):
            p = os.path.join(vd, fn)
            if not os.path.isfile(p) or fn.endswith(".json") or fn.endswith(".part"):
                continue
            ent = {"file": "media/video/" + fn, "bytes": os.path.getsize(p)}
            vid = os.path.splitext(fn)[0]
            if vid in idx:
                ent.update({k: idx[vid].get(k) for k in ("title", "duration_s", "width", "height", "url")})
            files.append(ent)
        host.emit_json({"ok": True, "dir": vd, "videos": files})
        return 0
    if act == "info":
        p = host.media_path(args.args[0])
        host.emit_json({"ok": os.path.isfile(p), "file": p, "probe": sources.probe(host, p)})
        return 0
    if act == "channels":
        std = (args.args[0] if args.args else "dvbt").lower()
        host.emit_json({"ok": True, "standard": std, "channels": T.channel_table(std)})
        return 0
    if act == "rates":
        std = (args.args[0] if args.args else "dvbt").lower()
        host.emit_json({"ok": True, "standard": std, "rates": T.rate_table(std)})
        return 0
    if act == "tools":
        host.emit_json({"ok": True, "tools": selftest(host)})
        return 0
    host.emit_json({"ok": False, "error": "media youtube|list|info|channels|rates|tools"})
    return 2


def selftest(host):
    import subprocess
    out = {}
    for name, argv in (("ffmpeg", ["-version"]), ("tsp", ["--version"]), ("tsanalyze", ["--version"]),
                       ("tstables", ["--version"]), ("yt-dlp", ["--version"])):
        p = host.tool(name, required=False)
        if not p:
            out[name] = None
            continue
        try:
            r = subprocess.run([p] + argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
            out[name] = r.stdout.decode("utf-8", "replace").strip().splitlines()[0][:80]
        except Exception as e:
            out[name] = "Fehler: %s" % e
    try:
        import yt_dlp
        out["yt_dlp_module"] = yt_dlp.version.__version__
    except Exception:
        out["yt_dlp_module"] = None
    out["font"] = sources.font_file()
    out["modulator_dvbt"] = __import__("dvb.chain", fromlist=["find_modulator"]).find_modulator(host, "dvbt")
    out["modulator_dvbc"] = __import__("dvb.chain", fromlist=["find_modulator"]).find_modulator(host, "dvbc")
    return out


# ---------------------------------------------------------------------------------------------
def run(args, host, sess=None):
    cmd = args.cmd
    if cmd == "live":
        return cmd_live(args, host)
    if cmd == "render":
        return cmd_render(args, host)
    if cmd == "check":
        return cmd_check(args, host)
    if cmd == "validate":
        return cmd_validate(args, host)
    if cmd == "ctl":
        return cmd_ctl(args, host, sess)
    if cmd == "media":
        return cmd_media(args, host)
    host.emit_json({"ok": False, "error": "Befehl %s fuer DVB nicht verfuegbar" % cmd})
    return 2
