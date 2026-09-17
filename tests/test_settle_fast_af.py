"""Passe de règlement RAPIDE `settle_analyses(af_only=True)` (user 2026-09-17) : régler tout de suite ce
qu'API-Football fournit seul, SANS attendre la boucle lente 10 min, SANS replis ni compteurs brûlés.

Deux invariants :
  A) un match foot FT chez API-Football est réglé dans la passe rapide (résultat écrit) ;
  B) un match qu'API-Football ne résout PAS encore est laissé INTACT (aucun repli, aucun result écrit,
     aucun compteur d'essai incrémenté) -> la boucle lente s'en chargera.
"""

import asyncio
import json
import os

from app import analyses, notify, settle_analyst


def _sidecar(dir_: str) -> str:
    side = os.path.join(dir_, "foot_fast1.json")
    d = {
        "sport": "foot", "id": "fast1", "home": "Alpha", "away": "Beta",
        "comp": "Test", "start": "2020-01-01T12:00:00Z",
        "pick": "1 @ 1.50", "pick_code": "1",
        "pub_home": 50, "pub_away": 50,            # public présent -> pas de backfill même en passe lente
    }
    json.dump(d, open(side, "w", encoding="utf-8"), ensure_ascii=False)
    return side


def _load(side: str) -> dict:
    return json.load(open(side, encoding="utf-8"))


def _common(monkeypatch, tmp_path):
    monkeypatch.setattr(analyses, "DIR", str(tmp_path))
    monkeypatch.setattr(analyses, "bets_of", lambda sport, mid: [])
    monkeypatch.setattr(analyses, "retained_bet", lambda s, m, **kw: {"result": "won"})
    monkeypatch.setattr(analyses, "status_of", lambda d: "finished")
    monkeypatch.setattr(analyses, "likely_finished", lambda d: True)
    monkeypatch.setattr(settle_analyst, "_APIFOOTBALL_SETTLE", True)
    monkeypatch.setattr(notify, "configured", lambda: False)   # on teste le RÈGLEMENT, pas le transport notif


def test_af_only_settles_from_apifootball(tmp_path, monkeypatch):
    """A) API-Football renvoie le score FT -> la passe rapide règle le pick (résultat écrit)."""
    side = _sidecar(str(tmp_path))
    _common(monkeypatch, tmp_path)
    monkeypatch.setattr(settle_analyst, "settle_pick", lambda c, score: "won")
    monkeypatch.setattr(settle_analyst, "_apifootball_score",
                        lambda d, cache: {"label": "1-0", "home": 1, "away": 0, "winner": "home",
                                          "src": "apifootball", "reg_home": 1, "reg_away": 0,
                                          "periods": {"1": [1, 0], "2": [0, 0]}})
    n = asyncio.run(settle_analyst._settle_analyses_impl(af_only=True))
    assert n == 1
    d = _load(side)
    assert d.get("result", {}).get("pick_result") == "won"
    assert d["result"]["raw"]["src"] == "apifootball"


def test_af_only_skips_when_apifootball_has_no_score(tmp_path, monkeypatch):
    """B) API-Football n'a pas (encore) le score -> passe rapide LAISSE le match intact (aucun repli, aucun
    result écrit, aucun compteur d'essai brûlé). Un repli appelé = échec du test."""
    side = _sidecar(str(tmp_path))
    _common(monkeypatch, tmp_path)
    monkeypatch.setattr(settle_analyst, "_apifootball_score", lambda d, cache: None)

    async def _boom(*a, **k):
        raise AssertionError("aucun repli (SofaScore/livescore/flashscore) ne doit être appelé en passe rapide")
    monkeypatch.setattr(settle_analyst, "_event_data", _boom)
    monkeypatch.setattr(settle_analyst, "_schedule_scores", _boom)

    n = asyncio.run(settle_analyst._settle_analyses_impl(af_only=True))
    assert n == 0
    d = _load(side)
    assert "result" not in d          # rien écrit
    assert "pick_tries" not in d      # aucun compteur d'essai brûlé
    assert "settle_v" not in d        # sidecar strictement intact
