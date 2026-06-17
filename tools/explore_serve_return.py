"""Validation : la domination SERVICE+RETOUR passée prédit-elle le vainqueur ?

On améliore le cœur du modèle (facteur 'surface' actuel = stats de saison agrégées).
Idée : pour chaque joueur, mesurer sa **tenue de service** (1 - taux de break subi) et
son **taux de break** (breaks réalisés / jeux de retour) à partir de l'historique, puis
prédire le vainqueur en comparant la 'domination' (tenue + break) des deux joueurs.

Avant d'intégrer ce facteur, on vérifie en walk-forward (sans fuite) qu'il prédit bien,
et on compare sa précision à celle du classement (~64% au back-test). Réutilise le cache
data/cache_stats.json (déjà rempli par explore_breaks) — aucune stat re-téléchargée.

Lancement :  python tools/explore_serve_return.py
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

from sofa_client import B, H  # noqa: E402
from explore_breaks import _load_cache, collect_events, collect_players  # noqa: E402

MIN_HISTORY = 6          # nb de matchs d'historique requis pour évaluer un joueur


def dominance(hold: float, brk: float) -> float:
    """Score de domination = tenue de service + taux de break (plus haut = plus fort)."""
    return hold + brk


def per_match(st: dict) -> tuple[float, float] | None:
    """(domination_home, domination_away) pour un match, depuis les stats brutes."""
    hg, ag = st.get("hg"), st.get("ag")
    bch, bca = st.get("bch"), st.get("bca")
    rgh, rga = st.get("rgh"), st.get("rga")
    if None in (hg, ag, bch, bca, rgh, rga) or hg <= 0 or ag <= 0 or rgh <= 0 or rga <= 0:
        return None
    hold_h = max(0.0, 1 - bca / hg)      # home a tenu (hg - fois breaké) / hg
    hold_a = max(0.0, 1 - bch / ag)
    brk_h = bch / rgh                    # home a breaké / jeux de retour home
    brk_a = bca / rga
    return dominance(hold_h, brk_h), dominance(hold_a, brk_a)


def walk_forward(events, cache):
    """Renvoie (samples, n). samples : (dom_diff_passé, home_won 0/1)."""
    events.sort(key=lambda ev: ev.get("startTimestamp") or 0)
    hist: dict[int, list[float]] = {}
    samples = []
    for ev in events:
        st = cache.get(str(ev.get("id")))
        if not st:
            continue
        doms = per_match(st)
        if doms is None:
            continue
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hid, aid = ht.get("id"), at.get("id")
        wc = ev.get("winnerCode")
        if hid is None or aid is None or wc not in (1, 2):
            continue
        ph, pa = hist.get(hid, []), hist.get(aid, [])
        if len(ph) >= MIN_HISTORY and len(pa) >= MIN_HISTORY:
            dom_h_past = sum(ph) / len(ph)
            dom_a_past = sum(pa) / len(pa)
            samples.append((dom_h_past - dom_a_past, 1 if wc == 1 else 0))
        hist.setdefault(hid, []).append(doms[0])
        hist.setdefault(aid, []).append(doms[1])
    return samples


def _sigmoid(z):
    return 1 / (1 + math.exp(-z)) if -35 < z < 35 else (0.0 if z < 0 else 1.0)


def _fit_logistic(samples, iters=60):
    """P(home) = sigmoid(b0 + b1*dom_diff), Newton-Raphson."""
    b0 = b1 = 0.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in samples:
            p = _sigmoid(b0 + b1 * x)
            e = p - y
            w = p * (1 - p)
            g0 += e; g1 += e * x
            h00 += w; h01 += w * x; h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        b0 -= (h11 * g0 - h01 * g1) / det
        b1 -= (-h01 * g0 + h00 * g1) / det
    return b0, b1


def main():
    print("Validation du facteur SERVICE+RETOUR (prédiction du vainqueur).")
    cache = _load_cache()
    have = sum(1 for v in cache.values() if v)
    print(f"  cache de stats : {have} matchs.")
    if have < 200:
        print("  ⚠️ cache vide — lance d'abord tools/explore_breaks.py.")
        return
    print("  Listes d'événements (vainqueur + ids)...")
    with httpx.Client(base_url=B, headers=H) as client:
        players = collect_players(client)
        events = collect_events(client, players)
    print(f"  {len(events)} matchs.")

    samples = walk_forward(events, cache)
    if len(samples) < 100:
        print(f"Trop peu d'échantillons ({len(samples)}).")
        return

    # Calibration : on transforme dom_diff en proba via régression logistique (CV moitié)
    train = [d for i, d in enumerate(samples) if i % 2 == 0]
    test = [d for i, d in enumerate(samples) if i % 2 == 1]
    b0, b1 = _fit_logistic(train)

    correct = brier = ll = 0.0
    for x, y in test:
        p = min(max(_sigmoid(b0 + b1 * x), 1e-6), 1 - 1e-6)
        correct += 1 if (p >= 0.5) == (y == 1) else 0
        brier += (p - y) ** 2
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    n = len(test)
    # corrélation brute dom_diff <-> issue
    mx = sum(x for x, _ in samples) / len(samples)
    my = sum(y for _, y in samples) / len(samples)
    cov = sum((x - mx) * (y - my) for x, y in samples)
    vx = math.sqrt(sum((x - mx) ** 2 for x, _ in samples))
    vy = math.sqrt(sum((y - my) ** 2 for _, y in samples))
    corr = cov / (vx * vy) if vx and vy else 0.0

    print("\n=== Résultat (service+retour seul, walk-forward) ===")
    print(f"  échantillons (test) : {n}")
    print(f"  précision           : {correct / n:.1%}")
    print(f"  Brier               : {brier / n:.4f}")
    print(f"  log-loss            : {ll / n:.4f}")
    print(f"  corrélation dom/issue: {corr:.3f}")
    print(f"  coeffs              : b0={b0:.3f} b1={b1:.3f}")
    print()
    print("  Repères : classement seul ~64% / Brier 0.226 ; Elo seul ~61% / 0.233.")
    if correct / n > 0.60 and corr > 0.20:
        print(">>> FACTEUR UTILE : à intégrer au modèle vainqueur (poids modéré).")
    elif correct / n > 0.56:
        print(">>> Apport modeste : intégrable à faible poids, en complément.")
    else:
        print(">>> Peu prédictif seul : ne pas remplacer le facteur surface actuel.")


if __name__ == "__main__":
    main()
