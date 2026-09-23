"""Tests de app/pinnacle.py — ancre sharp iProyal RESTAURÉE (user 2026-09-23, ancienne logique).

iProyal est PRIMAIRE par défaut (flag dédié `BETSFIX_DROP_IPROYAL_SHARP=0`, découplé de SofaScore) ;
quand il est coupé (flag=1), `sharp_probs`/`sharp_markets` délèguent proprement à API-Football (`_af_anchor`)."""

from app import pinnacle as pin


def test_iproyal_actif_par_defaut():
    # GARANTIE du revert : par défaut l'ancre utilise iProyal (drop=False), plus la délégation API-Football.
    assert pin._drop_iproyal() is False


def test_delegue_a_apifootball_quand_iproyal_coupe(monkeypatch):
    # Flag dédié ON -> repli propre : sharp_probs/sharp_markets renvoient l'ancre API-Football (`_af_anchor`).
    monkeypatch.setattr(pin, "_drop_iproyal", lambda: True)
    monkeypatch.setattr(pin, "_af_anchor",
                        lambda h, a, ko: {"sp": {"home": 0.9, "draw": 0.06, "away": 0.04, "margin": 0.03},
                                          "smk": {"totals": {2.5: 0.55}, "spreads": {}}})
    sp = pin.sharp_probs("A", "B", "foot", "2026-09-09T18:00:00Z")
    assert sp == {"home": 0.9, "draw": 0.06, "away": 0.04, "margin": 0.03}
    assert pin.sharp_markets("A", "B", "foot", "2026-09-09T18:00:00Z")["totals"] == {2.5: 0.55}


def test_sharp_probs_none_si_pas_ancre(monkeypatch):
    # iProyal coupé + API-Football ne résout pas -> None (jamais d'erreur).
    monkeypatch.setattr(pin, "_drop_iproyal", lambda: True)
    monkeypatch.setattr(pin, "_af_anchor", lambda h, a, ko: None)
    assert pin.sharp_probs("X", "Y", "foot") is None
    assert pin.sharp_markets("X", "Y", "foot") is None


def test_refresh_catalog_noop_quand_iproyal_coupe(monkeypatch):
    # iProyal coupé -> plus de catalogue 40 Mo à télécharger -> no-op (0).
    monkeypatch.setattr(pin, "_drop_iproyal", lambda: True)
    assert pin.refresh_catalog("foot") == 0
