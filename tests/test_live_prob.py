"""Barre « % live » (analyses.live_prob) : reflet de la cote/du score en direct. PURE AFFICHAGE."""

from app import analyses


def _p(**kw):
    base = dict(sport="foot", sel="", code="", home="Home FC", away="Away FC",
                hs=0, as_=0, minute=45, win_odds=None, ref_pct=None)
    base.update(kw)
    return analyses.live_prob(**base)


# --------------------------------------------------------------- vainqueur / DC (cote live)
def test_vainqueur_cote_live_demargee():
    # cotes 2.00 / 3.50 / 4.00 -> implicites 0.5/0.286/0.25 = 1.036, dé-margé home ~48%
    r = _p(sport="foot", sel="Victoire Home FC", code="1X2 1", win_odds=(2.0, 3.5, 4.0))
    assert r["source"] == "cote live"
    assert 46 <= r["pct"] <= 50


def test_double_chance_somme_deux_issues():
    r = _p(sport="foot", sel="Double chance : Home FC ou nul", code="DC 1X",
           win_odds=(2.0, 3.5, 4.0))
    # 1X = home + draw ; doit dépasser home seul
    assert r["source"] == "cote live"
    assert r["pct"] >= 70


def test_tennis_vainqueur_deux_voies():
    # pas de nul : ox absent -> 2 voies
    r = _p(sport="tennis", sel="Victoire Sinner", code="", home="Alcaraz", away="Sinner",
           hs=1, as_=0, win_odds=(1.5, None, 2.6))
    assert r is not None and r["source"] == "cote live"
    assert 35 <= r["pct"] <= 40


def test_trend_up_down_flat():
    up = _p(sel="Victoire Home FC", code="1X2 1", win_odds=(1.5, 4.0, 6.0), ref_pct=50)
    assert up["trend"] == "up"       # ~62% > 50
    down = _p(sel="Victoire Home FC", code="1X2 1", win_odds=(3.0, 3.3, 2.3), ref_pct=60)
    assert down["trend"] == "down"


# --------------------------------------------------------------- totaux buts (modèle + verrou)
def test_over_verrouille_quand_deja_franchi():
    r = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=2, as_=1, minute=70)
    assert r["source"] == "acquis" and r["pct"] == 100


def test_under_verrouille_perdu_quand_depasse():
    r = _p(sel="Moins de 2.5 buts", code="UNDER 2.5", hs=2, as_=1, minute=70)
    assert r["source"] == "perdu" and r["pct"] == 0


def test_over_modele_progresse_avec_le_temps_et_le_score():
    tot = analyses._FOOT_GOALS_90
    early = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=0, as_=0, minute=10)
    late = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=0, as_=0, minute=80)
    assert early["source"] == "modele".replace("modele", "modèle")
    # 0-0 : Over 2.5 devient de moins en moins probable à mesure que le temps passe
    assert early["pct"] > late["pct"]
    # à 2-2 (déjà franchi) -> verrouillé
    locked = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=2, as_=2, minute=80)
    assert locked["pct"] == 100
    assert tot > 0


def test_under_est_complement_de_over():
    over = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=1, as_=0, minute=55)
    under = _p(sel="Moins de 2.5 buts", code="UNDER 2.5", hs=1, as_=0, minute=55)
    assert over["pct"] + under["pct"] == 100


# --------------------------------------------------------------- équipe marque (modèle)
def test_equipe_marque_over():
    r = _p(sel="Home FC marque plus de 1.5 but", code="TEAMTOT HOME OVER 1.5",
           hs=1, as_=0, minute=60)
    assert r is not None and r["source"] in ("modèle", "acquis")
    assert 0 <= r["pct"] <= 100


# --------------------------------------------------------------- BTTS (modèle + verrou)
def test_btts_verrouille_quand_les_deux_ont_marque():
    r = _p(sel="Les deux équipes marquent : Oui", code="BTTS", hs=1, as_=1, minute=30)
    assert r["source"] == "acquis" and r["pct"] == 100


def test_btts_oui_baisse_si_une_equipe_muette_tard():
    tot = _p(sel="Les deux équipes marquent : Oui", code="BTTS", hs=1, as_=0, minute=85)
    assert tot["source"] == "modèle" and tot["pct"] < 40


def test_btts_non_est_complement():
    oui = _p(sel="Les deux équipes marquent : Oui", code="BTTS", hs=1, as_=0, minute=50)
    non = _p(sel="Les deux équipes marquent : Non", code="BTTS", hs=1, as_=0, minute=50)
    assert oui["pct"] + non["pct"] == 100


