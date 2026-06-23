"""Routeur **Sportradar** (feed GISMO, accès LIBRE sans clé) — exposé dans /docs, rangé PAR SPORT.

Mêmes données que la couche d'enrichissement du scan (`app/sportradar.py`) : forme (V/N/D), série en
cours, H2H, position au classement — plus une **passerelle GISMO brute** pour tout le reste du feed.

Résolution match Unibet -> id Sportradar : `/sportradar/find` (noms + jour). Les ids GISMO sont propres
à Sportradar ; on les obtient via `/find`. Les noms doivent être en FRANÇAIS (comme l'app).
"""
import httpx
from fastapi import APIRouter, HTTPException, Query

from app import sportradar as sr

# Un tag /docs par sport -> les endpoints se rangent SOUS leur sport (« ⚽ Football · Sportradar »).
TAG_FOOT = "⚽ Football · Sportradar"
TAG_TENNIS = "🎾 Tennis · Sportradar"
TAG_BASKET = "🏀 Basket · Sportradar"
_ALL = [TAG_FOOT, TAG_TENNIS, TAG_BASKET]

router = APIRouter(prefix="/sportradar")

_SPORT = {"football": "foot", "foot": "foot", "soccer": "foot",
          "tennis": "tennis", "basket": "basket", "basketball": "basket"}


def _norm(sport: str) -> str:
    s = _SPORT.get((sport or "").lower())
    if not s:
        raise HTTPException(status_code=422, detail="sport ∈ football / tennis / basket")
    return s


_SPORT_Q = Query("foot", pattern="^(?i)(football|foot|soccer|tennis|basket|basketball)$",
                 description="football / tennis / basket")


@router.get("/find", tags=_ALL, summary=" ")
async def find(
    home: str = Query(..., description="Équipe/joueur à domicile (nom FR, comme l'app)"),
    away: str = Query(..., description="Équipe/joueur à l'extérieur"),
    sport: str = _SPORT_Q,
    start: str | None = Query(None, description="Coup d'envoi ISO (cible le bon jour ±1)"),
) -> dict:
    """`{match_id}` Sportradar du match (noms FR + jour). 404 si absent de la page du jour."""
    sp = _norm(sport)
    async with httpx.AsyncClient() as cl:
        mid = await sr.resolve(cl, sp, home, away, start or "")
    if not mid:
        raise HTTPException(status_code=404, detail="Match introuvable sur Sportradar (page du jour).")
    return {"sport": sp, "match_id": mid}


@router.get("/facts", tags=_ALL, summary=" ")
async def facts(
    home: str = Query(..., description="Équipe/joueur à domicile (nom FR)"),
    away: str = Query(..., description="Équipe/joueur à l'extérieur"),
    sport: str = _SPORT_Q,
    start: str | None = Query(None, description="Coup d'envoi ISO (cible le bon jour ±1)"),
) -> dict:
    """Faits prêts pour l'analyse : forme (5 derniers V/N/D), série, H2H, position au classement."""
    sp = _norm(sport)
    async with httpx.AsyncClient() as cl:
        mid = await sr.resolve(cl, sp, home, away, start or "")
        f = await sr.facts(cl, sp, home, away, start or "")
    return {"sport": sp, "match_id": mid, "facts": f}


@router.get("/match/{match_id}/info", tags=_ALL, summary=" ")
async def match_info(match_id: int) -> dict:
    """`match_info` Sportradar (équipes, stade, tournoi, saison, coverage)."""
    async with httpx.AsyncClient() as cl:
        return await sr.info(cl, match_id) or {}


@router.get("/match/{match_id}/form", tags=_ALL, summary=" ")
async def match_form(match_id: int) -> dict:
    """Forme des 2 équipes (W/D/L) + série en cours (`stats_match_form`)."""
    async with httpx.AsyncClient() as cl:
        return await sr.gismo(cl, "stats_match_form", match_id) or {}


@router.get("/gismo/{endpoint}/{ident:path}", tags=_ALL, summary=" ")
async def gismo_raw(endpoint: str, ident: str) -> dict:
    """Passerelle BRUTE vers le feed GISMO. Ex : `stats_season_tables/101177`,
    `stats_team_versus/4475/4739`, `match_squads/66457012`, `match_timeline/66457012`."""
    async with httpx.AsyncClient() as cl:
        d = await sr.gismo(cl, endpoint, ident)
    if d is None:
        raise HTTPException(status_code=404, detail="GISMO : rien (endpoint/id invalide ou exception).")
    return {"endpoint": endpoint, "ident": ident, "data": d}
