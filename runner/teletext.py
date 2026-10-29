# -*- coding: utf-8 -*-
"""Teletext (EBU, ETS 300 706 Level 1): Seiten, Paketcodierung und Karussell fuer DVB und PAL.

Seitenquellen
  - Dashboard: Seitennummer, Titel, bis zu 23 Zeilen Text mit Markierungen {rot}/{red} ... (s. TAGS), Fastext-Links
  - Ordner: .tti-Dateien (Teletext-Rohformat: PN/SC/PS/OL/FL) und .txt-Dateien (Name = Seitennummer, z. B. 101.txt,
    Text wie im Dashboard); der Ordner wird alle 2 s neu eingelesen
  - automatische Seite 100 (Programmuebersicht und Seitenverzeichnis), falls keine eigene Seite 100 existiert

Ausgabe
  - Carousel.next_packet(): 42-Byte-Pakete (MRAG + 40 Datenbytes) in Uebertragungsreihenfolge (Bit 0 zuerst, wie t42)
  - DVB (EN 300 472): DvbTeletextTs erzeugt einen kleinen Transportstrom (PAT, PMT mit teletext_descriptor, PES), den
    der Mux-ffmpeg als Eingang liest und unveraendert in das Programm uebernimmt
  - PAL: vbi_line() setzt ein Paket als NRZ-Signal (6,9375 Mbit/s, 66 % Weisspegel) in eine Austastzeile
"""
import os
import re
import threading
import time
import zlib

import numpy as np

# --------------------------------------------------------------------------------------------- Codierung
HAM84 = [0x15, 0x02, 0x49, 0x5E, 0x64, 0x73, 0x38, 0x2F, 0xD0, 0xC7, 0x8C, 0x9B, 0xA1, 0xB6, 0xFD, 0xEA]
REV8 = bytes(int("{:08b}".format(i)[::-1], 2) for i in range(256))


def ham(n):
    return HAM84[n & 15]


def odd(c):
    c &= 0x7F
    return c | (0x80 if bin(c).count("1") % 2 == 0 else 0)


def bcd(n):
    return int("%x" % n, 16) if isinstance(n, int) else n


# Nationale Zeichensaetze (G0 Latin, Optionen nach ETS 300 706 Tabelle 36): Unicode -> Code
CHARSETS = {
    "de": {"#": 0x23, "$": 0x24, "§": 0x40, "Ä": 0x5B, "Ö": 0x5C, "Ü": 0x5D, "^": 0x5E, "_": 0x5F, "°": 0x60,
           "ä": 0x7B, "ö": 0x7C, "ü": 0x7D, "ß": 0x7E},
    "en": {"£": 0x23, "$": 0x24, "@": 0x40, "←": 0x5B, "½": 0x5C, "→": 0x5D, "↑": 0x5E, "#": 0x5F, "—": 0x60,
           "¼": 0x7B, "‖": 0x7C, "¾": 0x7D, "÷": 0x7E},
}
NATIONAL_BITS = {"en": 0, "de": 4}          # C12..C14 (Kopfzeilenbyte 13, Bits D2..D4)
_FALLBACK = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "é": "e", "è": "e", "à": "a",
             "@": "(at)", "[": "(", "]": ")", "{": "(", "}": ")", "|": "/", "\\": "/", "~": "-", "`": "'", "€": "EUR"}
_VARIABLE = set(range(0x23, 0x25)) | {0x40} | set(range(0x5B, 0x61)) | set(range(0x7B, 0x7F))

# Markierungen (Dashboard/.txt): je Markierung eine Zeichenzelle (Steuerzeichen werden als Leerzeichen dargestellt)
TAGS = {"red": 1, "green": 2, "yellow": 3, "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
        "rot": 1, "gruen": 2, "grün": 2, "gelb": 3, "blau": 4, "weiss": 7, "weiß": 7,
        "flash": 8, "blink": 8, "steady": 9, "normal": 12, "double": 13, "dh": 13, "doppelt": 13,
        "black-bg": 28, "nobg": 28, "new-bg": 29, "bg": 29, "hold": 30, "release": 31}
