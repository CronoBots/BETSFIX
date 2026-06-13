"""Routeur **Flashscore** — source de données GRATUITE (sans clé) jeu-par-jeu / point-par-point.

Expose dans /docs, **trié par sport** (Football ⚽ / Tennis 🎾 / Basket 🏀), tous les feeds Flashscore
exploitables : agenda du jour, score (mi-temps / sets / quart-temps), statistiques détaillées,
face-à-face, et pour le tennis le déroulé jeu-par-jeu (qui sert / qui gagne) utilisé en interne par le
RÈGLEMENT des marchés « 1er jeu de service » (cf. app/flashscore.py).

Les `matchId` sont propres à Flashscore : les obtenir via `…/{sport}/matches`, ou les résoudre depuis
des noms via `/flashscore/find`.
"""

import re

from fastapi import APIRouter, HTTPException, Query

from app import flashscore as fs

# Un tag /docs par sport -> les endpoints Flashscore se rangent SOUS leur sport dans /docs
# (organisation par sport : « ⚽ Football · Flashscore », etc.).
TAG_FOOT = "⚽ Football · Flashscore"
TAG_TENNIS = "🎾 Tennis · Flashscore"
TAG_BASKET = "🏀 Basket · Flashscore"

router = APIRouter(prefix="/flashscore")


def _need(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} indisponible (match introuvable ou sans données).")
    return value


def _records(code: str, match_id: str, what: str) -> dict:
    """Décode un feed brut (`df_hh`, `df_in`…) en enregistrements code->valeur."""
    feed = fs._feed(code, match_id)
    if not feed:
        raise HTTPException(status_code=404, detail=f"{what} indisponible.")
    rows = [{"code": c, "value": v}
            for c, v in re.findall(r"([A-Z]{2,3})" + fs._SEP_FLD + r"([^" + fs._SEP_REC + r"]*)", feed)]
    return {"match_id": match_id, "records": rows}


# ─────────────────────────── Commun ───────────────────────────

@router.get("/find", tags=[TAG_TENNIS, TAG_FOOT, TAG_BASKET],
            summary="Résoudre un matchId Flashscore depuis les noms (+ sport + jour)")
def fs_find(
    home: str = Query(..., description="Équipe/joueur à domicile"),
    away: str = Query(..., description="Équipe/joueur à l'extérieur"),
    sport: str = Query("tennis", pattern="^(?i)(football|foot|tennis|basket|basketball)$",
                       description="football / tennis / basket"),
    start: str | None = Query(None, description="Coup d'envoi ISO (cible le bon jour ±1 ; ex. 2026-06-13T11:00:00Z)"),
) -> dict:
    """Renvoie `{match_id}` Flashscore correspondant aux noms (cherché sur le jour du coup d'envoi ±1)."""
    return {"match_id": _need(fs.find_id(home, away, start, sport), "Match")}


# ─────────────────────────── ⚽ Football ───────────────────────────

@router.get("/football/matches", tags=[TAG_FOOT],
            summary="Agenda foot d'un jour (matchId + équipes + compétition + score)")
def foot_matches(
    day: int = Query(0, ge=-10, le=2, description="Décalage de jour : 0 = aujourd'hui, -1 = hier…"),
) -> list[dict]:
    """Tous les matchs foot du jour : `id` Flashscore, équipes, `league`, score et `start_ts`."""
    return fs.matches("football", day)


@router.get("/football/match/{match_id}/score", tags=[TAG_FOOT],
            summary="Score par mi-temps (1re / 2e période, score final)")
def foot_score(match_id: str) -> dict:
    return _need(fs.periods(match_id), "Score")


@router.get("/football/match/{match_id}/statistics", tags=[TAG_FOOT],
            summary="Statistiques détaillées (possession, tirs, corners, fautes…)")
def foot_statistics(match_id: str) -> dict:
    return _need(fs.statistics(match_id), "Statistiques")


@router.get("/football/match/{match_id}/incidents", tags=[TAG_FOOT],
            summary="Déroulé du match (buts, cartons, remplacements) — feed brut décodé")
def foot_incidents(match_id: str) -> dict:
    return {"match_id": match_id, **_need(fs.incidents(match_id), "Déroulé")}


