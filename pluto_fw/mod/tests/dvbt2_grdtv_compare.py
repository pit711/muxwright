#!/usr/bin/env python3
"""Stufenweiser Vergleich dvbt2_mod (--dump) gegen den gr-dtv-DVB-T2-Sender (GNU Radio 3.10, nur Prüfreferenz).
Gleicher TS in beide Ketten, gr-dtv mit denselben Rahmenparametern (aus dvbt2_mod --print-rates), Datei-Senken nach
jeder Stufe. Verglichen werden die ersten F vollständigen T2-Rahmen:
  LDPC-Codewort (BB+BCH+LDPC, natürliche Bitfolge) | Zellwörter nach Bit-IL/Demux | Zellen nach Mapping/Rotation/Q-Delay |
  Zeit-Interleaver-Ausgang | Rahmen vor Frequenz-IL (inkl. L1, Dummy, FC) | nach Frequenz-IL | IQ (P1 + Symbole)
Zellen/IQ: Skalierung per kleinster Quadrate, SNR in dB (int16-Rundung ⇒ ~50–60 dB bei IQ, > 80 dB bei Zellen).
  python3 dvbt2_grdtv_compare.py BIN TS.ts OUTDIR [--frames F] [-- weitere dvbt2_mod-Optionen]
Hinweis gr-dtv: Für die T2-Schutzintervalle 1/128, 19/256, 19/128 wird FFTSIZE_*_T2GI gesetzt (sonst sendet gr-dtv im
P1/L1-pre S2 = 101/001 statt normgerecht 111/110)."""
import json, os, subprocess, sys
import numpy as np
from gnuradio import gr, blocks, digital, dtv

args = sys.argv[1:]
extra = []
if "--" in args:
    i = args.index("--"); extra = args[i + 1:]; args = args[:i]
BIN, TS, OUT = args[0], args[1], args[2]
F = int(args[args.index("--frames") + 1]) if "--frames" in args else 3
os.makedirs(OUT, exist_ok=True)
R = json.loads(subprocess.run([BIN, "--print-rates"] + extra, capture_output=True, text=True, check=True).stdout)
print("Parameter:", json.dumps({k: R[k] for k in ("fft", "ext", "guard", "pp", "const", "rate", "fec_len", "ldata", "nfec", "nti", "t2frames", "l1_const", "ts_bps")}))

fft, gi = R["fft"], R["guard"]
t2gi = gi in ("1/128", "19/256", "19/128")
FFT = {1024: "FFTSIZE_1K", 2048: "FFTSIZE_2K", 4096: "FFTSIZE_4K", 8192: "FFTSIZE_8K", 16384: "FFTSIZE_16K", 32768: "FFTSIZE_32K"}[fft]
if t2gi and fft in (8192, 32768): FFT += "_T2GI"
GI = {"1/32": "GI_1_32", "1/16": "GI_1_16", "1/8": "GI_1_8", "1/4": "GI_1_4", "1/128": "GI_1_128", "19/128": "GI_19_128", "19/256": "GI_19_256"}[gi]
RATE = {"1/2": "C1_2", "3/5": "C3_5", "2/3": "C2_3", "3/4": "C3_4", "4/5": "C4_5", "5/6": "C5_6"}[R["rate"]]
CONST = {"QPSK": "MOD_QPSK", "16QAM": "MOD_16QAM", "64QAM": "MOD_64QAM", "256QAM": "MOD_256QAM"}[R["const"]]
L1 = {"bpsk": "L1_MOD_BPSK", "qpsk": "L1_MOD_QPSK", "16qam": "L1_MOD_16QAM", "64qam": "L1_MOD_64QAM"}[R["l1_const"]]
BW = {1700000: "BANDWIDTH_1_7_MHZ", 5000000: "BANDWIDTH_5_0_MHZ", 6000000: "BANDWIDTH_6_0_MHZ", 7000000: "BANDWIDTH_7_0_MHZ",
      8000000: "BANDWIDTH_8_0_MHZ", 10000000: "BANDWIDTH_10_0_MHZ"}[R["bandwidth_hz"]]
