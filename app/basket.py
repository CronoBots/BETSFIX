"""Module BASKET (NBA + WNBA) — **séparé du tennis**.

Modèle d'équipe simple et honnête : Elo d'équipe (tools/build_basket_elo.py) + avantage
du terrain -> probabilité de victoire, confrontée au moneyline Unibet pour repérer une
éventuelle value. Pas de simulation : un seul marché fiable (vainqueur) pour démarrer.

Deux ligues suivies ensemble (voir LEAGUES) : NBA (tournoi 132) et WNBA (486). Les ids
d'équipe SofaScore sont uniques entre ligues, donc un seul fichier Elo les contient
toutes. L'écart-type de marge diffère par ligue (NBA un peu plus dispersée).

Sources gratuites : SofaScore (scheduled-events basket) + Unibet BE (nba.json / wnba.json).
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

from app import sofa_http, sportcache, tracking, web
from app.dependencies import get_provider
from app.textutil import name_tokens, names_match

log = logging.getLogger("uvicorn")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELO_PATH = os.path.join(_ROOT, "data", "basket_elo.json")

WNBA_TID = 486
NBA_TID = 132
HOME_ADV = 65.0            # avantage du terrain en points Elo (~2.5-3 pts)
MODEL_TRUST = 0.50         # ancrage marché (l'Elo jeune est bruité -> on suit le book)
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.25, 0.75
MAX_DISAGREEMENT = 0.15    # si le modèle dépasse le marché de +15 pts, c'est le modèle
                           # (Elo jeune) qui a tort -> pas de value (garde-fou comme le tennis)

# Ligues suivies (nom SofaScore -> config). L'écart-type de marge diffère :
# la NBA a des scores plus élevés et des marges un peu plus dispersées que la WNBA.
LEAGUES = {
    "NBA":  {"tid": NBA_TID,  "unibet": "/listView/basketball/nba.json",  "sigma": 12.5},
    "WNBA": {"tid": WNBA_TID, "unibet": "/listView/basketball/wnba.json", "sigma": 11.0},
}

SOFA_B = "https://api.sofascore.com/api/v1"
SOFA_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
          "Origin": "https://www.sofascore.com"}
UNIBET_B = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
UNIBET_PARAMS = {"lang": "fr_BE", "market": "BE", "client_id": "2", "channel_id": "1"}
UNIBET_H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Referer": "https://www.unibet.be/"}


# ----------------------------------------------------------------- Elo / proba
def load_elo(path: str = ELO_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def win_prob(elo_home: float | None, elo_away: float | None) -> float | None:
    """Proba de victoire de l'équipe à domicile (avantage terrain inclus)."""
    if elo_home is None or elo_away is None:
        return None
    return expected(elo_home + HOME_ADV, elo_away)


SPREAD_SIGMA = 11.0       # écart-type de la marge (points) en WNBA


