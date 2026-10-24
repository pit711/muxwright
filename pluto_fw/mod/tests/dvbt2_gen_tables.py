#!/usr/bin/env python3
"""Erzeugt lib/dvbt2_tables.h aus ETSI EN 302 755 V1.4.1 (PDF, PyMuPDF/fitz):
  Tabelle 56 (PN-Folge Rahmenebene), Tabelle 68 (aktive P1-Träger), Tabelle 69 (S1/S2-Muster),
  Tabelle G.1 (Continual Pilots 32K je PP/CP-Gruppe), Tabelle G.2 (Zusatz-CPs Extended-Modus),
  Tabelle H.1 (reservierte P2-Träger) und die T2-eigenen LDPC-Tabellen A.3 (2/3 normal) und B.3 (3/5 kurz)
  aus tests/dvbt2_proto/t2_tables.h (gen_t2_tables.py, ebenfalls aus der Norm).
Nur Normtabellen (Fakten). Prüfung gegen gr-dtv: tests/dvbt2_tables_check.py.
  python dvbt2_gen_tables.py en302755b.pdf ../lib/dvbt2_tables.h ../tests/dvbt2_proto/t2_tables.h"""
import re, sys, fitz


def words(page):
    return [w for w in page.get_text("words")]


def page_of(d, key):
    for pn in range(len(d)):
        if key in d[pn].get_text():
            return pn
    raise SystemExit("nicht gefunden: " + key)


def table56(d):
    pn = page_of(d, "Table 56:")
    t = d[pn].get_text()
    seg = t.split("Table 56:")[1].split("9.2.3")[0]
    hexs = "".join(l.strip() for l in seg.splitlines() if re.fullmatch(r"[0-9A-F]{20,}", l.strip()))
    assert len(hexs) == 656, len(hexs)
    return hexs


def table68(d):
    pn = page_of(d, "Table 68:")
    ws = words(d[pn])
    y0 = min(w[1] for w in ws if w[4] == "68:")
    y1 = min(w[1] for w in ws if w[4] == "9.8.2.2" and w[1] > y0)
    vals = [int(w[4]) for w in sorted(ws, key=lambda w: (round(w[1]), w[0]))
            if y0 + 5 < w[1] < y1 - 2 and w[4].isdigit()]
    assert len(vals) == 384 and vals == sorted(vals), len(vals)
    return vals


def table69(d):
    pn = page_of(d, "Table 69:")
    t = d[pn].get_text()
    seg = t.split("Table 69:")[1]
    s1 = re.findall(r"^\s*([01]{3})\s*\n?\s*([0-9A-F]{16})\s*$", seg, re.M)
    s2 = re.findall(r"^\s*([01]{4})\s*\n?\s*([0-9A-F]{64})\s*$", seg, re.M)
    if len(s1) != 8 or len(s2) != 16:    # Layout-Rückfall: Hex-Zeilen in Reihenfolge
        hx = re.findall(r"\b([0-9A-F]{16}|[0-9A-F]{64})\b", seg)
        s1 = [(None, h) for h in hx if len(h) == 16][:8]
        s2 = [(None, h) for h in hx if len(h) == 64][:16]
    assert len(s1) == 8 and len(s2) == 16
    return [h for _, h in s1], [h for _, h in s2]


def table_g1(d):
    p0 = page_of(d, "Table G.1:")
    groups, cur = {}, None
    for pn in range(p0, p0 + 5):
        ws = words(d[pn])
        hdr = [w for w in ws if w[4] == "Group"]
        if not hdr:
            continue
        hy = hdr[0][1]
        stop = min([w[1] for w in ws if w[4].startswith("G.2") and w[1] > hy] + [1e9])
        foot = min([w[1] for w in ws if w[4] == "ETSI" and w[1] > 700] + [1e9])
        cps = sorted((w[1], int(w[4][2:])) for w in ws if re.fullmatch(r"CP\d", w[4]))
        ppx = {int(w[4][2:]): (w[0] + w[2]) / 2 for w in ws if re.fullmatch(r"PP\d", w[4]) and abs(w[1] - hy) < 3}
        for w in ws:
            if not w[4].isdigit() or w[1] <= hy + 3 or w[1] >= stop - 2 or w[1] >= foot - 2 or w[0] < 100:
                continue
            g = cur
            for cy, cn in cps:
                if cy <= w[1] + 2:
                    g = cn
            xc = (w[0] + w[2]) / 2
            pp = min(ppx, key=lambda k: abs(ppx[k] - xc))
            groups.setdefault((pp, g), []).append(int(w[4]))
        if cps:
            cur = cps[-1][1]
    return {k: sorted(v) for k, v in groups.items()}


def table_g2(d):
    """G.2 (klein): Wortpositionen unter der Überschrift; Spalten PP1..PP8 nach x, Zeilen 8K/16K/32K nach y."""
    pn = page_of(d, "Table G.2:")
    ws = words(d[pn])
    ty = min(w[1] for w in ws if w[4].startswith("G.2") and w[1] > 100)
    hdr = [w for w in ws if re.fullmatch(r"PP\d", w[4]) and w[1] > ty]
    hy = min(w[1] for w in hdr)
    ppx = {int(w[4][2:]): (w[0] + w[2]) / 2 for w in hdr if abs(w[1] - hy) < 3}
    rows = sorted((w[1], w[4]) for w in ws if w[4] in ("8K", "16K", "32K") and w[1] > hy)
    end = min([w[1] for w in ws if w[4] == "ETSI" and w[1] > hy] + [1e9])
    out = {}
    for w in ws:
        if not w[4].isdigit() or w[1] <= hy + 3 or w[1] >= end - 2 or w[0] < 90:
            continue
        r = None
        for ry, rn in rows:
            if ry <= w[1] + 2:
                r = rn
        xc = (w[0] + w[2]) / 2
        pp = min(ppx, key=lambda k: abs(ppx[k] - xc))
        out.setdefault((r, pp), []).append(int(w[4]))
    return {k: sorted(v) for k, v in out.items()}


