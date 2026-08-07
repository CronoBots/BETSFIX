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

from app.sources import _ESPN, _FOTMOB, _UNDERSTAT

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


async def _p_espn(c):
    return await _http_ok(c, f"{_ESPN}/site/v2/sports/tennis/atp/rankings")


async def _p_understat(c):
    return await _http_ok(c, f"{_UNDERSTAT}/", json_expected=False)


async def _p_pinnacle(c):
    """Pinnacle via la cascade réelle (direct gratuit -> repli proxy résidentiel si Cloudflare bloque l'IP).
    Le detail dit la voie active -> on voit si on consomme les Go du proxy (blocage IP en cours) ou non."""
    from app import pinnacle
    data = await asyncio.to_thread(pinnacle._get, "sports")
    n = len(data) if isinstance(data, list) else 0
    via = "proxy" if pinnacle._direct_blocked() else "direct"
    return (bool(data), f"{n} sports (via {via})" if data else "KO (direct + proxy)")


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
    ("pinnacle", "Pinnacle", "ancre sharp (proba de référence)", False, _p_pinnacle),
    # ESPN RETIRÉ de la sonde (user 2026-08-07 : app 100 % foot) — ESPN ne servait QUE tennis/basket, son
    # 403 déclenchait un faux « warn ». La sonde suit désormais uniquement les sources UTILES au football.
    ("understat", "Understat", "foot : xG (top-5 ligues)", False, _p_understat),
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
