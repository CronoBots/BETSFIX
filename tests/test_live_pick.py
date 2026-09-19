"""Track FANTÔME du pari LIVE (app.live_pick, EXPÉRIMENTAL, jamais publié). Tests PURS (aucun réseau) :
pricing/classification depuis le catalogue live, DC par NOM, règlement au score final (résultat/DC/totaux/
BTTS), throttle du log, isolation du store, et agrégation summary(). Voir docstring du module + CLAUDE.md."""

import json

import pytest

from app import analyses, live_pick as lp


@pytest.fixture(autouse=True)
def _clean_live_caches():
    """HERMÉTICITÉ (« Tests PURS ») : vide les caches live PARTAGÉS en mémoire avant chaque test. Sans ça, un
    test antérieur qui exerce le pipeline live (ex. un TestClient sur /directs dans un AUTRE fichier) laisse des
    entrées qui rendent `observe_match`/le modèle non déterministes en suite complète (bug d'ordre 2026-09-19).
    Les tests qui ont besoin d'une entrée la posent EUX-MÊMES dans leur corps (après cette fixture)."""
    for _c in (lp._PREMATCH_CACHE, lp._STATS_RATE_CACHE, lp._FIXID_CACHE, lp._AF_STATS_CACHE, lp._G90_CACHE,
               lp._SUMMARY_CACHE, lp._CURRENT_ALL_CACHE, lp._SETTLED_CACHE, lp._PM_TIER_CACHE):
        _c.clear()
    yield


def _cat():
    return [
        {"id": 1, "text": "Double chance - Lyon ou match nul", "odds": 1.35},
        {"id": 2, "text": "Résultat du match - Lyon", "odds": 1.80},
        {"id": 3, "text": "Moins de 3.5 buts", "odds": 1.30},
        {"id": 4, "text": "Les deux équipes marquent - Oui", "odds": 1.95},
        {"id": 5, "text": "Plus de 9.5 corners", "odds": 1.90},        # banni (corners)
        {"id": 6, "text": "Nombre de cartons - Plus de 4.5", "odds": 2.0},  # banni (cartons)
    ]


def test_price_catalog_bans_and_classifies():
    rows = lp.price_catalog(_cat(), "Lyon", "Rennes", 1, 0, 60)
    fams = {r["family"] for r in rows}
    assert "Corners" not in fams and "Cartons" not in fams          # marchés bannis exclus
    assert not any("corner" in r["sel"].lower() or "carton" in r["sel"].lower() for r in rows)
    assert {"Double chance", "Vainqueur", "Total Under", "Les 2 marquent"} <= fams
    # tout snapshot porte proba (0-1), cote (>1) et un EV cohérent
    for r in rows:
        assert 0.0 <= r["prob"] <= 1.0 and r["odds"] > 1
        assert abs(r["ev"] - (r["prob"] * r["odds"] - 1.0)) < 1e-9


def test_bans_player_props_and_scoreline_handicaps():
    """RÉGRESSION (données réelles 2026-09-13) : les props joueur (« <nom> - Marque au moins 3 buts ») et les
    handicaps de scoreline (« 3-Way Handicap (2-0) ») étaient mal-parsés en Total/Handicap de buts avec des
    EV absurdes (+6500 %). Ils DOIVENT être rejetés ; BTTS (« marquent ») NE doit PAS l'être."""
    cat = [
        {"id": 1, "text": "Pascal Gross - Marque au moins 3 buts Oui", "odds": 111.0},
        {"id": 2, "text": "3-Way Handicap (2-0) Brighton", "odds": 19.0},
        {"id": 3, "text": "Nombre total de buts en 1ère mi-temps Moins de 1.5", "odds": 1.5},
        {"id": 4, "text": "Les deux équipes marquent - Oui", "odds": 1.95},   # BTTS : à GARDER
    ]
    rows = lp.price_catalog(cat, "Coventry City", "Brighton", 0, 2, 69)
    sels = " | ".join(r["sel"].lower() for r in rows)
    assert "marque au moins" not in sels and "3-way" not in sels and "mi-temps" not in sels
    assert any(r["family"] == "Les 2 marquent" for r in rows)   # BTTS survit


