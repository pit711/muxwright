# -*- coding: utf-8 -*-
"""Kanaltabellen und Nutzbitraten fuer DVB-T (EN 300 744) und DVB-C (EN 300 429)."""
from fractions import Fraction

# ---------------------------------------------------------------------------------------------
# DVB-T: Mittenfrequenzen in MHz. Band III (7 MHz): E5-E12, Band IV/V (8 MHz): K21-K69.
# ---------------------------------------------------------------------------------------------
DVBT_CHANNELS = {}
for _n in range(5, 13):
    DVBT_CHANNELS["E%d" % _n] = (177.5 + 7.0 * (_n - 5), 7)
for _n in range(21, 70):
    DVBT_CHANNELS["K%d" % _n] = (474.0 + 8.0 * (_n - 21), 8)

# ---------------------------------------------------------------------------------------------
# DVB-C: deutsches digitales Kabelraster (8-MHz-Raster, wie Vodafone/KDG-Frequenzlisten und w_scan "DE"):
#   S02-S09 113..169, K05-K11 177..225, S11-S20 233..305, S21-S41 306..466 (ESB), K21-K69 474..858.
#   S10 und K12 gibt es im 8-MHz-Raster nicht (sie fielen auf K05 bzw. S11). Sonderkanaele = S-Kanaele.
#   Band I (K02-K04) nach analogem 7-MHz-Raster, selten fuer DVB-C genutzt.
# ---------------------------------------------------------------------------------------------
DVBC_CHANNELS = {"K02": (50.5, 7), "K03": (57.5, 7), "K04": (64.5, 7)}
for _n in range(2, 10):
    DVBC_CHANNELS["S%02d" % _n] = (113.0 + 8.0 * (_n - 2), 8)
for _n in range(5, 12):
    DVBC_CHANNELS["K%02d" % _n] = (177.0 + 8.0 * (_n - 5), 8)
for _n in range(11, 21):
    DVBC_CHANNELS["S%d" % _n] = (233.0 + 8.0 * (_n - 11), 8)
for _n in range(21, 42):
    DVBC_CHANNELS["S%d" % _n] = (306.0 + 8.0 * (_n - 21), 8)
for _n in range(21, 70):
    DVBC_CHANNELS["K%d" % _n] = (474.0 + 8.0 * (_n - 21), 8)


# ---------------------------------------------------------------------------------------------
# Amerika, 6-MHz-Raster (Mittenfrequenz = Unterkante + 3 MHz):
#   Antenne (FCC/Anatel): A2-A6, A7-A13 VHF, A14-A69 UHF. ISDB-Tb (Brasilien): Mitte + 1/7 MHz (ABNT NBR 15601).
#   Kabel (EIA-542 STD, J.83 Annex B): C2-C158.
# ---------------------------------------------------------------------------------------------
US_CHANNELS = {}
for _n, _e in ((2, 54), (3, 60), (4, 66), (5, 76), (6, 82)):
    US_CHANNELS["A%d" % _n] = (_e + 3.0, 6)
for _n in range(7, 14):
    US_CHANNELS["A%d" % _n] = (177.0 + 6.0 * (_n - 7), 6)
for _n in range(14, 70):
    US_CHANNELS["A%d" % _n] = (473.0 + 6.0 * (_n - 14), 6)
ISDBT_OFFSET_MHZ = 1.0 / 7.0
CATV_CHANNELS = {}
for _n, _e in ((2, 54), (3, 60), (4, 66), (5, 76), (6, 82)):
    CATV_CHANNELS["C%d" % _n] = (_e + 3.0, 6)
for _n in range(7, 14):
    CATV_CHANNELS["C%d" % _n] = (177.0 + 6.0 * (_n - 7), 6)
for _n in range(14, 23):
    CATV_CHANNELS["C%d" % _n] = (123.0 + 6.0 * (_n - 14), 6)
for _n in range(23, 95):
    CATV_CHANNELS["C%d" % _n] = (219.0 + 6.0 * (_n - 23), 6)
for _n in range(95, 100):
    CATV_CHANNELS["C%d" % _n] = (93.0 + 6.0 * (_n - 95), 6)
