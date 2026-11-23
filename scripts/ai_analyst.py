#!/usr/bin/env python3
"""Analyste IA du terminal : lit les données déjà produites par le robot (data/macro.json, data/reactions.json)
et écrit data/ai-analyst.json (débriefing du matin, récaps de séance, résumés de publications, scénarios).

Règles :
- aucune clé dans le code : la clé est lue dans une variable d'environnement (secret GitHub) ;
- bibliothèque standard Python uniquement (pas de SDK) ; le fournisseur et les modèles sont dans ai/config.json ;
- rien n'est ré-analysé : data/ai-state.json mémorise ce qui est déjà traité et le nombre d'appels du jour ;
- si la clé manque, si le quota est atteint ou si l'API tombe, le dernier résultat reste en place (jamais d'erreur côté site) ;
- les chiffres cités par l'IA sont contrôlés contre le contexte fourni (sinon : « à vérifier »).

Variables d'environnement :
  GEMINI_API_KEY / ANTHROPIC_API_KEY  clé du fournisseur choisi dans ai/config.json
  AI_MOCK=1      réponses factices (test sans réseau ni clé)
  AI_DRY=1       affiche la taille du contexte et les tâches prévues, sans appeler l'IA
  AI_NOW=...     date UTC forcée (AAAA-MM-JJTHH:MM) pour les tests
  AI_FORCE=...   tâches forcées : debrief,asia,europe,us,releases,scenarios (ignore l'état « déjà fait »)
  AI_CONFIG, AI_MACRO, AI_REACT, AI_OUT, AI_STATE   chemins alternatifs (tests)"""
import hashlib, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CFG_PATH = os.environ.get("AI_CONFIG", os.path.join(ROOT, "ai", "config.json"))
MACRO_PATH = os.environ.get("AI_MACRO", os.path.join(ROOT, "data", "macro.json"))
REACT_PATH = os.environ.get("AI_REACT", os.path.join(ROOT, "data", "reactions.json"))
OUT_PATH = os.environ.get("AI_OUT", os.path.join(ROOT, "data", "ai-analyst.json"))
STATE_PATH = os.environ.get("AI_STATE", os.path.join(ROOT, "data", "ai-state.json"))
MOCK = os.environ.get("AI_MOCK") == "1"
DRY = os.environ.get("AI_DRY") == "1"
FORCE = set(x for x in os.environ.get("AI_FORCE", "").split(",") if x)
DISCLAIMER = "Analyse indicative générée par IA, pas un conseil financier."


def now_utc():
    v = os.environ.get("AI_NOW")
    if v:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


NOW = now_utc()
TODAY = NOW.strftime("%Y-%m-%d")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%MZ")


_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def parse_ts(s):
    """'2026-11-22T16:05Z', '2026-11-22T16:05:00Z' ou '2026-11-24T19:30:00-05:00' -> datetime UTC (None si illisible)."""
    m = _TS.match(str(s or "").strip())
    if not m:
        return None
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6) or 0), tzinfo=timezone.utc)
        z = m.group(7)
        if z and z != "Z":
            sign = -1 if z[0] == "-" else 1
            dt -= sign * timedelta(hours=int(z[1:3]), minutes=int(z[-2:]))
        return dt
    except Exception:
        return None


def utc_min(s):
    """Date d'un événement du calendrier en UTC, à la minute : sert d'identifiant stable (« 2026-11-24T19:30 »)."""
    t = parse_ts(s)
    return t.strftime("%Y-%m-%dT%H:%M") if t else str(s or "")[:16]


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------------------------
#  Contexte : on extrait seulement l'utile de macro.json (quelques Ko au lieu de 4,6 Mo)
# ----------------------------------------------------------------------------------------------------------------
def fnum(v, d=2):
    return "n.d." if not isinstance(v, (int, float)) else ("%.*f" % (d, v)).rstrip("0").rstrip(".") if d else "%d" % round(v)


def closes(d, key):
    try:
        return [x for x in d["daily"][key]["c"] if isinstance(x[1], (int, float))]
    except Exception:
        return []


def move(c, n):
    if len(c) <= n:
        return None
    a, b = c[-1][1], c[-1 - n][1]
    return None if not b else (a / b - 1) * 100


def price_lines(d):
    names = [("gold", "Or (futures)", 1), ("silver", "Argent", 2), ("dxy", "Indice dollar DXY", 2), ("eur", "EUR/USD", 4), ("gbp", "GBP/USD", 4),
             ("jpy", "USD/JPY", 2), ("us10", "Taux US 10 ans (%)", 3), ("vix", "VIX", 2), ("brent", "Pétrole Brent", 2), ("nq", "Nasdaq 100", 0),
             ("es", "S&P 500", 0), ("btc", "Bitcoin", 0), ("copper", "Cuivre", 3)]
    out = []
    for k, lab, dec in names:
        c = closes(d, k)
        if not c:
            continue
        m1, m5, m20 = move(c, 1), move(c, 5), move(c, 20)
        out.append("- %s : %s (clôture du %s) ; 1 séance %s %% ; 5 séances %s %% ; 20 séances %s %%" % (
            lab, fnum(c[-1][1], dec), c[-1][0], fnum(m1, 2), fnum(m5, 2), fnum(m20, 2)))
    return out


def rates_lines(d):
    out = []
    for cur, v in (d.get("policy") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("v"), (int, float)):
            out.append("- %s (%s) : taux %s %%%s%s" % (cur, v.get("cb", ""), fnum(v["v"], 3),
                       " (précédent %s)" % fnum(v["prev"], 3) if isinstance(v.get("prev"), (int, float)) else "",
                       ", dernier changement le %s" % v["since"] if v.get("since") else ""))
    for k, lab in (("us_cpi", "Inflation US"), ("us_core", "Inflation sous-jacente US"), ("us_pce_core", "PCE sous-jacent US"), ("us_unemp", "Chômage US"), ("us_nfp", "Créations d'emplois US (k)")):
        v = (d.get("derived") or {}).get(k)
        if isinstance(v, dict) and isinstance(v.get("v"), (int, float)):
            out.append("- %s : %s (précédent %s, %s)" % (lab, fnum(v["v"], 2), fnum(v.get("prev"), 2), v.get("asof", "?")))
    for k, v in (d.get("core") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("v"), (int, float)):
            out.append("- Inflation sous-jacente %s : %s %% (%s)" % (k, fnum(v["v"], 2), v.get("asof", "?")))
    return out


