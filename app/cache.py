"""Cache clé/valeur avec TTL, persistance disque et lecture du périmé en secours.

- TTL par entrée (les données qui bougent peu sont gardées des heures).
- Persistance JSON optionnelle : le cache survit aux redémarrages -> pas de rafale
  d'appels (et donc moins de 403) au démarrage.
- ``get_stale`` renvoie la dernière valeur même expirée : sert de filet quand la
  source est indisponible (stale-while-error).
- **Éviction** : les entrées périmées depuis longtemps (au-delà de ``stale_grace``)
  et le surplus au-delà de ``max_entries`` sont supprimées, pour que le fichier ne
  grossisse pas sans fin (avant : 58 Mo réécrits en bloc) et que la sauvegarde reste
  rapide. La sauvegarde est **déchargée dans un thread** quand une boucle asyncio
  tourne, pour ne pas bloquer l'event loop.
"""

import asyncio
import json
import os
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, persist_path: str | None = None,
                 max_entries: int = 4000, stale_grace: float = 6 * 3600,
                 save_interval: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, dict] = {}   # key -> {"exp": epoch, "val": value}
        self._persist_path = persist_path
        self._max_entries = max_entries
        self._stale_grace = stale_grace      # on garde le périmé ce délai (filet anti-indispo)
        self._save_interval = save_interval
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

    # ------------------------------------------------------------- éviction
    def _evict(self, now: float) -> None:
        """Supprime les entrées mortes (périmées au-delà de la grâce) puis le surplus."""
        dead = [k for k, e in self._store.items() if now > e["exp"] + self._stale_grace]
        for k in dead:
            del self._store[k]
        excess = len(self._store) - self._max_entries
        if excess > 0:
            # on supprime celles dont l'expiration est la plus ancienne (les moins utiles)
            oldest = sorted(self._store.items(), key=lambda kv: kv[1]["exp"])[:excess]
            for k, _ in oldest:
                del self._store[k]

    # ----------------------------------------------------------- persistance
    def _load(self) -> None:
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                self._store = json.load(f)
        except (FileNotFoundError, ValueError):
            self._store = {}
            return
        self._evict(time.time())   # purge le mort hérité du run précédent

    def _maybe_save(self) -> None:
        if not self._persist_path:
            return
        now = time.time()
        if now - self._last_save < self._save_interval:  # anti-thrash
            return
        self._last_save = now
        self._evict(now)
        snapshot = dict(self._store)   # copie superficielle : références stables pour le dump
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:           # ne bloque pas l'event loop
            loop.run_in_executor(None, self._write, snapshot)
        else:
            self._write(snapshot)

    def _write(self, store: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            tmp = self._persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False)
            os.replace(tmp, self._persist_path)
        except OSError:
            pass  # le cache disque est un bonus, jamais bloquant
