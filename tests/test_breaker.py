"""Tests du disjoncteur anti-403 du provider SofaScore (sans réseau)."""

import time

import pytest

from app.config import Settings
from app.providers.sofascore import ProviderError, SofaScoreProvider


def test_circuit_breaker_opens_and_resets():
    p = SofaScoreProvider(Settings())
    # circuit fermé au départ : aucune erreur
    p._breaker_guard()

    # 1er échec -> circuit ouvert -> guard lève 503 sans réseau
    p._breaker_trip()
    assert p._fail_count == 1
    with pytest.raises(ProviderError) as ei:
        p._breaker_guard()
    assert ei.value.status_code == 503

    # échec CONCURRENT (circuit déjà ouvert) -> ne ré-escalade pas (rafale = 1 seul)
    p._breaker_trip()
    assert p._fail_count == 1

    # succès -> circuit refermé
    p._breaker_reset()
    assert p._fail_count == 0
    p._breaker_guard()  # ne lève plus


def test_light_trip_does_not_escalate():
    p = SofaScoreProvider(Settings())
    # erreur transitoire (light) : pause courte, SANS incrémenter le compteur d'échecs
    p._breaker_trip(light=True)
    assert p._fail_count == 0
    with pytest.raises(ProviderError):
        p._breaker_guard()


def test_failcount_decays_when_pause_expires():
    p = SofaScoreProvider(Settings())
    p._breaker_trip()                          # compteur = 1
    p._open_until = 0.0                        # force l'expiration (échecs non concurrents)
    p._breaker_trip()                          # compteur = 2
    assert p._fail_count == 2
    p._open_until = time.monotonic() - 1       # simule une pause expirée
    p._breaker_guard()                         # à l'expiration, le compteur redescend
    assert p._fail_count == 1                  # 2 -> 1 (pas d'empilement vers une pause permanente)
    assert p._open_until == 0.0
