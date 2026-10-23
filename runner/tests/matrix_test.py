#!/usr/bin/env python3
"""Funktionsmatrix: viele Varianten per `check` (render -> welle-cli) pruefen, Kurzbericht als JSON.

  python3 tests/matrix_test.py [--only name1,name2] [--seconds 10] > matrix.json
"""
import argparse, copy, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(os.path.dirname(HERE), "dablab_run.py")


def svc(key, sid, label, short, codec="aac-lc", br=64, sr=48000, ch=2, prof="EEP_A", lvl=3, typ="dabplus", **kw):
    s = {"key": key, "sid": sid, "label": label, "shortlabel": short, "type": typ,
         "audio": {"source": {"kind": "tone", "freq_hz": 1000, "level_dbfs": -18}, "codec": codec,
                   "samplerate": sr, "channels": ch, "bitrate": br},
         "protection": {"profile": prof, "level": lvl},
         "pad": {"enable": True, "bytes": 16, "dls": {"enable": True, "text": "DLS %s" % label}}}
    s.update(kw)
    return s


def base(mode=1, services=None, **ens):
    e = {"eid": "0x4FFF", "ecc": "0xE0", "label": "DABLAB Matrix", "shortlabel": "Matrix"}
    e.update(ens)
    return {"name": "matrix", "mode": mode, "ensemble": e, "services": services or [], "clusters": [],
            "tii": {"enable": False}, "rf": {"channel": "6C", "sample_rate": 4096000, "backoff_db": 12}}


V = {}
V["mode2"] = base(2, [svc("a", "0xD101", "Modus II", "ModusII", "he-aac", 48)])
V["mode3"] = base(3, [svc("a", "0xD102", "Modus III", "ModusIII", "he-aac", 48)])
V["mode4"] = base(4, [svc("a", "0xD103", "Modus IV", "ModusIV", "he-aac", 48)])
V["dabplus_32k_mono_heaac"] = base(1, [svc("a", "0xD111", "HE-AAC 32k Mono", "HE32", "he-aac", 32, 32000, 1),
                                       svc("b", "0xD112", "AAC-LC 32k St", "LC32", "aac-lc", 64, 32000, 2),
                                       svc("c", "0xD113", "HEv2 32k 8kbit", "HEv2 8", "he-aac-v2", 8, 32000, 2,
                                           pad={"enable": False})])
V["dabplus_rates"] = base(1, [svc("a", "0xD121", "AAC-LC 192k", "LC192", "aac-lc", 192, prof="EEP_B", lvl=1),
                              svc("b", "0xD122", "HE-AAC 24k", "HE24", "he-aac", 24, prof="EEP_A", lvl=1),
                              svc("c", "0xD123", "AAC-LC 96k 4-A", "LC96", "aac-lc", 96, prof="EEP_A", lvl=4),
                              svc("d", "0xD124", "AAC 128k 4-B", "AAC128", "aac-lc", 128, prof="EEP_B", lvl=4)])
V["mp2_uep_levels"] = base(1, [svc("a", "0xD131", "MP2 UEP1 64k", "UEP1", "mp2", 64, typ="dab", prof="UEP", lvl=1,
                                   pad={"enable": False}),
                               svc("b", "0xD132", "MP2 UEP5 384k", "UEP5", "mp2", 384, typ="dab", prof="UEP", lvl=5,
                                   pad={"enable": False}),
                               svc("c", "0xD133", "MP2 EEP2B 160k", "EEP2B", "mp2", 160, typ="dab", prof="EEP_B", lvl=2,
                                   pad={"enable": False})])
for s in V["mp2_uep_levels"]["services"]:
    s["audio"]["mp2_mode"] = "j"
V["utf8_lto_rc"] = base(1, [svc("a", "0xD141", "Grüße Öl", "Grüße", "he-aac", 48, pty=5, pty_dynamic=True,
                                language="0x09",
                                pad={"enable": True, "bytes": 16,
                                     "dls": {"enable": True, "text": "UTF-8: Grüße €", "charset": "utf8"}})],
                        charset="utf8", lto=-6, reconfig_counter=42, international_table=1)
V["tii_cfr"] = base(1, [svc("a", "0xD151", "TII CFR", "TII", "he-aac", 48)])
V["tii_cfr"]["tii"] = {"enable": True, "comb": 5, "pattern": 3}
V["tii_cfr"]["rf"]["cfr"] = True
V["tii_only"] = base(1, [svc("a", "0xD152", "TII 5/3", "TII", "he-aac", 48)])
V["tii_only"]["tii"] = {"enable": True, "comb": 5, "pattern": 3}
V["cfr_only"] = base(1, [svc("a", "0xD153", "CFR an", "CFR", "he-aac", 48)])
V["cfr_only"]["rf"]["cfr"] = True
V["cfr_strong"] = base(1, [svc("a", "0xD154", "CFR stark", "CFR", "he-aac", 48)])
V["cfr_strong"]["rf"].update({"cfr": True, "cfr_papr_db": 8, "cfr_error_clip": 1.0})
for _c, _p, _o in ((1, 11, False), (12, 40, False), (5, 3, True)):
    _n = "tii_%d_%d%s" % (_c, _p, "_old" if _o else "")
    V[_n] = base(1, [svc("a", "0xD155", "TII Test", "TII", "he-aac", 48)])
    V[_n]["tii"] = {"enable": True, "comb": _c, "pattern": _p, "old_variant": _o}
