"""Firmware-Flasher für den ADALM-Pluto: Image prüfen → (Sicherung) → RAM-Test → Flashen, alles per DFU.

Ablauf wie pluto_fw/boot_ram.cmd und README „Flashen, Weg B“:
  ram_test: SSH „device_reboot ram“ → U-Boot-DFU (0456:b674) → dfu-util -a firmware.dfu -D <img>.dfu → -e (Detach)
            → U-Boot bootet das Image aus dem RAM, der Flash bleibt unberührt (Strom trennen = alte Firmware).
  flash:    nur nach bestandenem RAM-Test DESSELBEN Images (SHA-256) und mit Bestätigung; vorher automatische Sicherung
            von mtd3; SSH „device_reboot sf“ → dfu-util schreibt mtd3 → Detach → Neustart. U-Boot/Env (mtd0/1) nie.
  backup:   SSH „cat /dev/mtd3“ → FIT (Länge aus dem FDT-Kopf) → runtime/firmware/backup_<datum>_<fw>.frm (+ md5).
Ist der Pluto per SSH nicht erreichbar, wartet der Flasher bis zu 3 min auf ein DFU-Gerät (Taste beim Einstecken halten).
Images: nur die mitgelieferte, am Gerät geprüfte Firmware (pluto_fw/firmware) und die eigenen Sicherungen (Rückweg).

Geräteauswahl (board, vom Nutzer gewählt, in flasher.json gespeichert):
  pluto         ADALM-Pluto: Ablauf oben (DFU, RAM-Test, Flash).
  fishball7010  Fishball/PlutoSky Zynq 7010 (AD9363): Muxwright auf Tezuka-Basis, startet nur von SD-Karte.
                sd_write kopiert sdimg/ auf einen Wechseldatenträger (FAT32); vorhandene gleichnamige Dateien werden
                vorher nach runtime/firmware/sd_backup_<datum>/ gesichert, danach wird jede Datei per SHA-256 geprüft.
                Der QSPI-Flash des Boards wird nie angefasst.
"""
import ctypes, datetime as dt, hashlib, json, os, re, shutil, struct, subprocess, threading, time, urllib.request, zlib

NOWIN = 0x08000000 if os.name == "nt" else 0
MAGIC = b"ITB PlutoSDR (ADALM-PLUTO)"
MTD3_MAX = 0x1E00000          # 30 MiB
DFU_IDS = "0456:b673,0456:b674"
BOARDS = {
    "pluto": {"method": "dfu"},
    "fishball7010": {"method": "sd", "dirs": (("pluto_fw", "firmware", "fishball7010"), ("pluto_fw", "out", "tezuka", "fishball"))},
}
SD_REQUIRED = ("BOOT.bin", "uImage", "uramdisk.image.xz", "devicetree.dtb", "uEnv.txt")
_SHA_CACHE = {}               # (Pfad, Größe, mtime_ns) -> SHA-256 der mitgelieferten Image-Dateien (nicht der Karte)


def sd_image(root, board):
    """Mitgeliefertes SD-Image eines Boards → dict (ok, errors, version, files[(rel, sha256, size)], dir) oder None."""
    for parts in BOARDS[board].get("dirs", ()):
        d = os.path.join(root, *parts)
        if not os.path.isdir(os.path.join(d, "sdimg")):
            continue
        errs, files = [], []
        sums = {}
        try:
            for ln in open(os.path.join(d, "SHA256SUMS"), encoding="utf-8"):
                m = re.match(r"([0-9a-f]{64})\s+\*?\.?/?(sdimg/\S+)", ln.strip())
                if m:
                    sums[m.group(2)] = m.group(1)
        except OSError:
            errs.append("SHA256SUMS missing")
        for n in SD_REQUIRED:
            if f"sdimg/{n}" not in sums:
                errs.append(f"{n} missing")
        for rel, want in sorted(sums.items()):
            p = os.path.join(d, *rel.split("/"))
            if not os.path.isfile(p):
                errs.append(f"{rel} missing")
                continue
            st = os.stat(p)
            key = (p, st.st_size, st.st_mtime_ns)
            if key not in _SHA_CACHE:
                _SHA_CACHE[key] = file_sha256(p)
            if _SHA_CACHE[key] != want:
                errs.append(f"{rel}: SHA-256 mismatch")
            files.append((rel[len("sdimg/"):], want, os.path.getsize(p)))
        return {"dir": d, "ok": not errs, "errors": errs, "version": sidecar_version(os.path.join(d, "x")),
                "files": files, "size": sum(f[2] for f in files)}
    return None


