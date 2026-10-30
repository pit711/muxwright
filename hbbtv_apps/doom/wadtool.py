# WAD-Werkzeug: Inhalt nach Art auflisten (--stats) oder eine verkleinerte Kopie ohne Ton/Musik schreiben (--strip out.wad)
import struct
import sys
import zlib
from collections import defaultdict


def read(path):
    d = open(path, "rb").read()
    kind, n, off = struct.unpack_from("<4sii", d, 0)
    lumps = []
    for i in range(n):
        lo, ls, name = struct.unpack_from("<ii8s", d, off + 16 * i)
        lumps.append((name.rstrip(b"\0").decode("ascii", "replace"), d[lo:lo + ls]))
    return kind, lumps


def category(name, data):
    if name.startswith(("DS", "DP")) and data[:2] in (b"\x03\x00", b"\x00\x00"):
        return "sound"
    if name.startswith("D_") or data[:4] in (b"MUS\x1a", b"MThd"):
        return "music"
    if name.startswith("DEMO"):
        return "demo"
    if name.startswith("GENMIDI") or name.startswith("DMXGUS"):
        return "music"
    return "other"


def write(path, kind, lumps):
    body, dirent, off = b"", b"", 12
    for name, data in lumps:
        dirent += struct.pack("<ii8s", off + len(body), len(data), name.encode("ascii")[:8])
        body += data
    with open(path, "wb") as f:
        f.write(struct.pack("<4sii", kind, len(lumps), 12 + len(body)) + body + dirent)


if __name__ == "__main__":
    kind, lumps = read(sys.argv[1])
    if "--stats" in sys.argv:
        s = defaultdict(lambda: [0, 0])
        for name, data in lumps:
            c = category(name, data)
            s[c][0] += 1
            s[c][1] += len(data)
        for c, (n, b) in sorted(s.items(), key=lambda x: -x[1][1]):
            print("%-8s %5d Lumps %8.2f MB" % (c, n, b / 1e6))
        big = sorted(lumps, key=lambda x: -len(x[1]))[:15]
        print("groesste:", ", ".join("%s %d kB" % (n, len(d) // 1024) for n, d in big))
        allb = b"".join(d for _, d in lumps)
        print("gesamt %.2f MB, zlib -9: %.2f MB" % (len(allb) / 1e6, len(zlib.compress(allb, 9)) / 1e6))
    if "--demos" in sys.argv:
        for name, data in lumps:
            if name.startswith("DEMO"):
                print(name, "Version", data[0], "Skill", data[1], "Episode", data[2], "Map", data[3])
    if "--e1" in sys.argv:
        # nur Episode 1: Karten E2..E4 komplett entfernen (Marker + 10 Kartenlumps), Musik leeren
        out, skip, keep = sys.argv[sys.argv.index("--e1") + 1], 0, []
        for name, data in lumps:
            if skip:
                skip -= 1
                continue
            if len(name) == 4 and name[0] == "E" and name[2] == "M" and name[1] in "234":
                skip = 10
                continue
            keep.append((name, b"" if category(name, data) == "music" else data))   # Toene bleiben (Web Audio)
        write(out, kind, keep)
        allb = b"".join(d for _, d in keep)
        print("E1-WAD:", out, "%.2f MB, zlib -9: %.2f MB, Lumps %d" % (len(allb) / 1e6, len(zlib.compress(allb, 9)) / 1e6, len(keep)))
    if "--strip" in sys.argv:
        out = sys.argv[sys.argv.index("--strip") + 1]
        keep = [(n, b"" if category(n, d) in ("sound", "music") else d) for n, d in lumps]   # Eintrag bleibt, Inhalt leer
        write(out, kind, keep)
        print("geschrieben:", out, sum(len(d) for _, d in keep) / 1e6, "MB")
