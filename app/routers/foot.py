"""Routeur Foot (Coupe du Monde + grandes compétitions).

- Page HTML : /foot (proba 1X2 Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Football ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, incidents, compositions, h2h,
  stats d'équipe par saison).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import foot
from app.dependencies import get_provider
from app.models import (
    MatchIncidents,
    MatchStatistics,
    TeamSeasonStatistics,
)
from app.providers.sofascore import ProviderError, SofaScoreProvider

router = APIRouter(tags=["Football"])


@router.get("/foot", response_class=HTMLResponse, include_in_schema=False)
async def foot_page() -> HTMLResponse:
    """Matchs des grandes compétitions (dont CdM) : proba 1X2 (Elo) vs cotes Unibet."""
    try:
        rows = await foot.board()
    except Exception:
        rows = []
    try:
        fin = await foot.finished()
    except Exception:
        fin = []
    return HTMLResponse(foot.render(rows, fin))


# ------------------------------------------------------------------- API JSON
@router.get("/foot/board", summary="Matchs à venir + proba 1X2 (Elo) + cotes Unibet + value")
async def foot_board() -> list[dict]:
    """Tableau des grandes compétitions : par match, P(1)/P(X)/P(2), BTTS, cotes et value éventuelle."""
    try:
        return await foot.board()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Données foot indisponibles: {exc}")


@router.get("/foot/finished", summary="Matchs récemment terminés + prédiction du modèle")
async def foot_finished() -> list[dict]:
    """Derniers matchs terminés des grandes compétitions, avec l'issue prédite (Elo)."""
    try:
        return await foot.finished()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Données foot indisponibles: {exc}")


@router.get(
    "/foot/competitions",
    summary="Grandes compétitions suivies (id SofaScore -> nom)",
)
async def foot_competitions() -> dict[int, str]:
    """Liste des compétitions prises en compte (Coupe du Monde + grands championnats + C1/C3)."""
    return foot.MAJOR_TIDS


@router.get(
    "/foot/match/{event_id}/statistics",
    summary="Statistiques d'un match (possession, tirs, xG, passes, duels…)",
    response_model=MatchStatistics,
)
async def foot_statistics(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStatistics:
    try:
        return await provider.get_event_statistics(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/incidents",
    summary="Fil du match : buts, cartons, remplacements, VAR",
    response_model=MatchIncidents,
)
async def foot_incidents(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchIncidents:
    try:
        return await provider.get_event_incidents(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/lineups",
    summary="Compositions d'un match (titulaires, remplaçants, notes)",
)
async def foot_lineups(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_lineups(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/h2h",
    summary="Confrontations directes des deux équipes",
)
async def foot_h2h(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_h2h(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/team/{team_id}/statistics",
    summary="Statistiques d'une équipe sur une compétition (saison courante par défaut)",
    response_model=TeamSeasonStatistics,
)
async def foot_team_statistics(
    team_id: int,
    tournament_id: int = Query(..., description="Id SofaScore de la compétition (ex: 17 = Premier League)"),
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
