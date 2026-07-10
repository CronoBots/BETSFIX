"""Cohérence du bloc « Paris provisoires » (onglet Stats) : le COMPTEUR (n/réglés/en attente) et la LISTE
affichée doivent TOUJOURS concorder — bug vécu 2026-07-10 (compteur « 7 » vs liste de 11) causé par deux
`_load()` séparés tombant de part et d'autre d'une écriture. Le fix = un snapshot unique partagé."""

from app import provisional as P


def _track(monkeypatch, d):
    monkeypatch.setattr(P, "_load", lambda: d)


def test_stats_et_entries_coherents_sur_meme_snapshot(monkeypatch):
    d = {
        "1": {"sport": "basket", "home": "A", "away": "B", "start": "2026-07-10T20:00:00Z",
              "name": "A - B", "sel": "A", "cote": 1.5, "code": "WIN HOME", "result": None},
        "2": {"sport": "tennis", "home": "C", "away": "D", "start": "2026-07-09T12:00:00Z",
              "name": "C - D", "sel": "C", "cote": 1.8, "code": "WIN HOME", "result": "won"},
        "3": {"sport": "foot", "home": "E", "away": "F", "start": "2026-07-09T18:00:00Z",
              "name": "E - F", "sel": "F", "cote": 2.0, "code": "WIN AWAY", "result": "lost"},
    }
    _track(monkeypatch, d)
    snap = P.load()
    s, e = P.stats(snap), P.entries(snap)
    assert s["n"] == len(e) == 3
    assert s["settled"] == sum(1 for x in e if x["result"] in ("won", "lost", "push")) == 2
    assert s["pending"] == sum(1 for x in e if x["result"] is None) == 1


def test_avg_cote_jamais_sous_1(monkeypatch):
    # une « cote moyenne » < 1 est impossible (bug d'affichage vu : 0.95) -> garde-fou
    d = {str(i): {"sport": "basket", "home": "A", "away": "B", "start": "2026-07-09T20:00:00Z",
                  "name": "A - B", "sel": "A", "cote": 1.2 + i * 0.1, "code": "WIN HOME",
                  "result": "won" if i % 2 else "lost"} for i in range(4)}
    _track(monkeypatch, d)
    s = P.stats()
    assert s["avg_cote"] is None or s["avg_cote"] >= 1.0


def test_snapshot_partage_isole_des_ecritures(monkeypatch):
    # deux appels sur le MÊME snapshot ne peuvent pas diverger même si _load() change après coup
    d1 = {"1": {"sport": "foot", "home": "A", "away": "B", "start": "2026-07-09T18:00:00Z",
                "name": "A - B", "sel": "A", "cote": 1.5, "code": "WIN HOME", "result": None}}
    _track(monkeypatch, d1)
    snap = P.load()
    # le fichier « grossit » (scan) APRÈS la prise du snapshot
    _track(monkeypatch, {**d1, "2": {"sport": "foot", "home": "C", "away": "D",
                                     "start": "2026-07-09T20:00:00Z", "name": "C - D", "sel": "C",
                                     "cote": 1.7, "code": "WIN HOME", "result": None}})
    # compteur et liste dérivés du snapshot pris AVANT -> restent cohérents (1 == 1)
    assert P.stats(snap)["n"] == len(P.entries(snap)) == 1
