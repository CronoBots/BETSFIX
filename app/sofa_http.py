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
import sys
from datetime import date

from curl_cffi.requests import AsyncSession

log = logging.getLogger("uvicorn")
IMPERSONATE = "chrome"      # profil TLS/JA3 rejoué (Chrome récent)
_session: AsyncSession | None = None
_proxy_sess: AsyncSession | None = None

# --- Garde-fous conso proxy (Go limités) ---------------------------------------------------
# allow_proxy : coupe-circuit global ; les boucles de FOND (suivi 3h, warmer) le passent à
#   False le temps de leur exécution pour ne jamais griller les Go sur du bulk.
# allow_bulk_proxy : autorise les GROS endpoints (scheduled-events ~4 Mo, standings, stats) via
#   proxy. False par défaut (app live) ; SEUL le scan le met à True (il les met en cache, 1/jour).
allow_proxy = True
allow_bulk_proxy = False
_BULK_ENDPOINTS = ("scheduled-events", "/standings", "/statistics", "/events/last", "/events/next")


def session() -> AsyncSession:
    """Session curl_cffi DIRECTE (sans proxy) — la voie normale, gratuite."""
    global _session
    if _session is None:
        _session = AsyncSession(impersonate=IMPERSONATE, timeout=20)
    return _session


def _proxy_session() -> AsyncSession | None:
    """Session curl_cffi via le proxy résidentiel `SOFA_PROXY` — DERNIER RECOURS uniquement.
    None si aucun proxy configuré. N'est utilisée que quand SofaScore direct est bloqué ET que
    RapidAPI ne rattrape pas (cf. get()), pour ne pas consommer les Go du proxy inutilement."""
    global _proxy_sess
    if "pytest" in sys.modules:      # jamais d'appel réseau réel (proxy) pendant les tests
        return None
    proxy = (_cfg().sofa_proxy or "").strip()
    if not proxy:
        return None
    if _proxy_sess is None:
        _proxy_sess = AsyncSession(impersonate=IMPERSONATE, timeout=25,
                                   proxies={"http": proxy, "https": proxy})
        log.info("Proxy SofaScore prêt (dernier recours, %s…)", proxy.split("@")[-1][:24])
    return _proxy_sess


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


async def _via_proxy(url, params, headers):
    """GET SofaScore via le proxy résidentiel (dernier recours). None si pas de proxy / échec /
    coupe-circuit. ÉCONOMIE Go : refusé si le proxy est globalement coupé (boucles de fond) ou
    si c'est un GROS endpoint bulk (scheduled-events ~4 Mo, standings, stats) non explicitement
    autorisé — ces appels n'ont aucune raison de griller les Go en continu."""
    if not allow_proxy:
        return None
    if not allow_bulk_proxy and any(b in url for b in _BULK_ENDPOINTS):
        return None
    ps = _proxy_session()
    if ps is None:
        return None
    try:
        r = await ps.get(url, params=params, headers=headers)
        if r.status_code == 200:
            log.info("SofaScore via PROXY (dernier recours) OK")
        return r
    except Exception:
        return None


async def get(url: str, params=None, headers=None):
    """GET SofaScore en CASCADE pour économiser le proxy :
      1) DIRECT (curl_cffi, gratuit) — si 200, on s'arrête là.
      2) sinon (403/429 ou timeout) -> repli RapidAPI (si clé + quota mensuel restant).
      3) sinon SEULEMENT -> proxy résidentiel `SOFA_PROXY` (dernier recours, consomme les Go).
    Renvoie une réponse avec .status_code / .json() / .content / .headers."""
    try:
        r = await session().get(url, params=params, headers=headers)
    except Exception:
        r = None
    # 1) direct OK -> retour immédiat (ni RapidAPI ni proxy : aucun Go/quota consommé)
    if r is not None and r.status_code not in (403, 429):
        return r
    # 1.5) NAVIGATEUR (Chrome headless + proxy -> SSR du site) AVANT RapidAPI, car GRATUIT + illimité.
    # Couvre event/{id} et event/{id}/incidents (règlement, score). On NE brûle donc PLUS le quota
    # RapidAPI MENSUEL (15000 req) sur ces appels répétitifs : on le RÉSERVE aux endpoints que le
    # navigateur ne couvre PAS (stats, h2h, planning) — la donnée à VRAIE valeur pour les analyses.
    try:
        from app import sofa_browser
        br = await sofa_browser.response_for(url)
        if br is not None:
            log.info("SofaScore via NAVIGATEUR (SSR) OK")
            return br
    except Exception:
        pass
    # 2) repli RapidAPI (mensuel) — réservé aux endpoints non couverts par le navigateur
    rr = await _rapid_get(url, params)
    if rr is not None and rr.status_code == 200:
        return rr
    # 3) proxy curl_cffi (uniquement si direct bloqué ET RapidAPI ne rattrape pas)
    pr = await _via_proxy(url, params, headers)
    if pr is not None and pr.status_code == 200:
        return pr
    # 4) rien n'a abouti -> meilleure réponse dispo (le 403 direct), ou on lève
    if r is not None:
        return r
    if rr is not None:
        return rr
    if pr is not None:
        return pr
    raise RuntimeError("SofaScore injoignable (direct, RapidAPI et proxy tous en échec)")