def _inv_norm(p: float) -> float:
    """Quantile de la loi normale (algorithme d'Acklam)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
         1.383577518672690e2, -3.066479806614716e1, 2.506628277459239e0]
    b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
         6.680131188771972e1, -1.328068155288572e1]
    cc = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838e0,
          -2.549732539343734e0, 4.374664141464968e0, 2.938163982698783e0]
    d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996e0,
         3.754408661907416e0]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((cc[0]*q+cc[1])*q+cc[2])*q+cc[3])*q+cc[4])*q+cc[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= 1 - pl:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((cc[0]*q+cc[1])*q+cc[2])*q+cc[3])*q+cc[4])*q+cc[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def expected_margin(p_home: float | None, sigma: float = SPREAD_SIGMA) -> float | None:
    """Marge attendue (points) de l'équipe à domicile, dérivée de la proba de victoire.

    `sigma` = écart-type de la marge de la ligue (WNBA ~11, NBA ~12.5).
    """
    if p_home is None:
        return None
    return sigma * _inv_norm(p_home)


_norm = name_tokens  # normalisation centralisée (cf. app/textutil.py)


def _devig(o1: float | None, o2: float | None) -> tuple[float, float] | None:
    if not o1 or not o2:
        return None
    a, b = 1 / o1, 1 / o2
    return a / (a + b), b / (a + b)


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
    sportcache.put(key, data, ttl=3600 if "/seasons" in path else sportcache.DEFAULT_TTL)
    return data


HORIZON_DAYS = 4          # fenêtre des matchs à venir (assez large pour les playoffs NBA espacés)


async def _season_id(client, tid: int):
    data = await _get(client, SOFA_B, f"/unique-tournament/{tid}/seasons")
    s = (data or {}).get("seasons") or []
    return s[0]["id"] if s else None


def _row_from_event(ev: dict, league: str) -> dict:
    ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
    return {
        "id": ev["id"], "league": league, "home_id": ht.get("id"), "away_id": at.get("id"),
        "home": ht.get("name", ""), "away": at.get("name", ""),
        "start": ev.get("startTimestamp"), "status": (ev.get("status") or {}).get("type"),
        "home_pts": (ev.get("homeScore") or {}).get("current"),
        "away_pts": (ev.get("awayScore") or {}).get("current"),
    }


async def _upcoming_games(client) -> list[dict]:
    """Matchs NBA + WNBA à venir / en cours (SofaScore).

    Deux sources fusionnées : l'agenda du jour (scheduled-events, calendriers denses
    comme la WNBA) ET les prochains matchs de chaque ligue (events/next, indispensable
    pour les playoffs NBA dont les matchs sont espacés de plusieurs jours).
    """
    now = datetime.now(timezone.utc)
    base = now.date()
    horizon = now + timedelta(days=HORIZON_DAYS)
    games, seen = [], set()

    def _add(ev: dict, league: str) -> None:
        st = (ev.get("status") or {}).get("type")
        if st not in ("notstarted", "inprogress") or ev.get("id") in seen:
            return
        ts = ev.get("startTimestamp")
        start = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        if start and start > horizon:
            return
        seen.add(ev["id"])
        games.append(_row_from_event(ev, league))

    # Source 1 : agenda quotidien (HORIZON_DAYS jours)
    for d in range(HORIZON_DAYS):
        data = await _get(client, SOFA_B, f"/sport/basketball/scheduled-events/{(base + timedelta(days=d)).isoformat()}")
        for ev in (data or {}).get("events", []) or []:
            league = (ev.get("tournament") or {}).get("name")
            if league in LEAGUES:
                _add(ev, league)

    # Source 2 : prochains matchs par ligue (capte les playoffs espacés)
    for league, cfg in LEAGUES.items():
        sid = await _season_id(client, cfg["tid"])
        if not sid:
            continue
        data = await _get(client, SOFA_B, f"/unique-tournament/{cfg['tid']}/season/{sid}/events/next/0")
        for ev in (data or {}).get("events", []) or []:
            _add(ev, league)

    games.sort(key=lambda g: g["start"] or 0)
    return games


async def _unibet_odds(client) -> list[dict]:
    """Cotes moneyline NBA + WNBA Unibet : [{home_tokens, away_tokens, oh, oa}]."""
    out = []
    for cfg in LEAGUES.values():
        data = await _get(client, UNIBET_B, cfg["unibet"], UNIBET_PARAMS)
        for entry in (data or {}).get("events", []) or []:
            ev = entry.get("event") or {}
            offers = entry.get("betOffers") or []
            money = next((b for b in offers if (b.get("betOfferType") or {}).get("name")
                          in ("Match", "Head to Head", "Moneyline")), offers[0] if offers else None)
            if not money:
                continue
            outs = money.get("outcomes") or []
            if len(outs) != 2:
                continue
            def dec(o):
                v = o.get("odds")
                return round(v / 1000, 3) if isinstance(v, (int, float)) else None
            # 'participant' / 'label' donne quelle équipe ; on relie via les noms de l'event
            out.append({
                "home_tokens": _norm(ev.get("homeName", "")),
                "away_tokens": _norm(ev.get("awayName", "")),
                "day": _odds_day(ev.get("start")),
                "oh": dec(outs[0]), "oa": dec(outs[1]),
            })
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
    """Relie un match SofaScore à ses cotes Unibet : noms NON génériques + même date.

    names_match ignore les mots génériques (« los », « city »…) qui apparieraient deux
    équipes différentes ; la date lève les dernières ambiguïtés."""
    ht, at = _norm(game["home"]), _norm(game["away"])
    ts = game.get("start")
    gday = datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else None
    for o in odds_list:
        if gday is not None and o["day"] is not None and o["day"] != gday:
            continue
        if names_match(ht, o["home_tokens"]) and names_match(at, o["away_tokens"]):
            return o["oh"], o["oa"]
        if names_match(ht, o["away_tokens"]) and names_match(at, o["home_tokens"]):   # sens inversé
            return o["oa"], o["oh"]
    return None, None


async def board() -> list[dict]:
    """Tableau WNBA prêt à afficher : par match, proba modèle + cotes + value."""
    elo = load_elo()
    async with httpx.AsyncClient(headers={}) as client:
        client.headers.update(SOFA_H)
        games = await _upcoming_games(client)
        client.headers.update(UNIBET_H)
        odds = await _unibet_odds(client)

    rows = []
    for g in games:
        eh = (elo.get(str(g["home_id"])) or {}).get("elo")
        ea = (elo.get(str(g["away_id"])) or {}).get("elo")
        if eh is None or ea is None:   # Elo absent (équipe non couverte par le build)
            log.info("basket: Elo manquant pour %s vs %s -> pas de prédiction",
                     g.get("home"), g.get("away"))
        p = win_prob(eh, ea)
        oh, oa = _match_odds(g, odds)
        imp = _devig(oh, oa)
        pick = None
        if p is not None and imp is not None:
            for side, model_p, odds_s, imp_s in (
                ("home", p, oh, imp[0]), ("away", 1 - p, oa, imp[1])):
                fair = MODEL_TRUST * model_p + (1 - MODEL_TRUST) * imp_s
                edge = fair - imp_s
                if (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp_s <= MAX_IMPLIED
                        and (model_p - imp_s) <= MAX_DISAGREEMENT   # modèle pas "aveugle"
                        and odds_s and (not pick or edge > pick["edge"])):
                    b = odds_s - 1
                    kf = max(0.0, (b * fair - (1 - fair)) / b) if b > 0 else 0.0
                    pick = {"side": side, "team": g[side], "odds": odds_s, "edge": edge,
                            "stake": round(min(kf * 0.25 * 100, 3.0), 2)}
        sigma = LEAGUES.get(g.get("league"), {}).get("sigma", SPREAD_SIGMA)
        rows.append({**g, "model_home": p, "margin": expected_margin(p, sigma), "oh": oh, "oa": oa,
                     "imp_home": imp[0] if imp else None, "pick": pick})
    return rows


async def enrich_display(rows: list[dict]) -> None:
    """Ajoute votes des fans + forme d'avant-match (via provider SofaScore caché).

    Même logique que le foot : seul le 1er affichage touche le réseau (stale-while-revalidate),
    limité aux matchs jouables, tolérant aux erreurs.
    """
    prov = get_provider()
    targets = [r for r in rows if r.get("status") in ("notstarted", "inprogress")][:12]

    async def one(r: dict) -> None:
        eid = r.get("id")
        try:
            v = await prov.get_votes(eid)
            if v.home_percent is not None:
                r["votes"] = (v.home_percent, v.away_percent)
        except Exception:
            pass
        try:
            pf = await prov.get_event_pregame_form(eid)
            if pf.home.form or pf.away.form:
                r["form"] = (pf.home.form, pf.away.form)
        except Exception:
            pass

    if targets:
        try:   # best-effort : si SofaScore traîne, on rend la page sans enrichissement
            await asyncio.wait_for(
                asyncio.gather(*[one(r) for r in targets], return_exceptions=True), timeout=3.0)
        except asyncio.TimeoutError:
            pass


# ----------------------------------------------------------------- rendu (page)
def _fmt_time(ts) -> str:
    if not ts:
        return ""
    return web.fmt_local(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat())


async def _finished_games(client, days: int = 2) -> list[dict]:
    """Matchs NBA + WNBA terminés récents (pour la section Terminés)."""
    base = datetime.now(timezone.utc).date()
    out = []
    for d in range(1, days + 1):
        day = (base - timedelta(days=d)).isoformat()
        data = await _get(client, SOFA_B, f"/sport/basketball/scheduled-events/{day}")
        for ev in (data or {}).get("events", []) or []:
            league = (ev.get("tournament") or {}).get("name")
            if league not in LEAGUES:
                continue
            if (ev.get("status") or {}).get("type") != "finished" or ev.get("winnerCode") not in (1, 2):
                continue
            ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
            out.append({"league": league, "home_id": ht.get("id"), "away_id": at.get("id"),
                        "home": ht.get("name", ""), "away": at.get("name", ""),
                        "winner": "home" if ev["winnerCode"] == 1 else "away",
                        "hs": (ev.get("homeScore") or {}).get("current"),
                        "as": (ev.get("awayScore") or {}).get("current"),
                        "ts": ev.get("startTimestamp") or 0})
    out.sort(key=lambda g: g["ts"], reverse=True)
    return out[:10]


def board_from_store() -> list[dict]:
    """Repli : reconstruit la board des matchs à venir depuis le SUIVI persisté
    (tracking_basket.json), quand SofaScore est en pause et que la board live est vide.

    Sans ça, l'onglet Basket apparaît vide alors que les mêmes matchs s'affichent dans
    les picks de l'accueil (qui, eux, lisent déjà le store)."""
    store = tracking.load(BASKET_TRACK_PATH)
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
        league = (rec.get("tour") or "wnba").upper()
        p = rec.get("model_home_prob")
        sigma = LEAGUES.get(league, {}).get("sigma", SPREAD_SIGMA)
        v = rec.get("value_pick")
        pick = ({"side": v["side"], "team": v.get("player"), "odds": v.get("odds"),
                 "edge": v.get("edge"), "stake": v.get("stake_pct")} if v else None)
        ph, pa = rec.get("public_home"), rec.get("public_away")
        oh, oa = rec.get("unibet_home_odds"), rec.get("unibet_away_odds")
        imp = _devig(oh, oa)
        rows.append({
            "id": rec.get("match_id"), "league": league, "status": "notstarted",
            "home": rec.get("home", ""), "away": rec.get("away", ""),
            "model_home": p, "margin": expected_margin(p, sigma),
            "oh": oh, "oa": oa,
            "imp_home": imp[0] if imp else None, "pick": pick, "start": dt.timestamp(),
            "votes": (ph, pa) if ph is not None else None,
        })
    rows.sort(key=lambda g: g["start"] or 0)
    return rows


