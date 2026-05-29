"""Point d'entrée de l'API Roland Garros (FastAPI)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.dependencies import shutdown_provider
from app.routers import matches, statistics


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Fermeture propre du client HTTP au shutdown
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

app.include_router(matches.router)
app.include_router(statistics.router)


@app.get("/", tags=["Général"], summary="Bienvenue")
async def root() -> dict:
    return {
        "name": "Roland Garros API",
        "version": __version__,
        "docs": "/docs",
        "endpoints": {
            "tous_les_matchs": "/matches?tour=atp",
            "un_match": "/matches/{match_id}?tour=atp",
            "infos_tournoi": "/matches/tournament?tour=atp",
            "stats_d_un_match": "/statistics/{match_id}",
            "stats_de_tous_les_matchs": "/statistics?tour=atp",
        },
    }


@app.get("/health", tags=["Général"], summary="Healthcheck")
async def health() -> dict:
    return {"status": "ok"}
