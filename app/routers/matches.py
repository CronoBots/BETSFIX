"""Endpoints liés aux matchs de Roland Garros."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_provider
from app.models import Match, TournamentInfo
from app.providers.sofascore import ProviderError, SofaScoreProvider

router = APIRouter(prefix="/matches", tags=["Matchs"])

Tour = Literal["atp", "wta"]


@router.get("", summary="Tous les matchs de Roland Garros", response_model=list[Match])
async def list_matches(
    tour: Tour = Query("atp", description="atp (hommes) ou wta (femmes)"),
    season: int | None = Query(
        None, description="Année de l'édition (ex: 2024). Par défaut : édition la plus récente."
    ),
    round: str | None = Query(None, description="Filtrer par round (ex: 'Finale', '1er tour')."),
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
        matches = [m for m in matches if m.round and round.lower() in m.round.lower()]
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