TAG_RE = re.compile(r"\{([a-zA-Zäöüß\-]+)\}")


def encode_text(s, charset="de"):
    """Text mit {farbe}-Markierungen -> Teletext-Codes (ohne Paritaet), hoechstens 40."""
    cs = CHARSETS.get(charset, CHARSETS["de"])
    out = []
    pos = 0
    for m in list(TAG_RE.finditer(s)) + [None]:
        seg = s[pos:m.start()] if m else s[pos:]
        for ch in seg:
            if ch in cs:
                out.append(cs[ch])
            elif 0x20 <= ord(ch) < 0x7F and ord(ch) not in _VARIABLE:
                out.append(ord(ch))
            else:
                out.extend(ord(x) for x in _FALLBACK.get(ch, "?") if 0x20 <= ord(x) < 0x7F and ord(x) not in _VARIABLE)
        if m:
            code = TAGS.get(m.group(1).lower())
            if code is None:
                out.extend(ord(x) for x in m.group(0) if ord(x) not in _VARIABLE)
            else:
                out.append(code)
            pos = m.end()
    return out[:40]


def visible_len(s):
    """Zeichenzellen einer Dashboard-Zeile (Markierung = 1 Zelle)."""
    return len(TAG_RE.sub("#", s))


# --------------------------------------------------------------------------------------------- Seiten
class Page:
    """Eine (Unter-)Seite: page = 0x100..0x8FF (Magazin = page >> 8, 8 -> 0), rows[1..24] = Liste von Codes."""

    def __init__(self, page, subcode=0, rows=None, links=None, title=""):
        self.page = int(page)
        self.subcode = int(subcode)
        self.rows = rows or {}
        self.links = links                      # 6 Seitennummern (rot, gruen, gelb, cyan, naechste, Index) oder None
        self.title = title
        self.erase = True                       # C4 beim naechsten Senden (Inhalt neu/geaendert)

    @property
    def mag(self):
        return (self.page >> 8) & 7

    def key(self):
        return (self.page, self.subcode)

    def fingerprint(self):
        return (tuple(sorted((r, tuple(v)) for r, v in self.rows.items())), tuple(self.links or ()))


def page_num(s):
    """'101' / 101 / '1A5' -> 0x101 (Teletext-Seiten sind hexadezimal codiert, normale Seiten = Dezimalziffern)."""
    s = str(s).strip()
    if not re.match(r"^[1-8][0-9A-Fa-f]{2}$", s):
        raise ValueError("Seitennummer %s: 100-8FF" % s)
    return int(s, 16)


def page_from_lines(num, lines, charset="de", links=None, title=""):
    rows = {}
    for i, ln in enumerate((lines or [])[:24]):
        if ln is None:
            continue
        codes = encode_text(str(ln).rstrip("\r\n"), charset)
        if codes:
            rows[i + 1] = codes
    lk = None
    if links and any(x not in (None, "") for x in links):
        lk = [page_num(x) if x not in (None, "") else 0x8FF for x in list(links)[:6]]
        lk += [0x8FF] * (4 - len(lk)) if len(lk) < 4 else []
        lk = (lk + [0x8FF, 0x100])[:6]                   # naechste Seite (keine), Index = 100
        if 24 not in rows:                               # Beschriftung der Farbtasten (Zeile 24)
            lab = "".join("{%s}%-9s" % (c, ("%03x" % p) if p != 0x8FF else "")
                          for c, p in zip(("red", "green", "yellow", "cyan"), lk[:4]))
            rows[24] = encode_text(lab, charset)
    return Page(page_num(num), 0, rows, lk, title)


