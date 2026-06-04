"""Simulation du déroulé d'un match de tennis pour évaluer TOUS les marchés Unibet.

Idée : à partir des % de points gagnés au service de chaque joueur (depuis les
stats), on calcule la probabilité de **tenir son service** (formule fermée du jeu
de tennis), puis on **simule** des milliers de matchs au niveau du jeu (gestion
des sets, tie-breaks, best-of-3/5). On en tire la distribution de : vainqueur,
nombre total de jeux, jeux par joueur, tie-breaks, score en sets, breaks…

Le niveau de service global vient des stats (il pilote le nombre de jeux/tie-breaks),
mais l'écart entre les deux joueurs est **calibré** pour que la proba de victoire
simulée colle à celle du modèle calibré (app/analysis). Les marchés annexes sont
donc cohérents avec notre estimation du vainqueur.

Fonctions pures (random seedé) → testables et déterministes.
"""

from __future__ import annotations

import random
import re

from app.models import MarketEdge, Match, PlayerStatistics, UnibetOdds
from app.providers.unibet import _norm_name

# Niveaux de service moyens par défaut (clay) si stats manquantes.
DEFAULT_SERVE = {"atp": 0.64, "wta": 0.57}
SERVE_MIN, SERVE_MAX = 0.52, 0.74

MODEL_TRUST = 0.35          # ancrage au marché (comme pour le vainqueur)
VALUE_THRESHOLD = 0.04      # edge minimal (4%) — un cran plus prudent sur les annexes
MIN_IMPLIED, MAX_IMPLIED = 0.08, 0.92
MAX_DISAGREEMENT = 0.18
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 4.0
N_SIM = 12000


def hold_prob(p: float) -> float:
    """P(le serveur gagne son jeu) selon p = P(gagner un point au service)."""
    q = 1 - p
    base = p**4 + 4 * p**4 * q + 10 * p**4 * q**2
    deuce = 20 * p**3 * q**3 * (p**2 / (p**2 + q**2)) if (p**2 + q**2) else 0
    return base + deuce


def serve_win_pct(stats: PlayerStatistics | None) -> float | None:
    """% de points gagnés au service à partir des stats (1ère + 2ème balle)."""
    if stats is None:
        return None
    fps, fpt = stats.first_serve_points_scored, stats.first_serve_points_total
    sps, spt = stats.second_serve_points_scored, stats.second_serve_points_total
    if None in (fps, fpt, sps, spt) or (fpt + spt) <= 0:
        return None
    return (fps + sps) / (fpt + spt)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _simulate(p1: float, p2: float, best_of: int, n: int, seed: int) -> dict:
    """Simule n matchs au niveau du jeu. Renvoie les distributions utiles."""
    rng = random.Random(seed)
    h1, h2 = hold_prob(p1), hold_prob(p2)
    denom = p1 * (1 - p2) + p2 * (1 - p1)
    tb1 = (p1 * (1 - p2) / denom) if denom else 0.5  # P(joueur1 gagne un tie-break)
    sets_to_win = 3 if best_of == 5 else 2

    win1 = 0
    tg, g1l, g2l, tbl, set_scores, brk1l, brk2l = [], [], [], [], [], [], []
    for _ in range(n):
        s1 = s2 = 0
        tg1 = tg2 = tbk = b1 = b2 = 0
        while s1 < sets_to_win and s2 < sets_to_win:
            g1 = g2 = 0
            server = 1 if ((s1 + s2) % 2 == 0) else 2  # alterne le 1er serveur par set
            while True:
                if (g1 >= 6 or g2 >= 6) and abs(g1 - g2) >= 2:
                    break
                if g1 == 6 and g2 == 6:
                    tbk += 1
                    if rng.random() < tb1:
                        g1 += 1
                    else:
                        g2 += 1
                    break
                hold = h1 if server == 1 else h2
                if rng.random() < hold:  # tenu
                    if server == 1:
                        g1 += 1
                    else:
                        g2 += 1
                else:                     # break
                    if server == 1:
                        g2 += 1
                        b2 += 1
                    else:
                        g1 += 1
                        b1 += 1
                server = 2 if server == 1 else 1
            tg1 += g1
            tg2 += g2
            if g1 > g2:
                s1 += 1
            else:
                s2 += 1
        if s1 > s2:
            win1 += 1
        tg.append(tg1 + tg2)
        g1l.append(tg1)
        g2l.append(tg2)
        tbl.append(tbk)
        brk1l.append(b1)
        brk2l.append(b2)
        set_scores.append((s1, s2))
    return {
        "n": n, "win1": win1, "total_games": tg, "games1": g1l, "games2": g2l,
        "tiebreaks": tbl, "breaks1": brk1l, "breaks2": brk2l, "set_scores": set_scores,
    }


