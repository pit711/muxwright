#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RDS-Encoder (EN 50067:1998 / IEC 62106) – Bitebene und Gruppenplaner.

Vorbilder (GPL-3.0, siehe NOTICE.md): PiFmRds (CRC, Offsetwoerter, Gruppen 0A/2A/4A), PiFmAdv (Flags, AF-Liste),
MiniRDS (1A, 3A, 10A, 11A RT+, 15A LPS, Prioritaeten/Wiederholraten, Offsetwort C'). EON (14A/14B) nach EN 50067 3.1.5.19.

Schnittstelle:
  enc = RdsEncoder(cfg)            # cfg = normalisierter Block fm.rds (siehe fm_tx.normalize_fm)
  bits = enc.bits(n, t_utc)        # n Rohbits (0/1, uint8), t_utc = UTC-Zeit des ersten Bits (fuer CT)
  enc.update({...})                # Laufzeitaenderung (PS, RT, RT+, TA, PTY, TP, MS, CT, PTYN, AF, LPS, EON-TA)
  enc.stats()                      # Gruppenzaehler
Die Differenzcodierung macht diff_encode(); Biphase/Formfilter liegen in dsp.py.
"""
import datetime
import math
import time

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

# --------------------------------------------------------------------------------------------
# Blockcode (EN 50067 Abschnitt 2, Annex A/B) – nach PiFmRds src/rds.c (GPL-3.0, (c) 2014 Christophe Jacquet)
# --------------------------------------------------------------------------------------------
POLY = 0x1B9          # x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1 (ohne x^10-Bit)
POLY_DEG = 10
OFFSET_WORDS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "D": 0x1B4, "C'": 0x350}
SYNDROMES = {"A": 383, "B": 14, "C": 303, "D": 663, "C'": 748}
BIT_RATE = 1187.5
GROUP_BITS = 104
GROUP_S = GROUP_BITS / BIT_RATE   # 87,58 ms


def crc10(block):
    """Pruefwort (10 Bit) eines 16-Bit-Blocks, klassische CRC-Rechnung wie in PiFmRds."""
    crc = 0
    for _ in range(16):
        bit = (block & 0x8000) != 0
        block = (block << 1) & 0xFFFF
        msb = (crc >> (POLY_DEG - 1)) & 1
        crc = (crc << 1) & 0x3FF
        if msb ^ bit:
            crc ^= POLY
    return crc


def syndrome(word26):
    """Syndrom eines 26-Bit-Worts (fuer Selbsttest/Decoder): Datenbits und Pruefbits gemeinsam durch den Generator."""
    reg = 0
    for i in range(25, -1, -1):
        bit = (word26 >> i) & 1
        msb = (reg >> 9) & 1
        reg = ((reg << 1) & 0x3FF) | 0
        if msb ^ bit:
            reg ^= POLY
    # Bei dieser Rechnung ist das Syndrom eines fehlerfreien Blocks mit Offset = Syndrom des Offsets
    return reg


def group_to_bits(blocks, version_b=False):
    """4 Bloecke (16 Bit) -> 104 Bit (MSB zuerst), Pruefwort = CRC xor Offsetwort; Typ B: C' fuer Block 3."""
    out = []
    names = ["A", "B", "C'" if version_b else "C", "D"]
    for i, blk in enumerate(blocks):
        blk &= 0xFFFF
        check = crc10(blk) ^ OFFSET_WORDS[names[i]]
        for j in range(15, -1, -1):
            out.append((blk >> j) & 1)
        for j in range(9, -1, -1):
            out.append((check >> j) & 1)
    return out


def diff_encode(bits, state):
    """Differenzcodierung out[i] = out[i-1] xor in[i] (EN 50067 1.7). Rueckgabe (out, neuer Zustand)."""
    if np is not None:
        b = np.asarray(bits, dtype=np.uint8)
        out = (np.cumsum(b, dtype=np.int64) + state) & 1
        out = out.astype(np.uint8)
        return out, int(out[-1]) if len(out) else state
    out = []
    for x in bits:
        state ^= int(x)
        out.append(state)
    return out, state


# --------------------------------------------------------------------------------------------
# Zeichensatz EN 50067 Annex E (Tabelle E.1) – wie redsea tables.cc; 0x20..0x7E weitgehend ASCII
# --------------------------------------------------------------------------------------------
_HI = (
    "áàéèíìóòúùÑÇŞß¡Ĳ"   # 0x80
    "âäêëîïôöûüñçşğıĳ"   # 0x90
    "ªα©‰Ǧěňőπ€£$←↑→↓"   # 0xA0
    "º¹²³±İńűµ¿÷°¼½¾§"   # 0xB0
    "ÁÀÉÈÍÌÓÒÚÙŘČŠŽĐĿ"   # 0xC0
    "ÂÄÊËÎÏÔÖÛÜřčšžđŀ"   # 0xD0
    "ÃÅÆŒŷÝÕØÞŊŔĆŚŹŦð"   # 0xE0
    "ãåæœŵýõøþŋŕćśźŧ"    # 0xF0 (0xFF unbelegt)
)
RDS_CHARS = {}
for _i in range(0x20, 0x7F):
    RDS_CHARS[_i] = chr(_i)
RDS_CHARS[0x24] = "¤"
RDS_CHARS[0x5E] = "―"
RDS_CHARS[0x60] = "‖"
RDS_CHARS[0x7E] = "¯"
for _i, _ch in enumerate(_HI):
    RDS_CHARS[0x80 + _i] = _ch
CHAR_TO_RDS = {v: k for k, v in RDS_CHARS.items()}
CHAR_TO_RDS.update({"$": 0x24, "^": 0x5E, "`": 0x60, "~": 0x7E, "\r": 0x0D, "\n": 0x0A})
_FALLBACK = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", " ": " "}


def encode_text(s, length, terminator=None):
    """Text -> RDS-Codes (Liste von Bytes) mit fester Laenge, Fuellung mit Leerzeichen.
    terminator (z. B. 0x0D fuer RT) wird angehaengt, wenn der Text kuerzer als length ist."""
    out = []
    for ch in str(s or ""):
        if len(out) >= length:
            break
        if ch in CHAR_TO_RDS:
            out.append(CHAR_TO_RDS[ch])
        elif ch in _FALLBACK:
            out.extend(CHAR_TO_RDS.get(c, 0x3F) for c in _FALLBACK[ch])
        else:
            import unicodedata
            base = unicodedata.normalize("NFKD", ch)
            out.append(CHAR_TO_RDS.get(base[:1], 0x3F) if base else 0x3F)
    out = out[:length]
    if terminator is not None and len(out) < length:
        out.append(terminator)
    while len(out) < length:
        out.append(0x20)
    return out


def decode_text(codes):
    return "".join(RDS_CHARS.get(c, "?") if c >= 0x20 else ("" if c in (0x0D, 0x0A, 0x00, 0x0B, 0x1F) else "?")
                   for c in codes)


# --------------------------------------------------------------------------------------------
# AF-Codes (EN 50067 3.2.1.6): 1..204 = 87,6..107,9 MHz, 205 Fueller, 224+n Anzahl, 250 LF/MF-Folgekennung
# --------------------------------------------------------------------------------------------
AF_FILLER = 205
AF_NONE = 224


def af_code(mhz):
    n = int(round((float(mhz) - 87.5) * 10))
    if 1 <= n <= 204:
        return n
    return None


def af_mhz(code):
    return round(87.5 + code / 10.0, 1) if 1 <= code <= 204 else None


def af_list_codes(afs, method="A", tuned_mhz=None, regional=()):
    """Liste der AF-Codes fuer Gruppe 0A/0B (Paare), Methode A oder B.
    Methode A: [224+n, f1, f2, ..., (205)]   n = Zahl der Frequenzen
    Methode B: [224+n, f_tuned, (f_tuned,f1 aufsteigend = gleiches Programm) | (f1,f_tuned absteigend = Regionalvariante) ...]"""
    codes = [c for c in (af_code(f) for f in afs or []) if c]
    reg = [c for c in (af_code(f) for f in regional or []) if c]
    if method.upper() == "B" and tuned_mhz is not None and af_code(tuned_mhz):
        t = af_code(tuned_mhz)
        pairs = []
        for c in codes:
            if c == t:
                continue
            pairs.append((min(t, c), max(t, c)))          # aufsteigend: gleiches Programm
        for c in reg:
            if c == t:
                continue
            pairs.append((max(t, c), min(t, c)))          # absteigend: Regionalvariante
        pairs = pairs[:12]
        n = 1 + 2 * len(pairs)
        out = [AF_NONE + n, t]
        for a, b in pairs:
            out += [a, b]
        return out
    codes = codes[:25]
    if not codes:
        return []
    out = [AF_NONE + len(codes)] + codes
    if len(out) % 2:
        out.append(AF_FILLER)
    return out


# --------------------------------------------------------------------------------------------
# RT+ Inhaltstypen (RDS Forum RT+ Spezifikation; identisch zu DL Plus 0..63)
# --------------------------------------------------------------------------------------------
RTPLUS_TYPES = {0: "DUMMY", 1: "ITEM.TITLE", 2: "ITEM.ALBUM", 3: "ITEM.TRACKNUMBER", 4: "ITEM.ARTIST",
                5: "ITEM.COMPOSITION", 6: "ITEM.MOVEMENT", 7: "ITEM.CONDUCTOR", 8: "ITEM.COMPOSER",
                9: "ITEM.BAND", 10: "ITEM.COMMENT", 11: "ITEM.GENRE", 12: "INFO.NEWS", 13: "INFO.NEWS.LOCAL",
                14: "INFO.STOCKMARKET", 15: "INFO.SPORT", 16: "INFO.LOTTERY", 17: "INFO.HOROSCOPE",
                18: "INFO.DAILY_DIVERSION", 19: "INFO.HEALTH", 20: "INFO.EVENT", 21: "INFO.SCENE",
                22: "INFO.CINEMA", 23: "INFO.STUPIDITY_MACHINE", 24: "INFO.DATE_TIME", 25: "INFO.WEATHER",
                26: "INFO.TRAFFIC", 27: "INFO.ALARM", 28: "INFO.ADVERTISEMENT", 29: "INFO.URL",
                30: "INFO.OTHER", 31: "STATIONNAME.SHORT", 32: "STATIONNAME.LONG", 33: "PROGRAMME.NOW",
                34: "PROGRAMME.NEXT", 35: "PROGRAMME.PART", 36: "PROGRAMME.HOST",
                37: "PROGRAMME.EDITORIAL_STAFF", 38: "PROGRAMME.FREQUENCY", 39: "PROGRAMME.HOMEPAGE",
                40: "PROGRAMME.SUBCHANNEL", 41: "PHONE.HOTLINE", 42: "PHONE.STUDIO", 43: "PHONE.OTHER",
                44: "SMS.STUDIO", 45: "SMS.OTHER", 46: "EMAIL.HOTLINE", 47: "EMAIL.STUDIO", 48: "EMAIL.OTHER",
                49: "MMS.OTHER", 50: "CHAT", 51: "CHAT.CENTER", 52: "VOTE.QUESTION", 53: "VOTE.CENTRE",
                59: "PRIVATE.1", 60: "PRIVATE.2", 61: "PRIVATE.3", 62: "DESCRIPTOR.PLACE",
                63: "DESCRIPTOR.APPOINTMENT"}
RTPLUS_NAMES = {v: k for k, v in RTPLUS_TYPES.items()}
AID_RTPLUS = 0x4BD7
AID_ERT = 0x6552

PTY_RDS = ["None", "News", "Current affairs", "Information", "Sport", "Education", "Drama", "Culture", "Science",
           "Varied", "Pop music", "Rock music", "Easy listening", "Light classical", "Serious classical",
           "Other music", "Weather", "Finance", "Children's programmes", "Social affairs", "Religion", "Phone-in",
           "Travel", "Leisure", "Jazz music", "Country music", "National music", "Oldies music", "Folk music",
           "Documentary", "Alarm test", "Alarm"]


def rtplus_type(v):
    if isinstance(v, int):
        return v & 63
    s = str(v).strip().upper()
    if s.isdigit():
        return int(s) & 63
    if s in RTPLUS_NAMES:
        return RTPLUS_NAMES[s]
    raise ValueError("RT+-Typ unbekannt: %r" % v)


def mjd_from_date(y, m, d):
    """Modified Julian Day (EN 50067 Annex G)."""
    l = 1 if m <= 2 else 0
    return 14956 + d + int((y - 1900 - l) * 365.25) + int((m + 1 + l * 12) * 30.6001)


def parse_pin(v):
    """PIN: 'DD:HH:MM' oder int (Tag<<11 | Stunde<<6 | Minute) oder None."""
    if v in (None, "", 0):
        return 0
    if isinstance(v, int):
        return v & 0xFFFF
    parts = [int(x) for x in str(v).replace("-", ":").split(":")]
    while len(parts) < 3:
        parts.append(0)
    d, h, mi = parts[:3]
    return ((d & 31) << 11) | ((h & 31) << 6) | (mi & 63)


# --------------------------------------------------------------------------------------------
# Encoder
# --------------------------------------------------------------------------------------------
DEFAULT_RATES = {"other_every": 1,      # vor jeder Gruppe pruefen, ob eine Sondergruppe faellig ist (wie MiniRDS)
                 "1a": 60, "3a": 20, "10a": 10, "11a": 30, "14a": 8, "15a": 20, "ta_burst": 8}


class RdsEncoder:
    def __init__(self, cfg, log=None, immediate_ct=False):
        self.log = log or (lambda *a, **k: None)
        self.c = {}
        self.ab = 0            # RT A/B-Flag
        self.ptyn_ab = 0
        self.item_toggle = 0
        self.item_running = 1
        self.ps_seg = 0
        self.rt_seg = 0
        self.af_pos = 0
        self.ptyn_seg = 0
        self.lps_seg = 0
        self.eon_idx = 0
        self.eon_var = 0
        self.oda_idx = 0
        self.lic_toggle = 0
        self.state = 0         # 0 = 0A, 1 = 2A
        self.counters = {}
        self.group_count = 0
        self.stat = {}
        self.last_ct_minute = None
        self.immediate_ct = immediate_ct
        self.pending = []      # vorrangige Gruppen (z. B. 14B-Burst)
        self.bitbuf = []
        self.bit_index = 0     # Bits seit Start (fuer die Zeitrechnung)
        self.rt_codes = [0x20] * 64
        self.rt_segments = 16
        self.lps_bytes = b""
        self.lps_segments = 0
        self.apply(cfg, initial=True)

    # -- Konfiguration / Laufzeitaenderungen --
    def apply(self, cfg, initial=False):
        c = self.c
        for k, v in cfg.items():
            c[k] = v
        c.setdefault("pi", 0xD3C4)
        c.setdefault("ps", "DABLAB")
        c.setdefault("rt", "")
        c.setdefault("rtplus", [])
        c.setdefault("pty", 0)
        c.setdefault("tp", True)
        c.setdefault("ta", False)
        c.setdefault("ms", True)
        c.setdefault("di", 1)
        c.setdefault("af", [])
        c.setdefault("af_method", "A")
        c.setdefault("af_regional", [])
        c.setdefault("tuned_mhz", None)
        c.setdefault("ecc", None)
        if str(c.get("standard", "rds")).lower() == "rbds":
            c["ecc"] = None                      # RBDS (NRSC-4-B): kein Extended Country Code
        c.setdefault("lic", None)
        c.setdefault("pin", 0)
        c.setdefault("ct", True)
        c.setdefault("utc_offset_halfhours", "auto")
        c.setdefault("ptyn", "")
        c.setdefault("lps", "")
        c.setdefault("eon", [])
        c.setdefault("group_version", "A")
        c.setdefault("rt_version", "A")
        c.setdefault("rt_full_segments", False)
        c.setdefault("oda", [])
        rates = dict(DEFAULT_RATES)
        rates.update(c.get("rates") or {})
        c["rates"] = rates
        if initial or "ps" in cfg:
            self.ps_codes = encode_text(c["ps"], 8)
            self.ps_seg = 0
        if initial or "rt" in cfg or "rt_version" in cfg or "rt_full_segments" in cfg:
            self._set_rt(c["rt"], flip=not initial)
        if initial or "rtplus" in cfg:
            self._set_rtplus(c["rtplus"], flip_item=("rtplus" in cfg and not initial and cfg.get("rtplus_new_item", True)))
        if initial or "ptyn" in cfg:
            self.ptyn_codes = encode_text(c["ptyn"], 8) if c["ptyn"] else None
            if not initial:
                self.ptyn_ab ^= 1
        if initial or "lps" in cfg:
            b = str(c["lps"] or "").encode("utf-8")[:32]
            if b and len(b) < 32:
                b += b"\r"                       # Abschluss wie beim RT; redsea erwartet CR oder 32 Byte
            self.lps_bytes = b
            self.lps_segments = (len(b) + 3) // 4 if b else 0
            self.lps_seg = 0
        if initial or "af" in cfg or "af_method" in cfg or "af_regional" in cfg or "tuned_mhz" in cfg:
            self.af_codes = af_list_codes(c["af"], c["af_method"], c["tuned_mhz"], c["af_regional"])
            self.af_pos = 0
        if initial or "eon" in cfg:
            self.eon = [self._norm_eon(e) for e in (c["eon"] or [])]
            self.eon_idx = self.eon_var = 0
        if initial or "oda" in cfg or "rtplus" in cfg:
            self.odas = []
            if self.rtplus_tags:
                self.odas.append((11, 0, 0x0000, AID_RTPLUS))
            for o in c.get("oda") or []:
                self.odas.append((int(o.get("group", 11)), int(o.get("version", 0)), int(o.get("message", 0)) & 0xFFFF,
                                  int(str(o.get("aid", "0")), 0) & 0xFFFF))
        if "eon_ta" in cfg:
            self._eon_ta(cfg["eon_ta"])

    update = apply

    def _set_rt(self, text, flip=True):
        c = self.c
        n = 32 if c["rt_version"].upper() == "B" else 64
        codes = encode_text(text, n, terminator=0x0D)
        self.rt_codes = codes
        per = 2 if n == 32 else 4
        segs = n // per
        if not c["rt_full_segments"]:
            try:
                last = codes.index(0x0D)
                segs = min(segs, last // per + 1)
            except ValueError:
                pass
        self.rt_segments = max(1, segs)
        self.rt_seg = 0
        if flip:
            self.ab ^= 1

    def _set_rtplus(self, tags, flip_item=False):
        """tags: [{"type": "ITEM.TITLE"|1, "start": n, "len": m} | {"type":..., "text": "..."}]; Text wird im RT gesucht."""
        rt_text = decode_text(self.rt_codes)
        out = []
        for t in tags or []:
            if not t:
                continue
            ty = rtplus_type(t.get("type", 0))
            if t.get("text") is not None and "start" not in t:
                s = rt_text.find(str(t["text"]))
                if s < 0:
                    continue
                st, ln = s, len(str(t["text"]))
            else:
                st, ln = int(t.get("start", 0)), int(t.get("len", t.get("length", 0)))
            if ln <= 0:
                continue
            out.append((ty, min(st, 63), ln))
        self.rtplus_tags = out[:2] if out else []
        if len(out) == 1:
            self.rtplus_tags = out
        if flip_item:
            self.item_toggle ^= 1
        self.item_running = 1 if (self.c.get("rtplus_running", True)) else 0

    def _norm_eon(self, e):
        e = dict(e)
        e["pi"] = int(str(e.get("pi", "0")), 0) & 0xFFFF
        e["ps_codes"] = encode_text(e.get("ps", ""), 8) if e.get("ps") else None
        e["pty"] = int(e.get("pty", 0)) & 31
        e["tp"] = bool(e.get("tp", True))
        e["ta"] = bool(e.get("ta", False))
        e["pin"] = parse_pin(e.get("pin"))
        afc = [c for c in (af_code(f) for f in e.get("af") or []) if c]
        pairs = []
        if afc:
            pairs.append((AF_NONE + len(afc), afc[0]))
            rest = afc[1:]
            for i in range(0, len(rest), 2):
                pairs.append((rest[i], rest[i + 1] if i + 1 < len(rest) else AF_FILLER))
        e["af_pairs"] = pairs
        mapped = []
        for tun, lst in (e.get("mapped_af") or {}).items():
            tc = af_code(float(tun))
            for f in lst or []:
                fc = af_code(f)
                if tc and fc:
                    mapped.append((tc, fc))
        e["mapped"] = mapped[:4]   # Varianten 5..8
        e["linkage"] = int(e.get("linkage", 0)) & 0xFFFF
        # Variantenfolge fuer diese ON
        var = []
        if e["ps_codes"]:
            var += [0, 1, 2, 3]
        var += [4 + i for i in range(len(pairs))] if pairs else []
        var += [("map", i) for i in range(len(mapped))]
        var.append(13)
        if e["pin"]:
            var.append(14)
        if e["linkage"]:
            var.append(12)
        e["variants"] = var
        return e

    def _eon_ta(self, d):
        """TA-Umschaltung eines anderen Programms: 14B-Burst (EN 50067 3.1.5.19) und 14A-Variante 13 aktualisieren."""
        pi = int(str(d.get("pi", "0")), 0) & 0xFFFF
        active = bool(d.get("active", True))
        for e in self.eon:
            if e["pi"] == pi:
                e["ta"] = active
        n = int(self.c["rates"].get("ta_burst", 8))
        tp_on = next((e["tp"] for e in self.eon if e["pi"] == pi), True)
        for i in range(n):
            b1 = (14 << 12) | (1 << 11) | (int(bool(self.c["tp"])) << 10) | (int(self.c["pty"]) << 5) \
                 | (int(tp_on) << 4) | (int(active) << 3)
            # Burst ueber ca. 1 s verteilen: nach jeder 14B zwei normale Gruppen
            self.pending.append(("14B", [self.c["pi"], b1, self.c["pi"], pi], True))
            self.pending.append(None)
            self.pending.append(None)

    # -- Gruppen --
    def _hdr(self, gtype, version_b=False):
        return (gtype << 12) | (int(version_b) << 11) | (int(bool(self.c["tp"])) << 10) | ((int(self.c["pty"]) & 31) << 5)

    def _group_0(self):
        c = self.c
        vb = c["group_version"].upper() == "B"
        seg = self.ps_seg
        di = c["di"]
        if isinstance(di, dict):
            di = (int(bool(di.get("dynamic_pty", False))) << 3) | (int(bool(di.get("compressed", False))) << 2) \
                 | (int(bool(di.get("artificial_head", False))) << 1) | int(bool(di.get("stereo", True)))
        di_bit = (int(di) >> (3 - seg)) & 1
        b1 = self._hdr(0, vb) | (int(bool(c["ta"])) << 4) | (int(bool(c["ms"])) << 3) | (di_bit << 2) | seg
        if vb:
            b2 = c["pi"]
        elif self.af_codes:
            b2 = (self.af_codes[self.af_pos] << 8) | self.af_codes[self.af_pos + 1]
            self.af_pos = (self.af_pos + 2) % len(self.af_codes)
        else:
            b2 = (AF_NONE << 8) | AF_FILLER   # keine AF: 224, Fueller
        b3 = (self.ps_codes[seg * 2] << 8) | self.ps_codes[seg * 2 + 1]
        self.ps_seg = (seg + 1) % 4
        return ("0B" if vb else "0A", [c["pi"], b1, b2, b3], vb)

    def _group_2(self):
        c = self.c
        vb = c["rt_version"].upper() == "B"
        seg = self.rt_seg
        b1 = self._hdr(2, vb) | (self.ab << 4) | seg
        if vb:
            b2 = c["pi"]
            b3 = (self.rt_codes[seg * 2] << 8) | self.rt_codes[seg * 2 + 1]
        else:
            b2 = (self.rt_codes[seg * 4] << 8) | self.rt_codes[seg * 4 + 1]
            b3 = (self.rt_codes[seg * 4 + 2] << 8) | self.rt_codes[seg * 4 + 3]
        self.rt_seg = (seg + 1) % self.rt_segments
        return ("2B" if vb else "2A", [c["pi"], b1, b2, b3], vb)

    def _group_1(self):
        c = self.c
        b1 = self._hdr(1) | 0   # Radio Paging Codes = 0
        if c["lic"] is not None and self.lic_toggle:
            b2 = (3 << 12) | (int(c["lic"]) & 0xFFF)          # Variante 3: Sprachcode
        else:
            b2 = (0 << 12) | (int(c["ecc"] or 0) & 0xFF)       # Variante 0: ECC (LA=0, Paging 0)
        if c["lic"] is not None:
            self.lic_toggle ^= 1
        b3 = parse_pin(c["pin"])
        return ("1A", [c["pi"], b1, b2, b3], False)

    def _group_3(self):
        g, v, msg, aid = self.odas[self.oda_idx % len(self.odas)]
        self.oda_idx += 1
        b1 = self._hdr(3) | ((g & 15) << 1) | (v & 1)
        return ("3A", [self.c["pi"], b1, msg, aid], False)

    def _group_4(self, t_utc):
        """CT: Zeit der Minutenkante (t_utc ist die Zeit des Gruppenendes bzw. der Kante)."""
        c = self.c
        dt = datetime.datetime.fromtimestamp(t_utc, tz=datetime.timezone.utc)
        mjd = mjd_from_date(dt.year, dt.month, dt.day)
        off = c["utc_offset_halfhours"]
        if off in (None, "auto"):
            lt = time.localtime(t_utc)
            off = int(round(lt.tm_gmtoff / 1800.0))
        off = int(off)
        b1 = self._hdr(4) | ((mjd >> 15) & 3)
        b2 = ((mjd & 0x7FFF) << 1) | (dt.hour >> 4)
        b3 = ((dt.hour & 0xF) << 12) | (dt.minute << 6) | ((1 if off < 0 else 0) << 5) | (abs(off) & 31)
        self.stat["ct_last"] = dt.strftime("%Y-%m-%d %H:%M UTC") + " offset %+d x 30 min" % off
        return ("4A", [c["pi"], b1, b2, b3], False)

    def _group_10(self):
        seg = self.ptyn_seg
        b1 = self._hdr(10) | (self.ptyn_ab << 4) | seg
        p = self.ptyn_codes
        b2 = (p[seg * 4] << 8) | p[seg * 4 + 1]
        b3 = (p[seg * 4 + 2] << 8) | p[seg * 4 + 3]
        self.ptyn_seg ^= 1
        return ("10A", [self.c["pi"], b1, b2, b3], False)

    def _group_11(self):
        """RT+ (zwei Tags je Gruppe), Bitaufteilung nach MiniRDS get_rds_rtplus_group; Laenge wird als len-1 gesendet."""
        tags = list(self.rtplus_tags) + [(0, 0, 1)] * 2
        (t1, s1, l1), (t2, s2, l2) = tags[0], tags[1]
        l1 = max(0, min(63, l1 - 1))
        l2 = max(0, min(31, l2 - 1))
        b1 = self._hdr(11) | (self.item_toggle << 4) | (self.item_running << 3) | ((t1 >> 3) & 7)
        b2 = ((t1 & 7) << 13) | ((s1 & 63) << 7) | ((l1 & 63) << 1) | ((t2 >> 5) & 1)
        b3 = ((t2 & 31) << 11) | ((s2 & 63) << 5) | (l2 & 31)
        return ("11A", [self.c["pi"], b1, b2, b3], False)

    def _group_14(self):
        e = self.eon[self.eon_idx % len(self.eon)]
        var = e["variants"][self.eon_var % len(e["variants"])]
        self.eon_var += 1
        if self.eon_var >= len(e["variants"]):
            self.eon_var = 0
            self.eon_idx = (self.eon_idx + 1) % len(self.eon)
        tp_on = int(e["tp"])
        if isinstance(var, tuple):           # gemappte Frequenz, Varianten 5..8
            i = var[1]
            v = 5 + i
            tc, fc = e["mapped"][i]
            b2 = (tc << 8) | fc
        elif var <= 3:
            v = var
            b2 = (e["ps_codes"][var * 2] << 8) | e["ps_codes"][var * 2 + 1]
        elif 4 <= var <= 8 and var - 4 < len(e["af_pairs"]):
            v = 4
            a, b = e["af_pairs"][var - 4]
            b2 = (a << 8) | b
        elif var == 12:
            v = 12
            b2 = e["linkage"]
        elif var == 13:
            v = 13
            b2 = (e["pty"] << 11) | int(e["ta"])
        else:
            v = 14
            b2 = e["pin"]
        b1 = self._hdr(14) | (tp_on << 4) | v
        return ("14A", [self.c["pi"], b1, b2, e["pi"]], False)

    def _group_15(self):
        seg = self.lps_seg
        b = self.lps_bytes[seg * 4: seg * 4 + 4]
        b = b + b" " * (4 - len(b))
        b1 = self._hdr(15) | seg
        self.lps_seg = (seg + 1) % max(1, self.lps_segments)
        return ("15A", [self.c["pi"], b1, (b[0] << 8) | b[1], (b[2] << 8) | b[3]], False)

    def _other_group(self):
        """Sondergruppen nach MiniRDS-Zaehlern; None, wenn keine faellig ist."""
        c, r = self.c, self.c["rates"]
        def due(name, every):
            if not every:
                return False
            self.counters[name] = self.counters.get(name, 0) + 1
            if self.counters[name] >= int(every):
                self.counters[name] = 0
                return True
            return False
        if c["ecc"] is not None or c["lic"] is not None or parse_pin(c["pin"]):
            if due("1a", r["1a"]):
                return self._group_1()
        if self.odas and due("3a", r["3a"]):
            return self._group_3()
        if self.ptyn_codes and due("10a", r["10a"]):
            return self._group_10()
        if self.rtplus_tags and due("11a", r["11a"]):
            return self._group_11()
        if self.eon and due("14a", r["14a"]):
            return self._group_14()
        if self.lps_segments and due("15a", r["15a"]):
            return self._group_15()
        return None

    def next_group(self, t_start_utc):
        """Naechste Gruppe; t_start_utc = Sendezeit (UTC, s) des ersten Bits dieser Gruppe."""
        g = None
        # 1) CT: Gruppenende innerhalb +-halbe Gruppe an der Minutenkante (EN 50067 3.1.5.6)
        if self.c["ct"]:
            m = math.floor(t_start_utc / 60.0 + 1) * 60.0
            if m != self.last_ct_minute and (m - t_start_utc) <= GROUP_S * 1.5:
                self.last_ct_minute = m
                g = self._group_4(m)
                self.stat["ct_edge_error_ms"] = round((t_start_utc + GROUP_S - m) * 1000, 1)
            elif self.immediate_ct and self.group_count == 24:
                # Pruefbetrieb: eine CT-Gruppe nach ~2 s (aktuelle Minute), damit redsea sie in kurzen Mitschnitten sieht
                self.last_ct_minute = math.floor(t_start_utc / 60.0) * 60.0
                g = self._group_4(self.last_ct_minute)
                self.stat["ct_immediate"] = True
        # 2) vorrangige Gruppen (14B-Burst)
        while g is None and self.pending:
            p = self.pending.pop(0)
            if p is not None:
                g = p
            else:
                break
        # 3) Sondergruppen nach Zaehlern, sonst Grundfolge 0A/2A
        if g is None and self.group_count % max(1, int(self.c["rates"].get("other_every", 1))) == 0:
            g = self._other_group()
        if g is None:
            if self.state == 0:
                g = self._group_0()
                self.state = 1
            else:
                g = self._group_2()
                self.state = 0
        self.group_count += 1
        self.stat[g[0]] = self.stat.get(g[0], 0) + 1
        return g

    def bits(self, n, t_utc_first_bit):
        """n Rohbits (uint8) ab der UTC-Zeit des ersten Bits."""
        out = []
        while len(self.bitbuf) < n:
            t_g = t_utc_first_bit + (len(self.bitbuf)) / BIT_RATE
            name, blocks, vb = self.next_group(t_g)
            self.bitbuf.extend(group_to_bits(blocks, vb))
        out, self.bitbuf = self.bitbuf[:n], self.bitbuf[n:]
        self.bit_index += n
        if np is not None:
            return np.array(out, dtype=np.uint8)
        return out

    def stats(self):
        d = dict(self.stat)
        d["groups"] = self.group_count
        d["ab"] = self.ab
        d["item_toggle"] = self.item_toggle
        return d

    def snapshot(self):
        """Aktuelle Sollwerte fuer den Pruefbericht."""
        c = self.c
        return {"pi": "0x%04X" % c["pi"], "ps": decode_text(self.ps_codes), "rt": decode_text(self.rt_codes).rstrip(),
                "pty": int(c["pty"]), "tp": bool(c["tp"]), "ta": bool(c["ta"]), "ms": bool(c["ms"]),
                "af": [af_mhz(x) for x in self.af_codes if 1 <= x <= 204] if c["af_method"].upper() == "A"
                else sorted(set(af_mhz(x) for x in self.af_codes[1:] if 1 <= x <= 204)),
                "af_method": c["af_method"].upper(), "ecc": c["ecc"], "ct": bool(c["ct"]),
                "ptyn": decode_text(self.ptyn_codes).rstrip() if self.ptyn_codes else "",
                "lps": self.lps_bytes.decode("utf-8", "replace").rstrip("\r "),
                "rtplus": [{"type": RTPLUS_TYPES.get(t, t), "start": s, "len": l,
                            "text": decode_text(self.rt_codes)[s:s + l]} for t, s, l in self.rtplus_tags],
                "eon": [{"pi": "0x%04X" % e["pi"], "ps": decode_text(e["ps_codes"]) if e["ps_codes"] else "",
                         "pty": e["pty"], "tp": e["tp"], "ta": e["ta"],
                         "af": [af_mhz(x) for p in e["af_pairs"] for x in p if 1 <= x <= 204]} for e in self.eon],
                "group_version": c["group_version"].upper(), "rt_version": c["rt_version"].upper()}


# --------------------------------------------------------------------------------------------
# Selbsttest der Bitebene (Decoder-Sicht): Syndrom-Pruefung und Rueckgewinnung der Bloecke
# --------------------------------------------------------------------------------------------
def check_group_bits(bits):
    """Prueft 104 Bits: liefert (ok, blocks). ok = alle Offsets passen (A, B, C|C', D)."""
    blocks, names = [], []
    for i in range(4):
        w = 0
        for b in bits[i * 26:(i + 1) * 26]:
            w = (w << 1) | int(b)
        data, chk = w >> 10, w & 0x3FF
        found = None
        for nm, off in OFFSET_WORDS.items():
            if (crc10(data) ^ off) == chk:
                found = nm
                break
        blocks.append(data)
        names.append(found)
    ok = names[0] == "A" and names[1] == "B" and names[2] in ("C", "C'") and names[3] == "D"
    return ok, blocks, names


def selftest():
    r = {"ok": True}
    # bekannte Werte: Offsetwoerter als Bitmuster laut EN 50067 Annex A
    r["offsets_bin"] = {k: format(v, "010b") for k, v in OFFSET_WORDS.items()}
    exp = {"A": "0011111100", "B": "0110011000", "C": "0101101000", "D": "0110110100", "C'": "1101010000"}
    r["offsets_ok"] = all(r["offsets_bin"][k] == exp[k] for k in exp)
    enc = RdsEncoder({"pi": 0xD3C4, "ps": "TESTÄÖÜß", "rt": "Hallo RDS äöüß", "ecc": 0xE0,
                      "rtplus": [{"type": "ITEM.TITLE", "start": 0, "len": 5}], "ptyn": "PTYNTEST",
                      "lps": "Langer Name", "eon": [{"pi": "0xD3C5", "ps": "EONTEST", "af": [99.9]}],
                      "af": [100.0, 101.3], "ct": True}, immediate_ct=True)
    bits = enc.bits(104 * 120, time.time())
    oks, types = 0, {}
    for i in range(120):
        ok, blocks, names = check_group_bits(bits[i * 104:(i + 1) * 104])
        oks += ok
        if ok:
            g = blocks[1] >> 11
            types["%d%s" % (g >> 1, "AB"[g & 1])] = types.get("%d%s" % (g >> 1, "AB"[g & 1]), 0) + 1
    r["groups_ok"] = oks
    r["group_types"] = dict(sorted(types.items()))
    r["charset"] = {"ä": hex(CHAR_TO_RDS["ä"]), "ö": hex(CHAR_TO_RDS["ö"]), "ü": hex(CHAR_TO_RDS["ü"]),
                    "ß": hex(CHAR_TO_RDS["ß"])}
    r["charset_ok"] = (CHAR_TO_RDS["ä"], CHAR_TO_RDS["ö"], CHAR_TO_RDS["ü"], CHAR_TO_RDS["ß"]) == (0x91, 0x97, 0x99, 0x8D)
    r["mjd_2026_10_23"] = mjd_from_date(2026, 10, 23)
    r["mjd_ok"] = mjd_from_date(2000, 1, 1) == 51544
    r["af_codes_A"] = af_list_codes([100.0, 101.3])
    r["af_codes_B"] = af_list_codes([100.0, 101.3], "B", 98.5, [89.0])
    r["ok"] = bool(r["offsets_ok"] and oks == 120 and r["charset_ok"] and r["mjd_ok"] and "4A" in types
                   and "11A" in types and "14A" in types and "15A" in types and "1A" in types and "3A" in types)
    return r


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1, ensure_ascii=False))
