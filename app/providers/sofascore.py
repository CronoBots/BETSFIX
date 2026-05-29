"""Provider SofaScore : source de données gratuite (sans clé) pour Roland Garros.

SofaScore expose une API JSON publique. Les endpoints utilisés ici :
  - /unique-tournament/{id}/seasons
  - /unique-tournament/{id}/season/{seasonId}/events/last/{page}
  - /unique-tournament/{id}/season/{seasonId}/events/next/{page}
  - /event/{eventId}
  - /event/{eventId}/statistics
  - /event/{eventId}/point-by-point
  - /event/{eventId}/h2h
  - /event/{eventId}/votes
  - /event/{eventId}/team-streaks
  - /event/{eventId}/odds/1/all
  - /team/{playerId}            (fiche joueur)
  - /team/{playerId}/rankings
  - /team/{playerId}/events/last/{page}
  - /team/{playerId}/image

Toute la logique réseau et la normalisation vers nos modèles vit ici, ce qui
permet de remplacer la source sans toucher au reste de l'API.
"""

from __future__ import annotations

import asyncio

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.models import (
    HeadToHead,
    Match,
    MatchPointByPoint,
    MatchOdds,
    MatchStatistics,
    MatchStreaks,
    MatchVotes,
    OddChoice,
    OddsMarket,
    PeriodStatistics,
    Player,
    PlayerProfile,
    PointByPointGame,
    PointByPointPoint,
    PointByPointSet,
    RankingEntry,
    Score,
    StatisticGroup,
    StatisticItem,
    Streak,
    TournamentInfo,
    TournamentSeason,
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

    async def _get_bytes(self, path: str) -> tuple[bytes, str]:
        """GET binaire (images). Retourne (contenu, content-type)."""
        try:
            resp = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Source de données injoignable: {exc}") from exc
        if resp.status_code == 404:
            raise ProviderError("Ressource introuvable chez la source.", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"La source a répondu {resp.status_code} pour {path}", status_code=502)
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

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

    async def get_seasons(self, tour: str) -> list[TournamentSeason]:
        """Toutes les éditions disponibles du tournoi (de la plus récente à la plus ancienne)."""
        tid = self._tour_id(tour)
        data = await self._get(f"/unique-tournament/{tid}/seasons")
        return [
            TournamentSeason(id=s.get("id"), year=_to_int(s.get("year")), name=s.get("name"))
            for s in data.get("seasons", []) or []
        ]

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

    async def get_point_by_point(self, match_id: int) -> MatchPointByPoint:
        """Déroulé point par point d'un match (tennis : /event/{id}/point-by-point)."""
        data = await self._get(f"/event/{match_id}/point-by-point")
        return self._normalize_point_by_point(match_id, data)

    async def get_head_to_head(self, tour: str, match_id: int) -> HeadToHead:
        """Bilan des confrontations directes des deux joueurs d'un match."""
        match = await self.get_match(tour, match_id)
        data = await self._get(f"/event/{match_id}/h2h")
        duel = data.get("teamDuel") or {}
        return HeadToHead(
            match_id=match_id,
            home=match.home,
            away=match.away,
            home_wins=duel.get("homeWins"),
            away_wins=duel.get("awayWins"),
            draws=duel.get("draws"),
        )

    async def get_votes(self, match_id: int) -> MatchVotes:
        """Pronostics des fans pour un match."""
        data = await self._get(f"/event/{match_id}/votes")
        vote = data.get("vote") or {}
        v1, v2 = vote.get("vote1"), vote.get("vote2")
        total = (v1 or 0) + (v2 or 0)
        pct = lambda v: round(100 * v / total, 1) if total and v is not None else None
        return MatchVotes(
            match_id=match_id,
            home_votes=v1,
            away_votes=v2,
            home_percent=pct(v1),
            away_percent=pct(v2),
        )

    async def get_odds(self, match_id: int) -> MatchOdds:
        """Cotes (paris) d'un match : tous les marchés et choix disponibles."""
        data = await self._get(f"/event/{match_id}/odds/1/all")
        markets = [
            OddsMarket(
                market_id=m.get("marketId"),
                name=m.get("marketName", ""),
                group=m.get("marketGroup"),
                period=m.get("marketPeriod"),
                is_live=m.get("isLive"),
                suspended=m.get("suspended"),
                handicap=m.get("choiceGroup"),
                choices=[
                    OddChoice(
                        name=c.get("name", ""),
                        fractional=c.get("fractionalValue"),
                        decimal=_fractional_to_decimal(c.get("fractionalValue")),
                        initial_fractional=c.get("initialFractionalValue"),
                        winning=c.get("winning"),
                        change=c.get("change"),
                    )
                    for c in m.get("choices", []) or []
                ],
            )
            for m in data.get("markets", []) or []
        ]
        return MatchOdds(match_id=match_id, markets=markets)

    async def get_streaks(self, match_id: int) -> MatchStreaks:
        """Séries en cours / records autour d'un match."""
        data = await self._get(f"/event/{match_id}/team-streaks")

        def _streaks(items: list) -> list[Streak]:
            return [
                Streak(
                    name=s.get("name", ""),
                    value=_as_str(s.get("value")),
                    side=s.get("team"),
                    continued=s.get("continued"),
                )
                for s in items or []
            ]

        return MatchStreaks(
            match_id=match_id,
            general=_streaks(data.get("general")),
            head_to_head=_streaks(data.get("head2head")),
        )

    # --------------------------------------------------------------- joueurs
    async def get_player(self, player_id: int) -> PlayerProfile:
        """Fiche détaillée d'un joueur (bio + classement courant)."""
        data = await self._get(f"/team/{player_id}")
        team = data.get("team") or {}
        info = team.get("playerTeamInfo") or {}
        return PlayerProfile(
            id=team.get("id"),
            name=team.get("name", ""),
            full_name=team.get("fullName"),
            short_name=team.get("shortName"),
            gender=team.get("gender"),
            country=(team.get("country") or {}).get("name"),
            national=team.get("national"),
            ranking=team.get("ranking"),
            plays=info.get("plays"),
            height_m=info.get("height"),
            weight_kg=info.get("weight"),
            turned_pro=_as_str(info.get("turnedPro")),
            birth_date=Match._ts_to_dt(info.get("birthDateTimestamp")),
            birth_place=(info.get("birthCity") or {}).get("name"),
            residence=(info.get("residenceCity") or {}).get("name"),
            prize_current=info.get("prizeCurrent"),
            prize_total=info.get("prizeTotal"),
            user_count=team.get("userCount"),
        )

    async def get_player_image(self, player_id: int) -> tuple[bytes, str]:
        """Photo d'un joueur (contenu binaire + content-type)."""
        return await self._get_bytes(f"/team/{player_id}/image")

    async def get_player_rankings(self, player_id: int) -> list[RankingEntry]:
        """Toutes les lignes de classement d'un joueur (ATP/WTA, Live, UTR…)."""
        data = await self._get(f"/team/{player_id}/rankings")
        return [
            RankingEntry(
                ranking_class=r.get("rankingClass"),
                type=r.get("type"),
                ranking=r.get("ranking"),
                points=r.get("points"),
                previous_ranking=r.get("previousRanking"),
                previous_points=r.get("previousPoints"),
                best_ranking=r.get("bestRanking"),
                tournaments_played=r.get("tournamentsPlayed"),
            )
            for r in data.get("rankings", []) or []
        ]

    async def get_player_matches(self, player_id: int, pages: int = 2) -> list[Match]:
        """Matchs récents d'un joueur (toutes compétitions), du plus récent au plus ancien."""
        matches: list[Match] = []
        seen: set[int] = set()
        for page in range(pages):
            try:
                data = await self._get(f"/team/{player_id}/events/last/{page}")
            except ProviderError as exc:
                if exc.status_code == 404:
                    break
                raise
            for ev in data.get("events", []) or []:
                eid = ev.get("id")
                if eid is not None and eid not in seen:
                    seen.add(eid)
                    matches.append(self._normalize_match(_tour_of(ev), ev))
            if not data.get("hasNextPage"):
                break
        matches.sort(key=lambda m: (m.start_time or _far_future()), reverse=True)
        return matches

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

        venue = ev.get("venue") or {}
        time = ev.get("time") or {}
        set_durations = [time.get(f"period{i}") for i in range(1, 6) if time.get(f"period{i}") is not None]
        total = sum(d for d in set_durations if d) or None

        return Match(
            id=ev["id"],
            tour=tour,
            tournament=(tournament.get("uniqueTournament") or {}).get("name", "Roland Garros"),
            season=_to_int(season.get("year")),
            round=round_info.get("name") or _round_name(round_info.get("round")),
            round_slug=round_info.get("slug"),
            round_code=round_info.get("round"),
            status=status.get("type"),
            status_description=status.get("description"),
            court=venue.get("name") or ev.get("courtName"),
            city=((venue.get("city") or {}).get("name")),
            country=((venue.get("country") or {}).get("name")),
            ground_type=ev.get("groundType"),
            start_time=Match._ts_to_dt(ev.get("startTimestamp")),
            duration_seconds=total,
            set_durations=set_durations,
            first_to_serve=_side(ev.get("firstToServe")),
            home=_player(ev.get("homeTeam")),
            away=_player(ev.get("awayTeam")),
            home_seed=_as_str(ev.get("homeTeamSeed")),
            away_seed=_as_str(ev.get("awayTeamSeed")),
            home_score=_score(ev.get("homeScore")),
            away_score=_score(ev.get("awayScore")),
            winner=winner,
            has_statistics=bool(ev.get("hasEventPlayerStatistics") or status.get("type") == "finished"),
            custom_id=ev.get("customId"),
            slug=ev.get("slug"),
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

    def _normalize_point_by_point(self, match_id: int, data: dict) -> MatchPointByPoint:
        sets: list[PointByPointSet] = []
        for set_block in data.get("pointByPoint", []) or []:
            games: list[PointByPointGame] = []
            for game in set_block.get("games", []) or []:
                score = game.get("score") or {}
                points = [
                    PointByPointPoint(
                        home=_as_str(pt.get("homePoint")),
                        away=_as_str(pt.get("awayPoint")),
                    )
                    for pt in game.get("points", []) or []
                ]
                games.append(
                    PointByPointGame(
                        game=game.get("game"),
                        home_score=score.get("homeScore"),
                        away_score=score.get("awayScore"),
                        server=_side(score.get("serving")),
                        points=points,
                    )
                )
            # SofaScore renvoie les jeux du plus récent au plus ancien : on remet
            # en ordre chronologique (jeu 1 -> n).
            games.sort(key=lambda g: g.game if g.game is not None else 0)
            sets.append(PointByPointSet(set=set_block.get("set"), games=games))
        sets.sort(key=lambda s: s.set if s.set is not None else 0)
        return MatchPointByPoint(match_id=match_id, sets=sets)


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


def _side(code) -> str | None:
    """SofaScore encode home=1, away=2."""
    return {1: "home", 2: "away"}.get(code)


def _tour_of(ev: dict) -> str:
    """Déduit 'atp'/'wta' d'un événement (via la catégorie du tournoi). Défaut: 'atp'."""
    tournament = ev.get("tournament") or {}
    cat = (tournament.get("uniqueTournament") or {}).get("category") or tournament.get("category") or {}
    slug = (cat.get("slug") or "").lower()
    if "wta" in slug:
        return "wta"
    gender = ((ev.get("homeTeam") or {}).get("gender") or "").upper()
    return "wta" if gender == "F" else "atp"


# Filtre de round bilingue. SofaScore renvoie les noms en anglais
# ('Final', 'Semifinals', ...) ; on accepte aussi les termes français usuels.
# Chaque clé (normalisée : minuscule, sans accent) pointe vers le nom anglais exact.
_ROUND_CANON = {
    # Finale
    "final": "Final", "finale": "Final",
    # Demi-finales
    "semifinals": "Semifinals", "semifinal": "Semifinals", "semi": "Semifinals",
    "demi-finale": "Semifinals", "demi-finales": "Semifinals",
    "demi finale": "Semifinals", "demi finales": "Semifinals", "demies": "Semifinals",
    # Quarts de finale
    "quarterfinals": "Quarterfinals", "quarterfinal": "Quarterfinals",
    "quart de finale": "Quarterfinals", "quarts de finale": "Quarterfinals", "quarts": "Quarterfinals",
    # Huitièmes / 4e tour
    "round of 16": "Round of 16", "huitieme de finale": "Round of 16",
    "huitiemes de finale": "Round of 16", "huitiemes": "Round of 16", "8e de finale": "Round of 16",
    # Tours
    "round of 32": "Round of 32",
    "round of 64": "Round of 64",
    "round of 128": "Round of 128", "1er tour": "Round of 128", "premier tour": "Round of 128",
}


def _norm_round(text: str) -> str:
    text = text.strip().lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ô", "o"), ("î", "i"), ("û", "u")):
        text = text.replace(a, b)
    return " ".join(text.split())


def round_matches(match: Match, query: str) -> bool:
    """Vrai si le round du match correspond à `query` (FR ou EN, slug accepté).

    Si la requête désigne un round connu (ex: 'Finale' -> 'Final'), on exige une
    correspondance exacte pour éviter que 'final' attrape aussi Quarter/Semifinals.
    Sinon, on retombe sur une recherche partielle (sous-chaîne) sur nom ou slug.
    """
    q = _norm_round(query)
    canon = _ROUND_CANON.get(q)
    if canon is not None:
        return (match.round or "").lower() == canon.lower()
    haystacks = (match.round or "", match.round_slug or "")
    return any(q in _norm_round(h) for h in haystacks if h)


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


def _fractional_to_decimal(fractional: str | None) -> float | None:
    """Convertit une cote fractionnaire 'a/b' en cote décimale (a/b + 1)."""
    if not fractional or "/" not in fractional:
        return None
    num, _, den = fractional.partition("/")
    try:
        return round(int(num) / int(den) + 1, 2)
    except (ValueError, ZeroDivisionError):
        return None


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
