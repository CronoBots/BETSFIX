"""Tests de normalisation LiveScore (pur, sans réseau)."""

from app.providers.livescore import _normalize, _parse_esd, _tokens


def test_parse_esd():
    dt = _parse_esd("20260530090000")
    assert dt.year == 2026 and dt.month == 5 and dt.day == 30 and dt.hour == 9
    assert _parse_esd(None) is None
    assert _parse_esd("bad") is None


def test_normalize_finished_match():
    ev = {
        "Eid": 1782301, "Esd": 20260529091000, "Eps": "FT", "Tr1": 0, "Tr2": 3,
        "T1": [{"Nm": "Nuno Borges", "ID": 1128}],
        "T2": [{"Nm": "Andrey Rublev", "ID": 222}],
    }
    m = _normalize("atp", ev)
    assert m.id == 1782301
    assert m.source == "livescore"
    assert m.status == "finished"
    assert m.home.name == "Nuno Borges"
    assert m.winner == "away"           # Tr2 (3) > Tr1 (0)
    assert m.away_score.sets_won == 3
    assert m.start_time.hour == 9


def test_normalize_notstarted():
    ev = {"Eid": 1, "Esd": 20260530090000, "Eps": "NS", "Tr1": None, "Tr2": None,
          "T1": [{"Nm": "A B"}], "T2": [{"Nm": "C D"}]}
    m = _normalize("wta", ev)
    assert m.status == "notstarted"
    assert m.winner is None


def test_tokens_matching():
    assert _tokens("Carlos Alcaraz") & _tokens("C. Alcaraz")  # nom de famille commun
    assert not (_tokens("Carlos Alcaraz") & _tokens("Novak Djokovic"))
