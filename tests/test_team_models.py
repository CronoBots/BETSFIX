"""Tests des modèles d'équipe foot/basket (maths pures + matching cotes), sans réseau."""

from datetime import datetime, timedelta, timezone

from app import basket, foot
from app.textutil import name_tokens


# --------------------------------------------------------------- basket
def test_expected_symmetric():
    assert abs(basket.expected(1500, 1500) - 0.5) < 1e-9
    assert basket.expected(1700, 1500) > 0.5


def test_win_prob_home_advantage_and_missing():
    p = basket.win_prob(1500, 1500)
    assert p is not None and p > 0.5           # avantage terrain
    assert basket.win_prob(None, 1500) is None
    assert basket.win_prob(1500, None) is None


def test_inv_norm_and_margin():
    assert abs(basket._inv_norm(0.5)) < 1e-6
    assert basket._inv_norm(0.84) > 0 > basket._inv_norm(0.16)
    assert abs(basket.expected_margin(0.5)) < 1e-6
    assert basket.expected_margin(None) is None
    assert basket.expected_margin(0.7) > 0


def test_devig_sums_to_one():
    a, b = basket._devig(1.5, 2.5)
    assert abs(a + b - 1.0) < 1e-9
    assert basket._devig(None, 2.0) is None


def _basket_odds(home, away, day, oh=1.8, oa=2.0):
    return {"home_tokens": name_tokens(home), "away_tokens": name_tokens(away),
            "day": day, "oh": oh, "oa": oa}


def test_basket_match_odds_straight_swapped_and_guards():
    day = datetime(2026, 6, 2, tzinfo=timezone.utc).date()
    ts = datetime(2026, 6, 2, 23, 0, tzinfo=timezone.utc).timestamp()
    game = {"home": "Dallas Wings", "away": "Seattle Storm", "start": ts}
    # straight
    oh, oa = basket._match_odds(game, [_basket_odds("Dallas Wings", "Seattle Storm", day)])
    assert (oh, oa) == (1.8, 2.0)
    # inversé
    oh, oa = basket._match_odds(game, [_basket_odds("Seattle Storm", "Dallas Wings", day, 1.8, 2.0)])
    assert (oh, oa) == (2.0, 1.8)
    # mauvaise date -> pas de match
    other = datetime(2026, 6, 3, tzinfo=timezone.utc).date()
    assert basket._match_odds(game, [_basket_odds("Dallas Wings", "Seattle Storm", other)]) == (None, None)
    # autre affiche (adversaire différent) -> pas de faux positif
    assert basket._match_odds(game, [_basket_odds("Atlanta Dream", "Chicago Sky", day)]) == (None, None)


# --------------------------------------------------------------- foot
def test_foot_outcome_probs_sums_to_one_and_missing():
    p = foot.outcome_probs(1600, 1500)
    assert p is not None and abs(sum(p) - 1.0) < 1e-6
    assert p[0] > p[2]                          # le plus fort (domicile) favori
    assert foot.outcome_probs(None, 1500) is None


def test_foot_neutral_removes_home_advantage():
    # à Elo égal, terrain neutre -> P(1) ≈ P(2) ; non neutre -> P(1) > P(2)
    pn = foot.outcome_probs(1500, 1500, neutral=True)
    ph = foot.outcome_probs(1500, 1500, neutral=False)
    assert abs(pn[0] - pn[2]) < 1e-6
    assert ph[0] > ph[2]


def test_foot_devig3_and_goals():
    d = foot._devig3(2.0, 3.4, 3.6)
    assert d is not None and abs(sum(d) - 1.0) < 1e-9
    assert foot._devig3(0, 3.0, 3.0) is None
    g = foot.goals_markets(1600, 1500)
    assert 0 < g["over25"] < 1 and 0 < g["btts"] < 1


def _foot_odds(home, away, day, o1=2.0, ox=3.3, o2=3.6):
    return {"home_tokens": name_tokens(home), "away_tokens": name_tokens(away),
            "day": day, "o1": o1, "ox": ox, "o2": o2}


