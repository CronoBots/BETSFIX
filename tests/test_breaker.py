"""Tests du disjoncteur anti-403 du provider SofaScore (sans réseau)."""

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

    # échec supplémentaire -> back-off plus long (compteur croît)
    open1 = p._open_until
    p._breaker_trip()
    assert p._fail_count == 2
    assert p._open_until >= open1  # délai au moins aussi long

    # succès -> circuit refermé
    p._breaker_reset()
    assert p._fail_count == 0
    p._breaker_guard()  # ne lève plus
