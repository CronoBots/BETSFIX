"""Module BASKET (WNBA) — **séparé du tennis**.

Modèle d'équipe simple et honnête : Elo d'équipe (tools/build_basket_elo.py) + avantage
du terrain -> probabilité de victoire, confrontée au moneyline Unibet pour repérer une
éventuelle value. Pas de simulation : un seul marché fiable (vainqueur) pour démarrer.

Sources gratuites : SofaScore (matchs WNBA, tournoi 486) + Unibet BE (cotes WNBA).
Conçu pour resservir à la NBA en octobre (changer le tournament id + le path Unibet).
"""

from __future__ import annotations

import html
import json
import math
import os
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from app import tracking, web

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELO_PATH = os.path.join(_ROOT, "data", "basket_elo.json")

WNBA_TID = 486
HOME_ADV = 65.0            # avantage du terrain en points Elo (~2.5-3 pts)
MODEL_TRUST = 0.50         # ancrage marché (l'Elo jeune est bruité -> on suit le book)
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.25, 0.75

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


def expected_margin(p_home: float | None) -> float | None:
    """Marge attendue (points) de l'équipe à domicile, dérivée de la proba de victoire."""
    if p_home is None:
        return None
    return SPREAD_SIGMA * _inv_norm(p_home)


def _norm(name: str) -> set[str]:
    """Tokens normalisés d'un nom d'équipe (sans accents, sans '(F)')."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    for junk in ("(f)", "(w)"):
        text = text.replace(junk, " ")
    toks = set()
    for t in text.replace("-", " ").replace(".", " ").split():
        if len(t) > 2 and t != "the":
            toks.add(t)
    return toks


def _devig(o1: float | None, o2: float | None) -> tuple[float, float] | None:
    if not o1 or not o2:
        return None
    a, b = 1 / o1, 1 / o2
    return a / (a + b), b / (a + b)


# ----------------------------------------------------------------- données
async def _get(client, base, path, params=None):
    try:
        r = await client.get(base + path, params=params, timeout=20)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def _upcoming_games(client) -> list[dict]:
    """Matchs WNBA à venir / en cours sur 3 jours (SofaScore)."""
    base = datetime.now(timezone.utc).date()
    games, seen = [], set()
    for d in range(3):
        day = (base + timedelta(days=d)).isoformat()
        data = await _get(client, SOFA_B, f"/sport/basketball/scheduled-events/{day}")
        for ev in (data or {}).get("events", []) or []:
            if (ev.get("tournament") or {}).get("name") != "WNBA":
                continue
            st = (ev.get("status") or {}).get("type")
            if st not in ("notstarted", "inprogress") or ev.get("id") in seen:
                continue
            seen.add(ev["id"])
            ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
            games.append({
                "id": ev["id"], "home_id": ht.get("id"), "away_id": at.get("id"),
                "home": ht.get("name", ""), "away": at.get("name", ""),
                "start": ev.get("startTimestamp"), "status": st,
                "home_pts": (ev.get("homeScore") or {}).get("current"),
                "away_pts": (ev.get("awayScore") or {}).get("current"),
            })
    games.sort(key=lambda g: g["start"] or 0)
    return games


async def _unibet_odds(client) -> list[dict]:
    """Cotes moneyline WNBA Unibet : [{home_tokens, away_tokens, oh, oa}]."""
    data = await _get(client, UNIBET_B, "/listView/basketball/wnba.json", UNIBET_PARAMS)
    out = []
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
            "oh": dec(outs[0]), "oa": dec(outs[1]),
        })
    return out


def _match_odds(game, odds_list):
    """Relie un match SofaScore à ses cotes Unibet (par tokens d'équipe)."""
    ht, at = _norm(game["home"]), _norm(game["away"])
    for o in odds_list:
        if (ht & o["home_tokens"]) and (at & o["away_tokens"]):
            return o["oh"], o["oa"]
        if (ht & o["away_tokens"]) and (at & o["home_tokens"]):   # sens inversé
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
                        and odds_s and (not pick or edge > pick["edge"])):
                    b = odds_s - 1
                    kf = max(0.0, (b * fair - (1 - fair)) / b) if b > 0 else 0.0
                    pick = {"side": side, "team": g[side], "odds": odds_s, "edge": edge,
                            "stake": round(min(kf * 0.25 * 100, 3.0), 2)}
        rows.append({**g, "model_home": p, "margin": expected_margin(p), "oh": oh, "oa": oa,
                     "imp_home": imp[0] if imp else None, "pick": pick})
    return rows


# ----------------------------------------------------------------- rendu (page)
def _fmt_time(ts) -> str:
    if not ts:
        return ""
    return web.fmt_local(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat())


