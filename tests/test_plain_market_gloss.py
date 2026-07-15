"""Glose « ↳ » en clair (web._plain_market) : CHAQUE pari affiché doit avoir une explication (demande user
2026-07-17 « il ne doit plus y avoir de paris sans explications ») + garde-fou selfcheck qui le vérifie."""

from app import web


# --------------------------------------------------------------- tennis : marchés de sets (le trou vécu)
def test_tennis_remporte_au_moins_un_set():
    g = web._plain_market("Daniel Altmaier remporte au moins un set", "tennis", "Daniel Altmaier", "Luciano Darderi")
    assert g and "manche" in g


def test_tennis_au_moins_deux_sets():
    assert web._plain_market("Cobolli remporte au moins deux sets", "tennis", "Cobolli", "X") == \
        "remporte au moins 2 manches"


def test_tennis_sans_perdre_de_set():
    g = web._plain_market("Alcaraz gagne sans perdre de set", "tennis", "Alcaraz", "X")
    assert g and "lâcher" in g


def test_tennis_vainqueur_inchange():
    assert web._plain_market("Flavio Cobolli vainqueur", "tennis", "Flavio Cobolli", "X") == "gagne le match (en sets)"


# --------------------------------------------------------------- foot : équipe marque (forme sans tiret)
def test_foot_equipe_marque_plus_de_05():
    assert web._plain_market("Argentine marque (Plus de 0.5 but)", "foot", "France", "Argentine") == \
        "Argentine marque au moins 1 but"


def test_foot_equipe_marque_moins_de_15():
    # « moins de 1.5 » = au plus 1 -> « moins de 2 buts » (jamais « moins de 1 » qui voudrait dire 0)
    assert web._plain_market("France marque (Moins de 1.5 but)", "foot", "France", "Argentine") == \
        "France marque moins de 2 buts"


def test_foot_equipe_marque_au_moins_2():
    assert web._plain_market("Argentine marque au moins 2 buts", "foot", "France", "Argentine") == \
        "Argentine marque au moins 2 buts"


# --------------------------------------------------------------- garantie TOTALE : n'importe quel pari joué
def test_bet_gloss_jamais_vide_sur_marches_exotiques():
    # demande user 2026-07-17 : « valable pour N'IMPORTE QUEL pari joué » -> _bet_gloss ne renvoie JAMAIS ''
    exotiques = [
        ("Sinner plus de 8.5 aces", "tennis"),
        ("Marco - Total jeux impairs", "tennis"),
        ("X - nombre de doubles fautes", "tennis"),
        ("Home - Rebonds joueur plus de 9.5", "basket"),
        ("Player X buteur", "foot"),
        ("Un marché totalement inédit et non codé", "foot"),
        ("Corner - handicap asiatique 2.5", "foot"),
    ]
    for sel, sport in exotiques:
        g = web._bet_gloss(sel, sport, "A", "B")
        assert g, f"pari sans explication : {sel!r}"


def test_bet_gloss_over_under_generique_avec_objet():
    assert web._bet_gloss("Sinner plus de 8.5 aces", "tennis", "A", "B") == "au moins 9 aces"


def test_bet_gloss_total_objet_nomme_par_equipe():
    # « total <objet> [de <équipe>] Plus/Moins de X » -> unité = l'objet (jamais « buts »), annotation ignorée
    assert web._bet_gloss("Total tirs cadrés moins de 4.5", "foot", "A", "B") == "moins de 5 tirs cadrés"
    assert web._bet_gloss("Nombre total de tirs cadrés de Argentine (réglé selon Opta Data) Plus de 2.5",
                          "foot", "France", "Argentine") == "Argentine : au moins 3 tirs cadrés"
    assert web._bet_gloss("Total de buts Plus de 2.5", "foot", "A", "B") == \
        "plus de 2 buts au total (les 2 équipes)"


def test_bet_gloss_prefere_le_cas_precis():
    # quand un cas PRÉCIS existe, _bet_gloss le renvoie (pas le générique)
    assert web._bet_gloss("Flavio Cobolli vainqueur", "tennis", "Flavio Cobolli", "X") == "gagne le match (en sets)"


def test_bet_gloss_vide_seulement_si_sel_vide():
    assert web._bet_gloss("", "foot", "A", "B") == ""
    assert web._bet_gloss("   ", "foot", "A", "B") == ""


# --------------------------------------------------------------- garde-fou selfcheck : 0 pari sans glose
def test_selfcheck_gloss_coverage_present_et_ok():
    from app import selfcheck
    rows, _ = selfcheck._load_rows()
    res = selfcheck._check_bet_gloss_coverage(rows)
    assert res["key"] == "bet_gloss_coverage"
    # sur l'état réel du dépôt : jamais d'anomalie (pari sans explication). Le repli générique = info tolérée.
    assert res["level"] in ("ok", "info"), res["items"]
    assert not any("SANS explication" in it for it in res["items"]), res["items"]
