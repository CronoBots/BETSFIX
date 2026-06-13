"""Routeur **Flashscore** — source de données JEU PAR JEU / POINT PAR POINT (gratuite, sans clé).

Sert à explorer et exposer dans /docs tous les feeds Flashscore exploitables : agenda, déroulé
jeu-par-jeu (qui sert / qui gagne chaque jeu), score par set, statistiques détaillées, face-à-face.
Utilisé en interne par le RÈGLEMENT des marchés « 1er jeu de service » (cf. app/flashscore.py).

Les `matchId` sont propres à Flashscore : les obtenir via `/flashscore/tennis/matches`, ou les
résoudre depuis des noms via `/flashscore/find`.
"""

import re

from fastapi import APIRouter, HTTPException, Query

from app import flashscore as fs

router = APIRouter(prefix="/flashscore", tags=["🟧 Flashscore (jeu-par-jeu)"])


def _need(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} indisponible (match introuvable ou sans données).")
    return value


@router.get("/tennis/matches", summary="Agenda tennis d'un jour (matchId Flashscore + joueurs)")
def fs_matches(
    day: int = Query(0, ge=-10, le=1, description="Décalage de jour : 0 = aujourd'hui, -1 = hier, etc."),
) -> list[dict]:
    """Liste les matchs tennis d'un jour avec leur `id` Flashscore et les noms (ordre = domicile - extérieur)."""
    return fs.matches(day)


@router.get("/find", summary="Résoudre le matchId Flashscore depuis les noms des joueurs")
def fs_find(
    home: str = Query(..., description="Joueur/équipe à domicile"),
    away: str = Query(..., description="Joueur/équipe à l'extérieur"),
    start: str | None = Query(None, description="Coup d'envoi ISO (pour cibler le bon jour ; ex. 2026-06-13T11:00:00Z)"),
) -> dict:
    """Renvoie `{match_id}` Flashscore correspondant aux noms (cherché sur le jour du coup d'envoi ±1)."""
    return {"match_id": _need(fs.find_id(home, away, start), "Match")}


@router.get("/match/{match_id}/points", summary="Déroulé JEU PAR JEU : serveur et vainqueur de chaque jeu")
def fs_points(match_id: str) -> dict:
    """Pour chaque jeu (du 1er au dernier) : qui SERT (`server`) et qui GAGNE (`winner`).
    Source des règlements « 1er jeu de service tenu »."""
    games = fs.points(match_id)
    if not games:
        raise HTTPException(status_code=404, detail="Déroulé jeu-par-jeu indisponible pour ce match.")
    return {"match_id": match_id, "games": games}


@router.get("/match/{match_id}/score", summary="Score par set (+ tie-breaks, durée, vainqueur)")
def fs_score(match_id: str) -> dict:
    return _need(fs.score(match_id), "Score")


@router.get("/match/{match_id}/statistics",
            summary="Statistiques détaillées (aces, 1er service %, balles de break…) par section")
def fs_statistics(match_id: str) -> dict:
    return _need(fs.statistics(match_id), "Statistiques")


@router.get("/match/{match_id}/first-service",
            summary="Régler « X remporte son 1er jeu de service » (won/lost)")
def fs_first_service(
    match_id: str,
    side: str = Query(..., pattern="^(?i)(home|away)$", description="Joueur concerné : home ou away"),
) -> dict:
    """Renvoie si le joueur a TENU son 1er jeu de service : `result` = won / lost (null si indispo)."""
    games = fs.points(match_id)
    want = "home" if side.lower() == "home" else "away"
    res = next(("won" if g["winner"] == want else "lost" for g in games if g["server"] == want), None)
    return {"match_id": match_id, "side": want, "result": res}


@router.get("/match/{match_id}/h2h", summary="Face-à-face (feed brut décodé) — historique des confrontations")
def fs_h2h(match_id: str) -> dict:
    """Feed `df_hh` décodé en enregistrements bruts (codes Flashscore -> valeurs). Volumineux."""
    feed = fs._feed("df_hh", match_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Face-à-face indisponible.")
    rows = [{"code": c, "value": v}
            for c, v in re.findall(r"([A-Z]{2,3})" + fs._SEP_FLD + r"([^" + fs._SEP_REC + r"]*)", feed)]
    return {"match_id": match_id, "records": rows}
