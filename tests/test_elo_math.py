"""Tests des maths Elo (marge de victoire + régression), utilisées par les builds."""

from app.elo_math import expected, mov_multiplier, regress_to_mean


def test_expected():
    assert abs(expected(1500, 1500) - 0.5) < 1e-9
    assert expected(1700, 1500) > 0.5
    assert expected(1500, 1700) < 0.5


def test_regress_to_mean():
    assert regress_to_mean(1700) == 0.75 * 1700 + 0.25 * 1500   # = 1650
    assert regress_to_mean(1500) == 1500                         # déjà à la moyenne
    assert regress_to_mean(1300) == 0.75 * 1300 + 0.25 * 1500   # = 1350


def test_mov_draw_is_neutral():
    assert mov_multiplier(0, 100) == 1.0       # nul -> ajustement standard
    assert mov_multiplier(-5, 100) == 1.0


def test_mov_increases_with_margin():
    # à écart Elo fixe, une marge plus grande pèse plus
    assert mov_multiplier(20, 0) > mov_multiplier(5, 0) > 0


def test_mov_dampens_favourites():
    # même marge : un gros favori (elo_diff élevé) gagne MOINS de points qu'un outsider
    margin = 12
    fav = mov_multiplier(margin, 300)     # le favori gagne large (attendu)
    underdog = mov_multiplier(margin, -300)  # l'outsider gagne large (surprenant)
    assert underdog > fav
