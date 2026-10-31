import json, urllib.request, time, os, datetime
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
ORIGIN = "https://philippembulu1-dot.github.io"
d0 = datetime.date.today(); d1 = d0 + datetime.timedelta(days=9)
P = {
 "cnbc_q": ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=US10Y%7CUS2Y%7CDE10Y-DE%7CFR10Y-FR%7C.VIX%7C.DXY%7C%40GC.1%7C.MOVE%7C%40LCO.1%7CEUR%3D&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json", {}),
 "cnbc_h": ("https://ts-api.cnbc.com/harmony/app/charts/1D.json?symbol=US10Y", {}),
 "tv_cal": ("https://economic-calendar.tradingview.com/events?from=%sT00:00:00.000Z&to=%sT23:59:59.000Z&countries=US,EU,GB,JP,CH,AU,CA,NZ,DE,FR,CN" % (d0, d1), {"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}),
 "tv_cal_noorigin": ("https://economic-calendar.tradingview.com/events?from=%sT00:00:00.000Z&to=%sT23:59:59.000Z&countries=US" % (d0, d1), {}),
 "ff_this": ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", {}),
 "ff_next": ("https://nfs.faireconomy.media/ff_calendar_nextweek.json", {}),
 "binance": ("https://data-api.binance.vision/api/v3/ticker/price?symbol=PAXGUSDT", {}),
 "frankfurter": ("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD", {}),
 "ecb": ("https://data-api.ecb.europa.eu/service/data/CISS/D.U2.Z0Z.4F.EC.SOV_GDPWN.IDX?lastNObservations=1&format=jsondata", {}),
 "kalshi": ("https://api.elections.kalshi.com/trade-api/v2/markets?limit=1", {}),
 "poly": ("https://gamma-api.polymarket.com/events?limit=1", {}),
 "treasury": ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_1?page[size]=1", {}),
 "yahoo": ("https://query1.finance.yahoo.com/v8/finance/chart/%5EMOVE?interval=1d&range=5d", {}),
 "cboe": ("https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json", {}),
}
out = {}
for k, (u, h) in P.items():
    hd = {"User-Agent": UA, "Accept": "*/*"}
    hd.update(h)
    if "Origin" not in h:
        hd["Origin"] = ORIGIN
    t = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=30)
        b = r.read().decode("utf-8", "replace")
        out[k] = {"ok": r.status, "acao": r.headers.get("Access-Control-Allow-Origin"), "len": len(b), "head": b[:1500]}
    except Exception as e:
        hh = getattr(e, "headers", None)
        out[k] = {"err": repr(e)[:200], "acao": hh.get("Access-Control-Allow-Origin") if hh else None}
    out[k]["s"] = round(time.time() - t, 1)
    # pré-vol CORS
    try:
        rq = urllib.request.Request(u, method="OPTIONS", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET", "User-Agent": UA})
        r2 = urllib.request.urlopen(rq, timeout=15)
        out[k]["preflight"] = [r2.status, r2.headers.get("Access-Control-Allow-Origin")]
    except Exception as e:
        out[k]["preflight"] = repr(e)[:80]
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe-live.json", "w"), ensure_ascii=False, indent=1)
