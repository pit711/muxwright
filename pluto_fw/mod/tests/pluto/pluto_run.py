"""pluto_run.py – Dateien nach /tmp auf den Pluto kopieren und Befehle ausführen (robust, mit Wiederholung).
Nutzung: python pluto_run.py put datei...   |   python pluto_run.py run "befehl" [timeout_s]
Nur /tmp, keine Reboots, kein iiod, kein DAC."""
import paramiko, sys, time, os

HOST, USER, PW = "192.168.2.1", "root", "analog"

def connect():
    for attempt in range(6):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(HOST, username=USER, password=PW, timeout=10, banner_timeout=10, auth_timeout=10,
                      look_for_keys=False, allow_agent=False)
            return c
        except Exception as ex:
            print(f"# ssh attempt {attempt} failed: {ex}", file=sys.stderr); time.sleep(5)
    raise SystemExit("ssh failed")

def put(files):
    c = connect()
    for f in files:
        dst = "/tmp/" + os.path.basename(f)
        data = open(f, "rb").read()
        i, o, e = c.exec_command(f"cat > {dst}.part && mv {dst}.part {dst} && chmod 755 {dst} && ls -la {dst}", timeout=120)
        i.write(data); i.channel.shutdown_write()
        print("# put", f, "->", o.read().decode().strip(), e.read().decode().strip())
    c.close()

def run(cmd, timeout=120):
    c = connect()
    i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=False)
    out = o.read().decode(errors="replace"); err = e.read().decode(errors="replace")
    rc = o.channel.recv_exit_status()
    c.close()
    sys.stdout.write(out)
    if err: sys.stdout.write("STDERR:\n" + err)
    print(f"# rc={rc}")

if __name__ == "__main__":
    if sys.argv[1] == "put": put(sys.argv[2:])
    else: run(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 120)
