"""Constantes réseau partagées SofaScore / Unibet (Kambi) — UNE seule source de vérité.

Les bases URL viennent de la config (surchargeables via .env) ; foot.py, basket.py,
match_select.py et settle_analyst.py en gardaient chacun une copie en dur -> centralisé ici.
Module feuille (n'importe que la config) : importable partout sans risque de cycle.
"""

from app.config import get_settings

_S = get_settings()

SOFA_B = _S.sofascore_base_url
SOFA_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
          "Origin": "https://www.sofascore.com"}

UNIBET_B = _S.unibet_base_url
UNIBET_PARAMS = {"lang": _S.unibet_lang, "market": _S.unibet_market,
                 "client_id": "2", "channel_id": "1"}
UNIBET_PARAMS_EN = {**UNIBET_PARAMS, "lang": "en_GB"}   # noms anglais pour matcher l'Elo
UNIBET_H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Referer": "https://www.unibet.be/"}
