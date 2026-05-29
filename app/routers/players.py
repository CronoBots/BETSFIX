"""Endpoints liés aux joueurs (fiche, classements, matchs récents)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_provider
from app.models import Match, PlayerProfile, RankingEntry
from app.providers.sofascore import ProviderError, SofaScoreProvider

router = APIRouter(prefix="/players", tags=["Joueurs"])


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
