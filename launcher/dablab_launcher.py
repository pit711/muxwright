r"""DABLab.exe – startet den DAB-Lab-Server und öffnet das Dashboard.

Liegt als DABLab.exe im Ordner dablab\ (neben pc\, web\, runner\). Der Server-Code wird zur Laufzeit aus pc\ geladen,
damit Änderungen dort ohne Neubau der .exe wirken. Python, numpy, scipy und pylibiio stecken in der .exe.
Fenster schließen = Server beenden; der Pluto wird dabei stummgeschaltet (−89,75 dB).
"""
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

# PyInstaller soll diese Pakete mitnehmen (der Server importiert sie zur Laufzeit)
import http.server, json, mimetypes, shlex, shutil, queue, collections, copy, traceback, argparse, atexit  # noqa: E401,F401
import zipfile, io, urllib.request, datetime, csv, hashlib  # noqa: E401,F401  (pc/setup.py, hwcheck.py, shop.py)
import datetime, re, signal, urllib.parse, ctypes.util  # noqa: E401,F401
import numpy  # noqa: F401
import scipy.signal  # noqa: F401

def _arg(name, default):
    a = sys.argv[1:]
    for i, v in enumerate(a):
        if v == name and i + 1 < len(a):
            return a[i + 1]
        if v.startswith(name + "="):
            return v.split("=", 1)[1]
    return default


PORT = int(_arg("--port", 8792))
URL = f"http://127.0.0.1:{PORT}/"
BASE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__)))
PC = os.path.join(BASE, "pc")
LIBIIO = os.environ.get("DABLAB_LIBIIO_DIR") or os.path.join(PC, "libiio")
NO_WINDOW = 0x08000000


def msg(text, title="DAB-Lab", icon=0x10):
    ctypes.windll.user32.MessageBoxW(None, text, title, icon)


def port_open():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def wsl_ok():
    try:
        out = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=20, creationflags=NO_WINDOW).stdout
        names = out.decode("utf-16-le", errors="ignore").replace("\x00", "").split()
        return _arg("--wsl-distro", "dablab") in names
    except Exception:
        return False


def mute_pluto():
    exe = os.path.join(LIBIIO, "iio_attr.exe")
    uri = _arg("--uri", "usb:")          # nur das Gerät, das dieser Server benutzt
    if os.path.exists(exe) and uri.startswith(("usb:", "ip:192.168.2.")):
        subprocess.run([exe, "-u", uri, "-c", "-o", "ad9361-phy", "voltage0", "hardwaregain", "-89.75"],
                       capture_output=True, timeout=10, creationflags=NO_WINDOW)


@ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
def _console_handler(event):
    # 0 Strg+C, 1 Strg+Pause, 2 Fenster schließen, 5 Abmelden, 6 Herunterfahren
    try:
        mute_pluto()
    except Exception:
        pass
    return False


def open_browser_when_ready():
    for _ in range(120):
        if port_open():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


def main():
    ctypes.windll.kernel32.SetConsoleTitleW("Muxwright Server")
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("Muxwright – Prüfsender für den ADALM-Pluto")
    print("==========================================")
    if not os.path.isfile(os.path.join(PC, "server.py")):
        msg(f"pc\\server.py nicht gefunden.\nDABLab.exe muss im Ordner dablab liegen.\n\n{BASE}")
        return 1
    if port_open():
        print("Server läuft bereits, öffne Dashboard …")
        webbrowser.open(URL)
        return 0
    if not wsl_ok():
        print("Hinweis: Linux-Umgebung noch nicht eingerichtet – das Dashboard führt durch die Installation "
              "(Einrichtung → Komponenten).")
    os.environ["DABLAB_LIBIIO_DIR"] = LIBIIO
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)
    if "--no-browser" in sys.argv:
        sys.argv.remove("--no-browser")
    else:
        threading.Thread(target=open_browser_when_ready, daemon=True).start()
    sys.path.insert(0, PC)
    os.chdir(PC)
    sys.argv = [os.path.join(PC, "server.py")] + sys.argv[1:]
    print(f"Dashboard: {URL}   (Fenster schließen = Server beenden, Pluto wird stummgeschaltet)\n")
    import server  # noqa: E402  (aus pc\, zur Laufzeit)
    try:
        return server.main()
    finally:
        mute_pluto()


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except SystemExit:
        raise
    except Exception as e:  # sichtbar machen statt stumm zu schließen
        import traceback
        traceback.print_exc()
        mute_pluto()
        input(f"\nFehler: {e}\nEnter zum Schließen …")
