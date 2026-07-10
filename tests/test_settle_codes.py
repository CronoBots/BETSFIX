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


# --------------------------------------------- fantômes tennis : tiebreaks, jeux du set, handicap de jeux
def _tennis_score(sets):
    """Score tennis {periods:{n:(jeux_h, jeux_a)}} depuis une liste de tuples par set."""
    return {"home": 0, "away": 0, "sets_home": None, "sets_away": None,
            "periods": {str(i + 1): s for i, s in enumerate(sets)}}


def test_code_tiebreak_total_over_under():
    # « (Nombre total de) Tiebreaks plus/moins de X » -> TIEBREAK OVER/UNDER (avant : générique OVER 0.5)
    assert code_from_pick("Nombre total de Tiebreaks plus de 0.5", "tennis", "A", "B") == "TIEBREAK OVER 0.5"
    assert code_from_pick("Total tiebreaks Plus de 0.5", "tennis", "A", "B") == "TIEBREAK OVER 0.5"
    assert code_from_pick("Nombre total de Tiebreaks Moins de 0.5", "tennis", "A", "B") == "TIEBREAK UNDER 0.5"
    # forme Oui/Non conservée
    assert code_from_pick("Un tie-break dans le match : Oui", "tennis", "A", "B") == "TIEBREAK YES"


def test_settle_tiebreak_compte_les_sets_76():
    sc = _tennis_score([(6, 7), (3, 6), (6, 3), (3, 6)])   # 1 set en tie-break (6-7)
    assert settle_pick("TIEBREAK OVER 0.5", sc) == "won"
    assert settle_pick("TIEBREAK UNDER 0.5", sc) == "lost"
    assert settle_pick("TIEBREAK YES", sc) == "won"
    sc0 = _tennis_score([(6, 4), (6, 3)])                  # aucun tie-break
    assert settle_pick("TIEBREAK OVER 0.5", sc0) == "lost"
    assert settle_pick("TIEBREAK UNDER 0.5", sc0) == "won"
    assert settle_pick("TIEBREAK OVER 1.5", _tennis_score([(7, 6), (6, 7)])) == "won"   # 2 tie-breaks


def test_code_et_settle_handicap_de_jeux():
    # « Handicap du jeu <joueur> ±X » -> GAMESHCAP (écart TOTAL de jeux), jamais HCAP (indéfini au tennis)
    assert code_from_pick("Handicap du jeu Novak Djokovic -4.5", "tennis", "Roman Safiullin",
                          "Novak Djokovic") == "GAMESHCAP AWAY -4.5"
    sc = _tennis_score([(6, 7), (3, 6), (6, 3), (3, 6)])   # Safiullin 18 jeux, Djokovic 22 -> écart 4
    assert settle_pick("GAMESHCAP AWAY -4.5", sc) == "lost"     # 22-18=4 < 4.5
    assert settle_pick("GAMESHCAP HOME +7.5", sc) == "won"      # 18+7.5=25.5 > 22


def test_code_jeux_dans_le_set():
    # « X jeux dans le set N » (jeux AVANT set) -> SETGAMES (avant : générique OVER X)
    assert code_from_pick("Plus de 9.5 jeux dans le set 1", "tennis", "A", "B") == "SETGAMES 1 OVER 9.5"
    assert code_from_pick("Moins de 12.5 jeux - Set 1", "tennis", "A", "B") == "SETGAMES 1 UNDER 12.5"


# ------------------------------------------------- traduction FR->EN des noms (matching sources de score)
def test_fr_en_noms_pays_pour_matching():
    from app.sources import _tok, _teams_match
    # pays manquants ajoutés -> jetons anglais présents (LiveScore/Flashscore nomment en anglais)
    assert "syria" in _tok("Syrie")
    assert "romania" in _tok("Roumanie")
    # traduction jeton à jeton : suffixe (F)/abréviation ne casse plus le lookup
    assert "malta" in _tok("Malte (F)")
    assert {"czech", "republic"} <= _tok("Rép.Tchèque")
    # un match FR (Unibet) matche l'événement EN (source de score)
    assert _teams_match("Syrie", "Irak", "Syria", "Iraq")
    assert _teams_match("Suède", "Rép.Tchèque", "Sweden", "Czech Republic")


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
