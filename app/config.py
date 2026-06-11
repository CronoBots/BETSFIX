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

    # Suivre TOUT le circuit principal (ATP/WTA), pas seulement Roland Garros.
    # True = l'agenda complet (continue après RG : gazon, dur…) -> atteint 100+ matchs.
    # False = Roland Garros uniquement (mode historique).
    track_full_tour: bool = True

    # Cotes Unibet Belgique (plateforme Kambi). 'ubbe' = Unibet Belgium.
    unibet_base_url: str = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
    unibet_lang: str = "fr_BE"
    unibet_market: str = "BE"

    # Proxy SofaScore (OPTIONNEL) : route UNIQUEMENT les requêtes SofaScore (curl_cffi) via ce
    # proxy, pour contourner un blocage IP Cloudflare. Mettre une IP RÉSIDENTIELLE (les datacenter
    # sont souvent déjà bloqués). Format : http://user:pass@host:port (ou socks5://...). Vide = direct.
    sofa_proxy: str = ""

    # Repli SofaScore via RapidAPI SportAPI7 (OPTIONNEL) : utilisé UNIQUEMENT quand SofaScore
    # renvoie 403/429 (rate-limit). Plafond/jour pour protéger le quota (Pro = 15 000/mois).
    rapidapi_key: str = ""
    rapidapi_host: str = "sportapi7.p.rapidapi.com"
    rapidapi_daily_cap: int = 400

    # Analyse rédigée par Claude (OPTIONNEL) : si la clé est vide, on garde l'analyse
    # gratuite générée localement (templatée). Mettre ANTHROPIC_API_KEY dans .env pour
    # passer en prose Claude (Haiku par défaut, ~bon marché en cache).
    anthropic_api_key: str = ""
    analysis_model: str = "claude-haiku-4-5-20251001"

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