def file_sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def removable_drives():
    """Windows: Wechseldatenträger → [{drive: 'E:', label, fs, size, free}]; andere Systeme: []."""
    if os.name != "nt":
        return []
    k = ctypes.windll.kernel32
    out, mask = [], k.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        if k.GetDriveTypeW(root) != 2:                  # DRIVE_REMOVABLE
            continue
        label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
        if not k.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261):
            continue                                    # kein Medium eingelegt
        free, total = ctypes.c_ulonglong(), ctypes.c_ulonglong()
        k.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), ctypes.byref(free))
        out.append({"drive": root[:2], "root": root, "label": label.value, "fs": fs.value, "size": total.value, "free": free.value})
    return out


def make_dfu(itb):
    body = itb + struct.pack("<HHHH", 0xFFFF, 0xB673, 0x0456, 0x0100) + b"UFD" + bytes([16])
    return body + struct.pack("<I", zlib.crc32(body) ^ 0xFFFFFFFF)


def make_frm(itb):
    return itb + hashlib.md5(itb).hexdigest().encode() + b"\n"


def check_itb(itb):
    """-> Liste der Fehler (leer = ok)."""
    err = []
    if itb[:4] != b"\xd0\x0d\xfe\xed":
        err.append("not a FIT image (FDT magic missing)")
    elif struct.unpack(">I", itb[4:8])[0] != len(itb):
        err.append("FIT length mismatch")
    if MAGIC not in itb[:8192]:
        err.append("not a Pluto firmware (\"ITB PlutoSDR (ADALM-PLUTO)\" missing)")
    if len(itb) > MTD3_MAX:
        err.append(f"too large for the flash ({len(itb) // 1048576} MiB > 30 MiB)")
    return err


