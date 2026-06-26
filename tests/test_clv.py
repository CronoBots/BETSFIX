"""CLV (Closing Line Value) — brique légère : clv = cote_prise / cote_clôture − 1 (>0 = on bat le
marché). Couvre les paris RÉSULTAT (1X2 / vainqueur / temps réglementaire) via odds_history."""

from app import clv


def test_clv_pct():
    assert clv.clv_pct(2.0, 1.8) == (2.0 / 1.8 - 1)          # cote prise meilleure -> CLV positif
    assert clv.clv_pct(1.8, 2.0) < 0                          # cote prise pire -> CLV négatif
    assert clv.clv_pct(2.0, 2.0) == 0
    assert clv.clv_pct(None, 1.8) is None
    assert clv.clv_pct(2.0, 0) is None


def test_result_side():
    assert clv._result_side("1X2 1") == "home"
    assert clv._result_side("1X2 2") == "away"
    assert clv._result_side("1X2 X") == "draw"
    assert clv._result_side("WIN HOME") == "home"
    assert clv._result_side("REGTIME AWAY") == "away"
    assert clv._result_side("REGTIME DRAW") == "draw"
    # PAS un pari résultat -> None (pas de cote 1X2 de clôture exploitable)
    assert clv._result_side("OVER 2.5") is None
    assert clv._result_side("DC 1X") is None
    assert clv._result_side("TEAMTOT HOME OVER 1.5") is None
    assert clv._result_side("") is None


def _mv(home, away, oh, ox, oa, closed=True):
    return {"home": home, "away": away, "closed": closed,
            "legs": {"home": {"now": oh}, "draw": {"now": ox}, "away": {"now": oa}}}


def test_pick_clv_home_pick():
    d = {"home": "France", "away": "Irak", "pick": "France gagne @1.55", "pick_code": "1X2 1"}
    mv = _mv("France", "Irak", 1.40, 4.0, 7.0)               # clôture home = 1.40, prise = 1.55
    assert abs(clv.pick_clv(d, mv) - (1.55 / 1.40 - 1)) < 1e-9


def test_pick_clv_oriente_par_le_nom_ordre_inverse():
    # l'historique stocke le match dans l'ORDRE INVERSE (Irak home, France away) -> on doit suivre
    # l'ÉQUIPE pariée (France), pas le slot home.
    d = {"home": "France", "away": "Irak", "pick": "France gagne @1.55", "pick_code": "1X2 1"}
    mv = _mv("Irak", "France", 7.0, 4.0, 1.40)               # ici 'away' = France = 1.40
    assert abs(clv.pick_clv(d, mv) - (1.55 / 1.40 - 1)) < 1e-9


def test_pick_clv_non_calculable():
    d = {"home": "A", "away": "B", "pick": "A gagne @1.55", "pick_code": "1X2 1"}
    assert clv.pick_clv(d, None) is None                      # pas d'historique
    assert clv.pick_clv(d, _mv("A", "B", 1.4, 4, 7, closed=False)) is None   # match pas commencé
    # marché non-résultat -> None même avec historique
    assert clv.pick_clv({"home": "A", "away": "B", "pick": "Plus de 2.5 @1.8", "pick_code": "OVER 2.5"},
                        _mv("A", "B", 1.4, 4, 7)) is None
