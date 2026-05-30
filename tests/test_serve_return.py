"""Tests du facteur service/retour (fonctions pures, sans réseau)."""

from app import serve_return as sr
from app.models import Match, Player


def _rec(dom, n, dom_clay=None, n_clay=0):
    return {"name": "X", "dom": dom, "dom_n": n, "dom_clay": dom_clay, "dom_clay_n": n_clay}


def test_prob_monotonic_and_symmetric():
    # home plus dominant -> proba > 0.5 ; inverse < 0.5
    assert sr.prob_from_serve_return(1.0, 0.8) > 0.6
    assert sr.prob_from_serve_return(0.8, 1.0) < 0.4
    # monotonie : plus l'écart de domination est grand, plus la proba grimpe
    assert sr.prob_from_serve_return(1.2, 0.8) > sr.prob_from_serve_return(1.0, 0.8)
    assert sr.prob_from_serve_return(None, 0.8) is None


def test_dominance_for_clay_fallback():
    rec = _rec(0.9, 100, dom_clay=0.7, n_clay=20)
    assert sr.dominance_for(rec, "Red clay") == 0.7      # assez de terre
    assert sr.dominance_for(rec, "Hard") == 0.9          # hors terre -> global
    thin = _rec(0.9, 100, dom_clay=0.7, n_clay=3)        # trop peu de terre
    assert sr.dominance_for(thin, "Red clay") == 0.9
    assert sr.dominance_for(None, "Hard") is None


def test_ratings_for_match():
    store = {"100": _rec(1.1, 50), "200": _rec(0.7, 50)}
    m = Match(id=1, tour="atp", ground_type="Red clay",
              home=Player(id=100, name="Big"), away=Player(id=200, name="Small"))
    dh, da = sr.ratings_for_match(m, store=store)
    assert dh == 1.1 and da == 0.7
    # joueur inconnu -> None de ce côté
    m2 = Match(id=2, tour="atp", home=Player(id=999), away=Player(id=200))
    assert sr.ratings_for_match(m2, store=store) == (None, 0.7)
