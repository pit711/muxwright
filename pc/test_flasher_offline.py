"""Offline-Test des Firmware-Flashers (kein Pluto nötig): gefälschtes dfu-util + gefälschtes SSH.

  python pc/test_flasher_offline.py
"""
import json, os, shutil, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flasher  # noqa: E402

ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "pluto_fw", "out", "build3")
FAKE_DFU = r'''
import sys, os, json
log = os.environ["FAKE_DFU_LOG"]
a = sys.argv[1:]
open(log, "a").write(json.dumps(a) + "\n")
if "-l" in a:
    if os.path.exists(log + ".dfu"):
        print('Found DFU: [0456:b674] ver=0221, devnum=9, cfg=1, intf=0, path="1-1", alt=1, name="firmware.dfu", serial="x"')
    sys.exit(0)
if "-D" in a:
    d = open(a[a.index("-D") + 1], "rb").read()
    sys.exit(0 if d[-8:-5] == b"UFD" or d[-13:-10] == b"UFD" or b"UFD" in d[-16:] else 3)
if "-e" in a:
    os.remove(log + ".dfu")
    open(log + ".booted", "w").write("1")
    sys.exit(0)
'''


def main():
    tmp = tempfile.mkdtemp(prefix="flasher_")
    try:
        root = os.path.join(tmp, "app")
        fwd = os.path.join(root, "pluto_fw", "firmware")
        os.makedirs(fwd)
        for n in ("pluto.frm", "VERSIONS"):
            shutil.copy(os.path.join(SRC, n), fwd)
        runtime = os.path.join(tmp, "runtime")
        dfu_py = os.path.join(tmp, "fake_dfu.py")
        open(dfu_py, "w").write(FAKE_DFU)
        log = os.path.join(tmp, "dfu.log")
        os.environ["FAKE_DFU_LOG"] = log
        itb = open(os.path.join(SRC, "pluto.itb"), "rb").read()
        mtd3 = itb + b"\xff" * (flasher.MTD3_MAX - len(itb))
        calls = []

        def ssh(cmd, timeout):
            calls.append(cmd)
            if cmd == "cat /dev/mtd3":
                return 0, mtd3, ""
            if "device_reboot" in cmd:
                open(log + ".dfu", "w").write("1")
                return 0, b"", ""
            if "VERSION" in cmd:
                return (0, b"v0.31-dablab.20261023-b3\n", "") if os.path.exists(log + ".booted") else (1, b"", "")
            return 1, b"", "?"

        holds = []

        class App:
            def hold(self, on):
                holds.append(on)

        f = flasher.Flasher(root, runtime, app=App(), dfu_cmd=[sys.executable, dfu_py], ssh=ssh, fw_now=lambda: "v0.31-dablab.20261023-b3",
                            wait_scale=0.02)

        def wait():
            for _ in range(500):
                if not f.status()["job"]["running"]:
                    return f.status()["job"]
                time.sleep(0.02)
            raise AssertionError("Auftrag hängt")

        # Formate
        assert flasher.make_dfu(itb) == open(os.path.join(SRC, "pluto.dfu"), "rb").read(), "DFU-Suffix weicht von dfu-suffix ab"
        assert flasher.make_frm(itb) == open(os.path.join(SRC, "pluto.frm"), "rb").read(), ".frm weicht ab"
        imgs = f.images()
        assert len(imgs) == 1 and imgs[0]["ok"] and imgs[0]["version"] == "v0.31-dablab.20261023-b3", imgs
        img = imgs[0]

        # beschädigtes Image wird abgelehnt
        bad = bytearray(open(os.path.join(fwd, "pluto.frm"), "rb").read())
        bad[5000] ^= 0xFF
        p = os.path.join(tmp, "bad.frm")
        open(p, "wb").write(bad)
        assert flasher.read_image(p)[1], "beschädigte .frm muss erkannt werden"

        # Flashen vor RAM-Test gesperrt
        code, r = f.start("flash", img["id"], confirm=True)
        assert code == 409, (code, r)

        # RAM-Test
        code, r = f.start("ram_test", img["id"])
        assert code == 200, r
        j = wait()
        assert j["result"] == "ok", j
        assert holds == [True, False], holds
        assert "sync; /usr/sbin/device_reboot ram" in calls
        img = f.images()[0]
        assert img["ram_tested"], "RAM-Test muss vermerkt werden"

        # Flashen ohne Bestätigung abgelehnt, mit Bestätigung: erst Sicherung, dann sf
        assert f.start("flash", img["id"])[0] == 400
        os.remove(log + ".booted")
        code, r = f.start("flash", img["id"], confirm=True)
        assert code == 200, r
        j = wait()
        assert j["result"] == "ok", j
        assert calls.count("cat /dev/mtd3") == 1 and "sync; /usr/sbin/device_reboot sf" in calls, calls
        backups = [i for i in f.images() if i["kind"] == "backup"]
        # Sicherung = gleiches Image wie das mitgelieferte → wird als Dublette zusammengefasst; Datei muss aber existieren
        files = [n for n in os.listdir(os.path.join(runtime, "firmware")) if n.startswith("backup_")]
        assert len(files) == 1, files
        assert flasher.read_image(os.path.join(runtime, "firmware", files[0]))[1] == [], "Sicherung muss gültig sein"
        st = json.load(open(os.path.join(runtime, "firmware", "flasher.json"), encoding="utf-8"))
        assert [h["action"] for h in st["history"]] == ["ram_test", "flash"], st["history"]

        # falsche Version nach dem Neustart → Fehler
        open(os.path.join(fwd, "VERSIONS"), "w").write("device-fw v9.99-anders\n")
        f._img_cache.clear()
        img = f.images()[0]
        code, r = f.start("ram_test", img["id"])
        j = wait()
        assert j["result"] == "fail" and "expected v9.99" in (j["error"] or ""), j

        # Fishball/PlutoSky: SD-Karten-Image (kleines Testimage) auf einen gefälschten Wechseldatenträger
        sdsrc = os.path.join(root, "pluto_fw", "firmware", "fishball7010")
        os.makedirs(os.path.join(sdsrc, "sdimg", "overclock"))
        for n in flasher.SD_REQUIRED + ("overclock/BOOT_fsbl_CPU950_DDR600",):
            open(os.path.join(sdsrc, "sdimg", *n.split("/")), "wb").write(os.urandom(4096) + n.encode())
        open(os.path.join(sdsrc, "VERSION"), "w").write("tezuka-test+muxwright\n")
        with open(os.path.join(sdsrc, "SHA256SUMS"), "w") as fs:
            for n in flasher.SD_REQUIRED + ("overclock/BOOT_fsbl_CPU950_DDR600",):
                fs.write(f"{flasher.file_sha256(os.path.join(sdsrc, 'sdimg', *n.split('/')))}  ./sdimg/{n}\n")
        card = os.path.join(tmp, "card")
        os.makedirs(card)
        open(os.path.join(card, "BOOT.bin"), "wb").write(b"alte Werksfirmware")
        open(os.path.join(card, "fremd.txt"), "w").write("bleibt")
        drives = [{"drive": "Q:", "root": card, "label": "SD", "fs": "FAT32", "size": 8 << 30, "free": 4 << 30}]
        f.drives = lambda: drives
        s = f.status()
        assert s["sd_images"]["fishball7010"]["ok"] and s["sd_images"]["fishball7010"]["version"] == "tezuka-test+muxwright", s["sd_images"]
        assert f.start("set_board", board="fishball7010")[0] == 200 and f.status()["board"] == "fishball7010"
        assert f.start("set_board", board="gibtsnicht")[0] == 400
        assert f.start("sd_write", drive="Q:")[0] == 400, "ohne Bestätigung"
        assert f.start("sd_write", drive="Z:", confirm=True)[0] == 400, "unbekanntes Laufwerk"
        drives[0]["fs"] = "exFAT"
        code, r = f.start("sd_write", drive="Q:", confirm=True)
        assert code == 400 and "FAT32" in r["errors"][0], r
        drives[0]["fs"] = "FAT32"
        code, r = f.start("sd_write", drive="Q:", confirm=True)
        assert code == 200, r
        j = wait()
        assert j["result"] == "ok", j
        for n in flasher.SD_REQUIRED:
            assert open(os.path.join(card, n), "rb").read() == open(os.path.join(sdsrc, "sdimg", n), "rb").read(), n
        assert open(os.path.join(card, "fremd.txt")).read() == "bleibt"
        bk = [n for n in os.listdir(os.path.join(runtime, "firmware")) if n.startswith("sd_backup_")]
        assert len(bk) == 1 and open(os.path.join(runtime, "firmware", bk[0], "BOOT.bin"), "rb").read() == b"alte Werksfirmware", bk
        # beschädigtes mitgeliefertes Image wird abgelehnt
        open(os.path.join(sdsrc, "sdimg", "uImage"), "ab").write(b"x")
        assert f.start("sd_write", drive="Q:", confirm=True)[0] == 400
        print("OK – Flasher offline geprüft:", len(open(log).read().splitlines()), "dfu-util-Aufrufe,", len(backups), "Sicherungen gelistet, SD-Karte geschrieben")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
