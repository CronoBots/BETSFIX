"""Tests du classement des endpoints /docs et du filtre 'à venir'."""

from datetime import datetime, timedelta, timezone

from app.main import _classify_tag, TAG_COTES, TAG_MODELE_ANALYSE, TAG_MODELE_SUIVI, \
    TAG_TENNIS_SRC, TAG_FOOT_SRC, TAG_BASKET_SRC, TAG_INTERFACE, TAG_META, TAG_FLASH
from app.routers.web import _is_upcoming


def test_classify_tag_natures():
    # Cotes = UNIBET uniquement ; les /odds SofaScore restent dans la source du sport
    assert _classify_tag("/matches/{id}/odds/unibet") == TAG_COTES
    assert _classify_tag("/foot/match/{id}/odds/unibet") == TAG_COTES
    assert _classify_tag("/matches/{id}/odds") == TAG_TENNIS_SRC
    assert _classify_tag("/foot/match/{id}/odds") == TAG_FOOT_SRC
    assert _classify_tag("/basket/match/{id}/odds") == TAG_BASKET_SRC
    # Modèle maison
    assert _classify_tag("/analysis/{id}") == TAG_MODELE_ANALYSE
    # Sources
    assert _classify_tag("/matches") == TAG_TENNIS_SRC
    assert _classify_tag("/players/{id}/rankings") == TAG_TENNIS_SRC
    assert _classify_tag("/foot/match/{id}/statistics") == TAG_FOOT_SRC
    assert _classify_tag("/basket/competition/{id}/standings") == TAG_BASKET_SRC
    # Pages HTML & méta
    assert _classify_tag("/foot") == TAG_INTERFACE
    assert _classify_tag("/basket") == TAG_INTERFACE
    assert _classify_tag("/app/match/{id}") == TAG_INTERFACE
    assert _classify_tag("/flashscore/{sport}/events") == TAG_FLASH
    assert _classify_tag("/api") == TAG_META


def test_is_upcoming():
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=3)).isoformat()
    past = (now - timedelta(hours=3)).isoformat()
    assert _is_upcoming({"start_time": future}) is True
    assert _is_upcoming({"start_time": past}) is False
    assert _is_upcoming({}) is True                       # heure inconnue -> on n'exclut pas
    assert _is_upcoming({"start_time": "pas-une-date"}) is True
    # datetime naïf traité comme UTC
    naive_future = (now + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert _is_upcoming({"start_time": naive_future}) is True


def test_bars_two_way():
    from app.web import bars_two_way
    # barres RÉPARTIES : home/away par source (model + implied dévig + public)
    b = bars_two_way(0.66, 0.6, (70, 30), "Home", "Away")
    assert b["home"] == "Home" and b["away"] == "Away" and b["bet"] == "Home"
    assert b["m_home"] == 0.66 and abs(b["m_away"] - 0.34) < 1e-9 and b["m_draw"] is None
    assert b["i_home"] == 0.6 and abs(b["i_away"] - 0.4) < 1e-9
    assert abs(b["pub_home"] - 0.7) < 1e-9 and abs(b["pub_away"] - 0.3) < 1e-9
    # favori = extérieur -> bet bascule côté away, mais home reste à GAUCHE
    b2 = bars_two_way(0.4, 0.45, (40, 60), "Home", "Away")
    assert b2["bet"] == "Away" and b2["m_home"] == 0.4
    assert bars_two_way(None, 0.5, None, "H", "A") == {}


def test_bars_foot():
    from app.web import bars_foot
    b = bars_foot((0.5, 0.3, 0.2), (0.45, 0.3, 0.25), (60, 40), "Croatie", "Belgique")
    assert b["home"] == "Croatie" and b["bet"] == "Croatie"
    assert b["m_home"] == 0.5 and b["m_draw"] == 0.3 and b["m_away"] == 0.2   # nul au milieu
    assert b["i_home"] == 0.45 and abs(b["pub_home"] - 0.6) < 1e-9
    # nul favori -> 'bet' = Match nul, mais les barres restent home/away
    bx = bars_foot((0.2, 0.5, 0.3), (0.25, 0.45, 0.3), (60, 40), "A", "B")
    assert bx["bet"] == "Match nul" and bx["m_home"] == 0.2 and bx["m_away"] == 0.3
    assert bars_foot(None, None, None, "A", "B") == {}


def test_votes_capture_draw():
    """Le vote du nul (voteX) doit être capté et inclus dans le total (foot 1X2)."""
    from app.providers.sofascore import SofaScoreProvider
    v = SofaScoreProvider._votes_from_data(1, {"vote": {"vote1": 5400, "voteX": 1300, "vote2": 3300}})
    assert (v.home_percent, v.draw_percent, v.away_percent) == (54.0, 13.0, 33.0)
    assert round(v.home_percent + v.draw_percent + v.away_percent) == 100
    # 2 issues (tennis/basket) : pas de nul
    v2 = SofaScoreProvider._votes_from_data(2, {"vote": {"vote1": 70, "vote2": 30}})
    assert v2.draw_percent is None and v2.home_percent == 70.0
