"""Partnerlinks für „Benötigte Hardware“ im Dashboard – aus den Website-Daten (website/data: slots.json, links.json,
amazon-*.json). Nur Links (Suche/Kurzlink/eBay/AliExpress), keine Preise: so gilt keine 24-h-Frist für Amazon-Daten.
Alle Links sind Werbung und werden im Dashboard als „Anzeige“ gekennzeichnet."""
import json, os, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "..", "website", "data"))
SPONSOR = [{"name": "Ko-fi", "url": "https://ko-fi.com/711it"}, {"name": "PayPal", "url": "https://paypal.me/711IT"}]
AMZ_HOST = {"de": "www.amazon.de", "us": "www.amazon.com"}


def _load(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


PACKAGED = os.path.join(HERE, "shop_data", "shop.json")   # im Installationspaket (installer/build.py), ohne Amazon-Produktdaten


EBAY_EU = ("de", "at", "ch", "fr", "it", "es", "nl", "be", "pl", "ie", "uk")
EBAY_NA = ("us", "ca")


def _ebay_site(market, ebay):
    ok = EBAY_NA if market == "us" else EBAY_EU
    return ebay if ebay in ok else ok[0]


def _packaged(market, lang, ebay=""):
    try:
        with open(PACKAGED, encoding="utf-8") as f:
            slots = json.load(f).get("slots") or []
    except (OSError, ValueError):
        return None
    out = [{"id": s["id"], "group": s.get("group"), "name": (s.get("name") or {}).get(lang) or (s.get("name") or {}).get("en") or (s.get("name") or {}).get("de") or s["id"],
            "note": (s.get("note") or {}).get(lang) or (s.get("note") or {}).get("en") or (s.get("note") or {}).get("de") or "", "amazon": (s.get("amazon") or {}).get(market, ""),
            "ebay": (s.get("ebay") or {}).get(_ebay_site(market, ebay)) or (s.get("ebay") or {}).get(market, ""), "ali": s.get("ali") or ""}
           for s in slots]
    return {"market": market, "ebay_site": _ebay_site(market, ebay), "slots": out, "sponsor": SPONSOR, "available": bool(out)}


def shop(market="de", lang="de", ebay=""):
    """market: „de“ = Europa (amazon.de), „us“ = USA/Kanada (amazon.com); ebay: Länderseite der Region."""
    market = market if market in AMZ_HOST else "de"
    site = _ebay_site(market, ebay)
    if not os.path.isfile(os.path.join(DATA, "slots.json")):
        return _packaged(market, lang, ebay) or {"market": market, "slots": [], "sponsor": SPONSOR, "available": False}
    slots = (_load("slots.json").get("slots")) or []
    links = (_load("links.json").get("slots")) or {}
    tag = _load(f"amazon-{market}.json").get("tag") or ""
    out = []
    for s in slots:
        q = s.get("search_de" if market == "de" else "search_us") or s.get("search_de") or ""
        short = (s.get("amazon_short") or {}).get(market) or ""
        amz = short if short.startswith("https://link.amazon/") else (
            f"https://{AMZ_HOST[market]}/s?k={urllib.parse.quote_plus(q)}&i={'computers' if s.get('group') == 'pc' else 'electronics'}&tag={tag}"
            if tag and q else "")
        ln = links.get(s.get("id"), {})
        name = s.get("name") or {}
        note = s.get("note") or {}
        out.append({"id": s.get("id"), "group": s.get("group"), "name": name.get(lang) or name.get("en") or name.get("de") or s.get("id"),
                    "note": note.get(lang) or note.get("en") or note.get("de") or "", "amazon": amz, "ebay": ln.get("ebay_" + site) or ln.get("ebay_" + market) or "",
                    "ali": ln.get("ali") or ""})
    return {"market": market, "ebay_site": site, "slots": out, "sponsor": SPONSOR, "available": bool(slots)}
