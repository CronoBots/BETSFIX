"""Modèle d'aide à la décision de pari (pré-match tennis).

Approche **transparente** : on combine plusieurs facteurs (classement, forme
récente, stats sur la surface, head-to-head) en une probabilité de victoire,
puis on la compare aux cotes Unibet pour repérer la *value* (edge positif) et
proposer une mise selon le critère de Kelly fractionné.

⚠️ C'est un modèle heuristique : il aide à objectiver une décision, il ne
garantit rien. Les fonctions ici sont pures (sans réseau) pour être testables.
"""

from __future__ import annotations

import math

from app.models import (
    AnalysisFactor,
    Match,
    MatchAnalysis,
    PlayerStatistics,
    UnibetOdds,
    ValueBet,
)

# Poids par défaut des facteurs (renormalisés si certains manquent).
WEIGHTS = {"classement": 0.40, "forme": 0.25, "surface": 0.20, "head_to_head": 0.15}

VALUE_THRESHOLD = 0.02  # edge minimal (2%) pour signaler une value
KELLY_FRACTION = 0.25   # on conseille un quart de Kelly (prudent)


def _elo_from_rank(rank: int | None) -> float | None:
    """Rating Elo approximatif décroissant avec le rang (n°1 ≈ 2200)."""
    if not rank or rank < 1:
        return None
    return 2200 - 400 * math.log10(rank)


def _prob_from_ratings(home: float, away: float) -> float:
    """Probabilité de victoire de 'home' (formule logistique Elo)."""
    return 1 / (1 + 10 ** (-(home - away) / 400))


def _normalize_pair(home: float | None, away: float | None) -> tuple[float, float] | None:
    """Transforme deux scores positifs en probabilités sommant à 1."""
    if home is None or away is None:
        return None
    total = home + away
    if total <= 0:
        return 0.5, 0.5
    return home / total, away / total


def win_rate(matches: list[Match], player_id: int | None, last: int = 20) -> tuple[int, int]:
    """(victoires, total) d'un joueur sur ses `last` derniers matchs terminés."""
    if player_id is None:
        return 0, 0
    wins = played = 0
    for m in matches:
        if m.status != "finished" or m.winner not in ("home", "away"):
            continue
        if player_id == (m.home.id):
            side = "home"
        elif player_id == (m.away.id):
            side = "away"
        else:
            continue
        played += 1
        if m.winner == side:
            wins += 1
        if played >= last:
            break
    return wins, played


def _surface_strength(stats: PlayerStatistics | None) -> float | None:
    """Score de domination sur la surface à partir des stats service + retour."""
    if stats is None:
        return None
    serve = stats.first_serve_points_won_percentage
    ret = stats.break_points_saved_converted_percentage  # conversion de breaks (retour)
    parts = [v for v in (serve, ret) if v is not None]
    if not parts:
        return None
    return sum(parts) / len(parts) / 100  # ramené sur 0-1


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


