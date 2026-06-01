"""Tests de la logique de suivi prédictions/résultats (pures, sans réseau)."""

from app import tracking
from app.models import AnalysisFactor, MatchAnalysis, Player, ValueBet


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


def _analysis_with_odds(mid, home_prob, home_odds, away_odds):
    """Analyse portant les cotes des deux côtés (comme en prod, value ou non)."""
    a = _analysis(mid, home_prob)
    a.value_bets = [
        ValueBet(side="home", player="Home", odds=home_odds, is_value=False),
        ValueBet(side="away", player="Away", odds=away_odds, is_value=False),
    ]
    return a


def test_clv_open_vs_close():
    store = {}
    # Premier log : cote d'ouverture du favori home = 2.0
    tracking.upsert_prediction(store, _analysis_with_odds(1, 0.6, 2.0, 2.0), "atp", "t0")
    assert store["1"]["open_home_odds"] == 2.0
    # Rafraîchissement : la cote home se raccourcit à 1.8 (clôture) -> on a battu la clôture
    tracking.upsert_prediction(store, _analysis_with_odds(1, 0.6, 1.8, 2.2), "atp", "t1")
    assert store["1"]["open_home_odds"] == 2.0          # ouverture figée
    assert store["1"]["unibet_home_odds"] == 1.8        # clôture = dernier log
    clv = tracking.clv_pct(store["1"])
    assert clv is not None and clv > 0                   # 2.0/1.8 - 1 > 0


def test_report_market_baseline_and_clv():
    store = {}
    # Favori home @1.5 (implicite ~0.6) qui gagne ; ouverture 1.7 -> CLV positif
    tracking.upsert_prediction(store, _analysis_with_odds(1, 0.65, 1.7, 2.6), "atp", "t0")
    tracking.upsert_prediction(store, _analysis_with_odds(1, 0.65, 1.5, 2.6), "atp", "t1")
    tracking.settle(store, 1, "home", 30, "t2")
    rep = tracking.report(store)
    assert rep["brier_marche"] is not None
    assert rep["log_loss_marche"] is not None
    assert rep["bat_le_marche"] in (True, False)
    assert rep["clv_evalue"] == 1
    assert rep["clv_moyen"] is not None


def test_calibration_table():
    store = {}
    tracking.upsert_prediction(store, _analysis(1, 0.7), "atp", "t0")
    tracking.upsert_prediction(store, _analysis(2, 0.72), "atp", "t0")
    tracking.settle(store, 1, "home", 30, "t1")   # favori (home) gagne
    tracking.settle(store, 2, "away", 30, "t1")   # favori (home) perd
    settled = [r for r in store.values() if r.get("result")]
    table = tracking.calibration_table(settled)
    assert table and table[0]["n"] == 2
    assert 0.0 <= table[0]["reel"] <= 1.0
    assert table[0]["reel"] == 0.5                 # 1 favori sur 2 gagne


def _analysis_rich(mid, home_prob, factors, surface="Red clay", tour_conf="moyenne"):
    """Analyse portant des facteurs + surface (comme en prod enrichie)."""
    a = MatchAnalysis(
        match_id=mid, home=Player(name="Home"), away=Player(name="Away"),
        ground_type=surface,
        model_home_probability=home_prob, model_away_probability=1 - home_prob,
        confidence=tour_conf,
        factors=[AnalysisFactor(name=n, home=h, away=1 - h, weight=w)
                 for n, h, w in factors],
        unibet_matched=True,
    )
    return a


def test_upsert_stores_factors_and_surface():
    store = {}
    a = _analysis_rich(1, 0.6, [("elo", 0.7, 0.45), ("classement", 0.55, 0.20)])
    tracking.upsert_prediction(store, a, "atp", "t0")
    rec = store["1"]
    assert rec["surface"] == "Red clay"
    assert {f["name"] for f in rec["factors"]} == {"elo", "classement"}
    assert rec["factors"][0]["weight"] == 0.45


def test_factor_breakdown_ranks_by_brier():
    store = {}
    # elo prédit bien (home gagne, elo dit 0.8), classement prédit mal (dit 0.3)
    for mid in (1, 2):
        tracking.upsert_prediction(
            store, _analysis_rich(mid, 0.6, [("elo", 0.8, 0.45), ("classement", 0.3, 0.20)]),
            "atp", "t0")
        tracking.settle(store, mid, "home", 30, "t1")  # home gagne -> elo a raison
    pred = [r for r in store.values() if r.get("result")]
    fb = tracking.factor_breakdown(pred)
    names = [f["name"] for f in fb]
    assert names[0] == "elo"            # meilleur Brier en tête
    assert names[-1] == "classement"    # pire en dernier
    elo_row = next(f for f in fb if f["name"] == "elo")
    assert elo_row["precision"] == 1.0  # elo a toujours désigné le bon


def test_report_has_breakdowns_and_surconfiance():
    store = {}
    tracking.upsert_prediction(
        store, _analysis_rich(1, 0.7, [("elo", 0.7, 0.45)], tour_conf="élevée"), "atp", "t0")
    tracking.upsert_prediction(
        store, _analysis_rich(2, 0.7, [("elo", 0.7, 0.45)], tour_conf="élevée"), "wta", "t0")
    tracking.settle(store, 1, "home", 30, "t1")  # favori gagne
    tracking.settle(store, 2, "away", 30, "t1")  # favori perd
    rep = tracking.report(store)
    # prédit 70% au favori, réel 50% -> surconfiance +0.2
    assert rep["surconfiance"] == 0.2
    assert any(b["label"] == "élevée" for b in rep["par_confiance"])
    assert {b["label"] for b in rep["par_tour"]} == {"ATP", "WTA"}
    assert any(b["label"] == "terre" for b in rep["par_surface"])
    assert rep["par_facteur"][0]["name"] == "elo"


def test_render_dashboard_ok():
    # vide
    h = tracking.render_dashboard({}, tracking.report({}))
    assert "<!doctype html>" in h and "BETSFIX" in h
    # peuplé
    store = {}
    tracking.upsert_prediction(store, _analysis(1, 0.7, "home", 2.0), "atp", "t0")
    tracking.settle(store, 1, "home", 30, "t1")
    h2 = tracking.render_dashboard(store, tracking.report(store))
    assert "✓" in h2  # le pari gagnant apparaît
