import json, urllib.request, urllib.parse, os, datetime, collections
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
SY = ["US10Y", "US2Y", "US5Y", "US30Y", "DE10Y-DE", "DE2Y-DE", "FR10Y-FR", "IT10Y-IT", "GB10Y-GB", "JP10Y-JP", ".VIX", ".DXY", "@GC.1", "@SI.1", ".MOVE", "MOVE", "@LCO.1", "@CL.1", "EUR=", "JPY=", "GBP=", "CHF=", "AUD=", "CAD=", "NZD=", "XAU=", "BTC.CM=", ".SPX", ".NDX", "@SP.1", "@ND.1", ".GDAXI", ".N225", ".VSTOXX", "@NG.1", "TTF.1", ".GVZ"]
out = {}
for i in range(0, len(SY), 12):
    part = SY[i:i + 12]
    u = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=%s&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json" % "%7C".join(urllib.parse.quote(s, safe="") for s in part)
    j = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30).read())
    for q in j["FormattedQuoteResult"]["FormattedQuote"]:
        out[q.get("symbol")] = [q.get("name"), q.get("last"), q.get("change"), q.get("last_time"), q.get("curmktstatus"), q.get("code"), q.get("realTime")]
d0 = datetime.date.today(); d1 = d0 + datetime.timedelta(days=9)
u = "https://economic-calendar.tradingview.com/events?from=%sT00:00:00.000Z&to=%sT23:59:59.000Z&countries=US,EU,GB,JP,CH,AU,CA,NZ,DE,FR,CN" % (d0 - datetime.timedelta(days=3), d1)
j = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}), timeout=40).read())
ev = j["result"]
cal = {"n": len(ev), "imp": collections.Counter(e.get("importance") for e in ev), "cty": collections.Counter(e.get("country") for e in ev),
       "keys": sorted(set(k for e in ev for k in e)), "hi": [[e["date"], e["country"], e["title"], e.get("importance"), e.get("actual"), e.get("forecast"), e.get("previous"), e.get("unit"), e.get("scale")] for e in ev if e.get("importance") == 1][:60]}
os.makedirs("data", exist_ok=True)
json.dump({"cnbc": out, "cal": cal}, open("data/probe-live.json", "w"), ensure_ascii=False, indent=1)