def read_image(path):
    """.frm (FIT + md5) oder .dfu (FIT + DFU-Suffix) → (itb, fehler)"""
    data = open(path, "rb").read()
    if path.lower().endswith(".frm"):
        itb, tail = data[:-33], data[-33:]
        if not re.fullmatch(rb"[0-9a-f]{32}\n", tail) or hashlib.md5(itb).hexdigest().encode() != tail[:32]:
            return itb, ["md5 checksum of the .frm file does not match (file damaged?)"]
    elif path.lower().endswith(".dfu"):
        itb, suf = data[:-16], data[-16:]
        if suf[8:11] != b"UFD" or make_dfu(itb) != data:
            return itb, ["DFU suffix/CRC of the .dfu file does not match"]
        if suf[2:6] != struct.pack("<HH", 0xB673, 0x0456):
            return itb, ["DFU file is not for the Pluto (USB ID)"]
    else:
        return b"", ["only .frm or .dfu"]
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
        self.drives = removable_drives                  # austauschbar für Tests
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
        board = s.get("board")
        sd = {}
        for b, spec in BOARDS.items():
            if spec["method"] == "sd":
                im = sd_image(self.root, b)
                sd[b] = im and {k: v for k, v in im.items() if k != "dir"}
        return {"images": self.images(), "job": job, "running_fw": self.fw_now(), "history": s.get("history", [])[-10:],
                "user_dir": self.fwdir, "board": board, "boards": {b: v["method"] for b, v in BOARDS.items()},
                "sd_images": sd, "sd_drives": self.drives()}

    def set_board(self, board):
        if board not in BOARDS:
            return 400, {"ok": False, "errors": ["unknown board"]}
        s = self._state()
        s["board"] = board
        self._save_state(s)
        return 200, {"ok": True, "board": board}

    # ---------------------------------------------------------------- Aufträge
    def start(self, action, image_id=None, confirm=False, drive=None, board=None):
        if action == "set_board":
            return self.set_board(board)
        if action == "sd_write":
            return self._start_sd(board or self._state().get("board"), drive, confirm)
        if action not in ("ram_test", "flash", "backup"):
            return 400, {"ok": False, "errors": ["unknown action"]}
        img = None
        if action != "backup":
            img = next((i for i in self.images() if i["id"] == image_id), None)
            if img is None:
                return 404, {"ok": False, "errors": ["image not found"]}
            if not img["ok"]:
                return 400, {"ok": False, "errors": img["errors"]}
        if action == "flash":
            if not img["ram_tested"] and img["kind"] != "backup":   # Sicherung = lief schon auf diesem Gerät
                return 409, {"ok": False, "errors": ["Pass the RAM test with exactly this image first, then flash"]}
            if confirm is not True:
                return 400, {"ok": False, "errors": ["Flashing must be confirmed explicitly (confirm: true)"]}
        with self.lock:
            if self.job["running"]:
                return 409, {"ok": False, "errors": ["A firmware job is already running"]}
            self.job = {"running": True, "action": action, "image": img and img["id"], "phase": "start", "lines": [], "result": None,
                        "error": None, "started": time.time(), "finished": None}
        threading.Thread(target=self._run, args=(action, img), daemon=True, name=f"fw-{action}").start()
        return 200, {"ok": True}

    def _start_sd(self, board, drive, confirm):
        if BOARDS.get(board, {}).get("method") != "sd":
            return 400, {"ok": False, "errors": ["this board has no SD card image"]}
        im = sd_image(self.root, board)
        if im is None:
            return 404, {"ok": False, "errors": ["SD card image not found"]}
        if not im["ok"]:
            return 400, {"ok": False, "errors": im["errors"]}
        d = next((x for x in self.drives() if x["drive"].upper() == str(drive or "").upper()), None)
        if d is None:
            return 400, {"ok": False, "errors": ["choose a removable drive (SD card)"]}
        if d["fs"].upper() not in ("FAT32", "FAT"):
            return 400, {"ok": False, "errors": [f"{d['drive']} is {d['fs'] or 'unformatted'} – format the card as FAT32 first"]}
        if confirm is not True:
            return 400, {"ok": False, "errors": ["Writing the SD card must be confirmed explicitly (confirm: true)"]}
        with self.lock:
            if self.job["running"]:
                return 409, {"ok": False, "errors": ["A firmware job is already running"]}
            self.job = {"running": True, "action": "sd_write", "image": board, "phase": "start", "lines": [], "result": None,
                        "error": None, "started": time.time(), "finished": None}
        threading.Thread(target=self._run_sd, args=(board, im, d), daemon=True, name="fw-sd").start()
        return 200, {"ok": True}

    def _run_sd(self, board, im, d):
        ok, err, extra = False, None, {}
        try:
            extra["drive"] = d["drive"]
            dst = d.get("root") or d["drive"] + "\\"
            need = im["size"] - sum(os.path.getsize(os.path.join(dst, *rel.split("/")))
                                    for rel, _, _ in im["files"] if os.path.isfile(os.path.join(dst, *rel.split("/"))))
            if need > d["free"] - 1048576:
                raise RuntimeError(f"not enough space on {d['drive']} ({need // 1048576} MiB needed)")
            old = [rel for rel, _, _ in im["files"] if os.path.isfile(os.path.join(dst, *rel.split("/")))]
            if old:
                bdir = os.path.join(self.fwdir, f"sd_backup_{dt.datetime.now():%Y%m%d-%H%M%S}_{d['drive'][0]}")
                self._line(f"Backing up {len(old)} existing file(s) from {d['drive']} to {bdir}", "backup")
                for rel in old:
                    t = os.path.join(bdir, *rel.split("/"))
                    os.makedirs(os.path.dirname(t), exist_ok=True)
                    shutil.copy2(os.path.join(dst, *rel.split("/")), t)
                extra["sd_backup"] = os.path.basename(bdir)
            self._line(f"Writing {im['version'] or board} to {d['drive']} ({d['label'] or 'no label'}, {d['fs']}) …", "copy")
            for rel, _, size in im["files"]:
                src = os.path.join(im["dir"], "sdimg", *rel.split("/"))
                t = os.path.join(dst, *rel.split("/"))
                os.makedirs(os.path.dirname(t), exist_ok=True)
                with open(src, "rb") as fi, open(t, "wb") as fo:
                    shutil.copyfileobj(fi, fo, 1 << 20)
                    fo.flush()
                    os.fsync(fo.fileno())
                self._line(f"  {rel} ({size // 1024} KiB)")
            self._line("Verifying (SHA-256) …", "verify")
            bad = [rel for rel, want, _ in im["files"] if file_sha256(os.path.join(dst, *rel.split("/"))) != want]
            if bad:
                raise RuntimeError("verification failed: " + ", ".join(bad))
            self._line(f"{len(im['files'])} files verified. Eject the card safely, insert it into the board and power it on.")
            extra["fw"] = im["version"]
            ok = True
        except Exception as e:
            err = f"{e}"
        s = self._state()
        s.setdefault("history", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"), "action": "sd_write",
                                            "image": board, "ok": ok, "error": err, **extra})
        self._save_state(s)
        self._line(("@@OK " if ok else "@@FAIL ") + (err or "sd_write"), "done")
        with self.lock:
            self.job.update(running=False, result="ok" if ok else "fail", error=err, finished=time.time(), **extra)
        self.log("info" if ok else "error", f"Firmware sd_write {board}: {'ok' if ok else err}")

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
                self._line("Transmission stopped, Pluto released for the flasher", "release")
                self.app.hold(True)
                held = True
            if action == "backup":
                extra["backup"] = self._backup()
                ok = True
            else:
                if action == "flash" and not self._has_backup():
                    self._line("No backup of the running firmware yet – creating it first", "backup")
                    try:
                        extra["backup"] = self._backup()
                    except Exception as e:
                        raise RuntimeError(f"Backup failed, flashing aborted: {e}")
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
        self._line("reading mtd3 (firmware partition) via SSH …", "backup")
        rc, data, e = self._ssh("cat /dev/mtd3", timeout=180)
        if rc != 0 or len(data) < 1024:
            raise RuntimeError(f"reading mtd3 failed ({rc}): {e}")
        if data[:4] != b"\xd0\x0d\xfe\xed":
            raise RuntimeError("mtd3 does not contain a FIT image")
        n = struct.unpack(">I", data[4:8])[0]
        itb = bytes(data[:n])
        errs = check_itb(itb)
        if errs:
            raise RuntimeError("backup invalid: " + "; ".join(errs))
        fw = re.sub(r"[^\w.\-]", "_", self.fw_now() or "unknown")
        os.makedirs(self.fwdir, exist_ok=True)
        dst = os.path.join(self.fwdir, f"backup_{dt.datetime.now():%Y%m%d-%H%M}_{fw}.frm")
        with open(dst, "wb") as f:
            f.write(make_frm(itb))
        self._line(f"Backup: {os.path.basename(dst)} ({n // 1024} KiB, md5 {hashlib.md5(itb).hexdigest()[:12]}…)")
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
        self._line(f"Pluto into DFU mode ({'RAM' if mode == 'ram' else 'Flash'}): device_reboot {mode}", "dfu_mode")
        try:
            self._ssh(f"sync; /usr/sbin/device_reboot {mode}", timeout=10)
        except Exception as e:
            self._line(f"SSH not possible ({e}) – please plug in the Pluto with the button held (DFU mode)")
        if not self._wait_dfu(180):
            raise RuntimeError("no DFU device found (0456:b674). Plug in the Pluto with the button held and try again.")
        self._line(f"uploading {img['name']} ({len(itb) // 1024} KiB) …", "download")
        rc, out = self._dfu_run(["-a", "firmware.dfu", "-D", tmp], 600)
        for s in out.splitlines()[-6:]:
            self._line(s)
        if rc != 0:
            raise RuntimeError(f"dfu-util download failed ({rc})")
        self._line("Detach – the Pluto reboots", "boot")
        rc, out = self._dfu_run(["-a", "firmware.dfu", "-e"], 60)
        if rc != 0:
            raise RuntimeError("Detach failed – the Pluto is still in DFU mode; disconnecting power restores the previous state"
                               if mode == "ram" else "Detach failed – disconnect the Pluto from power and plug it in again")
        try:
            os.remove(tmp)
        except OSError:
            pass
        fw = self._wait_boot(240 if mode == "sf" else 120)
        want = img.get("version")
        self._line(f"Pluto reports firmware {fw or '?'}", "verify")
        if want and fw and want != fw:
            raise RuntimeError(f"Pluto runs {fw}, expected {want}")
        if not fw:
            raise RuntimeError("Pluto did not respond after the reboot (no USB network at 192.168.2.1)")
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
