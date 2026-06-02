"""Routeur Foot (Coupe du Monde + grandes compétitions).

- Page HTML : /foot (proba 1X2 Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Football ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, incidents, compositions, h2h,
  stats d'équipe par saison).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from app import foot, sportcache
from app.dependencies import get_provider, get_unibet
from app.models import (
    MatchIncidents,
    MatchOdds,
    MatchStatistics,
    MatchStreaks,
    MatchVotes,
    PregameForm,
    Standings,
    TeamSeasonStatistics,
    UnibetOdds,
)
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(tags=["⚽ Football"])

RENDER_NET_BUDGET = 2.5  # s max d'attente réseau au rendu d'une page (sinon -> store)


async def _season(provider: SofaScoreProvider, tournament_id: int, season_id: int | None) -> int:
    sid = season_id or await provider.get_current_season_id(tournament_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="Aucune saison trouvée pour cette compétition.")
    return sid


@router.get("/foot", response_class=HTMLResponse, summary="Page Football (HTML)")
async def foot_page() -> HTMLResponse:
    """Matchs des grandes compétitions (dont CdM) : proba 1X2 (Elo) vs cotes Unibet."""
    # Budget réseau borné : si SofaScore traîne, on n'attend pas -> on sert le store.
    rows, fin = [], []
    try:
        rows = await asyncio.wait_for(foot.board(), timeout=RENDER_NET_BUDGET)
        if rows:
            await asyncio.wait_for(foot.enrich_display(rows), timeout=2.0)
    except (Exception, asyncio.TimeoutError):
        rows = []
    if not rows:                              # SofaScore lent/en pause -> board via UNIBET
        try:                                  # (matchs + cotes Unibet + Elo, sans SofaScore)
            rows = await asyncio.wait_for(foot.board_from_unibet(), timeout=RENDER_NET_BUDGET)
        except (Exception, asyncio.TimeoutError):
            rows = []
    if not rows:                              # dernier repli : le suivi persisté
        rows = foot.board_from_store()
    try:
        fin = await asyncio.wait_for(foot.finished(), timeout=2.0)
    except (Exception, asyncio.TimeoutError):
        fin = []
    return HTMLResponse(foot.render(rows, fin, paused=sportcache.blocked()))


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
    "/foot/match/{event_id}/pregame-form",
    summary="Forme d'avant-match : position, note, 5 derniers résultats des 2 équipes",
    response_model=PregameForm,
)
async def foot_pregame_form(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> PregameForm:
    try:
        return await provider.get_event_pregame_form(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/shotmap",
    summary="Carte des tirs avec xG par tir",
)
async def foot_shotmap(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_shotmap(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/win-probability",
    summary="Probabilité de victoire dans le temps (modèle live SofaScore)",
)
async def foot_win_probability(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_win_probability(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/momentum",
    summary="Graphe de momentum / pression du match",
)
async def foot_momentum(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_momentum(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/best-players",
    summary="Notes des joueurs + homme du match",
)
async def foot_best_players(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_best_players(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/odds",
    summary="Cotes SofaScore d'un match (cross-check du marché)",
    response_model=MatchOdds,
)
async def foot_odds(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchOdds:
    try:
        return await provider.get_odds(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/odds/unibet",
    summary="Cotes Unibet Belgique (tous les marchés) d'un match",
    response_model=UnibetOdds,
)
async def foot_odds_unibet(
    event_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> UnibetOdds:
    """Cotes Unibet Belgique (Kambi) pour un match de foot, tous marchés confondus
    (1X2, double chance, BTTS, totaux, handicaps…). Matché par noms d'équipes + date.
    Disponible pour les matchs à venir / en cours."""
    try:
        m = await provider.get_match("football", event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return await unibet.find_event_odds(
        "football", m.home.name, m.away.name, event_id, m.start_time)


@router.get(
    "/foot/match/{event_id}/votes",
    summary="Pronostics des fans (1-X-2)",
    response_model=MatchVotes,
)
async def foot_votes(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchVotes:
    try:
        return await provider.get_votes(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/streaks",
    summary="Séries en cours des deux équipes",
    response_model=MatchStreaks,
)
async def foot_streaks(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStreaks:
    try:
        return await provider.get_streaks(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/competition/{tournament_id}/standings",
    summary="Classement d'une compétition (forme, position, points)",
    response_model=Standings,
)
async def foot_standings(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> Standings:
    try:
        return await provider.get_standings(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/competition/{tournament_id}/top-players",
    summary="Meilleurs joueurs (buts, passes, notes, xG…) par catégorie",
)
async def foot_top_players(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_players(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/competition/{tournament_id}/top-teams",
    summary="Meilleures équipes (attaque, défense, possession…) par catégorie",
)
async def foot_top_teams(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_teams(tournament_id, await _season(provider, tournament_id, season_id))
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
        return await provider.get_team_season_statistics(
            team_id, tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/team/{team_id}/squad",
    summary="Effectif d'une équipe (joueurs + postes)",
)
async def foot_squad(
    team_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_team_squad(team_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/player/{player_id}",
    summary="Fiche d'un joueur (poste, équipe, taille, valeur…)",
)
async def foot_player(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_player_overview(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/player/{player_id}/statistics",
    summary="Statistiques d'un joueur sur une saison (buts, passes, xG, duels…)",
)
async def foot_player_statistics(
    player_id: int,
    tournament_id: int | None = Query(None, description="Compétition (par défaut : la plus récente avec stats)"),
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_player_overall_statistics(player_id, tournament_id, season_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/player/{player_id}/image",
    summary="Photo d'un joueur",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
async def foot_player_image(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> Response:
    try:
        content, ctype = await provider.get_player_portrait(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return Response(content=content, media_type=ctype)