def build_analysis(
    match: Match,
    home_matches: list[Match],
    away_matches: list[Match],
    home_stats: PlayerStatistics | None,
    away_stats: PlayerStatistics | None,
    home_wins_h2h: int | None,
    away_wins_h2h: int | None,
    unibet: UnibetOdds | None,
) -> MatchAnalysis:
    factors: list[AnalysisFactor] = []

    # 1) Classement
    rh, ra = _elo_from_rank(match.home.ranking), _elo_from_rank(match.away.ranking)
    if rh is not None and ra is not None:
        p = _prob_from_ratings(rh, ra)
        factors.append(AnalysisFactor(
            name="classement", home=round(p, 4), away=round(1 - p, 4),
            weight=WEIGHTS["classement"],
            detail=f"ATP {match.home.ranking} vs {match.away.ranking}",
        ))

    # 2) Forme récente
    hw, hp = win_rate(home_matches, match.home.id)
    aw, ap = win_rate(away_matches, match.away.id)
    if hp and ap:
        fh, fa = (hw + 1) / (hp + 2), (aw + 1) / (ap + 2)  # lissage de Laplace
        pair = _normalize_pair(fh, fa)
        if pair:
            factors.append(AnalysisFactor(
                name="forme", home=round(pair[0], 4), away=round(pair[1], 4),
                weight=WEIGHTS["forme"],
                detail=f"{hw}/{hp} vs {aw}/{ap} (derniers matchs)",
            ))

    # 3) Surface (stats du tournoi/surface)
    pair = _normalize_pair(_surface_strength(home_stats), _surface_strength(away_stats))
    if pair:
        factors.append(AnalysisFactor(
            name="surface", home=round(pair[0], 4), away=round(pair[1], 4),
            weight=WEIGHTS["surface"], detail="service + conversion de breaks sur la surface",
        ))

    # 4) Head-to-head
    if home_wins_h2h is not None and away_wins_h2h is not None and (home_wins_h2h + away_wins_h2h) > 0:
        hh = (home_wins_h2h + 1) / (home_wins_h2h + away_wins_h2h + 2)
        factors.append(AnalysisFactor(
            name="head_to_head", home=round(hh, 4), away=round(1 - hh, 4),
            weight=WEIGHTS["head_to_head"], detail=f"{home_wins_h2h}-{away_wins_h2h}",
        ))

    # Mélange pondéré (renormalisé sur les facteurs présents)
    p_home = p_away = None
    total_w = sum(f.weight for f in factors)
    if total_w > 0:
        p_home = sum(f.weight * f.home for f in factors) / total_w
        p_away = sum(f.weight * f.away for f in factors) / total_w

    analysis = MatchAnalysis(
        match_id=match.id,
        home=match.home,
        away=match.away,
        status=match.status,
        ground_type=match.ground_type,
        model_home_probability=round(p_home, 4) if p_home is not None else None,
        model_away_probability=round(p_away, 4) if p_away is not None else None,
        factors=factors,
        unibet_matched=bool(unibet and unibet.matched),
    )

    # Value betting vs cotes Unibet (marché vainqueur du match)
    if p_home is not None and unibet and unibet.matched:
        odds_home, odds_away = _match_winner_odds(unibet, match)
        implied = remove_vig(odds_home, odds_away)
        if implied:
            for side, player, prob, odds, imp in (
                ("home", match.home.name, p_home, odds_home, implied[0]),
                ("away", match.away.name, p_away, odds_away, implied[1]),
            ):
                edge = prob - imp
                f = kelly_fraction(prob, odds)
                analysis.value_bets.append(ValueBet(
                    side=side, player=player, odds=odds,
                    model_probability=round(prob, 4),
                    implied_probability=round(imp, 4),
                    edge=round(edge, 4),
                    kelly_fraction=round(f, 4),
                    recommended_stake_pct=round(f * KELLY_FRACTION * 100, 2),
                    is_value=edge >= VALUE_THRESHOLD and f > 0,
                ))

    analysis.recommendation = _recommendation(analysis)
    return analysis


def _match_winner_odds(unibet: UnibetOdds, match: Match) -> tuple[float | None, float | None]:
    """Extrait (cote_home, cote_away) du marché vainqueur de match chez Unibet."""
    from app.providers.unibet import _norm_name  # réutilise la normalisation des noms

    home_tokens = _norm_name(match.home.name)
    for mk in unibet.markets:
        if (mk.type or "").lower() != "match":
            continue
        if len(mk.outcomes) != 2:
            continue
        o1, o2 = mk.outcomes
        # Associe chaque cote au bon joueur via le libellé.
        if _norm_name(o1.label) & home_tokens:
            return o1.odds, o2.odds
        if _norm_name(o2.label) & home_tokens:
            return o2.odds, o1.odds
        return o1.odds, o2.odds  # repli : ordre tel quel
    return None, None


def _recommendation(a: MatchAnalysis) -> str:
    values = [v for v in a.value_bets if v.is_value]
    if not a.factors:
        return "Données insuffisantes pour une analyse fiable."
    if not a.unibet_matched:
        fav = a.home.name if (a.model_home_probability or 0) >= 0.5 else a.away.name
        return f"Modèle : favori {fav}. Cotes Unibet indisponibles (match non à l'affiche)."
    if not values:
        return "Aucune value détectée : les cotes Unibet sont conformes au modèle. S'abstenir."
    best = max(values, key=lambda v: v.edge or 0)
    return (
        f"Value sur {best.player} @ {best.odds} "
        f"(edge +{round((best.edge or 0) * 100, 1)} pts, mise conseillée "
        f"{best.recommended_stake_pct}% de bankroll en ¼-Kelly)."
    )
