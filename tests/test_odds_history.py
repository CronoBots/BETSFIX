"""Tests du suivi des variations de cote (odds_history) : cadence + calcul de dérive."""

from datetime import datetime, timedelta, timezone

import pytest

from app import odds_history as oh


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Isole le stockage dans un dossier temporaire (jamais les vraies données)."""
    monkeypatch.setattr(oh, "_DIR", str(tmp_path))


def _ev(start_dt, odds=(2.0, 3.4, 3.5), home="A", away="B"):
    return {"id": 1, "home": home, "away": away, "comp": "L", "start": start_dt.isoformat(), "odds": odds}


def test_due_first_snapshot_always():
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    assert oh._due([], now + timedelta(hours=5), now) is True


def test_due_hourly_when_far():
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=5)            # loin -> intervalle 60 min
    snaps = [{"ts": (now - timedelta(minutes=30)).isoformat(), "o1": 2.0}]
    assert oh._due(snaps, start, now) is False   # 30 min < 60 min
    snaps = [{"ts": (now - timedelta(minutes=61)).isoformat(), "o1": 2.0}]
    assert oh._due(snaps, start, now) is True    # > 60 min


def test_due_tightens_in_last_hour():
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(minutes=30)          # dernière heure -> intervalle 10 min
    snaps = [{"ts": (now - timedelta(minutes=11)).isoformat(), "o1": 2.0}]
    assert oh._due(snaps, start, now) is True
    snaps = [{"ts": (now - timedelta(minutes=5)).isoformat(), "o1": 2.0}]
    assert oh._due(snaps, start, now) is False


def test_due_frozen_after_kickoff():
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=1)           # match commencé
    snaps = [{"ts": (now - timedelta(hours=2)).isoformat(), "o1": 2.0}]
    assert oh._due(snaps, start, now) is False


def test_record_respects_interval():
    t0 = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    start = t0 + timedelta(hours=6)
    assert oh.record_all("foot", [_ev(start)], now=t0) == 1
    # 30 min plus tard, loin du match -> pas de nouveau relevé
    assert oh.record_all("foot", [_ev(start)], now=t0 + timedelta(minutes=30)) == 0
    # 61 min plus tard -> relevé
    assert oh.record_all("foot", [_ev(start)], now=t0 + timedelta(minutes=61)) == 1


def test_movement_computes_drift():
    t0 = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    start = t0 + timedelta(hours=6)
    oh.record_all("foot", [_ev(start, odds=(2.00, 3.4, 3.5))], now=t0)
    oh.record_all("foot", [_ev(start, odds=(1.80, 3.4, 4.2))], now=t0 + timedelta(minutes=61))
    mv = oh.movement("foot", "A", "B", now=t0 + timedelta(minutes=70))
    assert mv is not None
    assert mv["n"] == 2 and mv["closed"] is False
    home = mv["legs"]["home"]
    assert home["open"] == 2.00 and home["now"] == 1.80
    assert home["dir"] == "down" and home["pct"] == -10.0    # steam : cote raccourcie
    away = mv["legs"]["away"]
    assert away["dir"] == "up" and away["pct"] == 20.0       # drift : cote allongée


def test_movement_none_with_single_snapshot():
    t0 = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    oh.record_all("foot", [_ev(t0 + timedelta(hours=6))], now=t0)
    assert oh.movement("foot", "A", "B", now=t0) is None


def test_prune_drops_old_matches():
    t0 = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    old_start = t0 - timedelta(hours=72)         # commencé il y a 72 h -> purgé
    oh.record_all("foot", [_ev(old_start, home="OLD", away="X")], now=old_start - timedelta(hours=1))
    # un nouveau record déclenche la purge
    oh.record_all("foot", [_ev(t0 + timedelta(hours=2), home="NEW", away="Y")], now=t0)
    data = oh._load("foot")
    assert oh._key("NEW", "Y") in data
    assert oh._key("OLD", "X") not in data