def fed_lines(d):
    out, ks = [], (d.get("markets") or {}).get("kalshi") or []
    nxt = sorted([x for x in ks if x.get("s") == "KXFEDDECISION" and x.get("close")], key=lambda x: x["close"])
    if nxt:
        first = nxt[0]["close"]
        out.append("- Prochaine décision de la Fed (marché Kalshi, réunion du %s) :" % first)
        for x in nxt:
            if x["close"] == first and isinstance(x.get("p"), (int, float)):
                out.append("    %s : %s %%" % (x.get("o"), fnum(x["p"] * 100, 1)))
    fo = d.get("fomc") or []
    up = [x for x in fo if isinstance(x, str) and x >= TODAY]
    if up:
        out.append("- Prochaines réunions de la Fed : %s" % ", ".join(up[:3]))
    cv = (d.get("curves") or {}).get("us") or {}
    try:
        t = cv["tenors"]
        for lab in ("2A", "10A", "30A"):
            i = t.index(lab)
            out.append("- Courbe US %s : %s %% (il y a 1 semaine %s, il y a 1 mois %s)" % (lab, fnum(cv["now"]["v"][i], 2), fnum(cv["w1"]["v"][i], 2), fnum(cv["m1"]["v"][i], 2)))
    except Exception:
        pass
    return out


def budget_lines(d):
    out, df = [], d.get("deficits") or {}
    us = df.get("us") or {}
    if us:
        out.append("- États-Unis (exercice %s) : déficit cumulé %s Md$ (même période l'an dernier %s Md$), 12 mois glissants %s Md$ (%s)" % (
            us.get("fy", "?"), fnum(us.get("ytd"), 1), fnum(us.get("pytd"), 1), fnum(us.get("r12"), 1), us.get("d", "?")))
    uk = df.get("uk") or {}
    if uk:
        out.append("- Royaume-Uni : emprunt du mois %s Md£, cumul %s Md£ (an dernier %s), 12 mois %s Md£ (%s)" % (fnum(uk.get("m"), 1), fnum(uk.get("ytd"), 1), fnum(uk.get("pytd"), 1), fnum(uk.get("r12"), 1), uk.get("d", "?")))
    eu = df.get("eu") or {}
    for cc in ("DE", "FR", "IT", "ES", "BE", "GR"):
        v = eu.get(cc)
        if v and v.get("b9"):
            y = sorted(v["b9"].keys())[-1]
            gd = (v.get("gd") or {}).get(y)
            out.append("- %s : solde public %s %% du PIB, dette %s %% (%s)" % (v.get("name", cc), fnum(v["b9"][y], 1), fnum(gd, 1), y))
    for r in ((d.get("ezspread") or {}).get("cc") or [])[:6]:
        out.append("- Écart de taux 10 ans %s − Allemagne : %s pb (5 séances %s pb, 20 séances %s pb)" % (r.get("name", r.get("cc")), fnum(r.get("bp"), 1), fnum(r.get("d5"), 1), fnum(r.get("d20"), 1)))
    sov = (d.get("frag") or {}).get("sov") or {}
    if sov.get("v") is not None:
        out.append("- Stress souverain zone euro (SovCISS) : %s, rang %s/100 sur l'historique (%s)" % (fnum(sov.get("v"), 3), sov.get("p"), sov.get("d")))
    rule = (d.get("frag") or {}).get("rule") or {}
    if "score" in rule:
        out.append("- Score de fragmentation zone euro du terminal : %s sur %s" % (rule["score"], rule.get("max")))
    return out


def positioning_lines(d):
    out = []
    for k, lab in (("XAU", "Or"), ("DXY", "Dollar"), ("EUR", "Euro"), ("JPY", "Yen"), ("GBP", "Livre")):
        v = (d.get("cot") or {}).get(k)
        if isinstance(v, dict):
            out.append("- COT %s (fonds à levier, %s) : net %s (semaine précédente %s), rang sur 3 ans %s/100" % (lab, v.get("date"), v.get("net"), v.get("prev_net"), v.get("pctl")))
    for pair in ("EURUSD", "GBPUSD", "USDJPY"):
        v = ((d.get("retail") or {}).get("s") or {}).get(pair)
        if isinstance(v, dict):
            out.append("- Particuliers Myfxbook %s : %s %% acheteurs, %s %% vendeurs" % (pair, v.get("long"), v.get("short")))
    fng = d.get("fng") or {}
    if fng.get("score") is not None:
        out.append("- Indice peur/avidité CNN : %s (%s), il y a 1 semaine %s" % (fnum(fng.get("score"), 1), fng.get("rating"), fnum(fng.get("week"), 1)))
    mv = d.get("move") or {}
    if mv.get("v") is not None:
        out.append("- MOVE (volatilité des taux) : %s (%s)" % (fnum(mv["v"], 1), mv.get("d")))
    return out


def geo_lines(d):
    g, out = d.get("geo") or {}, []
    n = g.get("note") or {}
    if n:
        out.append("- Note de risque du terminal : %s ; raisons : %s" % (n.get("v"), " | ".join(n.get("why") or [])))
    for a in (g.get("alerts") or [])[:8]:
        out.append("- Alerte : %s" % (a[-1] if isinstance(a, list) and a else a))
    for h in (g.get("hot") or [])[:6]:
        try:
            out.append("- Point chaud (24 h) : %s (pays %s, %s événements de conflit)" % (h[6], h[2], h[4]))
        except Exception:
            pass
    r = (g.get("react") or {}).get("d1")
    if r:
        out.append("- Réaction des marchés sur 1 séance : or %s %%, dollar %s %%, taux 10 ans %s pb (%s)" % (r.get("or"), r.get("dollar"), r.get("taux"), (g.get("react") or {}).get("asof")))
    for sym, v in (g.get("mkt") or {}).items():
        if sym in ("BZ=F", "CL=F", "TTF=F") and isinstance(v, dict):
            out.append("- %s : %s (1 séance %s %%, 5 séances %s %%, 20 séances %s %%)" % (v.get("n"), v.get("c"), v.get("c1"), v.get("c5"), v.get("c20")))
    for p in (g.get("pm") or [])[:6]:
        out.append("- Marché de prédiction : « %s » : %s %%" % (p.get("q"), fnum((p.get("p") or 0) * 100, 1)))
    for s in ((g.get("sanc") or {}).get("it") or [])[:3]:
        out.append("- Sanctions OFAC (%s) : %s" % (s.get("d"), s.get("t")))
    for c in (g.get("cal") or [])[:4]:
        out.append("- Rendez-vous géopolitique : %s au %s (%s)" % (c[2], c[0], c[3]))
    for x in (g.get("news") or [])[:8]:
        out.append("- Titre : %s" % x.get("t"))
    return out


