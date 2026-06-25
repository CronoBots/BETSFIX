"""R2 — la notification Telegram d'un pari réglé ne doit JAMAIS être perdue par un crash entre la
persistance du résultat et l'envoi, ni JAMAIS être envoyée deux fois.

On pilote le vrai `_settle_analyses_impl` HORS-LIGNE (score lu depuis `result.raw` -> zéro réseau)
et on contrôle le succès/échec de l'envoi Telegram. Scénario de crash en 3 passes :
  1) envoi ÉCHOUE (= crash avant/pendant l'envoi)  -> flag NON posé, à ré-émettre
  2) envoi RÉUSSIT                                  -> flag posé, notif (enfin) partie
  3) rien à faire                                   -> AUCUN nouvel envoi (zéro doublon)
"""

import asyncio
import json
import os
import sys

import pytest

from app import analyses, notify, settle_analyst

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import card_image  # noqa: E402


def _sidecar(dir_: str) -> str:
    side = os.path.join(dir_, "foot_test1.json")
    d = {
        "sport": "foot", "id": "test1", "home": "Alpha", "away": "Beta",
        "comp": "Test", "start": "2020-01-01T12:00:00Z",
        "pick": "1 @ 1.50", "pick_code": "1",
        "pub_home": 50, "pub_away": 50,            # public présent -> pas de backfill réseau
        "settle_v": settle_analyst._SETTLE_VERSION,  # déjà à jour -> SEUL notify_pending le ré-ouvre
        "result": {"score": "1-0", "pick_result": "won",
                   "raw": {"label": "1-0", "home": 1, "away": 0}},  # raw présent -> zéro réseau
        "notified_pick": False,
    }
    json.dump(d, open(side, "w", encoding="utf-8"), ensure_ascii=False)
    return side


def _load(side: str) -> dict:
    return json.load(open(side, encoding="utf-8"))


def test_notif_zero_perte_zero_doublon(tmp_path, monkeypatch):
    side = _sidecar(str(tmp_path))

    # --- règlement déterministe, hors-ligne ---
    monkeypatch.setattr(settle_analyst, "settle_pick", lambda c, score: "won")
    monkeypatch.setattr(analyses, "DIR", str(tmp_path))
    monkeypatch.setattr(analyses, "bets_of", lambda sport, mid: [])
    monkeypatch.setattr(analyses, "status_of", lambda d: "finished")
    monkeypatch.setattr(analyses, "likely_finished", lambda d: True)

    # --- Telegram piloté ---
    monkeypatch.setattr(notify, "configured", lambda: True)
    monkeypatch.setattr(notify, "get_prono", lambda mid: None)

    async def _fake_render(card, png):           # pas de Chrome : on évite le rendu réel
        return None
    monkeypatch.setattr(card_image, "render_card", _fake_render)

    photo_calls = {"n": 0}
    send_ok = {"v": False}

    def _fake_photo(png, caption="", reply_to=None):
        photo_calls["n"] += 1
        return {"chat": 1} if send_ok["v"] else {}   # {} = échec (falsy)
    monkeypatch.setattr(notify, "send_photo_sync", _fake_photo)

    async def _fake_send(text, clean=False):
        return send_ok["v"]                          # repli texte : suit le même sort
    monkeypatch.setattr(notify, "send", _fake_send)

    # ---- passe 1 : l'envoi ÉCHOUE (crash simulé) -> rien n'est figé, à ré-émettre ----
    send_ok["v"] = False
    asyncio.run(settle_analyst._settle_analyses_impl())
    d1 = _load(side)
    assert photo_calls["n"] == 1, "la notif doit avoir été TENTÉE une fois"
    assert d1.get("notified_pick") is False, "envoi échoué -> flag PAS posé (sinon notif perdue)"
    assert d1.get("notify_tries") == 1

    # ---- passe 2 : l'envoi RÉUSSIT -> flag figé, notif enfin partie ----
    send_ok["v"] = True
    asyncio.run(settle_analyst._settle_analyses_impl())
    d2 = _load(side)
    assert photo_calls["n"] == 2, "la notif perdue doit être RÉ-ÉMISE à la passe suivante"
    assert d2.get("notified_pick") is True, "envoi réussi -> flag posé (idempotence)"
    assert d2.get("notify_tries") == 2

    # ---- passe 3 : plus rien à faire -> AUCUN nouvel envoi (zéro doublon) ----
    asyncio.run(settle_analyst._settle_analyses_impl())
    d3 = _load(side)
    assert photo_calls["n"] == 2, "déjà notifié -> JAMAIS de second envoi (zéro doublon)"
    assert d3.get("notified_pick") is True


def test_notif_borne_apres_5_echecs(tmp_path, monkeypatch):
    """Garde-fou : si Telegram reste injoignable, on n'essaie pas indéfiniment (notify_tries < 5)."""
    side = _sidecar(str(tmp_path))
    monkeypatch.setattr(settle_analyst, "settle_pick", lambda c, score: "won")
    monkeypatch.setattr(analyses, "DIR", str(tmp_path))
    monkeypatch.setattr(analyses, "bets_of", lambda sport, mid: [])
    monkeypatch.setattr(analyses, "status_of", lambda d: "finished")
    monkeypatch.setattr(analyses, "likely_finished", lambda d: True)
    monkeypatch.setattr(notify, "configured", lambda: True)
    monkeypatch.setattr(notify, "get_prono", lambda mid: None)

    async def _fake_render(card, png):
        return None
    monkeypatch.setattr(card_image, "render_card", _fake_render)

    calls = {"n": 0}

    def _fake_photo(png, caption="", reply_to=None):
        calls["n"] += 1
        return {}                                    # échoue toujours
    monkeypatch.setattr(notify, "send_photo_sync", _fake_photo)

    async def _fake_send(text, clean=False):
        return False
    monkeypatch.setattr(notify, "send", _fake_send)

    for _ in range(8):                               # 8 passes, mais borné à 5 tentatives
        asyncio.run(settle_analyst._settle_analyses_impl())
    assert calls["n"] == 5, "le re-traitement « réglé non notifié » est borné à 5 essais"
    assert _load(side).get("notify_tries") == 5
