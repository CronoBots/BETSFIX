"""Tests de app/player_stats.py — formatage du bloc props (player_stats mocké, aucun réseau)."""

from app import player_stats as ps


def _fake(monkeypatch, data: dict):
    monkeypatch.setattr(ps, "player_stats", lambda name: data.get(name, {}))


def test_props_block_formate_moyenne_et_forme(monkeypatch):
    _fake(monkeypatch, {
        "Jalen Brunson": {"avg": {"points": 25.2, "passes": 6.4}, "games": 100, "season": "2025-26",
                          "last5": {"points": [36, 32, 20, 30, 15], "passes": [7, 5, 6, 2, 5]}},
    })
    out = ps.props_block(["Jalen Brunson"])
    assert "DONNÉES JOUEURS" in out
    assert "Jalen Brunson [100 m. 2025-26]" in out
    assert "pts 25.2 (5 der. 36/32/20/30/15)" in out
    assert "passes 6.4 (5 der. 7/5/6/2/5)" in out


def test_props_block_dedup_et_cap(monkeypatch):
    _fake(monkeypatch, {f"J{i}": {"avg": {"points": i}, "games": 10, "season": "2025-26",
                                  "last5": {"points": [i]}} for i in range(12)})
    # doublons ignorés + cap à max_players
    out = ps.props_block(["J1", "J1", "J2"] + [f"J{i}" for i in range(12)], max_players=3)
    assert out.count("- J") == 3


def test_props_block_vide(monkeypatch):
    _fake(monkeypatch, {})
    assert ps.props_block(["Inconnu", "", None]) == ""
