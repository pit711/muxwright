"""Headless-Test des Windows-Installationspakets: still installieren, Server ohne echten Pluto starten, Einrichtung mit
Playwright durchklicken und dabei ALLE Komponenten in einer frischen WSL-Distro bauen lassen, danach Selbsttest und Render.

  python test/installer_e2e.py --setup ..\\installer\\Output\\Muxwright-Setup-0.9.0.exe [--dir H:\\muxtest] [--keep]

Sicherheit: der Testserver bekommt nur die Dokumentations-Adresse 192.0.2.1 (RFC 5737) als Pluto/Agent/RX und kein SSH –
er kann den echten Pluto nicht erreichen. Die WSL-Distro heißt „muxtest“ und wird vorher (falls vorhanden) entfernt.
"""
import argparse, json, os, pathlib, re, shutil, socket, subprocess, sys, time, urllib.error, urllib.request

from playwright.sync_api import expect, sync_playwright

T = lambda tid: f"[data-testid='{tid}']"  # noqa: E731
NOWIN = 0x08000000
ORDER = ["wsl_distro", "base", "ffmpeg", "tsduck", "odr", "welle", "modulators", "ytdlp"]
FORBIDDEN = re.compile(r"(ffmpeg|ffprobe|x264|x265|fdk|odr-|welle|tsp\.exe|libavcodec|yt-dlp)", re.I)
T0 = time.time()


def say(msg):
    print(f"[{time.time() - T0:7.0f}s] {msg}", flush=True)


def run(cmd, timeout=600, check=True):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NOWIN)
    out = r.stdout + r.stderr
    txt = out.decode("utf-16-le", "ignore").replace("\x00", "") if b"\x00" in out[:200] else out.decode("utf-8", "replace")
    if check and r.returncode:
        raise SystemExit(f"FEHLER ({r.returncode}): {' '.join(map(str, cmd))}\n{txt[-3000:]}")
    return txt


def first_json(text):
    i = text.find("{\n")
    if i < 0:
        return {}
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return {}


def wsl_names():
    return run(["wsl", "-l", "-q"], check=False).split()


