"""Tendances de service par joueur (aces) — base des marchés annexes.

Le taux d'aces par jeu de service est une **tendance individuelle stable** (cf.
tools/explore_aces.py : corrélation 0.51 passé->futur, +15.5% vs moyenne). On charge
ici un instantané (data/player_tendencies.json, construit par tools/build_tendencies.py)
et on expose des fonctions **pures** pour estimer les aces attendus d'un joueur dans un
match. Si l'instantané manque, tout retombe proprement sur None (rien ne s'affiche).

⚠️ Estimer les aces n'est PAS battre le book : le bookmaker connaît aussi ces taux.
C'est une information d'aide à la lecture, pas un signal de value tant qu'on ne l'a pas
confronté aux cotes Unibet et validé par le suivi (CLV/résultats).
"""

from __future__ import annotations

import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "data", "player_tendencies.json")

# Volumes minimaux de jeux de service avant de faire confiance à un taux : en-deçà,
# c'est du bruit (un joueur vu sur 2 matchs peut afficher un taux délirant). On préfère
# alors "tendance inconnue" (None) plutôt qu'une estimation trompeuse.
MIN_GAMES = 60
MIN_CLAY_GAMES = 90


def is_clay(ground_type: str | None) -> bool:
    return "clay" in (ground_type or "").lower()


def ace_rate(rec: dict | None, ground_type: str | None) -> float | None:
    """Taux d'aces (par jeu de service) pour ce joueur sur cette surface.

    Terre + assez de jeux terre -> taux terre ; sinon taux global s'il est assez
    fourni ; sinon None (tendance inconnue, on n'affiche rien).
    """
    if not rec:
        return None
    if (is_clay(ground_type) and rec.get("ace_rate_clay") is not None
            and (rec.get("ace_games_clay") or 0) >= MIN_CLAY_GAMES):
        return rec["ace_rate_clay"]
    if (rec.get("ace_games") or 0) < MIN_GAMES:
        return None
    return rec.get("ace_rate")


def expected_service_games(best_of: int, fav_prob: float | None) -> float:
    """Estimation du nb de jeux de service par joueur (les deux servent autant).

    Plus le match est serré, plus il y a de jeux ; un match déséquilibré est court.
    best_of=5 (ATP GC) -> plus de jeux que best_of=3 (WTA).
    """
    base = 17.0 if best_of == 5 else 11.0
    closeness = 1.0 - abs(2.0 * (fav_prob if fav_prob is not None else 0.5) - 1.0)
    return base + (3.0 if best_of == 5 else 2.0) * closeness


def expected_aces(rate: float | None, service_games: float | None) -> float | None:
    """Nombre d'aces attendu = taux x jeux de service. None si tendance inconnue."""
    if rate is None or service_games is None:
        return None
    return rate * service_games


def prob_over(line: float, lam: float | None) -> float | None:
    """P(aces > line) en modélisant le compte par une loi de Poisson de moyenne lam."""
    if lam is None or lam < 0:
        return None
    # P(X <= floor(line)) puis complément. Poisson CDF par sommation.
    k_max = int(math.floor(line))
    cdf = 0.0
    term = math.exp(-lam)  # P(X=0)
    for k in range(0, k_max + 1):
        if k > 0:
            term *= lam / k
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


# ----------------------------------------------------------------- I/O instantané
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


def for_match(match, best_of: int, fav_prob: float | None,
              store: dict | None = None) -> dict | None:
    """Récapitulatif aces des deux joueurs pour un match, ou None si aucune tendance.

    Renvoie {home_name, away_name, home_rate, away_rate, home_exp, away_exp,
    service_games}. home_exp/away_exp = aces attendus (arrondis à l'affichage).
    """
    store = store if store is not None else load_cached()
    if not store:
        return None
    rh = ace_rate(store.get(str(match.home.id)), match.ground_type)
    ra = ace_rate(store.get(str(match.away.id)), match.ground_type)
    if rh is None and ra is None:
        return None
    sg = expected_service_games(best_of, fav_prob)
    return {
        "home_name": match.home.name, "away_name": match.away.name,
        "home_rate": rh, "away_rate": ra,
        "home_exp": expected_aces(rh, sg), "away_exp": expected_aces(ra, sg),
        "service_games": sg,
    }
