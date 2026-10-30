# -*- coding: utf-8 -*-
"""HbbTV-Portal im Objektkarussell: Start, Wetter, Regenradar, Nachrichten, Programm (Umschalten im Multiplex),
Mediathek (Videos per HTTP aus dem LAN), Bilder, Spiele, Info - Live-Bild im Fenster (video/broadcast).

Statische Teile (hbbtv_static/portal.js, games.js, portal.css) bleiben unveraendert und damit im Cache der Fernseher;
index.html traegt die Daten (window.P). Netzdaten (Wetter, Radar, RSS) werden hoechstens alle refresh_min Minuten
abgerufen, lokale Daten (EIT der Dienste, Bilder- und Videoordner) bei jedem Aufruf (die Kette ruft alle 30 s auf;
das Karussell bekommt nur bei geaendertem Inhalt eine neue Version).
"""
import datetime
import email.utils
import html
import io
import json
import os
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

from . import hbbtv_app as W

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hbbtv_static")
DE_BOX = (47.0, 55.2, 5.5, 15.5)                 # Abdeckung DWD-Radar (lat min/max, lon min/max)
RADAR_BBOX = (612000, 5950000, 1725000, 7380000)  # EPSG:3857, Deutschland
RADAR_WH = (364, 466)
RADAR_URL = ("https://maps.dwd.de/geoserver/dwd/wms?service=WMS&version=1.1.1&request=GetMap&styles=&srs=EPSG:3857"
             "&bbox=%d,%d,%d,%d&width=%d&height=%d&format=image/png&transparent=true&layers=")
FEED_MAX = 4 * 1048576
DOOM_PHASE_CHUNKS = 18         # je Portion 18 x 128 KiB = 2,3 MB (+ Portal bleibt unter dem TV-Karussellspeicher)
DOOM_PHASE_S = 12              # Portion wechselt alle 12 s (bei 6 Mbit/s ~2 Umlaeufe je Portion)
GALLERY_MAX = 30
GALLERY_BOX = (760, 428)
MEDIA_EXT = (".mp4", ".m4v")
MEDIA_MAX = 60
IDTYPE = {"dvbt": 12, "dvbt2": 16, "dvbc": 10, "dvbs": 11}   # OIPF ID_DVB_T/T2/C/S fuer createChannelObject
DEFAULT_FEEDS = {"de": ["https://www.tagesschau.de/index~rss2.xml"], "en": ["https://feeds.bbci.co.uk/news/world/rss.xml"]}


