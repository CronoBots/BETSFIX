"""Cache clé/valeur avec TTL, persistance disque et lecture du périmé en secours.

- TTL par entrée (les données qui bougent peu sont gardées des heures).
- Persistance JSON optionnelle : le cache survit aux redémarrages -> pas de rafale
  d'appels (et donc moins de 403) au démarrage.
- ``get_stale`` renvoie la dernière valeur même expirée : sert de filet quand la
  source est indisponible (stale-while-error).
"""

import json
import os
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, persist_path: str | None = None) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, dict] = {}   # key -> {"exp": epoch, "val": value}
        self._persist_path = persist_path
        self._last_save = 0.0
        if persist_path:
            self._load()

    def get(self, key: str) -> Any | None:
        """Valeur encore fraîche, sinon None."""
        e = self._store.get(key)
        if e is None:
            return None
        if time.time() > e["exp"]:
            return None
        return e["val"]

    def get_stale(self, key: str) -> Any | None:
        """Dernière valeur connue même périmée (filet anti-indispo)."""
        e = self._store.get(key)
        return e["val"] if e else None

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self._store[key] = {"exp": time.time() + (ttl if ttl is not None else self._ttl),
                            "val": value}
        self._maybe_save()

    def clear(self) -> None:
        self._store.clear()

    # ----------------------------------------------------------- persistance
    def _load(self) -> None:
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                self._store = json.load(f)
        except (FileNotFoundError, ValueError):
            self._store = {}

    def _maybe_save(self) -> None:
        if not self._persist_path:
            return
        now = time.time()
        if now - self._last_save < 10:  # anti-thrash : au plus une écriture / 10s
            return
        self._last_save = now
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            tmp = self._persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False)
            os.replace(tmp, self._persist_path)
        except OSError:
            pass  # le cache disque est un bonus, jamais bloquant
