"""Évaluation des marchés d'aces Unibet (Total Aces, aces/joueur, le plus d'aces).

Stratégie **sharp** : le bookmaker estime très bien le *volume total* d'aces (durée
du match × niveau de service), mais notre signal validé (tools/explore_aces.py,
corrélation 0.51) porte sur la **répartition** entre les deux joueurs — qui sert le
plus d'aces. On :
  1. ANCRE le total d'aces sur la ligne « Total Aces » du book (lambda implicite) ;
  2. RÉPARTIT ce total entre les joueurs selon leur tendance individuelle ;
  3. ne cherche la value que sur les marchés par joueur / « le plus d'aces », là où
     notre edge est réel — pas sur le total (où l'on fait confiance au book).

Le compte d'aces d'un joueur est modélisé par une loi de Poisson de moyenne lambda.
Fonctions pures (sans réseau) -> testables.
"""

from __future__ import annotations

import math

from app.models import MarketEdge, Match, UnibetOdds
from app.providers.unibet import _norm_name
from app.tendencies import expected_service_games, prob_over

# Garde-fous (proches du reste du modèle, un cran prudent : marché de niche).
MODEL_TRUST = 0.50          # value = écart APRÈS mélange 50/50 modèle/book
VALUE_THRESHOLD = 0.05      # edge minimal (5 %)
MIN_IMPLIED, MAX_IMPLIED = 0.10, 0.90
MAX_DISAGREEMENT = 0.20     # au-delà, on suppose que le book sait qqch (blessure…)


def _devig(o1: float | None, o2: float | None) -> float | None:
    """Proba implicite du 1er résultat (vig retirée), ou None."""
    if not o1 or not o2:
        return None
    a, b = 1 / o1, 1 / o2
    return a / (a + b)


