"""Provider SofaScore : source de données gratuite (sans clé) pour Roland Garros.

SofaScore expose une API JSON publique. Les endpoints utilisés ici :
  - /unique-tournament/{id}/seasons
  - /unique-tournament/{id}/season/{seasonId}/events/last/{page}
  - /unique-tournament/{id}/season/{seasonId}/events/next/{page}
  - /event/{eventId}
  - /event/{eventId}/statistics

Toute la logique réseau et la normalisation vers nos modèles vit ici, ce qui
permet de remplacer la source sans toucher au reste de l'API.
"""

from __future__ import annotations

import asyncio

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.models import (
    Match,
    MatchStatistics,
    PeriodStatistics,
    Player,
    Score,
    StatisticGroup,
    StatisticItem,
    TournamentInfo,
)


class ProviderError(Exception):
    """Erreur levée quand la source de données est indisponible ou répond mal."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class SofaScoreProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache = TTLCache(settings.cache_ttl_seconds)
        self._client = httpx.AsyncClient(
            base_url=settings.sofascore_base_url,
            timeout=settings.http_timeout,
            headers={
                "User-Agent": settings.http_user_agent,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.sofascore.com/",
                "Origin": "https://www.sofascore.com",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----------------------------------------------------------------- réseau
    async def _get(self, path: str) -> dict:
        """GET avec cache TTL et gestion d'erreurs."""
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            resp = await self._client.get(path)
        except httpx.HTTPError as exc:  # réseau / timeout
            raise ProviderError(f"Source de données injoignable: {exc}") from exc

        if resp.status_code == 404:
            raise ProviderError("Ressource introuvable chez la source.", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(
                f"La source a répondu {resp.status_code} pour {path}",
                status_code=502,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Réponse non-JSON de la source.") from exc

        self._cache.set(path, data)
        return data

    def _tour_id(self, tour: str) -> int:
        try:
            return self._settings.tournament_ids[tour]
        except KeyError:
            raise ProviderError(f"Tour inconnu: {tour!r} (attendu 'atp' ou 'wta').", status_code=400)

    # ------------------------------------------------------------- tournois
    async def get_tournament_info(self, tour: str) -> TournamentInfo:
        tid = self._tour_id(tour)
        data = await self._get(f"/unique-tournament/{tid}/seasons")
        seasons = data.get("seasons", []) or []
        current = seasons[0] if seasons else {}
        return TournamentInfo(
            tour=tour,
            id=tid,
            name="Roland Garros" if tour == "atp" else "Roland Garros (WTA)",
            current_season_id=current.get("id"),
            current_season=_to_int(current.get("year")),
        )

    async def _resolve_season_id(self, tour: str, season: int | None) -> int:
        """Retourne l'identifiant de saison SofaScore pour une année donnée (ou la plus récente)."""
        tid = self._tour_id(tour)
        data = await self._get(f"/unique-tournament/{tid}/seasons")
        seasons = data.get("seasons", []) or []
        if not seasons:
            raise ProviderError("Aucune saison trouvée pour ce tournoi.", status_code=404)
        if season is None:
            return seasons[0]["id"]
        for s in seasons:
            if _to_int(s.get("year")) == season:
                return s["id"]
        raise ProviderError(f"Saison {season} introuvable pour {tour}.", status_code=404)

    # --------------------------------------------------------------- matchs
    async def get_matches(self, tour: str, season: int | None = None) -> list[Match]:
        """Récupère TOUS les matchs (passés + à venir) d'une édition de Roland Garros."""
        tid = self._tour_id(tour)
        season_id = await self._resolve_season_id(tour, season)
        events = await self._fetch_all_events(tid, season_id)

        matches = [self._normalize_match(tour, ev) for ev in events]
        # Tri chronologique, par ordre de round puis heure de début
        matches.sort(key=lambda m: (m.start_time or _far_future()))
        return matches

    async def _fetch_all_events(self, tid: int, season_id: int) -> list[dict]:
        """Pagine les événements 'last' (terminés) et 'next' (à venir)."""
        events: list[dict] = []
        seen: set[int] = set()
        for direction in ("last", "next"):
            page = 0
            while True:
                path = f"/unique-tournament/{tid}/season/{season_id}/events/{direction}/{page}"
                try:
                    data = await self._get(path)
                except ProviderError as exc:
                    if exc.status_code == 404:
                        break  # plus de pages
                    raise
                page_events = data.get("events", []) or []
                for ev in page_events:
                    eid = ev.get("id")
                    if eid is not None and eid not in seen:
                        seen.add(eid)
                        events.append(ev)
                if not data.get("hasNextPage"):
                    break
                page += 1
                if page > 50:  # garde-fou anti-boucle infinie
                    break
        return events

    async def get_match(self, tour: str, match_id: int) -> Match:
        data = await self._get(f"/event/{match_id}")
        event = data.get("event")
        if not event:
            raise ProviderError("Match introuvable.", status_code=404)
        return self._normalize_match(tour, event)

    # ----------------------------------------------------------- statistiques
    async def get_statistics(self, match_id: int) -> MatchStatistics:
        data = await self._get(f"/event/{match_id}/statistics")
        return self._normalize_statistics(match_id, data)

    async def get_all_statistics(
        self, tour: str, season: int | None = None
    ) -> dict[int, MatchStatistics]:
        """Récupère les statistiques de tous les matchs terminés (en parallèle)."""
        matches = await self.get_matches(tour, season)
        finished = [m for m in matches if m.status == "finished"]

        async def _safe(mid: int) -> tuple[int, MatchStatistics | None]:
            try:
                return mid, await self.get_statistics(mid)
            except ProviderError:
                return mid, None  # certains matchs n'ont pas de stats

        results = await asyncio.gather(*[_safe(m.id) for m in finished])
        return {mid: stats for mid, stats in results if stats is not None}

    # ------------------------------------------------------- normalisation
    def _normalize_match(self, tour: str, ev: dict) -> Match:
        round_info = ev.get("roundInfo") or {}
        status = ev.get("status") or {}
        tournament = ev.get("tournament") or {}
        season = ev.get("season") or {}
        winner_code = ev.get("winnerCode")
        winner = {1: "home", 2: "away"}.get(winner_code)

        return Match(
            id=ev["id"],
            tour=tour,
            tournament=(tournament.get("uniqueTournament") or {}).get("name", "Roland Garros"),
            season=_to_int(season.get("year")),
            round=round_info.get("name") or _round_name(round_info.get("round")),
            round_code=round_info.get("round"),
            status=status.get("type"),
            status_description=status.get("description"),
            court=(ev.get("venue") or {}).get("name") or ev.get("courtName"),
            start_time=Match._ts_to_dt(ev.get("startTimestamp")),
            home=_player(ev.get("homeTeam")),
            away=_player(ev.get("awayTeam")),
            home_score=_score(ev.get("homeScore")),
            away_score=_score(ev.get("awayScore")),
            winner=winner,
            has_statistics=bool(ev.get("hasEventPlayerStatistics") or status.get("type") == "finished"),
        )

    def _normalize_statistics(self, match_id: int, data: dict) -> MatchStatistics:
        periods: list[PeriodStatistics] = []
        for period in data.get("statistics", []) or []:
            groups: list[StatisticGroup] = []
            for group in period.get("groups", []) or []:
                items = [
                    StatisticItem(
                        name=item.get("name", ""),
                        home=_as_str(item.get("home")),
                        away=_as_str(item.get("away")),
                    )
                    for item in group.get("statisticsItems", []) or []
                ]
                groups.append(StatisticGroup(name=group.get("groupName", ""), items=items))
            periods.append(
                PeriodStatistics(period=period.get("period", "ALL"), groups=groups)
            )
        return MatchStatistics(match_id=match_id, periods=periods)


# --------------------------------------------------------------- helpers
def _player(team: dict | None) -> Player:
    team = team or {}
    country = (team.get("country") or {}).get("name") or team.get("nameCode")
    return Player(
        id=team.get("id"),
        name=team.get("name", ""),
        country=country,
        ranking=team.get("ranking"),
    )


def _score(score: dict | None) -> Score:
    score = score or {}
    sets: list[int | None] = []
    tiebreaks: list[int | None] = []
    for i in range(1, 6):  # jusqu'à 5 sets
        period = score.get(f"period{i}")
        if period is not None:
            sets.append(period)
            tiebreaks.append(score.get(f"period{i}TieBreak"))
    return Score(sets_won=score.get("current"), sets=sets, tiebreaks=tiebreaks)


def _round_name(code: int | None) -> str | None:
    mapping = {
        1: "Finale",
        2: "Demi-finale",
        4: "Quart de finale",
        8: "1/8 de finale",
        16: "1/16 de finale",
        32: "1/32 de finale",
        64: "1/64 de finale",
        128: "1er tour",
    }
    return mapping.get(code) if code is not None else None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _far_future():
    from datetime import datetime, timezone

    return datetime(9999, 1, 1, tzinfo=timezone.utc)
