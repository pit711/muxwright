"""PC-Hardware für die Einrichtung: NVIDIA-GPU (NVENC-Generation), CPU, RAM erkennen und bewerten; optional kurzer
Leistungstest mit dem Windows-ffmpeg (x264 1080p, NVENC HEVC 2160p). Bewertung nach der Anforderungsmatrix
(NVIDIA Video Encode/Decode Support Matrix, NVENC Application Note SDK 13.1, OpenBenchmarking x264/x265).

  detect()          -> {"gpu": [...], "cpu": {...}, "ram_gb": .., "caps": [...], "recommend": [...]}
  detect(bench=True) zusätzlich "bench": {"x264_1080p_fps": .., "nvenc_hevc_2160p_fps": ..}
"""
import json, os, re, shutil, subprocess, time

NOWIN = 0x08000000 if os.name == "nt" else 0
_LOCAL_FFMPEG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFMPEG_WIN = os.environ.get("DABLAB_WIN_FFMPEG") or (r"C:\ffmpeg\bin\ffmpeg.exe" if os.path.isfile(r"C:\ffmpeg\bin\ffmpeg.exe") else _LOCAL_FFMPEG)

# NVENC-Generationen (Rang: höher = mehr Funktionen)
GENS = [  # (Muster im GPU-Namen, Generation, Rang)
    (r"RTX\s*50\d\d|RTX PRO \d+ Blackwell", "Blackwell", 8),
    (r"RTX\s*40\d\d|RTX \d+ Ada|L4\b|L40", "Ada", 7),
    (r"RTX\s*30\d\d|RTX\s*2050|RTX A\d+|A\d0\b", "Ampere", 6),
    (r"RTX\s*20\d\d|GTX\s*16[56]0\s*(Ti|Super)|GTX\s*1660|TITAN RTX|Quadro RTX|T\d00\b", "Turing", 5),
    (r"GTX\s*1650|GTX\s*1630", "Turing (ältere NVENC)", 4),
    (r"GTX\s*10[5-8]0|TITAN Xp?|Quadro P\d+|GT\s*1030", "Pascal", 3),
    (r"GTX\s*9[5-8]0|GTX\s*TITAN X|Quadro M\d+", "Maxwell 2", 2),
    (r"GTX\s*7(45|50)", "Maxwell 1", 1),
    (r"GTX\s*[67]\d\d|GT\s*[67]\d\d", "Kepler", 0),
]


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NOWIN)
        return r.stdout
    except Exception:
        return ""


def gpus():
    out = []
    smi = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    txt = _run([smi, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"]) if os.path.exists(smi) or shutil.which("nvidia-smi") else ""
    for line in txt.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 3:
            name = p[0]
            gen, rank = "unbekannt", -1
            for rx, g, r in GENS:
                if re.search(rx, name, re.I):
                    gen, rank = g, r
                    break
            try:
                drv = float(p[1])
            except ValueError:
                drv = 0.0
            out.append({"name": name, "driver": p[1], "driver_major": int(drv), "vram_gb": round(float(p[2] or 0) / 1024, 1),
                        "nvenc_gen": gen, "rank": rank, "vendor": "NVIDIA"})
    if not out and os.name == "nt":   # andere Grafik (Intel/AMD) nur zur Anzeige
        txt = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
        for name in [x.strip() for x in txt.splitlines() if x.strip()]:
            out.append({"name": name, "vendor": "other", "nvenc_gen": None, "rank": -1})
    return out


def cpu_ram():
    if os.name != "nt":
        return {"name": "?", "cores": os.cpu_count() or 1, "threads": os.cpu_count() or 1, "mhz": 0}, 0
    ps = ("$c=Get-CimInstance Win32_Processor|Select -First 1; $m=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
          "@{name=$c.Name;cores=$c.NumberOfCores;threads=$c.NumberOfLogicalProcessors;mhz=$c.MaxClockSpeed;ram=$m}|ConvertTo-Json -Compress")
    try:
        d = json.loads(_run(["powershell", "-NoProfile", "-Command", ps]) or "{}")
    except ValueError:
        d = {}
    cpu = {"name": (d.get("name") or "?").strip(), "cores": int(d.get("cores") or 0), "threads": int(d.get("threads") or 0), "mhz": int(d.get("mhz") or 0)}
    return cpu, round(int(d.get("ram") or 0) / 2 ** 30)


def _bench_ffmpeg(args, frames, timeout=60):
    """-> fps (frames / Wandzeit) oder None."""
    if not os.path.exists(FFMPEG_WIN):
        return None
    t0 = time.time()
    try:
        r = subprocess.run([FFMPEG_WIN, "-hide_banner", "-nostats", "-loglevel", "error"] + args + ["-frames:v", str(frames), "-f", "null", "-"],
                           capture_output=True, text=True, timeout=timeout, creationflags=NOWIN)
    except Exception:
        return None
    dt = time.time() - t0
    return round(frames / dt, 1) if r.returncode == 0 and dt > 0 else None


def bench(has_nvenc):
    res = {}
    # CPU: x264 1080p25 veryfast (wie live HD), 250 Bilder Testbild
    res["x264_1080p_fps"] = _bench_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25", "-pix_fmt", "yuv420p",
                                           "-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M"], 250)
    if has_nvenc:
        res["nvenc_hevc_2160p_fps"] = _bench_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=25", "-pix_fmt", "yuv420p",
                                                     "-c:v", "hevc_nvenc", "-preset", "p4", "-b:v", "16M"], 150)
        res["nvenc_hevc10_2160p_fps"] = _bench_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=25", "-pix_fmt", "p010le",
                                                       "-c:v", "hevc_nvenc", "-profile:v", "main10", "-preset", "p4", "-b:v", "16M"], 100)
    return res


