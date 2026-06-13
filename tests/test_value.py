"""Tests PURS de app/value.py (de-vig, EV, annotation) — aucun appel réseau."""

from app import value as v


def test_devig_deux_issues():
    fair, margin = v.devig([1.91, 1.91])
    assert round(fair[0], 3) == 0.5 and round(fair[1], 3) == 0.5
    assert round(margin, 3) == 0.047              # ~4.7 % d'overround
    assert abs(sum(fair) - 1.0) < 1e-9            # les probas justes somment à 1


def test_devig_favori():
    fair, margin = v.devig([1.49, 2.65])
    assert round(fair[0], 2) == 0.64 and round(fair[1], 2) == 0.36
    assert margin > 0


def test_devig_vide_ou_invalide():
    assert v.devig([]) == ([], 0.0)
    assert v.devig([1.0, 0])[0] == []             # cotes <= 1 ignorées -> rien à de-viger


def test_ev():
    assert round(v.ev(0.72, 1.49), 3) == 0.073    # value +7.3 %
    assert v.ev(0.50, 2.00) == 0.0                # cote = proba juste -> EV nul
    assert v.ev(0, 2.0) == 0.0


def test_annotate_ajoute_proba_et_cote_juste():
    outs = [{"odds": 1.49}, {"odds": 2.65}]
    annotated, margin = v.annotate(outs)
    assert round(annotated[0]["fair_prob"], 2) == 0.64
    assert annotated[0]["fair_odds"] == round(1 / annotated[0]["fair_prob"], 2)
    assert margin > 0