RENDER_NET_BUDGET = 2.5   # s max d'attente réseau au rendu (sinon repli)


def _ub_dt(value):
    """Horodatage ISO Unibet -> datetime UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


async def board_from_unibet() -> list[dict]:
    """Matchs basket depuis UNIBET (moneyline NBA+WNBA) + Elo par nom. SANS SofaScore.
    Les noms d'équipes US sont identiques chez Unibet et SofaScore -> pas de bilingue."""
    elo = load_elo()
    # index AVEC la ligue : crucial car NBA et WNBA partagent les villes (« Atlanta Dream »
    # WNBA ne doit PAS matcher « Atlanta Hawks » NBA via le token « atlanta »).
    index = [(name_tokens(v.get("name", "")), v.get("elo"), v.get("league"))
             for v in elo.values() if v.get("name")]

    def elo_for(name, league):
        q = name_tokens(name)
        for toks, e, lg in index:
            if lg == league and names_match(q, toks):
                return e
        return None

    def _dec(o):
        v = o.get("odds")
        return round(v / 1000, 3) if isinstance(v, (int, float)) else None

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=HORIZON_DAYS)
    rows, seen = [], set()
    async with httpx.AsyncClient(headers=UNIBET_H) as client:
        for league, cfg in LEAGUES.items():
            data = await _get(client, UNIBET_B, cfg["unibet"], UNIBET_PARAMS)
            for entry in (data or {}).get("events", []) or []:
                ev = entry.get("event") or {}
                kid = ev.get("id")
                home = ev.get("homeName", "").replace(" (F)", "").replace(" (W)", "").strip()
                away = ev.get("awayName", "").replace(" (F)", "").replace(" (W)", "").strip()
                # NB : pas de filtre parenthèses ici -> les listViews nba/wnba.json sont des
                # vraies ligues (le « (F) » des noms WNBA est juste un marqueur), pas d'esports.
                if kid in seen:
                    continue
                start = _ub_dt(ev.get("start"))
                if start is None or start > horizon:
                    continue
                eh, ea = elo_for(home, league), elo_for(away, league)
                if eh is None or ea is None:
                    continue
                seen.add(kid)
                offers = entry.get("betOffers") or []
                money = next((b for b in offers if (b.get("betOfferType") or {}).get("name")
                              in ("Match", "Head to Head", "Moneyline")), offers[0] if offers else None)
                oh = oa = None
                outs = (money or {}).get("outcomes") or []
                if len(outs) == 2:
                    oh, oa = _dec(outs[0]), _dec(outs[1])
                p = win_prob(eh, ea)
                imp = _devig(oh, oa)
                pick = None
                if p is not None and imp is not None:
                    for side, mp, odds_s, imp_s in (("home", p, oh, imp[0]), ("away", 1 - p, oa, imp[1])):
                        fair = MODEL_TRUST * mp + (1 - MODEL_TRUST) * imp_s
                        edge = fair - imp_s
                        if (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp_s <= MAX_IMPLIED
                                and (mp - imp_s) <= MAX_DISAGREEMENT and odds_s
                                and (not pick or edge > pick["edge"])):
                            b = odds_s - 1
                            kf = max(0.0, (b * fair - (1 - fair)) / b) if b > 0 else 0.0
                            pick = {"side": side, "team": home if side == "home" else away,
                                    "odds": odds_s, "edge": edge, "stake": round(min(kf * 0.25 * 100, 3.0), 2)}
                status = "notstarted" if ev.get("state") == "NOT_STARTED" else "inprogress"
                rows.append({
                    "id": kid, "league": league, "status": status, "home": home, "away": away,
                    "model_home": p, "margin": expected_margin(p, cfg.get("sigma", SPREAD_SIGMA)),
                    "oh": oh, "oa": oa, "imp_home": imp[0] if imp else None, "pick": pick,
                    "start": start.timestamp(), "votes": None, "female": league == "WNBA",
                })
    rows.sort(key=lambda g: g["start"] or 0)
    return rows


