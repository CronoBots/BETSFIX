"""Validation LIVE des combinés : parseur de MÉTRIQUE par jambe + évaluateur (live & final) + statut
global. Couvre le bug « tirs réglés comme des buts » (la métrique vient du TEXTE, pas du code)."""

from app import analyses as A


def _info(sel, code=""):
    return A._leg_metric({"sel": sel, "code": code}, "Allemagne", "Curaçao")


def test_metric_distingue_tirs_de_buts():
    # MÊME code TEAMTOT … OVER, mais le TEXTE distingue buts / tirs / tirs cadrés
    g = _info("Curaçao Moins de 1.5 but", "TEAMTOT AWAY UNDER 1.5")
    assert g["metric"] == "goals" and g["side"] == "AWAY" and g["dir"] == "UNDER" and g["line"] == 1.5
    s = _info("Tirs Allemagne Plus de 22.5", "TEAMTOT HOME OVER 22.5")
    assert s["metric"] == "shots" and s["side"] == "HOME" and s["line"] == 22.5 and s["live_ok"]
    sot = _info("Allemagne Plus de 6.5 tirs cadrés", "TEAMTOT HOME OVER 6.5")
    assert sot["metric"] == "sot" and sot["live_ok"]


def test_metric_corners_cartons_rouge():
    assert _info("Total cartons Plus de 2.5", "CARDS OVER 2.5")["metric"] == "cards"
    r = _info("Carton rouge distribué : Non", "REDCARDS UNDER 0.5")
    assert r["metric"] == "redcards" and r["dir"] == "UNDER" and r["line"] == 0.5
    assert _info("Total corners Plus de 7.5", "CORNERS OVER 7.5")["metric"] == "corners"


def test_scope_1ere_mt_verrouillable_sur_stats():
    # 1ère MT d'une métrique df_st (corners/cartons/tirs) = verrouillable via les stats 1ère mi-temps
    i = _info("Corners 1ère MT Plus de 2.5", "CORNERS OVER 2.5")
    assert i["scope"] == "1H" and i["live_ok"] and i["metric"] == "corners"


def test_scope_non_verrouillable():
    # 1ère MT en BUTS (pas dans df_st) = pas suivable (live_ok False)
    assert not _info("Plus de 0.5 but en 1ère mi-temps", "")["live_ok"]   # buts 1H : pas de df_st
    # handicap en 1ère MT = hors périmètre (on ne suit le handicap que sur le match entier)
    assert not _info("Corners Handicap Allemagne 1ère mi-temps +3", "")["live_ok"]


def test_eval_1ere_mt_sur_cles_1h():
    info = {"metric": "corners", "side": None, "dir": "OVER", "line": 2.5,
            "scope": "1H", "live_ok": True}
    # utilise corners_*_1h, PAS le total du match
    assert A._eval_leg(info, {"corners_h_1h": 2, "corners_a_1h": 2, "corners_h": 9, "corners_a": 8})[0] == "won"
    assert A._eval_leg(info, {"corners_h_1h": 1, "corners_a_1h": 1, "corners_h": 9, "corners_a": 8})[0] == "pending"
    assert A._eval_leg(info, {"corners_h_1h": 1, "corners_a_1h": 1}, final=True)[0] == "lost"


def test_metric_depuis_texte_seul():
    # code vide : tout se lit sur le texte (ligne + sens + métrique)
    s = _info("Plus de 20.5 tirs", "")
    assert s["metric"] == "shots" and s["dir"] == "OVER" and s["line"] == 20.5 and s["live_ok"]


def test_eval_over_verrouille_des_depassement():
    info = {"metric": "shots", "side": "HOME", "dir": "OVER", "line": 22.5,
            "scope": "match", "live_ok": True}
    # live : acquis dès que le compteur dépasse la ligne
    assert A._eval_leg(info, {"shots_h": 23, "shots_a": 4})[0] == "won"
    # live : pas encore atteint -> en cours
    assert A._eval_leg(info, {"shots_h": 10, "shots_a": 4})[0] == "pending"
    # final : pas atteint -> perdu
    assert A._eval_leg(info, {"shots_h": 10, "shots_a": 4}, final=True)[0] == "lost"


def test_eval_under_verrouille_en_perte():
    info = {"metric": "goals", "side": None, "dir": "UNDER", "line": 5.5,
            "scope": "match", "live_ok": True}
    assert A._eval_leg(info, {"goals_h": 2, "goals_a": 1})[0] == "pending"          # 3 < 5.5
    assert A._eval_leg(info, {"goals_h": 2, "goals_a": 1}, final=True)[0] == "won"  # final
    assert A._eval_leg(info, {"goals_h": 4, "goals_a": 2})[0] == "lost"             # 6 > 5.5 verrouillé