def test_double_chance_par_nom_1x_12_x2():
    """Le catalogue n'écrit pas le jeton « 1X » -> résolution par NOM (équipe + « nul »)."""
    assert lp._dc_pair("Double chance - Lyon ou match nul", "Lyon", "Rennes") == "1X"
    assert lp._dc_pair("Double chance - Rennes ou match nul", "Lyon", "Rennes") == "X2"
    assert lp._dc_pair("Double chance - Lyon ou Rennes", "Lyon", "Rennes") == "12"
    assert lp._dc_pair("Résultat du match - Lyon", "Lyon", "Rennes") is None


@pytest.mark.parametrize("sel,fam,wside,fh,fa,expected", [
    ("Résultat du match - Lyon", "Vainqueur", "home", 2, 1, "won"),
    ("Résultat du match - Lyon", "Vainqueur", "home", 0, 1, "lost"),
    ("Double chance - Lyon ou match nul", "Double chance", "1X", 1, 1, "won"),
    ("Double chance - Lyon ou match nul", "Double chance", "1X", 0, 2, "lost"),
    ("Double chance - Rennes ou match nul", "Double chance", "X2", 0, 2, "won"),
    ("Les deux équipes marquent - Oui", "Les 2 marquent", None, 2, 1, "won"),
    ("Les deux équipes marquent - Oui", "Les 2 marquent", None, 2, 0, "lost"),
    ("Les deux équipes marquent - Non", "Les 2 marquent", None, 2, 0, "won"),
])
def test_settle_snap_result_dc_btts(sel, fam, wside, fh, fa, expected):
    snap = {"family": fam, "wside": wside, "info": {}, "sel": sel}
    assert lp._settle_snap(snap, fh, fa) == expected


def test_settle_snap_totaux_buts():
    """Total Under 3.5 réglé sur le score final via _eval_leg."""
    rows = lp.price_catalog([{"id": 1, "text": "Moins de 3.5 buts", "odds": 1.30}],
                            "Lyon", "Rennes", 1, 0, 60)
    assert rows and rows[0]["family"] == "Total Under"
    snap = {k: rows[0][k] for k in ("family", "wside", "info", "sel")}
    assert lp._settle_snap(snap, 2, 1) == "won"     # 3 buts < 3.5
    assert lp._settle_snap(snap, 2, 2) == "lost"    # 4 buts > 3.5


def test_stats_injection_raises_rate_on_shot_pressure(monkeypatch):
    """Optim 2 : la PRESSION DE TIRS live (xG-proxy) doit RELEVER le taux de buts attendu d'un match 0-0 très
    ouvert -> Unders moins probables. Sans stats -> repli tempo-buts seul. L'affichage ne fetch jamais (cache)."""
    monkeypatch.setattr(lp, "TEMPO_BLEND_ON", True)
    monkeypatch.setattr(lp, "STATS_INJECT_ON", True)
    # AFFICHAGE (allow_fetch=False) + cache vide -> _stats_rate90 RÉEL renvoie None (aucun appel réseau) — d'abord,
    # AVANT de monkeypatcher la fonction.
    lp._STATS_RATE_CACHE.clear()
    assert lp._stats_rate90("Y", "A", "B", "2026-09-13T18:00:00Z", 60, allow_fetch=False) is None
    # blend : sans stats (None) vs grosse pression de tirs
    monkeypatch.setattr(lp, "_stats_rate90", lambda *a, **k: None)
    base = lp._match_goals90(0, 0, 60, mid="X", allow_fetch=True)
    monkeypatch.setattr(lp, "_stats_rate90", lambda *a, **k: 4.5)           # grosse pression de tirs
    hot = lp._match_goals90(0, 0, 60, mid="X", allow_fetch=True)
    assert hot > base, "la pression de tirs doit relever le taux de buts attendu"


