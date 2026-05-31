"""Validation : le modèle de SETS (dérivé de la proba de vainqueur) est-il calibré ?

Les marchés "Set Handicap +2.5" / "gagne au moins un set" se déduisent de la proba de
victoire du match : si un joueur a une proba de set s, alors P(il perd 0-3 en bo5) =
(1-s)^3, donc P(au moins un set) = 1 - (1-s)^3. On infère s depuis la proba de match.

⚠️ L'hypothèse "sets indépendants" SOUS-estime souvent les écrasements (0-3) : le
meilleur joueur du jour domine plus que le hasard. On VÉRIFIE donc, sur l'historique,
si la proba prédite "au moins un set pour l'outsider" colle au taux réel — et on en
tire un facteur de correction si besoin.

Prédicteur de proba de match : le classement (prob_from_rankings). Données : scores set
par set des événements SofaScore.

Lancement :  python tools/explore_sets.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

import httpx  # noqa: E402

from app.analysis import prob_from_rankings  # noqa: E402
from build_elo import B, H  # noqa: E402
from explore_breaks import collect_events, collect_players  # noqa: E402


def match_prob_from_set(s: float, best_of: int) -> float:
    """P(gagner le match) si proba de gagner un set = s (sets indépendants)."""
    q = 1 - s
    if best_of == 5:
        return s**3 * (1 + 3 * q + 6 * q**2)
    return s**2 * (1 + 2 * q)


def set_prob_from_match(p: float, best_of: int) -> float:
    """Inverse : proba de set s telle que P(match) = p (dichotomie)."""
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
    """P(le joueur gagne >= 1 set) à partir de sa proba de match."""
    s = set_prob_from_match(p_match, best_of)
    k = 3 if best_of == 5 else 2
    return 1 - (1 - s) ** k


def _sets(ev):
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    sh = sa = 0
    for k in ("period1", "period2", "period3", "period4", "period5"):
        h, a = hs.get(k), as_.get(k)
        if h is None or a is None:
            continue
        if h > a:
            sh += 1
        elif a > h:
            sa += 1
    return sh, sa


def main():
    print("Validation du modèle de sets (au moins un set / set handicap +2.5).\n")
    with httpx.Client(base_url=B, headers=H) as client:
        players = collect_players(client)
        events = collect_events(client, players)
    print(f"  {len(events)} matchs collectés.")

    # Échantillons : (proba prédite "outsider >=1 set", réel 0/1)
    samples = []
    for ev in events:
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        rh, ra = ht.get("ranking"), at.get("ranking")
        wc = ev.get("winnerCode")
        if not rh or not ra or wc not in (1, 2):
            continue
        sh, sa = _sets(ev)
        winner_sets = max(sh, sa)
        best_of = 5 if winner_sets == 3 else (3 if winner_sets == 2 else None)
        if best_of is None or sh + sa == 0:
            continue
        p_home = prob_from_rankings(rh, ra)
        if p_home is None:
            continue
        # outsider = proba de match la plus faible
        if p_home <= 0.5:
            p_under, under_sets = p_home, sh
        else:
            p_under, under_sets = 1 - p_home, sa
        pred = at_least_one_set(p_under, best_of)
        samples.append((pred, 1 if under_sets >= 1 else 0))

    if len(samples) < 200:
        print(f"Trop peu d'échantillons ({len(samples)}).")
        return
    n = len(samples)
    mean_pred = sum(p for p, _ in samples) / n
    mean_real = sum(y for _, y in samples) / n
    print(f"  {n} matchs évalués.\n")
    print("=== 'Outsider gagne au moins un set' : prédit vs réel ===")
    print(f"  proba moyenne prédite : {mean_pred:.1%}")
    print(f"  taux réel observé     : {mean_real:.1%}")
    print(f"  écart (prédit - réel) : {mean_pred - mean_real:+.1%}")
    factor = mean_real / mean_pred if mean_pred else 1.0
    print(f"  -> facteur de correction global suggéré : x{factor:.3f}\n")

    # Calibration par tranche de proba prédite
    print(f"  {'tranche':>12} {'n':>6} {'prédit':>8} {'réel':>8}")
    bins = [[0, 0.0, 0] for _ in range(5)]   # 50-60,...,90-100 (l'outsider >=1 set est haut)
    for p, y in samples:
        i = min(int(p * 10) - 5, 4) if p >= 0.5 else 0
        i = max(0, i)
        bins[i][0] += 1
        bins[i][1] += p
        bins[i][2] += y
    for i, (cnt, sp, w) in enumerate(bins):
        if cnt:
            lo = 50 + i * 10
            print(f"  {lo:>3}-{lo+10:>3}%   {cnt:>6} {sp/cnt:>8.1%} {w/cnt:>8.1%}")
    print()
    if abs(mean_pred - mean_real) < 0.03:
        print(">>> Modèle de sets BIEN CALIBRÉ : utilisable pour les marchés set/handicap.")
    elif mean_pred > mean_real:
        print(">>> Modèle SUR-optimiste (écrasements sous-estimés) : appliquer le facteur "
              "de correction avant de chercher de la value.")
    else:
        print(">>> Modèle sous-optimiste : marge prudente, value possible sur 'au moins un set'.")


if __name__ == "__main__":
    main()
