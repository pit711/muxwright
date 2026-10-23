"""Komponenten, die der Nutzer in der Einrichtung selbst installiert (lizenzrechtlich heikle Teile kommen nicht mit Muxwright,
sondern aus den offiziellen Quellen – erst nach ausdrücklicher Bestätigung je Komponente).

  status()                      -> {"wsl": {...}, "components": [{key, installed, ...}], "job": {...}}
  install(key, accepted=True)   -> startet die Installation im Hintergrund (eine zur Zeit)
Bestätigungen stehen mit Zeitstempel in runtime/components.json (Nachweis).
"""
import datetime as dt, io, json, os, re, shutil, subprocess, threading, time, urllib.request, zipfile

NOWIN = 0x08000000 if os.name == "nt" else 0
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "wsl", "setup_components.sh")
WIN_FFMPEG_DIR = os.path.join(ROOT, "tools", "ffmpeg")
WIN_FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                  "ffmpeg-n7.1-latest-win64-lgpl-7.1.zip")   # n7.1: NVENC-SDK ≤ 12.2, läuft auch mit Treiberzweig 580 (GTX 9xx/10xx)

# key, Lizenz, Quelle, optional, Abhängigkeiten
COMPONENTS = [
    {"key": "wsl_distro", "license": "Ubuntu: freie Software (u. a. GPL)", "source": "https://ubuntu.com/desktop/wsl", "optional": False, "needs": []},
    {"key": "base", "license": "Ubuntu-Pakete (GPL, BSD, MIT u. a.)", "source": "https://packages.ubuntu.com/noble/", "optional": False, "needs": ["wsl_distro"]},
    {"key": "ffmpeg", "license": "LGPL-2.1+/GPL-2.0+ (mit x264/x265: GPL)", "source": "https://packages.ubuntu.com/noble/ffmpeg", "optional": False,
     "needs": ["wsl_distro"], "patents": True},
    {"key": "tsduck", "license": "BSD-2-Clause", "source": "https://github.com/tsduck/tsduck", "optional": False, "needs": ["base"]},
    {"key": "odr", "license": "GPL-3.0 (ODR-mmbTools) + FDK-AAC-Lizenz (Fraunhofer, ohne Patentlizenz)",
     "source": "https://github.com/Opendigitalradio", "optional": False, "needs": ["base"], "patents": True},
    {"key": "welle", "license": "GPL-2.0 (welle.io) + MIT (redsea)", "source": "https://github.com/AlbrechtL/welle.io", "optional": False,
     "needs": ["base"]},
    {"key": "modulators", "license": "MIT (Muxwright, eigener Code)", "source": "pluto_fw/mod", "optional": False, "needs": ["base"]},
    {"key": "ytdlp", "license": "Unlicense", "source": "https://github.com/yt-dlp/yt-dlp", "optional": True, "needs": ["base"]},
    {"key": "win_ffmpeg", "license": "LGPL-2.1+ (BtbN-Build, ohne x264/x265)", "source": "https://github.com/BtbN/FFmpeg-Builds", "optional": True,
     "needs": [], "patents": True},
]
WSL_KEYS = ("base", "ffmpeg", "tsduck", "odr", "welle", "modulators", "ytdlp")


