"""LiveScore `_find_event` : quand DEUX fixtures de noms proches (équipe 1re vs réserve « II ») tombent
le même jour à des heures différentes, on doit garder celle dont le COUP D'ENVOI est le plus proche de
l'heure cible — pas la première trouvée. Bug 2026-07-27 : Portland Timbers–Real Salt Lake 2-1 @02:30
(équipes premières) pris pour Portland Timbers II–Real Monarchs SLC 1-0 @20:00 (réserves) -> provisoire
« Plus de 2.5 buts » faussement réglé GAGNÉ alors que le vrai match finissait 1-0 (perdu)."""
from app import livescore

_TWO = [
    {"id": "1", "home": "Portland Timbers", "away": "Real Salt Lake", "status": "FT",
     "home_score": "2", "away_score": "1", "start": "2026-07-26T02:30:00+00:00"},
    {"id": "2", "home": "Portland Timbers II", "away": "Real Monarchs SLC", "status": "FT",
     "home_score": "1", "away_score": "0", "start": "2026-07-26T20:00:00+00:00"},
]


def test_find_event_prend_le_coup_denvoi_le_plus_proche(monkeypatch):
    monkeypatch.setattr(livescore, "_index",
                        lambda ls, ymd: _TWO if ymd == "20260726" else [])
    ev = livescore._find_event("Portland Timbers II", "Real Monarchs SLC",
                               "2026-07-26T20:00:00Z", "soccer")
    assert ev is not None and ev["id"] == "2"          # le match @20:00, pas celui @02:30
    assert (ev["home_score"], ev["away_score"]) == ("1", "0")


def test_find_event_prend_lautre_si_cest_lui_la_cible(monkeypatch):
    """Symétrie : viser 02:30 doit renvoyer le match des équipes premières (2-1)."""
    monkeypatch.setattr(livescore, "_index",
                        lambda ls, ymd: _TWO if ymd == "20260726" else [])
    ev = livescore._find_event("Portland Timbers", "Real Salt Lake",
                               "2026-07-26T02:30:00Z", "soccer")
    assert ev is not None and ev["id"] == "1"


def test_find_event_rejette_si_trop_loin(monkeypatch):
    """Seul le match @02:30 existe, mais on vise 20:00 (>6 h d'écart) -> aucun match (pas de faux règlement)."""
    monkeypatch.setattr(livescore, "_index",
                        lambda ls, ymd: [_TWO[0]] if ymd == "20260726" else [])
    ev = livescore._find_event("Portland Timbers II", "Real Monarchs SLC",
                               "2026-07-26T20:00:00Z", "soccer")
    assert ev is None
