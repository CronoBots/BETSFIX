"""Endpoints liés aux matchs de Roland Garros."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_provider, get_unibet
from app.models import (
    HeadToHead,
    Match,
    MatchOdds,
    MatchPointByPoint,
    MatchStreaks,
    MatchVotes,
    TournamentInfo,
    TournamentSeason,
    UnibetOdds,
)
from app.providers.sofascore import ProviderError, SofaScoreProvider, round_matches
from app.providers.unibet import UnibetProvider

router = APIRouter(prefix="/matches", tags=["Matchs"])

Tour = Literal["atp", "wta"]


@router.get("", summary="Tous les matchs de Roland Garros", response_model=list[Match])
async def list_matches(
    tour: Tour = Query("atp", description="atp (hommes) ou wta (femmes)"),
    season: int | None = Query(
        None, description="Année de l'édition (ex: 2024). Par défaut : édition la plus récente."
    ),
    round: str | None = Query(
        None,
        description="Filtrer par round, FR ou EN (ex: 'Finale'/'Final', '1er tour'/'Round of 128').",
    ),
    status: str | None = Query(
        None, description="Filtrer par statut: notstarted / inprogress / finished."
    ),
    player: str | None = Query(None, description="Filtrer par nom de joueur (recherche partielle)."),
    provider: SofaScoreProvider = Depends(get_provider),
) -> list[Match]:
    try:
        matches = await provider.get_matches(tour, season)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    if round:
        matches = [m for m in matches if round_matches(m, round)]
    if status:
        matches = [m for m in matches if (m.status or "").lower() == status.lower()]
    if player:
        p = player.lower()
        matches = [
            m for m in matches if p in m.home.name.lower() or p in m.away.name.lower()
        ]
    return matches


@router.get("/tournament", summary="Infos sur l'édition courante", response_model=TournamentInfo)
async def tournament_info(
    tour: Tour = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> TournamentInfo:
    try:
        return await provider.get_tournament_info(tour)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/round/{round}",
    summary="Matchs d'un round donné",
    response_model=list[Match],
)
async def matches_by_round(
    round: str,
    tour: Tour = Query("atp"),
    season: int | None = Query(None, description="Année de l'édition (par défaut : la plus récente)."),
    provider: SofaScoreProvider = Depends(get_provider),
) -> list[Match]:
    """Confort : matchs d'un round (FR ou EN). NB : SofaScore n'expose pas de route
    'events/round' pour le tennis, le filtrage est donc fait côté API."""
    try:
        matches = await provider.get_matches(tour, season)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return [m for m in matches if round_matches(m, round)]


@router.get(
    "/{match_id}/point-by-point",
    summary="Déroulé point par point d'un match",
    response_model=MatchPointByPoint,
)
async def match_point_by_point(
    match_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> MatchPointByPoint:
    try:
        return await provider.get_point_by_point(match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/seasons",
    summary="Éditions disponibles du tournoi",
    response_model=list[TournamentSeason],
)
async def list_seasons(
    tour: Tour = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> list[TournamentSeason]:
    try:
        return await provider.get_seasons(tour)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{match_id}/odds",
    summary="Cotes (paris) d'un match",
    response_model=MatchOdds,
)
async def match_odds(
    match_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> MatchOdds:
    try:
        return await provider.get_odds(match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{match_id}/odds/unibet",
    summary="Cotes Unibet Belgique (matchées sur l'événement)",
    response_model=UnibetOdds,
)
async def match_odds_unibet(
    match_id: int,
    tour: Tour = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> UnibetOdds:
    """Cotes Unibet Belgique (Kambi) pour un match. Disponible pour les matchs
    à venir / en cours uniquement."""
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return await unibet.find_odds(match)


@router.get(
    "/{match_id}/h2h",
    summary="Confrontations directes (head-to-head)",
    response_model=HeadToHead,
)
async def match_h2h(
    match_id: int,
    tour: Tour = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> HeadToHead:
    try:
        return await provider.get_head_to_head(tour, match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{match_id}/votes",
    summary="Pronostics des fans",
    response_model=MatchVotes,
)
async def match_votes(
    match_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> MatchVotes:
    try:
        return await provider.get_votes(match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{match_id}/streaks",
    summary="Séries et records autour du match",
    response_model=MatchStreaks,
)
async def match_streaks(
    match_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> MatchStreaks:
    try:
        return await provider.get_streaks(match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/{match_id}", summary="Détail d'un match", response_model=Match)
async def get_match(
    match_id: int,
    tour: Tour = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> Match:
    try:
        return await provider.get_match(tour, match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
