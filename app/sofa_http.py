"""Accès HTTP SofaScore via curl_cffi — imite l'empreinte TLS de Chrome (JA3).

Le 403 « Source en pause » de SofaScore n'était PAS un problème de cookie : Cloudflare bloque
les clients dont l'empreinte TLS n'est pas celle d'un vrai navigateur. httpx (Python) a une
empreinte « non-navigateur » -> 403 systématique, quels que soient les en-têtes/cookies.

curl_cffi rejoue l'empreinte exacte de Chrome -> les requêtes passent (HTTP 200), sans cookie
ni navigateur. Tous les appels SofaScore (provider + foot/basket) passent désormais par ici.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from curl_cffi.requests import AsyncSession

log = logging.getLogger("uvicorn")
IMPERSONATE = "chrome"      # profil TLS/JA3 rejoué (Chrome récent)
_session: AsyncSession | None = None


def session() -> AsyncSession:
    """Session curl_cffi partagée (créée à la 1ère utilisation, réutilisée ensuite)."""
    global _session
    if _session is None:
        _session = AsyncSession(impersonate=IMPERSONATE, timeout=20)
    return _session


# --------------------------------------------------------------- repli RapidAPI (SportAPI7)
# Quand SofaScore renvoie 403/429 (rate-limit), on REJOUE le MÊME chemin sur SportAPI7 (miroir
# SofaScore, mêmes URLs /api/v1/...). Plafonné par jour pour ne pas vider le quota. `allow_rapid`
# permet à un script gourmand (backtest) de le couper s'il ne veut pas consommer le quota.
allow_rapid = True
_SOFA_BASES = ("https://api.sofascore.com/api/v1", "https://www.sofascore.com/api/v1")
_rapid_day: str | None = None
_rapid_count = 0
_settings = None


def _cfg():
    global _settings
    if _settings is None:
        from app.config import get_settings
        _settings = get_settings()
    return _settings


def _rapid_target(url: str) -> str | None:
    s = _cfg()
    if not s.rapidapi_key:
        return None
    for b in _SOFA_BASES:
        if url.startswith(b):
            return f"https://{s.rapidapi_host}/api/v1" + url[len(b):]
    return None


def _quota_ok() -> bool:
    global _rapid_day, _rapid_count
    today = date.today().isoformat()
    if _rapid_day != today:
        _rapid_day, _rapid_count = today, 0
    return _rapid_count < _cfg().rapidapi_daily_cap


async def _rapid_get(url: str, params):
    global _rapid_count
    target = _rapid_target(url)
    if not target or not allow_rapid or not _quota_ok():
        return None
    import httpx
    s = _cfg()
    hdr = {"x-rapidapi-key": s.rapidapi_key, "x-rapidapi-host": s.rapidapi_host}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            for _ in range(2):
                _rapid_count += 1
                r = await c.get(target, params=params, headers=hdr)
                if r.status_code == 429:           # débit RapidAPI -> petite pause + 1 essai
                    await asyncio.sleep(1.0)
                    continue
                if r.status_code == 200:
                    log.info("repli RapidAPI OK (%s appels aujourd'hui)", _rapid_count)
                return r
            return r
    except Exception:
        return None


async def get(url: str, params=None, headers=None):
    """GET impersoné SofaScore. Sur 403/429, repli automatique RapidAPI (si clé + quota).
    Renvoie une réponse avec .status_code / .json() / .content / .headers."""
    r = await session().get(url, params=params, headers=headers)
    if r.status_code in (403, 429):
        rr = await _rapid_get(url, params)
        if rr is not None and rr.status_code == 200:
            return rr
    return r
