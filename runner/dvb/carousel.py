# -*- coding: utf-8 -*-
"""DSM-CC-Objektkarussell (ISO/IEC 13818-6, ETSI TR 101 202, TS 102 809 B) fuer HbbTV-Anwendungen im Sendesignal.

Aus einem Dateibaum {"index.html": b"...", "img/a.png": b"..."} entstehen Sektionen fuer eine PID:
  DSI (0x3B, ServiceGatewayInfo mit IOR der Wurzel), DII (0x3B, Modulliste), DDB (0x3C, Modulbloecke).
Objekte: ServiceGateway ("srg"), Verzeichnisse ("dir"), Dateien ("fil") als BIOP-Nachrichten; Verzeichnisse und kleine
Dateien teilen sich Module bis 64 KiB, jede Datei ueber 16 KiB bekommt ein eigenes Modul. Alles traegt der eine Elementarstrom mit association_tag =
component_tag (stream_identifier_descriptor in der PMT), wie es HbbTV (TS 102 796 7.2.5) erwartet.

Versionen: Carousel.build() vergleicht mit dem letzten Dateistand; bei einer Aenderung steigt die Version im
transactionId von DSI/DII, und die Module mit geaendertem Inhalt bekommen eine neue moduleVersion.
Ausgabe: Binaerdatei mit Sektionen fuer 'tsp -P inject --poll-files'; DSI und DII werden zwischen den DDB mehrfach
wiederholt, damit ein Empfaenger nicht einen ganzen Umlauf auf den Einstieg warten muss.
"""
import struct
import time
import zlib

TAG_BIOP = 0x49534F06          # TAG_BIOP (BIOP Profile Body)
TAG_OBJECT_LOCATION = 0x49534F50
TAG_CONN_BINDER = 0x49534F40
BIOP_DELIVERY_PARA_USE = 0x0016
BIOP_OBJECT_USE = 0x0017
BLOCK_SIZE = 4066              # 4096 Byte Sektion - Kopf/DDB-Kopf/CRC (TR 101 202 4.7.3)
MODULE_MAX = 65536             # TR 101 202: Module bis 64 KiB sind ueberall ladbar
REPEAT_EVERY = 32              # DSI+DII nach je 32 DDB (~130 KB) erneut
HOT_EVERY = 1024 * 1024        # bei kalter Nutzlast: uebrige Module nach je 1 MiB davon wiederholen

_CRC_TABLE = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) if _c & 0x80000000 else (_c << 1)
    _CRC_TABLE.append(_c & 0xFFFFFFFF)


def crc32_mpeg(data):
    c = 0xFFFFFFFF
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((c >> 24) ^ b) & 0xFF]
    return c


def _section(table_id, ext, version, num, last, payload):
    """Lange DSM-CC-Sektion mit CRC_32 (section_syntax_indicator 1, private_indicator 0)."""
    length = 5 + len(payload) + 4
    head = struct.pack(">BHHBBB", table_id, 0xB000 | length, ext & 0xFFFF, 0xC1 | ((version & 0x1F) << 1), num & 0xFF, last & 0xFF)
    body = head + payload
    return body + struct.pack(">I", crc32_mpeg(body))


def _kind(k):
    return k.encode("ascii") + b"\0"


def _ior(kind, carousel_id, module_id, key, assoc_tag, tid):
    """IOR mit BIOP Profile Body: ObjectLocation (Modul + Objektschluessel) und ConnBinder (Tap auf den DII)."""
    loc = struct.pack(">IHBBB", carousel_id, module_id, 1, 0, len(key)) + key
    obj_loc = struct.pack(">IB", TAG_OBJECT_LOCATION, len(loc)) + loc
    tap = struct.pack(">HHHBHII", 0, BIOP_DELIVERY_PARA_USE, assoc_tag, 10, 0x0001, tid, 0xFFFFFFFF)
    binder = struct.pack(">IBB", TAG_CONN_BINDER, 1 + len(tap), 1) + tap
    prof = struct.pack(">BB", 0, 2) + obj_loc + binder
    t = _kind(kind)
    return struct.pack(">I", len(t)) + t + struct.pack(">I", 1) + struct.pack(">II", TAG_BIOP, len(prof)) + prof


