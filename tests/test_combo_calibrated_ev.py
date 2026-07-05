"""Le combiné applique la MÊME calibration que le pari simple : chaque jambe passe par
`calibrated_conf` AVANT le produit (la sur-confiance se compose en combiné, sinon l'EV est
surévaluée), et le plancher de chance _COMBO_PROB_MIN est une BARRIÈRE DURE (plus de repli longshot).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import generate_analyses as gen  # noqa: E402
from app import analyses  # noqa: E402


def _cands():
    # Cote de jambe = 1.40 = la cote corrélée PAR jambe simulée par _fake_bb. La cohérence est REQUISE
    # depuis la correction de corrélation (2026-07-05) : la proba conjointe est ajustée par k = produit
    # des cotes / vraie cote combinée. Ici produit (1.40²=1.96) == vraie cote (_fake_bb) -> k=1 -> la proba
    # reste le PRODUIT DES PROBAS CALIBRÉES (ce que ces tests ciblent). Des cotes incohérentes (ex. 2.0
    # avec une cote combinée 1.96, physiquement impossible) feraient exploser k et fausseraient le test.
    return [{"sel": "A", "cote": 1.40, "code": "OVER 2.5", "oid": 101, "prob": 80},
            {"sel": "B", "cote": 1.40, "code": "DC 1X", "oid": 102, "prob": 80},
            {"sel": "C", "cote": 1.40, "code": "1X2 1", "oid": 103, "prob": 80}]


def _fake_bb(eid, oids):
    # cote « corrélée » simulée -> 1.40 par jambe : 1.96 à 2 jambes (DANS la fourchette foot 1.75-2.25),
    # 2.74 à 3 jambes (AU-DESSUS de _COMBO_REAL_MAX=2.25 -> écarté). (Fourchette resserrée le 2026-07-02.)
    p = 1.0
    for _ in oids:
        p *= 1.40
    return round(p, 2)


def test_combo_recalibre_la_proba_des_jambes(monkeypatch):
    monkeypatch.setattr(gen.unibet, "betbuilder_odds", _fake_bb)
    # calibration douce : 80 % annoncé -> 75 % réel (sur-confiance corrigée)
    monkeypatch.setattr(analyses, "calibrated_conf", lambda prob, sport, code: 75)
    res = gen._build_combo_from_pool("123", _cands(), "foot")
    assert res is not None
    # 3 jambes -> vraie cote 2.74 > _COMBO_REAL_MAX (2.25) -> écarté ; reste le 2 jambes (cote 1.96)
    assert len(res["legs"]) == 2
    # proba = produit des probas CALIBRÉES (0.75² = 56 %), PAS des brutes (0.80² = 64 %)
    assert res["prob"] == 56, "la proba combinée doit refléter la calibration, pas la confiance brute"


def test_combo_barre_les_longshots_apres_calibration(monkeypatch):
    monkeypatch.setattr(gen.unibet, "betbuilder_odds", _fake_bb)
    # forte sur-confiance corrigée : 80 % annoncé -> 50 % réel
    monkeypatch.setattr(analyses, "calibrated_conf", lambda prob, sport, code: 50)
    # BASKET/TENNIS : la chance mini est une BARRIÈRE DURE (pas de combiné phare de repli, contrairement au
    # FOOT/CdM qui garde toujours son combiné le plus sûr). 0.50² = 25 % < p_min -> ABSTENTION.
    res = gen._build_combo_from_pool("123", _cands(), "basket")
    assert res is None, "sous la chance mini calibrée, basket/tennis ne publient PAS de combiné (fini le longshot)"


def test_combo_sans_calibration_dispo_garde_la_proba_brute(monkeypatch):
    monkeypatch.setattr(gen.unibet, "betbuilder_odds", _fake_bb)
    # calibrated_conf renvoie la proba INCHANGÉE (échantillon trop maigre) -> comportement d'avant
    monkeypatch.setattr(analyses, "calibrated_conf", lambda prob, sport, code: prob)
    res = gen._build_combo_from_pool("123", _cands(), "foot")
    assert res is not None and res["prob"] == 64, "sans données de calibration, on garde la proba brute"


def _cands2():
    # 2 jambes @1.50 (produit 2.25), proba calibrée 75 % chacune (produit 0.5625 = 56 %).
    return [{"sel": "A", "cote": 1.50, "code": "OVER 150.5", "oid": 201, "prob": 75},
            {"sel": "B", "cote": 1.50, "code": "HCAP HOME", "oid": 202, "prob": 75}]


def test_combo_correlation_ajuste_la_proba(monkeypatch):
    """Correction de corrélation (2026-07-05) : la proba conjointe = produit des probas AJUSTÉ par
    k = produit_cotes / vraie_cote. k>1 (cote combinée SOUS le produit = jambes corrélées) -> proba
    RELEVÉE ; k<1 (cote AU-DESSUS = anti-corrélées) -> proba ABAISSÉE. Sans ça, un combiné anti-corrélé
    afficherait une fausse value (cas FAA/ADF)."""
    monkeypatch.setattr(analyses, "calibrated_conf", lambda prob, sport, code: 75)

    def build(real):
        monkeypatch.setattr(gen.unibet, "betbuilder_odds", lambda eid, oids: real)
        return gen._build_combo_from_pool("123", _cands2(), "basket")

    corr = build(1.90)   # vraie cote SOUS le produit 2.25 -> corrélation positive -> proba relevée, GARDÉ
    neut = build(2.25)   # vraie cote == produit -> indépendantes (k=1) -> proba = produit (56 %), GARDÉ
    anti = build(2.70)   # vraie cote AU-DESSUS du produit -> ANTI-corrélées (k<1) -> ÉCARTÉ (garde-fou)
    assert corr and neut, "corrélé positif et indépendant (k>=1) doivent produire un combiné"
    assert anti is None, "anti-corrélé (k<1) = hedge same-match illogique -> doit être ÉCARTÉ"
    assert corr["prob"] == 67 and neut["prob"] == 56
    assert corr["prob"] > neut["prob"]
    # EV réelle = real × prob = produit_probas × produit_cotes -> quasi CONSTANTE (~1.27), indépendante de
    # la corrélation : la correction ne « crée » pas de value, elle corrige la PROBA affichée.
    for r in (corr, neut):
        assert abs(r["real_odds"] * r["prob"] / 100 - 1.27) <= 0.02


def _cands3():
    return [{"sel": "A", "cote": 1.70, "code": "X", "oid": 301, "prob": 70},
            {"sel": "B", "cote": 1.30, "code": "Y", "oid": 302, "prob": 80}]


def test_combo_rejette_domine_par_une_jambe(monkeypatch):
    """Un combiné dont la vraie cote ne dépasse pas d'au moins +10 % sa jambe la plus haute est DOMINÉ
    (jouer la jambe seule rapporte plus AVEC moins de risque) -> écarté. Cas réel signalé : Mexique
    combiné @1.47 alors que la jambe « Moins de 2.5 » vaut @1.58."""
    monkeypatch.setattr(analyses, "calibrated_conf", lambda prob, sport, code: None)
    monkeypatch.setattr(analyses, "combo_player_props_allowed", lambda: (True, ""))
    gen._CATALOG_CACHE.pop("E2", None)               # pas de catalogue -> _leg_odds = cote POOL (max = 1.70)

    def build(real):
        monkeypatch.setattr(gen.unibet, "betbuilder_odds", lambda eid, oids: real)
        return gen._build_combo_from_pool("E2", _cands3(), "basket")

    assert build(1.85) is None       # 1.85 < 1.70×1.10=1.87 -> DOMINÉ (passe pourtant la fourchette) -> écarté
    assert build(2.05) is not None   # 2.05 > 1.87 -> le combiné apporte un vrai gain de cote -> retenu


def test_combopick_designation(monkeypatch):
    """Fix B : l'analyste DÉCIDE son combiné via `COMBOPICK:`. La désignation est prioritaire, mais passe
    quand même les filtres logique (corrélation) -> une désignation incohérente est écartée."""
    monkeypatch.setattr(analyses, "calibrated_conf", lambda prob, sport, code: None)
    monkeypatch.setattr(analyses, "combo_player_props_allowed", lambda: (True, ""))
    O1, O2 = 100001, 100002
    gen._CATALOG_CACHE["E"] = [{"id": O1, "text": "Favori ne perd pas", "odds": 1.30},
                               {"id": O2, "text": "Favori gagne une MT", "odds": 1.55}]
    pool = (f"POOL: Favori ne perd pas @1.30 [{O1}] (78%) - x\n"
            f"POOL: Favori gagne une MT @1.55 [{O2}] (70%) - y\n")

    def mk(desig, comp="Ligue", real=1.90):
        monkeypatch.setattr(gen.unibet, "betbuilder_odds", lambda eid, oids: real)
        return gen._make_combo(pool + f"PICK: WIN HOME\n{desig}\n", "foot", "A", "B", event_id="E", comp=comp)

    assert mk("COMBOPICK: 100001+100002") is not None          # corrélé (real<produit -> k>1) -> retenu
    assert mk("COMBOPICK: NONE") is None                        # abstention explicite hors CdM -> respectée
    assert mk("COMBOPICK: NONE", comp="Coupe du Monde 2026") is not None   # CdM -> combiné garanti
    assert mk("COMBOPICK: 100001+100002", real=2.20) is None    # anti-corrélé (real>produit -> k<1) -> écarté
