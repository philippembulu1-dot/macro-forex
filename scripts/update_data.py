#!/usr/bin/env python3
"""Robot de mise à jour : récupère des données publiques gratuites et écrit data/macro.json
(et data/seasonal.json pour la saisonnalité 20 ans, recalculée une fois par semaine).

Règles :
- chaque source est isolée : une panne n'empêche pas les autres, l'échec est noté dans le fichier ;
- aucune clé API, bibliothèque standard Python uniquement ;
- aucune valeur inventée : une source en échec reste vide, le site affiche « échec ».

Test hors ligne : MF_MOCK_DIR=<dossier> lit des fichiers au lieu d'Internet.
Diagnostic : MF_ONLY=fred,cot,... ne lance que certaines sources."""
import csv, io, json, os, re, sys, time, statistics, threading, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta, date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.environ.get("MF_OUT", os.path.join(ROOT, "data", "macro.json"))
OUT_SEAS = os.environ.get("MF_OUT_SEAS", os.path.join(ROOT, "data", "seasonal.json"))
MOCK = os.environ.get("MF_MOCK_DIR")
ONLY = set(x for x in os.environ.get("MF_ONLY", "").split(",") if x)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
SOURCES = []
LOCK = threading.Lock()
T0 = time.time()


def want(group):
    return not ONLY or group in ONLY


def http(key, url, tries=2, headers=None, timeout=20):
    if MOCK:
        p = os.path.join(MOCK, key)
        if not os.path.exists(p):
            raise IOError("mock absent: " + key)
        with open(p, encoding="utf-8") as f:
            return f.read()
    last = None
    for i in range(tries):
        try:
            hd = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.8"}
            hd.update(headers or {})
            req = urllib.request.Request(url, headers=hd)
            # Durée maximale par téléchargement : un serveur qui répond au compte-gouttes est coupé.
            end = time.time() + timeout * 3
            buf = bytearray()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if time.time() > end:
                        raise TimeoutError("réponse trop lente (> %d s)" % (timeout * 3))
            return bytes(buf).decode("utf-8-sig", "replace")
        except urllib.error.HTTPError as e:  # 4xx : inutile d'insister (sauf 429)
            last = e
            if e.code in (400, 401, 403, 404, 410, 418):
                break
            time.sleep(2)
        except Exception as e:  # noqa
            last = e
            time.sleep(2)
    raise last


def fr_err(e):
    """Messages d'erreur en français simple."""
    t = str(e)
    m = re.search(r"HTTP Error (\d+)", t)
    if m:
        c = int(m.group(1))
        return {403: "accès refusé par le site (403)", 404: "adresse introuvable (404)", 418: "le site refuse les robots (418)",
                429: "trop de demandes, le site limite (429)", 500: "erreur du site (500)", 502: "site indisponible (502)",
                503: "site indisponible (503)"}.get(c, "erreur du site (%d)" % c)
    if "timed out" in t or "TimeoutError" in t or "trop lente" in t:
        return "le site n'a pas répondu à temps" + (" (" + t + ")" if "trop lente" in t else "")
    if "is not in list" in t:
        return "format du fichier changé (" + t[:60] + ")"
    if "Name or service not known" in t or "getaddrinfo" in t:
        return "adresse du site introuvable"
    if "Connection reset" in t or "Connection refused" in t:
        return "connexion refusée par le site"
    if "Expecting value" in t or "JSONDecodeError" in t:
        return "réponse illisible (pas du JSON)"
    return t


def note(sid, label, kind, ok, asof=None, err=None, url=None):
    with LOCK:
        SOURCES.append({"id": sid, "label": label, "kind": kind, "ok": bool(ok), "asof": asof,
                        "err": (fr_err(err)[:160] if err else None), "url": url})
        # affichage immédiat : on suit la progression dans l'onglet Actions
        print("[%4.0fs] %-5s %-26s %s" % (time.time() - T0, "ok" if ok else "ÉCHEC", sid, asof if ok else fr_err(err)[:110]), flush=True)


def num(s):
    try:
        v = float(str(s).replace(",", "").strip())
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def parse_date(s):
    if not s:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        return m.group(1) if m else None


# ---------------------------------------------------------------- FRED (CSV public, sans clé)
FRED_DAILY = [
    ("DGS10", "Rendement US 10 ans", "daily"), ("DGS2", "Rendement US 2 ans", "daily"),
    ("T10Y2Y", "Écart 10 ans − 2 ans", "daily"), ("DFII10", "Rendement réel 10 ans (TIPS)", "daily"),
    ("T10YIE", "Inflation anticipée 10 ans (breakeven)", "daily"), ("T5YIFR", "Inflation anticipée 5 ans dans 5 ans", "daily"),
    ("VIXCLS", "VIX", "daily"), ("GVZCLS", "Volatilité implicite de l'or (GVZ)", "daily"),
    ("DFEDTARU", "Fed : borne haute", "daily"), ("DFEDTARL", "Fed : borne basse", "daily"),
    ("ECBDFR", "BCE : taux de dépôt", "daily"), ("DTWEXBGS", "Dollar large (indice Fed)", "daily"),
    ("BAMLH0A0HYM2", "Spread de crédit haut rendement (US)", "daily"), ("BAMLC0A0CM", "Spread de crédit qualité (US)", "daily"),
    ("NFCI", "Conditions financières (Fed Chicago)", "weekly"), ("STLFSI4", "Stress financier (Fed St. Louis)", "weekly"),
    ("WALCL", "Bilan de la Fed", "weekly"),
]
FRED_MONTHLY = [("CPIAUCSL", "IPC US"), ("CPILFESL", "IPC US sous-jacent"), ("PCEPILFE", "PCE sous-jacent US"),
                ("UNRATE", "Chômage US"), ("PAYEMS", "Emplois US")]
# Séries OCDE relayées par FRED : retard de plusieurs semaines, certaines arrêtées. Gardées en dernier recours.
OECD_RATES = [("GB", "BoE"), ("JP", "BoJ"), ("CH", "BNS"), ("AU", "RBA"), ("CA", "BoC"), ("NZ", "RBNZ")]
OECD_CPI = [("EZ", "zone euro"), ("GB", "Royaume-Uni"), ("JP", "Japon"), ("CH", "Suisse"), ("CA", "Canada"), ("AU", "Australie"), ("NZ", "Nouvelle-Zélande")]


def fred(sid, start):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=%s" % (sid, start)
    txt = http("fred_" + sid + ".csv", url, tries=1, timeout=35)
    rows = list(csv.reader(io.StringIO(txt)))
    if not rows or len(rows[0]) < 2 or not re.match(r"(observation_date|DATE)$", rows[0][0].strip(), re.I):
        raise ValueError("format inattendu: " + (",".join(rows[0])[:60] if rows else "vide"))
    out = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        v = num(r[1])  # FRED met "." quand la valeur manque
        if v is not None:
            out.append((r[0].strip()[:10], v))
    if not out:
        raise ValueError("série vide")
    return out


