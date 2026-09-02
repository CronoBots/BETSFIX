"""Paywall abonnement (Phase 1) : comptes, sessions signées, et masquage serveur des pronos.

On vérifie le CŒUR de sécurité : un non-abonné ne reçoit JAMAIS les octets du pari (remplacés par
le cache « 🔒 abonnés »), tandis qu'abonné/propriétaire voient le pari (marqueurs simplement retirés).
"""
import os
import tempfile

import pytest

from app import accounts, paywall, userdb


@pytest.fixture
def store(monkeypatch):
    """Isole TOUT l'état des comptes dans un dossier temporaire.

    ⚠️ CORRIGÉ le 2026-09-02 : la fixture ne patchait que `accounts._STORE` (l'ancien fichier JSON).
    Depuis la migration en SQLite du 2026-08-27, les comptes vivent dans `userdb` -> les tests écrivaient
    dans la VRAIE base `data/users.db`. Conséquences observées : 4 comptes de test (`a@b.com`, `free@b.com`,
    `jean@mail.com`, `sub@b.com`) créés en production, et `test_compte_creation_et_login` qui ne passait
    QU'AU PREMIER LANCEMENT (ensuite « doublon »). On redirige donc `userdb._DB` et on force la
    reconnexion, avant ET après le test.
    """
    d = tempfile.mkdtemp()
    monkeypatch.setattr(accounts, "_STORE", os.path.join(d, "accounts.json"))
    monkeypatch.setattr(accounts, "_SECRET_FILE", os.path.join(d, ".secret"))
    monkeypatch.setenv("BETSFIX_SESSION_SECRET", "test-secret-123")
    monkeypatch.setattr(accounts, "_OWNERS_ENV", set())
    monkeypatch.setattr(accounts, "_OWNERS_FILE", os.path.join(d, "owners.json"))
    monkeypatch.setattr(userdb, "_DATA", d)
    monkeypatch.setattr(userdb, "_DB", os.path.join(d, "users.db"))
    monkeypatch.setattr(userdb, "_conn", None)          # force une NOUVELLE connexion sur la base temp
    yield d
    try:                                                # referme la connexion temp -> la prod reprend
        if userdb._conn is not None:
            userdb._conn.close()
    except Exception:
        pass
    userdb._conn = None


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


def test_essai_gratuit_automatique_a_l_inscription(store):
    """ESSAI 3 JOURS AUTO (user 2026-08-27) : une inscription donne accès tout de suite. C'est ce
    comportement qui faisait échouer les tests d'abonnement écrits AVANT — ils sont désormais explicites."""
    accounts.create_user("trial@b.com", "motdepasse")
    assert accounts.plan_of("trial@b.com") == "trial"
    assert accounts.is_subscriber("trial@b.com") is True      # l'essai OUVRE l'accès


def test_abonnement_et_session(store, monkeypatch):
    # On COUPE l'essai auto pour tester la logique d'ABONNEMENT seule (sinon tout compte neuf est
    # « abonné » via son essai, et l'assertion ci-dessous ne mesurerait plus rien).
    monkeypatch.setattr(accounts, "TRIAL_ON_SIGNUP", False)
    accounts.create_user("a@b.com", "motdepasse")
    assert accounts.is_subscriber("a@b.com") is False
    accounts.set_subscription("a@b.com", True)
    assert accounts.is_subscriber("a@b.com") is True
    tok = accounts.make_session("a@b.com")
    assert accounts.read_session(tok) == "a@b.com"
    assert accounts.read_session(tok[:-2] + "zz") is None                  # signature falsifiée
    assert accounts.read_session("nimporte") is None


def test_can_see_picks(store, monkeypatch):
    monkeypatch.setattr(accounts, "TRIAL_ON_SIGNUP", False)   # « free » doit rester SANS accès
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