for _n in range(100, 159):
    CATV_CHANNELS["C%d" % _n] = (651.0 + 6.0 * (_n - 100), 6)


# China (GY/T 106, DTMB/DVB-C): DS13-DS24 474-562 MHz, DS25-DS68 610-954 MHz (Mitte), dazu VHF DS6-DS12
CN_CHANNELS = {}
for _n in range(6, 13):
    CN_CHANNELS["DS%d" % _n] = (171.0 + 8.0 * (_n - 6), 8)
for _n in range(13, 25):
    CN_CHANNELS["DS%d" % _n] = (474.0 + 8.0 * (_n - 13), 8)
for _n in range(25, 69):
    CN_CHANNELS["DS%d" % _n] = (610.0 + 8.0 * (_n - 25), 8)
# Japan (ISDB-T): J13-J62, Mitte = 473 + 6 (n-13) + 1/7 MHz
JP_CHANNELS = {}
for _n in range(13, 63):
    JP_CHANNELS["J%d" % _n] = (473.0 + 6.0 * (_n - 13), 6)


def _table(standard):
    if standard == "dtmb":
        return CN_CHANNELS
    if standard == "isdbt_jp":
        return JP_CHANNELS
    if standard in ("atsc", "isdbt"):
        return US_CHANNELS
    if standard == "j83b":
        return CATV_CHANNELS
    return DVBT_CHANNELS if standard == "dvbt" else DVBC_CHANNELS


def channel_lookup(standard, name):
    """-> (freq_hz, bandwidth_mhz) oder None. Namen tolerant: 'k21', 'K05', 'K5', 'S2', 's02'.
    standard: dvbt, dvbc, atsc, isdbt (Mitte + 1/7 MHz), j83b (Kabelraster EIA-542)."""
    if not name:
        return None
    tab = _table(standard)
    n = str(name).strip().upper()
    cands = [n]
    if len(n) >= 2 and n[1:].isdigit():
        cands += [n[0] + "%02d" % int(n[1:]), n[0] + "%d" % int(n[1:])]
    for c in cands:
        if c in tab:
            mhz, bw = tab[c]
            if standard in ("isdbt", "isdbt_jp"):
                mhz += ISDBT_OFFSET_MHZ
            return int(round(mhz * 1e6)), bw
    return None


def channel_table(standard):
    tab = _table(standard)
    return [{"channel": k, "freq_mhz": v[0], "bandwidth_mhz": v[1]} for k, v in tab.items()]


# ---------------------------------------------------------------------------------------------
# DVB-T-Nutzbitrate (EN 300 744 Tab. 15): R = b * CR * 188/204 * 1512/2048 * fs / (1 + GI),
# fs = Bandbreite * 8/7 MHz (Elementarperiode T = 7/(8*BW) us). 2k und 8k liefern dieselbe Rate.
# Beispiel 8 MHz, 64-QAM, 2/3, GI 1/32: 24 128 342,25 b/s (stimmt mit dvbtrate.c / gr-dtv-Teststreams).
# ---------------------------------------------------------------------------------------------
DVBT_BITS = {"QPSK": 2, "16-QAM": 4, "64-QAM": 6}
DVBT_CODE_RATES = {"1/2": Fraction(1, 2), "2/3": Fraction(2, 3), "3/4": Fraction(3, 4), "5/6": Fraction(5, 6),
                   "7/8": Fraction(7, 8)}
DVBT_GUARDS = {"1/4": Fraction(1, 4), "1/8": Fraction(1, 8), "1/16": Fraction(1, 16), "1/32": Fraction(1, 32)}
DVBT_MODES = {"2k": 2048, "8k": 8192}
DVBT_BANDWIDTHS = (5, 6, 7, 8)


def dvbt_useful_bitrate(constellation, code_rate, guard, bandwidth_mhz):
    """Exakte Nutzbitrate in b/s als Fraction."""
    b = DVBT_BITS[constellation]
    cr = DVBT_CODE_RATES[code_rate]
    gi = DVBT_GUARDS[guard]
    fs = Fraction(int(bandwidth_mhz) * 8000000, 7)
    return b * cr * Fraction(188, 204) * Fraction(1512, 2048) * fs / (1 + gi)


