"""Tests de la logique de suivi prédictions/résultats (pures, sans réseau)."""

from app import tracking
from app.models import MatchAnalysis, Player, ValueBet


def _analysis(mid, home_prob, pick_side=None, pick_odds=None, pick_edge=0.05):
    vbs = []
    if pick_side:
        vbs = [ValueBet(side=pick_side, player="X", odds=pick_odds, edge=pick_edge,
                        recommended_stake_pct=1.0, is_value=True)]
        # l'autre côté présent mais non-value (comme en prod)
        other = "away" if pick_side == "home" else "home"
        vbs.append(ValueBet(side=other, player="Y", odds=2.0, is_value=False))
    return MatchAnalysis(
        match_id=mid, home=Player(name="Home"), away=Player(name="Away"),
        model_home_probability=home_prob, model_away_probability=1 - home_prob,
        confidence="moyenne", value_bets=vbs, unibet_matched=True,
    )


def test_upsert_and_settle_winning_pick():
    store = {}
    a = _analysis(1, 0.6, pick_side="home", pick_odds=2.5)
    assert tracking.upsert_prediction(store, a, "atp", "t0") is True
    assert store["1"]["value_pick"]["side"] == "home"
    # règle : home gagne -> pari gagnant -> pnl = 1.5
    assert tracking.settle(store, 1, "home", 30, "t1") is True
    assert store["1"]["result"]["value_pnl"] == 1.5
    # re-settle ne fait rien
    assert tracking.settle(store, 1, "home", 30, "t2") is False


def test_settle_losing_pick():
    store = {}
    tracking.upsert_prediction(store, _analysis(2, 0.55, "home", 2.0), "atp", "t0")
    tracking.settle(store, 2, "away", 28, "t1")
    assert store["2"]["result"]["value_pnl"] == -1.0


def test_no_settle_on_unfinished_or_unknown():
    store = {}
    tracking.upsert_prediction(store, _analysis(3, 0.5), "wta", "t0")
    assert tracking.settle(store, 3, None, None, "t1") is False
    assert tracking.settle(store, 999, "home", 20, "t1") is False  # inconnu


def test_report_metrics():
    store = {}
    # 3 matchs réglés : 2 favoris home gagnent, 1 perd ; 2 paris value (1 gagne 1 perd)
    tracking.upsert_prediction(store, _analysis(1, 0.7, "home", 2.0), "atp", "t0")
    tracking.upsert_prediction(store, _analysis(2, 0.65), "atp", "t0")
    tracking.upsert_prediction(store, _analysis(3, 0.6, "home", 3.0), "atp", "t0")
    tracking.settle(store, 1, "home", 30, "t1")  # value gagne +1.0
    tracking.settle(store, 2, "home", 28, "t1")  # favori correct
    tracking.settle(store, 3, "away", 35, "t1")  # value perd -1.0, modèle se trompe
    rep = tracking.report(store)
    assert rep["matchs_regles"] == 3
    assert rep["predictions_evaluees"] == 3
    assert rep["precision_modele"] == round(2 / 3, 3)
    assert rep["value_paris_regles"] == 2
    assert rep["value_pnl_unites"] == 0.0  # +1.0 -1.0
    assert rep["brier"] is not None


def test_render_dashboard_ok():
    # vide
    h = tracking.render_dashboard({}, tracking.report({}))
    assert "<!doctype html>" in h and "BetsFix" in h
    # peuplé
    store = {}
    tracking.upsert_prediction(store, _analysis(1, 0.7, "home", 2.0), "atp", "t0")
    tracking.settle(store, 1, "home", 30, "t1")
    h2 = tracking.render_dashboard(store, tracking.report(store))
    assert "✓" in h2  # le pari gagnant apparaît
