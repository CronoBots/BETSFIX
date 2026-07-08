"""FANTÔMES DU PICK PRÉCÉDENT (demande user 2026-07-08).

À la ré-analyse rapprochée (~1 h avant le coup d'envoi), le pari RETENU pour le ROI/stats est TOUJOURS
le DERNIER généré. Mais les prédictions conseillées AVANT ne doivent pas disparaître du calibrage : elles
sont reportées en « fantômes » (shadow) dans le nouveau sidecar, par UNION DE CODES (aucun doublon, donc
aucun double-comptage ROI puisque seul le nouveau pick finit dans `bets`)."""
import json
import os

import tools.generate_analyses as g


def _write(tmp, sport, fid, side):
    p = os.path.join(tmp, f"{sport}_{fid}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False)
    return p


def test_ancien_pick_reporte_en_fantome_si_change(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "OUT", str(tmp_path))
    sport, fid = "foot", "T1"
    # Nouveau sidecar : le pick a CHANGÉ (OVER 2.5), un seul shadow.
    _write(tmp_path, sport, fid, {
        "sport": sport, "id": fid, "pick": "Plus de 2.5 buts @1.9", "pick_code": "OVER 2.5",
        "shadow": [{"sel": "Plus de 2.5 buts", "cote": 1.9, "prob": 60, "code": "OVER 2.5", "result": None}]})
    # Ancien pick (du matin) : WIN HOME -> doit devenir fantôme ; OVER 2.5 déjà présent -> pas de doublon.
    old = {"shadow": [
        {"sel": "Equipe A gagne", "cote": 1.5, "prob": 70, "code": "WIN HOME", "result": None},
        {"sel": "Plus de 2.5 buts", "cote": 1.85, "prob": 58, "code": "OVER 2.5", "result": None}]}
    g._carry_shadow_from_old(sport, fid, old)

    res = json.load(open(os.path.join(tmp_path, f"{sport}_{fid}.json"), encoding="utf-8"))
    codes = sorted(s["code"] for s in res["shadow"])
    assert codes == ["OVER 2.5", "WIN HOME"]          # union, sans doublon OVER 2.5
    ghosts = [s for s in res["shadow"] if s.get("ghost_from") == "pre_refresh"]
    assert [(s["code"], s["prob"]) for s in ghosts] == [("WIN HOME", 70)]
    assert all(s["result"] is None for s in res["shadow"])   # pré-coup d'envoi


def test_report_fantome_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "OUT", str(tmp_path))
    sport, fid = "foot", "T2"
    _write(tmp_path, sport, fid, {
        "sport": sport, "id": fid,
        "shadow": [{"sel": "X", "cote": 2.0, "prob": 50, "code": "DRAW", "result": None}]})
    old = {"shadow": [{"sel": "A gagne", "cote": 1.6, "prob": 65, "code": "WIN HOME", "result": None}]}
    g._carry_shadow_from_old(sport, fid, old)
    g._carry_shadow_from_old(sport, fid, old)     # 2e appel -> aucun ajout
    res = json.load(open(os.path.join(tmp_path, f"{sport}_{fid}.json"), encoding="utf-8"))
    assert sorted(s["code"] for s in res["shadow"]) == ["DRAW", "WIN HOME"]


def test_pas_de_report_si_inchange(tmp_path, monkeypatch):
    """Prono INCHANGÉ à la ré-analyse : mêmes codes -> shadow inchangé (aucun fantôme ajouté)."""
    monkeypatch.setattr(g, "OUT", str(tmp_path))
    sport, fid = "foot", "T3"
    shadow = [{"sel": "A gagne", "cote": 1.5, "prob": 70, "code": "WIN HOME", "result": None}]
    _write(tmp_path, sport, fid, {"sport": sport, "id": fid, "shadow": list(shadow)})
    g._carry_shadow_from_old(sport, fid, {"shadow": list(shadow)})
    res = json.load(open(os.path.join(tmp_path, f"{sport}_{fid}.json"), encoding="utf-8"))
    assert len(res["shadow"]) == 1
    assert not any(s.get("ghost_from") for s in res["shadow"])
