"""Tests du matching Unibet multi-sport (find_event_odds) et des champs calculés."""

from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.models import UnibetMarket, UnibetOdds, UnibetOutcome
from app.providers.unibet import UnibetProvider


def test_unibet_odds_computed_counts():
    odds = UnibetOdds(match_id=1, matched=True, markets=[
        UnibetMarket(label="Vainqueur", outcomes=[
            UnibetOutcome(label="A", odds=1.8), UnibetOutcome(label="B", odds=2.0)]),
        UnibetMarket(label="Total", outcomes=[
            UnibetOutcome(label="+", odds=1.9), UnibetOutcome(label="-", odds=1.9),
            UnibetOutcome(label="=", odds=10.0)]),
    ])
    d = odds.model_dump()
    assert d["markets_count"] == 2
    assert d["outcomes_count"] == 5
    assert UnibetOdds(match_id=2, matched=False).model_dump()["markets_count"] == 0


def _event(home, away, day_iso, kid=111):
    return {"event": {"id": kid, "homeName": home, "awayName": away,
                      "name": f"{home} - {away}", "start": day_iso}}


@pytest.mark.asyncio
async def test_find_event_odds_matches_and_date(monkeypatch):
    prov = UnibetProvider(get_settings())
    start = datetime(2026, 6, 14, 18, 0, tzinfo=timezone.utc)

    async def fake_events(sport):
        return [_event("Manchester United", "Arsenal", "2026-06-14T18:00:00Z")]

    async def fake_markets(kid, entry):
        return [UnibetMarket(label="Match", type="Match", outcomes=[
            UnibetOutcome(label="Manchester United", odds=1.7),
            UnibetOutcome(label="Arsenal", odds=2.1)])]

    monkeypatch.setattr(prov, "_events", fake_events)
    monkeypatch.setattr(prov, "_all_markets", fake_markets)

    # bon match -> trouvé, tous les marchés
    res = await prov.find_event_odds("football", "Manchester United", "Arsenal", 99, start)
    assert res.matched and res.markets_count == 1

    # nom générique seul (Newcastle United) -> pas de faux match
    res2 = await prov.find_event_odds("football", "Newcastle United", "Arsenal", 99, start)
    assert res2.matched is False

    # mauvaise date -> pas de match
    bad = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)
    res3 = await prov.find_event_odds("football", "Manchester United", "Arsenal", 99, bad)
    assert res3.matched is False

    await prov.aclose()
