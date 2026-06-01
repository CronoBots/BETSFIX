"""Routeur Basket (WNBA).

- Page HTML : /basket (proba Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Basketball ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, compositions, h2h, stats d'équipe).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from app import basket, sportcache
from app.dependencies import get_provider, get_unibet
from app.models import (
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

router = APIRouter(tags=["🏀 Basketball"])


async def _season(provider: SofaScoreProvider, tournament_id: int, season_id: int | None) -> int:
    sid = season_id or await provider.get_current_season_id(tournament_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="Aucune saison trouvée pour cette compétition.")
    return sid


@router.get("/basket", response_class=HTMLResponse, summary="Page Basket (HTML)")
async def basket_page() -> HTMLResponse:
    """Tableau WNBA : matchs à venir, proba modèle (Elo) vs cotes Unibet, value."""
    try:
        rows = await basket.board()
        await basket.enrich_display(rows)   # votes fans + forme (provider caché)
    except Exception:
        rows = []
    try:
        fin = await basket.finished()
    except Exception:
        fin = []
    return HTMLResponse(basket.render(rows, fin, paused=sportcache.blocked()))


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
    "/basket/match/{event_id}/incidents",
    summary="Scores par quart-temps + déroulé du match (brut SofaScore)",
)
async def basket_incidents(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_incidents_raw(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/pregame-form",
    summary="Forme d'avant-match : position, note, 5 derniers résultats des 2 équipes",
    response_model=PregameForm,
)
async def basket_pregame_form(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> PregameForm:
    try:
        return await provider.get_event_pregame_form(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/momentum",
    summary="Graphe de momentum / pression du match",
)
async def basket_momentum(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_momentum(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/odds",
    summary="Cotes SofaScore d'un match (cross-check du marché)",
    response_model=MatchOdds,
)
async def basket_odds(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchOdds:
    try:
        return await provider.get_odds(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/odds/unibet",
    summary="Cotes Unibet Belgique (tous les marchés) d'un match",
    response_model=UnibetOdds,
)
async def basket_odds_unibet(
    event_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> UnibetOdds:
    """Cotes Unibet Belgique (Kambi) pour un match de basket, tous marchés confondus.
    Matché par noms d'équipes + date. Disponible pour les matchs à venir / en cours."""
    try:
        m = await provider.get_match("basketball", event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return await unibet.find_event_odds(
        "basketball", m.home.name, m.away.name, event_id, m.start_time)


@router.get(
    "/basket/match/{event_id}/votes",
    summary="Pronostics des fans",
    response_model=MatchVotes,
)
async def basket_votes(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchVotes:
    try:
        return await provider.get_votes(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/streaks",
    summary="Séries en cours des deux équipes",
    response_model=MatchStreaks,
)
async def basket_streaks(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStreaks:
    try:
        return await provider.get_streaks(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/competition/{tournament_id}/standings",
    summary="Classement d'une compétition (V/D, position)",
    response_model=Standings,
)
async def basket_standings(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> Standings:
    try:
        return await provider.get_standings(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/competition/{tournament_id}/top-players",
    summary="Meilleurs joueurs (points, rebonds, passes…) par catégorie",
)
async def basket_top_players(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_players(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/competition/{tournament_id}/top-teams",
    summary="Meilleures équipes (attaque, défense…) par catégorie",
)
async def basket_top_teams(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_teams(tournament_id, await _season(provider, tournament_id, season_id))
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
        return await provider.get_team_season_statistics(
            team_id, tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/team/{team_id}/squad",
    summary="Effectif d'une équipe (joueurs + postes)",
)
async def basket_squad(
    team_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_team_squad(team_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/player/{player_id}",
    summary="Fiche d'un joueur (poste, équipe, taille…)",
)
async def basket_player(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_player_overview(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


    # Stats par joueur en basket : SofaScore n'expose pas d'agrégat saison fiable.
    # Les box scores par joueur (points, rebonds, passes, 3pts…) sont disponibles
    # match par match via /basket/match/{event_id}/lineups.


@router.get(
    "/basket/player/{player_id}/image",
    summary="Photo d'un joueur",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
async def basket_player_image(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> Response:
    try:
        content, ctype = await provider.get_player_portrait(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return Response(content=content, media_type=ctype)
