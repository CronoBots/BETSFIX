"""Provider de classements ATP/WTA (jeu de données public, indépendant de SofaScore).

Source : dépôts GitHub de Jeff Sackmann (raw, stable, jamais rate-limité). Permet
d'afficher le favori du modèle (basé sur le classement) même quand SofaScore bloque.
Mis en cache en mémoire et rafraîchi ~1x/jour.
"""

from __future__ import annotations

import csv
import io
import time
import unicodedata

import httpx

_BASE = "https://raw.githubusercontent.com/JeffSackmann"
_URLS = {
    "atp": (f"{_BASE}/tennis_atp/master/atp_players.csv",
            f"{_BASE}/tennis_atp/master/atp_rankings_current.csv"),
    "wta": (f"{_BASE}/tennis_wta/master/wta_players.csv",
            f"{_BASE}/tennis_wta/master/wta_rankings_current.csv"),
}
_TTL = 12 * 3600


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(text.replace(".", " ").replace("-", " ").split())


class RankingsProvider:
    def __init__(self, settings) -> None:
        self._client = httpx.AsyncClient(timeout=settings.http_timeout,
                                         headers={"User-Agent": settings.http_user_agent})
        self._by_full: dict[str, dict[str, int]] = {"atp": {}, "wta": {}}
        self._by_last: dict[str, dict[str, int]] = {"atp": {}, "wta": {}}
        self._loaded_at: dict[str, float] = {"atp": 0.0, "wta": 0.0}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure(self, tour: str) -> None:
        if time.time() - self._loaded_at.get(tour, 0) < _TTL and self._by_full[tour]:
            return
        players_url, rankings_url = _URLS[tour]
        try:
            pr = await self._client.get(players_url)
            rr = await self._client.get(rankings_url)
            pr.raise_for_status()
            rr.raise_for_status()
        except httpx.HTTPError:
            return  # on garde l'éventuel cache précédent
        id_to_name = {}
        for row in csv.DictReader(io.StringIO(pr.text)):
            pid = row.get("player_id") or row.get("id")
            nm = f"{row.get('name_first','')} {row.get('name_last','')}".strip()
            if pid and nm:
                id_to_name[pid] = nm
        # garde le classement de la date la plus récente du fichier
        rows = list(csv.DictReader(io.StringIO(rr.text)))
        if not rows:
            return
        last_date = max(r["ranking_date"] for r in rows)
        full, last = {}, {}
        for r in rows:
            if r["ranking_date"] != last_date:
                continue
            nm = id_to_name.get(r.get("player"))
            if not nm:
                continue
            try:
                rank = int(r["rank"])
            except (ValueError, TypeError):
                continue
            n = _norm(nm)
            full[n] = rank
            ln = n.split()[-1] if n else ""
            # index nom de famille : on garde le mieux classé en cas d'homonyme
            if ln and (ln not in last or rank < last[ln]):
                last[ln] = rank
        if full:
            self._by_full[tour] = full
            self._by_last[tour] = last
            self._loaded_at[tour] = time.time()

    async def rank(self, tour: str, name: str) -> int | None:
        tour = "wta" if tour == "wta" else "atp"
        await self._ensure(tour)
        n = _norm(name)
        if n in self._by_full[tour]:
            return self._by_full[tour][n]
        # repli : nom de famille (dernier token)
        tokens = n.split()
        if tokens:
            return self._by_last[tour].get(tokens[-1])
        return None