def headline_lines(d, hours=40, n_urgent=22, n_other=14):
    lim = NOW - timedelta(hours=hours)
    fl = [x for x in (d.get("flash") or []) if (parse_ts(x.get("ts")) or NOW) >= lim]
    urg = [x for x in fl if x.get("u")][:n_urgent]
    oth = [x for x in fl if not x.get("u") and x.get("k") != "trader"][:n_other]
    out = []
    for x in urg + oth:
        t = (x.get("t") or "").strip()
        ex = (x.get("x") or "").strip()
        out.append("- [%s %s%s] %s%s" % ((x.get("ts") or "")[5:16].replace("T", " "), x.get("src", ""), ", urgent" if x.get("u") else "", t, (" — " + ex[:160]) if ex and ex != t else ""))
    return out


def cb_lines(d):
    out = []
    for cur, v in (d.get("cb_news") or {}).items():
        if isinstance(v, dict):
            out.append("- %s (%s) : %s à %s %% le %s ; titre : %s" % (cur, v.get("cb", ""), v.get("act"), v.get("v"), v.get("d"), v.get("t")))
    for x in (d.get("news") or [])[:10]:
        out.append("- Communiqué officiel (%s, %s) : %s" % (x.get("src"), x.get("d"), x.get("t")))
    return out


def bank_view_lines(d):
    out = []
    for x in (d.get("analyses") or [])[:8]:
        out.append("- %s (%s) : %s — %s" % (x.get("src"), x.get("d"), x.get("t"), (x.get("x") or "")[:220]))
    for x in (d.get("consensus_auto") or [])[:6]:
        out.append("- Prévision de banque (%s, %s) : %s" % (x.get("bank"), x.get("asset"), x.get("t")))
    return out


def ev_line(e):
    return "%s | %s | %s | importance %s | prévu %s | précédent %s | réel %s" % (utc_min(e.get("d")).replace("T", " "), e.get("c"), e.get("t"), e.get("i"), e.get("f") or "n.d.", e.get("p") or "n.d.", e.get("a") or "pas encore publié")


def upcoming_events(d, cfg, hours=None):
    cur = set(cfg["event_currencies"])
    lim = NOW + timedelta(hours=hours or 24 * 7)
    out = []
    for e in (d.get("calendar") or {}).get("events") or []:
        t = parse_ts(e.get("d"))
        if t and NOW - timedelta(minutes=5) <= t <= lim and e.get("c") in cur and e.get("i") in ("High", "Medium"):
            out.append((t, e))
    out.sort(key=lambda x: (x[0], 0 if x[1].get("i") == "High" else 1))
    return out


def calendar_lines(d, cfg):
    ups = upcoming_events(d, cfg)
    hi = [e for t, e in ups if e.get("i") == "High"][:16]
    out = ["- " + ev_line(e) for e in hi]
    lim = NOW - timedelta(hours=30)
    for e in (d.get("calendar") or {}).get("events") or []:
        t = parse_ts(e.get("d"))
        if t and lim <= t <= NOW and e.get("i") == "High" and e.get("a") not in (None, "") and e.get("c") in cfg["event_currencies"]:
            out.append("- (déjà publié) " + ev_line(e))
    return out


def build_context(d, cfg, parts):
    loc = NOW + timedelta(hours=1)
    blocks = [("DATE", ["Maintenant : %s UTC (%s à Kinshasa, UTC+1). Données du robot générées le %s." % (iso(NOW), loc.strftime("%Y-%m-%d %H:%M"), d.get("generated"))])]
    fn = {"prix": price_lines, "taux": rates_lines, "fed": fed_lines, "budget": budget_lines, "positionnement": positioning_lines, "geo": geo_lines,
          "titres": headline_lines, "banques_centrales": cb_lines, "avis_banques": bank_view_lines}
    titles = {"prix": "PRIX ET MOUVEMENTS", "taux": "TAUX DIRECTEURS, INFLATION, EMPLOI", "fed": "FED : ANTICIPATIONS ET COURBE", "budget": "BUDGET, DETTE ET ZONE EURO",
              "positionnement": "POSITIONNEMENT ET SENTIMENT", "geo": "GÉOPOLITIQUE", "titres": "TITRES DE L'ACTUALITÉ (récents)", "banques_centrales": "BANQUES CENTRALES : DÉCISIONS ET COMMUNIQUÉS",
              "avis_banques": "NOTES ET PRÉVISIONS DE BANQUES (presse)"}
    for p in parts:
        if p == "calendrier":
            blocks.append(("CALENDRIER (importance forte, heure UTC)", calendar_lines(d, cfg)))
        elif p in fn:
            blocks.append((titles[p], fn[p](d)))
    return "\n\n".join("## %s\n%s" % (t, "\n".join(l) if l else "(aucune donnée)") for t, l in blocks)


# ----------------------------------------------------------------------------------------------------------------
#  Séances : mouvements calculés par le script (jamais par l'IA) à partir des bougies horaires du robot
# ----------------------------------------------------------------------------------------------------------------
SESSION_ASSETS = [("gold", "Or", 2), ("dxy", "Dollar (DXY)", 2), ("EURUSD", "EUR/USD", 4), ("GBPUSD", "GBP/USD", 4), ("USDJPY", "USD/JPY", 2), ("nq", "Nasdaq 100", 0), ("oil", "Pétrole", 2)]


def session_window(sid, cfg, day):
    s = cfg["sessions_utc"][sid]
    h0, m0 = [int(x) for x in s["start"].split(":")]
    h1, m1 = [int(x) for x in s["end"].split(":")]
    base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return base + timedelta(hours=h0, minutes=m0), base + timedelta(hours=h1, minutes=m1)


