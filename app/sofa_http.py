"""Accès HTTP SofaScore via curl_cffi — imite l'empreinte TLS de Chrome (JA3).

Le 403 « Source en pause » de SofaScore n'était PAS un problème de cookie : Cloudflare bloque
les clients dont l'empreinte TLS n'est pas celle d'un vrai navigateur. httpx (Python) a une
empreinte « non-navigateur » -> 403 systématique, quels que soient les en-têtes/cookies.

curl_cffi rejoue l'empreinte exacte de Chrome -> les requêtes passent (HTTP 200), sans cookie
ni navigateur. Tous les appels SofaScore (provider + foot/basket) passent désormais par ici.
"""

from __future__ import annotations

from curl_cffi.requests import AsyncSession

IMPERSONATE = "chrome"      # profil TLS/JA3 rejoué (Chrome récent)
_session: AsyncSession | None = None


def session() -> AsyncSession:
    """Session curl_cffi partagée (créée à la 1ère utilisation, réutilisée ensuite)."""
    global _session
    if _session is None:
        _session = AsyncSession(impersonate=IMPERSONATE, timeout=20)
    return _session


async def get(url: str, params=None, headers=None):
    """GET impersoné. Renvoie la réponse curl_cffi (.status_code / .json() / .content / .headers)."""
    return await session().get(url, params=params, headers=headers)
