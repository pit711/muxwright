"""Stand-in for dablab_run.py in hardware-free server tests (--backend local --runner tests_fake/fake_runner.py).

live:  reads the config; output udp -> sends 1316-byte datagrams to host:port at ~2 Mbit/s and prints
       status lines {"msg":"status","out_rate_bps","target_bps"}; output modulator -> int16 zeros on stdout.
media youtube <url> [--max-height N]: progress lines like the real runner, then the result JSON on stdout.
       A url containing "fail" ends with an error.
selftest: {"ok":true}
"""
import json
import os
import signal
import socket
import sys
import time


def err(**kw):
    kw.setdefault("t", round(time.time(), 3))
    sys.stderr.write(json.dumps(kw) + "\n")
    sys.stderr.flush()


def live(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    err(session=f"fake-{os.getpid()}")
    out = cfg.get("output") or {}
    with open(os.path.join(os.path.dirname(cfg_path), "fake_runner_last_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    t0 = last = time.time()
    sent = 0
    if out.get("kind") == "udp":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        pkt = (b"\x47\x1f\xff\x10" + b"\xff" * 184) * 7
        while True:
            s.sendto(pkt, (out["host"], int(out["port"])))
            sent += len(pkt)
            time.sleep(0.005)
            if time.time() - last >= 1.0:
                last = time.time()
                err(lvl="info", msg="status", uptime_s=round(last - t0, 1), out_bytes=sent,
                    out_rate_bps=round(sent * 8 / (last - t0)), target_bps=24128342, procs=2)
    else:
        z = bytes(4 * 65536)
        try:
            while True:
                sys.stdout.buffer.write(z)
        except (BrokenPipeError, OSError):
            pass


def youtube(url, mh):
    vid = "abc123"
    total = 10_000_000
    for i in range(1, 6):
        err(msg="youtube", youtube={"status": "downloading", "percent": i * 20.0, "downloaded_bytes": i * total // 5,
                                    "total_bytes": total, "speed_bps": 5_000_000, "eta_s": 5 - i,
                                    "file": None})
        time.sleep(0.15)
        if "fail" in url and i == 3:
            err(lvl="error", msg="yt-dlp: Video nicht verfügbar")
            print(json.dumps({"ok": False, "error": "Video nicht verfügbar"}))
            sys.exit(1)
    rt = os.environ.get("DABLAB_RUNTIME", ".")
    f = os.path.join(rt, "media", "video", vid + ".mp4")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "wb").write(b"\x00" * 1000)
    err(msg="youtube", youtube={"status": "finished", "percent": 100.0, "downloaded_bytes": total, "total_bytes": total,
                                "speed_bps": 5_000_000, "eta_s": 0, "file": f})
    print(json.dumps({"ok": True, "file": f, "id": vid, "title": "Testvideo", "duration_s": 12.5, "width": 854,
                      "height": min(mh, 480), "url": url, "license": "CC BY 3.0", "cached": False}))


def main():
    a = sys.argv[1:]
    if not a:
        return 2
    if a[0] == "selftest":
        print(json.dumps({"ok": True, "fake": True}))
    elif a[0] == "live":
        live(a[a.index("--config") + 1])
    elif a[:2] == ["media", "youtube"]:
        mh = int(a[a.index("--max-height") + 1]) if "--max-height" in a else 1080
        youtube(a[2], mh)
    else:
        print(json.dumps({"ok": False, "error": "fake: unbekannt " + " ".join(a)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
