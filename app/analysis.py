"""Modèle d'aide à la décision de pari (pré-match tennis).

Approche **transparente et calibrée sur données réelles** :
1. Deux facteurs de force à quasi-égalité : le **classement** calibré par régression
   logistique sur ~1150 matchs RG (cf. tools/backtest.py, log-loss ≈ 0.64) et l'**Elo
   par surface** (force réelle pondérée par la qualité des adversaires, note terre
   distincte ; cf. tools/build_elo.py). Le back-test (tools/backtest_model.py) montre
   que le classement prédit un peu mieux — d'où des poids rééquilibrés (cf. WEIGHTS).
2. Des facteurs **forme vs attente** (sur-/sous-performance par rapport au rang de
   l'adversaire, pondérée par récence + spécifique terre), **surface**
   (service/retour) et **head-to-head** qui ajustent la base.
3. La probabilité du modèle est ensuite **ancrée au marché** (cotes Unibet) :
   le marché est un estimateur sharp, on ne s'en écarte que modérément. C'est ce
   qui évite les fausses "values" sur les gros outsiders.
4. La *value* (edge) et la mise (Kelly fractionné, plafonné) en découlent.

⚠️ Modèle d'aide à la décision, sans garantie de gain. Fonctions pures (sans
réseau) pour être testables.
"""

from __future__ import annotations

import math

from app.elo import prob_from_elo
from app.models import (
    AnalysisFactor,
    Match,
    MatchAnalysis,
    PlayerStatistics,
    UnibetOdds,
    ValueBet,
)

# Coefficients calibrés par régression logistique sur les matchs RG historiques
# (tools/backtest.py). P(home) = sigmoid(b0 + b1 * (ln(rank_away) - ln(rank_home))).
RANK_B0 = 0.0507
RANK_B1 = 0.3668

# Poids des facteurs (renormalisés sur les facteurs présents).
# Rééquilibrage **mesuré par le back-test** (tools/backtest_model.py, ~17k matchs
# walk-forward) : le classement seul prédit MIEUX que l'Elo seul (log-loss 0.644 vs
# 0.657), et le mélange optimal Elo/classement est ~30/70 — l'inverse de l'ancien
# 0.45/0.20. On ne perche pas sur l'optimum (vallée plate, risque de sur-ajustement) :
# on met les deux à quasi-égalité (Elo 0.30 / classement 0.35), ce qui retire la
# domination injustifiée de l'Elo tout en gardant sa valeur sur les cas que le rang
# rate (spécialistes de surface, retours de blessure).
WEIGHTS = {"elo": 0.30, "classement": 0.35, "forme": 0.20, "surface": 0.10,
           "head_to_head": 0.05}

# Ancrage au marché : confiance accordée au modèle vs aux cotes du bookmaker.
# fair = MODEL_TRUST * modèle + (1 - MODEL_TRUST) * marché. Le marché étant sharp,
# on lui laisse le dessus -> on ne signale une value que sur un vrai désaccord.
MODEL_TRUST = 0.35

VALUE_THRESHOLD = 0.03      # edge minimal (3%) APRÈS ancrage pour signaler une value
MIN_IMPLIED = 0.07         # on ignore les outsiders extrêmes (< 7% implicite)
# Garde-fou : si le modèle s'écarte énormément du marché (> 15 pts bruts), c'est
# presque sûrement que le modèle ignore une info (forme/blessure/spécialiste de
# surface...). On ne crie PAS à la value dans ce cas — on le signale comme désaccord.
MAX_DISAGREEMENT = 0.15
KELLY_FRACTION = 0.25      # quart de Kelly (prudent)
MAX_STAKE_PCT = 5.0        # plafond de mise conseillée (% bankroll)
FORM_DECAY = 0.92          # pondération de récence (match i pèse 0.92^i)

# Recalibration anti-surconfiance. Un facteur SHRINK rapproche la proba de 0.5
# (<1 = on tempère ; 1.0 = aucune correction). **Mesuré par le back-test**
# (tools/backtest_model.py, ~17k matchs walk-forward) : le shrink qui minimise le
# log-loss vaut **1.00** — le mélange n'est PAS surconfiant globalement (sur les
# favoris il est même plutôt sous-confiant). On laisse donc la correction à 1.0 et on
# garde le mécanisme : si un futur back-test détecte de la surconfiance, baisser ici.
CALIB_SHRINK = 1.00


