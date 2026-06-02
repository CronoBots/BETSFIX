"""Module FOOT (Coupe du Monde + grandes compétitions) — **séparé** du tennis/basket.

Spécificité : 3 issues (1-X-2, le match nul existe). Modèle : Elo d'équipe
(tools/build_foot_elo.py) -> supériorité de buts -> double Poisson -> P(1)/P(X)/P(2),
confronté au 1X2 Unibet pour repérer une value. Filtre « grandes compétitions » par ID
(Coupe du Monde + top championnats + C1/C3), pas les petits championnats.

⚠️ Modèle jeune + venues neutres en CdM : avantage terrain faible, value à confirmer.
Sources gratuites : SofaScore + Unibet BE.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone

import httpx

from app import flags, sofa_http, sportcache, tracking, web
from app.dependencies import get_provider
from app.textutil import name_tokens, names_match

log = logging.getLogger("uvicorn")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELO_PATH = os.path.join(_ROOT, "data", "foot_elo.json")

# Grandes compétitions (SofaScore unique-tournament id -> libellé court).
MAJOR_TIDS = {16: "Coupe du Monde", 17: "Premier League", 8: "LaLiga", 23: "Serie A",
              35: "Bundesliga", 34: "Ligue 1", 7: "Ligue des Champions",
              679: "Europa League", 1: "Euro", 18: "Coupe du Monde",
              851: "Amicaux Int."}


def _short_comp(name: str) -> str:
    """Abrège les noms de compétition trop longs pour l'en-tête (ex. « Amicaux Int. »)."""
    low = (name or "").lower()
    if "amicaux" in low and "internati" in low:
        return "Amicaux Int."
    return name or "Football"

# Compétitions à venues majoritairement NEUTRES : le « domicile » SofaScore est
# arbitraire (sauf pays hôte), donc aucun avantage terrain ne doit s'appliquer.
NEUTRAL_COMPS = {"Coupe du Monde", "Euro"}

HOME_ADV = 35.0           # faible : beaucoup de venues neutres en grand tournoi
GOALS_TOTAL = 2.7         # total de buts moyen (baseline)
SUP_PER_100 = 0.45        # 100 pts Elo ~ 0.45 but de supériorité
HORIZON_DAYS = 14         # la CdM démarre dans ~11 jours -> fenêtre large
MODEL_TRUST = 0.50
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.12, 0.80
MAX_DISAGREEMENT = 0.15    # si le modèle dépasse le marché de +15 pts, c'est le modèle
                           # (Elo jeune) qui a tort -> pas de value (garde-fou comme le tennis)

SOFA_B = "https://api.sofascore.com/api/v1"
SOFA_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
          "Origin": "https://www.sofascore.com"}
UNIBET_B = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
UNIBET_PARAMS = {"lang": "fr_BE", "market": "BE", "client_id": "2", "channel_id": "1"}
UNIBET_PARAMS_EN = {**UNIBET_PARAMS, "lang": "en_GB"}   # noms anglais pour matcher l'Elo
UNIBET_H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Referer": "https://www.unibet.be/"}


