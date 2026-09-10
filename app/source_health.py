# -*- coding: utf-8 -*-
"""Santé des sources (Phase 4) — ping LÉGER de chaque source de données (analyse + règlement) pour
détecter PROACTIVEMENT une source morte AVANT qu'elle dégrade silencieusement les analyses. Complète la
traçabilité de complétude PAR FICHE (`sources`/`data_score` du sidecar) par une surveillance GLOBALE, en
amont. 100 % réseau, AUCUN effet de bord sur les données.

Surfaces : GET /health/sources · CLI `tools/source_health.py` (alerte Telegram si une source CRITIQUE
tombe) · branché en fin de `deploy/scan_daily.ps1`. Voir [[selfcheck-integrity-audit]],
[[check-connected-sources-first]] (carte des sources = CLAUDE.md §Sources)."""
import asyncio
import time
import httpx
from datetime import datetime, timezone

from app.sources import _FOTMOB   # _ESPN (tennis/basket) + _UNDERSTAT (retiré 2026-09-10) hors app foot

_UA = {"User-Agent": "Mozilla/5.0"}
_T = 12


async def _http_ok(client, url, headers=None, json_expected=True):
    """Requête légère : (ok, detail). ok=True si 200 + payload plausible (JSON non vide / corps non vide)."""
    r = await client.get(url, headers=headers or _UA, timeout=_T)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    if json_expected:
        try:
            j = r.json()
        except Exception:
            return False, "200 mais JSON illisible"
        if not j:
            return False, "200 mais JSON vide"
    elif not (r.text or "").strip():
        return False, "200 mais corps vide"
    return True, "OK"


async def _p_fotmob(c):
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return await _http_ok(c, f"{_FOTMOB}/matches?date={today}")


async def _p_pinnacle(c):
    """Ancre SHARP « Pinnacle » = servie par API-Football (user 2026-09-10 : iProyal RETIRÉ, plus de proxy
    résidentiel ni catalogue 40 Mo). La santé réelle de l'ancre est portée par la sonde API-Football."""
    from app import apifootball as _AF
    return (True, "délégué à API-Football (sans proxy)") if _AF.configured() \
        else (False, "API-Football non configuré")


async def _p_theoddsapi(c):
    """The Odds API = ancre sharp PRIMAIRE (Pinnacle via vraie API). Ping l'endpoint /sports qui NE CONSOMME
    PAS de crédit (gratuit) mais renvoie l'en-tête x-requests-remaining -> on affiche les CRÉDITS RESTANTS du
    mois (surveillance quota, jamais AVEUGLE). Non configuré = repli scraping Pinnacle (dégrade proprement)."""
    from app import theoddsapi
    if not theoddsapi.configured():
        return (False, "non configuré (.env : ODDS_API_KEY) — repli scraping Pinnacle")
    key = theoddsapi._key()
    r = await c.get(f"{theoddsapi._BASE}/sports/?apiKey={key}", timeout=_T)
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code} (clé invalide ?)")
    rem = r.headers.get("x-requests-remaining")
    used = r.headers.get("x-requests-used")
    if rem is not None:
        try:                                               # persiste pour le garde-fou plancher du module
            theoddsapi._last_remaining = int(float(rem))
            theoddsapi._save_quota()
        except Exception:
            pass
        return (True, f"OK — {rem} crédits restants ce mois (utilisés : {used or '?'} / 500)")
    return (True, "OK (clé valide)")


async def _p_apifootball(c):
    """API-Football = RÈGLEMENT + ancre SHARP + cotes OMAP primaires (depuis 2026-09-09). Ping /status :
    affiche le plan + le QUOTA du jour (surveillance : clé valide/active ? proche du plafond 7500 ?). Repli
    scraping PARTOUT si KO (dégrade proprement -> non critique). Rend visible une clé expirée AVANT qu'elle morde."""
    from app import apifootball as AF
    if not AF.configured():
        return (False, "non configuré (.env : BETSFIX_APIFOOTBALL_KEY) — repli scraping partout")

    def _work():
        cl = AF._client()
        try:
            return AF._get(cl, "/status").get("response") or {}
        finally:
            cl.close()
    r = await asyncio.to_thread(_work)
    sub = r.get("subscription") or {}
    req = r.get("requests") or {}
    if not sub:
        return (False, "réponse vide (clé invalide/expirée ?)")
    plan, active = sub.get("plan"), sub.get("active")
    cur, lim = req.get("current"), req.get("limit_day")
    return (bool(active), f"{plan} · {cur}/{lim} req aujourd'hui" + ("" if active else " — INACTIF"))


async def _p_flashscore(c):
    return await _http_ok(c, "https://www.flashscore.com/", json_expected=False)


async def _p_sportradar(c):
    from app import sportradar
    gm = await sportradar.gismo(c, "config_tree", 1)       # endpoint de config GISMO stable
    return (bool(gm), "OK" if gm else "réponse vide")