def _win_prob(p1, p2, best_of, n, seed):
    r = _simulate(p1, p2, best_of, n, seed)
    return r["win1"] / r["n"]


def _devig_pair(o1, o2):
    if not o1 or not o2:
        return None
    a, b = 1 / o1, 1 / o2
    return a / (a + b)


def extract_market_anchors(unibet, home_tokens):
    """Récupère ce que le marché 'sait' : proba vainqueur home + ligne de jeux principale."""
    win_home = None
    games_line = games_over = None
    best_central = 9.9  # on cherche la ligne dont la proba 'over' est la plus proche de 0.5
    for mk in unibet.markets:
        lab = (mk.label or "").lower()
        mtype = mk.type or ""
        outs = mk.outcomes
        if mtype == "Match" and "cotes du match" in lab and len(outs) == 2:
            o1, o2 = outs
            p = _devig_pair(o1.odds, o2.odds)
            if p is not None:
                win_home = p if (_norm_name(o1.label) & home_tokens) else 1 - p
        elif lab == "nombre total de jeux" and mtype == "Plus de/Moins de" and len(outs) == 2:
            over = next((o for o in outs if "plus" in o.label.lower()), None)
            under = next((o for o in outs if "moins" in o.label.lower()), None)
            if over and under and over.odds and under.odds and over.line is not None:
                imp_over = _devig_pair(over.odds, under.odds)
                if imp_over is not None and abs(imp_over - 0.5) < best_central:
                    best_central = abs(imp_over - 0.5)
                    games_line = over.line
                    games_over = imp_over
    return win_home, games_line, games_over


def calibrate_to_market(target_win, games_line, games_over, fallback_level,
                        best_of, seed) -> dict:
    """Cale la simulation sur le MARCHÉ : proba vainqueur ET nombre total de jeux.

    Ainsi les marchés 'shape' (jeux/sets/tie-breaks) sont cohérents avec le book ;
    la value ne ressort que sur de vrais écarts de structure (rares)."""
    avg = _clamp(fallback_level, SERVE_MIN + 0.02, SERVE_MAX - 0.02)
    target_win = _clamp(target_win if target_win is not None else 0.5, 0.05, 0.95)

    def solve_gap(level):
        lo, hi = -0.28, 0.28
        for _ in range(6):
            mid = (lo + hi) / 2
            p1 = _clamp(level + mid / 2, SERVE_MIN, SERVE_MAX)
            p2 = _clamp(level - mid / 2, SERVE_MIN, SERVE_MAX)
            if _win_prob(p1, p2, best_of, 1500, seed) < target_win:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    gap = solve_gap(avg)
    # Coordonnée descente : ajuste le niveau pour coller à la ligne de jeux du marché
    if games_line is not None and games_over is not None:
        lo, hi = SERVE_MIN + 0.02, SERVE_MAX - 0.02
        for _ in range(6):
            avg = (lo + hi) / 2
            gap = solve_gap(avg)
            p1 = _clamp(avg + gap / 2, SERVE_MIN, SERVE_MAX)
            p2 = _clamp(avg - gap / 2, SERVE_MIN, SERVE_MAX)
            sim = _simulate(p1, p2, best_of, 1500, seed)
            over = _p_over(sim["total_games"], games_line)
            # plus de service -> plus de jeux -> P(over) plus haut
            if over < games_over:
                lo = avg
            else:
                hi = avg

    p1 = _clamp(avg + gap / 2, SERVE_MIN, SERVE_MAX)
    p2 = _clamp(avg - gap / 2, SERVE_MIN, SERVE_MAX)
    sim = _simulate(p1, p2, best_of, N_SIM, seed + 1)
    sim["p1"], sim["p2"] = p1, p2
    return sim


