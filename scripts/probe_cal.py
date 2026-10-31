# Sonde ponctuelle : sources automatiques pour les calendriers géopolitique et économique
import json, re, time, urllib.request, urllib.parse, os
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
R = {}
def get(u, hd=None, n=400000):
    h = {"User-Agent": UA, "Accept": "*/*"}; h.update(hd or {})
    with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=30) as r:
        return r.status, r.read(n).decode("utf-8", "replace")
def wd(term):
    st, t = get("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({"action": "wbsearchentities", "search": term, "language": "en", "format": "json", "limit": 3}), {"User-Agent": "macro-forex-robot/1.0 (github.com/philippembulu1-dot/macro-forex)"})
    res = json.loads(t).get("search") or []
    out = []
    for x in res[:2]:
        st, e = get("https://www.wikidata.org/wiki/Special:EntityData/%s.json" % x["id"], {"User-Agent": "macro-forex-robot/1.0"})
        cl = json.loads(e)["entities"][x["id"]].get("claims", {})
        dates = {}
        for p in ("P585", "P580", "P582"):
            for c in cl.get(p, []):
                v = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("time")
                if v: dates[p] = v
        out.append({"id": x["id"], "label": x.get("label"), "desc": x.get("description"), "dates": dates})
    return out
for term in ["2027 G20 summit", "G7 summit 2027", "53rd G7 summit", "2027 NATO summit", "COP32", "2027 United Nations Climate Change Conference", "APEC 2027", "2027 BRICS summit", "2027 French presidential election", "2026 G20 Miami summit", "2026 United States elections"]:
    try: R["wd:" + term] = wd(term)
    except Exception as e: R["wd:" + term] = repr(e)[:200]
    time.sleep(0.5)
for k, u in {"opec_home": "https://www.opec.org/", "opec_pr": "https://www.opec.org/press-releases.html", "opec_pr2": "https://opec.org/press-releases",
             "consilium_cal": "https://www.consilium.europa.eu/en/meetings/calendar/", "consilium_euco": "https://www.consilium.europa.eu/en/european-council/",
             "investing_widget": "https://sslecal2.investing.com/?columns=exc_flags,exc_currency,exc_importance,exc_actual,exc_forecast,exc_previous&features=datepicker,timezone,timeselector,filters&countries=5,72,4,35,12,6,25,43&calType=week&timeZone=16&lang=5",
             "myfxbook_widget": "https://widgets.myfxbook.com/widgets/calendar.html?lang=fr&impacts=0,1,2,3&symbols=USD,EUR,GBP,JPY,CHF,AUD,CAD,NZD,CNY",
             "ff_next": "https://nfs.faireconomy.media/ff_calendar_nextweek.json", "fxstreet": "https://calendar-api.fxstreet.com/fr/api/v1/eventDates/2026-11-02T00:00:00Z/2026-11-06T23:59:59Z?volatilities=HIGH"}.items():
    try:
        st, t = get(u)
        R[k] = {"ok": st, "len": len(t), "title": (re.search(r"<title>(.*?)</title>", t, re.S) or [None, ""])[1][:120] if "<title" in t else t[:200],
                "pr": re.findall(r'href="([^"]*pr-detail[^"]*)"', t)[:5], "next": re.findall(r"next (?:ONOMM |meeting)[^.<]{0,120}", t)[:3],
                "euco": re.findall(r"European Council[^<]{0,80}", t)[:6], "ics": re.findall(r'href="([^"]+\.ics[^"]*)"', t)[:3]}
    except Exception as e:
        R[k] = repr(e)[:200]
os.makedirs("data", exist_ok=True)
json.dump(R, open("data/probe-cal.json", "w"), ensure_ascii=False, indent=1)
