"""Hardware-free tests: playlist store/import/export, multipart parser, config validation, and the
playlist HTTP API against a test instance (no Pluto: --uri ip:127.0.0.1, own --runtime folder).

    python test_api_offline.py            # unit tests only
    python test_api_offline.py --http     # also starts a server on :8793 and exercises /api/playlists, /api/upload
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import playlists  # noqa: E402

FAILS = []


def check(name, cond, info=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {info}" if info and not cond else ""))
    if not cond:
        FAILS.append(name)


def unit():
    rt = tempfile.mkdtemp(prefix="dablab_t_")
    media = os.path.join(rt, "media")
    os.makedirs(media)
    for n in ("a.mp3", "b.flac", "c d.ogg"):
        open(os.path.join(media, n), "wb").write(b"x")
    st = playlists.Store(os.path.join(rt, "playlists"), media)
    pl = st.save("Test", {"items": ["media/a.mp3", {"file": "b.flac", "title": "B", "artist": "Art", "duration_s": "12.5"},
                                     {"file": media + "\\c d.ogg"}, {"file": "missing.mp3"}]})
    check("save normalizes paths", [i["file"] for i in pl["items"]] == ["media/a.mp3", "media/b.flac", "media/c d.ogg", "media/missing.mp3"])
    check("exists flag", [i["exists"] for i in pl["items"]] == [True, True, True, False])
    check("duration parsed", pl["items"][1]["duration_s"] == 12.5)
    check("summary", st.summary()[0]["count"] == 4 and st.summary()[0]["missing"] == 1)
    try:
        st.save("Bad", {"items": [{"file": "../x.mp3"}, {"file": "media/t.txt"}]})
        check("rejects escape/non-audio", False)
    except playlists.PlaylistError as e:
        check("rejects escape/non-audio", "innerhalb media" in str(e) and "kein Audioformat" in str(e))
    try:
        st.path("../evil")
        check("rejects bad name", False)
    except playlists.PlaylistError:
        check("rejects bad name", True)
    m3u = "﻿#EXTM3U\r\n#EXTINF:123,Artist - Title\r\na.mp3\r\n#EXTINF:-1,Only\r\nmedia/b.flac\r\n# comment\r\nsub/x.wav\r\n"
    pl2, errs = st.parse_import(m3u, "Imp")
    check("m3u import", [(i["file"], i["artist"], i["title"], i["duration_s"]) for i in pl2["items"]] ==
          [("media/a.mp3", "Artist", "Title", 123.0), ("media/b.flac", "", "Only", None), ("media/sub/x.wav", "", "", None)], str(pl2))
    pls = "[playlist]\nFile1=c d.ogg\nTitle1=X - Y\nLength1=10\nFile2=a.mp3\nNumberOfEntries=2\n"
    pl3, _ = st.parse_import(pls, "P")
    check("pls import", [(i["file"], i["title"]) for i in pl3["items"]] == [("media/c d.ogg", "Y"), ("media/a.mp3", "")])
    exp = st.export_m3u8(pl2)
    check("m3u8 export", exp.startswith("#EXTM3U\n#EXTINF:123,Artist - Title\na.mp3\n"), exp)
    rt2, _ = st.parse_import(exp, "Round")
    check("export/import roundtrip", [i["file"] for i in rt2["items"]] == [i["file"] for i in pl2["items"]])
    errs = []
    playlists.validate_source({"kind": "playlist", "playlist": "Test", "shuffle": "yes", "crossfade_s": 99}, st, errs, "s")
    check("validate_source flags", len(errs) == 2, errs)
    errs = []
    playlists.validate_source({"kind": "playlist", "playlist": "Nope"}, st, errs, "s")
    check("validate_source missing playlist", len(errs) == 1 and "gibt es nicht" in errs[0], errs)
    check("delete", st.delete("Test") and not st.exists("Test"))

    import server
    body = (b"--XX\r\nContent-Disposition: form-data; name=\"files\"; filename=\"one.mp3\"\r\nContent-Type: audio/mpeg\r\n\r\nAAA\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nignored\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"files\"; filename=\"zwei%20x.flac\"\r\n\r\nBB\r\nBB\r\n--XX--\r\n")
    files = server.parse_multipart('multipart/form-data; boundary=XX', body)
    check("multipart parse", files == [("one.mp3", b"AAA"), ("zwei x.flac", b"BB\r\nBB")], str(files))
    cfg = json.loads(json.dumps(server.DEFAULT_CONFIG))
    cfg["services"][0]["audio"]["source"] = {"kind": "playlist", "playlist": "Nope"}
    _, errs = server.validate_config(cfg)
    check("config: unknown playlist rejected", any("gibt es nicht" in e for e in errs), errs)
    cfg["services"][0]["audio"]["source"] = {"kind": "playlist"}
    _, errs = server.validate_config(cfg)
    check("config: playlist name required", any("Name fehlt" in e for e in errs), errs)
    world(server)
    shutil.rmtree(rt, ignore_errors=True)


def world(server):
    """Weltweite Standards (untested) durch die echte Server-Prüfung: Kanal, Rate, Ausgabeweg, typische Fehler."""
    def prog(codec="mpeg2", audio="ac3", **kw):
        return dict({"key": "prg1", "name": "LAB 1", "provider": "LAB", "sid": "0x0001", "lcn": 1, "type": "tv",
                     "video": {"source": {"kind": "testpattern", "pattern": "smptebars"}, "codec": codec, "resolution": "sd",
                               "bitrate_kbps": 4000, "encoder": "cpu", "hdr": "sdr"},
                     "audio": {"codec": audio, "bitrate_kbps": 192, "source": {"kind": "tone"}}, "eit": {"title": "T", "description": "D"}}, **kw)

    def run(mode, sec, rf, progs):
        cfg = json.loads(json.dumps(server.DEFAULT_CONFIG))
        cfg["mode"] = mode
        cfg[mode] = sec
        cfg["rf"].update(rf)
        cfg["dvb"] = {"programs": progs, "si": {"network_id": "0x04D2", "network_name": "LAB", "onid": "0x04D2", "tsid": "0x04D2"}}
        return server.validate_config(cfg)

    c, e = run("atsc", {}, {"channel": "A14"}, [prog()])
    check("world: ATSC A14 473 MHz, 19,39 Mbit/s, Gerät", not e and c["rf"]["freq_hz"] == 473000000 and abs(c["_dvb"]["rate_bps"] - 19392658.46) < 1
          and c["_path"] == "device", (e, c.get("rf"), c.get("_path")))
    c, e = run("isdbt", {"layers": 2, "region": "br"}, {"channel": "A20"}, [prog("h264", "aac"), prog("h264", "aac", key="prg2", sid="0x0002", lcn=2, oneseg=True)])
    check("world: ISDB-Tb 2 Schichten 17,27 Mbit/s, Mitte + 1/7 MHz", not e and c["rf"]["freq_hz"] == 509142857 and abs(c["_dvb"]["rate_bps"] - 17267628.8) < 2, (e, c.get("rf")))
    _, e = run("isdbt", {"mode": 2}, {"channel": "A20"}, [prog("h264", "aac")])
    check("world: ISDB-T Mode 2 abgelehnt (nicht echtzeitfähig)", any("mode 2" in x for x in e), e)
    c, e = run("isdbt", {"region": "jp"}, {"channel": "J27"}, [prog("mpeg2", "aac")])
    check("world: ISDB-T Japan J27 557,142857 MHz", not e and c["rf"]["freq_hz"] == 557142857, (e, c.get("rf")))
    c, e = run("dtmb", {"constellation": "64-QAM", "code_rate": "0.8", "header": 420}, {"channel": "DS25"}, [prog("h264", "mp2")])
    check("world: DTMB DS25 610 MHz, 64-QAM 0,8 PN420 32,49 Mbit/s", not e and c["rf"]["freq_hz"] == 610000000 and abs(c["_dvb"]["rate_bps"] - 32486400) < 1, (e, c.get("rf")))
    _, e = run("dtmb", {"constellation": "32-QAM", "code_rate": "0.4", "header": 420}, {"channel": "DS25"}, [prog("h264", "mp2")])
    check("world: DTMB 32-QAM nur 0,8", any("dtmb.code_rate" in x for x in e), e)
    c, e = run("dvbc", {"annex": "b", "constellation": "256-QAM", "sps": 2}, {"channel": "C80"}, [prog()])
    check("world: J.83 B 256-QAM 38,81 Mbit/s, C80 561 MHz, Gerät", not e and c["rf"]["freq_hz"] == 561000000 and abs(c["_dvb"]["rate_bps"] - 38810701) < 2
          and c["_path"] == "device", (e, c.get("rf"), c.get("_path")))
    cfg = json.loads(json.dumps(server.DEFAULT_CONFIG))
    cfg["mode"] = "pal"
    cfg["pal"] = {"system": "NTSC_M", "image": {"kind": "testpattern", "pattern": "smptebars"}, "sound": {"kind": "tone"}}
    cfg["rf"].update({"channel": "A14"})
    c, e = server.validate_config(cfg)
    check("world: NTSC-M A14 Bildträger 471,25 MHz, 7,993 MS/s", not e and c["rf"]["freq_hz"] == 471250000 and c["_pal"]["fs"] == 7993007, (e, c.get("rf"), c.get("_pal")))
    cfg = json.loads(json.dumps(server.DEFAULT_CONFIG))
    cfg["mode"] = "fm"
    cfg["fm"] = {"freq_mhz": 80.0, "region": "jp", "rds": {"enable": True, "standard": "rbds", "pi": "0x54A8", "ps": "LAB"}}
    cfg["rf"].update({"channel": "fm", "freq_hz": 80000000})
    c, e = server.validate_config(cfg)
    check("world: UKW Japan 80,0 MHz erlaubt", not e and c["rf"]["freq_hz"] == 80000000, e)
    cfg["fm"]["region"] = "eu"
    _, e = server.validate_config(cfg)
    check("world: UKW 80,0 MHz ohne Region Japan abgelehnt", any("87,5" in x for x in e), e)


def http_test(port=None):
    import socket
    if port is None:                       # free port: never collide with a live or mock server
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
    rt = tempfile.mkdtemp(prefix="dablab_rt_")
    base = f"http://127.0.0.1:{port}"
    p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "server.py"), "--port", str(port), "--uri", "ip:127.0.0.1",
                          "--source", "testgen", "--runtime", rt, "--no-priority", "--agent", "off"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    def req(path, data=None, method=None, ctype="application/json"):
        r = urllib.request.Request(base + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                   headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(r, timeout=30) as h:
                return h.status, h.read(), h.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    try:
        for _ in range(40):
            time.sleep(0.5)
            try:
                s, b, _ = req("/api/state")
                break
            except Exception:
                continue
        st = json.loads(b)
        if st.get("server", {}).get("pid") != p.pid:
            raise SystemExit(f"Port {port}: fremder Server (pid {st.get('server', {}).get('pid')}), Abbruch")
        check("server up without Pluto", st["pluto"]["connected"] is False)
        s, b, _ = req("/api/tx/start", b"{}")
        check("tx/start refused without Pluto", s == 503, b)
        # multipart upload of two files + playlist append
        body = (b"--B1\r\nContent-Disposition: form-data; name=\"f\"; filename=\"song one.mp3\"\r\n\r\n" + b"\x00" * 100 + b"\r\n"
                b"--B1\r\nContent-Disposition: form-data; name=\"f\"; filename=\"song2.ogg\"\r\n\r\n" + b"\x01" * 50 + b"\r\n"
                b"--B1\r\nContent-Disposition: form-data; name=\"f\"; filename=\"bad.txt\"\r\n\r\nxx\r\n--B1--\r\n")
        s, b, _ = req("/api/upload?kind=audio&playlist=Demo", body, ctype="multipart/form-data; boundary=B1")
        j = json.loads(b)
        check("multi upload", s == 200 and [f["file"] for f in j["files"]] == ["media/song one.mp3", "media/song2.ogg"], b[:300])
        check("non-audio rejected in list", j["errors"] == ["bad.txt: kein Audioformat"], j["errors"])
        check("playlist appended", j["playlist"]["name"] == "Demo" and len(j["playlist"]["items"]) == 2)
        s, b, _ = req("/api/upload?kind=audio&name=raw.wav", b"RIFF")
        check("raw upload still works", s == 200 and json.loads(b)["file"] == "media/raw.wav", b)
        s, b, _ = req("/api/playlists")
        check("list playlists", json.loads(b)["playlists"][0]["count"] == 2, b)
        s, b, _ = req("/api/playlists/Demo", json.dumps({"items": [{"file": "media/raw.wav", "title": "Raw", "duration_s": 3}, "song2.ogg"]}).encode(), "PUT")
        check("PUT playlist", s == 200 and [i["file"] for i in json.loads(b)["items"]] == ["media/raw.wav", "media/song2.ogg"], b)
        s, b, _ = req("/api/playlists/Demo", json.dumps({"items": [{"file": "../x.mp3"}]}).encode(), "PUT")
        check("PUT playlist invalid -> 400", s == 400, b)
        s, b, h = req("/api/playlists/Demo/export")
        check("export m3u8", s == 200 and b.startswith(b"#EXTM3U\n#EXTINF:3,Raw\nraw.wav\n"), b)
        s, b, _ = req("/api/playlists/Imp/import", b"#EXTM3U\n#EXTINF:5,A - B\nsong one.mp3\nsong2.ogg\n", ctype="text/plain")
        j = json.loads(b)
        check("import m3u", s == 200 and len(j["items"]) == 2 and j["items"][0]["artist"] == "A", b)
        s, b, _ = req("/api/live", json.dumps({"playlist": {"service": "srv1", "action": "fly"}}).encode())
        check("live playlist validation", s == 400, b)
        s, b, _ = req("/api/live", json.dumps({"playlist": {"service": "srv1", "action": "next"}}).encode())
        check("live playlist without session -> 409", s == 409, b)
        cfg = st["config"]
        cfg["services"][0]["audio"]["source"] = {"kind": "playlist", "playlist": "Demo", "shuffle": True, "loop": True,
                                                  "crossfade_s": 2, "normalize": True, "dls_from_tags": True}
        s, b, _ = req("/api/config", json.dumps(cfg).encode(), "PUT")
        check("config with playlist source accepted", s == 200, b)
        s, b, _ = req("/api/playlists/Demo", method="DELETE")
        check("delete playlist", s == 200)
        s, b, _ = req("/api/config", json.dumps(cfg).encode(), "PUT")
        check("config rejects deleted playlist", s == 400, b)
        s, b, _ = req("/api/state")
        st = json.loads(b)
        check("state has tx.now_playing", "now_playing" in st["tx"])
        check("state rf.path/standard", st["rf"]["path"] == "usb" and st["standard"] == "dab" and st["pluto"]["path"] == "usb", st.get("rf"))
        # ---- multi-standard
        s, b, _ = req("/api/channels")
        ch = json.loads(b)
        check("channels.json served", ch["dab"]["band3"]["6C"] == 185.36 and ch["dvbt"]["channels"]["K21"]["center_mhz"] == 474 and ch["fm"]["raster_khz"] == 100)
        s, b, _ = req("/api/presets/UKW%20Standard")
        fm = json.loads(b)
        check("FM preset exists", s == 200 and fm["mode"] == "fm", b[:200])
        fm["fm"]["freq_mhz"] = 98.05
        s, b, _ = req("/api/config", json.dumps(fm).encode(), "PUT")
        j = json.loads(b)
        check("FM config accepted with raster warning", s == 200 and j["standard"] == "fm" and any("Raster" in w for w in j["warnings"]), b[:300])
        s, b, _ = req("/api/state")
        st = json.loads(b)
        check("FM rf.freq_hz from fm.freq_mhz", st["config"]["rf"]["freq_hz"] == 98050000 and st["config"]["rf"]["channel"] == "fm" and st["config"]["rf"]["backoff_db"] == 3, st["config"]["rf"])
        s, b, _ = req("/api/rf", json.dumps({"freq_hz": 101300000, "atten_db": 70}).encode())
        check("FM /api/rf freq", s == 200 and json.loads(b)["rf"]["channel"] == "fm", b[:200])
        s, b, _ = req("/api/state")
        check("FM /api/rf syncs fm.freq_mhz", json.loads(b)["config"]["fm"]["freq_mhz"] == 101.3)
        s, b, _ = req("/api/rf", json.dumps({"freq_hz": 120000000}).encode())
        check("FM /api/rf out of band -> 400", s == 400, b)
        s, b, _ = req("/api/live", json.dumps({"rds": {"ps": "TOOLONGPS", "ta": "x", "bogus": 1}}).encode())
        j = json.loads(b)
        check("FM live rds validation", s == 400 and len(j["errors"]) == 3, b)
        s, b, _ = req("/api/live", json.dumps({"dls": {"service": "srv1", "text": "x"}, "slide": {}}).encode())
        check("FM live rejects DAB-only key slide", s == 400, b)
        s, b, _ = req("/api/live", json.dumps({"rds": {"ps": "DABLAB", "ta": True, "rt": "Hallo"}}).encode())
        check("FM live rds ok -> 409 without session", s == 409, b)
        s, b, _ = req("/api/standards")
        check("standards endpoint", json.loads(b)["current"] == "fm")
        fm["fm"]["rds"]["pty"] = 99
        s, b, _ = req("/api/config", json.dumps(fm).encode(), "PUT")
        check("FM invalid pty -> 400", s == 400 and "pty" in b.decode(), b[:200])
        svcs = [{"key": "tv1", "service_id": "0x0001", "name": "LAB TV 1", "video": {"codec": "mpeg2", "size": "sd"}, "audio": {"codec": "mp2", "bitrate_kbps": 192}, "source": {"kind": "testpattern"}}]
        dvb = {"name": "T", "mode": "dvbt", "dvbt": {"channel": "k33", "constellation": "64-QAM", "anything": [1, 2]},
               "services": svcs, "rf": {"atten_db": 60}}
        s, b, _ = req("/api/config", json.dumps(dvb).encode(), "PUT")
        check("DVB-T opaque config accepted", s == 200 and json.loads(b)["standard"] == "dvbt", b[:200])
        s, b, _ = req("/api/state")
        c2 = json.loads(b)["config"]
        check("DVB-T channel -> freq", c2["rf"]["channel"] == "K33" and c2["rf"]["freq_hz"] == 570000000 and c2["dvbt"]["anything"] == [1, 2], c2["rf"])
        s, b, _ = req("/api/rf", json.dumps({"channel": "K24"}).encode())
        check("DVB-T /api/rf channel", s == 200 and json.loads(b)["rf"]["freq_hz"] == 498000000, b[:200])
        s, b, _ = req("/api/rf", json.dumps({"channel": "6C"}).encode())
        check("DVB-T rejects DAB channel", s == 400, b)
        s, b, _ = req("/api/config", json.dumps({"name": "C", "mode": "dvbc", "dvbc": {"freq_hz": 386000000}, "services": svcs}).encode(), "PUT")
        check("DVB-C custom freq accepted", s == 200, b[:200])
        # ---- media index + delete
        s, b, _ = req("/api/media")
        j = json.loads(b)
        check("media list has probe fields", s == 200 and all("probed" in m for m in j["media"] if m["kind"] == "audio") and "ffprobe" in j, b[:300])
        s, b, _ = req("/api/media/raw.wav", method="DELETE")
        j = json.loads(b)
        check("media delete", s == 200 and j["playlists"] == ["Imp"] or j["playlists"] == [], b)
        s, b, _ = req("/api/media/raw.wav", method="DELETE")
        check("media delete twice -> 404", s == 404)
        s, b, _ = req("/api/media/..%2F..%2Fpc_config.json", method="DELETE")
        check("media delete path escape -> 404", s == 404, b)
    finally:
        p.terminate()
        try:
            out = p.communicate(timeout=10)[0].decode("utf-8", "replace")
        except Exception:
            p.kill()
            out = ""
        if "--verbose" in sys.argv:
            print(out)
        shutil.rmtree(rt, ignore_errors=True)


if __name__ == "__main__":
    unit()
    if "--http" in sys.argv:
        http_test()
    print(f"\n{len(FAILS)} Fehler" + (": " + ", ".join(FAILS) if FAILS else ""))
    sys.exit(1 if FAILS else 0)