def test_foot_match_odds_generic_and_date_guard():
    day = datetime(2026, 6, 14, tzinfo=timezone.utc).date()
    ts = datetime(2026, 6, 14, 18, 0, tzinfo=timezone.utc).timestamp()
    game = {"home": "Manchester United", "away": "Arsenal", "start": ts}
    # vrai match
    assert foot._match_odds(game, [_foot_odds("Manchester United", "Arsenal", day)])[0] == 2.0
    # « United » seul ne doit PAS apparier Newcastle United
    assert foot._match_odds(game, [_foot_odds("Newcastle United", "Arsenal", day)]) == (None, None, None)


def test_board_from_store_basket(monkeypatch):
    from app import basket, tracking
    soon = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    fake = {
        "1": {"match_id": 1, "tour": "wnba", "home": "Dallas Wings", "away": "Seattle Storm",
              "model_home_prob": 0.66, "start_time": soon,
              "unibet_home_odds": 1.15, "unibet_away_odds": 5.6,
              "value_pick": {"side": "home", "player": "Dallas Wings", "odds": 1.15,
                             "edge": 0.04, "stake_pct": 1.0}},
        "2": {"match_id": 2, "home": "X", "away": "Y", "result": {"winner": "home"}},  # réglé -> exclu
    }
    monkeypatch.setattr(tracking, "load", lambda *a, **k: fake)
    rows = basket.board_from_store()
    assert len(rows) == 1                       # le match réglé est exclu
    assert rows[0]["home"] == "Dallas Wings"
    assert rows[0]["oh"] == 1.15
    assert rows[0]["pick"]["side"] == "home"
    assert rows[0]["model_home"] == 0.66


def test_foot_best_bet():
    """Moteur 'perle rare' foot : meilleur équilibre confiance×value, JAMAIS un pari < 1.5."""
    from app import foot
    from app.providers.unibet import UnibetMarket, UnibetOutcome
    mk = [UnibetMarket(label="Résultat du match", type="Match", outcomes=[
              UnibetOutcome(label="1", odds=1.30), UnibetOutcome(label="X", odds=5.5),
              UnibetOutcome(label="2", odds=9.0)]),
          UnibetMarket(label="Nombre total de buts", type="Plus de/Moins de", outcomes=[
              UnibetOutcome(label="Plus de", odds=1.55, line=1.5),
              UnibetOutcome(label="Moins de", odds=2.40, line=1.5)])]
    bb = foot.best_bet(1900, 1500, True, mk)
    assert bb is not None
    assert bb["odds"] >= 1.5 and bb["model_prob"] >= 0.52 and bb["edge"] >= 0.04
    assert bb["odds"] != 1.30                 # le favori sous 1.5 n'est PAS la perle
    assert foot.best_bet(1900, 1500, True, []) is None
    assert foot.best_bet(None, None, True, mk) is None


def test_foot_best_picks_conf_and_value():
    """Values ET confiances depuis le MÊME pool : confiance = proba max, value = edge max."""
    from app.providers.unibet import UnibetMarket, UnibetOutcome
    mk = [UnibetMarket(label="Temps réglementaire", type="Match", outcomes=[
              UnibetOutcome(label="1", odds=1.30), UnibetOutcome(label="X", odds=5.5),
              UnibetOutcome(label="2", odds=9.0)]),
          # marché sûr (proba haute, petit edge)
          UnibetMarket(label="Nombre total de buts", type="Plus de/Moins de", outcomes=[
              UnibetOutcome(label="Plus de", odds=1.55, line=1.5),
              UnibetOutcome(label="Moins de", odds=2.40, line=1.5)]),
          # marché plus risqué mais cote généreuse (edge plus gros)
          UnibetMarket(label="Les deux équipes marquent", type="Oui/Non", outcomes=[
              UnibetOutcome(label="Oui", odds=2.30), UnibetOutcome(label="Non", odds=1.62)])]
    p = foot.best_picks(1850, 1480, True, mk, home="Espagne", away="Irak")
    assert p is not None
    # confiance = la plus probable ; value = le plus gros edge ; tirées du même pool
    assert p["confidence"]["model_prob"] >= p["value"]["model_prob"]
    assert p["value"]["edge"] >= p["confidence"]["edge"]
    assert foot.best_picks(None, None, True, [], home="A", away="B") is None


