"""Module FOOT (Coupe du Monde + grandes compétitions) — **séparé** du tennis/basket.

Spécificité : 3 issues (1-X-2, le match nul existe). Modèle : Elo d'équipe
(tools/build_foot_elo.py) -> supériorité de buts -> double Poisson -> P(1)/P(X)/P(2),
confronté au 1X2 Unibet pour repérer une value. Filtre « grandes compétitions » par ID
(Coupe du Monde + top championnats + C1/C3), pas les petits championnats.

⚠️ Modèle jeune + venues neutres en CdM : avantage terrain faible, value à confirmer.
Sources gratuites : SofaScore + Unibet BE.
"""

from __future__ import annotations

import html
import json
import math
import os
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from app import web

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELO_PATH = os.path.join(_ROOT, "data", "foot_elo.json")

# Grandes compétitions (SofaScore unique-tournament id -> libellé court).
MAJOR_TIDS = {16: "Coupe du Monde", 17: "Premier League", 8: "LaLiga", 23: "Serie A",
              35: "Bundesliga", 34: "Ligue 1", 7: "Ligue des Champions",
              679: "Europa League", 1: "Euro", 18: "Coupe du Monde"}

HOME_ADV = 35.0           # faible : beaucoup de venues neutres en grand tournoi
GOALS_TOTAL = 2.7         # total de buts moyen (baseline)
SUP_PER_100 = 0.45        # 100 pts Elo ~ 0.45 but de supériorité
HORIZON_DAYS = 14         # la CdM démarre dans ~11 jours -> fenêtre large
MODEL_TRUST = 0.50
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.12, 0.80

SOFA_B = "https://api.sofascore.com/api/v1"
SOFA_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
          "Origin": "https://www.sofascore.com"}
UNIBET_B = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
UNIBET_PARAMS = {"lang": "fr_BE", "market": "BE", "client_id": "2", "channel_id": "1"}
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


def outcome_probs(elo_home: float | None, elo_away: float | None,
                  kmax: int = 10) -> tuple[float, float, float] | None:
    """(P(domicile), P(nul), P(extérieur)) via double Poisson dérivé de l'Elo."""
    if elo_home is None or elo_away is None:
        return None
    sup = (elo_home + HOME_ADV - elo_away) / 100.0 * SUP_PER_100
    lh = max(0.15, (GOALS_TOTAL + sup) / 2)
    la = max(0.15, (GOALS_TOTAL - sup) / 2)
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


def _norm(name: str) -> set[str]:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return {t for t in text.replace("-", " ").replace(".", " ").split()
            if len(t) > 2 and t not in ("the", "fc", "cf")}


def _devig3(o1, ox, o2):
    odds = [o1, ox, o2]
    if not all(odds):
        return None
    raws = [1 / o for o in odds]
    tot = sum(raws)
    return [r / tot for r in raws]


# ----------------------------------------------------------------- données
async def _get(client, base, path, params=None):
    try:
        r = await client.get(base + path, params=params, timeout=20)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


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
                    "o1": dec(outs[0]), "ox": dec(outs[1]), "o2": dec(outs[2])})
    return out


def _match_odds(game, odds_list):
    ht, at = _norm(game["home"]), _norm(game["away"])
    for o in odds_list:
        if (ht & o["home_tokens"]) and (at & o["away_tokens"]):
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
        probs = outcome_probs(eh, ea)
        o1, ox, o2 = _match_odds(g, odds)
        imp = _devig3(o1, ox, o2)
        pick = None
        if probs and imp:
            labels = [("1", g["home"], o1), ("X", "Match nul", ox), ("2", g["away"], o2)]
            for i, (code, name, odd) in enumerate(labels):
                fair = MODEL_TRUST * probs[i] + (1 - MODEL_TRUST) * imp[i]
                edge = fair - imp[i]
                if (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp[i] <= MAX_IMPLIED
                        and odd and (not pick or edge > pick["edge"])):
                    pick = {"code": code, "team": name, "odds": odd, "edge": edge}
        rows.append({**g, "probs": probs, "o1": o1, "ox": ox, "o2": o2,
                     "imp": imp, "pick": pick})
    return rows


# ----------------------------------------------------------------- rendu
def _fmt_time(ts) -> str:
    if not ts:
        return ""
    return web.fmt_local(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat())


def render(rows: list[dict]) -> str:
    e = html.escape
    out = ['<div class="banner">⚽ <b>Coupe du Monde & grandes compétitions</b> — modèle '
           '<b>Elo de sélection</b> (1-X-2 via double Poisson) vs cotes Unibet. Modèle jeune '
           'et venues neutres : les « value » sont à <b>confirmer</b>. Sport séparé.</div>']

    def game_row(r):
        probs = r.get("probs")
        pk = r.get("pick")
        badge = (f'<span class="badge b-val">VALUE +{round(pk["edge"]*100,1)} pts</span>'
                 if pk else "")
        top = (f'<span class="live">🔴 EN DIRECT</span>' if r["status"] == "inprogress"
               else e(_fmt_time(r.get("start"))))
        if probs:
            line = (f'<b>{round(probs[0]*100)}%</b> · nul {round(probs[1]*100)}% · '
                    f'<b>{round(probs[2]*100)}%</b>')
        else:
            line = '<span class="dim">Elo indisponible</span>'
        if r.get("o1"):
            line += f' <span class="dim">· cotes {r["o1"]}/{r["ox"]}/{r["o2"]}</span>'
        else:
            line += ' <span class="dim">· cotes Unibet à venir</span>'
        pick_line = ""
        if pk:
            pick_line = (f'<div class="dim">pari : <b class="pos">{e(pk["team"])}</b> '
                         f'@{pk["odds"]} · +{round(pk["edge"]*100,1)} pts (à confirmer)</div>')
        cls = "row pick" if pk else "row"
        return (f'<div class="{cls}">'
                f'<div class="rowtop"><span>{e(r["comp"])} · {top}</span>{badge}</div>'
                f'<div class="players">{e(r["home"])} <span class="dim">vs</span> {e(r["away"])}</div>'
                f'<div class="dim">modèle (1/N/2) : {line}</div>{pick_line}</div>')

    picks = [r for r in rows if r.get("pick")]
    if picks:
        out.append(f'<h2>💰 Value Foot ({len(picks)})</h2>')
        out.extend(game_row(r) for r in picks)
    out.append(f'<h2>⚽ Matchs ({len(rows)})</h2>')
    if rows:
        out.extend(game_row(r) for r in rows)
    else:
        out.append('<div class="dim">Aucun match de grande compétition à venir '
                   f'(≤ {HORIZON_DAYS} jours). La Coupe du Monde démarre le 11 juin.</div>')
    return web.layout("Football", "foot", "".join(out), refresh=True)
