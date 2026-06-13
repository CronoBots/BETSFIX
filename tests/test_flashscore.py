"""Tests des fonctions PURES de app/flashscore.py (aucun appel réseau)."""

from app import flashscore as fs


def test_game_winner_depuis_les_points():
    assert fs._game_winner("0:15, 15:15, 30:15, 40:15") == "home"   # home mène 40-15
    assert fs._game_winner("15:0, 0:40, 15:40") == "away"
    assert fs._game_winner("40:40, A:40") == "home"                 # avantage home
    assert fs._game_winner("40:40, 40:A") == "away"
    assert fs._game_winner("30:30") is None                         # égalité non tranchée
    assert fs._game_winner("") is None


def test_games_parse_un_feed_synthetique():
    # 3 jeux : home sert+gagne, away sert+gagne, home sert et se fait BREAK (HK=2)
    feed = ("~HC÷0¬HG÷1¬HK÷1¬HL÷15:0, 30:0, 40:0"
            "¬~HC÷1¬HG÷2¬HK÷2¬HL÷0:15, 0:30, 0:40"
            "¬~HC÷2¬HG÷1¬HK÷2¬HL÷15:0, 15:40")
    games = fs._parse_games(feed)
    assert len(games) == 3
    assert games[0] == {"server": "home", "winner": "home"}   # home tient
    assert games[1] == {"server": "away", "winner": "away"}
    assert games[2] == {"server": "home", "winner": "away"}   # home breaké


def test_settle_first_service_logique(monkeypatch):
    games = [{"server": "away", "winner": "away"},     # jeu 1 : away sert et tient
             {"server": "home", "winner": "away"},     # jeu 2 : home sert et se fait breaker
             {"server": "away", "winner": "home"}]
    monkeypatch.setattr(fs, "_find_match_id", lambda h, a, s=None: "X")
    monkeypatch.setattr(fs, "_games", lambda mid: games)
    # 1er jeu de service de HOME = jeu 2 -> perdu (breaké)
    assert fs.settle_hold1("A", "B", "HOME") == "lost"
    # 1er jeu de service de AWAY = jeu 1 -> tenu
    assert fs.settle_hold1("A", "B", "AWAY") == "won"
