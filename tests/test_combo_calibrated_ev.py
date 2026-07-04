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
    return [{"sel": "A", "cote": 2.0, "code": "OVER 2.5", "oid": 101, "prob": 80},
            {"sel": "B", "cote": 2.0, "code": "DC 1X", "oid": 102, "prob": 80},
            {"sel": "C", "cote": 2.0, "code": "1X2 1", "oid": 103, "prob": 80}]


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