def parse_tti(text):
    """Teletext-Rohformat (.tti, wie vbit2/wxTED): mehrere Seiten je Datei; Text der OL-Zeilen sind Teletext-Codes
    (Steuerzeichen als ESC + Code+0x40 oder als Byte 0x80-0x9F)."""
    pages, cur, desc = [], None, ""
    for raw in text.splitlines():
        if len(raw) < 3 or raw[2] != ",":
            continue
        tag, rest = raw[:2].upper(), raw[3:]
        if tag == "PN":
            n = rest.strip()
            cur = Page(int(n[:3], 16), int(n[3:5] or "0", 16) if len(n) > 3 else 0, title=desc)
            pages.append(cur)
        elif tag == "DE" and (cur is None or cur.rows):     # DE steht meist vor PN (gilt fuer die folgende Seite)
            desc = rest.strip()
        elif cur is None:
            continue
        elif tag == "SC":
            try:
                cur.subcode = int(rest.strip() or "0", 16)
            except ValueError:
                pass
        elif tag == "DE":
            cur.title = rest.strip()
        elif tag == "FL":
            try:
                cur.links = [int(x.strip() or "8ff", 16) for x in rest.split(",")[:6]]
                cur.links += [0x8FF] * (6 - len(cur.links))
            except ValueError:
                pass
        elif tag == "OL":
            m = re.match(r"^(\d+),(.*)$", rest, re.S)
            if not m:
                continue
            row = int(m.group(1))
            if not 1 <= row <= 24:
                continue
            codes, esc = [], False
            for ch in m.group(2):
                c = ord(ch)
                if esc:
                    codes.append((c - 0x40) & 0x7F)
                    esc = False
                elif c == 0x1B:
                    esc = True
                elif 0x80 <= c < 0xA0:
                    codes.append(c - 0x80)
                else:
                    codes.append(c & 0x7F)
            cur.rows[row] = codes[:40]
    return pages


def load_folder(path, charset="de", log=None):
    """Alle .tti/.txt eines Ordners -> Seitenliste. .txt: Dateiname beginnt mit der Seitennummer (101.txt, 101 Wetter.txt)."""
    pages = []
    try:
        names = sorted(os.listdir(path))
    except OSError as e:
        if log:
            log("warn", "Teletext-Ordner %s: %s" % (path, e))
        return pages
    for fn in names:
        fp = os.path.join(path, fn)
        low = fn.lower()
        try:
            if low.endswith(".tti") or low.endswith(".ttix"):
                with open(fp, "rb") as f:
                    pages += parse_tti(f.read().decode("latin-1"))
            elif low.endswith(".txt"):
                m = re.match(r"^([1-8][0-9]{2})", fn)
                if not m:
                    continue
                with open(fp, "rb") as f:
                    raw = f.read()
                try:
                    txt = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    txt = raw.decode("cp1252")
                lines = txt.splitlines()
                pages.append(page_from_lines(m.group(1), lines[:23], charset, title=os.path.splitext(fn)[0][3:].strip(" -_")))
        except Exception as e:      # eine kaputte Datei darf das Karussell nicht anhalten
            if log:
                log("warn", "Teletext: %s nicht lesbar: %s" % (fn, e))
    return pages


def index_page(title, programmes, pages, charset="de"):
    """Automatische Seite 100: Programme (Name + Sendung) und Seitenverzeichnis."""
    L = ["{double}{yellow}%s" % title[:36], "", "{cyan}Programme"]
    for p in programmes[:8]:
        L.append("{white}%-18.18s{green}%-19.19s" % (p.get("name", ""), p.get("now", "")))
    L += ["", "{cyan}Seiten"]
    for pg in sorted((x for x in pages if x.page != 0x100), key=lambda x: x.page)[:12]:
        L.append("{white}%03x {yellow}%-34.34s" % (pg.page, pg.title or ""))
    while len(L) < 22:
        L.append("")
    L.append("{blue}{new-bg}{white}Muxwright Teletext  " + time.strftime("%d.%m.%Y"))
    return page_from_lines("100", L, charset, title=title)