async def _resolve_sofa_ids(rows: list[dict]) -> None:
    """Pose l'id SofaScore (noms + date, via scheduled-events basket) dans row['id'] pour
    l'enrichissement. Best-effort : ignoré si SofaScore est en pause."""
    if not rows or sportcache.blocked():
        return
    days = sorted({datetime.fromtimestamp(r["start"], tz=timezone.utc).date().isoformat()
                   for r in rows if r.get("start")})
    index = []
    async with httpx.AsyncClient(headers=SOFA_H) as c:
        for day in days:
            data = await _get(c, SOFA_B, f"/sport/basketball/scheduled-events/{day}")
            for ev in (data or {}).get("events", []) or []:
                ts = ev.get("startTimestamp")
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else None
                index.append((name_tokens((ev.get("homeTeam") or {}).get("name", "")),
                              name_tokens((ev.get("awayTeam") or {}).get("name", "")), d, ev.get("id")))
    for r in rows:
        rd = datetime.fromtimestamp(r["start"], tz=timezone.utc).date().isoformat() if r.get("start") else None
        rh, ra = name_tokens(r["home"]), name_tokens(r["away"])
        for ht, at, d, sid in index:
            if names_match(rh, ht) and names_match(ra, at) and (d is None or rd is None or d == rd):
                r["id"] = sid
                break


