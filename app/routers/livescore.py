"""Routeur **LiveScore** — source de données GRATUITE (sans clé, JSON propre) pour les 3 sports.

Expose dans /docs, **rangé par sport** (⚽ Football / 🎾 Tennis / 🏀 Basket) : agenda du jour, matchs
en direct, et score détaillé d'un match (mi-temps foot / quart-temps basket / sets + jeux tennis).
Utilisé en interne par le RÈGLEMENT de secours (cf. app/livescore.py) quand SofaScore est bloqué.

Les `Eid` sont propres à LiveScore : les obtenir via `…/{sport}/matches`, ou les résoudre depuis des
noms via `/livescore/find`.
"""

from fastapi import APIRouter, HTTPException, Query

from app import livescore as ls

# Un tag /docs par sport -> les endpoints LiveScore se rangent SOUS leur sport (« ⚽ Football · LiveScore »).
TAG_FOOT = "⚽ Football · LiveScore"
TAG_TENNIS = "🎾 Tennis · LiveScore"
TAG_BASKET = "🏀 Basket · LiveScore"

router = APIRouter(prefix="/livescore")


def _need(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} indisponible (match introuvable ou sans données).")
    return value


# ─────────────────────────── Commun ───────────────────────────

@router.get("/find", tags=[TAG_FOOT, TAG_TENNIS, TAG_BASKET], summary=" ")
def fs_find(
    home: str = Query(..., description="Équipe/joueur à domicile"),
    away: str = Query(..., description="Équipe/joueur à l'extérieur"),
    sport: str = Query("foot", pattern="^(?i)(football|foot|soccer|tennis|basket|basketball)$",
                       description="football / tennis / basket"),
    start: str | None = Query(None, description="Coup d'envoi ISO (cible le bon jour ±1 ; ex. 2026-06-13T19:00:00Z)"),
) -> dict:
    """Renvoie `{event_id}` LiveScore correspondant aux noms (cherché sur le jour du coup d'envoi ±1)."""
    return {"event_id": _need(ls.find_id(home, away, start, sport), "Match")}


# ─────────────────────────── ⚽ Football ───────────────────────────

@router.get("/football/matches", tags=[TAG_FOOT], summary=" ")
def foot_matches(
    day: int = Query(0, ge=-10, le=7, description="Décalage de jour : 0 = aujourd'hui, -1 = hier…"),
) -> list[dict]:
    """Agenda foot d'un jour : `id` LiveScore, équipes, `league`, statut, score, heure (`start`)."""
    return ls.matches("foot", day)


@router.get("/football/live", tags=[TAG_FOOT], summary=" ")
def foot_live() -> list[dict]:
    """Matchs de foot EN DIRECT (id, équipes, ligue, statut, score)."""
    return ls.live("foot")


@router.get("/football/match/{event_id}/score", tags=[TAG_FOOT], summary=" ")
def foot_score(event_id: str) -> dict:
    """Score détaillé d'un match foot : statut, score final, par mi-temps (`periods`)."""
    return _need(ls.scoreboard("foot", event_id), "Score")


# ─────────────────────────── 🎾 Tennis ───────────────────────────

@router.get("/tennis/matches", tags=[TAG_TENNIS], summary=" ")
def tennis_matches(
    day: int = Query(0, ge=-10, le=7, description="Décalage de jour : 0 = aujourd'hui, -1 = hier…"),
) -> list[dict]:
    """Agenda tennis d'un jour : `id` LiveScore, joueurs, tournoi (`league`), statut, score, heure."""
    return ls.matches("tennis", day)


@router.get("/tennis/live", tags=[TAG_TENNIS], summary=" ")
def tennis_live() -> list[dict]:
    """Matchs de tennis EN DIRECT (id, joueurs, tournoi, statut, score en sets)."""
    return ls.live("tennis")


@router.get("/tennis/match/{event_id}/score", tags=[TAG_TENNIS], summary=" ")
def tennis_score(event_id: str) -> dict:
    """Score détaillé d'un match tennis : statut, sets gagnés, jeux par set (`periods`)."""
    return _need(ls.scoreboard("tennis", event_id), "Score")


# ─────────────────────────── 🏀 Basket ───────────────────────────

@router.get("/basket/matches", tags=[TAG_BASKET], summary=" ")
def basket_matches(
    day: int = Query(0, ge=-10, le=7, description="Décalage de jour : 0 = aujourd'hui, -1 = hier…"),
) -> list[dict]:
    """Agenda basket d'un jour : `id` LiveScore, équipes, ligue, statut, score, heure (`start`)."""
    return ls.matches("basket", day)


@router.get("/basket/live", tags=[TAG_BASKET], summary=" ")
def basket_live() -> list[dict]:
    """Matchs de basket EN DIRECT (id, équipes, ligue, statut, score)."""
    return ls.live("basket")


@router.get("/basket/match/{event_id}/score", tags=[TAG_BASKET], summary=" ")
def basket_score(event_id: str) -> dict:
    """Score détaillé d'un match basket : statut, score final, par quart-temps (`periods`)."""
    return _need(ls.scoreboard("basket", event_id), "Score")
