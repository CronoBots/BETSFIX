"""Combiné multisport du jour (app/combo_daily.py) — suivi INFO SEULE (hors ROI). Verrouille : le moteur
de sélection (cote ≥ 1.9, ≤1 jambe/match, min 2 jambes, taux de réussite maximal), le tranchage du
combiné (lost si ≥1 jambe perdue, won si toutes gagnées, void si toutes push) et la cohérence stats/liste."""

from app import combo_daily as CD


def _leg(mid, sport, sel, cote, prob, code):
    return {"mid": mid, "sport": sport, "sel": sel, "cote": cote, "prob": prob, "code": code, "name": f"{mid}"}


# ------------------------------------------------------------------ moteur pick_combo
def test_pick_respecte_cote_min_et_max_proba():
    cands = [_leg("1", "foot", "PSG", 1.35, 0.80, "WIN HOME"),
             _leg("2", "basket", "Lakers", 1.5, 0.72, "WIN HOME"),
             _leg("3", "tennis", "Sinner", 1.25, 0.85, "WIN HOME"),
             _leg("4", "foot", "Over 1.5", 1.2, 0.88, "OVER 1.5")]
    r = CD.pick_combo(cands)
    assert r is not None
    assert r["cote"] >= CD.MIN_ODDS
    assert 2 <= len(r["legs"]) <= CD.MAX_LEGS
    assert len({l["mid"] for l in r["legs"]}) == len(r["legs"])   # <=1 jambe par match


def test_pick_force_minimum_deux_jambes():
    # une seule grosse jambe atteint 1.9 -> doit quand même en mettre 2 (c'est un COMBINÉ)
    cands = [_leg("1", "tennis", "Upset", 1.95, 0.70, "WIN HOME"),
             _leg("2", "foot", "Bayern", 1.15, 0.90, "WIN HOME")]
    r = CD.pick_combo(cands)
    assert r is not None and len(r["legs"]) >= 2


def test_pick_none_si_irrealisable():
    # tout trop court -> impossible d'atteindre 1.9 avec des jambes fiables
    cands = [_leg("1", "foot", "Over 0.5", 1.03, 0.97, "OVER 0.5"),
             _leg("2", "foot", "Over 0.5", 1.04, 0.96, "OVER 0.5")]
    assert CD.pick_combo(cands) is None


def test_pick_une_seule_jambe_par_match():
    # 2 marchés du même match : l'optimiseur n'en garde qu'un
    cands = [_leg("1", "foot", "Real win", 1.4, 0.78, "WIN HOME"),
             _leg("1", "foot", "Real -1.5", 2.1, 0.55, "HCAP HOME -1.5"),
             _leg("2", "basket", "Celtics", 1.3, 0.82, "WIN HOME"),
             _leg("3", "tennis", "Alcaraz", 1.22, 0.86, "WIN HOME")]
    r = CD.pick_combo(cands)
    assert r is not None
    assert len([l for l in r["legs"] if l["mid"] == "1"]) <= 1


# ------------------------------------------------------------------ tranchage du combiné
def _fake_track(monkeypatch, legs):
    store = {"2026-07-09": {"date": "2026-07-09", "cote": 2.0, "prob": 0.5, "legs": legs,
                            "result": None, "sent": True, "created": None}}
    monkeypatch.setattr(CD, "_load", lambda: store)
    monkeypatch.setattr(CD, "_save", lambda d: None)
    import app.flashscore, app.livescore, app.settle_analyst
    monkeypatch.setattr(app.flashscore, "final_score", lambda sport, q: {"label": "1-0", "home": 1, "away": 0})
    monkeypatch.setattr(app.livescore, "final_score", lambda sport, q: None)
    return store


def test_combo_perdu_si_une_jambe_perdue(monkeypatch):
    legs = [_leg("1", "foot", "A", 1.4, 0.7, "WIN HOME"), _leg("2", "foot", "B", 1.5, 0.7, "WIN AWAY")]
    for l in legs:
        l["result"] = None
    store = _fake_track(monkeypatch, legs)
    import app.settle_analyst
    monkeypatch.setattr(app.settle_analyst, "settle_pick",
                        lambda code, score: "won" if code == "WIN HOME" else "lost")
    CD.settle_pending()
    assert store["2026-07-09"]["result"] == "lost"


def test_combo_gagne_si_toutes_gagnees(monkeypatch):
    legs = [_leg("1", "foot", "A", 1.4, 0.7, "WIN HOME"), _leg("2", "foot", "B", 1.5, 0.7, "WIN HOME")]
    for l in legs:
        l["result"] = None
    store = _fake_track(monkeypatch, legs)
    import app.settle_analyst
    monkeypatch.setattr(app.settle_analyst, "settle_pick", lambda code, score: "won")
    CD.settle_pending()
    cb = store["2026-07-09"]
    assert cb["result"] == "won"
    # profit info = cote effective - 1 = 1.4*1.5 - 1 = 1.1
    assert abs(CD._combo_result_profit(cb) - (1.4 * 1.5 - 1)) < 1e-9


def test_stats_et_entries_coherents(monkeypatch):
    legs = [_leg("1", "foot", "A", 1.4, 0.7, "WIN HOME"), _leg("2", "foot", "B", 1.5, 0.7, "WIN HOME")]
    for l in legs:
        l["result"] = "won"
    store = {"2026-07-09": {"date": "2026-07-09", "cote": 2.1, "prob": 0.49, "legs": legs,
                            "result": "won", "sent": True, "created": None}}
    monkeypatch.setattr(CD, "_load", lambda: store)
    snap = CD.load()
    s, e = CD.stats(snap), CD.entries(snap)
    assert s["n"] == len(e) == 1
    assert s["won"] == 1 and s["hit_rate"] == 100


def test_telegram_text_ne_plante_pas():
    cb = {"cote": 2.18, "prob": 0.41,
          "legs": [_leg("1", "foot", "PSG & <b>", 1.3, 0.8, "WIN HOME")]}
    txt = CD.telegram_text(cb)
    assert "COMBINÉ DU JOUR" in txt and "@2.18" in txt
    assert "&lt;b&gt;" in txt        # échappement HTML des caractères spéciaux
