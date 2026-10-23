# -*- coding: utf-8 -*-
"""PAL-Normen und Kanaltabellen (Bildtraegerfrequenzen in MHz)."""

# Norm -> Tontraeger-Abstand, Restseitenband (unten), Videobandbreite (oben), Kanalbreite, Tonhub (Nennwert)
SYSTEMS = {
    "BG": {"sound_mhz": 5.5, "vestige_mhz": 0.75, "video_mhz": 5.0, "channel_mhz": (7, 8), "deviation_hz": 50000},
    "DK": {"sound_mhz": 6.5, "vestige_mhz": 0.75, "video_mhz": 6.0, "channel_mhz": (8,), "deviation_hz": 50000},
    "I": {"sound_mhz": 6.0, "vestige_mhz": 1.25, "video_mhz": 5.5, "channel_mhz": (8,), "deviation_hz": 50000},
}
FSC = 4433618.75          # PAL-Farbhilfstraeger
LINE_S = 64e-6
LINES = 625

# Kanaele (Bildtraeger, MHz). VHF Band I/III 7-MHz-Raster (E2-E12), UHF 8 MHz (K21-K69),
# Kabel (analoges deutsches Raster): S02-S10 (7 MHz, 112,25 ...), K05-K12 wie E5-E12, S11-S20 (7 MHz, 231,25 ...),
# S21-S41 (8 MHz, 303,25 ... 463,25).
CHANNELS = {"E2": 48.25, "E3": 55.25, "E4": 62.25}
for _n in range(5, 13):
    CHANNELS["E%d" % _n] = 175.25 + 7.0 * (_n - 5)
    CHANNELS["K%02d" % _n] = 175.25 + 7.0 * (_n - 5)
for _n in range(21, 70):
    CHANNELS["K%d" % _n] = 471.25 + 8.0 * (_n - 21)
for _n in range(2, 11):
    CHANNELS["S%02d" % _n] = 112.25 + 7.0 * (_n - 2)
for _n in range(11, 21):
    CHANNELS["S%d" % _n] = 231.25 + 7.0 * (_n - 11)
for _n in range(21, 42):
    CHANNELS["S%d" % _n] = 303.25 + 8.0 * (_n - 21)


def channel_lookup(name):
    if not name:
        return None
    n = str(name).strip().upper()
    cands = [n]
    if len(n) >= 2 and n[1:].isdigit():
        cands += [n[0] + "%02d" % int(n[1:]), n[0] + "%d" % int(n[1:])]
    for c in cands:
        if c in CHANNELS:
            return int(round(CHANNELS[c] * 1e6))
    return None


def channel_table():
    return [{"channel": k, "vision_mhz": round(v, 3)} for k, v in CHANNELS.items()]


def default_offset(system, fs):
    """Bildtraeger im Basisband bei -offset: mittig zwischen unterer Restseitenband-Kante (-vestige-0,5 MHz) und
    Tontraeger (+sound+0,1 MHz), gerundet auf 25 kHz (ganze Periodenzahl je Vollbild)."""
    s = SYSTEMS[system]
    lo = -(s["vestige_mhz"] + 0.5) * 1e6
    hi = (s["sound_mhz"] + 0.1) * 1e6
    off = (lo + hi) / 2.0
    if system == "BG" and fs >= 8e6:
        off = 2.25e6               # am LG-Fernseher erprobter Wert (pal_bg_gen.py)
    if system == "BG" and fs < 8e6:
        off = 2.1e6                # 7 MS/s: Ton bei +3,4 MHz, Restseitenband bis -3,35 MHz
    return float(int(round(off / 25000.0)) * 25000)
