"""Marchés de SETS : "remporte au moins un set" et "Set Handicap ±2.5".

Ces paris à faible cote / haute probabilité se déduisent de la proba de vainqueur du
match (notre modèle, validé) via la proba de gagner un set. Mais le modèle "sets
indépendants" SUR-estime la compétitivité (les 0-3 sont plus fréquents que le hasard).
On applique donc une **correction calibrée sur l'historique** (tools/explore_sets.py :
prédit 69% vs réel 56% pour "outsider ≥1 set" -> correction linéaire).

Résultat honnête : après correction, le modèle REJOINT le book sur ces marchés (ils
sont bien cotés). La value n'apparaît que quand notre proba de match s'écarte du book.
Fonctions pures (sans réseau).
"""

from __future__ import annotations

from app.models import MarketEdge, Match, UnibetOdds
from app.providers.unibet import _norm_name

# Correction calibrée (régression sur ~4250 matchs, cf. tools/explore_sets.py) :
# proba_réelle("au moins un set") ≈ A * proba_IID + B, dans la plage des outsiders.
CAL_A, CAL_B = 1.09, -0.216

MODEL_TRUST = 0.50
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.20, 0.92


def match_prob_from_set(s: float, best_of: int) -> float:
    q = 1 - s
    if best_of == 5:
        return s**3 * (1 + 3 * q + 6 * q**2)
    return s**2 * (1 + 2 * q)


def set_prob_from_match(p: float, best_of: int) -> float:
    """Proba de gagner un set telle que P(match)=p (dichotomie, sets indépendants)."""
    p = min(max(p, 1e-4), 1 - 1e-4)
    lo, hi = 1e-4, 1 - 1e-4
    for _ in range(40):
        mid = (lo + hi) / 2
        if match_prob_from_set(mid, best_of) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def at_least_one_set(p_match: float, best_of: int) -> float:
    """P(le joueur gagne >= 1 set), CALIBRÉE sur le réel.

    On part du modèle indépendant puis on corrige (il sur-estime). Pour un favori très
    net (proba IID >= 0.88) la correction ne s'applique pas (il prend quasi toujours un
    set) : on garde la valeur brute.
    """
    s = set_prob_from_match(p_match, best_of)
    k = 3 if best_of == 5 else 2
    raw = 1 - (1 - s) ** k
    if raw >= 0.88:
        return raw
    return max(0.02, min(0.97, CAL_A * raw + CAL_B))


def _devig(o1, o2):
    if not o1 or not o2:
        return None
    a, b = 1 / o1, 1 / o2
    return a / (a + b)


def _edge(model_p, imp, odds):
    fair = MODEL_TRUST * model_p + (1 - MODEL_TRUST) * imp
    edge = fair - imp
    stake = 0.0
    if odds and odds > 1:
        b = odds - 1
        stake = min(max(0.0, (b * fair - (1 - fair)) / b) * 0.25 * 100, 3.0)
    is_value = (edge >= VALUE_THRESHOLD and MIN_IMPLIED <= imp <= MAX_IMPLIED and stake > 0)
    return round(edge, 4), is_value, round(stake, 2)


def evaluate(match: Match, unibet: UnibetOdds, best_of: int,
             p_home: float | None, p_away: float | None) -> list[MarketEdge]:
    """Évalue 'remporte au moins un set' et 'Set Handicap ±2.5' (calibrés)."""
    if p_home is None or p_away is None:
        return []
    home_tokens = _norm_name(match.home.name)
    als_home = at_least_one_set(p_home, best_of)
    als_away = at_least_one_set(p_away, best_of)
    out: list[MarketEdge] = []

    for mk in unibet.markets:
        label = mk.label or ""
        lab = label.lower()
        outs = mk.outcomes

        # "X remporte au moins un set" : Oui / Non
        if "au moins un set" in lab and len(outs) == 2:
            cited_home = bool(_norm_name(label) & home_tokens)
            als = als_home if cited_home else als_away
            o_oui = next((o for o in outs if (o.label or "").lower().startswith("oui")), None)
            o_non = next((o for o in outs if (o.label or "").lower().startswith("non")), None)
            if not (o_oui and o_non):
                continue
            imp_oui = _devig(o_oui.odds, o_non.odds)
            for o, mp, imp in ((o_oui, als, imp_oui),
                               (o_non, 1 - als, 1 - imp_oui if imp_oui else None)):
                if imp is None:
                    continue
                edge, isv, stake = _edge(mp, imp, o.odds)
                out.append(MarketEdge(
                    market=label, selection=o.label, odds=o.odds,
                    model_probability=round(mp, 4), implied_probability=round(imp, 4),
                    edge=edge, recommended_stake_pct=stake, is_value=isv))
            continue

        # "Nombre total de sets" : Plus/Moins. P(match NON sec) = P(les deux gagnent
        # >=1 set) = als_home + als_away - 1 (events "perd 0 set" mutuellement exclusifs).
        # On ne traite que la ligne standard (3.5 en bo5, 2.5 en bo3).
        if "total de sets" in lab and len(outs) == 2:
            std_line = 3.5 if best_of == 5 else 2.5
            over = next((o for o in outs if "plus" in (o.label or "").lower()), None)
            under = next((o for o in outs if "moins" in (o.label or "").lower()), None)
            if not (over and under) or over.line is None or abs(over.line - std_line) > 0.01:
                continue
            p_over = max(0.02, min(0.98, als_home + als_away - 1))
            imp_over = _devig(over.odds, under.odds)
            for o, mp, imp in ((over, p_over, imp_over),
                               (under, 1 - p_over, 1 - imp_over if imp_over else None)):
                if imp is None:
                    continue
                edge, isv, stake = _edge(mp, imp, o.odds)
                out.append(MarketEdge(
                    market=label, selection=o.label, line=o.line, odds=o.odds,
                    model_probability=round(mp, 4), implied_probability=round(imp, 4),
                    edge=edge, recommended_stake_pct=stake, is_value=isv))
            continue

        # "Set Handicap" : on ne traite que les lignes ±2.5 (= au moins un set / 3-0)
        if "set handicap" in lab and len(outs) >= 2:
            imps = _devig_multi([o.odds for o in outs])
            for o, imp in zip(outs, imps):
                if o.line is None or imp is None:
                    continue
                cited_home = bool(_norm_name(o.label) & home_tokens)
                als_cited = als_home if cited_home else als_away
                als_opp = als_away if cited_home else als_home
                if abs(o.line - 2.5) < 0.01:          # X +2.5 : X gagne >= 1 set
                    mp = als_cited
                elif abs(o.line + 2.5) < 0.01:        # X -2.5 : X gagne 3-0 (adv 0 set)
                    mp = 1 - als_opp
                else:
                    continue                          # autres lignes : non modélisées ici
                edge, isv, stake = _edge(mp, imp, o.odds)
                out.append(MarketEdge(
                    market=label, selection=o.label, line=o.line, odds=o.odds,
                    model_probability=round(mp, 4), implied_probability=round(imp, 4),
                    edge=edge, recommended_stake_pct=stake, is_value=isv))
            continue

    return out


def _devig_multi(odds_list):
    raws = [1 / o if o else 0 for o in odds_list]
    tot = sum(raws)
    return [r / tot if tot else None for r in raws]
