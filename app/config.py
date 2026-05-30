"""Configuration de l'application, chargée depuis les variables d'environnement / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Source de données gratuite (SofaScore, sans clé)
    sofascore_base_url: str = "https://api.sofascore.com/api/v1"

    # Identifiants uniqueTournament SofaScore pour Roland Garros
    rg_atp_tournament_id: int = 2480
    rg_wta_tournament_id: int = 2577

    # Cotes Unibet Belgique (plateforme Kambi). 'ubbe' = Unibet Belgium.
    unibet_base_url: str = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
    unibet_lang: str = "fr_BE"
    unibet_market: str = "BE"

    # Cache et réseau
    cache_ttl_seconds: int = 120
    http_timeout: float = 20.0
    http_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    @property
    def tournament_ids(self) -> dict[str, int]:
        """Mappe le 'tour' (atp/wta) vers l'identifiant SofaScore."""
        return {"atp": self.rg_atp_tournament_id, "wta": self.rg_wta_tournament_id}


@lru_cache
def get_settings() -> Settings:
    return Settings()