def calibrate_and_simulate(model_p_home: float, serve_level: float, best_of: int,
                           seed: int) -> dict:
    """Calibre l'écart de service pour que P(victoire home simulée) ≈ model_p_home."""
    avg = _clamp(serve_level, SERVE_MIN + 0.02, SERVE_MAX - 0.02)
    lo, hi = -0.28, 0.28
    for _ in range(7):  # recherche dichotomique sur l'écart de service
        mid = (lo + hi) / 2
        p1 = _clamp(avg + mid / 2, SERVE_MIN, SERVE_MAX)
        p2 = _clamp(avg - mid / 2, SERVE_MIN, SERVE_MAX)
        wp = _win_prob(p1, p2, best_of, 2500, seed)
        if wp < model_p_home:
            lo = mid
        else:
            hi = mid
    delta = (lo + hi) / 2
    p1 = _clamp(avg + delta / 2, SERVE_MIN, SERVE_MAX)
    p2 = _clamp(avg - delta / 2, SERVE_MIN, SERVE_MAX)
    sim = _simulate(p1, p2, best_of, N_SIM, seed + 1)
    sim["p1"], sim["p2"] = p1, p2
    return sim


# ------------------------------------------------------------- évaluation
def _p_over(values, line):
    return sum(1 for v in values if v > line) / len(values)


def _devig(odds_list):
    raws = [1 / o if o else 0 for o in odds_list]
    tot = sum(raws)
    return [r / tot if tot else None for r in raws]


def _model_prob_for_outcome(market_label, mtype, outcome, home_tokens, sim) -> float | None:
    """Probabilité modèle pour un choix donné, selon le type de marché."""
    n = sim["n"]
    label = (outcome.label or "")
    lab = market_label.lower()
    line = outcome.line
    is_home = bool(_norm_name(label) & home_tokens)

    # Over/Under
    if "moins de" in label.lower() or "plus de" in label.lower() or mtype == "Plus de/Moins de":
        over = "plus" in label.lower()
        if line is None:
            return None
        if "tiebreak" in lab:
            p = _p_over(sim["tiebreaks"], line)
        elif "remport" in lab and "jeux" in lab:  # jeux remportés par un joueur
            # déterminé par le joueur cité dans le LABEL du marché (géré en amont)
            return None
        elif "jeux" in lab and "set" in lab:
            return None  # total de jeux d'un set précis : non modélisé ici
        elif "jeux" in lab:
            p = _p_over(sim["total_games"], line)
        elif "sets" in lab:
            totals = [a + b for a, b in sim["set_scores"]]
            p = _p_over(totals, line)
        else:
            return None
        return p if over else 1 - p

    # Vainqueur du match
    if mtype == "Match" and ("cotes du match" in lab or lab == "match"):
        wp = sim["win1"] / n
        return wp if is_home else 1 - wp

    # "remporte au moins un set"
    if "au moins un set" in lab:
        # le joueur cité est dans le market_label ; ici label = Oui/Non
        return None  # géré spécifiquement plus bas

    # Pari de set (score exact en sets) : labels "3-1", "0-3"...
    if mtype == "Résultat" and "pari de set" in lab and "-" in label:
        try:
            a, b = (int(x) for x in label.split("-"))
        except ValueError:
            return None
        return sum(1 for s in sim["set_scores"] if s == (a, b)) / n

    return None


