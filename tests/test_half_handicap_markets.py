"""Marchés rendus réglables (v39) : VAINQUEUR d'une mi-temps (HALFRES, « Mi-temps <équipe> ») via le
score par période, et HANDICAP 3 voies (HCAP3, « 3-Way Handicap (X-Y) ») via le score final ajusté.
Ces marchés sortaient un code VIDE -> jambes de combiné jamais réglées -> combiné jamais publié.
"""

from app.settle_analyst import code_from_pick as C, settle_pick as S


# --------------------------------------------------------------- HALFRES (vainqueur d'une mi-temps)
def test_halfres_parsing():
    assert C("Mi-temps France", "foot", "France", "Irak") == "HALFRES HOME 1H"
    assert C("2ème mi-temps Brésil", "foot", "Ecosse", "Brésil") == "HALFRES AWAY 2H"
    assert C("2ème mi-temps Angleterre", "foot", "Angleterre", "Ghana") == "HALFRES HOME 2H"
    assert C("Match nul à la mi-temps", "foot", "France", "Irak") == "HALFRES DRAW 1H"


def test_halfres_ne_casse_pas_les_autres_marches_mitemps():
    # « gagne AU MOINS une mi-temps » reste WINHALF (≠ vainqueur d'UNE mi-temps précise)
    assert C("Etats-Unis gagne au moins l'une des mi-temps", "foot", "Etats-Unis", "Australie") == "WINHALF HOME"
    # buts par équipe en 1ère MT reste TEAMHALF
    assert C("Brésil plus de 0.5 but 1ère mi-temps", "foot", "Brésil", "Haiti") == "TEAMHALF HOME 1H OVER 0.5"
    # corners en MT : reste un code CORNERS (scope 1ère MT appliqué par la métrique) — PAS détourné en HALFRES
    assert C("Total corners 1ère MT Plus de 2.5", "foot", "A", "B") == "CORNERS OVER 2.5"


def test_halfres_settle():
    sc = {"home": 2, "away": 1, "periods": {1: (1, 0), 2: (1, 1)}}   # MT1 1-0 (HOME), MT2 1-1 (nul)
    assert S("HALFRES HOME 1H", sc) == "won"
    assert S("HALFRES AWAY 1H", sc) == "lost"
    assert S("HALFRES DRAW 1H", sc) == "lost"
    assert S("HALFRES DRAW 2H", sc) == "won"
    assert S("HALFRES HOME 2H", sc) == "lost"
    # période absente -> non réglable (retentera), jamais un faux résultat
    assert S("HALFRES HOME 2H", {"home": 2, "away": 1, "periods": {1: (1, 0)}}) is None


# --------------------------------------------------------------- HCAP3 (handicap 3 voies « (X-Y) »)
def test_walkover_le_joueur_qui_avance_gagne():
    # walkover/forfait : le joueur qui AVANCE gagne -> pari SUR lui = gagné, sur le forfait = perdu.
    # JAMAIS de void (demande user).
    sc = {"walkover": True, "winner": "home"}
    assert S("SET HOME", sc) == "won"          # « remporte un set » sur le vainqueur
    assert S("SET AWAY", sc) == "lost"         # sur le joueur forfait
    assert S("WIN HOME", sc) == "won"
    assert S("WIN AWAY", sc) == "lost"
    assert S("1X2 1", sc) == "won"
    assert S("1X2 2", sc) == "lost"
    assert S("SETHCAP AWAY -1.5", sc) == "lost"
    # marché SANS côté (total de jeux/sets) -> la règle ne s'applique pas -> non réglable (reste en attente)
    assert S("TOTGAMES OVER 20.5", sc) is None
    assert S("SETSTOT OVER 2.5", sc) is None


def test_winhalf_non_negation():
    # bug NZ-Belgique : « gagne au moins une mi-temps NON » était réglé comme « Oui » -> faux.
    # NZ perd les 2 MT (0-1 puis 1-4) -> « NZ gagne une MT : Non » est VRAI.
    sc = {"home": 1, "away": 5, "periods": {1: (0, 1), 2: (1, 4)}}
    assert C("Nouvelle Zelande gagne au moins une mi-temps Non", "foot", "Nouvelle Zelande", "Belgique") \
        == "WINHALF HOME NO"
    assert S("WINHALF HOME NO", sc) == "won"        # NZ n'a gagné aucune MT -> « Non » gagne
    assert S("WINHALF HOME", sc) == "lost"          # version « Oui » -> perdu
    # équipe qui gagne la 1ère MT : « Oui » gagne, « Non » perd
    sc2 = {"home": 3, "away": 1, "periods": {1: (2, 0), 2: (1, 1)}}
    assert S("WINHALF HOME", sc2) == "won"
    assert S("WINHALF HOME NO", sc2) == "lost"


def test_hcap3_parsing():
    # le nom d'équipe est APRÈS la parenthèse « (1-0) » -> détecté sur le texte entier
    assert C("3-Way Handicap (1-0) Algérie", "foot", "Jordan", "Algérie") == "HCAP3 AWAY 1 0"
    assert C("3-Way Handicap (0-1) Jordan", "foot", "Jordan", "Algérie") == "HCAP3 HOME 0 1"
    assert C("3-Way Handicap (1-0) Match nul", "foot", "Jordan", "Algérie") == "HCAP3 DRAW 1 0"


def test_hcap3_settle():
    # Jordan 1 - 1 Algérie, handicap (1-0) -> score ajusté 2-1 -> HOME gagne
    sc = {"home": 1, "away": 1}
    assert S("HCAP3 HOME 1 0", sc) == "won"
    assert S("HCAP3 AWAY 1 0", sc) == "lost"
    assert S("HCAP3 DRAW 1 0", sc) == "lost"
    # 0-2, handicap (1-0) -> ajusté 1-2 -> AWAY gagne
    assert S("HCAP3 AWAY 1 0", {"home": 0, "away": 2}) == "won"
    # 1-0, handicap (0-1) -> ajusté 1-1 -> nul
    assert S("HCAP3 DRAW 0 1", {"home": 1, "away": 0}) == "won"
    # score absent -> non réglable
    assert S("HCAP3 HOME 1 0", {"home": None, "away": None}) is None
