"""Provider SofaScore : source de données gratuite (sans clé) pour Roland Garros.

SofaScore expose une API JSON publique. Les endpoints utilisés ici :
  - /unique-tournament/{id}/seasons
  - /unique-tournament/{id}/season/{seasonId}/events/last/{page}
  - /unique-tournament/{id}/season/{seasonId}/events/next/{page}
  - /event/{eventId}
  - /event/{eventId}/statistics        (tennis + foot + basket : même structure)
  - /event/{eventId}/incidents         (foot : buts, cartons, remplacements)
  - /event/{eventId}/lineups           (foot/basket : compositions)
  - /event/{eventId}/point-by-point
  - /event/{eventId}/h2h
  - /event/{eventId}/votes
  - /event/{eventId}/team-streaks
  - /event/{eventId}/odds/1/all
  - /team/{teamId}/unique-tournament/{tid}/season/{sid}/statistics/overall  (stats équipe foot/basket)
  - /team/{playerId}            (fiche joueur)
  - /team/{playerId}/rankings
  - /team/{playerId}/events/last/{page}
  - /team/{playerId}/image
  - /team/{playerId}/team-statistics/seasons
  - /team/{playerId}/unique-tournament/{tid}/season/{sid}/statistics/overall

Toute la logique réseau et la normalisation vers nos modèles vit ici, ce qui
permet de remplacer la source sans toucher au reste de l'API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from curl_cffi.requests import AsyncSession

from app import sofa_http
from app.cache import TTLCache
from app.config import Settings

log = logging.getLogger("uvicorn")

# Disjoncteur anti-403 : back-off croissant quand SofaScore rate-limite.
BREAKER_BASE_S = 30       # 1ère pause (vrai 403/429)
BREAKER_MAX_S = 120       # plafond (2 min) — assez pour souffler, pas trop pour ne pas rester bloqué
BREAKER_LIGHT_S = 15      # pause courte sur erreur réseau transitoire (timeout/coupure), sans escalade

_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "..", "data", "cache_sofascore.json")

def _ttl_for(path: str) -> float | None:
    """Durée de cache selon le type de donnée (les statiques tiennent des heures)."""
    if "/seasons" in path:
        return 6 * 3600
    if "/statistics/overall" in path or "/team-statistics/" in path or "/rankings" in path:
        return 3600
    if "/h2h" in path or "/point-by-point" in path:
        return 1800
    if "/incidents" in path or "/lineups" in path or "/pregame-form" in path:
        return 600
    if "/votes" in path:        # votes des fans : changent lentement -> cache long
        return 1800
    if "/standings/" in path or "/top-players/" in path or "/top-teams/" in path:
        return 3600
    if "/team/" in path and "/events" not in path:  # fiche joueur
        return 3600
    return None  # défaut (events, stats live, odds) -> TTL de base
from app.models import (
    HeadToHead,
    Match,
    MatchIncident,
    MatchIncidents,
    MatchPointByPoint,
    MatchOdds,
    MatchStatistics,
    MatchStreaks,
    MatchVotes,
    PregameForm,
    Standings,
    StandingRow,
    TeamForm,
    TeamSeasonStatistics,
    OddChoice,
    OddsMarket,
    PeriodStatistics,
    Player,
    PlayerProfile,
    PlayerStatistics,
    PlayerStatsAvailability,
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
        # Pas de persistance disque sous pytest (isolation des tests).
        persist = None if "pytest" in sys.modules else os.path.normpath(_CACHE_FILE)
        self._cache = TTLCache(settings.cache_ttl_seconds, persist_path=persist)
        self._refreshing: set[str] = set()   # chemins en cours de rafraîchissement (fond)
        # Borne les rafraîchissements de fond simultanés : une page touchant 50 chemins
        # périmés ne lance plus 50 requêtes d'un coup (rafale -> 403).
        self._refresh_sem = asyncio.Semaphore(4)
        # Borne TOUTE la concurrence réseau vers SofaScore (y compris les appels directs
        # comme les ~14 votes en parallèle de la home) : empêche les rafales qui déclenchent
        # le rate-limit 403. Au plus N requêtes réelles en vol à un instant donné.
        self._net_sem = asyncio.Semaphore(3)
        # Rate-limit DOUX : au moins MIN_REQ_GAP entre deux requêtes réseau réelles, pour
        # que les grosses passes (boucle de suivi) ne dépassent plus le seuil 403 de SofaScore.
        self._min_gap = 0.25
        self._last_req = 0.0
        # URL de base (curl_cffi ne gère pas base_url -> on préfixe nous-mêmes).
        self._base = settings.sofascore_base_url.rstrip("/")
        _headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
        }
        # PROD : curl_cffi imite l'empreinte TLS de Chrome (JA3) -> contourne le 403 Cloudflare
        # (le vrai motif des « Source en pause »). TESTS : httpx, pour que respx intercepte.
        if "pytest" in sys.modules:
            self._client = httpx.AsyncClient(timeout=settings.http_timeout, headers=_headers)
        else:
            self._client = AsyncSession(impersonate=sofa_http.IMPERSONATE,
                                        timeout=settings.http_timeout, headers=_headers)
        self._fail_count = 0
        self._open_until = 0.0  # horloge monotone : circuit ouvert jusqu'à cet instant

    async def aclose(self) -> None:
        # httpx -> aclose() ; curl_cffi -> close()
        closer = getattr(self._client, "aclose", None) or self._client.close
        await closer()

    # --------------------------------------------------------- disjoncteur
    def _breaker_guard(self) -> None:
        """Lève une erreur immédiate si le circuit est ouvert (sans toucher au réseau)."""
        remaining = self._open_until - time.monotonic()
        if remaining > 0:
            raise ProviderError(
                f"SofaScore en pause anti-403 ({int(remaining)}s restantes).",
                status_code=503,
            )
        # Pause expirée : on fait redescendre le compteur (le back-off ne s'empile plus
        # vers une pause permanente, même sans fetch réussi entre-temps).
        if self._open_until:
            self._open_until = 0.0
            self._fail_count = max(0, self._fail_count - 1)

    def _breaker_trip(self, light: bool = False) -> None:
        """Ouvre le circuit. `light` = erreur réseau transitoire (pause courte, sans escalade)."""
        now = time.monotonic()
        # Déjà ouvert : les échecs CONCURRENTS d'une même rafale (ex. 3 votes en vol qui
        # prennent un 403 ensemble) ne doivent compter que pour un -> pas de ré-escalade.
        if self._open_until > now:
            return
        if light:
            self._open_until = now + BREAKER_LIGHT_S
            return
        self._fail_count += 1
        delay = min(BREAKER_BASE_S * (2 ** (self._fail_count - 1)), BREAKER_MAX_S)
        self._open_until = now + delay
        log.warning("SofaScore rate-limit : circuit ouvert %ss (échec #%s)", delay, self._fail_count)

    def _breaker_reset(self) -> None:
        if self._fail_count:
            log.info("SofaScore rétabli : circuit refermé.")
        self._fail_count = 0
        self._open_until = 0.0

    def breaker_status(self) -> dict:
        """État de la source : circuit ouvert (en pause) ou fermé (OK)."""
        remaining = self._open_until - time.monotonic()
        return {"ok": remaining <= 0, "paused_seconds": max(0, int(remaining))}

    # ----------------------------------------------------------------- réseau
    async def _get(self, path: str) -> dict:
        """GET avec **stale-while-revalidate** : on sert le cache (même périmé)
        instantanément et on rafraîchit en arrière-plan. L'utilisateur n'attend donc
        le réseau qu'au tout premier chargement (cache vide) ; ensuite c'est immédiat.
        """
        fresh = self._cache.get(path)
        if fresh is not None:
            return fresh
        stale = self._cache.get_stale(path)
        if stale is not None:
            # On ne rafraîchit en fond que si le circuit est fermé (sinon on sert le
            # périmé sans gaspiller une tâche/requête vouée au 403) et un seul par chemin.
            if path not in self._refreshing and self._open_until <= time.monotonic():
                self._refreshing.add(path)
                asyncio.create_task(self._background_refresh(path))
            return stale
        return await self._fetch_and_cache(path)    # première fois : fetch bloquant

    async def _background_refresh(self, path: str) -> None:
        try:
            async with self._refresh_sem:           # borne la concurrence des refresh
                await self._fetch_and_cache(path)
        except Exception:
            pass  # le rafraîchissement de fond ne doit jamais faire de bruit
        finally:
            self._refreshing.discard(path)

    async def _fetch_and_cache(self, path: str) -> dict:
        """Appel réseau réel + mise en cache (avec repli sur le périmé en cas d'erreur)."""
        # Circuit SofaScore OUVERT (blocage prolongé) -> on tente RapidAPI directement, sans
        # taper SofaScore. C'est le cas où le repli est le plus utile (le guard lèverait sinon).
        if self._open_until - time.monotonic() > 0:
            rr = await sofa_http._rapid_get(self._base + path, None)
            if rr is not None and rr.status_code == 200:
                data = rr.json()
                self._cache.set(path, data, ttl=_ttl_for(path))
                return data
        try:
            # Le guard est vérifié APRÈS acquisition du sémaphore : si les 1ères requêtes
            # d'une rafale prennent un 403, les suivantes (en file) voient le circuit ouvert
            # et abandonnent sans taper le réseau (au lieu de toutes passer un guard encore fermé).
            async with self._net_sem:           # borne la concurrence réseau (anti-rafale 403)
                self._breaker_guard()
                gap = self._min_gap - (time.monotonic() - self._last_req)
                if gap > 0:                     # espace les requêtes (rate-limit doux)
                    await asyncio.sleep(gap)
                self._last_req = time.monotonic()
                resp = await self._client.get(self._base + path)
            # SofaScore bloqué (403/429) -> repli RapidAPI sur le MÊME chemin, avant le circuit
            if resp.status_code in (403, 429):
                rr = await sofa_http._rapid_get(self._base + path, None)
                if rr is not None and rr.status_code == 200:
                    resp = rr
            if resp.status_code == 404:
                raise ProviderError("Ressource introuvable chez la source.", status_code=404)
            if resp.status_code in (403, 429):  # rate-limit -> ouvre le circuit
                self._breaker_trip()
                raise ProviderError(f"SofaScore a limité l'accès ({resp.status_code}).")
            if resp.status_code >= 400:
                raise ProviderError(f"La source a répondu {resp.status_code} pour {path}")
            data = resp.json()
        except ProviderError as exc:
            if exc.status_code == 404:
                raise
            stale = self._cache.get_stale(path)
            if stale is not None:
                return stale
            raise
        except Exception as exc:
            # Erreur transitoire (timeout, coupure tunnel, JSON invalide, erreur curl_cffi) :
            # ce n'est PAS un rate-limit -> pause courte sans escalade, on sert le périmé si on l'a.
            self._breaker_trip(light=True)
            stale = self._cache.get_stale(path)
            if stale is not None:
                return stale
            raise ProviderError(f"Source de données injoignable: {exc}") from exc

        self._breaker_reset()
        self._cache.set(path, data, ttl=_ttl_for(path))
        return data

    async def _get_bytes(self, path: str) -> tuple[bytes, str]:
        """GET binaire (images). Retourne (contenu, content-type)."""
        self._breaker_guard()
        try:
            resp = await self._client.get(self._base + path)
        except Exception as exc:
            self._breaker_trip()
            raise ProviderError(f"Source de données injoignable: {exc}") from exc
        if resp.status_code == 404:
            raise ProviderError("Ressource introuvable chez la source.", status_code=404)
        if resp.status_code in (403, 429):
            self._breaker_trip()
            raise ProviderError(f"SofaScore a limité l'accès ({resp.status_code}).", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"La source a répondu {resp.status_code} pour {path}", status_code=502)
        self._breaker_reset()
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

    # Catégories SofaScore du circuit principal (on exclut Challenger/ITF/Juniors/UTR).
    _TOUR_CATEGORIES = {"atp": {"ATP"}, "wta": {"WTA", "WTA 125"}}

    @staticmethod
    def _is_singles(ev: dict) -> bool:
        """Exclut le double (équipes à deux joueurs ou libellé 'X / Y')."""
        for side in ("homeTeam", "awayTeam"):
            t = ev.get(side) or {}
            if t.get("subTeams") or "/" in (t.get("name") or ""):
                return False
        return True

    async def get_scheduled_matches(self, tour: str, days: int = 3) -> list[Match]:
        """Tous les matchs du **circuit principal** (ATP/WTA), sur `days` jours.

        Contrairement à get_matches (Roland Garros uniquement), on lit l'agenda tennis
        complet (/sport/tennis/scheduled-events/{date}) et on filtre par catégorie
        (ATP / WTA / WTA 125) et simples. Permet de continuer à suivre après RG : la
        catégorie 'ATP'/'WTA' bascule automatiquement sur les tournois suivants (gazon,
        dur…). Les surfaces sont lues sur chaque match.
        """
        wanted = self._TOUR_CATEGORIES.get(tour, {"ATP"})
        base = datetime.now(timezone.utc).date()
        matches: list[Match] = []
        seen: set[int] = set()
        for d in range(max(1, days)):
            day = (base + timedelta(days=d)).isoformat()
            try:
                data = await self._get(f"/sport/tennis/scheduled-events/{day}")
            except ProviderError:
                continue
            for ev in data.get("events", []) or []:
                cat = ((ev.get("tournament") or {}).get("category") or {}).get("name", "")
                if cat not in wanted or not self._is_singles(ev):
                    continue
                eid = ev.get("id")
                if eid is None or eid in seen:
                    continue
                seen.add(eid)
                matches.append(self._normalize_match(tour, ev))
        matches.sort(key=lambda m: m.start_time or _far_future())
        return matches

    # ----------------------------------------------------------- statistiques
    async def get_statistics(self, match_id: int) -> MatchStatistics:
        data = await self._get(f"/event/{match_id}/statistics")
        return self._normalize_statistics(match_id, data)

    async def get_point_by_point(self, match_id: int) -> MatchPointByPoint:
        """Déroulé point par point d'un match (tennis : /event/{id}/point-by-point)."""
        data = await self._get(f"/event/{match_id}/point-by-point")
        return self._normalize_point_by_point(match_id, data)

    # ------------------------------------------ stats génériques (foot / basket)
    async def get_event_statistics(self, event_id: int) -> MatchStatistics:
        """Stats d'un match, tous sports (possession/tirs/xG en foot, rebonds/3pts en basket).

        Même endpoint et même structure que le tennis : /event/{id}/statistics.
        """
        return await self.get_statistics(event_id)

    async def get_event_incidents(self, event_id: int) -> MatchIncidents:
        """Fil des évènements d'un match de foot (buts, cartons, remplacements, VAR)."""
        data = await self._get(f"/event/{event_id}/incidents")
        incidents: list[MatchIncident] = []
        for inc in data.get("incidents", []) or []:
            itype = inc.get("incidentType")
            player = inc.get("player") or {}
            assist = inc.get("assist1") or inc.get("playerIn") or {}
            incidents.append(MatchIncident(
                type=itype,
                minute=inc.get("time"),
                added_time=inc.get("addedTime"),
                side=_home_side(inc.get("isHome")),
                player=player.get("name") or (inc.get("playerOut") or {}).get("name"),
                assist=assist.get("name") or None,
                detail=inc.get("incidentClass") or inc.get("reason") or inc.get("varDecision"),
                home_score=inc.get("homeScore"),
                away_score=inc.get("awayScore"),
            ))
        # SofaScore renvoie du plus récent au plus ancien -> ordre chronologique
        incidents.reverse()
        return MatchIncidents(match_id=event_id, incidents=incidents)

    async def get_event_lineups(self, event_id: int) -> dict:
        """Compositions d'un match (titulaires, remplaçants, notes). Structure brute SofaScore."""
        return await self._get(f"/event/{event_id}/lineups")

    async def get_event_h2h(self, event_id: int) -> dict:
        """Bilan des confrontations directes (brut) — sport-agnostique (foot/basket)."""
        data = await self._get(f"/event/{event_id}/h2h")
        return data.get("teamDuel") or {}

    async def get_current_season_id(self, tournament_id: int) -> int | None:
        """Identifiant de la saison en cours d'une compétition (foot/basket)."""
        data = await self._get(f"/unique-tournament/{tournament_id}/seasons")
        seasons = data.get("seasons") or []
        return seasons[0].get("id") if seasons else None

    async def get_team_season_statistics(
        self, team_id: int, tournament_id: int, season_id: int
    ) -> TeamSeasonStatistics:
        """Stats agrégées d'une équipe sur une saison (foot/basket) : /team/.../statistics/overall."""
        data = await self._get(
            f"/team/{team_id}/unique-tournament/{tournament_id}"
            f"/season/{season_id}/statistics/overall"
        )
        st = data.get("statistics") or {}
        return TeamSeasonStatistics(
            team_id=team_id, tournament_id=tournament_id, season_id=season_id,
            matches=st.get("matches") or st.get("appearances"),
            statistics={k: _round_pct(v) if isinstance(v, float) else v for k, v in st.items()},
        )

    async def get_team_recent_goals(self, team_id: int, n: int = 12):
        """(buts marqués, buts encaissés, nb matchs) d'une équipe sur ses `n` derniers matchs
        TERMINÉS, toutes compétitions — base du modèle de buts par forme. None si rien d'exploitable.
        Cache stale-while-revalidate du provider (la forme bouge lentement)."""
        data = await self._get(f"/team/{team_id}/events/last/0")
        evs = [e for e in (data or {}).get("events", [])
               if (e.get("status") or {}).get("type") == "finished"
               and (e.get("homeScore") or {}).get("current") is not None
               and (e.get("awayScore") or {}).get("current") is not None]
        evs.sort(key=lambda e: e.get("startTimestamp") or 0, reverse=True)
        gf = ga = cnt = 0
        for e in evs[:n]:
            hs = (e.get("homeScore") or {}).get("current")
            as_ = (e.get("awayScore") or {}).get("current")
            at_home = (e.get("homeTeam") or {}).get("id") == team_id
            gf += hs if at_home else as_
            ga += as_ if at_home else hs
            cnt += 1
        return (gf, ga, cnt) if cnt else None

    async def get_standings(self, tournament_id: int, season_id: int) -> Standings:
        """Classement d'une compétition (foot : pts/V/N/D ; basket : V/D)."""
        data = await self._get(
            f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
        )
        blocks = data.get("standings") or []
        block = blocks[0] if blocks else {}
        rows = [
            StandingRow(
                position=r.get("position"),
                team_id=(r.get("team") or {}).get("id"),
                team=(r.get("team") or {}).get("name", ""),
                played=r.get("matches"),
                wins=r.get("wins"),
                draws=r.get("draws"),
                losses=r.get("losses"),
                scores_for=r.get("scoresFor"),
                scores_against=r.get("scoresAgainst"),
                diff=r.get("scoreDiffFormatted"),
                points=r.get("points"),
            )
            for r in block.get("rows", []) or []
        ]
        return Standings(tournament_id=tournament_id, season_id=season_id,
                         name=block.get("name", ""), rows=rows)

    async def get_top_players(self, tournament_id: int, season_id: int) -> dict:
        """Meilleurs joueurs d'une compétition par catégorie (buts, passes, notes, xG…)."""
        return await self._get(
            f"/unique-tournament/{tournament_id}/season/{season_id}/top-players/overall"
        )

    async def get_top_teams(self, tournament_id: int, season_id: int) -> dict:
        """Meilleures équipes d'une compétition par catégorie (attaque, défense, possession…)."""
        return await self._get(
            f"/unique-tournament/{tournament_id}/season/{season_id}/top-teams/overall"
        )

    async def get_event_best_players(self, event_id: int) -> dict:
        """Meilleurs joueurs d'un match + homme du match (notes SofaScore). Foot."""
        return await self._get(f"/event/{event_id}/best-players/summary")

    async def get_event_incidents_raw(self, event_id: int) -> dict:
        """Incidents bruts d'un match (basket : scores par quart-temps + paniers)."""
        return await self._get(f"/event/{event_id}/incidents")

    async def get_event_pregame_form(self, event_id: int) -> PregameForm:
        """Forme d'avant-match des deux équipes (position, note, 5 derniers résultats)."""
        data = await self._get(f"/event/{event_id}/pregame-form")

        def _form(d: dict | None) -> TeamForm:
            d = d or {}
            return TeamForm(
                avg_rating=_to_float(d.get("avgRating")),
                position=d.get("position"),
                points=_to_int(d.get("value")),
                form=d.get("form") or [],
            )

        return PregameForm(
            match_id=event_id, label=data.get("label"),
            home=_form(data.get("homeTeam")), away=_form(data.get("awayTeam")),
        )

    async def get_event_shotmap(self, event_id: int) -> dict:
        """Carte des tirs d'un match de foot, avec **xG par tir** (brut SofaScore)."""
        return await self._get(f"/event/{event_id}/shotmap")

    async def get_event_win_probability(self, event_id: int) -> dict:
        """Probabilité de victoire dans le temps (modèle live SofaScore). Foot."""
        return await self._get(f"/event/{event_id}/win-probability")

    async def get_event_momentum(self, event_id: int) -> dict:
        """Graphe de momentum / pression du match (foot/basket)."""
        return await self._get(f"/event/{event_id}/graph")

    async def get_team_squad(self, team_id: int) -> dict:
        """Effectif d'une équipe (joueurs + postes). Foot/basket."""
        return await self._get(f"/team/{team_id}/players")

    async def get_player_overview(self, player_id: int) -> dict:
        """Fiche d'un joueur foot/basket (poste, équipe, taille, valeur…). Brut SofaScore."""
        data = await self._get(f"/player/{player_id}")
        return data.get("player") or data

    async def get_player_portrait(self, player_id: int) -> tuple[bytes, str]:
        """Photo d'un joueur foot/basket (/player/{id}/image — distinct du tennis)."""
        return await self._get_bytes(f"/player/{player_id}/image")

    async def get_player_overall_statistics(
        self, player_id: int, tournament_id: int | None = None, season_id: int | None = None
    ) -> dict:
        """Stats d'un joueur foot/basket sur une saison (résout la plus récente par défaut)."""
        seasons = await self._get(f"/player/{player_id}/statistics/seasons")
        uts = seasons.get("uniqueTournamentSeasons") or []
        if not uts:
            raise ProviderError("Aucune statistique disponible pour ce joueur.", status_code=404)
        entry = None
        if tournament_id is not None:
            entry = next((u for u in uts
                          if (u.get("uniqueTournament") or {}).get("id") == tournament_id), None)
        entry = entry or uts[0]
        tid = (entry.get("uniqueTournament") or {}).get("id")
        sids = entry.get("seasons") or []
        sid = season_id or (sids[0].get("id") if sids else None)
        if tid is None or sid is None:
            raise ProviderError("Aucune saison de stats pour ce joueur.", status_code=404)
        data = await self._get(
            f"/player/{player_id}/unique-tournament/{tid}/season/{sid}/statistics/overall"
        )
        st = data.get("statistics") or {}
        return {
            "player_id": player_id, "tournament_id": tid, "season_id": sid,
            "appearances": st.get("appearances") or st.get("matches"),
            "statistics": {k: _round_pct(v) if isinstance(v, float) else v for k, v in st.items()},
        }

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
        return self._votes_from_data(match_id, data)

    def get_votes_cached(self, match_id: int) -> MatchVotes | None:
        """Votes DÉJÀ en cache (fresh ou périmé), SANS appel réseau. None si absent.

        Sert au rendu des pages (accueil) pour ne JAMAIS déclencher de rafale réseau :
        les votes sont peuplés en fond par la boucle de suivi."""
        path = f"/event/{match_id}/votes"
        data = self._cache.get(path) or self._cache.get_stale(path)
        return self._votes_from_data(match_id, data) if data is not None else None

    @staticmethod
    def _votes_from_data(match_id: int, data: dict) -> MatchVotes:
        vote = (data or {}).get("vote") or {}
        v1, vx, v2 = vote.get("vote1"), vote.get("voteX"), vote.get("vote2")
        # On INCLUT le nul (voteX) dans le total -> parts 1/X/2 réelles (foot). Sans nul
        # (tennis/basket), voteX est absent et le total reste home+away.
        total = (v1 or 0) + (vx or 0) + (v2 or 0)
        pct = lambda v: round(100 * v / total, 1) if total and v is not None else None
        return MatchVotes(match_id=match_id, home_votes=v1, away_votes=v2, draw_votes=vx,
                          home_percent=pct(v1), away_percent=pct(v2), draw_percent=pct(vx))

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

    async def get_player_stats_availability(self, player_id: int) -> list[PlayerStatsAvailability]:
        """Tournois/saisons pour lesquels le joueur a des statistiques."""
        data = await self._get(f"/team/{player_id}/team-statistics/seasons")
        out: list[PlayerStatsAvailability] = []
        for ut in data.get("uniqueTournamentSeasons", []) or []:
            tournament = ut.get("uniqueTournament") or {}
            out.append(
                PlayerStatsAvailability(
                    tournament_id=tournament.get("id"),
                    tournament_name=tournament.get("name"),
                    seasons=[
                        TournamentSeason(id=s.get("id"), year=_to_int(s.get("year")), name=s.get("name"))
                        for s in ut.get("seasons", []) or []
                    ],
                )
            )
        return out

    async def get_player_statistics(
        self,
        player_id: int,
        tour: str = "atp",
        season: int | None = None,
        tournament_id: int | None = None,
    ) -> PlayerStatistics:
        """Stats agrégées d'un joueur. Par défaut : Roland Garros, saison la plus récente.

        `tournament_id` permet d'interroger n'importe quel tournoi (pas seulement RG).
        """
        tid = tournament_id or self._tour_id(tour)
        avail = await self.get_player_stats_availability(player_id)
        entry = next((a for a in avail if a.tournament_id == tid), None)
        if entry is None or not entry.seasons:
            raise ProviderError("Aucune statistique disponible pour ce joueur/tournoi.", status_code=404)
        if season is None:
            chosen = entry.seasons[0]
        else:
            chosen = next((s for s in entry.seasons if s.year == season), None)
            if chosen is None:
                raise ProviderError(f"Saison {season} indisponible pour ce joueur/tournoi.", status_code=404)

        data = await self._get(
            f"/team/{player_id}/unique-tournament/{tid}/season/{chosen.id}/statistics/overall"
        )
        return _player_statistics(player_id, tid, chosen, data.get("statistics") or {})

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
                        key=item.get("key"),
                        home_value=_num(item.get("homeValue")),
                        away_value=_num(item.get("awayValue")),
                        home_total=_num(item.get("homeTotal")),
                        away_total=_num(item.get("awayTotal")),
                        compare_code=item.get("compareCode"),
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


def _home_side(is_home) -> str | None:
    """Incidents foot : champ booléen isHome -> 'home' / 'away'."""
    if is_home is True:
        return "home"
    if is_home is False:
        return "away"
    return None


def _num(value) -> float | None:
    """Convertit une valeur numérique SofaScore (int/float) en float, sinon None."""
    return float(value) if isinstance(value, (int, float)) else None


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


def _round_pct(value) -> float | None:
    """Arrondit les pourcentages/moyennes (souvent renvoyés avec 12 décimales)."""
    return round(value, 2) if isinstance(value, (int, float)) else None


def _player_statistics(player_id, tid, season, st: dict) -> "PlayerStatistics":
    from app.models import PlayerStatistics

    return PlayerStatistics(
        player_id=player_id,
        tournament_id=tid,
        season_id=season.id,
        season_year=season.year,
        matches=st.get("matches"),
        wins=st.get("wins"),
        aces=st.get("aces"),
        avg_aces=_round_pct(st.get("avgAces")),
        double_faults=st.get("doubleFaults"),
        avg_double_faults=_round_pct(st.get("avgDoubleFaults")),
        first_serve_percentage=_round_pct(st.get("firstServePercentage")),
        first_serve_points_won_percentage=_round_pct(st.get("firstServePointsWonPercentage")),
        second_serve_percentage=_round_pct(st.get("secondServePercentage")),
        second_serve_points_won_percentage=_round_pct(st.get("secondServePointsWonPercentage")),
        total_serve_attempts=st.get("totalServeAttempts"),
        first_serve_points_scored=st.get("firstServePointsScored"),
        first_serve_points_total=st.get("firstServePointsTotal"),
        second_serve_points_scored=st.get("secondServePointsScored"),
        second_serve_points_total=st.get("secondServePointsTotal"),
        break_points_scored=st.get("breakPointsScored"),
        break_points_total=st.get("breakPointsTotal"),
        break_points_saved_percentage=_round_pct(st.get("breakPointsSavedPercentage")),
        break_points_saved_converted_percentage=_round_pct(st.get("breakPointsSavedConvertedPercentage")),
        opponent_break_points_scored=st.get("opponentBreakPointsScored"),
        opponent_break_points_total=st.get("opponentBreakPointsTotal"),
        winners_total=st.get("winnersTotal"),
        unforced_errors_total=st.get("unforcedErrorsTotal"),
        tiebreaks_won=st.get("tiebreaksWon"),
        tiebreak_losses=st.get("tiebreakLosses"),
        tiebreak_win_percentage=_round_pct(st.get("tiebreakWinPercentage")),
    )


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


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_str(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _far_future():
    from datetime import datetime, timezone

    return datetime(9999, 1, 1, tzinfo=timezone.utc)