async def _p_sofascore(c):
    """SofaScore via la cascade (direct curl_cffi gratuit -> repli RapidAPI payant). ok=True dès qu'une voie
    répond 200 sur un endpoint stable (live football). ÉCONOMIE QUOTA : on ne ping RapidAPI que si le DIRECT
    tombe (mirroir de la vraie cascade). Suit la stabilité jour/jour : la source avait été jugée MORTE lors
    d'une DOUBLE panne temporaire (Cloudflare 403 direct + quota RapidAPI épuisé), re-vérifiée VIVANTE le
    2026-07-28 (3 voies 200, résolution _resolve_sofa 4/4). Le detail expose la voie active."""
    from app import sofa_http
    url = "https://api.sofascore.com/api/v1/sport/football/events/live"
    direct = "?"
    try:                                                   # 1) DIRECT (gratuit) = voie normale
        r = await sofa_http.session().get(url)
        if r.status_code == 200 and isinstance((r.json() or {}).get("events"), list):
            return True, "direct OK"
        direct = f"HTTP {r.status_code}"
    except Exception as e:
        direct = type(e).__name__
    try:                                                   # 2) direct KO -> RapidAPI rattrape-t-il ?
        rr = await sofa_http._rapid_get(url, None)
        if rr is not None and rr.status_code == 200:
            return True, f"direct KO ({direct}), RapidAPI OK"
    except Exception:
        pass
    return False, f"direct {direct} + RapidAPI KO"


async def _p_unibet(c):
    from app import unibet
    n = await asyncio.to_thread(lambda: len(unibet.matches("foot")))
    return (n > 0, f"{n} matchs" if n else "0 match (sélection à sec)")


async def _p_livescore(c):
    from app import livescore
    n = await asyncio.to_thread(lambda: len(livescore.matches("foot")))
    return (n > 0, f"{n} matchs" if n else "0 match")


# (clé, label, rôle, CRITIQUE) — critique = pilier sans lequel le système ne peut PAS produire d'analyse
# fiable (Unibet = sélection+cotes ; FotMob = source n°1 foot analyse ET règlement des tirs). Les autres
# dégradent sans planter (replis en cascade), donc « importantes » = warn si down, pas error.
_SOURCES = [
    ("unibet", "Unibet (Kambi)", "cotes + sélection des matchs", True, _p_unibet),
    ("fotmob", "FotMob", "foot : analyse + règlement tirs", True, _p_fotmob),
    ("pinnacle", "Pinnacle (iProyal)", "ancre sharp PRIMAIRE (catalogue MONDIAL via proxy résidentiel)",
     False, _p_pinnacle),
    ("theoddsapi", "The Odds API", "ancre sharp de repli (gratuit, 68 ligues)", False, _p_theoddsapi),
    ("apifootball", "API-Football", "RÈGLEMENT + ancre SHARP + cotes OMAP primaires (repli scraping)",
     False, _p_apifootball),
    # ESPN RETIRÉ de la sonde (user 2026-08-07 : app 100 % foot) — ESPN ne servait QUE tennis/basket, son
    # 403 déclenchait un faux « warn ». La sonde suit désormais uniquement les sources UTILES au football.
    # Understat RETIRÉ de la sonde (user 2026-09-10) — xG migré sur API-Football (top-5), Understat plus appelé.
    ("flashscore", "Flashscore", "forme/H2H/score (repli règlement)", False, _p_flashscore),
    ("livescore", "LiveScore", "score live + règlement", False, _p_livescore),
    ("sportradar", "Sportradar GISMO", "periods/stats (règlement)", False, _p_sportradar),
    ("sofascore", "SofaScore", "séries/votes/scores live — re-vérifié VIVANT 2026-07-28 (garder RapidAPI)",
     False, _p_sofascore),
]


async def check_all() -> dict:
    """Ping TOUTES les sources EN PARALLÈLE (latence mesurée). Renvoie {status, ts, sources:[...],
    down, down_critical}. status = 'error' si une source CRITIQUE est down, 'warn' si une non-critique
    est down, sinon 'ok'. Ne lève jamais (chaque ping est isolé)."""
    async with httpx.AsyncClient() as client:
        async def _run(key, label, role, crit, fn):
            t0 = time.perf_counter()
            try:
                ok, detail = await fn(client)
            except Exception as e:
                ok, detail = False, type(e).__name__
            return {"key": key, "label": label, "role": role, "critical": crit, "ok": ok,
                    "latency_ms": round((time.perf_counter() - t0) * 1000), "detail": detail}
        results = await asyncio.gather(*[_run(*s) for s in _SOURCES])
    down = [r for r in results if not r["ok"]]
    down_crit = [r for r in down if r["critical"]]
    status = "error" if down_crit else ("warn" if down else "ok")
    return {"status": status, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": results, "down": [r["key"] for r in down],
            "down_critical": [r["key"] for r in down_crit]}
