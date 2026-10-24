#!/usr/bin/env python3
"""atsc_ts_compare.py ORIG.ts RX.ts [--loop] – empfangenen TS paketweise mit dem Original vergleichen.
Sucht den Versatz (erstes Paket mit genau einem Treffer im Original), zählt danach gleiche/abweichende Pakete
und Pakete mit gesetztem transport_error_indicator (RS-Decoder nicht korrigierbar). Ausgabe: eine JSON-Zeile.
Hinweis: Der Empfänger braucht ca. 1 Feld + Interleaver-Laufzeit zum Einrasten; diese Pakete fehlen am Anfang."""
import sys, json
import numpy as np

orig = np.fromfile(sys.argv[1], np.uint8)
rx = np.fromfile(sys.argv[2], np.uint8)
loop = "--loop" in sys.argv
o = orig[: len(orig) // 188 * 188].reshape(-1, 188)
r = rx[: len(rx) // 188 * 188].reshape(-1, 188)
idx = {}
for i, p in enumerate(o):
    idx.setdefault(p.tobytes(), []).append(i)
start = off = None
for j, p in enumerate(r):
    if p[1] & 0x80: continue
    hit = idx.get(p.tobytes())
    if hit and len(hit) == 1:
        start, off = j, hit[0] - j
        break
res = {"rx_packets": int(len(r)), "orig_packets": int(len(o))}
if start is None:
    res["error"] = "kein Paket wiedergefunden"
else:
    n = len(r) - start
    k = np.arange(start, len(r)) + off
    valid = k < len(o) if not loop else np.ones(n, bool)
    k = k % len(o)
    a, b = r[start:][valid], o[k[valid]]
    diff = (a != b).any(axis=1)
    tei = (a[:, 1] & 0x80) != 0
    res["tei_before_lock"] = int(((r[:start, 1] & 0x80) != 0).sum())
    res.update(first_match_rx=int(start), offset=int(off), compared=int(valid.sum()), equal=int((~diff).sum()),
               differing=int(diff.sum()), tei=int(tei.sum()), byte_errors=int((a != b).sum()),
               tail_beyond_orig=int((~valid).sum()))
print(json.dumps(res))