g = lambda n: getattr(dtv, n)
FS = dtv.FECFRAME_SHORT if R["fec_len"] == 16200 else dtv.FECFRAME_NORMAL
E = dtv.CARRIERS_EXTENDED if R["ext"] else dtv.CARRIERS_NORMAL
PP = g("PILOT_PP%d" % R["pp"]); ROT = dtv.ROTATION_ON if R["rotation"] else dtv.ROTATION_OFF
extra = extra if any(a == "--frequency" for a in extra) else extra + ["--frequency", "729833333"]   # gr-dtv: FREQUENCY fest
nd, fb, ti, nt2 = R["ldata"], R["nfec"], R["nti"], R["t2frames"]
guard = fft * int(gi.split("/")[0]) // int(gi.split("/")[1])

# TS: Pakete für F Rahmen + Reserve (Original-TS wird zugeschnitten)
dfl = R["kbch"] - 80
need = int(np.ceil(F * fb * dfl / (187 * 8))) + 200
raw = np.fromfile(TS, dtype=np.uint8)
start = int(np.argmax(raw[:1000] == 0x47))
raw = raw[start:start + 188 * need]
assert len(raw) == 188 * need, "TS zu kurz"
tsf = os.path.join(OUT, "in.ts"); raw.tofile(tsf)

def p(n): return os.path.join(OUT, n)
tb = gr.top_block()
src = blocks.file_source(1, tsf, False)
chain = [dtv.dvb_bbheader_bb(dtv.STANDARD_DVBT2, FS, g(RATE), dtv.RO_0_35, dtv.INPUTMODE_HIEFF, dtv.INBAND_OFF, fb, 4000000),
         dtv.dvb_bbscrambler_bb(dtv.STANDARD_DVBT2, FS, g(RATE)),
         dtv.dvb_bch_bb(dtv.STANDARD_DVBT2, FS, g(RATE)),
         dtv.dvb_ldpc_bb(dtv.STANDARD_DVBT2, FS, g(RATE), dtv.MOD_OTHER),
         dtv.dvbt2_interleaver_bb(FS, g(RATE), g(CONST)),
         dtv.dvbt2_modulator_bc(FS, g(CONST), ROT),
         dtv.dvbt2_cellinterleaver_cc(FS, g(CONST), fb, ti),
         dtv.dvbt2_framemapper_cc(FS, g(RATE), g(CONST), ROT, fb, ti, E, g(FFT), g(GI), g(L1), PP, nt2, nd, dtv.PAPR_OFF,
                                  dtv.VERSION_111, dtv.PREAMBLE_T2_SISO, dtv.INPUTMODE_HIEFF, dtv.RESERVED_OFF,
                                  dtv.L1_SCRAMBLED_OFF, dtv.INBAND_OFF),
         dtv.dvbt2_freqinterleaver_cc(E, g(FFT), PP, g(GI), nd, dtv.PAPR_OFF, dtv.VERSION_111, dtv.PREAMBLE_T2_SISO),
         dtv.dvbt2_pilotgenerator_cc(E, g(FFT), PP, g(GI), nd, dtv.PAPR_OFF, dtv.VERSION_111, dtv.PREAMBLE_T2_SISO,
                                     dtv.MISO_TX1, dtv.EQUALIZATION_OFF, g(BW), fft),
         digital.ofdm_cyclic_prefixer(fft, fft + guard, 0, ""),
         dtv.dvbt2_p1insertion_cc(E, g(FFT), g(GI), nd, dtv.PREAMBLE_T2_SISO, dtv.SHOWLEVELS_OFF, 3.3)]
tb.connect(src, *chain)
taps = {"ldpc": (3, 1), "lab": (4, 1), "map": (5, 8), "ti": (6, 8), "frm": (7, 8), "fi": (8, 8), "iq": (11, 8)}
for name, (idx, sz) in taps.items():
    tb.connect(chain[idx], blocks.file_sink(sz, p("gr." + name)))
tb.run()

subprocess.run([BIN, "-i", tsf, "-o", p("my.iq"), "--frames", str(F), "--dump", p("my"), "--quiet"] + extra, check=True)

ncell, ctot = R["ncells"], R["ctot"]
nfr = F * fb
ok = True
def res(tag, good, info):
    global ok
    ok &= good
    print(f"{tag:42s} {'GLEICH' if good else 'ABWEICHUNG'}  {info}")

