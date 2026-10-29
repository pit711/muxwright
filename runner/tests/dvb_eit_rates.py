#!/usr/bin/env python3
"""EIT im TS auswerten (PID 18): je table_id/section_number Anzahl, Version und groesster Abstand in Sekunden (aus der
Paketposition und der TS-Rate), dazu die Ereignisse des letzten Abschnitts.
Aufruf: dvb_eit_rates.py f.ts --rate R"""
import argparse
import struct

ap = argparse.ArgumentParser()
ap.add_argument("ts")
ap.add_argument("--rate", type=float, required=True, help="TS-Rate in b/s")
ap.add_argument("--sizes", help="Protokoll 'Zeit Dateigroesse' (dvb_schedule_live.sh): Versionswechsel p/f mit Wanduhr")
a = ap.parse_args()

secs, last, pf_ver = {}, {}, []


def take(buf, idx):
    """Vollstaendige Abschnitte aus buf entnehmen -> Rest."""
    while len(buf) >= 3 and buf[0] != 0xFF:
        ln = ((buf[1] & 0x0F) << 8) | buf[2]
        if len(buf) < 3 + ln:
            break
        sec, buf = buf[:3 + ln], buf[3 + ln:]
        secs.setdefault((sec[0], sec[6]), []).append(idx)
        last[(sec[0], sec[6])] = sec
        if sec[0] == 0x4E and sec[6] == 0 and (not pf_ver or pf_ver[-1][1] != (sec[5] >> 1) & 31):
            pf_ver.append((idx, (sec[5] >> 1) & 31, sec))
    return buf if (buf and buf[0] != 0xFF) else b""


buf, idx = b"", 0
with open(a.ts, "rb") as f:
    while True:
        p = f.read(188)
        if len(p) < 188:
            break
        idx += 1
        if ((p[1] & 0x1F) << 8) | p[2] != 18:
            continue
        af = (p[3] >> 4) & 3
        pl = p[5 + p[4]:] if af in (2, 3) else p[4:]
        if p[1] & 0x40:
            ptr = pl[0]
            if buf:
                take(buf + pl[1:1 + ptr], idx)
            buf = take(pl[1 + ptr:], idx)
        elif buf:
            buf = take(buf + pl, idx)

for (tid, sn), pos in sorted(secs.items()):
    gaps = [(b - c) * 188 * 8 / a.rate for c, b in zip(pos, pos[1:])]
    sec = last[(tid, sn)]
    evs, q = [], 14
    while q < len(sec) - 4:
        eid, mjd = struct.unpack(">HH", sec[q:q + 4])
        dl = ((sec[q + 10] & 0x0F) << 8) | sec[q + 11]
        d = sec[q + 12:q + 12 + dl]
        name = d[6:6 + d[5]].decode("latin-1", "replace") if len(d) > 6 and d[0] == 0x4D else ""
        evs.append("id %04X %02x:%02x:%02x UTC dauer %02x:%02x:%02x rs %d %s" % (eid, sec[q + 4], sec[q + 5], sec[q + 6], sec[q + 7],
                                                                         sec[q + 8], sec[q + 9], sec[q + 10] >> 5, name))
        q += 12 + dl
    print("tid 0x%02X sn %3d v%-2d n=%-4d max_abstand=%5.2f s  %s" % (tid, sn, (sec[5] >> 1) & 31, len(pos),
                                                                    max(gaps) if gaps else -1, "; ".join(evs)))
if a.sizes:
    sz = [tuple(map(float, ln.split())) for ln in open(a.sizes) if len(ln.split()) == 2]
    for idx, ver, sec in pf_ver:
        b = idx * 188
        w = next((ta + (tb - ta) * (b - sa) / (sb - sa) for (ta, sa), (tb, sb) in zip(sz, sz[1:]) if sa <= b <= sb and sb > sa), None)
        d = sec[14 + 12:]
        name = d[6:6 + d[5]].decode("latin-1", "replace") if len(d) > 6 and d[0] == 0x4D else ""
        print("EIT p/f v%d '%s' ab Byte %d, geschrieben um %s (%s)" % (ver, name, b, "%.2f" % w if w else "?",
                                                                   __import__("time").strftime("%H:%M:%S", __import__("time").localtime(w)) if w else "?"))