def test_foot_form_model_and_settle():
    """Modèle de buts par forme réelle + règlement automatique des perles."""
    from app import foot
    # équipe moyenne -> attaque/défense ~1.0 ; offensive -> attaque > 1
    att_moy, _ = foot._team_strength(gf=14, ga=12, n=10)
    att_off, _ = foot._team_strength(gf=25, ga=6, n=10)
    assert att_off > att_moy > 0.9
    # domicile attendu > extérieur, buts positifs
    lh, la = foot._lambdas_form(foot._team_strength(20, 8, 10),
                                foot._team_strength(12, 14, 10), neutral=False)
    assert lh > la > 0
    # règlement : totaux / BTTS / 1X2
    assert foot.settle_perle({"kind": "ou", "side": "over", "line": 2.5}, 2, 1) is True
    assert foot.settle_perle({"kind": "ou", "side": "over", "line": 2.5}, 1, 1) is False
    assert foot.settle_perle({"kind": "ou", "side": "under", "line": 2.5}, 1, 1) is True
    assert foot.settle_perle({"kind": "btts", "side": "yes"}, 1, 2) is True
    assert foot.settle_perle({"kind": "btts", "side": "no"}, 0, 3) is True
    assert foot.settle_perle({"kind": "1x2", "side": "1"}, 2, 0) is True
    assert foot.settle_perle({"kind": "1x2", "side": "2"}, 2, 0) is False
    # marchés PAR ÉQUIPE + pair/impair + score exact + nombre exact
    assert foot.settle_perle({"kind": "team_ou", "side": "over", "line": 0.5, "team": "home"}, 2, 0) is True
    assert foot.settle_perle({"kind": "team_ou", "side": "over", "line": 0.5, "team": "away"}, 2, 0) is False
    assert foot.settle_perle({"kind": "team_ou", "side": "under", "line": 1.5, "team": "away"}, 3, 1) is True
    assert foot.settle_perle({"kind": "parity", "side": "even"}, 2, 0) is True
    assert foot.settle_perle({"kind": "parity", "side": "odd"}, 2, 1) is True
    assert foot.settle_perle({"kind": "exact", "side": "2-1", "sc": [2, 1]}, 2, 1) is True
    assert foot.settle_perle({"kind": "exact", "side": "2-1", "sc": [2, 1]}, 1, 1) is False
    assert foot.settle_perle({"kind": "nbexact", "side": "3", "k": 3, "ge": True}, 2, 2) is True
    assert foot.settle_perle({"kind": "nbexact", "side": "2", "k": 2, "ge": False}, 1, 1) is True
    # handicap asiatique (lignes .5, règlement net)
    assert foot.settle_perle({"kind": "hasian", "side": "h", "team": "home", "line": -1.5}, 3, 1) is True
    assert foot.settle_perle({"kind": "hasian", "side": "h", "team": "home", "line": -1.5}, 2, 1) is False
    assert foot.settle_perle({"kind": "hasian", "side": "h", "team": "away", "line": 1.5}, 2, 1) is True
    assert foot.settle_perle(None, 1, 1) is None


def test_foot_result_market_guard():
    """Marché de RÉSULTAT : un écart modèle/marché trop grand est ignoré (pas de faux edge)."""
    from app.providers.unibet import UnibetMarket, UnibetOutcome
    # double chance « 1X » très généreuse alors que le modèle surévalue énormément le domicile :
    # l'écart dépasse MAX_DISAGREEMENT -> aucune perle (on s'aligne sur le marché efficient).
    mk = [UnibetMarket(label="Double Chance", type="", outcomes=[
              UnibetOutcome(label="1X", odds=2.5), UnibetOutcome(label="12", odds=1.2),
              UnibetOutcome(label="X2", odds=1.5)])]
    bb = foot.best_bet(2200, 1300, False, mk, lambdas=(3.0, 0.3), home="A", away="B")
    assert bb is None or bb["kind"] != "dc"


