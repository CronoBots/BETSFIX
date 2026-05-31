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


def service_games_range(best_of: int) -> tuple[float, float]:
    """Fourchette de jeux de service par joueur : match court (sec) -> long (distance)."""
    return (15.0, 24.0) if best_of == 5 else (10.0, 15.0)


def opponent_ace_factor(opp_return_rate: float | None, avg: float = 0.19) -> float:
    """Multiplicateur d'aces selon la force de RETOUR de l'adversaire.

    Un bon retourneur (taux de break > moyenne) remet plus de balles -> moins d'aces.
    Effet modéré (les aces restent surtout déterminés par le serveur). Borné.
    """
    if opp_return_rate is None or avg <= 0:
        return 1.0
    factor = 1.0 - 0.35 * (opp_return_rate - avg) / avg
    return max(0.8, min(1.15, factor))


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


def _ace_pack(rate, opp_ret, sg_short, sg_long, line):
    """Détail aces d'un joueur : taux ajusté adversaire, fourchette, P(plus de ligne)."""
    if rate is None:
        return {"rate": None}
    factor = opponent_ace_factor(opp_ret)
    adj = rate * factor
    exp_low, exp_high = adj * sg_short, adj * sg_long
    pack = {"rate": rate, "factor": round(factor, 2), "adj_rate": round(adj, 3),
            "exp_low": exp_low, "exp_high": exp_high,
            "exp_mid": adj * (sg_short + sg_long) / 2, "line": line}
    if line is not None:
        pack["p_over_low"] = prob_over(line, exp_low)
        pack["p_over_high"] = prob_over(line, exp_high)
    return pack


def for_match(match, best_of: int, fav_prob: float | None,
              store: dict | None = None, opp_ret_home: float | None = None,
              opp_ret_away: float | None = None, line_home: float | None = None,
              line_away: float | None = None) -> dict | None:
    """Récapitulatif aces des deux joueurs (fourchette durée + ajustement adversaire).

    opp_ret_* = force de retour de l'adversaire (réduit les aces d'un bon retourneur).
    line_* = ligne Unibet 'total aces joueur' (-> P(plus de la ligne)). None si absente.
    Renvoie {home_name, away_name, home:{...}, away:{...}, sg_short, sg_long} ou None.
    """
    store = store if store is not None else load_cached()
    if not store:
        return None
    rh = ace_rate(store.get(str(match.home.id)), match.ground_type)
    ra = ace_rate(store.get(str(match.away.id)), match.ground_type)
    if rh is None and ra is None:
        return None
    sg_short, sg_long = service_games_range(best_of)
    return {
        "home_name": match.home.name, "away_name": match.away.name,
        "sg_short": sg_short, "sg_long": sg_long,
        # l'adversaire de 'home' est 'away' (et inversement) -> retour croisé
        "home": _ace_pack(rh, opp_ret_away, sg_short, sg_long, line_home),
        "away": _ace_pack(ra, opp_ret_home, sg_short, sg_long, line_away),
    }
