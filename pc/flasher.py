"""Firmware-Flasher für den ADALM-Pluto: Image prüfen → (Sicherung) → RAM-Test → Flashen, alles per DFU.

Ablauf wie pluto_fw/boot_ram.cmd und README „Flashen, Weg B“:
  ram_test: SSH „device_reboot ram“ → U-Boot-DFU (0456:b674) → dfu-util -a firmware.dfu -D <img>.dfu → -e (Detach)
            → U-Boot bootet das Image aus dem RAM, der Flash bleibt unberührt (Strom trennen = alte Firmware).
  flash:    nur nach bestandenem RAM-Test DESSELBEN Images (SHA-256) und mit Bestätigung; vorher automatische Sicherung
            von mtd3; SSH „device_reboot sf“ → dfu-util schreibt mtd3 → Detach → Neustart. U-Boot/Env (mtd0/1) nie.
  backup:   SSH „cat /dev/mtd3“ → FIT (Länge aus dem FDT-Kopf) → runtime/firmware/backup_<datum>_<fw>.frm (+ md5).
Ist der Pluto per SSH nicht erreichbar, wartet der Flasher bis zu 3 min auf ein DFU-Gerät (Taste beim Einstecken halten).
Images: nur die mitgelieferte, am Gerät geprüfte Firmware (pluto_fw/firmware) und die eigenen Sicherungen (Rückweg).
"""
import datetime as dt, hashlib, json, os, re, struct, subprocess, threading, time, urllib.request, zlib

NOWIN = 0x08000000 if os.name == "nt" else 0
MAGIC = b"ITB PlutoSDR (ADALM-PLUTO)"
MTD3_MAX = 0x1E00000          # 30 MiB
DFU_IDS = "0456:b673,0456:b674"


def make_dfu(itb):
    body = itb + struct.pack("<HHHH", 0xFFFF, 0xB673, 0x0456, 0x0100) + b"UFD" + bytes([16])
    return body + struct.pack("<I", zlib.crc32(body) ^ 0xFFFFFFFF)


def make_frm(itb):
    return itb + hashlib.md5(itb).hexdigest().encode() + b"\n"


def check_itb(itb):
    """-> Liste der Fehler (leer = ok)."""
    err = []
    if itb[:4] != b"\xd0\x0d\xfe\xed":
        err.append("kein FIT-Image (FDT-Magic fehlt)")
    elif struct.unpack(">I", itb[4:8])[0] != len(itb):
        err.append("FIT-Länge stimmt nicht")
    if MAGIC not in itb[:8192]:
        err.append("keine Pluto-Firmware („ITB PlutoSDR (ADALM-PLUTO)“ fehlt)")
    if len(itb) > MTD3_MAX:
        err.append(f"zu groß für den Flash ({len(itb) // 1048576} MiB > 30 MiB)")
    return err


def read_image(path):
    """.frm (FIT + md5) oder .dfu (FIT + DFU-Suffix) → (itb, fehler)"""
    data = open(path, "rb").read()
    if path.lower().endswith(".frm"):
        itb, tail = data[:-33], data[-33:]
        if not re.fullmatch(rb"[0-9a-f]{32}\n", tail) or hashlib.md5(itb).hexdigest().encode() != tail[:32]:
            return itb, ["md5-Prüfsumme der .frm-Datei stimmt nicht (Datei beschädigt?)"]
    elif path.lower().endswith(".dfu"):
        itb, suf = data[:-16], data[-16:]
        if suf[8:11] != b"UFD" or make_dfu(itb) != data:
            return itb, ["DFU-Suffix/CRC der .dfu-Datei stimmt nicht"]
        if suf[2:6] != struct.pack("<HH", 0xB673, 0x0456):
            return itb, ["DFU-Datei ist nicht für den Pluto (USB-ID)"]
    else:
        return b"", ["nur .frm oder .dfu"]
    return itb, check_itb(itb)