def lambda_from_line(line: float, p_over: float) -> float:
    """Lambda Poisson tel que P(X > line) = p_over (recherche dichotomique).

    P(X>line) croît avec lambda -> bisection sûre. Sert à lire le 'volume' que le
    book price sur une ligne Over/Under d'aces.
    """
    p_over = min(max(p_over, 1e-4), 1 - 1e-4)
    lo, hi = 0.01, 80.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if prob_over(line, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def split_lambda(total: float, rate_home: float | None,
                 rate_away: float | None) -> tuple[float, float]:
    """Répartit un total d'aces entre les joueurs selon leur tendance.

    Si une tendance manque, on partage 50/50 (on ne prétend rien savoir de plus)."""
    if rate_home and rate_away and (rate_home + rate_away) > 0:
        fh = rate_home / (rate_home + rate_away)
    else:
        fh = 0.5
    return total * fh, total * (1 - fh)


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def most_aces_probs(lam_home: float, lam_away: float,
                    kmax: int = 60) -> tuple[float, float, float]:
    """(P(home>away), P(égalité), P(away>home)) pour deux comptes Poisson indépendants."""
    ph = [_poisson_pmf(k, lam_home) for k in range(kmax + 1)]
    pa = [_poisson_pmf(k, lam_away) for k in range(kmax + 1)]
    p_home = p_eq = p_away = 0.0
    for i in range(kmax + 1):
        cum_lt = sum(pa[:i])          # P(away < i)
        p_home += ph[i] * cum_lt
        p_eq += ph[i] * pa[i]
        p_away += ph[i] * (1 - cum_lt - pa[i])
    return p_home, p_eq, p_away


def _is_ace_market(label: str) -> bool:
    return "ace" in (label or "").lower()


def _edge(model_p: float, imp: float, odds: float | None) -> tuple[float, bool, float]:
    """(edge ancré, is_value, mise %). Mêmes garde-fous que le reste du modèle."""
    fair = MODEL_TRUST * model_p + (1 - MODEL_TRUST) * imp
    edge = fair - imp
    raw_gap = model_p - imp
    stake = 0.0
    if odds and odds > 1:
        b = odds - 1
        f = max(0.0, (b * fair - (1 - fair)) / b)
        stake = min(f * 0.25 * 100, 3.0)            # ¼ Kelly, plafond 3 %
    is_value = (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp <= MAX_IMPLIED
                and raw_gap <= MAX_DISAGREEMENT and stake > 0)
    return edge, is_value, stake


def evaluate(match: Match, unibet: UnibetOdds, best_of: int,
             rate_home: float | None, rate_away: float | None,
             fav_prob: float | None) -> list[MarketEdge]:
    """Évalue tous les marchés d'aces d'Unibet. Renvoie une liste de MarketEdge."""
    home_tokens = _norm_name(match.home.name)

    # 1) Lambda TOTAL : ancré sur la ligne 'Total Aces' du book si dispo, sinon estimé.
    lam_total = None
    for mk in unibet.markets:
        if (mk.label or "").strip().lower() == "total aces" and len(mk.outcomes) == 2:
            over = next((o for o in mk.outcomes if "plus" in (o.label or "").lower()), None)
            under = next((o for o in mk.outcomes if "moins" in (o.label or "").lower()), None)
            if over and under and over.line is not None:
                p_over = _devig(over.odds, under.odds)
                if p_over is not None:
                    lam_total = lambda_from_line(over.line, p_over)
            break
    if lam_total is None:                 # repli : notre propre estimation
        sg = expected_service_games(best_of, fav_prob)
        rh, ra = rate_home or 0.0, rate_away or 0.0
        lam_total = (rh + ra) * sg if (rh or ra) else None
    if lam_total is None:
        return []                          # aucune tendance ET pas de ligne -> rien

    lam_home, lam_away = split_lambda(lam_total, rate_home, rate_away)

    out: list[MarketEdge] = []
    for mk in unibet.markets:
        label = mk.label or ""
        if not _is_ace_market(label):
            continue
        lab = label.lower()
        outs = mk.outcomes

        # Total Aces : on price comme le book par construction -> info, jamais 'value'.
        if lab.strip() == "total aces" and len(outs) == 2:
            for o in outs:
                if o.line is None:
                    continue
                over = "plus" in (o.label or "").lower()
                mp = prob_over(o.line, lam_total)
                mp = mp if over else 1 - mp
                imp = o.implied_probability
                out.append(MarketEdge(
                    market=label, selection=o.label, line=o.line, odds=o.odds,
                    model_probability=round(mp, 4),
                    implied_probability=round(imp, 4) if imp else None,
                    edge=0.0, recommended_stake_pct=0.0, is_value=False))
            continue

        # Aces par joueur : "Nombre total d'aces - <joueur>"
        if "aces" in lab and ("nombre" in lab or " - " in label) and len(outs) == 2:
            lam = lam_home if (_norm_name(label) & home_tokens) else lam_away
            o1, o2 = outs
            i1 = _devig(o1.odds, o2.odds)
            for o, imp in ((o1, i1), (o2, 1 - i1 if i1 is not None else None)):
                if o.line is None or imp is None:
                    continue
                over = "plus" in (o.label or "").lower()
                mp = prob_over(o.line, lam)
                mp = mp if over else 1 - mp
                edge, is_value, stake = _edge(mp, imp, o.odds)
                out.append(MarketEdge(
                    market=label, selection=o.label, line=o.line, odds=o.odds,
                    model_probability=round(mp, 4), implied_probability=round(imp, 4),
                    edge=round(edge, 4), recommended_stake_pct=round(stake, 2),
                    is_value=is_value))
            continue

        # "Le plus d'aces" : 1 / X (égalité) / 2
        if "plus d'aces" in lab or "plus d aces" in lab:
            p_home, p_eq, p_away = most_aces_probs(lam_home, lam_away)
            raws = [1 / o.odds if o.odds else 0 for o in outs]
            tot = sum(raws) or 1
            for o in outs:
                key = (o.label or "").strip().lower()
                mp = {"1": p_home, "x": p_eq, "2": p_away}.get(key)
                if mp is None:
                    continue
                imp = (1 / o.odds) / tot if o.odds else None
                if imp is None:
                    continue
                edge, is_value, stake = _edge(mp, imp, o.odds)
                out.append(MarketEdge(
                    market=label, selection=o.label, line=o.line, odds=o.odds,
                    model_probability=round(mp, 4), implied_probability=round(imp, 4),
                    edge=round(edge, 4), recommended_stake_pct=round(stake, 2),
                    is_value=is_value))
            continue

    return out
