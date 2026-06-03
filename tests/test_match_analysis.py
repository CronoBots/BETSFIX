"""Tests du générateur d'analyse rédigée (templaté, déterministe, sans réseau)."""

from app.match_analysis import _templated, _wrap


def test_value_outsider():
    b = {"sport": "tennis", "home": "Laura Mair", "away": "Yiming Dang",
         "favorite": "Laura Mair", "underdog": "Yiming Dang", "fav_prob": 0.62, "fav_odds": 1.5,
         "confidence": "moyenne", "value": {"name": "Yiming Dang", "odds": 2.48, "edge": 0.07},
         "surface": "dur", "surface_edge": True, "fav_form_wins": 4, "fav_form_n": 5,
         "h2h_fav": 2, "h2h_opp": 0, "match_id": 1027867062}
    txt = _templated(b)
    assert "Laura Mair" in txt and "62 %" in txt          # favori + proba
    assert "value" in txt.lower() and "2.48" in txt        # verdict value avec la cote
    assert "le dur" in txt                                 # genre correct (pas « la dur »)


def test_confiance_pure():
    b = {"favorite": "PSG", "underdog": "Lyon", "fav_prob": 0.71, "fav_odds": 1.4,
         "confidence": "élevée", "value": None, "fav_form_wins": 3, "fav_form_n": 5,
         "h2h_fav": 1, "h2h_opp": 3, "match_id": 42}
    txt = _templated(b)
    assert "PSG part large favori" in txt or "Net avantage à PSG" in txt
    assert "confiance" in txt.lower()                      # pari de confiance, pas value
    assert "value" not in txt.lower()


def test_match_ouvert_et_garde_fous():
    b = {"favorite": "A", "underdog": "B", "fav_prob": 0.51, "confidence": "faible",
         "value": None, "margin": 3, "public_fav": 0.8, "match_id": 7}
    txt = _templated(b)
    assert "ouvert" in txt.lower() or "serré" in txt.lower()
    assert "s'abstenir" in txt or "passer" in txt          # pas de pari
    assert "⚠️" in txt and "prudence" in txt               # garde-fou échantillon faible
    assert "public sur-mise" in txt                        # divergence public signalée


def test_deterministe():
    b = {"favorite": "X", "underdog": "Y", "fav_prob": 0.6, "match_id": 99}
    assert _templated(b) == _templated(b)                  # même entrée -> même texte


def test_wrap_html():
    out = _wrap("Texte d'analyse.", by_claude=False, tag=("💎 VALUE", "val"))
    assert "Notre analyse" in out and "Texte d'analyse." in out
    assert 'class="an-card"' in out and "💎 VALUE" in out   # carte premium + verdict
    assert _wrap("", by_claude=False) == ""                # vide -> rien


def test_classement_insight():
    # favori MIEUX classé -> appui
    b = {"favorite": "A", "underdog": "B", "fav_prob": 0.7, "fav_odds": 1.3,
         "fav_rank": 1, "dog_rank": 40, "match_id": 1}
    assert "Mieux classé" in _templated(b)
    # favori MOINS bien classé -> insight « pourtant » (le modèle aime l'outsider)
    b2 = {"favorite": "A", "underdog": "B", "fav_prob": 0.55, "fav_odds": 1.8,
          "fav_rank": 60, "dog_rank": 12, "match_id": 2}
    assert "moins bien classé" in _templated(b2)
    # écart de classement faible -> pas mentionné (pas de bruit)
    b3 = {"favorite": "A", "underdog": "B", "fav_prob": 0.6, "fav_rank": 10, "dog_rank": 13, "match_id": 3}
    assert "classé" not in _templated(b3)
