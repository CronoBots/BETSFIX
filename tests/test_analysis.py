"""Tests du modèle d'analyse de paris (fonctions pures, sans réseau)."""

from app.analysis import (
    build_analysis,
    kelly_fraction,
    remove_vig,
    win_rate,
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
        id=1,
        tour="atp",
        ground_type="Red clay",
        status="notstarted",
        home=Player(id=100, name="Carlos Alcaraz", ranking=home_rank),
        away=Player(id=200, name="Alexander Zverev", ranking=away_rank),
    )


def test_remove_vig_sums_to_one():
    ph, pa = remove_vig(1.5, 2.5)
    assert round(ph + pa, 6) == 1.0
    assert ph > pa  # favori = cote la plus basse


def test_kelly_zero_when_no_edge():
    # proba 0.4 sur une cote 2.0 (implicite 0.5) -> pas de value -> 0
    assert kelly_fraction(0.4, 2.0) == 0.0


def test_kelly_positive_with_edge():
    # proba 0.6 sur une cote 2.0 -> edge -> fraction positive
    f = kelly_fraction(0.6, 2.0)
    assert 0 < f < 1


def test_win_rate_counts_player_side():
    matches = [
        Match(id=1, tour="atp", status="finished", winner="home",
              home=Player(id=100), away=Player(id=999)),
        Match(id=2, tour="atp", status="finished", winner="home",
              home=Player(id=888), away=Player(id=100)),  # 100 perd (away, winner home)
        Match(id=3, tour="atp", status="notstarted", winner=None,
              home=Player(id=100), away=Player(id=1)),     # ignoré (pas fini)
    ]
    wins, played = win_rate(matches, 100)
    assert (wins, played) == (1, 2)


def _unibet_with_match_winner(odds_home, odds_away):
    return UnibetOdds(
        match_id=1, matched=True, kambi_event_id=42, event_name="Alcaraz - Zverev",
        markets=[UnibetMarket(label="Cotes du match", type="Match", outcomes=[
            UnibetOutcome(label="Carlos Alcaraz", odds=odds_home),
            UnibetOutcome(label="Alexander Zverev", odds=odds_away),
        ])],
    )


def test_build_analysis_detects_value():
    match = _match(home_rank=2, away_rank=3)
    stats_home = PlayerStatistics(player_id=100, first_serve_points_won_percentage=72,
                                  break_points_saved_converted_percentage=50)
    stats_away = PlayerStatistics(player_id=200, first_serve_points_won_percentage=68,
                                  break_points_saved_converted_percentage=45)
    # Le favori du modèle (Alcaraz) est sur-coté par Unibet -> value côté home
    unibet = _unibet_with_match_winner(odds_home=2.5, odds_away=1.5)

    a = build_analysis(
        match=match,
        home_matches=[], away_matches=[],
        home_stats=stats_home, away_stats=stats_away,
        home_wins_h2h=6, away_wins_h2h=4,
        unibet=unibet,
    )
    assert a.model_home_probability is not None
    assert round(a.model_home_probability + a.model_away_probability, 4) == 1.0
    # 4 facteurs : classement, surface, h2h (pas de forme car listes vides)
    names = {f.name for f in a.factors}
    assert {"classement", "surface", "head_to_head"} <= names
    # Value côté home (cote 2.5 alors que le modèle le voit favori)
    home_bet = next(v for v in a.value_bets if v.side == "home")
    assert home_bet.edge > 0
    assert home_bet.is_value is True
    assert home_bet.recommended_stake_pct > 0
    assert "value" in a.recommendation.lower()


def test_build_analysis_without_unibet():
    a = build_analysis(
        match=_match(), home_matches=[], away_matches=[],
        home_stats=None, away_stats=None,
        home_wins_h2h=None, away_wins_h2h=None,
        unibet=UnibetOdds(match_id=1, matched=False),
    )
    assert a.unibet_matched is False
    assert a.value_bets == []
    assert a.model_home_probability is not None  # classement suffit