def year_ago(obs):
    y, m = obs[-1][0][:4], obs[-1][0][5:7]
    tgt = "%d-%s" % (int(y) - 1, m)
    for d, v in obs:
        if d[:7] == tgt:
            return v
    return None


def yoy(o):
    """Glissement annuel du dernier point et du précédent (séries d'indice mensuelles)."""
    if not o or len(o) < 14:
        return None
    ya, ya2 = year_ago(o), year_ago(o[:-1])
    if not ya:
        return None
    return {"v": round((o[-1][1] / ya - 1) * 100, 2), "prev": round((o[-2][1] / ya2 - 1) * 100, 2) if ya2 else None, "asof": o[-1][0]}


FRED_OK = {"v": None}


def fred_reachable():
    """Un seul essai rapide : si FRED ne répond pas aux serveurs de GitHub, inutile d'attendre 35 séries."""
    if FRED_OK["v"] is None:
        try:
            fred("DGS10", (NOW - timedelta(days=20)).strftime("%Y-%m-%d"))
            FRED_OK["v"] = True
        except Exception as e:
            FRED_OK["v"] = False
            FRED_OK["err"] = "FRED injoignable depuis GitHub (%s)" % str(e)[:80]
    return FRED_OK["v"]


def do_fred(data):
    if not fred_reachable():
        for sid, label, kind in FRED_DAILY:
            note("fred." + sid, label, kind, False, err=FRED_OK["err"], url="https://fred.stlouisfed.org/series/" + sid)
        for sid, label in FRED_MONTHLY:
            note("fred." + sid, label, "monthly", False, err=FRED_OK["err"], url="https://fred.stlouisfed.org/series/" + sid)
        fallbacks(data)
        return
    start_d = (NOW - timedelta(days=400)).strftime("%Y-%m-%d")
    start_m = (NOW - timedelta(days=900)).strftime("%Y-%m-%d")
    def one_d(item):
        sid, label, kind = item
        try:
            o = fred(sid, start_d)
            data["fred"][sid] = {"v": o[-1][1], "prev": o[-2][1] if len(o) > 1 else None, "asof": o[-1][0],
                                 "hist": [[d, v] for d, v in o[-260:]]}
            note("fred." + sid, label, kind, True, o[-1][0], url="https://fred.stlouisfed.org/series/" + sid)
        except Exception as e:
            note("fred." + sid, label, kind, False, err=e, url="https://fred.stlouisfed.org/series/" + sid)

    raw = {}

    def one_m(item):
        sid, label = item
        try:
            raw[sid] = fred(sid, start_m)
            note("fred." + sid, label, "monthly", True, raw[sid][-1][0], url="https://fred.stlouisfed.org/series/" + sid)
        except Exception as e:
            note("fred." + sid, label, "monthly", False, err=e, url="https://fred.stlouisfed.org/series/" + sid)

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one_d, FRED_DAILY))
        list(ex.map(one_m, FRED_MONTHLY))
    fallbacks(data)
    d = data["derived"]
    for sid, key in (("CPIAUCSL", "us_cpi"), ("CPILFESL", "us_core"), ("PCEPILFE", "us_pce_core")):
        y = yoy(raw.get(sid))
        if y:
            d[key] = y
    if raw.get("UNRATE") and len(raw["UNRATE"]) > 1:
        o = raw["UNRATE"]
        d["us_unemp"] = {"v": o[-1][1], "prev": o[-2][1], "asof": o[-1][0]}
    if raw.get("PAYEMS") and len(raw["PAYEMS"]) > 2:
        o = raw["PAYEMS"]
        d["us_nfp"] = {"v": round(o[-1][1] - o[-2][1]), "prev": round(o[-2][1] - o[-3][1]), "asof": o[-1][0]}


