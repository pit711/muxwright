#!/usr/bin/env python3
"""Gegenprüfung lib/dvbt2_tables.h (aus der Norm extrahiert) gegen die Tabellen im gr-dtv-Quelltext (nur Vergleich,
kein Code übernommen): PN-Folge, P1-Träger und S1/S2-Muster, Continual Pilots G.1/G.2, reservierte P2-Träger H.1.
  python3 dvbt2_tables_check.py ../lib/dvbt2_tables.h /pfad/gr-dtv/lib/dvbt2"""
import re, sys

hdr = open(sys.argv[1], encoding="utf-8").read()
gd = sys.argv[2].rstrip("/")


def harr(name):
    m = re.search(r"\b" + name + r"\s*\[[^]]*\](?:\[[^]]*\])?\s*=\s*\{(.*?)\};", hdr, re.S)
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


def hstr(name):
    m = re.search(r"\b" + name + r"\[\d+\]\s*=\s*\{(.*?)\};", hdr, re.S)
    return re.findall(r'"([0-9A-F]+)"', m.group(1))


def garr(src, name):
    m = re.search(r"::" + name + r"\[[^]]*\](?:\[[^]]*\])?\s*=\s*\{(.*?)\};", src, re.S)
    return [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\d+", m.group(1))] if m else None


pg = open(gd + "/dvbt2_pilotgenerator_cc_impl.cc").read()
p1 = open(gd + "/dvbt2_p1insertion_cc_impl.cc").read()
ok = True


def chk(tag, a, b):
    global ok
    same = a == b
    ok &= same
    print(f"{tag:34s} {'gleich' if same else 'ABWEICHUNG'}  ({len(a)} Werte)" + ("" if same else f"  nur Norm {sorted(set(a)-set(b))[:6]} nur gr {sorted(set(b)-set(a))[:6]}"))


chk("Tab. 56 PN-Folge", harr("t2_pn_seq"), garr(pg, "pn_sequence_table"))
chk("Tab. 68 P1-Träger", harr("t2_p1_carriers"), garr(p1, "p1_active_carriers"))
s1 = [b for h in hstr("t2_p1_s1") for b in bytes.fromhex(h)]
s2 = [b for h in hstr("t2_p1_s2") for b in bytes.fromhex(h)]
chk("Tab. 69 S1-Muster", s1, garr(p1, "s1_modulation_patterns"))
chk("Tab. 69 S2-Muster", s2, garr(p1, "s2_modulation_patterns"))
val, idx = harr("t2_cp_val"), harr("t2_cp_idx")
for pp in range(1, 9):
    for g in range(1, 7):
        a = sorted(val[idx[(pp - 1) * 7 + g - 1]:idx[(pp - 1) * 7 + g]])
        b = garr(pg, f"pp{pp}_cp{g}")
        if not a and b is None:
            continue
        if pp == 3 and g == 6 and not a:
            print(f"G.1 PP3 CP6                        nur gr ({len(b)} Werte) – PP3 ist bei 32K nicht zulässig (Tab. 58)")
            continue
        chk(f"G.1 PP{pp} CP{g}", a, sorted(b or []))
for fft in ("8k", "16k", "32k"):
    v, ix = harr(f"t2_cpext_{fft}_val"), harr(f"t2_cpext_{fft}_idx")
    for pp in range(1, 9):
        a = v[ix[pp - 1]:ix[pp]]
        b = garr(pg, f"pp{pp}_{fft}")
        if not a and b is None:
            continue
        if b is not None and a != b and fft == "16k" and pp == 1:
            print(f"G.2 {fft} PP{pp}                       Norm {a} / gr {b} (Tippfehler in gr-dtv: 3636 statt 13636)")
            continue
        chk(f"G.2 {fft} PP{pp}", a, b or [])
for n in ("1k", "2k", "4k", "8k", "16k", "32k"):
    chk(f"H.1 P2-Reserve {n}", harr(f"t2_p2res_{n}"), garr(pg, f"p2_papr_map_{n}"))
print("ALLE TABELLEN GLEICH (bis auf dokumentierte gr-Abweichungen)" if ok else "ABWEICHUNGEN")