def rate(gpu_list, cpu, ram_gb, b=None):
    """Fähigkeiten (ok | warn | no) und ehrliche Empfehlungen – nur wenn die Hardware unter der Empfehlung liegt."""
    nv = max((g for g in gpu_list if g.get("vendor") == "NVIDIA"), key=lambda g: g.get("rank", -1), default=None)
    rank = nv["rank"] if nv else -1
    th = cpu.get("threads") or 0
    b = b or {}
    x264 = b.get("x264_1080p_fps")
    hevc = b.get("nvenc_hevc_2160p_fps")
    hevc10 = b.get("nvenc_hevc10_2160p_fps")
    caps = []

    def cap(k, st, how):
        caps.append({"k": k, "status": st, "how": how})

    cap("dab_fm", "ok" if th >= 2 else "warn", "cpu")
    cap("sd", "ok" if th >= 4 else "warn", "cpu")
    # HD H.264 live
    if rank >= 1 and (hevc is None or hevc > 0):
        cap("hd", "ok", "nvenc")
    elif x264 is not None:
        # Testbild ist leicht zu kodieren; reales Material braucht grob 3–4× mehr → erst ab ~100 fps Reserve
        cap("hd", "ok" if x264 >= 100 else "warn" if x264 >= 55 else "no", "cpu")
    else:
        cap("hd", "ok" if th >= 12 else "warn", "cpu")
    # UHD HEVC 8 Bit
    if rank >= 2:
        cap("uhd", "ok" if (hevc is None or hevc >= 30) and rank >= 3 else "warn", "nvenc")
    else:
        cap("uhd", "warn" if th >= 32 else "no", "cpu")
    # UHD HDR10 (HEVC Main10)
    if rank >= 3:
        cap("hdr10", "ok" if (hevc10 is None or hevc10 >= 30) else "warn", "nvenc")
    else:
        cap("hdr10", "warn" if th >= 32 else "no", "cpu")
    cap("datv", "ok" if (rank >= 1 or th >= 8) else "warn", "nvenc" if rank >= 1 else "cpu")
    cap("multi", "ok" if (rank >= 3 and ram_gb >= 16) else "warn" if (rank >= 1 or th >= 16) else "no", "nvenc" if rank >= 1 else "cpu")
    rec = []
    if rank < 5:
        rec.append({"k": "gpu", "why": "uhd_hdr" if rank < 3 else "reserve"})
    if ram_gb and ram_gb < 16:
        rec.append({"k": "ram", "why": "ram_low"})
    if th < 12 and rank < 1:
        rec.append({"k": "cpu", "why": "cpu_low"})
    weak = any(c["status"] != "ok" for c in caps if c["k"] in ("hd", "uhd", "multi"))
    return caps, rec, weak, nv


_CACHE = {}


def detect(bench_run=False):
    if not bench_run and _CACHE.get("base") and time.time() - _CACHE["t"] < 300:
        return _CACHE["base"]
    g = gpus()
    cpu, ram = cpu_ram()
    b = bench(any(x.get("vendor") == "NVIDIA" and x.get("rank", -1) >= 1 for x in g)) if bench_run else (_CACHE.get("bench") or None)
    caps, rec, weak, nv = rate(g, cpu, ram, b)
    out = {"gpu": g, "nvenc": nv, "cpu": cpu, "ram_gb": ram, "caps": caps, "recommend": rec, "weak": weak, "bench": b, "t": time.time(),
           "ffmpeg": os.path.exists(FFMPEG_WIN)}
    if bench_run:
        _CACHE["bench"] = b
    _CACHE.update(base=out, t=time.time())
    return out
