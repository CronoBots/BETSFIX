"""Tests du simulateur de match et de l'évaluation des marchés (sans réseau)."""

from app.markets import (
    calibrate_and_simulate,
    evaluate_markets,
    hold_prob,
    serve_win_pct,
    _simulate,
)
from app.models import (
    Match, Player, PlayerStatistics, UnibetMarket, UnibetOdds, UnibetOutcome,
)


def test_hold_prob_monotonic():
    assert abs(hold_prob(0.5) - 0.5) < 0.02
    assert hold_prob(0.65) > 0.80
    assert hold_prob(0.72) > 0.90
    assert hold_prob(0.60) < hold_prob(0.70)


def test_serve_win_pct():
    st = PlayerStatistics(player_id=1, first_serve_points_scored=70, first_serve_points_total=100,
                          second_serve_points_scored=30, second_serve_points_total=50)
    assert abs(serve_win_pct(st) - (100 / 150)) < 1e-9
    assert serve_win_pct(None) is None


def test_simulate_favorite_wins_more():
    # Serveur très dominant vs faible -> gagne la grande majorité
    sim = _simulate(0.72, 0.56, best_of=3, n=2000, seed=1)
    assert sim["win1"] / sim["n"] > 0.7
    # cohérence des longueurs
    assert len(sim["total_games"]) == 2000
    assert all(g >= 12 for g in sim["total_games"])  # au moins 2 sets de 6+ jeux


def test_calibration_hits_target():
    sim = calibrate_and_simulate(model_p_home=0.65, serve_level=0.63, best_of=5, seed=7)
    wp = sim["win1"] / sim["n"]
    assert abs(wp - 0.65) < 0.06  # calibration raisonnablement proche


def test_evaluate_markets_structure():
    match = Match(id=1, tour="atp",
                  home=Player(id=1, name="Carlos Alcaraz"),
                  away=Player(id=2, name="Alexander Zverev"))
    odds = UnibetOdds(match_id=1, matched=True, markets=[
        UnibetMarket(label="Nombre total de jeux", type="Plus de/Moins de", outcomes=[
            UnibetOutcome(label="Plus de", odds=1.9, line=38.5),
            UnibetOutcome(label="Moins de", odds=1.9, line=38.5)]),
        UnibetMarket(label="Cotes du match", type="Match", outcomes=[
            UnibetOutcome(label="Carlos Alcaraz", odds=1.5),
            UnibetOutcome(label="Alexander Zverev", odds=2.5)]),
    ])
    sim = calibrate_and_simulate(0.6, 0.63, 5, seed=1)
    edges = evaluate_markets(match, odds, sim)
    assert len(edges) == 4  # 2 marchés x 2 issues
    for e in edges:
        assert 0 <= (e.model_probability or 0) <= 1
        assert 0 <= (e.implied_probability or 0) <= 1
    # Les deux issues d'un marché O/U : probas modèle sommant ~1
    ou = [e for e in edges if e.market == "Nombre total de jeux"]
    assert abs(sum(e.model_probability for e in ou) - 1) < 0.01
