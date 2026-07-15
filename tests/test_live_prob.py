"""Barre « Chance live » (analyses.live_prob) : % « fair » = FUSION de 3 signaux — cote actuelle du direct
+ analyse d'avant-match + statistique du direct (modèle score+temps). PURE AFFICHAGE (jamais au ROI)."""

from app import analyses


def _p(**kw):
    base = dict(sport="foot", sel="", code="", home="Home FC", away="Away FC",
                hs=0, as_=0, minute=45, win_odds=None, ref_pct=None, catalog=None, vals=None)
    base.update(kw)
    return analyses.live_prob(**base)


def _cat(*rows):
    return [{"id": i, "text": txt, "odds": od} for i, (txt, od) in enumerate(rows)]


_GOALS_OU = _cat(("Nombre total de buts Plus de 2.5", 1.90),
                 ("Nombre total de buts Moins de 2.5", 1.90))


# --------------------------------------------------------------- verrous (déjà tranché) : 100 / 0
def test_over_verrouille_acquis():
    r = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=2, as_=1, minute=70, catalog=_GOALS_OU, ref_pct=60)
    assert r["source"] == "acquis" and r["pct"] == 100


def test_under_verrouille_perdu():
    r = _p(sel="Moins de 2.5 buts", code="UNDER 2.5", hs=2, as_=1, minute=70, ref_pct=60)
    assert r["source"] == "perdu" and r["pct"] == 0


def test_btts_verrouille_quand_les_deux_ont_marque():
    r = _p(sel="Les deux équipes marquent : Oui", code="BTTS", hs=1, as_=1, minute=30)
    assert r["source"] == "acquis" and r["pct"] == 100


def test_corners_verrouilles_via_compteur_live():
    r = _p(sel="Plus de 9.5 corners", code="CORNERS OVER 9.5", hs=0, as_=0, minute=80,
           vals={"corners_h": 6, "corners_a": 5})
    assert r["source"] == "acquis" and r["pct"] == 100


# --------------------------------------------------------------- fusion des 3 signaux
def test_fusion_trois_signaux_total_buts():
    r = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=1, as_=0, minute=30,
           catalog=_GOALS_OU, ref_pct=60)
    assert r["source"] == "cote + stats live + analyse"      # les 3 présents
    assert 0 < r["pct"] < 100


def test_fusion_vainqueur_foot():
    r = _p(sel="Victoire Home FC", code="1X2 1", hs=1, as_=0, minute=63,
           win_odds=(1.6, 4.0, 6.0), ref_pct=55)
    assert r["source"] == "cote + stats live + analyse"
    assert r["pct"] > 55                                     # mène 1-0 tard -> au-dessus de l'avant-match


def test_vainqueur_meneur_bat_mene_tard():
    lead = _p(sel="Victoire Home FC", code="1X2 1", hs=1, as_=0, minute=80,
              win_odds=(1.5, 4.0, 6.0), ref_pct=55)
    trail = _p(sel="Victoire Home FC", code="1X2 1", hs=0, as_=1, minute=80,
               win_odds=(3.0, 3.3, 2.3), ref_pct=55)
    assert lead["pct"] > trail["pct"]
    assert lead["trend"] == "up" and trail["trend"] == "down"


def test_over_00_decroit_avec_le_temps():
    early = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=0, as_=0, minute=10,
               catalog=_GOALS_OU, ref_pct=60)
    late = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=0, as_=0, minute=80,
              catalog=_GOALS_OU, ref_pct=60)
    assert early["pct"] > late["pct"]                        # le direct (0-0 tardif) tire le % vers le bas


def test_handicap_buts_desormais_modelise():
    # AVANT : pas de barre. MAINTENANT : modèle de direct (score + temps) même sans cote.
    r = _p(sel="Home FC -1.5 (handicap)", code="", hs=2, as_=0, minute=60, ref_pct=55)
    assert r is not None and "stats live" in r["source"]
    assert 0 <= r["pct"] <= 100


def test_corners_stats_live_sans_cote():
    # compteur live connu, pas de catalogue -> le modèle « stats live » suffit à afficher une barre
    r = _p(sel="Plus de 9.5 corners", code="CORNERS OVER 9.5", hs=0, as_=0, minute=70,
           vals={"corners_h": 5, "corners_a": 3}, ref_pct=55)
    assert r is not None and "stats live" in r["source"]


# --------------------------------------------------------------- sports sans modèle : cote + analyse
def test_tennis_vainqueur_cote_plus_analyse():
    r = _p(sport="tennis", sel="Victoire Sinner", code="", home="Alcaraz", away="Sinner",
           hs=1, as_=0, minute=None, win_odds=(1.5, None, 2.6), ref_pct=60)
    assert r is not None and r["source"] == "cote + analyse"   # pas de modèle in-play tennis
    assert 0 < r["pct"] < 100


# --------------------------------------------------------------- garde-fous : pas de faux %
def test_pas_de_barre_sans_score():
    assert _p(sel="Victoire Home FC", code="1X2 1", hs=None, as_=None,
              win_odds=(2.0, 3.5, 4.0)) is None


