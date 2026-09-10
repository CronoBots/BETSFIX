"""Tests de app/pinnacle.py — délégation de l'ancre sharp à API-Football (iProyal retiré 2026-09-10)."""

from app import pinnacle as pin


def test_sharp_probs_delegue_a_apifootball(monkeypatch):
    # L'ancre est servie par API-Football (`_af_anchor`) : sharp_probs renvoie sa clé `sp`.
    monkeypatch.setattr(pin, "_af_anchor",
                        lambda h, a, ko: {"sp": {"home": 0.9, "draw": 0.06, "away": 0.04, "margin": 0.03},
                                          "smk": {"totals": {2.5: 0.55}, "spreads": {}}})
    sp = pin.sharp_probs("A", "B", "foot", "2026-09-09T18:00:00Z")
    assert sp == {"home": 0.9, "draw": 0.06, "away": 0.04, "margin": 0.03}
    assert pin.sharp_markets("A", "B", "foot", "2026-09-09T18:00:00Z")["totals"] == {2.5: 0.55}


def test_sharp_probs_none_si_pas_ancre(monkeypatch):
    # API-Football ne résout pas le match -> None (jamais d'erreur).
    monkeypatch.setattr(pin, "_af_anchor", lambda h, a, ko: None)
    assert pin.sharp_probs("X", "Y", "foot") is None
    assert pin.sharp_markets("X", "Y", "foot") is None


def test_refresh_catalog_noop():
    # Plus de catalogue Pinnacle 40 Mo (ancre via API-Football sans proxy) -> no-op.
    assert pin.refresh_catalog("foot") == 0