# --------------------------------------------------------------------------------------------- Pakete
WEEKDAYS = {"de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], "en": ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]}


def mrag(mag, row):
    return bytes([ham((mag & 7) | ((row & 1) << 3)), ham(row >> 1)])


def header_packet(pg, text32, charset="de", erase=False, serial=True):
    s = pg.subcode
    d = [ham(pg.page & 15), ham((pg.page >> 4) & 15), ham(s & 15), ham(((s >> 4) & 7) | (8 if erase else 0)),
         ham((s >> 8) & 15), ham((s >> 12) & 3), ham(0), ham((1 if serial else 0) | (NATIONAL_BITS.get(charset, 4) << 1))]
    codes = encode_text(text32, charset)
    codes += [0x20] * (32 - len(codes))
    return mrag(pg.mag, 0) + bytes(d) + bytes(odd(c) for c in codes[:32])


def row_packet(mag, row, codes):
    c = list(codes) + [0x20] * (40 - len(codes))
    return mrag(mag, row) + bytes(odd(x) for x in c[:40])


def fastext_packet(pg):
    """X/27/0: sechs Links (rot, gruen, gelb, cyan, naechste Seite, Index), Unterseite beliebig (3F7F)."""
    d = [ham(0)]
    for ln in pg.links:
        m = ((ln >> 8) & 7) ^ pg.mag
        d += [ham(ln & 15), ham((ln >> 4) & 15), ham(0xF), ham(0x7 | ((m & 1) << 3)), ham(0xF), ham(0x3 | ((m >> 1) << 2))]
    d += [ham(0xF), 0, 0]                      # Link-Steuerbyte: Zeile 24 anzeigen; CRC (ungenutzt)
    return mrag(pg.mag, 27) + bytes(d)


class Carousel:
    """Liefert endlos Pakete: je Seite Kopfzeile (mit Uhrzeit), belegte Zeilen und ggf. Fastext. Thread-sicher genug fuer
    einen Erzeuger; reload() laeuft im selben Thread."""

    def __init__(self, cfg, programmes=None, log=None):
        self.cfg = cfg
        self.charset = cfg.get("charset", "de")
        self.header = cfg.get("header") or "Muxwright"
        self.folder = cfg.get("_folder_path")
        self.programmes = programmes or []
        self.log = log
        self.pages = []
        self.seen = {}
        self.order = []
        self.queue = []
        self.pos = 0
        self.sub_idx = {}
        self.last_scan = 0.0
        self.folder_sig = None
        self.lock = threading.Lock()
        self.reload(force=True)

    def _build(self):
        pages = []
        for p in self.cfg.get("pages") or []:
            try:
                pages.append(page_from_lines(p.get("page", "101"), p.get("lines") or [], self.charset, p.get("links"),
                                             p.get("title", "")))
            except ValueError as e:
                if self.log:
                    self.log("warn", "Teletext: %s" % e)
        if self.folder:
            pages += load_folder(self.folder, self.charset, self.log)
        if self.cfg.get("auto_index", True) and not any(p.page == 0x100 for p in pages):
            pages.append(index_page(self.header, self.programmes, pages, self.charset))
        return pages

    def _folder_signature(self):
        try:
            return tuple(sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(self.folder)))
        except OSError:
            return None

    def reload(self, force=False):
        if not force and self.folder:
            sig = self._folder_signature()
            if sig == self.folder_sig:
                return False
            self.folder_sig = sig
        elif force and self.folder:
            self.folder_sig = self._folder_signature()
        pages = self._build()
        for p in pages:                          # C4 nur, wenn sich der Inhalt geaendert hat
            fp = p.fingerprint()
            p.erase = self.seen.get(p.key()) != fp
            self.seen[p.key()] = fp
        groups = {}
        for p in pages:
            groups.setdefault(p.page, []).append(p)
        with self.lock:
            self.pages = pages
            self.groups = {k: sorted(v, key=lambda x: x.subcode) for k, v in groups.items()}
            self.order = sorted(self.groups)
        return True

    def page_list(self):
        return [{"page": "%03x" % p.page, "subcode": p.subcode, "title": p.title, "rows": len(p.rows)} for p in self.pages]

    def header_text(self, pg, now=None):
        t = time.localtime(now or time.time())
        wd = WEEKDAYS.get(self.charset, WEEKDAYS["de"])[t.tm_wday]
        name = "%-9.9s" % self.header
        return "%s %03x %s %02d.%02d. %02d:%02d:%02d" % (name, pg.page, wd, t.tm_mday, t.tm_mon, t.tm_hour, t.tm_min, t.tm_sec)

    def packets_for(self, pg, now=None):
        out = [header_packet(pg, self.header_text(pg, now), self.charset, erase=pg.erase)]
        pg.erase = False
        for r in sorted(pg.rows):
            out.append(row_packet(pg.mag, r, pg.rows[r]))
        if pg.links:
            out.append(fastext_packet(pg))
        return out

    def next_page(self):
        with self.lock:
            if not self.order:
                return None
            num = self.order[self.pos % len(self.order)]
            self.pos += 1
            subs = self.groups[num]
            i = self.sub_idx.get(num, 0) % len(subs)
            self.sub_idx[num] = i + 1
            return subs[i]

    def next_packet(self):
        if not self.queue:
            if self.folder and time.time() - self.last_scan > 2.0:
                self.last_scan = time.time()
                if self.reload() and self.log:
                    self.log("info", "Teletext: %d Seiten (Ordner neu eingelesen)" % len(self.pages))
            pg = self.next_page()
            if pg is None:
                return None
            self.queue = self.packets_for(pg)
        return self.queue.pop(0)

    def all_packets(self, now=None):
        """Ein kompletter Durchlauf (fuer die PAL-Schleife)."""
        out = []
        for num in self.order:
            for pg in self.groups[num]:
                pg.erase = True
                out += self.packets_for(pg, now)
        return out


