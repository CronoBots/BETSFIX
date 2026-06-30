"""Paywall abonnement (Phase 1) : comptes, sessions signées, et masquage serveur des pronos.

On vérifie le CŒUR de sécurité : un non-abonné ne reçoit JAMAIS les octets du pari (remplacés par
le cache « 🔒 abonnés »), tandis qu'abonné/propriétaire voient le pari (marqueurs simplement retirés).
"""
import os
import tempfile

import pytest

from app import accounts, paywall


@pytest.fixture
def store(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(accounts, "_STORE", os.path.join(d, "accounts.json"))
    monkeypatch.setattr(accounts, "_SECRET_FILE", os.path.join(d, ".secret"))
    monkeypatch.setenv("BETSFIX_SESSION_SECRET", "test-secret-123")
    monkeypatch.setattr(accounts, "_OWNERS_ENV", set())
    monkeypatch.setattr(accounts, "_OWNERS_FILE", os.path.join(d, "owners.json"))
    return d


class _Req:
    def __init__(self, host="1.2.3.4", cf=None, cookie=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {"cf-connecting-ip": cf} if cf else {}
        self.cookies = {accounts.COOKIE: cookie} if cookie else {}


def test_compte_creation_et_login(store):
    ok, err = accounts.create_user("Jean@Mail.com", "motdepasse")
    assert ok and not err
    assert accounts.create_user("jean@mail.com", "autre123")[0] is False   # doublon
    assert accounts.create_user("bad", "motdepasse")[0] is False           # email invalide
    assert accounts.create_user("ok@mail.com", "court")[0] is False        # mdp trop court
    assert accounts.verify_login("jean@mail.com", "motdepasse") is True
    assert accounts.verify_login("jean@mail.com", "X") is False


def test_abonnement_et_session(store):
    accounts.create_user("a@b.com", "motdepasse")
    assert accounts.is_subscriber("a@b.com") is False
    accounts.set_subscription("a@b.com", True)
    assert accounts.is_subscriber("a@b.com") is True
    tok = accounts.make_session("a@b.com")
    assert accounts.read_session(tok) == "a@b.com"
    assert accounts.read_session(tok[:-2] + "zz") is None                  # signature falsifiée
    assert accounts.read_session("nimporte") is None


def test_can_see_picks(store):
    accounts.create_user("sub@b.com", "motdepasse")
    accounts.set_subscription("sub@b.com", True)
    accounts.create_user("free@b.com", "motdepasse")
    # propriétaire local -> toujours
    assert accounts.can_see_picks(_Req(host="127.0.0.1")) is True
    # public sans session -> non
    assert accounts.can_see_picks(_Req(host="9.9.9.9", cf="9.9.9.9")) is False
    # public abonné -> oui ; public non-abonné -> non
    assert accounts.can_see_picks(_Req(cf="9.9.9.9", cookie=accounts.make_session("sub@b.com"))) is True
    assert accounts.can_see_picks(_Req(cf="9.9.9.9", cookie=accounts.make_session("free@b.com"))) is False


def test_paywall_masque_le_prono():
    html = "AVANT" + paywall.wrap('<div class="da-combo ">Over 2.5 @1.80</div>') + "APRES"
    # abonné : pari visible, marqueurs retirés
    vu = paywall.apply(html, can_see=True)
    assert "Over 2.5" in vu and paywall.MARK_OPEN not in vu and "prono-lock" not in vu
    # non-abonné : AUCUN octet du pari, remplacé par le cache abonnés
    masque = paywall.apply(html, can_see=False)
    assert "Over 2.5" not in masque and "1.80" not in masque
    assert "prono-lock" in masque and paywall.MARK_OPEN not in masque
    assert masque.startswith("AVANT") and masque.endswith("APRES")


def test_paywall_sans_marqueur_inchange():
    assert paywall.apply("<div>stats publiques</div>", can_see=False) == "<div>stats publiques</div>"
