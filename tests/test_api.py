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
    final = next(m for m in data if m["round"] == "Finale")
    assert final["home"]["name"] == "Alcaraz C."
    assert final["winner"] == "home"
    assert final["home_score"]["sets_won"] == 3
    assert final["home_score"]["sets"] == [6, 2, 5, 6, 6]
    assert final["status"] == "finished"


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
def test_get_single_match(client):
    respx.mock.get(f"{BASE}/event/11958222").mock(
        return_value=httpx.Response(200, json=fixtures.EVENT_DETAIL)
    )
    resp = client.get("/matches/11958222?tour=atp")
    assert resp.status_code == 200
    assert resp.json()["id"] == 11958222


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