def evaluate_markets(match: Match, unibet: UnibetOdds, sim: dict) -> list[MarketEdge]:
    home_tokens = _norm_name(match.home.name)
    out: list[MarketEdge] = []
    wp_home = sim["win1"] / sim["n"]

    for mk in unibet.markets:
        mtype = mk.type or ""
        label = mk.label or ""
        lab = label.lower()
        outs = mk.outcomes
        if not outs:
            continue
        implied = _devig([o.odds for o in outs])

        # Probabilités modèle par choix (selon le marché)
        model_ps: list[float | None] = [None] * len(outs)

        if "remport" in lab and "jeux" in lab and mtype == "Plus de/Moins de":
            # "Nombre total de jeux remportés par <joueur>"
            who = sim["games1"] if (_norm_name(label) & home_tokens) else sim["games2"]
            for i, o in enumerate(outs):
                if o.line is None:
                    continue
                p = _p_over(who, o.line)
                model_ps[i] = p if "plus" in o.label.lower() else 1 - p

        elif "au moins un set" in lab:
            # P(le joueur cité gagne >= 1 set)
            cited_home = bool(_norm_name(label) & home_tokens)
            won_at_least = sum(1 for s in sim["set_scores"]
                               if (s[0] if cited_home else s[1]) >= 1) / sim["n"]
            for i, o in enumerate(outs):
                yes = o.label.lower().startswith("oui")
                model_ps[i] = won_at_least if yes else 1 - won_at_least

        elif "handicap du jeu" in lab and mtype == "Handicap":
            # X(-3.5) : P(jeux_X - jeux_adv > 3.5)
            for i, o in enumerate(outs):
                if o.line is None:
                    continue
                diff = [a - b for a, b in zip(sim["games1"], sim["games2"])]
                if not (_norm_name(o.label) & home_tokens):
                    diff = [-d for d in diff]
                model_ps[i] = sum(1 for d in diff if d > o.line) / sim["n"]

        elif "set handicap" in lab and mtype == "Handicap":
            for i, o in enumerate(outs):
                if o.line is None:
                    continue
                diff = [a - b for a, b in sim["set_scores"]]
                if not (_norm_name(o.label) & home_tokens):
                    diff = [-d for d in diff]
                model_ps[i] = sum(1 for d in diff if d > o.line) / sim["n"]

        elif "plupart des jeux" in lab or "plus grand nombre de breaks" in lab:
            a_list = sim["games1"] if "jeux" in lab else sim["breaks1"]
            b_list = sim["games2"] if "jeux" in lab else sim["breaks2"]
            p1 = sum(1 for a, b in zip(a_list, b_list) if a > b) / sim["n"]
            p2 = sum(1 for a, b in zip(a_list, b_list) if b > a) / sim["n"]
            pX = 1 - p1 - p2
            for i, o in enumerate(outs):
                model_ps[i] = {"1": p1, "2": p2, "x": pX}.get(o.label.lower().strip())

        else:
            for i, o in enumerate(outs):
                model_ps[i] = _model_prob_for_outcome(label, mtype, o, home_tokens, sim)

        # Construit les MarketEdge avec ancrage marché + garde-fous
        for i, o in enumerate(outs):
            mp, imp = model_ps[i], implied[i]
            if mp is None or imp is None:
                continue
            fair = MODEL_TRUST * mp + (1 - MODEL_TRUST) * imp
            edge = fair - imp
            raw_gap = mp - imp
            f = 0.0
            if o.odds and o.odds > 1:
                b = o.odds - 1
                f = max(0.0, (b * fair - (1 - fair)) / b)
            is_value = (
                edge >= VALUE_THRESHOLD
                and MIN_IMPLIED <= imp <= MAX_IMPLIED
                and raw_gap <= MAX_DISAGREEMENT
                and f > 0
            )
            out.append(MarketEdge(
                market=label, selection=o.label, line=o.line, odds=o.odds,
                model_probability=round(mp, 4), implied_probability=round(imp, 4),
                edge=round(edge, 4),
                recommended_stake_pct=round(min(f * KELLY_FRACTION * 100, MAX_STAKE_PCT), 2),
                is_value=is_value,
            ))
    return out


