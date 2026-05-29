"""Provider Unibet Belgique (plateforme Kambi).

Unibet Belgique (unibet.be) tourne sur Kambi, qui expose une API d'offre
publique (sans clé). Le « offering » de l'enseigne belge est ``ubbe``.

Endpoints utilisés :
  - /listView/tennis.json                 (tous les matchs de tennis + cote principale)
  - /betoffer/event/{kambiEventId}.json   (tous les marchés d'un match)

Les cotes ne sont disponibles que pour les matchs **à venir / en cours**
(c'est l'offre du bookmaker), pas pour les matchs déjà joués.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.models import Match, UnibetMarket, UnibetOdds, UnibetOutcome


class UnibetProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Offre du bookmaker : cache un peu plus long que les data sportives.
        self._cache = TTLCache(max(settings.cache_ttl_seconds, 60))
        self._params = {
            "lang": settings.unibet_lang,
            "market": settings.unibet_market,
            "client_id": "2",
            "channel_id": "1",
        }
        self._client = httpx.AsyncClient(
            base_url=settings.unibet_base_url,
            timeout=settings.http_timeout,
            headers={
                "User-Agent": settings.http_user_agent,
                "Accept": "application/json",
                "Referer": "https://www.unibet.be/",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> dict:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            resp = await self._client.get(path, params=self._params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError:
            # Le bookmaker peut être indisponible : on ne casse pas l'analyse.
            return {}
        self._cache.set(path, data)
        return data

    # ------------------------------------------------------------ matching
    async def _tennis_events(self) -> list[dict]:
        data = await self._get("/listView/tennis.json")
        return data.get("events", []) or []

    async def find_odds(self, match: Match) -> UnibetOdds:
        """Retrouve les cotes Unibet pour un match SofaScore (par noms + date)."""
        target_home = _norm_name(match.home.name)
        target_away = _norm_name(match.away.name)
        match_day = match.start_time.date() if match.start_time else None

        best = None
        for entry in await self._tennis_events():
            ev = entry.get("event") or {}
            h = _norm_name(ev.get("homeName", ""))
            a = _norm_name(ev.get("awayName", ""))
            # Les deux joueurs doivent correspondre (peu importe le sens home/away).
            straight = _names_match(target_home, h) and _names_match(target_away, a)
            swapped = _names_match(target_home, a) and _names_match(target_away, h)
            if not (straight or swapped):
                continue
            # Désambiguïse par la date si on l'a.
            if match_day is not None:
                start = _parse_dt(ev.get("start"))
                if start is not None and start.date() != match_day:
                    continue
            best = (ev, entry, swapped)
            break

        if best is None:
            return UnibetOdds(match_id=match.id, matched=False)

        ev, entry, swapped = best
        kambi_id = ev.get("id")
        markets = await self._all_markets(kambi_id, entry)
        return UnibetOdds(
            match_id=match.id,
            matched=True,
            kambi_event_id=kambi_id,
            event_name=ev.get("name"),
            start_time=_parse_dt(ev.get("start")),
            markets=markets,
        )

    async def _all_markets(self, kambi_id, list_entry: dict) -> list[UnibetMarket]:
        """Tous les marchés d'un événement (sinon repli sur la cote principale)."""
        data = await self._get(f"/betoffer/event/{kambi_id}.json")
        offers = data.get("betOffers")
        if not offers:
            offers = list_entry.get("betOffers", []) or []
        return [_market(bo) for bo in offers]

    async def match_winner(self, match: Match) -> UnibetOdds:
        """Variante légère : uniquement le marché 'vainqueur du match'."""
        odds = await self.find_odds(match)
        if odds.matched:
            odds.markets = [m for m in odds.markets if (m.type or "").lower() == "match"][:1]
        return odds


# --------------------------------------------------------------- helpers
def _market(bo: dict) -> UnibetMarket:
    crit = (bo.get("criterion") or {}).get("label", "")
    btype = (bo.get("betOfferType") or {}).get("name")
    outcomes = []
    for o in bo.get("outcomes", []) or []:
        odds_milli = o.get("odds")
        decimal = round(odds_milli / 1000, 3) if isinstance(odds_milli, (int, float)) else None
        line = o.get("line")
        outcomes.append(
            UnibetOutcome(
                label=o.get("label", ""),
                participant=o.get("participant"),
                odds=decimal,
                fractional=o.get("oddsFractional"),
                line=round(line / 1000, 2) if isinstance(line, (int, float)) else None,
                implied_probability=round(1 / decimal, 4) if decimal else None,
            )
        )
    return UnibetMarket(label=crit, type=btype, outcomes=outcomes)


def _norm_name(name: str) -> set[str]:
    """Normalise un nom en jeu de tokens (minuscules, sans accents, sans initiales)."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    tokens = set()
    for tok in text.replace(".", " ").replace("-", " ").split():
        if len(tok) > 1:  # ignore les initiales ('C.')
            tokens.add(tok)
    return tokens


def _names_match(a: set[str], b: set[str]) -> bool:
    """Vrai si les deux noms partagent au moins un token significatif (nom de famille)."""
    return bool(a and b and a & b)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
