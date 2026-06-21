"""Données Unibet (plateforme **Kambi**, `eu-offering-api.kambicdn.com/.../ubbe`) — gratuit, sans clé.

Au-delà des cotes 1X2 déjà utilisées par l'app, Kambi expose énormément : agenda par sport (listView),
matchs en direct, et surtout **TOUS les marchés d'un match** (betoffer/event : handicaps, totaux, mi-temps,
props joueur…) + l'arbre des compétitions (group). Ce module en fait une API propre (cotes converties en
DÉCIMAL) pour /docs, en réutilisant la même base/params/headers que le reste de l'app (app/netconst.py).

Best-effort STRICT : timeout court, toute panne -> [] / None.
"""

from __future__ import annotations

import json
import urllib.error
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


def _pp_label(o: dict, crit_label: str) -> str:
    """Issue Kambi -> libellé lisible « Critère : Issue [ligne] »."""
    ln = o.get("line")
    txt = f"{crit_label} : {o.get('label', '')}"
    if ln is not None:
        txt += f" {int(ln) / 1000:g}"
    return txt.strip()


def _pp_legs(group: dict) -> list:
    """Récupère les outcome ids d'un `group` prepack (jambes sous group.groups[].outcomes[].id)."""
    res = [o["id"] for o in group.get("outcomes", [])]
    for g in group.get("groups", []):
        res += _pp_legs(g)
    return res


def prepack_combos(event_id: str) -> list[dict]:
    """Combinés MÊME-MATCH pré-construits par Unibet (`prepackcoupon/event`) avec leur **vraie cote
    corrélée** (le moteur Bet Builder rabote/booste les jambes corrélées : la cote n'est PAS le produit).
    Renvoie [{prepack_id, real_odds, legs:[{sel,odds,outcome_id}], naive, shave_pct, n}] (≥2 jambes),
    trié par cote réelle croissante. [] si indisponible (best-effort, sans navigateur)."""
    pp = _get(f"prepackcoupon/event/{event_id}.json")
    if not pp:
        return []
    omap: dict = {}
    full = _get(f"betoffer/event/{event_id}.json") or {}
    for src in (pp.get("betOffers") or [], full.get("betOffers") or []):
        for bo in src:
            crit = (bo.get("criterion") or {}).get("label", "")
            for o in bo.get("outcomes", []):
                omap[o["id"]] = (_pp_label(o, crit), _odds(o.get("odds")))
    combos, seen = [], set()
    for cp in pp.get("prePackCoupons", []):
        if cp.get("status") != "OPEN":
            continue
        for row in cp.get("prePackCouponRows", []):
            ids = _pp_legs(row.get("group", {}))
            if len(ids) < 2:
                continue
            real = _odds((row.get("odds") or {}).get("decimal"))
            if not real:
                continue
            legs, naive, ok = [], 1.0, True
            for i in ids:
                hit = omap.get(i)
                if not hit:
                    ok = False
                    break
                legs.append({"sel": hit[0], "odds": hit[1], "outcome_id": i})
                naive *= (hit[1] or 1)
            if not ok:
                continue
            key = tuple(sorted(ids))
            if key in seen:
                continue
            seen.add(key)
            combos.append({"prepack_id": row.get("id"), "real_odds": real, "legs": legs,
                           "naive": round(naive, 3),
                           "shave_pct": round(100 * (1 - real / naive), 1) if naive else None,
                           "n": len(legs)})
    combos.sort(key=lambda c: c["real_odds"])
    return combos


def betbuilder_odds(event_id: str, outcome_ids: list) -> float | None:
    """VRAIE cote corrélée d'un combiné MÊME-MATCH **arbitraire** (Bet Builder Unibet/Kambi), via
    l'endpoint de validation de coupon `coupon/validate.json`. SANS login (isUserLoggedIn=false).
    Astuce : on envoie une cote BIDON -> Kambi rejette avec ODDS_CHANGED qui contient la VRAIE cote.
    Renvoie la cote décimale, ou None si non combinable / indisponible. (≥2 issues éligibles bet_builder.)"""
    if not outcome_ids or len(outcome_ids) < 2:
        return None
    group = {"operation": "AND",
             "groups": [{"operation": "AND", "outcomeIds": [int(o)]} for o in outcome_ids]}
    payload = {"couponRows": [{"index": 0, "odds": 2000, "group": group, "type": "BET_BUILDER"}],
               "bets": [{"couponRowIndexes": [0], "eachWay": False}], "isUserLoggedIn": False}
    url = ("https://cf-mt-auth-api.kambicdn.com/player/api/v2019/ubbe/coupon/validate.json?"
           + urllib.parse.urlencode(UNIBET_PARAMS))
    headers = {**UNIBET_H, "Content-Type": "application/json",
               "Origin": "https://fr.unibetsports.be", "Referer": "https://fr.unibetsports.be/"}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        body = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
        j = json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code != 409:                       # 409 = FAIL avec détails (dont ODDS_CHANGED) -> exploitable
            return None
        try:
            j = json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            return None
    except Exception:
        return None
    if j.get("status") == "SUCCESS":            # par chance la cote bidon 2.00 était la vraie
        return 2.0
    for rerr in j.get("couponRowErrors", []):
        for err in rerr.get("errors", []):
            if err.get("type") == "ODDS_CHANGED":
                for arg in err.get("arguments", []):
                    if arg.get("type") == "ODDS":
                        return _odds(arg.get("value"))
    return None                                  # NOT_COMBINABLE / autre -> pas de cote


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