# ============================== moteur PERLE tennis (sélection sur les MarketEdge existants)
# Le tennis évalue déjà TOUS les marchés (vainqueur, sets, jeux, aces, simulateur) avec un edge
# ancré au marché. La « perle » = même logique que foot/basket : depuis ce pool, la CONFIANCE
# (proba max) et la VALUE (edge max), cote conf ≥ 1,20 / value ≥ 1,50.
T_MIN_PROB = 0.52
T_MIN_ODDS = 1.20
T_VALUE_MIN_ODDS = 1.50
T_MIN_EDGE = 0.03
T_N_CONFIANCES = 2
T_CONF2_MIN_PROB = 0.62    # le 2e pari de confiance doit rester solide


def _edge_label(me) -> str:
    """Libellé lisible d'un pari tennis (le marché donne le contexte que la sélection seule perd)."""
    sel = (me.selection or "").strip()
    mkt = (me.market or "").strip()
    low = mkt.lower()
    if sel.lower() in ("oui", "non"):                         # « X remporte au moins un set : Oui »
        return mkt if sel.lower() == "oui" else f"NON — {mkt}"
    if me.line is not None and sel.lower() in ("plus de", "moins de", "plus", "moins", "over", "under"):
        unit = "jeux" if "jeu" in low else ("aces" if "ace" in low else "")
        return f"{sel} {me.line:g} {unit}".strip()
    if me.line is not None:                                    # handicap : « Joueur -4.5 »
        return f"{sel} {me.line:+g}".strip()
    return sel


def best_picks_tennis(edges) -> dict | None:
    """1-2 confiances (proba max, marchés distincts) + value (edge max, cote ≥ 1,50) depuis les
    MarketEdge déjà calculés. None si aucun pari jouable."""
    cands = []
    for me in edges or []:
        mp_raw, od, eg, imp = (me.model_probability, me.odds, me.edge, me.implied_probability)
        if mp_raw is None or od is None or eg is None or imp is None:
            continue
        # proba ANCRÉE au marché (cohérente avec l'edge), pas la proba brute du simulateur
        # (sur-confiante sur les petits tournois). Garde-fou : si le brut s'écarte trop du marché,
        # c'est le modèle qui a tort -> on écarte (comme foot/basket).
        fair = imp + eg
        if abs(mp_raw - imp) > 0.20:
            continue
        if fair >= T_MIN_PROB and od >= T_MIN_ODDS and eg >= T_MIN_EDGE:
            cands.append({"selection": _edge_label(me), "market": me.market,
                          "odds": round(od, 2), "model_prob": round(fair, 4), "edge": round(eg, 4)})
    if not cands:
        return None
    confidences, seen, seen_sel = [], set(), set()
    for c in sorted(cands, key=lambda c: -c["model_prob"]):
        if c["market"] in seen:
            continue
        # 2e pari = marché ET sélection DISTINCTS (sinon doublon -> rien).
        sel = (c.get("selection") or "").strip().lower()
        if sel in seen_sel:
            continue
        if confidences and c["model_prob"] < T_CONF2_MIN_PROB:   # 2e pari : solide aussi
            break
        confidences.append(c)
        seen.add(c["market"])
        seen_sel.add(sel)
        if len(confidences) >= T_N_CONFIANCES:
            break
    payed = [c for c in cands if c["odds"] >= T_VALUE_MIN_ODDS]
    value = max(payed, key=lambda c: c["edge"]) if payed else None
    return {"confidences": confidences, "confidence": confidences[0], "value": value}


