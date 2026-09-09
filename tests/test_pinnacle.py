"""Tests de app/pinnacle.py — conversion cotes + de-vig/alignement (réseau mocké)."""

from app import pinnacle as pin


def test_dec_american_vers_decimal():
    assert pin._dec(273) == 3.73                 # +273 -> 3.73
    assert pin._dec(-569) == round(100 / 569 + 1, 4)
    assert pin._dec(100) == 2.0
    assert pin._dec(0) is None
    assert pin._dec(None) is None


def test_sharp_probs_devig_et_alignement(monkeypatch):
    # NOTRE home = Connecticut Sun, mais Pinnacle liste Indiana Fever en « home » -> doit RÉALIGNER.
    monkeypatch.setattr(pin, "_drop_iproyal", lambda: False)   # teste le CHEMIN iProyal (retiré par défaut)
    monkeypatch.setattr(pin, "_matchups",
                        lambda sport: [{"id": 1, "home": "Indiana Fever", "away": "Connecticut Sun"}])
    markets = [{"type": "moneyline", "period": 0, "prices": [
        {"designation": "home", "price": -360},   # Indiana Fever favori
        {"designation": "away", "price": 280}]}]   # Connecticut Sun outsider
    monkeypatch.setattr(pin, "_get", lambda path: markets)
    sp = pin.sharp_probs("Connecticut Sun", "Indiana Fever", "basket")
    assert sp is not None
    assert sp["home"] < sp["away"]                # notre home (Sun) = outsider -> proba basse
    assert abs((sp["home"] + sp["away"]) - 1.0) < 0.001   # probas justes -> somment à 1
    assert sp["draw"] is None and sp["margin"] > 0


def test_sharp_probs_foot_avec_nul(monkeypatch):
    monkeypatch.setattr(pin, "_drop_iproyal", lambda: False)   # teste le CHEMIN iProyal (retiré par défaut)
    monkeypatch.setattr(pin, "_matchups", lambda sport: [{"id": 9, "home": "Lyon", "away": "Paris"}])
    markets = [{"type": "moneyline", "period": 0, "prices": [
        {"designation": "home", "price": 200}, {"designation": "draw", "price": 240},
        {"designation": "away", "price": 130}]}]
    monkeypatch.setattr(pin, "_get", lambda path: markets)
    sp = pin.sharp_probs("Lyon", "Paris", "foot")
    assert sp["draw"] is not None
    assert abs((sp["home"] + sp["draw"] + sp["away"]) - 1.0) < 0.001


def test_sharp_probs_match_introuvable(monkeypatch):
    monkeypatch.setattr(pin, "_drop_iproyal", lambda: False)   # teste le CHEMIN iProyal (retiré par défaut)
    monkeypatch.setattr(pin, "_matchups", lambda sport: [])
    assert pin.sharp_probs("X", "Y", "basket") is None


def test_drop_iproyal_defaut_on(monkeypatch):
    # SOURCE iPROYAL RETIRÉE (défaut) : sharp_probs/markets délèguent à API-Football, catalogue coupé, ZÉRO
    # appel iProyal (`_matchups`/`_get` ne doivent JAMAIS être touchés). Réversible via BETSFIX_DROP_IPROYAL=0.
    monkeypatch.delenv("BETSFIX_DROP_IPROYAL", raising=False)
    assert pin._drop_iproyal() is True
    def _boom(*a, **k):
        raise AssertionError("chemin iProyal atteint alors que la source est retirée")
    monkeypatch.setattr(pin, "_matchups", _boom)
    monkeypatch.setattr(pin, "_get", _boom)
    monkeypatch.setattr(pin, "_af_anchor",
                        lambda h, a, ko: {"sp": {"home": 0.9, "draw": 0.06, "away": 0.04, "margin": 0.03},
                                          "smk": {"totals": {2.5: 0.55}, "spreads": {}}})
    assert pin.sharp_probs("A", "B", "foot", "2026-09-09T18:00:00Z")["home"] == 0.9
    assert pin.sharp_markets("A", "B", "foot", "2026-09-09T18:00:00Z")["totals"] == {2.5: 0.55}
    assert pin.refresh_catalog("foot") == 0                    # catalogue 40 Mo NON téléchargé
    monkeypatch.setenv("BETSFIX_DROP_IPROYAL", "0")
    assert pin._drop_iproyal() is False                        # réversible