# ----------------------------------------------------------------- modèle
def load_elo(path: str = ELO_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _pois(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _lambdas(elo_home: float, elo_away: float, neutral: bool = False) -> tuple[float, float]:
    """Buts attendus (domicile, extérieur) selon l'Elo + avantage terrain.

    `neutral=True` (CdM/Euro) annule l'avantage terrain : le « domicile » est arbitraire."""
    home_adv = 0.0 if neutral else HOME_ADV
    sup = (elo_home + home_adv - elo_away) / 100.0 * SUP_PER_100
    return max(0.15, (GOALS_TOTAL + sup) / 2), max(0.15, (GOALS_TOTAL - sup) / 2)


def goals_markets(elo_home: float | None, elo_away: float | None,
                  neutral: bool = False) -> dict | None:
    """Marchés de buts dérivés du double Poisson : O/U 2.5 et BTTS (les deux marquent)."""
    if elo_home is None or elo_away is None:
        return None
    lh, la = _lambdas(elo_home, elo_away, neutral)
    lt = lh + la
    p_le2 = math.exp(-lt) * (1 + lt + lt * lt / 2)   # P(total ≤ 2 buts)
    btts = (1 - math.exp(-lh)) * (1 - math.exp(-la))
    return {"over25": 1 - p_le2, "btts": btts}


def outcome_probs(elo_home: float | None, elo_away: float | None,
                  kmax: int = 10, neutral: bool = False) -> tuple[float, float, float] | None:
    """(P(domicile), P(nul), P(extérieur)) via double Poisson dérivé de l'Elo."""
    if elo_home is None or elo_away is None:
        return None
    lh, la = _lambdas(elo_home, elo_away, neutral)
    ph = [_pois(i, lh) for i in range(kmax + 1)]
    pa = [_pois(j, la) for j in range(kmax + 1)]
    p1 = px = p2 = 0.0
    for i in range(kmax + 1):
        for j in range(kmax + 1):
            pr = ph[i] * pa[j]
            if i > j:
                p1 += pr
            elif i == j:
                px += pr
            else:
                p2 += pr
    tot = p1 + px + p2
    return (p1 / tot, px / tot, p2 / tot) if tot else None


_norm = name_tokens  # normalisation centralisée (cf. app/textutil.py)


def _devig3(o1, ox, o2):
    odds = [o1, ox, o2]
    if not all(odds):
        return None
    raws = [1 / o for o in odds]
    tot = sum(raws)
    return [r / tot for r in raws]


# ----------------------------------------------------------------- données
async def _get(client, base, path, params=None):
    key = base + path + (str(sorted(params.items())) if params else "")
    cached = sportcache.get(key)
    if cached is not None:
        return cached
    is_sofa = base == SOFA_B
    if is_sofa and sportcache.blocked():   # disjoncteur ouvert -> on ne tape pas SofaScore
        return None
    try:
        # SofaScore -> curl_cffi (empreinte TLS Chrome, anti-403) ; le reste -> httpx fourni.
        if is_sofa:
            r = await sofa_http.get(base + path, params=params)
        else:
            r = await client.get(base + path, params=params, timeout=20)
        if is_sofa and r.status_code in (403, 429):
            sportcache.trip()
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    # les listes de saisons changent rarement -> TTL long ; le reste -> TTL court
    sportcache.put(key, data, ttl=3600 if "/seasons" in path else sportcache.DEFAULT_TTL)
    return data


async def _season_id(client, tid):
    data = await _get(client, SOFA_B, f"/unique-tournament/{tid}/seasons")
    s = (data or {}).get("seasons") or []
    return s[0]["id"] if s else None


async def _upcoming_games(client) -> list[dict]:
    """Matchs à venir des grandes compétitions (fenêtre HORIZON_DAYS)."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=HORIZON_DAYS)
    games, seen = [], set()
    for tid, label in MAJOR_TIDS.items():
        sid = await _season_id(client, tid)
        if not sid:
            continue
        for page in range(2):
            data = await _get(client, SOFA_B,
                              f"/unique-tournament/{tid}/season/{sid}/events/next/{page}")
            evs = (data or {}).get("events") or []
            for ev in evs:
                st = (ev.get("status") or {}).get("type")
                ts = ev.get("startTimestamp")
                if st not in ("notstarted", "inprogress") or ev.get("id") in seen:
                    continue
                start = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
                if start and start > horizon:
                    continue
                seen.add(ev["id"])
                ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
                games.append({
                    "id": ev["id"], "comp": label,
                    "home_id": ht.get("id"), "away_id": at.get("id"),
                    "home": ht.get("name", ""), "away": at.get("name", ""),
                    "start": ts, "status": st,
                })
            if not (data or {}).get("hasNextPage"):
                break
    games.sort(key=lambda g: g["start"] or 0)
    return games


async def _unibet_odds(client) -> list[dict]:
    """Cotes 1X2 foot Unibet : [{home_tokens, away_tokens, o1, ox, o2}]."""
    data = await _get(client, UNIBET_B, "/listView/football.json", UNIBET_PARAMS)
    out = []
    for entry in (data or {}).get("events", []) or []:
        ev = entry.get("event") or {}
        offers = entry.get("betOffers") or []
        main = next((b for b in offers if len((b.get("outcomes") or [])) == 3), None)
        if not main:
            continue
        outs = main["outcomes"]

        def dec(o):
            v = o.get("odds")
            return round(v / 1000, 3) if isinstance(v, (int, float)) else None
        # ordre Kambi : 1 (home), X (draw), 2 (away)
        out.append({"home_tokens": _norm(ev.get("homeName", "")),
                    "away_tokens": _norm(ev.get("awayName", "")),
                    "day": _odds_day(ev.get("start")),
                    "o1": dec(outs[0]), "ox": dec(outs[1]), "o2": dec(outs[2])})
    return out


def _odds_day(value):
    """Date (UTC) d'un événement Unibet, pour désambiguïser le matching par noms."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except (ValueError, TypeError):
        return None


def _match_odds(game, odds_list):
    """Cotes 1X2 Unibet d'un match : matching par noms NON génériques + même date.

    On exige un token discriminant partagé des DEUX côtés (names_match ignore « united »,
    « fc »…) et la même date si connue, pour ne pas coller les cotes d'un autre match."""
    ht, at = _norm(game["home"]), _norm(game["away"])
    ts = game.get("start")
    gday = datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else None
    for o in odds_list:
        if not (names_match(ht, o["home_tokens"]) and names_match(at, o["away_tokens"])):
            continue
        if gday is not None and o["day"] is not None and o["day"] != gday:
            continue
        return o["o1"], o["ox"], o["o2"]
    return None, None, None


async def board() -> list[dict]:
    elo = load_elo()
    async with httpx.AsyncClient() as client:
        client.headers.update(SOFA_H)
        games = await _upcoming_games(client)
        client.headers.update(UNIBET_H)
        odds = await _unibet_odds(client)

    rows = []
    for g in games:
        eh = (elo.get(str(g["home_id"])) or {}).get("elo")
        ea = (elo.get(str(g["away_id"])) or {}).get("elo")
        if eh is None or ea is None:   # Elo absent (ex. sélection CdM non couverte)
            log.info("foot: Elo manquant pour %s vs %s -> pas de prédiction",
                     g.get("home"), g.get("away"))
        neutral = g.get("comp") in NEUTRAL_COMPS
        probs = outcome_probs(eh, ea, neutral=neutral)
        o1, ox, o2 = _match_odds(g, odds)
        imp = _devig3(o1, ox, o2)
        pick = None
        if probs and imp:
            labels = [("1", g["home"], o1), ("X", "Match nul", ox), ("2", g["away"], o2)]
            for i, (code, name, odd) in enumerate(labels):
                fair = MODEL_TRUST * probs[i] + (1 - MODEL_TRUST) * imp[i]
                edge = fair - imp[i]
                if (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp[i] <= MAX_IMPLIED
                        and (probs[i] - imp[i]) <= MAX_DISAGREEMENT   # modèle pas "aveugle"
                        and odd and (not pick or edge > pick["edge"])):
                    pick = {"code": code, "team": name, "odds": odd, "edge": edge}
        rows.append({**g, "probs": probs, "goals": goals_markets(eh, ea, neutral=neutral),
                     "o1": o1, "ox": ox, "o2": o2, "imp": imp, "pick": pick})
    return rows


def board_from_store() -> list[dict]:
    """Repli : reconstruit la board foot depuis le SUIVI persisté (tracking_foot.json)
    quand SofaScore est en pause. Évite un onglet Foot vide alors que les mêmes matchs
    apparaissent dans les picks de l'accueil (qui lisent déjà le store)."""
    store = tracking.load(FOOT_TRACK_PATH)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=HORIZON_DAYS)
    rows = []
    for rec in store.values():
        if rec.get("result"):
            continue
        st = rec.get("start_time")
        try:
            dt = datetime.fromisoformat(st) if st else None
        except ValueError:
            dt = None
        if dt is None or dt < now or dt > horizon:   # uniquement les matchs À VENIR
            continue
        pr = ((rec["p_home"], rec["p_draw"], rec["p_away"])
              if rec.get("p_home") is not None else None)
        v = rec.get("value_pick")
        pick = ({"code": v["code"], "team": v.get("team"), "odds": v.get("odds"),
                 "edge": v.get("edge")} if v else None)
        o1, ox, o2 = rec.get("o1"), rec.get("ox"), rec.get("o2")
        ph, pa = rec.get("public_home"), rec.get("public_away")
        rows.append({
            "id": rec.get("match_id"), "comp": rec.get("comp"), "status": "notstarted",
            "home": rec.get("home", ""), "away": rec.get("away", ""),
            "probs": pr, "goals": None, "o1": o1, "ox": ox, "o2": o2,
            "imp": _devig3(o1, ox, o2), "pick": pick, "start": dt.timestamp(),
            "votes": (ph, pa) if ph is not None else None,
        })
    rows.sort(key=lambda g: g["start"] or 0)
    return rows


def _ub_dt(value):
    """Horodatage ISO Unibet -> datetime UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _elo_index(elo: dict):
    """Liste (tokens, elo) pour résoudre l'Elo par NOM (Unibet en_GB ~ Elo anglais)."""
    return [(name_tokens(v.get("name", "")), v.get("elo")) for v in elo.values() if v.get("name")]


def _elo_for(tokens, index):
    for toks, e in index:
        if names_match(tokens, toks):
            return e
    return None


async def board_from_unibet() -> list[dict]:
    """Board foot construite UNIQUEMENT depuis Unibet (matchs + cotes 1X2) + Elo par nom.

    Affichage en FRANÇAIS (fr_BE) ; l'Elo est résolu via les noms ANGLAIS (en_GB) liés par
    l'id Kambi. AUCUN appel SofaScore -> les matchs (dont les amicaux internationaux)
    s'affichent même quand SofaScore est en pause. On ne garde que ceux dont on connaît
    l'Elo des 2 équipes (filtre naturel : nations + grandes équipes ; esports exclus)."""
    elo = load_elo()
    index = _elo_index(elo)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=HORIZON_DAYS)
    async with httpx.AsyncClient(headers=UNIBET_H) as client:
        fr = await _get(client, UNIBET_B, "/listView/football.json", UNIBET_PARAMS)
        en = await _get(client, UNIBET_B, "/listView/football.json", UNIBET_PARAMS_EN)
    en_names = {}
    for entry in (en or {}).get("events", []) or []:
        ev = entry.get("event") or {}
        en_names[ev.get("id")] = (ev.get("homeName", ""), ev.get("awayName", ""))

    def _dec(o):
        vv = o.get("odds")
        return round(vv / 1000, 3) if isinstance(vv, (int, float)) else None

    rows, seen = [], set()
    for entry in (fr or {}).get("events", []) or []:
        ev = entry.get("event") or {}
        kid = ev.get("id")
        home, away = ev.get("homeName", ""), ev.get("awayName", "")
        group = _short_comp(ev.get("group"))
        path = " ".join(p.get("name", "") for p in (ev.get("path") or []))
        ctx = f"{group} {path}".lower()
        # match féminin : marqueur « (W) »/« (F) » sur un nom, ou compétition « Women/Féminin »
        female = ("(w)" in home.lower() or "(f)" in home.lower()
                  or "(w)" in away.lower() or "(f)" in away.lower()
                  or "women" in ctx or "fémin" in ctx or "femin" in ctx)
        # marqueur féminin retiré du nom affiché
        for mark in (" (W)", " (F)"):
            home, away = home.replace(mark, "").strip(), away.replace(mark, "").strip()
        # exclut l'esports (joueur entre parenthèses, groupe « Cyber »/path « Esports »).
        # NB : un éventuel « (W) » a déjà été retiré ci-dessus -> pas pris pour de l'esports.
        if "(" in home or "(" in away or "esport" in path.lower() or "cyber" in group.lower():
            continue
        if kid in seen:
            continue
        start = _ub_dt(ev.get("start"))
        if start is None or start > horizon:
            continue
        en_home, en_away = en_names.get(kid, (home, away))
        eh = _elo_for(name_tokens(en_home), index)
        ea = _elo_for(name_tokens(en_away), index)
        if eh is None or ea is None:          # Elo inconnu d'un camp -> on ne montre pas
            continue
        seen.add(kid)
        offers = entry.get("betOffers") or []
        main = next((b for b in offers if len(b.get("outcomes") or []) == 3), None)
        o1 = ox = o2 = None
        if main:
            outs = main["outcomes"]
            o1, ox, o2 = _dec(outs[0]), _dec(outs[1]), _dec(outs[2])
        probs = outcome_probs(eh, ea)
        imp = _devig3(o1, ox, o2)
        pick = None
        if probs and imp:
            for i, (code, nm, odd) in enumerate([("1", home, o1), ("X", "Match nul", ox), ("2", away, o2)]):
                fair = MODEL_TRUST * probs[i] + (1 - MODEL_TRUST) * imp[i]
                edge = fair - imp[i]
                if (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp[i] <= MAX_IMPLIED
                        and (probs[i] - imp[i]) <= MAX_DISAGREEMENT and odd
                        and (not pick or edge > pick["edge"])):
                    pick = {"code": code, "team": nm, "odds": odd, "edge": edge}
        status = "notstarted" if ev.get("state") == "NOT_STARTED" else "inprogress"
        rows.append({
            "id": kid, "comp": group, "status": status, "home": home, "away": away,
            "home_en": en_home, "away_en": en_away,   # noms anglais -> matcher SofaScore
            "probs": probs, "goals": goals_markets(eh, ea),
            "o1": o1, "ox": ox, "o2": o2, "imp": imp, "pick": pick,
            "start": start.timestamp(), "female": female,
        })
    rows.sort(key=lambda g: g["start"] or 0)
    return rows


