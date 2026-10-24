#!/usr/bin/env python3
"""DTMB-Prüf-TS: erzeugen (gen N DATEI [SEED]) und vergleichen (cmp REF DEKODIERT [JSON]).
Testpakete: PID 0x0100, Zähler (CC), Nutzlast = 4-Byte-Laufnummer + Zufallsbytes. Der Vergleich ordnet die
dekodierten Pakete über die Laufnummer zu und zählt identische, fehlerhafte und fehlende Pakete; Fremd-/Nullpakete
(Auffüllung, Nachlauf) werden gezählt, aber nicht als Fehler gewertet."""
import sys, json, numpy as np

def gen(n, path, seed=1):
    rng = np.random.default_rng(seed)
    pk = np.zeros((n, 188), np.uint8)
    pk[:, 0] = 0x47; pk[:, 1] = 0x01; pk[:, 2] = 0x00
    pk[:, 3] = 0x10 | (np.arange(n) & 15)
    seq = np.arange(n, dtype='>u4').view(np.uint8).reshape(n, 4)
    pk[:, 4:8] = seq
    pk[:, 8:] = rng.integers(0, 256, (n, 180), dtype=np.uint8)
    pk.tofile(path)

def cmp(ref, dec, out=None):
    R = np.fromfile(ref, np.uint8).reshape(-1, 188)
    raw = np.fromfile(dec, np.uint8)
    D = raw[: raw.size // 188 * 188].reshape(-1, 188)
    pid = ((D[:, 1].astype(int) & 0x1F) << 8) | D[:, 2]
    test = (pid == 0x100) & (D[:, 0] == 0x47)
    seqs = D[test, 4:8].copy().view('>u4').ravel()
    ok = bad = 0; first = last = None; got = set(); badlist = []
    for k, s in zip(np.nonzero(test)[0], seqs):
        if s < len(R) and (D[k] == R[s]).all():
            ok += 1; got.add(int(s)); first = s if first is None else first; last = s
        else:
            bad += 1; badlist.append(int(k))
    # zusammenhängender Bereich ab dem ersten korrekten Paket: Lücken
    missing = (int(last) - int(first) + 1 - len(got)) if first is not None else 0
    order_ok = bool(np.all(np.diff(seqs[[i for i, k in enumerate(np.nonzero(test)[0]) if int(k) not in set(badlist)]]) == 1)) if ok > 1 else False
    res = dict(ref_packets=int(len(R)), decoded_packets=int(len(D)), test_packets_ok=ok, test_packets_bad=bad,
               other_packets=int((~test).sum()), first_seq=None if first is None else int(first), last_seq=None if last is None else int(last),
               missing_in_range=missing, in_order=order_ok)
    print(json.dumps(res))
    if out:
        json.dump(res, open(out, 'w'), indent=1)
    return 0 if (ok > 0 and bad == 0 and missing == 0) else 1

if __name__ == '__main__':
    if sys.argv[1] == 'gen':
        gen(int(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 1)
    else:
        sys.exit(cmp(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None))