def _biop(key, kind, object_info, body):
    """BIOP-Nachricht (Kopf + Objektschluessel/-art/-info + Nachrichtenrumpf)."""
    k = _kind(kind)
    sub = (struct.pack(">B", len(key)) + key + struct.pack(">I", len(k)) + k + struct.pack(">H", len(object_info))
           + object_info + b"\0" + struct.pack(">I", len(body)) + body)
    return b"BIOP" + bytes([1, 0, 0, 0]) + struct.pack(">I", len(sub)) + sub


class _Obj:
    def __init__(self, path, kind, data=None):
        self.path, self.kind, self.data = path, kind, data
        self.children = []      # (Name, _Obj)
        self.key = b""
        self.module = 0


SMALL_MAX = 16384              # Dateien bis 16 KiB teilen sich Module mit den Verzeichnissen, groessere bekommen eigene
SMALL_IDS = 15                 # Modulnummern 1..15: Verzeichnisse + kleine Dateien, ab 16: je eine grosse Datei


def _order(path, size, start):
    """Reihenfolge im Karussell: Startseite, dann Seiten/Skripte/Stile, dann der Rest (kleine Dateien zuerst)."""
    ext = path.rsplit(".", 1)[-1].lower()
    return (0 if path == start else 1 if ext in ("html", "xhtml", "js", "css") else 2, size, path)


