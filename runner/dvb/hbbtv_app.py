# -*- coding: utf-8 -*-
"""Inhalte fuer HbbTV im Objektkarussell: eingebaute Wetter-App oder ein Anwendungsordner.

Wetter: Ort per Open-Meteo-Geocoding (oder lat/lon), Vorhersage per Open-Meteo oder MET Norway (yr.no) - beide ohne
Schluessel, Quelle wird in der App genannt. provider "auto": Open-Meteo, bei Fehler (z. B. Tageslimit 429) MET Norway;
dessen Antwort wird in die Open-Meteo-Form umgerechnet (current/hourly/daily, WMO-Codes). Die Seite wird fertig gerendert (Daten im HTML, kein XHR auf dvb://), JavaScript nur fuer
rote Taste, Ein-/Ausblenden und Neuladen nach dem Aktualisierungsintervall (Empfaenger laden aus dem Karussell neu,
sobald die DII-Version steigt). XHTML nach HbbTV 1.1.1 (laeuft auch auf HbbTV-2-Geraeten), CSS ohne Flexbox.
"""
import datetime
import json
import os
import urllib.parse
import urllib.request

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
UA = "Muxwright/0.6.1 https://muxwright.com"      # MET Norway verlangt eine erkennbare Kennung mit Kontakt
FOLDER_MAX = 4 * 1048576          # Anwendungsordner: mehr ist im Karussell nicht sinnvoll (Ladezeit)

# WMO-Wettercodes (Open-Meteo) -> (deutsch, englisch, Symbol)
WMO = {
    0: ("Klar", "Clear", "sun"), 1: ("Überwiegend klar", "Mainly clear", "sun"), 2: ("Teils bewölkt", "Partly cloudy", "part"),
    3: ("Bedeckt", "Overcast", "cloud"), 45: ("Nebel", "Fog", "fog"), 48: ("Reifnebel", "Rime fog", "fog"),
    51: ("Leichter Niesel", "Light drizzle", "rain"), 53: ("Niesel", "Drizzle", "rain"), 55: ("Starker Niesel", "Dense drizzle", "rain"),
    56: ("Gefrierender Niesel", "Freezing drizzle", "rain"), 57: ("Gefrierender Niesel", "Freezing drizzle", "rain"),
    61: ("Leichter Regen", "Light rain", "rain"), 63: ("Regen", "Rain", "rain"), 65: ("Starker Regen", "Heavy rain", "rain"),
    66: ("Gefrierender Regen", "Freezing rain", "rain"), 67: ("Gefrierender Regen", "Freezing rain", "rain"),
    71: ("Leichter Schnee", "Light snow", "snow"), 73: ("Schnee", "Snow", "snow"), 75: ("Starker Schnee", "Heavy snow", "snow"),
    77: ("Schneegriesel", "Snow grains", "snow"), 80: ("Regenschauer", "Rain showers", "rain"), 81: ("Regenschauer", "Rain showers", "rain"),
    82: ("Heftige Schauer", "Violent showers", "rain"), 85: ("Schneeschauer", "Snow showers", "snow"), 86: ("Schneeschauer", "Snow showers", "snow"),
    95: ("Gewitter", "Thunderstorm", "storm"), 96: ("Gewitter mit Hagel", "Thunderstorm, hail", "storm"),
    99: ("Gewitter mit Hagel", "Thunderstorm, hail", "storm"),
}
TXT = {
    "de": {"feels": "gefühlt", "wind": "Wind", "hum": "Feuchte", "rain": "Regen", "hours": "Die nächsten Stunden",
           "days": "Die nächsten Tage", "as_of": "Stand", "src": "Wetterdaten: {src}", "hint": "Wetter",
           "close": "Rote Taste: schließen", "none": "Wetterdaten zurzeit nicht verfügbar",
           "wd": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], "today": "Heute", "clock": "Uhr"},
    "en": {"feels": "feels like", "wind": "Wind", "hum": "Humidity", "rain": "Rain", "hours": "Next hours",
           "days": "Next days", "as_of": "Updated", "src": "Weather data: {src}", "hint": "Weather",
           "close": "Red button: close", "none": "Weather data currently unavailable",
           "wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "today": "Today", "clock": ""},
}
DIRS = {"de": ["N", "NO", "O", "SO", "S", "SW", "W", "NW"], "en": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]}


def _get(url, params, timeout=10):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(place, lang="de"):
    """Ortsname -> (lat, lon, Anzeigename) oder None."""
    j = _get(GEO_URL, {"name": place, "count": 1, "language": lang, "format": "json"})
    r = (j.get("results") or [None])[0]
    if not r:
        return None
    return float(r["latitude"]), float(r["longitude"]), r.get("name") or place


