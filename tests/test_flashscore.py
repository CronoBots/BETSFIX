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


def test_parse_prematch_forme_et_h2h():
    # Feed df_hh synthétique : 2 derniers de A, 2 derniers de B, puis H2H (gagnant marqué « * »).
    # Sections « filtrées par lieu » répétées APRÈS le 1er H2H : doivent être ignorées.
    R = "¬"
    feed = (
        f"KA{chr(0x00f7)}Overall"
        f"¬~KB{chr(0x00f7)}Last matches: A¬~KC{chr(0x00f7)}1¬WIS{chr(0x00f7)}w¬KL{chr(0x00f7)}2:0"
        f"¬~KC{chr(0x00f7)}2¬WIS{chr(0x00f7)}l¬KL{chr(0x00f7)}0:1"
        f"¬~KB{chr(0x00f7)}Last matches: B¬~KC{chr(0x00f7)}3¬WIS{chr(0x00f7)}wo¬KL{chr(0x00f7)}3:2"
        f"¬~KB{chr(0x00f7)}Head-to-head matches"
        f"¬~KC{chr(0x00f7)}9¬KJ{chr(0x00f7)}*A¬KK{chr(0x00f7)}B¬KL{chr(0x00f7)}2:1"
        f"¬~KC{chr(0x00f7)}8¬KJ{chr(0x00f7)}A¬KK{chr(0x00f7)}*B¬KL{chr(0x00f7)}0:3"
        # répétition filtrée par lieu -> ignorée
        f"¬~KB{chr(0x00f7)}Last matches: A¬~KC{chr(0x00f7)}99¬WIS{chr(0x00f7)}l¬KL{chr(0x00f7)}0:9"
    )
    assert R  # le séparateur ¬ est bien dans le feed
    d = fs._parse_prematch(feed)
    assert [r["res"] for r in d["home_form"]] == ["w", "l"]          # B "filtré" non ajouté à A
    assert [r["res"] for r in d["away_form"]] == ["wo"]
    assert len(d["h2h"]) == 2
    assert d["h2h"][0] == {"score": "2:1", "winner_name": "A", "a": "A", "b": "B"}
    assert d["h2h"][1]["winner_name"] == "B"


def test_lineups_parse(monkeypatch):
    F, R = chr(0x00f7), chr(0x00ac)
    # feed > 100 car. (seuil de garde de lineups) : formations home/away + répétition à dédupliquer
    pad = R.join(f"LN{F}Joueur{i}" for i in range(12))
    feed = (f"LA{F}Formation{R}LB{F}Starting Lineups{R}LD{F}1-4-3-3{R}{pad}{R}"
            f"LD{F}1-3-5-2{R}LD{F}1-4-3-3{R}")
    monkeypatch.setattr(fs, "_feed", lambda code, mid: feed)
    lu = fs.lineups("ID")
    assert lu["home_formation"] == "1-4-3-3" and lu["away_formation"] == "1-3-5-2"
    assert lu["status"] == "Starting Lineups"
    monkeypatch.setattr(fs, "_find_match_id", lambda h, a, s=None, sport="football": "ID")
    f = fs.lineup_facts("Lyon", "Paris")
    assert f and "Lyon en 1-4-3-3" in f[0] and "Paris en 1-3-5-2" in f[0] and "confirmées" in f[0]


def test_goals_for_against_et_tendances():
    # buts pour/contre selon le côté du sujet
    assert fs._goals_for_against("2:1", "home") == (2, 1)
    assert fs._goals_for_against("2:1", "away") == (1, 2)
    assert fs._goals_for_against("x", "home") == (None, None)
    # tendances foot : moyennes + % +2.5 / BTTS
    rows = [{"gf": 2, "ga": 1}, {"gf": 0, "ga": 0}, {"gf": 3, "ga": 2}, {"gf": 1, "ga": 1}]
    t = fs._tendencies(rows, "foot")
    assert "1.5 buts marqués/match" in t and "1.0 encaissés" in t
    assert "50% +2.5 buts" in t          # 2 matchs sur 4 ont total >= 3
    assert "75% BTTS" in t               # 3 matchs sur 4 ont les 2 équipes qui marquent
    assert "pts marqués/match" in fs._tendencies(rows, "basket") and "total moyen" in fs._tendencies(rows, "basket")
    assert fs._tendencies(rows[:2], "foot") is None      # <3 matchs -> None


def test_final_score_ignore_match_en_cours(monkeypatch):
    """Régression 2026-07-01 : un match LIVE (statut Flashscore AB=2) a DÉJÀ un score partiel dans
    l'index -> `final_score` doit s'ABSTENIR (None), sinon règlement FAUX. Terminé (AB=3) -> score."""
    d = {"sport": "foot", "home": "Angleterre", "away": "Congo DR", "start": "2026-07-01T16:00:00Z"}
    row = lambda status, hs, as_: [{"id": "X", "home": "Angleterre", "away": "Congo DR",
                                    "home_score": hs, "away_score": as_, "status": status,
                                    "note": None, "league": None, "start_ts": None}]
    monkeypatch.setattr(fs, "periods", lambda mid: None)
    # AB=2 (live, 1-1 transitoire) -> abstention
    monkeypatch.setattr(fs, "_match_index", lambda sport, off: row("2", "1", "1"))
    assert fs.final_score("foot", d) is None
    # AB=1 (pas commencé) -> abstention
    monkeypatch.setattr(fs, "_match_index", lambda sport, off: row("1", None, None))
    assert fs.final_score("foot", d) is None
    # AB=3 (terminé, 2-1) -> score final rendu
    monkeypatch.setattr(fs, "_match_index", lambda sport, off: row("3", "2", "1"))
    out = fs.final_score("foot", d)
    assert out and out["home"] == 2 and out["away"] == 1


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
