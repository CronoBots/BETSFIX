"""Tests du modèle de tendances de service (aces) — fonctions pures, sans réseau."""

from app import tendencies as t
from app.models import Match, Player


def _rec(rate, games, rate_clay=None, games_clay=0):
    return {"name": "X", "ace_rate": rate, "ace_games": games,
            "ace_rate_clay": rate_clay, "ace_games_clay": games_clay}


def test_ace_rate_uses_clay_when_enough_data():
    rec = _rec(0.5, 1000, rate_clay=0.3, games_clay=200)
    assert t.ace_rate(rec, "Red clay") == 0.3        # terre + assez de jeux
    assert t.ace_rate(rec, "Hard") == 0.5            # hors terre -> global


def test_ace_rate_falls_back_when_clay_thin():
    rec = _rec(0.5, 1000, rate_clay=0.3, games_clay=10)  # trop peu de terre
    assert t.ace_rate(rec, "Red clay") == 0.5
    assert t.ace_rate(None, "Hard") is None


def test_ace_rate_none_when_too_few_games():
    # Joueur vu sur trop peu de jeux -> tendance non fiable -> None
    rec = _rec(1.38, 24, rate_clay=None, games_clay=0)
    assert t.ace_rate(rec, "Red clay") is None
    assert t.ace_rate(rec, "Hard") is None


def test_expected_service_games_format_and_closeness():
    # best_of=5 > best_of=3 ; match serré (0.5) > match déséquilibré (0.9)
    assert t.expected_service_games(5, 0.5) > t.expected_service_games(3, 0.5)
    assert t.expected_service_games(3, 0.5) > t.expected_service_games(3, 0.95)


def test_expected_aces():
    assert t.expected_aces(0.5, 12) == 6.0
    assert t.expected_aces(None, 12) is None


def test_prob_over_poisson_monotonic():
    # plus la ligne est haute, moins c'est probable ; bornes [0,1]
    p_low = t.prob_over(5.5, 8.0)
    p_high = t.prob_over(11.5, 8.0)
    assert 0.0 <= p_high < p_low <= 1.0
    assert t.prob_over(3.5, 0.0) == 0.0              # lam=0 -> jamais d'ace
    assert t.prob_over(3.5, None) is None


def test_for_match_builds_summary():
    store = {"100": _rec(0.6, 500), "200": _rec(0.2, 500)}
    m = Match(id=1, tour="atp", ground_type="Red clay",
              home=Player(id=100, name="Big Server"), away=Player(id=200, name="Pusher"))
    out = t.for_match(m, best_of=5, fav_prob=0.6, store=store)
    assert out["home_rate"] == 0.6 and out["away_rate"] == 0.2
    assert out["home_exp"] > out["away_exp"]          # le gros serveur en fait plus
    assert out["service_games"] > 0


def test_for_match_none_when_no_data():
    m = Match(id=1, tour="atp", home=Player(id=1), away=Player(id=2))
    assert t.for_match(m, best_of=5, fav_prob=0.5, store={}) is None