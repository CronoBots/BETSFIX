"""Ancre SHARP (Pinnacle dé-viggé) — servie par API-Football, SANS proxy (user 2026-09-10 : iProyal RETIRÉ).

Historique : l'ancre venait de Pinnacle via le proxy résidentiel iProyal (catalogue matchups ~40 Mo). Depuis
la migration API-Football, `apifootball.sharp_anchor` fournit la MÊME ancre (Pinnacle dé-viggé, même dé-vig
multiplicatif, formats `sp`/`smk` identiques — prouvé identique à iProyal au même instant, 2026-09-09) mais
SANS proxy → iProyal résilié → VPS → tunnel Cloudflare coupé. Ce module n'est donc plus qu'un DÉLÉGUÉ mince
vers API-Football. Le repli The Odds API (sans proxy) est assuré par les appelants (generate_analyses,
prioritaire sur ce délégué). Best-effort STRICT : toute panne → None.

API PUBLIQUE conservée (drop-in) : `sharp_probs`, `sharp_markets`, `refresh_catalog`.
"""

from __future__ import annotations

import time as _time

_af_anchor_cache: dict = {}     # (home,away,ko) -> (expire_ts, anchor|None) : sharp_probs ET sharp_markets
_AF_ANCHOR_TTL = 120            # partagent 1 seul fetch/match


def _af_anchor(home: str, away: str, ko: str | None):
    """Ancre sharp via API-Football (SANS proxy), cachée par match. None si indispo. Best-effort strict."""
    k = (home, away, ko or "")
    hit = _af_anchor_cache.get(k)
    if hit and hit[0] > _time.time():
        return hit[1]
    anchor = None
    try:
        from app import apifootball as _AF
        if _AF.configured():
            cl = _AF._client()
            try:
                anchor = _AF.sharp_anchor(cl, home, away, ko or "")
            finally:
                cl.close()
    except Exception:
        anchor = None
    _af_anchor_cache[k] = (_time.time() + _AF_ANCHOR_TTL, anchor)
    return anchor


def sharp_probs(home: str, away: str, sport: str, ko: str | None = None) -> dict | None:
    """Probas SHARP de-viggées du VAINQUEUR : {home, away, draw, margin}, alignées sur NOTRE home/away.
    Servi par API-Football (Pinnacle dé-viggé, sans proxy). None si match/cote introuvable."""
    a = _af_anchor(home, away, ko)
    return a.get("sp") if a else None


def sharp_markets(home: str, away: str, sport: str, ko: str | None = None) -> dict | None:
    """Probas SHARP de-viggées PAR MARCHÉ au-delà du 1X2 (totals/spreads), via API-Football. None si indispo."""
    a = _af_anchor(home, away, ko)
    return a.get("smk") if a else None


def refresh_catalog(sport: str = "foot") -> int:
    """NO-OP depuis le retrait d'iProyal (user 2026-09-10) : plus de catalogue Pinnacle 40 Mo à télécharger —
    l'ancre est résolue par match via API-Football. Conservé (appelé au scan du matin) pour compat : renvoie 0."""
    return 0
