# -*- coding: utf-8 -*-
"""Test historique de la stratégie XAU/USD (score S à six piliers).

Lancé par .github/workflows/strategy-analysis.yml (serveurs GitHub, qui joignent les sources).
Écrit data/strategy-analysis.json. Bibliothèque standard uniquement.

Piliers testables avec un historique gratuit : taux réels (TIPS 10 ans), attentes Fed
(approximées par le rendement US 2 ans : pas d'historique gratuit des Fed Funds futures),
dollar (DXY), positionnement (COT CFTC) et risque (approximé par le VIX : la géopolitique
n'a pas d'historique chiffré). Le pilier Flux (ETF, banques centrales) n'a pas d'historique
quotidien gratuit : il vaut 0 dans le test. Aucun chiffre n'est inventé : tout vient des séries.
"""
import json, math, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "strategy-analysis.json")
START = "2006-01-01"
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def http(url, headers=None, timeout=40, tries=3):
    last = None
    for i in range(tries):
        try:
            hd = {"User-Agent": UA, "Accept": "*/*"}
            hd.update(headers or {})
            with urllib.request.urlopen(urllib.request.Request(url, headers=hd), timeout=timeout) as r:
                return r.read().decode("utf-8-sig", "replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(3)
    raise last


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


# ---------------------------------------------------------------- sources
def fred(sid, start=START):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("clé FRED absente")
    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
        {"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start})
    j = json.loads(http(url))
    out = [(o["date"], num(o["value"])) for o in j.get("observations", [])]
    out = [(d, v) for d, v in out if v is not None]
    if len(out) < 50:
        raise ValueError("série %s trop courte" % sid)
    return out


def yahoo_daily(sym):
    p1, p2 = int(datetime(2005, 1, 1, tzinfo=timezone.utc).timestamp()), int(time.time())
    last = None
    for host in ("query2", "query1"):
        for q in ("interval=1d&range=max", "interval=1d&period1=%d&period2=%d" % (p1, p2), "interval=1d&range=25y"):
            for ua in (UA, "macro-forex robot (https://github.com/philippembulu1-dot/macro-forex)"):
                try:
                    j = json.loads(http("https://%s.finance.yahoo.com/v8/finance/chart/%s?%s" % (host, urllib.parse.quote(sym), q), headers={"User-Agent": ua}, tries=1))
                    r = j["chart"]["result"][0]
                    qq = r["indicators"]["quote"][0]
                    out = []
                    for t, h, l, c in zip(r["timestamp"], qq["high"], qq["low"], qq["close"]):
                        if None in (h, l, c):
                            continue
                        out.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), h, l, c))
                    if len(out) < 500 or out[0][0] > "2007-01-01":
                        raise ValueError("historique %s trop court (%d, début %s)" % (sym, len(out), out[0][0] if out else "-"))
                    log("yahoo", sym, host, q[:20])
                    return out
                except Exception as e:  # noqa
                    last = e
                    time.sleep(6)
    raise last


def yahoo_hourly(sym):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=60m&range=730d" % urllib.parse.quote(sym)
    j = json.loads(http(url))
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    return [(t, h, l, c) for t, h, l, c in zip(r["timestamp"], q["high"], q["low"], q["close"]) if None not in (h, l, c)]


def cot_gold():
    q = urllib.parse.urlencode({
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all",
        "$where": "cftc_contract_market_code='088691' AND report_date_as_yyyy_mm_dd >= '2003-01-01'",
        "$order": "report_date_as_yyyy_mm_dd", "$limit": "5000"})
    rows = json.loads(http("https://publicreporting.cftc.gov/resource/6dca-aqww.json?" + q))
    out = []
    for r in rows:
        L, S, OI = num(r.get("noncomm_positions_long_all")), num(r.get("noncomm_positions_short_all")), num(r.get("open_interest_all"))
        if None in (L, S, OI) or OI <= 0:
            continue
        out.append((r["report_date_as_yyyy_mm_dd"][:10], (L - S) / OI * 100))
    if len(out) < 200:
        raise ValueError("COT trop court")
    return out


# ---------------------------------------------------------------- outils
def asof(series):
    """Renvoie une fonction date -> dernière valeur connue à cette date (ou None)."""
    ds = [d for d, _ in series]
    vs = [v for _, v in series]
    import bisect

    def f(d):
        i = bisect.bisect_right(ds, d) - 1
        return vs[i] if i >= 0 else None
    return f


