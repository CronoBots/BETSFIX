"""Point d'entrée de l'API BETSFIX multi-sports (FastAPI)."""

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
from app import fragcache
from app.dependencies import get_provider, get_rankings, get_unibet, shutdown_provider
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


async def _panel_warmer():
    """Pré-chauffe le cache des panneaux de liste pour que PERSONNE n'attende le calcul
    réseau (l'accueil = ~2,6 s à froid). On recalcule juste sous le TTL ; via force_refresh,
    l'ancienne valeur reste servie pendant le recalcul -> aucun « trou » froid."""
    await asyncio.sleep(8)   # laisse l'app démarrer
    panels = [
        ("panel/home", lambda: web.home(provider=get_provider(), frag=1)),
        ("panel/tennis", lambda: web.matches_page(
            provider=get_provider(), rankings=get_rankings(), unibet=get_unibet(), frag=1)),
        ("panel/directs", lambda: web.directs_page(unibet=get_unibet(), frag=1)),
        ("panel/foot", lambda: foot.foot_page(frag=1)),
        ("panel/basket", lambda: basket.basket_page(frag=1)),
    ]
    while True:
        for key, call in panels:
            try:
                fragcache.force_refresh(key)
                await call()
            except Exception as exc:   # ne jamais tuer le réchauffeur
                log.debug("warmer %s: %s", key, exc)
        await asyncio.sleep(15)        # < TTL 20s -> le cache ne se vide jamais


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_tracking_loop()),
             asyncio.create_task(_panel_warmer())]
    yield
    for t in tasks:
        t.cancel()
    await shutdown_provider()


# --------------------------------------------------------------------------- #
# Organisation de /docs PAR NATURE DE DONNÉE (et non plus par sport), pour qu'un
# consommateur (humain ou bot) ne confonde JAMAIS :
#   🟢 une SOURCE de faits bruts (SofaScore, Flashscore) — données factuelles ;
#   🟡 des COTES de bookmaker (Unibet/SofaScore) — prix de marché bruts ;
#   🔴 une SORTIE DE NOTRE MODÈLE (analyse, value, prédiction, suivi) — un CALCUL,
#      surtout pas une source à réinjecter telle quelle.
# Les tags sont assignés par chemin dans _retag_routes() (voir plus bas), donc les
# tags posés au niveau des routeurs sont sans effet : seule cette liste fait foi.
# --------------------------------------------------------------------------- #
TAG_TENNIS_SRC = "🎾 Tennis · Données SofaScore (source)"
TAG_FOOT_SRC = "⚽ Foot · Données SofaScore (source)"
TAG_BASKET_SRC = "🏀 Basket · Données SofaScore (source)"
TAG_COTES = "💰 Cotes & paris Unibet"
TAG_MODELE_ANALYSE = "🧠 Modèle maison · Analyse & value (PAS une source)"
TAG_MODELE_SUIVI = "📊 Modèle maison · Suivi & performance"
TAG_FLASH = "🟧 Flashscore (source alternative)"
TAG_INTERFACE = "🖥️ Interface (pages HTML)"
TAG_META = "ℹ️ Méta"

# Tags SANS description : le titre porte déjà l'info (source / cotes / modèle).
# Seules les COTES UNIBET vivent dans la section dédiée ; les cotes SofaScore sont
# purement informatives et restent dans la section « Données SofaScore » du sport.
OPENAPI_TAGS = [
    {"name": TAG_TENNIS_SRC},
    {"name": TAG_FOOT_SRC},
    {"name": TAG_BASKET_SRC},
    {"name": TAG_COTES},
    {"name": TAG_MODELE_ANALYSE},
    {"name": TAG_MODELE_SUIVI},
    {"name": TAG_FLASH},
    {"name": TAG_INTERFACE},
    {"name": TAG_META},
]


