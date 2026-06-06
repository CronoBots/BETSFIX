"""Tests du rendu markdown->HTML des analyses pré-générées + chargement."""

import xml.etree.ElementTree as ET

from app import analyses

SAMPLE = """<!-- généré 2026-06-06 -->
# Belgique - Tunisie
## Faits
- **Belgique** forte à domicile
- Tunisie peu prolifique

| Pari | Cote | Chance |
|---|---|---|
| Belgique | 1.23 | 73% |
| Moins de 3.5 | 1.41 | 72% |

> Attention : c'est un amical.

Voir [Sofascore](https://www.sofascore.com/x).
"""


def test_md_to_html_renders_subset():
    h = analyses.to_html(SAMPLE)
    assert "<!--" not in h                              # en-tête commentaire viré
    assert "<table" in h and "<th>Pari</th>" in h and "<td>1.23</td>" in h
    assert 'class="da-h da-h1"' in h and "<b>Belgique</b>" in h
    assert "<ul" in h and h.count("<li>") == 2
    assert "da-quote" in h
    assert 'href="https://www.sofascore.com/x"' in h and "Sofascore" in h
    ET.fromstring("<root>" + h + "</root>")             # HTML bien formé


def test_load_and_render_missing():
    assert analyses.load("foot", "nope_999") is None
    assert analyses.render("foot", None) is None
    assert analyses.render("foot", "nope_999") is None
