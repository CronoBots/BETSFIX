"""Un combiné ne doit PAS être publié tant que CHAQUE jambe n'est pas validée — même si une jambe
déjà perdue le rend mathématiquement perdu (cas Équateur-Allemagne : « Temps réglementaire » perdu
mais « But dans les 2 mi-temps » sans résultat -> carte publiée avec une jambe sans ✅/❌).

On pilote le vrai `_settle_analyses_impl` HORS-LIGNE (résultats de jambe contrôlés) sur deux passes :
  1) la 2e jambe n'est pas encore réglable -> verdict global EN ATTENTE, rien n'est publié ;
  2) la 2e jambe se règle -> verdict « perdu », combiné publié (toutes les jambes marquées).
"""

import asyncio
import json
import os
import sys

from app import analyses, notify, settle_analyst

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import card_image  # noqa: E402


def _sidecar(dir_: str) -> str:
    side = os.path.join(dir_, "foot_cmb1.json")
    d = {
        "sport": "foot", "id": "cmb1", "home": "Equateur", "away": "Allemagne",
        "comp": "Coupe du monde 2026", "start": "2020-01-01T20:00:00Z",
        "pick": "1 @ 1.50", "pick_code": "1",
        "pub_home": 50, "pub_away": 50,
        "settle_v": settle_analyst._SETTLE_VERSION,
        "result": {"score": "2-1", "pick_result": "lost",
                   "raw": {"label": "2-1", "home": 2, "away": 1}},
        "notified_pick": False,
        "combo": {"total": 1.97, "legs": [
            {"sel": "Temps réglementaire Allemagne", "code": "L1", "cote": 1.6},
            {"sel": "But dans les deux mi-temps Oui", "code": "L2", "cote": 1.5}]},
    }
    json.dump(d, open(side, "w", encoding="utf-8"), ensure_ascii=False)
    return side


def _load(side):
    return json.load(open(side, encoding="utf-8"))


def test_combo_attend_chaque_jambe_avant_publication(tmp_path, monkeypatch):
    side = _sidecar(str(tmp_path))
    leg2 = {"v": None}                                # résultat de la 2e jambe, contrôlé

    monkeypatch.setattr(settle_analyst, "settle_pick",
                        lambda c, score: {"1": "lost", "L1": "lost", "L2": leg2["v"]}.get(c))
    # noms de jambe -> code stable (sinon code_from_pick redérive REGTIME… et casse le mapping du test)
    monkeypatch.setattr(settle_analyst, "code_from_pick",
                        lambda sel, *a, **k: {"Temps réglementaire Allemagne": "L1",
                                              "But dans les deux mi-temps Oui": "L2"}.get(sel, ""))
    # force le règlement de jambe par CODE (pas par métrique live) -> déterministe
    monkeypatch.setattr(analyses, "_leg_metric", lambda leg, h, a: {"live_ok": False})
    monkeypatch.setattr(analyses, "DIR", str(tmp_path))
    monkeypatch.setattr(analyses, "bets_of", lambda sport, mid: [])
    monkeypatch.setattr(analyses, "status_of", lambda d: "finished")
    monkeypatch.setattr(analyses, "likely_finished", lambda d: True)
    monkeypatch.setattr(analyses, "retained_bet", lambda s, m: None)   # simple NON affiché -> isole le combiné

    monkeypatch.setattr(notify, "configured", lambda: True)
    monkeypatch.setattr(notify, "get_prono", lambda mid: None)

    async def _fake_render(card, png):
        return None
    monkeypatch.setattr(card_image, "render_card", _fake_render)

    photo = {"n": 0}

    def _fake_photo(png, caption="", reply_to=None):
        photo["n"] += 1
        return {"chat": 1}
    monkeypatch.setattr(notify, "send_photo_sync", _fake_photo)

    async def _fake_send(text, clean=False):
        return True
    monkeypatch.setattr(notify, "send", _fake_send)

    # ---- passe 1 : la 2e jambe n'est PAS réglable -> verdict en attente, RIEN n'est publié ----
    asyncio.run(settle_analyst._settle_analyses_impl())
    d1 = _load(side)
    assert d1["combo"]["legs"][0]["result"] == "lost", "1re jambe réglée perdue"
    assert d1["combo"]["legs"][1]["result"] is None, "2e jambe pas encore réglable"
    assert d1["combo"].get("result") is None, "verdict global EN ATTENTE tant qu'une jambe manque"
    assert not d1.get("notified_combo"), "combiné PAS publié tant qu'une jambe n'est pas validée"
    assert d1.get("combo_tries") == 1
    assert photo["n"] == 0, "aucune carte combiné envoyée"

    # ---- passe 2 : la 2e jambe se règle -> verdict « perdu », combiné publié (jambes complètes) ----
    leg2["v"] = "won"
    asyncio.run(settle_analyst._settle_analyses_impl())
    d2 = _load(side)
    assert d2["combo"]["legs"][1]["result"] == "won", "2e jambe désormais réglée"
    assert d2["combo"]["result"] == "lost", "une jambe perdue -> combiné perdu, MAINTENANT que tout est réglé"
    assert d2.get("notified_combo") is True, "combiné publié une fois CHAQUE jambe validée"
    assert photo["n"] == 1, "exactement une carte combiné envoyée"


