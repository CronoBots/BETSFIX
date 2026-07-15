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


# --------------------------------------------------------------- garde-fou selfcheck : 0 pari sans glose
def test_selfcheck_gloss_coverage_present_et_ok():
    from app import selfcheck
    rows, _ = selfcheck._load_rows()
    res = selfcheck._check_bet_gloss_coverage(rows)
    assert res["key"] == "bet_gloss_coverage"
    # sur l'état réel du dépôt, aucun pari affiché ne doit rester sans explication
    assert res["level"] == "ok", res["items"]
