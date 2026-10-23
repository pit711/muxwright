"""E2E-Test über das echte Dashboard, den echten Server, die WSL-Signalkette und den Pluto auf Kanal 6C.

Ablauf (headless Chromium, bedient wie ein Mensch über data-testid):
  1. Server starten (pc/server.py --min-atten 40) oder --url nutzen
  2. Drei-Dienste-Konfiguration importieren (HE-AACv2 + DLS/Slideshow, AAC-LC Stereo, DAB MP2), übernehmen
  3. HF: Kanal 6C, Dämpfung 40 dB (Freigabe Nutzer: nie unter 40 dB), Start, Senden an → ON AIR
  4. 30 s Dauerlauf: Underruns = 0, Rate 4,096 MS/s
  5. DLS live ändern (eindeutiger Text)
  6. Prüfen: Offline-Check und Kontrollempfang über den Pluto-RX → welle-cli muss Ensemble, alle 3 Dienste und den neuen DLS liefern
  7. Not-Aus → Pluto stumm (−89,75 dB), Zustand AUS
Aufruf: python test/e2e_6c.py [--url http://127.0.0.1:8792/] [--rx-gain 50] [--atten 40]
Screenshots + Bericht: test/e2e_6c/
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "e2e_6c"
CONFIG = ROOT / "runner" / "examples" / "drei_dienste.json"
T = lambda tid: f"[data-testid='{tid}']"  # noqa: E731
MIN_ATTEN = 40.0
RESULT: dict = {"steps": []}


def log(name, ok=True, **kw):
    RESULT["steps"].append({"step": name, "ok": ok, **kw})
    print(f"  {'✓' if ok else '✗'} {name} {json.dumps(kw, ensure_ascii=False) if kw else ''}", flush=True)
    if not ok:
        raise AssertionError(f"{name}: {kw}")


def api(base, path, data=None):
    req = urllib.request.Request(base.rstrip("/") + path, method="POST" if data is not None else "GET",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"null")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, timeout, what, every=0.5):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(every)
    raise AssertionError(f"Zeitüberschreitung: {what} (zuletzt {last!r})")


def shot(page: Page, name):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


def run(base, rx_gain, atten, seconds):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    labels = {s["key"]: s["label"] for s in cfg["services"]}
    ens_label = cfg["ensemble"]["label"]
    dls_new = f"E2E 6C {time.strftime('%H%M%S')}"
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        page = br.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(base)
        expect(page.locator(T("conn"))).to_have_class("conn ok", timeout=20000)
        st = api(base, "/api/state")
        log("Dashboard geladen, Pluto verbunden", bool(st["pluto"]["connected"]), fw=st["pluto"].get("fw"))

        # 2. Konfiguration importieren + übernehmen
        page.click(T("nav-presets"))
        page.set_input_files(T("preset-import-input"), str(CONFIG))
        page.wait_for_timeout(800)
        apply = page.locator(T("apply"))
        if apply.is_visible():
            apply.click()
            if page.locator(T("confirm-restart-ok")).is_visible():
                page.click(T("confirm-restart-ok"))
        wait_for(lambda: api(base, "/api/state")["config"]["ensemble"]["label"] == ens_label, 20, "Konfiguration übernommen")
        log("Drei-Dienste-Konfiguration importiert", True, services=list(labels.values()))

        # 3. HF: 6C, Dämpfung, Start, Senden an
        page.click(T("nav-hf"))
        page.select_option(T("rf-channel"), "6C")
        page.fill(T("rf-atten-input"), str(atten))
        page.press(T("rf-atten-input"), "Enter")
        page.wait_for_timeout(500)
        if apply.is_visible():
            apply.click()
            if page.locator(T("confirm-restart-ok")).is_visible():
                page.click(T("confirm-restart-ok"))
        api(base, "/api/tx/start", {})
        wait_for(lambda: api(base, "/api/state")["tx"]["state"] == "running", 90, "Kette läuft")
        # „Übernehmen“ stellt die Dämpfung des Entwurfs wieder her → Zielwert nach dem Start erneut setzen
        page.fill(T("rf-atten-input"), str(atten))
        page.press(T("rf-atten-input"), "Enter")
        wait_for(lambda: abs(api(base, "/api/state")["config"]["rf"]["atten_db"] - atten) < 0.01, 15, "Dämpfung gesetzt")
        # gestylter Schieber: die Spur fängt Zeigerereignisse ab → Checkbox direkt umschalten
        if not page.locator(T("rf-enable")).is_checked():
            page.locator(T("rf-enable")).evaluate("e => e.click()")
        expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=20000)
        st = api(base, "/api/state")
        assert st["pluto"]["atten_db"] >= MIN_ATTEN - 0.01, st["pluto"]
        log("ON AIR auf 6C", abs(st["pluto"]["lo_hz"] - 185_360_000) < 5, lo_hz=st["pluto"]["lo_hz"], atten_db=st["pluto"]["atten_db"])
        shot(page, "01_onair_hf")

        # 4. Dauerlauf
        u0 = st["tx"].get("underruns", 0)
        time.sleep(seconds)
        st = api(base, "/api/state")
        du = st["tx"].get("underruns", 0) - u0
        log(f"{seconds} s Dauerlauf ohne Underruns", du == 0, underruns=du, rate=st["tx"].get("rate_ms") or st["tx"].get("rate"))

        # 5. DLS live
        page.click(T("nav-dienste"))
        page.click(T("svc-item-0"))
        page.click(T("svc-tab-pad"))
        page.fill(T("dls-text"), dls_new)
        page.click(T("dls-send"))
        page.wait_for_timeout(1500)
        log("DLS live gesendet", True, text=dls_new)
        shot(page, "02_dls_live")

        # 6. Offline-Prüfung + Kontrollempfang
        page.click(T("nav-pruefen"))
        page.click(T("check-run"))
        expect(page.locator(T("report-check-verdict"))).to_be_visible(timeout=180000)
        chk = api(base, "/api/state").get("check") or {}
        log("Offline-Prüfung", bool(chk.get("sync")), ensemble=(chk.get("ensemble") or {}).get("label"),
            services=[s.get("label") for s in chk.get("services", [])])
        page.fill(T("monitor-seconds"), "25")
        page.fill(T("monitor-gain"), str(rx_gain))
        page.click(T("monitor-run"))
        if page.locator(T("confirm-monitor-ok")).is_visible():
            page.click(T("confirm-monitor-ok"))
        expect(page.locator(T("report-monitor-verdict"))).to_be_visible(timeout=240000)
        page.click(T("report-tab-monitor"))
        shot(page, "03_kontrollempfang")
        raw = api(base, "/api/state").get("monitor") or {}
        (OUT / "monitor_report.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
        mon = raw.get("decode") or raw          # neuer Server: Prüfbericht unter "decode", Spektrum daneben
        log("Kontrollempfang: Mitschnitt", bool(raw.get("ok")), rx_path=raw.get("rx_path"),
            underruns_during=raw.get("tx_underruns_during"), spectrum_delta_db=(raw.get("spectrum") or {}).get("delta_db"))
        got = {s.get("label", "").strip(): s for s in mon.get("services", [])}
        log("Kontrollempfang: Synchronisation", bool(mon.get("sync")), snr_db=mon.get("snr_db"))
        log("Kontrollempfang: Ensemble-Label", (mon.get("ensemble") or {}).get("label", "").strip() == ens_label,
            label=(mon.get("ensemble") or {}).get("label"))
        missing = [l for l in labels.values() if l not in got]
        log("Kontrollempfang: alle Dienste", not missing, found=list(got), missing=missing)
        dls_rx = (got.get(labels["srv1"]) or {}).get("dls", "")
        log("Kontrollempfang: neuer DLS-Text", dls_new in (dls_rx or ""), dls=dls_rx)

        # 7. Not-Aus
        page.click(T("estop"))
        wait_for(lambda: api(base, "/api/state")["tx"]["state"] in ("idle", "stopped"), 30, "Kette gestoppt")
        st = api(base, "/api/state")
        log("Not-Aus: Pluto stumm", st["pluto"]["atten_db"] >= 89.7, atten_db=st["pluto"]["atten_db"])
        expect(page.locator(T("tx-state"))).to_have_text("AUS", timeout=10000)
        shot(page, "04_notaus")
        log("keine JS-Fehler", not errors, errors=errors[:5])
        br.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--rx-gain", type=float, default=50)
    ap.add_argument("--atten", type=float, default=40)
    ap.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    a.atten = max(MIN_ATTEN, a.atten)
    srv = None
    base = a.url
    if not base:
        port = free_port()
        base = f"http://127.0.0.1:{port}/"
        srv = subprocess.Popen([sys.executable, "-u", str(ROOT / "pc" / "server.py"), "--port", str(port),
                                "--min-atten", str(MIN_ATTEN)], stdout=open(HERE / "e2e_6c_server.log", "w"),
                               stderr=subprocess.STDOUT)
        wait_for(lambda: api(base, "/api/state"), 30, "Server startet")
    ok = False
    try:
        run(base, a.rx_gain, a.atten, a.seconds)
        ok = True
    finally:
        try:
            api(base, "/api/tx/stop", {})
        except Exception:
            pass
        if srv:
            srv.terminate()
            srv.wait(15)
        RESULT["ok"] = ok
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "result.json").write_text(json.dumps(RESULT, indent=1, ensure_ascii=False), encoding="utf-8")
        print("E2E 6C:", "BESTANDEN" if ok else "FEHLGESCHLAGEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
