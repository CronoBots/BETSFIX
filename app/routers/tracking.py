"""Suivi des prédictions vs résultats (CLV / ROI) — endpoints + jobs."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app import elo, serve_return, tracking, web, window
from app.config import get_settings
from app.analysis import build_analysis
from app.dependencies import get_livescore, get_provider, get_unibet
from app.routers.analysis import _gather_context
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(prefix="/tracking", tags=["📊 Suivi & performance"])

# Fenêtre de logging du suivi : logique COMMUNE aux 3 sports (cf. app/window.py).
VOID_AFTER = timedelta(days=3)  # un match non terminé 3 j après l'heure prévue = annulé/reporté


def _now():
    return datetime.now(timezone.utc)


async def run_snapshot(provider: SofaScoreProvider, unibet: UnibetProvider) -> int:
    """Logge prédictions + cotes Unibet des matchs à venir (≈ cote de clôture)."""
    store = tracking.load()
    now = _now()
    horizon = window.cutoff(now)
    updated = 0
    full_tour = get_settings().track_full_tour
    for tour in ("atp", "wta"):
        try:
            matches = (await provider.get_scheduled_matches(tour, days=window.agenda_days()) if full_tour
                       else await provider.get_matches(tour))
        except ProviderError:
            continue
        for m in matches:
            if m.status != "notstarted" or m.start_time is None:
                continue
            if not (now <= m.start_time <= horizon):
                continue
            # Unibet D'ABORD (cache, gratuit) : on ne fetch les stats SofaScore (lourdes)
            # QUE pour les matchs réellement jouables -> beaucoup moins de requêtes SofaScore.
            odds = await unibet.find_odds(m)
            if not (odds and odds.matched):
                continue
            try:
                hm, am, hs, as_, h2h, _ = await _gather_context(m, tour, provider, unibet)
            except ProviderError:
                continue
            await asyncio.sleep(0.4)     # filet d'eau : la boucle ne fait jamais de pic
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
            # 🎯 PERLE tennis : confiance (proba max) + value (edge max) depuis TOUS les marchés
            try:
                from app import markets as _mk
                edges = _mk.tennis_all_edges(m, odds, analysis, tour, m.id, hs, as_)
                picks = _mk.best_picks_tennis(edges)
                rec = store.get(str(m.id))
                if rec is not None:
                    # 🔒 MÉMORISER et NE JAMAIS PERDRE : on ne remplace une perle que par une
                    # NOUVELLE valeur, jamais par None (échec transitoire -> on garde l'ancienne).
                    confs = (picks["confidences"] if picks else []) or []
                    def _keep(new, old):
                        return new if new is not None else old
                    rec["perle"] = _keep(confs[0] if confs else None, rec.get("perle"))
                    rec["perle2"] = _keep(confs[1] if len(confs) > 1 else None, rec.get("perle2"))
                    rec["perle_value"] = _keep((picks or {}).get("value"), rec.get("perle_value"))
            except Exception:
                pass
            # Votes des fans -> persistés pour TOUS les matchs suivis (barre PUBLIC stable
            # et homogène avec le basket). En fond, throttlé, et caché 30 min.
            try:
                v = await provider.get_votes(m.id)
                rec = store.get(str(m.id))
                if rec is not None and v.home_percent is not None:
                    rec["public_home"], rec["public_away"] = v.home_percent, v.away_percent
            except ProviderError:
                pass
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
        # On ne fetch (frais) que les matchs DÉJÀ COMMENCÉS : un match futur ne peut pas
        # être terminé -> on évite des appels réseau inutiles (le fetch de règlement est frais).
        st = rec.get("start_time")
        try:
            if st and datetime.fromisoformat(st) > now:
                continue
        except ValueError:
            pass
        winner = total = score = None
        sets_h = sets_a = None
        try:
            # RÈGLEMENT -> fetch FRAIS (le cache stale-while-revalidate peut servir un
            # « notstarted » périmé pour un match en fait terminé -> jamais réglé).
            m = await provider.get_match(rec.get("tour", "atp"), rec["match_id"], force_refresh=True)
            if m.status == "finished" and m.winner in ("home", "away"):
                winner = m.winner
                sh, sa = (m.home_score.sets or []), (m.away_score.sets or [])
                total = (sum(g for g in sh if g is not None) +
                         sum(g for g in sa if g is not None)) or None
                # sets gagnés par chaque joueur (pour régler « au moins un set », handicaps…)
                sets_h = sum(1 for h, a in zip(sh, sa) if h is not None and a is not None and h > a)
                sets_a = sum(1 for h, a in zip(sh, sa) if h is not None and a is not None and a > h)
                score = web.fmt_score(m.home_score, m.away_score) or None
        except ProviderError:
            # Repli LiveScore : on règle au moins le vainqueur (par noms + date)
            try:
                winner = await livescore.find_result(
                    rec.get("tour", "atp"), rec.get("home", ""), rec.get("away", ""),
                    rec.get("start_time"))
            except Exception:
                winner = None
        if winner and tracking.settle(store, rec["match_id"], winner, total, now.isoformat(),
                                      sets_home=sets_h, sets_away=sets_a, score=score):
            if score and rec.get("result"):
                rec["result"]["score"] = score
            settled += 1
            continue
        # Match jamais « finished » longtemps après l'heure prévue -> annulé/reporté : on
        # le clôt (void) pour qu'il cesse d'être ré-essayé et de gonfler le store.
        st = rec.get("start_time")
        if st:
            try:
                dt = datetime.fromisoformat(st)
            except ValueError:
                dt = None
            if dt and (now - dt) > VOID_AFTER:
                if tracking.void(store, rec["match_id"], "non terminé (reporté/annulé ?)",
                                 now.isoformat()):
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
