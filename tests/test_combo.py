"""Tests du COMBINÉ « grand tournoi » : détection tournoi + parsing de la ligne COMBO."""

import importlib.util
import os

# generate_analyses est dans tools/ -> import par chemin
_spec = importlib.util.spec_from_file_location(
    "generate_analyses",
    os.path.join(os.path.dirname(__file__), "..", "tools", "generate_analyses.py"))
ga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ga)


def test_is_big_match():
    # Coupe du Monde 2026 UNIQUEMENT (plus les autres grandes compétitions)
    assert ga._is_big_match("Coupe du Monde 2026")
    assert ga._is_big_match("FIFA World Cup")
    assert not ga._is_big_match("Ligue des Champions")
    assert not ga._is_big_match("UEFA Champions League")
    assert not ga._is_big_match("Primera B Argentina")
    assert not ga._is_big_match("")


def test_parse_combo():
    analysis = (
        "## 🎲 Combiné\n"
        "- Suisse plus de 0.5 carton @1.30\n- Plus de 2.5 cartons @1.45\n- Suisse premier but @1.70\n"
        "**Cote combinée : 3.20**\n\n"
        "PICK: WIN AWAY\n"
        "COMBO: Suisse plus de 0.5 carton @1.30 | Plus de 2.5 cartons @1.45 | Suisse premier but @1.70 = 3.20\n")
    c = ga._parse_combo(analysis, "foot", "Qatar", "Suisse")
    assert c is not None
    assert len(c["legs"]) == 3
    assert c["legs"][0]["cote"] == 1.30
    assert round(c["total"], 2) == round(1.30 * 1.45 * 1.70, 2)
    assert all("code" in leg for leg in c["legs"])


def test_parse_combo_entoure_de_backticks():
    # l'analyste entoure parfois la ligne de `code` -> le préfixe backtick ne doit PAS casser le parse
    analysis = "`COMBO: Total Moins de 3.5 @1.12 | Corners MT1 Moins de 5.5 @1.22 = 1.37`\n"
    c = ga._parse_combo(analysis, "foot", "A", "B")
    assert c is not None and len(c["legs"]) == 2 and c["legs"][0]["cote"] == 1.12


def test_parse_combo_absent():
    assert ga._parse_combo("PICK: WIN HOME\n", "foot", "A", "B") is None
    # une seule jambe -> pas un combiné
    assert ga._parse_combo("COMBO: X @1.5 = 1.5\n", "foot", "A", "B") is None