# --------------------------------------------------------------- garde-fous (pas de faux %)
def test_pas_de_barre_sans_score():
    assert _p(sel="Victoire Home FC", code="1X2 1", hs=None, as_=None,
              win_odds=(2.0, 3.5, 4.0)) is None


def test_corners_pas_de_barre_vainqueur():
    # un pari corners ne doit PAS emprunter la cote vainqueur -> pas de barre
    r = _p(sel="Home FC plus de 4.5 corners", code="CORNERS HOME OVER 4.5",
           win_odds=(2.0, 3.5, 4.0), hs=1, as_=0)
    assert r is None


def test_buteur_pas_de_barre_vainqueur():
    r = _p(sel="Premier buteur : joueur X", code="", win_odds=(2.0, 3.5, 4.0), hs=0, as_=0)
    assert r is None


def test_handicap_pas_de_barre_vainqueur():
    r = _p(sel="Home FC -1.5 (handicap)", code="", win_odds=(2.0, 3.5, 4.0), hs=1, as_=0)
    assert r is None


# --------------------------------------------------------------- cote live du marché (catalogue Bet Builder)
def _cat(*rows):
    return [{"id": i, "text": txt, "odds": od} for i, (txt, od) in enumerate(rows)]


def test_catalogue_over_under_demarge():
    cat = _cat(("Nombre total de buts Plus de 2.5", 1.90),
              ("Nombre total de buts Moins de 2.5", 1.90))
    r = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=1, as_=0, minute=30, catalog=cat)
    assert r["source"] == "cote live"     # cote du marché, PAS le modèle
    assert 48 <= r["pct"] <= 52           # 1.90/1.90 -> ~50 % dé-margé


def test_catalogue_corners_desormais_couvert():
    # AVANT : corners = pas de barre. MAINTENANT : cote live du marché.
    cat = _cat(("Total des corners Plus de 9.5", 1.80),
              ("Total des corners Moins de 9.5", 2.00))
    r = _p(sel="Plus de 9.5 corners", code="CORNERS OVER 9.5", hs=0, as_=0, minute=20, catalog=cat)
    assert r is not None and r["source"] == "cote live"
    assert 50 <= r["pct"] <= 55


def test_catalogue_equipe_marque():
    cat = _cat(("Home FC - Nombre de buts Plus de 1.5", 2.10),
              ("Home FC - Nombre de buts Moins de 1.5", 1.70))
    r = _p(sel="Home FC marque plus de 1.5 but", code="TEAMTOT HOME OVER 1.5",
           hs=1, as_=0, minute=40, catalog=cat)
    assert r["source"] == "cote live"
    assert 40 <= r["pct"] <= 48


def test_catalogue_choisit_la_bonne_ligne():
    cat = _cat(("Nombre total de buts Plus de 1.5", 1.20),
              ("Nombre total de buts Moins de 1.5", 4.50),
              ("Nombre total de buts Plus de 3.5", 3.00),
              ("Nombre total de buts Moins de 3.5", 1.35))
    r = _p(sel="Plus de 3.5 buts", code="OVER 3.5", hs=1, as_=1, minute=50, catalog=cat)
    assert r["source"] == "cote live"
    # 3.00 vs 1.35 -> Over 3.5 ~31 % (bonne ligne, pas la 1.5)
    assert 28 <= r["pct"] <= 34


def test_verrou_prioritaire_sur_le_catalogue():
    # total déjà franchi -> 100 % « acquis », même si le catalogue price encore l'issue
    cat = _cat(("Nombre total de buts Plus de 1.5", 1.05),
              ("Nombre total de buts Moins de 1.5", 8.00))
    r = _p(sel="Plus de 1.5 buts", code="OVER 1.5", hs=2, as_=0, minute=70, catalog=cat)
    assert r["source"] == "acquis" and r["pct"] == 100


def test_catalogue_absent_repli_modele():
    r = _p(sel="Plus de 2.5 buts", code="OVER 2.5", hs=1, as_=0, minute=30, catalog=[])
    assert r["source"] == "modèle"


def test_catalogue_singleton_implicite_brute():
    # une seule issue du marché dispo -> proba implicite brute (reflet direct de la cote), pas None
    cat = _cat(("Total des tirs cadrés Plus de 8.5", 1.50))
    r = _p(sel="Plus de 8.5 tirs cadrés", code="SOT OVER 8.5", hs=0, as_=0, minute=25, catalog=cat)
    assert r is not None and r["source"] == "cote live"
    assert 64 <= r["pct"] <= 68           # 1/1.50 ~ 66,7 %
