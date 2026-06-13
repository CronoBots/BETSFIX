"""Tests de l'analyse joueur étendue tennis (service, Flashscore) + foot (ESPN). Sans réseau."""

from app import flashscore as fs
from app import player_stats as ps

_F = chr(0x00f7)
_R = chr(0x00ac)


def _rec(**kv):
    return _R.join(f"{k}{_F}{v}" for k, v in kv.items())


def test_recent_match_ids_tennis():
    # df_hh synthétique : 2 matchs récents de A (home), 1 de B, puis Head-to-head (ignoré).
    feed = "~".join([
        _rec(KB="Last matches: A"),
        _rec(KP="m1", KS="home", KL="2:0"),
        _rec(KP="m2", KS="away", KL="0:2"),
        _rec(KB="Last matches: B"),
        _rec(KP="m3", KS="home", KL="2:1"),
        _rec(KB="Head-to-head matches"),
        _rec(KP="zz", KS="home", KL="2:0"),
    ])
    home, away = fs._recent_match_ids(feed)
    assert home == [("m1", "home"), ("m2", "away")]
    assert away == [("m3", "home")]


def test_serve_item_lecture_colonne():
    stats = {"sections": [{"name": "Match", "categories": [
        {"name": "Service", "items": [
            {"name": "Aces", "home": "7", "away": "3"},
            {"name": "1st serve percentage", "home": "62%", "away": "55%"}]}]}]}
    assert fs._serve_item(stats, "Aces", "home") == "7"
    assert fs._serve_item(stats, "Aces", "away") == "3"
    assert fs._serve_item(stats, "1st serve percentage", "home") == "62%"
    assert fs._serve_item(stats, "Double Faults", "home") is None


def test_soccer_props_block(monkeypatch):
    monkeypatch.setattr(ps, "soccer_player_stats", lambda n: {
        "Brahim Diaz": {"comp": "AFCON", "starts": 7, "goals": 5, "assists": 0,
                        "shots": 19, "shots_on_target": 10}}.get(n, {}))
    out = ps.soccer_props_block(["Brahim Diaz", "Inconnu"])
    assert "DONNÉES JOUEURS" in out
    assert "Brahim Diaz [7 match. AFCON]" in out
    assert "5 buts (0.71/match)" in out
    assert "19 tirs (2.7/match)" in out


def test_soccer_props_block_vide(monkeypatch):
    monkeypatch.setattr(ps, "soccer_player_stats", lambda n: {})
    assert ps.soccer_props_block(["X"]) == ""
