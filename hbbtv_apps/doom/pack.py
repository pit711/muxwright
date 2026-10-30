# Doom fuer das Objektkarussell packen: doom.js (mit eingebettetem Speicherabbild) und WAD jeweils als rohes Deflate,
# in Stuecke zu 128 KiB geteilt (je Stueck ein Karussellmodul; DII fasst ~140 Module) + manifest.js fuer doom.html.
#   python3 pack.py doom.js freedoom1.wad AUSGABEORDNER
import json
import os
import sys
import zlib

CHUNK = 128 * 1024


def pack(data, prefix, out):
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    z = co.compress(data) + co.flush()
    names = []
    for i in range(0, len(z), CHUNK):
        n = "c/%s%03d.bin" % (prefix, i // CHUNK)
        with open(os.path.join(out, n), "wb") as f:
            f.write(z[i:i + CHUNK])
        names.append(n)
    return {"files": names, "size": len(data), "packed": len(z), "crc": zlib.crc32(data)}


if __name__ == "__main__":
    js, wad, out = sys.argv[1:4]
    os.makedirs(os.path.join(out, "c"), exist_ok=True)
    for f in os.listdir(os.path.join(out, "c")):
        os.remove(os.path.join(out, "c", f))
    m = {"js": pack(open(js, "rb").read(), "j", out), "wad": pack(open(wad, "rb").read(), "w", out),
         "wadname": os.path.basename(wad)}
    with open(os.path.join(out, "manifest.js"), "w", encoding="utf-8") as f:
        f.write("var DOOM_MANIFEST = %s;\n" % json.dumps(m))
    tot = sum(os.path.getsize(os.path.join(out, n)) for k in ("js", "wad") for n in m[k]["files"])
    print("JS %.0f kB -> %.0f kB, WAD %.1f MB -> %.1f MB, %d Stuecke, zusammen %.1f MB"
          % (m["js"]["size"] / 1e3, m["js"]["packed"] / 1e3, m["wad"]["size"] / 1e6, m["wad"]["packed"] / 1e6,
             len(m["js"]["files"]) + len(m["wad"]["files"]), tot / 1e6))
