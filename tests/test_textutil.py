"""Tests de la normalisation de noms centralisée (app/textutil.py)."""

from app.textutil import fold, name_tokens, names_match, name_substring


def test_fold_strips_accents():
    assert fold("Menšík") == "mensik"
    assert fold("Tsitsipás") == "tsitsipas"
    assert fold("Comesaña") == "comesana"
    assert fold("") == ""


def test_name_tokens_drops_initials_and_punctuation():
    assert name_tokens("Mensik J.") == {"mensik"}          # initiale ignorée (len 1)
    assert name_tokens("Auger-Aliassime") == {"auger", "aliassime"}
    assert name_tokens("Atlanta Dream (W)") == {"atlanta", "dream"}  # marqueur (W) retiré
    assert "wu" in name_tokens("Yibing Wu")                # nom court légitime gardé


def test_names_match_true_on_discriminant():
    assert names_match(name_tokens("Jakub Menšík"), name_tokens("Mensik J."))
    assert names_match(name_tokens("Manchester United"), name_tokens("Man Utd Manchester"))
    assert names_match(name_tokens("Atlanta Dream (W)"), name_tokens("Atlanta Dream"))


def test_names_match_false_on_generic_only():
    # « united » seul ne suffit pas -> pas de faux positif
    assert not names_match(name_tokens("Manchester United"), name_tokens("Newcastle United"))
    assert not names_match(name_tokens("Real Madrid"), name_tokens("Real Sociedad"))
    assert not names_match(set(), {"x"})


def test_name_substring_accent_tolerant():
    assert name_substring("mensik", "Jakub Menšík")        # le bug d'origine
    assert name_substring("TSITSIPAS", "Stefanos Tsitsipás")
    assert not name_substring("federer", "Rafael Nadal")
