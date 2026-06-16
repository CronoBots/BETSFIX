"""Validation du pari par un PANEL de 3 agents (scan) : parsing des verdicts + règle de majorité 2/3."""

import asyncio
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "generate_analyses",
    os.path.join(os.path.dirname(__file__), "..", "tools", "generate_analyses.py"))
ga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ga)


def test_parse_validation():
    assert ga._parse_validation("VERDICT: VALIDÉ\nPROBA: 72\nRAISON: forme nette") == {
        "verdict": "valide", "prob": 72, "reason": "forme nette"}
    assert ga._parse_validation("**VERDICT :** REJETÉ\nPROBA: 58\nRAISON: blessure clé")["verdict"] == "rejete"
    # sortie illisible -> REJETÉ par prudence, proba None
    assert ga._parse_validation("n'importe quoi") == {"verdict": "rejete", "prob": None, "reason": ""}


def _run(monkey_outputs):
    """Exécute _validate_bet en remplaçant run_claude par une file de sorties prédéfinies."""
    outs = list(monkey_outputs)
    ga.run_claude = lambda *a, **k: outs.pop(0)
    bet = {"sel": "Équipe A vainqueur", "cote": 1.6}
    return asyncio.run(ga._validate_bet("DOSSIER…", bet, 70, "foot"))


def test_majorite_valide():
    r = _run(["VERDICT: VALIDÉ\nPROBA: 72\nRAISON: a",
              "VERDICT: VALIDÉ\nPROBA: 68\nRAISON: b",
              "VERDICT: REJETÉ\nPROBA: 61\nRAISON: c"])
    assert r["verdict"] == "valide" and r["n_ok"] == 2 and r["n"] == 3
    assert r["consensus_prob"] == round((72 + 68 + 61) / 3)


def test_majorite_rejete():
    r = _run(["VERDICT: REJETÉ\nPROBA: 60\nRAISON: a",
              "VERDICT: VALIDÉ\nPROBA: 66\nRAISON: b",
              "VERDICT: REJETÉ\nPROBA: 55\nRAISON: c"])
    assert r["verdict"] == "rejete" and r["n_ok"] == 1


def test_sortie_illisible_rejette():
    # 2 sorties vides (illisibles) + 1 valide -> rejet (prudence : illisible = rejet)
    r = _run(["", "VERDICT: VALIDÉ\nPROBA: 70\nRAISON: ok", ""])
    assert r["verdict"] == "rejete" and r["n_ok"] == 1
