"""Back-test **walk-forward** du modèle (Elo par surface + classement).

But : récolter de l'info pour AMÉLIORER le modèle, à grande échelle (centaines/
milliers de matchs) là où la page Perf n'en a qu'une poignée. On rejoue l'historique
dans l'ordre chronologique : pour CHAQUE match on prédit avec les notes Elo
construites *uniquement sur le passé*, puis on met l'Elo à jour. Aucune fuite de
données — c'est une vraie évaluation hors-échantillon.

Ce que l'outil mesure :
  • Brier / log-loss / précision de chaque variante : classement seul, Elo seul,
    Elo+classement (les 2 facteurs dispo hors-ligne), et la version recalibrée ;
  • l'**ablation** : combiner Elo et classement fait-il mieux que chacun seul ?
  • la **calibration** (proba prédite vs taux réel) → révèle la surconfiance ;
  • le **shrink optimal** (CALIB_SHRINK) qui minimise le log-loss → à reporter dans
    app/analysis.py.

Note : la forme, la surface (stats service) et le h2h ne sont PAS rejoués ici (ils
exigent le contexte live de chaque match) — c'est justement pourquoi on enrichit le
suivi en direct (tracking.json) pour les diagnostiquer a posteriori.

Lancement :  python tools/backtest_model.py   (ou double-clic build_backtest.bat)
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

from app import elo  # noqa: E402
from app.analysis import WEIGHTS, prob_from_rankings, recalibrate  # noqa: E402
from build_elo import collect_events, collect_player_ids, B, H  # noqa: E402

# Un joueur doit avoir au moins ce nombre de matchs déjà joués pour qu'on évalue sa
# prédiction : sinon sa note Elo est encore au démarrage (1500) et fausse la mesure.
WARMUP = 10

# Poids relatifs Elo/classement repris du modèle (renormalisés sur ces deux facteurs).
_W_ELO = WEIGHTS["elo"] / (WEIGHTS["elo"] + WEIGHTS["classement"])
_W_RANK = 1.0 - _W_ELO


# --------------------------------------------------------------------- métriques
def log_loss(samples) -> float:
    s = 0.0
    for p, y in samples:
        p = min(max(p, 1e-12), 1 - 1e-12)
        s += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return s / len(samples)


def brier(samples) -> float:
    return sum((p - y) ** 2 for p, y in samples) / len(samples)


def accuracy(samples) -> float:
    return sum(1 for p, y in samples if (p >= 0.5) == (y == 1)) / len(samples)


def calibration(samples, bins=10):
    buckets = [[0, 0.0, 0] for _ in range(bins)]  # [n, somme_p, victoires]
    for p, y in samples:
        i = min(int(p * bins), bins - 1)
        buckets[i][0] += 1
        buckets[i][1] += p
        buckets[i][2] += y
    out = []
    for i, (n, sp, w) in enumerate(buckets):
        if n:
            out.append((f"{i/bins:.0%}-{(i+1)/bins:.0%}", n, sp / n, w / n))
    return out


def _line(name, samples):
    return (f"  {name:<26} n={len(samples):>5}  "
            f"log-loss={log_loss(samples):.4f}  "
            f"Brier={brier(samples):.4f}  "
            f"précision={accuracy(samples):.1%}")


# ------------------------------------------------------------------ walk-forward
def replay(events) -> list[dict]:
    """Rejoue l'historique chronologiquement et renvoie une prédiction par match.

    Chaque échantillon : {y, p_elo, p_rank|None, clay}. La note Elo utilisée est
    celle construite AVANT ce match (hors-échantillon) ; on met à jour ensuite.
    """
    events.sort(key=lambda ev: ev.get("startTimestamp") or 0)
    store: dict = {}
    samples: list[dict] = []
    for ev in events:
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hid, aid = ht.get("id"), at.get("id")
        if hid is None or aid is None:
            continue
        ground = ev.get("groundType")
        clay = elo.is_clay(ground)
        y = 1 if ev.get("winnerCode") == 1 else 0

        rh, ra = store.get(str(hid)), store.get(str(aid))
        eh = elo.surface_rating(rh, ground)
        ea = elo.surface_rating(ra, ground)
        warmed = (rh and (rh.get("overall_n") or 0) >= WARMUP and
                  ra and (ra.get("overall_n") or 0) >= WARMUP)
        if eh is not None and ea is not None and warmed:
            samples.append({
                "y": y,
                "p_elo": elo.expected_score(eh, ea),
                "p_rank": prob_from_rankings(ht.get("ranking"), at.get("ranking")),
                "clay": clay,
            })

        elo.update_ratings(store, hid, aid, home_won=(y == 1), on_clay=clay,
                           home_name=ht.get("name", ""), away_name=at.get("name", ""))
    return samples


def _blend(s, shrink=1.0) -> float:
    """Mélange Elo+classement (renormalisé), recalibré par `shrink`."""
    if s["p_rank"] is None:
        p = s["p_elo"]
    else:
        p = _W_ELO * s["p_elo"] + _W_RANK * s["p_rank"]
    return recalibrate(p, shrink)


def best_shrink(with_rank):
    """Cherche le shrink qui minimise le log-loss du mélange (pas de 0.05)."""
    best, best_ll = 1.0, float("inf")
    for k in range(10, 21):           # 0.50 -> 1.00
        s = k / 20.0
        ll = log_loss([(_blend(d, s), d["y"]) for d in with_rank])
        if ll < best_ll:
            best, best_ll = s, ll
    return best, best_ll


def main():
    print("Back-test walk-forward du modèle (Elo + classement).")
    print("Collecte de l'historique SofaScore (peut prendre quelques minutes)...\n")
    with httpx.Client(base_url=B, headers=H) as client:
        players = collect_player_ids(client)
        print(f"  {len(players)} joueurs ciblés.")
        events = collect_events(client, players)
    print(f"  {len(events)} matchs uniques collectés.\n")

    samples = replay(events)
    if not samples:
        print("Pas assez de données (Elo non réchauffé). Réessaie plus tard.")
        return
    with_rank = [s for s in samples if s["p_rank"] is not None]
    print(f"Évalués (Elo réchauffé ≥{WARMUP} matchs) : {len(samples)} matchs "
          f"dont {len(with_rank)} avec classements connus.\n")

    # --- Ablation : chaque facteur seul vs combiné (sur le MÊME sous-ensemble) ---
    print("=== Ablation des facteurs (sous-ensemble avec classements) ===")
    print(_line("classement seul", [(s["p_rank"], s["y"]) for s in with_rank]))
    print(_line("Elo seul", [(s["p_elo"], s["y"]) for s in with_rank]))
    print(_line("Elo + classement", [(_blend(s), s["y"]) for s in with_rank]))

    # Baseline : favori certain (montre le coût de la surconfiance)
    naive = [(0.999 if s["p_elo"] >= 0.5 else 0.001, s["y"]) for s in with_rank]
    print(_line("favori certain (naïf)", naive))
    print("  → si 'Elo+classement' a le log-loss le plus bas, combiner aide. Un "
          "facteur au Brier > 0.25 ou précision < 50% dégrade le mélange.\n")

    # --- Recalibration : shrink optimal ---
    shrink, ll_s = best_shrink(with_rank)
    base_ll = log_loss([(_blend(s), s["y"]) for s in with_rank])
    print("=== Recalibration (anti-surconfiance) ===")
    print(f"  log-loss brut (shrink=1.00) : {base_ll:.4f}")
    print(f"  shrink optimal              : {shrink:.2f}  (log-loss {ll_s:.4f})")
    gain = (base_ll - ll_s) / base_ll * 100 if base_ll else 0
    print(f"  gain                        : {gain:+.1f}%")
    print(f"  >>> À reporter dans app/analysis.py : CALIB_SHRINK = {shrink:.2f}\n")

    # --- Calibration du mélange recalibré ---
    print("=== Calibration (Elo+classement recalibré, sous-ensemble complet) ===")
    print(f"  {'tranche':>10} {'n':>6} {'prédit':>8} {'réel':>8}")
    for label, n, pmean, real in calibration([(_blend(s, shrink), s["y"]) for s in with_rank]):
        flag = "  <- surconfiant" if pmean - real > 0.05 else ""
        print(f"  {label:>10} {n:>6} {pmean:>8.1%} {real:>8.1%}{flag}")

    # --- Découpe par surface (où le modèle est-il meilleur ?) ---
    print("\n=== Performance par surface (Elo+classement recalibré) ===")
    for label, subset in (("terre battue", [s for s in with_rank if s["clay"]]),
                          ("dur/autre", [s for s in with_rank if not s["clay"]])):
        if subset:
            print(_line(label, [(_blend(s, shrink), s["y"]) for s in subset]))


if __name__ == "__main__":
    main()
