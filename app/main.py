"""Point d'entrée de l'API Roland Garros (FastAPI)."""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app import basket as basket_sport
from app.dependencies import get_provider, get_unibet, shutdown_provider
from app.routers import analysis, basket, foot, matches, players, statistics, tracking, web
from app.routers.tracking import run_settle, run_snapshot

log = logging.getLogger("uvicorn")
TRACKING_INTERVAL_S = 3 * 3600   # rythme normal : toutes les 3h
TRACKING_RETRY_S = 20 * 60       # SofaScore bloqué : on réessaie toutes les 20 min

# Reconstruction automatique des notes du modèle (Elo/tendances/service-retour).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_MARKER = os.path.join(_ROOT, "data", ".last_data_build")
DATA_REBUILD_S = 7 * 24 * 3600   # une fois par semaine


async def _maybe_rebuild_data() -> None:
    """Relance build_data_all (sous-processus) si > 1 semaine depuis le dernier build.

    Isolé en sous-processus : ne bloque pas la boucle et ne peut pas crasher l'app.
    Un fichier marqueur (mtime) évite de relancer à chaque passe ou à chaque reboot.
    """
    try:
        age = time.time() - os.path.getmtime(_DATA_MARKER)
    except OSError:
        age = None  # marqueur absent -> jamais construit (ou nouveau déploiement)
    if age is not None and age < DATA_REBUILD_S:
        return
    os.makedirs(os.path.dirname(_DATA_MARKER), exist_ok=True)
    with open(_DATA_MARKER, "w", encoding="utf-8") as f:  # touch -> évite re-déclenchement
        f.write(str(int(time.time())))

    async def _run():
        log.info("data: reconstruction hebdo des notes (Elo/tendances/service-retour)...")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, os.path.join(_ROOT, "tools", "build_data_all.py"),
                cwd=_ROOT, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            log.info("data: reconstruction terminée (code %s)", proc.returncode)
        except Exception as exc:  # ne jamais tuer la boucle
            log.warning("data rebuild error: %s", exc)

    asyncio.create_task(_run())   # lancement en tâche de fond, sans bloquer le suivi


async def _tracking_loop():
    """Tâche de fond : enregistre cotes/prédictions et règle les résultats.

    Cadence adaptative : si SofaScore est bloqué (disjoncteur ouvert), on réessaie
    plus souvent (1 tentative / 20 min) pour réchauffer le cache dès qu'une fenêtre
    s'ouvre ; sinon rythme normal de 3h.
    """
    await asyncio.sleep(60)  # laisse l'app démarrer
    while True:
        try:
            n = await run_snapshot(get_provider(), get_unibet())
            s = await run_settle(get_provider())
            log.info("tracking: %s prédictions loggées/màj, %s matchs réglés", n, s)
            try:   # basket (WNBA) — suivi séparé, ne casse pas le tennis s'il échoue
                nb, sb = await basket_sport.run_snapshot(), await basket_sport.run_settle()
                log.info("basket: %s loggés/màj, %s réglés", nb, sb)
            except Exception as exc:
                log.warning("basket loop error: %s", exc)
            await _maybe_rebuild_data()   # reconstruit les notes 1x/semaine (auto)
        except Exception as exc:  # ne jamais tuer la boucle
            log.warning("tracking loop error: %s", exc)
        healthy = get_provider().breaker_status()["ok"]
        delay = TRACKING_INTERVAL_S if healthy else TRACKING_RETRY_S
        log.info("tracking: SofaScore %s -> prochaine passe dans %s min",
                 "OK" if healthy else "bloqué", delay // 60)
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_tracking_loop())
    yield
    task.cancel()
    await shutdown_provider()


app = FastAPI(
    title="Roland Garros API",
    version=__version__,
    description=(
        "API qui récupère **tous les matchs** et **toutes les statistiques** de Roland Garros "
        "(simple messieurs ATP et dames WTA), à partir de la source gratuite SofaScore.\n\n"
        "Documentation interactive : `/docs` · Schéma OpenAPI : `/openapi.json`"
    ),
    lifespan=lifespan,
)

# CORS ouvert : usage personnel, permet d'appeler l'API depuis un navigateur
# mobile ou un futur front-end sans blocage.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches.router)
app.include_router(statistics.router)
app.include_router(players.router)
app.include_router(analysis.router)
app.include_router(tracking.router)
app.include_router(basket.router)
app.include_router(foot.router)
app.include_router(web.router)


@app.get("/api", tags=["Général"], summary="Liste des endpoints (JSON)")
async def root() -> dict:
    return {
        "name": "Roland Garros API",
        "version": __version__,
        "docs": "/docs",
        "endpoints": {
            "tous_les_matchs": "/matches?tour=atp",
            "un_match": "/matches/{match_id}?tour=atp",
            "matchs_d_un_round": "/matches/round/{round}?tour=atp",
            "point_par_point": "/matches/{match_id}/point-by-point",
            "head_to_head": "/matches/{match_id}/h2h?tour=atp",
            "pronostics_fans": "/matches/{match_id}/votes",
            "series": "/matches/{match_id}/streaks",
            "cotes": "/matches/{match_id}/odds",
            "cotes_unibet": "/matches/{match_id}/odds/unibet?tour=atp",
            "analyse_paris": "/analysis/{match_id}?tour=atp",
            "analyse_tous_marches": "/analysis/{match_id}/markets?tour=atp",
            "suivi_performance": "/tracking/report",
            "tableau_de_bord": "/tracking/dashboard",
            "editions_disponibles": "/matches/seasons?tour=atp",
            "infos_tournoi": "/matches/tournament?tour=atp",
            "stats_d_un_match": "/statistics/{match_id}",
            "stats_de_tous_les_matchs": "/statistics?tour=atp",
            "fiche_joueur": "/players/{player_id}",
            "photo_joueur": "/players/{player_id}/image",
            "stats_joueur": "/players/{player_id}/statistics?tour=atp",
            "stats_dispo_joueur": "/players/{player_id}/statistics/available",
            "classements_joueur": "/players/{player_id}/rankings",
            "matchs_joueur": "/players/{player_id}/matches",
        },
    }


@app.get("/health", tags=["Général"], summary="Healthcheck")
async def health() -> dict:
    return {"status": "ok"}