async def _resolve_sofa_ids(rows: list[dict]) -> None:
    """Retrouve l'id SofaScore de chaque match Unibet (par noms ANGLAIS + date) et le pose
    dans row['id'] -> permet l'enrichissement SofaScore (votes/forme) sur une board Unibet.
    Best-effort : si SofaScore est en pause, on n'y touche pas (les matchs restent affichés)."""
    if not rows or sportcache.blocked():
        return
    days = sorted({datetime.fromtimestamp(r["start"], tz=timezone.utc).date().isoformat()
                   for r in rows if r.get("start")})
    index = []   # (home_tokens, away_tokens, date_iso, sofa_id)
    async with httpx.AsyncClient(headers=SOFA_H) as c:
        for day in days:
            data = await _get(c, SOFA_B, f"/sport/football/scheduled-events/{day}")
            for ev in (data or {}).get("events", []) or []:
                ts = ev.get("startTimestamp")
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else None
                index.append((name_tokens((ev.get("homeTeam") or {}).get("name", "")),
                              name_tokens((ev.get("awayTeam") or {}).get("name", "")),
                              d, ev.get("id")))
    for r in rows:
        rd = datetime.fromtimestamp(r["start"], tz=timezone.utc).date().isoformat() if r.get("start") else None
        rh, ra = name_tokens(r.get("home_en") or r["home"]), name_tokens(r.get("away_en") or r["away"])
        for ht, at, d, sid in index:
            if names_match(rh, ht) and names_match(ra, at) and (d is None or rd is None or d == rd):
                # id SofaScore stocké À PART (r['id'] reste l'id Unibet, clé de store STABLE ->
                # pas de doublon quand SofaScore passe de bloqué à résolu entre deux runs).
                r["sofa_id"] = sid
                r["sofa_ok"] = True
                break


