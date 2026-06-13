"""Tests PURS de app/livescore.py (parsing du scoreboard pour le RÈGLEMENT, aucun appel réseau).

NB : distinct de tests/test_livescore.py (ancien provider tennis app/providers/livescore.py)."""

from app import livescore as ls
from app.settle_analyst import settle_pick


def test_scoreboard_foot_mi_temps():
    sb = {"Eps": "FT", "Tr1": "2", "Tr2": "1", "Trh1": "0", "Trh2": "1"}
    sc = ls._parse_scoreboard("soccer", sb)
    assert sc["home"] == 2 and sc["away"] == 1 and sc["label"] == "2-1"
    assert sc["periods"] == {1: (0, 1), 2: (2, 0)}        # 2e mi-temps = final - mi-temps
    assert settle_pick("OVER 2.5", sc) == "won"
    assert settle_pick("1X2 1", sc) == "won"
    assert settle_pick("BTTS YES", sc) == "won"


def test_scoreboard_basket_quart_temps():
    sb = {"Eps": "FT", "Tr1": "81", "Tr2": "83",
          "Tr1Q1": "21", "Tr2Q1": "23", "Tr1Q2": "18", "Tr2Q2": "17",
          "Tr1Q3": "20", "Tr2Q3": "20", "Tr1Q4": "22", "Tr2Q4": "23"}
    sc = ls._parse_scoreboard("basketball", sb)
    assert sc["home"] == 81 and sc["away"] == 83
    assert sc["periods"][1] == (21, 23) and sc["periods"][4] == (22, 23)
    assert settle_pick("OVER 163.5", sc) == "won"
    assert settle_pick("1X2 2", sc) == "won"                # extérieur gagne


def test_scoreboard_tennis_sets():
    sb = {"Eps": "FT", "Tr1": "2", "Tr2": "1",
          "Tr1S1": "7", "Tr2S1": "6", "Tr1S2": "4", "Tr2S2": "6", "Tr1S3": "10", "Tr2S3": "7"}
    sc = ls._parse_scoreboard("tennis", sb)
    assert sc["home"] is None and sc["sets_home"] == 2 and sc["sets_away"] == 1
    assert sc["label"] == "2-1 (sets)"
    assert settle_pick("WIN HOME", sc) == "won"
    assert settle_pick("SETSTOT OVER 2.5", sc) == "won"
    assert settle_pick("SETGAMES 1 OVER 12.5", sc) == "won"   # set 1 = 7+6 = 13 jeux


def test_non_termine_ou_partiel_non_regle():
    # match en cours / forfait / abandon -> jamais réglé (pas de mauvais règlement)
    assert ls._parse_scoreboard("soccer", {"Eps": "49'", "Tr1": "1", "Tr2": "0"}) is None
    assert ls._parse_scoreboard("tennis", {"Eps": "Ret.", "Tr1": "1", "Tr2": "0"}) is None
    assert ls._parse_scoreboard("soccer", {"Eps": "FT"}) is None      # statut OK mais pas de score