class Carousel:
    def __init__(self, carousel_id=1, assoc_tag=0x10, start="index.html", cold=()):
        """cold: Pfad-Praefixe grosser Nutzlast (z. B. "doom/"); alle anderen Module werden zwischen deren Bloecken
        wiederholt (HOT_EVERY), damit Startseite und Portal schnell laden, auch wenn das Karussell viele MB traegt."""
        self.carousel_id, self.assoc_tag, self.start, self.cold = carousel_id, assoc_tag, start, tuple(cold)
        self.cold_mods = set()
        # Version in transactionId (DSI/DII), 14 Bit: Startwert aus der Uhrzeit, damit Fernseher nach einem Neustart der
        # Sendung nicht den Stand der vorigen Sitzung fuer aktuell halten (Cache)
        self.version = int(time.time() // 60) & 0x3FFF
        self.mod_state = {}             # moduleId -> (Inhalt, Version)
        self.large_ids = {}             # Pfad grosser Datei -> feste Modulnummer (bleibt im Cache der Fernseher)
        self._sig = None

    # -------------------------------------------------------------- Baum
    def _tree(self, files):
        root = _Obj("", "srg")
        dirs = {"": root}
        order = sorted(files, key=lambda p: _order(p, len(files[p]), self.start))
        for path in order:
            parts = [p for p in path.replace("\\", "/").split("/") if p]
            cur = ""
            for d in parts[:-1]:
                nxt = cur + "/" + d if cur else d
                if nxt not in dirs:
                    o = _Obj(nxt, "dir")
                    dirs[nxt] = o
                    dirs[cur].children.append((d, o))
                cur = nxt
            f = _Obj(path, "fil", bytes(files[path]))
            dirs[cur].children.append((parts[-1], f))
        fils = [files_o for files_o in (o for d in dirs.values() for _, o in d.children) if files_o.kind == "fil"]
        fils.sort(key=lambda o: _order(o.path, len(o.data), self.start))
        # Objektschluessel aus dem Pfad: bleibt gleich, wenn andere Dateien dazukommen (Module unveraendert)
        used = set()
        for o in list(dirs.values()) + fils:
            k = zlib.crc32(("%s:%s" % (o.kind, o.path)).encode("utf-8")) or 1
            while k in used:
                k = (k + 1) & 0xFFFFFFFF or 1
            used.add(k)
            o.key = struct.pack(">I", k)
        return root, list(dirs.values()), fils

    def _tids(self):
        v = (self.version & 0x3FFF) << 16
        return 0x80000000 | v, 0x80000000 | v | 0x0002     # DSI: Identifikation 0, DII: 1 (Bit 1..15)

    def _message(self, o, dii_tid):
        if o.kind == "fil":
            return _biop(o.key, "fil", struct.pack(">Q", len(o.data)), struct.pack(">I", len(o.data)) + o.data)
        b = struct.pack(">H", len(o.children))
        for name, c in o.children:
            n = name.encode("utf-8") + b"\0"
            b += struct.pack(">BB", 1, len(n)) + n + struct.pack(">B", 4) + _kind(c.kind) + b"\x01"
            b += _ior(c.kind, self.carousel_id, c.module, c.key, self.assoc_tag, dii_tid)
            info = struct.pack(">Q", len(c.data)) if c.kind == "fil" else b""
            b += struct.pack(">H", len(info)) + info
        return _biop(o.key, o.kind, b"", b)

    # -------------------------------------------------------------- Aufbau
    def build(self, files):
        """files: {Pfad: bytes} -> (Sektionen als bytes, Info-dict).
        Module: Verzeichnisse und kleine Dateien zusammen (Nr. 1..15), jede grosse Datei in einem eigenen Modul mit fester
        Nummer. Bei einer Aenderung steigt die DII-Version; Module mit unveraendertem Inhalt behalten ihre Version, damit
        Fernseher z. B. Bilder und Spiele nicht neu laden, wenn sich nur die Wetterdaten aendern."""
        root, dirs, fils = self._tree(files)
        _, probe = self._tids()
        small = dirs + [o for o in fils if len(o.data) <= SMALL_MAX]
        large = [o for o in fils if len(o.data) > SMALL_MAX]
        groups, cur, cur_size = [], [], 0
        for o in small:
            sz = len(self._message(o, probe))
            if cur and cur_size + sz > MODULE_MAX:
                groups.append(cur)
                cur, cur_size = [], 0
            cur.append(o)
            cur_size += sz
        if cur:
            groups.append(cur)
        if len(groups) > SMALL_IDS:
            raise ValueError("Karussell: zu viele kleine Dateien (mehr als %d x 64 KiB)" % SMALL_IDS)
        modules = {}
        for mid, g in enumerate(groups, 1):
            modules[mid] = g
        for p in [p for p in self.large_ids if p not in {o.path for o in large}]:
            del self.large_ids[p]
        for o in large:
            if o.path not in self.large_ids:
                taken = set(self.large_ids.values())
                self.large_ids[o.path] = next(i for i in range(SMALL_IDS + 1, 0xFFFF) if i not in taken)
            modules[self.large_ids[o.path]] = [o]
        for mid, mod in modules.items():
            for o in mod:
                o.module = mid
        sig = zlib.crc32(repr(sorted((p, zlib.crc32(bytes(d))) for p, d in files.items())).encode())
        if self.mod_state and sig != self._sig:
            self.version += 1
        self._sig = sig
        dsi_tid, dii_tid = self._tids()
        state = {}
        for mid in sorted(modules):
            d = b"".join(self._message(o, dii_tid) for o in modules[mid])
            old = self.mod_state.get(mid)
            # Modulversion aus dem Inhalt (1..255): gleicher Inhalt = gleiche Version, auch ueber Neustarts hinweg;
            # geaenderter Inhalt bekommt sicher eine andere Version als zuvor
            ver = zlib.crc32(d) % 255 + 1
            if old is not None:
                if old[0] == d:
                    ver = old[1]
                elif ver == old[1]:
                    ver = ver % 255 + 1
            state[mid] = (d, ver)
        self.mod_state = state
        self.cold_mods = {mid for mid, mod in modules.items()
                          if self.cold and all(o.kind == "fil" and o.path.startswith(self.cold) for o in mod)}
        return self._sections(root, dsi_tid, dii_tid), {
            "version": self.version, "modules": len(state), "bytes": sum(len(d) for d, _ in state.values()),
            "objects": len(dirs) + len(fils)}

    def _sections(self, root, dsi_tid, dii_tid):
        cid, tag = self.carousel_id, self.assoc_tag
        # DSI: ServiceGatewayInfo = IOR(SRG) + downloadTaps 0 + serviceContextList 0 + userInfo 0
        sgi = _ior("srg", cid, root.module, root.key, tag, dii_tid) + b"\0\0" + struct.pack(">H", 0)
        dsi_body = b"\xFF" * 20 + struct.pack(">H", 0) + struct.pack(">H", len(sgi)) + sgi
        dsi = _section(0x3B, dsi_tid & 0xFFFF, 0, 0, 0,
                       struct.pack(">BBHIBBH", 0x11, 0x03, 0x1006, dsi_tid, 0xFF, 0, len(dsi_body)) + dsi_body)
        # DII: Modulliste mit BIOP::ModuleInfo (Timeouts, Tap BIOP_OBJECT_USE auf den Elementarstrom)
        mods = b""
        for mid, (d, ver) in sorted(self.mod_state.items()):
            tap = struct.pack(">HHHB", 0, BIOP_OBJECT_USE, tag, 0)
            mi = struct.pack(">IIIB", 0xFFFFFFFF, 0xFFFFFFFF, 0, 1) + tap + b"\0"
            mods += struct.pack(">HIBB", mid, len(d), ver, len(mi)) + mi
        dii_body = (struct.pack(">IHBBIIH", cid, BLOCK_SIZE, 0, 0, 0, 0, 0) + struct.pack(">H", len(self.mod_state))
                    + mods + struct.pack(">H", 0))
        dii = _section(0x3B, dii_tid & 0xFFFF, 0, 0, 0,
                       struct.pack(">BBHIBBH", 0x11, 0x03, 0x1002, dii_tid, 0xFF, 0, len(dii_body)) + dii_body)
        if len(dii) > 4096:
            raise ValueError("Karussell: zu viele Module fuer einen DII (%d)" % len(self.mod_state))
        def ddbs(mid):
            d, ver = self.mod_state[mid]
            blocks = [d[i:i + BLOCK_SIZE] for i in range(0, max(len(d), 1), BLOCK_SIZE)]
            last = len(blocks) - 1
            for bn, blk in enumerate(blocks):
                body = struct.pack(">HBBH", mid, ver, 0xFF, bn) + blk
                msg = struct.pack(">BBHIBBH", 0x11, 0x03, 0x1003, cid, 0xFF, 0, len(body)) + body
                yield _section(0x3C, mid, ver, bn, last, msg)

        hot = [mid for mid in sorted(self.mod_state) if mid not in self.cold_mods]
        cold = [mid for mid in sorted(self.mod_state) if mid in self.cold_mods]
        hot_secs = [s for mid in hot for s in ddbs(mid)]
        out, n, since = [dsi, dii], 0, 0

        def emit(sec):
            nonlocal n
            out.append(sec)
            n += 1
            if n % REPEAT_EVERY == 0:
                out.extend([dsi, dii])

        for sec in hot_secs:
            emit(sec)
        for mid in cold:
            for sec in ddbs(mid):
                emit(sec)
                since += len(sec)
                if since >= HOT_EVERY:            # Portal-Module zwischendurch erneut (DDB duerfen sich wiederholen)
                    since = 0
                    for h in hot_secs:
                        emit(h)
        return b"".join(out)


def cycle_seconds(section_bytes, bitrate_bps):
    """Dauer eines Umlaufs bei der Injektionsrate (Sektionen in TS-Pakete zu 184 Byte Nutzlast)."""
    packets = section_bytes / 184.0 + 1
    return packets * 188 * 8 / float(bitrate_bps)
