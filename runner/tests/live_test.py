#!/usr/bin/env python3
"""Abnahmetest fuer `live`: Echtzeit-Leser (16,384 MB/s), ctl-Aenderungen, langsamer Leser, Aufraeumen.

Aufruf (im runtime-/runner-Ordner):
  python3 tests/live_test.py --config examples/durchsagen.json --seconds 30 --outdir /tmp/lt
Optionen: --sigterm (Abbruch per SIGTERM statt stdout-EOF), --no-decode
"""
import argparse, json, os, signal, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(os.path.dirname(HERE), "dablab_run.py")


def ps_lines():
    out = subprocess.run(["ps", "-eo", "pid,ppid,stat,args"], stdout=subprocess.PIPE).stdout.decode("utf-8", "replace")
    return out.splitlines()[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--slow-seconds", type=float, default=10)
    ap.add_argument("--rate", type=int, default=4096000)
    ap.add_argument("--outdir", default="/tmp/dablab_livetest")
    ap.add_argument("--sigterm", action="store_true")
    ap.add_argument("--no-decode", action="store_true")
    ap.add_argument("--ctl-at", type=float, default=12.0)
    ap.add_argument("--cluster", type=int, default=1)
    ap.add_argument("--dls-service", default=None)
    ap.add_argument("--new-slide", default="media/logo_320x240.png")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    R = {"config": a.config}
    bps = a.rate * 4
    p = subprocess.Popen([sys.executable, RUNNER, "live", "--config", a.config], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, bufsize=0)
    err_lines = []
    sess = {}

    def rd_err():
        for line in iter(p.stderr.readline, b""):
            s = line.decode("utf-8", "replace").strip()
            err_lines.append((time.time(), s))
            if not sess and s.startswith('{"session"'):
                sess.update(json.loads(s))
    th = threading.Thread(target=rd_err, daemon=True)
    th.start()
    fd = p.stdout.fileno()
    # erstes Byte abwarten
    t_start_cmd = time.time()
    first = os.read(fd, 4)
    t0 = time.time()
    R["startup_s"] = round(t0 - t_start_cmd, 2)
    got = len(first)
    per_sec = {}
    segA = open(os.path.join(a.outdir, "A.s16"), "wb")
    segB = open(os.path.join(a.outdir, "B.s16"), "wb")
    ctl_res = []
    ctl_done = False
    cfg = json.load(open(a.config))
    dls_key = a.dls_service or next((s["key"] for s in cfg["services"] if (s.get("pad") or {}).get("enable")), None)
    sls_key = next((s["key"] for s in cfg["services"] if ((s.get("pad") or {}).get("sls") or {}).get("enable")), None)
    has_cluster = any(int(str(k.get("id")), 0) == a.cluster for k in cfg.get("clusters", []))
    new_text = "NEU per ctl %s" % time.strftime("%H:%M:%S")
    R["ctl_text"] = new_text
    # Phase 1: Echtzeit
    while True:
        now = time.time()
        el = now - t0
        if el >= a.seconds:
            break
        target = int(bps * el)
        want = target - got
        if want <= 0:
            time.sleep(0.002)
            continue
        d = os.read(fd, min(want, 262144) // 4 * 4 or 4)
        if not d:
            R["eof_early"] = True
            break
        sec = int(el)
        per_sec[sec] = per_sec.get(sec, 0) + len(d)
        if 4 <= el < a.ctl_at:
            segA.write(d)
        if el >= a.ctl_at + 8:
            segB.write(d)
        got += len(d)
        if not ctl_done and el >= a.ctl_at:
            ctl_done = True
            reqs = []
            if dls_key:
                reqs.append({"dls": {"service": dls_key, "text": new_text}})
            if has_cluster:
                reqs.append({"announcement": {"cluster": a.cluster, "active": True}})
            if sls_key:
                reqs.append({"slide": {"service": sls_key, "file": a.new_slide}})
            if dls_key:
                reqs.append({"label": {"service": dls_key, "label": "Label per ctl", "shortlabel": "ctl"}})
            for req in reqs:
                t1 = time.time()
                r = subprocess.run([sys.executable, RUNNER, "ctl", "--session", sess.get("session", "?"), json.dumps(req)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                ctl_res.append({"req": req, "rc": r.returncode, "out": json.loads(r.stdout.decode() or "{}"),
                                "ms": round((time.time() - t1) * 1000)})
    segA.close()
    segB.close()
    t_rt = time.time() - t0
    secs = sorted(per_sec)
    full = [per_sec[s] / 4 for s in secs[1:-1]]  # erste/letzte Sekunde unvollstaendig
    R["realtime"] = {"seconds": round(t_rt, 2), "bytes": got, "samples": got // 4,
                     "rate_avg_sps": round(got / 4 / t_rt), "rate_min_sps": min(full) if full else None,
                     "rate_max_sps": max(full) if full else None, "target_sps": a.rate,
                     "deviation_ppm": round((got / 4 / t_rt - a.rate) / a.rate * 1e6)}
    R["ctl"] = ctl_res
    # Phase 2: langsamer Leser (25 %)
    slow_bytes = 0
    t1 = time.time()
    errs_before = len([l for _, l in err_lines if '"lvl": "error"' in l])
    while time.time() - t1 < a.slow_seconds:
        target = int(bps * 0.25 * (time.time() - t1))
        want = target - slow_bytes
        if want <= 0:
            time.sleep(0.005)
            continue
        d = os.read(fd, min(want, 65536) // 4 * 4 or 4)
        if not d:
            break
        slow_bytes += len(d)
    el = time.time() - t1
    R["slow_reader"] = {"seconds": round(el, 2), "rate_sps": round(slow_bytes / 4 / el), "fraction": round(slow_bytes / 4 / el / a.rate, 3),
                        "runner_errors": len([l for _, l in err_lines if '"lvl": "error"' in l]) - errs_before,
                        "runner_alive": p.poll() is None}
    # Phase 3: Ende
    t2 = time.time()
    if a.sigterm:
        p.send_signal(signal.SIGTERM)
        while os.read(fd, 65536):
            pass
    p.stdout.close()
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        rc = "TIMEOUT"
        p.kill()
    R["shutdown"] = {"how": "SIGTERM" if a.sigterm else "stdout geschlossen", "rc": rc,
                     "seconds": round(time.time() - t2, 2)}
    th.join(timeout=2)
    time.sleep(0.5)
    sid = sess.get("session", "")
    left = [l for l in ps_lines() if (sid[-6:] in l or ("dablab_" + sid.replace("-", "")[-10:]) in l) and "live_test" not in l]
    me = str(os.getpid())
    zomb = [l for l in ps_lines() if l.split()[2].startswith("Z") and l.split()[1] != me]
    # Zombies, deren Elternprozess nicht zu uns gehoert (z. B. die ssh-Shell), sind fremd -> mit Elternkommando melden
    zomb = [z + "  <- parent: " + next((l for l in ps_lines() if l.split()[0] == z.split()[1]), "?") for z in zomb]
    R["leftover_processes"] = left
    R["zombies"] = zomb
    tmp_left = [x for x in os.listdir("/tmp") if x.startswith("dablab-" + sid[-6:]) or x.startswith("dablab_" + sid.replace("-", "")[-10:])]
    R["leftover_tmp"] = tmp_left
    R["session"] = sid
    R["runner_log_tail"] = [l for _, l in err_lines[-6:]]
    R["runner_warnings"] = [l for _, l in err_lines if '"lvl": "warn"' in l or '"lvl": "error"' in l][:10]
    R["status_lines"] = [json.loads(l).get("rate_sps") for _, l in err_lines if '"msg": "Status"' in l]
    # Dekodieren
    if not a.no_decode:
        for seg in ("A", "B"):
            s16 = os.path.join(a.outdir, seg + ".s16")
            cf = os.path.join(a.outdir, seg + ".cf32.iq")
            subprocess.run([sys.executable, RUNNER, "iqconv", "--in", s16, "--in-rate", str(a.rate), "--out", cf],
                           stdout=subprocess.DEVNULL)
            r = subprocess.run([sys.executable, RUNNER, "decode", "--iq", cf, "--seconds", "8", "--config", a.config],
                               stdout=subprocess.PIPE)
            rep = json.loads(r.stdout.decode() or "{}")
            R["decode_" + seg] = {
                "sync": rep.get("sync"), "snr_db": rep.get("snr_db"),
                "services": {s["sid"]: {"label": s["label"], "shortlabel": s["shortlabel"], "dls": s["dls"],
                                        "slides": [(x["w"], x["h"], x["bytes"], x["type"]) for x in s.get("slides", [])],
                                        "errors": s["errors"]} for s in rep.get("services", [])},
                "fig0_19": rep.get("announcements"), "al_flag": (rep.get("ensemble") or {}).get("al_flag"),
                "seconds_iq": round(os.path.getsize(cf) / 8 / 2048000, 2)}
    print(json.dumps(R, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
