"""Point d'entrée de l'API BETSFIX multi-sports (FastAPI)."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app import fragcache
from app.dependencies import get_provider, get_rankings, get_unibet, shutdown_provider
from app.routers import (
    analysis, basket, flashscore, foot, livescore, matches, players, statistics, unibet, web,
)

log = logging.getLogger("uvicorn")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


async def _settle_loop():
    """Boucle de fond du NOUVEAU système : règlement des matchs ANALYSÉS terminés (~10 min) ->
    stats à jour rapidement. (Simulation de bankroll/CLV retirée le 2026-06-14 ; suivi Elo retiré.)"""
    from app import settle_analyst
    await asyncio.sleep(90)    # laisse l'app démarrer
    while True:
        try:
            na = await settle_analyst.settle_analyses()
            if na:
                log.info("analyses réglées : %s", na)
        except Exception as exc:
            log.warning("settle analyses error: %s", exc)
        await asyncio.sleep(10 * 60)


async def _odds_loop():
    """Suivi des VARIATIONS de cote (Unibet, gratuit) : relève les matchs à venir des 3 sports.
    Réveil toutes les 10 min ; `odds_history` décide quels matchs relever (1/h, resserré à 10 min
    dans la dernière heure avant le coup d'envoi). Aucun appel SofaScore."""
    from app import match_select, odds_history
    await asyncio.sleep(40)    # laisse l'app démarrer
    while True:
        try:
            for sp in ("foot", "tennis", "basket"):
                events = await match_select.fetch_events_with_odds(sp)
                n = odds_history.record_all(sp, events)
                if n:
                    log.info("odds history %s : %d relevé(s)", sp, n)
        except Exception as exc:
            log.warning("odds loop error: %s", exc)
        await asyncio.sleep(10 * 60)


def _apply_pending_reset(data: str | None = None) -> bool:
    """Si data/.reset-pending existe : vide les stores de suivi (tennis/foot/basket) + les analyses,
    puis retire la sentinelle. Permet une remise à zéro PROPRE au PROCHAIN démarrage, sans devoir
    rebooter dans l'instant (l'ancien process en mémoire ne peut plus la défaire)."""
    import glob
    import json
    if data is None:
        data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    sentinel = os.path.join(data, ".reset-pending")
    if not os.path.exists(sentinel):
        return False
    for fn in ("tracking_tennis.json", "tracking_foot.json", "tracking_basket.json", "tracking.json"):
        p = os.path.join(data, fn)
        if os.path.exists(p):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({}, f)
            except OSError:
                pass
    removed = 0
    for md in glob.glob(os.path.join(data, "analyses", "*.md")):
        try:
            os.remove(md)
            removed += 1
        except OSError:
            pass
    try:
        os.remove(sentinel)
    except OSError:
        pass
    log.info("Remise à zéro appliquée au démarrage : stores vidés, %d analyse(s) supprimée(s).", removed)
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    if "pytest" not in sys.modules:          # JAMAIS sur les données réelles pendant les tests
        _apply_pending_reset()               # purge en attente (sentinelle) AVANT lecture des stores
    tasks = [asyncio.create_task(_settle_loop()),       # nouveau système (analyste) uniquement
             asyncio.create_task(_odds_loop()),         # suivi des variations de cote (Unibet)
             asyncio.create_task(_panel_warmer())]
    yield
    for t in tasks:
        t.cancel()
    from app import sofa_browser
    await sofa_browser.aclose()      # sinon le Chrome headless (+ profil temp) survit au reload
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
# Organisation /docs PAR SPORT : pour chaque sport, ses sources contiguës
# (Données SofaScore -> Unibet -> Flashscore -> LiveScore), puis les outils transverses.
# Données SofaScore (faits bruts) :
TAG_FOOT_SRC = "⚽ Football · Données SofaScore"
TAG_TENNIS_SRC = "🎾 Tennis · Données SofaScore"
TAG_BASKET_SRC = "🏀 Basket · Données SofaScore"
# Unibet / Flashscore / LiveScore : un tag PAR SPORT (chaînes EXACTES définies dans les routeurs).
from app.routers.unibet import TAG_FOOT as TAG_FOOT_UNIBET  # noqa: E402  "⚽ Football · Unibet"
from app.routers.unibet import TAG_TENNIS as TAG_TENNIS_UNIBET  # noqa: E402  "🎾 Tennis · Unibet"
from app.routers.unibet import TAG_BASKET as TAG_BASKET_UNIBET  # noqa: E402  "🏀 Basket · Unibet"
from app.routers.flashscore import TAG_FOOT as TAG_FLASH_FOOT  # noqa: E402  "⚽ Football · Flashscore"
from app.routers.flashscore import TAG_TENNIS as TAG_FLASH_TENNIS  # noqa: E402  "🎾 Tennis · Flashscore"
from app.routers.flashscore import TAG_BASKET as TAG_FLASH_BASKET  # noqa: E402  "🏀 Basket · Flashscore"
from app.routers.livescore import TAG_FOOT as TAG_LIVE_FOOT  # noqa: E402  "⚽ Football · LiveScore"
from app.routers.livescore import TAG_TENNIS as TAG_LIVE_TENNIS  # noqa: E402  "🎾 Tennis · LiveScore"
from app.routers.livescore import TAG_BASKET as TAG_LIVE_BASKET  # noqa: E402  "🏀 Basket · LiveScore"
# Transverses :
TAG_MODELE_ANALYSE = "🧠 Modèle maison · Analyse & value (PAS une source)"
TAG_INTERFACE = "🖥️ Interface (pages HTML)"
TAG_META = "ℹ️ Méta"

# Tags SANS description : le titre porte déjà l'info. Ordre = par SPORT (foot, tennis,
# basket), chaque sport regroupant Données SofaScore -> Unibet -> Flashscore -> LiveScore ;
# puis Modèle maison, Interface, Méta.
OPENAPI_TAGS = [
    {"name": TAG_FOOT_SRC},
    {"name": TAG_FOOT_UNIBET},
    {"name": TAG_FLASH_FOOT},
    {"name": TAG_LIVE_FOOT},
    {"name": TAG_TENNIS_SRC},
    {"name": TAG_TENNIS_UNIBET},
    {"name": TAG_FLASH_TENNIS},
    {"name": TAG_LIVE_TENNIS},
    {"name": TAG_BASKET_SRC},
    {"name": TAG_BASKET_UNIBET},
    {"name": TAG_FLASH_BASKET},
    {"name": TAG_LIVE_BASKET},
    {"name": TAG_MODELE_ANALYSE},
    {"name": TAG_INTERFACE},
    {"name": TAG_META},
]


def _classify_tag(path: str) -> str | None:
    """Tag /docs d'un endpoint d'après son chemin : regroupé PAR SPORT (foot/tennis/basket),
    chaque sport ayant ses sous-sections Données SofaScore / Cotes Unibet ; Flashscore garde
    ses propres tags par sport (posés au routeur). Transverses : Modèle, Interface, Méta."""
    p = path
    # 💰 Cotes UNIBET 1X2 (sur id SofaScore), rangées dans la section « Unibet » du sport — aux
    #    côtés des endpoints du routeur /unibet (marchés, agenda, live, compétitions).
    if p.endswith("/odds/unibet"):
        if p.startswith("/foot"):
            return TAG_FOOT_UNIBET
        if p.startswith("/basket"):
            return TAG_BASKET_UNIBET
        return TAG_TENNIS_UNIBET            # /matches/{id}/odds/unibet
    # 🧠 Modèle maison : analyse / value / prédictions
    if p.startswith("/analysis"):
        return TAG_MODELE_ANALYSE
    # 🖥️ Pages HTML (dont les pages d'accueil sport /basket et /foot)
    if p == "/" or p.startswith("/app") or p in ("/basket", "/foot"):
        return TAG_INTERFACE
    # ℹ️ Méta
    if p in ("/api", "/health"):
        return TAG_META
    # 🟧 Unibet / Flashscore / LiveScore : on NE retague PAS (les routeurs posent eux-mêmes un tag
    #    PAR SPORT, ⚽/🎾/🏀 ; renvoyer None préserve ces tags au lieu de tout réunir sous un seul).
    if p.startswith("/flashscore") or p.startswith("/livescore") or p.startswith("/unibet"):
        return None
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

# COMPRESSION : le HTML monospace (CSS inline + cartes répétitives) se comprime ~8×
# (ex. accueil 172 Ko -> ~20 Ko). Gain majeur sur mobile/4G via le tunnel.
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402  (regroupé avec les middlewares)

app.add_middleware(GZipMiddleware, minimum_size=512)


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """Empêche le cache des pages HTML : on évite qu'un onglet affiche une vieille
    version (ex. ancien fond/logo) alors que le code a changé. Les fichiers statiques
    (logos/icônes, versionnés par ?v=) sont au contraire mis en cache LONGTEMPS :
    ils ne re-téléchargent plus à chaque visite (le ?v= casse le cache quand on change l'image)."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    elif request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"   # 7 jours
    return resp

app.include_router(matches.router)
app.include_router(statistics.router)
app.include_router(players.router)
app.include_router(analysis.router)
app.include_router(basket.router)
app.include_router(foot.router)
app.include_router(unibet.router)
app.include_router(flashscore.router)
app.include_router(livescore.router)
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
        },
    }


@app.get("/health", tags=["ℹ️ Méta"], summary="Healthcheck")
async def health() -> dict:
    return {"status": "ok"}


# Une fois TOUTES les routes enregistrées, on (re)classe chaque endpoint par nature
# de donnée pour /docs (source SofaScore / cotes / modèle / …). À faire en dernier.
_retag_routes(app)