def test_foot_halftime_model_and_settle():
    """Marchés MI-TEMPS : grille découpée, HT/FT joint, règlement sur le score de la période."""
    # la grille mi-temps a moins de buts attendus que le plein-temps
    assert foot._p_over(foot._grid_half(2.0, 1.4, 0.45), 1.5) < foot._p_over(foot._grid_l(2.0, 1.4), 1.5)
    jt = foot._p_htft(foot._grid_half(2.4, 0.3, 0.45), foot._grid_half(2.4, 0.3, 0.55))
    assert abs(sum(jt.values()) - 1.0) < 1e-6          # loi de proba
    assert jt[("1", "1")] > jt[("2", "2")]             # gros favori : mène et gagne >> perd et perd
    # règlement mi-temps : sur le score de la période, pas le score final
    p_h1 = {"kind": "ou", "side": "over", "line": 0.5, "period": "h1"}
    assert foot.settle_perle(p_h1, 2, 1, h1_home=1, h1_away=0) is True    # 1 but en 1re MT
    assert foot.settle_perle(p_h1, 2, 1, h1_home=0, h1_away=0) is False   # 0 but en 1re MT
    assert foot.settle_perle(p_h1, 2, 1) is None                          # score pause inconnu
    p_h2 = {"kind": "team_ou", "side": "over", "line": 0.5, "team": "home", "period": "h2"}
    assert foot.settle_perle(p_h2, 3, 0, h1_home=1, h1_away=0) is True    # dom marque en 2e MT (3-1=2)
    # HT/FT : mène à la pause puis se fait remonter
    p_htft = {"kind": "htft", "htft": ["1", "2"]}
    assert foot.settle_perle(p_htft, 1, 2, h1_home=1, h1_away=0) is True
    assert foot.settle_perle(p_htft, 1, 2, h1_home=0, h1_away=0) is False


def test_foot_corner_card_markets():
    """Marchés CORNERS & CARTONS : modèle dédié (forme corners/cartons) + règlement sur stats réelles."""
    from app.providers.unibet import UnibetMarket, UnibetOutcome
    cfh = {"n": 30, "cf": 6.5, "ca": 3.5, "yf": 2.0, "ya": 2.0}
    cfa = {"n": 30, "cf": 3.5, "ca": 6.0, "yf": 2.0, "ya": 2.0}
    assert foot._corner_lambdas(cfh, cfa)[0] > foot._corner_lambdas(cfh, cfa)[1]   # dom + de corners
    # perle « corners par équipe » quand la cote est généreuse ET la forme connue
    cfh2 = {"n": 30, "cf": 4.5, "ca": 4.0, "yf": 2.0, "ya": 2.0}
    cfa2 = {"n": 30, "cf": 4.0, "ca": 4.5, "yf": 2.0, "ya": 2.0}
    mk = [UnibetMarket(label="Nombre total de corners par Real", type="", outcomes=[
              UnibetOutcome(label="Plus de", odds=1.80, line=3.5),
              UnibetOutcome(label="Moins de", odds=2.00, line=3.5)])]
    bb = foot.best_bet(1700, 1500, False, mk, home="Real", away="Getafe", corner_form=(cfh2, cfa2))
    assert bb is not None and bb["kind"] == "c_team"
    assert foot.best_bet(1700, 1500, False, mk, home="Real", away="Getafe") is None   # sans forme
    # règlement sur les stats réelles du match (corners 6-3, cartons 2-3)
    ms = {"corners_h": 6, "corners_a": 3, "cards_h": 2, "cards_a": 3}
    assert foot.settle_perle({"kind": "c_ou", "side": "over", "line": 9.5}, 1, 0, match_stats=ms) is False
    assert foot.settle_perle({"kind": "c_team", "side": "over", "line": 4.5, "team": "home"}, 1, 0, match_stats=ms) is True
    assert foot.settle_perle({"kind": "c_1x2", "side": "1"}, 1, 0, match_stats=ms) is True
    assert foot.settle_perle({"kind": "k_ou", "side": "over", "line": 3.5}, 1, 0, match_stats=ms) is True
    assert foot.settle_perle({"kind": "c_ou", "side": "over", "line": 9.5}, 1, 0) is None   # pas de stats