def test_combo_jamais_publie_si_jambe_jamais_validable(tmp_path, monkeypatch):
    """Consigne stricte : si une jambe reste NON validable, on NE publie PAS une carte incomplète —
    et on ne boucle pas indéfiniment (essais bornés à 8 puis le sidecar est laissé en attente)."""
    side = _sidecar(str(tmp_path))
    monkeypatch.setattr(settle_analyst, "settle_pick",
                        lambda c, score: {"1": "lost", "L1": "lost", "L2": None}.get(c))
    monkeypatch.setattr(settle_analyst, "code_from_pick",
                        lambda sel, *a, **k: {"Temps réglementaire Allemagne": "L1",
                                              "But dans les deux mi-temps Oui": "L2"}.get(sel, ""))
    monkeypatch.setattr(analyses, "_leg_metric", lambda leg, h, a: {"live_ok": False})
    monkeypatch.setattr(analyses, "DIR", str(tmp_path))
    monkeypatch.setattr(analyses, "bets_of", lambda sport, mid: [])
    monkeypatch.setattr(analyses, "status_of", lambda d: "finished")
    monkeypatch.setattr(analyses, "likely_finished", lambda d: True)
    monkeypatch.setattr(analyses, "retained_bet", lambda s, m: None)
    monkeypatch.setattr(notify, "configured", lambda: True)
    monkeypatch.setattr(notify, "get_prono", lambda mid: None)

    async def _fake_render(card, png):
        return None
    monkeypatch.setattr(card_image, "render_card", _fake_render)
    monkeypatch.setattr(notify, "send_photo_sync", lambda png, caption="", reply_to=None: {"chat": 1})

    async def _fake_send(text, clean=False):
        return True
    monkeypatch.setattr(notify, "send", _fake_send)

    photo = {"n": 0}
    monkeypatch.setattr(notify, "send_photo_sync",
                        lambda png, caption="", reply_to=None: photo.__setitem__("n", photo["n"] + 1) or {"chat": 1})

    for _ in range(10):
        asyncio.run(settle_analyst._settle_analyses_impl())
    d = _load(side)
    assert d.get("combo_tries") == 8, "essais bornés à 8 (pas de boucle infinie)"
    assert d["combo"]["result"] is None, "verdict jamais tranché tant qu'une jambe n'est pas validée"
    assert not d.get("notified_combo"), "JAMAIS publié si une jambe n'est pas validable"
    assert photo["n"] == 0, "aucune carte combiné envoyée"
