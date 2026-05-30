"""Facteur SERVICE+RETOUR du modèle vainqueur (domination réelle, par surface).

Remplace l'ancien facteur 'surface' (stats de saison agrégées) par la **domination
service+retour** d'un joueur : tenue de service + taux de break, construite sur son
historique (tools/build_serve_return.py) et pondérée par récence. Validé comme
prédicteur du vainqueur au niveau de l'Elo (tools/explore_serve_return.py : 61.4 %).

Coefficients calibrés (régression logistique walk-forward) : P(home) =
sigmoid(SR_B0 + SR_B1 * (dom_home - dom_away)). Fonctions pures (sans réseau). Si le
snapshot manque pour un joueur, le facteur retombe proprement sur l'ancien calcul de
surface (cf. app.analysis).

Format du snapshot : {"<player_id>": {"name", "dom", "dom_n", "dom_clay",
"dom_clay_n"}}.
"""

from __future__ import annotations

import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "data", "serve_return.json")

# Calibrés sur ~2400 matchs walk-forward (tools/explore_serve_return.py).
SR_B0 = -0.09
SR_B1 = 4.91

# En-deçà, la note terre est trop bruitée -> on prend la note globale.
MIN_CLAY = 10


def is_clay(ground_type: str | None) -> bool:
    return "clay" in (ground_type or "").lower()


def dominance_for(rec: dict | None, ground_type: str | None) -> float | None:
    """Note de domination à utiliser pour ce joueur sur cette surface (ou None)."""
    if not rec:
        return None
    if (is_clay(ground_type) and rec.get("dom_clay") is not None
            and (rec.get("dom_clay_n") or 0) >= MIN_CLAY):
        return rec["dom_clay"]
    return rec.get("dom")


def prob_from_serve_return(dom_home: float | None,
                           dom_away: float | None) -> float | None:
    """Probabilité de victoire de 'home' selon la domination service+retour."""
    if dom_home is None or dom_away is None:
        return None
    z = SR_B0 + SR_B1 * (dom_home - dom_away)
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1 / (1 + math.exp(-z))


def load(path: str = PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


_cache: dict = {"mtime": None, "store": {}}


def load_cached(path: str = PATH) -> dict:
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache["mtime"] != mt:
        _cache["store"] = load(path)
        _cache["mtime"] = mt
    return _cache["store"]


def ratings_for_match(match, store: dict | None = None) -> tuple[float | None, float | None]:
    """(dom_home, dom_away) adaptées à la surface du match. Vide -> (None, None)."""
    store = store if store is not None else load_cached()
    if not store:
        return None, None
    dh = dominance_for(store.get(str(match.home.id)), match.ground_type)
    da = dominance_for(store.get(str(match.away.id)), match.ground_type)
    return dh, da
