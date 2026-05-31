"""Provider **Flashscore** — source de données ALTERNATIVE, indépendante de SofaScore.

⚠️ Cataloguée dans /docs uniquement : Flashscore n'est **pas** utilisé par le modèle,
le suivi ou les pages de l'app. Le but est de répertorier une 2ᵉ source de statistiques.

Flashscore n'expose pas de JSON : son client web charge des « feeds » à un format
délimité maison (séparateurs ÷ entre clé/valeur, ¬ entre champs, ~ entre sections),
via le host `*.flashscore.ninja` avec l'en-tête obligatoire `x-fsign`.

Feeds confirmés (foot=1, tennis=2, basket=3) :
  - f_{sport}_0_3_en_1            -> agenda du jour (matchs, équipes, scores, ligue)
  - df_st_1_{matchId}            -> statistiques de match (xG, tirs / aces / rebonds…)
  - df_sui_1_{matchId}           -> résumé (lieu, TV, infos)
  - df_hh_1_{matchId}            -> confrontations directes (historique)

Robustesse : le préfixe numérique du host et le `x-fsign` peuvent changer côté
Flashscore ; en cas d'échec, on lève proprement (pas de crash, source best-effort).
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("uvicorn")

# Hosts essayés dans l'ordre (le préfixe numérique varie selon les déploiements FS).
FS_HOSTS = [
    "https://2.flashscore.ninja/2/x/feed",
    "https://46.flashscore.ninja/46/x/feed",
    "https://d.flashscore.com/x/feed",
]
FS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.flashscore.com/",
    "Origin": "https://www.flashscore.com",
    "x-fsign": "SW9D1eZo",
}
SPORT_IDS = {"foot": 1, "football": 1, "tennis": 2, "basket": 3, "basketball": 3}

# Séparateurs du format Flashscore.
SEP_SECTION = "~"
SEP_FIELD = "¬"
SEP_KV = "÷"


class FlashscoreError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------- bas niveau
async def _fetch(feed: str) -> str:
    """Récupère un feed Flashscore (essaie plusieurs hosts). Retourne le corps brut."""
    last_exc: Exception | None = None
    async with httpx.AsyncClient(headers=FS_HEADERS, timeout=20, follow_redirects=True) as c:
        for host in FS_HOSTS:
            try:
                r = await c.get(f"{host}/{feed}")
            except httpx.HTTPError as exc:
                last_exc = exc
                continue
            if r.status_code == 200 and len(r.text) > 3:
                return r.text
            last_exc = FlashscoreError(f"Flashscore a répondu {r.status_code} (len {len(r.text)}).")
    if isinstance(last_exc, FlashscoreError):
        raise last_exc
    raise FlashscoreError(f"Flashscore injoignable: {last_exc}")


def _rows(body: str) -> list[dict]:
    """Découpe un feed en sections -> liste de dicts {clé: valeur}."""
    out: list[dict] = []
    for section in body.split(SEP_SECTION):
        kv: dict[str, str] = {}
        for field in section.split(SEP_FIELD):
            if SEP_KV in field:
                k, v = field.split(SEP_KV, 1)
                kv[k] = v
        if kv:
            out.append(kv)
    return out


def _status(stage: str | None) -> str:
    """Mappe le code d'étape Flashscore (AB) vers un statut lisible."""
    return {"1": "scheduled", "3": "finished"}.get(stage or "", "live")


# --------------------------------------------------------------- parseurs
def parse_events(body: str) -> list[dict]:
    """Agenda du jour -> [{id, league, home, away, home_score, away_score, start, status}]."""
    matches: list[dict] = []
    league = None
    for kv in _rows(body):
        if "ZA" in kv:                     # en-tête de compétition
            league = kv.get("ZA")
        if "AA" in kv:                     # une rencontre
            matches.append({
                "id": kv.get("AA"),
                "league": league,
                "home": kv.get("AE"),
                "away": kv.get("AF"),
                "home_score": kv.get("AG"),
                "away_score": kv.get("AH"),
                "start": int(kv["AD"]) if kv.get("AD", "").isdigit() else None,
                "status": _status(kv.get("AB")),
            })
    return matches


def parse_statistics(body: str) -> list[dict]:
    """Statistiques -> [{period, groups:[{name, items:[{name, home, away}]}]}]."""
    periods: list[dict] = []
    cur_period: dict | None = None
    cur_group: dict | None = None
    for kv in _rows(body):
        if "SE" in kv:                     # nouvelle période (Match, 1st half…)
            cur_period = {"period": kv["SE"], "groups": []}
            periods.append(cur_period)
            cur_group = None
        if "SF" in kv:                     # nouveau groupe (Service, Scoring…)
            cur_group = {"name": kv["SF"], "items": []}
            if cur_period is None:
                cur_period = {"period": "Match", "groups": []}
                periods.append(cur_period)
            cur_period["groups"].append(cur_group)
        if "SG" in kv:                     # une statistique
            item = {"name": kv["SG"], "home": kv.get("SH"), "away": kv.get("SI")}
            if cur_group is None:
                cur_group = {"name": "", "items": []}
                if cur_period is None:
                    cur_period = {"period": "Match", "groups": []}
                    periods.append(cur_period)
                cur_period["groups"].append(cur_group)
            cur_group["items"].append(item)
    return periods


# --------------------------------------------------------------- API publique
async def events(sport: str) -> list[dict]:
    sid = SPORT_IDS.get(sport.lower())
    if sid is None:
        raise FlashscoreError(f"Sport inconnu: {sport!r} (foot/tennis/basket).", status_code=400)
    return parse_events(await _fetch(f"f_{sid}_0_3_en_1"))


async def statistics(match_id: str) -> dict:
    body = await _fetch(f"df_st_1_{match_id}")
    return {"match_id": match_id, "source": "flashscore", "periods": parse_statistics(body)}


async def summary(match_id: str) -> dict:
    body = await _fetch(f"df_sui_1_{match_id}")
    return {"match_id": match_id, "source": "flashscore", "rows": _rows(body)}


async def head_to_head(match_id: str) -> dict:
    body = await _fetch(f"df_hh_1_{match_id}")
    return {"match_id": match_id, "source": "flashscore", "rows": _rows(body)}
