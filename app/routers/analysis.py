"""Endpoint d'aide à la décision de pari : analyse pré-match complète."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analysis import build_analysis
from app.dependencies import get_provider, get_unibet
from app.models import MatchAnalysis
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(prefix="/analysis", tags=["Analyse / Paris"])

Tour = Literal["atp", "wta"]


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

    async def _safe(coro):
        try:
            return await coro
        except ProviderError:
            return None

    home_id, away_id = match.home.id, match.away.id
    (
        home_matches,
        away_matches,
        home_stats,
        away_stats,
        h2h,
        unibet_odds,
    ) = await asyncio.gather(
        _safe(provider.get_player_matches(home_id)) if home_id else _noop(),
        _safe(provider.get_player_matches(away_id)) if away_id else _noop(),
        _safe(provider.get_player_statistics(home_id, tour)) if home_id else _noop(),
        _safe(provider.get_player_statistics(away_id, tour)) if away_id else _noop(),
        _safe(provider.get_head_to_head(tour, match_id)),
        unibet.find_odds(match),
    )

    return build_analysis(
        match=match,
        home_matches=home_matches or [],
        away_matches=away_matches or [],
        home_stats=home_stats,
        away_stats=away_stats,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=unibet_odds,
    )


async def _noop():
    return None
