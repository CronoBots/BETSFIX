"""Endpoints liés aux statistiques des matchs."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_provider
from app.models import MatchStatistics
from app.providers.sofascore import ProviderError, SofaScoreProvider

router = APIRouter(prefix="/statistics", tags=["🎾 Tennis · Statistiques"])

Tour = Literal["atp", "wta"]


@router.get(
    "/{match_id}",
    summary="Statistiques détaillées d'un match",
    response_model=MatchStatistics,
)
async def match_statistics(
    match_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
) -> MatchStatistics:
    try:
        return await provider.get_statistics(match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "",
    summary="Statistiques de TOUS les matchs terminés",
    response_model=dict[int, MatchStatistics],
)
async def all_statistics(
    tour: Tour = Query("atp", description="atp (hommes) ou wta (femmes)"),
    season: int | None = Query(None, description="Année de l'édition (par défaut : la plus récente)."),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict[int, MatchStatistics]:
    try:
        return await provider.get_all_statistics(tour, season)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
