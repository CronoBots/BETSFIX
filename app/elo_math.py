"""Maths Elo partagées (testables) : marge de victoire + régression inter-saison.

Réutilisé par les builds foot/basket (tools/build_*_elo.py) pour une note Elo plus juste :
- la **marge de victoire** (style FiveThirtyEight) pondère l'ajustement -> un favori qui
  écrase ne gagne pas trop de points (anti auto-corrélation), un outsider qui gagne large
  en gagne plus ;
- la **régression vers la moyenne** entre saisons reflète le renouvellement des effectifs.
"""

from __future__ import annotations

import math


def expected(elo_a: float, elo_b: float) -> float:
    """Probabilité que A batte B selon l'écart Elo (formule logistique classique)."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def mov_multiplier(margin: float, elo_diff: float) -> float:
    """Multiplicateur de marge de victoire (FiveThirtyEight).

    `margin`   = |différence de score| (>0 résultat décisif, 0 = nul -> multiplicateur 1).
    `elo_diff` = Elo_gagnant - Elo_perdant AVANT le match (avantage terrain inclus).

    Le terme 2.2/(elo_diff*0.001+2.2) corrige l'auto-corrélation : quand un gros favori
    (elo_diff élevé) gagne, le bonus de marge est réduit ; quand un outsider gagne large,
    il est amplifié.
    """
    if margin <= 0:
        return 1.0
    return math.log(abs(margin) + 1.0) * (2.2 / (elo_diff * 0.001 + 2.2))


def regress_to_mean(elo: float, base: float = 1500.0, frac: float = 0.25) -> float:
    """Régresse un Elo de `frac` vers `base` (à appliquer au changement de saison)."""
    return (1.0 - frac) * elo + frac * base