def test_foot_extreme_weak_team_no_false_value():
    """Équipe extrême très faible (Andorre) : pas de fausse value buts (garde-fou + plancher bas)."""
    from app.providers.unibet import UnibetMarket, UnibetOutcome
    sh = foot._team_strength(gf=2, ga=18, n=12)    # Andorre : ne marque quasiment jamais
    sa = foot._team_strength(gf=6, ga=20, n=12)    # Liechtenstein
    assert sh[0] < 0.50                            # attaque faible possible (plancher 0.40)
    lam = foot._lambdas_form(sh, sa, neutral=False)
    assert foot._p_btts(foot._grid_l(*lam)) < 0.35   # BTTS réaliste (et non ~0.55)
    # marché BTTS « Oui » à grosse cote (marché = 19 %) : pas de FAUSSE value haute (BTTS-Oui / Over).
    mk = [UnibetMarket(label="Les deux équipes marquent", type="Oui/Non", outcomes=[
              UnibetOutcome(label="Oui", odds=5.2), UnibetOutcome(label="Non", odds=1.15)]),
          UnibetMarket(label="Nombre total de buts", type="Plus de/Moins de", outcomes=[
              UnibetOutcome(label="Plus de", odds=2.60, line=2.5),
              UnibetOutcome(label="Moins de", odds=1.48, line=2.5)])]
    bb = foot.best_bet(None, None, False, mk, lambdas=lam, home="A", away="B")
    # un pari « Moins de » est légitime ; mais JAMAIS de BTTS-Oui / Over (la fausse value d'avant)
    if bb:
        assert bb["side"] in ("under", "no")


def test_foot_sos_adjustment():
    """Force de calendrier : marquer peu CONTRE DES GROS est crédité (att SOS > att brute)."""
    idx = foot._elo_index({"a": {"name": "Strong FC", "elo": 1850}})
    # 3 buts en 5 matchs, mais tous contre une équipe FORTE (qui concède peu)
    hard = ([{"gf": 1, "ga": 1, "opp": "Strong FC", "home": True}] * 3
            + [{"gf": 0, "ga": 2, "opp": "Strong FC", "home": False}] * 2)
    brut = foot._team_strength(gf=3, ga=7, n=5)
    sos = foot._strength_sos(hard, idx)
    assert sos is not None and sos[0] > brut[0]          # attaque créditée par la difficulté
    # adversaire inconnu -> force moyenne (pas de crash) ; liste vide -> None
    assert foot._strength_sos([{"gf": 2, "ga": 1, "opp": "Inconnu ZzZ", "home": True}], idx) is not None
    assert foot._strength_sos([], idx) is None


def test_foot_team_goals_markets():
    """Le moteur évalue les marchés PAR ÉQUIPE (totaux d'un camp, but / pas de but)."""
    from app.providers.unibet import UnibetMarket, UnibetOutcome
    grid = foot._grid_l(2.2, 0.2)        # domicile fort, extérieur muet
    assert foot._p_team_over(grid, "home", 0.5) > 0.8
    assert foot._p_team_over(grid, "away", 0.5) < 0.45
    # « Irak ne marque pas » sous-coté par le marché -> doit ressortir (côté away, under 0.5)
    mk = [UnibetMarket(label="Nombre total de buts par Irak", type="Plus de/Moins de", outcomes=[
              UnibetOutcome(label="Plus de", odds=3.5, line=0.5),
              UnibetOutcome(label="Moins de", odds=1.7, line=0.5)])]
    bb = foot.best_bet(1900, 1500, True, mk, lambdas=(2.2, 0.2), home="Espagne", away="Irak")
    assert bb is not None and bb["kind"] == "team_ou" and bb["team"] == "away"
    assert bb["side"] == "under" and "ne marque pas" in bb["selection"]


def test_model_disagrees_market_guard():
    """Garde-fou MATCH : modèle trop loin du marché 1X2 -> aucune perle (le marché a raison)."""
    # Belgique 1.33 vs Tunisie : modèle 49 % vs marché ~71 % -> divergence -> True (perle coupée)
    assert foot._model_disagrees_market(
        {"probs": (0.49, 0.25, 0.26), "o1": 1.33, "ox": 5.0, "o2": 9.5}) is True
    # Modèle ≈ marché -> False (perle autorisée)
    assert foot._model_disagrees_market(
        {"probs": (0.55, 0.25, 0.20), "o1": 1.7, "ox": 3.6, "o2": 5.0}) is False
    # Données manquantes -> ne bloque pas
    assert foot._model_disagrees_market({"probs": None, "o1": 1.5, "ox": 4.0, "o2": 6.0}) is False