def settle_tennis_perle(perle, winner, sets_home, sets_away, total_games,
                        home_name, away_name, score_str=None):
    """Règle une perle tennis -> P&L (mise plate 1 u) ou None si NON vérifiable de façon fiable.
    Marchés réglés : « au moins un set », Total de jeux (Plus/Moins), Vainqueur, Handicap de jeux
    (match entier ou set 1) via le score set par set. Les aces -> None (pas de données).
    `score_str` = score set par set (ex. « 6-4 6-1 »)."""
    if not (isinstance(perle, dict) and perle.get("odds")) or winner not in ("home", "away"):
        return None
    odds = perle["odds"]
    market = (perle.get("market") or "").lower()
    sel = perle.get("selection") or ""
    sl = sel.lower()

    def pnl(w):
        return (odds - 1) if w else -1.0

    def _sets():
        out = []
        for part in str(score_str or "").split():
            if "-" in part:
                try:
                    out.append(tuple(int(x) for x in part.split("-")))
                except ValueError:
                    pass
        return out

    def which():   # quel joueur la sélection désigne-t-elle ? (par les noms)
        st = _norm_name(sel)
        ht, at = _norm_name(home_name or ""), _norm_name(away_name or "")
        if (st & ht) and not (st & at):
            return "home"
        if (st & at) and not (st & ht):
            return "away"
        return None

    # Handicap de JEUX (match entier ou set 1) : « X -2.5 » / « X +3.5 »
    if "handicap" in market and ("jeu" in market or "game" in market):
        who = which()
        mo = re.search(r"[+-]\d+(?:[.,]\d+)?", sel)
        sets = _sets()
        if not (who and mo and sets):
            return None
        line = float(mo.group(0).replace(",", "."))
        if "set 1" in market or "set1" in market or "1er set" in market or "1re" in market:
            gh, ga = sets[0]                       # jeux du SET 1 uniquement
        else:
            gh, ga = sum(h for h, a in sets), sum(a for h, a in sets)   # jeux du match
        margin = (gh - ga) if who == "home" else (ga - gh)
        return pnl(margin + line > 0)              # lignes .5 -> jamais de push
    # « X remporte au moins un set »
    if "au moins un set" in sl or "moins un set" in sl or "at least one set" in sl:
        who = which()
        if who is None or sets_home is None or sets_away is None:
            return None
        return pnl((sets_home if who == "home" else sets_away) >= 1)
    # Total de jeux : « Plus de 20.5 jeux » / « Moins de … »
    if "jeu" in sl and total_games is not None:
        mo = re.search(r"(\d+(?:[.,]\d+)?)", sl)
        if mo:
            line = float(mo.group(1).replace(",", "."))
            if "plus" in sl or "over" in sl:
                return pnl(total_games > line)
            if "moins" in sl or "under" in sl:
                return pnl(total_games < line)
    # Vainqueur (marché dédié, sélection = nom d'un joueur)
    if "vainqueur" in market or "winner" in market:
        who = which()
        return pnl(winner == who) if who else None
    return None


def tennis_perle_live_status(perle, score_str, home_name, away_name):
    """Statut LIVE d'une perle tennis : 'won'/'lost'/None. « au moins un set » -> 'won' dès qu'un
    set est gagné ; « Plus de N jeux » -> 'won' si dépassé ; « Moins de N jeux » -> 'lost' si dépassé."""
    if not (isinstance(perle, dict) and perle.get("selection") and score_str):
        return None
    sel = (perle.get("selection") or "").lower()
    sets = []
    for part in str(score_str).split():
        if "-" in part:
            try:
                h, a = (int(x) for x in part.split("-"))
            except ValueError:
                continue
            sets.append((h, a))
    if not sets:
        return None

    def sets_won(side):
        n = 0
        for h, a in sets:
            hi, lo = max(h, a), min(h, a)
            if (hi >= 6 and hi - lo >= 2) or hi == 7:
                if (h > a) == (side == "home"):
                    n += 1
        return n
    total_games = sum(h + a for h, a in sets)
    if "au moins un set" in sel or "moins un set" in sel:
        st = _norm_name(perle.get("selection") or "")
        ht, at = _norm_name(home_name or ""), _norm_name(away_name or "")
        who = "home" if (st & ht) and not (st & at) else ("away" if (st & at) and not (st & ht) else None)
        return "won" if (who and sets_won(who) >= 1) else None
    if "jeu" in sel:
        mo = re.search(r"(\d+(?:[.,]\d+)?)", sel)
        if mo:
            line = float(mo.group(1).replace(",", "."))
            if ("plus" in sel or "over" in sel) and total_games > line:
                return "won"
            if ("moins" in sel or "under" in sel) and total_games > line:
                return "lost"
    return None