def _attach_from_store(rows: list[dict]) -> None:
    """Relie chaque match Unibet au suivi (par nom + date) -> id SofaScore + votes, SANS
    aucun appel SofaScore (le store est peuplé en fond). Garde le RENDU 100 % hors-SofaScore
    (les noms anglais home_en/away_en matchent les noms SofaScore du store)."""
    store = tracking.load(FOOT_TRACK_PATH)
    idx = []
    for rec in store.values():
        st = rec.get("start_time")
        try:
            d = datetime.fromisoformat(st).date() if st else None
        except ValueError:
            d = None
        idx.append((name_tokens(rec.get("home", "")), name_tokens(rec.get("away", "")), d, rec))
    for r in rows:
        rd = datetime.fromtimestamp(r["start"], tz=timezone.utc).date() if r.get("start") else None
        # on tente le matching sur les noms FR (board Unibet) ET EN (SofaScore) : le store peut
        # contenir l'un ou l'autre selon la source qui l'a peuplé.
        rh_fr, ra_fr = name_tokens(r.get("home", "")), name_tokens(r.get("away", ""))
        rh_en, ra_en = name_tokens(r.get("home_en", "")), name_tokens(r.get("away_en", ""))
        best = None
        for sht, sat, d, rec in idx:
            if d is not None and rd is not None and d != rd:
                continue
            if ((names_match(rh_fr, sht) or names_match(rh_en, sht))
                    and (names_match(ra_fr, sat) or names_match(ra_en, sat))):
                best = rec
                if rec.get("public_home") is not None:
                    break          # on privilégie le rec QUI A des votes (dédoublonnage transition)
        if best is not None:
            mid = best.get("match_id")
            if mid:
                r["id"] = mid
                r["sofa_ok"] = True     # id SofaScore résolu -> fiche détaillée cliquable
            if best.get("public_home") is not None:
                r["votes"] = (best["public_home"], best["public_away"])


