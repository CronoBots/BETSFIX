"""Tests des notes Elo par surface (fonctions pures, sans réseau)."""

from app import elo
from app.models import Match, Player


def test_expected_and_prob():
    # Notes égales -> 50/50 ; +400 d'écart -> ~91%
    assert round(elo.expected_score(1500, 1500), 3) == 0.5
    assert elo.prob_from_elo(1900, 1500) > 0.9
    assert elo.prob_from_elo(None, 1500) is None


def test_update_ratings_winner_gains():
    store = {}
    elo.update_ratings(store, 1, 2, home_won=True, on_clay=True,
                       home_name="A", away_name="B")
    assert store["1"]["overall"] > 1500 > store["2"]["overall"]
    # Le match terre met aussi à jour la note terre
    assert store["1"]["clay"] > 1500
    assert store["1"]["clay_n"] == 1 and store["1"]["overall_n"] == 1


def test_update_ratings_hardcourt_skips_clay():
    store = {}
    elo.update_ratings(store, 1, 2, home_won=True, on_clay=False)
    assert store["1"]["overall_n"] == 1
    assert store["1"]["clay_n"] == 0          # pas touché hors terre
    assert store["1"]["clay"] == elo.BASE


def test_surface_rating_falls_back_when_few_clay():
    rec = {"overall": 1700, "overall_n": 50, "clay": 1900, "clay_n": 3}
    # Pas assez de matchs terre -> on prend la note globale
    assert elo.surface_rating(rec, "Red clay") == 1700
    rec["clay_n"] = elo.MIN_CLAY_MATCHES
    assert elo.surface_rating(rec, "Red clay") == 1900   # assez -> note terre
    assert elo.surface_rating(rec, "Hardcourt") == 1700  # hors terre -> globale
    assert elo.surface_rating(None, "clay") is None


def test_ratings_for_match():
    store = {"100": {"overall": 1800, "overall_n": 40, "clay": 1850, "clay_n": 20},
             "200": {"overall": 1600, "overall_n": 40, "clay": 1500, "clay_n": 20}}
    m = Match(id=1, tour="atp", ground_type="Red clay",
              home=Player(id=100), away=Player(id=200))
    eh, ea = elo.ratings_for_match(m, store)
    assert eh == 1850 and ea == 1500     # surface terre
    # Joueur inconnu -> None
    m2 = Match(id=2, tour="atp", ground_type="clay",
               home=Player(id=100), away=Player(id=999))
    eh2, ea2 = elo.ratings_for_match(m2, store)
    assert eh2 == 1850 and ea2 is None