def recalibrate(p: float, shrink: float = CALIB_SHRINK) -> float:
    """Rapproche une proba de 0.5 pour corriger la surconfiance (shrink ∈ ]0,1])."""
    return 0.5 + (p - 0.5) * shrink


def sigmoid(z: float) -> float:
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1 / (1 + math.exp(-z))


def prob_from_rankings(rank_home: int | None, rank_away: int | None) -> float | None:
    """Probabilité de victoire de 'home' selon le classement (modèle calibré)."""
    if not rank_home or not rank_away or rank_home < 1 or rank_away < 1:
        return None
    x = math.log(rank_away) - math.log(rank_home)
    return sigmoid(RANK_B0 + RANK_B1 * x)


def _normalize_pair(home: float | None, away: float | None) -> tuple[float, float] | None:
    """Transforme deux scores positifs en probabilités sommant à 1."""
    if home is None or away is None:
        return None
    total = home + away
    if total <= 0:
        return 0.5, 0.5
    return home / total, away / total


def _player_side(m: Match, player_id: int) -> str | None:
    if player_id == m.home.id:
        return "home"
    if player_id == m.away.id:
        return "away"
    return None


def weighted_form(matches: list[Match], player_id: int | None, last: int = 25,
                  clay_only: bool = False) -> tuple[float, float]:
    """Forme pondérée par récence : (victoires_pondérées, total_pondéré).

    Les matchs récents pèsent plus (FORM_DECAY^i). `clay_only` ne compte que la
    terre battue. Retourne des floats (lissables ensuite).
    """
    if player_id is None:
        return 0.0, 0.0
    wsum = wwin = 0.0
    i = 0
    for m in matches:
        if m.status != "finished" or m.winner not in ("home", "away"):
            continue
        if clay_only and "clay" not in (m.ground_type or "").lower():
            continue
        side = _player_side(m, player_id)
        if side is None:
            continue
        w = FORM_DECAY ** i
        wsum += w
        if m.winner == side:
            wwin += w
        i += 1
        if i >= last:
            break
    return wwin, wsum


def form_rating(matches: list[Match], player_id: int | None, last: int = 25,
                clay_only: bool = False) -> tuple[float, float, int]:
    """Forme **vs attente** : a-t-on fait mieux que ce que le rang prévoyait ?

    Pour chaque match : résultat (1/0) − proba attendue contre cet adversaire selon
    son classement. Battre un top-10 compte donc bien plus que battre un n°200 ; une
    défaite contre plus fort ne pénalise presque pas. Pondéré par récence (FORM_DECAY).
    Retourne (résidu_moyen ∈ ~[-0.5, 0.5], poids_total, n_matchs).
    """
    if player_id is None:
        return 0.0, 0.0, 0
    wsum = rsum = 0.0
    i = 0
    for m in matches:
        if m.status != "finished" or m.winner not in ("home", "away"):
            continue
        if clay_only and "clay" not in (m.ground_type or "").lower():
            continue
        side = _player_side(m, player_id)
        if side is None:
            continue
        my_rank = m.home.ranking if side == "home" else m.away.ranking
        opp_rank = m.away.ranking if side == "home" else m.home.ranking
        expected = prob_from_rankings(my_rank, opp_rank)
        if expected is None:  # adversaire/rang inconnu -> attente neutre
            expected = 0.5
        won = 1.0 if m.winner == side else 0.0
        w = FORM_DECAY ** i
        wsum += w
        rsum += w * (won - expected)
        i += 1
        if i >= last:
            break
    if wsum <= 0:
        return 0.0, 0.0, 0
    return rsum / wsum, wsum, i


def _form_pair(home_matches, away_matches, match: Match) -> tuple[tuple[float, float] | None, str]:
    """Probabilités de forme (home, away) basées sur la performance vs attente,
    en privilégiant la terre battue."""
    # Terre battue d'abord si assez de données des deux côtés
    rh, _, nh = form_rating(home_matches, match.home.id, clay_only=True)
    ra, _, na = form_rating(away_matches, match.away.id, clay_only=True)
    label = "terre battue"
    if nh < 3 or na < 3:  # pas assez de clay -> toutes surfaces
        rh, _, nh = form_rating(home_matches, match.home.id)
        ra, _, na = form_rating(away_matches, match.away.id)
        label = "toutes surfaces"
    if nh <= 0 or na <= 0:
        return None, label
    # Chaque résidu ajuste la base 0.5 (sur-/sous-performance), puis on normalise.
    fh = min(max(0.5 + rh, 0.02), 0.98)
    fa = min(max(0.5 + ra, 0.02), 0.98)
    return _normalize_pair(fh, fa), f"forme vs attente ({label})"