def test_eval_valeur_manquante_reste_pending():
    info = {"metric": "corners", "side": None, "dir": "OVER", "line": 7.5,
            "scope": "match", "live_ok": True}
    assert A._eval_leg(info, {})[0] == "pending"           # stats pas encore récupérées
    assert A._eval_leg(info, {}, final=True)[0] is None    # final sans stats -> non réglable (retentera)


def test_combo_live_status_global():
    d = {"home": "Allemagne", "away": "Curaçao", "sport": "foot", "combo": {"legs": [
        {"sel": "Tirs Allemagne Plus de 22.5", "code": "TEAMTOT HOME OVER 22.5", "cote": 1.4},
        {"sel": "Total buts Moins de 5.5", "code": "UNDER 5.5", "cote": 1.2},
    ]}}
    # une jambe perdue (final-style verrouillé) -> combiné perdu
    st = A.combo_live_status(d, {"shots_h": 23, "shots_a": 3, "goals_h": 4, "goals_a": 2})
    assert st["legs"][0]["status"] == "won" and st["legs"][1]["status"] == "lost"
    assert st["status"] == "lost"
    # toutes acquises -> gagné
    st2 = A.combo_live_status(d, {"shots_h": 23, "shots_a": 3, "goals_h": 1, "goals_a": 0})
    assert st2["status"] == "pending"   # buts Moins 5.5 pas encore verrouillé (1 < 5.5) -> en cours


def test_combo_live_status_sans_combo():
    assert A.combo_live_status({"home": "A", "away": "B"}, {}) is None


def test_bothhalves_metric_et_reglement():
    i = _info("But dans les deux mi-temps Oui", "")
    assert i["metric"] == "bothhalves" and i["yes"] and i["live_ok"]
    j = _info("But dans les deux mi-temps Non", "")
    assert j["metric"] == "bothhalves" and not j["yes"]
    # 0-0 (aucun but en 1ère MT, 2e MT entamée) -> « Oui » verrouillé PERDU
    assert A._eval_leg(i, {"goals_1h_total": 0, "goals_2h_total": 0})[0] == "lost"
    # un but dans chaque mi-temps, au final -> « Oui » gagné
    assert A._eval_leg(i, {"goals_1h_total": 1, "goals_2h_total": 1}, final=True)[0] == "won"
    # but seulement en 2e MT -> « Oui » perdu (1ère MT à 0, verrouillé)
    assert A._eval_leg(i, {"goals_1h_total": 0, "goals_2h_total": 2}, final=True)[0] == "lost"
    # 1ère MT en cours 0-0 (pas de 2e MT) -> en cours, pas verrouillé
    assert A._eval_leg(i, {"goals_1h_total": 0})[0] == "pending"


def test_handicap_corners_suivi_et_reglement():
    i = _info("Corners Handicap Allemagne +5", "")
    assert i["dir"] == "HCAP" and i["side"] == "HOME" and i["line"] == 5.0 and i["live_ok"]
    # +5 = « l'adversaire ne mène pas de +5 corners » -> écart adverse = away - home, seuil 5
    s, v = A._eval_leg(i, {"corners_h": 2, "corners_a": 6})
    assert s == "pending" and v == 4          # écart adverse 6-2 = 4
    assert A._eval_leg(i, {"corners_h": 2, "corners_a": 6}, final=True)[0] == "won"    # 4 < 5
    assert A._eval_leg(i, {"corners_h": 0, "corners_a": 6}, final=True)[0] == "lost"   # 6 > 5
    # affichage = SCORE AJUSTÉ « mien+handicap - adverse » (ex. 4 corners +5 = 9 vs 1 -> « 9-1 »)
    d = {"home": "Allemagne", "away": "Curaçao", "combo": {"legs": [
        {"sel": "Corners Handicap Allemagne +5", "code": "", "cote": 1.3}]}}
    assert A.combo_live_status(d, {"corners_h": 4, "corners_a": 1})["legs"][0]["disp"] == "9-1"
    assert A.combo_live_status(d, {"corners_h": 0, "corners_a": 0})["legs"][0]["disp"] == "5-0"
    # handicap côté AWAY (-3 = mon équipe mène de +3)
    j = _info("Corners Handicap Curaçao -3", "")
    assert j["side"] == "AWAY" and j["line"] == -3.0
    assert A._eval_leg(j, {"corners_h": 2, "corners_a": 6}, final=True)[0] == "won"    # écart 6-2=4 > 3


def test_eval_tolere_valeurs_str():
    # Unibet renvoie le score en chaîne ("1") -> ne doit PAS planter (str > float), coercition en int
    info = {"metric": "goals", "side": None, "dir": "OVER", "line": 1.5,
            "scope": "match", "live_ok": True}
    assert A._eval_leg(info, {"goals_h": "1", "goals_a": "1"})[0] == "won"     # 1+1=2 > 1.5
    assert A._eval_leg(info, {"goals_h": "1", "goals_a": "0"})[0] == "pending"  # 1 < 1.5
    assert A._as_int("2") == 2 and A._as_int(3) == 3 and A._as_int(None) is None and A._as_int("x") is None
