"""Routeur **Unibet** (plateforme Kambi) — cotes & marchés GRATUITS (sans clé), rangés par sport.

Expose dans /docs, par sport (⚽/🎾/🏀) : agenda, matchs en direct, l'arbre des compétitions, et surtout
**TOUS les marchés d'un match** (handicaps, totaux, mi-temps, props joueur…) avec cotes en DÉCIMAL.
Les `event_id` sont propres à Unibet : les obtenir via `…/{sport}/matches` ou `/unibet/find`.
"""

from fastapi import APIRouter, HTTPException, Query

from app import unibet as ub

# Un tag /docs par sport -> les endpoints Unibet se rangent SOUS leur sport (« ⚽ Football · Unibet »).
# Ces chaînes servent AUSSI à main.py pour ranger les /odds/unibet (1X2) dans la même section.
TAG_FOOT = "⚽ Football · Unibet"
TAG_TENNIS = "🎾 Tennis · Unibet"
TAG_BASKET = "🏀 Basket · Unibet"

router = APIRouter(prefix="/unibet")


def _need(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} indisponible (match introuvable ou sans données).")
    return value


# ─────────────────────────── Commun ───────────────────────────

@router.get("/find", tags=[TAG_FOOT, TAG_TENNIS, TAG_BASKET], summary=" ")
def ub_find(
    home: str = Query(..., description="Équipe/joueur à domicile"),
    away: str = Query(..., description="Équipe/joueur à l'extérieur"),
    sport: str = Query("foot", pattern="^(?i)(football|foot|tennis|basket|basketball)$",
                       description="football / tennis / basket"),
) -> dict:
    """Renvoie `{event_id}` Unibet correspondant aux noms (cherché dans l'agenda du sport)."""
    return {"event_id": _need(ub.find_id(home, away, sport), "Match")}


# ─────────────────────────── ⚽ Football ───────────────────────────

@router.get("/football/matches", tags=[TAG_FOOT], summary=" ")
def foot_matches() -> list[dict]:
    """Agenda foot Unibet (matchs à venir) : id, équipes, compétition, heure, nb de marchés."""
    return ub.matches("foot")


@router.get("/football/live", tags=[TAG_FOOT], summary=" ")
def foot_live() -> list[dict]:
    """Matchs de foot EN DIRECT chez Unibet (+ score live)."""
    return ub.live("foot")


@router.get("/football/competitions", tags=[TAG_FOOT], summary=" ")
def foot_competitions() -> list[dict]:
    """Compétitions de foot proposées par Unibet (id, nom, nb de matchs ouverts)."""
    return ub.competitions("foot")


@router.get("/football/match/{event_id}/markets", tags=[TAG_FOOT], summary=" ")
def foot_markets(event_id: str) -> dict:
    """TOUS les marchés d'un match foot (cotes décimales) : 1X2, handicaps, totaux, mi-temps, props…"""
    return _need(ub.markets(event_id), "Marchés")


# ─────────────────────────── 🎾 Tennis ───────────────────────────

@router.get("/tennis/matches", tags=[TAG_TENNIS], summary=" ")
def tennis_matches() -> list[dict]:
    """Agenda tennis Unibet (matchs à venir) : id, joueurs, tournoi, heure, nb de marchés."""
    return ub.matches("tennis")


@router.get("/tennis/live", tags=[TAG_TENNIS], summary=" ")
def tennis_live() -> list[dict]:
    """Matchs de tennis EN DIRECT chez Unibet (+ score live)."""
    return ub.live("tennis")


@router.get("/tennis/competitions", tags=[TAG_TENNIS], summary=" ")
def tennis_competitions() -> list[dict]:
    """Tournois de tennis proposés par Unibet (id, nom, nb de matchs ouverts)."""
    return ub.competitions("tennis")


@router.get("/tennis/match/{event_id}/markets", tags=[TAG_TENNIS], summary=" ")
def tennis_markets(event_id: str) -> dict:
    """TOUS les marchés d'un match tennis (cotes décimales) : vainqueur, sets, jeux, handicaps…"""
    return _need(ub.markets(event_id), "Marchés")


# ─────────────────────────── 🏀 Basket ───────────────────────────

@router.get("/basket/matches", tags=[TAG_BASKET], summary=" ")
def basket_matches() -> list[dict]:
    """Agenda basket Unibet (matchs à venir) : id, équipes, ligue, heure, nb de marchés."""
    return ub.matches("basket")


@router.get("/basket/live", tags=[TAG_BASKET], summary=" ")
def basket_live() -> list[dict]:
    """Matchs de basket EN DIRECT chez Unibet (+ score live)."""
    return ub.live("basket")


@router.get("/basket/competitions", tags=[TAG_BASKET], summary=" ")
def basket_competitions() -> list[dict]:
    """Ligues de basket proposées par Unibet (id, nom, nb de matchs ouverts)."""
    return ub.competitions("basket")


@router.get("/basket/match/{event_id}/markets", tags=[TAG_BASKET], summary=" ")
def basket_markets(event_id: str) -> dict:
    """TOUS les marchés d'un match basket (cotes décimales) : vainqueur, handicaps, totaux, par période…"""
    return _need(ub.markets(event_id), "Marchés")
