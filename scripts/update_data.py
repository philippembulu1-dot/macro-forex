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
OUT_HIST = os.environ.get("MF_OUT_HIST", os.path.join(ROOT, "data", "macro-history.json"))
OUT_SURP = os.environ.get("MF_OUT_SURP", os.path.join(ROOT, "data", "surprises.json"))
DIAG = {}
PREVIOUS = {}
try:  # fichier précédent : sert à conserver les dernières données valides d'une source instable
    with open(OUT, encoding="utf-8") as _f:
        PREVIOUS = json.load(_f)
except Exception:
    PREVIOUS = {}
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
            DIAG["nfci"] = {"entete": rows[0][:5], "lignes": len(rows), "premiere": rows[1][:3] if len(rows) > 1 else None, "derniere": rows[-1][:3]}
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
            data["cycle_src"] = {"unemp": ser.get("LNS14000000", [])[-36:], "nfp": ser.get("CES0000000001", [])[-36:],
                                 "cpi": ser.get("CUSR0000SA0", [])[-48:], "core": ser.get("CUSR0000SA0L1E", [])[-48:]}
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
    # Bilan de la Fed : page officielle de la publication hebdomadaire H.4.1 (ligne « Total assets »)
    if need("WALCL"):
        try:
            html = http("fed_h41.html", "https://www.federalreserve.gov/releases/h41/current/default.htm", timeout=60, tries=2)
            txt = re.sub(r"<[^>]+>", " ", html)
            txt = re.sub(r"&nbsp;|&#160;|&#xa0;|&#xA0;|\xa0", " ", txt)
            txt = re.sub(r"\s+", " ", txt)
            m = re.search(r"Total assets(?:\s*\(\d+\))?\s+([\d,]{6,})\s+([+\-−])\s*([\d,]+)", txt)
            if not m:
                DIAG["h41_extrait"] = txt[txt.find("Total assets") - 80: txt.find("Total assets") + 160] if "Total assets" in txt else txt[:200]
                raise ValueError("ligne « Total assets » introuvable sur la page H.4.1")
            v = num(m.group(1))
            ch = num(m.group(3))
            if ch is not None and m.group(2) in "-−":
                ch = -ch
            dm = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})", txt)
            asof = datetime.strptime(dm.group(0), "%B %d, %Y").strftime("%Y-%m-%d") if dm else TODAY
            F["WALCL"] = {"v": v, "prev": v - ch if ch is not None else None, "asof": asof, "hist": [], "via": "Réserve fédérale (H.4.1)"}
            note("fed.h41", "Bilan de la Fed (Réserve fédérale, H.4.1)", "weekly", True, asof, url="https://www.federalreserve.gov/releases/h41/current/")
        except Exception as e:
            note("fed.h41", "Bilan de la Fed (Réserve fédérale, H.4.1)", "weekly", False, err=e, url="https://www.federalreserve.gov/releases/h41/current/")
    # Dernière chance pour les séries qui n'existent que chez la Fed de St. Louis : délai long, FRED puis ALFRED
    # FRED et ALFRED testés le 31 oct. 2026 avec 90 s d'attente : inaccessibles depuis GitHub. Ces séries n'ont pas d'autre source gratuite.
    with LOCK:
        for x in SOURCES:
            if x["id"] in ("fred.STLFSI4", "fred.BAMLH0A0HYM2", "fred.BAMLC0A0CM", "fred.PCEPILFE") and not x["ok"]:
                x["kind"] = "config" if x["id"] in ("fred.BAMLH0A0HYM2", "fred.BAMLC0A0CM") else "nofree"
                x["err"] = ("à configurer : clé gratuite FRED dans les secrets GitHub (FRED_API_KEY) ; en attendant, composante crédit de l'indice OFR"
                            if x["kind"] == "config" else "non disponible gratuitement depuis GitHub (FRED seulement) : à consulter sur le site")
    # Une série FRED remplacée par une source de secours n'est plus affichée comme une panne
    with LOCK:
        SOURCES[:] = [x for x in SOURCES if not (x["id"].startswith("fred.") and not x["ok"] and x["id"][5:] in F)]
        if "DXY" in F:
            SOURCES[:] = [x for x in SOURCES if not (x["id"] == "fred.DTWEXBGS" and not x["ok"])]
        if data["derived"].get("us_cpi"):
            SOURCES[:] = [x for x in SOURCES if not (x["id"] in ("fred.CPIAUCSL", "fred.CPILFESL", "fred.UNRATE", "fred.PAYEMS") and not x["ok"])]
        if data["derived"].get("us_pce_core"):
            SOURCES[:] = [x for x in SOURCES if not (x["id"] == "fred.PCEPILFE" and not x["ok"])]


def do_oecd(data):
    if not fred_reachable():
        # FRED refuse les serveurs de GitHub : ces séries sont remplacées par les sources nationales (BoE, BoC, BNS, ONS, BCE, BRI…).
        DIAG["oecd_fred"] = "FRED injoignable : remplacé par l'API de l'OCDE et les sources nationales"
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


def bis_rate(cc):
    """Taux directeur publié par la BRI (Banque des règlements internationaux), série quotidienne WS_CBPOL."""
    start = (NOW - timedelta(days=200)).strftime("%Y-%m-%d")
    last = None
    for url in ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.%s?startPeriod=%s&format=csv" % (cc, start),
                "https://stats.bis.org/api/v1/data/WS_CBPOL/D.%s?startPeriod=%s&format=csv" % (cc, start)):
        try:
            txt = http("bis_%s.csv" % cc, url, tries=1, timeout=25)
            rows = list(csv.DictReader(io.StringIO(txt)))
            obs = [(r.get("TIME_PERIOD", "")[:10], num(r.get("OBS_VALUE"))) for r in rows if r.get("TIME_PERIOD")]
            obs = sorted(o for o in obs if o[1] is not None)
            if not obs:
                DIAG["bis_" + cc] = txt[:160]
                raise ValueError("aucune valeur dans la réponse")
            res = last2(obs)
            res["prev"] = next((v for d, v in reversed(obs) if v != res["v"]), None)  # dernier niveau différent = avant la dernière décision
            chg = None
            for d, v in reversed(obs):
                if v != res["v"]:
                    break
                chg = d
            res["since"] = chg if res["prev"] is not None else None  # premier jour au niveau actuel (dans la fenêtre lue)
            res["via"] = "BRI (WS_CBPOL)"
            return res
        except Exception as e:
            last = e
    raise last


BIS_CB = [("US", "USD", "Fed"), ("XM", "EUR", "BCE"), ("GB", "GBP", "BoE"), ("JP", "JPY", "BoJ"), ("CH", "CHF", "BNS"), ("AU", "AUD", "RBA"), ("CA", "CAD", "BoC"), ("NZ", "NZD", "RBNZ")]


def do_policy(data):
    """Taux directeurs des 8 banques centrales, source unique et officielle : BRI (WS_CBPOL, quotidien, quelques jours de retard)."""
    out = {}
    for cc, cur, name in BIS_CB:
        try:
            r = bis_rate(cc)
            r["cb"] = name
            out[cur] = r
        except Exception as e:
            DIAG["bis_" + cc] = fr_err(e)[:120]
    data["policy"] = out
    newest = max((v["asof"] for v in out.values()), default=None)
    note("bis.policy", "Taux directeurs des 8 banques centrales (BRI, publiés avec environ une semaine de retard) %d/8" % len(out), "weekly", len(out) > 0, newest,
         err=None if out else "aucune réponse", url="https://data.bis.org/topics/CBPOL")


# Cotations différées (Yahoo, bougies d'1 h sur 7 jours) : or, dollar et les 7 paires du G8
INTRA = [("gold", "GC=F"), ("dxy", "DX-Y.NYB"), ("EURUSD", "EURUSD=X"), ("GBPUSD", "GBPUSD=X"), ("USDJPY", "JPY=X"), ("USDCHF", "CHF=X"),
         ("USDCAD", "CAD=X"), ("AUDUSD", "AUDUSD=X"), ("NZDUSD", "NZDUSD=X")]