V["slideshow_data_spi"] = base(1, [
    svc("a", "0xD161", "Audio mit SLS", "SLS", "he-aac-v2", 64,
        pad={"enable": True, "bytes": 58, "dls": {"enable": True, "text": "SLS-Test"},
             "sls": {"enable": True, "slides": ["media/slide2.jpg"]}}),
    {"key": "pkt", "sid": "0xE0D10162", "label": "Paket-SLS", "shortlabel": "PktSLS", "type": "data",
     "data": {"kind": "slideshow_packet", "bitrate": 16, "packet_address": 7, "files": ["media/slide1.jpg"]},
     "protection": {"profile": "EEP_A", "level": 3}},
    {"key": "spi", "sid": "0xE0D10163", "label": "SPI Logos", "shortlabel": "SPI", "type": "data",
     "data": {"kind": "spi", "bitrate": 16, "packet_address": 8, "files": ["media/logo.png"]},
     "spi": {"logo": "media/logo_320x240.png"}, "protection": {"profile": "EEP_A", "level": 3}}])
V["sources"] = base(1, [
    svc("a", "0xD171", "Rauschen", "Rausch", source={"kind": "noise", "level_dbfs": -20}),
    svc("b", "0xD172", "Sweep", "Sweep", source={"kind": "sweep", "level_dbfs": -18}),
    svc("c", "0xD173", "Polaritaet", "Polar", source={"kind": "polarity", "level_dbfs": -12, "freq_hz": 50}),
    svc("d", "0xD174", "Stille", "Stille", source={"kind": "silence"})])
for s in V["sources"]["services"]:
    s["audio"]["source"] = s.pop("source")
V["overload"] = base(1, [svc("s%d" % i, "0xD18%d" % i, "Voll %d" % i, "Voll%d" % i, "aac-lc", 192, prof="EEP_A", lvl=1)
                         for i in range(4)])


def run(name, cfg, seconds):
    fd, path = tempfile.mkstemp(suffix=".json", prefix="mx_%s_" % name)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, ensure_ascii=False)
    t0 = time.time()
    r = subprocess.run([sys.executable, RUNNER, "check", "--config", path, "--seconds", str(seconds), "--wait", "35"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.unlink(path)
    try:
        rep = json.loads(r.stdout.decode())
    except Exception:
        return {"ok": False, "rc": r.returncode, "stdout": r.stdout.decode()[-500:], "stderr": r.stderr.decode()[-500:]}
    out = {"ok": rep.get("ok"), "rc": r.returncode, "elapsed_s": round(time.time() - t0, 1),
           "errors": rep.get("errors"), "mismatches": rep.get("mismatches"), "warnings": rep.get("warnings"),
           "snr_db": rep.get("snr_db"), "capacity_cu": (rep.get("capacity") or {}).get("used_cu"),
           "ensemble": rep.get("ensemble"), "tii": [(t["comb"], t["pattern"]) for t in rep.get("tii", [])],
           "level_s16": (rep.get("render") or {}).get("level_s16"),
           "services": [{k: s.get(k) for k in ("sid", "label", "shortlabel", "fig2label", "codec", "mode", "bitrate",
                                               "protection", "samplerate", "channels", "pty", "pty_dynamic",
                                               "language", "dls", "audio_level_db", "errors")}
                        | {"slides": [(x["w"], x["h"], x["bytes"]) for x in s.get("slides", [])],
                           "user_apps": [u["name"] for u in s.get("user_apps", [])]}
                        if sys.version_info >= (3, 9) else s for s in rep.get("services", [])],
           "packet_components": rep.get("packet_components"),
           "tx_fic_figs": (rep.get("tx_fic") or {}).get("figs_seen"),
           "tx_user_apps": (rep.get("tx_fic") or {}).get("user_apps"),
           "data_services": (rep.get("render") or {}).get("data_services")}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--seconds", type=float, default=10)
    a = ap.parse_args()
    names = a.only.split(",") if a.only else list(V)
    res = {}
    for n in names:
        res[n] = run(n, V[n], a.seconds)
        sys.stderr.write("%s: ok=%s %s\n" % (n, res[n].get("ok"), res[n].get("mismatches") or res[n].get("errors") or ""))
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