# --------------------------------------------------------------------------------------------- DVB (EN 300 472)
def _crc32(b):
    crc = 0xFFFFFFFF
    for x in b:
        crc ^= x << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def _section(table_id, ext, body):
    sec = bytes([table_id, 0, 0, ext >> 8, ext & 0xFF, 0xC1, 0, 0]) + body
    ln = len(sec) - 3 + 4
    sec = bytes([table_id, 0xB0 | (ln >> 8), ln & 0xFF]) + sec[3:]
    return sec + _crc32(sec).to_bytes(4, "big")


def teletext_descriptor(language="deu", initial_page=0x100):
    mag = (initial_page >> 8) & 7
    return bytes([0x56, 5]) + language[:3].encode("ascii", "replace") + bytes([(1 << 3) | mag, initial_page & 0xFF])


class DvbTeletextTs:
    """Transportstrom mit einem Teletext-PES je Bild (40 ms): PAT/PMT (PID 0x100) + Teletext (PID 0x101).
    units: Dateneinheiten je PES; 3, 7, 11 ... fuellen ganze TS-Pakete (46 + 46 n = 184 k)."""
    PMT_PID, PID = 0x100, 0x101

    def __init__(self, carousel, units=7, language="deu", initial_page=0x100):
        self.car = carousel
        self.units = max(3, units - (units + 1) % 4) if (units + 1) % 4 else units
        self.cc = {0: 0, self.PMT_PID: 0, self.PID: 0}
        self.pts = 90000
        self.n = 0
        pat = _section(0x00, 1, bytes([0, 1, 0xE0 | (self.PMT_PID >> 8), self.PMT_PID & 0xFF]))
        es = bytes([0x06, 0xE0 | (self.PID >> 8), self.PID & 0xFF])
        desc = teletext_descriptor(language, initial_page)
        es += bytes([0xF0 | (len(desc) >> 8), len(desc) & 0xFF]) + desc
        pmt = _section(0x02, 1, bytes([0xE0 | (self.PID >> 8), self.PID & 0xFF, 0xF0, 0]) + es)
        self.psi = [(0, pat), (self.PMT_PID, pmt)]

    def _ts(self, pid, payload, start):
        out = bytearray()
        first = True
        while payload or first:
            chunk, payload = payload[:184], payload[184:]
            hdr = bytes([0x47, (0x40 if (start and first) else 0) | (pid >> 8), pid & 0xFF])
            cc = self.cc[pid]
            self.cc[pid] = (cc + 1) & 15
            if len(chunk) < 184:                  # Anpassungsfeld als Fueller
                fill = 184 - len(chunk)
                af = bytes([fill - 1]) + (bytes([0]) + b"\xff" * (fill - 2) if fill >= 2 else b"")
                out += hdr + bytes([0x30 | cc]) + af + chunk
            else:
                out += hdr + bytes([0x10 | cc]) + chunk
            first = False
        return bytes(out)

    def pes(self):
        units = bytearray()
        for _ in range(self.units):
            pk = self.car.next_packet()
            if pk is None:
                units += bytes([0xFF, 0x2C]) + b"\xff" * 44
            else:
                units += bytes([0x02, 0x2C, 0xC0 | 0x20 | 7, 0xE4]) + bytes(REV8[b] for b in pk)  # field 1, Zeile 7
        p = self.pts
        ptsb = bytes([0x21 | ((p >> 29) & 0x0E), (p >> 22) & 0xFF, 0x01 | ((p >> 14) & 0xFE), (p >> 7) & 0xFF, 0x01 | ((p << 1) & 0xFE)])
        body = bytes([0x84, 0x80, 0x24]) + ptsb + b"\xff" * 31 + bytes([0x10]) + bytes(units)
        ln = len(body)
        self.pts += 3600
        return bytes([0, 0, 1, 0xBD, ln >> 8, ln & 0xFF]) + body

    def chunk(self):
        """TS-Pakete fuer ein Bild (40 ms); PAT/PMT alle 5 Bilder."""
        out = bytearray()
        if self.n % 5 == 0:
            for pid, sec in self.psi:
                out += self._ts(pid, b"\x00" + sec, True)
        out += self._ts(self.PID, self.pes(), True)
        self.n += 1
        return bytes(out)

    def bitrate(self):
        k = (46 + 46 * self.units) // 184
        return int(25 * (k + 0.4) * 188 * 8)


