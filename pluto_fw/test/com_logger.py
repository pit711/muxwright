"""COM-Port-Mitschnitt mit automatischem Wiederverbinden (Pluto-ACM-Gadget verschwindet beim Neustart).
python com_logger.py COM33 ausgabe.txt SEKUNDEN"""
import sys, time, serial

port, out, dur = sys.argv[1], sys.argv[2], float(sys.argv[3])
t0 = time.time()
with open(out, "a", encoding="utf-8", errors="replace") as f:
    f.write(f"\n=== Mitschnitt {port} ab {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"); f.flush()
    s = None
    while time.time() - t0 < dur:
        if s is None:
            try:
                s = serial.Serial(port, 115200, timeout=0.5)
                f.write(f"[{time.time()-t0:7.1f}s] {port} geöffnet\n"); f.flush()
                s.write(b"\r\n")
            except Exception:
                time.sleep(0.5); continue
        try:
            d = s.read(4096)
            if d:
                f.write(d.decode("utf-8", "replace")); f.flush()
        except Exception as e:
            f.write(f"\n[{time.time()-t0:7.1f}s] {port} weg ({e.__class__.__name__})\n"); f.flush()
            try: s.close()
            except Exception: pass
            s = None
    f.write(f"\n=== Ende {time.strftime('%H:%M:%S')} ===\n")
