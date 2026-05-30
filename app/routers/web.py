"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app import tracking, web
from app.analysis import build_analysis
from app.analysis import _match_winner_odds
from app.dependencies import get_provider, get_unibet
from app.routers.analysis import _gather_context
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(tags=["Plateforme"], include_in_schema=False)

HORIZON_HOURS = 48


@router.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse(web.render_home(tracking.report(tracking.load())))


@router.get("/app", response_class=HTMLResponse)
async def matches_page(
    provider: SofaScoreProvider = Depends(get_provider),
) -> HTMLResponse:
    """Liste des matchs à venir (ATP+WTA), avec badge value depuis le suivi."""
    store = tracking.load()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HORIZON_HOURS)
    groups = []
    for tour, title in (("atp", "ATP — à venir"), ("wta", "WTA — à venir")):
        rows = []
        try:
            matches = await provider.get_matches(tour)
        except ProviderError:
            matches = []
        for m in matches:
            if m.status not in ("notstarted", "inprogress"):
                continue
            if m.start_time and m.start_time > horizon:
                continue
            rec = store.get(str(m.id), {})
            hp = rec.get("model_home_prob")
            if hp is None:
                fav = favp = None
            elif hp >= 0.5:
                fav, favp = m.home.name, f"{round(hp*100)}%"
            else:
                fav, favp = m.away.name, f"{round((1-hp)*100)}%"
            vpick = rec.get("value_pick")
            rows.append({
                "id": m.id, "tour": tour, "home": m.home.name, "away": m.away.name,
                "status": m.status,
                "time": m.start_time.strftime("%d/%m %H:%M") if m.start_time else "",
                "fav": fav, "favp": favp, "confidence": rec.get("confidence"),
                "value": (f'{vpick["player"]} @{vpick["odds"]}' if vpick else None),
            })
        rows.sort(key=lambda r: r["time"])
        groups.append((title, rows))
    return HTMLResponse(web.render_matches(groups))


@router.get("/app/match/{match_id}", response_class=HTMLResponse)
async def match_detail(
    match_id: int,
    tour: str = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> HTMLResponse:
    tour = "wta" if tour == "wta" else "atp"
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError:
        return HTMLResponse(web.layout("Erreur", "matches",
                            '<div class="banner">Match introuvable.</div>'
                            '<a class="dim" href="/app">← Retour</a>'), status_code=404)

    hm, am, hs, as_, h2h, odds = await _gather_context(match, tour, provider, unibet)
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds,
    )
    winner_odds = _match_winner_odds(odds, match) if (odds and odds.matched) else (None, None)
    return HTMLResponse(web.render_match_detail(analysis, winner_odds))