def api(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", required=True)
    ap.add_argument("--dir", default=r"H:\muxtest")
    ap.add_argument("--distro", default="muxtest")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--keep", action="store_true", help="Installation und Distro am Ende behalten")
    ap.add_argument("--reuse", action="store_true", help="vorhandene Installation/Distro weiterverwenden")
    a = ap.parse_args()
    assert a.distro not in ("dablab", "Ubuntu-24.04"), "nie die Arbeits-Distro verwenden"
    base = pathlib.Path(a.dir)
    app = base / "app"
    shots = base / "shots"
    if port_open(a.port):
        raise SystemExit(f"Port {a.port} ist belegt")

    # ------------------------------------------------------------ 1. frisch & still installieren
    if not a.reuse:
        if a.distro in wsl_names():
            say(f"entferne alte Test-Distro {a.distro}")
            run(["wsl", "--unregister", a.distro])
        if (app / "unins000.exe").exists():
            run([str(app / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=False)
            time.sleep(3)
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    shots.mkdir(exist_ok=True)
    if not (app / "Muxwright.exe").exists():
        say("installiere still")
        run([a.setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", f"/DIR={app}", f"/LOG={base / 'setup.log'}",
             "/LANG=de", "/TASKS="], timeout=900)
    files = [p for p in app.rglob("*") if p.is_file()]
    bad = [str(p.relative_to(app)) for p in files if FORBIDDEN.search(p.name) and p.suffix.lower() in (".exe", ".dll", ".so", ".a", ".lib", "")]
    mb = sum(p.stat().st_size for p in files) / 1e6
    say(f"installiert: {len(files)} Dateien, {mb:.0f} MB")
    assert (app / "Muxwright.exe").exists() and (app / "THIRD_PARTY.txt").exists() and (app / "pc" / "shop_data" / "shop.json").exists()
    assert (app / "pluto_fw" / "firmware" / "pluto.frm").exists() and (app / "wsl" / "setup_components.sh").exists()
    assert not bad, f"Codecs/Fremdprogramme im Paket: {bad}"
    assert not (app / "runtime" / "onboarding.json").exists()

    # ------------------------------------------------------------ 2. Server ohne echten Pluto
    env = {k: v for k, v in os.environ.items() if not k.startswith("DABLAB_")}
    log = open(base / "server.log", "wb")
    srv = subprocess.Popen([str(app / "Muxwright.exe"), "--no-browser", "--port", str(a.port), "--uri", "ip:192.0.2.1",
                            "--agent", "http://192.0.2.1:8080", "--rx-uri", "ip:192.0.2.1", "--device-host", "192.0.2.1",
                            "--no-device-ssh", "--wsl-distro", a.distro, "--no-priority"],
                           cwd=app, stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=NOWIN)
    try:
        for _ in range(240):
            if port_open(a.port):
                break
            if srv.poll() is not None:
                raise SystemExit("Server beendet:\n" + (base / "server.log").read_text("utf-8", "replace")[-3000:])
            time.sleep(0.5)
        st = api(a.port, "/api/state")
        assert st["onboarding"]["done"] is False, "frische Installation muss die Einrichtung verlangen"
        code = None
        try:
            api(a.port, "/api/rf", {"enabled": True})
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 403, f"Senden vor der Einrichtung muss gesperrt sein (HTTP {code})"
        try:
            api(a.port, "/api/setup/install", {"component": "base"})
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400, "Installation ohne Lizenzbestätigung muss abgelehnt werden"
        say("Server läuft, Sendesperre und Lizenzpflicht greifen")

        # -------------------------------------------------------- 3. Einrichtung durchklicken
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            page = br.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{a.port}/")
            expect(page.locator(T("onb-next"))).to_be_visible(timeout=20000)
            page.click(T("onb-next"))                                           # Willkommen → Gerät
            expect(page.locator(T("onb-check-pluto"))).not_to_have_class(re.compile(r"\bok\b"))   # kein Pluto erreichbar
            page.click(T("onb-next"))                                           # → PC-Leistung
            page.click(T("onb-next"))                                           # → Betriebsart
            page.click(T("onb-mode-lab"))
            page.click(T("onb-next"))
            for k in ("shielded", "no_broadcast_radiation", "terms", "privacy", "codecs"):
                page.check(T(f"onb-accept-{k}"))
            page.click(T("onb-next"))                                           # → Komponenten
            expect(page.locator(T("comp-wsl_distro"))).to_be_visible(timeout=60000)
            page.screenshot(path=str(shots / "komponenten_vorher.png"), full_page=True)
            for k in ORDER:
                card = page.locator(T(f"comp-{k}"))
                if "ok" in (card.get_attribute("class") or ""):
                    say(f"{k}: schon installiert")
                    continue
                expect(page.locator(T(f"comp-install-{k}"))).to_be_disabled()
                page.check(T(f"comp-accept-{k}"))
                expect(page.locator(T(f"comp-install-{k}"))).to_be_enabled(timeout=10000)
                page.click(T(f"comp-install-{k}"))
                t = time.time()
                say(f"{k}: Installation gestartet")
                deadline = t + 3600
                while time.time() < deadline:
                    cls = card.get_attribute("class") or ""
                    if re.search(r"\bok\b", cls):
                        break
                    if re.search(r"\bbad\b", cls):
                        page.screenshot(path=str(shots / f"fehler_{k}.png"), full_page=True)
                        job = api(a.port, "/api/setup")["job"]
                        raise SystemExit(f"{k} fehlgeschlagen: {job.get('error')}\n" + "\n".join(job.get("lines", [])[-30:]))
                    time.sleep(3)
                else:
                    raise SystemExit(f"{k}: Zeitüberschreitung")
                say(f"{k}: installiert in {time.time() - t:.0f} s")
            expect(page.locator(T("comp-all-done"))).to_be_visible(timeout=120000)
            page.screenshot(path=str(shots / "komponenten_fertig.png"), full_page=True)
            page.click(T("onb-next"))
            page.click(T("onb-finish"))
            expect(page.locator(T("onb-next"))).to_have_count(0, timeout=10000)
            page.screenshot(path=str(shots / "dashboard.png"))
            br.close()

        st = api(a.port, "/api/state")
        assert st["onboarding"]["done"] is True
        sup = api(a.port, "/api/setup?fresh=1")
        assert sup["complete"], sup
        cons = json.loads((app / "runtime" / "components.json").read_text("utf-8"))
        assert set(ORDER) <= set(cons), f"Zustimmungsnachweis unvollständig: {sorted(cons)}"
        say(f"Einrichtung abgeschlossen, Ubuntu {sup.get('ubuntu')}, Nachweis für {len(cons)} Komponenten")
    finally:
        # Muxwright.exe (PyInstaller) startet einen Kindprozess: ganzen Baum beenden
        subprocess.run(["taskkill", "/PID", str(srv.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
        try:
            srv.wait(15)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()

    # ------------------------------------------------------------ 4. Selbsttest und Render in der neuen Distro
    def w(p):
        p = str(p).replace("\\", "/")
        return f"/mnt/{p[0].lower()}{p[2:]}"
    runner = w(app / "runner" / "dablab_run.py")
    out = run(["wsl", "-d", a.distro, "--", "python3", runner, "selftest", "--quick"], timeout=1800, check=False)
    (base / "selftest.log").write_text(out, "utf-8")
    st = first_json(out)
    bad = [c["name"] for c in st.get("checks", []) if not c.get("ok")]
    say(f"Selbsttest: ok={st.get('ok')} {len(st.get('checks', []))} Punkte" + (f", fehlgeschlagen: {bad}" if bad else ""))
    if st.get("ok") is not True:
        raise SystemExit(f"Selbsttest fehlgeschlagen: {bad}")
    tmp = w(base)
    for name, cfg, fmt, sec in (("dab", "minimal_ton.json", "cf32", 4), ("dvbt", "dvbt_testbild_sd.json", "ts", 4),
                                ("fm", "fm_standard.json", "cf32", 4)):
        dst = f"{tmp}/render_{name}.{fmt}"
        o = run(["wsl", "-d", a.distro, "--", "python3", runner, "render", "--config", w(app / "runner" / "examples" / cfg),
                 "--seconds", str(sec), "--format", fmt, "--out", dst], timeout=1800, check=False)
        f = base / f"render_{name}.{fmt}"
        size = f.stat().st_size if f.exists() else 0
        say(f"Render {name}: {size / 1e6:.1f} MB")
        if not size:
            raise SystemExit(f"Render {name} fehlgeschlagen:\n{o[-3000:]}")
    o = run(["wsl", "-d", a.distro, "--", "python3", runner, "check", "--config", w(app / "runner" / "examples" / "minimal_ton.json"),
             "--seconds", "12"], timeout=1800, check=False)
    (base / "check_dab.log").write_text(o, "utf-8")
    ck = first_json(o)
    svc = [x.get("label") for x in ck.get("services", [])]
    say(f"DAB-Prüfung (welle-cli): ok={ck.get('ok')} sync={ck.get('sync')} SNR {ck.get('snr_db')} dB, Dienste {svc}, "
        f"Abweichungen {ck.get('mismatches')}")
    if ck.get("ok") is not True:
        raise SystemExit("DAB-Prüfung fehlgeschlagen")

    if not a.keep:
        say("räume auf (Distro und Installation entfernen; --keep behält beides)")
        run(["wsl", "--unregister", a.distro], check=False)
        run([str(app / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=False)
    say("OK – Installationspaket headless geprüft")


if __name__ == "__main__":
    main()