def _surface_strength(stats: PlayerStatistics | None) -> float | None:
    """Score de domination sur la surface (service + conversion de breaks)."""
    if stats is None:
        return None
    serve = stats.first_serve_points_won_percentage
    ret = stats.break_points_saved_converted_percentage
    parts = [v for v in (serve, ret) if v is not None]
    if not parts:
        return None
    return sum(parts) / len(parts) / 100


def remove_vig(odds_home: float | None, odds_away: float | None) -> tuple[float, float] | None:
    """Probabilités implicites débarrassées de la marge du bookmaker."""
    if not odds_home or not odds_away:
        return None
    ih, ia = 1 / odds_home, 1 / odds_away
    total = ih + ia
    return ih / total, ia / total


def kelly_fraction(prob: float | None, odds: float | None) -> float:
    """Fraction de bankroll optimale (Kelly). 0 si pas de value."""
    if not prob or not odds or odds <= 1:
        return 0.0
    b = odds - 1
    f = (b * prob - (1 - prob)) / b
    return max(0.0, f)


def _confidence(factors: list[AnalysisFactor], n_form: float, n_h2h: int) -> str:
    score = len(factors)
    if score >= 4 and n_form >= 8 and n_h2h >= 2:
        return "élevée"
    if score >= 3 and n_form >= 4:
        return "moyenne"
    return "faible"


def build_analysis(
    match: Match,
    home_matches: list[Match],
    away_matches: list[Match],
    home_stats: PlayerStatistics | None,
    away_stats: PlayerStatistics | None,
    home_wins_h2h: int | None,
    away_wins_h2h: int | None,
    unibet: UnibetOdds | None,
    elo_home: float | None = None,
    elo_away: float | None = None,
) -> MatchAnalysis:
    factors: list[AnalysisFactor] = []

    # 0) Elo par surface (force réelle pondérée par les adversaires)
    pe = prob_from_elo(elo_home, elo_away)
    if pe is not None:
        surf = "terre" if "clay" in (match.ground_type or "").lower() else "global"
        factors.append(AnalysisFactor(
            name="elo", home=round(pe, 4), away=round(1 - pe, 4),
            weight=WEIGHTS["elo"],
            detail=f"Elo {surf} {round(elo_home)} vs {round(elo_away)}",
        ))

    # 1) Classement (calibré) — repli et complément de l'Elo
    p = prob_from_rankings(match.home.ranking, match.away.ranking)
    if p is not None:
        factors.append(AnalysisFactor(
            name="classement", home=round(p, 4), away=round(1 - p, 4),
            weight=WEIGHTS["classement"],
            detail=f"rangs {match.home.ranking} vs {match.away.ranking} (modèle calibré)",
        ))

    # 2) Forme récente (récence + terre battue)
    form_pair, form_label = _form_pair(home_matches, away_matches, match)
    _, n_form = weighted_form(home_matches, match.home.id)
    if form_pair:
        factors.append(AnalysisFactor(
            name="forme", home=round(form_pair[0], 4), away=round(form_pair[1], 4),
            weight=WEIGHTS["forme"], detail=form_label,
        ))

    # 3) Surface (stats service/retour)
    pair = _normalize_pair(_surface_strength(home_stats), _surface_strength(away_stats))
    if pair:
        factors.append(AnalysisFactor(
            name="surface", home=round(pair[0], 4), away=round(pair[1], 4),
            weight=WEIGHTS["surface"], detail="service + conversion de breaks sur la surface",
        ))

    # 4) Head-to-head
    n_h2h = (home_wins_h2h or 0) + (away_wins_h2h or 0)
    if home_wins_h2h is not None and away_wins_h2h is not None and n_h2h > 0:
        hh = (home_wins_h2h + 1) / (n_h2h + 2)
        factors.append(AnalysisFactor(
            name="head_to_head", home=round(hh, 4), away=round(1 - hh, 4),
            weight=WEIGHTS["head_to_head"], detail=f"{home_wins_h2h}-{away_wins_h2h}",
        ))

    # Mélange pondéré (renormalisé sur les facteurs présents), puis recalibration
    # anti-surconfiance (on tempère vers 0.5). La proba retenue ci-dessous sert à la
    # fois à l'affichage, au suivi et au calcul de value : on veut une proba honnête.
    p_home = p_away = None
    total_w = sum(f.weight for f in factors)
    if total_w > 0:
        p_home = recalibrate(sum(f.weight * f.home for f in factors) / total_w)
        p_away = 1 - p_home

    analysis = MatchAnalysis(
        match_id=match.id,
        home=match.home,
        away=match.away,
        status=match.status,
        ground_type=match.ground_type,
        model_home_probability=round(p_home, 4) if p_home is not None else None,
        model_away_probability=round(p_away, 4) if p_away is not None else None,
        confidence=_confidence(factors, n_form, n_h2h),
        factors=factors,
        unibet_matched=bool(unibet and unibet.matched),
    )

    # Value betting vs cotes Unibet, AVEC ancrage au marché
    if p_home is not None and unibet and unibet.matched:
        odds_home, odds_away = _match_winner_odds(unibet, match)
        implied = remove_vig(odds_home, odds_away)
        if implied:
            for side, player, model_p, odds, imp in (
                ("home", match.home.name, p_home, odds_home, implied[0]),
                ("away", match.away.name, p_away, odds_away, implied[1]),
            ):
                # Ancrage : on tire le modèle vers le marché (sharp).
                fair = MODEL_TRUST * model_p + (1 - MODEL_TRUST) * imp
                edge = fair - imp
                raw_gap = model_p - imp  # désaccord brut modèle vs marché
                f = kelly_fraction(fair, odds)
                stake = min(f * KELLY_FRACTION * 100, MAX_STAKE_PCT)
                is_value = (
                    edge >= VALUE_THRESHOLD
                    and imp >= MIN_IMPLIED            # pas d'outsider extrême
                    and raw_gap <= MAX_DISAGREEMENT   # pas de désaccord majeur (modèle aveugle)
                    and f > 0
                    and analysis.confidence != "faible"
                )
                analysis.value_bets.append(ValueBet(
                    side=side, player=player, odds=odds,
                    model_probability=round(model_p, 4),
                    implied_probability=round(imp, 4),
                    fair_probability=round(fair, 4),
                    edge=round(edge, 4),
                    kelly_fraction=round(f, 4),
                    recommended_stake_pct=round(stake, 2),
                    is_value=is_value,
                ))

    analysis.recommendation = _recommendation(analysis)
    return analysis