async def board_resilient() -> list[dict]:
    """SOURCE UNIQUE des matchs foot (onglet ET accueil). MATCHS + cotes via UNIBET (français),
    proba via Elo (par nom), enrichissement (id SofaScore + votes) lu dans le STORE
    -> rendu 100 % hors-SofaScore. Replis : board SofaScore directe puis store."""
    try:
        rows = await asyncio.wait_for(board_from_unibet(), timeout=2.5)
        if rows:
            _attach_from_store(rows)       # store local, aucun appel SofaScore
            return rows
    except (Exception, asyncio.TimeoutError):
        pass
    return board_from_store()              # repli store (toujours hors-SofaScore au rendu)


async def enrich_display(rows: list[dict]) -> None:
    """Ajoute votes des fans + forme d'avant-match aux matchs affichés (à venir / en direct).

    Passe par le provider SofaScore **caché** (stale-while-revalidate) -> pas de surcharge :
    seul le tout premier affichage touche le réseau, ensuite c'est servi du cache. Limité
    aux matchs jouables et tolérant aux erreurs (si ça échoue, on n'affiche juste rien).
    """
    prov = get_provider()
    # Uniquement les matchs dont l'id SofaScore est CONFIRMÉ (sofa_ok) : on ne tire jamais de
    # votes sur un id Unibet (mauvais id -> requêtes inutiles -> risque de pause).
    targets = [r for r in rows if r.get("status") in ("notstarted", "inprogress")
               and r.get("sofa_id")]

    async def votes(r: dict) -> None:
        try:
            v = await prov.get_votes(r["sofa_id"])
            if v.home_percent is not None:
                r["votes"] = (v.home_percent, v.away_percent)
        except Exception:
            pass

    async def form(r: dict) -> None:
        try:
            pf = await prov.get_event_pregame_form(r["sofa_id"])
            if pf.home.form or pf.away.form:
                r["form"] = (pf.home.form, pf.away.form)
        except Exception:
            pass

    # Votes pour TOUTES les rencontres (priorité barre PUBLIC) ; forme (plus lourde) limitée
    # aux premières. La concurrence est bornée par le provider (sémaphore + min_gap).
    async def one(r: dict, with_form: bool) -> None:
        await votes(r)
        if with_form:
            await form(r)

    if targets:
        try:   # best-effort : si SofaScore traîne, on persiste ce qu'on a déjà
            await asyncio.wait_for(asyncio.gather(
                *[one(r, i < 14) for i, r in enumerate(targets[:40])],
                return_exceptions=True), timeout=30.0)
        except asyncio.TimeoutError:
            pass


# ----------------------------------------------------------------- rendu
def _fmt_time(ts) -> str:
    if not ts:
        return ""
    return web.fmt_local(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat())


