"""Tests de la dérivation des codes de règlement (code_from_pick) et de settle_pick —
verrouille les formats de libellés Unibet/analyste rencontrés en production."""

from app.settle_analyst import code_from_pick, settle_pick

H, A = "Mexique", "Afrique du Sud"


def _score(h, a):
    return {"home": h, "away": a, "periods": {}, "sets_home": None, "sets_away": None}


def test_total_match_unite_apres_le_nombre():
    assert code_from_pick("Moins de 2.5 buts", "foot", H, A) == "UNDER 2.5"
    assert code_from_pick("Plus de 162.5 points (prol. incluses)", "basket", H, A) == "OVER 162.5"


def test_total_match_unite_avant_le_nombre():
    # le libellé qui restait « en attente » en prod : l'unité est AVANT le nombre
    assert code_from_pick("Nombre total de buts – Moins de 2.5", "foot", H, A) == "UNDER 2.5"
    assert code_from_pick("Nombre total de points : Plus de 173,5", "basket", H, A) == "OVER 173.5"


def test_total_equipe_prioritaire_sur_total_match():
    code = code_from_pick("Mexique – Plus de 1.5 buts (équipe)", "foot", H, A)
    assert code == "TEAMTOT HOME OVER 1.5"


def test_settle_pick_totaux():
    assert settle_pick("UNDER 2.5", _score(2, 0)) == "won"     # 2 buts < 2.5
    assert settle_pick("UNDER 2.5", _score(2, 1)) == "lost"    # 3 buts
    assert settle_pick("OVER 2.5", _score(2, 1)) == "won"
    assert settle_pick("UNDER 3.0", _score(2, 1)) == "push"    # ligne entière atteinte pile


def test_handicap_et_vainqueur():
    assert code_from_pick("Indiana Fever -6.5 (handicap)", "basket", "Indiana Fever", "Chicago Sky") \
        == "HCAP HOME -6.5"
    assert settle_pick("HCAP HOME -6.5", _score(114, 106)) == "won"
    assert settle_pick("WIN AWAY", _score(89, 105)) == "won"


# ------------------------------------------------------------------ garde-fou cotes moyennes
def test_recommend_garde_fou_cote_170():
    from app.analyses import _recommend
    # 66 % @ 1.85 (EV +22 %) : AVANT il était joué -> zone 39 % de réussite réelle, désormais exclu
    data = [{"sel": "X", "cote": 1.85, "prob": 66}]
    assert _recommend(data)["verdict"] == "skip"
    # 72 % @ 1.85 : confiance >= 70 exigée à cote >= 1.70 -> joué
    data = [{"sel": "X", "cote": 1.85, "prob": 72}]
    assert _recommend(data)["verdict"] == "play"
    # 66 % @ 1.45 : cote < 1.70, seuil 65 inchangé -> joué si EV ok (66x1.45-1 = -4% -> skip EV)
    data = [{"sel": "X", "cote": 1.60, "prob": 66}]
    assert _recommend(data)["verdict"] == "play"   # 66%x1.60-1 = +5.6% EV
