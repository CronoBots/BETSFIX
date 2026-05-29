"""Tests de l'API Roland Garros avec la source SofaScore mockée (respx)."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.dependencies import get_provider
from app.main import app
from tests import fixtures

BASE = "https://api.sofascore.com/api/v1"
ATP = 2480
SEASON_ID = 57175


@pytest.fixture
def client():
    # Réinitialise le provider (et son cache) pour chaque test
    get_settings.cache_clear()
    import app.dependencies as deps

    deps._provider = None
    get_provider()  # crée un provider neuf
    with TestClient(app) as c:
        yield c
    deps._provider = None


def _mock_common(router: respx.MockRouter) -> None:
    router.get(f"{BASE}/unique-tournament/{ATP}/seasons").mock(
        return_value=httpx.Response(200, json=fixtures.SEASONS)
    )
    router.get(
        f"{BASE}/unique-tournament/{ATP}/season/{SEASON_ID}/events/last/0"
    ).mock(return_value=httpx.Response(200, json=fixtures.EVENTS_LAST))
    router.get(
        f"{BASE}/unique-tournament/{ATP}/season/{SEASON_ID}/events/next/0"
    ).mock(return_value=httpx.Response(200, json=fixtures.EVENTS_NEXT))


@respx.mock
def test_list_all_matches(client):
    _mock_common(respx.mock)
    resp = client.get("/matches?tour=atp")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    final = next(m for m in data if m["round"] == "Final")
    assert final["round_slug"] == "final"
    assert final["home"]["name"] == "Alcaraz C."
    assert final["winner"] == "home"
    assert final["home_score"]["sets_won"] == 3
    assert final["home_score"]["sets"] == [6, 2, 5, 6, 6]
    assert final["status"] == "finished"


@respx.mock
def test_filter_by_round_bilingual(client):
    _mock_common(respx.mock)
    # 'Finale' (FR) doit cibler exactement 'Final' (EN), sans attraper 'Semifinals'
    for query in ("Finale", "Final", "final"):
        resp = client.get(f"/matches?tour=atp&round={query}")
        assert resp.status_code == 200
        data = resp.json()
        assert [m["round"] for m in data] == ["Final"], query
    # 'Demi-finale' (FR) -> 'Semifinals' (EN)
    resp = client.get("/matches?tour=atp&round=Demi-finale")
    assert [m["round"] for m in resp.json()] == ["Semifinals"]


@respx.mock
def test_matches_by_round_endpoint(client):
    _mock_common(respx.mock)
    resp = client.get("/matches/round/Finale?tour=atp")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["round"] == "Final"


@respx.mock
def test_filter_by_player(client):
    _mock_common(respx.mock)
    resp = client.get("/matches?tour=atp&player=djokovic")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "Djokovic" in data[0]["away"]["name"]


@respx.mock
def test_filter_by_status(client):
    _mock_common(respx.mock)
    resp = client.get("/matches?tour=atp&status=finished")
    assert resp.status_code == 200
    assert all(m["status"] == "finished" for m in resp.json())


@respx.mock
def test_match_statistics(client):
    respx.mock.get(f"{BASE}/event/11958222/statistics").mock(
        return_value=httpx.Response(200, json=fixtures.STATISTICS)
    )
    resp = client.get("/statistics/11958222")
    assert resp.status_code == 200
    data = resp.json()
    assert data["match_id"] == 11958222
    service = data["periods"][0]["groups"][0]
    assert service["name"] == "Service"
    aces = service["items"][0]
    assert aces["name"] == "Aces"
    assert aces["home"] == "12"


@respx.mock
def test_all_statistics(client):
    _mock_common(respx.mock)
    respx.mock.get(f"{BASE}/event/11958222/statistics").mock(
        return_value=httpx.Response(200, json=fixtures.STATISTICS)
    )
    resp = client.get("/statistics?tour=atp")
    assert resp.status_code == 200
    data = resp.json()
    # Seul le match terminé (id 11958222) a des stats
    assert "11958222" in data
    assert "11958900" not in data


@respx.mock
def test_point_by_point(client):
    respx.mock.get(f"{BASE}/event/11958222/point-by-point").mock(
        return_value=httpx.Response(200, json=fixtures.POINT_BY_POINT)
    )
    resp = client.get("/matches/11958222/point-by-point")
    assert resp.status_code == 200
    data = resp.json()
    assert data["match_id"] == 11958222
    # Sets remis en ordre chronologique (1 puis 2)
    assert [s["set"] for s in data["sets"]] == [1, 2]
    set2 = data["sets"][1]
    # Jeux remis en ordre chronologique (1 puis 2)
    assert [g["game"] for g in set2["games"]] == [1, 2]
    game2 = set2["games"][1]
    assert game2["server"] == "away"
    assert game2["points"][0] == {"home": "0", "away": "15"}


@respx.mock
def test_get_single_match(client):
    respx.mock.get(f"{BASE}/event/11958222").mock(
        return_value=httpx.Response(200, json=fixtures.EVENT_DETAIL)
    )
    resp = client.get("/matches/11958222?tour=atp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 11958222
    # Champs enrichis
    assert data["court"] == "Court Philippe Chatrier"
    assert data["city"] == "Paris"
    assert data["ground_type"] == "Red clay"
    assert data["duration_seconds"] == 2588 + 3130 + 3910 + 2569 + 3367
    assert data["set_durations"] == [2588, 3130, 3910, 2569, 3367]
    assert data["first_to_serve"] == "away"
    assert data["home_seed"] == "3"


@respx.mock
def test_head_to_head(client):
    respx.mock.get(f"{BASE}/event/11958222").mock(
        return_value=httpx.Response(200, json=fixtures.EVENT_DETAIL)
    )
    respx.mock.get(f"{BASE}/event/11958222/h2h").mock(
        return_value=httpx.Response(200, json=fixtures.H2H)
    )
    resp = client.get("/matches/11958222/h2h?tour=atp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["home_wins"] == 4
    assert data["away_wins"] == 6
    assert data["home"]["name"] == "Alcaraz C."


@respx.mock
def test_votes(client):
    respx.mock.get(f"{BASE}/event/11958222/votes").mock(
        return_value=httpx.Response(200, json=fixtures.VOTES)
    )
    resp = client.get("/matches/11958222/votes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["home_votes"] == 16040
    assert data["away_percent"] == 62.6


@respx.mock
def test_streaks(client):
    respx.mock.get(f"{BASE}/event/11958222/team-streaks").mock(
        return_value=httpx.Response(200, json=fixtures.TEAM_STREAKS)
    )
    resp = client.get("/matches/11958222/streaks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["general"][0]["name"] == "Wins"
    assert data["general"][0]["side"] == "home"


@respx.mock
def test_player_profile(client):
    respx.mock.get(f"{BASE}/team/2").mock(
        return_value=httpx.Response(200, json=fixtures.PLAYER)
    )
    resp = client.get("/players/2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Alexander Zverev"
    assert data["plays"] == "right-handed"
    assert data["height_m"] == 1.98
    assert data["weight_kg"] == 90
    assert data["prize_total"] == 54458156
    assert data["birth_place"] == "Hamburg"


@respx.mock
def test_player_rankings(client):
    respx.mock.get(f"{BASE}/team/2/rankings").mock(
        return_value=httpx.Response(200, json=fixtures.PLAYER_RANKINGS)
    )
    resp = client.get("/players/2/rankings")
    assert resp.status_code == 200
    data = resp.json()
    assert {r["ranking_class"] for r in data} == {"team", "livetennis", "utr"}
    atp = next(r for r in data if r["ranking_class"] == "team")
    assert atp["ranking"] == 3
    assert atp["best_ranking"] == 2


@respx.mock
def test_player_matches(client):
    respx.mock.get(f"{BASE}/team/2/events/last/0").mock(
        return_value=httpx.Response(200, json=fixtures.PLAYER_EVENTS)
    )
    resp = client.get("/players/2/matches?pages=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["tour"] == "atp"
    assert data[0]["round"] == "Round of 32"


@respx.mock
def test_tournament_info(client):
    _mock_common(respx.mock)
    resp = client.get("/matches/tournament?tour=atp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_season"] == 2024
    assert data["current_season_id"] == SEASON_ID


@respx.mock
def test_source_unavailable_returns_502(client):
    respx.mock.get(f"{BASE}/unique-tournament/{ATP}/seasons").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    resp = client.get("/matches?tour=atp")
    assert resp.status_code == 502


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
