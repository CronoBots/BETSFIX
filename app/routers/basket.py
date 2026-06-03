"""Routeur Basket (WNBA).

- Page HTML : /basket (proba Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Basketball ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, compositions, h2h, stats d'équipe).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from app import basket, fragcache, sportcache, tracking, web
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

RENDER_NET_BUDGET = 2.5  # s max d'attente réseau au rendu d'une page (sinon -> store)


async def _season(provider: SofaScoreProvider, tournament_id: int, season_id: int | None) -> int:
    sid = season_id or await provider.get_current_season_id(tournament_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="Aucune saison trouvée pour cette compétition.")
    return sid


@router.get("/basket", response_class=HTMLResponse, summary="Page Basket (HTML)")
async def basket_page(frag: int = 0) -> HTMLResponse:
    """Tableau NBA & WNBA : matchs à venir, proba modèle (Elo) vs cotes Unibet, value."""
    rows = await basket.board_resilient()       # MÊME source que l'accueil (cohérence)
    fin = basket.finished_from_store()          # terminés depuis le store (hors-SofaScore)
    return HTMLResponse(basket.render(rows, fin, paused=sportcache.blocked(), frag=bool(frag)))


@router.get("/basket/match/{event_id}", response_class=HTMLResponse,
            summary="Fiche détaillée d'un match basket (prédiction + forme + H2H)")
async def basket_match(event_id: int, frag: int = 0,
                       provider: SofaScoreProvider = Depends(get_provider),
                       unibet: UnibetProvider = Depends(get_unibet)) -> HTMLResponse:
    """Fiche : prédiction (issue du suivi) + analyse SofaScore (forme des 2 équipes, H2H)."""
    if frag:
        cached = fragcache.get(f"basket/{event_id}")
        if cached:
            return HTMLResponse(cached)
    store = tracking.load(basket.BASKET_TRACK_PATH)
    rec = next((r for r in store.values() if str(r.get("match_id")) == str(event_id)), None)
    home = away = ""
    prediction = odds_cells = when = None
    comp = "Basket"
    if rec:
        home, away, comp = rec.get("home", ""), rec.get("away", ""), (rec.get("tour") or "Basket").upper()
        when = web.fmt_local(rec.get("start_time"), with_date=True)
        oh, oa = rec.get("unibet_home_odds"), rec.get("unibet_away_odds")
        odds_cells = [(home, oh), (away, oa)]
        mh = rec.get("model_home_prob")
        if mh is not None:
            votes = ((rec.get("public_home"), rec.get("public_away"))
                     if rec.get("public_home") is not None else None)
            prediction = web.bars_two_way(mh, (basket._devig(oh, oa) or (None, None))[0], votes, home, away)
    forms = h2h = None
    try:
        pf = await provider.get_event_pregame_form(event_id)
        forms = [("", home, pf.home.model_dump()), ("", away, pf.away.model_dump())]
    except ProviderError:
        pass
    try:
        d = await provider.get_event_h2h(event_id)
        h2h = {"home_wins": d.get("homeWins"), "away_wins": d.get("awayWins"), "draws": None}
    except ProviderError:
        pass
    # 🎯 Paris conseillés : value + confiance (favori net ≥65 %)
    extra = ""
    mh = (rec or {}).get("model_home_prob")
    if rec and mh is not None:
        vp = rec.get("value_pick")
        value = (vp["player"], vp["odds"], vp["edge"]) if vp and vp.get("odds") else None
        p_fav = max(mh, 1 - mh)
        confidence = None
        if p_fav >= 0.65:
            fav = home if mh >= 0.5 else away
            confidence = (fav, p_fav, rec.get("unibet_home_odds") if mh >= 0.5 else rec.get("unibet_away_odds"))
        extra = web.recommended_bets(value, confidence)
    # Marge attendue (modèle) : écart de points prévu en faveur du favori
    margin = (rec or {}).get("margin")
    if margin and mh is not None:
        fav = home if mh >= 0.5 else away
        extra += (f'<h2>🏀 Marge attendue</h2>'
                  f'<div class="banner">Le modèle prévoit <b>{fav}</b> vainqueur d\'environ '
                  f'<b>{abs(round(margin))} points</b> (écart moyen estimé).</div>')
    # 💰 TOUS les paris Unibet de l'event (moneyline, handicaps, totaux, quart-temps…)
    try:
        from datetime import datetime
        st = datetime.fromisoformat(rec["start_time"]) if rec and rec.get("start_time") else None
        uo = await unibet.find_event_odds("basketball", home, away, event_id, st)
        if uo.matched:
            extra += web.render_unibet_markets(uo.markets)
    except Exception:
        pass
    # Classement + 5 derniers résultats détaillés (SofaScore, best-effort)
    try:
        from app.routers.foot import team_context
        extra += await team_context(event_id, home, away, unit="points")
    except Exception:
        pass
    ctx = {"home": home or "Match", "away": away, "home_flag": "", "away_flag": "",
           "comp": comp, "when": when, "prediction": prediction, "odds_cells": odds_cells,
           "forms": forms, "h2h": h2h, "extra": extra, "back_url": "/basket",
           "back_label": "Basket", "sport_key": "basket"}
    html = web.render_sport_match_detail(ctx, frag=bool(frag))
    if frag and (forms or h2h or extra):
        fragcache.put(f"basket/{event_id}", html)
    return HTMLResponse(html)


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

# NB : pas d'agrégat saison fiable par joueur en basket chez SofaScore. Les box scores
# (points, rebonds, passes, 3pts…) sont dispos match par match via .../lineups.


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