def dvbt_sample_rate(bandwidth_mhz):
    return Fraction(int(bandwidth_mhz) * 8000000, 7)


def dvbt_packets_per_superframe(mode, constellation, code_rate):
    """TS-Pakete je Superrahmen (4 Rahmen x 68 Symbole): 8k: 1008*b*CR, 2k: 252*b*CR."""
    f = 1008 if mode == "8k" else 252
    return int(f * DVBT_BITS[constellation] * DVBT_CODE_RATES[code_rate])


# ---------------------------------------------------------------------------------------------
# DVB-C: R = SR * log2(M) * 188/204  (3,45 MBd / 256-QAM = 25 435 294,1 b/s; 6,9 MBd / 256-QAM = 50 870 588,2)
# ---------------------------------------------------------------------------------------------
DVBC_BITS = {"16-QAM": 4, "32-QAM": 5, "64-QAM": 6, "128-QAM": 7, "256-QAM": 8}


DVBS_CODE_RATES = {"1/2": Fraction(1, 2), "2/3": Fraction(2, 3), "3/4": Fraction(3, 4), "5/6": Fraction(5, 6),
                   "7/8": Fraction(7, 8)}


def dvbs_useful_bitrate(code_rate, symbol_rate):
    """DVB-S (EN 300 421, QPSK): R = SR * 2 * CR * 188/204  (22 MBd, 7/8: 35 480 392,2 b/s)."""
    return Fraction(int(symbol_rate)) * 2 * DVBS_CODE_RATES[code_rate] * Fraction(188, 204)


# ---------------------------------------------------------------------------------------------
# DVB-S2 (EN 302 307-1, CCM, Normal-FECFRAME): R = SR * (Kbch - 80) / PLFRAME-Symbole
#   PLFRAME = 90 (PLHEADER) + 64800/bits + 36 * floor((Slots - 1) / 16) bei Pilots, Slots = 64800/bits/90
#   (22 MBd, QPSK 3/4, Pilots: 31 945 676,3 b/s) – genau wie dvbs2_ts_bitrate() in pluto_fw/mod/lib/dvbs2.c
# ---------------------------------------------------------------------------------------------
DVBS2_RATES = ["1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10"]
DVBS2_KBCH = dict(zip(DVBS2_RATES, [16008, 21408, 25728, 32208, 38688, 43040, 48408, 51648, 53840, 57472, 58192]))
DVBS2_BITS = {"QPSK": 2, "8PSK": 3}
DVBS2_MOD_RATES = {"QPSK": DVBS2_RATES, "8PSK": ["3/5", "2/3", "3/4", "5/6", "8/9", "9/10"]}
DVBS2_ROLLOFFS = ("0.35", "0.25", "0.20")
# NIT FEC_inner (EN 300 468 Tab. 35) kennt 1/4, 1/3, 2/5 nicht
DVBS2_NIT_FEC = {r: (r if r not in ("1/4", "1/3", "2/5") else "undefined") for r in DVBS2_RATES}


# ---------------------------------------------------------------------------------------------
# Amateurfunk-DATV (DVB-S/S2 direkt auf der Sendefrequenz, IQ per USB): Bänder (Hz), belegte Bandbreite muss hineinpassen.
#   qo100: Breitband-Uplink des Es'hail-2/QO-100-Transponders (2401,5–2409,5 MHz, Downlink = Uplink + 8089,5 MHz)
#   Grenzen der Amateurfunkbänder nach DE-Frequenzplan/IARU R1 (Pluto: 70 MHz … 6 GHz)
# ---------------------------------------------------------------------------------------------
HAM_BANDS = {"qo100": (2401.5e6, 2409.5e6, "QO-100 Breitband-Uplink"), "13cm": (2320e6, 2450e6, "13 cm"),
             "23cm": (1240e6, 1300e6, "23 cm"), "70cm": (430e6, 440e6, "70 cm"), "6cm": (5650e6, 5850e6, "6 cm")}
QO100_DOWNLINK_OFFSET = 8089.5e6
HAM_SR_MIN, HAM_SR_MAX = 25000, 3500000
CALLSIGN_RE = r"^(?=.*\d)[A-Z0-9]{1,3}\d[A-Z0-9]{0,3}[A-Z](/[A-Z0-9]{1,4})?$"


