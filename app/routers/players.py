"""Endpoints liés aux joueurs (fiche, classements, matchs récents)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.dependencies import get_provider
from app.models import (
    Match,
    PlayerProfile,
    PlayerStatistics,
    PlayerStatsAvailability,
    RankingEntry,
)
from app.providers.sofascore import ProviderError, SofaScoreProvider

router = APIRouter(prefix="/players", tags=["Joueurs"])

Tour = Literal["atp", "wta"]


@router.get(
    "/{player_id}",
    summary="Fiche détaillée d'un joueur",
    response_model=PlayerProfile,
)
async def player_profile(
    player_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> PlayerProfile:
    try:
        return await provider.get_player(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{player_id}/statistics/available",
    summary="Tournois/saisons avec stats disponibles pour le joueur",
    response_model=list[PlayerStatsAvailability],
)
async def player_stats_available(
    player_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> list[PlayerStatsAvailability]:
    try:
        return await provider.get_player_stats_availability(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{player_id}/statistics",
    summary="Statistiques agrégées d'un joueur (analyse de forme)",
    response_model=PlayerStatistics,
)
async def player_statistics(
    player_id: int,
    tour: Tour = Query("atp", description="atp / wta (choisit le tournoi Roland Garros par défaut)"),
    season: int | None = Query(None, description="Année (par défaut : la plus récente avec stats)."),
    tournament_id: int | None = Query(
        None, description="ID SofaScore d'un autre tournoi (sinon Roland Garros)."
    ),
    provider: SofaScoreProvider = Depends(get_provider),
) -> PlayerStatistics:
    try:
        return await provider.get_player_statistics(player_id, tour, season, tournament_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{player_id}/image",
    summary="Photo d'un joueur",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
async def player_image(
    player_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> Response:
    try:
        content, content_type = await provider.get_player_image(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return Response(content=content, media_type=content_type)


@router.get(
    "/{player_id}/rankings",
    summary="Classements d'un joueur (ATP/WTA, Live, UTR…)",
    response_model=list[RankingEntry],
)
async def player_rankings(
    player_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> list[RankingEntry]:
    try:
        return await provider.get_player_rankings(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/{player_id}/matches",
    summary="Matchs récents d'un joueur (toutes compétitions)",
    response_model=list[Match],
)
async def player_matches(
    player_id: int,
    pages: int = Query(2, ge=1, le=10, description="Nombre de pages de résultats à agréger."),
    provider: SofaScoreProvider = Depends(get_provider),
) -> list[Match]:
    try:
        return await provider.get_player_matches(player_id, pages)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
