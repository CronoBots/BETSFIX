"""Point d'entrée de l'API Roland Garros (FastAPI)."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.dependencies import get_provider, get_unibet, shutdown_provider
from app.routers import analysis, matches, players, statistics, tracking
from app.routers.tracking import run_settle, run_snapshot

log = logging.getLogger("uvicorn")
TRACKING_INTERVAL_S = 3 * 3600  # snapshot + settle toutes les 3h


async def _tracking_loop():
    """Tâche de fond : enregistre cotes/prédictions et règle les résultats."""
    await asyncio.sleep(60)  # laisse l'app démarrer
    while True:
        try:
            n = await run_snapshot(get_provider(), get_unibet())
            s = await run_settle(get_provider())
            log.info("tracking: %s prédictions loggées/màj, %s matchs réglés", n, s)
        except Exception as exc:  # ne jamais tuer la boucle
            log.warning("tracking loop error: %s", exc)
        await asyncio.sleep(TRACKING_INTERVAL_S)


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


@app.get("/", tags=["Général"], summary="Bienvenue")
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