async def _finished_games(client, days: int = 3) -> list[dict]:
    """Matchs terminés récents des grandes compétitions (section Terminés)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    out = []
    for tid, label in MAJOR_TIDS.items():
        sid = await _season_id(client, tid)
        if not sid:
            continue
        data = await _get(client, SOFA_B, f"/unique-tournament/{tid}/season/{sid}/events/last/0")
        for ev in (data or {}).get("events", []) or []:
            if (ev.get("status") or {}).get("type") != "finished" or ev.get("winnerCode") not in (1, 2, 3):
                continue
            ts = ev.get("startTimestamp") or 0
            if ts < cutoff:
                continue
            ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
            out.append({"comp": label, "home_id": ht.get("id"), "away_id": at.get("id"),
                        "home": ht.get("name", ""), "away": at.get("name", ""),
                        "winner": {1: "home", 2: "away", 3: "draw"}[ev["winnerCode"]],
                        "hs": (ev.get("homeScore") or {}).get("current"),
                        "as": (ev.get("awayScore") or {}).get("current"), "ts": ts})
    out.sort(key=lambda g: g["ts"], reverse=True)
    return out[:10]


async def finished() -> list[dict]:
    elo = load_elo()
    async with httpx.AsyncClient(headers=SOFA_H) as c:
        games = await _finished_games(c)
    for g in games:
        eh = (elo.get(str(g["home_id"])) or {}).get("elo")
        ea = (elo.get(str(g["away_id"])) or {}).get("elo")
        g["probs"] = outcome_probs(eh, ea)
    return games


def finished_from_store(limit: int = 8) -> list[dict]:
    """Matchs récemment terminés depuis le suivi (SANS appel SofaScore) — pour le rendu."""
    store = tracking.load(FOOT_TRACK_PATH)
    out = []
    for rec in store.values():
        res = rec.get("result")
        if not res or res.get("winner") not in ("home", "away", "draw") or res.get("void"):
            continue
        pr = ((rec["p_home"], rec["p_draw"], rec["p_away"])
              if rec.get("p_home") is not None else None)
        out.append({"comp": rec.get("comp"), "home": rec.get("home", ""), "away": rec.get("away", ""),
                    "winner": res["winner"], "probs": pr, "hs": None, "as": None,
                    "_at": res.get("settled_at", "")})
    out.sort(key=lambda g: g["_at"], reverse=True)
    return out[:limit]


def render(rows: list[dict], finished_rows: list[dict] | None = None,
           paused: bool = False, frag: bool = False) -> str:
    e = html.escape

    def model_line(r):
        # Barre de cotes Unibet claire 1-X-2 (home / Nul / away) ; sinon état Elo.
        sub = ""
        if not r.get("probs"):
            sub += '<div class="dim">Elo indisponible</div>'
        if r.get("o1"):
            pr = r.get("probs")
            hi = max(range(3), key=lambda k: pr[k]) if pr else None   # issue pronostiquée
            sub += web.odds_row([(r["home"], r["o1"]), ("Nul", r["ox"]), (r["away"], r["o2"])],
                                highlight_idx=hi)
        fm = r.get("form")
        if fm:
            sub += web.form_compare(r["home"], fm[0], r["away"], fm[1])
        # (les votes communauté sont déjà dans la barre PUBLIC -> pas de doublon ici)
        return sub

    value, live, upcoming = [], [], []
    for r in rows:
        pk = r.get("pick")
        badge = (f'<span class="badge b-val">VALUE +{round(pk["edge"]*100,1)} pts</span>'
                 if pk else "")
        base = {"tour": r.get("comp"), "status": r["status"], "time": _fmt_time(r.get("start")),
                "start_ts": r.get("start"), "home": r["home"], "away": r["away"],
                "female": r.get("female"),
                "home_flag": flags.flag(r["home"]), "away_flag": flags.flag(r["away"]),
                "url": f'/foot/match/{r["id"]}' if r.get("sofa_ok") else None,
                **web.bars_foot(r.get("probs"), r.get("imp"), r.get("votes"), r["home"], r["away"])}
        (live if r["status"] == "inprogress" else upcoming).append(
            {**base, "prob": r.get("probs"), "sub": model_line(r),
             "badge": badge, "pick": bool(pk)})
        if pk:
            _hi = {"1": 0, "X": 1, "2": 2}.get(pk.get("code"))
            oddsrow = web.odds_row([(r["home"], r.get("o1")), ("Nul", r.get("ox")), (r["away"], r.get("o2"))],
                                   highlight_idx=_hi)
            value.append({**base, "badge": badge, "pick": True,
                          "sub": oddsrow + f'<div class="dim">pari : <b class="pos">{e(pk["team"])}</b> '
                                 f'@{pk["odds"]} · +{round(pk["edge"]*100,1)} pts (à confirmer)</div>'})

    fin = []
    for r in (finished_rows or []):
        probs = r.get("probs")
        sub, badge = "", ""
        if probs:
            names = [r["home"], "nul", r["away"]]
            fav_i = max(range(3), key=lambda i: probs[i])
            wi = {"home": 0, "draw": 1, "away": 2}[r["winner"]]
            ok = fav_i == wi
            badge = ('<span class="pos">✓ modèle ok</span>' if ok
                     else '<span class="neg">✗ raté</span>')
            wname = {"home": r["home"], "draw": "Match nul", "away": r["away"]}[r["winner"]]
            sub = (f'<div class="dim">prédit : <b>{e(names[fav_i])}</b> {round(probs[fav_i]*100)}% '
                   f'· résultat : <b>{e(wname)}</b></div>')
        fin.append({"tour": r.get("comp"), "status": "finished", "home": r["home"], "away": r["away"],
                    "home_flag": flags.flag(r["home"]), "away_flag": flags.flag(r["away"]),
                    "score": f'{r.get("hs")}-{r.get("as")}' if r.get("hs") is not None else "terminé",
                    "sub": sub, "badge": badge})

    intro = ('⚽ <b>Coupe du Monde & grandes compétitions</b> — Elo de sélection (1-X-2 via '
             'double Poisson) vs Unibet. Modèle jeune + venues neutres : value à <b>confirmer</b>.')
    if not (value or live or upcoming or fin):
        intro += ' La Coupe du Monde démarre le 11 juin.'
    return web.render_sport_matches("foot", "Football", value, live, upcoming, fin,
                                    intro=intro, paused=paused, frag=frag)


# ----------------------------------------------------------------- suivi (3 issues)
FOOT_TRACK_PATH = os.path.join(_ROOT, "data", "tracking_foot.json")
_CODE_TO_WINNER = {"1": "home", "X": "draw", "2": "away"}


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _clamp(p):
    return min(max(p if p is not None else 1 / 3, 1e-6), 1 - 1e-6)


def _upsert(store: dict, g: dict, now: str) -> bool:
    rec = store.get(str(g["id"]), {})
    if rec.get("result"):
        return False
    pr, pk = g.get("probs"), g.get("pick")
    # match_id = id SofaScore (résolu via l'agenda) pour le détail/settle/votes ; à défaut on
    # garde l'id déjà connu, sinon l'id Unibet. La CLÉ du store reste g['id'] (Unibet, stable).
    sofa_id = g.get("sofa_id") or rec.get("match_id") or g["id"]
    rec.update({
        "match_id": sofa_id, "sport": "foot", "comp": g.get("comp"),
        "home": g["home"], "away": g["away"], "start_time": _iso(g.get("start")),
        "p_home": pr[0] if pr else None, "p_draw": pr[1] if pr else None,
        "p_away": pr[2] if pr else None,
        "o1": g.get("o1"), "ox": g.get("ox"), "o2": g.get("o2"),
        "value_pick": ({"code": pk["code"], "team": pk["team"], "odds": pk["odds"],
                        "edge": pk["edge"]} if pk else None),
        "last_update": now,
    })
    vt = g.get("votes")               # votes des fans (persistés -> barre PUBLIC stable)
    if vt and vt[0] is not None:
        rec["public_home"], rec["public_away"] = vt[0], vt[1]
    rec.setdefault("first_logged", now)
    for k in ("o1", "ox", "o2"):
        rec.setdefault("open_" + k, g.get(k))
    store[str(g["id"])] = rec
    return True


async def run_snapshot() -> int:
    store = tracking.load(FOOT_TRACK_PATH)
    now = datetime.now(timezone.utc).isoformat()
    # On part des matchs RÉELLEMENT affichés (board Unibet, large : amicaux inclus) plutôt
    # que des seules grandes compétitions SofaScore -> les votes du public sont récupérés
    # pour CHAQUE rencontre existante, pas seulement la CdM & co.
    rows = await board_from_unibet()
    await _resolve_sofa_ids(rows)      # id SofaScore par nom+date (agenda du jour, large)
    await enrich_display(rows)         # votes + forme -> persistés dans le store
    n = 0
    for g in rows:
        if g.get("o1") and g.get("probs") and _upsert(store, g, now):
            n += 1
    tracking.save(store, FOOT_TRACK_PATH)
    return n


async def run_settle() -> int:
    store = tracking.load(FOOT_TRACK_PATH)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    s = 0
    async with httpx.AsyncClient(headers=SOFA_H) as c:
        for rec in list(store.values()):
            if rec.get("result"):
                continue
            data = await _get(c, SOFA_B, f"/event/{rec['match_id']}")
            ev = (data or {}).get("event") or {}
            wc = ev.get("winnerCode")
            if (ev.get("status") or {}).get("type") == "finished" and wc in (1, 2, 3):
                winner = {1: "home", 2: "away", 3: "draw"}[wc]
                pnl = None
                pk = rec.get("value_pick")
                if pk and pk.get("odds"):
                    won = _CODE_TO_WINNER.get(pk["code"]) == winner
                    pnl = (pk["odds"] - 1) if won else -1.0
                rec["result"] = {"winner": winner, "settled_at": now, "value_pnl": pnl}
                s += 1
                continue
            # Match jamais terminé longtemps après l'heure prévue -> annulé/reporté : on clôt.
            if _stale(rec, now_dt) and tracking.void(
                    store, rec["match_id"], "non terminé (reporté/annulé ?)", now):
                s += 1
    tracking.save(store, FOOT_TRACK_PATH)
    return s


def _stale(rec: dict, now_dt: datetime, days: int = 3) -> bool:
    """Vrai si le match était prévu il y a plus de `days` jours et n'a pas abouti."""
    st = rec.get("start_time")
    if not st:
        return False
    try:
        dt = datetime.fromisoformat(st)
    except ValueError:
        return False
    return (now_dt - dt) > timedelta(days=days)


