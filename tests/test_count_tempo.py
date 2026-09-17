"""Rythme PROPRE au match pour les marchés comptés (user 2026-09-17, levier #1). La projection du RESTANT
d'un compté (corners/cartons/tirs/fautes/…) doit suivre le rythme OBSERVÉ mélangé au prior-ligue (Bayes),
au lieu d'un taux-ligue FIXE. AFFICHAGE/SIGNAUX seulement (jamais la sélection pré-match)."""

from app import analyses, live_pick


def test_blend_early_is_prior():
    """Tôt dans le match (peu de temps écoulé), le taux ~= prior-ligue (aucune donnée fiable encore)."""
    r = analyses._blend_count_rate90(cur=1, prior90=10.0, rem=0.92)   # f≈0.08
    assert abs(r - 10.0) < 1.5


def test_blend_late_follows_observed_pace():
    """Tard, un match à HAUT rythme (compte observé bien au-dessus du prior) tire le taux VERS l'observé."""
    fast = analyses._blend_count_rate90(cur=14, prior90=10.0, rem=0.1)   # 14 corners à ~81' -> pace élevé
    slow = analyses._blend_count_rate90(cur=3, prior90=10.0, rem=0.1)    # 3 corners à ~81' -> pace faible
    assert fast > 12.0 and slow < 8.0
    assert fast > slow


def test_blend_off_returns_prior(monkeypatch):
    """Flag OFF -> taux-ligue fixe d'avant (réversibilité stricte)."""
    monkeypatch.setattr(analyses, "COUNT_TEMPO_ON", False)
    assert analyses._blend_count_rate90(cur=14, prior90=10.0, rem=0.1) == 10.0


def test_foot_count_pct_high_tempo_raises_over():
    """`_foot_count_pct` : un match nerveux en corners donne un Over PLUS probable qu'avec le taux-ligue fixe."""
    info = {"metric": "corners", "scope": "match", "dir": "OVER", "side": None, "line": 10.5}
    vals = {"corners_h": 5, "corners_a": 3}          # 8 corners à la ~45' (rem 0.5) = pace 16/match >> 10 ligue
    rem = 0.5
    p_blend = analyses._foot_count_pct(info, vals, rem)
    assert p_blend is not None
    # même situation, modèle FIXE (flag off) -> Over moins probable
    analyses.COUNT_TEMPO_ON = False
    try:
        p_fixed = analyses._foot_count_pct(info, vals, rem)
    finally:
        analyses.COUNT_TEMPO_ON = True
    assert p_blend > p_fixed


def test_extra_count_pct_uses_blend():
    """`live_pick._extra_count_pct` (fautes) suit aussi le rythme du match (match nerveux -> Over ↑)."""
    info = {"metric": "fouls", "scope": "match", "dir": "OVER", "side": None, "line": 24.5}
    vals = {"fouls_h": 12, "fouls_a": 10}            # 22 fautes à la ~45' = pace 44/match >> 22 ligue
    rem = 0.5
    p_blend = live_pick._extra_count_pct(info, vals, rem)
    analyses.COUNT_TEMPO_ON = False
    try:
        p_fixed = live_pick._extra_count_pct(info, vals, rem)
    finally:
        analyses.COUNT_TEMPO_ON = True
    assert p_blend is not None and p_fixed is not None
    assert p_blend > p_fixed
