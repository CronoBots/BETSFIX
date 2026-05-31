"""Tests du modèle de marchés de sets (calibré) — fonctions pures."""

from app import set_markets as sm
from app.models import Match, Player, UnibetMarket, UnibetOdds, UnibetOutcome


def test_match_set_inversion():
    # set_prob redonne bien la proba de match
    for p in (0.55, 0.7, 0.85):
        s = sm.set_prob_from_match(p, 5)
        assert abs(sm.match_prob_from_set(s, 5) - p) < 1e-3


def test_at_least_one_set_calibrated():
    # favori net : prend quasi toujours un set (proche de 1, non corrigé)
    assert sm.at_least_one_set(0.9, 5) > 0.95
    # outsider : la calibration ABAISSE la proba brute (modèle IID sur-optimiste)
    s = sm.set_prob_from_match(0.25, 5)
    raw = 1 - (1 - s) ** 3
    assert sm.at_least_one_set(0.25, 5) < raw
    # monotonie
    assert sm.at_least_one_set(0.6, 5) > sm.at_least_one_set(0.4, 5)


def test_evaluate_set_markets():
    match = Match(id=1, tour="atp", ground_type="Red clay",
                  home=Player(id=100, name="Rafael Jodar"),
                  away=Player(id=200, name="Pablo Carreno Busta"))
    odds = UnibetOdds(match_id=1, matched=True, markets=[
        UnibetMarket(label="Pablo Carreno Busta remporte au moins un set", type="Oui/Non",
                     outcomes=[UnibetOutcome(label="Oui", odds=1.55),
                               UnibetOutcome(label="Non", odds=2.3)]),
        UnibetMarket(label="Set Handicap", type="Handicap", outcomes=[
            UnibetOutcome(label="Pablo Carreno Busta", odds=1.55, line=2.5),
            UnibetOutcome(label="Rafael Jodar", odds=2.3, line=-2.5)]),
    ])
    edges = sm.evaluate(match, odds, best_of=5, p_home=0.45, p_away=0.55)
    assert edges
    als = [e for e in edges if "au moins un set" in e.market]
    assert als and all(0 <= (e.model_probability or 0) <= 1 for e in als)
    sh = [e for e in edges if e.market == "Set Handicap"]
    assert sh and all(e.model_probability is not None for e in sh)


def test_evaluate_total_sets():
    match = Match(id=1, tour="atp", home=Player(id=100, name="A"), away=Player(id=200, name="B"))
    odds = UnibetOdds(match_id=1, matched=True, markets=[
        UnibetMarket(label="Nombre total de sets", type="Plus de/Moins de", outcomes=[
            UnibetOutcome(label="Plus de", odds=1.9, line=3.5),
            UnibetOutcome(label="Moins de", odds=1.9, line=3.5)])])
    edges = sm.evaluate(match, odds, best_of=5, p_home=0.45, p_away=0.55)
    over = next(e for e in edges if "plus" in e.selection.lower())
    assert 0.0 < (over.model_probability or 0) < 1.0   # P(4+ sets) cohérente


def test_evaluate_empty_without_probs():
    match = Match(id=1, tour="atp", home=Player(id=1), away=Player(id=2))
    assert sm.evaluate(match, UnibetOdds(match_id=1, matched=True), 5, None, 0.5) == []
