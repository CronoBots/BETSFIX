"""Données Unibet (plateforme **Kambi**, `eu-offering-api.kambicdn.com/.../ubbe`) — gratuit, sans clé.

Au-delà des cotes 1X2 déjà utilisées par l'app, Kambi expose énormément : agenda par sport (listView),
matchs en direct, et surtout **TOUS les marchés d'un match** (betoffer/event : handicaps, totaux, mi-temps,
props joueur…) + l'arbre des compétitions (group). Ce module en fait une API propre (cotes converties en
DÉCIMAL) pour /docs, en réutilisant la même base/params/headers que le reste de l'app (app/netconst.py).

Best-effort STRICT : timeout court, toute panne -> [] / None.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from app.netconst import UNIBET_B, UNIBET_H, UNIBET_PARAMS

# Sport de l'app -> chemin listView Kambi / nom de sport Kambi (filtre live).
_PATH = {"foot": "football", "football": "football", "tennis": "tennis",
         "basket": "basketball", "basketball": "basketball"}
_KSPORT = {"foot": "FOOTBALL", "football": "FOOTBALL", "tennis": "TENNIS",
           "basket": "BASKETBALL", "basketball": "BASKETBALL"}


def _get(path: str, extra: dict | None = None, timeout: float = 15.0):
    """GET JSON Kambi (params + headers communs). dict/list, ou None si KO."""
    params = dict(UNIBET_PARAMS)
    if extra:
        params.update(extra)
    url = f"{UNIBET_B}/{path}?" + urllib.parse.urlencode(params)
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UNIBET_H),
                                     timeout=timeout).read().decode("utf-8", "replace")
        return json.loads(raw)
    except Exception:
        return None


def _odds(v):
    """Milli-cotes Kambi (8000) -> cote décimale (8.0). None si absent."""
    try:
        return round(int(v) / 1000, 3)
    except (TypeError, ValueError):
        return None


def _line(v):
    """Ligne Kambi (2500) -> 2.5. None si absent."""
    try:
        return int(v) / 1000
    except (TypeError, ValueError):
        return None


def _event_row(ev: dict) -> dict:
    return {"id": str(ev.get("id")), "home": ev.get("homeName"), "away": ev.get("awayName"),
            "name": ev.get("name"), "league": ev.get("group"), "league_id": ev.get("groupId"),
            "sport": ev.get("sport"), "start": ev.get("start"), "state": ev.get("state"),
            "markets_count": ev.get("nonLiveBoCount"), "live_markets": ev.get("liveBoCount")}


def matches(sport: str) -> list:
    """Agenda Unibet d'un sport (listView = matchs populaires à venir) : [{id, home, away, league,
    start, markets_count…}]. [] si sport inconnu / indisponible."""
    path = _PATH.get(sport)
    if not path:
        return []
    j = _get(f"listView/{path}.json")
    out = []
    for it in (j or {}).get("events") or []:
        ev = it.get("event") or {}
        if ev.get("id"):
            out.append(_event_row(ev))
    return out


def live(sport: str) -> list:
    """Matchs Unibet EN DIRECT d'un sport (même format que matches()). [] si aucun."""
    ks = _KSPORT.get(sport)
    if not ks:
        return []
    j = _get("event/live/open.json")
    out = []
    for it in (j or {}).get("liveEvents") or []:
        ev = it.get("event") or {}
        if ev.get("sport") == ks and ev.get("id"):
            row = _event_row(ev)
            ls = (it.get("liveData") or {}).get("score") or {}
            row["score"] = {"home": ls.get("home"), "away": ls.get("away")} if ls else None
            out.append(row)
    return out


def markets(event_id: str) -> dict | None:
    """TOUS les marchés Unibet d'un match (betoffer/event) regroupés : {event, markets:[{name,
    outcomes:[{label, odds, line, participant}]}]}. Cotes en DÉCIMAL. None si introuvable."""
    j = _get(f"betoffer/event/{event_id}.json")
    if not j:
        return None
    ev = ((j.get("events") or [{}])[0])
    mk = []
    for o in j.get("betOffers") or []:
        crit = (o.get("criterion") or {}).get("label")
        outs = [{"label": x.get("label"), "odds": _odds(x.get("odds")),
                 "line": _line(x.get("line")), "participant": x.get("participant")}
                for x in (o.get("outcomes") or [])]
        mk.append({"name": crit, "type": (o.get("betOfferType") or {}).get("name"), "outcomes": outs})
    if not mk:
        return None
    return {"event": _event_row(ev) if ev.get("id") else {"id": str(event_id)}, "markets": mk}


def competitions(sport: str) -> list:
    """Compétitions Unibet d'un sport (depuis l'arbre `group`) : [{id, name, events}] (events =
    nb de matchs ouverts). [] si indisponible."""
    ks_name = {"foot": "Football", "football": "Football", "tennis": "Tennis",
               "basket": "Basketball", "basketball": "Basketball"}.get(sport)
    j = _get("group.json")
    root = (j or {}).get("group") or {}
    node = next((g for g in root.get("groups") or [] if g.get("name") == ks_name), None)
    if not node:
        return []
    out = []

    def _walk(g):
        kids = g.get("groups") or []
        if not kids:                        # feuille = compétition
            out.append({"id": str(g.get("id")), "name": g.get("name"),
                        "events": g.get("eventCount")})
        for c in kids:
            _walk(c)

    for c in node.get("groups") or []:
        _walk(c)
    return out


def find_id(home: str, away: str, sport: str) -> str | None:
    """Résout l'id Unibet d'un match depuis les noms (dans le listView du sport). None si introuvable."""
    from app.sources import _teams_match, _tok
    th, ta = _tok(home), _tok(away)
    rows = matches(sport)
    for m in rows:
        if _teams_match(home, away, m["home"] or "", m["away"] or ""):
            return m["id"]
    for m in rows:
        fh, fa = _tok(m["home"] or ""), _tok(m["away"] or "")
        if (th & fh and ta & fa) or (th & fa and ta & fh):
            return m["id"]
    return None