def _match_winner_odds(unibet: UnibetOdds, match: Match) -> tuple[float | None, float | None]:
    """Extrait (cote_home, cote_away) du marché vainqueur de match chez Unibet."""
    from app.providers.unibet import _norm_name

    home_tokens = _norm_name(match.home.name)
    for mk in unibet.markets:
        if (mk.type or "").lower() != "match":
            continue
        if len(mk.outcomes) != 2:
            continue
        o1, o2 = mk.outcomes
        if _norm_name(o1.label) & home_tokens:
            return o1.odds, o2.odds
        if _norm_name(o2.label) & home_tokens:
            return o2.odds, o1.odds
        return o1.odds, o2.odds
    return None, None


def _recommendation(a: MatchAnalysis) -> str:
    """Résumé neutre (aide à la décision, pas un conseil de pari)."""
    if not a.factors:
        return "Données insuffisantes pour une analyse fiable."
    fav = a.home.name if (a.model_home_probability or 0) >= 0.5 else a.away.name
    favp = max(a.model_home_probability or 0, a.model_away_probability or 0)
    head = f"Lecture du modèle (confiance {a.confidence}) : favori {fav} à {favp:.0%}."
    if not a.unibet_matched:
        return head + " Cotes Unibet indisponibles (match non à l'affiche)."
    # Écart au marché, présenté comme une INFO (le marché reste la référence sharp).
    diffs = [v for v in a.value_bets if (v.edge or 0) >= 0.06]
    if diffs:
        d = max(diffs, key=lambda v: v.edge or 0)
        return (head + f" Le modèle est plus optimiste qu'Unibet sur {d.player} "
                f"(à recouper — désaccord ≠ pari gagnant).")
    return head + " Cotes Unibet globalement conformes au modèle."
