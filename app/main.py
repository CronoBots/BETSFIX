"""Point d'entrée de l'API BetsFix multi-sports (FastAPI)."""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app import basket as basket_sport
from app import foot as foot_sport
from app.dependencies import get_provider, get_unibet, shutdown_provider
from app.routers import (
    analysis, basket, flashscore, foot, matches, players, statistics, tracking, web,
)
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
            try:   # foot (CdM + grandes compétitions) — suivi séparé
                nf, sf = await foot_sport.run_snapshot(), await foot_sport.run_settle()
                log.info("foot: %s loggés/màj, %s réglés", nf, sf)
            except Exception as exc:
                log.warning("foot loop error: %s", exc)
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


# Ordre et description des sections de /docs (regroupées par sport).
OPENAPI_TAGS = [
    {"name": "🎾 Tennis",
     "description": "ATP/WTA — matchs (fiche, h2h, cotes, point par point, séries, votes), "
                    "statistiques de match, joueurs (fiche, photo, classements, stats), "
                    "et analyse value vs cotes (tous les marchés : aces, sets…)."},
    {"name": "⚽ Football",
     "description": "Coupe du Monde + grandes compétitions : board 1X2, stats match (xG…), "
                    "incidents, compositions, classement, top joueurs/équipes, cotes."},
    {"name": "🏀 Basketball",
     "description": "NBA + WNBA : board (Elo, marge), stats match, classement, "
                    "top joueurs/équipes, cotes, effectifs."},
    {"name": "🟧 Flashscore (source alternative)",
     "description": "Source de stats **indépendante de SofaScore**, répertoriée ici pour "
                    "exploration — non utilisée par le modèle ni l'app. Ids propres à "
                    "Flashscore (via /flashscore/{sport}/events). Best-effort : feeds non "
                    "officiels, peuvent changer."},
    {"name": "📊 Suivi & performance",
     "description": "Calibration multi-sports : Brier, log-loss, CLV, track record. "
                    "Tableau de bord par sport (?sport=tennis|foot|basket)."},
    {"name": "🖥️ Interface (pages HTML)",
     "description": "Pages de l'application (rendu HTML), pour mémoire — pas des ressources JSON."},
    {"name": "ℹ️ Méta",
     "description": "Catalogue des endpoints et healthcheck."},
]