def _attach_from_store(rows: list[dict]) -> None:
    """Relie chaque match Unibet au suivi (par nom + date) -> id SofaScore + votes, SANS
    aucun appel SofaScore (le store est peuplé en fond par la boucle de suivi). C'est ce
    qui garde le RENDU des pages 100 % hors-SofaScore (plus de rafales -> plus de pauses)."""
    store = tracking.load(BASKET_TRACK_PATH)
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
        rh, ra = name_tokens(r["home"]), name_tokens(r["away"])
        for sht, sat, d, rec in idx:
            if d is not None and rd is not None and d != rd:
                continue
            if names_match(rh, sht) and names_match(ra, sat):
                mid = rec.get("match_id")
                if mid:
                    r["id"] = mid
                    r["sofa_ok"] = True     # id SofaScore résolu -> fiche détaillée cliquable
                if rec.get("public_home") is not None:
                    r["votes"] = (rec["public_home"], rec["public_away"])
                break


async def board_resilient() -> list[dict]:
    """SOURCE UNIQUE des matchs basket (onglet ET accueil). MATCHS + cotes via UNIBET + Elo,
    enrichissement (id SofaScore + votes) lu dans le STORE -> rendu 100 % hors-SofaScore.
    Replis : board SofaScore directe puis store."""
    try:
        rows = await asyncio.wait_for(board_from_unibet(), timeout=RENDER_NET_BUDGET)
        if rows:
            _attach_from_store(rows)       # store local, aucun appel SofaScore
            return rows
    except (Exception, asyncio.TimeoutError):
        pass
    return board_from_store()              # repli store (toujours hors-SofaScore au rendu)


