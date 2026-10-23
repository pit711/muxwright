"""UDP-Durchsatz PC -> Pluto (TS-Größe 1316 Byte) mit Kernelzählern auf dem Pluto.
python udp_rate_test.py [Mbit/s ...]   (Empfang = UDP InDatagrams+NoPorts des Pluto-Kernels laut dablab-agent /api/status; kein Socket nötig)"""
import json, socket, subprocess, sys, time, urllib.request

P = "192.168.2.1"; PORT = 5005; PAY = 1316; DUR = 10.0
SSH = ["wsl", "-d", "dablab", "-u", "root", "--", "sshpass", "-p", "analog", "ssh", "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=5", f"root@{P}"]


def status():
    return json.loads(urllib.request.urlopen(f"http://{P}:8080/api/status", timeout=10).read())


def counters():
    s = status()
    return s["udp"]["in"] + s["udp"]["noport"], s["udp"]["rcvbuf_err"], s["net"]["usb0"]["rx_drop"], s["net"]["usb0"]["rx_bytes"], s["cpu_pct"]


def main():
    rates = [float(x) for x in sys.argv[1:]] or [20, 35, 50, 80, 120]
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    buf = bytes([0x47]) + bytes(PAY - 1)
    print(f"{'Soll Mbit/s':>11} {'gesendet':>9} {'empfangen':>9} {'Verlust %':>9} {'Ist Mbit/s':>10} {'rcvbuf_err':>10} {'rx_drop':>8}  CPU")
    for r in rates:
        time.sleep(0.5)
        i0, e0, d0, b0, _ = counters()
        pps = r * 1e6 / 8 / PAY; n = 0; t0 = time.perf_counter()
        while True:
            t = time.perf_counter() - t0
            if t >= DUR: break
            due = int(t * pps) + 1
            while n < due:
                try: sk.sendto(buf, (P, PORT)); n += 1
                except BlockingIOError: break
        time.sleep(1.0)
        i1, e1, d1, b1, cpu = counters()
        got = i1 - i0
        print(f"{r:11.1f} {n:9d} {got:9d} {100*(n-got)/max(n,1):9.2f} {got*PAY*8/DUR/1e6:10.1f} {e1-e0:10d} {d1-d0:8d}  {cpu}")


if __name__ == "__main__":
    main()
