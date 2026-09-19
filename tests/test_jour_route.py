"""Non-régression du fragment calendrier `/jour` (onglet Pronos).

Régression réelle (2026-09-19) : la refonte Signaux du 18/09 a retiré `web._signaux_day_matches`, mais le
routeur l'appelait encore pour le jour COURANT et pour chaque jour PASSÉ -> `AttributeError` -> **500 sur
chaque jour de l'historique** (« Internal Server Error » en remontant le calendrier). Aucun test ne frappait
la route -> passé inaperçu. On vérifie ici que `/jour` répond 200 pour aujourd'hui ET pour un jour passé.
"""

from fastapi.testclient import TestClient

from app.main import app
from app import web

client = TestClient(app)


def test_jour_today_ok():
    today = web._sport_today().isoformat()
    r = client.get("/jour", params={"date": today})
    assert r.status_code == 200, r.text[:300]


def test_jour_past_days_ok():
    # Plusieurs jours passés : la branche `_day_view` + zone Signaux réglés doit rester en 200.
    for date in ("2026-09-18", "2026-09-17", "2026-09-16", "2026-09-12"):
        r = client.get("/jour", params={"date": date})
        assert r.status_code == 200, f"{date} -> {r.status_code} : {r.text[:200]}"


def test_signaux_day_zone_is_str():
    # Le helper de remplacement renvoie TOUJOURS une chaîne (jamais une exception / une liste).
    z = web._signaux_day_zone("foot", "2026-09-18")
    assert isinstance(z, str)