async def finished() -> list[dict]:
    elo = load_elo()
    async with httpx.AsyncClient(headers=SOFA_H) as c:
        games = await _finished_games(c)
    for g in games:
        eh = (elo.get(str(g["home_id"])) or {}).get("elo")
        ea = (elo.get(str(g["away_id"])) or {}).get("elo")
        g["model_home"] = win_prob(eh, ea)
    return games


def finished_from_store(limit: int = 8) -> list[dict]:
    """Matchs récemment terminés depuis le suivi (SANS appel SofaScore) — pour le rendu."""
    store = tracking.load(BASKET_TRACK_PATH)
    out = []
    for rec in store.values():
        res = rec.get("result")
        if not res or res.get("winner") not in ("home", "away") or res.get("void"):
            continue
        out.append({"league": (rec.get("tour") or "").upper() or "Basket",
                    "home": rec.get("home", ""), "away": rec.get("away", ""),
                    "winner": res["winner"], "model_home": rec.get("model_home_prob"),
                    "hs": None, "as": None, "_at": res.get("settled_at", "")})
    out.sort(key=lambda g: g["_at"], reverse=True)
    return out[:limit]


def render(rows: list[dict], finished_rows: list[dict] | None = None,
           paused: bool = False, frag: bool = False) -> str:
    e = html.escape
    value, live, upcoming = [], [], []
    for r in rows:
        p = r.get("model_home")
        # Barre de cotes Unibet claire (nom + cote par équipe) ; sinon état Elo.
        if r.get("oh"):
            sub_html = web.odds_row([(r["home"], r.get("oh")), (r["away"], r.get("oa"))])
        elif p is None:
            sub_html = '<div class="dim">Elo indisponible</div>'
        else:
            sub_html = ""
        fm = r.get("form")
        if fm:
            sub_html += web.form_compare(r["home"], fm[0], r["away"], fm[1])
        # (les votes communauté sont déjà dans la barre PUBLIC -> pas de doublon ici)
        pk = r.get("pick")
        badge = (f'<span class="badge b-val">VALUE +{round(pk["edge"]*100,1)} pts</span>'
                 if pk else "")
        female = r.get("female") if r.get("female") is not None \
            else (r.get("league") or "").upper() == "WNBA"
        base = {"tour": r.get("league", "Basket"), "status": r["status"], "time": _fmt_time(r.get("start")),
                "start_ts": r.get("start"), "home": r["home"], "away": r["away"], "female": female,
                "url": f'/basket/match/{r["id"]}' if r.get("sofa_ok") else None,
                "score": (f'{r.get("home_pts")}-{r.get("away_pts")}'
                          if r["status"] == "inprogress" and r.get("home_pts") is not None else ""),
                **web.bars_two_way(p, r.get("imp_home"), r.get("votes"), r["home"], r["away"])}
        (live if r["status"] == "inprogress" else upcoming).append(
            {**base, "prob": p, "prob_labels": (r["home"].split()[-1], r["away"].split()[-1]),
             "sub": sub_html, "badge": badge, "pick": bool(pk)})
        if pk:
            oddsrow = web.odds_row([(r["home"], r.get("oh")), (r["away"], r.get("oa"))])
            value.append({**base, "badge": badge, "pick": True,
                          "sub": oddsrow + f'<div class="dim">pari : <b class="pos">{e(pk["team"])}</b> '
                                 f'@{pk["odds"]} · +{round(pk["edge"]*100,1)} pts (à confirmer)</div>'})

    fin = []
    for r in (finished_rows or []):
        p = r.get("model_home")
        if p is not None:
            fav = r["home"] if p >= 0.5 else r["away"]
            ok = (r["winner"] == "home") == (p >= 0.5)
            wname = r["home"] if r["winner"] == "home" else r["away"]
            badge = ('<span class="pos">✓ modèle ok</span>' if ok
                     else '<span class="neg">✗ raté</span>')
            sub = (f'<div class="dim">favori modèle : {e(fav)} {round(max(p,1-p)*100)}% '
                   f'· vainqueur : <b>{e(wname)}</b></div>')
        else:
            badge, sub = "", ""
        fin.append({"tour": r.get("league", "Basket"), "status": "finished",
                    "home": r["home"], "away": r["away"],
                    "female": (r.get("league") or "").upper() == "WNBA",
                    "score": f'{r.get("hs")}-{r.get("as")}' if r.get("hs") is not None else "terminé",
                    "sub": sub, "badge": badge})

    intro = ('🏀 <b>NBA & WNBA</b> — Elo d\'équipe + avantage du terrain vs cotes Unibet. '
             'Les « value » restent à <b>confirmer par le suivi</b> (CLV).')
    return web.render_sport_matches("basket", "Basket NBA & WNBA", value, live, upcoming, fin,
                                    intro=intro, paused=paused, frag=frag)