@router.get("/football/match/{match_id}/h2h", tags=[TAG_FOOT],
            summary="Face-à-face (historique des confrontations) — feed brut décodé")
def foot_h2h(match_id: str) -> dict:
    return _records("df_hh", match_id, "Face-à-face")


# ─────────────────────────── 🎾 Tennis ───────────────────────────

@router.get("/tennis/matches", tags=[TAG_TENNIS],
            summary="Agenda tennis d'un jour (matchId + joueurs + tournoi + score)")
def tennis_matches(
    day: int = Query(0, ge=-10, le=2, description="Décalage de jour : 0 = aujourd'hui, -1 = hier…"),
) -> list[dict]:
    """Tous les matchs tennis du jour : `id` Flashscore, joueurs, `league`, score et `start_ts`."""
    return fs.matches("tennis", day)


@router.get("/tennis/match/{match_id}/points", tags=[TAG_TENNIS],
            summary="Déroulé JEU PAR JEU : serveur et vainqueur de chaque jeu")
def tennis_points(match_id: str) -> dict:
    """Pour chaque jeu (du 1er au dernier) : qui SERT (`server`) et qui GAGNE (`winner`).
    Source des règlements « 1er jeu de service tenu »."""
    games = fs.points(match_id)
    if not games:
        raise HTTPException(status_code=404, detail="Déroulé jeu-par-jeu indisponible pour ce match.")
    return {"match_id": match_id, "games": games}


@router.get("/tennis/match/{match_id}/first-service", tags=[TAG_TENNIS],
            summary="Régler « X remporte son 1er jeu de service » (won/lost)")
def tennis_first_service(
    match_id: str,
    side: str = Query(..., pattern="^(?i)(home|away)$", description="Joueur concerné : home ou away"),
) -> dict:
    """Renvoie si le joueur a TENU son 1er jeu de service : `result` = won / lost (null si indispo)."""
    games = fs.points(match_id)
    want = "home" if side.lower() == "home" else "away"
    res = next(("won" if g["winner"] == want else "lost" for g in games if g["server"] == want), None)
    return {"match_id": match_id, "side": want, "result": res}


@router.get("/tennis/match/{match_id}/score", tags=[TAG_TENNIS],
            summary="Score par set (+ tie-breaks, durée, vainqueur)")
def tennis_score(match_id: str) -> dict:
    return _need(fs.score(match_id), "Score")


@router.get("/tennis/match/{match_id}/statistics", tags=[TAG_TENNIS],
            summary="Statistiques détaillées (aces, 1er service %, balles de break…) par section")
def tennis_statistics(match_id: str) -> dict:
    return _need(fs.statistics(match_id), "Statistiques")


@router.get("/tennis/match/{match_id}/h2h", tags=[TAG_TENNIS],
            summary="Face-à-face (historique des confrontations) — feed brut décodé")
def tennis_h2h(match_id: str) -> dict:
    return _records("df_hh", match_id, "Face-à-face")


# ─────────────────────────── 🏀 Basket ───────────────────────────

@router.get("/basket/matches", tags=[TAG_BASKET],
            summary="Agenda basket d'un jour (matchId + équipes + ligue + score)")
def basket_matches(
    day: int = Query(0, ge=-10, le=2, description="Décalage de jour : 0 = aujourd'hui, -1 = hier…"),
) -> list[dict]:
    """Tous les matchs basket du jour : `id` Flashscore, équipes, `league`, score et `start_ts`."""
    return fs.matches("basket", day)


@router.get("/basket/match/{match_id}/score", tags=[TAG_BASKET],
            summary="Score par quart-temps (+ score final, vainqueur)")
def basket_score(match_id: str) -> dict:
    """Le feed `df_su` du basket expose les quart-temps (rendus dans `sets`) et le total."""
    return _need(fs.score(match_id), "Score")


@router.get("/basket/match/{match_id}/h2h", tags=[TAG_BASKET],
            summary="Face-à-face (historique des confrontations) — feed brut décodé")
def basket_h2h(match_id: str) -> dict:
    return _records("df_hh", match_id, "Face-à-face")