# ---------------------------------------------------------------- Secours officiels quand FRED ne répond pas
def treasury(kind, cols):
    """Trésor américain : CSV quotidien sans clé. kind = daily_treasury_yield_curve ou daily_treasury_real_yield_curve."""
    obs = {}
    for yr in (NOW.year - 1, NOW.year):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/%d/all"
               "?type=%s&field_tdr_date_value=%d&page&_format=csv" % (yr, kind, yr))
        rows = list(csv.DictReader(io.StringIO(http("ust_%s_%d.csv" % (kind, yr), url, timeout=30))))
        for r in rows:
            d = datetime.strptime(r["Date"].strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
            for c in cols:
                key = next((k for k in r if k and k.strip().upper() == c.upper()), None)
                v = num(r.get(key)) if key else None
                if v is not None:
                    obs.setdefault(c, {})[d] = v
    return {c: sorted(v.items()) for c, v in obs.items()}


def cboe(sym):
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv" % sym
    out = []
    for r in csv.DictReader(io.StringIO(http("cboe_%s.csv" % sym, url, timeout=30))):
        v = num(r.get("CLOSE") or r.get(sym))
        if v is not None:
            out.append((datetime.strptime(r["DATE"].strip(), "%m/%d/%Y").strftime("%Y-%m-%d"), v))
    return out


def put(data, sid, obs, via):
    if not obs:
        raise ValueError("série vide")
    obs = sorted(obs)[-260:]
    data["fred"][sid] = {"v": obs[-1][1], "prev": obs[-2][1] if len(obs) > 1 else None, "asof": obs[-1][0],
                         "hist": [[d, v] for d, v in obs], "via": via}


NOMINAL, REAL = {}, {}


def fallbacks(data):
    F = data["fred"]
    need = lambda *ids: [i for i in ids if i not in F]
    if need("DGS10", "DGS2", "T10Y2Y"):
        try:
            t = treasury("daily_treasury_yield_curve", ["2 Yr", "5 Yr", "10 Yr"])
            NOMINAL.update(t)
            ten, two = t.get("10 Yr", []), t.get("2 Yr", [])
            if "DGS10" not in F:
                put(data, "DGS10", ten, "Trésor US")
            if "DGS2" not in F:
                put(data, "DGS2", two, "Trésor US")
            if "T10Y2Y" not in F:
                d2 = dict(two)
                put(data, "T10Y2Y", [(d, round(v - d2[d], 2)) for d, v in ten if d in d2], "Trésor US (10 ans − 2 ans)")
            note("ust.nominal", "Taux US 2 et 10 ans (Trésor américain, secours)", "daily", True, F["DGS10"]["asof"],
                 url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
        except Exception as e:
            note("ust.nominal", "Taux US 2 et 10 ans (Trésor américain, secours)", "daily", False, err=e,
                 url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
    if need("DFII10"):
        try:
            rt = treasury("daily_treasury_real_yield_curve", ["5 YR", "10 YR"])
            REAL.update(rt)
            put(data, "DFII10", rt.get("10 YR", []), "Trésor US")
            note("ust.real", "Taux réel US 10 ans (Trésor américain, secours)", "daily", True, F["DFII10"]["asof"],
                 url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
        except Exception as e:
            note("ust.real", "Taux réel US 10 ans (Trésor américain, secours)", "daily", False, err=e,
                 url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
    for sym, sid in (("VIX", "VIXCLS"), ("GVZ", "GVZCLS")):
        if need(sid):
            try:
                put(data, sid, cboe(sym), "CBOE")
                note("cboe." + sym, sym + " (CBOE, secours)", "daily", True, F[sid]["asof"], url="https://www.cboe.com/tradable_products/vix/")
            except Exception as e:
                note("cboe." + sym, sym + " (CBOE, secours)", "daily", False, err=e, url="https://www.cboe.com/tradable_products/vix/")
    if need("DFEDTARU", "DFEDTARL"):
        try:
            j = json.loads(http("nyfed_effr.json", "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/30.json", timeout=30))
            rows = sorted(j.get("refRates") or [], key=lambda r: r["effectiveDate"])
            put(data, "DFEDTARU", [(r["effectiveDate"], num(r.get("targetRateTo"))) for r in rows if num(r.get("targetRateTo")) is not None], "Fed de New York")
            put(data, "DFEDTARL", [(r["effectiveDate"], num(r.get("targetRateFrom"))) for r in rows if num(r.get("targetRateFrom")) is not None], "Fed de New York")
            note("nyfed.target", "Fourchette cible de la Fed (Fed de New York, secours)", "daily", True, F["DFEDTARU"]["asof"],
                 url="https://www.newyorkfed.org/markets/reference-rates/effr")
        except Exception as e:
            note("nyfed.target", "Fourchette cible de la Fed (Fed de New York, secours)", "daily", False, err=e,
                 url="https://www.newyorkfed.org/markets/reference-rates/effr")
    # Inflation anticipée = taux nominal − taux réel (c'est la définition utilisée par FRED pour T10YIE)
    if need("T10YIE", "T5YIFR") and NOMINAL.get("10 Yr") and REAL.get("10 YR"):
        try:
            n10, r10 = dict(NOMINAL["10 Yr"]), dict(REAL["10 YR"])
            n5, r5 = dict(NOMINAL.get("5 Yr", [])), dict(REAL.get("5 YR", []))
            be10 = [(d, round(n10[d] - r10[d], 2)) for d in sorted(r10) if d in n10]
            if "T10YIE" not in F:
                put(data, "T10YIE", be10, "calcul maison : 10 ans nominal − réel (Trésor US)")
            if "T5YIFR" not in F and n5 and r5:
                b10 = dict(be10)
                put(data, "T5YIFR", [(d, round(2 * b10[d] - (n5[d] - r5[d]), 2)) for d in sorted(r5) if d in n5 and d in b10],
                    "calcul maison : 2 × inflation 10 ans − inflation 5 ans (Trésor US)")
            note("calc.breakeven", "Inflation anticipée 10 ans et 5 ans dans 5 ans (calcul maison, Trésor US)", "daily", True, F["T10YIE"]["asof"],
                 url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
        except Exception as e:
            note("calc.breakeven", "Inflation anticipée (calcul maison, Trésor US)", "daily", False, err=e)
    # Indice du dollar (formule officielle du DXY) recalculé avec les taux de référence BCE
    if need("DTWEXBGS") and "DXY" not in F:
        try:
            url = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY+GBP+CAD+SEK+CHF.EUR.SP00.A?format=csvdata&lastNObservations=270"
            by = {}
            for r in csv.DictReader(io.StringIO(http("ecb_exr.csv", url, timeout=40))):
                v = num(r.get("OBS_VALUE"))
                if v:
                    by.setdefault(r["TIME_PERIOD"], {})[r["CURRENCY"]] = v
            obs = []
            for d, x in sorted(by.items()):
                if len(x) == 6:
                    u = x["USD"]
                    obs.append((d, round(50.14348112 * u ** -0.576 * (x["JPY"] / u) ** 0.136 * (u / x["GBP"]) ** -0.119 * (x["CAD"] / u) ** 0.091
                                         * (x["SEK"] / u) ** 0.042 * (x["CHF"] / u) ** 0.036, 3)))
            put(data, "DXY", obs, "calcul maison : formule DXY avec les taux BCE")
            note("calc.dxy", "Indice du dollar DXY (recalculé avec les taux BCE)", "daily", True, F["DXY"]["asof"], url="https://data.ecb.europa.eu/")
        except Exception as e:
            note("calc.dxy", "Indice du dollar DXY (recalculé avec les taux BCE)", "daily", False, err=e, url="https://data.ecb.europa.eu/")
    # Conditions financières : fichier de la Fed de Chicago
    if need("NFCI"):
        try:
            rows = list(csv.reader(io.StringIO(http("nfci.csv", "https://www.chicagofed.org/-/media/publications/nfci/nfci-data-series-csv.csv", timeout=40))))
            head = [c.strip().upper() for c in rows[0]]
            ci = head.index("NFCI")
            obs = []
            for r in rows[1:]:
                if len(r) > ci and num(r[ci]) is not None:
                    d = r[0].strip()
                    for f_ in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
                        try:
                            obs.append((datetime.strptime(d, f_).strftime("%Y-%m-%d"), num(r[ci])))
                            break
                        except ValueError:
                            pass
            put(data, "NFCI", obs, "Fed de Chicago")
            note("chifed.nfci", "Conditions financières NFCI (Fed de Chicago)", "weekly", True, F["NFCI"]["asof"], url="https://www.chicagofed.org/nfci")
        except Exception as e:
            note("chifed.nfci", "Conditions financières NFCI (Fed de Chicago)", "weekly", False, err=e, url="https://www.chicagofed.org/nfci")
    # Inflation, chômage, emplois US : Bureau des statistiques du travail (API v1, sans clé)
    if not data["derived"].get("us_cpi"):
        try:
            body = json.dumps({"seriesid": ["CUSR0000SA0", "CUSR0000SA0L1E", "LNS14000000", "CES0000000001"]}).encode()
            if MOCK:
                txt = http("bls.json", "")
            else:
                req = urllib.request.Request("https://api.bls.gov/publicAPI/v1/timeseries/data/", data=body,
                                             headers={"Content-Type": "application/json", "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=40) as r:
                    txt = r.read().decode("utf-8")
            j = json.loads(txt)
            if j.get("status") != "REQUEST_SUCCEEDED":
                raise ValueError("BLS : " + " ".join(j.get("message") or [])[:100])
            ser = {}
            for x in j["Results"]["series"]:
                ser[x["seriesID"]] = sorted(("%s-%s-01" % (o["year"], o["period"][1:]), num(o["value"])) for o in x["data"]
                                            if o["period"].startswith("M") and o["period"] != "M13" and num(o["value"]) is not None)
            D = data["derived"]
            for sid, key in (("CUSR0000SA0", "us_cpi"), ("CUSR0000SA0L1E", "us_core")):
                y = yoy(ser.get(sid))
                if y:
                    D[key] = y
            u = ser.get("LNS14000000")
            if u and len(u) > 1:
                D["us_unemp"] = {"v": u[-1][1], "prev": u[-2][1], "asof": u[-1][0]}
            n = ser.get("CES0000000001")
            if n and len(n) > 2:
                D["us_nfp"] = {"v": round(n[-1][1] - n[-2][1]), "prev": round(n[-2][1] - n[-3][1]), "asof": n[-1][0]}
            if not D.get("us_cpi"):
                raise ValueError("série IPC vide")
            note("bls.us", "Inflation, chômage et emplois US (BLS)", "monthly", True, D["us_cpi"]["asof"], url="https://www.bls.gov/")
        except Exception as e:
            note("bls.us", "Inflation, chômage et emplois US (BLS)", "monthly", False, err=e, url="https://www.bls.gov/")
    if need("ECBDFR"):
        try:
            url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata&lastNObservations=30"
            rows = list(csv.DictReader(io.StringIO(http("ecb_dfr.csv", url, timeout=30))))
            put(data, "ECBDFR", [(r["TIME_PERIOD"], num(r["OBS_VALUE"])) for r in rows if num(r["OBS_VALUE"]) is not None], "BCE")
            note("ecb.dfr", "Taux de dépôt BCE (BCE, secours)", "daily", True, F["ECBDFR"]["asof"], url="https://data.ecb.europa.eu/")
        except Exception as e:
            note("ecb.dfr", "Taux de dépôt BCE (BCE, secours)", "daily", False, err=e, url="https://data.ecb.europa.eu/")
    # Une série FRED remplacée par une source de secours n'est plus affichée comme une panne
    with LOCK:
        SOURCES[:] = [x for x in SOURCES if not (x["id"].startswith("fred.") and not x["ok"] and x["id"][5:] in F)]
        if "DXY" in F:
            SOURCES[:] = [x for x in SOURCES if not (x["id"] == "fred.DTWEXBGS" and not x["ok"])]
        if data["derived"].get("us_cpi"):
            SOURCES[:] = [x for x in SOURCES if not (x["id"] in ("fred.CPIAUCSL", "fred.CPILFESL", "fred.UNRATE", "fred.PAYEMS") and not x["ok"])]


def do_oecd(data):
    if not fred_reachable():
        note("oecd.fred", "Séries OCDE via FRED (taux et inflation des autres pays)", "monthly", False, err=FRED_OK["err"])
        return
    start_m = (NOW - timedelta(days=900)).strftime("%Y-%m-%d")
    for cc, name in OECD_RATES:
        sid = "IRSTCB01%sM156N" % cc
        try:
            o = fred(sid, start_m)
            data["oecd"]["rate_" + cc] = {"v": o[-1][1], "prev": o[-2][1] if len(o) > 1 else None, "asof": o[-1][0]}
            note("oecd.rate." + cc, "Taux directeur " + name + " (OCDE via FRED, retard)", "monthly", True, o[-1][0])
        except Exception as e:
            note("oecd.rate." + cc, "Taux directeur " + name + " (OCDE via FRED, retard)", "monthly", False, err=e)
    for cc, name in OECD_CPI:
        got, err = False, None
        for per in ("M", "Q"):
            sid = "CPALTT01%s%s659N" % (cc, per)
            try:
                o = fred(sid, start_m)
                data["oecd"]["cpi_" + cc] = {"v": o[-1][1], "prev": o[-2][1] if len(o) > 1 else None, "asof": o[-1][0]}
                note("oecd.cpi." + cc, "Inflation " + name + " (OCDE via FRED, retard)", "monthly" if per == "M" else "quarterly", True, o[-1][0])
                got = True
                break
            except Exception as e:
                err = e
        if not got:
            note("oecd.cpi." + cc, "Inflation " + name + " (OCDE via FRED, retard)", "monthly", False, err=err)


# ---------------------------------------------------------------- Sources nationales officielles
def nat(data, key, label, kind, url, fn):
    """Enregistre data['national'][key] = {v, prev, asof} à partir d'une fonction d'analyse."""
    try:
        res = fn()
        if not res or res.get("v") is None:
            raise ValueError("aucune valeur")
        res["src"] = url
        data["national"][key] = res
        note("nat." + key, label, kind, True, res["asof"], url=url)
    except Exception as e:
        note("nat." + key, label, kind, False, err=e, url=url)


def last2(obs):
    obs = [o for o in obs if o[1] is not None]
    obs.sort()
    if not obs:
        return None
    return {"v": obs[-1][1], "prev": obs[-2][1] if len(obs) > 1 else None, "asof": obs[-1][0]}


def boe_rate():
    url = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2024"
           "&Dateto=now&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
    txt = http("boe_rate.csv", url, headers={"Referer": "https://www.bankofengland.co.uk/"})
    obs = []
    for r in csv.reader(io.StringIO(txt)):
        if len(r) >= 2 and re.match(r"\d{2} \w{3} \d{4}", r[0].strip()):
            obs.append((datetime.strptime(r[0].strip(), "%d %b %Y").strftime("%Y-%m-%d"), num(r[1])))
    return last2(obs)


def valet(series, key):
    url = "https://www.bankofcanada.ca/valet/observations/%s/json?recent=40" % series
    j = json.loads(http(key, url))
    obs = [(o["d"], num(o.get(series, {}).get("v"))) for o in j.get("observations", [])]
    return last2(obs)


def boc_cpi():
    url = "https://www.bankofcanada.ca/valet/observations/group/CPI_MONTHLY/json?recent=3"
    j = json.loads(http("boc_cpi.json", url))
    obs = []
    for o in j.get("observations", []):
        v = o.get("STATIC_TOTALCPICHANGE") or o.get("CPI_TOTAL_CHANGE") or {}
        obs.append((o["d"], num(v.get("v"))))
    return last2(obs)


def rba_rate():
    url = "https://www.rba.gov.au/statistics/tables/csv/f1.1-data.csv"
    rows = list(csv.reader(io.StringIO(http("rba_f11.csv", url))))
    col, ids, titles = None, [], []
    for r in rows[:20]:
        if r and r[0].strip().lower() == "series id":
            ids = [c.strip().upper() for c in r]
        if r and r[0].strip().lower() == "title":
            titles = [c.strip().lower() for c in r]
    for i, c in enumerate(ids):
        if c.startswith("FIRMMCRT"):
            col = i
            break
    if col is None:
        col = next((i for i, t in enumerate(titles) if "cash rate target" in t), None)
    if col is None:
        raise ValueError("colonne du taux cible absente (identifiants : %s)" % ",".join(ids[1:6]))
    obs = []
    for r in rows:
        if r and re.match(r"\d{2}-\w{3}-\d{4}", r[0].strip()) and len(r) > col:
            obs.append((datetime.strptime(r[0].strip(), "%d-%b-%Y").strftime("%Y-%m-%d"), num(r[col])))
    return last2(obs)


def eurostat_hicp():
    """La zone euro a changé de code avec l'entrée de la Bulgarie (2026) : on essaie plusieurs codes, on garde le plus récent."""
    best = None
    for geo in ("EA21", "EA", "EA20"):
        try:
            url = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr"
                   "?format=JSON&lang=EN&geo=%s&coicop=CP00&unit=RCH_A&lastTimePeriod=3" % geo)
            j = json.loads(http("eurostat_hicp_%s.json" % geo, url, timeout=30, tries=1))
            idx = j["dimension"]["time"]["category"]["index"]
            r = last2([(per[:7] + "-01", num(j["value"].get(str(pos)))) for per, pos in idx.items()])
            if r and (not best or r["asof"] > best["asof"]):
                best = r
        except Exception:
            pass
    if not best:
        raise ValueError("Eurostat : aucun code de zone euro n'a répondu")
    return best


def ez_cpi():
    best, err = None, None
    for fn in (eurostat_hicp, ecb_hicp):
        try:
            r = fn()
            if r and (not best or r["asof"] > best["asof"]):
                best = r
        except Exception as e:
            err = e
    if not best:
        raise err or ValueError("aucune valeur")
    return best


def ecb_hicp():
    url = "https://data-api.ecb.europa.eu/service/data/ICP/M.U2.N.000000.4.ANR?format=csvdata&lastNObservations=3"
    rows = list(csv.DictReader(io.StringIO(http("ecb_hicp.csv", url))))
    return last2([(r["TIME_PERIOD"] + "-01", num(r["OBS_VALUE"])) for r in rows])


def ons_cpi():
    url = "https://www.ons.gov.uk/generator?format=csv&uri=/economy/inflationandpriceindices/timeseries/d7g7/mm23"
    obs = []
    for r in csv.reader(io.StringIO(http("ons_cpi.csv", url))):
        if len(r) >= 2 and re.match(r"\d{4} [A-Z]{3}$", r[0].strip()):
            obs.append((datetime.strptime(r[0].strip().title(), "%Y %b").strftime("%Y-%m-01"), num(r[1])))
    return last2(obs)


def snb_rate():
    url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
    obs = []
    for r in csv.reader(io.StringIO(http("snb_rate.csv", url)), delimiter=";"):
        if len(r) >= 3 and re.match(r"\d{4}-\d{2}", r[0]) and r[1].strip() == "LZ":
            obs.append((r[0][:7] + "-01", num(r[2])))
    return last2(obs)


def do_national(data):
    nat(data, "rate_GB", "Taux directeur BoE (Banque d'Angleterre)", "daily", "https://www.bankofengland.co.uk/boeapps/database/", boe_rate)
    nat(data, "rate_CA", "Taux directeur BoC (Banque du Canada)", "daily", "https://www.bankofcanada.ca/valet/", lambda: valet("V39079", "boc_rate.json"))
    nat(data, "rate_AU", "Taux directeur RBA (Banque de réserve d'Australie)", "daily", "https://www.rba.gov.au/statistics/tables/", rba_rate)
    nat(data, "rate_CH", "Taux directeur BNS (Banque nationale suisse)", "monthly", "https://data.snb.ch/", snb_rate)
    nat(data, "cpi_EZ", "Inflation zone euro (Eurostat / BCE, IPCH)", "monthly", "https://ec.europa.eu/eurostat/web/hicp", ez_cpi)
    nat(data, "cpi_GB", "Inflation Royaume-Uni (ONS)", "monthly", "https://www.ons.gov.uk/economy/inflationandpriceindices", ons_cpi)
    nat(data, "cpi_CA", "Inflation Canada (Banque du Canada)", "monthly", "https://www.bankofcanada.ca/rates/price-indexes/cpi/", boc_cpi)


# ---------------------------------------------------------------- COT (CFTC, « Legacy », futures seuls)
COT_CODES = {"088691": "XAU", "099741": "EUR", "096742": "GBP", "097741": "JPY", "092741": "CHF", "232741": "AUD", "090741": "CAD", "112741": "NZD"}


def do_cot(data):
    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    try:
        since = (NOW - timedelta(days=3 * 365 + 10)).strftime("%Y-%m-%dT00:00:00")
        where = "cftc_contract_market_code in(%s) AND report_date_as_yyyy_mm_dd >= '%s'" % (",".join("'%s'" % c for c in COT_CODES), since)
        q = urllib.parse.urlencode({"$where": where, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "2000",
                                    "$select": "cftc_contract_market_code,report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,comm_positions_long_all,comm_positions_short_all,open_interest_all"})
        rows = json.loads(http("cot.json", url + "?" + q))
        if isinstance(rows, dict):
            raise ValueError(rows.get("message", "réponse inattendue"))
        by = {}
        for r in rows:
            k = COT_CODES.get(str(r.get("cftc_contract_market_code", "")).strip())
            if k:
                by.setdefault(k, []).append(r)
        newest = None
        I = lambda r, f: int(float(r.get(f) or 0))
        for k, lst in by.items():
            lst.sort(key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""), reverse=True)
            net = lambda r: I(r, "noncomm_positions_long_all") - I(r, "noncomm_positions_short_all")
            cnet = lambda r: I(r, "comm_positions_long_all") - I(r, "comm_positions_short_all")
            nets = [net(r) for r in lst]
            cur = lst[0]
            lo, hi = min(nets), max(nets)
            data["cot"][k] = {"date": cur["report_date_as_yyyy_mm_dd"][:10], "long": I(cur, "noncomm_positions_long_all"),
                              "short": I(cur, "noncomm_positions_short_all"), "net": nets[0], "prev_net": nets[1] if len(nets) > 1 else None,
                              "oi": I(cur, "open_interest_all"), "comm_net": cnet(cur), "comm_prev": cnet(lst[1]) if len(lst) > 1 else None,
                              "hist": nets[:12][::-1], "idx3y": round((nets[0] - lo) / (hi - lo) * 100) if hi > lo else None, "weeks": len(nets)}
            if not newest or data["cot"][k]["date"] > newest:
                newest = data["cot"][k]["date"]
        if not data["cot"]:
            raise ValueError("aucune ligne COT")
        note("cftc.cot", "COT CFTC (positions des fonds)", "weekly", True, newest, url="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm")
    except Exception as e:
        note("cftc.cot", "COT CFTC (positions des fonds)", "weekly", False, err=e, url="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm")


# ---------------------------------------------------------------- Fear & Greed (CNN)
def do_fng(data):
    try:
        txt = http("fng.json", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata", tries=2, headers={
            "Origin": "https://edition.cnn.com", "Referer": "https://edition.cnn.com/", "Accept": "application/json"})
        j = json.loads(txt)
        fg = j["fear_and_greed"]
        asof = parse_date(str(fg.get("timestamp", ""))) or TODAY
        hist = [[datetime.fromtimestamp(p["x"] / 1000, timezone.utc).strftime("%Y-%m-%d"), round(float(p["y"]), 1)]
                for p in (j.get("fear_and_greed_historical", {}).get("data") or [])[-60:]]
        data["fng"] = {"score": round(float(fg["score"]), 1), "rating": fg.get("rating"), "prev_close": fg.get("previous_close"),
                       "week": fg.get("previous_1_week"), "month": fg.get("previous_1_month"), "year": fg.get("previous_1_year"), "asof": asof, "hist": hist}
        note("cnn.fng", "Fear & Greed (CNN)", "daily", True, asof, url="https://edition.cnn.com/markets/fear-and-greed")
    except Exception as e:
        note("cnn.fng", "Fear & Greed (CNN)", "daily", False, err=e, url="https://edition.cnn.com/markets/fear-and-greed")


# ---------------------------------------------------------------- Communiqués officiels (RSS)
FEEDS = [
    ("Fed", "USD", ["https://www.federalreserve.gov/feeds/press_all.xml"]),
    ("BCE", "EUR", ["https://www.ecb.europa.eu/rss/press.html"]),
    ("BoE", "GBP", ["https://www.bankofengland.co.uk/rss/news", "https://www.bankofengland.co.uk/rss/publications"]),
    ("BoJ", "JPY", ["https://www.boj.or.jp/en/rss/whatsnew.xml"]),
    ("BNS", "CHF", ["https://www.snb.ch/public/en/rss/pressrel", "https://www.snb.ch/en/rss/pressrel"]),
    ("RBA", "AUD", ["https://www.rba.gov.au/rss/rss-cb-media-releases.xml"]),
    ("BoC", "CAD", ["https://www.bankofcanada.ca/content_type/press-releases/feed/"]),
    ("RBNZ", "NZD", ["https://www.rbnz.govt.nz/feeds/news", "https://www.rbnz.govt.nz/hub/news/rss"]),
]
ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(txt):
    txt = txt.lstrip()
    if not txt.startswith("<"):
        raise ValueError("pas du XML")
    root = ET.fromstring(txt.encode("utf-8"))
    out = []
    for it in list(root.iter("item")) + list(root.iter(ATOM + "entry")) + list(root.iter("{http://purl.org/rss/1.0/}item")):
        def g(*tags):
            for tag in tags:
                for full in (tag, ATOM + tag, "{http://purl.org/dc/elements/1.1/}" + tag, "{http://purl.org/rss/1.0/}" + tag):
                    e = it.find(full)
                    if e is not None:
                        return e
            return None
        t, l, dt = g("title"), g("link"), g("pubDate", "updated", "published", "date")
        if t is None or not (t.text or "").strip():
            continue
        link = (l.text or "").strip() if (l is not None and (l.text or "").strip()) else (l.get("href") if l is not None else "")
        out.append({"t": re.sub(r"\s+", " ", t.text).strip()[:200], "l": link or "", "d": parse_date((dt.text or "").strip() if dt is not None else "")})
    return out


def do_rss(data):
    items = []

    def one(feed):
        name, cur_, urls = feed
        err, ok = None, False
        for k, url in enumerate(urls):
            try:
                got = parse_feed(http("rss_%s_%d.xml" % (name, k), url, tries=2))[:6]
                if not got:
                    raise ValueError("flux vide")
                for g in got:
                    g.update({"src": name, "c": cur_})
                with LOCK:
                    items.extend(got)
                note("rss." + name, "Communiqués " + name, "news", True, max((g["d"] for g in got if g["d"]), default=None), url=url)
                ok = True
                break
            except Exception as e:
                err = "%s (%s)" % (e, url)
        if not ok:
            note("rss." + name, "Communiqués " + name, "news", False, err=err, url=urls[0])
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(one, FEEDS))
    items.sort(key=lambda i: i["d"] or "", reverse=True)
    data["news"] = items[:48]


# ---------------------------------------------------------------- Calendrier économique (export Forex Factory)
def do_calendar(data):
    evs = []
    for k, (key, url) in enumerate((("this", "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),)):
        try:
            rows = json.loads(http("ff_%s.json" % key, url, tries=2))
            if not isinstance(rows, list):
                raise ValueError("réponse inattendue")
            for r in rows:
                evs.append({"t": (r.get("title") or "")[:90], "c": r.get("country") or "", "d": r.get("date") or "",
                            "i": r.get("impact") or "", "f": r.get("forecast") or "", "p": r.get("previous") or "", "a": r.get("actual") or ""})
            note("cal.ff." + key, "Calendrier économique (" + ("semaine en cours" if key == "this" else "semaine suivante") + ", Forex Factory)",
                 "calendar", True, TODAY, url="https://www.forexfactory.com/calendar")
        except Exception as e:
            note("cal.ff." + key, "Calendrier économique (" + ("semaine en cours" if key == "this" else "semaine suivante") + ", Forex Factory)",
                 "calendar", False, err=e, url="https://www.forexfactory.com/calendar")
    seen, out = set(), []
    for e in sorted(evs, key=lambda e: e["d"]):
        k = (e["d"], e["c"], e["t"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    data["calendar"] = {"fetched": NOW.strftime("%Y-%m-%dT%H:%MZ"), "events": out}


# ---------------------------------------------------------------- Probabilités de taux (marchés de prédiction publics)
def kalshi_price(m, f):
    v = m.get(f + "_dollars")
    if v is not None:
        return num(v)
    v = m.get(f)
    return num(v) / 100 if num(v) is not None else None


def do_markets(data):
    out = {"kalshi": [], "polymarket": []}
    for series in ("KXFEDDECISION", "KXFED"):
        try:
            url = "https://api.elections.kalshi.com/trade-api/v2/markets?" + urllib.parse.urlencode({"series_ticker": series, "status": "open", "limit": "200"})
            j = json.loads(http("kalshi_%s.json" % series, url, tries=2))
            ms = j.get("markets") or []
            if not ms:
                raise ValueError("aucun marché ouvert")
            for m in ms:
                yb, ya, lp = kalshi_price(m, "yes_bid"), kalshi_price(m, "yes_ask"), kalshi_price(m, "last_price")
                mid = (yb + ya) / 2 if (yb is not None and ya is not None and ya > 0) else lp
                out["kalshi"].append({"s": series, "ev": m.get("event_ticker"), "tk": m.get("ticker"), "t": m.get("title"),
                                      "o": m.get("yes_sub_title") or m.get("subtitle") or "", "p": round(mid, 3) if mid is not None else None,
                                      "bid": yb, "ask": ya, "vol": m.get("volume"), "close": (m.get("close_time") or "")[:10]})
            note("pm.kalshi." + series, "Probabilités Fed (Kalshi, " + series + ")", "daily", True, TODAY, url="https://kalshi.com/markets/kxfeddecision")
        except Exception as e:
            note("pm.kalshi." + series, "Probabilités Fed (Kalshi, " + series + ")", "daily", False, err=e, url="https://kalshi.com/markets/kxfeddecision")
    try:
        url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({"closed": "false", "limit": "100", "tag_slug": "fed-rates"})
        evs = json.loads(http("poly.json", url, tries=2))
        n = 0
        for ev in evs if isinstance(evs, list) else []:
            for m in ev.get("markets") or []:
                try:
                    outs, prices = json.loads(m.get("outcomes") or "[]"), json.loads(m.get("outcomePrices") or "[]")
                except ValueError:
                    continue
                if m.get("closed") or not outs:
                    continue
                pyes = num(prices[outs.index("Yes")]) if "Yes" in outs and len(prices) == len(outs) else None
                out["polymarket"].append({"ev": ev.get("title"), "q": m.get("groupItemTitle") or m.get("question"), "p": pyes,
                                          "close": (m.get("endDate") or "")[:10], "vol": num(m.get("volume")), "slug": ev.get("slug")})
                n += 1
        if not n:
            raise ValueError("aucun marché Fed ouvert")
        note("pm.poly", "Probabilités Fed (Polymarket)", "daily", True, TODAY, url="https://polymarket.com/")
    except Exception as e:
        note("pm.poly", "Probabilités Fed (Polymarket)", "daily", False, err=e, url="https://polymarket.com/")
    data["markets"] = out


# ---------------------------------------------------------------- Banque mondiale + FMI (tous les pays)
WB_IND = [("FP.CPI.TOTL.ZG", "inf", "Inflation annuelle (Banque mondiale)"), ("NY.GDP.MKTP.KD.ZG", "gdp", "Croissance du PIB (Banque mondiale)"),
          ("SL.UEM.TOTL.ZS", "unemp", "Chômage, estimation OIT (Banque mondiale)"), ("BN.CAB.XOKA.GD.ZS", "ca", "Compte courant, % PIB (Banque mondiale)"),
          ("FR.INR.LEND", "lend", "Taux prêteur bancaire (Banque mondiale)")]
IMF_IND = [("PCPIPCH", "inf_f", "Inflation, prévision FMI (WEO)"), ("NGDP_RPCH", "gdp_f", "Croissance, prévision FMI (WEO)")]


def do_world(data):
    w = {}

    def wb(ind):
        code, key, label = ind
        url = "https://api.worldbank.org/v2/country/all/indicator/%s?format=json&mrnev=1&per_page=600" % code
        try:
            j = json.loads(http("wb_%s.json" % key, url, tries=2, timeout=40))
            rows = j[1] if isinstance(j, list) and len(j) > 1 else []
            n, newest = 0, ""
            for r in rows or []:
                iso, v = r.get("countryiso3code"), num(r.get("value"))
                if iso and v is not None:
                    with LOCK:
                        w.setdefault(iso, {"n": (r.get("country") or {}).get("value")})[key] = [round(v, 2), r.get("date")]
                    n += 1
                    newest = max(newest, r.get("date") or "")
            if not n:
                raise ValueError("aucune valeur")
            note("wb." + key, label, "annual", True, newest + "-12-31" if newest else None, url="https://data.worldbank.org/indicator/" + code)
        except Exception as e:
            note("wb." + key, label, "annual", False, err=e, url="https://data.worldbank.org/indicator/" + code)
    yr = str(NOW.year)

    def imf(ind):
        code, key, label = ind
        url = "https://www.imf.org/external/datamapper/api/v1/%s?periods=%s" % (code, yr)
        try:
            j = json.loads(http("imf_%s.json" % key, url, tries=2, timeout=40, headers={
                "Accept": "application/json, text/plain, */*", "Referer": "https://www.imf.org/external/datamapper/"}))
            vals = (j.get("values") or {}).get(code) or {}
            n = 0
            for iso, per in vals.items():
                v = num((per or {}).get(yr))
                if v is not None and len(iso) == 3:
                    with LOCK:
                        w.setdefault(iso, {})[key] = [round(v, 2), yr]
                    n += 1
            if not n:
                raise ValueError("aucune valeur")
            note("imf." + key, label + " " + yr, "annual", True, TODAY, url="https://www.imf.org/external/datamapper/" + code)
        except Exception as e:
            note("imf." + key, label + " " + yr, "annual", False, err=e, url="https://www.imf.org/external/datamapper/" + code)
    with ThreadPoolExecutor(7) as ex:
        list(ex.map(wb, WB_IND))  # FMI retiré le 30 oct. 2026 : il refuse les serveurs de GitHub (403)
    data["world"] = w


# ---------------------------------------------------------------- Saisonnalité 20 ans (fichier séparé)
GH = "https://raw.githubusercontent.com/datasets/"
FX_CSV = GH + "exchange-rates/main/data/daily.csv"          # Fed H.10 (relais datahub/FRED)
GOLD_CSV = GH + "gold-prices/main/data/monthly.csv"         # Banque mondiale « Pink Sheet », moyennes mensuelles
# Pays H.10 → (paire, inverser ?). Vérifié le 30 oct. 2026 : ce fichier donne TOUTES les devises en unités par dollar
# (Euro = 0,8929 quand la BCE donne EUR/USD = 1,1193), donc EUR, GBP, AUD, NZD sont inversés pour obtenir la cotation marché.
FX_MAP = {"Euro": ("EURUSD", True), "United Kingdom": ("GBPUSD", True), "Japan": ("USDJPY", False), "Switzerland": ("USDCHF", False),
          "Australia": ("AUDUSD", True), "Canada": ("USDCAD", False), "New Zealand": ("NZDUSD", True)}


def month_stats(rets, years):
    out = []
    for m in range(1, 13):
        xs = [rets[(y, m)] for y in years if (y, m) in rets]
        if not xs:
            out.append(None)
            continue
        out.append({"avg": round(sum(xs) / len(xs), 3), "med": round(statistics.median(xs), 3),
                    "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100), "n": len(xs)})
    return out


def week_stats(wrets, years):
    out = []
    for w in range(1, 53):
        xs = [wrets[(y, w)] for y in years if (y, w) in wrets]
        out.append({"avg": round(sum(xs) / len(xs), 3), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100), "n": len(xs)} if xs else None)
    return out


def do_seasonal(force=False):
    """Recalcule data/seasonal.json au plus une fois par semaine (les historiques bougent peu)."""
    if not force and os.path.exists(OUT_SEAS):
        try:
            with open(OUT_SEAS, encoding="utf-8") as f:
                old = json.load(f)
            if (NOW - datetime.strptime(old["generated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days < 7 and old.get("series"):
                note("seas", "Saisonnalité 20 ans (recalcul hebdomadaire)", "weekly", True, old["generated"][:10], url=FX_CSV)
                return
        except Exception:
            pass
    y1 = NOW.year - 1
    years = list(range(y1 - 19, y1 + 1))
    res = {"generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "years": [years[0], years[-1]], "series": {}}
    errs = []
    # Paires : clôtures quotidiennes Fed H.10
    try:
        daily = {}
        for r in csv.DictReader(io.StringIO(http("fx_daily.csv", FX_CSV, timeout=120))):
            k = FX_MAP.get(r.get("Country"))
            v = num(r.get("Exchange rate") or r.get("Value"))
            if k and v and v > 0 and r["Date"] >= "%d-12-01" % (years[0] - 1):
                daily.setdefault(k[0], []).append((r["Date"], 1 / v if k[1] else v))
        for pair, obs in daily.items():
            obs.sort()
            mclose, wclose = {}, {}
            for d, v in obs:
                dt = date.fromisoformat(d)
                mclose[(dt.year, dt.month)] = v
                iso = dt.isocalendar()
                wclose[(iso[0], iso[1])] = v
            rets, keys = {}, sorted(mclose)
            for a, b in zip(keys, keys[1:]):
                rets[b] = (mclose[b] / mclose[a] - 1) * 100
            wrets, wk = {}, sorted(wclose)
            for a, b in zip(wk, wk[1:]):
                if b[1] <= 52:
                    wrets[b] = (wclose[b] / wclose[a] - 1) * 100
            cur = [round(rets[(NOW.year, m)], 3) if (NOW.year, m) in rets else None for m in range(1, 13)]
            res["series"][pair] = {"basis": "clôtures quotidiennes Fed H.10 (dernier jour du mois / de la semaine)",
                                   "asof": obs[-1][0], "monthly": month_stats(rets, years), "weekly": week_stats(wrets, years), "cur": cur}
        if not daily:
            raise ValueError("aucune paire")
        e = res["series"].get("EURUSD")
        if e and not (0.7 < daily["EURUSD"][-1][1] < 1.8):  # garde-fou sur le sens de cotation
            raise ValueError("EUR/USD hors plage, sens de cotation à revoir")
        note("seas.fx", "Saisonnalité 20 ans des paires (Fed H.10)", "weekly", True, max(s["asof"] for s in res["series"].values()), url=FX_CSV)
    except Exception as e:
        errs.append(e)
        note("seas.fx", "Saisonnalité 20 ans des paires (Fed H.10)", "weekly", False, err=e, url=FX_CSV)
    # Or : moyennes mensuelles Banque mondiale
    try:
        mavg = {}
        for r in csv.DictReader(io.StringIO(http("gold_monthly.csv", GOLD_CSV, timeout=60))):
            v = num(r.get("Price"))
            if v and re.match(r"\d{4}-\d{2}$", r.get("Date", "")):
                mavg[(int(r["Date"][:4]), int(r["Date"][5:]))] = v
        keys = sorted(mavg)
        rets = {b: (mavg[b] / mavg[a] - 1) * 100 for a, b in zip(keys, keys[1:])}
        cur = [round(rets[(NOW.year, m)], 3) if (NOW.year, m) in rets else None for m in range(1, 13)]
        last = keys[-1]
        res["series"]["XAUUSD"] = {"basis": "moyennes mensuelles Banque mondiale (Pink Sheet), pas des clôtures",
                                   "asof": "%d-%02d" % last, "monthly": month_stats(rets, years), "weekly": None, "cur": cur}
        note("seas.gold", "Saisonnalité 20 ans de l'or (Banque mondiale)", "monthly", True, "%d-%02d-28" % last, url=GOLD_CSV)
    except Exception as e:
        errs.append(e)
        note("seas.gold", "Saisonnalité 20 ans de l'or (Banque mondiale)", "monthly", False, err=e, url=GOLD_CSV)
    if res["series"]:
        os.makedirs(os.path.dirname(os.path.abspath(OUT_SEAS)), exist_ok=True)
        with open(OUT_SEAS, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- Programme principal
def main():
    data = {"fred": {}, "derived": {}, "oecd": {}, "national": {}, "cot": {}, "news": []}
    steps = [("fred", do_fred), ("oecd", do_oecd), ("national", do_national), ("cot", do_cot), ("fng", do_fng),
             ("rss", do_rss), ("calendar", do_calendar), ("markets", do_markets), ("world", do_world)]
    def run(step):
        name, fn = step
        try:
            fn(data)
        except Exception as e:  # filet de sécurité : une erreur de code n'arrête pas le reste
            note("robot." + name, "Bloc " + name, "daily", False, err=e)
    todo = [st for st in steps if want(st[0])]
    if want("seasonal"):
        todo.append(("seasonal", lambda d: do_seasonal(force=os.environ.get("MF_SEAS_FORCE") == "1")))
    # Chronomètre global : au-delà de BUDGET secondes, on enregistre ce qu'on a et on s'arrête.
    budget = int(os.environ.get("MF_BUDGET", "420"))
    ex = ThreadPoolExecutor(len(todo) or 1)
    futs = {ex.submit(run, st): st[0] for st in todo}
    done, pending = wait(futs, timeout=budget)
    for f in pending:
        note("robot." + futs[f], "Bloc " + futs[f] + " (arrêté : délai global de %d s dépassé)" % budget, "daily", False, err="délai dépassé")

    out = {"generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": SOURCES}
    out.update(data)
    ok = sum(1 for s in SOURCES if s["ok"])
    SOURCES.sort(key=lambda s: s["id"])
    print("BILAN : %d/%d sources OK en %.0f s" % (ok, len(SOURCES), time.time() - T0), flush=True)
    if ok == 0:
        sys.exit(1)  # rien récupéré : on ne remplace pas l'ancien fichier
    if ONLY:
        return  # mode diagnostic : on n'écrase pas le fichier complet
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with LOCK:
        txt = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # coupe net les éventuels téléchargements encore bloqués