def _classify_tag(path: str) -> str | None:
    """Tag /docs d'un endpoint d'après son chemin (nature de la donnée, pas le sport)."""
    p = path
    # 🟡 Cotes UNIBET uniquement (les /odds SofaScore, informatifs, restent dans la
    #    section « Données SofaScore » du sport via les règles plus bas).
    if p.endswith("/odds/unibet"):
        return TAG_COTES
    # 🔴 Modèle maison : analyse / value / prédictions
    if p.startswith("/analysis") or p in (
        "/basket/board", "/basket/finished", "/foot/board", "/foot/finished"):
        return TAG_MODELE_ANALYSE
    # 🔴 Modèle maison : suivi & performance
    if p.startswith("/tracking"):
        return TAG_MODELE_SUIVI
    # 🖥️ Pages HTML (dont les pages d'accueil sport /basket et /foot)
    if p == "/" or p.startswith("/app") or p in ("/basket", "/foot"):
        return TAG_INTERFACE
    # ℹ️ Méta
    if p in ("/api", "/health"):
        return TAG_META
    # 🟧 Source alternative
    if p.startswith("/flashscore"):
        return TAG_FLASH
    # 🟢 Sources SofaScore par sport (le reste)
    if p.startswith(("/matches", "/players", "/statistics")):
        return TAG_TENNIS_SRC
    if p.startswith("/foot"):
        return TAG_FOOT_SRC
    if p.startswith("/basket"):
        return TAG_BASKET_SRC
    return None


def _retag_routes(application) -> None:
    """Réassigne le tag /docs de chaque route selon sa nature (voir _classify_tag)."""
    from fastapi.routing import APIRoute
    for route in application.routes:
        if isinstance(route, APIRoute):
            tag = _classify_tag(route.path)
            if tag:
                route.tags = [tag]

