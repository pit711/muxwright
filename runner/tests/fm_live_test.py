#!/usr/bin/env python3
"""Abnahmetest UKW `live`: Echtzeit-Leser (16,384 MB/s = 4,096 MS/s int16 I/Q), ctl zur Laufzeit (RT/PS/TA),
zwei Mitschnittabschnitte (A vor, B nach dem ctl), CPU-Anteil aus den Statuszeilen, Aufraeumen der Kindprozesse.

Aufruf (im runner-Ordner, WSL):
  python3 tests/fm_live_test.py --config examples/fm_standard.json --seconds 60 --outdir /tmp/fmlt
Abschnitte: A = 5..25 s, B = 35..55 s; ctl bei --ctl-at (Standard 30 s). Danach `decode` beider Abschnitte.
"""
import argparse, json, os, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(os.path.dirname(HERE), "dablab_run.py")


def children(pid):
    out = subprocess.run(["ps", "-eo", "pid,ppid,args"], stdout=subprocess.PIPE).stdout.decode("utf-8", "replace")
    res = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[1] == str(pid):
            res.append((int(parts[0]), parts[2] if len(parts) > 2 else ""))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--rate", type=int, default=4096000)
    ap.add_argument("--outdir", default="/tmp/dablab_fm_livetest")
    ap.add_argument("--ctl-at", type=float, default=30.0)
    ap.add_argument("--no-decode", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    R = {"config": a.config, "seconds": a.seconds}
    bps = a.rate * 4
    p = subprocess.Popen([sys.executable, RUNNER, "live", "--config", a.config], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, bufsize=0)
    err = []
    sess = {}

    def rd_err():
        for line in iter(p.stderr.readline, b""):
            s = line.decode("utf-8", "replace").strip()
            err.append((time.time(), s))
            if not sess and s.startswith('{"session"'):
                sess.update(json.loads(s))
    threading.Thread(target=rd_err, daemon=True).start()
    fd = p.stdout.fileno()
    t_cmd = time.time()
    first = os.read(fd, 4)
    t0 = time.time()
    R["startup_s"] = round(t0 - t_cmd, 2)
    got = len(first)
    segA = open(os.path.join(a.outdir, "A.s16"), "wb")
    segB = open(os.path.join(a.outdir, "B.s16"), "wb")
    backlog_max = 0
    per = {}
    ctl_done = False
    new_rt = "NEU per ctl %s" % time.strftime("%H:%M:%S")
    R["ctl"] = {"rt": new_rt, "ps": "CTLTEST", "ta": True}
    kids = []
    ctl_thread = [None]

    def do_ctl():
        tc = time.time()
        r = subprocess.run([sys.executable, RUNNER, "ctl", "--session", sess["session"],
                            json.dumps({"rds": {"rt": new_rt, "ps": "CTLTEST", "ta": True}})],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        R["ctl_result"] = {"rc": r.returncode, "latency_s": round(time.time() - tc, 2),
                           "out": (r.stdout.decode("utf-8", "replace")[:600])}

    # Leser wie der PC-Server: 1-MB-Bloecke (pc/server.py liest min(1<<20, n) blockierend)
    CH = 1 << 20
    while True:
        el = time.time() - t0
        if el >= a.seconds:
            break
        want = int(bps * el) - got
        if want < CH:
            time.sleep(max(0.001, (CH - want) / bps))
            continue
        backlog_max = max(backlog_max, want)
        d = os.read(fd, CH)
        if not d:
            R["eof_early"] = True
            break
        got += len(d)
        per[int(el)] = per.get(int(el), 0) + len(d)
        if 5 <= el < 25:
            segA.write(d)
        elif 35 <= el < 55:
            segB.write(d)
        if el >= 2 and not kids:
            kids = children(p.pid)
        if not ctl_done and el >= a.ctl_at and sess.get("session"):
            ctl_done = True
            ctl_thread[0] = threading.Thread(target=do_ctl, daemon=True)
            ctl_thread[0].start()
    if ctl_thread[0]:
        ctl_thread[0].join(timeout=25)
    segA.close()
    segB.close()
    R["bytes"] = got
    secs = sorted(per)[2:-2]
    R["rate_min_sps"] = min(per[s] / 4 for s in secs) if secs else None
    R["rate_avg_sps"] = round(got / 4 / (time.time() - t0))
    R["max_backlog_ms"] = round(backlog_max / bps * 1000, 1)
    R["note_backlog"] = "Leser holt 1-MB-Bloecke (64 ms); Rueckstand > ~200 ms hiesse: Erzeuger zu langsam"
    # stdout schliessen -> EOF/EPIPE im Runner -> Ende
    p.stdout.close()
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.terminate()
        rc = p.wait(timeout=5)
    R["exit_rc"] = rc
    time.sleep(0.5)
    alive = []
    for pid, args_ in kids:
        try:
            os.kill(pid, 0)
            alive.append((pid, args_[:80]))
        except OSError:
            pass
    R["children_before"] = [k[1][:60] for k in kids]
    R["children_alive_after"] = alive
    # Statuszeilen auswerten
    st = []
    for t, s in err:
        if s.startswith("{"):
            try:
                j = json.loads(s)
            except Exception:
                continue
            if j.get("msg") == "Status":
                st.append(j)
    R["status_lines"] = len(st)
    if st:
        R["cpu_pct"] = {"mean": round(sum(j["cpu_pct"] for j in st) / len(st), 1), "max": max(j["cpu_pct"] for j in st),
                        "values": [j["cpu_pct"] for j in st]}
        R["rate_sps_status"] = [j["rate_sps"] for j in st]
        R["level_last"] = st[-1].get("level")
        R["carrier_last"] = st[-1].get("carriers")
    R["log_tail"] = [s[:300] for _, s in err[-4:]]
    R["session"] = sess.get("session")
    with open(os.path.join(a.outdir, "runner_stderr.log"), "w") as f:
        for t, s in err:
            f.write("%.3f %s\n" % (t, s))
    if not a.no_decode:
        for name in ("A", "B"):
            path = os.path.join(a.outdir, name + ".s16")
            r = subprocess.run([sys.executable, RUNNER, "decode", "--iq", path, "--config", a.config, "--seconds", "19",
                                "--format", "s16", "--rate", str(a.rate)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=600)
            try:
                rep = json.loads(r.stdout.decode("utf-8", "replace"))
            except Exception:
                rep = {"ok": False, "stderr": r.stderr.decode("utf-8", "replace")[-800:]}
            with open(os.path.join(os.path.dirname(HERE), "tests", "results_wsl", "fm_live_decode_%s.json" % name), "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1, ensure_ascii=False)
            m = rep.get("measured") or {}
            rd = rep.get("rds") or {}
            R["decode_" + name] = {"ok": rep.get("ok"), "deviation": m.get("deviation"), "pilot": m.get("pilot"),
                                   "rds_sub": m.get("rds"), "mpx_power_dbr": m.get("mpx_power_dbr"),
                                   "block_grid_lines": (m.get("audio") or {}).get("block_grid_lines"), "separation_db": (m.get("audio") or {}).get("separation_db"),
                                   "sinad_db": [(m.get("audio") or {}).get(s, {}).get("sinad_db") for s in ("left", "right")],
                                   "ps": rd.get("ps"), "rt": rd.get("radiotext"), "ta": rd.get("ta"), "pi": rd.get("pi"),
                                   "groups": rd.get("groups"), "bler": rd.get("bler_mean_pct"), "ct": rd.get("clock_time"),
                                   "mismatches": rep.get("mismatches")}
        b = R.get("decode_B") or {}
        R["ctl_verified"] = bool(b.get("rt") and b["rt"].rstrip() == new_rt and (b.get("ps") or "").rstrip() == "CTLTEST"
                                 and b.get("ta") is True)
    print(json.dumps(R, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
