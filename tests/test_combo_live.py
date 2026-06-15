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


def test_scope_mi_temps_non_verrouillable():
    # 1ère mi-temps / deux mi-temps / handicap = pas verrouillables en live (live_ok False)
    assert _info("Corners 1ère MT Plus de 2.5", "CORNERS OVER 2.5")["scope"] == "1H"
    assert not _info("Corners 1ère MT Plus de 2.5", "CORNERS OVER 2.5")["live_ok"]
    assert _info("But dans les deux mi-temps Oui", "")["scope"] == "both"
    assert not _info("Corners Handicap Allemagne +5", "")["live_ok"]


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