def test_pas_de_barre_sans_aucun_signal_live():
    # marché non modélisable + aucune cote en main -> pas de barre (l'avant-match seul ne « bouge » pas)
    assert _p(sel="Premier buteur : joueur X", code="", hs=0, as_=0, ref_pct=40) is None


def test_tennis_sans_cote_pas_de_barre():
    assert _p(sport="tennis", sel="Victoire Sinner", home="Alcaraz", away="Sinner",
              hs=1, as_=0, ref_pct=60) is None


def test_corners_sans_cote_ni_compteur_pas_de_barre():
    assert _p(sel="Plus de 9.5 corners", code="CORNERS OVER 9.5", hs=1, as_=0, minute=30) is None


# --------------------------------------------------------------- basket : handicap NON confondu avec vainqueur
def _pb(**kw):
    base = dict(sport="basket", sel="", code="", home="Minnesota Lynx", away="Los Angeles Sparks",
                hs=28, as_=28, minute=None, win_odds=None, ref_pct=None, catalog=None, vals=None,
                game_frac=0.43)
    base.update(kw)
    return analyses.live_prob(**base)


def test_handicap_signe_non_pris_pour_vainqueur():
    # BUG capture 2026-07-17 : « Sparks +17.5 » était lu comme « Sparks vainqueur » -> 44 %. Correctif.
    assert analyses._winner_side("Los Angeles Sparks +17.5 (prol. incl.)", "", "Minnesota Lynx",
                                 "Los Angeles Sparks", "basket") is None
    r = _pb(sel="Los Angeles Sparks +17.5 (prol. incl.)", ref_pct=80, win_odds=(2.1, None, 1.8))
    assert r is not None and "stats live" in r["source"]
    assert r["pct"] >= 70                # couvrir +17.5 à égalité = très probable (≠ 44 %)


def test_basket_handicap_adverse_faible():
    # Lynx -17.5 à égalité (doivent gagner de 18+) = peu probable
    r = _pb(sel="Minnesota Lynx -17.5", ref_pct=40, win_odds=(1.8, None, 2.1))
    assert r["pct"] <= 35


def test_basket_vainqueur_egalite_proche_50():
    r = _pb(sel="Los Angeles Sparks vainqueur", ref_pct=50, win_odds=(2.0, None, 2.0))
    assert 40 <= r["pct"] <= 60


def test_basket_total_points_modele():
    # 56 pts à ~43 % du match -> projection ~130 ; Over 150.5 improbable, Under probable
    over = _pb(sel="Plus de 150.5 points", code="", ref_pct=55)
    under = _pb(sel="Moins de 150.5 points", code="", ref_pct=55)
    assert over is not None and under is not None
    assert over["pct"] < under["pct"]


def test_basket_handicap_bascule_avec_le_score():
    # Sparks (extérieur) +17.5 : plus probable quand ils MÈNENT que quand ils sont MENÉS de 15
    mene = _pb(sel="Los Angeles Sparks +17.5", hs=35, as_=20, ref_pct=60)    # Sparks menés de 15
    devant = _pb(sel="Los Angeles Sparks +17.5", hs=20, as_=32, ref_pct=60)  # Sparks devant de 12
    assert devant["pct"] > mene["pct"]


def test_basket_frac_wnba_vs_nba():
    from app import match_select
    ld = {"matchClock": {"periodId": "QUARTER_2", "minutesLeftInPeriod": 2, "secondsLeftInMinute": 43}}
    fw = match_select.basket_frac(ld, "WNBA")
    fn = match_select.basket_frac(ld, "NBA")
    assert 0.4 < fw < 0.46 and 0.4 < fn < 0.46      # Q2, 2:43 restant -> ~43-44 % dans les deux ligues
    assert match_select.basket_frac({"matchClock": {"periodId": "OVERTIME"}}, "NBA") == 0.98
    assert match_select.basket_frac({}, "NBA") is None


# --------------------------------------------------------------- foot : handicap sans le mot « handicap »
def test_foot_handicap_signe_modelise_pas_vainqueur():
    # « France -1.5 » (sans le mot handicap) doit être MODÉLISÉ (couvre l'écart), pas lu comme « France gagne »
    assert analyses._winner_side("France -1.5", "", "France", "Argentine", "foot") is None
    r = analyses.live_prob("foot", "France -1.5", "", "France", "Argentine", 2, 0, 60, None, 70)
    assert r is not None and "stats live" in r["source"]


def test_le_pct_est_borne_0_100():
    for mn in (1, 30, 60, 89):
        for sc in ((0, 0), (3, 0), (0, 3)):
            r = _p(sel="Victoire Home FC", code="1X2 1", hs=sc[0], as_=sc[1], minute=mn,
                   win_odds=(2.0, 3.4, 3.6), ref_pct=50)
            assert r is None or 0 <= r["pct"] <= 100
