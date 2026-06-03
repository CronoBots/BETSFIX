"""Cache mémoire court (TTL) des fragments d'analyse de match.

Quand plusieurs utilisateurs — ou plusieurs clics — ouvrent la MÊME fiche, on ne refait pas
les appels SofaScore/Unibet ni le rendu : on sert le HTML mis en cache. Indispensable en
forte charge (un match « viral » ouvert par des milliers de personnes = 1 seule récupération).
"""

from __future__ import annotations

import time

_store: dict[str, tuple[float, str]] = {}   # clé -> (expiration_epoch, html)
_force: set[str] = set()                     # clés à recalculer une fois (pré-chauffage)
DEFAULT_TTL = 300                            # 5 min : assez frais, gros gain de charge
_MAX = 600                                   # garde-fou mémoire


def get(key: str) -> str | None:
    """HTML en cache si encore valide, sinon None. Une clé marquée `force_refresh` renvoie
    None UNE fois (déclenche un recalcul) sans perdre la valeur stockée entre-temps."""
    if key in _force:
        _force.discard(key)
        return None
    v = _store.get(key)
    if v and v[0] > time.time():
        return v[1]
    return None


def force_refresh(key: str) -> None:
    """Force UN recalcul au prochain get() (utilisé par le pré-chauffage de fond) : l'ancienne
    valeur reste servie aux autres requêtes jusqu'à ce que le recalcul la remplace (put)."""
    _force.add(key)


def put(key: str, html: str, ttl: float = DEFAULT_TTL) -> None:
    _store[key] = (time.time() + ttl, html)
    if len(_store) > _MAX:                    # purge des entrées expirées
        now = time.time()
        for k in [k for k, (exp, _) in list(_store.items()) if exp <= now]:
            _store.pop(k, None)
