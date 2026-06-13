"""Tests PURS de app/clv.py (re-pricing d'un pari + calcul CLV) — aucun appel réseau."""

from app import clv

_MK = {"markets": [
    {"name": "Cotes du match", "outcomes": [
        {"label": "1", "odds": 1.85, "participant": "Spurs"},
        {"label": "2", "odds": 2.00, "participant": "Knicks"}]},
    {"name": "Nombre total de points", "outcomes": [
        {"label": "Plus de", "odds": 1.90, "line": 216.5},
        {"label": "Moins de", "odds": 1.92, "line": 216.5}]},
    {"name": "Handicap", "outcomes": [
        {"label": "Spurs -5", "odds": 1.86, "line": -5, "participant": "Spurs"},
        {"label": "Knicks 5", "odds": 1.96, "line": 5, "participant": "Knicks"}]},
    {"name": "Double chance", "outcomes": [
        {"label": "1X", "odds": 1.25}, {"label": "12", "odds": 1.30}, {"label": "X2", "odds": 1.40}]},
]}


def test_price_pick_marches_principaux():
    assert clv.price_pick("WIN HOME", "Spurs", "Knicks", _MK) == 1.85
    assert clv.price_pick("1X2 2", "Spurs", "Knicks", _MK) == 2.00
    assert clv.price_pick("OVER 216.5", "Spurs", "Knicks", _MK) == 1.90
    assert clv.price_pick("UNDER 216.5", "Spurs", "Knicks", _MK) == 1.92
    assert clv.price_pick("HCAP HOME -5", "Spurs", "Knicks", _MK) == 1.86
    assert clv.price_pick("DC 1X", "Spurs", "Knicks", _MK) == 1.25


def test_price_pick_introuvable():
    assert clv.price_pick("OVER 999", "Spurs", "Knicks", _MK) is None     # ligne absente
    assert clv.price_pick("SETSCORE 2 0", "A", "B", _MK) is None          # code non géré
    assert clv.price_pick("WIN HOME", "Spurs", "Knicks", None) is None
    assert clv.price_pick("", "Spurs", "Knicks", _MK) is None


def test_clv_pct():
    assert clv.clv_pct(2.10, 1.85) == 0.1351        # +13.5 % : on a battu la clôture
    assert clv.clv_pct(1.80, 2.00) == -0.1          # −10 % : pire que la clôture
    assert clv.clv_pct(2.0, 2.0) == 0.0
    assert clv.clv_pct(None, 1.9) is None
    assert clv.clv_pct(1.9, 1.0) is None            # clôture <= 1 invalide
