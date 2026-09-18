# -*- coding: utf-8 -*-
"""Watchdog santé des STATS live (anti-blackout silencieux) — incident 17→18/09/2026.

On teste la LOGIQUE de décision (état + sonde) sans IO réel : sonde/alerte/purge/reload sont injectées.
"""
import pytest

from app import live_pick


@pytest.fixture(autouse=True)
def _reset_wd():
    live_pick._STATS_WD.update({"blackout_since": None, "alerted_ep": False,
                                "healed_ep": False, "last_reload": None})
    yield


def _probe(n_run, n_stat):
    return lambda: (n_run, n_stat)


def test_healthy_when_some_stats_resets_state():
    live_pick._STATS_WD["blackout_since"] = 100.0
    assert live_pick.stats_watchdog(5, 2, now=1000.0) == "ok"
    assert live_pick._STATS_WD["blackout_since"] is None


def test_too_few_live_matches_inconclusive():
    assert live_pick.stats_watchdog(1, 0, now=1000.0) == "low"
    assert live_pick._STATS_WD["blackout_since"] is None


def test_blackout_opens_but_waits_before_acting():
    assert live_pick.stats_watchdog(4, 0, now=1000.0) == "watch"
    assert live_pick._STATS_WD["blackout_since"] == 1000.0
    # 5 min plus tard : toujours en observation (seuil alerte = 10 min)
    assert live_pick.stats_watchdog(4, 0, now=1000.0 + 5 * 60) == "watch"


def test_probe_no_coverage_stays_quiet():
    """0 stat côté API à la sonde = ligues non couvertes (nuit ligues mineures) -> pas d'alarme."""
    alerts = []
    live_pick._STATS_WD["blackout_since"] = 0.0
    act = live_pick.stats_watchdog(5, 0, now=11 * 60, probe=_probe(6, 0),
                                   alert=alerts.append, clear=lambda: None, reloader=lambda: True)
    assert act == "probe-nocover"
    assert alerts == []


def test_process_wedged_alerts_and_heals_then_reloads():
    alerts, cleared, reloaded = [], [], []
    live_pick._STATS_WD["blackout_since"] = 0.0
    # 1er passage après le seuil d'alerte : sonde prouve que l'API répond -> alerte + purge caches
    act1 = live_pick.stats_watchdog(5, 0, now=11 * 60, probe=_probe(6, 4),
                                    alert=alerts.append, clear=lambda: cleared.append(1),
                                    reloader=lambda: reloaded.append(1) or True)
    assert act1 == "heal"
    assert len(alerts) == 1 and cleared == [1] and reloaded == []
    # passage suivant, blackout persiste au-delà du seuil reload -> reload uvicorn auto
    act2 = live_pick.stats_watchdog(5, 0, now=15 * 60, probe=_probe(6, 4),
                                    alert=alerts.append, clear=lambda: cleared.append(1),
                                    reloader=lambda: reloaded.append(1) or True)
    assert act2 == "reload"
    assert reloaded == [1]
    assert live_pick._STATS_WD["last_reload"] == 15 * 60


def test_reload_cooldown_blocks_second_reload_within_hour():
    reloaded = []
    live_pick._STATS_WD.update({"blackout_since": 0.0, "alerted_ep": True, "healed_ep": True,
                                "last_reload": 15 * 60})
    act = live_pick.stats_watchdog(5, 0, now=15 * 60 + 20 * 60, probe=_probe(6, 4),
                                   alert=lambda _t: None, clear=lambda: None,
                                   reloader=lambda: reloaded.append(1) or True)
    assert act == "heal"                    # < 60 min depuis le dernier reload -> pas de nouveau reload
    assert reloaded == []


def test_recovery_after_heal_resets_episode():
    live_pick._STATS_WD.update({"blackout_since": 0.0, "alerted_ep": True, "healed_ep": True})
    assert live_pick.stats_watchdog(5, 3, now=20 * 60) == "ok"
    assert live_pick._STATS_WD["alerted_ep"] is False
    assert live_pick._STATS_WD["healed_ep"] is False


def test_flag_off_disables_watchdog(monkeypatch):
    monkeypatch.setattr(live_pick, "STATS_WD_ON", False)
    assert live_pick.stats_watchdog(9, 0, now=99 * 60) == "off"
