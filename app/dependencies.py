"""Dépendances partagées (injection FastAPI) : providers réutilisés entre requêtes."""

from app.config import get_settings
from app.providers.livescore import LiveScoreProvider
from app.providers.rankings import RankingsProvider
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

_provider: SofaScoreProvider | None = None
_unibet: UnibetProvider | None = None
_livescore: LiveScoreProvider | None = None
_rankings: RankingsProvider | None = None


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


def get_livescore() -> LiveScoreProvider:
    global _livescore
    if _livescore is None:
        _livescore = LiveScoreProvider(get_settings())
    return _livescore


def get_rankings() -> RankingsProvider:
    global _rankings
    if _rankings is None:
        _rankings = RankingsProvider(get_settings())
    return _rankings


async def matches_with_fallback(tour: str) -> tuple[list, str]:
    """Liste des matchs avec repli LiveScore si SofaScore échoue.

    Renvoie (matchs, source) où source = 'sofascore' ou 'livescore'.
    """
    try:
        prov = get_provider()
        if get_settings().track_full_tour:
            return await prov.get_scheduled_matches(tour), "sofascore"
        return await prov.get_matches(tour), "sofascore"
    except ProviderError:
        try:
            return await get_livescore().get_matches(tour), "livescore"
        except Exception:
            return [], "sofascore"


async def shutdown_provider() -> None:
    global _provider, _unibet, _livescore, _rankings
    for p in (_provider, _unibet, _livescore, _rankings):
        if p is not None:
            await p.aclose()
    _provider = _unibet = _livescore = _rankings = None
