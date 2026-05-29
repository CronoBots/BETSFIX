"""Endpoint d'aide à la décision de pari : analyse pré-match complète."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analysis import build_analysis
from app.dependencies import get_provider, get_unibet
from app.markets import (
    DEFAULT_SERVE,
    calibrate_and_simulate,
    evaluate_markets,
    serve_win_pct,
)
from app.models import MatchAnalysis, MatchMarketsAnalysis
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(prefix="/analysis", tags=["Analyse / Paris"])

Tour = Literal["atp", "wta"]


async def _noop():
    return None


async def _gather_context(match, tour, provider, unibet):
    """Récupère en parallèle : forme, stats, h2h, cotes Unibet."""
    import asyncio

    async def _safe(coro):
        try:
            return await coro
        except ProviderError:
            return None

    hid, aid = match.home.id, match.away.id
    return await asyncio.gather(
        _safe(provider.get_player_matches(hid)) if hid else _noop(),
        _safe(provider.get_player_matches(aid)) if aid else _noop(),
        _safe(provider.get_player_statistics(hid, tour)) if hid else _noop(),
        _safe(provider.get_player_statistics(aid, tour)) if aid else _noop(),
        _safe(provider.get_head_to_head(tour, match.id)),
        unibet.find_odds(match),
    )


@router.get(
    "/{match_id}",
    summary="Analyse pré-match + détection de value (cotes Unibet)",
    response_model=MatchAnalysis,
)
async def analyze_match(
    match_id: int,
    tour: Tour = Query("atp", description="atp / wta"),
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> MatchAnalysis:
    """Combine classement, forme récente, stats de surface et head-to-head en une
    probabilité de victoire, puis la confronte aux cotes Unibet Belgique pour
    repérer la *value* et proposer une mise (Kelly fractionné)."""
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    hm, am, hs, as_, h2h, odds = await _gather_context(match, tour, provider, unibet)
    return build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds,
    )


@router.get(
    "/{match_id}/markets",
    summary="Value sur TOUS les marchés Unibet (jeux, sets, tie-breaks, handicaps…)",
    response_model=MatchMarketsAnalysis,
)
async def analyze_markets(
    match_id: int,
    tour: Tour = Query("atp", description="atp / wta"),
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> MatchMarketsAnalysis:
    """Simule le déroulé du match (à partir des stats de service, calibré sur la
    proba de vainqueur du modèle) et compare CHAQUE marché Unibet à sa probabilité
    estimée pour détecter la value au-delà du simple vainqueur."""
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    hm, am, hs, as_, h2h, odds = await _gather_context(match, tour, provider, unibet)
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds,
    )
    result = MatchMarketsAnalysis(
        match_id=match_id, home=match.home, away=match.away,
        best_of=5 if tour == "atp" else 3,
        model_home_probability=analysis.model_home_probability,
        unibet_matched=bool(odds and odds.matched),
    )
    if not (odds and odds.matched):
        result.note = "Cotes Unibet indisponibles (match non à l'affiche)."
        return result

    # Niveau de service (pilote le nombre de jeux/tie-breaks) depuis les stats
    levels = [v for v in (serve_win_pct(hs), serve_win_pct(as_)) if v is not None]
    serve_level = sum(levels) / len(levels) if levels else DEFAULT_SERVE[tour]
    p_home = analysis.model_home_probability or 0.5

    sim = calibrate_and_simulate(p_home, serve_level, result.best_of, seed=match_id)
    edges = evaluate_markets(match, odds, sim)
    result.all_markets = edges
    result.markets_evaluated = len(edges)
    result.value_bets = sorted([e for e in edges if e.is_value],
                               key=lambda e: e.edge or 0, reverse=True)
    result.note = (
        f"{result.markets_evaluated} sélections évaluées, "
        f"{len(result.value_bets)} value détectée(s). Best-of-{result.best_of}."
    )
    return result