app = FastAPI(
    title="BETSFIX API — multi-sports",
    version=__version__,
    description=(
        "API **BETSFIX** : récupère matchs, **statistiques complètes** et cotes pour "
        "**3 sports**, et confronte un modèle aux cotes du marché pour repérer la *value*.\n\n"
        "- 🎾 **Tennis** ATP/WTA — 🏀 **Basket** NBA & WNBA — ⚽ **Foot** (CdM + grandes compétitions)\n"
        "- Source de données gratuite **SofaScore** + cotes **Unibet**.\n"
        "- Les sections de cette page sont **regroupées par sport**.\n\n"
        "⚠️ Outil d'aide à la décision : un modèle simple ne bat pas un book sérieux. "
        "Le juge de paix est le **CLV**, pas un pari isolé.\n\n"
        "Documentation interactive : `/docs` · Schéma OpenAPI : `/openapi.json`"
    ),
    openapi_tags=OPENAPI_TAGS,
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
app.include_router(flashscore.router)
app.include_router(web.router)

# PWA : fichiers statiques (icônes) + manifest -> app installable sur l'écran d'accueil
app.mount("/static", StaticFiles(directory=os.path.join(_ROOT, "static")), name="static")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> JSONResponse:
    return JSONResponse({
        "name": "BetsFix — Analyse paris multi-sports",
        "short_name": "BetsFix",
        "description": "Tennis · Basket · Foot — modèle vs cotes, value, calibration.",
        "start_url": "/", "scope": "/", "display": "standalone",
        "orientation": "portrait",
        "background_color": "#080a0f", "theme_color": "#080a0f",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/api", tags=["ℹ️ Méta"], summary="Catalogue des endpoints (JSON), groupé par sport")
async def root() -> dict:
    return {
        "name": "BetsFix API",
        "version": __version__,
        "docs": "/docs",
        "sports": {
            "tennis": {
                "matchs": "/matches?tour=atp",
                "un_match": "/matches/{match_id}?tour=atp",
                "matchs_d_un_round": "/matches/round/{round}?tour=atp",
                "point_par_point": "/matches/{match_id}/point-by-point",
                "head_to_head": "/matches/{match_id}/h2h?tour=atp",
                "votes": "/matches/{match_id}/votes",
                "series": "/matches/{match_id}/streaks",
                "cotes": "/matches/{match_id}/odds",
                "cotes_unibet": "/matches/{match_id}/odds/unibet?tour=atp",
                "stats_match": "/statistics/{match_id}",
                "stats_tous_matchs": "/statistics?tour=atp",
                "analyse_paris": "/analysis/{match_id}?tour=atp",
                "analyse_tous_marches": "/analysis/{match_id}/markets?tour=atp",
                "fiche_joueur": "/players/{player_id}",
                "photo_joueur": "/players/{player_id}/image",
                "stats_joueur": "/players/{player_id}/statistics?tour=atp",
                "classements_joueur": "/players/{player_id}/rankings",
                "matchs_joueur": "/players/{player_id}/matches",
                "editions": "/matches/seasons?tour=atp",
            },
            "foot": {
                "board": "/foot/board",
                "termines": "/foot/finished",
                "competitions": "/foot/competitions",
                "forme_avant_match": "/foot/match/{event_id}/pregame-form",
                "stats_match": "/foot/match/{event_id}/statistics",
                "tirs_xg": "/foot/match/{event_id}/shotmap",
                "proba_live": "/foot/match/{event_id}/win-probability",
                "momentum": "/foot/match/{event_id}/momentum",
                "incidents": "/foot/match/{event_id}/incidents",
                "compositions": "/foot/match/{event_id}/lineups",
                "notes_joueurs": "/foot/match/{event_id}/best-players",
                "h2h": "/foot/match/{event_id}/h2h",
                "cotes": "/foot/match/{event_id}/odds",
                "votes": "/foot/match/{event_id}/votes",
                "series": "/foot/match/{event_id}/streaks",
                "classement": "/foot/competition/{tournament_id}/standings",
                "top_joueurs": "/foot/competition/{tournament_id}/top-players",
                "top_equipes": "/foot/competition/{tournament_id}/top-teams",
                "stats_equipe": "/foot/team/{team_id}/statistics?tournament_id=17",
                "effectif": "/foot/team/{team_id}/squad",
                "fiche_joueur": "/foot/player/{player_id}",
                "stats_joueur": "/foot/player/{player_id}/statistics",
                "photo_joueur": "/foot/player/{player_id}/image",
            },
            "basket": {
                "board": "/basket/board",
                "termines": "/basket/finished",
                "forme_avant_match": "/basket/match/{event_id}/pregame-form",
                "stats_match": "/basket/match/{event_id}/statistics",
                "momentum": "/basket/match/{event_id}/momentum",
                "incidents_quart_temps": "/basket/match/{event_id}/incidents",
                "compositions": "/basket/match/{event_id}/lineups",
                "h2h": "/basket/match/{event_id}/h2h",
                "cotes": "/basket/match/{event_id}/odds",
                "votes": "/basket/match/{event_id}/votes",
                "series": "/basket/match/{event_id}/streaks",
                "classement_nba": "/basket/competition/132/standings",
                "classement_wnba": "/basket/competition/486/standings",
                "top_joueurs": "/basket/competition/{tournament_id}/top-players",
                "top_equipes": "/basket/competition/{tournament_id}/top-teams",
                "stats_equipe": "/basket/team/{team_id}/statistics",
                "effectif": "/basket/team/{team_id}/squad",
                "fiche_joueur": "/basket/player/{player_id}",
                "photo_joueur": "/basket/player/{player_id}/image",
                "box_scores_joueurs": "/basket/match/{event_id}/lineups",
            },
            "suivi": {
                "rapport": "/tracking/report?sport=tennis",
                "tableau_de_bord": "/tracking/dashboard?sport=tennis",
                "journal": "/tracking/log",
                "confiances_du_jour": "/tracking/today",
            },
            "flashscore_source_alternative": {
                "agenda": "/flashscore/{sport}/events  (sport: foot|tennis|basket)",
                "stats_match": "/flashscore/match/{match_id}/statistics?period=1",
                "compositions": "/flashscore/match/{match_id}/lineups",
                "resume": "/flashscore/match/{match_id}/summary",
                "h2h": "/flashscore/match/{match_id}/h2h",
            },
        },
    }


@app.get("/health", tags=["ℹ️ Méta"], summary="Healthcheck")
async def health() -> dict:
    return {"status": "ok"}