def sidecar_version(path):
    d = os.path.dirname(path)
    for name, rx in (("VERSION", r"^(\S+)"), ("VERSIONS", r"^device-fw\s+(\S+)")):
        try:
            m = re.search(rx, open(os.path.join(d, name), encoding="utf-8", errors="replace").read(), re.M)
            if m:
                return m.group(1)
        except OSError:
            pass
    m = re.search(r"backup_\d{8}-\d{4}_(.+)\.frm$", os.path.basename(path))
    return m.group(1) if m else None


class Flasher:
    def __init__(self, root, runtime, app=None, device_host="192.168.2.1", dfu_cmd=None, ssh=None, log=None,
                 agent_url=None, fw_now=None, wait_scale=1.0):
        self.root, self.runtime = root, runtime
        self.app = app                                  # hold(True/False): Pluto freigeben/übernehmen
        self.host = device_host
        exe = os.path.join(root, "pluto_fw", "tools", "dfu-util-win64", "dfu-util.exe")
        self.dfu_cmd = dfu_cmd or [exe]
        self.ssh = ssh                                  # callable(cmd, timeout) -> (rc, out bytes, err)
        self.log = log or (lambda lvl, msg: None)
        self.agent_url = agent_url
        self.fw_now = fw_now or (lambda: None)          # aktuell laufende Firmware (aus iio-Kontext)
        self.wait_scale = wait_scale
        self.lock = threading.Lock()
        self.job = {"running": False, "action": None, "image": None, "phase": None, "lines": [], "result": None, "error": None,
                    "started": None, "finished": None}
        self._img_cache = {}

    # ---------------------------------------------------------------- Zustand
    @property
    def fwdir(self):
        return os.path.join(self.runtime, "firmware")

    @property
    def state_file(self):
        return os.path.join(self.fwdir, "flasher.json")

    def _state(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"ram_tested": {}, "history": []}

    def _save_state(self, s):
        os.makedirs(self.fwdir, exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.state_file)

    def _candidates(self):
        r = self.root
        yield "shipped", os.path.join(r, "pluto_fw", "firmware", "pluto.frm")
        yield "shipped", os.path.join(r, "pluto_fw", "out", "build3", "pluto.frm")
        if os.path.isdir(self.fwdir):                    # nur eigene Sicherungen (Rückweg), keine fremden Images
            for n in sorted(os.listdir(self.fwdir)):
                if n.startswith("backup_") and n.lower().endswith(".frm"):
                    yield "backup", os.path.join(self.fwdir, n)

    def images(self):
        out, seen = [], set()
        for kind, p in self._candidates():
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            key = (p, st.st_size, st.st_mtime)
            info = self._img_cache.get(key)
            if info is None:
                itb, err = read_image(p)
                info = {"sha256": hashlib.sha256(itb).hexdigest(), "errors": err, "size": len(itb)}
                self._img_cache[key] = info
            if info["sha256"] in seen:
                continue
            seen.add(info["sha256"])
            out.append({"id": info["sha256"][:16], "kind": kind, "path": p, "name": os.path.relpath(p, self.root),
                        "version": sidecar_version(p), "size": info["size"], "sha256": info["sha256"], "ok": not info["errors"],
                        "errors": info["errors"], "mtime": int(st.st_mtime)})
        rt = self._state().get("ram_tested", {})
        for i in out:
            i["ram_tested"] = rt.get(i["sha256"])
        return out

    def status(self):
        with self.lock:
            job = dict(self.job, lines=self.job["lines"][-40:])
        s = self._state()
        return {"images": self.images(), "job": job, "running_fw": self.fw_now(), "history": s.get("history", [])[-10:],
                "user_dir": self.fwdir}

    # ---------------------------------------------------------------- Aufträge
    def start(self, action, image_id=None, confirm=False):
        if action not in ("ram_test", "flash", "backup"):
            return 400, {"ok": False, "errors": ["unbekannte Aktion"]}
        img = None
        if action != "backup":
            img = next((i for i in self.images() if i["id"] == image_id), None)
            if img is None:
                return 404, {"ok": False, "errors": ["Image nicht gefunden"]}
            if not img["ok"]:
                return 400, {"ok": False, "errors": img["errors"]}
        if action == "flash":
            if not img["ram_tested"] and img["kind"] != "backup":   # Sicherung = lief schon auf diesem Gerät
                return 409, {"ok": False, "errors": ["Erst den RAM-Test mit genau diesem Image bestehen, dann flashen"]}
            if confirm is not True:
                return 400, {"ok": False, "errors": ["Flashen muss ausdrücklich bestätigt werden (confirm: true)"]}
        with self.lock:
            if self.job["running"]:
                return 409, {"ok": False, "errors": ["Es läuft bereits ein Firmware-Auftrag"]}
            self.job = {"running": True, "action": action, "image": img and img["id"], "phase": "start", "lines": [], "result": None,
                        "error": None, "started": time.time(), "finished": None}
        threading.Thread(target=self._run, args=(action, img), daemon=True, name=f"fw-{action}").start()
        return 200, {"ok": True}

    def _line(self, s, phase=None):
        with self.lock:
            if phase:
                self.job["phase"] = phase
            self.job["lines"].append(str(s).rstrip()[:300])
            self.job["lines"] = self.job["lines"][-300:]

    def _run(self, action, img):
        ok, err, extra = False, None, {}
        held = False
        try:
            if self.app is not None:
                self._line("Senden gestoppt, Pluto für den Flasher freigegeben", "release")
                self.app.hold(True)
                held = True
            if action == "backup":
                extra["backup"] = self._backup()
                ok = True
            else:
                if action == "flash" and not self._has_backup():
                    self._line("Noch keine Sicherung der laufenden Firmware – lege sie zuerst an", "backup")
                    try:
                        extra["backup"] = self._backup()
                    except Exception as e:
                        raise RuntimeError(f"Sicherung fehlgeschlagen, Flashen abgebrochen: {e}")
                fw = self._dfu(img, "ram" if action == "ram_test" else "sf")
                extra["fw"] = fw
                if action == "ram_test":
                    s = self._state()
                    s.setdefault("ram_tested", {})[img["sha256"]] = {"at": dt.datetime.now().isoformat(timespec="seconds"), "fw": fw}
                    self._save_state(s)
                ok = True
        except Exception as e:
            err = f"{e}"
        finally:
            if held:
                self.app.hold(False)
        s = self._state()
        s.setdefault("history", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"), "action": action,
                                            "image": img and img["name"], "sha256": img and img["sha256"], "ok": ok, "error": err, **extra})
        self._save_state(s)
        self._line(("@@OK " if ok else "@@FAIL ") + (err or action), "done")
        with self.lock:
            self.job.update(running=False, result="ok" if ok else "fail", error=err, finished=time.time(), **extra)
        self.log("info" if ok else "error", f"Firmware {action}: {'ok' if ok else err}")

    # ---------------------------------------------------------------- Einzelschritte
    def _has_backup(self):
        return os.path.isdir(self.fwdir) and any(x.startswith("backup_") and x.endswith(".frm") for x in os.listdir(self.fwdir))

    def _ssh(self, cmd, timeout=30):
        if self.ssh is None:
            from device import Device
            d = Device(self.host)
            try:
                return d.run(cmd, timeout=timeout)
            finally:
                d.close()
        return self.ssh(cmd, timeout)

    def _backup(self):
        self._line("lese mtd3 (Firmware-Partition) per SSH …", "backup")
        rc, data, e = self._ssh("cat /dev/mtd3", timeout=180)
        if rc != 0 or len(data) < 1024:
            raise RuntimeError(f"mtd3 lesen fehlgeschlagen ({rc}): {e}")
        if data[:4] != b"\xd0\x0d\xfe\xed":
            raise RuntimeError("mtd3 enthält kein FIT-Image")
        n = struct.unpack(">I", data[4:8])[0]
        itb = bytes(data[:n])
        errs = check_itb(itb)
        if errs:
            raise RuntimeError("Sicherung ungültig: " + "; ".join(errs))
        fw = re.sub(r"[^\w.\-]", "_", self.fw_now() or "unbekannt")
        os.makedirs(self.fwdir, exist_ok=True)
        dst = os.path.join(self.fwdir, f"backup_{dt.datetime.now():%Y%m%d-%H%M}_{fw}.frm")
        with open(dst, "wb") as f:
            f.write(make_frm(itb))
        self._line(f"Sicherung: {os.path.basename(dst)} ({n // 1024} KiB, md5 {hashlib.md5(itb).hexdigest()[:12]}…)")
        return os.path.basename(dst)

    def _dfu_run(self, args, timeout):
        r = subprocess.run(self.dfu_cmd + ["-d", DFU_IDS] + args, capture_output=True, timeout=timeout, creationflags=NOWIN)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        return r.returncode, out

    def _wait_dfu(self, secs):
        t0 = time.time()
        while time.time() - t0 < secs * self.wait_scale:
            rc, out = self._dfu_run(["-l"], 20)
            if 'name="firmware.dfu"' in out:
                return True
            time.sleep(1.5 * self.wait_scale)
        return False

    def _dfu(self, img, mode):
        itb, errs = read_image(img["path"])
        if errs:
            raise RuntimeError("; ".join(errs))
        os.makedirs(self.fwdir, exist_ok=True)
        tmp = os.path.join(self.fwdir, "_upload.dfu")
        with open(tmp, "wb") as f:
            f.write(make_dfu(itb))
        self._line(f"Pluto in den DFU-Modus ({'RAM' if mode == 'ram' else 'Flash'}): device_reboot {mode}", "dfu_mode")
        try:
            self._ssh(f"sync; /usr/sbin/device_reboot {mode}", timeout=10)
        except Exception as e:
            self._line(f"SSH nicht möglich ({e}) – Pluto bitte mit gedrückter Taste einstecken (DFU-Modus)")
        if not self._wait_dfu(180):
            raise RuntimeError("kein DFU-Gerät gefunden (0456:b674). Pluto mit gedrückter Taste einstecken und erneut versuchen.")
        self._line(f"lade {img['name']} ({len(itb) // 1024} KiB) …", "download")
        rc, out = self._dfu_run(["-a", "firmware.dfu", "-D", tmp], 600)
        for s in out.splitlines()[-6:]:
            self._line(s)
        if rc != 0:
            raise RuntimeError(f"dfu-util Download fehlgeschlagen ({rc})")
        self._line("Detach – der Pluto startet neu", "boot")
        rc, out = self._dfu_run(["-a", "firmware.dfu", "-e"], 60)
        if rc != 0:
            raise RuntimeError("Detach fehlgeschlagen – der Pluto steht noch im DFU-Modus; Strom trennen stellt den alten Zustand her"
                               if mode == "ram" else "Detach fehlgeschlagen – Pluto vom Strom trennen und neu einstecken")
        try:
            os.remove(tmp)
        except OSError:
            pass
        fw = self._wait_boot(240 if mode == "sf" else 120)
        want = img.get("version")
        self._line(f"Pluto meldet sich mit Firmware {fw or '?'}", "verify")
        if want and fw and want != fw:
            raise RuntimeError(f"Pluto läuft mit {fw}, erwartet {want}")
        if not fw:
            raise RuntimeError("Pluto hat sich nach dem Neustart nicht gemeldet")
        return fw

    def _wait_boot(self, secs):
        t0 = time.time()
        time.sleep(8 * self.wait_scale)
        while time.time() - t0 < secs * self.wait_scale:
            fw = None
            if self.agent_url:
                try:
                    with urllib.request.urlopen(self.agent_url.rstrip("/") + "/api/status", timeout=3) as r:
                        a = json.loads(r.read())
                    fw = a.get("fw") or a.get("fw_version")
                except Exception:
                    fw = None
            if not fw:
                try:
                    rc, out, _ = self._ssh("cat /opt/VERSION 2>/dev/null || fw_printenv -n fw_version 2>/dev/null", timeout=8)
                    fw = out.decode("utf-8", "replace").strip().split()[0] if rc == 0 and out.strip() else None
                except Exception:
                    fw = None
            if fw:
                return fw
            time.sleep(3 * self.wait_scale)
        return None