def _clv(rec) -> float | None:
    pk = rec.get("value_pick")
    if not pk:
        return None
    keys = {"1": ("open_o1", "o1"), "X": ("open_ox", "ox"), "2": ("open_o2", "o2")}.get(pk["code"])
    if not keys:
        return None
    op, cl = rec.get(keys[0]), rec.get(keys[1])
    if not op or not cl or op <= 1 or cl <= 1:
        return None
    return op / cl - 1.0


def report(store: dict) -> dict:
    # Les void (annulés/reportés, sans gagnant) sont exclus des métriques.
    settled = [r for r in store.values() if r.get("result") and r.get("p_home") is not None
               and not r["result"].get("void")]
    n = len(settled)
    brier = ll = correct = 0.0
    mbrier = mll = 0.0
    mn = 0
    for r in settled:
        p = [_clamp(r["p_home"]), _clamp(r["p_draw"]), _clamp(r["p_away"])]
        w = {"home": 0, "draw": 1, "away": 2}[r["result"]["winner"]]
        y = [0, 0, 0]
        y[w] = 1
        brier += sum((p[i] - y[i]) ** 2 for i in range(3))
        ll += -math.log(p[w])
        if max(range(3), key=lambda i: p[i]) == w:
            correct += 1
        mk = _devig3(r.get("o1"), r.get("ox"), r.get("o2"))
        if mk:
            mk = [_clamp(x) for x in mk]
            mbrier += sum((mk[i] - y[i]) ** 2 for i in range(3))
            mll += -math.log(mk[w])
            mn += 1
    clvs = [c for c in (_clv(r) for r in settled) if c is not None]
    picks = [r for r in store.values() if r.get("value_pick") and r.get("result")
             and r["result"].get("value_pnl") is not None]
    pnl = sum(r["result"]["value_pnl"] for r in picks)
    wins = sum(1 for r in picks if r["result"]["value_pnl"] > 0)
    return {
        "matchs_suivis": len(store), "matchs_regles": len(settled), "predictions_evaluees": n,
        "precision_modele": round(correct / n, 3) if n else None,
        "brier": round(brier / n, 4) if n else None,
        "brier_marche": round(mbrier / mn, 4) if mn else None,
        "bat_le_marche": (None if not mn else (brier / n) < (mbrier / mn)),
        "log_loss": round(ll / n, 4) if n else None,
        "log_loss_marche": round(mll / mn, 4) if mn else None,
        "clv_evalue": len(clvs),
        "clv_moyen": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "value_paris_regles": len(picks),
        "value_taux_reussite": round(wins / len(picks), 3) if picks else None,
        "value_pnl_unites": round(pnl, 2) if picks else 0.0,
        "value_roi": round(pnl / len(picks), 3) if picks else None,
    }


