"""Empfangenen TS bitgenau mit Quell-TS vergleichen (Quelle evtl. geloopt, Mitschnitt beginnt irgendwo).
Ausrichtung über ein eindeutiges Paket (kommt in der Quelle genau einmal vor), dann paketweise Vergleich mit Umlauf.
python ts_align_compare.py empfangen.ts quelle.ts"""
import sys
import numpy as np

rx = np.fromfile(sys.argv[1], np.uint8); ref = np.fromfile(sys.argv[2], np.uint8)
# auf 0x47-Raster synchronisieren
s = next(i for i in range(188) if all(rx[i + k * 188] == 0x47 for k in range(min(20, (len(rx) - i) // 188))))
rx = rx[s:]; n = len(rx) // 188; R = rx[:n * 188].reshape(n, 188)
m = len(ref) // 188; F = ref[:m * 188].reshape(m, 188)
pid = lambda p: ((int(p[1]) & 0x1F) << 8) | int(p[2])
# Hash je Quellpaket
fh = {}
for i in range(m):
    fh.setdefault(F[i].tobytes(), []).append(i)
off = None
for j in range(min(n, 4000)):
    if pid(R[j]) == 0x1FFF: continue
    hits = fh.get(R[j].tobytes(), [])
    if len(hits) == 1:
        off = (hits[0] - j) % m; anchor = j; break
if off is None:
    print("keine eindeutige Ausrichtung gefunden"); sys.exit(1)
idx = (np.arange(n) + off) % m
diff = np.unpackbits(np.bitwise_xor(R, F[idx]), axis=1).sum(axis=1)
bad = int((diff > 0).sum()); bits = int(diff.sum())
print(f"Sync-Offset {s} Byte, Anker Paket {anchor} → Quellpaket {(anchor + off) % m}, Quelle {m} Pakete (Umlauf berücksichtigt)")
print(f"verglichen {n} Pakete ({n*188*8} Bit): fehlerhafte Pakete {bad}, Bitfehler {bits}")
if bad:
    first = np.nonzero(diff)[0][:10]; print("erste fehlerhafte Paketindizes:", first.tolist())
sys.exit(0 if bits == 0 else 2)
