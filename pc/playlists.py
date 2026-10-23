"""Playlists for DAB-Lab (SPEC.md, Ergänzung 23.10.).

Storage: runtime/playlists/<name>.json  ->  {"name":..,"items":[{"file":"media/x.mp3","title":..,"artist":..,"duration_s":..}]}
Import: .m3u/.m3u8/.pls text (paths relative to media/). Export: .m3u8 text.
"""
import json
import os
import re
import urllib.parse

AUDIO_EXT = ("mp3", "ogg", "oga", "opus", "flac", "wav", "aac", "m4a", "wma", "mp2", "aif", "aiff")
NAME_RE = re.compile(r"[\w .()+-]{1,64}")


class PlaylistError(ValueError):
    pass


def valid_name(name):
    return bool(name) and bool(NAME_RE.fullmatch(name)) and not name.startswith(".")


class Store:
    def __init__(self, pl_dir, media_dir):
        self.dir = pl_dir
        self.media = media_dir
        os.makedirs(pl_dir, exist_ok=True)

    def path(self, name):
        if not valid_name(name):
            raise PlaylistError("Playlist-Name ungültig ([A-Za-z0-9 ._()+-], max. 64)")
        return os.path.join(self.dir, name + ".json")

    def exists(self, name):
        return valid_name(name) and os.path.isfile(self.path(name))

    def names(self):
        return sorted(n[:-5] for n in os.listdir(self.dir) if n.endswith(".json"))

    def summary(self):
        out = []
        for n in self.names():
            try:
                pl = self.load(n)
            except Exception as e:
                out.append({"name": n, "error": str(e)})
                continue
            durs = [i.get("duration_s") for i in pl["items"]]
            out.append({"name": n, "count": len(pl["items"]),
                        "duration_s": round(sum(d for d in durs if d), 1) if any(durs) else None,
                        "missing": sum(1 for i in pl["items"] if not i.get("exists", True)),
                        "mtime": os.path.getmtime(self.path(n))})
        return out

    def load(self, name):
        with open(self.path(name), encoding="utf-8") as f:
            pl = json.load(f)
        pl, _ = self.normalize(pl, name)
        return pl

    def save(self, name, pl):
        pl, errs = self.normalize(pl, name)
        if errs:
            raise PlaylistError("; ".join(errs))
        tmp = self.path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"name": pl["name"], "items": [{k: v for k, v in i.items() if k != "exists"} for i in pl["items"]]},
                      f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path(name))
        return pl

    def delete(self, name):
        p = self.path(name)
        if not os.path.isfile(p):
            return False
        os.remove(p)
        return True

    # ------------------------------------------------------------------ validation
    def media_rel(self, file):
        """'media/x.mp3' | 'x.mp3' | 'G:\\...\\media\\x.mp3' -> 'media/x.mp3' or None if outside media/."""
        if not isinstance(file, str) or not file.strip():
            return None
        f = file.strip().replace("\\", "/")
        if f.lower().startswith("file://"):
            f = urllib.parse.unquote(f[7:]).lstrip("/")
        if re.match(r"^[A-Za-z]:/", f) or f.startswith("/"):
            try:
                rel = os.path.relpath(f, self.media)
            except ValueError:
                return None
            if rel.startswith(".."):
                return None
            f = rel.replace("\\", "/")
        f = re.sub(r"^(\./)+", "", f)
        if f.startswith("media/"):
            f = f[6:]
        if f.startswith("../") or "/../" in f or not f:
            return None
        return "media/" + f

    def normalize(self, pl, name=None):
        errs = []
        if not isinstance(pl, dict):
            return None, ["Playlist muss ein JSON-Objekt sein"]
        out = {"name": str(pl.get("name") or name or ""), "items": []}
        if name and out["name"] != name:
            out["name"] = name
        items = pl.get("items")
        if not isinstance(items, list):
            return out, ["items fehlt (Liste)"]
        for i, it in enumerate(items):
            if isinstance(it, str):
                it = {"file": it}
            if not isinstance(it, dict):
                errs.append(f"items[{i}]: Objekt erwartet")
                continue
            rel = self.media_rel(it.get("file"))
            if rel is None:
                errs.append(f"items[{i}].file: Pfad innerhalb media/ erwartet ({it.get('file')!r})")
                continue
            ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
            if ext not in AUDIO_EXT:
                errs.append(f"items[{i}].file: kein Audioformat (.{ext})")
            d = it.get("duration_s")
            try:
                d = round(float(d), 2) if d not in (None, "", 0, -1) else None
                if d is not None and d <= 0:
                    d = None
            except (TypeError, ValueError):
                d = None
            o = {"file": rel, "title": str(it.get("title") or "")[:128], "artist": str(it.get("artist") or "")[:128],
                 "duration_s": d, "exists": os.path.isfile(os.path.join(self.media, rel[6:]))}
            for k in ("album", "gain_db"):
                if it.get(k) not in (None, ""):
                    o[k] = it[k]
            out["items"].append(o)
        return out, errs

    # ------------------------------------------------------------------ m3u / pls
    def parse_import(self, text, name=None):
        text = text.lstrip("\ufeff")
        lines = [l.rstrip("\r") for l in text.split("\n")]
        items = []
        if any(l.strip().lower() == "[playlist]" for l in lines[:5]):
            files, titles, lens = {}, {}, {}
            for l in lines:
                m = re.match(r"^\s*(File|Title|Length)(\d+)\s*=\s*(.*)$", l, re.I)
                if m:
                    {"file": files, "title": titles, "length": lens}[m.group(1).lower()][int(m.group(2))] = m.group(3).strip()
            for k in sorted(files):
                t = titles.get(k, "")
                artist, title = _split_artist_title(t)
                items.append({"file": files[k], "title": title, "artist": artist, "duration_s": _num(lens.get(k))})
        else:
            pend = None
            for l in lines:
                s = l.strip()
                if not s:
                    continue
                if s.upper().startswith("#EXTINF:"):
                    m = re.match(r"#EXTINF:\s*(-?[\d.]+)[^,]*,(.*)$", s, re.I)
                    if m:
                        artist, title = _split_artist_title(m.group(2).strip())
                        pend = {"duration_s": _num(m.group(1)), "title": title, "artist": artist}
                    continue
                if s.startswith("#"):
                    continue
                it = {"file": s}
                if pend:
                    it.update(pend)
                    pend = None
                items.append(it)
        pl, errs = self.normalize({"name": name or "", "items": items}, name)
        return pl, errs

    @staticmethod
    def export_m3u8(pl):
        out = ["#EXTM3U"]
        for it in pl["items"]:
            d = it.get("duration_s")
            label = " - ".join(x for x in (it.get("artist"), it.get("title")) if x) or os.path.basename(it["file"])
            out.append(f"#EXTINF:{int(round(d)) if d else -1},{label}")
            out.append(it["file"][6:])            # relative to media/
        return "\n".join(out) + "\n"


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _split_artist_title(s):
    if " - " in s:
        a, t = s.split(" - ", 1)
        return a.strip(), t.strip()
    return "", s.strip()


def validate_source(src, store, errs, prefix):
    """Validate audio.source for kind 'playlist' (called from server.validate_config)."""
    name = src.get("playlist")
    if not isinstance(name, str) or not valid_name(name):
        errs.append(f"{prefix}.playlist: Name fehlt/ungültig")
    elif not store.exists(name):
        errs.append(f"{prefix}.playlist '{name}' gibt es nicht (runtime/playlists/)")
    for k in ("shuffle", "loop", "normalize", "dls_from_tags"):
        if k in src and not isinstance(src[k], bool):
            errs.append(f"{prefix}.{k}: true/false")
    cf = src.get("crossfade_s", 0)
    if not isinstance(cf, (int, float)) or not 0 <= cf <= 12:
        errs.append(f"{prefix}.crossfade_s: 0–12")
