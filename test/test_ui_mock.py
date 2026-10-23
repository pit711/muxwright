"""E2E-Test des Dashboards gegen test/mock_server.py (Playwright, Chromium headless).

Aufruf:  python test/test_ui_mock.py            (startet den Mock selbst auf einem freien Port)
         python test/test_ui_mock.py --url http://127.0.0.1:8793/   (vorhandenen Server nutzen)
Screenshots: test/shots/<bereich>_<hell|dunkel>_<desktop|handy>.png
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

HERE = Path(__file__).resolve().parent
for _st in (sys.stdout, sys.stderr):
    try:
        _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
SHOTS = HERE / "shots"
PAGES = ["regie", "hf", "ensemble", "dienste", "medien", "durchsagen", "kapazitaet", "pruefen", "presets", "log"]
T = lambda tid: f"[data-testid='{tid}']"  # noqa: E731


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(base, path, data=None, method=None):
    req = urllib.request.Request(base.rstrip("/") + path, method=method or ("POST" if data is not None else "GET"),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


class Console:
    def __init__(self):
        self.errors: list[str] = []

    def attach(self, page: Page):
        if os.environ.get("DABLAB_DEBUG"):
            page.on("response", lambda r: print("   RESP", r.status, r.request.method, r.url.split("/", 3)[-1]) if "/api/playlists" in r.url or "/api/upload" in r.url else None)
            page.on("console", lambda m: print("   CONSOLE", m.type, m.text))
        page.on("console", lambda m: self.errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))


BASE = None


def step(name):
    extra = ""
    if BASE and os.environ.get("DABLAB_DEBUG"):
        try:
            rf = api(BASE, "/api/state")["config"]["rf"]
            extra = f"   [rf {rf['channel']} {rf['atten_db']} dB enabled={rf['enabled']}]"
        except Exception:  # noqa: BLE001
            pass
    print(f"  · {name}{extra}", flush=True)


def no_hscroll(page: Page, where: str):
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"horizontaler Überlauf auf {where}: {w}"


def goto(page: Page, pid: str):
    page.click(T(f"nav-{pid}"))
    expect(page.locator("#main")).to_have_attribute("data-page", pid)


def functional(page: Page, base: str):
    global BASE
    BASE = base
    page.goto(base)
    expect(page.locator(T("tx-state"))).to_have_text("AUS")
    expect(page.locator(T("conn"))).to_have_class("conn ok", timeout=5000)
    expect(page.locator(T("st-runner"))).to_contain_text("bereit")

    # ---------------------------------------------------------------- HF
    step("HF: Kanal, Dämpfung mit Sicherheitsabfrage")
    goto(page, "hf")
    page.select_option(T("rf-channel"), "5C")
    expect(page.locator(T("tx-status"))).to_contain_text("5C")
    expect(page.locator(T("tx-status"))).to_contain_text("178,352")
    assert api(base, "/api/state")["config"]["rf"]["channel"] == "5C"
    expect(page.locator(T("hdr-dbm"))).to_contain_text("−52,2")
    page.fill(T("rf-atten-input"), "30")
    page.press(T("rf-atten-input"), "Enter")
    expect(page.locator(T("confirm-atten"))).to_be_visible()
    page.click(T("confirm-atten-cancel"))
    time.sleep(0.3)
    assert api(base, "/api/state")["config"]["rf"]["atten_db"] == 60
    page.click(T("rf-atten-step--10"))
    expect(page.locator(T("hdr-atten"))).to_contain_text("50,00")
    assert api(base, "/api/state")["config"]["rf"]["atten_db"] == 50

    step("Start, Senden an → ON AIR")
    expect(page.locator(T("tx-start"))).to_have_count(0)                     # nur noch ein Schalter: „Senden“
    api(base, "/api/tx/start", {})                                         # Kette ohne HF (Zustand BEREIT)
    expect(page.locator(T("tx-state"))).to_have_text("BEREIT", timeout=8000)
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    expect(page.locator(T("st-rate"))).to_contain_text("MS/s", timeout=5000)

    step("Pegelrampe mit Live-Fortschritt, Schwelle markieren")
    for tid, v in (("sweep-from", "50"), ("sweep-to", "53"), ("sweep-step", "1"), ("sweep-dwell", "1")):
        page.fill(T(tid), v)
        page.locator(T(tid)).dispatch_event("change")
    page.click(T("sweep-start"))
    expect(page.locator(T("sweep-status"))).to_contain_text("Stufe", timeout=5000)
    expect(page.locator(T("sweep-status"))).to_contain_text("Beendet", timeout=15000)
    expect(page.locator(T("sweep-status"))).to_contain_text("53,00 dB")
    page.click(T("sweep-mark"))
    expect(page.locator(T("sweep-marks"))).to_contain_text("53,00 dB")

    # ---------------------------------------------------------------- Ensemble
    step("Ensemble: Label live, Kurzlabel-Auswahl, Hex-Validierung")
    goto(page, "ensemble")
    page.fill(T("ens-label"), "DABLAB Test")
    expect(page.locator(T("applybar"))).to_be_visible()
    expect(page.locator(T("apply"))).to_have_text("Live übernehmen")
    page.locator(f"{T('ens-shortchips')} button").nth(7).click()   # „T“ hinzu
    expect(page.locator(T("ens-shortlabel"))).to_have_value("DABLABT")
    page.fill(T("ens-eid"), "0xZZ")
    expect(page.locator(".field[data-path='ensemble.eid']")).to_have_class("field invalid")
    expect(page.locator(T("apply"))).to_be_disabled()
    page.fill(T("ens-eid"), "4fff")
    page.locator(T("ens-eid")).blur()
    expect(page.locator(T("ens-eid"))).to_have_value("0x4FFF")
    expect(page.locator(T("apply"))).to_be_enabled()
    page.fill(T("ens-label"), "DABLAB Ä€ Test 12")  # 17 Zeichen → Fehler
    expect(page.locator(T("apply"))).to_be_disabled()
    page.fill(T("ens-label"), "DABLAB Test")
    page.click(T("apply"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    assert api(base, "/api/state")["config"]["ensemble"]["shortlabel"] == "DABLABT"

    # ---------------------------------------------------------------- Dienste
    step("Dienste: hinzufügen, Codec/Bitrate/Schutz, Typ DAB")
    goto(page, "dienste")
    expect(page.locator(".svcitem")).to_have_count(3)
    page.click(T("svc-add"))
    expect(page.locator(".svcitem")).to_have_count(4)
    page.click(T("svc-tab-audio"))
    page.select_option(T("svc-codec"), "aac-lc")
    expect(page.locator(f"{T('svc-channels')} button")).to_have_count(2)
    opts = page.eval_on_selector_all(f"{T('svc-bitrate')} option", "o => o.map(x => +x.value)")
    assert opts[0] == 8 and opts[-1] == 192 and all(b % 8 == 0 for b in opts), opts
    page.select_option(T("svc-bitrate"), "96")
    prot = page.eval_on_selector_all(f"{T('svc-protection')} option", "o => o.map(x => x.value)")
    assert "EEP_B:1" in prot and not any(p.startswith("UEP") for p in prot), prot
    page.select_option(T("svc-protection"), "EEP_A:2")
    expect(page.locator(".cuinfo")).to_contain_text("96")     # 8 × 12 = 96 CU
    page.click(T("svc-tab-general"))
    page.click(T("svc-type-dab"))
    page.click(T("svc-tab-audio"))
    expect(page.locator(T("svc-codec"))).to_have_value("mp2")
    prot = page.eval_on_selector_all(f"{T('svc-protection')} option", "o => o.map(x => x.value)")
    assert "UEP:3" in prot and "UEP:5" in prot, prot
    expect(page.locator(T("svc-protection"))).to_have_value("UEP:3")
    page.click(T("svc-tab-source"))
    page.click(T("src-kind-lr_test"))
    expect(page.locator(T("src-left"))).to_be_visible()

    step("Dienste: DLS live senden, Slideshow-Thumbnails")
    page.click(T("svc-item-0"))
    page.click(T("svc-tab-pad"))
    page.fill(T("dls-text"), "Hallo vom E2E-Test äöü")
    page.click(T("dls-send"))
    expect(page.locator(".toast", has_text="DLS gesendet")).to_be_visible()
    assert api(base, "/api/state")["config"]["services"][0]["pad"]["dls"]["text"] == "Hallo vom E2E-Test äöü"
    page.click(T("svc-tab-sls"))
    expect(page.locator(f"{T('sls-thumbs')} img")).to_have_count(2)
    page.wait_for_function("() => [...document.querySelectorAll('.thumb img')].every(i => i.complete && i.naturalWidth > 0)")
    page.set_input_files(T("sls-upload-input"), files=[{"name": "e2e slide.png", "mimeType": "image/png",
                                                        "buffer": (Path(__import__('tempfile').gettempdir()) / "dablab_mock_media" / "slide3.png").read_bytes()}])
    expect(page.locator(f"{T('sls-thumbs')} img")).to_have_count(3)

    step("Playlist-Quelle und Jetzt-läuft-Anzeige")
    page.click(T("svc-item-0"))                      # srv1 hat im Mock die Playlist „Demo“
    page.click(T("svc-tab-source"))
    expect(page.locator(T("src-kind-playlist"))).to_have_class("on")
    expect(page.locator(T("src-playlist"))).to_have_value("Demo")
    expect(page.locator(T("np-title"))).not_to_have_text(re.compile(r"^(Kein Titel gemeldet|Wiedergabe gestoppt)$"), timeout=5000)
    expect(page.locator(T("hdr-np"))).to_be_visible()
    expect(page.locator(T("hdr-np-title"))).not_to_be_empty()
    page.click(T("np-next"))
    expect(page.locator(".toast", has_text="Nächster Titel")).to_be_visible()
    page.click(T("np-prev"))
    expect(page.locator(".toast", has_text="Vorheriger Titel")).to_be_visible()
    page.click(T("svc-item-1"))                      # Testton → Playlist „Sprache“
    page.click(T("svc-tab-source"))
    page.click(T("src-kind-playlist"))
    expect(page.locator(T("apply"))).to_be_disabled()   # Playlist fehlt → Validierung
    page.select_option(T("src-playlist"), "Sprache")
    for tid in ("src-shuffle", "src-loop", "src-normalize", "src-dls-tags", "src-crossfade"):
        expect(page.locator(T(tid))).to_be_visible()
    page.locator(T("src-shuffle")).check(force=True)
    page.fill(T("src-crossfade"), "2")
    expect(page.locator(T("apply"))).to_be_enabled()
    page.click(T("svc-item-3"))

    step("Übernehmen (Neustart) bei Strukturänderung")
    expect(page.locator(T("apply"))).to_have_text("Übernehmen (Neustart)")
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    st = api(base, "/api/state")
    assert len(st["config"]["services"]) == 4 and st["config"]["services"][3]["audio"]["codec"] == "mp2"
    s1 = st["config"]["services"][1]["audio"]["source"]
    assert s1["kind"] == "playlist" and s1["playlist"] == "Sprache" and s1["shuffle"] is True and s1["crossfade_s"] == 2
    expect(page.locator(T("tx-state"))).to_have_text("BEREIT", timeout=8000)   # Neustart → stumm
    assert st["config"]["rf"]["atten_db"] == 53, st["config"]["rf"]            # Rampenendwert (53 dB) überlebt das Übernehmen

    step("Live-Dämpfung während Bearbeitung → Übernehmen (Neustart) → Dämpfung bleibt")
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    goto(page, "ensemble")
    page.fill(T("ens-label"), "DABLAB Tx offen")                                # Entwurf „dirty“ (Kurzlabel DABLABT bleibt gültig)
    goto(page, "hf")
    page.fill(T("rf-atten-input"), "45")
    page.press(T("rf-atten-input"), "Enter")
    expect(page.locator(T("hdr-atten"))).to_contain_text("45,00")
    assert api(base, "/api/state")["config"]["rf"]["atten_db"] == 45
    goto(page, "dienste")
    page.click(T("svc-item-0"))
    page.click(T("svc-tab-audio"))
    page.select_option(T("svc-bitrate"), "96")                                  # Strukturänderung → Neustart
    expect(page.locator(T("apply"))).to_have_text("Übernehmen (Neustart)")
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    st = api(base, "/api/state")
    assert st["config"]["rf"]["atten_db"] == 45, st["config"]["rf"]
    assert st["config"]["ensemble"]["label"] == "DABLAB Tx offen" and st["config"]["services"][0]["audio"]["bitrate"] == 96
    expect(page.locator(T("hdr-atten"))).to_contain_text("45,00")

    step("Kopfzeile: Senden an, aber Pluto stumm (≥ 89 dB)")
    expect(page.locator(T("tx-state"))).to_have_text("BEREIT", timeout=8000)
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    api(base, "/api/rf", {"atten_db": 89.75})
    expect(page.locator(T("tx-state"))).to_have_text("AN, STUMM", timeout=5000)
    expect(page.locator(T("tx-status"))).to_have_class("txstatus muted")
    api(base, "/api/rf", {"atten_db": 50})
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    expect(page.locator(T("hdr-atten"))).to_contain_text("50,00")

    # ---------------------------------------------------------------- Medien & Playlists
    step("Medien: Upload, Playlist anlegen, sortieren, Export/Import, löschen")
    for fn in ("e2e_a.mp3", "e2e_b.ogg"):          # Reste früherer Läufe (Medienordner ist persistent)
        try:
            api(base, f"/api/media/{fn}", method="DELETE")
        except Exception:  # noqa: BLE001
            pass
    for pn in ("E2E Liste", "E2E Umbenannt"):
        try:
            api(base, f"/api/playlists/{pn.replace(' ', '%20')}", method="DELETE")
        except Exception:  # noqa: BLE001
            pass
    goto(page, "medien")
    names = lambda: page.eval_on_selector_all(f"{T('media-list')} tbody tr td:first-child b", "els => els.map(e => e.textContent)")  # noqa: E731
    n0 = len(names())
    assert n0 >= 6, names()
    page.set_input_files(T("media-upload-input"), files=[
        {"name": "e2e_a.mp3", "mimeType": "audio/mpeg", "buffer": b"ID3" + b"\0" * 40000},
        {"name": "e2e_b.ogg", "mimeType": "audio/ogg", "buffer": b"OggS" + b"\0" * 20000}])
    expect(page.locator(f"{T('media-list')} tbody tr")).to_have_count(n0 + 2)
    expect(page.locator(T("media-list"))).to_contain_text("e2e_a.mp3")
    expect(page.locator(T("pl-add-0"))).to_be_disabled()          # keine Playlist gewählt
    page.click(T("pl-new"))
    page.fill(T("pl-name-input"), "E2E Liste")
    page.click(T("pl-name-ok"))
    expect(page.locator(T("pl-select"))).to_have_value("E2E Liste")
    expect(page.locator(T("pl-add-0"))).to_be_enabled()
    first, second = names()[:2]
    page.click(T("pl-add-0"))
    page.click(T("pl-add-1"))
    page.click(T("pl-add-2"))
    expect(page.locator(f"{T('pl-items')} li")).to_have_count(3)
    expect(page.locator(T("pl-item-0"))).to_contain_text(first)
    page.click(T("pl-down-0"))                                       # Sortierung: 1 ↔ 2
    expect(page.locator(T("pl-item-1"))).to_contain_text(first)
    expect(page.locator(T("pl-item-0"))).to_contain_text(second)
    page.click(T("pl-remove-2"))
    expect(page.locator(f"{T('pl-items')} li")).to_have_count(2)
    page.click(T("pl-save"))
    expect(page.locator(".toast", has_text="gespeichert")).to_be_visible()
    saved = api(base, "/api/playlists/E2E%20Liste")
    assert [i["file"].split("/")[-1] for i in saved["items"]] == [second, first], saved
    with page.expect_download() as dl:
        page.click(T("pl-export"))
    m3u = Path(dl.value.path()).read_text(encoding="utf-8")
    assert m3u.startswith("#EXTM3U") and second in m3u and first in m3u, m3u
    page.set_input_files(T("pl-import-input"), files=[{"name": "import.m3u8", "mimeType": "audio/mpegurl",
        "buffer": "#EXTM3U\n#EXTINF:214,DAB-Lab Band - Laborsong\nsong.mp3\n#EXTINF:7,Jingle\njingle.ogg\n#EXTINF:38,Ansage\nsprache.wav\n".encode()}])
    expect(page.locator(".toast", has_text="importiert")).to_be_visible()
    expect(page.locator(f"{T('pl-items')} li")).to_have_count(3)
    expect(page.locator(T("pl-item-0"))).to_contain_text("Laborsong")
    page.click(T("pl-rename"))
    page.fill(T("pl-name-input"), "E2E Umbenannt")
    page.click(T("pl-name-ok"))
    expect(page.locator(".toast", has_text="Umbenannt")).to_be_visible()
    expect(page.locator(T("pl-select"))).to_have_value("E2E Umbenannt")
    assert "E2E Liste" not in [p["name"] for p in api(base, "/api/playlists")["playlists"]]
    page.click(T("pl-del"))
    page.click(T("confirm-pl-del-ok"))
    expect(page.locator(T("pl-select"))).to_have_value("")
    for fn in ("e2e_a.mp3", "e2e_b.ogg"):
        page.click(T(f"media-del-{names().index(fn)}"))
        page.click(T("confirm-media-del-ok"))
        expect(page.locator(T("media-list"))).not_to_contain_text(fn)
    expect(page.locator(f"{T('media-list')} tbody tr")).to_have_count(n0)
    expect(page.locator(T("np-srv1-title"))).to_be_visible()        # Jetzt-läuft-Karte des Playlist-Dienstes
    goto(page, "dienste")

    # ---------------------------------------------------------------- Kapazität
    step("Kapazität: Überbuchung blockiert")
    page.click(T("svc-item-1"))
    page.click(T("svc-tab-audio"))
    page.select_option(T("svc-bitrate"), "192")
    page.select_option(T("svc-protection"), "EEP_A:1")
    page.click(T("svc-dup"))
    page.click(T("svc-dup"))
    goto(page, "kapazitaet")
    expect(page.locator(T("cap-over"))).to_be_visible()
    expect(page.locator(T("apply"))).to_be_disabled()
    expect(page.locator(T("nav-kapazitaet"))).to_contain_text("1")
    page.click(T("apply-discard"))
    expect(page.locator(T("cap-over"))).to_have_count(0)

    # ---------------------------------------------------------------- Durchsagen
    step("Durchsagen: Live-Schalter, Alarm-Cluster")
    goto(page, "durchsagen")
    page.locator(f"{T('cluster-1')} .bigswitch").click()                      # Label klicken (Checkbox ist verborgen)
    expect(page.locator(T("cluster-1"))).to_have_class("anncard active")
    expect(page.locator(T("ann-switch-1"))).to_be_checked()
    assert api(base, "/api/state")["config"]["clusters"][0]["active"] is True
    page.locator(f"{T('cluster-1')} .bigswitch").click()
    expect(page.locator(T("cluster-1"))).to_have_class("anncard")
    assert api(base, "/api/state")["config"]["clusters"][0]["active"] is False
    page.click(T("cluster-add-alarm"))
    expect(page.locator(T("cluster-255"))).to_be_visible()
    expect(page.locator(T("ann-switch-255"))).to_be_disabled()
    page.click(T("apply-discard"))

    # ---------------------------------------------------------------- Prüfen
    step("Prüfen: Offline-Prüfung und Kontrollempfang mit Soll/Ist")
    goto(page, "pruefen")
    page.click(T("check-run"))
    expect(page.locator(T("report-check-verdict"))).to_contain_text("Abweichung", timeout=10000)
    expect(page.locator(f"{T('report-check')} tr.bad")).to_have_count(1)
    expect(page.locator(f"{T('report-check-svc-srv2')} tr.bad")).to_contain_text("Kurzlabel")
    page.wait_for_function("() => [...document.querySelectorAll('.slides img')].length > 0 && [...document.querySelectorAll('.slides img')].every(i => i.complete && i.naturalWidth > 0)")
    page.fill(T("monitor-gain"), "70")                                          # hoher RX-Gain → Übersteuerung im Mock
    page.locator(T("monitor-gain")).dispatch_event("change")
    page.click(T("monitor-run"))
    expect(page.locator(T("report-monitor"))).to_be_visible(timeout=10000)
    expect(page.locator(T("report-monitor-rx"))).to_contain_text("proc:usb")          # HF-Mitschnitt-Karte (äußerer Bericht)
    expect(page.locator(f"{T('report-monitor-spectrum')} svg path.sp-line")).to_have_count(1)
    expect(page.locator(T("report-monitor-clip"))).to_contain_text("RX-Gain senken")
    expect(page.locator(T("report-monitor-verdict"))).not_to_contain_text("Kein Sync")  # Dekodierung aus `decode` gelesen
    expect(page.locator(f"{T('report-monitor-svc-srv1')} table.cmp")).to_be_visible()
    expect(page.locator(f"{T('report-monitor')} .err.bad").first).to_be_visible()
    mon = api(base, "/api/state")["monitor"]
    assert "decode" in mon and mon["decode"]["sync"] is True and "spectrum" in mon
    page.fill(T("monitor-gain"), "30")
    page.locator(T("monitor-gain")).dispatch_event("change")

    # ---------------------------------------------------------------- Presets & Testpläne
    step("Presets speichern/laden, Testplan, Protokoll")
    goto(page, "presets")
    page.fill(T("preset-name"), "E2E Test")
    page.click(T("preset-save"))
    expect(page.locator(T("preset-row-E2E Test"))).to_be_visible()
    page.select_option(T("preset-select"), "Standard")
    page.click(T("preset-load"))
    expect(page.locator(".toast", has_text="geladen")).to_be_visible()
    # Preset bringt Kanal 6C mit (Server steht auf 5C): wird beim Übernehmen live gesetzt, Dämpfung bleibt unangetastet
    att_before = api(base, "/api/state")["config"]["rf"]["atten_db"]
    expect(page.locator(T("rf-channel-read") if False else T("applybar"))).to_be_visible()
    page.click(T("apply"))
    if page.locator(T("confirm-restart-ok")).count():
        page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    expect(page.locator(T("tx-status"))).to_contain_text("6C")
    st = api(base, "/api/state")
    assert st["config"]["rf"]["channel"] == "6C" and st["config"]["rf"]["freq_hz"] == 185360000, st["config"]["rf"]
    assert st["config"]["rf"]["atten_db"] == att_before, (st["config"]["rf"], att_before)
    page.click(T("plan-codec"))
    page.click(T("step-load-0"))
    expect(page.locator(T("nav-dienste"))).to_contain_text("10")
    page.click(T("step-pass-0"))
    expect(page.locator(T("step-0"))).to_have_class("step pass")
    with page.expect_download() as dl:
        page.click(T("protocol-json"))
    data = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
    assert data["testplan"]["results"][0]["result"] == "pass" and data["check"]["sync"] is True
    with page.context.expect_page() as pop:
        page.click(T("protocol-html"))
    proto = pop.value
    proto.wait_for_load_state()
    expect(proto.locator("h1")).to_contain_text("Messprotokoll PlutoSDR-Regie")
    expect(proto.locator("tr.bad").first).to_be_visible()
    proto.screenshot(path=str(SHOTS / "protokoll_druckansicht.png"), full_page=True)
    proto.close()
    page.click(T("plan-close"))
    page.click(T("apply-discard"))

    # ---------------------------------------------------------------- Standards UKW / DVB-T / DVB-C
    step("UKW: Umschalten, Sender/RDS, Übernehmen (Neustart), RDS live, Prüfen")
    goto(page, "hf")
    page.click(T("std-fm"))
    expect(page.locator(T("nav-sender"))).to_be_visible()
    expect(page.locator(T("nav-rds"))).to_be_visible()
    expect(page.locator(T("nav-dienste"))).to_have_count(0)
    expect(page.locator(T("apply"))).to_have_text("Übernehmen (Neustart)")
    page.fill(T("fm-freq"), "101.2")
    page.press(T("fm-freq"), "Enter")
    # Server fährt noch DAB+: die Frequenz wird nur vorgemerkt und geht mit dem Standardwechsel an den Server
    expect(page.locator(T("fm-freq"))).to_have_value("101.2")
    assert api(base, "/api/state")["config"]["rf"]["channel"] != "fm"
    goto(page, "sender")
    page.fill(T("fm-deviation"), "60")
    expect(page.locator(T("fm-audio-dev"))).to_contain_text("51,25")             # 60 − 6,75 − 2,0 (SPEC: Audiohub)
    page.click(T("fm-second-enable") + " + .track")
    expect(page.locator(T("src2-kind"))).to_be_visible()                          # Quelle des zweiten Trägers
    goto(page, "rds")
    page.fill(T("rds-ps"), "TEST-PS!")
    page.fill(T("rds-rt"), "Radiotext aus dem E2E-Test")
    page.fill(T("rds-af-input"), "104.4")
    page.press(T("rds-af-input"), "Enter")
    expect(page.locator(f"{T('rds-af-list')} .chip")).to_have_count(3)           # 2 Vorgaben (Mock) + 1
    page.click(T("rds-eon-add"))
    expect(page.locator(T("eon-pi-0"))).to_be_visible()
    page.fill(T("rds-pi"), "0xZZ")
    expect(page.locator(T("apply"))).to_be_disabled()
    page.fill(T("rds-pi"), "0xD3C1")
    expect(page.locator(T("apply"))).to_be_enabled()
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    st = api(base, "/api/state")["config"]
    assert st["mode"] == "fm", st["mode"]
    assert st["fm"]["deviation_khz"] == 60, st["fm"]["deviation_khz"]
    assert st["fm"]["rds"]["ps"] == "TEST-PS!", st["fm"]["rds"]["ps"]
    assert st["fm"]["second_carrier"]["enable"] is True, st["fm"]["second_carrier"]
    assert st["fm"]["freq_mhz"] == 101.2 and st["rf"]["freq_hz"] == 101200000, (st["fm"]["freq_mhz"], st["rf"]["freq_hz"])
    assert 104.4 in st["fm"]["rds"]["af"], st["fm"]["rds"]["af"]
    assert len(st["fm"]["rds"]["eon"]) == 1, st["fm"]["rds"]["eon"]
    expect(page.locator(T("hdr-std"))).to_have_text("UKW")
    expect(page.locator(T("hdr-std-info"))).to_contain_text("±60 kHz")
    expect(page.locator(T("tx-state"))).to_have_text("BEREIT", timeout=8000)
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    page.fill(T("rds-ps"), "LIVE-PS")
    page.click(T("rds-ps-send"))
    expect(page.locator(".toast", has_text="PS senden gesendet")).to_be_visible()
    assert api(base, "/api/state")["config"]["fm"]["rds"]["ps"] == "LIVE-PS", api(base, "/api/state")["config"]["fm"]["rds"]["ps"]
    page.click(T("rds-ta-live"))
    expect(page.locator(".toast", has_text="TA ein")).to_be_visible()
    assert api(base, "/api/state")["config"]["fm"]["rds"]["ta"] is True
    expect(page.locator(T("rds-ta-live"))).to_contain_text("beenden")
    goto(page, "pruefen")
    page.click(T("check-run"))
    expect(page.locator(T("report-check"))).to_be_visible(timeout=10000)
    expect(page.locator(T("report-check-mpx"))).to_contain_text("Spitzenhub")
    expect(page.locator(T("report-check-measured"))).to_contain_text("MPX-Leistung")
    expect(page.locator(T("report-check-rds"))).to_contain_text("LIVE-PS")
    expect(page.locator(f"{T('report-check-rds')} tr.bad")).to_have_count(0)      # 7 Zeichen → Mock meldet keine Abweichung
    page.click(T("monitor-run"))
    expect(page.locator(T("report-monitor-rx"))).to_be_visible(timeout=10000)
    expect(page.locator(T("report-monitor-rds"))).to_be_visible()
    goto(page, "presets")
    expect(page.locator(T("plan-fm_rds"))).to_be_visible()
    expect(page.locator(T("plan-codec"))).to_have_count(0)                       # DAB-Pläne ausgeblendet

    step("DVB-T: Programme, YouTube-Download, Modulation, TS-Budget, Übernehmen, Prüfen")
    page.click(T("std-dvbt"))
    expect(page.locator(T("nav-programme"))).to_be_visible()
    goto(page, "programme")
    n0 = page.locator(".svcitem").count()
    page.click(T("prg-add"))
    expect(page.locator(".svcitem")).to_have_count(n0 + 1)
    page.click(T("prg-tab-video"))
    page.click(T("vid-kind-youtube"))
    expect(page.locator(T("apply"))).to_be_disabled()                            # Video noch nicht geladen
    page.fill(T("vid-yt-url"), "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    page.click(T("vid-yt-start"))
    expect(page.locator(T("vid-yt-status"))).to_contain_text("geladen", timeout=15000)
    expect(page.locator(T("vid-yt-status"))).to_contain_text("media/video/")
    expect(page.locator(T("apply"))).to_be_enabled()
    page.click(T("vid-codec-h264"))
    page.select_option(T("vid-res"), "hd")
    page.click(T("prg-tab-eit"))
    page.fill(T("eit-title"), "E2E-Sendung")
    goto(page, "modulation")
    rate_before = page.inner_text(T("dvb-netrate"))
    page.click(T("dvbt-constellation-16-QAM"))
    expect(page.locator(T("dvb-netrate"))).not_to_have_text(rate_before)
    expect(page.locator(T("dvb-netrate"))).to_contain_text("16,086")             # 8 MHz, 16-QAM 2/3, GI 1/32
    page.click(T("dvbt-bandwidth-6"))
    expect(page.locator(T("dvb-sps"))).to_contain_text("6,857")
    goto(page, "programme")
    expect(page.locator(T("ts-over"))).to_be_visible()                          # 4 Programme passen nicht in 12 Mbit/s
    expect(page.locator(T("apply"))).to_be_disabled()
    goto(page, "modulation")
    page.click(T("dvbt-bandwidth-8"))
    goto(page, "programme")
    page.click(T(f"prg-item-{n0}"))
    page.click(T("prg-tab-video"))
    page.fill(T("vid-bitrate"), "4000")
    expect(page.locator(T("ts-over"))).to_have_count(0)
    expect(page.locator(T("apply"))).to_be_enabled()
    goto(page, "hf")
    expect(page.locator(T("rf-path-required"))).to_contain_text("Modulator")    # 8 MHz → 9,14 MS/s → Pluto
    page.select_option(T("dvb-channel"), "K30")
    expect(page.locator(T("rf-channel-read"))).to_have_text("K30")              # vorgemerkt, geht mit dem Wechsel zu DVB-T
    goto(page, "si")
    page.fill(T("si-network-name"), "E2E Netz")
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    st = api(base, "/api/state")
    c = st["config"]
    assert c["mode"] == "dvbt" and c["dvbt"]["constellation"] == "16-QAM" and c["dvbt"]["bandwidth_mhz"] == 8 and c["dvb"]["si"]["network_name"] == "E2E Netz", (c["mode"], c["dvbt"], c["dvb"]["si"])
    assert len(c["dvb"]["programs"]) == n0 + 1 and c["dvb"]["programs"][-1]["video"]["source"]["kind"] == "youtube" and c["dvb"]["programs"][-1]["video"]["source"]["file"].startswith("media/video/")
    assert st["rf"]["path"] == "device" and st["rf"]["path_label"] == "pluto", st["rf"]   # Server-Format: usb|device + path_label
    goto(page, "hf")
    expect(page.locator(T("rf-path-actual"))).to_contain_text("Modulator", timeout=5000)
    expect(page.locator(T("tx-state"))).to_have_text("BEREIT", timeout=8000)
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    goto(page, "pruefen")
    page.click(T("check-run"))
    expect(page.locator(T("report-check"))).to_be_visible(timeout=10000)
    expect(page.locator(T("report-check-mod"))).to_contain_text("16-QAM")
    expect(page.locator(T("report-check-si"))).to_contain_text("E2E Netz")
    expect(page.locator(T("report-check-prg-prg1"))).to_be_visible()
    expect(page.locator(f"{T('report-check')} tr.bad")).to_have_count(1)        # Mock: EIT-Titel des 2. Programms weicht ab
    goto(page, "presets")
    expect(page.locator(T("plan-dvbt_const"))).to_be_visible()

    step("DVB-C: Symbolrate, Ausgabeweg Pluto, Übernehmen")
    page.click(T("std-dvbc"))
    goto(page, "modulation")
    expect(page.locator(T("dvb-sps"))).to_contain_text("13,800")
    page.click(T("dvbc-sr-3450000"))
    expect(page.locator(T("dvb-sps"))).to_contain_text("6,900")
    page.click(T("dvbc-constellation-64-QAM"))
    expect(page.locator(T("dvb-netrate"))).to_contain_text("19,076")            # 3,45 MBd · 6 · 188/204
    goto(page, "hf")
    expect(page.locator(T("rf-channel-read"))).to_have_text("S21")
    expect(page.locator(T("rf-path-required"))).to_contain_text("USB")
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    c = api(base, "/api/state")["config"]
    assert c["mode"] == "dvbc" and c["dvbc"]["symbol_rate"] == 3450000 and c["dvbc"]["constellation"] == "64-QAM" and c["rf"]["channel"] == "S21", (c["mode"], c["dvbc"], c["rf"])
    expect(page.locator(T("hdr-std-info"))).to_contain_text("3,450 MBd 64-QAM")

    step("PAL: Kanal K36, Testbild/Text, Ton, Übernehmen, Text live, Prüfen mit Bild")
    page.click(T("std-pal"))
    expect(page.locator(T("nav-pal"))).to_be_visible()
    goto(page, "hf")
    page.select_option(T("dvb-channel"), "K36")
    expect(page.locator(T("pal-carriers"))).to_contain_text("591,250")
    expect(page.locator(T("pal-carriers"))).to_contain_text("596,750")         # Ton +5,5 MHz (B/G)
    expect(page.locator(T("rf-path-required"))).to_contain_text("Modulator")
    goto(page, "pal")
    page.fill(T("pal-text-top"), "E2E PAL")
    expect(page.locator(f"{T('pal-preview')} text").first).to_have_text("E2E PAL")
    page.select_option(T("pal-pattern"), "grey")
    page.click(T("pal-kind-video"))
    expect(page.locator(T("pal-loop-hint"))).to_contain_text("160 ms")
    expect(page.locator(T("apply"))).to_be_disabled()                           # Videodatei fehlt
    page.click(T("pal-kind-testpattern"))
    page.click(T("pal-live-true"))
    expect(page.locator(T("pal-live-hint"))).to_contain_text("7,000 MS/s")
    page.click(T("pal-live-false"))
    page.fill(T("pal-audio-dev"), "50")
    page.click(T("pal-system-DK"))
    expect(page.locator(T("pal-system-carriers"))).to_contain_text("597,750")  # D/K: +6,5 MHz
    page.click(T("pal-system-BG"))
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    c = api(base, "/api/state")["config"]
    assert c["mode"] == "pal" and c["rf"]["channel"] == "K36" and c["rf"]["freq_hz"] == 591250000, (c["mode"], c["rf"])
    assert c["pal"]["image"]["text"] == "E2E PAL" and c["pal"]["image"]["pattern"] == "grey" and c["pal"]["sound"]["deviation_hz"] == 50000, c["pal"]
    expect(page.locator(T("hdr-std"))).to_have_text("PAL")
    expect(page.locator(T("tx-state"))).to_have_text("BEREIT", timeout=8000)
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=5000)
    page.fill(T("pal-text-top"), "LIVE TEXT")
    page.click(T("pal-text-send"))
    expect(page.locator(".toast", has_text="Bildtext gesendet")).to_be_visible()
    assert api(base, "/api/state")["config"]["pal"]["image"]["text"] == "LIVE TEXT"
    goto(page, "pruefen")
    page.click(T("check-run"))
    expect(page.locator(T("report-check-pal"))).to_contain_text("Bildträger", timeout=10000)
    page.wait_for_function("() => { const i = document.querySelector(\"[data-testid='report-check-image'] img\"); return i && i.complete && i.naturalWidth > 0; }")
    expect(page.locator(f"{T('report-check-pal')} tr.bad")).to_have_count(0)
    goto(page, "presets")
    page.click(T("plan-field_lg_oled"))
    page.click(T("step-load-0"))
    expect(page.locator(T("std-dvbt"))).to_have_class(re.compile(r"\bon\b"))    # Schritt wechselt den Standard
    goto(page, "hf")
    expect(page.locator(T("rf-channel-read"))).to_have_text("K36")
    goto(page, "presets")
    page.click(T("step-load-2"))
    expect(page.locator(T("std-pal"))).to_have_class(re.compile(r"\bon\b"))
    page.click(T("plan-close"))
    page.click(T("apply-discard"))

    step("DVB-S: Warnhinweis, Transponder/LOF → Sat-ZF, DVB-S2-Parameter, Feldtest-Schritt, Übernehmen, Prüfen")
    page.click(T("std-dvbs"))
    expect(page.locator(".toast", has_text="13/18 V")).to_be_visible()
    goto(page, "modulation")
    expect(page.locator(T("dvbs-lnb-warning"))).to_contain_text("DC-Block")
    expect(page.locator(T("dvbs-if"))).to_contain_text("1.550")
    page.fill(T("dvbs-transponder"), "12000")
    expect(page.locator(T("dvbs-if"))).to_contain_text("2.250")
    expect(page.locator(T("apply"))).to_be_disabled()                           # ZF > 2150 MHz
    page.select_option(T("dvbs-lof-preset"), "10600")
    expect(page.locator(T("dvbs-if"))).to_contain_text("1.400")
    expect(page.locator(T("dvbs-22khz"))).to_be_checked()
    page.click(T("dvbs-system-dvbs2"))
    page.click(T("dvbs-modulation-8PSK"))
    expect(page.locator(T("dvbs-rolloff"))).to_be_visible()
    goto(page, "hf")
    expect(page.locator(T("dvbs-lnb-warning"))).to_be_visible()
    goto(page, "presets")
    page.click(T("plan-field_lg_oled"))
    page.click(T("step-load-3"))
    page.click(T("plan-close"))
    goto(page, "modulation")
    expect(page.locator(T("dvbs-if"))).to_contain_text("1.288")
    expect(page.locator(T("dvbs-transponder-read"))).to_contain_text("11.038 MHz V")
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    st = api(base, "/api/state")
    c = st["config"]
    assert c["mode"] == "dvbs" and c["rf"]["freq_hz"] == 1288000000 and c["dvbs"]["symbol_rate"] == 22000000 and c["dvbs"]["fec"] == "7/8" and c["dvbs"]["polarization"] == "V", (c["mode"], c["rf"], c["dvbs"])
    assert c["dvb"]["si"]["onid"] == "0xFF03" and [p["name"] for p in c["dvb"]["programs"]] == ["DABLAB Sat HD", "DABLAB Sat UHD", "DABLAB Sat HDR10"] and c["dvb"]["programs"][2]["video"]["hdr"] == "hdr10" and st["rf"]["path_label"] == "pluto"
    expect(page.locator(T("hdr-std"))).to_have_text("DVB-S")
    goto(page, "pruefen")
    page.click(T("check-run"))
    expect(page.locator(T("report-check-mod"))).to_contain_text("11038 MHz", timeout=10000)
    expect(page.locator(f"{T('report-check-mod')} tr.bad")).to_have_count(0)
    goto(page, "presets")
    page.click(T("plan-field_lg_oled"))
    page.click(T("step-load-0"))
    goto(page, "modulation")
    expect(page.locator(T("dvbt-constellation-16-QAM"))).to_have_class(re.compile(r"\bon\b"))
    expect(page.locator(T("dvbt-guard-1/4"))).to_have_class(re.compile(r"\bon\b"))
    goto(page, "si")
    expect(page.locator(T("si-onid"))).to_have_value("0xFF01")
    goto(page, "presets")
    page.click(T("plan-close"))
    page.click(T("apply-discard"))

    step("DVB-T: Encoder GPU/NVENC, HEVC UHD HDR10, Capture-/Bildschirm-Quelle, YouTube-Cookies")
    page.click(T("std-dvbt"))
    goto(page, "programme")
    page.click(T("prg-tab-video"))
    page.click(T("vid-hdr-hdr10"))
    expect(page.locator(T("vid-codec-hevc"))).to_have_class(re.compile(r"\bon\b"))      # HDR erzwingt HEVC
    page.select_option(T("vid-res"), "uhd")
    page.click(T("vid-encoder-nvenc"))
    expect(page.locator(".field[data-path$='video.encoder'] .msg")).to_contain_text("RTX 3060")
    page.click(T("vid-kind-capture"))
    expect(page.locator(T("apply"))).to_be_disabled()                            # Gerät fehlt
    page.select_option(T("vid-capture-device"), "USB3.0 Capture (Elgato Cam Link 4K)")
    page.click(T("vid-kind-screen"))
    expect(page.locator(T("vid-screen-region"))).to_be_visible()
    page.click(T("vid-kind-youtube"))
    expect(page.locator(T("vid-yt-cookie-hint"))).to_contain_text("Cookies")
    page.select_option(T("vid-yt-cookies"), "firefox")
    page.click(T("vid-codec-mpeg2"))
    expect(page.locator(T("vid-hdr-sdr"))).to_have_class(re.compile(r"\bon\b"))       # MPEG-2 → SDR, CPU
    page.click(T("apply-discard"))

    step("Regie: Kacheln, Auf Sendung (Standardwechsel per Klick), Stumm, Not-Aus")
    page.click(T("std-dab"))
    page.click(T("apply"))
    page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden()
    goto(page, "regie")
    expect(page.locator(".tile")).to_have_count(6)
    expect(page.locator(T("regie-tile-dab"))).to_have_class(re.compile(r"\bactive\b"))
    page.click(T("regie-onair-fm"))
    page.click(T("confirm-onair-ok"))
    page.wait_for_timeout(1500)
    if os.environ.get("DABLAB_DEBUG"):
        print("   TOASTS:", page.locator(".toast").all_inner_texts(), "PAGE:", page.get_attribute("#main", "data-page"))
    expect(page.locator(T("regie-status-fm"))).to_have_text("ON AIR", timeout=15000)
    c = api(base, "/api/state")
    assert c["config"]["mode"] == "fm" and c["config"]["rf"]["enabled"] is True and c["tx"]["state"] == "running", (c["config"]["mode"], c["config"]["rf"], c["tx"]["state"])
    expect(page.locator(T("hdr-std"))).to_have_text("UKW")
    page.click(T("regie-mute-fm"))
    expect(page.locator(T("regie-status-fm"))).to_have_text("STUMM", timeout=5000)
    page.click(T("regie-onair-fm"))
    expect(page.locator(T("regie-status-fm"))).to_have_text("ON AIR", timeout=8000)
    page.click(T("regie-edit-dvbs"))
    expect(page.locator("#main")).to_have_attribute("data-page", "modulation")
    expect(page.locator(T("dvbs-lnb-warning"))).to_be_visible()
    page.click(T("apply-discard"))
    goto(page, "regie")
    expect(page.locator(T("regie-estop"))).to_have_count(0)        # nur ein Not-Aus (Kopfzeile)
    page.click(T("estop"))
    expect(page.locator(T("tx-state"))).to_have_text("AUS", timeout=5000)
    page.click(T("regie-onair-dab"))
    page.click(T("confirm-onair-ok"))
    expect(page.locator(T("regie-status-dab"))).to_have_text("ON AIR", timeout=15000)
    page.locator(T("rf-enable")).uncheck(force=True)

    step("Feldtest DVB-S (11038 V, 22000 kS/s, 7/8 drei Dienste bzw. 1/2) laden")
    goto(page, "presets")
    page.click(T("plan-field_lg_oled"))
    page.click(T("step-load-3"))
    goto(page, "modulation")
    expect(page.locator(T("dvbs-if"))).to_contain_text("1.288")
    goto(page, "programme")
    expect(page.locator(".svcitem")).to_have_count(3)
    expect(page.locator(T("ts-over"))).to_have_count(0)
    goto(page, "presets")
    page.click(T("step-load-4"))
    goto(page, "programme")
    expect(page.locator(".svcitem")).to_have_count(1)
    goto(page, "presets")
    page.click(T("plan-close"))
    page.click(T("apply-discard"))

    step("Zurück zu DAB+ (über die Regie bereits aktiv)")
    expect(page.locator(T("nav-dienste"))).to_be_visible()
    c = api(base, "/api/state")["config"]
    assert c["mode"] == 1 and c["rf"]["channel"] == "6C", (c["mode"], c["rf"])
    expect(page.locator(T("hdr-std"))).to_have_text("DAB+")
    expect(page.locator(T("tx-state"))).to_have_text("AUS", timeout=8000)          # „Senden aus“ hat die Kette gestoppt

    # ---------------------------------------------------------------- Log
    step("Log: Filter")
    goto(page, "log")
    expect(page.locator(".logline").first).to_be_visible()
    page.fill(T("log-filter"), "Pegelrampe")
    page.wait_for_timeout(300)
    n = page.locator(".logline").count()
    assert n >= 1 and all("Pegelrampe" in t for t in page.locator(".logline .lm").all_inner_texts())

    # ---------------------------------------------------------------- Not-Aus & Verbindung
    step("Not-Aus")
    page.locator(T("rf-enable")).check(force=True)
    expect(page.locator(T("tx-state"))).to_have_text("ON AIR")
    page.click(T("estop"))
    expect(page.locator(T("tx-state"))).to_have_text("AUS", timeout=5000)
    st = api(base, "/api/state")
    assert st["tx"]["state"] == "idle" and st["pluto"]["atten_db"] == 89.75

    step("Verbindungsabbruch wird angezeigt und neu verbunden")
    api(base, "/api/_mock/outage", {"seconds": 3})   # Server-Ausfall simulieren
    expect(page.locator(T("conn"))).to_have_class("conn bad", timeout=5000)
    expect(page.locator("body")).to_have_class(re.compile(r"\boffline\b"))
    expect(page.locator(T("conn"))).to_have_class("conn ok", timeout=20000)
    expect(page.locator(T("tx-state"))).to_have_text("AUS")


def auto_apply(browser, base: str, console: Console):
    """Automatisch übernehmen (Standard): Live-Änderung und Strukturänderung ohne Klick, Neustart ohne Dialog; abschaltbar."""
    ctx = browser.new_context(locale="de-DE", viewport={"width": 1440, "height": 900}, color_scheme="light")
    page = ctx.new_page()
    console.attach(page)
    page.goto(base)
    expect(page.locator(T("conn"))).to_have_class("conn ok", timeout=15000)

    step("Automatisch übernehmen: Live-Änderung ohne Klick")
    if api(base, "/api/state")["tx"]["state"] != "running":
        api(base, "/api/tx/start", {})
    for _ in range(100):
        if api(base, "/api/state")["tx"]["state"] == "running":
            break
        time.sleep(0.1)
    goto(page, "ensemble")
    page.fill(T("ens-label"), "DABLAB Test Auto")
    expect(page.locator(T("apply-auto"))).to_be_checked()
    page.wait_for_timeout(300)
    errs = page.locator(".ab-errlist li").all_inner_texts()
    assert not errs, f"Entwurf ungültig: {errs}"
    expect(page.locator(T("applybar"))).to_be_hidden(timeout=6000)
    assert api(base, "/api/state")["config"]["ensemble"]["label"] == "DABLAB Test Auto"

    step("Automatisch übernehmen: Strukturänderung startet die Kette ohne Dialog neu")
    goto(page, "dienste")
    page.click(T("svc-item-0"))
    page.click(T("svc-tab-audio"))
    cur = page.locator(T("svc-bitrate")).input_value()
    new = "128" if cur != "128" else "96"
    page.select_option(T("svc-bitrate"), new)
    expect(page.locator(T("applybar"))).to_be_hidden(timeout=10000)
    expect(page.locator("dialog[open]")).to_have_count(0)
    st = api(base, "/api/state")
    assert int(st["config"]["services"][0]["audio"]["bitrate"]) == int(new), st["config"]["services"][0]["audio"]

    step("Ein Schalter: „Senden aus“ stoppt die Kette, „Senden an“ startet sie und schaltet ein")
    for want in ("ON AIR", "AUS", "ON AIR"):
        if want == "AUS":
            page.locator(T("rf-enable")).uncheck(force=True)
        else:
            page.locator(T("rf-enable")).check(force=True)
            if page.locator(T("confirm-atten-ok")).count():
                page.click(T("confirm-atten-ok"))
        expect(page.locator(T("tx-state"))).to_have_text(want, timeout=15000)

    step("Automatik aus: Änderung bleibt in der Leiste stehen")
    goto(page, "ensemble")
    page.fill(T("ens-label"), "DABLAB Test Aus")
    page.locator(T("apply-auto")).uncheck()
    page.wait_for_timeout(3000)
    expect(page.locator(T("applybar"))).to_be_visible()
    assert api(base, "/api/state")["config"]["ensemble"]["label"] == "DABLAB Test Auto"
    page.click(T("apply"))
    expect(page.locator(T("applybar"))).to_be_hidden(timeout=6000)
    assert api(base, "/api/state")["config"]["ensemble"]["label"] == "DABLAB Test Aus"
    page.evaluate("localStorage.removeItem('dablab.autoApply')")
    api(base, "/api/tx/stop", {})
    ctx.close()


def onboarding_flow(browser, base: str, console: Console):
    """Einrichtung beim ersten Start: Pflicht-Assistent, Senden gesperrt bis bestätigt; Labor und Amateurfunk."""
    api(base, "/api/onboarding/reset", {})
    ctx = browser.new_context(locale="de-DE", viewport={"width": 1440, "height": 900}, color_scheme="light")
    page = ctx.new_page()
    console.attach(page)
    page.goto(base)

    step("Einrichtung: erscheint beim ersten Aufruf, Escape schließt nicht")
    expect(page.locator(T("onboarding"))).to_be_visible(timeout=10000)
    page.keyboard.press("Escape")
    expect(page.locator(T("onboarding"))).to_be_visible()
    r = urllib.request.Request(base.rstrip("/") + "/api/rf", method="POST", data=json.dumps({"enabled": True}).encode(),
                               headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(r, timeout=10)
        raise AssertionError("Senden ohne Einrichtung wurde nicht abgelehnt")
    except urllib.error.HTTPError as e:
        assert e.code == 403, e.code

    step("Einrichtung: Firmware-Prüfung, Labor, alle Bestätigungen nötig")
    page.click(T("onb-next"))
    expect(page.locator(T("onb-check-pluto"))).to_have_class(re.compile(r"\bok\b"))
    expect(page.locator(T("onb-check-fw"))).to_have_class(re.compile(r"\bok\b"))
    page.click(T("onb-next"))
    expect(page.locator(T("hw-cap-uhd"))).to_have_class(re.compile(r"\bok\b"))      # Schritt PC-Leistung
    page.click(T("hw-bench"))
    expect(page.locator(T("hw-bench-res"))).to_contain_text("NVENC")
    page.click(T("onb-next"))
    page.click(T("onb-mode-lab"))
    page.click(T("onb-next"))
    for k in ("shielded", "no_broadcast_radiation", "terms", "privacy"):
        page.check(T(f"onb-accept-{k}"))
    expect(page.locator(T("onb-next"))).to_be_disabled()                         # „codecs“ fehlt noch
    page.check(T("onb-accept-codecs"))
    page.click(T("onb-next"))

    step("Einrichtung: Komponenten nur nach Bestätigung je Komponente installierbar")
    expect(page.locator(T("onb-components"))).to_be_visible()
    expect(page.locator(T("comp-odr"))).to_have_class(re.compile(r"\bok\b"))
    expect(page.locator(T("comp-install-ytdlp"))).to_be_disabled()               # erst bestätigen
    page.check(T("comp-accept-ytdlp"))
    page.click(T("comp-install-ytdlp"))
    expect(page.locator(T("comp-ytdlp"))).to_have_class(re.compile(r"\bok\b"), timeout=10000)
    page.click(T("onb-next"))
    page.click(T("onb-finish"))
    expect(page.locator(T("onboarding"))).to_have_count(0, timeout=5000)
    ob = api(base, "/api/state")["onboarding"]
    assert ob["done"] and ob["mode"] == "lab", ob

    step("Firmware: RAM-Test vor Flashen, Bestätigung nötig, Danke-Liste")
    goto(page, "firmware")
    expect(page.locator(T("page-firmware"))).to_be_visible()
    expect(page.locator(T("fw-running"))).to_contain_text("v0.31-dablab")
    flash = page.locator("[data-testid^='fw-flash-']")
    expect(flash).to_be_disabled()                                               # weder RAM-Test noch Bestätigung
    page.click("[data-testid^='fw-ram_test-']")
    expect(page.locator(T("fw-job-result"))).to_contain_text("✓", timeout=10000)
    expect(page.locator(T("fw-tested-shipped"))).not_to_contain_text("noch nicht")
    expect(flash).to_be_disabled()                                               # Bestätigung fehlt
    page.check(T("fw-confirm"))
    expect(flash).to_be_enabled()
    flash.click()
    expect(page.locator(T("fw-history"))).to_contain_text("flash", timeout=10000)

    step("Einrichtung: erneut öffnen, Amateurfunk mit Rufzeichen")
    cfg_before = api(base, "/api/state")["config"]
    page.click(T("onb-open"))
    page.locator(T("onb-credits")).locator("summary").click()
    expect(page.locator(T("onb-credits"))).to_contain_text("F5OEO")
    for _ in range(3):
        page.click(T("onb-next"))
    page.click(T("onb-mode-ham"))
    expect(page.locator(T("onb-next"))).to_be_disabled()
    page.fill(T("onb-callsign"), "dl0abc")
    page.click(T("onb-next"))
    for k in ("shielded", "no_broadcast_radiation", "terms", "privacy", "codecs", "ham_license", "ham_rules"):
        page.check(T(f"onb-accept-{k}"))
    page.click(T("onb-next"))
    page.click(T("onb-next"))                                                      # Komponenten (alles da)
    page.click(T("onb-finish"))
    expect(page.locator(T("onboarding"))).to_have_count(0, timeout=5000)
    ob = api(base, "/api/state")["onboarding"]
    assert ob["mode"] == "ham" and ob["callsign"] == "DL0ABC", ob
    # Dashboard wechselt in den Amateurfunk-Betrieb: DVB-S, DATV-Karte mit Rufzeichen, Übernehmen nötig
    expect(page.locator(T("card-datv"))).to_be_visible(timeout=5000)
    expect(page.locator(T("std-dvbs"))).to_have_attribute("aria-checked", "true")
    expect(page.locator(T("datv-callsign"))).to_have_value("DL0ABC")
    expect(page.locator(".applybar")).not_to_contain_text("Fehler –")                 # DATV-Entwurf ohne Prüffehler (nur 1 Programm)
    # vorige Konfiguration wiederherstellen: erst warten, bis „Automatisch übernehmen“ den Wechsel zum Server geschickt hat
    for _ in range(40):
        if api(base, "/api/state")["config"]["rf"].get("channel") == "ham" or page.locator(T("apply")).is_enabled():
            break
        page.wait_for_timeout(250)
    if page.locator(T("apply")).is_enabled():
        page.click(T("apply-discard"))
    api(base, "/api/config", cfg_before, method="PUT")
    page.wait_for_timeout(1500)
    if page.locator(T("apply-reload")).is_visible():                                # Entwurf war noch offen → Serverstand laden
        page.click(T("apply-reload"))
    expect(page.locator(T("std-dvbs"))).to_have_attribute("aria-checked", "false", timeout=8000)

    step("Benötigte Hardware: PC-Bewertung, Partnerlinks als Anzeige, Unterstützen")
    goto(page, "hardware")
    expect(page.locator(T("hw-cap-hdr10"))).to_be_visible()
    expect(page.locator(T("shop-group-ham"))).to_be_visible()
    expect(page.locator(T("shop-group-radio")).locator("h3 .mini-ad")).to_be_visible()      # „Anzeige“ einmal je Gruppe
    expect(page.locator(T("shop-pluto")).locator(".mini-ad")).to_have_count(0)
    expect(page.locator(T("sponsor-kofi"))).to_have_attribute("href", "https://ko-fi.com/711it")

    step("Logdatei: Ansicht mit Sprache, Original-Markierung")
    goto(page, "logdatei")
    expect(page.locator(T("logf-list")).locator(".logline")).to_have_count(2)

    step("Einstellungen: Sprache gilt fürs ganze Dashboard")
    goto(page, "einstellungen")
    page.select_option(T("settings-lang"), "en")                                   # lädt neu
    expect(page.locator("html")).to_have_attribute("lang", "en")
    goto(page, "logdatei")
    expect(page.locator(T("logf-list"))).to_contain_text("(original)")
    goto(page, "einstellungen")
    page.select_option(T("settings-lang"), "de")
    expect(page.locator("html")).to_have_attribute("lang", "de")
    page.evaluate("localStorage.removeItem('dablab.lang')")
    ctx.close()


def datv_ui(browser, base: str, console: Console):
    """DVB-S-Seite: Umschalten auf Amateurfunk-DATV, Band/Frequenz/Rufzeichen, Validierung, Regie-Kachel."""
    ctx = browser.new_context(locale="de-DE", viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.add_init_script("try { localStorage.setItem('dablab.autoApply', 'false') } catch (e) {}")
    page = ctx.new_page()
    console.attach(page)
    page.goto(base + "#/regie")
    expect(page.locator(T("conn"))).to_have_class("conn ok", timeout=15000)

    step("DATV: DVB-S auf Amateurfunk umschalten, QO-100-Voreinstellung")
    page.click(T("regie-edit-dvbs"))
    expect(page.locator("#main")).to_have_attribute("data-page", "modulation")
    page.click(T("dvbs-use-ham"))
    expect(page.locator(T("datv-license"))).to_be_visible()
    expect(page.locator(T("dvbs-lnb-warning"))).to_have_count(0)
    expect(page.locator(T("datv-freq-read"))).to_contain_text("2.406")
    expect(page.locator(T("datv-dl"))).to_contain_text("10.495")             # QO-100-Downlink = Uplink + 8089,5 MHz

    step("DATV: Rufzeichen Pflicht, Bandgrenze wird geprüft")
    page.click(T("datv-sr-1000000"))                                       # mehrere Programme aus den vorigen Schritten
    expect(page.locator(".field[data-path='dvbs.callsign']")).to_have_class(re.compile("invalid"))
    page.fill(T("datv-callsign"), "dl0abc")
    expect(page.locator(".field[data-path='dvbs.callsign']")).not_to_have_class(re.compile("invalid"))
    page.fill(T("datv-freq"), "2409.0")                                    # ± 675 kHz ragt über 2409,5 MHz
    expect(page.locator(".field[data-path='dvbs.ham_freq_mhz']")).to_have_class(re.compile("invalid"))
    page.fill(T("datv-freq"), "2406")
    expect(page.locator(".field[data-path='dvbs.ham_freq_mhz']")).not_to_have_class(re.compile("invalid"))
    page.select_option(T("datv-band"), "70cm")
    expect(page.locator(T("datv-freq-read"))).to_contain_text("437")
    if page.locator(T("apply")).is_disabled():
        page.click(T("apply-errors"))
        raise AssertionError(f"DATV-Entwurf ungültig: {page.locator('.ab-errlist li').all_inner_texts()}")
    page.click(T("apply"))
    if page.locator(T("confirm-restart-ok")).count():
        page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden(timeout=8000)
    c = api(base, "/api/state")["config"]
    assert c["dvbs"]["use"] == "ham" and c["dvbs"]["callsign"] == "DL0ABC" and c["rf"]["channel"] == "ham", (c["dvbs"], c["rf"])
    assert abs(c["rf"]["freq_hz"] - 437000000) < 2, c["rf"]
    assert all(pr.get("provider") == "DL0ABC" for pr in c["dvb"]["programs"]), [pr.get("provider") for pr in c["dvb"]["programs"]]

    step("DATV: Regie-Kachel zeigt Rufzeichen und Band, zurück auf Satellit")
    goto(page, "regie")
    expect(page.locator(T("regie-tiles"))).to_contain_text("DATV DL0ABC")
    page.click(T("regie-edit-dvbs"))
    page.click(T("dvbs-use-sat"))
    expect(page.locator(T("dvbs-lnb-warning"))).to_be_visible()
    page.click(T("apply"))
    if page.locator(T("confirm-restart-ok")).count():
        page.click(T("confirm-restart-ok"))
    expect(page.locator(T("applybar"))).to_be_hidden(timeout=8000)
    c = api(base, "/api/state")["config"]
    assert c["dvbs"]["use"] == "sat" and c["rf"]["channel"] == "sat", (c["dvbs"].get("use"), c["rf"])
    ctx.close()


def screenshots(browser, base: str, console: Console):
    variants = [("hell", "light", "desktop", {"width": 1440, "height": 900}),
                ("dunkel", "dark", "desktop", {"width": 1440, "height": 900}),
                ("hell", "light", "handy", {"width": 390, "height": 844}),
                ("dunkel", "dark", "handy", {"width": 390, "height": 844})]
    # etwas Zustand für aussagekräftige Bilder: Kette läuft und sendet, Bericht vorhanden
    api(base, "/api/tx/start", {})
    time.sleep(1)
    api(base, "/api/rf", {"enabled": True})
    api(base, "/api/check", {"seconds": 5})
    for de, scheme, dev, vp in variants:
        ctx = browser.new_context(locale="de-DE", viewport=vp, color_scheme=scheme, device_scale_factor=2 if dev == "handy" else 1,
                                  is_mobile=dev == "handy", has_touch=dev == "handy")
        page = ctx.new_page()
        console.attach(page)
        page.goto(base + "#/hf")
        expect(page.locator(T("tx-state"))).to_have_text("ON AIR", timeout=8000)
        for pid in PAGES:
            page.goto(base + f"#/{pid}")
            expect(page.locator("#main")).to_have_attribute("data-page", pid)
            page.wait_for_timeout(600)
            no_hscroll(page, f"{pid}/{dev}")
            page.screenshot(path=str(SHOTS / f"{pid}_{de}_{dev}.png"), full_page=True)
        page.goto(base + "#/dienste")
        page.click(T("svc-tab-sls"))
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / f"dienste-slideshow_{de}_{dev}.png"), full_page=True)
        page.click(T("svc-tab-source"))
        expect(page.locator(T("np-title"))).not_to_have_text(re.compile(r"^(Kein Titel gemeldet|Wiedergabe gestoppt)$"), timeout=5000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / f"dienste-playlist_{de}_{dev}.png"), full_page=True)
        page.goto(base + "#/medien")
        page.select_option(T("pl-select"), "Demo")
        page.wait_for_timeout(600)
        no_hscroll(page, f"medien-playlist/{dev}")
        page.screenshot(path=str(SHOTS / f"medien-playlist_{de}_{dev}.png"), full_page=True)
        # Weitere Standards: Konfiguration per API umschalten, Bericht erzeugen, Bereiche fotografieren
        for std, rf, pids in (("fm", {"channel": "fm", "freq_hz": 100600000}, ["hf", "sender", "rds", "pruefen", "presets"]),
                              ("dvbt", {"channel": "K21", "freq_hz": 474000000}, ["hf", "modulation", "programme", "si", "pruefen"]),
                              ("dvbc", {"channel": "S21", "freq_hz": 306000000}, ["modulation", "programme"]),
                              ("pal", {"channel": "K36", "freq_hz": 591250000}, ["hf", "pal", "pruefen"]),
                              ("dvbs", {"channel": "sat", "freq_hz": 1550000000}, ["hf", "modulation", "pruefen"])):
            cfg = api(base, "/api/state")["config"]
            cfg["mode"] = std
            cfg["rf"].update(rf)
            api(base, "/api/config", cfg, method="PUT")
            api(base, "/api/tx/stop", {}); api(base, "/api/tx/start", {}); time.sleep(0.8)
            api(base, "/api/rf", {"enabled": True})
            api(base, "/api/check", {"seconds": 5})
            for pid in pids:
                page.goto(base + f"#/{pid}")
                expect(page.locator("#main")).to_have_attribute("data-page", pid)
                page.wait_for_timeout(700)
                page.evaluate("window.scrollTo(0, 0)")
                no_hscroll(page, f"{std}-{pid}/{dev}")
                page.screenshot(path=str(SHOTS / f"{std}-{pid}_{de}_{dev}.png"), full_page=True)
            if std == "dvbt":
                page.goto(base + "#/programme"); page.click(T("prg-tab-video")); page.wait_for_timeout(400); page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(SHOTS / f"dvbt-programme-video_{de}_{dev}.png"), full_page=True)
        cfg = api(base, "/api/state")["config"]
        cfg["mode"] = 1; cfg["rf"].update({"channel": "6C", "freq_hz": 185360000})
        api(base, "/api/config", cfg, method="PUT")
        api(base, "/api/tx/stop", {}); api(base, "/api/tx/start", {}); time.sleep(0.8); api(base, "/api/rf", {"enabled": True})
        ctx.close()
    api(base, "/api/tx/stop", {})


WEBSITE = [("regie", None, "regie"), ("dab", 1, "dienste"), ("ukw", "fm", "rds"), ("dvbt", "dvbt", "programme"), ("dvbc", "dvbc", "modulation"),
           ("dvbs", "dvbs", "modulation"), ("pal", "pal", "pal")]
STD_RF = {1: {"channel": "6C", "freq_hz": 185360000}, "fm": {"channel": "fm", "freq_hz": 100600000}, "dvbt": {"channel": "K36", "freq_hz": 594000000},
          "dvbc": {"channel": "K30", "freq_hz": 546000000}, "dvbs": {"channel": "sat", "freq_hz": 1288000000}, "pal": {"channel": "K36", "freq_hz": 591250000}}


def website_shots(browser, base: str, console: Console):
    """Je Standard eine Desktop-Ansicht (1440x900) hell und dunkel für die Website; Kette läuft und sendet."""
    out = SHOTS / "website"
    out.mkdir(exist_ok=True)
    for name, mode, pid in WEBSITE:
        cfg = api(base, "/api/state")["config"]
        m = mode if mode is not None else 1
        cfg["mode"] = m
        cfg["rf"].update(STD_RF[m])
        if m == "dvbs":
            cfg["dvbs"].update({"transponder_mhz": 11038, "polarization": "V", "lof_mhz": 9750, "symbol_rate": 22000000, "fec": "7/8"})
        if m == "dvbt":
            cfg["dvbt"].update({"bandwidth_mhz": 8, "fft": "8k", "constellation": "64-QAM", "code_rate": "2/3", "guard": "1/32"})
        if m == "dvbc":
            cfg["dvbc"].update({"symbol_rate": 6900000, "constellation": "256-QAM"})
        if m in ("dvbt", "dvbc", "dvbs"):
            def prog(n, name, lcn, typ="tv", vc="h264", res="hd", vbr=6000, ac="aac", abr=128, hdr="sdr", title="Testsendung"):
                return {"key": f"prg{n}", "name": name, "provider": "PlutoSDR-Regie", "sid": f"0x{n:04X}", "lcn": lcn, "type": typ,
                        "video": {"source": {"kind": "testpattern", "pattern": "smptehdbars", "file": "", "url": "", "playlist": "", "loop": True}, "codec": vc, "resolution": res, "bitrate_kbps": vbr, "encoder": "cpu", "hdr": hdr},
                        "audio": {"codec": ac, "bitrate_kbps": abr, "source": {"kind": "tone", "freq_hz": 1000, "level_dbfs": -18}},
                        "eit": {"title": title, "description": "Live aus der PlutoSDR-Regie", "duration_min": 60}}
            cfg["dvb"]["programs"] = ([prog(1, "Regie HD", 1, vbr=7000), prog(2, "Regie UHD", 2, vc="hevc", res="uhd", vbr=14000), prog(3, "Regie HDR10", 3, vc="hevc", res="uhd", vbr=10000, hdr="hdr10")] if m == "dvbs"
                                      else [prog(1, "Regie HD", 1, vbr=8000), prog(2, "Regie SD", 2, vc="mpeg2", res="sd", vbr=3500, ac="mp2", abr=192), prog(3, "Regie Radio", 99, typ="radio", ac="mp2", abr=192)])
            cfg["dvb"]["si"].update({"network_name": "PlutoSDR-Regie"})
        if m == "fm":
            cfg["fm"]["rds"].update({"ps": "REGIE", "rt": "PlutoSDR-Regie – UKW-Stereo mit RDS", "af": [93.3, 99.9, 104.4]})
        if m == "pal":
            cfg["pal"]["image"].update({"kind": "testpattern", "pattern": "pal75bars", "text": "PlutoSDR-Regie", "text2": ""})
        api(base, "/api/config", cfg, method="PUT")
        api(base, "/api/tx/stop", {})
        api(base, "/api/tx/start", {})
        time.sleep(0.9)
        api(base, "/api/rf", {"enabled": True, "atten_db": 50})
        for de, scheme in (("hell", "light"), ("dunkel", "dark")):
            ctx = browser.new_context(locale="de-DE", viewport={"width": 1440, "height": 900}, color_scheme=scheme, device_scale_factor=1)
            page = ctx.new_page()
            console.attach(page)
            page.goto(base + f"#/{pid}")
            expect(page.locator("#main")).to_have_attribute("data-page", pid)
            page.add_style_tag(content="#toasts{display:none!important}")
            if pid == "dienste":
                page.click(T("svc-tab-sls"))
            if pid == "programme":                      # erster TV-Dienst, Reiter Video (Encoder/HDR/Quelle)
                page.locator(".svcitem").first.click()
                page.click(T("prg-tab-video"))
            page.wait_for_timeout(900)
            page.screenshot(path=str(out / f"{name}_{de}.png"))
            ctx.close()
    cfg = api(base, "/api/state")["config"]
    cfg["mode"] = 1
    cfg["rf"].update(STD_RF[1])
    api(base, "/api/config", cfg, method="PUT")
    api(base, "/api/tx/stop", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots", choices=["all", "website", "none"], default="all",
                    help="all = Funktionstest + alle Screenshots, website = nur Website-Bilder, none = nur Funktionstest (RAM sparen)")
    a = ap.parse_args()
    SHOTS.mkdir(exist_ok=True)
    proc = None
    base = a.url
    if not base:
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(HERE / "mock_server.py"), "--port", str(port), "--fast"],
                                stdout=open(SHOTS / "mock_server.log", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}/"
        for _ in range(50):
            try:
                api(base, "/api/state")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
    console = Console()
    ok = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not a.headed)
            ctx = browser.new_context(locale="de-DE", viewport={"width": 1440, "height": 900}, color_scheme="light", accept_downloads=True)
            # Der Funktionstest bedient „Übernehmen“ und den Neustart-Dialog von Hand → Automatik aus
            ctx.add_init_script("try { localStorage.setItem('dablab.autoApply', 'false') } catch (e) {}")
            page = ctx.new_page()
            console.attach(page)
            ctx.on("page", console.attach)
            print("Funktionstest:")
            functional(page, base)
            ctx.close()
            auto_apply(browser, base, console)
            onboarding_flow(browser, base, console)
            datv_ui(browser, base, console)
            if a.shots == "all":
                print("Screenshots:")
                screenshots(browser, base, console)
            if a.shots in ("all", "website"):
                print("Website-Screenshots:")
                website_shots(browser, base, console)
            browser.close()
        # Netzwerkabbrüche aus dem Verbindungstest sind erwartet
        errs = [e for e in console.errors if "503" not in e]
        assert not errs, "Konsolenfehler:\n" + "\n".join(errs)
        ok = True
        print(f"OK – alle Prüfungen bestanden, Screenshots in {SHOTS}")
    finally:
        if proc:
            proc.terminate()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