def session_moves(d, t0, t1):
    out = []
    for key, lab, dec in SESSION_ASSETS:
        try:
            c = d["intraday"][key]["c"]
        except Exception:
            continue
        pts = [(parse_ts(x[0]), x[1]) for x in c if isinstance(x[1], (int, float))]
        pts = [(t, v) for t, v in pts if t]
        a = [p for p in pts if p[0] <= t0]
        b = [p for p in pts if t0 < p[0] <= t1]
        if not a or not b or t0 - a[-1][0] > timedelta(hours=6):
            continue
        v0, v1 = a[-1][1], b[-1][1]
        if v0:
            out.append({"actif": lab, "debut": round(v0, dec), "fin": round(v1, dec), "pct": round((v1 / v0 - 1) * 100, 2)})
    return out


def session_headlines(d, t0, t1, n=18):
    sel = [x for x in (d.get("flash") or []) if t0 <= (parse_ts(x.get("ts")) or t0 - timedelta(days=9)) <= t1 + timedelta(minutes=20)]
    sel.sort(key=lambda x: (not x.get("u"), x.get("ts") or ""), reverse=False)
    return ["- [%s %s] %s" % ((x.get("ts") or "")[11:16], x.get("src", ""), (x.get("t") or "").strip()) for x in sel[:n]]


# ----------------------------------------------------------------------------------------------------------------
#  Fournisseurs d'IA (interchangeables)
# ----------------------------------------------------------------------------------------------------------------
class QuotaError(Exception):
    pass


class ApiDown(Exception):
    pass


class ModelError(Exception):
    pass


class TransientError(ModelError):
    """Service surchargé ou en panne passagère (HTTP 500/502/503/504) : on essaie le modèle suivant."""