def test_all_markets_counted_events(monkeypatch):
    """« Tous les marchés » (user) : corners/cartons/tirs pricés via les compteurs live API-Football ; les props
    JOUEUR restent bannis (pas de +6500 % garbage)."""
    monkeypatch.setattr(lp, "ALL_MARKETS_ON", True)
    monkeypatch.setitem(lp._AF_STATS_CACHE, "X",
                        (9e18, {"home": {"corners": 4, "shots_total": 8, "shots_on": 3, "yellow": 1, "red": 0},
                                "away": {"corners": 3, "shots_total": 6, "shots_on": 2, "yellow": 2, "red": 0}}))
    cat = [{"id": 1, "text": "Nombre total de corners Plus de 9.5", "odds": 1.90},
           {"id": 2, "text": "Total de cartons Plus de 4.5", "odds": 2.0},
           {"id": 3, "text": "Pascal Gross - Marque au moins 2 buts", "odds": 41.0}]   # prop -> banni
    rows = lp.price_catalog(cat, "A", "B", 1, 0, 60, mid="X", ko="2026-09-13T18:00:00Z", allow_fetch=True)
    fams = {r["family"] for r in rows}
    assert "Corners" in fams and "Cartons" in fams          # événements comptés pricés
    assert not any("marque au moins" in r["sel"].lower() for r in rows)   # prop joueur banni
    # compteurs indispo (cache vide) -> corners non pricés (repli : pas de garbage)
    lp._AF_STATS_CACHE.clear()
    rows2 = lp.price_catalog(cat, "A", "B", 1, 0, 60, mid="Y", ko="2026-09-13T18:00:00Z", allow_fetch=False)
    assert not any(r["family"] in ("Corners", "Cartons") for r in rows2)


def test_late_game_uplift(monkeypatch):
    """Optim 3 : surcote de fin de match (buts plus fréquents tard) -> facteur 1.0 avant LATE_FROM, croissant
    jusqu'à ~1+LATE_UPLIFT à 90'. Corrige la sur-confiance des Unders tardifs."""
    monkeypatch.setattr(lp, "LATE_UPLIFT_ON", True)
    assert lp._late_factor(50) == 1.0                          # avant 70' -> pas de surcote
    assert lp._late_factor(70) == 1.0
    assert lp._late_factor(90) > 1.25                          # ~1.28 à 90'
    assert lp._late_factor(88) > lp._late_factor(80) > 1.0     # croissant
    monkeypatch.setattr(lp, "LATE_UPLIFT_ON", False)
    assert lp._late_factor(90) == 1.0                          # réversible


def test_observe_settle_summary_end_to_end(tmp_path, monkeypatch):
    """Chaîne complète : observe (log) -> settle (score final) -> summary, dans un store isolé temporaire."""
    monkeypatch.setattr(lp, "_STORE", str(tmp_path))
    monkeypatch.setattr(lp, "MINUTE_LOG_MIN", 10)
    monkeypatch.setattr(lp, "TEMPO_BLEND_ON", False)   # test des MÉCANIQUES (pas du tuning modèle) -> taux-ligue fixe
    from app import match_select
    monkeypatch.setattr(match_select, "live_state_for",
                        lambda sport, h, a: {"score": {"home": 1, "away": 0},
                                             "matchClock": {"minute": 60}})
    monkeypatch.setattr(analyses, "live_catalog", lambda mid: _cat())
    monkeypatch.setattr(lp, "_af_live_stats", lambda *a, **k: None)   # test PUR (aucun réseau) : pas de fetch stats live

    d = {"sport": "foot", "id": "EVT1", "home": "Lyon", "away": "Rennes",
         "comp": "Ligue 1", "start": "2026-09-13T18:00:00Z"}
    added = lp.observe_match(d)
    assert added >= 2                               # DC 1X (p≈.95) + Total Under 3.5 (p≈.94) qualifient en-bande
    rec = lp._load("foot", "EVT1")
    assert rec and rec["snaps"] and not rec["settled"]
    assert all(s["result"] is None for s in rec["snaps"])

    # throttle : même minute (< LOG_GAP_MIN) -> aucun nouveau snapshot
    assert lp.observe_match(d) == 0

    # règlement au score final 2-1 (Lyon gagne, 3 buts, BTTS oui)
    settled_sidecar = {**d, "result": {"score": "2-1"}}
    monkeypatch.setattr(analyses, "meta", lambda sport, mid: settled_sidecar)
    monkeypatch.setattr(analyses, "status_of", lambda dd: "finished")
    assert lp.settle_all() == 1
    rec = lp._load("foot", "EVT1")
    assert rec["settled"] and all(s["result"] in ("won", "lost", "push") for s in rec["snaps"])

    s = lp.summary()
    assert s["matches"] == 1 and s["snaps_pending"] == 0
    assert s["canonical"]["n"] == 1                 # 1 pick canonique/match (1er qualifiant >= 45')
    assert s["all_settled"]["n"] == len(rec["snaps"])
    assert 0.0 <= s["canonical"]["winrate"] <= 100.0


