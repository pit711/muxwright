#!/usr/bin/env python3
"""Vergleicht einen von gr-isdbt zurückgewonnenen Schicht-TS mit dem Original (paketgenau).
Aufruf: isdbt_compare.py ORIG.ts RX.ts PIDS [TSP_JE_RAHMEN]
  PIDS: Kommaliste (z. B. 0x101 oder 0,0x11,0x1fc8,0x201) | not:LISTE (alle außer diesen) | all
Nullpakete (PID 0x1FFF) werden auf beiden Seiten ignoriert (Original: Eingangsstopfung, die der Modulator
verwirft; Empfang: Stopfung des Modulators bei leerer Schicht-FIFO). Der Empfangsstrom beginnt nach dem
Einschwingen irgendwo im Original; Startposition per Inhaltssuche, danach Paket-für-Paket-Vergleich."""
import sys, json
orig, rx = open(sys.argv[1], 'rb').read(), open(sys.argv[2], 'rb').read()
sel = sys.argv[3] if len(sys.argv) > 3 else 'all'
npf = int(sys.argv[4]) if len(sys.argv) > 4 else 0   # TSP je Rahmen: Lücken nur in den ersten 2 Rahmen (Einrasten) zulässig
neg = sel.startswith('not:')
pidset = set() if sel == 'all' else {int(x, 0) for x in sel[4 if neg else 0:].split(',')}
def pid(p): return ((p[1] & 0x1f) << 8) | p[2]
def want(p):
    q = pid(p)
    if q == 0x1fff: return False
    if sel == 'all': return True
    return (q not in pidset) if neg else (q in pidset)
def pkts(b): return [b[i:i + 188] for i in range(0, len(b) - 187, 188)]
po = [p for p in pkts(orig) if want(p)]
prall = pkts(rx)
pr = [p for p in prall if pid(p) != 0x1fff]
res = {'orig_packets': len(po), 'rx_packets': len(prall), 'rx_nulls': len(prall) - len(pr)}
import bisect
occ = {}
for i, p in enumerate(po): occ.setdefault(p, []).append(i)
idx = {p: v[0] for p, v in occ.items()}
def find(p, exp):
    # nächstes Vorkommen ab exp (wiederholte PSI-Pakete sind inhaltsgleich)
    v = occ.get(p)
    if v is None: return None
    k = bisect.bisect_left(v, exp)
    return v[k] if k < len(v) else v[-1]
start = next((k for k, p in enumerate(pr) if p in idx), None)
if start is None:
    res.update(ok=False, reason='kein Originalpaket im Empfangsstrom gefunden', sync_ok=sum(1 for p in prall if p[0] == 0x47))
else:
    # Paket für Paket; Lücken (vom Empfänger verworfene RS-Fehlpakete) werden gezählt und übersprungen,
    # fremde/verfälschte Pakete zählen als "corrupt". Ab first_clean gibt es weder Lücken noch Fehler.
    exp = idx[pr[start]]; good = corrupt = 0; gaps = []; last_err = start
    for k in range(start, len(pr)):
        j = find(pr[k], exp)
        if j is None: corrupt += 1; last_err = k; continue
        if j != exp:
            gaps.append((k, j - exp)); last_err = k
        good += 1; exp = j + 1
    tail = len(pr) - last_err
    res.update(rx_skip=start, orig_offset=idx[pr[start]], identical=good, corrupt=corrupt, gaps=len(gaps),
               lost=sum(g for _, g in gaps if g > 0), gap_list=gaps[:10], clean_tail_packets=tail,
               ok=(corrupt == 0 and good > 0 and all(g > 0 and (npf == 0 or k < start + 2 * npf) for k, g in gaps)))
print(json.dumps(res))
