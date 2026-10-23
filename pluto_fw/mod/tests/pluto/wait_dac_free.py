"""Wartet, bis der DAC-Puffer des Pluto ≥ 90 s frei ist (buffer/enable=0, kein iqplay). Nur lesend.
Gibt 'FREE' aus oder nach max. Wartezeit 'TIMEOUT'. Robust gegen Neustarts (SSH-Wiederholung)."""
import sys, time, paramiko
HOST = "192.168.2.1"; maxwait = float(sys.argv[1]) if len(sys.argv) > 1 else 1500
cmd = ("D=$(dirname $(grep -l '^cf-ad9361-dds-core-lpc$' /sys/bus/iio/devices/iio:device*/name)); "
       "echo $(cat $D/buffer/enable) $(ps | grep -v grep | grep -c iqplay) $(cut -d' ' -f1 /proc/uptime)")
t0 = time.time(); free_since = None; last = ""
while time.time() - t0 < maxwait:
    try:
        c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(HOST, username="root", password="analog", timeout=8, banner_timeout=8, auth_timeout=8, look_for_keys=False, allow_agent=False)
        _, o, _ = c.exec_command(cmd, timeout=15); s = o.read().decode().split(); c.close()
        busy = not (len(s) >= 2 and s[0] == "0" and s[1] == "0")
    except Exception as ex:
        s = [f"ssh:{type(ex).__name__}"]; busy = True
    state = " ".join(s)
    if busy: free_since = None
    elif free_since is None: free_since = time.time()
    if state != last: print(f"{time.time()-t0:6.0f}s state={state}", flush=True); last = state
    if free_since and time.time() - free_since >= 90: print("FREE", flush=True); sys.exit(0)
    time.sleep(15)
print("TIMEOUT", flush=True); sys.exit(1)
