"""Routeur Basket (WNBA).

- Page HTML : /basket (proba Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Basketball ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, compositions, h2h, stats d'équipe).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import basket
from app.dependencies import get_provider
from app.models import MatchStatistics, TeamSeasonStatistics
from app.providers.sofascore import ProviderError, SofaScoreProvider

router = APIRouter(tags=["Basketball"])


@router.get("/basket", response_class=HTMLResponse, include_in_schema=False)
async def basket_page() -> HTMLResponse:
    """Tableau WNBA : matchs à venir, proba modèle (Elo) vs cotes Unibet, value."""
    try:
        rows = await basket.board()
    except Exception:
        rows = []
    try:
        fin = await basket.finished()
    except Exception:
        fin = []
    return HTMLResponse(basket.render(rows, fin))


# ------------------------------------------------------------------- API JSON
@router.get("/basket/board", summary="Matchs WNBA à venir + proba (Elo) + marge attendue + cotes + value")
async def basket_board() -> list[dict]:
    """Tableau WNBA : par match, proba de victoire, marge attendue (points), cotes et value éventuelle."""
    try:
        return await basket.board()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Données basket indisponibles: {exc}")


@router.get("/basket/finished", summary="Matchs WNBA récemment terminés + prédiction du modèle")
async def basket_finished() -> list[dict]:
    """Derniers matchs WNBA terminés, avec le favori du modèle (Elo)."""
    try:
        return await basket.finished()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Données basket indisponibles: {exc}")


@router.get(
    "/basket/match/{event_id}/statistics",
    summary="Statistiques d'un match (points, rebonds, 3pts, lancers, turnovers…)",
    response_model=MatchStatistics,
)
async def basket_statistics(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStatistics:
    try:
        return await provider.get_event_statistics(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/lineups",
    summary="Effectifs / cinq de départ d'un match",
)
async def basket_lineups(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_lineups(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/h2h",
    summary="Confrontations directes des deux équipes",
)
async def basket_h2h(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_h2h(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/team/{team_id}/statistics",
    summary="Statistiques d'une équipe WNBA sur la saison",
    response_model=TeamSeasonStatistics,
)
async def basket_team_statistics(
    team_id: int,
    tournament_id: int = Query(basket.WNBA_TID, description="Id SofaScore de la compétition (486 = WNBA)"),
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> TeamSeasonStatistics:
    try:
        sid = season_id or await provider.get_current_season_id(tournament_id)
        if sid is None:
            raise HTTPException(status_code=404, detail="Aucune saison trouvée pour cette compétition.")
        return await provider.get_team_season_statistics(team_id, tournament_id, sid)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
