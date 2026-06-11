"""Suivi des VARIATIONS de cote avant un match (1X2 vainqueur, source Unibet/Kambi — gratuit).

But : enregistrer la cote au fil du temps pour chaque match à venir et mesurer comment le book
fait bouger sa ligne avec l'info (ouverture → clôture). Permet de voir les « steam » (cote qui se
raccourcit = argent qui rentre) et les « drift » (cote qui s'allonge).

Cadence (décidée par `_due`) : ~1 relevé / heure, RESSERRÉ à ~10 min dans la DERNIÈRE HEURE avant
le coup d'envoi. Une fois le match commencé, on fige : le dernier relevé pré-match = la clôture.

Stockage : 1 fichier JSON par sport (`data/odds_history/{sport}.json`), dict clé = noms d'équipes
normalisés (même clé que les cotes live) -> lookup trivial depuis la fiche match. Fonctions pures
+ I/O fichier ; aucun appel réseau ici (les cotes arrivent du listView via match_select).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds_history")

FAR_INTERVAL_MIN = 60     # > 1 h avant le match : 1 relevé / heure
NEAR_INTERVAL_MIN = 10    # ≤ 1 h avant : resserré à 10 min
NEAR_WINDOW_MIN = 60      # seuil « dernière heure »
PRUNE_AFTER_H = 48        # purge des matchs commencés depuis plus de 48 h
_MAX_SNAPS = 80           # garde-fou par match


def _key(home: str | None, away: str | None) -> str:
    return f"{(home or '').strip().lower()}|{(away or '').strip().lower()}"


def _path(sport: str) -> str:
    return os.path.join(_DIR, f"{sport}.json")


def _load(sport: str) -> dict:
    try:
        with open(_path(sport), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(sport: str, data: dict) -> None:
    os.makedirs(_DIR, exist_ok=True)
    tmp = _path(sport) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _path(sport))    # écriture atomique


def _parse(ts) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _interval_min(start_dt: datetime | None, now: datetime) -> int:
    """Intervalle de relevé voulu (min) selon le temps restant avant le coup d'envoi."""
    if start_dt is None:
        return FAR_INTERVAL_MIN
    mins = (start_dt - now).total_seconds() / 60
    return NEAR_INTERVAL_MIN if 0 < mins <= NEAR_WINDOW_MIN else FAR_INTERVAL_MIN


def _due(snaps: list, start_dt: datetime | None, now: datetime) -> bool:
    """Faut-il enregistrer un nouveau relevé maintenant ? Non si le match a commencé (on fige)."""
    if start_dt is not None and start_dt <= now:
        return False
    if not snaps:
        return True
    last = _parse(snaps[-1].get("ts"))
    if last is None:
        return True
    gap = (now - last).total_seconds() / 60
    return gap >= _interval_min(start_dt, now) - 1        # 1 min de slack (réveil pas pile à l'heure)


def _prune(data: dict, now: datetime) -> None:
    """Retire les matchs commencés depuis plus de PRUNE_AFTER_H (séries devenues inutiles)."""
    cutoff = now - timedelta(hours=PRUNE_AFTER_H)
    for k in list(data.keys()):
        sd = _parse((data[k] or {}).get("start"))
        if sd is not None and sd < cutoff:
            del data[k]


def record_all(sport: str, events: list, now: datetime | None = None) -> int:
    """`events` = [{id, home, away, comp, start(ISO), odds:(o1,ox,o2)}]. Ajoute un relevé par match
    SI l'intervalle voulu est écoulé, purge les vieux matchs, sauvegarde. Renvoie le nb de relevés ajoutés."""
    now = now or datetime.now(timezone.utc)
    data = _load(sport)
    added = 0
    for ev in events or []:
        odds = ev.get("odds")
        if not odds:
            continue
        k = _key(ev.get("home"), ev.get("away"))
        start_dt = _parse(ev.get("start"))
        entry = data.get(k)
        if entry is None:
            entry = {"id": ev.get("id"), "home": ev.get("home"), "away": ev.get("away"),
                     "comp": ev.get("comp"), "start": ev.get("start"), "snapshots": []}
            data[k] = entry
        snaps = entry["snapshots"]
        if _due(snaps, start_dt, now):
            o1, ox, o2 = (list(odds) + [None, None, None])[:3]
            snap = {"ts": now.isoformat(), "o1": o1, "o2": o2}
            if ox is not None:
                snap["ox"] = ox
            snaps.append(snap)
            if len(snaps) > _MAX_SNAPS:
                del snaps[:-_MAX_SNAPS]
            entry["start"] = ev.get("start") or entry.get("start")
            added += 1
    _prune(data, now)
    _save(sport, data)
    return added


def movement(sport: str, home: str, away: str, now: datetime | None = None) -> dict | None:
    """Variation de cote 1X2 d'un match : par issue {open, now, pct, dir(up/down/flat), series}.
    `dir` = 'down' (cote raccourcie = steam) / 'up' (allongée = drift). None si < 2 relevés."""
    entry = _load(sport).get(_key(home, away))
    if not entry:
        return None
    snaps = entry.get("snapshots") or []
    if len(snaps) < 2:
        return None
    now = now or datetime.now(timezone.utc)
    start_dt = _parse(entry.get("start"))
    closed = start_dt is not None and start_dt <= now
    first, last = snaps[0], snaps[-1]

    def leg(code: str) -> dict | None:
        o0, o1 = first.get(code), last.get(code)
        if not o0 or not o1:
            return None
        pct = (o1 - o0) / o0 * 100
        direction = "down" if o1 < o0 - 1e-9 else ("up" if o1 > o0 + 1e-9 else "flat")
        return {"open": o0, "now": o1, "pct": round(pct, 1), "dir": direction,
                "series": [s.get(code) for s in snaps if s.get(code)]}

    legs = {"home": leg("o1"), "draw": leg("ox"), "away": leg("o2")}
    if not any(legs.values()):
        return None
    return {"home": entry.get("home"), "away": entry.get("away"), "n": len(snaps),
            "closed": closed, "opened": first.get("ts"), "updated": last.get("ts"), "legs": legs}
