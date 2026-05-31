"""Suivi des prédictions vs résultats (CLV / ROI) — endpoints + jobs."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app import elo, serve_return, tracking
from app.config import get_settings
from app.analysis import build_analysis
from app.dependencies import get_livescore, get_provider, get_unibet
from app.routers.analysis import _gather_context
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(prefix="/tracking", tags=["📊 Suivi & performance"])

HORIZON_HOURS = 48  # on ne logge que les matchs à venir dans cette fenêtre


def _now():
    return datetime.now(timezone.utc)


async def run_snapshot(provider: SofaScoreProvider, unibet: UnibetProvider) -> int:
    """Logge prédictions + cotes Unibet des matchs à venir (≈ cote de clôture)."""
    store = tracking.load()
    now = _now()
    horizon = now + timedelta(hours=HORIZON_HOURS)
    updated = 0
    full_tour = get_settings().track_full_tour
    for tour in ("atp", "wta"):
        try:
            matches = (await provider.get_scheduled_matches(tour) if full_tour
                       else await provider.get_matches(tour))
        except ProviderError:
            continue
        for m in matches:
            if m.status != "notstarted" or m.start_time is None:
                continue
            if not (now <= m.start_time <= horizon):
                continue
            try:
                hm, am, hs, as_, h2h, odds = await _gather_context(m, tour, provider, unibet)
            except ProviderError:
                continue
            if not (odds and odds.matched):
                continue  # pas de cote Unibet -> rien à suivre
            elo_home, elo_away = elo.ratings_for_match(m)
            sr_home, sr_away = serve_return.ratings_for_match(m)
            analysis = build_analysis(
                match=m, home_matches=hm or [], away_matches=am or [],
                home_stats=hs, away_stats=as_,
                home_wins_h2h=h2h.home_wins if h2h else None,
                away_wins_h2h=h2h.away_wins if h2h else None,
                unibet=odds, elo_home=elo_home, elo_away=elo_away,
                sr_home=sr_home, sr_away=sr_away,
            )
            st_iso = m.start_time.isoformat() if m.start_time else None
            if tracking.upsert_prediction(store, analysis, tour, now.isoformat(), st_iso):
                updated += 1
    tracking.save(store)
    return updated


async def run_settle(provider: SofaScoreProvider) -> int:
    """Renseigne le résultat réel des matchs suivis désormais terminés."""
    store = tracking.load()
    now = _now()
    livescore = get_livescore()
    settled = 0
    for rec in list(store.values()):
        if rec.get("result"):
            continue
        winner = total = None
        try:
            m = await provider.get_match(rec.get("tour", "atp"), rec["match_id"])
            if m.status == "finished" and m.winner in ("home", "away"):
                winner = m.winner
                total = (sum(g for g in (m.home_score.sets or []) if g is not None) +
                         sum(g for g in (m.away_score.sets or []) if g is not None)) or None
        except ProviderError:
            # Repli LiveScore : on règle au moins le vainqueur (par noms + date)
            try:
                winner = await livescore.find_result(
                    rec.get("tour", "atp"), rec.get("home", ""), rec.get("away", ""),
                    rec.get("start_time"))
            except Exception:
                winner = None
        if winner and tracking.settle(store, rec["match_id"], winner, total, now.isoformat()):
            settled += 1
    tracking.save(store)
    return settled


@router.post("/snapshot", summary="Logge prédictions + cotes des matchs à venir")
async def snapshot(
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> dict:
    n = await run_snapshot(provider, unibet)
    return {"predictions_loggees_ou_maj": n}


@router.post("/settle", summary="Renseigne les résultats des matchs terminés")
async def settle_endpoint(
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    n = await run_settle(provider)
    return {"matchs_regles": n}


@router.get("/report", summary="Performance du modèle (calibration, ROI des value)")
async def get_report() -> dict:
    return tracking.report(tracking.load())


@router.get("/dashboard", response_class=HTMLResponse,
            summary="Tableau de bord lisible (mobile)")
async def dashboard(sport: str = Query("tennis")) -> HTMLResponse:
    sport = sport if sport in ("tennis", "basket", "foot") else "tennis"
    if sport == "foot":
        from app import foot
        store = tracking.load(foot.FOOT_TRACK_PATH)
        return HTMLResponse(foot.render_dashboard(store, foot.report(store)))
    if sport == "basket":
        from app.basket import BASKET_TRACK_PATH
        store = tracking.load(BASKET_TRACK_PATH)
    else:
        store = tracking.load()
    return HTMLResponse(tracking.render_dashboard(store, tracking.report(store), sport=sport))


@router.get("/today", response_class=HTMLResponse,
            summary="Matchs à venir analysés (mobile)")
async def today() -> HTMLResponse:
    return HTMLResponse(tracking.render_today(tracking.load()))


@router.get("/log", summary="Détail des prédictions suivies")
async def get_log() -> list[dict]:
    store = tracking.load()
    return sorted(store.values(), key=lambda r: r.get("first_logged", ""), reverse=True)