# ----------------------------------------------------------------- suivi (séparé)
BASKET_TRACK_PATH = os.path.join(_ROOT, "data", "tracking_basket.json")


def _upsert(store: dict, g: dict, now_iso: str) -> bool:
    rec = store.get(str(g["id"]), {})
    if rec.get("result"):
        return False
    pick = g.get("pick")
    rec.update({
        "match_id": g["id"], "sport": "basket", "tour": (g.get("league") or "").lower() or "wnba",
        "home": g["home"], "away": g["away"], "model_home_prob": g["model_home"],
        "start_time": (datetime.fromtimestamp(g["start"], tz=timezone.utc).isoformat()
                       if g.get("start") else None),
        "unibet_home_odds": g.get("oh"), "unibet_away_odds": g.get("oa"),
        "value_pick": ({"side": pick["side"], "player": pick["team"], "odds": pick["odds"],
                        "edge": pick["edge"], "stake_pct": pick.get("stake")} if pick else None),
        "last_update": now_iso,
    })
    vt = g.get("votes")               # votes des fans (persistés -> barre PUBLIC stable)
    if vt and vt[0] is not None:
        rec["public_home"], rec["public_away"] = vt[0], vt[1]
    rec.setdefault("first_logged", now_iso)
    rec.setdefault("open_home_odds", g.get("oh"))
    rec.setdefault("open_away_odds", g.get("oa"))
    store[str(g["id"])] = rec
    return True


async def run_snapshot() -> int:
    """Logue les prédictions WNBA (proba + cotes + value + votes) -> tracking_basket.json."""
    store = tracking.load(BASKET_TRACK_PATH)
    now = datetime.now(timezone.utc).isoformat()
    rows = await board()
    await enrich_display(rows)         # capture les votes pour les persister
    n = 0
    for g in rows:
        if g.get("oh") and g.get("model_home") is not None and _upsert(store, g, now):
            n += 1
    tracking.save(store, BASKET_TRACK_PATH)
    return n


async def run_settle() -> int:
    """Renseigne le résultat des matchs terminés (vainqueur), clôt les annulés/reportés."""
    store = tracking.load(BASKET_TRACK_PATH)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    s = 0
    async with httpx.AsyncClient(headers=SOFA_H) as c:
        for rec in list(store.values()):
            if rec.get("result"):
                continue
            data = await _get(c, SOFA_B, f"/event/{rec['match_id']}")
            ev = (data or {}).get("event") or {}
            if (ev.get("status") or {}).get("type") == "finished" and ev.get("winnerCode") in (1, 2):
                winner = "home" if ev["winnerCode"] == 1 else "away"
                if tracking.settle(store, rec["match_id"], winner, None, now):
                    s += 1
                continue
            if _stale(rec, now_dt) and tracking.void(
                    store, rec["match_id"], "non terminé (reporté/annulé ?)", now):
                s += 1
    tracking.save(store, BASKET_TRACK_PATH)
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
