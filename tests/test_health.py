"""Sonde de vie de l'API.

Extrait de `tests/test_api.py` lors de sa suppression (2026-09-02) : ce fichier testait la surface API
TENNIS (`/matches?tour=atp`, `/players/<id>/rankings`, `/statistics/<id>`…), retirée avec le sport le
2026-08-13 — 22 tests échouaient donc en 404 depuis, sans qu'aucun code ne soit en cause. Seul ce
contrôle-ci gardait de la valeur : il vérifie que l'application se monte et répond.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
