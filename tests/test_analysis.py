"""Tests du modèle d'analyse de paris (fonctions pures, sans réseau)."""

from app.analysis import (
    MODEL_TRUST,
    build_analysis,
    form_rating,
    kelly_fraction,
    prob_from_rankings,
    recalibrate,
    remove_vig,
    weighted_form,
)
from app.models import (
    Match,
    Player,
    PlayerStatistics,
    UnibetMarket,
    UnibetOdds,
    UnibetOutcome,
)


def _match(home_rank=10, away_rank=20):
    return Match(
        id=1, tour="atp", ground_type="Red clay", status="notstarted",
        home=Player(id=100, name="Carlos Alcaraz", ranking=home_rank),
        away=Player(id=200, name="Alexander Zverev", ranking=away_rank),
    )


def _history(player_id, wins, losses, start=1000):
    """Crée des matchs terminés où `player_id` gagne `wins` fois, perd `losses`."""
    out = []
    i = start
    for _ in range(wins):
        out.append(Match(id=i, tour="atp", status="finished", winner="home",
                          home=Player(id=player_id), away=Player(id=9999)))
        i += 1
    for _ in range(losses):
        out.append(Match(id=i, tour="atp", status="finished", winner="home",
                          home=Player(id=8888), away=Player(id=player_id)))
        i += 1
    return out


def test_prob_from_rankings_favors_better_rank():
    p = prob_from_rankings(2, 50)
    assert p > 0.6
    # Quasi-symétrie : inverser les rangs somme ~1 (léger biais dû à l'intercept calibré)
    assert abs(p + prob_from_rankings(50, 2) - 1.0) < 0.02


def test_prob_from_rankings_missing():
    assert prob_from_rankings(None, 10) is None


def test_remove_vig_sums_to_one():
    ph, pa = remove_vig(1.5, 2.5)
    assert round(ph + pa, 6) == 1.0
    assert ph > pa


def test_kelly():
    assert kelly_fraction(0.4, 2.0) == 0.0          # pas de value
    assert 0 < kelly_fraction(0.6, 2.0) < 1          # value


def test_weighted_form_recency():
    # 5 victoires (récentes) puis 5 défaites (anciennes) -> forme > 0.5
    matches = _history(100, wins=5, losses=5)
    wwin, wsum = weighted_form(matches, 100)
    assert wsum > 0
    assert wwin / wsum > 0.5


def test_form_rating_rewards_beating_strong_opponents():
    # Joueur n°50 qui bat 5 fois des top-5 (résultat très au-dessus de l'attente)
    strong = [Match(id=i, tour="atp", status="finished", winner="home",
                    home=Player(id=1, ranking=50), away=Player(id=9000 + i, ranking=3))
              for i in range(5)]
    # Même joueur qui bat 5 fois des n°300 (attendu -> peu de mérite)
    weak = [Match(id=i, tour="atp", status="finished", winner="home",
                  home=Player(id=1, ranking=50), away=Player(id=8000 + i, ranking=300))
            for i in range(5)]
    r_strong, _, n1 = form_rating(strong, 1)
    r_weak, _, n2 = form_rating(weak, 1)
    assert n1 == 5 and n2 == 5
    # Battre des plus forts = sur-performance bien plus marquée
    assert r_strong > r_weak > 0


def test_form_rating_penalizes_losing_to_weak():
    # n°10 qui perd contre des n°200 -> sous-performance nette (résidu négatif)
    losses = [Match(id=i, tour="atp", status="finished", winner="away",
                    home=Player(id=1, ranking=10), away=Player(id=8000 + i, ranking=200))
              for i in range(4)]
    r, _, n = form_rating(losses, 1)
    assert n == 4 and r < 0


def test_recalibrate_shrinks_toward_half():
    # shrink=1 -> identité ; shrink<1 -> rapproche de 0.5
    assert recalibrate(0.8, 1.0) == 0.8
    assert recalibrate(0.5, 0.5) == 0.5            # 0.5 reste 0.5
    assert 0.5 < recalibrate(0.8, 0.5) < 0.8       # tempéré
    # symétrique autour de 0.5
    assert round(recalibrate(0.8, 0.6) + recalibrate(0.2, 0.6), 6) == 1.0