def http_post(url, headers, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=dict(headers, **{"Content-Type": "application/json"}), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        txt = ""
        try:
            txt = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if e.code == 429 or (e.code == 403 and re.search(r"quota|rate|exhaust", txt, re.I)):
            raise QuotaError("quota ou limite de débit (HTTP %d)" % e.code)
        if e.code in (400, 404):
            raise ModelError("HTTP %d : %s" % (e.code, re.sub(r"\s+", " ", txt)[:160]))
        if e.code in (401, 403):
            raise ApiDown("clé refusée (HTTP %d)" % e.code)
        if e.code in (500, 502, 503, 504):
            raise TransientError("HTTP %d : %s" % (e.code, re.sub(r"\s+", " ", txt)[:220] or "service surchargé ou indisponible"))
        raise ApiDown("HTTP %d" % e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiDown("réseau : %s" % str(e)[:100])


def call_gemini(model, system, prompt, key, cfg):
    lim = cfg["limits"]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": lim["temperature"], "responseMimeType": "application/json", "maxOutputTokens": lim["max_output_tokens"]}}
    j = http_post("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model, {"x-goog-api-key": key}, body, lim["timeout_seconds"])
    try:
        cand = j["candidates"][0]
        txt = "".join(p.get("text", "") for p in cand["content"]["parts"])
    except Exception:
        raise ModelError("réponse vide ou bloquée : %s" % json.dumps(j.get("promptFeedback", j))[:160])
    if cand.get("finishReason") == "MAX_TOKENS":
        raise ModelError("réponse tronquée (MAX_TOKENS)")
    return txt


def call_anthropic(model, system, prompt, key, cfg):
    lim = cfg["limits"]
    body = {"model": model, "max_tokens": lim["max_output_tokens"], "temperature": lim["temperature"], "system": system, "messages": [{"role": "user", "content": prompt}]}
    j = http_post("https://api.anthropic.com/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, body, lim["timeout_seconds"])
    try:
        txt = "".join(b.get("text", "") for b in j["content"] if b.get("type") == "text")
    except Exception:
        raise ModelError("réponse vide")
    if j.get("stop_reason") == "max_tokens":
        raise ModelError("réponse tronquée (max_tokens)")
    return txt


PROVIDERS = {"gemini": call_gemini, "anthropic": call_anthropic}


def parse_json(txt):
    t = (txt or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


# ----------------------------------------------------------------------------------------------------------------
#  Invites (prompts)
# ----------------------------------------------------------------------------------------------------------------
SYSTEM = """Tu es l'analyste macro d'un terminal de trading personnel (forex, or). Tu écris en français clair, pour un trader débutant à Kinshasa.
RÈGLES ABSOLUES :
1. Tu t'appuies UNIQUEMENT sur le CONTEXTE fourni. Tu n'inventes aucun chiffre, date, citation, niveau de prix ni événement. Si une donnée manque, tu l'écris dans « limites » au lieu de la deviner.
2. Tu recopies les chiffres tels qu'ils figurent dans le contexte (même arrondi). Tu ne calcules pas de nouveaux niveaux de prix, cibles, stops ou objectifs.
3. Tu distingues le fait (ce qui est dans le contexte) de l'interprétation (ce qui est probable). Tu emploies « probable », « pourrait », « indicatif » pour l'interprétation.
4. Tu n'écris aucune consigne d'achat ou de vente, aucune taille de position. Tu décris des biais et des facteurs, pas des ordres.
5. Tu hiérarchises : tu ne cites que les sujets qui font réellement bouger le marché aujourd'hui (guerre, énergie, banques centrales, inflation, emploi, budgets, taux), pas tous les titres. Les titres de sport, faits divers ou politique sans lien avec les marchés sont ignorés.
6. Ton travail est une ANALYSE constructive, pas une liste de titres : tu relies les faits entre eux (cause → effet), tu expliques POURQUOI le marché bouge, tu proposes un scénario central et un scénario alternatif, et tu dis ce qui changerait ta lecture. Chaque affirmation importante s'appuie sur un fait du contexte.
7. Tu réponds UNIQUEMENT par un objet JSON valide respectant le schéma demandé, sans texte autour ni balises."""

DEBRIEF_SCHEMA = """Schéma JSON exact à produire :
{
 "titre": "une phrase d'accroche (140 caractères max) sur ce qui domine le marché ce matin",
 "lignes": ["EXACTEMENT 5 lignes de briefing, 240 caractères max chacune, de la plus importante à la moins importante ; chaque ligne = un fait chiffré + ce qu'il signifie pour le marché"],
 "analyse": {
  "situation": "3 à 5 phrases : la synthèse de la situation, en reliant les faits entre eux (régime du marché, ce qui domine, ce qui est secondaire)",
  "chaine_causale": ["3 à 5 maillons « cause → effet » qui expliquent les mouvements (ex. énergie → inflation → taux → dollar → or), chacun appuyé par un chiffre ou un titre du contexte"],
  "scenario_central": {"description": "le scénario le plus probable pour les prochains jours, en 2 à 3 phrases", "conditions": "ce qui doit se confirmer (donnée, événement du calendrier, niveau déjà cité dans le contexte)", "effet": {"or": "1 phrase", "dollar": "1 phrase", "euro": "1 phrase"}},
  "scenario_alternatif": {"description": "le scénario qui contredirait le central, en 2 phrases", "conditions": "ce qui le déclencherait", "effet": {"or": "1 phrase", "dollar": "1 phrase", "euro": "1 phrase"}},
  "ce_qui_changerait_la_vue": ["2 à 4 signaux précis (donnée, annonce du calendrier, évolution d'un chiffre du contexte) qui invalideraient le scénario central"],
  "conclusion": "2 phrases : la lecture d'ensemble et la prudence à garder"
 },
 "sections": [
  {"id": "geopolitique", "titre": "Géopolitique", "resume": "2 à 4 phrases", "points": ["2 à 4 faits issus du contexte, avec leur effet probable sur l'or, le dollar, l'euro, le pétrole"]},
  {"id": "budget", "titre": "Budgets, dette et obligations", "resume": "...", "points": ["..."]},
  {"id": "macro", "titre": "Macro : inflation, emploi, croissance", "resume": "...", "points": ["..."]},
  {"id": "banques_centrales", "titre": "Banques centrales et taux", "resume": "...", "points": ["..."]}
 ],
 "catalyseurs": [{"quand": "date et heure UTC telles que dans le calendrier", "evenement": "nom", "pourquoi": "pourquoi cela peut faire bouger le marché (1 phrase)", "devises": ["USD"]}],
 "drivers": [{"nom": "moteur du marché (ex. anticipations de hausse de taux de la Fed)", "or": "hausse|baisse|neutre|incertain", "dollar": "hausse|baisse|neutre|incertain", "euro": "hausse|baisse|neutre|incertain", "pourquoi": "1 phrase avec les chiffres du contexte"}],
 "actifs": {"or": {"biais": "haussier|baissier|neutre|incertain", "pourquoi": "2 phrases"}, "dollar": {"biais": "...", "pourquoi": "..."}, "euro": {"biais": "...", "pourquoi": "..."}},
 "a_surveiller": ["3 à 5 points concrets pour la journée"],
 "limites": "ce que les données ne permettent pas de dire (1 à 2 phrases)"
}
Contraintes : 3 à 6 catalyseurs (les plus proches d'abord, jamais d'événement absent du calendrier), 3 à 6 drivers classés par importance, biais « indicatif »."""

SESSION_SCHEMA = """Schéma JSON exact à produire :
{"resume": "2 à 3 phrases sur ce qui a dominé la séance", "points": ["2 à 4 faits ou titres qui expliquent les mouvements (avec leur heure UTC si connue)"], "lecture": "1 phrase : ce que cela change pour la séance suivante", "limites": "1 phrase sur ce qui manque"}
Les mouvements chiffrés de la séance te sont donnés : tu les commentes, tu ne les recalcules pas."""

RELEASE_SCHEMA = """Pour CHAQUE publication listée, produis un élément. Schéma JSON exact :
{"publications": [{"id": "l'identifiant donné", "lecture": "1 à 2 phrases : réel contre prévu et contre précédent", "ecart": "au-dessus|conforme|en dessous|indisponible", "impact": {"dollar": "1 phrase", "euro": "1 phrase", "or": "1 phrase"}, "reaction_marche": "1 phrase qui reprend les mouvements mesurés par le robot s'ils sont fournis, sinon « non mesurée »", "confiance": "faible|moyenne|élevée"}]}
Pour « ecart », utilise l'écart calculé fourni. Pour l'impact, appuie-toi sur le contexte (anticipations de la Fed, inflation, positionnement)."""

SCENARIO_SCHEMA = """Pour CHAQUE annonce listée, produis 3 scénarios. Schéma JSON exact :
{"annonces": [{"id": "l'identifiant donné", "enjeu": "1 phrase : pourquoi cette annonce compte maintenant (contexte du marché)", "scenarios": {
  "au_dessus": {"condition": "chiffre au-dessus de la prévision", "dollar": "1 phrase", "euro": "1 phrase", "or": "1 phrase"},
  "dans_les_attentes": {"condition": "chiffre proche de la prévision", "dollar": "...", "euro": "...", "or": "..."},
  "en_dessous": {"condition": "chiffre en dessous de la prévision", "dollar": "...", "euro": "...", "or": "..."}}}]}
Les conditions reprennent la prévision et le précédent du calendrier tels quels. Aucun niveau de prix."""


# ----------------------------------------------------------------------------------------------------------------
#  Contrôle anti-invention : chaque nombre cité doit figurer dans le contexte
# ----------------------------------------------------------------------------------------------------------------
NUM = re.compile(r"(?<![\w.])[-−+]?\d[\d\s]*(?:[.,]\d+)?")


def norm_nums(text):
    out = set()
    for m in NUM.finditer(text or ""):
        s = m.group(0).replace("−", "-").replace(" ", "").replace(",", ".").lstrip("+")
        try:
            v = float(s)
        except Exception:
            continue
        out.add(round(abs(v), 4))
    return out


def unverified_numbers(obj, ctx):
    have = norm_nums(ctx)
    texts = []

    def walk(x):
        if isinstance(x, str):
            texts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    bad = []
    for t in texts:
        for m in NUM.finditer(t):
            raw = m.group(0).strip()
            s = raw.replace("−", "-").replace(" ", "").replace(",", ".").lstrip("+")
            try:
                v = abs(float(s))
            except Exception:
                continue
            if v < 10 and "." not in s:      # petits entiers (« 3 points », « 5 lignes »)
                continue
            if 1990 <= v <= 2035 and "." not in s:  # années
                continue
            ok = any(abs(v - h) <= max(0.051, h * 0.0006) for h in have)
            if not ok and raw not in bad:
                bad.append(raw)
    return bad[:8]


# ----------------------------------------------------------------------------------------------------------------
#  Écart réel / prévu calculé par le script
# ----------------------------------------------------------------------------------------------------------------
def to_num(v):
    if v in (None, ""):
        return None
    s = str(v).replace(",", ".").replace(" ", "")
    m = re.match(r"^([-−]?\d+(?:\.\d+)?)\s*([KMBT%]?)", s)
    if not m:
        return None
    x = float(m.group(1).replace("−", "-"))
    return x * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2), 1)


def surprise(e):
    a, f = to_num(e.get("a")), to_num(e.get("f"))
    if a is None or f is None:
        return "indisponible"
    tol = max(abs(f) * 0.02, 1e-9) if f else 1e-9
    return "au-dessus" if a > f + tol else "en dessous" if a < f - tol else "conforme"


def ev_id(e):
    return "%s|%s|%s" % (utc_min(e.get("d")), e.get("c"), e.get("t"))


def reaction_text(e, react):
    for r in (react.get("events") or []):
        if utc_min(r.get("d")) == utc_min(e.get("d")) and r.get("c") == e.get("c") and r.get("t") == e.get("t"):
            names = {"gold": "or", "dxy": "dollar", "eur": "EUR/USD", "nq": "Nasdaq", "us10": "taux 10 ans (pb)"}
            parts = []
            for k, lab in names.items():
                v = (r.get("r") or {}).get(k)
                if v:
                    parts.append("%s : %s" % (lab, " / ".join("n.d." if x is None else ("%+g" % x) for x in v)))
            if parts:
                return "mouvements mesurés après 15 min / 1 h / 4 h (en %%) : %s" % " ; ".join(parts)
    return "pas de mesure disponible"


# ----------------------------------------------------------------------------------------------------------------
#  Mock (tests sans réseau)
# ----------------------------------------------------------------------------------------------------------------
def mock_reply(kind, prompt):
    ids = re.findall(r"^ID: (.+)$", prompt, re.M)
    if kind == "debrief":
        return {"titre": "[TEST] Séance dominée par la peur et les anticipations de hausse de taux", "lignes": ["[TEST] Ligne %d du briefing." % i for i in range(1, 6)],
                "analyse": {"situation": "[TEST] Synthèse de la situation.", "chaine_causale": ["[TEST] énergie → inflation", "[TEST] inflation → taux"],
                            "scenario_central": {"description": "[TEST] Central", "conditions": "[TEST]", "effet": {"or": "[TEST]", "dollar": "[TEST]", "euro": "[TEST]"}},
                            "scenario_alternatif": {"description": "[TEST] Alternatif", "conditions": "[TEST]", "effet": {"or": "[TEST]", "dollar": "[TEST]", "euro": "[TEST]"}},
                            "ce_qui_changerait_la_vue": ["[TEST] Signal"], "conclusion": "[TEST] Conclusion."},
                "sections": [{"id": s, "titre": t, "resume": "[TEST] Résumé de la section.", "points": ["[TEST] Point 1", "[TEST] Point 2"]} for s, t in
                             (("geopolitique", "Géopolitique"), ("budget", "Budgets, dette et obligations"), ("macro", "Macro : inflation, emploi, croissance"), ("banques_centrales", "Banques centrales et taux"))],
                "catalyseurs": [{"quand": "2026-11-24 15:00", "evenement": "[TEST] Événement", "pourquoi": "[TEST] Pourquoi", "devises": ["USD"]}],
                "drivers": [{"nom": "[TEST] Driver", "or": "baisse", "dollar": "hausse", "euro": "baisse", "pourquoi": "[TEST] Pourquoi"}],
                "actifs": {k: {"biais": "incertain", "pourquoi": "[TEST]"} for k in ("or", "dollar", "euro")}, "a_surveiller": ["[TEST] Point"], "limites": "[TEST] Réponse factice."}
    if kind == "session":
        return {"resume": "[TEST] Résumé de séance.", "points": ["[TEST] Point"], "lecture": "[TEST] Lecture.", "limites": "[TEST]"}
    if kind == "releases":
        return {"publications": [{"id": i, "lecture": "[TEST] Lecture", "ecart": "conforme", "impact": {"dollar": "[TEST]", "euro": "[TEST]", "or": "[TEST]"}, "reaction_marche": "non mesurée", "confiance": "faible"} for i in ids]}
    sc = {"condition": "[TEST]", "dollar": "[TEST]", "euro": "[TEST]", "or": "[TEST]"}
    return {"annonces": [{"id": i, "enjeu": "[TEST] Enjeu", "scenarios": {"au_dessus": sc, "dans_les_attentes": sc, "en_dessous": sc}} for i in ids]}


# ----------------------------------------------------------------------------------------------------------------
#  Exécution
# ----------------------------------------------------------------------------------------------------------------
class Runner:
    def __init__(self, cfg, state):
        self.cfg, self.state = cfg, state
        self.prov = cfg["provider"]
        pc = cfg["providers"][self.prov]
        self.key = os.environ.get(pc["secret"], "").strip()
        self.stop = None          # raison d'arrêt du passage (quota, API en panne)
        self.model_used = {}
        self.made = 0

    def can_call(self):
        if self.stop:
            return False
        if self.state["calls"] >= self.cfg["limits"]["max_calls_per_day"]:
            self.stop = "plafond du jour atteint (%d appels)" % self.cfg["limits"]["max_calls_per_day"]
            return False
        return True

    def generate(self, kind, tier, system, prompt, ctx):
        """Un appel à l'IA. Retourne (objet JSON, modèle, chiffres à vérifier) ou None.
        Les modèles du palier demandé sont essayés d'abord, puis ceux de l'autre palier en dernier recours.
        Une erreur de surcharge (503) ne compte pas dans le plafond du jour : seule une réponse reçue compte."""
        if not self.can_call():
            return None
        if MOCK:
            self.state["calls"] += 1
            self.made += 1
            return mock_reply(kind, prompt), "mock", []
        pc = self.cfg["providers"][self.prov]
        other = "fast" if tier == "strong" else "strong"
        models = list(pc[tier]) + [m for m in pc[other] if m not in pc[tier]]
        cap = self.cfg["limits"]["max_calls_per_day"]
        last, transients = None, 0
        for m in models:
            if self.state["calls"] >= cap:
                self.stop = "plafond du jour atteint (%d appels)" % cap
                return None
            try:
                txt = PROVIDERS[self.prov](m, system, prompt, self.key, self.cfg)
                self.state["calls"] += 1
                obj = parse_json(txt)
                if not isinstance(obj, dict):
                    raise ModelError("JSON inattendu")
                self.made += 1
                self.model_used[kind] = m
                return obj, m, unverified_numbers(obj, ctx)
            except QuotaError as e:
                self.state["calls"] += 1
                self.stop = str(e)
                self.state["backoff_until"] = iso(NOW + timedelta(minutes=self.cfg["limits"]["backoff_minutes_quota"]))
                self.state["last_error"] = "quota : " + str(e)
                return None
            except ApiDown as e:
                self.stop = str(e)
                self.state["backoff_until"] = iso(NOW + timedelta(minutes=self.cfg["limits"]["backoff_minutes_error"]))
                self.state["last_error"] = "API : " + str(e)
                return None
            except (ModelError, ValueError) as e:
                last = "%s : %s" % (m, e)
                print("  essai %s : %s" % (m, e))
                if isinstance(e, TransientError):
                    transients += 1
                else:
                    self.state["calls"] += 1
                continue
        self.state["last_error"] = "%s : %s" % (kind, last)
        if models and transients == len(models):   # tous les modèles surchargés : on s'arrête et on réessaie plus tard
            self.stop = "service d'IA surchargé (%s)" % last
            self.state["backoff_until"] = iso(NOW + timedelta(minutes=self.cfg["limits"]["backoff_minutes_error"]))
            self.state["last_error"] = "API : " + last
        return None


def main():
    cfg = load_json(CFG_PATH, None)
    if not cfg:
        print("ai/config.json introuvable ou illisible : rien à faire.")
        return 0
    d = load_json(MACRO_PATH, None)
    if not d or not d.get("generated"):
        print("data/macro.json absent : rien à faire.")
        return 0
    react = load_json(REACT_PATH, {})
    state = load_json(STATE_PATH, {})
    if state.get("day") != TODAY:
        state.update({"day": TODAY, "calls": 0, "debriefs_today": 0})
    state.setdefault("done", {})
    state["done"] = {k: v for k, v in state["done"].items() if (parse_ts(v) or NOW) > NOW - timedelta(days=10)}
    out = load_json(OUT_PATH, {})
    lim = cfg["limits"]
    run = Runner(cfg, state)

    if not MOCK and not DRY and not run.key:
        print("Clé %s absente : analyste IA non activé (le site affichera « non activé »)." % cfg["providers"][run.prov]["secret"])
        return 0
    bo = parse_ts(state.get("backoff_until"))
    if bo and bo > NOW and not FORCE and not MOCK and not DRY:
        print("Pause jusqu'à %s UTC (%s)." % (iso(bo), state.get("last_error", "")))
        return 0

    done = state["done"]
    tasks = []   # (genre, clé d'état, fonction)

    # --- 1. Débriefing -------------------------------------------------------------------------------------------
    ctx_deb = build_context(d, cfg, ["prix", "taux", "fed", "budget", "positionnement", "geo", "titres", "banques_centrales", "avis_banques", "calendrier"])
    fp = hashlib.sha1("|".join([TODAY] + [x.get("t", "") for x in (d.get("flash") or []) if x.get("u")][:10] + [str(a) for a in ((d.get("geo") or {}).get("alerts") or [])]).encode("utf-8")).hexdigest()[:12]
    last_deb = parse_ts((out.get("debrief") or {}).get("gen"))
    due_deb = NOW.hour >= lim["debrief_after_utc_hour"] and (
        not last_deb or last_deb.strftime("%Y-%m-%d") != TODAY or
        (NOW - last_deb >= timedelta(hours=lim["debrief_refresh_hours"]) and state.get("debrief_fp") != fp and state.get("debriefs_today", 0) < lim["debrief_max_per_day"]))
    if due_deb or "debrief" in FORCE:
        tasks.append(("debrief", "debrief", ctx_deb, fp))

    # --- 2. Récaps de séance (une fois par séance terminée, en semaine) ---------------------------------------------
    for sid in ("asia", "europe", "us"):
        day = TODAY
        s_end = session_window(sid, cfg, day)[1]
        if NOW < s_end:   # séance pas finie : on regarde celle de la veille pour rester utile au réveil
            day = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
            s_end = session_window(sid, cfg, day)[1]
        if datetime.strptime(day, "%Y-%m-%d").weekday() >= 5 and sid not in FORCE:
            continue
        key = "session:%s:%s" % (day, sid)
        if NOW - s_end <= timedelta(hours=20) and (key not in done or sid in FORCE):
            tasks.append(("session", key, (sid, day), None))

    # --- 3. Publications importantes déjà sorties -------------------------------------------------------------------
    rel = []
    for e in (d.get("calendar") or {}).get("events") or []:
        t = parse_ts(e.get("d"))
        if (t and NOW - timedelta(hours=lim["release_window_hours"]) <= t <= NOW and e.get("i") == "High" and e.get("c") in cfg["event_currencies"]
                and e.get("a") not in (None, "") and ("rel:" + ev_id(e) not in done or "releases" in FORCE)):
            rel.append(e)
    rel.sort(key=lambda e: parse_ts(e.get("d")) or NOW)
    if rel:
        tasks.append(("releases", "releases", rel[:lim["max_events_per_call"]], None))

    # --- 4. Scénarios avant les grosses annonces -----------------------------------------------------------------------
    sc = []
    for t, e in upcoming_events(d, cfg, lim["scenario_ahead_hours"]):
        if e.get("i") == "High" and e.get("a") in (None, "") and ("sc:" + ev_id(e) not in done or "scenarios" in FORCE):
            sc.append(e)
    prio = {c: i for i, c in enumerate(cfg["event_currencies"])}
    sc.sort(key=lambda e: (utc_min(e.get("d"))[:13], prio.get(e.get("c"), 99)))
    if sc:
        tasks.append(("scenarios", "scenarios", sc[:lim["max_events_per_call"]], None))

    print("Tâches prévues : %s ; appels du jour : %d/%d" % (", ".join(t[0] for t in tasks) or "aucune", state["calls"], lim["max_calls_per_day"]))
    if DRY:
        print("Taille du contexte du débriefing : %d caractères (≈ %d mots)" % (len(ctx_deb), len(ctx_deb.split())))
        print(ctx_deb[:3000])
        return 0

    changed = False
    stamp = iso(NOW)

    for kind, key, payload, extra in tasks:
        if run.stop:
            break
        if kind == "debrief":
            prompt = "Rédige le DÉBRIEFING DU MATIN du terminal.\n\n%s\n\n# CONTEXTE\n%s" % (DEBRIEF_SCHEMA, payload)
            r = run.generate("debrief", "strong", SYSTEM, prompt, payload)
            if r:
                obj, model, bad = r
                obj.update({"gen": stamp, "model": model, "a_verifier": bad, "donnees_du": d.get("generated")})
                out["debrief"] = obj
                state["debrief_fp"] = extra
                state["debriefs_today"] = state.get("debriefs_today", 0) + 1
                changed = True
        elif kind == "session":
            sid, day = payload
            t0, t1 = session_window(sid, cfg, day)
            mv = session_moves(d, t0, t1)
            hl = session_headlines(d, t0, t1)
            ctx = build_context(d, cfg, ["prix", "fed", "geo", "titres"]) + "\n\n## MOUVEMENTS CALCULÉS PAR LE SCRIPT (séance %s, %s à %s UTC)\n%s\n\n## TITRES PENDANT LA SÉANCE\n%s" % (
                cfg["sessions_utc"][sid]["label"], t0.strftime("%H:%M"), t1.strftime("%H:%M"), "\n".join("- %s : %s → %s (%+.2f %%)" % (m["actif"], m["debut"], m["fin"], m["pct"]) for m in mv) or "(aucun mouvement mesurable)", "\n".join(hl) or "(aucun)")
            prompt = "Rédige le RÉCAP DE LA SÉANCE « %s » du %s (de %s à %s UTC).\n\n%s\n\n# CONTEXTE\n%s" % (cfg["sessions_utc"][sid]["label"], day, t0.strftime("%H:%M"), t1.strftime("%H:%M"), SESSION_SCHEMA, ctx)
            r = run.generate("session", "fast", SYSTEM, prompt, ctx)
            if r:
                obj, model, bad = r
                obj.update({"gen": stamp, "model": model, "a_verifier": bad, "jour": day, "label": cfg["sessions_utc"][sid]["label"], "mouvements": mv})
                out.setdefault("sessions", {})[sid] = obj
                done[key] = stamp
                changed = True
        elif kind == "releases":
            lines = []
            for e in payload:
                lines.append("ID: %s\n  %s\n  écart calculé : %s\n  %s" % (ev_id(e), ev_line(e), surprise(e), reaction_text(e, react)))
            ctx = build_context(d, cfg, ["prix", "fed", "taux", "positionnement", "titres"]) + "\n\n## PUBLICATIONS À ANALYSER\n" + "\n".join(lines)
            prompt = "Analyse ces publications importantes déjà sorties.\n\n%s\n\n# CONTEXTE\n%s" % (RELEASE_SCHEMA, ctx)
            r = run.generate("releases", "fast", SYSTEM, prompt, ctx)
            if r:
                obj, model, bad = r
                byid = {ev_id(e): e for e in payload}
                new = []
                for it in obj.get("publications") or []:
                    e = byid.get(it.get("id"))
                    if not e:
                        continue
                    it.update({"gen": stamp, "model": model, "date": utc_min(e.get("d")), "devise": e.get("c"), "titre": e.get("t"), "prevu": e.get("f"), "precedent": e.get("p"), "reel": e.get("a"), "ecart_calcule": surprise(e), "a_verifier": bad})
                    new.append(it)
                    done["rel:" + ev_id(e)] = stamp
                if new:
                    keep = {x["id"]: x for x in out.get("releases") or []}
                    keep.update({x["id"]: x for x in new})
                    out["releases"] = sorted(keep.values(), key=lambda x: x.get("date", ""), reverse=True)[:lim["keep_releases"]]
                    changed = True
        elif kind == "scenarios":
            lines = ["ID: %s\n  %s" % (ev_id(e), ev_line(e)) for e in payload]
            ctx = build_context(d, cfg, ["prix", "fed", "taux", "positionnement", "geo", "titres"]) + "\n\n## ANNONCES À VENIR\n" + "\n".join(lines)
            prompt = "Prépare les scénarios pour ces annonces à venir.\n\n%s\n\n# CONTEXTE\n%s" % (SCENARIO_SCHEMA, ctx)
            r = run.generate("scenarios", "strong", SYSTEM, prompt, ctx)
            if r:
                obj, model, bad = r
                byid = {ev_id(e): e for e in payload}
                new = []
                for it in obj.get("annonces") or []:
                    e = byid.get(it.get("id"))
                    if not e:
                        continue
                    it.update({"gen": stamp, "model": model, "date": utc_min(e.get("d")), "devise": e.get("c"), "titre": e.get("t"), "prevu": e.get("f"), "precedent": e.get("p"), "a_verifier": bad})
                    new.append(it)
                    done["sc:" + ev_id(e)] = stamp
                if new:
                    keep = {x["id"]: x for x in out.get("scenarios") or []}
                    keep.update({x["id"]: x for x in new})
                    cut = (NOW - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M")
                    out["scenarios"] = sorted([x for x in keep.values() if x.get("date", "") >= cut], key=lambda x: x.get("date", ""))[:lim["keep_scenarios"]]
                    changed = True

    # --- Écriture -------------------------------------------------------------------------------------------------------
    status = "quota" if run.stop and "quota" in (state.get("last_error") or "") else "plafond" if run.stop and "plafond" in run.stop else "erreur" if run.stop else "ok"
    meta = {"provider": run.prov, "status": status, "status_msg": run.stop or "", "calls_today": state["calls"], "cap": lim["max_calls_per_day"], "disclaimer": DISCLAIMER}
    if changed:
        out.update({"v": 1, "generated": stamp})
        out.update(meta)
        save_json(OUT_PATH, out)
        state["last_error"] = None if status == "ok" else state.get("last_error")
        if status == "ok":
            state["backoff_until"] = None   # une réussite efface toute pause restée enregistrée
        print("Écrit %s (%d appel(s) réussi(s))." % (os.path.relpath(OUT_PATH, ROOT), run.made))
    elif (out or run.stop) and any(out.get(k) != v for k, v in meta.items() if k != "calls_today"):
        out.update(meta)
        save_json(OUT_PATH, out)
        print("Statut mis à jour : %s." % status)
    else:
        print("Rien de nouveau à analyser.")
    save_json(STATE_PATH, state)
    if run.stop:
        print("Arrêt anticipé : %s" % run.stop)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:   # une erreur du script ne doit jamais faire échouer le workflow ni toucher aux données existantes
        print("Erreur inattendue de l'analyste IA (données existantes conservées) : %s" % e)
        sys.exit(0)