def test_settle_all_fast_from_apifootball(tmp_path, monkeypatch):
    """Règlement RAPIDE (settle_all_fast) : clôt le shadow directement depuis le score FT d'API-Football,
    SANS que le sidecar ait un `result` (le vrai pari, lui, garde le pipeline analyste 10 min)."""
    monkeypatch.setattr(lp, "_STORE", str(tmp_path))
    monkeypatch.setattr(lp, "MINUTE_LOG_MIN", 10)
    monkeypatch.setattr(lp, "TEMPO_BLEND_ON", False)
    from app import match_select, settle_analyst, apifootball
    monkeypatch.setattr(match_select, "live_state_for",
                        lambda sport, h, a: {"score": {"home": 1, "away": 0},
                                             "matchClock": {"minute": 60}})
    monkeypatch.setattr(analyses, "live_catalog", lambda mid: _cat())
    monkeypatch.setattr(lp, "_af_live_stats", lambda *a, **k: None)   # test PUR (aucun réseau) : pas de fetch stats live

    d = {"sport": "foot", "id": "EVT2", "home": "Lyon", "away": "Rennes",
         "comp": "Ligue 1", "start": "2026-09-13T18:00:00Z"}
    assert lp.observe_match(d) >= 2
    rec = lp._load("foot", "EVT2")
    assert rec and not rec["settled"] and all(s["result"] is None for s in rec["snaps"])

    # sidecar SANS result (analyste pas encore passé) mais API-Football renvoie FT 2-1 (MT 1-0).
    monkeypatch.setattr(analyses, "meta", lambda sport, mid: d)
    monkeypatch.setattr(apifootball, "configured", lambda: True)
    monkeypatch.setattr(settle_analyst, "_apifootball_score",
                        lambda dd, cache: {"reg_home": 2, "reg_away": 1, "periods": {"1": [1, 0], "2": [1, 1]},
                                           "src": "apifootball"})
    assert lp.settle_all_fast() == 1
    rec = lp._load("foot", "EVT2")
    assert rec["settled"] and rec["final"] == "2-1"
    assert all(s["result"] in ("won", "lost", "push") for s in rec["snaps"])
    assert lp.settle_all_fast() == 0                        # idempotent (déjà réglé)


def test_store_is_isolated_from_sidecars(tmp_path, monkeypatch):
    """Le store live_shadow est SÉPARÉ (data/live_shadow/) -> jamais dans le sidecar (invariant selfcheck)."""
    monkeypatch.setattr(lp, "_STORE", str(tmp_path))
    lp._save({"sport": "foot", "match_id": "Z", "home": "A", "away": "B", "snaps": [], "settled": False})
    files = list(tmp_path.glob("*.json"))
    assert files and files[0].name == "foot_Z.json"
    # le fichier ne porte aucune clé de sidecar (bets/stat_bet/shadow) -> pas de confusion possible
    rec = json.load(open(files[0], encoding="utf-8"))
    assert "bets" not in rec and "stat_bet" not in rec and "shadow" not in rec
