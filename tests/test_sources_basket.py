"""Régression : matching d'équipe NBA/WNBA par SURNOM (bug « New York » vs « New Orleans »)."""

from app import sources as s


def test_nick():
    assert s._nick("New York Knicks") == "knicks"
    assert s._nick("New Orleans Pelicans") == "pelicans"
    assert s._nick("Los Angeles Lakers") == "lakers"
    assert s._nick("") == ""


def test_bb_team_rows_pas_de_confusion_new_york_new_orleans():
    # Pelicans listés AVANT Knicks : l'ancien code (token « new ») renvoyait les Pelicans pour les Knicks.
    inj = {"New Orleans Pelicans": ["Murphy", "Murray"], "New York Knicks": ["Brunson"]}
    assert s._bb_team_rows(inj, "New York Knicks") == ["Brunson"]
    assert s._bb_team_rows(inj, "New Orleans Pelicans") == ["Murphy", "Murray"]
    assert s._bb_team_rows(inj, "San Antonio Spurs") is None        # absent -> pas de faux match


def test_bb_team_rows_meme_ville_distinguee():
    inj = {"Los Angeles Clippers": ["C"], "Los Angeles Lakers": ["L"]}
    assert s._bb_team_rows(inj, "Los Angeles Lakers") == ["L"]
    assert s._bb_team_rows(inj, "Los Angeles Clippers") == ["C"]


def test_is_home_et_side_of_derbies():
    # derby de Manchester : « City » ne doit pas matcher « United » via « manchester »
    assert s._is_home("Manchester City", "Manchester United", "Manchester City") is False
    assert s._side_of("Manchester City", "Manchester United", "Manchester City") == "away"
    # deux Madrid distingués
    assert s._side_of("Real Madrid", "Real Madrid", "Atletico Madrid") == "home"
    assert s._side_of("Atletico Madrid", "Real Madrid", "Atletico Madrid") == "away"
    # aucun jeton commun / ambigu -> None
    assert s._side_of("Inconnu", "Real Madrid", "Atletico Madrid") is None