def win2wsl(p):
    p = os.path.abspath(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m else p


class Setup:
    def __init__(self, distro="dablab", runtime=None, log=None, distro_dir=None):
        self.distro = distro
        self.runtime = runtime or os.path.join(ROOT, "runtime")
        self.distro_dir = distro_dir or os.path.join(ROOT, "wsl", "fs")
        self.log = log or (lambda lvl, msg: None)
        self.lock = threading.Lock()
        self.job = {"running": False, "key": None, "lines": [], "result": None, "error": None, "started": None, "finished": None}
        self._cache, self._cache_t = None, 0

    # ---------------------------------------------------------------- Zustimmungen (Nachweis)
    @property
    def consent_file(self):
        return os.path.join(self.runtime, "components.json")

    def consents(self):
        try:
            with open(self.consent_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _consent(self, key):
        c = self.consents()
        meta = next(x for x in COMPONENTS if x["key"] == key)
        c[key] = {"accepted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "license": meta["license"],
                  "source": meta["source"], "patent_note": bool(meta.get("patents"))}
        os.makedirs(self.runtime, exist_ok=True)
        tmp = self.consent_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.consent_file)

    # ---------------------------------------------------------------- Status
    def wsl_state(self):
        try:
            r = subprocess.run(["wsl", "--status"], capture_output=True, timeout=20, creationflags=NOWIN)
            ok = r.returncode == 0
        except Exception:
            ok = False
        names = []
        if ok:
            try:
                r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=20, creationflags=NOWIN)
                names = r.stdout.decode("utf-16-le", "ignore").replace("\x00", "").split()
            except Exception:
                pass
        return {"installed": ok, "distro": self.distro, "distro_ok": self.distro in names}

    def win_ffmpeg(self):
        for p in (os.environ.get("DABLAB_WIN_FFMPEG", ""), r"C:\ffmpeg\bin\ffmpeg.exe", os.path.join(WIN_FFMPEG_DIR, "bin", "ffmpeg.exe")):
            if p and os.path.isfile(p):
                return p
        return None

    def status(self, fresh=False):
        if not fresh and self._cache and time.time() - self._cache_t < 20 and not self.job["running"]:
            return dict(self._cache, job=self._job())
        w = self.wsl_state()
        inst = {}
        if w["distro_ok"] and os.path.isfile(SCRIPT):
            try:
                r = subprocess.run(["wsl", "-d", self.distro, "-u", "root", "--", "bash", win2wsl(SCRIPT), win2wsl(ROOT), "check"],
                                   capture_output=True, timeout=120, creationflags=NOWIN)
                m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"))
                inst = json.loads(m.group(0)) if m else {}
            except Exception:
                inst = {}
        cons = self.consents()
        comps = []
        for c in COMPONENTS:
            k = c["key"]
            installed = w["distro_ok"] if k == "wsl_distro" else (self.win_ffmpeg() is not None) if k == "win_ffmpeg" else bool(inst.get(k))
            comps.append(dict(c, installed=installed, accepted=k in cons, accepted_at=(cons.get(k) or {}).get("accepted_at")))
        req = [c for c in comps if not c["optional"]]
        self._cache = {"wsl": w, "components": comps, "ubuntu": inst.get("ubuntu"), "complete": all(c["installed"] for c in req)}
        self._cache_t = time.time()
        return dict(self._cache, job=self._job())

    def _job(self):
        with self.lock:
            j = dict(self.job)
            j["lines"] = j["lines"][-40:]
            return j

    # ---------------------------------------------------------------- Installation
    def install(self, key, accepted=False):
        meta = next((x for x in COMPONENTS if x["key"] == key), None)
        if not meta:
            return 400, {"ok": False, "errors": [f"unbekannte Komponente {key}"]}
        if not accepted:
            return 400, {"ok": False, "errors": ["Lizenz/Quelle der Komponente muss bestätigt werden (accepted: true)"]}
        with self.lock:
            if self.job["running"]:
                return 409, {"ok": False, "errors": [f"Installation von {self.job['key']} läuft noch"]}
            self.job = {"running": True, "key": key, "lines": [], "result": None, "error": None, "started": time.time(), "finished": None}
        self._consent(key)
        self.log("info", f"Komponente {key}: Lizenz bestätigt, Installation gestartet ({meta['source']})")
        threading.Thread(target=self._run, args=(key,), daemon=True, name=f"setup-{key}").start()
        return 200, {"ok": True, "job": self._job()}

    def _line(self, s):
        with self.lock:
            self.job["lines"].append(s.rstrip()[:300])
            self.job["lines"] = self.job["lines"][-400:]

    def _finish(self, ok, err=None):
        with self.lock:
            self.job.update(running=False, result="ok" if ok else "fail", error=err, finished=time.time())
        self._cache = None
        self.log("info" if ok else "error", f"Komponente {self.job['key']}: {'installiert' if ok else 'fehlgeschlagen – ' + str(err)}")

    def _run(self, key):
        try:
            if key == "wsl_distro":
                return self._finish(*self._install_distro())
            if key == "win_ffmpeg":
                return self._finish(*self._install_win_ffmpeg())
            if not self.wsl_state()["distro_ok"]:
                return self._finish(False, "zuerst die Linux-Umgebung (WSL) installieren")
            cmd = ["wsl", "-d", self.distro, "-u", "root", "--", "bash", win2wsl(SCRIPT), win2wsl(ROOT), key]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NOWIN)
            ok, err = False, None
            for raw in iter(p.stdout.readline, b""):
                s = raw.decode("utf-8", "replace")
                self._line(s)
                if s.startswith("@@OK"):
                    ok = True
                elif s.startswith("@@FAIL"):
                    err = s.split(" ", 2)[-1].strip()
            p.wait()
            self._finish(ok and p.returncode == 0, err or (None if ok else f"Rückgabewert {p.returncode}"))
        except Exception as e:
            self._finish(False, f"{type(e).__name__}: {e}")

    def _install_distro(self):
        w = self.wsl_state()
        if not w["installed"]:
            return False, "WSL ist nicht installiert: als Administrator „wsl --install --no-distribution“ ausführen und neu starten"
        if w["distro_ok"]:
            self._line("@@OK wsl_distro (vorhanden)")
            return True, None
        os.makedirs(self.distro_dir, exist_ok=True)
        cmd = ["wsl", "--install", "Ubuntu-24.04", "--name", self.distro, "--location", self.distro_dir, "--no-launch", "--web-download"]
        self._line("@@STEP " + " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=NOWIN)
        out = (r.stdout + r.stderr).decode("utf-16-le", "ignore").replace("\x00", "")
        for s in out.splitlines():
            if s.strip():
                self._line(s)
        if not self.wsl_state()["distro_ok"]:
            return False, f"wsl --install fehlgeschlagen ({r.returncode})"
        r = subprocess.run(["wsl", "-d", self.distro, "-u", "root", "--", "true"], capture_output=True, timeout=300, creationflags=NOWIN)
        self._line("@@OK wsl_distro")
        return r.returncode == 0, None if r.returncode == 0 else "Erststart der Linux-Umgebung fehlgeschlagen"

    def _install_win_ffmpeg(self):
        if self.win_ffmpeg():
            self._line("@@OK win_ffmpeg (vorhanden)")
            return True, None
        self._line(f"@@STEP lade {WIN_FFMPEG_URL}")
        req = urllib.request.Request(WIN_FFMPEG_URL, headers={"User-Agent": "Muxwright-Setup"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
        self._line(f"@@STEP entpacke {len(data) // 1048576} MB nach {WIN_FFMPEG_DIR}")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            top = z.namelist()[0].split("/")[0]
            tmp = WIN_FFMPEG_DIR + ".tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            z.extractall(tmp)
        shutil.rmtree(WIN_FFMPEG_DIR, ignore_errors=True)
        os.replace(os.path.join(tmp, top), WIN_FFMPEG_DIR)
        shutil.rmtree(tmp, ignore_errors=True)
        ok = os.path.isfile(os.path.join(WIN_FFMPEG_DIR, "bin", "ffmpeg.exe"))
        if ok:
            self._line("@@OK win_ffmpeg")
        return ok, None if ok else "ffmpeg.exe fehlt nach dem Entpacken"
