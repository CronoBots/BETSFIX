"""Détection de VALUE sur les marchés Unibet (de-vig + EV) — pour les 3 sports, sans inventer de cote.

Principe HONNÊTE : la cote d'un book = sa proba implicite + une MARGE (overround). En retirant la marge
(de-vig proportionnel) on obtient la **proba JUSTE** du marché = l'ancre de référence. Un pari a de la
VALUE quand la proba ESTIMÉE (analyste, multi-sources) dépasse cette proba juste :
    EV = proba_estimée × cote − 1   (> 0 = value).

Ce module fournit : `devig` (probas justes + marge), `annotate` (proba juste/cote juste par issue) et
`ev`. Le dossier annote ainsi CHAQUE marché -> l'analyste compare sa proba à la proba juste de chaque
issue au lieu de la chercher à l'œil. Pure arithmétique sur les cotes RÉELLES.
"""

from __future__ import annotations


def devig(odds: list) -> tuple:
    """(probas_justes, marge) d'un marché par de-vig PROPORTIONNEL. `odds` = cotes décimales (>1).
    La marge (overround) = somme des probas implicites − 1. ([], 0.0) si entrée vide/invalide."""
    inv = [1.0 / o for o in odds if isinstance(o, (int, float)) and o > 1]
    s = sum(inv)
    if s <= 0:
        return ([], 0.0)
    return ([x / s for x in inv], s - 1.0)


def ev(prob: float, odds: float) -> float:
    """Espérance d'un pari de proba `prob` à la cote `odds` : prob×cote − 1 (>0 = value)."""
    if not (prob and odds):
        return 0.0
    return prob * odds - 1.0


def annotate(outcomes: list) -> tuple:
    """Annote les issues d'UN marché : ajoute `fair_prob` (proba juste) et `fair_odds` (cote juste)
    à chaque issue qui a une cote. Renvoie (outcomes, marge). De-vig sur TOUTES les issues cotées."""
    cotes = [o.get("odds") for o in outcomes if o.get("odds")]
    fair, margin = devig(cotes)
    it = iter(fair)
    for o in outcomes:
        if o.get("odds"):
            p = next(it, None)
            o["fair_prob"] = round(p, 4) if p else None
            o["fair_odds"] = round(1.0 / p, 2) if p else None
    return outcomes, round(margin, 4)