def tennis_perle_live_won(perle, score_str, home_name, away_name) -> bool:
    """Perle tennis DÉJÀ gagnée vu le score LIVE (set par set), ex. « au moins un set » dès qu'un
    set est remporté, ou « Plus de N jeux » dès que le total dépasse N. False sinon (ou incertain)."""
    if not (isinstance(perle, dict) and perle.get("selection") and score_str):
        return False
    sel = (perle.get("selection") or "").lower()
    sets = []
    for part in str(score_str).split():
        if "-" in part:
            try:
                h, a = (int(x) for x in part.split("-"))
            except ValueError:
                continue
            sets.append((h, a))
    if not sets:
        return False

    def sets_won(side):                                # sets TERMINÉS gagnés par ce camp
        n = 0
        for h, a in sets:
            hi, lo = max(h, a), min(h, a)
            if (hi >= 6 and hi - lo >= 2) or hi == 7:   # set terminé
                if (h > a) == (side == "home"):
                    n += 1
        return n
    total_games = sum(h + a for h, a in sets)
    # « X remporte au moins un set »
    if "au moins un set" in sel or "moins un set" in sel:
        st = _norm_name(perle.get("selection") or "")
        ht, at = _norm_name(home_name or ""), _norm_name(away_name or "")
        who = "home" if (st & ht) and not (st & at) else ("away" if (st & at) and not (st & ht) else None)
        return bool(who and sets_won(who) >= 1)
    # « Plus de N jeux » : gagné dès que le total dépasse la ligne
    if "jeu" in sel and ("plus" in sel or "over" in sel):
        mo = re.search(r"(\d+(?:[.,]\d+)?)", sel)
        if mo:
            return total_games > float(mo.group(1).replace(",", "."))
    return False


def tennis_all_edges(match, odds, analysis, tour, seed, home_stats=None, away_stats=None):
    """TOUS les edges tennis d'un match (vainqueur, sets, jeux/handicap, aces, simulateur) en une
    liste de MarketEdge — réutilisé par le détail ET le snapshot pour en tirer la perle."""
    from app import ace_markets, set_markets, tendencies
    best_of = 5 if tour == "atp" else 3
    edges: list[MarketEdge] = []
    for vb in (analysis.value_bets or []):
        edges.append(MarketEdge(market="Vainqueur", selection=vb.player, odds=vb.odds,
                                model_probability=vb.model_probability,
                                implied_probability=vb.implied_probability, edge=vb.edge, line=None))
    try:
        edges += set_markets.evaluate(match, odds, best_of,
                                      analysis.model_home_probability, analysis.model_away_probability)
    except Exception:
        pass
    try:
        store = tendencies.load_cached()
        fav_prob = max(analysis.model_home_probability or 0.5, analysis.model_away_probability or 0.5)
        rh = tendencies.ace_rate(store.get(str(match.home.id)), match.ground_type)
        ra = tendencies.ace_rate(store.get(str(match.away.id)), match.ground_type)
        edges += ace_markets.evaluate(match, odds, best_of, rh, ra, fav_prob)
    except Exception:
        pass
    try:
        levels = [v for v in (serve_win_pct(home_stats), serve_win_pct(away_stats)) if v is not None]
        serve_level = sum(levels) / len(levels) if levels else DEFAULT_SERVE[tour]
        home_tokens = _norm_name(match.home.name)
        mkt_win, games_line, games_over = extract_market_anchors(odds, home_tokens)
        model_p = analysis.model_home_probability
        target = (0.7 * mkt_win + 0.3 * model_p) if (mkt_win is not None and model_p is not None) \
            else (mkt_win if mkt_win is not None else (model_p or 0.5))
        sim = calibrate_to_market(target, games_line, games_over, serve_level, best_of, seed=seed)
        edges += evaluate_markets(match, odds, sim)
    except Exception:
        pass
    return edges