def render_dashboard(store: dict, rep: dict) -> str:
    e = html.escape

    def card(label, value, sub="", color="var(--text)"):
        return (f'<div class="card"><div class="lbl">{e(label)}</div>'
                f'<div class="val" style="color:{color}">{e(str(value))}</div>'
                f'<div class="sub">{e(sub)}</div></div>')

    def num(x):
        return x if x is not None else "—"

    prec = rep.get("precision_modele")
    pc = "#9aa0a6" if prec is None else ("#34d27b" if prec >= 0.45 else "#f25d6e")
    bmod, bmkt = rep.get("brier"), rep.get("brier_marche")
    bc = "#e8eaed"
    bsub = "1-X-2 (plus bas = mieux)"
    if bmod is not None and bmkt is not None:
        beat = rep.get("bat_le_marche")
        bc = "#34d27b" if beat else "#f25d6e"
        bsub = "bat le marché ✓" if beat else f"marché : {bmkt}"
    clv = rep.get("clv_moyen")
    cc = "#9aa0a6" if clv is None else ("#34d27b" if clv > 0 else "#f25d6e")
    ctxt = "—" if clv is None else f"{'+' if clv >= 0 else ''}{round(clv*100,1)}%"
    roi = rep.get("value_roi")

    cards = "".join([
        card("Précision (1X2)", f"{round(prec*100)}%" if prec is not None else "—",
             f"{rep.get('predictions_evaluees',0)} matchs", pc),
        card("Brier modèle", num(bmod), bsub, bc),
        card("Brier marché", num(bmkt), "réf. à battre"),
        card("CLV moyen", ctxt, f"{rep.get('clv_evalue',0)} picks · >0 = edge", cc),
        card("Log-loss", num(rep.get("log_loss")), f"marché : {num(rep.get('log_loss_marche'))}"),
        card("Matchs suivis", rep.get("matchs_suivis", 0), f"{rep.get('matchs_regles',0)} réglés"),
    ])

    # Track record des paris foot
    bets = [r for r in store.values() if r.get("value_pick") and r.get("result")
            and r["result"].get("value_pnl") is not None]
    bets.sort(key=lambda r: r["result"].get("settled_at", ""), reverse=True)
    bets_html = ""
    if bets:
        def brow(r):
            v, res = r["value_pick"], r["result"]
            won = res["value_pnl"] > 0
            mark = ('<span class="pos">✓ gagné</span>' if won
                    else '<span class="neg">✗ perdu</span>')
            return (f'<tr><td>{e(r["home"])} v {e(r["away"])}<br>'
                    f'<span class="dim">{e(v["team"])} @{v["odds"]}</span></td><td>{mark}</td>'
                    f'<td class="{"pos" if won else "neg"}">'
                    f'{"+" if res["value_pnl"]>=0 else ""}{round(res["value_pnl"],2)}</td></tr>')
        pnl = rep.get("value_pnl_unites", 0) or 0
        bets_html = (
            f'<h2>Track record des paris foot ({len(bets)})</h2>'
            f'<div class="banner">Mise plate 1 unité. P&amp;L <b>{"+" if pnl>=0 else ""}{pnl} u</b> · '
            f'réussite {round((rep.get("value_taux_reussite") or 0)*100)}% · '
            f'ROI {round((roi or 0)*100)}%. Peu significatif tant qu\'on n\'a pas ~100 paris.</div>'
            '<table><tr><td class="dim">pari</td><td class="dim">résultat</td>'
            f'<td class="dim">P&amp;L (u)</td></tr>{"".join(brow(r) for r in bets[:30])}</table>')

    body = (f'<div class="grid">{cards}</div>'
            '<div class="banner">Perf <b>foot</b> — calibration <b>1-X-2 (3 issues)</b> : '
            'précision = l\'issue la plus probable est-elle la bonne ? Brier/log-loss '
            'multiclasses vs marché. Fiable à partir de ~100 matchs réglés.</div>'
            f'{bets_html}')
    return web.layout("Fiabilité foot", "foot", body, subnav="perf", refresh=True)
