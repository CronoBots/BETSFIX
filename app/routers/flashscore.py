"""Routeur **Flashscore** — source ALTERNATIVE, répertoriée dans /docs uniquement.

⚠️ Volontairement isolé : ces endpoints servent à explorer une 2ᵉ source de stats.
Flashscore n'est branché ni sur le modèle, ni sur le suivi, ni sur les pages de l'app.
Les ids de match sont **propres à Flashscore** (obtenus via /flashscore/{sport}/events).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.providers import flashscore as fs

router = APIRouter(prefix="/flashscore", tags=["🟧 Flashscore (source alternative)"])

Sport = Literal["foot", "tennis", "basket"]


@router.get(
    "/{sport}/events",
    summary="Agenda du jour Flashscore (matchs, équipes, scores, ligue) — id Flashscore",
)
async def fs_events(sport: Sport) -> list[dict]:
    try:
        return await fs.events(sport)
    except fs.FlashscoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/match/{match_id}/statistics",
    summary="Statistiques d'un match (xG/tirs en foot, aces en tennis, rebonds en basket)",
)
async def fs_statistics(
    match_id: str,
    period: int = Query(1, ge=1, le=3, description="1 = match entier, 2/3 = par période/mi-temps"),
) -> dict:
    try:
        return await fs.statistics(match_id, period)
    except fs.FlashscoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/match/{match_id}/lineups",
    summary="Compositions / formations d'un match (foot) — brut Flashscore",
)
async def fs_lineups(match_id: str) -> dict:
    try:
        return await fs.lineups(match_id)
    except fs.FlashscoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/match/{match_id}/incidents",
    summary="Déroulé du match : buts, cartons, pénos, remplacements (foot)",
)
async def fs_incidents(match_id: str) -> dict:
    try:
        return await fs.incidents(match_id)
    except fs.FlashscoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/match/{match_id}/summary",
    summary="Résumé d'un match (lieu, diffuseurs, infos) — brut Flashscore",
)
async def fs_summary(match_id: str) -> dict:
    try:
        return await fs.summary(match_id)
    except fs.FlashscoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/match/{match_id}/h2h",
    summary="Confrontations directes (historique) — brut Flashscore",
)
async def fs_h2h(match_id: str) -> dict:
    try:
        return await fs.head_to_head(match_id)
    except fs.FlashscoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
