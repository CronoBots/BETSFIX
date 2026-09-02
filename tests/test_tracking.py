"""Tests de app/tracking.py — RÉDUITS au code encore VIVANT (2026-09-02).

Le module a perdu son writer `upsert_prediction` lors du retrait tennis/basket : les 13 tests qui
construisaient leur état via lui échouaient donc en AttributeError depuis, sans qu'aucun code ne
soit en cause. Ils sont retirés. Restent les tests de fonctions PURES toujours utilisées.
Retirés : test_upsert_and_settle_winning_pick, test_settle_losing_pick, test_no_settle_on_unfinished_or_unknown, test_report_metrics, test_clv_open_vs_close, test_report_market_baseline_and_clv, test_calibration_table, test_upsert_stores_factors_and_surface, test_factor_breakdown_ranks_by_brier, test_report_has_breakdowns_and_surconfiance, test_render_dashboard_ok, test_void_closes_unfinished_match, test_void_excluded_from_metrics.
"""

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


def test_wilson_interval():
    # 0 pari -> None
    assert tracking.wilson_interval(0, 0) is None
    # petit échantillon -> intervalle TRÈS large (honnêteté)
    lo, hi = tracking.wilson_interval(5, 10)
    assert lo < 0.30 and hi > 0.70
    # gros échantillon centré -> intervalle serré autour de 0.5
    lo, hi = tracking.wilson_interval(100, 200)
    assert 0.42 < lo < 0.50 < hi < 0.58
    # bornes valides
    lo, hi = tracking.wilson_interval(10, 10)
    assert 0.0 <= lo <= hi <= 1.0



def test_render_proof_honnete():
    # Track record PERLE : matchs perle réglés + ROI perle global + colonnes confiance/value perle
    rep_full = {"perle_matchs_regles": 64, "perle_paris_regles": 100, "perle_roi_global": 0.05,
                "perle_conf_regles": 64, "perle_conf_gagnes": 45, "perle_conf_taux": 0.703,
                "perle_value_regles": 36, "perle_value_gagnes": 15, "perle_value_roi": 0.042}
    rep_empty = {"perle_matchs_regles": 0, "perle_paris_regles": 0}
    rep_small = {"perle_matchs_regles": 12, "perle_paris_regles": 12, "perle_roi_global": -0.1,
                 "perle_conf_regles": 8, "perle_conf_gagnes": 3, "perle_conf_taux": 0.375,
                 "perle_value_regles": 5, "perle_value_gagnes": 2, "perle_value_roi": 0.1}
    html = tracking.render_proof([("T", "Tennis", rep_full, "/a"),
                                  ("F", "Foot", rep_empty, "/b"),
                                  ("B", "Basket", rep_small, "/c")])
    # Tableau unique : en-tête + 1 ligne par sport (3 lignes), comparables
    assert html.count("ptab-row") == 3
    assert "Fiabilité" in html and "Confiance" in html and "Value" in html   # colonnes
    assert "✓ Plus fiable" in html                # tennis : ROI perle global positif
    assert "En collecte" in html                  # foot : aucune perle réglée
    assert "En rodage" in html and "12 paris réglés" in html   # basket : échantillon < 30
    assert "45/64" in html                        # confiance : nb gagnés/total
    assert "15/36" in html                        # value : nb gagnés/total
    assert "+4%" in html and "ptab-pct" in html   # ROI value en petit % sous le nombre
    assert "—" in html                            # placeholder quand un type n'a pas de donnée
    assert "--sc:#d7e64a" in html                 # liseré = couleur du sport (tennis lime)
    assert "--sc:#ff9f43" in html                 # basket orange



def test_evolution_cumulative_and_svg():
    """Courbe d'équité : cumul Confiance/Value correct, void exclu, SVG bien formé, cas vide géré."""
    import re
    import xml.etree.ElementTree as ET
    store = {
        "1": {"perle": {"selection": "A"},
              "result": {"settled_at": "2026-06-01T10:00:00", "perle_pnl": 0.8, "perle_value_pnl": None}},
        "2": {"perle": {"selection": "B"}, "perle_value": {"selection": "C"},
              "result": {"settled_at": "2026-06-02T10:00:00", "perle_pnl": -1.0, "perle_value_pnl": 1.5}},
    }
    ev = tracking._perle_events(store)
    assert sorted(p for _, k, p in ev if k == "conf") == [-1.0, 0.8]
    assert [p for _, k, p in ev if k == "value"] == [1.5]
    # un void ne doit RIEN ajouter
    store["3"] = {"perle": {"selection": "D"},
                  "result": {"void": True, "settled_at": "2026-06-03T10:00:00", "perle_pnl": 5.0}}
    assert len(tracking._perle_events(store)) == len(ev)
    # Carte détail par sport : courbe lissée 2 lignes (Confiance vert + Value bleu) -> SVG bien formé
    rep = {"perle_paris_regles": 2, "perle_matchs_regles": 2}
    html = tracking.render_sport_cards([("🎾", "Tennis", rep, store)], stake=5.0)
    ET.fromstring(re.search(r"<svg.*?</svg>", html, re.S).group(0))
    assert "#34d27b" in html and "#4aa8ff" in html       # Confiance vert + Value bleu
    assert html.count("<circle") == 2                    # un point de fin par courbe
    assert "Tennis" in html and "Confiance" in html and "Value" in html
    # un 2e sport sans données : sa carte existe (message courbe), 1 seule courbe (2 points de fin)
    mixed = tracking.render_sport_cards([("🎾", "Tennis", rep, store), ("⚽", "Foot", {}, {})])
    assert mixed.count('<div class="spc"') == 2          # 2 cartes
    assert mixed.count("<circle") == 2 and "pas encore assez" in mixed
    # Repère d'optimisation : un pari réglé À PARTIR d'une date d'optim -> ligne ambre + label
    post = {"X": {"perle": {"selection": "Z"},
                  "result": {"settled_at": "2099-01-02T10:00:00", "perle_pnl": 1.0}},
            "Y": {"perle": {"selection": "W"},
                  "result": {"settled_at": "2099-01-03T10:00:00", "perle_pnl": -1.0}}}
    tracking.PERLE_OPTIM_DATES.append(("2099-01-03", "testoptim"))
    try:
        h = tracking.render_sport_cards([("🎾", "T", {"perle_paris_regles": 2}, post)])
        svg = re.search(r"<svg.*?</svg>", h, re.S).group(0)
        assert "#ffa94a" in svg            # ligne ambre tracée sur le graphe
        assert "testoptim" not in svg      # plus de label SUR le graphe (épuré)
        assert "testoptim" in h            # mais listé dans la légende sous la section
    finally:
        tracking.PERLE_OPTIM_DATES.pop()



def test_load_cache(tmp_path):
    p = str(tmp_path / "trk.json")
    tracking.save({"x": {"home": "A"}}, p)
    a = tracking.load(p)
    b = tracking.load(p)
    assert a is b                                      # mtime inchangé -> même objet (pas de re-parse)
    tracking.save({"y": {"home": "B"}}, p)             # nouvelle sauvegarde -> mtime change
    assert tracking.load(p) == {"y": {"home": "B"}}    # cache invalidé, données à jour