def render(rows: list[dict]) -> str:
    e = html.escape
    out = ['<div class="banner">🏀 <b>WNBA</b> — modèle <b>Elo d\'équipe</b> '
           '(avantage du terrain) confronté aux cotes Unibet. Saison jeune : l\'Elo est '
           'encore bruité, les « value » sont à <b>confirmer par le suivi</b>. '
           'Sport séparé du tennis.</div>']

    def game_row(r):
        p = r.get("model_home")
        if r["status"] == "inprogress":
            sc = (f' <span class="dim">{r["home_pts"]}-{r["away_pts"]}</span>'
                  if r.get("home_pts") is not None else "")
            top = f'<span class="live">🔴 EN DIRECT</span>{sc}'
        else:
            top = e(_fmt_time(r.get("start")))
        pk = r.get("pick")
        badge = (f'<span class="badge b-val">VALUE +{round(pk["edge"]*100,1)} pts</span>'
                 if pk else "")
        if p is not None:
            fav = r["home"] if p >= 0.5 else r["away"]
            line = (f'modèle : <b>{e(fav)}</b> {round(max(p,1-p)*100)}%')
            m = r.get("margin")
            if m is not None and abs(m) >= 0.5:
                line += f' · marge attendue ~{abs(round(m))} pts'
        else:
            line = '<span class="dim">Elo indisponible (équipe inconnue)</span>'
        if r.get("oh") and r.get("oa"):
            line += f' · cotes {r["oh"]} / {r["oa"]}'
        else:
            line += ' · <span class="dim">cotes Unibet indisponibles</span>'
        pick_line = ""
        if pk:
            pick_line = (f'<div class="dim">pari : <b class="pos">{e(pk["team"])}</b> '
                         f'@{pk["odds"]} · +{round(pk["edge"]*100,1)} pts vs book (à confirmer)</div>')
        cls = "row pick" if pk else "row"
        bar = web._bar(p) if p is not None else ""
        return (f'<div class="{cls}">'
                f'<div class="rowtop"><span>WNBA · {top}</span>{badge}</div>'
                f'<div class="players">{e(r["home"])} <span class="dim">vs</span> {e(r["away"])}</div>'
                f'{bar}<div class="dim">{line}</div>{pick_line}</div>')

    picks = [r for r in rows if r.get("pick")]
    if picks:
        out.append(f'<h2>💰 Value WNBA ({len(picks)})</h2>')
        out.extend(game_row(r) for r in picks)
    out.append(f'<h2>🏀 Matchs WNBA ({len(rows)})</h2>')
    if rows:
        out.extend(game_row(r) for r in rows)
    else:
        out.append('<div class="dim">Aucun match WNBA à venir (≤ 3 jours).</div>')
    out.append('<a class="big" href="/tracking/dashboard?sport=basket">📊 Fiabilité du '
               'modèle basket<div class="d">Calibration et track record, séparés du '
               'tennis</div></a>')
    return web.layout("Basket WNBA", "basket", "".join(out), refresh=True)


# ----------------------------------------------------------------- suivi (séparé)
BASKET_TRACK_PATH = os.path.join(_ROOT, "data", "tracking_basket.json")


def _upsert(store: dict, g: dict, now_iso: str) -> bool:
    rec = store.get(str(g["id"]), {})
    if rec.get("result"):
        return False
    pick = g.get("pick")
    rec.update({
        "match_id": g["id"], "sport": "basket", "tour": "wnba",
        "home": g["home"], "away": g["away"], "model_home_prob": g["model_home"],
        "start_time": (datetime.fromtimestamp(g["start"], tz=timezone.utc).isoformat()
                       if g.get("start") else None),
        "unibet_home_odds": g.get("oh"), "unibet_away_odds": g.get("oa"),
        "value_pick": ({"side": pick["side"], "player": pick["team"], "odds": pick["odds"],
                        "edge": pick["edge"], "stake_pct": pick.get("stake")} if pick else None),
        "last_update": now_iso,
    })
    rec.setdefault("first_logged", now_iso)
    rec.setdefault("open_home_odds", g.get("oh"))
    rec.setdefault("open_away_odds", g.get("oa"))
    store[str(g["id"])] = rec
    return True


async def run_snapshot() -> int:
    """Logue les prédictions WNBA (proba + cotes + value) -> tracking_basket.json."""
    store = tracking.load(BASKET_TRACK_PATH)
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for g in await board():
        if g.get("oh") and g.get("model_home") is not None and _upsert(store, g, now):
            n += 1
    tracking.save(store, BASKET_TRACK_PATH)
    return n


async def run_settle() -> int:
    """Renseigne le résultat des matchs WNBA terminés (vainqueur)."""
    store = tracking.load(BASKET_TRACK_PATH)
    now = datetime.now(timezone.utc).isoformat()
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
    tracking.save(store, BASKET_TRACK_PATH)
    return s
