"""Klick-Durchlauf durch das echte Dashboard mit Chrome: Einrichtung (Labor), alle Bereiche, alle Bedienelemente.

Sammelt JavaScript-Fehler, Konsolenfehler, HTTP-Fehler (≥ 400), Fehler-Toasts, horizontales Scrollen und kaputte Bilder.
Ausgelassen wird nur, was ein Betriebssystem-Fenster öffnen würde (Datei-Upload, Drucken) sowie Löschen.
Die Serverkonfiguration wird vorher gesichert und am Ende zurückgeschrieben; am Ende Not-Aus (Pluto stumm).
  python test/clickthrough.py [--url http://127.0.0.1:8792/] [--headless]
Bericht + Screenshots: test/clickthrough/
"""
import argparse, json, re, sys, time, urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass
OUT = Path(__file__).resolve().parent / "clickthrough"
SKIP = re.compile(r"upload|import|print|drucken|delete|trash|löschen|entfernen|remove|youtube|file|datei|reset|zurücksetzen|logout", re.I)
T = lambda tid: f"[data-testid='{tid}']"  # noqa: E731


def api(base, path, data=None, method=None):
    req = urllib.request.Request(base.rstrip("/") + path, method=method or ("POST" if data is not None else "GET"),
                                 data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8792/")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max-per-page", type=int, default=60)
    a = ap.parse_args()
    base = a.url
    OUT.mkdir(exist_ok=True)
    saved_cfg = api(base, "/api/state")["config"]
    bk = OUT / f"config_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"     # nie überschreiben
    bk.write_text(json.dumps(saved_cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    issues, clicks = [], []
    cur = {"where": "start"}

    def issue(kind, msg):
        e = {"where": cur["where"], "kind": kind, "msg": str(msg)[:400]}
        if e not in issues:
            issues.append(e)
            print(f"  ! [{e['where']}] {kind}: {e['msg'][:160]}", flush=True)

    with sync_playwright() as pw:
        try:
            br = pw.chromium.launch(channel="chrome", headless=a.headless)
        except Exception:
            br = pw.chromium.launch(headless=a.headless)
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: issue("js-exception", e))
        page.on("console", lambda m: issue("console-" + m.type, m.text) if m.type in ("error", "warning") and "favicon" not in m.text else None)
        def on_resp(r):
            if r.status >= 400 and "/api/" in r.url:
                try:
                    body = r.text()[:300]
                except Exception:
                    body = ""
                issue("http-%d" % r.status, f"{r.request.method} {r.url} → {body}")
        page.on("response", on_resp)
        page.on("requestfailed", lambda r: issue("request-failed", f"{r.method} {r.url} {r.failure}") if "/api/events" not in r.url else None)
        page.on("dialog", lambda d: d.dismiss())
        page.on("filechooser", lambda fc: issue("filechooser", "Dateiauswahl geöffnet (sollte ausgelassen sein)"))

        def toasts():
            for t in page.locator("#toasts .toast.error, #toasts .error").all_inner_texts():
                issue("toast-error", t)

        def close_dialogs():
            for _ in range(3):
                d = page.locator("dialog[open]:not(.onb)")
                if not d.count():
                    return
                cancel = d.locator("[data-testid$='-cancel'], button[value='cancel']")
                if cancel.count():
                    cancel.first.click()
                else:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(250)

        page.goto(base)
        page.wait_for_timeout(2500)

        # ---------------------------------------------------------------- Einrichtung
        cur["where"] = "einrichtung"
        if page.locator(T("onboarding")).count():
            for lang in ("en", "fr", "pl", "de"):
                page.select_option(T("onb-lang"), lang)
                page.wait_for_timeout(200)
            page.click(T("onb-next"))
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "00_einrichtung_geraet.png"))
            page.click(T("onb-next"))
            page.click(T("onb-mode-lab"))
            page.click(T("onb-next"))
            for cb in page.locator(".onb-legal input[type=checkbox]").all():
                cb.check()
            page.click(T("onb-next"))
            page.screenshot(path=str(OUT / "01_einrichtung_fertig.png"))
            page.click(T("onb-finish"))
            page.wait_for_timeout(1500)
            if page.locator(T("onboarding")).count():
                issue("onboarding", "Dialog nach Abschluss noch offen")
        ob = api(base, "/api/state").get("onboarding")
        print("Einrichtung:", ob, flush=True)

        # ---------------------------------------------------------------- Bereiche
        navs = page.locator("nav a[href^='#/'], .sidenav a[href^='#/']")
        hrefs = []
        for i in range(navs.count()):
            hr = navs.nth(i).get_attribute("href")
            if hr and hr not in hrefs:
                hrefs.append(hr)
        print("Bereiche:", hrefs, flush=True)
        for n, hr in enumerate(hrefs):
            pid = hr[2:]
            cur["where"] = pid
            page.goto(base + hr)
            page.wait_for_timeout(1200)
            sw = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if sw > 2:
                issue("layout", f"horizontales Scrollen um {sw}px")
            broken = page.evaluate("[...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.src).map(i => i.src)")
            for b in broken:
                issue("bild", f"nicht geladen: {b}")
            page.screenshot(path=str(OUT / f"{n + 2:02d}_{pid}.png"), full_page=True)
            # Bedienelemente im Inhalt (nicht Kopfzeile/Navigation): Knöpfe, Schalter, Auswahllisten, Tabs
            done = 0
            seen = set()
            for _round in range(3):
                els = page.locator("#main button:visible, #main [role=tab]:visible, #main input[type=checkbox]:visible, #main select:visible")
                cnt = els.count()
                for i in range(cnt):
                    if done >= a.max_per_page:
                        break
                    el = els.nth(i)
                    try:
                        tid = el.get_attribute("data-testid") or ""
                        txt = (el.inner_text(timeout=500) or "").strip()[:40] if el.evaluate("e => e.tagName") != "SELECT" else ""
                        key = tid or txt or f"#{i}"
                        if key in seen or SKIP.search(tid + " " + txt) or el.is_disabled():
                            continue
                        seen.add(key)
                        tag = el.evaluate("e => e.tagName + (e.type ? ':' + e.type : '')")
                        if tag.startswith("SELECT"):
                            opts = el.locator("option").all()
                            vals = [o.get_attribute("value") for o in opts if not o.get_attribute("disabled")]
                            old = el.input_value()
                            if len(vals) > 1:
                                el.select_option(vals[1] if vals[0] == old else vals[0])
                                page.wait_for_timeout(250)
                                if el.count() and el.is_visible():
                                    el.select_option(old)
                        elif tag.endswith("checkbox"):
                            el.click(force=True)
                            page.wait_for_timeout(250)
                            el.click(force=True)
                        else:
                            el.click(timeout=2000)
                        clicks.append({"page": pid, "el": key})
                        done += 1
                        page.wait_for_timeout(350)
                        close_dialogs()
                        toasts()
                        if not page.url.endswith(hr):
                            page.goto(base + hr)
                            page.wait_for_timeout(600)
                    except Exception as e:
                        if "Timeout" not in str(e) and "detached" not in str(e) and "not attached" not in str(e):
                            issue("klick", f"{key if 'key' in dir() else i}: {str(e).splitlines()[0]}")
            print(f"  {pid}: {done} Bedienelemente", flush=True)

        # ---------------------------------------------------------------- Kopfzeile + Regie „Auf Sendung“
        cur["where"] = "regie-onair"
        page.goto(base + "#/regie")
        page.wait_for_timeout(1000)
        for std in ("dab", "fm", "dvbt", "dvbc", "dvbs", "pal"):
            for _ in range(30):                      # nach Not-Aus erst den Zustand AUS abwarten
                if api(base, "/api/state")["tx"]["state"] in ("idle", "error"):
                    break
                page.wait_for_timeout(500)
            page.wait_for_timeout(800)
            b = page.locator(T(f"regie-onair-{std}"))
            if not b.count() or b.is_disabled():
                issue("tx", f"{std}: Knopf „Auf Sendung“ {'fehlt' if not b.count() else 'gesperrt'}")
                continue
            b.click()
            if page.locator(T("confirm-onair-ok")).count():
                page.click(T("confirm-onair-ok"))
            ok = False
            for _ in range(60):
                st = api(base, "/api/state")
                if st["tx"]["state"] == "running" and st["config"]["rf"].get("enabled"):
                    ok = True
                    break
                if st["tx"]["state"] == "error":
                    issue("tx", f"{std}: {st['tx'].get('error')}")
                    break
                page.wait_for_timeout(1000)
            toasts()
            page.screenshot(path=str(OUT / f"90_onair_{std}.png"))
            print(f"  Auf Sendung {std}: {'ON AIR' if ok else 'nicht erreicht'} (Dämpfung {st['pluto'].get('atten_db')} dB)", flush=True)
            if not ok:
                issue("tx", f"{std}: ON AIR nicht erreicht (Zustand {st['tx']['state']})")
            page.click(T("estop"))
            page.wait_for_timeout(1500)
        cur["where"] = "kopfzeile"
        for tid in ("onb-open", "theme-toggle"):
            if page.locator(T(tid)).count():
                page.click(T(tid))
                page.wait_for_timeout(400)
                if page.locator(T("onb-close")).count():
                    page.click(T("onb-close"))
        page.click(T("estop"))
        page.wait_for_timeout(1000)
        toasts()

        # ---------------------------------------------------------------- Aufräumen
        api(base, "/api/tx/stop", {})
        api(base, "/api/config", saved_cfg, "PUT")
        st = api(base, "/api/state")
        print("Ende: TX", st["tx"]["state"], "Pluto", st["pluto"].get("atten_db"), "dB", flush=True)
        br.close()
    rep = {"issues": issues, "clicks": len(clicks), "pages": hrefs}
    (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(clicks)} Bedienelemente geklickt, {len(issues)} Auffälligkeiten → {OUT / 'report.json'}")


if __name__ == "__main__":
    main()
