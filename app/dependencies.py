"""Dépendances partagées (injection FastAPI) : providers réutilisés entre requêtes."""

from app.config import get_settings
from app.providers.sofascore import SofaScoreProvider
from app.providers.unibet import UnibetProvider

_provider: SofaScoreProvider | None = None
_unibet: UnibetProvider | None = None


def get_provider() -> SofaScoreProvider:
    global _provider
    if _provider is None:
        _provider = SofaScoreProvider(get_settings())
    return _provider


def get_unibet() -> UnibetProvider:
    global _unibet
    if _unibet is None:
        _unibet = UnibetProvider(get_settings())
    return _unibet


async def shutdown_provider() -> None:
    global _provider, _unibet
    if _provider is not None:
        await _provider.aclose()
        _provider = None
    if _unibet is not None:
        await _unibet.aclose()
        _unibet = None
