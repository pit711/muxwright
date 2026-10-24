# -*- coding: utf-8 -*-
"""PAL-Normen und Kanaltabellen (Bildtraegerfrequenzen in MHz)."""

# Norm -> Tontraeger-Abstand, Restseitenband (unten), Videobandbreite (oben), Kanalbreite, Tonhub (Nennwert)
# lines/line_s/fsc/color/frames: Zeilenzahl, Zeilendauer, Farbtraeger, Farbcodierung (pal = V-Umschaltung, ntsc ohne),
# Vollbilder je Schleife (Farbtraegerphase wiederholt sich exakt); setup = Schwarzabhebung (NTSC-M USA 7,5 IRE).
FH_525 = 4.5e6 / 286          # 15734,2657 Hz (NTSC-M, PAL-M)
SYSTEMS = {
    "BG": {"sound_mhz": 5.5, "vestige_mhz": 0.75, "video_mhz": 5.0, "channel_mhz": (7, 8), "deviation_hz": 50000, "preemphasis_us": 50,
           "lines": 625, "line_s": 64e-6, "fsc": 4433618.75, "color": "pal", "frames": 4, "setup": 0.0},
    "DK": {"sound_mhz": 6.5, "vestige_mhz": 0.75, "video_mhz": 6.0, "channel_mhz": (8,), "deviation_hz": 50000, "preemphasis_us": 50,
           "lines": 625, "line_s": 64e-6, "fsc": 4433618.75, "color": "pal", "frames": 4, "setup": 0.0},
    "I": {"sound_mhz": 6.0, "vestige_mhz": 1.25, "video_mhz": 5.5, "channel_mhz": (8,), "deviation_hz": 50000, "preemphasis_us": 50,
          "lines": 625, "line_s": 64e-6, "fsc": 4433618.75, "color": "pal", "frames": 4, "setup": 0.0},
    # Amerika (untested): NTSC-M (USA, Kanada, Mexiko …), PAL-M (Brasilien), PAL-N (Argentinien, Paraguay, Uruguay)
    "NTSC_M": {"sound_mhz": 4.5, "vestige_mhz": 0.75, "video_mhz": 4.2, "channel_mhz": (6,), "deviation_hz": 25000, "preemphasis_us": 75,
               "lines": 525, "line_s": 1 / FH_525, "fsc": 227.5 * FH_525, "color": "ntsc", "frames": 2, "setup": 0.0536},
    "PAL_M": {"sound_mhz": 4.5, "vestige_mhz": 0.75, "video_mhz": 4.2, "channel_mhz": (6,), "deviation_hz": 25000, "preemphasis_us": 75,
              "lines": 525, "line_s": 1 / FH_525, "fsc": 227.25 * FH_525, "color": "pal", "frames": 4, "setup": 0.0},
    # Japan (untested): NTSC-J = NTSC-M ohne Schwarzabhebung (0 IRE)
    "NTSC_J": {"sound_mhz": 4.5, "vestige_mhz": 0.75, "video_mhz": 4.2, "channel_mhz": (6,), "deviation_hz": 25000, "preemphasis_us": 75,
               "lines": 525, "line_s": 1 / FH_525, "fsc": 227.5 * FH_525, "color": "ntsc", "frames": 2, "setup": 0.0},
    "PAL_N": {"sound_mhz": 4.5, "vestige_mhz": 0.75, "video_mhz": 4.2, "channel_mhz": (6,), "deviation_hz": 25000, "preemphasis_us": 75,
              "lines": 625, "line_s": 64e-6, "fsc": 3582056.25, "color": "pal", "frames": 4, "setup": 0.0},
}
UNTESTED = ("NTSC_M", "PAL_M", "PAL_N", "NTSC_J")
FSC = 4433618.75          # PAL-Farbhilfstraeger (625er Normen B/G, D/K, I)
LINE_S = 64e-6
LINES = 625
FS_525 = 508 * FH_525     # 7,993007 MS/s: ganze Abtastwerte je Zeile (525er Schleife nahtlos)

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
# Amerika, 6-MHz-Raster (FCC/ITU Region 2): Bildtraeger = Kanalunterkante + 1,25 MHz. A2-A6, A7-A13 VHF, A14-A69 UHF
for _n, _m in ((2, 55.25), (3, 61.25), (4, 67.25), (5, 77.25), (6, 83.25)):
    CHANNELS["A%d" % _n] = _m
for _n in range(7, 14):
    CHANNELS["A%d" % _n] = 175.25 + 6.0 * (_n - 7)
for _n in range(14, 70):
    CHANNELS["A%d" % _n] = 471.25 + 6.0 * (_n - 14)
# China (PAL-D/K, GY/T 106): DS1-DS5 VHF I, DS6-DS12 VHF III, DS13-DS24 UHF IV, DS25-DS68 UHF V (8-MHz-Raster)
for _n, _m in ((1, 49.75), (2, 57.75), (3, 65.75), (4, 77.25), (5, 85.25)):
    CHANNELS["DS%d" % _n] = _m
for _n in range(6, 13):
    CHANNELS["DS%d" % _n] = 168.25 + 8.0 * (_n - 6)
for _n in range(13, 25):
    CHANNELS["DS%d" % _n] = 471.25 + 8.0 * (_n - 13)
for _n in range(25, 69):
    CHANNELS["DS%d" % _n] = 607.25 + 8.0 * (_n - 25)
# Japan (NTSC-J): J1-J3 VHF-Low, J4-J12 VHF-High, J13-J62 UHF
for _n, _m in ((1, 91.25), (2, 97.25), (3, 103.25), (4, 171.25), (5, 177.25), (6, 183.25), (7, 189.25), (8, 193.25), (9, 199.25),
               (10, 205.25), (11, 211.25), (12, 217.25)):
    CHANNELS["J%d" % _n] = _m
for _n in range(13, 63):
    CHANNELS["J%d" % _n] = 471.25 + 6.0 * (_n - 13)


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


def loop_s(system):
    s = SYSTEMS[system]
    return s["frames"] * s["lines"] * s["line_s"]


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
    if s["lines"] == 525:          # ganze Zeilenfrequenz-Vielfache: Bild- und Tontraeger mit ganzer Periodenzahl je Schleife
        return float(round(off / FH_525) * FH_525)
    return float(int(round(off / 25000.0)) * 25000)