def pctl(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def rank_pct(hist, x):
    if not hist:
        return None
    return sum(1 for h in hist if h < x) / len(hist) * 100


def corr(a, b):
    n = len(a)
    if n < 10:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def stats(rets):
    n = len(rets)
    if n == 0:
        return {"n": 0}
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (n - 1)) if n > 1 else 0
    return {"n": n, "moy": round(m, 3), "med": round(sorted(rets)[n // 2], 3), "hausse_pct": round(sum(1 for r in rets if r > 0) / n * 100, 1), "ecart_type": round(sd, 3)}


def note_change(d, big, small, invert=True):
    """Note d'après une variation : baisse = favorable à l'or si invert."""
    if d is None:
        return None
    x = -d if invert else d
    if x >= big:
        return 2
    if x >= small:
        return 1
    if x <= -big:
        return -2
    if x <= -small:
        return -1
    return 0


# ---------------------------------------------------------------- analyse
def main():
    res = {"genere": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "sources": {}, "notes_methode": []}
    S = {}
    for name, fn in (("dfii10", lambda: fred("DFII10", "2003-01-01")), ("dgs2", lambda: fred("DGS2", "2003-01-01")),
                     ("vix", lambda: fred("VIXCLS", "2003-01-01")), ("cot", cot_gold),
                     ("t10y2y", lambda: fred("T10Y2Y", "2003-01-01")), ("nfci", lambda: fred("NFCI", "2003-01-01")),
                     ("sahm", lambda: fred("SAHMREALTIME", "2003-01-01")), ("icsa", lambda: fred("ICSA", "2003-01-01")),
                     ("pcecore", lambda: fred("PCEPILFE", "2002-01-01")), ("hy", lambda: fred("BAMLH0A0HYM2", "2003-01-01")),
                     ("gold", lambda: yahoo_daily("GC=F")), ("dxy", lambda: yahoo_daily("DX-Y.NYB"))):
        try:
            S[name] = fn()
            first = S[name][0][0]
            res["sources"][name] = {"ok": True, "debut": first, "fin": S[name][-1][0], "n": len(S[name])}
            log("OK", name, first, S[name][-1][0], len(S[name]))
        except Exception as e:  # noqa
            res["sources"][name] = {"ok": False, "erreur": str(e)[:200]}
            log("ÉCHEC", name, e)
    try:
        S["gold_h1"] = yahoo_hourly("GC=F")
        res["sources"]["gold_h1"] = {"ok": True, "n": len(S["gold_h1"])}
    except Exception as e:  # noqa
        res["sources"]["gold_h1"] = {"ok": False, "erreur": str(e)[:200]}

    if "gold" not in S:
        raise SystemExit("or indisponible : analyse impossible")
    G = [g for g in S["gold"] if g[0] >= START]
    dates = [g[0] for g in G]
    close = [g[3] for g in G]
    N = len(G)
    A = {k: asof([(d, v) for d, v in S[k]]) for k in ("dfii10", "dgs2", "vix", "t10y2y", "nfci", "sahm", "icsa", "hy") if k in S}
    if "dxy" in S:
        A["dxy"] = asof([(d, c) for d, h, l, c in S["dxy"]])
    if "cot" in S:
        # Le COT du mardi est publié le vendredi : on ne l'utilise qu'à partir du vendredi suivant.
        A["cot"] = asof([((datetime.strptime(d, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d"), v) for d, v in S["cot"]])
        cot_list = [((datetime.strptime(d, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d"), v) for d, v in S["cot"]]

    def val(k, i):
        return A[k](dates[i]) if k in A and i >= 0 else None

    def chg(k, i, n):
        a, b = val(k, i), val(k, i - n)
        return None if a is None or b is None else a - b

    # --- 1. Distribution des variations (pour calibrer les seuils)
    dist = {}
    for k, lab in (("dfii10", "TIPS 10 ans"), ("dgs2", "US 2 ans")):
        if k not in A:
            continue
        for n in (5, 20):
            for per, lo, hi in (("2006-2026", "2006", "2100"), ("2022-2026", "2022", "2100")):
                xs = sorted(abs(chg(k, i, n)) * 100 for i in range(n, N) if dates[i][:4] >= lo and dates[i][:4] < hi and chg(k, i, n) is not None)
                dist["%s_%dj_%s" % (k, n, per)] = {"lab": lab, "pb_p50": round(pctl(xs, 50), 1), "pb_p75": round(pctl(xs, 75), 1), "pb_p90": round(pctl(xs, 90), 1),
                                                   "part_au_dessus_10pb": round(sum(1 for x in xs if x >= 10) / len(xs) * 100, 1),
                                                   "part_au_dessus_15pb": round(sum(1 for x in xs if x >= 15) / len(xs) * 100, 1),
                                                   "part_au_dessus_25pb": round(sum(1 for x in xs if x >= 25) / len(xs) * 100, 1),
                                                   "part_au_dessus_30pb": round(sum(1 for x in xs if x >= 30) / len(xs) * 100, 1)}
    if "dxy" in A:
        for n in (5, 20):
            xs = sorted(abs(val("dxy", i) / val("dxy", i - n) - 1) * 100 for i in range(n, N) if val("dxy", i) and val("dxy", i - n))
            dist["dxy_%dj" % n] = {"lab": "DXY en %", "p50": round(pctl(xs, 50), 2), "p75": round(pctl(xs, 75), 2), "p90": round(pctl(xs, 90), 2),
                                   "part_au_dessus_0_5pct": round(sum(1 for x in xs if x >= 0.5) / len(xs) * 100, 1)}
    res["distribution"] = dist

    # --- 2. Notes par pilier (grille du document) et score S
    W = {"taux_reels": 0.25, "fed": 0.20, "dollar": 0.15, "flux": 0.15, "risque": 0.15, "position": 0.10}

    def notes(i, n):
        big = 0.30 if n == 20 else 0.15
        small = 0.10 if n == 20 else 0.05
        fbig, fsmall = (0.25, 0.10) if n == 20 else (0.15, 0.05)
        r = {}
        r["taux_reels"] = note_change(chg("dfii10", i, n), big, small)
        r["fed"] = note_change(chg("dgs2", i, n), fbig, fsmall)
        if "dxy" in A and i >= 63:
            px = [val("dxy", j) for j in range(i - 63, i)]
            cur = val("dxy", i)
            look = 63 if n == 20 else 20
            win = [p for p in px[-look:] if p]
            ma50 = [val("dxy", j) for j in range(i - 49, i + 1)]
            ma50 = sum(ma50) / 50 if None not in ma50 else None
            d = chg("dxy", i, n)
            if cur is None or not win or ma50 is None or d is None:
                r["dollar"] = None
            elif cur < min(win):
                r["dollar"] = 2
            elif cur > max(win):
                r["dollar"] = -2
            elif n == 20 and cur < ma50 and d < 0:
                r["dollar"] = 1
            elif n == 20 and cur > ma50 and d > 0:
                r["dollar"] = -1
            elif n == 5 and d / cur * 100 <= -0.5:
                r["dollar"] = 1
            elif n == 5 and d / cur * 100 >= 0.5:
                r["dollar"] = -1
            else:
                r["dollar"] = 0
        else:
            r["dollar"] = None
        r["flux"] = 0
        v, dv = val("vix", i), chg("vix", i, n)
        if v is None or dv is None:
            r["risque"] = None
        else:
            r["risque"] = 1 if (v >= 20 and dv > 0) else (-1 if v < 15 else 0)
        if "cot" in A:
            cur = val("cot", i)
            hist = [x for d, x in cot_list if d <= dates[i]][-157:-1]
            if cur is None or len(hist) < 100:
                r["position"] = None
            else:
                p = rank_pct(hist, cur)
                r["position"] = 2 if p < 10 else 1 if p < 25 else -2 if p > 90 else -1 if p > 75 else 0
        else:
            r["position"] = None
        return r

    def S_of(r):
        if any(r[k] is None for k in ("taux_reels", "fed", "dollar", "risque", "position")):
            return None
        return sum(W[k] * r[k] for k in W)

    ma200 = [None] * N
    acc = 0.0
    for i in range(N):
        acc += close[i]
        if i >= 200:
            acc -= close[i - 200]
        if i >= 199:
            ma200[i] = acc / 200

    rows = []  # échantillon hebdomadaire (1 jour sur 5) pour limiter les chevauchements
    for i in range(70, N - 1, 5):
        r20, r5 = notes(i, 20), notes(i, 5)
        f20 = (close[i + 20] / close[i] - 1) * 100 if i + 20 < N else None
        f5 = (close[i + 5] / close[i] - 1) * 100 if i + 5 < N else None
        rows.append({"d": dates[i], "i": i, "n20": r20, "n5": r5, "S20": S_of(r20), "S5": S_of(r5), "f20": f20, "f5": f5,
                     "trend": None if ma200[i] is None else (close[i] > ma200[i])})

    periods = (("2006-2021", "2006", "2022"), ("2022-2026", "2022", "2100"), ("tout", "2006", "2100"))

    def sel(lo, hi, key):
        return [r for r in rows if lo <= r["d"][:4] < hi and r[key] is not None]

    base = {}
    for per, lo, hi in periods:
        base[per] = {"20j": stats([r["f20"] for r in sel(lo, hi, "f20")]), "5j": stats([r["f5"] for r in sel(lo, hi, "f5")])}
    res["base"] = base

    pil = {}
    for k in ("taux_reels", "fed", "dollar", "risque", "position"):
        pil[k] = {}
        for per, lo, hi in periods:
            pr = {}
            for horizon, nk, fk in (("20j", "n20", "f20"), ("5j", "n5", "f5")):
                rr = [r for r in sel(lo, hi, fk) if r[nk][k] is not None]
                byn = {}
                for nt in (-2, -1, 0, 1, 2):
                    byn[str(nt)] = stats([r[fk] for r in rr if r[nk][k] == nt])
                c = corr([r[nk][k] for r in rr], [r[fk] for r in rr])
                pr[horizon] = {"par_note": byn, "correlation_note_rendement": None if c is None else round(c, 3)}
            pil[k][per] = pr
    res["piliers"] = pil

    def zone(s, up=0.5):
        if s is None:
            return None
        return "haussier" if s >= up else "baissier" if s <= -up else "neutre"

    sc = {}
    for per, lo, hi in periods:
        sc[per] = {}
        for horizon, sk, fk in (("20j", "S20", "f20"), ("5j", "S5", "f5")):
            rr = [r for r in sel(lo, hi, fk) if r[sk] is not None]
            out = {}
            for th in (0.25, 0.5):
                z = {}
                for zn in ("haussier", "neutre", "baissier"):
                    z[zn] = stats([r[fk] for r in rr if zone(r[sk], th) == zn])
                    z[zn]["part_du_temps_pct"] = round(len([1 for r in rr if zone(r[sk], th) == zn]) / max(1, len(rr)) * 100, 1)
                out["seuil_%s" % th] = z
            c = corr([r[sk] for r in rr], [r[fk] for r in rr])
            out["correlation_S_rendement"] = None if c is None else round(c, 3)
            # Filtre de tendance : biais haussier seulement si le prix est au-dessus de la moyenne 200 jours
            out["haussier_et_tendance"] = stats([r[fk] for r in rr if zone(r[sk], 0.25) == "haussier" and r["trend"]])
            out["baissier_et_tendance"] = stats([r[fk] for r in rr if zone(r[sk], 0.25) == "baissier" and r["trend"] is False])
            out["tendance_seule_haut"] = stats([r[fk] for r in rr if r["trend"]])
            out["tendance_seule_bas"] = stats([r[fk] for r in rr if r["trend"] is False])
            out["S_max_observe"] = round(max(r[sk] for r in rr), 2) if rr else None
            out["S_min_observe"] = round(min(r[sk] for r in rr), 2) if rr else None
            sc[per][horizon] = out
    res["score"] = sc

    # --- 3. Lien or / taux réels, année par année (variations hebdomadaires)
    yc = {}
    if "dfii10" in A:
        for y in range(2006, int(dates[-1][:4]) + 1):
            idx = [i for i in range(5, N, 5) if dates[i][:4] == str(y)]
            a, b = [], []
            for i in idx:
                dr = chg("dfii10", i, 5)
                if dr is None:
                    continue
                a.append((close[i] / close[i - 5] - 1) * 100)
                b.append(dr)
            c = corr(a, b)
            ylev = val("dfii10", idx[-1]) if idx else None
            yc[str(y)] = {"corr": None if c is None else round(c, 2), "n": len(a), "tips_fin": ylev, "or_fin": round(close[idx[-1]], 1) if idx else None}
    res["or_taux_reels_par_annee"] = yc

    # --- 4. ATR (taille des stops)
    tr = []
    for i in range(1, N):
        h, l, pc = G[i][1], G[i][2], G[i - 1][3]
        tr.append((dates[i], max(h - l, abs(h - pc), abs(l - pc))))
    atr = []
    for i in range(13, len(tr)):
        atr.append((tr[i][0], sum(x for _, x in tr[i - 13:i + 1]) / 14))
    last_year = [a for d, a in atr if d >= (datetime.strptime(dates[-1], "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")]
    res["atr"] = {"jour_actuel": round(atr[-1][1], 1), "jour_date": atr[-1][0], "jour_median_1an": round(sorted(last_year)[len(last_year) // 2], 1),
                  "jour_en_pct_du_prix": round(atr[-1][1] / close[-1] * 100, 2), "prix": round(close[-1], 1)}
    if "gold_h1" in S and len(S["gold_h1"]) > 200:
        b4 = {}
        for t, h, l, c in S["gold_h1"]:
            k = t // 14400
            if k not in b4:
                b4[k] = [h, l, c]
            else:
                b4[k][0] = max(b4[k][0], h)
                b4[k][1] = min(b4[k][1], l)
                b4[k][2] = c
        ks = sorted(b4)
        tr4 = [max(b4[ks[j]][0] - b4[ks[j]][1], abs(b4[ks[j]][0] - b4[ks[j - 1]][2]), abs(b4[ks[j]][1] - b4[ks[j - 1]][2])) for j in range(1, len(ks))]
        a4 = [sum(tr4[j - 13:j + 1]) / 14 for j in range(13, len(tr4))]
        h1 = S["gold_h1"]
        tr1 = [max(h1[j][1] - h1[j][2], abs(h1[j][1] - h1[j - 1][3]), abs(h1[j][2] - h1[j - 1][3])) for j in range(1, len(h1))]
        a1 = [sum(tr1[j - 13:j + 1]) / 14 for j in range(13, len(tr1))]
        res["atr"].update({"h4_actuel": round(a4[-1], 1), "h4_median": round(sorted(a4[-1500:])[len(a4[-1500:]) // 2], 1),
                           "h1_actuel": round(a1[-1], 1), "h1_median": round(sorted(a1[-3000:])[len(a1[-3000:]) // 2], 1),
                           "h1_date": datetime.fromtimestamp(h1[-1][0], timezone.utc).strftime("%Y-%m-%d %H:%M")})

    # --- 5. Application à la dernière date disponible
    i = N - 1
    r20, r5 = notes(i, 20), notes(i, 5)
    cur = {"date": dates[i], "or": round(close[i], 1), "notes_20j": r20, "S20": None if S_of(r20) is None else round(S_of(r20), 3),
           "notes_5j": r5, "S5": None if S_of(r5) is None else round(S_of(r5), 3),
           "mesures": {"tips10": val("dfii10", i), "tips10_var20j_pb": None if chg("dfii10", i, 20) is None else round(chg("dfii10", i, 20) * 100),
                       "tips10_var5j_pb": None if chg("dfii10", i, 5) is None else round(chg("dfii10", i, 5) * 100),
                       "us2a": val("dgs2", i), "us2a_var20j_pb": None if chg("dgs2", i, 20) is None else round(chg("dgs2", i, 20) * 100),
                       "us2a_var5j_pb": None if chg("dgs2", i, 5) is None else round(chg("dgs2", i, 5) * 100),
                       "dxy": val("dxy", i), "dxy_var20j_pct": None if not val("dxy", i - 20) else round((val("dxy", i) / val("dxy", i - 20) - 1) * 100, 2),
                       "vix": val("vix", i), "vix_var20j": None if chg("vix", i, 20) is None else round(chg("vix", i, 20), 2),
                       "cot_net_pct_oi": None if val("cot", i) is None else round(val("cot", i), 1),
                       "cot_percentile_3ans": None, "ma200": None if ma200[i] is None else round(ma200[i], 1),
                       "t10y2y": val("t10y2y", i), "nfci": val("nfci", i), "sahm": val("sahm", i), "hy": val("hy", i), "icsa": val("icsa", i)}}
    if "cot" in A:
        hist = [x for d, x in cot_list if d <= dates[i]][-157:-1]
        cur["mesures"]["cot_percentile_3ans"] = round(rank_pct(hist, val("cot", i)), 1)
    if "pcecore" in S:
        pc = S["pcecore"]
        yoy = [(pc[j][0], (pc[j][1] / pc[j - 12][1] - 1) * 100) for j in range(12, len(pc))]
        cur["mesures"]["core_pce_aa"] = round(yoy[-1][1], 2)
        cur["mesures"]["core_pce_mois"] = yoy[-1][0]
        cur["mesures"]["core_pce_aa_6m_avant"] = round(yoy[-7][1], 2)
    if "icsa" in S:
        ic = S["icsa"]
        m4 = [(ic[j][0], sum(v for _, v in ic[j - 3:j + 1]) / 4) for j in range(3, len(ic))]
        low12 = min(v for d, v in m4[-53:])
        cur["mesures"]["claims_m4"] = round(m4[-1][1])
        cur["mesures"]["claims_vs_plus_bas_12m_pct"] = round((m4[-1][1] / low12 - 1) * 100, 1)
    res["aujourdhui"] = cur
    res["log"] = LOG[-40:]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    log("écrit", OUT)


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # noqa : on publie l'erreur pour pouvoir la lire depuis le dépôt
        import traceback
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"erreur": repr(e), "trace": traceback.format_exc()[-3000:], "log": LOG[-60:],
                       "genere": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}, f, ensure_ascii=False, indent=1)
        print(traceback.format_exc())