def _get_bytes(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": W.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _clean(s, n):
    """HTML aus RSS-Texten entfernen, Entities aufloesen, kuerzen."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + " …"


def _parse_feed(raw, url):
    """Feeds kommen von fremden Servern: erst mit expat pruefen, das beim ersten DOCTYPE oder ENTITY abbricht
    (unabhaengig von Kodierung und Position - kein XXE, keine Entity-Expansion), dann mit ElementTree lesen."""
    import xml.parsers.expat

    def deny(*_):
        raise ValueError("Feed %s abgelehnt (DOCTYPE/ENTITY)" % url)

    p = xml.parsers.expat.ParserCreate()
    p.StartDoctypeDeclHandler = deny
    p.EntityDeclHandler = deny
    p.ExternalEntityRefHandler = deny
    p.Parse(raw, True)
    return ET.fromstring(raw)


def fetch_news(feeds, zone, limit=30):
    """RSS 2.0 / Atom -> [{title, text, time, ts}], neueste zuerst, Quelle = Kanaltitel."""
    items, srcs = [], []
    for url in feeds:
        raw = _get_bytes(url)
        if len(raw) > FEED_MAX:
            raise ValueError("Feed %s groesser als %d MB" % (url, FEED_MAX // 1048576))
        root = _parse_feed(raw, url)
        ch = root.find("channel")
        if ch is not None:
            srcs.append(_clean(ch.findtext("title") or url, 40).split(" - ")[0])
            enc = "{http://purl.org/rss/1.0/modules/content/}encoded"
            entries = []
            for i in ch.findall("item"):
                t, d = i.findtext("title"), i.findtext("description")
                if not d or _clean(d, 200) == _clean(t, 200):        # z. B. Tagesschau: Beschreibung = Titel
                    d = i.findtext(enc) or d
                entries.append((t, d, i.findtext("pubDate")))
        else:                                     # Atom
            ns = "{http://www.w3.org/2005/Atom}"
            srcs.append(_clean(root.findtext(ns + "title") or url, 40))
            entries = [(e.findtext(ns + "title"), e.findtext(ns + "summary") or e.findtext(ns + "content"),
                        e.findtext(ns + "updated")) for e in root.findall(ns + "entry")]
        for t, d, p in entries:
            ts = 0
            try:
                ts = email.utils.parsedate_to_datetime(p).timestamp() if p and "," in p else \
                    datetime.datetime.fromisoformat((p or "").replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
            items.append({"title": _clean(t, 110), "text": _clean(d, 420), "ts": ts})
    items.sort(key=lambda x: -x["ts"])
    for i in items:
        i["time"] = datetime.datetime.fromtimestamp(i["ts"], zone).strftime("%H:%M") if i["ts"] else ""
    return {"src": ", ".join(dict.fromkeys(srcs)), "items": [dict((k, v) for k, v in i.items() if k != "ts") for i in items[:limit]]}


FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def fetch_fx():
    """EZB-Referenzkurse (EUR-Basis) -> {"date", "rates": {USD: 1.08, ...}}; XML ohne DTD (wie die Feeds)."""
    root = _parse_feed(_get_bytes(FX_URL), FX_URL)
    rates, date = {}, ""
    for e in root.iter():
        if e.get("time"):
            date = e.get("time")
        if e.get("currency") and e.get("rate"):
            rates[e.get("currency")] = float(e.get("rate"))
    if not rates:
        raise ValueError("keine Kurse")
    return {"date": date, "rates": rates}


def _merc(lat, lon):
    import math
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90 + lat) * math.pi / 360.0)) * 20037508.34 / math.pi
    return x, y


def fetch_radar(lat, lon, place):
    """DWD-Niederschlagsradar + Laendergrenzen (WMS, transparent) auf dunklem Grund, Ort markiert -> PNG-Bytes."""
    from PIL import Image, ImageDraw
    w, h = RADAR_WH
    base = RADAR_URL % (RADAR_BBOX + (w, h))
    bg = Image.new("RGBA", (w, h), (16, 30, 52, 255))
    radar = Image.open(io.BytesIO(_get_bytes(base + "dwd:Niederschlagsradar"))).convert("RGBA")
    land = Image.open(io.BytesIO(_get_bytes(base + "dwd:Laender"))).convert("RGBA")
    # Grenzen hell einfaerben (DWD liefert sie gelb), Radar darueber kraeftiger
    a = land.split()[3]
    border = Image.new("RGBA", (w, h), (140, 160, 195, 255))
    border.putalpha(a)
    # nur Niederschlag behalten: graue Maske ausserhalb der Radarabdeckung und magentafarbenen Rand entfernen
    px = radar.load()
    for yy in range(h):
        for xx in range(w):
            r_, g_, b_, a_ = px[xx, yy]
            if a_ < 60 or max(r_, g_, b_) - min(r_, g_, b_) < 40 or (r_ > 180 and g_ < 90 and b_ > 180):
                px[xx, yy] = (0, 0, 0, 0)
            else:
                px[xx, yy] = (r_, g_, b_, 255)
    bg.alpha_composite(border)
    bg.alpha_composite(radar)
    if lat is not None:
        x, y = _merc(lat, lon)
        px = (x - RADAR_BBOX[0]) / (RADAR_BBOX[2] - RADAR_BBOX[0]) * w
        py = (RADAR_BBOX[3] - y) / (RADAR_BBOX[3] - RADAR_BBOX[1]) * h
        d = ImageDraw.Draw(bg)
        d.ellipse((px - 6, py - 6, px + 6, py + 6), fill=(226, 35, 26, 255), outline=(255, 255, 255, 255), width=2)
        try:
            d.text((px + 10, py - 8), place, fill=(255, 255, 255, 255))
        except Exception:
            pass
    out = io.BytesIO()
    bg.convert("RGB").quantize(colors=64).save(out, "PNG", optimize=True)
    return out.getvalue()


class PortalApp:
    def __init__(self, hb, cfg, svc, lang, log, host, sdir):
        self.hb, self.cfg, self.svc, self.lang, self.log, self.host, self.sdir = hb, cfg, svc, lang, log, host, sdir
        self.pc = hb["portal"]
        self.weather = W.WeatherApp(hb["weather"], lang, log)
        self.net_t = 0
        self.net = {"weather": None, "radar": None, "news": None, "fx": None}
        self.gal_cache, self.thumb_cache, self.skipped = {}, {}, set()
        self.cache_dir = os.path.join(sdir, "hbbtv_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        tz = hb["weather"].get("timezone")
        try:
            import zoneinfo
            self.zone = zoneinfo.ZoneInfo(tz) if tz else datetime.datetime.now().astimezone().tzinfo
        except Exception:
            self.zone = datetime.datetime.now().astimezone().tzinfo
        with open(os.path.join(STATIC, "portal.js"), "rb") as f:
            self.static = {"portal.js": f.read()}
        for n in ("games.js", "games2.js", "tools.js", "tools2.js", "portal.css"):
            with open(os.path.join(STATIC, n), "rb") as f:
                self.static[n] = f.read()

    # ---------------------------------------------------------------- Netzdaten
    def _refresh_net(self):
        pages, ok = self.pc["pages"], True
        if pages.get("weather") or pages.get("radar"):
            try:
                if not self.weather.loc:
                    self.weather.loc = self.weather._locate()
                    self.log("info", "HbbTV-Portal: Ort %s (%.3f, %.3f)" % (self.weather.loc[2], self.weather.loc[0], self.weather.loc[1]))
                self.weather.last = W.fetch_any(self.weather.loc[0], self.weather.loc[1], self.hb["weather"].get("provider", "auto"),
                                                self.hb["weather"].get("timezone"), self.log)
                self.net["weather"] = self.weather.last
            except Exception as e:
                ok = False
                self.log("warn", "HbbTV-Portal: Wetter nicht abrufbar (%s)" % e)
        if pages.get("radar") and self.weather.loc:
            lat, lon = self.weather.loc[0], self.weather.loc[1]
            if DE_BOX[0] <= lat <= DE_BOX[1] and DE_BOX[2] <= lon <= DE_BOX[3]:
                try:
                    self.net["radar"] = (fetch_radar(lat, lon, self.weather.loc[2]), datetime.datetime.now(self.zone).strftime("%H:%M"))
                except Exception as e:
                    ok = False
                    self.log("warn", "HbbTV-Portal: Regenradar nicht abrufbar (%s)" % e)
        if pages.get("tools"):
            try:
                self.net["fx"] = fetch_fx()
            except Exception as e:
                self.log("info", "HbbTV-Portal: EZB-Kurse nicht abrufbar (%s)" % e)
        if pages.get("news") and self.pc["feeds"]:
            try:
                self.net["news"] = fetch_news(self.pc["feeds"], self.zone)
            except Exception as e:
                ok = False
                self.log("warn", "HbbTV-Portal: Nachrichten nicht abrufbar (%s)" % e)
        self.net_t = time.time()
        return ok

    # ---------------------------------------------------------------- lokale Daten
    def _epg(self):
        out, ts = [], self.cfg["ts"]
        for s in self.cfg["services"]:
            e = {"name": s["name"], "sid": s["service_id"], "here": s is self.svc, "now": None, "next": None}
            try:
                root = ET.parse(os.path.join(self.sdir, "eit", "eit_%s.xml" % s["key"])).getroot()
                evs = root.findall(".//event")
                for slot, ev in zip(("now", "next"), evs):
                    st = datetime.datetime.strptime(ev.get("start_time"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                    sd = ev.find("short_event_descriptor")
                    e[slot] = {"title": (sd.findtext("event_name") if sd is not None else "") or "",
                               "text": (sd.findtext("text") if sd is not None else "") or "",
                               "start": st.astimezone(self.zone).strftime("%H:%M")}
            except Exception:
                ev = s.get("eit") or {}
                if ev.get("title"):
                    e["now"] = {"title": ev["title"], "text": ev.get("text") or "", "start": ""}
            out.append(e)
        return out

    def _gallery(self):
        d = self.pc.get("gallery_dir")
        if not self.pc["pages"].get("gallery") or not d:
            return [], {}
        from PIL import Image
        root = self.host.media_path(d)
        files, items = {}, []
        try:
            names = sorted(n for n in os.listdir(root) if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")))
        except OSError:
            return [], {}
        for n in names[:GALLERY_MAX]:
            p = os.path.join(root, n)
            try:
                key = (p, os.path.getmtime(p))
                if key not in self.gal_cache:
                    im = Image.open(p).convert("RGB")
                    im.thumbnail(GALLERY_BOX)
                    b = io.BytesIO()
                    im.save(b, "JPEG", quality=80, optimize=True)
                    self.gal_cache[key] = (b.getvalue(), im.size)
                data, (w, h) = self.gal_cache[key]
            except Exception as e:
                self.log("warn", "HbbTV-Portal: Bild %s uebersprungen (%s)" % (n, e))
                continue
            rel = "gal/%02d.jpg" % len(items)
            files[rel] = data
            items.append({"img": rel, "w": w, "h": h, "title": os.path.splitext(n)[0].replace("_", " ")})
        return items, files

    def _media(self):
        base = self.hb.get("media_base")
        d = self.pc.get("media_dir") or "media/video"
        if not self.pc["pages"].get("media"):
            return None, {}
        if not base:
            if "nobase" not in self.skipped:
                self.skipped.add("nobase")
                self.log("warn", "HbbTV-Mediathek: aus - der Muxwright-Server muss im LAN-Modus laufen (--lan), damit Fernseher die Videos laden koennen")
            return None, {}
        root = self.host.media_path(d)
        files, items = {}, []
        try:
            names = sorted(n for n in os.listdir(root) if n.lower().endswith(MEDIA_EXT))
        except OSError:
            return None, {}
        rel_dir = d.replace("\\", "/").strip("/")
        if rel_dir.startswith("media/"):
            rel_dir = rel_dir[6:]
        titles = {}
        try:
            with open(os.path.join(root, "youtube_index.json"), encoding="utf-8") as f:
                titles = {v.get("file"): v.get("title") for v in json.load(f).values()}
        except Exception:
            pass
        for n in names:
            if len(items) >= MEDIA_MAX:
                break
            p = os.path.join(root, n)
            key = (p, os.path.getmtime(p))
            if key not in self.thumb_cache:
                self.thumb_cache[key] = self._thumb(p)
            th, dur, why = self.thumb_cache[key]
            if why:
                if key not in self.skipped:
                    self.skipped.add(key)
                    self.log("info", "HbbTV-Mediathek: %s ausgelassen (%s - Fernseher spielen H.264 + AAC in MP4)" % (n, why))
                continue
            it = {"title": titles.get(n) or os.path.splitext(n)[0].replace("_", " "), "url": base + urllib.request.quote("%s/%s" % (rel_dir, n)), "dur": dur}
            if th:
                rel = "thumb/%02d.jpg" % len(items)
                files[rel] = th
                it["thumb"] = rel
            items.append(it)
        return {"items": items}, files

    def _doom_dir(self):
        root = getattr(self.host, "RUNTIME", None)
        d = os.path.join(root, "hbbtv_apps", "doom") if root else None
        return d if d and os.path.isfile(os.path.join(d, "manifest.js")) else None

    def _doom_files(self):
        """Doom (Chocolate Doom als asm.js + Freedoom, gepackt in Stuecke) aus runtime/hbbtv_apps/doom ins Karussell
        (Ordner doom/, im Karussell als kalte Nutzlast - das Portal wird dazwischen wiederholt)."""
        d = self._doom_dir()
        if not d or not self.pc.get("doom", True) or not self.pc["pages"].get("games"):
            return {}
        stamp = os.path.getmtime(os.path.join(d, "manifest.js"))
        if getattr(self, "_doom_cache", (None,))[0] != stamp:
            files = {}
            for r, _, ns in os.walk(d):
                for n in ns:
                    if n.endswith((".html", ".js", ".bin", ".txt", ".c", ".sh")):
                        p = os.path.join(r, n)
                        with open(p, "rb") as f:
                            files["doom/" + os.path.relpath(p, d).replace("\\", "/")] = f.read()
            chunks = sorted(p for p in files if p.startswith("doom/c/"))
            phases = [chunks[i:i + DOOM_PHASE_CHUNKS] for i in range(0, len(chunks), DOOM_PHASE_CHUNKS)] or [[]]
            self._doom_cache = (stamp, files, phases)
            self.log("info", "HbbTV-Portal: Doom im Karussell (%d Dateien, %.1f MB, %d Portionen zu je %d s)"
                     % (len(files), sum(map(len, files.values())) / 1e6, len(phases), DOOM_PHASE_S))
        _, files, phases = self._doom_cache
        # Fernseher halten nur wenige MB Karussell im Speicher (LG webOS 4.5: ~4,5 MB, danach 404): die Stuecke
        # wechseln portionsweise; jede Portion ist eine neue Karussellversion, der Fernseher verwirft dabei die alten
        # Module. doom.html fragt fehlende Stuecke jede Sekunde erneut an und behaelt geladene im eigenen Speicher.
        part = set(phases[int(time.time() // DOOM_PHASE_S) % len(phases)])
        return {p: d for p, d in files.items() if not p.startswith("doom/c/") or p in part}

    def _doom_url(self, in_carousel):
        """Doom-Start: aus dem Karussell (relativ) oder, falls dort nicht dabei, vom Muxwright-PC per LAN."""
        if in_carousel:
            return "doom/doom.html"
        base = self.hb.get("media_base")
        return base.replace("/hbbtv/media/", "/hbbtv/apps/doom/doom.html") if base and self._doom_dir() else None

    def _rf(self):
        rf = self.cfg.get("rf") or {}
        return {"channel": rf.get("channel") or "", "freq_mhz": round((rf.get("freq_hz") or 0) / 1e6, 3)}

    def _mux(self):
        """Senderinfo im Portal: Standard, Modulation, Nutzbitrate, Programme."""
        c, std = self.cfg, self.cfg["mode"]
        d = c.get(std) or {}
        mod = " ".join(str(d.get(k)) for k in ("fft", "mode", "constellation", "code_rate", "guard") if d.get(k))
        svcs = [{"name": s["name"], "sid": s["service_id"],
                 "info": ("%s %s" % (s["video"]["codec"].upper(), s["video"].get("size", "")) if s["type"] == "tv" else "Radio")
                 + " · " + s["audio"]["codec"].upper()} for s in c["services"]]
        return {"std": std, "mod": mod, "rate_mbps": round(c["_rate_bps"] / 1e6, 3),
                "ids": "0x%04X / 0x%04X" % (c["ts"]["original_network_id"], c["ts"]["transport_stream_id"]), "services": svcs}

    def _thumb(self, path):
        """Vorschaubild (240x135, JPEG), Dauer und Grund, falls Fernseher die Datei nicht abspielen (sonst None)."""
        dur, th, why = "", None, None
        try:
            r = subprocess.run([self.host.tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,profile",
                                "-of", "json", path], capture_output=True, timeout=20)
            j = json.loads(r.stdout.decode() or "{}")
            sec = float((j.get("format") or {}).get("duration") or 0)
            for st in j.get("streams") or []:
                if st.get("codec_type") == "video" and (st.get("codec_name") != "h264" or "4:4:4" in str(st.get("profile")) or "10" in str(st.get("profile"))):
                    why = "Video %s %s" % (st.get("codec_name"), st.get("profile") or "")
                elif st.get("codec_type") == "audio" and st.get("codec_name") not in ("aac", "mp3", "ac3", "eac3"):
                    why = "Ton %s" % st.get("codec_name")
            if why:
                return None, "", why.strip()
            dur = "%d:%02d" % (sec // 60, sec % 60) if sec else ""
            r = subprocess.run([self.host.tool("ffmpeg"), "-v", "error", "-ss", str(max(1, sec * 0.3)), "-i", path, "-frames:v", "1",
                                "-vf", "scale=240:134:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=240:136:(ow-iw)/2:(oh-ih)/2",
                                "-q:v", "5", "-f", "mjpeg", "-"], capture_output=True, timeout=30)
            th = r.stdout or None
        except Exception as e:
            self.log("warn", "HbbTV-Portal: Vorschau fuer %s fehlt (%s)" % (os.path.basename(path), e))
        return th, dur, why

    # ---------------------------------------------------------------- Seite
    def _weather_data(self):
        wx = self.net["weather"]
        if not wx:
            return None
        L, li = W.TXT.get(self.lang, W.TXT["en"]), 0 if self.lang == "de" else 1
        cur = wx["current"]
        dirs = W.DIRS["de" if self.lang == "de" else "en"]
        h = wx["hourly"]
        start = next((i for i, t in enumerate(h["time"]) if t[:13] >= cur["time"][:13]), 0)
        hours = [{"h": h["time"][i][11:16], "code": h["weather_code"][i], "t": round(h["temperature_2m"][i])}
                 for i in range(start + 1, len(h["time"]), 3)][:6]
        dd = wx["daily"]
        days = [{"wd": (datetime.date.fromisoformat(dd["time"][n]).weekday() + 1) % 7, "code": dd["weather_code"][n],
                 "max": round(dd["temperature_2m_max"][n]), "min": round(dd["temperature_2m_min"][n])} for n in range(min(6, len(dd["time"])))]
        return {"place": self.weather.loc[2] if self.weather.loc else "", "src": "%s %s · %s" % (L["as_of"], wx.get("_fetched", ""), wx.get("_src", "")),
                "now": {"t": round(cur["temperature_2m"]), "code": cur["weather_code"], "night": not cur.get("is_day", 1),
                        "cond": W.WMO.get(int(cur["weather_code"]), ("", "", ""))[li], "feels": round(cur["apparent_temperature"]),
                        "wind": round(cur["wind_speed_10m"]), "dir": dirs[int((float(cur["wind_direction_10m"]) + 22.5) // 45) % 8],
                        "hum": round(cur["relative_humidity_2m"])},
                "hours": hours, "days": days}

    def files(self):
        ok = True
        if time.time() - self.net_t >= self.hb["weather"]["refresh_min"] * 60 or not self.net_t:
            ok = self._refresh_net()
        pages = self.pc["pages"]
        files = dict(self.static)
        gallery, gf = self._gallery()
        media, mf = self._media()
        files.update(gf)
        files.update(mf)
        P = {"lang": self.lang, "title": self.pc["title"], "live": bool(self.pc.get("live", True)),
             "refresh_ms": 5 * 60000,
             "report_url": (self.hb.get("media_base") or "").replace("/hbbtv/media/", "/hbbtv/report") or None,
             "doom_url": None, "onid": self.cfg["ts"]["original_network_id"], "tsid": self.cfg["ts"]["transport_stream_id"],
             "idtype": IDTYPE.get(self.cfg["mode"], 12),
             "weather": self._weather_data() if pages.get("weather") else None, "weather_on": bool(pages.get("weather")),
             "radar": None, "news": self.net["news"] if pages.get("news") else None,
             "epg": self._epg() if pages.get("epg") else [], "gallery": gallery, "media": media,
             "games": bool(pages.get("games")), "tools": bool(pages.get("tools")),
             "ssh_url": (self.hb.get("media_base") or "").replace("/hbbtv/media/", "/hbbtv/ssh/") or None,
             "fx": self.net["fx"], "rf": self._rf(), "mux": self._mux(),
             "geo": {"lat": self.weather.loc[0], "lon": self.weather.loc[1], "place": self.weather.loc[2]} if self.weather.loc else None,
             "info": {"title": self.pc.get("info_title") or self.svc["name"], "text": self.pc.get("info_text") or ""} if pages.get("info") else None,
             "tech": "%s · %s · Muxwright" % (self.svc["name"], self.cfg["mode"].upper())}
        if pages.get("games"):
            doom = self._doom_files()
            files.update(doom)
            P["doom_url"] = self._doom_url(bool(doom))
        if pages.get("radar") and self.net["radar"]:
            files["img/radar.png"] = self.net["radar"][0]
            P["radar"] = {"img": "img/radar.png", "w": RADAR_WH[0], "h": RADAR_WH[1], "time": self.net["radar"][1],
                          "src": "Radar: Deutscher Wetterdienst (DWD)"}
        data = json.dumps(P, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("]]>", "]]\\u003e")
        files["index.html"] = self._page(data).encode("utf-8")
        return files, ok

    def _page(self, data):
        return "\n".join([
            '<?xml version="1.0" encoding="utf-8"?>',
            '<!DOCTYPE html PUBLIC "-//HbbTV//1.1.1//EN" "http://www.hbbtv.org/dtd/HbbTV-1.1.1.dtd">',
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="%s">' % self.lang,
            "<head>",
            '<meta http-equiv="content-type" content="application/vnd.hbbtv.xhtml+xml; charset=utf-8"/>',
            "<title>%s</title>" % W._esc(self.pc["title"]),
            '<link rel="stylesheet" type="text/css" href="portal.css"/>',
            '<script type="text/javascript">//<![CDATA[\nvar P = %s;\n//]]></script>' % data,
            '<script type="text/javascript" src="portal.js"></script>',
            '<script type="text/javascript" src="games.js"></script>',
            '<script type="text/javascript" src="games2.js"></script>',
            '<script type="text/javascript" src="tools.js"></script>',
            '<script type="text/javascript" src="tools2.js"></script>',
            "</head>",
            '<body onload="init()">',
            '<object type="application/oipfApplicationManager" id="appmgr" style="position:absolute;left:0;top:0;width:0;height:0"></object>',
            '<object type="video/broadcast" id="vb"></object>',
            '<div id="hint"><span class="rb"></span>%s</div>' % W._esc(self.pc["title"]),
            '<div id="portal">',
            ' <div id="live"></div><div id="livel">LIVE</div>',
            ' <div id="head" class="box"><div class="t" id="htitle"></div><div class="c" id="clock"></div></div>',
            ' <div id="menu" class="box"></div>',
            ' <div id="main" class="box"><div id="ptitle"></div><div id="pbody"></div><div id="pfoot"></div></div>',
            "</div>",
            '<div id="osd"></div>',
            '<div id="full"></div>',
            '<div id="osk"></div>',
            "</body>", "</html>", ""])
