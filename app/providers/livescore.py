"""Provider LiveScore — source de secours (failover) quand SofaScore échoue.

LiveScore expose une API publique sans clé, indexée par DATE :
  /v1/api/app/date/tennis/{YYYYMMDD}/0   (offset tz 0 = UTC)

On l'utilise UNIQUEMENT en repli (liste des matchs + résultats) pour que la
plateforme ne soit jamais vide quand SofaScore renvoie un 403/timeout. Les IDs
LiveScore (Eid) diffèrent de ceux de SofaScore : ces matchs sont marqués
``source="livescore"`` et ne portent pas l'analyse complète (qui exige SofaScore).
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.models import Match, Player, Score

# Libellés de stage LiveScore -> tour
_TOUR_STAGE = {"atp": "men's singles", "wta": "women's singles"}
_STATUS = {"NS": "notstarted", "FT": "finished"}


class LiveScoreProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache = TTLCache(max(settings.cache_ttl_seconds, 120))
        self._client = httpx.AsyncClient(
            base_url="https://prod-public-api.livescore.com/v1/api/app",
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.http_user_agent, "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _date(self, yyyymmdd: str) -> dict:
        cached = self._cache.get(yyyymmdd)
        if cached is not None:
            return cached
        try:
            r = await self._client.get(f"/date/tennis/{yyyymmdd}/0")
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError:
            return {}
        self._cache.set(yyyymmdd, data)
        return data

    async def get_matches(self, tour: str, days: int = 3) -> list[Match]:
        """Matchs RG (single) sur quelques jours autour d'aujourd'hui (repli)."""
        want = _TOUR_STAGE.get(tour, "men's singles")
        today = datetime.now(timezone.utc).date()
        out: list[Match] = []
        seen: set = set()
        for off in range(-1, days):
            d = (today + timedelta(days=off)).strftime("%Y%m%d")
            data = await self._date(d)
            for stage in data.get("Stages", []) or []:
                name = (stage.get("Snm", "") + " " + stage.get("Cnm", "")).lower()
                if "french open" not in name or want not in name:
                    continue
                for ev in stage.get("Events", []) or []:
                    eid = ev.get("Eid")
                    if eid is None or eid in seen:
                        continue
                    seen.add(eid)
                    out.append(_normalize(tour, ev))
        out.sort(key=lambda m: (m.start_time or datetime.max.replace(tzinfo=timezone.utc)))
        return out

    async def find_result(self, tour: str, home: str, away: str, day_iso: str | None):
        """Cherche (winner, sets) d'un match par noms + date (repli pour le suivi)."""
        days = [datetime.now(timezone.utc).date().strftime("%Y%m%d")]
        if day_iso:
            try:
                days.insert(0, datetime.fromisoformat(day_iso).strftime("%Y%m%d"))
            except ValueError:
                pass
        th, ta = _tokens(home), _tokens(away)
        for tour_key in (tour,):
            for matches in [await self.get_matches(tour_key, days=2)]:
                for m in matches:
                    mh, ma = _tokens(m.home.name), _tokens(m.away.name)
                    if (th & mh and ta & ma) or (th & ma and ta & mh):
                        if m.winner in ("home", "away"):
                            swapped = not (th & mh)
                            winner = m.winner
                            if swapped:
                                winner = "away" if m.winner == "home" else "home"
                            return winner
        return None


# --------------------------------------------------------------- helpers
def _normalize(tour: str, ev: dict) -> Match:
    t1 = (ev.get("T1") or [{}])[0]
    t2 = (ev.get("T2") or [{}])[0]
    eps = ev.get("Eps", "")
    status = _STATUS.get(eps, "inprogress")
    tr1, tr2 = ev.get("Tr1"), ev.get("Tr2")
    winner = None
    if status == "finished" and tr1 is not None and tr2 is not None:
        winner = "home" if int(tr1) > int(tr2) else "away"
    return Match(
        id=int(ev.get("Eid") or 0),
        tour=tour,
        tournament="Roland Garros",
        status=status,
        start_time=_parse_esd(ev.get("Esd")),
        home=Player(id=_to_int(t1.get("ID")), name=t1.get("Nm", "")),
        away=Player(id=_to_int(t2.get("ID")), name=t2.get("Nm", "")),
        home_score=Score(sets_won=_to_int(tr1)),
        away_score=Score(sets_won=_to_int(tr2)),
        winner=winner,
        source="livescore",
    )


def _parse_esd(esd) -> datetime | None:
    if not esd:
        return None
    try:
        return datetime.strptime(str(esd), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _tokens(name: str) -> set:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return {t for t in text.replace(".", " ").replace("-", " ").split() if len(t) > 1}
