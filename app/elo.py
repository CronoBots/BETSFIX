"""Notes Elo par surface — le facteur de force le plus fiable du modèle.

Le classement ATP/WTA est une somme de points sur 52 semaines : il retarde, est
faussé par les absences/blessures et ignore *contre qui* on a gagné. L'Elo corrige
tout ça en ne bougeant qu'au résultat, pondéré par la force de l'adversaire — et un
**Elo spécifique terre battue** capture les spécialistes (Nadal-like) que le rang
brut rate.

Le calcul des notes (tools/build_elo.py) est séparé du *service* : ici on ne fait
que charger un instantané (data/elo_ratings.json) et exposer des fonctions **pures**
(sans réseau) pour l'analyse. Si l'instantané manque, tout retombe proprement sur le
classement (facteur classement déjà présent dans analysis.py).

Format du store : {"<player_id>": {"name", "overall", "overall_n", "clay",
"clay_n"}}. Les notes sont des Elo classiques (base 1500).
"""

from __future__ import annotations

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATINGS_PATH = os.path.join(_ROOT, "data", "elo_ratings.json")

BASE = 1500.0          # note de départ d'un joueur inconnu
K = 24.0               # vitesse d'ajustement (classique tennis : 20-32)
MIN_CLAY_MATCHES = 8   # en-deçà, l'Elo terre est trop bruité -> on prend le global


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probabilité Elo que A batte B (logistique base 10, échelle 400)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def prob_from_elo(elo_home: float | None, elo_away: float | None) -> float | None:
    """Probabilité de victoire de 'home' selon les notes Elo (None si manquant)."""
    if elo_home is None or elo_away is None:
        return None
    return expected_score(elo_home, elo_away)


def is_clay(ground_type: str | None) -> bool:
    return "clay" in (ground_type or "").lower()


def surface_rating(rec: dict | None, ground_type: str | None) -> float | None:
    """Note à utiliser pour ce joueur sur cette surface.

    Terre battue + assez de matchs terre -> Elo terre ; sinon Elo global. None si on
    n'a aucune note fiable pour ce joueur.
    """
    if not rec:
        return None
    if is_clay(ground_type) and (rec.get("clay_n") or 0) >= MIN_CLAY_MATCHES:
        r = rec.get("clay")
        if r is not None:
            return r
    return rec.get("overall")


# ----------------------------------------------------------- mise à jour (build)
def update_ratings(store: dict, home_id, away_id, home_won: bool,
                    on_clay: bool, home_name: str = "", away_name: str = "") -> None:
    """Met à jour le store Elo avec un match terminé (chronologie croissante).

    Met à jour la note globale des deux joueurs ; si le match est sur terre battue,
    met aussi à jour leurs notes terre. Fonction pure (mute `store`, sans réseau).
    """
    if home_id is None or away_id is None:
        return
    hk, ak = str(home_id), str(away_id)
    h = store.setdefault(hk, {"name": home_name, "overall": BASE, "overall_n": 0,
                              "clay": BASE, "clay_n": 0})
    a = store.setdefault(ak, {"name": away_name, "overall": BASE, "overall_n": 0,
                              "clay": BASE, "clay_n": 0})
    if home_name:
        h["name"] = home_name
    if away_name:
        a["name"] = away_name

    sh = 1.0 if home_won else 0.0
    for field, n_field in (("overall", "overall_n"), ("clay", "clay_n")) if on_clay \
            else (("overall", "overall_n"),):
        eh = expected_score(h[field], a[field])
        h[field] += K * (sh - eh)
        a[field] += K * ((1.0 - sh) - (1.0 - eh))
        h[n_field] += 1
        a[n_field] += 1


# ----------------------------------------------------------------- I/O instantané
def load(path: str = RATINGS_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save(store: dict, path: str = RATINGS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    os.replace(tmp, path)


# Cache mémoire de l'instantané, rechargé si le fichier change (mtime).
_cache: dict = {"mtime": None, "store": {}}


def load_cached(path: str = RATINGS_PATH) -> dict:
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache["mtime"] != mt:
        _cache["store"] = load(path)
        _cache["mtime"] = mt
    return _cache["store"]


def ratings_for_match(match, store: dict | None = None) -> tuple[float | None, float | None]:
    """(elo_home, elo_away) adaptés à la surface du match. Vide -> (None, None)."""
    store = store if store is not None else load_cached()
    if not store:
        return None, None
    eh = surface_rating(store.get(str(match.home.id)), match.ground_type)
    ea = surface_rating(store.get(str(match.away.id)), match.ground_type)
    return eh, ea