def dvb_bitrate(units):
    units = max(3, units - (units + 1) % 4) if (units + 1) % 4 else units
    return int(25 * ((46 + 46 * units) // 184 + 0.4) * 188 * 8)


# --------------------------------------------------------------------------------------------- PAL (VBI)
BIT_RATE = 6.9375e6
VBI_LINES = list(range(7, 23)) + list(range(320, 336))      # je Halbbild 16 Zeilen (vor dem Bild, nach dem Vsync)
START_US = 11.6                                              # Taktvorlauf ab 0H (ETS 300 706: 12 µs, +0,4/-1,0)
LEVEL = 0.462                                                # "1" = 66 % von Schwarz->Weiss (0,7)
_OS = 16


def _bits(pk):
    data = bytes([0x55, 0x55, 0x27]) + pk
    return np.unpackbits(np.frombuffer(data, np.uint8), bitorder="little")


_KERNEL = np.hanning(_OS + 2)[1:-1]
_KERNEL /= _KERNEL.sum()


def vbi_line(pk, spl, line_s=64e-6):
    """Paket (42 Byte) -> Zeilensignal (spl Samples ueber 64 µs, Schwarz = 0, "1" = 0,462); Flanken als Kosinus
    ueber eine Bitdauer (Bandbreite < 5 MHz)."""
    b = _bits(pk).astype(np.float32)
    nrz = np.repeat(b, _OS)
    sm = np.convolve(np.concatenate([np.zeros(_OS), nrz, np.zeros(_OS)]), _KERNEL, "same")[_OS:-_OS]
    fs = spl / line_s
    t = np.arange(spl) / fs - START_US * 1e-6
    x = t * BIT_RATE * _OS
    out = np.interp(x, np.arange(len(sm)), sm, left=0.0, right=0.0)
    return (out * LEVEL).astype(np.float32)


def vbi_slice(line, spl, line_s=64e-6):
    """Gegenstueck fuer Pruefungen: Zeilensignal (Schwarz 0, Weiss 0,7) -> 42 Byte oder None (Taktvorlauf/Rahmen falsch)."""
    fs = spl / line_s
    thr = LEVEL / 2
    best = None
    for dt in np.linspace(-4, 4, 161):           # Bitphase +-4 Bit (Laufzeit von Filtern im Empfangsweg)
        tc = (START_US * 1e-6) + (np.arange(360) + 0.5 + dt) / BIT_RATE
        v = np.interp(tc * fs, np.arange(spl), line)
        bits = (v > thr).astype(np.uint8)
        by = np.packbits(bits, bitorder="little")
        if by[0] == 0x55 and by[1] == 0x55 and by[2] == 0x27:
            margin = float(np.min(np.abs(v - thr)))
            if best is None or margin > best[0]:
                best = (margin, bytes(by[3:]))
    return best[1] if best else None


def unham(b):
    try:
        return HAM84.index(b)
    except ValueError:
        return None


def t42_to_dvb_pes(packets, pts):
    """Fuer Pruefungen: t42-Pakete -> ein Teletext-PES (zvbi kann dann dekodieren)."""
    units = b"".join(bytes([0x02, 0x2C, 0xE7, 0xE4]) + bytes(REV8[b] for b in pk) for pk in packets)
    ptsb = bytes([0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF, 0x01 | ((pts >> 14) & 0xFE), (pts >> 7) & 0xFF, 0x01 | ((pts << 1) & 0xFE)])
    body = bytes([0x84, 0x80, 0x24]) + ptsb + b"\xff" * 31 + bytes([0x10]) + units
    pad = (-(len(body) + 6)) % 184
    body += (bytes([0xFF, 0x2C]) + b"\xff" * 44) * (pad // 46)
    return bytes([0, 0, 1, 0xBD, len(body) >> 8, len(body) & 0xFF]) + body


def config_defaults(t):
    """Gemeinsame Normalisierung (DVB und PAL). -> Fehlerliste."""
    E = []
    t.setdefault("enable", False)
    t.setdefault("header", "Muxwright")
    t["charset"] = t.get("charset") if t.get("charset") in CHARSETS else "de"
    t.setdefault("language", "deu" if t["charset"] == "de" else "eng")
    t.setdefault("auto_index", True)
    t.setdefault("pages", [])
    t.setdefault("folder", "")
    try:
        t["_initial_page"] = page_num(t.get("initial_page", "100"))
    except ValueError as e:
        E.append("teletext.initial_page: %s" % e)
        t["_initial_page"] = 0x100
    seen = set()
    for i, p in enumerate(t["pages"]):
        try:
            n = page_num(p.get("page"))
        except ValueError as e:
            E.append("teletext.pages[%d]: %s" % (i, e))
            continue
        if (n, p.get("sub", 0)) in seen:
            E.append("teletext.pages[%d]: Seite %03x doppelt" % (i, n))
        seen.add((n, p.get("sub", 0)))
        if len(p.get("lines") or []) > 23:
            E.append("teletext.pages[%d]: hoechstens 23 Zeilen" % i)
        for j, ln in enumerate(p.get("lines") or []):
            if visible_len(str(ln)) > 40:
                E.append("teletext.pages[%d] Zeile %d: hoechstens 40 Zeichen" % (i, j + 1))
                break
        for x in p.get("links") or []:
            if x not in (None, ""):
                try:
                    page_num(x)
                except ValueError as e:
                    E.append("teletext.pages[%d].links: %s" % (i, e))
    return E