def table_h1(d):
    pn = page_of(d, "Table H.1:")
    ws = words(d[pn])
    y0 = min(w[1] for w in ws if w[4] == "H.1:")
    y1 = min([w[1] for w in ws if w[4] == "H.2:" and w[1] > y0] + [w[1] for w in ws if w[4] == "ETSI" and w[1] > 700])
    toks = [w for w in sorted(ws, key=lambda w: (round(w[1]), w[0])) if y0 + 5 < w[1] < y1 - 2 and w[0] > 100]
    vals = [int(w[4].rstrip(",")) for w in toks if w[4].rstrip(",").isdigit()]
    res, k = {}, 0
    for n, cnt in ((1024, 10), (2048, 18), (4096, 36), (8192, 72), (16384, 144), (32768, 288)):
        res[n] = vals[k:k + cnt]; k += cnt
        assert res[n] == sorted(res[n]), n
    assert k == len(vals), (k, len(vals))
    return res


def carr(name, typ, vals, per=20):
    s = ",".join(map(str, vals))
    return f"static const {typ} {name}[{len(vals)}] = {{{s}}};"


def main(pdf, outp, ldpc_h):
    d = fitz.open(pdf)
    o = ["/* Automatisch erzeugt aus ETSI EN 302 755 V1.4.1 (tests/dvbt2_gen_tables.py) – Normtabellen, kein Fremdcode. */",
         "#ifndef DVBT2_TABLES_H", "#define DVBT2_TABLES_H", "#include <stdint.h>", ""]
    pnhex = table56(d)
    o.append("/* Tabelle 56: PN-Folge der Rahmenebene (2624 Chips, MSB zuerst) */")
    o.append(carr("t2_pn_seq", "uint8_t", [int(pnhex[i:i + 2], 16) for i in range(0, 656, 2)]))
    o.append("/* Tabelle 68: aktive Träger des P1-Symbols */")
    o.append(carr("t2_p1_carriers", "uint16_t", table68(d)))
    s1, s2 = table69(d)
    o.append("/* Tabelle 69: S1-/S2-Modulationsmuster (Hex, MSB zuerst) */")
    o.append("static const char *const t2_p1_s1[8] = {" + ",".join(f'"{h}"' for h in s1) + "};")
    o.append("static const char *const t2_p1_s2[16] = {" + ",".join(f'"{h}"' for h in s2) + "};")
    g1 = table_g1(d)
    flat, idx = [], []
    for pp in range(1, 9):
        row = []
        for g in range(1, 7):
            row.append(len(flat)); flat += g1.get((pp, g), [])
        row.append(len(flat)); idx.append(row)
    o.append("/* Tabelle G.1: Continual Pilots (Trägerindex 32K) je Pilotmuster PP1..PP8 und CP-Gruppe 1..6:")
    o.append("   Werte t2_cp_val[t2_cp_idx[pp-1][g-1] .. t2_cp_idx[pp-1][g]) */")
    o.append(carr("t2_cp_val", "uint16_t", flat))
    o.append("static const uint16_t t2_cp_idx[8][7] = {" + ",".join("{" + ",".join(map(str, r)) + "}" for r in idx) + "};")
    g2 = table_g2(d)
    o.append("/* Tabelle G.2: zusätzliche Continual Pilots im Extended-Modus (absolute Trägerindizes) je FFT 8K/16K/32K und PP */")
    for fft in ("8K", "16K", "32K"):
        flat, idx = [], []
        for pp in range(1, 9):
            idx.append(len(flat)); flat += g2.get((fft, pp), [])
        idx.append(len(flat))
        o.append(carr(f"t2_cpext_{fft.lower()}_val", "uint16_t", flat or [0]))
        o.append(carr(f"t2_cpext_{fft.lower()}_idx", "uint16_t", idx))
    h1 = table_h1(d)
    o.append("/* Tabelle H.1: reservierte Träger im P2-Symbol (Normalmodus; Extended: + Kext) */")
    for n, v in h1.items():
        o.append(carr(f"t2_p2res_{n // 1024}k", "uint16_t", v))
    o.append("")
    o.append("/* LDPC-Tabellen A.3 (normal, 2/3) und B.3 (kurz, 3/5) – aus tests/dvbt2_proto/t2_tables.h (gen_t2_tables.py) */")
    for ln in open(ldpc_h, encoding="utf-8"):
        if ln.startswith("static const uint16_t ldpc_t2_"):
            o.append(ln.rstrip())
    o += ["", "#endif"]
    open(outp, "w", encoding="utf-8", newline="\n").write("\n".join(o) + "\n")
    print("G.1:", {k: len(v) for k, v in sorted(g1.items())}, file=sys.stderr)
    print("G.2:", {k: v for k, v in sorted(g2.items())}, file=sys.stderr)


main(*sys.argv[1:4])
