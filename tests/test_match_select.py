"""Tests de la sélection des matchs importants (profondeur de marché, hors eSports)."""

from app.match_select import rank_important


def _ev(name, group, markets, path=None):
    return {"event": {"id": name, "name": name, "homeName": name.split("-")[0],
                      "awayName": name.split("-")[-1], "group": group,
                      "nonLiveBoCount": markets, "path": [{"name": p} for p in (path or [])]}}


def test_rank_by_market_depth_excludes_esports():
    events = [
        _ev("Petit-Match", "Suède Division 2", 5),
        _ev("Belgique-Tunisie", "Amicaux Internationaux", 127),
        _ev("Bot1-Bot2", "Esports Battle (2x4min)", 0, path=["|Esports Football|"]),
        _ev("Cyber1-Cyber2", "Cyber Live Arena", 8, path=["|Esports Football|"]),
        _ev("Portugal-Chili", "Amicaux Internationaux", 549),
    ]
    top = rank_important(events, top_n=10)
    # eSports exclus (même celui avec 8 marchés)
    names = [r["name"] for r in top]
    assert "Bot1-Bot2" not in names and "Cyber1-Cyber2" not in names
    # triés par profondeur de marché décroissante
    assert names == ["Portugal-Chili", "Belgique-Tunisie", "Petit-Match"]
    assert top[0]["markets"] == 549 and top[0]["comp"] == "Amicaux Internationaux"


def test_top_n_cap():
    events = [_ev(f"M{i}-X", "Ligue", i) for i in range(20)]
    top = rank_important(events, top_n=10)
    assert len(top) == 10
    assert top[0]["markets"] == 19 and top[-1]["markets"] == 10   # les 10 plus profonds
