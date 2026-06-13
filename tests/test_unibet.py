"""Tests PURS de app/unibet.py (conversion cotes/lignes + normalisation, aucun appel réseau)."""

from app import unibet as ub


def test_odds_milli_vers_decimal():
    assert ub._odds(8000) == 8.0          # 8000 milli-cotes -> 8.0
    assert ub._odds(1980) == 1.98
    assert ub._odds(None) is None
    assert ub._odds("x") is None


def test_line_kambi():
    assert ub._line(2500) == 2.5          # total 2.5 buts
    assert ub._line(-1500) == -1.5        # handicap
    assert ub._line(None) is None


def test_event_row_normalise():
    ev = {"id": 123, "homeName": "A", "awayName": "B", "group": "Ligue X",
          "groupId": 9, "sport": "FOOTBALL", "start": "2026-06-13T19:00:00Z",
          "state": "NOT_STARTED", "nonLiveBoCount": 42}
    r = ub._event_row(ev)
    assert r["id"] == "123" and r["home"] == "A" and r["away"] == "B"
    assert r["league"] == "Ligue X" and r["markets_count"] == 42
