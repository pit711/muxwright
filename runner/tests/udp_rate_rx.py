#!/usr/bin/env python3
"""UDP-Empfaenger mit Zeitstempeln: misst die Rate eines TS-ueber-UDP-Stroms im eingeschwungenen Zustand.
  python3 udp_rate_rx.py --port 12345 --seconds 60 --skip 10 [--out f.ts]"""
import argparse, json, socket, time
ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=12345)
ap.add_argument("--seconds", type=float, default=60)
ap.add_argument("--skip", type=float, default=10)
ap.add_argument("--out")
a = ap.parse_args()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
s.bind(("127.0.0.1", a.port))
s.settimeout(5)
f = open(a.out, "wb") if a.out else None
t0 = None
marks = []
total = 0
dgrams = 0
while True:
    try:
        b = s.recv(65536)
    except socket.timeout:
        break
    now = time.monotonic()
    if t0 is None:
        t0 = now
    total += len(b)
    dgrams += 1
    if f:
        f.write(b)
    if now - t0 >= a.skip:
        marks.append((now, total))
    if now - t0 >= a.seconds:
        break
(t1, b1), (t2, b2) = marks[0], marks[-1]
r = (b2 - b1) * 8 / (t2 - t1)
print(json.dumps({"bytes": total, "datagrams": dgrams, "window_s": round(t2 - t1, 3), "rate_bps": round(r, 1),
                  "packets_per_datagram": round(total / 188 / max(dgrams, 1), 2)}))
