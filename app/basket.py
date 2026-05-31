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
import os
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from app import web

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
                        and (not pick or edge > pick["edge"])):
                    pick = {"side": side, "team": g[side], "odds": odds_s, "edge": edge}
        rows.append({**g, "model_home": p, "oh": oh, "oa": oa,
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
    return web.layout("Basket WNBA", "basket", "".join(out), refresh=True)
