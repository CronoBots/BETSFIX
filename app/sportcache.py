"""Cache mémoire ultra-léger partagé par les modules foot/basket.

Ces modules interrogent SofaScore/Unibet via leur propre client (hors provider
tennis). Sans cache, chaque chargement de page re-télécharge tout -> lenteur et
risque de rate-limit 403. Ce cache TTL court évite les appels redondants : au plus
un fetch par clé toutes les `ttl` secondes, quel que soit le nombre de visites.

Seuls les résultats valides (non None) sont mis en cache -> un échec réseau est
retenté au prochain appel (pas de mémorisation d'une erreur).
"""

from __future__ import annotations

import time

_store: dict[str, tuple[float, object]] = {}
DEFAULT_TTL = 90.0          # secondes : assez court pour rester frais, assez long pour amortir

# Disjoncteur anti-403 pour SofaScore (foot/basket n'ont pas le breaker du provider).
_blocked_until = 0.0
BREAKER_S = 90.0            # pause après un 403/429 : on ne re-tape pas SofaScore pendant ce temps


def get(key: str):
    """Valeur en cache si encore valide, sinon None."""
    entry = _store.get(key)
    if entry is not None and entry[0] > time.monotonic():
        return entry[1]
    return None


def put(key: str, value, ttl: float = DEFAULT_TTL) -> None:
    if value is not None:
        _store[key] = (time.monotonic() + ttl, value)


def blocked() -> bool:
    """Vrai si SofaScore est en pause anti-403 (on évite de le solliciter)."""
    return time.monotonic() < _blocked_until


def trip(seconds: float = BREAKER_S) -> None:
    """Ouvre le disjoncteur : on cesse de taper SofaScore pendant `seconds`."""
    global _blocked_until
    _blocked_until = time.monotonic() + seconds


def reset() -> None:
    global _blocked_until
    _blocked_until = 0.0


def clear() -> None:   # utilitaire (tests)
    _store.clear()
    reset()
