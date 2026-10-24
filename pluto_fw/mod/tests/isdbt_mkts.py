#!/usr/bin/env python3
"""Test-TS für isdbt_mod: zwei PIDs (A = 0x101, B = 0x100) im Verhältnis der Schichtraten, Nutzlast
= Paketnummer + Zufallsbytes (eindeutig, damit die Rückgewinnung paketgenau verglichen werden kann).
Aufruf: isdbt_mkts.py OUT.ts FRAMES NA NB   (NA/NB = TSP je Rahmen, z. B. 64 2592)"""
import sys, random, struct
out, frames, na, nb = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rnd = random.Random(1234)
cc = {0x100: 0, 0x101: 0}
seq = {0x100: 0, 0x101: 0}
def pkt(pid):
    h = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10 | cc[pid]])
    cc[pid] = (cc[pid] + 1) & 15
    body = struct.pack('>I', seq[pid]) + bytes(rnd.getrandbits(8) for _ in range(180))
    seq[pid] += 1
    return h + body
with open(out, 'wb') as f:
    for fr in range(frames):
        # gleichmäßig verteilen (Bresenham)
        tot = na + nb; ia = 0
        for i in range(tot):
            if na and (i + 1) * na // tot > ia: f.write(pkt(0x101)); ia += 1
            else: f.write(pkt(0x100))
print(seq)
