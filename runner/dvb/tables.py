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


def channel_lookup(standard, name):
    """-> (freq_hz, bandwidth_mhz) oder None. Namen tolerant: 'k21', 'K05', 'K5', 'S2', 's02'."""
    if not name:
        return None
    tab = DVBT_CHANNELS if standard == "dvbt" else DVBC_CHANNELS
    n = str(name).strip().upper()
    cands = [n]
    if len(n) >= 2 and n[1:].isdigit():
        cands += [n[0] + "%02d" % int(n[1:]), n[0] + "%d" % int(n[1:])]
    for c in cands:
        if c in tab:
            mhz, bw = tab[c]
            return int(round(mhz * 1e6)), bw
    return None


def channel_table(standard):
    tab = DVBT_CHANNELS if standard == "dvbt" else DVBC_CHANNELS
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


def ham_sps(symbol_rate):
    """Samples/Symbol für USB-IQ: AD9364 braucht ≥ 521 kS/s, USB schafft ≤ 7 MS/s."""
    for sps in (2, 4, 8):
        if symbol_rate * sps >= 600000:
            return sps
    return 8


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
