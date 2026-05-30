"""Back-test COMBINÉ walk-forward : Elo + classement + service/retour.

Les trois facteurs de force du modèle sont désormais calculables hors-ligne. On les
rejoue ensemble (walk-forward, sans fuite) et on cherche la pondération qui minimise
le log-loss — pour confirmer que le modèle complet s'est amélioré et trouver les poids
optimaux entre ces trois facteurs.

Réutilise data/cache_stats.json (service/retour, déjà rempli par explore_breaks) ; ne
re-télécharge que les listes d'événements (Elo + classements + chronologie).

Lancement :  python tools/backtest_combined.py
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

from app import elo, serve_return  # noqa: E402
from app.analysis import prob_from_rankings  # noqa: E402
from build_elo import B, H  # noqa: E402
from explore_breaks import _load_cache, collect_events, collect_players  # noqa: E402
from explore_serve_return import per_match  # noqa: E402

WARMUP_ELO = 10
WARMUP_SR = 6


def replay(events, cache):
    """Walk-forward : renvoie les samples {p_elo, p_rank, p_sr, y}."""
    events.sort(key=lambda ev: ev.get("startTimestamp") or 0)
    estore: dict = {}
    sr_hist: dict[int, list[float]] = {}
    samples = []
    for ev in events:
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hid, aid = ht.get("id"), at.get("id")
        wc = ev.get("winnerCode")
        ground = ev.get("groundType")
        clay = elo.is_clay(ground)
        if hid is None or aid is None or wc not in (1, 2):
            continue
        y = 1 if wc == 1 else 0

        # --- prédictions AVANT mise à jour (hors-échantillon) ---
        eh = elo.surface_rating(estore.get(str(hid)), ground)
        ea = elo.surface_rating(estore.get(str(aid)), ground)
        rh = estore.get(str(hid))
        ra = estore.get(str(aid))
        warm_elo = (rh and (rh.get("overall_n") or 0) >= WARMUP_ELO and
                    ra and (ra.get("overall_n") or 0) >= WARMUP_ELO)
        p_elo = elo.expected_score(eh, ea) if (eh is not None and ea is not None and warm_elo) else None
        p_rank = prob_from_rankings(ht.get("ranking"), at.get("ranking"))

        ph, pa = sr_hist.get(hid, []), sr_hist.get(aid, [])
        p_sr = None
        if len(ph) >= WARMUP_SR and len(pa) >= WARMUP_SR:
            p_sr = serve_return.prob_from_serve_return(sum(ph) / len(ph), sum(pa) / len(pa))

        if p_elo is not None and p_rank is not None and p_sr is not None:
            samples.append({"elo": p_elo, "rank": p_rank, "sr": p_sr, "y": y})

        # --- mises à jour ---
        elo.update_ratings(estore, hid, aid, home_won=(y == 1), on_clay=clay,
                           home_name=ht.get("name", ""), away_name=at.get("name", ""))
        st = cache.get(str(ev.get("id")))
        if st:
            doms = per_match(st)
            if doms is not None:
                sr_hist.setdefault(hid, []).append(doms[0])
                sr_hist.setdefault(aid, []).append(doms[1])
    return samples


def _blend(s, we, wr, ws):
    return we * s["elo"] + wr * s["rank"] + ws * s["sr"]


def log_loss(samples, we, wr, ws):
    t = 0.0
    for s in samples:
        p = min(max(_blend(s, we, wr, ws), 1e-12), 1 - 1e-12)
        t += -(s["y"] * math.log(p) + (1 - s["y"]) * math.log(1 - p))
    return t / len(samples)


def brier(samples, we, wr, ws):
    return sum((_blend(s, we, wr, ws) - s["y"]) ** 2 for s in samples) / len(samples)


def accuracy(samples, we, wr, ws):
    return sum(1 for s in samples if (_blend(s, we, wr, ws) >= 0.5) == (s["y"] == 1)) / len(samples)


def search_weights(samples):
    """Grille sur le simplexe (we+wr+ws=1, pas 0.05). Renvoie (we,wr,ws,ll)."""
    best, best_ll = (0.34, 0.33, 0.33), float("inf")
    for ie in range(0, 21):
        we = ie / 20
        for ir in range(0, 21 - ie):
            wr = ir / 20
            ws = 1 - we - wr
            ll = log_loss(samples, we, wr, ws)
            if ll < best_ll:
                best, best_ll = (we, wr, ws), ll
    return (*best, best_ll)


def main():
    print("Back-test combiné : Elo + classement + service/retour (walk-forward).\n")
    cache = _load_cache()
    if sum(1 for v in cache.values() if v) < 200:
        print("⚠️ cache_stats.json vide — lance d'abord tools/explore_breaks.py.")
        return
    with httpx.Client(base_url=B, headers=H) as client:
        players = collect_players(client)
        events = collect_events(client, players)
    print(f"  {len(events)} matchs collectés.")
    samples = replay(events, cache)
    print(f"  {len(samples)} matchs avec les 3 facteurs disponibles + réchauffés.\n")
    if len(samples) < 200:
        print("Trop peu d'échantillons.")
        return

    # CV moitié alternée (déterministe)
    train = [s for i, s in enumerate(samples) if i % 2 == 0]
    test = [s for i, s in enumerate(samples) if i % 2 == 1]

    print("=== Chaque facteur seul (sur test) ===")
    for name, w in (("Elo", (1, 0, 0)), ("classement", (0, 1, 0)), ("service/retour", (0, 0, 1))):
        print(f"  {name:16} log-loss={log_loss(test, *w):.4f}  Brier={brier(test, *w):.4f}  "
              f"précision={accuracy(test, *w):.1%}")

    # Poids actuels du modèle, normalisés sur ces 3 facteurs (elo .30, classt .35, surf .10)
    cur = (0.30 / 0.75, 0.35 / 0.75, 0.10 / 0.75)
    print("\n=== Pondération ACTUELLE (normalisée sur les 3) ===")
    print(f"  Elo {cur[0]:.2f} / classt {cur[1]:.2f} / serv-ret {cur[2]:.2f}  "
          f"-> log-loss(test)={log_loss(test, *cur):.4f}  précision={accuracy(test, *cur):.1%}")

    we, wr, ws, _ = search_weights(train)
    print("\n=== Pondération OPTIMALE (calée sur train, évaluée sur test) ===")
    print(f"  Elo {we:.2f} / classt {wr:.2f} / serv-ret {ws:.2f}  "
          f"-> log-loss(test)={log_loss(test, we, wr, ws):.4f}  précision={accuracy(test, we, wr, ws):.1%}")

    # Traduction en poids du modèle complet (les 3 occupent 0.75 du total ; forme 0.20 +
    # h2h 0.05 = 0.25 conservés).
    scale = 0.75
    print("\n>>> Poids modèle suggérés (forme 0.20 / h2h 0.05 conservés) :")
    print(f"    elo={we*scale:.2f}  classement={wr*scale:.2f}  surface={ws*scale:.2f}")
    gain = (log_loss(test, *cur) - log_loss(test, we, wr, ws))
    print(f"\n    gain log-loss vs actuel : {gain:+.4f}  "
          f"({'à appliquer' if gain > 0.002 else 'négligeable -> garder l’actuel'})")


if __name__ == "__main__":
    main()