def test_recalibration_is_wired_in_build_analysis():
    # Match SANS rang/forme/stats/h2h : seul le facteur Elo subsiste, donc le mélange
    # = proba Elo. La proba finale doit être exactement la version recalibrée.
    from app.analysis import CALIB_SHRINK
    from app.elo import expected_score

    match = Match(id=1, tour="atp", ground_type="Red clay", status="notstarted",
                  home=Player(id=100, name="A"), away=Player(id=200, name="B"))
    a = build_analysis(match, [], [], None, None, None, None,
                       UnibetOdds(match_id=1, matched=False),
                       elo_home=2000, elo_away=1400)
    assert {f.name for f in a.factors} == {"elo"}      # seul l'Elo est présent
    p_elo = expected_score(2000, 1400)
    assert abs((a.model_home_probability or 0) - recalibrate(p_elo, CALIB_SHRINK)) < 1e-4
    assert (a.model_home_probability or 0) < p_elo      # tempéré vers 0.5


def test_build_analysis_uses_elo_factor():
    match = _match(home_rank=10, away_rank=10)
    # Elo nettement favorable à 'home' alors que les rangs sont égaux
    a = build_analysis(match, [], [], None, None, None, None,
                       UnibetOdds(match_id=1, matched=False),
                       elo_home=1900, elo_away=1500)
    assert "elo" in {f.name for f in a.factors}
    # Le facteur Elo (poids dominant) tire la proba home au-dessus du 50/50 des rangs
    assert (a.model_home_probability or 0) > 0.55


def test_build_analysis_detects_value():
    match = _match(home_rank=2, away_rank=3)
    stats_home = PlayerStatistics(player_id=100, first_serve_points_won_percentage=72,
                                  break_points_saved_converted_percentage=52)
    stats_away = PlayerStatistics(player_id=200, first_serve_points_won_percentage=66,
                                  break_points_saved_converted_percentage=44)
    home_hist = _history(100, wins=9, losses=1)
    away_hist = _history(200, wins=4, losses=6, start=2000)
    # Marché donne 'home' légèrement outsider (2.2) alors que le modèle le voit
    # favori -> désaccord MODÉRÉ (dans la bande plausible) = vraie value
    unibet = UnibetOdds(match_id=1, matched=True, kambi_event_id=42,
                        markets=[UnibetMarket(label="Cotes du match", type="Match", outcomes=[
                            UnibetOutcome(label="Carlos Alcaraz", odds=2.2),
                            UnibetOutcome(label="Alexander Zverev", odds=1.7)])])
    a = build_analysis(match, home_hist, away_hist, stats_home, stats_away, 6, 4, unibet)

    assert round((a.model_home_probability or 0) + (a.model_away_probability or 0), 4) == 1.0
    assert {"classement", "forme", "surface", "head_to_head"} <= {f.name for f in a.factors}
    assert a.confidence in ("moyenne", "élevée")
    home_bet = next(v for v in a.value_bets if v.side == "home")
    # L'edge est ancré au marché : plus petit que l'écart brut modèle-marché
    assert home_bet.edge > 0
    assert home_bet.is_value is True
    assert 0 < (home_bet.recommended_stake_pct or 0) <= 5.0  # plafonné


def test_market_anchoring_shrinks_edge():
    # fair = MODEL_TRUST*model + (1-MODEL_TRUST)*implied -> edge réduit
    match = _match(home_rank=1, away_rank=200)  # énorme favori côté modèle
    unibet = UnibetOdds(match_id=1, matched=True,
                        markets=[UnibetMarket(label="Match", type="Match", outcomes=[
                            UnibetOutcome(label="Carlos Alcaraz", odds=1.02),
                            UnibetOutcome(label="Alexander Zverev", odds=25.0)])])
    a = build_analysis(match, [], [], None, None, None, None, unibet)
    away_bet = next(v for v in a.value_bets if v.side == "away")
    # Outsider extrême (implicite < 5%) -> jamais signalé comme value
    assert away_bet.implied_probability < 0.05
    assert away_bet.is_value is False


def test_build_analysis_without_unibet():
    a = build_analysis(_match(), [], [], None, None, None, None,
                       UnibetOdds(match_id=1, matched=False))
    assert a.unibet_matched is False
    assert a.value_bets == []
    assert a.model_home_probability is not None  # le classement suffit
    assert a.confidence is not None