def do_intraday(data):
    out, bad = {}, []
    for key, sym in INTRA:
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=60m&range=8d" % urllib.parse.quote(sym)
            r = json.loads(http("yi_%s.json" % key, url, tries=2, timeout=20))["chart"]["result"][0]
            q, m = r["indicators"]["quote"][0], r.get("meta") or {}
            rows = [[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), round(c, 6)] for t, c in zip(r.get("timestamp") or [], q.get("close") or []) if c is not None]
            if len(rows) < 20:
                raise ValueError("trop peu de bougies")
            p_, t_ = m.get("regularMarketPrice"), m.get("regularMarketTime")
            out[key] = {"sym": sym, "c": rows[-170:], "p": p_ if p_ else rows[-1][1],
                        "t": datetime.fromtimestamp(t_, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if t_ else rows[-1][0]}
        except Exception as e:
            bad.append(key)
            DIAG["intra_" + key] = fr_err(e)[:120]
    data["intraday"] = out
    note("yahoo.intraday", "Cotations différées heure par heure : or, DXY, 7 paires (Yahoo Finance, non officiel) %d/%d" % (len(out), len(INTRA)),
         "daily", len(out) > 0, max((v["t"] for v in out.values()), default=None), err=("échec : " + ", ".join(bad)) if bad else None, url="https://finance.yahoo.com/")


OUT_ETF = os.environ.get("MF_OUT_ETF", os.path.join(ROOT, "data", "gold-etf.json"))


def ishares_snapshot(pid, slug, tick):
    """Fiche quotidienne d'un ETF iShares (CSV public) : parts en circulation et quantité d'or détenue."""
    url = "https://www.ishares.com/us/products/%s/%s/1467271812596.ajax?fileType=csv&fileName=%s_holdings&dataType=fund" % (pid, slug, tick)
    txt = http("ish_%s.csv" % tick, url, tries=2, timeout=30)
    if "%PDF" in txt[:10] or "<html" in txt[:300].lower():
        raise ValueError("réponse non CSV")
    asof, shares, oz, nav = None, None, None, None
    lines = [l for l in txt.replace("\r", "").split("\n")]
    head_i = None
    for i, l in enumerate(lines[:40]):
        ll = l.lower()
        if "holdings as of" in ll or "fund holdings as of" in ll:
            m = re.search(r'"?([A-Za-z]{3} \d{1,2}, \d{4})"?', l)
            if m:
                asof = datetime.strptime(m.group(1), "%b %d, %Y").strftime("%Y-%m-%d")
        if ll.startswith("shares outstanding"):
            shares = num(re.sub(r"[^\d.]", "", l.split(",", 1)[1] if "," in l else l))
        if ll.startswith("ticker,") or ll.startswith('"ticker"'):
            head_i = i
            break
    if head_i is not None:
        rows = list(csv.reader(lines[head_i:head_i + 30]))
        h = [c.strip().lower() for c in rows[0]]
        qi = next((i for i, c in enumerate(h) if c in ("quantity", "shares")), None)
        mi = next((i for i, c in enumerate(h) if "market value" in c), None)
        for r in rows[1:]:
            if len(r) > 1 and "gold" in " ".join(r).lower():
                if qi is not None and qi < len(r):
                    oz = num(re.sub(r"[^\d.]", "", r[qi]))
                if mi is not None and mi < len(r):
                    nav = num(re.sub(r"[^\d.]", "", r[mi]))
                break
    if not shares and not oz:
        DIAG["ish_" + tick] = txt[:300]
        raise ValueError("parts et onces introuvables")
    return {"d": asof or TODAY, "shares": shares, "oz": oz, "val": nav}


def gld_archive():
    """Archive officielle GLD (SPDR) : tonnes d'or détenues, chaque jour ouvré."""
    last = None
    for url, hd in (("https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv", {"Referer": "https://www.spdrgoldshares.com/usa/historical-data/"}),
                    ("https://spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv", {"Referer": "https://spdrgoldshares.com/usa/historical-data/", "Accept": "text/csv,*/*"}),
                    ("https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_SG_archive_EN.csv", {"Referer": "https://www.spdrgoldshares.com/singapore/"})):
        try:
            txt = http("gld_%d.csv" % len(url), url, tries=1, timeout=40, headers=hd)
            if txt[:5] == "%PDF-":
                raise ValueError("PDF au lieu du CSV")
            txt = txt.replace("\r\n", "\n").replace("\r", "\n")
            rows = []
            for line in txt.split("\n"):
                try:
                    rows.extend(csv.reader([line]))
                except csv.Error:
                    rows.append(line.split(","))
            hi = next((i for i, r in enumerate(rows[:15]) if r and any("tonnes" in c.lower() for c in r)), None)
            if hi is None:
                raise ValueError("colonne des tonnes absente")
            ct = next(i for i, c in enumerate(rows[hi]) if "tonnes" in c.lower())
            out = []
            for r in rows[hi + 1:]:
                if len(r) <= ct or not r[0].strip():
                    continue
                d = None
                for fmt_ in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                    try:
                        d = datetime.strptime(r[0].strip(), fmt_).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass
                v = num(re.sub(r"[^\d.\-]", "", r[ct]))
                if d and v:
                    out.append([d, round(v, 2)])
            out.sort()
            if len(out) < 20:
                raise ValueError("trop peu de lignes")
            return out
        except Exception as e:
            last = e
            DIAG["gld_%d" % len(DIAG)] = url[-30:] + " : " + fr_err(e)[:80]
    raise last


def nasdaq_shares(tick):
    """Parts en circulation d'un ETF (API publique de Nasdaq.com, sans clé)."""
    url = "https://api.nasdaq.com/api/quote/%s/summary?assetclass=etf" % tick
    j = json.loads(http("nq_%s.json" % tick, url, tries=2, timeout=20, headers={"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}))
    sd = ((j.get("data") or {}).get("summaryData") or {})
    g = lambda k: num(re.sub(r"[^\d.]", "", str((sd.get(k) or {}).get("value", "")))) if sd.get(k) else None
    aum, mcap, pc = g("AUM"), g("MarketCap"), g("PreviousClose")
    if not aum and not mcap:
        DIAG["nq_" + tick] = json.dumps(sd)[:300]
        raise ValueError("encours (AUM) absent")
    return {"aum": aum, "mcap": mcap, "pc": pc, "raw": str((sd.get("AUM") or {}).get("value", ""))[:30]}


def do_goldetf(data):
    """Flux des ETF d'or : archive GLD si le fichier répond, sinon fiches quotidiennes mémorisées par le robot (GLD par l'archive, IAU par iShares)."""
    st = {}
    try:
        with open(OUT_ETF, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    st.setdefault("snap", {})
    ok_any = False
    try:
        arc = gld_archive()
        st["gld"] = arc[-400:]
        note("etf.gld", "Or détenu par l'ETF GLD (SPDR, archive officielle, tonnes)", "daily", True, arc[-1][0], url="https://www.spdrgoldshares.com/usa/historical-data/")
        ok_any = True
    except Exception as e:
        DIAG["etf_gld"] = "archive SPDR refusée (PDF ou 404) : remplacée par l'encours Nasdaq"
    try:
        sn = ishares_snapshot("239561", "ishares-gold-trust-fund", "IAU")
        lst = st["snap"].setdefault("IAU", [])
        if not lst or lst[-1]["d"] != sn["d"]:
            lst.append(sn)
        else:
            lst[-1] = sn
        st["snap"]["IAU"] = lst[-500:]
        note("etf.iau", "ETF IAU (iShares) : parts et or détenu, mémorisés chaque jour", "daily", True, sn["d"], url="https://www.ishares.com/us/products/239561/ishares-gold-trust-fund")
        ok_any = True
    except Exception as e:
        DIAG["etf_iau"] = "fiche iShares refusée : remplacée par l'encours Nasdaq"
    # Parts en circulation de GLD et IAU (Nasdaq) : mémorisées chaque jour ; flux = variation des parts × cours
    for tick in ("GLD", "IAU"):
        try:
            sh = nasdaq_shares(tick)  # encours (AUM) et capitalisation : flux = variation de l'encours non expliquée par le prix
            px = None
            try:
                rj = json.loads(http("yq_%s.json" % tick, "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5d" % tick, tries=1, timeout=15))
                px = (rj["chart"]["result"][0].get("meta") or {}).get("regularMarketPrice")
            except Exception:
                pass
            lst = st["snap"].setdefault(tick + "_nq", [])
            row = {"d": TODAY, "aum": sh["aum"], "mcap": sh["mcap"], "pc": sh["pc"], "px": px, "raw": sh["raw"]}
            if lst and lst[-1]["d"] == TODAY:
                lst[-1] = row
            else:
                lst.append(row)
            st["snap"][tick + "_nq"] = lst[-500:]
            note("etf.nq." + tick, "ETF %s : encours et capitalisation (Nasdaq), mémorisés chaque jour" % tick, "daily", True, TODAY, url="https://www.nasdaq.com/market-activity/etf/%s" % tick.lower())
            ok_any = True
        except Exception as e:
            note("etf.nq." + tick, "ETF %s : encours et capitalisation (Nasdaq), mémorisés chaque jour" % tick, "daily", False, err=e, url="https://www.nasdaq.com/market-activity/etf/%s" % tick.lower())
    st["generated"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT_ETF, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    data["goldetf"] = {"gld": (st.get("gld") or [])[-260:], "snap": {k: v[-260:] for k, v in st["snap"].items()}}


# Contrats à terme sur le taux des fonds fédéraux (CBOT, 30 jours) : base du calcul « méthode CME FedWatch »
ZQ_M = "FGHJKMNQUVXZ"


def do_zq(data):
    out, bad = {}, []
    y, m = NOW.year, NOW.month
    for i in range(16):
        yy, mm = y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1
        sym = "ZQ%s%02d.CBT" % (ZQ_M[mm - 1], yy % 100)
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5d" % urllib.parse.quote(sym)
            r = json.loads(http("zq_%s.json" % sym, url, tries=2, timeout=15))["chart"]["result"][0]
            meta = r.get("meta") or {}
            closes = [c for c in (r["indicators"]["quote"][0].get("close") or []) if c]
            p_ = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
            if not p_ or not (80 < p_ < 101):
                raise ValueError("prix absent")
            t_ = meta.get("regularMarketTime")
            out["%d-%02d" % (yy, mm)] = {"p": p_, "sym": sym, "t": datetime.fromtimestamp(t_, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if t_ else None}
        except Exception as e:
            bad.append(sym)
            DIAG["zq_" + sym] = fr_err(e)[:80]
    data["zq"] = out
    note("cbot.zq", "Contrats à terme fed funds (CBOT, via Yahoo) %d/16 mois : calcul méthode CME FedWatch" % len(out), "daily", len(out) >= 3,
         max((v["t"] or "" for v in out.values()), default=None) or None, err=None if len(out) >= 3 else "contrats introuvables (%s)" % ", ".join(bad[:3]),
         url="https://www.cmegroup.com/markets/interest-rates/stirs/30-day-federal-fund.html")


def do_wgc(data):
    """Veille de la dernière édition du rapport trimestriel du World Gold Council (Gold Demand Trends)."""
    url = "https://www.gold.org/goldhub/research/gold-demand-trends"
    try:
        txt = http("wgc.html", url, tries=2, timeout=30)
        eds = sorted(set(re.findall(r"gold-demand-trends-(q[1-4])-(20\d\d)", txt.lower())), key=lambda x: (x[1], x[0]))
        if not eds:
            raise ValueError("aucune édition trouvée")
        q, yv = eds[-1]
        data["wgc"] = {"latest": "%s %s" % (q.upper(), yv), "url": "https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-%s-%s" % (q, yv)}
        note("wgc.gdt", "Demande d'or (World Gold Council) : veille des nouvelles éditions", "quarterly", True, TODAY, url=url)
    except Exception as e:
        note("wgc.gdt", "Demande d'or (World Gold Council) : veille des nouvelles éditions", "quarterly", False, err=e, url=url)


def do_gld(data):
    """Or détenu par le fonds GLD (SPDR Gold Shares), en tonnes : le plus grand ETF d'or, publié chaque jour par l'émetteur."""
    url = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"
    try:
        txt = http("gld.csv", url, tries=2, timeout=40).replace("\r\n", "\n").replace("\r", "\n")
        rows = []
        for line in txt.split("\n"):  # lecture ligne par ligne : le fichier contient des guillemets mal fermés
            try:
                rows.extend(csv.reader([line]))
            except csv.Error:
                rows.append(line.split(","))
        hi, head = None, None
        for i, r in enumerate(rows[:15]):
            if r and any("tonnes" in c.lower() for c in r):
                hi, head = i, [c.strip().lower() for c in r]
                break
        if head is None:
            DIAG["gld_head"] = txt[:40]
            raise ValueError("le fichier historique de l'émetteur n'est plus un CSV (PDF) : pas de source gratuite automatisable")
        ct = next(i for i, c in enumerate(head) if "tonnes" in c)
        out = []
        for r in rows[hi + 1:]:
            if len(r) <= ct or not r[0].strip():
                continue
            d = None
            for fmt_ in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    d = datetime.strptime(r[0].strip(), fmt_).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass
            v = num(re.sub(r"[^\d.\-]", "", r[ct]))
            if d and v:
                out.append([d, round(v, 2)])
        out.sort()
        if len(out) < 20:
            raise ValueError("trop peu de lignes")
        data["gld"] = {"t": out[-260:]}
        note("gld", "Or détenu par l'ETF GLD (SPDR, tonnes)", "daily", True, out[-1][0], url="https://www.spdrgoldshares.com/usa/historical-data/")
    except Exception as e:
        if PREVIOUS.get("gld"):
            data["gld"] = PREVIOUS["gld"]
        note("gld", "Or détenu par l'ETF GLD (SPDR, tonnes)", "nofree" if "PDF" in str(e) else "daily", False, err=e, url="https://www.spdrgoldshares.com/usa/historical-data/")


def rba_or_bis():
    try:
        res = rba_rate()
        if res and res.get("v") is not None:
            return res
    except Exception as e:
        DIAG["rba"] = fr_err(e)[:120]
    return bis_rate("AU")


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
    try:  # diagnostic : quelles zones euro existent et jusqu'à quel mois
        url = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr"
               "?format=JSON&lang=EN&coicop=CP00&unit=RCH_A&lastTimePeriod=1")
        j = json.loads(http("eurostat_all.json", url, timeout=40, tries=1))
        geos = j["dimension"]["geo"]["category"]["index"]
        tim = list(j["dimension"]["time"]["category"]["index"].keys())
        DIAG["eurostat_zones"] = {"mois_le_plus_recent": tim, "zones_EA": [g for g in geos if g.startswith("EA")]}
    except Exception as e:
        DIAG["eurostat_zones"] = "échec : " + fr_err(e)
    for geo in ("EA21", "EA", "EA20"):
        try:
            url = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr"
                   "?format=JSON&lang=EN&geo=%s&coicop=CP00&unit=RCH_A&lastTimePeriod=3" % geo)
            j = json.loads(http("eurostat_hicp_%s.json" % geo, url, timeout=30, tries=1))
            idx = j["dimension"]["time"]["category"]["index"]
            r = last2([(per[:7] + "-01", num(j["value"].get(str(pos)))) for per, pos in idx.items()])
            DIAG.setdefault("eurostat_geo", {})[geo] = r["asof"] if r else "vide"
            if r and (not best or r["asof"] > best["asof"]):
                best = r
        except Exception:
            pass
    if not best:
        raise ValueError("Eurostat : aucun code de zone euro n'a répondu")
    return best


def eurostat_minr(coicop):
    """Eurostat a remplacé prc_hicp_manr (arrêté en déc. 2025) par prc_hicp_minr (ECOICOP v2) en 2026."""
    last = None
    for geo in ("EA", "EA21", "EA20"):
        try:
            url = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr"
                   "?format=JSON&lang=EN&geo=%s&coicop18=%s&unit=RCH_A&sinceTimePeriod=%d-01" % (geo, coicop, NOW.year - 1))
            j = json.loads(http("eurostat_minr_%s_%s.json" % (geo, coicop), url, timeout=30, tries=1))
            tidx = j["dimension"]["time"]["category"]["index"]
            vals = j.get("value") or {}
            obs = []
            for t, i in tidx.items():
                v = vals.get(str(i))
                if v is not None:
                    obs.append((t[:7] + "-01", float(v)))
            r = last2(obs)
            DIAG.setdefault("eurostat_minr", {})[geo + "/" + coicop] = r["asof"] if r else "vide"
            if r:
                return r
        except Exception as e:
            last = e
            DIAG.setdefault("eurostat_minr", {})[geo + "/" + coicop] = fr_err(e)[:60]
    raise last or ValueError("vide")


def ez_cpi():
    best, err = None, None
    for fn in (lambda: eurostat_minr("TOTAL"), eurostat_hicp, ecb_hicp):
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
    best = None
    for key in ("M.U2.N.000000.4.ANR", "M.U2.N.000000.4F.ANR", "M.I9.N.000000.4.ANR"):
        try:
            url = "https://data-api.ecb.europa.eu/service/data/ICP/%s?format=csvdata&lastNObservations=3" % key
            rows = list(csv.DictReader(io.StringIO(http("ecb_hicp_%s.csv" % key, url, tries=1))))
            r = last2([(rw["TIME_PERIOD"] + "-01", num(rw["OBS_VALUE"])) for rw in rows])
            DIAG.setdefault("bce_hicp", {})[key] = r["asof"] if r else "vide"
            if r and (not best or r["asof"] > best["asof"]):
                best = r
        except Exception as e:
            DIAG.setdefault("bce_hicp", {})[key] = "échec : " + fr_err(e)
    if not best:
        raise ValueError("BCE : aucune série IPCH")
    return best


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
    nat(data, "rate_AU", "Taux directeur RBA (Banque de réserve d'Australie ; via la BRI, environ une semaine de retard)", "weekly", "https://www.rba.gov.au/statistics/tables/", rba_or_bis)
    nat(data, "rate_CH", "Taux directeur BNS (Banque nationale suisse)", "monthly", "https://data.snb.ch/", snb_rate)
    nat(data, "cpi_EZ", "Inflation zone euro (Eurostat / BCE, IPCH)", "monthly", "https://ec.europa.eu/eurostat/web/hicp", ez_cpi)
    nat(data, "cpi_GB", "Inflation Royaume-Uni (ONS)", "monthly", "https://www.ons.gov.uk/economy/inflationandpriceindices", ons_cpi)
    nat(data, "cpi_CA", "Inflation Canada (Banque du Canada)", "monthly", "https://www.bankofcanada.ca/rates/price-indexes/cpi/", boc_cpi)


# ---------------------------------------------------------------- COT (CFTC, « Legacy », futures seuls)
COT_CODES = {"088691": "XAU", "099741": "EUR", "096742": "GBP", "097741": "JPY", "092741": "CHF", "232741": "AUD", "090741": "CAD", "112741": "NZD"}


def cot_detail(dataset, codes, groups, key):
    """Rapports COT détaillés : « Disaggregated » (or : gestionnaires de fonds, producteurs, banques de swap)
    et « Traders in Financial Futures » (devises : gestionnaires d'actifs, fonds à levier, intermédiaires)."""
    url = "https://publicreporting.cftc.gov/resource/%s.json" % dataset
    since = (NOW - timedelta(days=400)).strftime("%Y-%m-%dT00:00:00")
    where = "cftc_contract_market_code in(%s) AND report_date_as_yyyy_mm_dd >= '%s'" % (",".join("'%s'" % c for c in codes), since)
    q = urllib.parse.urlencode({"$where": where, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "1200"})
    rows = json.loads(http("cot_%s.json" % key, url + "?" + q, timeout=40))
    if isinstance(rows, dict):
        raise ValueError(rows.get("message", "réponse inattendue"))
    if not rows:
        raise ValueError("aucune ligne")
    keys = list(rows[0].keys())
    def find(part, side):
        cand = [k for k in keys if part in k and ("_" + side) in k and k.endswith("_all") and "pct" not in k and "change" not in k and "spread" not in k]
        if not cand:
            cand = [k for k in keys if part in k and ("_" + side) in k and "pct" not in k and "change" not in k and "spread" not in k]
        return sorted(cand, key=len)[0] if cand else None
    cols = {g: (find(part, "long"), find(part, "short")) for g, part in groups}
    if not any(a and b for a, b in cols.values()):
        DIAG["cot_" + key + "_keys"] = ",".join(keys)[:400]
        raise ValueError("colonnes introuvables")
    by = {}
    for r in rows:
        c = codes.get(str(r.get("cftc_contract_market_code", "")).strip())
        if c:
            by.setdefault(c, []).append(r)
    out = {}
    I = lambda r, f: int(float(r.get(f) or 0)) if f else 0
    for c, lst in by.items():
        lst.sort(key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""), reverse=True)
        o = {"date": lst[0]["report_date_as_yyyy_mm_dd"][:10], "oi": I(lst[0], "open_interest_all"), "g": {}}
        for g, (lk, sk) in cols.items():
            if lk and sk:
                nets = [I(r, lk) - I(r, sk) for r in lst]
                lo, hi = min(nets), max(nets)
                o["g"][g] = {"long": I(lst[0], lk), "short": I(lst[0], sk), "net": nets[0], "prev": nets[1] if len(nets) > 1 else None,
                             "hist": nets[:26][::-1], "idx1y": round((nets[0] - lo) / (hi - lo) * 100) if hi > lo else None}
        out[c] = o
    return out


def do_cot_detail(data):
    res = {}
    for dataset, codes, groups, key, lab in (
            ("72hh-3qpy", {"088691": "XAU", "084691": "XAG"}, [("mm", "m_money_positions"), ("prod", "prod_merc_positions"), ("swap", "swap")], "disagg",
             "COT détaillé or et argent (gestionnaires de fonds, producteurs, banques de swap)"),
            ("gpe5-46if", {v: k for k, v in (("EUR", "099741"), ("GBP", "096742"), ("JPY", "097741"), ("CHF", "092741"), ("AUD", "232741"), ("CAD", "090741"), ("NZD", "112741"))},
             [("am", "asset_mgr_positions"), ("lev", "lev_money_positions"), ("dealer", "dealer_positions")], "tff",
             "COT détaillé devises (gestionnaires d'actifs, fonds à levier, intermédiaires)")):
        try:
            r = cot_detail(dataset, codes, groups, key)
            res.update(r)
            note("cftc." + key, lab, "weekly", True, max(v["date"] for v in r.values()), url="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm")
        except Exception as e:
            note("cftc." + key, lab, "weekly", False, err=e, url="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm")
    data["cot2"] = res


# ---------------------------------------------------------------- Remplaçants des séries FRED inaccessibles depuis GitHub
def do_ofr(data):
    """Indice de stress financier de l'OFR (Office of Financial Research, Trésor américain) : officiel, quotidien, avec ses 5 composantes."""
    url = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"
    try:
        rows = list(csv.reader(io.StringIO(http("ofr_fsi.csv", url, tries=2, timeout=40))))
        h = [c.strip() for c in rows[0]]
        lo = [c.lower() for c in h]
        ci = next(i for i, c in enumerate(lo) if c.replace(" ", "") in ("ofrfsi", "fsi"))
        comps = {k: next((i for i, c in enumerate(lo) if c.startswith(k)), None) for k in ("credit", "equity", "safe", "funding", "volatility")}
        obs = []
        for r in rows[1:]:
            if len(r) > ci and r[0] and num(r[ci]) is not None:
                obs.append([r[0][:10], num(r[ci])] + [num(r[comps[k]]) if comps[k] is not None and len(r) > comps[k] else None for k in ("credit", "equity", "safe", "funding", "volatility")])
        obs.sort()
        if len(obs) < 30:
            raise ValueError("trop peu de lignes")
        last = obs[-1]
        ci_ = 2  # composante « crédit » : thermomètre officiel du crédit, utilisé si les écarts ICE ne sont pas disponibles
        if obs[-1][ci_] is not None:
            data["fred"]["OFRCREDIT"] = {"v": obs[-1][ci_], "prev": obs[-6][ci_], "asof": last[0], "hist": [[o[0], o[ci_]] for o in obs[-260:] if o[ci_] is not None],
                                        "via": "OFR, composante crédit de l'indice de stress"}
        data["fred"]["STLFSI4"] = {"v": last[1], "prev": obs[-6][1], "asof": last[0], "hist": [[o[0], o[1]] for o in obs[-260:]],
                                  "via": "OFR (Office of Financial Research), remplace l'indice de St. Louis", "comp": dict(zip(("credit", "actions", "refuges", "financement", "volatilité"), last[2:]))}
        note("ofr.fsi", "Indice de stress financier (OFR, Trésor américain) et ses composantes", "daily", True, last[0], url="https://www.financialresearch.gov/financial-stress-index/")
    except Exception as e:
        note("ofr.fsi", "Indice de stress financier (OFR, Trésor américain) et ses composantes", "daily", False, err=e, url="https://www.financialresearch.gov/financial-stress-index/")


def do_bea(data):
    """Inflation PCE (globale et hors alimentation et énergie) : fichier mensuel public du BEA, sans clé."""
    url = "https://apps.bea.gov/national/Release/TXT/NipaDataM.txt"
    try:
        txt = http("bea_nipam.txt", url, tries=2, timeout=60)
        want_ = {"DPCERG": [], "DPCCRG": []}
        for line in txt.splitlines():
            if line[:7] in ("DPCERG,", "DPCCRG,"):
                parts = [x.strip().strip('"') for x in line.split(",", 2)]
                if len(parts) == 3 and re.match(r"\d{4}M\d{2}", parts[1]):
                    want_[parts[0]].append(("%s-%s-01" % (parts[1][:4], parts[1][5:7]), num(parts[2].replace(",", ""))))
        out = {}
        for code, key in (("DPCERG", "us_pce"), ("DPCCRG", "us_pce_core")):
            ser = sorted(o for o in want_[code] if o[1])
            by = dict(ser)
            yo = []
            for d, v in ser:
                y0 = "%04d-%s" % (int(d[:4]) - 1, d[5:])
                if y0 in by:
                    yo.append([d, round((v / by[y0] - 1) * 100, 2)])
            if len(yo) < 3:
                raise ValueError("série %s absente" % code)
            out[key] = yo
        data["derived"]["us_pce"] = {"v": out["us_pce"][-1][1], "prev": out["us_pce"][-2][1], "asof": out["us_pce"][-1][0]}
        data["derived"]["us_pce_core"] = {"v": out["us_pce_core"][-1][1], "prev": out["us_pce_core"][-2][1], "asof": out["us_pce_core"][-1][0]}
        data["fred"]["PCEPILFE"] = {"v": out["us_pce_core"][-1][1], "prev": out["us_pce_core"][-2][1], "asof": out["us_pce_core"][-1][0], "hist": out["us_pce_core"][-36:],
                                   "via": "BEA (Bureau of Economic Analysis), sur 1 an"}
        note("bea.pce", "Inflation PCE et PCE sous-jacente (BEA)", "monthly", True, out["us_pce_core"][-1][0], url="https://www.bea.gov/data/personal-consumption-expenditures-price-index")
    except Exception as e:
        note("bea.pce", "Inflation PCE et PCE sous-jacente (BEA)", "monthly", False, err=e, url="https://www.bea.gov/data/personal-consumption-expenditures-price-index")


OECD_CPI = {"JPN": "JP", "CHE": "CH", "AUS": "AU", "NZL": "NZ", "EA20": "EZ", "GBR": "GB", "CAN": "CA", "USA": "US"}


def do_oecd_sdmx(data):
    """Inflation des 8 économies : API publique de l'OCDE (SDMX), sans clé ; remplace les séries OCDE de FRED."""
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/%s.M+Q.N.CPI.PA._T.N.GY?startPeriod=%d-01&dimensionAtObservation=AllDimensions&format=csvfile"
           % ("+".join(OECD_CPI), NOW.year - 2))
    try:
        rows = list(csv.DictReader(io.StringIO(http("oecd_cpi.csv", url, tries=2, timeout=60))))
        if not rows:
            raise ValueError("réponse vide")
        by = {}
        for r in rows:
            a, f, t, v = r.get("REF_AREA"), r.get("FREQ"), r.get("TIME_PERIOD", ""), num(r.get("OBS_VALUE"))
            if a in OECD_CPI and v is not None:
                if f == "Q":
                    q = re.match(r"(\d{4})-Q(\d)", t)
                    if not q:
                        continue
                    t = "%s-%02d" % (q.group(1), int(q.group(2)) * 3)
                by.setdefault(a, {}).setdefault(f, []).append((t[:7] + "-01", v))
        got = []
        for a, cc in OECD_CPI.items():
            fr = by.get(a, {})
            ser = sorted(fr.get("M") or fr.get("Q") or [])
            if not ser:
                continue
            data["oecd"]["cpi_" + cc] = {"v": ser[-1][1], "prev": ser[-2][1] if len(ser) > 1 else None, "asof": ser[-1][0], "freq": "M" if fr.get("M") else "Q"}
            nat_ = data["national"].get("cpi_" + cc)
            if cc not in ("US",) and (not nat_ or nat_.get("asof", "") < ser[-1][0]):
                data["national"]["cpi_" + cc] = {"v": ser[-1][1], "prev": ser[-2][1] if len(ser) > 1 else None, "asof": ser[-1][0], "src": "https://data-explorer.oecd.org/", "via": "OCDE"}
            got.append(cc)
        if not got:
            DIAG["oecd_cols"] = ",".join(rows[0].keys())[:200]
            raise ValueError("aucune série lue")
        note("oecd.sdmx", "Inflation des 8 économies (OCDE, API publique) : %s" % ", ".join(got), "monthly", True, max(data["oecd"]["cpi_" + c]["asof"] for c in got), url="https://data-explorer.oecd.org/")
    except Exception as e:
        note("oecd.sdmx", "Inflation des 8 économies (OCDE, API publique)", "monthly", False, err=e, url="https://data-explorer.oecd.org/")


def do_fred_api(data):
    """Écarts de crédit ICE (haut rendement, qualité) : API officielle de la Fed de St. Louis avec une clé gratuite (secret GitHub FRED_API_KEY)."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    DIAG["fred_api_key"] = "présente" if key else "absente"
    if not key:
        return
    start = (NOW - timedelta(days=400)).strftime("%Y-%m-%d")
    for sid, lab in (("BAMLH0A0HYM2", "Écart de crédit haut rendement (ICE, via l'API FRED)"), ("BAMLC0A0CM", "Écart de crédit qualité (ICE, via l'API FRED)"),
                     ("NFCI", "Conditions financières NFCI (via l'API FRED, plus récent que le fichier de Chicago)")):
        url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode({"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start})
        try:
            j = json.loads(http("fredapi_%s.json" % sid, url, tries=2, timeout=30))
            obs = [[o["date"], num(o["value"])] for o in j.get("observations", []) if num(o.get("value")) is not None]
            if len(obs) < 10:
                raise ValueError("série vide")
            old = data["fred"].get(sid)
            if old and old.get("asof", "") >= obs[-1][0]:
                continue  # la source déjà lue est au moins aussi récente
            data["fred"][sid] = {"v": obs[-1][1], "prev": obs[-6][1], "asof": obs[-1][0], "hist": obs[-260:], "via": "FRED (API officielle)"}
            note("fredapi." + sid, lab, "weekly" if sid == "NFCI" else "daily", True, obs[-1][0], url="https://fred.stlouisfed.org/series/" + sid)
        except Exception as e:
            msg = fr_err(e).replace(key, "***")
            note("fredapi." + sid, lab, "daily", False, err=msg, url="https://fred.stlouisfed.org/series/" + sid)


def probe_credit(data):
    """Écarts de crédit ICE (HY, IG) : n'existent gratuitement que chez la Fed de St. Louis ; on essaie ses autres adresses."""
    for key, url in (("api", "https://api.stlouisfed.org/fred/series/observations?series_id=BAMLH0A0HYM2&file_type=json"),
                     ("research", "https://research.stlouisfed.org/fred2/series/BAMLH0A0HYM2/downloaddata/BAMLH0A0HYM2.csv"),
                     ("alfred", "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=BAMLH0A0HYM2")):
        try:
            txt = http("probe_%s" % key, url, tries=1, timeout=12)
            DIAG["credit_" + key] = "répond : " + txt[:80].replace("\n", " ")
        except Exception as e:
            DIAG["credit_" + key] = fr_err(e)[:90]


def do_core(data):
    """Inflation sous-jacente (hors alimentation et énergie) des 8 économies : OCDE (SDMX) + Eurostat pour la zone euro ;
    inflation du Japon : BRI (série longue des prix), absente de l'API de l'OCDE."""
    core, got = {}, []
    for exp in ("_TXCP01_NRG", "TXCP01_NRG", "_TXNRG_01_02"):
        try:
            url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/%s.M+Q.N.CPI.PA.%s.N.GY?startPeriod=%d-01&dimensionAtObservation=AllDimensions&format=csvfile"
                   % ("+".join(OECD_CPI), exp, NOW.year - 2))
            rows = list(csv.DictReader(io.StringIO(http("oecd_core_%s.csv" % exp, url, tries=1, timeout=50))))
            by = {}
            for r in rows:
                a, f, t, v = r.get("REF_AREA"), r.get("FREQ"), r.get("TIME_PERIOD", ""), num(r.get("OBS_VALUE"))
                if a in OECD_CPI and v is not None:
                    if f == "Q":
                        q = re.match(r"(\d{4})-Q(\d)", t)
                        if not q:
                            continue
                        t = "%s-%02d" % (q.group(1), int(q.group(2)) * 3)
                    by.setdefault(a, {}).setdefault(f, []).append((t[:7] + "-01", v))
            for a, cc in OECD_CPI.items():
                ser = sorted((by.get(a) or {}).get("M") or (by.get(a) or {}).get("Q") or [])
                if ser:
                    core[cc] = {"v": round(ser[-1][1], 2), "prev": round(ser[-2][1], 2) if len(ser) > 1 else None, "asof": ser[-1][0], "via": "OCDE"}
            DIAG["oecd_core_" + exp] = ",".join(sorted(core)) or "vide"
            if core:
                break
        except Exception as e:
            DIAG["oecd_core_" + exp] = fr_err(e)[:80]
    for co in ("TOT_X_NRG_FOOD", "TOT_X_NRG_FOOD_NP", "TOTXNRGFOOD"):
        try:
            r = eurostat_minr(co)
            if not core.get("EZ") or core["EZ"]["asof"] < r["asof"]:
                core["EZ"] = {"v": r["v"], "prev": r["prev"], "asof": r["asof"], "via": "Eurostat"}
            break
        except Exception:
            pass
    for ser in ("CPIX", "CPI_TRIM"):
        try:
            r = valet(ser, "valet_%s.json" % ser)
            if r and (not core.get("CA") or core["CA"]["asof"] < r["asof"]):
                core["CA"] = {"v": r["v"], "prev": r["prev"], "asof": r["asof"], "via": "Banque du Canada (%s)" % ser}
                break
        except Exception as e:
            DIAG["core_ca_" + ser] = fr_err(e)[:60]
    try:
        obs, cols, tot = [], None, []
        for r in csv.reader(io.StringIO(http("snb_infl.csv", "https://data.snb.ch/api/cube/plkoprinfla/data/csv/en", tries=1, timeout=30)), delimiter=";"):
            if len(r) >= 3 and re.match(r"\d{4}-\d{2}", r[0]):
                cols = cols or set()
                cols.add(r[1].strip())
                if r[1].strip() in ("SNB1", "KI1", "K1"):
                    obs.append((r[0][:7] + "-01", num(r[2])))
                if r[1].strip() == "TLK" and num(r[2]) is not None:
                    tot.append((r[0][:7] + "-01", num(r[2])))
        DIAG["snb_infl_cols"] = ",".join(sorted(cols or []))[:120]
        rt = last2(tot)  # inflation globale suisse (BNS)
        if rt and (not data["national"].get("cpi_CH") or data["national"]["cpi_CH"].get("asof", "") < rt["asof"]):
            rt["src"], rt["via"] = "https://data.snb.ch/", "BNS"
            data["national"]["cpi_CH"] = rt
        r = last2(obs)
        if r and (not core.get("CH") or core["CH"]["asof"] < r["asof"]):
            core["CH"] = {"v": r["v"], "prev": r["prev"], "asof": r["asof"], "via": "BNS (inflation sous-jacente 1)"}
    except Exception as e:
        DIAG["core_ch"] = fr_err(e)[:60]
    D = data["derived"].get("us_core")
    if D:
        core["US"] = {"v": D["v"], "prev": D.get("prev"), "asof": D["asof"], "via": "BLS"}
    data["core"] = core
    got = sorted(core)
    note("core.cpi", "Inflation sous-jacente des 8 économies (OCDE, Eurostat, BLS) : %s" % ", ".join(got), "monthly", len(got) >= 4,
         max((v["asof"] for v in core.values()), default=None), err=None if len(got) >= 4 else "trop peu de pays", url="https://data-explorer.oecd.org/")
    # Japon : inflation globale (BRI)
    try:
        url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_LONG_CPI/1.0/M.JP.771?startPeriod=%d-01&format=csv" % (NOW.year - 2)
        rows = list(csv.DictReader(io.StringIO(http("bis_cpi_jp.csv", url, tries=2, timeout=30))))
        obs = sorted((r["TIME_PERIOD"][:7] + "-01", num(r.get("OBS_VALUE"))) for r in rows if r.get("TIME_PERIOD") and num(r.get("OBS_VALUE")) is not None)
        if not obs:
            raise ValueError("aucune valeur")
        res = last2(obs)
        res["via"], res["src"] = "BRI", "https://data.bis.org/topics/CPI"
        old = data["national"].get("cpi_JP")
        if not old or old.get("asof", "") < res["asof"]:
            data["national"]["cpi_JP"] = res
        note("bis.cpi.jp", "Inflation du Japon (BRI)", "monthly", True, res["asof"], url="https://data.bis.org/topics/CPI")
    except Exception as e:
        note("bis.cpi.jp", "Inflation du Japon (BRI)", "monthly", False, err=e, url="https://data.bis.org/topics/CPI")


def do_fomc_dates(data):
    """Dates officielles des réunions de la Fed (page du calendrier du FOMC)."""
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    try:
        txt = http("fomc_cal.html", url, tries=2, timeout=30)
        MON = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"])}
        out = []
        for yb in re.finditer(r"(20\d\d) FOMC Meetings(.*?)(?=20\d\d FOMC Meetings|$)", txt, re.S):
            y = int(yb.group(1))
            for m in re.finditer(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', yb.group(2), re.S):
                mon, days = m.group(1).split("/")[-1], re.sub(r"[^\d\-–]", "", m.group(2))
                last_day = re.split(r"[-–]", days)[-1]
                if mon in MON and last_day.isdigit():
                    mo = MON[mon] if "/" not in m.group(1) or int(last_day) < 15 else MON[mon]
                    out.append("%d-%02d-%02d" % (y, mo, int(last_day)))
        out = sorted(set(d for d in out if d >= (NOW - timedelta(days=60)).strftime("%Y-%m-%d")))
        if len(out) < 4:
            DIAG["fomc_extrait"] = re.sub(r"\s+", " ", txt[txt.find("FOMC Meetings"):txt.find("FOMC Meetings") + 400]) if "FOMC Meetings" in txt else txt[:200]
            raise ValueError("dates introuvables")
        data["fomc"] = out
        note("fed.fomc", "Calendrier officiel des réunions de la Fed", "monthly", True, TODAY, url=url)
    except Exception as e:
        note("fed.fomc", "Calendrier officiel des réunions de la Fed", "monthly", False, err=e, url=url)


def strip_html(t):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&#160;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t)


def do_wgc_figures(data):
    """Chiffres clés de la dernière édition du rapport trimestriel du World Gold Council, lus dans son résumé public."""
    w = data.get("wgc")
    if not w:
        return
    try:
        txt = strip_html(http("wgc_ed.html", w["url"], tries=2, timeout=30))
        pats = {"tot": r"[Tt]otal gold demand[^.]{0,160}?([\d,]+(?:\.\d+)?)\s?t\b", "cb": r"[Cc]entral bank[^.]{0,200}?([\d,]+(?:\.\d+)?)\s?t\b",
                "etf": r"ETF[^.]{0,200}?([\-−+]?[\d,]+(?:\.\d+)?)\s?t\b", "jew": r"[Jj]ewel(?:le)?ry[^.]{0,200}?([\d,]+(?:\.\d+)?)\s?t\b",
                "bar": r"[Bb]ar and coin[^.]{0,200}?([\d,]+(?:\.\d+)?)\s?t\b", "tech": r"[Tt]echnology[^.]{0,200}?([\d,]+(?:\.\d+)?)\s?t\b"}
        fig = {}
        for k, pt in pats.items():
            m = re.search(pt, txt)
            if m:
                v = num(m.group(1).replace(",", "").replace("−", "-"))
                ctx = txt[max(0, m.start() - 20): m.end() + 10]
                if k == "etf" and re.search(r"outflow|fell|declin|sold|redemption", ctx, re.I):
                    v = -abs(v)
                fig[k] = {"v": v, "txt": ctx[:200]}
        if not fig:
            DIAG["wgc_extrait"] = txt[:300]
            raise ValueError("aucun chiffre lu dans le résumé")
        w["fig"] = fig
        note("wgc.fig", "Demande d'or : chiffres clés du dernier rapport WGC (%s), lus automatiquement" % w["latest"], "quarterly", True, TODAY, url=w["url"])
    except Exception as e:
        note("wgc.fig", "Demande d'or : chiffres clés du dernier rapport WGC", "quarterly", False, err=e, url=w["url"])


def do_ecb_proj_table(data):
    """Tableau des projections de la BCE, lu sur la page officielle de la dernière publication."""
    P = (data.get("projections") or {}).get("ecb")
    if not P or not P.get("url"):
        return
    try:
        tp = Tables()
        tp.feed(http("ecb_proj_page.html", P["url"], tries=2, timeout=40))
        want_ = {"gdp": r"^real gdp", "hicp": r"^hicp(\s|$)(?!.*excl)", "core": r"hicp excl.*(energy|food)", "unemp": r"unemployment"}
        best, found, main = None, {}, None
        for head, rows in tp.tables:
            years = None
            for r in rows[:4]:
                ys = [c.strip() for c in r if re.fullmatch(r"20\d\d", c.strip())]
                if len(ys) >= 3:
                    years = ys
                    break
            if not years:
                continue
            n = 1
            while n < len(years) and int(years[n]) == int(years[n - 1]) + 1:
                n += 1  # premières années croissantes = niveaux ; la suite = révisions
            if main is None:
                main = years[:n]
            for r in rows:
                lab = re.sub(r"\s*\d\)$", "", (r[0] if r else "").strip().lower())
                vals = [num(c.replace("−", "-").replace(",", ".")) for c in r[1:]]
                vals = [v for v in vals if v is not None]
                for k, rx in want_.items():
                    if k not in found and re.search(rx, lab) and len(vals) >= n and years[:n] == main:
                        found[k] = vals[:n]
        if main and len(found) >= 2:
            best = {"years": main, "rows": found}
        if not best:
            DIAG["ecb_proj_tables"] = [(h[:40], (rows[0] if rows else [])[:5]) for h, rows in tp.tables[:6]]
            raise ValueError("tableau des projections introuvable")
        P["tbl"] = best
        note("proj.ecb.tbl", "Projections de la BCE (%s), tableau lu automatiquement" % P.get("latest"), "quarterly", True, (P.get("latest") or TODAY[:7]) + "-01", url=P["url"])
    except Exception as e:
        note("proj.ecb.tbl", "Projections de la BCE, tableau lu automatiquement", "quarterly", False, err=e, url=P["url"])


BANKS = ["Goldman Sachs", "JPMorgan", "J.P. Morgan", "Morgan Stanley", "Citi", "UBS", "Bank of America", "BofA", "HSBC", "Deutsche Bank", "Commerzbank", "ING", "Wells Fargo", "Macquarie", "BNP Paribas", "Société Générale", "Barclays"]


def do_consensus_news(data):
    """Prévisions publiées par les banques, relevées automatiquement dans les titres de presse (Google Actualités, RSS public)."""
    out, seen, nfeed = [], set(), 0
    for asset, q in (("gold", "bank raises OR cuts gold price forecast"), ("gold", "gold price forecast Goldman OR JPMorgan OR UBS OR Citi"),
                     ("eurusd", "EUR/USD forecast bank Goldman OR ING OR Citi OR UBS"), ("fed", "Fed rate cut forecast Goldman OR JPMorgan OR Morgan Stanley")):
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:60d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        try:
            root = ET.fromstring(http("gnews_%d.xml" % nfeed, url, tries=2, timeout=25).encode("utf-8"))
            nfeed += 1
        except Exception as e:
            DIAG["gnews_" + asset] = fr_err(e)[:80]
            continue
        for it in root.iter("item"):
            t = (it.findtext("title") or "").strip()
            link, pub = it.findtext("link") or "", it.findtext("pubDate") or ""
            bank = next((b for b in BANKS if re.search(r"\b" + re.escape(b) + r"\b", t)), None)
            if not bank:
                continue
            val = None
            if asset == "gold" and re.search(r"gold", t, re.I):
                # garder le chiffre présenté comme une prévision, pas un niveau de prix du jour (« recule vers 4 000 $ », « support »…)
                for m in re.finditer(r"\$\s?(\d{1,2},?\d{3})(?!\d)", t):
                    before = t[max(0, m.start() - 30):m.start()].lower()
                    if re.search(r"toward|falls?|retest|support|near|hits?|below|above|drops?|slips?|tumbles?|surges? past|breaks?|tops?|record", before):
                        continue
                    if not re.search(r"sees?|forecast|target|predicts?|projects?|raises?|lifts?|cuts?|keeps?|expects?|\bto\b|\bat\b|by", before + " " + t[m.end():m.end() + 25].lower()):
                        continue
                    v = num(m.group(1).replace(",", ""))
                    if v and 1500 < v < 15000:
                        val = v
                        break
            elif asset == "eurusd" and re.search(r"EUR/?USD|euro", t, re.I):
                m = re.search(r"\b(1\.\d{2,4})\b", t)
                if m:
                    val = num(m.group(1))
            elif asset == "fed":
                m = re.search(r"\b(one|two|three|four|no|\d)\s+(?:more\s+)?(?:rate\s+)?(cuts?|hikes?)\b", t, re.I)
                if m:
                    val = m.group(0)
            if val is None:
                continue
            try:
                d = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
            except Exception:
                d = TODAY
            key = (bank.replace("J.P. Morgan", "JPMorgan").replace("BofA", "Bank of America"), asset, str(val))
            if key in seen:
                continue
            seen.add(key)
            out.append({"bank": key[0], "asset": asset, "v": val, "t": t[:160], "l": link, "d": d})
    out.sort(key=lambda x: x["d"], reverse=True)
    if out:
        data["consensus_auto"] = out[:40]
    elif (PREVIOUS.get("consensus_auto")):
        data["consensus_auto"] = PREVIOUS["consensus_auto"]
    note("news.consensus", "Prévisions des banques relevées dans la presse (%d)" % len(out), "daily", bool(out), TODAY if out else None,
         err=None if out else "aucun titre exploitable", url="https://news.google.com/")


KEI_AREAS = {"USA": "USD", "EA20": "EUR", "GBR": "GBP", "JPN": "JPY", "CHE": "CHF", "AUS": "AUD", "CAN": "CAD", "NZL": "NZD"}


def do_kei(data):
    """Indicateurs économiques clés de l'OCDE (KEI) : PIB, chômage, ventes au détail, confiance… des 8 économies, sans clé."""
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/%s?startPeriod=%d-01&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
           % ("+".join(KEI_AREAS) + "........", NOW.year - 1))
    try:
        txt = http("oecd_kei.csv", url, tries=2, timeout=90)
        rows = list(csv.DictReader(io.StringIO(txt)))
        if not rows:
            raise ValueError("réponse vide")
        cols = list(rows[0].keys())
        DIAG["kei_cols"] = ",".join(cols)[:300]
        dims = [c for c in cols if c not in ("TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "UNIT_MULT", "DECIMALS", "STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION") and not c.startswith(("Time", "Observation", "Unit multiplier", "Decimals", "Structure", "Action"))]
        out, labs = {}, set()
        for r in rows:
            area = r.get("REF_AREA")
            cur = KEI_AREAS.get(area)
            v = num(r.get("OBS_VALUE"))
            if not cur or v is None:
                continue
            key = "|".join(r.get(d, "") for d in ("MEASURE", "UNIT_MEASURE", "ADJUSTMENT", "TRANSFORMATION", "FREQ", "ACTIVITY"))
            lab = r.get("Measure") or r.get("MEASURE")
            labs.add((r.get("MEASURE"), lab, r.get("TRANSFORMATION"), r.get("Transformation"), r.get("FREQ")))
            e = out.setdefault(cur, {}).setdefault(key, {"m": r.get("MEASURE"), "lab": lab, "unit": r.get("Unit of measure") or r.get("UNIT_MEASURE"),
                                                        "tr": r.get("TRANSFORMATION"), "trl": r.get("Transformation"), "f": r.get("FREQ"), "act": r.get("ACTIVITY"), "adj": r.get("ADJUSTMENT"), "obs": []})
            e["obs"].append([r.get("TIME_PERIOD"), v])
        for cur in out:
            for k in out[cur]:
                out[cur][k]["obs"] = sorted(out[cur][k]["obs"])[-3:]
        DIAG["kei_mesures"] = sorted(set("%s:%s:%s:%s" % (a, (b or "")[:30], c, e) for a, b, c, d, e in labs))[:80]
        data["kei"] = out
        note("oecd.kei", "Indicateurs clés des 8 économies (OCDE)", "monthly", True, max((o["obs"][-1][0] for c in out.values() for o in c.values()), default=TODAY)[:10], url="https://data-explorer.oecd.org/")
    except Exception as e:
        note("oecd.kei", "Indicateurs clés des 8 économies (OCDE)", "monthly", False, err=e, url="https://data-explorer.oecd.org/")


def do_ffmem(data):
    """Mémoire du dernier chiffre publié de chaque indicateur du calendrier (champ « précédent » de Forex Factory), conservée d'une semaine à l'autre."""
    mem = dict((PREVIOUS.get("ffmem") or {}))
    n = 0
    for e in ((data.get("calendar") or {}).get("events") or []) + (data.pop("calendar_extra", None) or []):
        if not e.get("p") or e.get("c") not in ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"):
            continue
        k = e["c"] + "|" + e["t"]
        old = mem.get(k)
        if not old or old.get("d", "") <= e["d"][:10]:
            mem[k] = {"p": e["p"], "d": e["d"][:10], "f": e.get("f") or ""}
            n += 1
        if e.get("a"):
            mem[k]["a"] = e["a"]
    data["ffmem"] = mem
    note("ff.mem", "Derniers chiffres publiés, mémorisés depuis le calendrier (%d indicateurs)" % len(mem), "weekly", bool(mem), TODAY if mem else None, url="https://www.forexfactory.com/calendar")


CB_NEWS = [("AUD", "RBA", r"\bRBA\b|Reserve Bank of Australia", "RBA cash rate decision"), ("NZD", "RBNZ", r"\bRBNZ\b|Reserve Bank of New Zealand", "RBNZ official cash rate decision"),
           ("JPY", "BoJ", r"\bBOJ\b|\bBoJ\b|Bank of Japan", "Bank of Japan interest rate decision"), ("CHF", "BNS", r"\bSNB\b|Swiss National Bank", "Swiss National Bank interest rate decision"),
           ("GBP", "BoE", r"\bBoE\b|Bank of England", "Bank of England interest rate decision"), ("CAD", "BoC", r"Bank of Canada|\bBoC\b", "Bank of Canada interest rate decision")]


def do_cb_news(data):
    """Décisions de taux annoncées dans la presse le jour même (la BRI ne les publie qu'avec une semaine de retard)."""
    out = {}
    for cur, cb, rx, q in CB_NEWS:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:10d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        try:
            root = ET.fromstring(http("gn_cb_%s.xml" % cur, url, tries=1, timeout=20).encode("utf-8"))
        except Exception as e:
            DIAG["cbnews_" + cur] = fr_err(e)[:60]
            continue
        for it in root.iter("item"):
            t = it.findtext("title") or ""
            if not re.search(rx, t):
                continue
            m = re.search(r"\b(raises?|hikes?|lifts?|increases?|cuts?|lowers?|reduces?|holds?|keeps?|leaves?|maintains?|stays?|unchanged)\b.{0,70}?(-?\d{1,2}(?:\.\d{1,2})?)\s?(?:%|per ?cent)", t, re.I)
            if not m or re.search(r"\b(expect|expected|seen|likely|could|may|might|forecast|poll|preview|bets?|odds|will|set to|ahead|eyes|weighs|to (?:hold|raise|hike|cut|keep))\b", t, re.I):
                continue  # on ne garde que les décisions annoncées, pas les prévisions
            try:
                d = datetime.strptime((it.findtext("pubDate") or "")[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
            except Exception:
                continue
            v = num(m.group(2))
            verb = m.group(1).lower()
            act = "hausse" if re.match(r"rais|hike|lift|increas", verb) else "baisse" if re.match(r"cut|lower|reduc", verb) else "maintien"
            if v is not None and 0 <= v <= 15 and (cur not in out or out[cur]["d"] < d):
                out[cur] = {"v": v, "d": d, "act": act, "t": t[:160], "l": it.findtext("link") or "", "cb": cb}
    data["cb_news"] = out
    note("news.cbdecision", "Décisions de taux annoncées dans la presse (complément de la BRI) : %d" % len(out), "daily", True, TODAY, url="https://news.google.com/")


def retail_public(data):
    """Sentiment des particuliers sans identifiants : page publique « Community Outlook » de Myfxbook."""
    url = "https://www.myfxbook.com/community/outlook"
    txt = http("mfb_public.html", url, tries=2, timeout=30)
    out = {}
    for m in re.finditer(r"community/outlook/([A-Z]{6})\b", txt):
        sym = m.group(1)
        if sym in out:
            continue
        seg = strip_html(txt[m.end(): m.end() + 4000])
        sh = re.search(r"Short\D{0,40}?(\d{1,3}(?:\.\d+)?)\s?%", seg)
        lo = re.search(r"Long\D{0,40}?(\d{1,3}(?:\.\d+)?)\s?%", seg)
        if not (sh and lo):
            pc = re.findall(r"(\d{1,3}(?:\.\d+)?)\s?%", seg)[:2]
            if len(pc) == 2 and abs(float(pc[0]) + float(pc[1]) - 100) < 1.5:
                DIAG.setdefault("mfb_ordre", seg[:160])
            continue
        s_, l_ = float(sh.group(1)), float(lo.group(1))
        if abs(s_ + l_ - 100) < 1.5:
            out[sym] = {"long": round(l_, 1), "short": round(s_, 1)}
    if not out:
        DIAG["mfb_extrait"] = strip_html(txt)[:300]
        raise ValueError("pourcentages introuvables sur la page publique")
    return out


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
    # Semaines voisines (si Forex Factory les publie) : seulement pour mémoriser les derniers chiffres connus, sans les afficher
    extra = []
    for key in ("nextweek", "lastweek"):
        try:
            rows = json.loads(http("ff_%s.json" % key, "https://nfs.faireconomy.media/ff_calendar_%s.json" % key, tries=1, timeout=20))
            for r in rows if isinstance(rows, list) else []:
                extra.append({"t": (r.get("title") or "")[:90], "c": r.get("country") or "", "d": r.get("date") or "", "p": r.get("previous") or "", "f": r.get("forecast") or "", "a": r.get("actual") or ""})
            DIAG["ff_" + key] = "%d événements" % len(rows)
        except Exception as e:
            DIAG["ff_" + key] = fr_err(e)[:60]
    data["calendar_extra"] = extra
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
            ms, cursor = [], ""
            for page in range(8):
                prm = {"series_ticker": series, "status": "open", "limit": "200"}
                if cursor:
                    prm["cursor"] = cursor
                url = "https://api.elections.kalshi.com/trade-api/v2/markets?" + urllib.parse.urlencode(prm)
                j = json.loads(http("kalshi_%s.json" % series if page == 0 else "kalshi_%s_%d.json" % (series, page), url, tries=2))
                ms += j.get("markets") or []
                cursor = j.get("cursor") or ""
                if not cursor or not j.get("markets"):
                    break
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


# ---------------------------------------------------------------- Vidéos (flux publics YouTube, sans clé)
# 30 oct. : Zonebourse retiré (chaîne trouvée inactive depuis 2010), Reuters retiré (surtout politique).
VIDEO_CHANNELS = [("BFM Business", "fr", "BFMBusiness"), ("Bloomberg Television", "en", "markets"),
                  ("CNBC Television", "en", "CNBCtelevision"), ("Kitco News", "en", "kitco")]
YT = "{http://www.youtube.com/xml/schemas/2015}"


def do_videos(data):
    vids, ids = [], dict(PREVIOUS.get("video_ids") or {})
    old = PREVIOUS.get("videos") or []

    def one(ch):
        name, lang, handle = ch
        try:
            cid = ids.get(handle)
            if not cid:  # identifiant mémorisé après la première découverte : ensuite on va directement au flux
                page = http("yt_%s.html" % handle, "https://www.youtube.com/@%s" % handle, tries=2, timeout=25,
                            headers={"Cookie": "CONSENT=YES+1", "Accept-Language": "fr-FR,fr;q=0.9"})
                m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page) or re.search(r'channel_id=(UC[\w-]{22})', page)
                if not m:
                    raise ValueError("identifiant de chaîne introuvable")
                cid = m.group(1)
                with LOCK:
                    ids[handle] = cid
            feed = http("yt_%s.xml" % handle, "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid, tries=2, timeout=25)
            root = ET.fromstring(feed.encode("utf-8"))
            got = []
            for e in list(root.iter(ATOM + "entry"))[:6]:
                vid = e.find(YT + "videoId")
                t = e.find(ATOM + "title")
                d = e.find(ATOM + "published")
                if vid is not None and t is not None:
                    got.append({"ch": name, "lang": lang, "id": vid.text, "t": (t.text or "")[:150], "d": (d.text or "")[:10] if d is not None else None})
            if not got:
                raise ValueError("flux vide")
            with LOCK:
                vids.extend(got)
            note("yt." + handle, "Vidéos " + name, "news", True, max(g["d"] or "" for g in got) or None, url="https://www.youtube.com/@" + handle)
        except Exception as e:
            keep = [v for v in old if v.get("ch") == name]
            if keep:  # on garde les dernières vidéos connues de la chaîne
                with LOCK:
                    vids.extend(keep)
                note("yt." + handle, "Vidéos " + name + " — dernières vidéos connues conservées", "news", True, max(v.get("d") or "" for v in keep) or None, url="https://www.youtube.com/@" + handle)
            else:
                note("yt." + handle, "Vidéos " + name, "news", False, err=e, url="https://www.youtube.com/@" + handle)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, VIDEO_CHANNELS))
    vids.sort(key=lambda v: v["d"] or "", reverse=True)
    data["videos"] = vids[:36]
    data["video_ids"] = ids


# ---------------------------------------------------------------- Trafic maritime : détroits stratégiques (FMI PortWatch)
def do_shipping(data):
    url0 = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
    lab = "Trafic des détroits stratégiques (FMI PortWatch)"
    try:
        d0 = NOW - timedelta(days=70)
        flds = "date,portname,n_total,n_tanker,n_container,capacity"
        variants = [  # chaque variante est contrôlée : il faut des données de moins de 30 jours
            {"where": "year >= %d" % d0.year, "orderByFields": "date DESC", "resultRecordCount": "2000"},
            {"where": "year >= %d AND month >= %d" % (NOW.year, max(1, NOW.month - 1)) if NOW.month > 1 else "year >= %d" % NOW.year, "orderByFields": "date DESC", "resultRecordCount": "2000"},
            {"where": "1=1", "orderByFields": "date DESC", "resultRecordCount": "2000"},
            {"where": "1=1", "orderByFields": "OBJECTID DESC", "resultRecordCount": "2000"},
            {"where": "1=1", "orderByFields": "FID DESC", "resultRecordCount": "2000"},
            {"where": "year >= %d" % d0.year, "orderByFields": "date DESC", "resultRecordCount": "2000"},
            {"where": "(year > %d) OR (year = %d AND month >= %d)" % (d0.year, d0.year, d0.month), "resultRecordCount": "2000"},
        ]
        feats, errs = None, []
        for k, v in enumerate(variants):
            try:
                v = dict(v, outFields=flds, returnGeometry="false", f="json")
                jj = json.loads(http("portwatch.json" if k == 0 else "portwatch_%d.json" % k, url0 + "?" + urllib.parse.urlencode(v), timeout=40, tries=1))
                if jj.get("error"):
                    raise ValueError(str(jj["error"].get("message"))[:60])
                got = jj.get("features") or []
                dd = [pw_date((x.get("attributes") or {}).get("date")) for x in got]
                dd = [d for d in dd if d]
                newest_ = max(dd) if dd else ""
                if not got or ageDaysPy(newest_) > 30:
                    raise ValueError("lignes trop anciennes ou vides (%s)" % (newest_ or "aucune date"))
                nports = len(set((x.get("attributes") or {}).get("portname") for x in got))
                if nports < 20:  # le serveur range ses lignes détroit par détroit : il faut couvrir tous les passages
                    raise ValueError("seulement %d passage(s) dans la réponse" % nports)
                feats = got
                DIAG["portwatch_variante"] = k
                break
            except Exception as e:
                errs.append("variante %d : %s" % (k, fr_err(e)))
        DIAG["portwatch_erreurs"] = errs
        if feats is None:
            raise ValueError("aucune variante de requête n'a donné de données récentes")
        by = {}
        for f in feats:
            a_ = f.get("attributes") or {}
            d = pw_date(a_.get("date"))
            if not d:
                continue
            by.setdefault(a_.get("portname"), []).append([d, a_.get("n_total"), a_.get("n_tanker"), a_.get("n_container"), a_.get("capacity")])
        out, newest = {}, ""
        for name, rows in by.items():
            if not name:
                continue
            rows.sort()
            out[name] = rows[-60:]
            newest = max(newest, rows[-1][0])
        DIAG["portwatch"] = {"lignes": len(feats), "passages": len(out), "dernier_jour": newest}
        if not out:
            raise ValueError("aucune donnée")
        if ageDaysPy(newest) > 30:  # garde-fou : jamais de vieilles données présentées comme récentes
            raise ValueError("données reçues trop anciennes (%s)" % newest)
        data["shipping"] = {"src": "UN Global Platform ; FMI PortWatch (portwatch.imf.org)", "asof": newest, "c": out}
        note("imf.portwatch", lab, "weekly", True, newest, url="https://portwatch.imf.org/")
    except Exception as e:
        prev = PREVIOUS.get("shipping")
        if prev and prev.get("asof") and ageDaysPy(prev["asof"]) <= 30:
            data["shipping"] = prev  # on garde les dernières données valides, avec leur vraie date
            note("imf.portwatch", lab + " — dernières données valides conservées", "weekly", True, prev["asof"], url="https://portwatch.imf.org/")
        else:
            note("imf.portwatch", lab, "weekly", False, err=e, url="https://portwatch.imf.org/")


def pw_date(v):
    """PortWatch donne la date soit en millisecondes, soit en texte (« 2026-10-25 … » ou « 10/25/2026 »)."""
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, timezone.utc).strftime("%Y-%m-%d")
    t = str(v or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
    return None


def ageDaysPy(d):
    try:
        return (NOW.date() - date.fromisoformat(str(d)[:10])).days
    except Exception:
        return 9999


# ---------------------------------------------------------------- Sentiment des particuliers : widget gratuit Dukascopy (vérifié par le robot)
DUKA_CANDIDATES = [
    "https://freeserv.dukascopy.com/2.0/?path=swfx_sentiment_index/index&lang=fr&showHeader=false",
    "https://freeserv.dukascopy.com/2.0/?path=sentiment_index/index&lang=fr&showHeader=false",
]


def do_sentiment_widget(data):
    """On n'affiche le widget que si une adresse répond ET contient bien un widget de sentiment."""
    tried = []
    for i, u in enumerate(DUKA_CANDIDATES):
        try:
            t = http("duka_%d.html" % i, u, tries=1, timeout=20)
            ok = bool(re.search(r"sentiment", t, re.I)) and not re.search(r"not found|404|error", t[:400], re.I)
            tried.append([u, "ok" if ok else "contenu inattendu"])
            if ok:
                data["sentiment_widget"] = {"url": u, "checked": TODAY}
                note("duka.widget", "Widget de sentiment Dukascopy (vérifié)", "daily", True, TODAY, url="https://www.dukascopy.com/swiss/english/marketwatch/sentiment/")
                DIAG["dukascopy"] = tried
                return
        except Exception as e:
            tried.append([u, fr_err(e)])
    DIAG["dukascopy"] = tried
    note("duka.widget", "Widget de sentiment Dukascopy (vérifié)", "daily", False, err="aucune adresse de widget confirmée",
         url="https://www.dukascopy.com/swiss/english/marketwatch/sentiment/")


# ---------------------------------------------------------------- Courbes de taux : États-Unis (Trésor) et zone euro (BCE)
US_TENORS = [("1 Mo", "1M"), ("3 Mo", "3M"), ("6 Mo", "6M"), ("1 Yr", "1A"), ("2 Yr", "2A"), ("3 Yr", "3A"), ("5 Yr", "5A"), ("7 Yr", "7A"), ("10 Yr", "10A"), ("20 Yr", "20A"), ("30 Yr", "30A")]
EU_TENORS = [("SR_1M", "1M"), ("SR_3M", "3M"), ("SR_6M", "6M"), ("SR_1Y", "1A"), ("SR_2Y", "2A"), ("SR_3Y", "3A"), ("SR_5Y", "5A"), ("SR_7Y", "7A"), ("SR_10Y", "10A"), ("SR_20Y", "20A"), ("SR_30Y", "30A")]


def snap(series_by_tenor, labels):
    """Courbe du dernier jour, d'il y a ~1 semaine et ~1 mois (jours de marché)."""
    dates = sorted(set(d for s_ in series_by_tenor.values() for d, _ in s_))
    if not dates:
        return None
    out = {"tenors": labels}
    for key, back in (("now", 0), ("w1", 5), ("m1", 21), ("m3", 63)):
        if len(dates) <= back:
            continue
        d = dates[-1 - back]
        out[key] = {"d": d, "v": [dict(series_by_tenor.get(t, [])).get(d) for t in labels]}
    return out


def do_curves(data):
    C = {}
    try:
        t = treasury("daily_treasury_yield_curve", [x[0] for x in US_TENORS])
        sb = {lab: t.get(col, []) for col, lab in US_TENORS}
        c = snap(sb, [x[1] for x in US_TENORS])
        if not c:
            raise ValueError("courbe vide")
        c["hist2"] = sb["2A"][-130:]
        c["hist10"] = sb["10A"][-130:]
        C["us"] = c
        note("curve.us", "Courbe des taux US (Trésor américain)", "daily", True, c["now"]["d"], url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
    except Exception as e:
        note("curve.us", "Courbe des taux US (Trésor américain)", "daily", False, err=e, url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
    try:
        key = "B.U2.EUR.4F.G_N_A.SV_C_YM." + "+".join(x[0] for x in EU_TENORS)
        url = "https://data-api.ecb.europa.eu/service/data/YC/%s?format=csvdata&lastNObservations=135" % key
        sb = {}
        for r in csv.DictReader(io.StringIO(http("ecb_yc.csv", url, timeout=40))):
            tenor = (r.get("DATA_TYPE_FM") or r.get("KEY", "").split(".")[-1]).strip()
            lab = dict(EU_TENORS).get(tenor)
            v = num(r.get("OBS_VALUE"))
            if lab and v is not None:
                sb.setdefault(lab, []).append((r["TIME_PERIOD"], round(v, 3)))
        for k in sb:
            sb[k].sort()
        c = snap(sb, [x[1] for x in EU_TENORS])
        if not c:
            raise ValueError("courbe vide")
        c["hist2"] = sb.get("2A", [])[-130:]
        c["hist10"] = sb.get("10A", [])[-130:]
        C["eu"] = c
        note("curve.eu", "Courbe des taux zone euro (BCE, États notés AAA)", "daily", True, c["now"]["d"], url="https://data.ecb.europa.eu/")
    except Exception as e:
        note("curve.eu", "Courbe des taux zone euro (BCE, États notés AAA)", "daily", False, err=e, url="https://data.ecb.europa.eu/")
    data["curves"] = C


# ---------------------------------------------------------------- Analyses de marché et de banques (flux publics)
ANALYSIS_FEEDS = [
    ("ING THINK", ["https://think.ing.com/rss/", "https://think.ing.com/feed/", "https://think.ing.com/rss"]),
    # FXStreet retiré le 31 oct. 2026 : refuse les serveurs de GitHub (403).
    ("investingLive", ["https://investinglive.com/feed/news", "https://investinglive.com/feed/", "https://investinglive.com/rss"]),
]
BANKS = ["ING", "Goldman", "JPMorgan", "JP Morgan", "Morgan Stanley", "Citi", "UBS", "BofA", "Bank of America", "HSBC", "Barclays", "Commerzbank",
         "Danske", "MUFG", "Rabobank", "Nomura", "Deutsche Bank", "BNP", "Societe Generale", "Société Générale", "SocGen", "Wells Fargo", "Scotiabank",
         "RBC", "TD Securities", "Credit Agricole", "Crédit Agricole", "Natixis", "Nordea", "SEB", "Standard Chartered", "Westpac", "ANZ", "NAB",
         "Mizuho", "Julius Baer", "Pictet", "BBH", "Saxo", "Capital Economics", "Pantheon", "Macquarie", "Jefferies", "Bank of Montreal", "BMO"]
RX_USD = re.compile(r"\b(USD|US dollar|dollar|DXY|Fed|FOMC|Powell|Treasur|US yields?|EUR/USD|USD/JPY|GBP/USD)\b", re.I)
RX_EUR = re.compile(r"\b(EUR|euro|ECB|Lagarde|eurozone|euro area|Bund|EUR/USD|EUR/GBP)\b", re.I)
RX_BANKFX = re.compile(r"\b(FX|rates?|dollar|euro|Fed|ECB|yields?|inflation|eurozone|Treasur\w*|Bund|central bank|US|America|Europe\w*|German\w*|policy)\b", re.I)
RX_RATE = re.compile(r"\b(rate|rates|hike|cut|yield|yields|inflation|CPI|PCE|policy|hawk|dove|hawkish|dovish|pricing|basis points|bps)\b", re.I)


def do_analyses(data):
    items = []

    def one(feed):
        name, urls = feed
        err = None
        for k, url in enumerate(urls):
            try:
                got = parse_feed(http("an_%s_%d.xml" % (re.sub(r"\W", "", name), k), url, tries=1, timeout=25))
                if not got:
                    raise ValueError("flux vide")
                keep = 0
                for g in got:
                    t = g["t"]
                    usd, eur, rate = bool(RX_USD.search(t)), bool(RX_EUR.search(t)), bool(RX_RATE.search(t))
                    bank = next((b for b in BANKS if re.search(r"\b" + re.escape(b) + r"\b", t)), None)
                    if name == "ING THINK":  # recherche de banque : on garde changes, taux et macro US / zone euro
                        bank = bank or "ING"
                        if RX_BANKFX.search(t):
                            usd = usd or bool(re.search(r"\b(US|America|Fed|dollar|Treasur)", t, re.I))
                            eur = eur or bool(re.search(r"\b(euro|ECB|Europe|German|Bund|eurozone)", t, re.I))
                            rate = rate or bool(re.search(r"rates?|yield|Fed|ECB|central bank|policy", t, re.I))
                            if not (usd or eur):
                                usd = eur = False
                            g.update({"src": name, "usd": usd, "eur": eur, "bank": bank, "rate": rate})
                            with LOCK:
                                items.append(g)
                            keep += 1
                        continue
                    if (usd or eur) and (rate or bank):
                        g.update({"src": name, "usd": usd, "eur": eur, "bank": bank, "rate": rate})
                        with LOCK:
                            items.append(g)
                        keep += 1
                note("an." + re.sub(r"\W", "", name).lower(), "Analyses : " + name, "news", True,
                     max((g["d"] for g in got if g["d"]), default=TODAY), url=url)
                DIAG.setdefault("analyses", {})[name] = {"url": url, "articles": len(got), "retenus": keep}
                return
            except Exception as e:
                err = "%s (%s)" % (fr_err(e), url)
        note("an." + re.sub(r"\W", "", name).lower(), "Analyses : " + name, "news", False, err=err, url=urls[0])
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, ANALYSIS_FEEDS))
    seen, out = set(), []
    for it in sorted(items, key=lambda i: i["d"] or "", reverse=True):
        k = it["t"].lower()[:80]
        if k not in seen:
            seen.add(k)
            out.append(it)
    data["analyses"] = out[:80]


# ---------------------------------------------------------------- Sentiment des particuliers : API officielle Myfxbook (compte gratuit)
def do_retail(data):
    """Identifiants lus dans les secrets GitHub (MYFXBOOK_EMAIL, MYFXBOOK_PASSWORD) : jamais écrits dans les fichiers ni le journal.
    Secrets créés par l'utilisateur le 31 oct. 2026 (vérifiés à 01 h 08)."""
    lab, url_info = "Sentiment des particuliers (Myfxbook)", "https://www.myfxbook.com/community/outlook"
    em, pw = os.environ.get("MYFXBOOK_EMAIL", "").strip(), os.environ.get("MYFXBOOK_PASSWORD", "").strip("\r\n")  # retour à la ligne collé par erreur
    # Diagnostic sans jamais révéler le contenu : seulement « présent » ou « vide »
    DIAG["myfxbook_secrets"] = {"MYFXBOOK_EMAIL": "présent" if em else "vide", "MYFXBOOK_PASSWORD": "présent" if pw else "vide",
                                "variables_vues": sorted(k for k in os.environ if k.upper().startswith("MYFX"))}
    prev = PREVIOUS.get("retail")
    if not (em and pw) and not MOCK:
        try:
            out = retail_public(data)
            data["retail"] = {"asof": NOW.strftime("%Y-%m-%dT%H:%MZ"), "src": "Myfxbook Community Outlook (page publique)", "s": out}
            note("myfxbook", lab + " : page publique, sans identifiants", "daily", True, TODAY, url=url_info)
            return
        except Exception as e:
            DIAG["mfb_public"] = fr_err(e)[:100]
        if prev:
            data["retail"] = prev
        note("myfxbook", lab, "config", False, err="à configurer : compte Myfxbook gratuit + secrets GitHub (voir le guide)", url=url_info)
        return
    session = None
    try:
        if MOCK:
            session = "mock"
        else:
            j, tried = None, []
            # essais : e-mail tel quel, puis en minuscules ; mot de passe tel quel, puis sans espaces autour
            for e_, p_ in ((em, pw), (em.lower(), pw), (em.lower(), pw.strip())):
                if (e_, p_) in tried:
                    continue
                tried.append((e_, p_))
                j = json.loads(http("mfb_login.json", "https://www.myfxbook.com/api/login.json?" + urllib.parse.urlencode({"email": e_, "password": p_}), tries=1, timeout=30))
                if not j.get("error"):
                    break
            DIAG["myfxbook_essais"] = len(tried)
            if j.get("error"):
                raise ValueError("connexion refusée par Myfxbook : " + str(j.get("message", ""))[:80])
            session = urllib.parse.unquote(j.get("session") or "")
        j = json.loads(http("mfb_outlook.json", "https://www.myfxbook.com/api/get-community-outlook.json?session=" + urllib.parse.quote(session or ""), tries=2, timeout=30))
        if j.get("error"):
            raise ValueError("Myfxbook : " + str(j.get("message", ""))[:80])
        out = {}
        for x in j.get("symbols") or []:
            n = str(x.get("name", "")).upper()
            lp, sp = num(x.get("longPercentage")), num(x.get("shortPercentage"))
            if n and lp is not None and sp is not None:
                out[n] = {"long": round(lp, 1), "short": round(sp, 1), "lpos": x.get("longPositions"), "spos": x.get("shortPositions"),
                          "lvol": num(x.get("longVolume")), "svol": num(x.get("shortVolume")), "alp": num(x.get("avgLongPrice")), "asp": num(x.get("avgShortPrice"))}
        if not out:
            raise ValueError("aucune paire reçue")
        data["retail"] = {"asof": NOW.strftime("%Y-%m-%dT%H:%MZ"), "src": "Myfxbook Community Outlook", "s": out}
        note("myfxbook", lab, "daily", True, TODAY, url=url_info)
    except Exception as e:
        if prev:
            data["retail"] = prev  # on garde les derniers chiffres valides, avec leur date
        msg = fr_err(e)
        if em:
            msg = msg.replace(em, "***")
        if pw:
            msg = msg.replace(pw, "***")
        note("myfxbook", lab, "daily", False, err=msg, url=url_info)
    finally:
        if session and not MOCK:
            try:
                http("mfb_logout.json", "https://www.myfxbook.com/api/logout.json?session=" + urllib.parse.quote(session), tries=1, timeout=15)
            except Exception:
                pass


# ---------------------------------------------------------------- Réserves des banques centrales et or (Banque mondiale, annuel)
def do_reserves(data):
    res, newest = {}, ""
    for code, key in (("FI.RES.TOTL.CD", "tot"), ("FI.RES.XGLD.CD", "xg")):
        url = "https://api.worldbank.org/v2/country/all/indicator/%s?format=json&date=2018:%d&per_page=20000" % (code, NOW.year)
        try:
            j = json.loads(http("wbr_%s.json" % key, url, tries=2, timeout=60))
            n = 0
            for r in (j[1] if isinstance(j, list) and len(j) > 1 else []) or []:
                iso, v = r.get("countryiso3code"), num(r.get("value"))
                if iso and v is not None:
                    with LOCK:
                        res.setdefault(iso, {"n": (r.get("country") or {}).get("value")}).setdefault(key, []).append([int(r["date"]), round(v / 1e9, 2)])
                    n += 1
                    newest = max(newest, r["date"])
            if not n:
                raise ValueError("aucune valeur")
            note("wb.res." + key, ("Réserves totales, or compris" if key == "tot" else "Réserves hors or") + " (Banque mondiale, annuel)", "annual", True,
                 newest + "-12-31", url="https://data.worldbank.org/indicator/" + code)
        except Exception as e:
            note("wb.res." + key, ("Réserves totales, or compris" if key == "tot" else "Réserves hors or") + " (Banque mondiale, annuel)", "annual", False,
                 err=e, url="https://data.worldbank.org/indicator/" + code)
    for iso in res:
        for k in ("tot", "xg"):
            if k in res[iso]:
                res[iso][k].sort()
    data["reserves"] = res


# ---------------------------------------------------------------- Surprises économiques : prévision (Forex Factory) contre chiffre officiel
# Le robot mémorise chaque prévision de la semaine, puis la compare au chiffre officiel quand il paraît.
SURP_MAP = [("USD", r"^CPI y/y$", "cpi", "IPC a/a", " %"), ("USD", r"^Unemployment Rate$", "unemp", "Chômage", " %"),
            ("USD", r"^Non-Farm Employment Change$", "nfp", "Créations d'emplois (NFP)", " k"),
            ("GBP", r"^CPI y/y$", "cpi", "IPC a/a", " %"), ("CAD", r"^CPI y/y$", "cpi", "IPC a/a", " %"),
            ("EUR", r"^(CPI Flash Estimate y/y|Final CPI y/y)$", "cpi", "IPC a/a", " %")]


def actual_for(data, cur, key):
    D, N = data.get("derived") or {}, data.get("national") or {}
    if cur == "USD":
        x = {"cpi": D.get("us_cpi"), "unemp": D.get("us_unemp"), "nfp": D.get("us_nfp")}.get(key)
    else:
        x = N.get({"GBP": "cpi_GB", "CAD": "cpi_CA", "EUR": "cpi_EZ"}[cur]) if key == "cpi" else None
    return x


def do_surprises(data):
    st = {"forecasts": [], "results": []}
    try:
        with open(OUT_SURP, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        pass
    have = set((x["k"], x["period"]) for x in st["forecasts"])
    done = set((x["k"], x["period"]) for x in st["results"])
    for e in ((data.get("calendar") or {}).get("events") or []):
        for cur, rx, key, lab, unit in SURP_MAP:
            if e.get("c") == cur and re.match(rx, e.get("t") or "") and e.get("f"):
                fv = num(re.sub(r"[^\d.\-]", "", e["f"]))
                if fv is None:
                    continue
                d = (e.get("d") or "")[:10]
                y, m = int(d[:4]), int(d[5:7])
                py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
                period = "%d-%02d" % (py, pm)
                k = cur + "|" + key
                if (k, period) not in have:
                    st["forecasts"].append({"k": k, "c": cur, "key": key, "lab": lab, "u": unit, "d": d, "period": period, "f": fv, "t": e.get("t")})
                    have.add((k, period))
    for fc in st["forecasts"]:
        if (fc["k"], fc["period"]) in done:
            continue
        a = actual_for(data, fc["c"], fc["key"])
        if a and str(a.get("asof", ""))[:7] == fc["period"] and a.get("v") is not None:
            sp = round(a["v"] - fc["f"], 2)
            st["results"].append({"k": fc["k"], "c": fc["c"], "lab": fc["lab"], "u": fc["u"], "period": fc["period"], "d": fc["d"], "f": fc["f"], "a": a["v"],
                                  "s": sp, "good": (sp > 0) if fc["key"] != "unemp" else (sp < 0)})
            done.add((fc["k"], fc["period"]))
    st["forecasts"] = st["forecasts"][-200:]
    st["results"] = sorted(st["results"], key=lambda r: r["d"])[-200:]
    st["generated"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT_SURP, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    note("surp", "Surprises économiques (prévisions mémorisées : %d, résultats : %d)" % (len(st["forecasts"]), len(st["results"])), "weekly", True, TODAY,
         url="https://www.forexfactory.com/calendar")


# ---------------------------------------------------------------- Projections de la Fed (SEP, « dot plot ») et veille des projections de la BCE
from html.parser import HTMLParser


class Tables(HTMLParser):
    """Lit tous les tableaux d'une page : [(titre précédent, [[cellules]])]."""
    def __init__(self):
        super().__init__()
        self.tables, self.cur, self.row, self.cell, self.last_head, self.in_head, self.buf = [], None, None, None, "", False, ""

    def handle_starttag(self, tag, a):
        if tag in ("h3", "h4", "h5", "caption"):
            self.in_head, self.buf = True, ""
        elif tag == "table":
            self.cur = []
        elif tag == "tr" and self.cur is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = ""

    def handle_endtag(self, tag):
        if tag in ("h3", "h4", "h5", "caption") and self.in_head:
            self.in_head = False
            if self.buf.strip():
                self.last_head = re.sub(r"\s+", " ", self.buf).strip()
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.cur is not None:
            if self.row:
                self.cur.append(self.row)
            self.row = None
        elif tag == "table" and self.cur is not None:
            self.tables.append((self.last_head, self.cur))
            self.cur = None

    def handle_data(self, d):
        if self.in_head:
            self.buf += d
        if self.cell is not None:
            self.cell += d


def do_projections(data):
    P = {}
    # --- Fed : dernière page « fomcprojtabl » trouvée sur le calendrier du FOMC
    try:
        cal = http("fomc_cal.html", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", timeout=40)
        dates = sorted(set(re.findall(r"fomcprojtabl(\d{8})\.htm", cal)))
        if not dates:
            raise ValueError("aucune page de projections trouvée sur le calendrier")
        d8 = dates[-1]
        url = "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl%s.htm" % d8
        tp = Tables()
        tp.feed(http("fomc_sep.html", url, timeout=40))
        med, dots = None, None
        for head, rows in tp.tables:
            flat = " ".join(" ".join(r) for r in rows)
            if med is None and "Change in real GDP" in flat and "Federal funds rate" in flat:
                yrs = None
                for r in rows[:3]:
                    ys = [c for c in r if re.match(r"^(\d{4}|Longer run)$", c)]
                    if len(ys) >= 3:
                        yrs = ys[:len(ys) // 3] if len(ys) >= 9 else ys
                        break
                if not yrs:
                    continue
                n = len(yrs)
                med = {"years": yrs, "rows": {}}
                for r in rows:
                    lab = re.sub(r"\d+$", "", r[0]).strip() if r else ""
                    for key, rx in (("gdp", r"^Change in real GDP"), ("unemp", r"^Unemployment rate"), ("pce", r"^PCE inflation"),
                                    ("core", r"^Core PCE inflation"), ("ffr", r"^Federal funds rate")):
                        if re.match(rx, lab) and key not in med["rows"]:
                            vals = [num(c) for c in r[1:1 + n]]
                            prev_row = rows[rows.index(r) + 1] if rows.index(r) + 1 < len(rows) else []
                            prev = [num(re.sub(r"[()]", "", c)) for c in prev_row[1:1 + n]] if prev_row and re.search(r"projection", prev_row[0], re.I) else None
                            med["rows"][key] = {"v": vals, "prev": prev}
            if dots is None and re.search(r"Figure 2", head or "") or (dots is None and rows and re.match(r"^\d\.\d{3}$", (rows[1][0] if len(rows) > 1 and rows[1] else ""))):
                hdr = rows[0]
                yrs = [c for c in hdr[1:] if re.match(r"^(\d{4}|Longer run)$", c)]
                lev = []
                for r in rows[1:]:
                    x = num(r[0]) if r else None
                    if x is not None and 0 <= x < 15:
                        lev.append([x] + [int(num(c)) if num(c) is not None else 0 for c in r[1:1 + len(yrs)]])
                if yrs and lev:
                    dots = {"years": yrs, "levels": lev}
        if not med or not med["rows"].get("ffr"):
            raise ValueError("tableau des médianes illisible")
        P["fed"] = {"date": "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:]), "url": url, "median": med, "dots": dots}
        note("proj.fed", "Projections de la Fed (dot plot)", "quarterly", True, P["fed"]["date"], url=url)
    except Exception as e:
        note("proj.fed", "Projections de la Fed (dot plot)", "quarterly", False, err=e, url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    # --- BCE : date de la dernière publication des projections (les chiffres sont saisis et sourcés sur le site)
    try:
        page = http("ecb_proj.html", "https://www.ecb.europa.eu/press/projections/html/index.en.html", timeout=40)
        links = re.findall(r'href="(/press/projections/html/ecb\.projections(\d{6})_[^"]+\.en\.html)"', page)
        if not links:
            raise ValueError("aucune publication trouvée")
        links.sort(key=lambda x: x[1])
        path, ym = links[-1]
        P["ecb"] = {"latest": ym[:4] + "-" + ym[4:], "url": "https://www.ecb.europa.eu" + path}
        note("proj.ecb", "Projections de la BCE (veille des publications)", "quarterly", True, ym[:4] + "-" + ym[4:] + "-01", url=P["ecb"]["url"])
    except Exception as e:
        note("proj.ecb", "Projections de la BCE (veille des publications)", "quarterly", False, err=e, url="https://www.ecb.europa.eu/press/projections/html/index.en.html")
    data["projections"] = P


# ---------------------------------------------------------------- Historique macro depuis 2001 (fichier séparé)
def do_history(force=False):
    """Inflation et croissance annuelles de tous les pays depuis 2001 (Banque mondiale). Recalcul une fois par semaine."""
    if not force and os.path.exists(OUT_HIST):
        try:
            with open(OUT_HIST, encoding="utf-8") as f:
                old = json.load(f)
            if (NOW - datetime.strptime(old["generated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days < 7 and old.get("c"):
                note("wb.hist", "Historique 2001+ inflation et croissance (Banque mondiale, hebdo)", "weekly", True, old["generated"][:10],
                     url="https://data.worldbank.org/")
                return
        except Exception:
            pass
    res, newest = {}, ""
    for code, key in (("FP.CPI.TOTL.ZG", "inf"), ("NY.GDP.MKTP.KD.ZG", "gdp")):
        url = "https://api.worldbank.org/v2/country/all/indicator/%s?format=json&date=2001:%d&per_page=20000" % (code, NOW.year)
        j = json.loads(http("wbh_%s.json" % key, url, tries=2, timeout=60))
        for r in (j[1] if isinstance(j, list) and len(j) > 1 else []) or []:
            iso, v = r.get("countryiso3code"), num(r.get("value"))
            if iso and v is not None:
                res.setdefault(iso, {}).setdefault(key, []).append([int(r["date"]), round(v, 2)])
                newest = max(newest, r["date"])
    for iso in res:
        for k in res[iso]:
            res[iso][k].sort()
    if not res:
        raise ValueError("aucune donnée")
    with open(OUT_HIST, "w", encoding="utf-8") as f:
        json.dump({"generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "src": "Banque mondiale (FP.CPI.TOTL.ZG, NY.GDP.MKTP.KD.ZG)", "c": res},
                  f, ensure_ascii=False, separators=(",", ":"))
    note("wb.hist", "Historique 2001+ inflation et croissance (Banque mondiale, hebdo)", "weekly", True, newest + "-12-31", url="https://data.worldbank.org/")


# ---------------------------------------------------------------- Programme principal
# ---------------------------------------------------------------- Mémoire des réactions du marché autour des publications
# Pour chaque publication importante : variation de l'or, du dollar (DXY), de l'EUR/USD, du Nasdaq 100 et du taux US à 10 ans
# 15 min, 1 h et 4 h après l'heure de sortie. Bougies Yahoo Finance (non officiel, gratuit) ; secours : Binance (or via PAXG, EUR/USDT).
OUT_REACT = os.environ.get("MF_OUT_REACT", os.path.join(ROOT, "data", "reactions.json"))
REACT_ASSETS = [("gold", "GC=F", "PAXGUSDT"), ("dxy", "DX-Y.NYB", None), ("eur", "EURUSD=X", "EURUSDT"), ("nq", "NQ=F", None), ("us10", "^TNX", None)]
REACT_TYPES = [("pce", r"PCE"), ("cpi", r"CPI|HICP|Consumer Price"), ("ppi", r"PPI|Producer Price"), ("nfp", r"Non-Farm|Employment Situation|Employment Change"),
               ("unemp", r"Unemployment Rate"), ("claims", r"Unemployment Claims|Jobless"), ("gdp", r"GDP"), ("retail", r"Retail Sales"),
               ("ism", r"ISM|PMI"), ("rate", r"Rate Statement|Interest Rate|Cash Rate|Bank Rate|Policy Rate|Overnight Rate|Federal Funds|Main Refinancing|Monetary Policy"),
               ("presser", r"Press Conference"), ("conf", r"Confidence|Sentiment"), ("jolts", r"JOLTS"), ("wages", r"Earnings|Wage")]


def react_type(title):
    for k, rx in REACT_TYPES:
        if re.search(rx, title or "", re.I):
            return k
    return "autre"


def yahoo_bars(sym, interval, rng):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s&includePrePost=true" % (urllib.parse.quote(sym), interval, rng)
    j = json.loads(http("yh_%s_%s.json" % (re.sub(r"\W", "", sym), interval), url, tries=2, timeout=25))
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = [(t, o, c) for t, o, c in zip(r.get("timestamp") or [], q.get("open") or [], q.get("close") or []) if o is not None and c is not None]
    if len(out) < 50:
        raise ValueError("trop peu de bougies")
    return out


def binance_bars(sym, start):
    out, t = [], start
    while t < time.time() * 1000 - 9e5 and len(out) < 6000:
        url = "https://data-api.binance.vision/api/v3/klines?symbol=%s&interval=15m&startTime=%d&limit=1000" % (sym, t)
        rows = json.loads(http("bn_%s_%d.json" % (sym, t), url, tries=2))
        if not rows:
            break
        out += [(int(k[0]) // 1000, float(k[1]), float(k[4])) for k in rows]
        t = int(rows[-1][0]) + 900000
        if len(rows) < 1000:
            break
    if len(out) < 50:
        raise ValueError("trop peu de bougies")
    return out


def move_after(bars, step, t, mins, rate=False):
    """Variation entre l'heure t (secondes) et t + mins : % (ou points de base pour un taux)."""
    base = None
    for st, o, c in bars:
        if st + step <= t:
            if t - (st + step) <= step:
                base = c
        elif st <= t < st + step:
            base = base if base is not None else o
            break
        else:
            break
    if base is None:
        return None
    for st, o, c in bars:
        if st + step >= t + mins * 60:
            if st > t + mins * 60:
                return None  # trou dans les données (week-end, fermeture)
            return round((c - base) * 100, 1) if rate else round((c / base - 1) * 100, 3)
    return None


def bls_schedule():
    """Dates et heures officielles des publications IPC et emploi (calendrier BLS, ~2 ans)."""
    txt = None
    for hd in ({"User-Agent": "macro-forex robot (https://github.com/philippembulu1-dot/macro-forex)"}, None):  # le BLS demande un robot identifié
        try:
            txt = http("bls.ics", "https://www.bls.gov/schedule/news_release/bls.ics", tries=1, timeout=25, headers=hd)
            break
        except Exception as e:
            last = e
    if txt is None:
        raise last
    evs = []
    for blk in txt.split("BEGIN:VEVENT")[1:]:
        summ = re.search(r"SUMMARY:(.*)", blk)
        dt = re.search(r"DTSTART(?:;TZID=([^:]+))?:(\d{8}T\d{6})(Z?)", blk)
        if not summ or not dt:
            continue
        s_ = summ.group(1).strip()
        typ = "cpi" if re.match(r"Consumer Price Index", s_) else "nfp" if re.match(r"Employment Situation", s_) else None
        if not typ:
            continue
        d = datetime.strptime(dt.group(2), "%Y%m%dT%H%M%S")
        if dt.group(3) == "Z":
            d = d.replace(tzinfo=timezone.utc)
        else:
            try:
                from zoneinfo import ZoneInfo
                d = d.replace(tzinfo=ZoneInfo(dt.group(1) or "America/New_York")).astimezone(timezone.utc)
            except Exception:
                continue
        evs.append({"d": d.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": "USD", "t": "CPI m/m" if typ == "cpi" else "Non-Farm Employment Change", "type": typ, "i": "High", "src": "BLS"})
    if not evs:
        raise ValueError("aucune publication lue")
    return evs


def do_reactions(data):
    st = {"events": []}
    try:
        with open(OUT_REACT, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        pass
    have = {e["id"]: e for e in st.get("events", [])}
    now = time.time()
    cands = []
    for e in ((data.get("calendar") or {}).get("events") or []):
        if e.get("i") != "High" or e.get("c") not in ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"):
            continue
        try:
            t = datetime.fromisoformat(e["d"]).astimezone(timezone.utc)
        except Exception:
            continue
        cands.append({"d": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": e["c"], "t": e["t"], "type": react_type(e["t"]), "i": "High", "f": e.get("f") or "", "p": e.get("p") or "", "a": e.get("a") or "", "src": "FF"})
    # Calendrier BLS (rattrapage 2 ans) : testé le 31 oct. 2026, le site refuse les serveurs de GitHub (403). Mémoire construite au fil des publications.
    todo = []
    for c in cands:
        ts = datetime.strptime(c["d"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        if ts > now - 4.5 * 3600:
            continue  # pas encore 4 h de recul
        c["id"] = c["d"][:16] + "|" + c["c"] + "|" + c["type"]
        old = have.get(c["id"])
        fine_ok = old and (old.get("rf") is not None or ts < now - 55 * 86400)
        if old and old.get("r") and all(v and v[2] is not None for v in old["r"].values()) and fine_ok:
            for k in ("f", "p", "a"):  # complète la prévision si l'ancienne entrée n'en avait pas
                if c.get(k) and not old.get(k):
                    old[k] = c[k]
            continue
        c["ts"] = ts
        todo.append(c)
    bars, src, fbars = {}, {}, {}
    if todo:
        first = min(c["ts"] for c in todo)
        for key, ysym, bsym in REACT_ASSETS:
            got = []
            for interval, rng, step in (("15m", "60d", 900), ("60m", "730d", 3600)):
                if interval == "60m" and first > now - 55 * 86400:
                    continue
                try:
                    got.append((step, yahoo_bars(ysym, interval, rng)))
                    src[key] = "Yahoo Finance"
                except Exception as e:
                    DIAG["react_" + key + "_" + interval] = fr_err(e)[:120]
            if not got and bsym:
                try:
                    got.append((900, binance_bars(bsym, int((max(first, now - 59 * 86400) - 3600) * 1000))))
                    src[key] = "Binance (" + bsym + ")"
                except Exception as e:
                    DIAG["react_" + key + "_bn"] = fr_err(e)[:120]
            bars[key] = got
            fine = []
            for interval, rng, step in (("1m", "7d", 60), ("5m", "60d", 300)):
                if interval == "1m" and first < now - 6.5 * 86400 and max(c["ts"] for c in todo) < now - 6.5 * 86400:
                    continue
                try:
                    fine.append((step, yahoo_bars(ysym, interval, rng)))
                except Exception as e:
                    DIAG["react_" + key + "_" + interval] = fr_err(e)[:120]
            fbars[key] = fine
        n_ok = sum(1 for k in bars if bars[k])
        note("react.prices", "Prix autour des publications (%d actifs sur %d : %s)" % (n_ok, len(REACT_ASSETS), ", ".join(sorted(set(src.values()))) or "aucun"),
             "daily", n_ok > 0, TODAY if n_ok else None, err=None if n_ok else "aucune source de prix", url="https://finance.yahoo.com/")
    for c in todo:
        r, gran = {}, None
        for key, _y, _b in REACT_ASSETS:
            for step, b in bars.get(key) or []:
                if b and b[0][0] <= c["ts"] - step:
                    v = [move_after(b, step, c["ts"], m, rate=(key == "us10")) for m in (15, 60, 240)]
                    if step == 3600:
                        v[0] = None  # bougies d'une heure : pas de mesure à 15 min
                    if any(x is not None for x in v):
                        r[key] = v
                        gran = min(gran or step, step)
                        break
        rf = {}
        for key, _y, _b in REACT_ASSETS:
            vals = [None, None, None]
            for step, b in fbars.get(key) or []:
                if not b or b[0][0] > c["ts"] - step:
                    continue
                for i, m in enumerate((1, 5, 30)):
                    if vals[i] is None and m * 60 >= step:
                        vals[i] = move_after(b, step, c["ts"], m, rate=(key == "us10"))
            if any(v is not None for v in vals):
                rf[key] = vals
        if r:
            ts = c.pop("ts")
            c["r"], c["g"] = r, ("15 min" if gran == 900 else "1 h")
            c["rf"] = rf  # 1, 5 et 30 min (bougies 1 min sur 7 jours, 5 min sur 60 jours)
            have[c["id"]] = c
        else:
            c.pop("ts", None)
    # rattache la surprise (chiffre officiel contre prévision) quand le registre l'a calculée
    try:
        with open(OUT_SURP, encoding="utf-8") as f:
            sp = json.load(f).get("results", [])
    except Exception:
        sp = []
    for e in have.values():
        for x in sp:
            if x["c"] == e["c"] and x["d"] == e["d"][:10] and x["k"].split("|")[1] == e["type"] and "s" not in e:
                e["s"], e["fv"], e["av"], e["u"] = x["s"], x["f"], x["a"], x["u"]
    evs = sorted(have.values(), key=lambda e: e["d"])[-1500:]
    out = {"generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "src": src or st.get("src", {}), "events": evs}
    with open(OUT_REACT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    note("react", "Mémoire des réactions du marché (%d publications enregistrées, %d nouvelles)" % (len(evs), sum(1 for c in todo if c.get("r"))), "daily",
         True, TODAY, url="https://finance.yahoo.com/")


# ---------------------------------------------------------------- Clôtures quotidiennes multi-actifs (résumé « Ce matin »)
DAILY = [("gold", "GC=F", "Or (contrat à terme)"), ("silver", "SI=F", "Argent"), ("nq", "NQ=F", "Nasdaq 100 (contrat à terme)"), ("es", "ES=F", "S&P 500 (contrat à terme)"),
         ("dxy", "DX-Y.NYB", "Dollar (DXY)"), ("us10", "^TNX", "Taux US 10 ans"), ("eur", "EURUSD=X", "EUR/USD"), ("gbp", "GBPUSD=X", "GBP/USD"),
         ("jpy", "JPY=X", "USD/JPY"), ("oil", "CL=F", "Pétrole WTI"), ("vix", "^VIX", "VIX"), ("btc", "BTC-USD", "Bitcoin")]


def do_daily(data):
    out, bad = {}, []
    for key, sym, name in DAILY:
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=6mo" % urllib.parse.quote(sym)
            r = json.loads(http("yd_%s.json" % key, url, tries=2, timeout=20))["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            rows = [[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), round(c, 5)] for t, c in zip(r.get("timestamp") or [], q.get("close") or []) if c is not None]
            if len(rows) < 25:
                raise ValueError("trop peu de clôtures")
            out[key] = {"n": name, "sym": sym, "c": rows[-130:], "live": (r.get("meta") or {}).get("regularMarketPrice")}
            if key == "gold":  # bougies complètes de l'or (ouverture, plus haut, plus bas) pour les pivots quand Binance est injoignable
                out[key]["ohlc"] = [[datetime.fromtimestamp(t_, timezone.utc).strftime("%Y-%m-%d"), round(o, 2), round(h, 2), round(l, 2)]
                                    for t_, o, h, l in zip(r.get("timestamp") or [], q.get("open") or [], q.get("high") or [], q.get("low") or []) if o and h and l][-130:]
        except Exception as e:
            bad.append(key)
            DIAG["daily_" + key] = fr_err(e)[:120]
            if (PREVIOUS.get("daily") or {}).get(key):
                out[key] = PREVIOUS["daily"][key]
    data["daily"] = out
    last = max((v["c"][-1][0] for v in out.values()), default=None)
    note("yahoo.daily", "Clôtures quotidiennes : or, Nasdaq, S&P, dollar, taux, devises, pétrole (Yahoo Finance, non officiel) %d/%d" % (len(DAILY) - len(bad), len(DAILY)),
         "daily", len(bad) < len(DAILY), last, err=("échec : " + ", ".join(bad)) if bad else None, url="https://finance.yahoo.com/")



# =====================================================================
# GÉOPOLITIQUE (31 oct. 2026) : équivalent gratuit d'un moniteur de risque pays.
# Sources vérifiées depuis GitHub le 31 oct. 2026 :
#  - GDELT 2.0 : événements codés toutes les 15 min (https://data.gdeltproject.org/gdeltv2/) ;
#  - indice de risque géopolitique de Caldara et Iacoviello (quotidien + 44 pays, mensuel) ;
#  - avis de voyage officiels des États-Unis (cadataapi.state.gov) ;
#  - séismes USGS ; titres Google Actualités.
# Les scores sont des calculs maison étiquetés comme tels : aucune note d'agence n'est simulée.
# =====================================================================
OUT_GEO = os.environ.get("MF_OUT_GEO", os.path.join(ROOT, "data", "geo-history.json"))
FIPS3 = dict((p[:2], p[2:]) for p in "AAABW ACATG AEARE AFAFG AGDZA AJAZE ALALB AMARM ANAND AOAGO AQASM ARARG ASAUS AUAUT AVAIA AYATA BABHR BBBRB BCBWA BDBMU BEBEL BFBHS BGBGD BHBLZ BKBIH BLBOL BMMMR BNBEN BOBLR BPSLB BRBRA BTBTN BUBGR BVBVT BXBRN BYBDI CACAN CBKHM CDTCD CELKA CFCOG CGCOD CHCHN CICHL CJCYM CKCCK CMCMR CNCOM COCOL CQMNP CSCRI CTCAF CUCUB CVCPV CWCOK CYCYP DADNK DJDJI DODMA DRDOM ECECU EGEGY EIIRL EKGNQ ENEST ERERI ESSLV ETETH EZCZE FGGUF FIFIN FJFJI FKFLK FMFSM FOFRO FPPYF FRFRA FSATF GAGMB GBGAB GGGEO GHGHA GIGIB GJGRD GKGGY GLGRL GMDEU GPGLP GQGUM GRGRC GTGTM GVGIN GYGUY GZWEPSE HAHTI HKHKG HMHMD HOHND HRHRV HUHUN ICISL IDIDN IMIMN ININD IOIOT IRIRN ISISR ITITA IVCIV IZIRQ JAJPN JEJEY JMJAM JOJOR KEKEN KGKGZ KNPRK KRKIR KSKOR KTCXR KUKWT KVXKX KZKAZ LALAO LELBN LGLVA LHLTU LILBR LOSVK LSLIE LTLSO LULUX LYLBY MAMDG MBMTQ MCMAC MDMDA MFMYT MGMNG MHMSR MIMWI MJMNE MKMKD MLMLI MNMCO MOMAR MPMUS MRMRT MTMLT MUOMN MVMDV MXMEX MYMYS MZMOZ NCNCL NENIU NFNFK NGNER NHVUT NINGA NLNLD NNSXM NONOR NPNPL NRNRU NSSUR NUNIC NZNZL ODSSD PAPRY PCPCN PEPER PKPAK PLPOL PMPAN POPRT PPPNG PSPLW PUGNB QAQAT REREU RIKVSRB RMMHL RNMAF ROROU RPPHL RQPRI RSRUS RWRWA SASAU SBSPM SCKNA SESYC SFZAF SGSEN SHSHN SISVN SLSLE SMSMR SNSGP SOSOM SPESP STLCA SUSDN SVJNSJM SWSWE SXSGS SYSYR SZCHE TBBLM TDTTO THTHA TITJK TKTCA TLTKL TNTON TOTGO TPSTP TSTUN TTTLS TUTUR TVTUV TWTWN TXTKM TZTZA UCCUW UGUGA UKGBR UPUKR USUSA UVBFA UYURY UZUZB VCVCT VEVEN VIVGB VMVNM VQVIR VTVAT WANAM WFWLF WIESH WSWSM WZSWZ YMYEM ZAZMB ZIZWE aUMI  ALA".split())
FIPS3.update({"WE": "PSE", "GZ": "PSE"})
ISO23 = dict((p[:2], p[2:]) for p in "ADAND AEARE AFAFG AGATG AIAIA ALALB AMARM AOAGO AQATA ARARG ASASM ATAUT AUAUS AWABW AXALA AZAZE BABIH BBBRB BDBGD BEBEL BFBFA BGBGR BHBHR BIBDI BJBEN BLBLM BMBMU BNBRN BOBOL BQBES BRBRA BSBHS BTBTN BVBVT BWBWA BYBLR BZBLZ CACAN CCCCK CDCOD CFCAF CGCOG CHCHE CICIV CKCOK CLCHL CMCMR CNCHN COCOL CRCRI CUCUB CVCPV CWCUW CXCXR CYCYP CZCZE DEDEU DJDJI DKDNK DMDMA DODOM DZDZA ECECU EEEST EGEGY EHESH ERERI ESESP ETETH FIFIN FJFJI FKFLK FMFSM FOFRO FRFRA GAGAB GBGBR GDGRD GEGEO GFGUF GGGGY GHGHA GIGIB GLGRL GMGMB GNGIN GPGLP GQGNQ GRGRC GSSGS GTGTM GUGUM GWGNB GYGUY HKHKG HMHMD HNHND HRHRV HTHTI HUHUN IDIDN IEIRL ILISR IMIMN ININD IOIOT IQIRQ IRIRN ISISL ITITA JEJEY JMJAM JOJOR JPJPN KEKEN KGKGZ KHKHM KIKIR KMCOM KNKNA KPPRK KRKOR KWKWT KYCYM KZKAZ LALAO LBLBN LCLCA LILIE LKLKA LRLBR LSLSO LTLTU LULUX LVLVA LYLBY MAMAR MCMCO MDMDA MEMNE MFMAF MGMDG MHMHL MKMKD MLMLI MMMMR MNMNG MOMAC MPMNP MQMTQ MRMRT MSMSR MTMLT MUMUS MVMDV MWMWI MXMEX MYMYS MZMOZ NANAM NCNCL NENER NFNFK NGNGA NINIC NLNLD NONOR NPNPL NRNRU NUNIU NZNZL OMOMN PAPAN PEPER PFPYF PGPNG PHPHL PKPAK PLPOL PMSPM PNPCN PRPRI PSPSE PTPRT PWPLW PYPRY QAQAT REREU ROROU RSSRB RURUS RWRWA SASAU SBSLB SCSYC SDSDN SESWE SGSGP SHSHN SISVN SJSJM SKSVK SLSLE SMSMR SNSEN SOSOM SRSUR SSSSD STSTP SVSLV SXSXM SYSYR SZSWZ TCTCA TDTCD TFATF TGTGO THTHA TJTJK TKTKL TLTLS TMTKM TNTUN TOTON TRTUR TTTTO TVTUV TWTWN TZTZA UAUKR UGUGA UMUMI USUSA UYURY UZUZB VAVAT VCVCT VEVEN VGVGB VIVIR VNVNM VUVUT WFWLF WSWSM XKXKX YEYEM YTMYT ZAZAF ZMZMB ZWZWE".split())
CAMEO_FR = {"14": "manifestations", "15": "démonstration de force", "16": "rupture de relations, sanctions", "17": "coercition",
            "18": "attaque", "19": "combats", "20": "violence de masse"}
# Pays dont une escalade touche un canal direct de l'or (énergie, détroits, grandes puissances)
GEO_CANAL = {"IRN": "énergie, Ormuz", "ISR": "Moyen-Orient", "PSE": "Moyen-Orient", "LBN": "Moyen-Orient", "SYR": "Moyen-Orient",
             "YEM": "mer Rouge, Bab el-Mandeb", "SAU": "énergie", "IRQ": "énergie", "ARE": "énergie, Ormuz", "QAT": "énergie, gaz",
             "KWT": "énergie", "OMN": "Ormuz", "LBY": "énergie", "VEN": "énergie", "NGA": "énergie", "RUS": "grande puissance, énergie",
             "UKR": "guerre en Europe", "CHN": "grande puissance", "TWN": "Taïwan, semi-conducteurs", "PRK": "nucléaire",
             "KOR": "péninsule coréenne", "IND": "puissance nucléaire", "PAK": "puissance nucléaire", "EGY": "canal de Suez",
             "USA": "grande puissance", "TUR": "Bosphore", "PAN": "canal de Panama", "SDN": "mer Rouge", "ERI": "mer Rouge", "DJI": "Bab el-Mandeb"}
ADV_FR = {"terrorism": "terrorisme", "unrest": "troubles civils", "civil unrest": "troubles civils", "crime": "criminalité",
          "kidnapping": "enlèvements", "armed conflict": "conflit armé", "wrongful detention": "détentions arbitraires",
          "health": "santé", "natural disasters": "catastrophes naturelles", "hostage taking": "prises d'otages",
          "landmines": "mines", "civil war": "guerre civile", "military conflict": "conflit militaire"}


def http_bytes(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def gdelt_file(stamp):
    """Lit un fichier d'événements GDELT de 15 min ; renvoie les lignes utiles."""
    import zipfile
    z = http_bytes("https://data.gdeltproject.org/gdeltv2/%s.export.CSV.zip" % stamp, timeout=40)
    zf = zipfile.ZipFile(io.BytesIO(z))
    txt = zf.read(zf.namelist()[0]).decode("utf-8", "replace")
    rows = []
    for line in txt.split("\n"):
        f = line.split("\t")
        if len(f) < 61:
            continue
        rows.append(f)
    return rows


def geo_hist_load():
    try:
        with open(OUT_GEO, encoding="utf-8") as f:
            h = json.load(f)
        if isinstance(h, dict) and "days" in h:
            return h
    except Exception:
        pass
    return {"done": [], "days": {}, "hot": []}


def do_geo(data):
    g = {"src": "GDELT 2.0 (événements codés dans la presse mondiale), calcul maison"}
    # ---------- 1. GDELT : nouveaux fichiers de 15 min depuis le dernier passage ----------
    try:
        last = http_bytes("https://data.gdeltproject.org/gdeltv2/lastupdate.txt", timeout=30).decode()
        m = re.search(r"/(\d{14})\.export\.CSV\.zip", last)
        if not m:
            raise ValueError("fichier « lastupdate » illisible")
        newest = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        h = geo_hist_load()
        done = set(h.get("done") or [])
        bf = set(h.get("bf") or [])
        # 24 h de fichiers au maximum ; 16 par passage en temps normal, 96 si l'historique est vide
        want_n = 96 if len(done) < 40 else 16
        stamps = [(newest - timedelta(minutes=15 * k)).strftime("%Y%m%d%H%M%S") for k in range(96)]
        todo = [s for s in stamps if s not in done][:want_n]
        # Base de comparaison : 30 jours passés échantillonnés (12 fichiers par jour, un toutes les 2 h),
        # remplis 3 jours par passage pour ne pas alourdir le robot.
        old_days = h.get("days") or {}
        miss = []
        for k in range(2, 32):
            dd = (newest - timedelta(days=k)).strftime("%Y-%m-%d")
            if (old_days.get(dd) or {}).get("n", 0) < 8:
                miss.append(dd)
        for dd in miss[:3]:
            for hh in range(0, 24, 2):
                st = dd.replace("-", "") + "%02d0000" % hh
                if st not in bf and st not in done:
                    todo.append(st)

        def one(st):
            try:
                return st, gdelt_file(st)
            except Exception as e:  # un fichier manquant n'arrête pas les autres
                return st, e
        got = []
        with ThreadPoolExecutor(8) as ex:
            for st, rows in ex.map(one, todo):
                if isinstance(rows, Exception):
                    continue
                got.append((st, rows))
        if not got and not done:
            raise ValueError("aucun fichier d'événements lu")
        days = h.get("days") or {}
        hot = [x for x in (h.get("hot") or []) if x.get("t", "") >= (newest - timedelta(hours=24)).strftime("%Y%m%d%H%M%S")]
        for st, rows in got:
            day = st[:4] + "-" + st[4:6] + "-" + st[6:8]
            D = days.setdefault(day, {"n": 0, "c": {}})
            D["n"] += 1
            for f in rows:
                cc = FIPS3.get(f[53])
                if not cc:
                    continue
                root, quad = f[28][:2], f[29]
                try:
                    ment = int(f[31] or 0)
                except ValueError:
                    ment = 0
                c = D["c"].setdefault(cc, [0, 0, 0, 0])  # événements, conflit matériel, manifestations, mentions des conflits
                c[0] += 1
                if quad == "4":
                    c[1] += 1
                    c[3] += ment
                if root == "14":
                    c[2] += 1
                if root in ("15", "16", "17", "18", "19", "20") and ment >= 4 and st >= (newest - timedelta(hours=24)).strftime("%Y%m%d%H%M%S"):
                    try:
                        lat, lon = round(float(f[56]), 2), round(float(f[57]), 2)
                    except ValueError:
                        continue
                    hot.append({"t": st, "iso": cc, "r": root, "m": ment, "lat": lat, "lon": lon, "loc": f[52][:60], "u": f[60].strip()[:300]})
            if st >= (newest - timedelta(hours=30)).strftime("%Y%m%d%H%M%S"):
                done.add(st)
            else:
                bf.add(st)
        keep = (newest - timedelta(days=45)).strftime("%Y-%m-%d")
        days = {k: v for k, v in days.items() if k >= keep}
        h = {"done": sorted(s for s in done if s >= (newest - timedelta(hours=30)).strftime("%Y%m%d%H%M%S")),
             "bf": sorted(s for s in bf if s[:8] >= keep.replace("-", "")), "days": days, "hot": hot[-1500:]}
        with LOCK:
            try:
                with open(OUT_GEO, "w", encoding="utf-8") as f:
                    json.dump(h, f, ensure_ascii=False, separators=(",", ":"))
            except Exception as e:
                DIAG["geo_ecriture"] = fr_err(e)
        # ---------- agrégats sur 24 h glissantes ----------
        cut = (newest - timedelta(hours=24)).strftime("%Y%m%d%H%M%S")
        last24 = [s for s in done if s >= cut]
        n24 = len(last24)
        if n24 < 8:
            raise ValueError("seulement %d fichiers de 15 min sur 24 h" % n24)
        # Taux par fichier : jour en cours + veille au prorata, pour comparer à l'historique
        today = newest.strftime("%Y-%m-%d")
        cur, curn = {}, 0
        for dkey in (today, (newest - timedelta(days=1)).strftime("%Y-%m-%d")):
            D = days.get(dkey)
            if not D:
                continue
            curn += D["n"]
            for cc, v in D["c"].items():
                a = cur.setdefault(cc, [0, 0, 0, 0])
                for i in range(4):
                    a[i] += v[i]
        past = [days[k] for k in sorted(days) if k < (newest - timedelta(days=1)).strftime("%Y-%m-%d") and days[k]["n"] >= 8][-30:]
        world_conf = sum(v[1] for v in cur.values()) or 1
        ranked = sorted(cur.items(), key=lambda kv: -kv[1][1])
        rk = {cc: i for i, (cc, v) in enumerate(ranked)}
        nct = max(1, len(ranked))
        C = {}
        for cc, v in cur.items():
            rate = v[1] / max(1, curn)
            hist = [p["c"].get(cc, [0, 0, 0, 0])[1] / p["n"] for p in past]
            z = None
            if len(hist) >= 5 and v[1] >= 10:
                mu = sum(hist) / len(hist)
                sd = (sum((x - mu) ** 2 for x in hist) / len(hist)) ** 0.5
                z = round((rate - mu) / max(sd, 0.25 * mu, 0.02), 2)
            lvl = round(100 * (1 - rk[cc] / nct)) if v[1] >= 3 else 0
            C[cc] = [lvl, z, v[0], v[1], v[2], round(v[1] / world_conf * 100, 2)]
        # Points chauds : regroupement par cellule de 1 degré, on garde la plus citée
        cells = {}
        for x in hot:
            if x["t"] < cut:
                continue
            k = (round(x["lat"]), round(x["lon"]))
            c = cells.get(k)
            if c is None or x["m"] > c["m"]:
                nb = (c["nb"] if c else 0) + 1
                cells[k] = dict(x, nb=nb)
            else:
                c["nb"] += 1
        hotl = sorted(cells.values(), key=lambda x: -(x["m"] + 3 * x["nb"]))[:40]
        g.update({"asof": newest.strftime("%Y-%m-%dT%H:%MZ"), "n24": n24, "days": len(past), "c": C,
                  "hot": [[x["lat"], x["lon"], x["iso"], x["r"], x["m"], x["nb"], x["loc"], x["u"], x["t"]] for x in hotl],
                  "esc": [cc for cc, v in sorted(C.items(), key=lambda kv: -(kv[1][1] or -99)) if v[1] is not None and v[1] >= 1.5][:15]})
        DIAG["geo"] = {"fichiers_lus": len(got), "fichiers_24h": n24, "jours_historique": len(past), "pays": len(C)}
        note("geo.gdelt", "Géopolitique : tension par pays et points chauds (GDELT, calcul maison)", "daily", True, newest.strftime("%Y-%m-%d"), url="https://www.gdeltproject.org/")
    except Exception as e:
        prev = (PREVIOUS.get("geo") or {})
        if prev.get("c") and prev.get("asof") and ageDaysPy(prev["asof"][:10]) <= 2:
            for k in ("asof", "n24", "days", "c", "hot", "esc"):
                g[k] = prev.get(k)
            note("geo.gdelt", "Géopolitique GDELT — dernières données valides conservées", "daily", True, prev["asof"][:10], url="https://www.gdeltproject.org/")
        else:
            note("geo.gdelt", "Géopolitique : tension par pays et points chauds (GDELT)", "daily", False, err=e, url="https://www.gdeltproject.org/")
    # ---------- 2. Indice de risque géopolitique (Caldara et Iacoviello, Réserve fédérale) ----------
    try:
        import xlrd  # installé par le workflow (pip install xlrd) : lecture des fichiers .xls
        b = http_bytes("https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls", timeout=60)
        sh = xlrd.open_workbook(file_contents=b).sheet_by_index(0)
        hd = [str(x) for x in sh.row_values(0)]
        ix = {k: hd.index(k) for k in ("DAY", "GPRD", "GPRD_ACT", "GPRD_THREAT", "GPRD_MA7", "GPRD_MA30") if k in hd}
        rows = []
        for r in range(1, sh.nrows):
            v = sh.row_values(r)
            d = str(v[ix["DAY"]]).split(".")[0]
            if len(d) != 8:
                continue
            try:
                rows.append([d[:4] + "-" + d[4:6] + "-" + d[6:], round(float(v[ix["GPRD"]]), 1), round(float(v[ix["GPRD_MA7"]]), 1),
                             round(float(v[ix["GPRD_MA30"]]), 1), round(float(v[ix["GPRD_ACT"]]), 1), round(float(v[ix["GPRD_THREAT"]]), 1)])
            except (ValueError, TypeError):
                continue
        if len(rows) < 400:
            raise ValueError("série quotidienne trop courte")
        ma7_3y = sorted(x[2] for x in rows[-1095:])
        pct = round(sum(1 for x in ma7_3y if x < rows[-1][2]) / len(ma7_3y) * 100)
        gp = {"asof": rows[-1][0], "d": rows[-400:], "pct3y": pct}
        try:  # indices par pays (mensuels)
            b2 = http_bytes("https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls", timeout=60)
            s2 = xlrd.open_workbook(file_contents=b2).sheet_by_index(0)
            h2 = [str(x) for x in s2.row_values(0)]
            last_r = s2.nrows - 1
            mserial = s2.row_values(last_r)[h2.index("month")]
            month = (datetime(1899, 12, 30) + timedelta(days=float(mserial))).strftime("%Y-%m")
            ct = {}
            for j, name in enumerate(h2):
                if not name.startswith("GPRC_"):
                    continue
                iso = name[5:]
                vals = []
                for r in range(max(1, last_r - 119), last_r + 1):
                    try:
                        vals.append(float(s2.row_values(r)[j]))
                    except (ValueError, TypeError):
                        pass
                if len(vals) < 24:
                    continue
                cur_v = vals[-1]
                p = round(sum(1 for x in vals if x < cur_v) / len(vals) * 100)
                ct[iso] = [round(cur_v, 2), p, round(vals[-2], 2)]
            gp["ctry"] = ct
            gp["month"] = month
        except Exception as e:
            DIAG["gpr_pays"] = fr_err(e)
        g["gpr"] = gp
        note("geo.gpr", "Indice de risque géopolitique (Caldara et Iacoviello)", "daily", True, rows[-1][0], url="https://www.matteoiacoviello.com/gpr.htm")
    except Exception as e:
        prev = (PREVIOUS.get("geo") or {}).get("gpr")
        if prev and ageDaysPy(prev.get("asof", "2000-01-01")) <= 21:
            g["gpr"] = prev
            note("geo.gpr", "Indice de risque géopolitique — dernières données valides conservées", "daily", True, prev["asof"], url="https://www.matteoiacoviello.com/gpr.htm")
        else:
            note("geo.gpr", "Indice de risque géopolitique (Caldara et Iacoviello)", "daily", False, err=e, url="https://www.matteoiacoviello.com/gpr.htm")
    # ---------- 3. Avis de voyage officiels des États-Unis ----------
    try:
        j = json.loads(http("travel_us.json", "https://cadataapi.state.gov/api/TravelAdvisories", timeout=40))
        # Codes pays : on choisit le système (ISO ou FIPS) qui fait correspondre le plus de noms
        def score(tab):
            return sum(1 for a in j for c in (a.get("Category") or [])[:1] if tab.get(c))
        tab = ISO23 if score(ISO23) >= score(FIPS3) else FIPS3
        adv, newest = {}, ""
        for a in j:
            t = a.get("Title") or ""
            mm = re.search(r"Level\s*(\d)", t)
            cs = a.get("Category") or []
            if not mm or not cs:
                continue
            iso = tab.get(cs[0])
            if not iso:
                continue
            summ = (a.get("Summary") or "").lower()
            why = []
            for k_, v_ in ADV_FR.items():
                if k_ in summ and v_ not in why:
                    why.append(v_)
            up = (a.get("Updated") or a.get("Published") or "")[:10]
            newest = max(newest, up)
            adv[iso] = [int(mm.group(1)), up, why[:5], a.get("Link") or ""]
        if len(adv) < 100:
            raise ValueError("seulement %d pays lus" % len(adv))
        g["adv"] = adv
        g["adv_codes"] = "ISO" if tab is ISO23 else "FIPS"
        note("geo.adv", "Avis de voyage officiels (Département d'État américain)", "weekly", True, newest, url="https://travel.state.gov/")
    except Exception as e:
        prev = (PREVIOUS.get("geo") or {}).get("adv")
        if prev:
            g["adv"] = prev
            note("geo.adv", "Avis de voyage officiels — dernières données valides conservées", "weekly", True, max(v[1] for v in prev.values()), url="https://travel.state.gov/")
        else:
            note("geo.adv", "Avis de voyage officiels (Département d'État américain)", "weekly", False, err=e, url="https://travel.state.gov/")
    # ---------- 4. Séismes importants (USGS) ----------
    try:
        j = json.loads(http("usgs.json", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson", timeout=30))
        q = []
        for f in j.get("features") or []:
            p, gm = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
            if (p.get("mag") or 0) >= 5.5 and len(gm) >= 2:
                q.append([round(gm[1], 2), round(gm[0], 2), p.get("mag"), (p.get("place") or "")[:80], datetime.fromtimestamp((p.get("time") or 0) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), p.get("url") or ""])
        q.sort(key=lambda x: -x[2])
        g["quakes"] = q[:15]
        note("geo.usgs", "Séismes de magnitude 5,5 et plus (USGS, 7 jours)", "daily", True, TODAY, url="https://earthquake.usgs.gov/")
    except Exception as e:
        note("geo.usgs", "Séismes (USGS)", "daily", False, err=e, url="https://earthquake.usgs.gov/")
    # ---------- 5. Titres géopolitiques du jour ----------
    try:
        qq = urllib.parse.quote('(guerre OR frappe OR missile OR sanctions OR "cessez-le-feu" OR attaque OR blocus OR Ormuz OR Taïwan) when:1d')
        items = parse_feed(http("geo_news.xml", "https://news.google.com/rss/search?q=%s&hl=fr&gl=FR&ceid=FR:fr" % qq, timeout=25))
        seen, out = set(), []
        for it in items:
            key = it["t"][:60].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        if not out:
            raise ValueError("aucun titre")
        g["news"] = out[:15]
        note("geo.news", "Titres géopolitiques du jour (Google Actualités)", "daily", True, TODAY, url="https://news.google.com/")
    except Exception as e:
        note("geo.news", "Titres géopolitiques (Google Actualités)", "daily", False, err=e, url="https://news.google.com/")
    data["geo"] = g


def geo_note(data):
    """Note proposée du pilier « Risque et refuge » (stratégie, étape 4) : proposition automatique, à valider."""
    g = data.get("geo") or {}
    gp = g.get("gpr") or {}
    vix = (data.get("fred") or {}).get("VIXCLS") or {}
    why, val = [], 0
    vv, vh = vix.get("v"), [x[1] for x in (vix.get("hist") or []) if x[1] is not None]
    v5 = (vv - vh[-6]) if vv is not None and len(vh) >= 6 else None
    pct = gp.get("pct3y")
    d = gp.get("d") or []
    ma7, ma30 = (d[-1][2], d[-1][3]) if d else (None, None)
    esc = [c for c in (g.get("esc") or []) if c in GEO_CANAL and ((g.get("c") or {}).get(c) or [0, 0])[1] is not None and g["c"][c][1] >= 2]
    sh = (data.get("shipping") or {}).get("c") or {}
    drops = []
    for nm, lab in (("Strait of Hormuz", "Ormuz"), ("Bab el-Mandeb Strait", "Bab el-Mandeb"), ("Suez Canal", "Suez")):
        s = [x[1] for x in sh.get(nm, []) if isinstance(x[1], (int, float))]
        if len(s) >= 14:
            a, b = sum(s[-7:]) / 7, sum(s[-14:-7]) / 7
            if b and (a / b - 1) * 100 <= -15:
                drops.append(lab)
    if esc and (drops or (pct is not None and pct >= 90)) and vv is not None and vv >= 20:
        val = 2
    elif (pct is not None and pct >= 75) or (esc and v5 is not None and v5 > 0):
        val = 1
    elif pct is not None and pct <= 25 and vv is not None and vv < 15:
        val = -2
    elif ma7 and ma30 and ma7 <= ma30 * 0.9 and v5 is not None and v5 < 0:
        val = -1
    if pct is not None:
        why.append("indice GPR (moyenne 7 jours) au %de percentile sur 3 ans" % pct)
    if ma7 and ma30:
        why.append("moyenne 7 jours %s la moyenne 30 jours (%.0f contre %.0f)" % ("au-dessus de" if ma7 > ma30 else "sous", ma7, ma30))
    if esc:
        why.append("escalade dans un pays à canal direct pour l'or : " + ", ".join(esc[:4]))
    if drops:
        why.append("trafic en chute de 15 % ou plus : " + ", ".join(drops))
    if vv is not None:
        why.append("VIX %.1f%s" % (vv, "" if v5 is None else " (%+.1f sur 5 séances)" % v5))
    if not why:
        return
    g["note"] = {"v": val, "why": why, "asof": TODAY}
    data["geo"] = g


def main():
    data = {"fred": {}, "derived": {}, "oecd": {}, "national": {}, "cot": {}, "news": []}
    steps = [("fred", do_fred), ("oecd", do_oecd), ("national", do_national), ("cot", do_cot), ("fng", do_fng),
             ("rss", do_rss), ("calendar", do_calendar), ("markets", do_markets), ("world", do_world), ("videos", do_videos), ("shipping", do_shipping), ("curves", do_curves), ("analyses", do_analyses), ("retail", do_retail), ("reserves", do_reserves), ("daily", do_daily), ("policy", do_policy), ("intraday", do_intraday), ("goldetf", do_goldetf), ("cot2", do_cot_detail), ("zq", do_zq), ("ofr", do_ofr), ("bea", do_bea), ("oecdsdmx", do_oecd_sdmx), ("fredapi", do_fred_api), ("fomc", do_fomc_dates), ("consensus", do_consensus_news), ("cbnews", do_cb_news), ("kei", do_kei),
             ("wgcproj", lambda d: (do_wgc(d), do_wgc_figures(d))), ("ecbproj", lambda d: (do_projections(d), do_ecb_proj_table(d))), ("geo", do_geo)]
    # Widget Dukascopy : testé le 30 oct. 2026, le site refuse les serveurs de GitHub (403) ; non affiché faute de vérification.
    def run(step):
        name, fn = step
        try:
            fn(data)
        except Exception as e:  # filet de sécurité : une erreur de code n'arrête pas le reste
            note("robot." + name, "Bloc " + name, "daily", False, err=e)
    todo = [st for st in steps if want(st[0])]
    if want("seasonal"):
        todo.append(("seasonal", lambda d: do_seasonal(force=os.environ.get("MF_SEAS_FORCE") == "1")))
    if want("history"):
        def hist(d):
            try:
                do_history(force=os.environ.get("MF_HIST_FORCE") == "1")
            except Exception as e:
                note("wb.hist", "Historique 2001+ inflation et croissance (Banque mondiale, hebdo)", "weekly", False, err=e, url="https://data.worldbank.org/")
        todo.append(("history", hist))
    # Chronomètre global : au-delà de BUDGET secondes, on enregistre ce qu'on a et on s'arrête.
    budget = int(os.environ.get("MF_BUDGET", "540"))
    ex = ThreadPoolExecutor(len(todo) or 1)
    futs = {ex.submit(run, st): st[0] for st in todo}
    done, pending = wait(futs, timeout=budget)
    for f in pending:
        note("robot." + futs[f], "Bloc " + futs[f] + " (arrêté : délai global de %d s dépassé)" % budget, "daily", False, err="délai dépassé")
    if want("calendar") or want("surprises"):
        try:
            do_surprises(data)
        except Exception as e:
            note("surp", "Surprises économiques", "weekly", False, err=e)
    try:
        do_ffmem(data)
    except Exception as e:
        note("ff.mem", "Derniers chiffres publiés (calendrier)", "weekly", False, err=e)
    try:
        do_core(data)  # après les autres blocs : utilise l'inflation sous-jacente américaine déjà lue
    except Exception as e:
        note("core.cpi", "Inflation sous-jacente des 8 économies", "monthly", False, err=e)
    if want("geo"):
        try:
            geo_note(data)  # après les autres blocs : utilise le VIX et le trafic des détroits
        except Exception as e:
            note("geo.note", "Note proposée du pilier Risque et refuge", "daily", False, err=e)
    if want("reactions") or want("calendar"):
        try:
            do_reactions(data)
        except Exception as e:
            note("react", "Mémoire des réactions du marché", "daily", False, err=e)

    # Séries FRED remplacées par une autre source officielle (OFR, BEA…) : plus affichées comme des pannes
    with LOCK:
        SOURCES[:] = [x for x in SOURCES if not (x["id"].startswith("fred.") and not x["ok"] and x["id"][5:] in data["fred"])]
    out = {"generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": SOURCES, "diag": DIAG}
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
