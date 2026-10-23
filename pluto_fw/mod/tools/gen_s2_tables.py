#!/usr/bin/env python3
"""Erzeugt lib/dvbs2_tables.h aus ETSI EN 302 307-1 V1.4.1 (PDF): LDPC-Paritätsadresstabellen Annex B (normal) und
Annex C (kurz). Lesereihenfolge: Textblöcke je Seite spaltenweise (links oben→unten, dann rechts), Seiten nacheinander.
Zeilengrenzen aus der Gradverteilung der Norm (X Zeilen mit L Einträgen, Rest mit 3; L eindeutig aus Summe/Zeilenzahl
und der ersten Zeile). Prüft Zeilenzahl = k_ldpc/360 und Adressen < n_ldpc - k_ldpc. Endgültige Prüfung: bitgenauer
Vergleich des LDPC-Encoders mit gr-dtv (tests/s2_compare_grdtv.py).
  python gen_s2_tables.py en_30230701v010401p.pdf ../lib/dvbs2_tables.h"""
import re, sys
import fitz

KLDPC_N = {"1/4": 16200, "1/3": 21600, "2/5": 25920, "1/2": 32400, "3/5": 38880, "2/3": 43200, "3/4": 48600,
           "4/5": 51840, "5/6": 54000, "8/9": 57600, "9/10": 58320}
# Zeilengrad der „langen“ Zeilen (die übrigen haben 3 Einträge), aus der Struktur der Tabellen B.1–B.11 / C.1–C.10
LONG_N = {"1/4": 12, "1/3": 12, "2/5": 12, "1/2": 8, "3/5": 12, "2/3": 13, "3/4": 12, "4/5": 11, "5/6": 13, "8/9": 4, "9/10": 4}
LONG_S = {"1/4": 12, "1/3": 12, "2/5": 12, "1/2": 8, "3/5": 12, "2/3": 13, "3/4": 12, "4/5": 3, "5/6": 13, "8/9": 4}
KLDPC_S = {"1/4": 3240, "1/3": 5400, "2/5": 6480, "1/2": 7200, "3/5": 9720, "2/3": 10800, "3/4": 11880,
           "4/5": 12600, "5/6": 13320, "8/9": 14400}

def _cluster_cols(xs, tol=40):
    xs = sorted(xs); cols = []
    for x in xs:
        if cols and x - cols[-1][-1] <= tol: cols[-1].append(x)
        else: cols.append([x])
    return [sum(c)/len(c) for c in cols]

def page_stream(p):
    """Liefert je Seite: Tabellenkopf-Marker und ALLE Zahl-Token in korrekter Lesereihenfolge.
    Innerhalb einer Tabelle: Spalten nach x geclustert, Spalten von links nach rechts, je Spalte Zeilen von oben
    nach unten, Token je Zeile von links nach rechts. So werden umbrochene lange Zeilen (8/9, 9/10) und die
    mehrspaltig gesetzten kurzen Zeilen (2/5 u. a.) korrekt linearisiert."""
    lines = []
    for blk in p.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            txt = "".join(sp["text"] for sp in ln["spans"]).strip()
            x0, y0 = ln["bbox"][0], ln["bbox"][1]
            if not txt or y0 < 60 or y0 > 790: continue
            lines.append((y0, x0, txt))
    heads = sorted((y0, m.group(1), m.group(3)) for y0, x0, txt in lines
                   for m in [re.search(r"Table ([BC])\.(\d+): Rate (\d+/\d+)", txt)] if m)
    bounds = [-1] + [h[0] for h in heads] + [1e9]
    out = []
    for si in range(len(bounds) - 1):
        if si > 0: out.append(("T", heads[si - 1][1], heads[si - 1][2]))
        seg = [(y0, x0, txt) for y0, x0, txt in lines
               if bounds[si] < y0 < bounds[si + 1] and not txt.startswith("Table")
               and all(w.isdigit() for w in txt.split()) and txt.split()]
        if not seg: continue
        cols = _cluster_cols([x0 for y0, x0, txt in seg])
        def colidx(x): return min(range(len(cols)), key=lambda i: abs(cols[i] - x))
        for ci in range(len(cols)):
            for y0, x0, txt in sorted((e for e in seg if colidx(e[1]) == ci)):
                out.append(("N", [int(t) for t in txt.split()]))
    return out

def main(pdf, out):
    d = fitz.open(pdf)
    seqs = {}; cur = None; first_line = {}
    for pn in range(38, 60):
        if pn >= len(d): break
        for it in page_stream(d[pn]):
            if it[0] == "T": cur = (it[1], it[2]); seqs[cur] = []; continue
            if cur is not None:
                if cur not in first_line: first_line[cur] = it[1]
                seqs[cur] += it[1]
    o = ["/* Automatisch erzeugt aus ETSI EN 302 307-1 V1.4.1, Annex B/C (tools/gen_s2_tables.py) – nicht von Hand ändern */",
         "#ifndef DVBS2_TABLES_H", "#define DVBS2_TABLES_H", "#include <stdint.h>"]
    for (ann, rate), flat in sorted(seqs.items()):
        kl = (KLDPC_N if ann == "B" else KLDPC_S)[rate]; nl = 64800 if ann == "B" else 16200
        R = kl // 360; T = len(flat)
        L = (LONG_N if ann == "B" else LONG_S)[rate]
        if L == 3:
            X = R
            if T != 3 * R: raise SystemExit(f"Tabelle {ann} {rate}: T={T} passt nicht zu 3×{R}")
        else:
            if (T - 3 * R) % (L - 3): raise SystemExit(f"Tabelle {ann} {rate}: T={T} passt nicht zu L={L}, R={R}")
            X = (T - 3 * R) // (L - 3)
        cands = [(L, X)]; fl = len(first_line.get((ann, rate), []))
        rows = []; k = 0
        for r in range(R):
            n = L if r < X else 3
            rows.append(flat[k:k + n]); k += n
        mx = max(max(r) for r in rows)
        if k != T or mx >= nl - kl: raise SystemExit(f"Tabelle {ann} {rate}: Fehler (k={k}, T={T}, max={mx})")
        name = f"ldpc_{'n' if ann == 'B' else 's'}_{rate.replace('/', '_')}"
        offs = [0]
        for r in rows: offs.append(offs[-1] + len(r))
        o.append(f"static const uint16_t {name}_addr[{T}] = {{" + ",".join(map(str, flat)) + "};")
        o.append(f"static const uint16_t {name}_row[{len(offs)}] = {{" + ",".join(map(str, offs)) + "};")
        print(f"{ann} {rate}: R={R} T={T} L={L} X={X} (Kandidaten {cands}, erste Zeile {fl})")
    o.append("#endif")
    open(out, "w", encoding="utf-8", newline="\n").write("\n".join(o) + "\n")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