app = FastAPI(
    title="BETSFIX API — multi-sports",
    version=__version__,
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


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """Empêche le cache des pages HTML : on évite qu'un onglet affiche une vieille
    version (ex. ancien fond/logo) alors que le code a changé. Les fichiers statiques
    (logos, versionnés par ?v=) gardent leur cache normal."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

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
        "name": "BETSFIX — Analyse paris multi-sports",
        "short_name": "BETSFIX",
        "description": "Tennis · Basket · Foot — modèle vs cotes, value, calibration.",
        "start_url": "/", "scope": "/", "display": "standalone",
        "orientation": "portrait",
        "background_color": "#080a0f", "theme_color": "#080a0f",
        "icons": [
            {"src": "/static/icon-192.png?v=2", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png?v=2", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/api", tags=["ℹ️ Méta"],
         summary="Catalogue des endpoints (JSON), groupé par NATURE (source / cotes / modèle)")
async def root() -> dict:
    return {
        "name": "BETSFIX API",
        "version": __version__,
        "docs": "/docs",
        # ⚠️ Lecture impérative pour un bot/agent : ne pas confondre les natures.
        "_lire_avant_usage": {
            "sources": "Faits BRUTS (SofaScore, Flashscore). À utiliser comme base d'analyse.",
            "cotes_unibet": "Prix RÉELS du bookmaker Unibet (les seules à utiliser). "
                            "Les cotes SofaScore sont juste informatives (rangées dans sources).",
            "modele": "⚠️ CALCULS de BETSFIX (probas, value, prédictions). PAS une source : "
                      "ne jamais réinjecter ces valeurs comme si c'étaient des faits.",
        },
        # 🟢 SOURCES — faits bruts
        "sources": {
            "tennis_sofascore": {
                "matchs": "/matches?tour=atp",
                "un_match": "/matches/{match_id}?tour=atp",
                "matchs_d_un_round": "/matches/round/{round}?tour=atp",
                "point_par_point": "/matches/{match_id}/point-by-point",
                "head_to_head": "/matches/{match_id}/h2h?tour=atp",
                "votes": "/matches/{match_id}/votes",
                "series": "/matches/{match_id}/streaks",
                "stats_match": "/statistics/{match_id}",
                "stats_tous_matchs": "/statistics?tour=atp",
                "fiche_joueur": "/players/{player_id}",
                "photo_joueur": "/players/{player_id}/image",
                "stats_joueur": "/players/{player_id}/statistics?tour=atp",
                "classements_joueur": "/players/{player_id}/rankings",
                "matchs_joueur": "/players/{player_id}/matches",
                "editions": "/matches/seasons?tour=atp",
                "cotes_sofascore_informatif": "/matches/{match_id}/odds",
            },
            "foot_sofascore": {
                "competitions": "/foot/competitions",
                "forme_avant_match": "/foot/match/{event_id}/pregame-form",
                "stats_match": "/foot/match/{event_id}/statistics",
                "tirs_xg": "/foot/match/{event_id}/shotmap",
                "proba_live_sofascore": "/foot/match/{event_id}/win-probability",
                "momentum": "/foot/match/{event_id}/momentum",
                "incidents": "/foot/match/{event_id}/incidents",
                "compositions": "/foot/match/{event_id}/lineups",
                "notes_joueurs": "/foot/match/{event_id}/best-players",
                "h2h": "/foot/match/{event_id}/h2h",
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
                "cotes_sofascore_informatif": "/foot/match/{event_id}/odds",
            },
            "basket_sofascore": {
                "forme_avant_match": "/basket/match/{event_id}/pregame-form",
                "stats_match": "/basket/match/{event_id}/statistics",
                "momentum": "/basket/match/{event_id}/momentum",
                "incidents_quart_temps": "/basket/match/{event_id}/incidents",
                "compositions": "/basket/match/{event_id}/lineups",
                "h2h": "/basket/match/{event_id}/h2h",
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
                "cotes_sofascore_informatif": "/basket/match/{event_id}/odds",
            },
            "flashscore_alternative": {
                "agenda": "/flashscore/{sport}/events  (sport: foot|tennis|basket)",
                "stats_match": "/flashscore/match/{match_id}/statistics?period=1",
                "compositions": "/flashscore/match/{match_id}/lineups",
                "incidents": "/flashscore/match/{match_id}/incidents",
                "resume": "/flashscore/match/{match_id}/summary",
                "h2h": "/flashscore/match/{match_id}/h2h",
            },
        },
        # 🟡 COTES — prix réels du marché : UNIBET uniquement (tous marchés par sport).
        # Les cotes SofaScore sont seulement informatives -> rangées dans `sources`.
        "cotes_unibet": {
            "tennis_tous_marches": "/matches/{match_id}/odds/unibet?tour=atp",
            "foot_tous_marches": "/foot/match/{event_id}/odds/unibet",
            "basket_tous_marches": "/basket/match/{event_id}/odds/unibet",
        },
        # 🔴 MODÈLE MAISON — calculs, PAS une source
        "modele_maison": {
            "_avertissement": "Sorties calculées par BETSFIX (probas/value/prédictions). "
                              "Ne pas utiliser comme donnée factuelle.",
            "tennis_analyse_paris": "/analysis/{match_id}?tour=atp",
            "tennis_analyse_tous_marches": "/analysis/{match_id}/markets?tour=atp",
            "foot_board": "/foot/board",
            "foot_termines": "/foot/finished",
            "basket_board": "/basket/board",
            "basket_termines": "/basket/finished",
            "suivi_rapport": "/tracking/report?sport=tennis",
            "suivi_tableau_de_bord": "/tracking/dashboard?sport=tennis",
            "suivi_journal": "/tracking/log",
            "suivi_confiances_du_jour": "/tracking/today",
        },
    }


@app.get("/health", tags=["ℹ️ Méta"], summary="Healthcheck")
async def health() -> dict:
    return {"status": "ok"}


# Une fois TOUTES les routes enregistrées, on (re)classe chaque endpoint par nature
# de donnée pour /docs (source SofaScore / cotes / modèle / …). À faire en dernier.
_retag_routes(app)
