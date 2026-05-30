"""Tests du moteur de marchés d'aces (fonctions pures, sans réseau)."""

from app import ace_markets as am
from app.models import Match, Player, UnibetMarket, UnibetOdds, UnibetOutcome


def test_lambda_from_line_inverts_poisson():
    # à la ligne médiane (P_over ~ 0.5), lambda doit être proche de la ligne
    lam = am.lambda_from_line(16.5, 0.5)
    assert 15.0 < lam < 18.0
    # P_over plus élevé -> lambda plus grand
    assert am.lambda_from_line(16.5, 0.7) > am.lambda_from_line(16.5, 0.3)


def test_split_lambda_follows_tendency():
    lh, la = am.split_lambda(20.0, 0.6, 0.2)        # home sert 3x plus d'aces
    assert round(lh + la, 6) == 20.0
    assert lh > la and abs(lh - 15.0) < 0.01        # 0.6/0.8 * 20 = 15
    # tendance manquante -> 50/50
    assert am.split_lambda(20.0, None, 0.2) == (10.0, 10.0)


def test_most_aces_probs_sum_to_one():
    ph, pe, pa = am.most_aces_probs(9.0, 6.0)
    assert abs(ph + pe + pa - 1.0) < 1e-6
    assert ph > pa                                   # le plus gros lambda gagne plus souvent


def _odds(match_id=1):
    """UnibetOdds synthétique avec les 3 types de marchés d'aces."""
    return UnibetOdds(match_id=match_id, matched=True, markets=[
        UnibetMarket(label="Total Aces", type="Plus de/Moins de", outcomes=[
            UnibetOutcome(label="Plus de", odds=1.86, line=16.5, implied_probability=round(1/1.86, 4)),
            UnibetOutcome(label="Moins de", odds=1.84, line=16.5, implied_probability=round(1/1.84, 4))]),
        UnibetMarket(label="Nombre total d'aces - Casper Ruud", type="Plus de/Moins de", outcomes=[
            UnibetOutcome(label="Plus de", odds=1.9, line=9.5, implied_probability=round(1/1.9, 4)),
            UnibetOutcome(label="Moins de", odds=1.79, line=9.5, implied_probability=round(1/1.79, 4))]),
        UnibetMarket(label="Le plus d'aces", type="Match", outcomes=[
            UnibetOutcome(label="1", odds=1.5), UnibetOutcome(label="X", odds=12.0),
            UnibetOutcome(label="2", odds=2.9)]),
    ])


def _match():
    return Match(id=1, tour="atp", ground_type="Red clay",
                 home=Player(id=100, name="Casper Ruud"),
                 away=Player(id=200, name="Joao Fonseca"))


def test_evaluate_produces_edges_and_total_is_info_only():
    edges = am.evaluate(_match(), _odds(), best_of=5,
                        rate_home=0.55, rate_away=0.40, fav_prob=0.6)
    assert edges
    # Le marché Total Aces est calé sur le book -> jamais signalé comme value
    total = [e for e in edges if e.market == "Total Aces"]
    assert total and all(e.is_value is False for e in total)
    # Les probas du joueur cité sont calculées (lambda issu de la répartition)
    ruud = [e for e in edges if "Ruud" in e.market]
    assert ruud and all(e.model_probability is not None for e in ruud)
    # "Le plus d'aces" : 3 issues évaluées, probas valides
    most = [e for e in edges if e.market == "Le plus d'aces"]
    assert len(most) == 3
    assert all(0.0 <= (e.model_probability or 0) <= 1.0 for e in most)


def test_evaluate_empty_without_tendency_and_without_line():
    # pas de ligne Total Aces ET pas de tendance -> rien à évaluer
    odds = UnibetOdds(match_id=1, matched=True, markets=[
        UnibetMarket(label="Le plus d'aces", type="Match", outcomes=[
            UnibetOutcome(label="1", odds=1.5), UnibetOutcome(label="2", odds=2.5)])])
    assert am.evaluate(_match(), odds, best_of=5,
                       rate_home=None, rate_away=None, fav_prob=0.5) == []
