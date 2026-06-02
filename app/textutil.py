"""Outils de normalisation de noms — centralisés (joueurs ET équipes).

Avant, la même logique NFKD (mise en minuscule + suppression des accents) était
réécrite dans 6 modules (unibet, livescore, rankings, foot, basket, matches), avec
des variantes divergentes. Tout passe désormais par ici.

`names_match` est le matcheur SofaScore ↔ Unibet : il exige au moins un token
DISCRIMINANT partagé (on ignore les mots génériques type « united », « fc », « real »
qui, seuls, apparieraient deux équipes différentes). À combiner avec un contrôle de
date côté appelant pour lever les dernières ambiguïtés.
"""

from __future__ import annotations

import unicodedata

# Mots qui n'identifient PAS une équipe à eux seuls : s'ils sont le seul token
# commun, ce n'est pas une correspondance (évite « Manchester United » ↔ « Newcastle
# United », « Real Madrid » ↔ « Real Sociedad », « LA Lakers » ↔ « LA Clippers »…).
GENERIC_TOKENS = frozenset({
    # mentions de club / statut (toutes langues courantes)
    "fc", "cf", "sc", "ac", "afc", "cd", "ca", "sv", "bk", "if", "ff", "club",
    "calcio", "sport", "sports", "sporting", "deportivo", "deportes", "atletico",
    "athletic", "racing", "united", "city", "town", "county", "rovers", "wanderers",
    "real", "dynamo", "dinamo", "lokomotiv", "spartak", "inter", "national",
    "national", "team", "select", "sociedad", "union", "olympique", "olympic",
    # articles / prépositions
    "the", "de", "del", "la", "le", "el", "los", "las", "du", "des", "of", "and",
    "al", "as", "ec", "sk", "fk", "us", "ud", "cd",
    # géographie générique fréquente
    "san", "santa", "new", "north", "south", "east", "west", "saint", "st",
})


def fold(text: str) -> str:
    """Minuscule + sans accents (repli ASCII). « Menšík » -> « mensik »."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def name_tokens(name: str, min_len: int = 2) -> set[str]:
    """Jeu de tokens d'un nom : sans accents, sans ponctuation, sans initiales.

    `min_len=2` ignore les initiales (« C. ») mais garde les noms courts légitimes
    (« Wu », « Li »). Mettre `min_len=1` pour tout garder.
    """
    folded = fold(name)
    for sep in ".-/()',":   # ponctuation et marqueurs « (W) »/« (F) » -> séparateurs
        folded = folded.replace(sep, " ")
    return {t for t in folded.split() if len(t) >= min_len}


def name_substring(query: str, name: str) -> bool:
    """Recherche tolérante : `query` (replié) est-il une sous-chaîne de `name` (replié) ?

    Sert au filtre de recherche par joueur : « mensik » trouve « Menšík »."""
    return fold(query) in fold(name)


def names_match(a_tokens: set[str], b_tokens: set[str]) -> bool:
    """Vrai si deux noms (en tokens) désignent la même entité.

    Exige au moins un token commun NON générique (« united » seul ne suffit pas).
    À renforcer par un contrôle de date côté appelant."""
    if not a_tokens or not b_tokens:
        return False
    common = (a_tokens & b_tokens) - GENERIC_TOKENS
    return bool(common)