def fetch_weather(lat, lon):
    return _get(WX_URL, {
        "latitude": "%.4f" % lat, "longitude": "%.4f" % lon, "timezone": "auto", "forecast_days": 6, "wind_speed_unit": "kmh",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,is_day",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})


# MET Norway symbol_code (ohne _day/_night/_polartwilight) -> WMO-Code
MET_WMO = {"clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3, "fog": 45,
           "lightrain": 61, "rain": 63, "heavyrain": 65, "lightrainshowers": 80, "rainshowers": 81, "heavyrainshowers": 82,
           "lightsleet": 66, "sleet": 66, "heavysleet": 67, "lightsleetshowers": 66, "sleetshowers": 66, "heavysleetshowers": 67,
           "lightsnow": 71, "snow": 73, "heavysnow": 75, "lightsnowshowers": 85, "snowshowers": 85, "heavysnowshowers": 86}


def _met_code(sym):
    base = (sym or "cloudy").split("_")[0]
    if "thunder" in base:
        return 95
    return MET_WMO.get(base, 3)


def _feels(t, v_kmh):
    """Windchill (DWD/NWS) unter 10 °C und ab 5 km/h, sonst die Lufttemperatur."""
    if t <= 10 and v_kmh >= 5:
        return 13.12 + 0.6215 * t - 11.37 * v_kmh ** 0.16 + 0.3965 * t * v_kmh ** 0.16
    return t


def fetch_met(lat, lon, tz=None):
    """MET Norway locationforecast compact -> Open-Meteo-Form (Zeiten lokal, Wind in km/h)."""
    j = _get(MET_URL, {"lat": "%.4f" % lat, "lon": "%.4f" % lon})
    ts = j["properties"]["timeseries"]
    zone = None
    if tz:
        try:
            import zoneinfo
            zone = zoneinfo.ZoneInfo(tz)
        except Exception:
            zone = None

    def local(t):
        d = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d.astimezone(zone) if zone else d.astimezone()

    hours = {"time": [], "temperature_2m": [], "weather_code": [], "precipitation_probability": []}
    days = {}
    for e in ts:
        det = e["data"]["instant"]["details"]
        nxt = e["data"].get("next_1_hours") or e["data"].get("next_6_hours") or {}
        sym = (nxt.get("summary") or {}).get("symbol_code")
        lt = local(e["time"])
        if "next_1_hours" in e["data"]:
            hours["time"].append(lt.strftime("%Y-%m-%dT%H:%M"))
            hours["temperature_2m"].append(det["air_temperature"])
            hours["weather_code"].append(_met_code(sym))
            hours["precipitation_probability"].append(None)
        d = days.setdefault(lt.date().isoformat(), {"t": [], "sym": None, "best": 99})
        d["t"].append(det["air_temperature"])
        s6 = ((e["data"].get("next_6_hours") or {}).get("summary") or {}).get("symbol_code")
        if s6 and abs(lt.hour - 12) < d["best"]:       # Tagessymbol: 6-h-Vorhersage ab etwa Mittag
            d["sym"], d["best"] = s6, abs(lt.hour - 12)
    first = ts[0]
    det = first["data"]["instant"]["details"]
    sym = ((first["data"].get("next_1_hours") or {}).get("summary") or {}).get("symbol_code") or ""
    v = det.get("wind_speed", 0) * 3.6
    dk = sorted(days)[:6]
    return {
        "_src": "MET Norway (yr.no)",
        "current": {"time": local(first["time"]).strftime("%Y-%m-%dT%H:%M"), "temperature_2m": det["air_temperature"],
                    "relative_humidity_2m": det.get("relative_humidity", 0), "apparent_temperature": _feels(det["air_temperature"], v),
                    "weather_code": _met_code(sym), "wind_speed_10m": v, "wind_direction_10m": det.get("wind_from_direction", 0),
                    "is_day": 0 if "_night" in sym else 1},
        "hourly": hours,
        "daily": {"time": dk, "weather_code": [_met_code(days[k]["sym"]) for k in dk],
                  "temperature_2m_max": [max(days[k]["t"]) for k in dk], "temperature_2m_min": [min(days[k]["t"]) for k in dk],
                  "precipitation_probability_max": [None for _ in dk]}}


def _now_local(tz=None):
    try:
        import zoneinfo
        return datetime.datetime.now(zoneinfo.ZoneInfo(tz)) if tz else datetime.datetime.now().astimezone()
    except Exception:
        return datetime.datetime.now().astimezone()


def fetch_any(lat, lon, provider="auto", tz=None, log=None):
    """Vorhersage vom gewaehlten Anbieter; 'auto' = Open-Meteo, dann MET Norway."""
    order = {"open-meteo": [fetch_weather], "met.no": [fetch_met]}.get(provider, [fetch_weather, fetch_met])
    err = None
    for f in order:
        try:
            wx = f(lat, lon, tz) if f is fetch_met else f(lat, lon)
            wx.setdefault("_src", "Open-Meteo.com")
            wx["_fetched"] = _now_local(tz).strftime("%H:%M")      # "Stand" = Abrufzeit, nicht erster Vorhersagewert
            return wx
        except Exception as e:
            err = e
            if log and f is not order[-1]:
                log("info", "HbbTV-Wetter: %s nicht erreichbar (%s), versuche Ausweichquelle" % ("MET Norway" if f is fetch_met else "Open-Meteo", e))
    raise err


# ------------------------------------------------------------------------------------------------ Darstellung
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _icon(code, size, night=False):
    """Wettersymbol aus CSS-Formen (keine Bilder, keine Schriftzeichen, die alte TVs nicht haben)."""
    kind = WMO.get(int(code), ("", "", "cloud"))[2]
    s = size
    parts = []
    if kind in ("sun", "part"):
        c = "#cfd8e6" if night else "#ffb300"
        d = s * (0.62 if kind == "sun" else 0.5)
        o = (s - d) / 2 if kind == "sun" else s * 0.06
        parts.append('<div class="ic-sun" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx;background:%s"></div>' % (o, o, d, d, c))
    if kind in ("part", "cloud", "rain", "snow", "storm", "fog"):
        top = s * (0.30 if kind == "part" else 0.18)
        col = "#9aa7b8" if kind in ("rain", "snow", "storm") else "#e3e8ef"
        parts.append('<div class="ic-cl" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx;background:%s"></div>'
                     % (s * 0.10, top + s * 0.18, s * 0.80, s * 0.30, col))
        parts.append('<div class="ic-cl" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx;background:%s"></div>'
                     % (s * 0.28, top, s * 0.40, s * 0.40, col))
    if kind == "rain":
        for i in range(3):
            parts.append('<div class="ic-dr" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx"></div>'
                         % (s * (0.25 + 0.2 * i), s * 0.72, max(2, s * 0.06), s * 0.18))
    elif kind == "snow":
        for i in range(3):
            parts.append('<div class="ic-sn" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx"></div>'
                         % (s * (0.24 + 0.2 * i), s * 0.74, s * 0.11, s * 0.11))
    elif kind == "storm":
        parts.append('<div class="ic-st" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx"></div>' % (s * 0.44, s * 0.64, s * 0.1, s * 0.3))
    elif kind == "fog":
        for i in range(2):
            parts.append('<div class="ic-fg" style="left:%dpx;top:%dpx;width:%dpx;height:%dpx"></div>'
                         % (s * 0.12, s * (0.70 + 0.13 * i), s * 0.76, max(3, s * 0.07)))
    return '<div class="ic" style="width:%dpx;height:%dpx">%s</div>' % (s, s, "".join(parts))


CSS = """
body{margin:0;padding:0;width:1280px;height:720px;overflow:hidden;background:transparent;font-family:Tiresias,'Tiresias Screenfont',Arial,sans-serif;color:#fff}
#hint{position:absolute;right:96px;bottom:48px;height:44px;padding:0 18px 0 12px;background:rgba(12,22,40,0.95);border-radius:22px;font-size:24px;line-height:44px;visibility:hidden}
#hint .rb{display:inline-block;width:22px;height:22px;border-radius:11px;background:#e2231a;margin:0 10px -3px 0}
#panel{position:absolute;left:96px;top:36px;width:1088px;height:648px;background:rgba(10,18,34,0.97);border-radius:14px;visibility:hidden}
.ic{position:relative;display:inline-block}
.ic div{position:absolute}
.ic-sun,.ic-cl,.ic-sn{border-radius:999px}
.ic-dr{background:#59a8ff;border-radius:2px}
.ic-sn{background:#fff}
.ic-st{background:#ffd600}
.ic-fg{background:#c5ccd6;border-radius:3px}
#place{position:absolute;left:32px;top:20px;font-size:38px;font-weight:bold}
#now{position:absolute;left:32px;top:74px;width:520px;height:150px}
#now .ic{position:absolute;left:0;top:0}
#temp{position:absolute;left:160px;top:-6px;font-size:92px;line-height:110px}
#cond{position:absolute;left:164px;top:104px;font-size:28px;color:#cfe0ff}
#facts{position:absolute;left:560px;top:84px;width:496px;font-size:26px;line-height:42px}
#facts span{color:#9fb3d1}
h2{position:absolute;left:32px;margin:0;font-size:24px;font-weight:normal;color:#9fb3d1}
.row{position:absolute;left:32px;width:1024px}
.cell{position:absolute;top:0;text-align:center}
.cell .t{font-size:24px;color:#cfe0ff}
.cell .v{font-size:30px}
.cell .p{font-size:20px;color:#59a8ff}
.cell .ic{margin:4px 0 0 0}
.cell .p{height:24px}
#foot{position:absolute;left:32px;width:1024px;top:610px;font-size:20px;color:#9fb3d1}
#foot .r{position:absolute;right:0;top:0}
#none{position:absolute;left:32px;top:120px;font-size:34px}
"""

JS = """
var KEY_RED = (typeof VK_RED != 'undefined') ? VK_RED : 403, KEY_BACK = (typeof VK_BACK != 'undefined') ? VK_BACK : 461;
var app = null, shown = false, reloadDue = false;
function keyset(m) { try { if (app && app.privateData && app.privateData.keyset) app.privateData.keyset.setValue(m); } catch (e) {} }
function vis(id, on) { var e = document.getElementById(id); if (e) e.style.visibility = on ? 'visible' : 'hidden'; }
function show(on) {
  shown = on; vis('panel', on); vis('hint', !on && hintOn); keyset(on ? 0x11 : 0x1);
  if (!on && reloadDue) document.location.reload();
}
var hintOn = true;
function init() {
  try { var m = document.getElementById('appmgr'); app = m.getOwnerApplication(document); app.show(); } catch (e) {}
  show(document.location.hash == '#open');
  setTimeout(function () { hintOn = false; if (!shown) vis('hint', false); }, 15000);
  setTimeout(function () { reloadDue = true; if (!shown) document.location.reload(); }, REFRESH_MS);
}
document.onkeydown = function (ev) {
  ev = ev || window.event; var k = ev.keyCode;
  if (k == KEY_RED) { show(!shown); hintOn = false; }
  else if (shown && (k == KEY_BACK || k == 8 || k == 27)) { show(false); }
  else return true;
  if (ev.preventDefault) ev.preventDefault();
  return false;
};
"""


def render_weather(wx, place, lang="de", refresh_min=15, error=None):
    """XHTML-Seite (bytes) fuer index.html; wx = Open-Meteo-Antwort oder None."""
    L = TXT.get(lang, TXT["en"])
    li = 0 if lang == "de" else 1
    body = []
    if wx:
        cur = wx["current"]
        night = not cur.get("is_day", 1)
        cond = WMO.get(int(cur["weather_code"]), ("", "", ""))[li]
        d = DIRS["de" if lang == "de" else "en"][int((float(cur["wind_direction_10m"]) + 22.5) // 45) % 8]
        body += ['<div id="place">%s</div>' % _esc(place),
                 '<div id="now">%s<div id="temp">%d°</div><div id="cond">%s</div></div>'
                 % (_icon(cur["weather_code"], 130, night), round(cur["temperature_2m"]), _esc(cond)),
                 '<div id="facts"><span>%s</span> %d°<br/><span>%s</span> %d km/h %s<br/><span>%s</span> %d %%</div>'
                 % (L["feels"], round(cur["apparent_temperature"]), L["wind"], round(cur["wind_speed_10m"]), d,
                    L["hum"], round(cur["relative_humidity_2m"]))]
        # Stunden: ab der aktuellen Stunde alle 3 h, 8 Werte
        h = wx["hourly"]
        now = cur["time"][:13]
        start = next((i for i, t in enumerate(h["time"]) if t[:13] >= now), 0)
        idx = [i for i in range(start + 1, len(h["time"]), 3)][:8]
        body.append('<h2 style="top:236px">%s</h2><div class="row" style="top:268px">' % L["hours"])
        for n, i in enumerate(idx):
            pp = h["precipitation_probability"][i]
            body.append('<div class="cell" style="left:%dpx;width:128px"><div class="t">%s %s</div>%s<div class="v">%d°</div>'
                        '<div class="p">%s</div></div>'
                        % (n * 128, h["time"][i][11:16], L["clock"], _icon(h["weather_code"][i], 48),
                           round(h["temperature_2m"][i]), ("%d %%" % pp) if pp is not None else "&#160;"))
        body.append("</div>")
        dd = wx["daily"]
        body.append('<h2 style="top:426px">%s</h2><div class="row" style="top:458px">' % L["days"])
        for n in range(min(6, len(dd["time"]))):
            day = datetime.date.fromisoformat(dd["time"][n])
            name = L["today"] if n == 0 else L["wd"][day.weekday()]
            pp = dd["precipitation_probability_max"][n]
            body.append('<div class="cell" style="left:%dpx;width:170px"><div class="t">%s</div>%s<div class="v">%d° / %d°</div>'
                        '<div class="p">%s</div></div>'
                        % (n * 170, _esc(name), _icon(dd["weather_code"][n], 48), round(dd["temperature_2m_max"][n]),
                           round(dd["temperature_2m_min"][n]), ("%s %d %%" % (L["rain"], pp)) if pp is not None else "&#160;"))
        body.append("</div>")
        as_of = wx.get("_fetched") or cur["time"][11:16]
        hint_t = "%s %d°" % (L["hint"], round(cur["temperature_2m"]))
    else:
        body += ['<div id="place">%s</div>' % _esc(place), '<div id="none">%s</div>' % L["none"]]
        as_of = datetime.datetime.now().strftime("%H:%M")
        hint_t = L["hint"]
    src = L["src"].format(src=(wx or {}).get("_src", "Open-Meteo.com / MET Norway"))
    body.append('<div id="foot">%s %s · %s · Muxwright<div class="r">%s</div></div>' % (L["as_of"], as_of, _esc(src), L["close"]))
    page = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE html PUBLIC "-//HbbTV//1.1.1//EN" "http://www.hbbtv.org/dtd/HbbTV-1.1.1.dtd">',
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="%s">' % lang,
        "<head>", '<meta http-equiv="content-type" content="application/vnd.hbbtv.xhtml+xml; charset=utf-8"/>',
        "<title>%s</title>" % _esc(L["hint"]),
        '<style type="text/css">%s</style>' % CSS,
        '<script type="text/javascript">//<![CDATA[\nvar REFRESH_MS = %d;\n%s//]]></script>' % (max(1, int(refresh_min)) * 60000, JS),
        "</head>", '<body onload="init()">',
        '<object type="application/oipfApplicationManager" id="appmgr" style="position:absolute;left:0;top:0;width:0;height:0"></object>',
        '<div id="hint"><span class="rb"></span>%s</div>' % _esc(hint_t),
        '<div id="panel">%s</div>' % "".join(body),
        "</body>", "</html>", ""]
    return "\n".join(page).encode("utf-8")


class WeatherApp:
    """Haelt den Ort fest (Geocoding einmal) und liefert {Pfad: bytes} fuer das Karussell."""

    def __init__(self, conf, lang="de", log=None):
        self.conf, self.lang, self.log = conf, lang, log or (lambda *a: None)
        self.loc = None
        self.last = None            # letzte gueltige Antwort (bei Ausfall weiter zeigen)

    def _locate(self):
        c = self.conf
        if c.get("lat") is not None and c.get("lon") is not None:
            return float(c["lat"]), float(c["lon"]), c.get("place") or "%.2f, %.2f" % (float(c["lat"]), float(c["lon"]))
        g = geocode(c.get("place") or "Berlin", self.lang)
        if not g:
            raise ValueError("Ort %r nicht gefunden" % c.get("place"))
        return g

    def files(self):
        err = None
        try:
            if not self.loc:
                self.loc = self._locate()
                self.log("info", "HbbTV-Wetter: %s (%.3f, %.3f)" % (self.loc[2], self.loc[0], self.loc[1]))
            self.last = fetch_any(self.loc[0], self.loc[1], self.conf.get("provider", "auto"), self.conf.get("timezone"), self.log)
        except Exception as e:
            err = str(e)
            self.log("warn", "HbbTV-Wetter: Abruf fehlgeschlagen (%s)%s" % (e, ", zeige letzten Stand" if self.last else ""))
        place = self.loc[2] if self.loc else (self.conf.get("place") or "")
        return {"index.html": render_weather(self.last, place, self.lang, self.conf.get("refresh_min", 15), err)}, err is None


def folder_files(path):
    """Anwendungsordner -> {relativer Pfad: bytes}; versteckte Dateien werden ausgelassen."""
    out, total = {}, 0
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, path).replace("\\", "/")
            with open(p, "rb") as fh:
                out[rel] = fh.read()
            total += len(out[rel])
            if total > FOLDER_MAX:
                raise ValueError("HbbTV-Ordner %s ist groesser als %d MB" % (path, FOLDER_MAX // 1048576))
    return out
