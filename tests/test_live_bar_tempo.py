"""Barre « chance live » des paris de BUTS (Confiance/Value) devenue TEMPO-AWARE (user 2026-09-17) : sa
composante MODÈLE utilise le taux de buts/90 LIVE du match (buts observés + pression de tirs, via le cache
rempli par l'observe loop) au lieu du taux-ligue fixe 2.7. PURE AFFICHAGE (ni sélection, ni ROI, ni règlement)."""

import time

from app import analyses, live_pick


def _clear():
    live_pick._G90_CACHE.clear()


def test_cache_hit_and_cold_fallback():
    _clear()
    live_pick._G90_CACHE[live_pick._g90_key("Lyon", "Rennes")] = (time.time(), 4.2)
    assert live_pick.match_goals90_cached("Lyon", "Rennes", 1, 0, 60) == 4.2      # frais -> cache
    # froid -> repli score-tempo (buts observés + prior + surcote-fin), jamais None sur un score lisible
    r = live_pick.match_goals90_cached("Aaa", "Bbb", 1, 1, 60)
    assert isinstance(r, float) and r > 0


def test_cache_expires():
    _clear()
    live_pick._G90_CACHE[live_pick._g90_key("Lyon", "Rennes")] = (time.time() - 999, 9.9)
    # périmé -> ignore le 9.9, repli score-tempo (bien < 9.9)
    assert live_pick.match_goals90_cached("Lyon", "Rennes", 0, 0, 20) < 5.0


def test_live_bar_reflects_match_tempo():
    """Deux 0-0 à la 60' : un match à HAUT rythme de buts (g90 élevé) rend « Plus de 2.5 buts » PLUS probable
    qu'un match fermé -> la barre chance live n'est plus aveugle au tempo (avant : identique, taux-ligue fixe)."""
    _clear()
    args = ("foot", "Plus de 2.5 buts", "", "Lyon", "Rennes", 0, 0)
    kw = dict(minute=60, ref_pct=50.0)

    live_pick._G90_CACHE[live_pick._g90_key("Lyon", "Rennes")] = (time.time(), 5.0)   # match ouvert
    hot = analyses.live_prob(*args, **kw)["pct"]

    live_pick._G90_CACHE[live_pick._g90_key("Lyon", "Rennes")] = (time.time(), 1.2)   # match fermé
    cold = analyses.live_prob(*args, **kw)["pct"]

    assert hot > cold, f"Over 2.5 devrait être plus probable en match ouvert ({hot}) qu'en match fermé ({cold})"


def test_tempo_off_reverts_to_league_rate(monkeypatch):
    """TEMPO_BLEND_ON=False -> _match_goals90 renvoie None -> le modèle retombe sur le taux-ligue fixe (revert)."""
    _clear()
    monkeypatch.setattr(live_pick, "TEMPO_BLEND_ON", False)
    assert live_pick.match_goals90_cached("Lyon", "Rennes", 1, 0, 60) is None
