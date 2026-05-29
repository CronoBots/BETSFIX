"""Dépendances partagées (injection FastAPI) : provider unique réutilisé entre requêtes."""

from app.config import get_settings
from app.providers.sofascore import SofaScoreProvider

_provider: SofaScoreProvider | None = None


def get_provider() -> SofaScoreProvider:
    global _provider
    if _provider is None:
        _provider = SofaScoreProvider(get_settings())
    return _provider


async def shutdown_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.aclose()
        _provider = None
