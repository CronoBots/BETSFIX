"""Garde-fou de RÉGRESSION (bug 2026-09-07) : le vivier de sélection `match_candidates` ne doit
JAMAIS retenir un ghost `ghost_from=="pre_refresh"` (report de l'analyse PRÉCÉDENTE, calibration-only).
Sinon un vieux pari sûr est publié malgré une abstention fraîche (cas Cruz Azul–Santos).
Voir mémoire `selection-excludes-pre-refresh-ghosts`. Tests PURS (aucun réseau)."""

from app import confidence_pick as cp


def _sidecar(shadow):
    return {"sport": "foot", "home": "Cruz Azul", "away": "Santos Laguna", "shadow": shadow}


def test_match_candidates_exclut_ghost_pre_refresh():
    """Un DC sûr présent UNIQUEMENT en pre_refresh -> aucun candidat -> pas de pari (abstention)."""
    d = _sidecar([
        {"sel": "Cruz Azul ou nul (double chance)", "prob": 84, "cote": 1.14,
         "result": None, "ghost_from": "pre_refresh"},
        {"sel": "Cruz Azul gagne", "prob": 70, "cote": 1.33, "result": None},   # 1X2 = hors marché Confiance
    ])
    cands = cp.match_candidates(d)                     # marchés défaut = DC/Handicap
    assert all("double chance" not in c["sel"].lower() for c in cands), \
        "un ghost pre_refresh a fuité dans le vivier de sélection"
    assert cp.pick_from_candidates(cands) is None      # rien d'éligible -> abstention (comme la fiche QC)


def test_match_candidates_prefere_le_ghost_frais():
    """Même sélection en frais (82) ET pre_refresh (84 plus haut) : seul le FRAIS survit (pre_refresh ignoré
    AVANT le dédup, donc le 84 périmé ne l'emporte pas)."""
    d = _sidecar([
        {"sel": "Cruz Azul ou nul (double chance)", "prob": 82, "cote": 1.16, "result": None},           # frais
        {"sel": "Cruz Azul ou nul (double chance)", "prob": 84, "cote": 1.14,
         "result": None, "ghost_from": "pre_refresh"},                                                    # périmé
    ])
    dc = [c for c in cp.match_candidates(d) if "double chance" in c["sel"].lower()]
    assert len(dc) == 1
    assert dc[0]["prob"] == 82.0 and dc[0]["cote"] == 1.16   # le frais, pas le pre_refresh