gl = np.fromfile(p("gr.ldpc"), np.uint8)
ml = np.unpackbits(np.fromfile(p("my.ldpc"), np.uint8))
n = min(len(gl), len(ml), nfr * R["fec_len"])
d = int(np.sum(gl[:n] != ml[:n]))
res("BB+BCH+LDPC (Bits)", d == 0 and n == nfr * R["fec_len"], f"{n} Bits ({n // R['fec_len']} FEC-Blöcke), {d} Abweichungen")
kb = R["kbch"]; nb = n // R["fec_len"]
for nm, a0, a1 in (("  davon BBFRAME", 0, kb), ("  davon BCH-Parität", kb, kb + (192 if R["fec_len"] == 64800 and R["rate"] not in ("2/3", "5/6") else 160 if R["fec_len"] == 64800 else 168)), ("  davon LDPC-Parität", None, None)):
    G2 = gl[:nb * R["fec_len"]].reshape(nb, -1); M2 = ml[:nb * R["fec_len"]].reshape(nb, -1)
    if a0 is None:
        a0 = kb + (192 if R["fec_len"] == 64800 and R["rate"] not in ("2/3", "5/6") else 160 if R["fec_len"] == 64800 else 168); a1 = R["fec_len"]
    print(f"{nm:42s} {int(np.sum(G2[:, a0:a1] != M2[:, a0:a1]))} Abweichungen")
gb = np.fromfile(p("gr.lab"), np.uint8); mb = np.fromfile(p("my.lab"), np.uint8)
n = min(len(gb), len(mb), nfr * ncell)
d = int(np.sum(gb[:n] != mb[:n]))
res("Bit-Interleaver + Demux (Zellwörter)", d == 0 and n == nfr * ncell, f"{n} Zellen, {d} Abweichungen")

def cmp_cells(tag, grf, myf, count):
    G = np.fromfile(grf, np.complex64)[:count].astype(np.complex128)
    M = np.fromfile(myf, np.int16).astype(np.float64)
    M = (M[0::2] + 1j * M[1::2])[:count]
    n = min(len(G), len(M))
    G, M = G[:n], M[:n]
    gain = np.vdot(G, M).real / np.vdot(G, G).real
    e = M / gain - G
    snr = 10 * np.log10(np.sum(np.abs(G) ** 2) / max(np.sum(np.abs(e) ** 2), 1e-30))
    worst = np.max(np.abs(e)) / max(np.sqrt(np.mean(np.abs(G) ** 2)), 1e-30)
    good = n == count and snr > 40
    res(tag, good, f"{n} Werte, Skalierung {gain:.2f}, SNR {snr:.1f} dB, max. Fehler {20*np.log10(max(worst,1e-12)):.1f} dB rel. RMS")
    return G, M / gain, gain

cmp_cells("Mapping + Rotation + Q-Delay (Zellen)", p("gr.map"), p("my.map"), nfr * ncell)
cmp_cells("Zell- + Zeit-Interleaver (Zellen)", p("gr.ti"), p("my.ti"), nfr * ncell)
Gf, Mf, _ = cmp_cells("Rahmen vor Frequenz-IL (L1/PLP/Dummy/FC)", p("gr.frm"), p("my.frm"), F * ctot)
# L1-Anteile getrennt (erste Zellen des 1. P2-Symbols bzw. zickzack bei NP2 > 1)
cmp_cells("Frequenz-Interleaver (Zellen)", p("gr.fi"), p("my.fi"), F * ctot)
fl = 2048 + (nd + R["np2"]) * (fft + guard)
G, M, gain = cmp_cells("IQ gesamt (P1 + P2 + Daten + GI)", p("gr.iq"), p("my.iq"), F * fl)
for fr in range(F):
    b = fr * fl
    for nm, a0, a1 in (("P1", 0, 2048), ("P2", 2048, 2048 + R["np2"] * (fft + guard)), ("Daten", 2048 + R["np2"] * (fft + guard), fl)):
        g_ = G[b + a0:b + a1]; m_ = M[b + a0:b + a1]
        snr = 10 * np.log10(np.sum(np.abs(g_) ** 2) / max(np.sum(np.abs(m_ - g_) ** 2), 1e-30))
        print(f"   Rahmen {fr} {nm:6s} SNR {snr:6.1f} dB")
print("ERGEBNIS:", "alle Stufen gleich (Zellen bis auf Rundung)" if ok else "ABWEICHUNGEN")
sys.exit(0 if ok else 1)