HAM_FS_MIN = 2100000   # AD9364 ohne FIR-Interpolation: ab 2,083 MS/s (pc/pluto.py schaltet das FIR für USB ab)


def ham_sps(symbol_rate):
    """Samples/Symbol für USB-IQ: kleinste ganze Zahl ≥ 2 mit Symbolrate × sps ≥ 2,1 MS/s (USB schafft ≤ 7 MS/s).
    QO-100 333 kS/s → 7 (2,331 MS/s), 125 kS/s → 17, 25 kS/s → 84."""
    return max(2, -(-HAM_FS_MIN // int(symbol_rate)))


def ham_band_of(freq_hz, half_bw, want=None):
    """-> (Band, Fehler). want: gewähltes Band (Pflicht, ganz darin); sonst das erste passende.
    Ein Signal, das den QO-100-Uplink nur teilweise trifft, ist nie zulässig (stört den Transponder-Rand)."""
    f0, f1 = freq_hz - half_bw, freq_hz + half_bw
    qlo, qhi, _ = HAM_BANDS["qo100"]
    if f0 < qhi and f1 > qlo and not (qlo <= f0 and f1 <= qhi):
        return None, "ragt über den QO-100-Breitband-Uplink (2401,5–2409,5 MHz) hinaus"
    order = [want] if want in HAM_BANDS else list(HAM_BANDS)
    for k in order:
        lo, hi, _n = HAM_BANDS[k]
        if lo <= f0 and f1 <= hi:
            return k, None
    if want in HAM_BANDS:
        return None, "liegt nicht vollständig im gewählten Band %s" % HAM_BANDS[want][2]
    return None, "liegt nicht vollständig in einem Amateurfunkband (%s)" % ", ".join(v[2] for v in HAM_BANDS.values())


def dvbs2_plframe_symbols(modulation, pilots):
    ns = 64800 // DVBS2_BITS[modulation]
    return 90 + ns + (36 * ((ns // 90 - 1) // 16) if pilots else 0)


def dvbs2_useful_bitrate(modulation, code_rate, symbol_rate, pilots):
    return Fraction(int(symbol_rate)) * (DVBS2_KBCH[code_rate] - 80) / dvbs2_plframe_symbols(modulation, pilots)


def dvbc_useful_bitrate(constellation, symbol_rate):
    return Fraction(int(symbol_rate)) * DVBC_BITS[constellation] * Fraction(188, 204)


# ---------------------------------------------------------------------------------------------
# ATSC 1.0 (A/53 Teil 2): 8-VSB, Symbolrate 684/286 * 4,5 MHz; je Halbbild 313 Segmente a 832 Symbole,
# davon 312 Datensegmente mit je einem 188-Byte-Paket -> 19 392 658,46 b/s.
# ---------------------------------------------------------------------------------------------
ATSC_SYMBOL_RATE = Fraction(4500000 * 684, 286)


def atsc_useful_bitrate():
    return ATSC_SYMBOL_RATE * Fraction(312 * 188 * 8, 313 * 832)


# ---------------------------------------------------------------------------------------------
# J.83 Annex B (SCTE 07, US-Kabel): RS(128,122) ueber GF(128), Trellis 14/15 (64-QAM) bzw. 19/20 (256-QAM),
# Rahmen 60 bzw. 88 RS-Bloecke + 42/40 Bit Sync. 64-QAM 26,970 Mb/s, 256-QAM 38,811 Mb/s.
# ---------------------------------------------------------------------------------------------
J83B = {"64-QAM": (5056941, 6, Fraction(14, 15), 60, 42), "256-QAM": (5360537, 8, Fraction(19, 20), 88, 40)}


def j83b_useful_bitrate(constellation):
    sr, b, tr, n, sync = J83B[constellation]
    return Fraction(sr) * b * tr * Fraction(n * 122 * 7, n * 128 * 7 + sync)


# ---------------------------------------------------------------------------------------------
# ISDB-T(b) 6 MHz (ARIB STD-B31 / ABNT NBR 15601): 13 Segmente; je Segment 96 * 2^(m-1) Datentraeger in
# Tu = 252 * 2^(m-1) us -> modusunabhaengig 96/252 MHz * b * CR * 188/204 / (1 + GI) je Segment.
# Beispiel 13 Segmente 64-QAM 3/4 GI 1/8: 18,25 Mb/s. Abtastrate 512/63 MHz.
# ---------------------------------------------------------------------------------------------
ISDBT_BITS = {"QPSK": 2, "16-QAM": 4, "64-QAM": 6}           # DQPSK setzt isdbt_mod nicht um
ISDBT_SAMPLE_RATE = Fraction(512000000, 63)
ISDBT_TI = (0, 1, 2, 4)                 # Zeitverschachtelung I (Mode 3: 0, 1, 2, 4)


def isdbt_layer_bitrate(segments, constellation, code_rate, guard):
    return (Fraction(96000000, 252) * int(segments) * ISDBT_BITS[constellation] * DVBT_CODE_RATES[code_rate]
            * Fraction(188, 204) / (1 + DVBT_GUARDS[guard]))


# ---------------------------------------------------------------------------------------------
# DTMB (GB 20600-2006): 7,56 MS/s, Signalrahmen = Rahmenkopf (PN 420/595/945) + Rahmenkoerper 3780 Symbole
# (36 System- + 3744 Datensymbole). LDPC (7488, 3048/4572/6096) = 4/6/8 BCH(762,752)-Bloecke.
# Beispiel 64-QAM 0,8 PN420: 32,486 Mb/s; 16-QAM 0,6 PN945: 14,438 Mb/s.
# ---------------------------------------------------------------------------------------------
DTMB_SR = 7560000
DTMB_BITS = {"4-QAM-NR": 1, "4-QAM": 2, "16-QAM": 4, "32-QAM": 5, "64-QAM": 6}
DTMB_RATES = {"0.4": 3008, "0.6": 4512, "0.8": 6016}
DTMB_HEADERS = (420, 595, 945)
DTMB_ALLOWED = {"4-QAM-NR": ("0.8",), "4-QAM": ("0.4", "0.6", "0.8"), "16-QAM": ("0.4", "0.6", "0.8"), "32-QAM": ("0.8",),
                "64-QAM": ("0.4", "0.6", "0.8")}


def dtmb_useful_bitrate(constellation, code_rate, header):
    return Fraction(DTMB_SR, 3780 + int(header)) * Fraction(3744 * DTMB_BITS[constellation], 7488) * DTMB_RATES[code_rate]


# J.83 Annex C (Japan, ISDB-C): Codierung wie Annex A, RRC 0,13; ueblich 64-QAM 5,274 MBd
J83C_SR = 5274000


def is_psip(cfg):
    """ATSC-Systemschicht (PSIP statt DVB-SI): ATSC und US-Kabel (J.83 B)."""
    return cfg.get("mode") == "atsc" or (cfg.get("mode") == "dvbc" and (cfg.get("dvbc") or {}).get("annex") == "b")


def bitrate_str(fr, decimals=1):
    """Fraction -> Dezimalstring mit festen Nachkommastellen (fuer tsp --bitrate)."""
    scale = 10 ** decimals
    v = round(fr * scale)
    return "%d.%0*d" % (v // scale, decimals, v % scale) if decimals else str(v)


def rate_table(standard):
    """Uebersicht aller Kombinationen (fuer Dashboard/README)."""
    out = []
    if standard == "dvbt":
        for bw in DVBT_BANDWIDTHS:
            for c in DVBT_BITS:
                for cr in DVBT_CODE_RATES:
                    for g in DVBT_GUARDS:
                        out.append({"bandwidth_mhz": bw, "constellation": c, "code_rate": cr, "guard": g,
                                    "bitrate_bps": float(dvbt_useful_bitrate(c, cr, g, bw))})
    else:
        for sr in (3450000, 3500000, 4000000, 5000000, 6111000, 6875000, 6900000):
            for c in DVBC_BITS:
                out.append({"symbol_rate": sr, "constellation": c,
                            "bitrate_bps": float(dvbc_useful_bitrate(c, sr))})
    return out
