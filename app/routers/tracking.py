"""Suivi des prédictions vs résultats (CLV / ROI) — endpoints + jobs."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app import tracking
from app.analysis import build_analysis
from app.dependencies import get_provider, get_unibet
from app.routers.analysis import _gather_context
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(prefix="/tracking", tags=["Suivi / Performance"])

HORIZON_HOURS = 48  # on ne logge que les matchs à venir dans cette fenêtre


def _now():
    return datetime.now(timezone.utc)


async def run_snapshot(provider: SofaScoreProvider, unibet: UnibetProvider) -> int:
    """Logge prédictions + cotes Unibet des matchs à venir (≈ cote de clôture)."""
    store = tracking.load()
    now = _now()
    horizon = now + timedelta(hours=HORIZON_HOURS)
    updated = 0
    for tour in ("atp", "wta"):
        try:
            matches = await provider.get_matches(tour)
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
            analysis = build_analysis(
                match=m, home_matches=hm or [], away_matches=am or [],
                home_stats=hs, away_stats=as_,
                home_wins_h2h=h2h.home_wins if h2h else None,
                away_wins_h2h=h2h.away_wins if h2h else None,
                unibet=odds,
            )
            if tracking.upsert_prediction(store, analysis, tour, now.isoformat()):
                updated += 1
    tracking.save(store)
    return updated


async def run_settle(provider: SofaScoreProvider) -> int:
    """Renseigne le résultat réel des matchs suivis désormais terminés."""
    store = tracking.load()
    now = _now()
    settled = 0
    for rec in list(store.values()):
        if rec.get("result"):
            continue
        try:
            m = await provider.get_match(rec.get("tour", "atp"), rec["match_id"])
        except ProviderError:
            continue
        if m.status != "finished" or m.winner not in ("home", "away"):
            continue
        total = sum(g for g in (m.home_score.sets or []) if g is not None) + \
            sum(g for g in (m.away_score.sets or []) if g is not None)
        if tracking.settle(store, m.id, m.winner, total or None, now.isoformat()):
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
async def dashboard() -> HTMLResponse:
    store = tracking.load()
    return HTMLResponse(tracking.render_dashboard(store, tracking.report(store)))


@router.get("/log", summary="Détail des prédictions suivies")
async def get_log() -> list[dict]:
    store = tracking.load()
    return sorted(store.values(), key=lambda r: r.get("first_logged", ""), reverse=True)
