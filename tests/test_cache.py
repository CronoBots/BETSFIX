"""Tests du cache : TTL, lecture du périmé (stale), persistance, TTL par type."""

from app.cache import TTLCache
from app.providers.sofascore import _ttl_for


def test_fresh_then_stale():
    c = TTLCache(ttl_seconds=0.01)
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}
    import time
    time.sleep(0.02)
    assert c.get("k") is None          # expiré
    assert c.get_stale("k") == {"v": 1}  # mais toujours dispo en secours


def test_per_entry_ttl():
    c = TTLCache(ttl_seconds=0.01)
    c.set("long", 42, ttl=100)
    import time
    time.sleep(0.02)
    assert c.get("long") == 42  # TTL spécifique respecté


def test_persistence(tmp_path):
    p = str(tmp_path / "cache.json")
    c1 = TTLCache(ttl_seconds=100, persist_path=p)
    c1._last_save = 0  # force l'écriture immédiate
    c1.set("k", {"x": 9})
    c2 = TTLCache(ttl_seconds=100, persist_path=p)  # nouveau cache, même fichier
    assert c2.get("k") == {"x": 9}


def test_ttl_for_tiers():
    assert _ttl_for("/unique-tournament/2480/seasons") == 6 * 3600
    assert _ttl_for("/team/123/rankings") == 3600
    assert _ttl_for("/event/1/h2h") == 1800
    # events / live -> défaut (None)
    assert _ttl_for("/unique-tournament/2480/season/5/events/last/0") is None


def test_eviction_drops_dead_and_caps_size():
    from app.cache import TTLCache
    # grâce courte : les entrées périmées au-delà sont supprimées
    c = TTLCache(ttl_seconds=0.01, max_entries=3, stale_grace=0.0)
    for i in range(10):
        c.set(f"k{i}", i)
    import time
    time.sleep(0.02)
    c.set("fresh", 1)          # déclenche l'éviction
    # au plus max_entries entrées restent, et les mortes (grâce 0) sont parties
    assert len(c._store) <= 3
