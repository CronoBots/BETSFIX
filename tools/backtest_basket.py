"""Backtest du modèle basket : Elo + avantage terrain (NBA & WNBA).

But : VALIDER la proba de victoire (Brier/log-loss vs hasard 0.25/0.69) et CALIBRER
l'avantage terrain (HOME_ADV) + l'écart-type de marge (sigma), sur l'historique, avant de
toucher au modèle live. Walk-forward chronologique : pour chaque match on prédit AVANT de
connaître le résultat (Elo vu jusque-là), puis on met à jour l'Elo — aucune fuite du futur.
Update Elo IDENTIQUE à tools/build_basket_elo.py (cohérence). Résumable (rate-limit SofaScore).

Lancement :  python tools/backtest_basket.py
Matchs mis en cache (data/basket_backtest_events.json) -> reruns instantanés.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from app import basket, sofa_http
from app.elo_math import expected, mov_multiplier

B = "https://api.sofascore.com/api/v1"
LEAGUES = {132: ("NBA", 12.5), 486: ("WNBA", 11.0)}   # tid -> (nom, sigma live)
BASE, K, HOME_ADV_LIVE = 1500.0, 24.0, 65.0           # mêmes que build_basket_elo.py
PAGES = 8
MIN_GAMES = 8                                          # warm-up Elo avant d'évaluer
HOME_ADV_GRID = [0, 30, 50, 65, 85, 110]               # 65 = valeur live
PACE = 0.4

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(_ROOT, "data", "basket_backtest_events.json")
PROGRESS = os.path.join(_ROOT, "data", "basket_backtest_progress.json")


# ----------------------------------------------------------------- collecte (curl_cffi)
async def _get(path):
    try:
        r = await sofa_http.get(B + path)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def _get_retry(path, tries=3):
    for k in range(tries):
        d = await _get(path)
        if d is not None:
            return d
        await asyncio.sleep(1.5 * (k + 1))
    return None


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


async def collect():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            evs = json.load(f)
        print(f"  (cache) {len(evs)} matchs.")
        return evs
    # REPRENABLE : on garde {events, done(pages traitées)}. Chaque run avance ; rate-limit -> on
    # sauve et on reprend au prochain lancement. Cache final figé quand tout est parcouru.
    prog = {"events": {}, "done": []}
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding="utf-8") as f:
            prog = json.load(f)
        print(f"  (reprise) {len(prog['events'])} matchs, {len(prog['done'])} pages faites.")
    done = set(prog["done"])
    for tid, (lname, _sig) in LEAGUES.items():
        seasons = (await _get_retry(f"/unique-tournament/{tid}/seasons") or {}).get("seasons", [])
        if not seasons:
            _save(PROGRESS, prog)
            print(f"  ⚠️ SofaScore rate-limit ({lname}) — relance dans ~15 min "
                  f"({len(prog['events'])} matchs collectés).")
            return []
        for sid in [s["id"] for s in seasons[:2]]:        # saison courante + précédente (volume)
            for page in range(PAGES):
                tag = f"{tid}/{sid}/{page}"
                if tag in done:
                    continue
                data = await _get_retry(f"/unique-tournament/{tid}/season/{sid}/events/last/{page}")
                if data is None:
                    _save(PROGRESS, prog)
                    print(f"  ⏸️ rate-limit ({lname} p{page}) — progrès sauvé "
                          f"({len(prog['events'])} matchs). Relance pour continuer.")
                    return []
                for ev in data.get("events", []) or []:
                    eid = ev.get("id")
                    st = (ev.get("status") or {}).get("type")
                    hs = (ev.get("homeScore") or {}).get("current")
                    as_ = (ev.get("awayScore") or {}).get("current")
                    if (eid and st == "finished" and ev.get("winnerCode") in (1, 2)
                            and hs is not None and as_ is not None):
                        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
                        prog["events"][str(eid)] = {
                            "id": eid, "t": ev.get("startTimestamp") or 0, "lg": tid,
                            "h": ht.get("id"), "a": at.get("id"),
                            "wc": ev.get("winnerCode"), "hs": hs, "as": as_}
                done.add(tag)
                prog["done"] = list(done)
                if not data.get("hasNextPage"):
                    break
                await asyncio.sleep(PACE)
            _save(PROGRESS, prog)
    evs = [e for e in prog["events"].values() if e["h"] and e["a"]]
    _save(CACHE, evs)
    print(f"  ✓ {len(evs)} matchs collectés et mis en cache.")
    return evs


# ----------------------------------------------------------------- métriques (2 issues)
class Metric:
    def __init__(self):
        self.ll = self.brier = 0.0
        self.correct = self.n = 0

    def add(self, p_home, y):           # y = 0 si le HOME gagne, 1 sinon
        p = min(max(p_home, 1e-9), 1 - 1e-9)
        py = p if y == 0 else 1 - p
        self.ll += -math.log(py)
        self.brier += (p - (1.0 if y == 0 else 0.0)) ** 2
        self.correct += 1 if (p >= 0.5) == (y == 0) else 0
        self.n += 1

    def row(self, label):
        n = self.n or 1
        return (f"  {label:14s} n={self.n:5d}  logloss={self.ll / n:.4f}  "
                f"Brier={self.brier / n:.4f}  precision={self.correct / n * 100:4.1f}%")


def run(events):
    events = sorted(events, key=lambda e: e["t"])
    elo: dict = defaultdict(lambda: BASE)
    games: dict = defaultdict(int)
    m_ha = {ha: Metric() for ha in HOME_ADV_GRID}
    sig_err: dict = defaultdict(lambda: [0.0, 0])   # league -> [somme erreurs², n] pour la marge

    for e in events:
        h, a, wc = e["h"], e["a"], e["wc"]
        y = 0 if wc == 1 else 1
        if games[h] >= MIN_GAMES and games[a] >= MIN_GAMES:
            for ha in HOME_ADV_GRID:
                m_ha[ha].add(expected(elo[h] + ha, elo[a]), y)
            # calibration marge : marge prévue (sigma·invnorm(p)) vs marge réelle, au HOME_ADV live
            p_live = expected(elo[h] + HOME_ADV_LIVE, elo[a])
            sig = LEAGUES.get(e["lg"], ("", 12.5))[1]
            pred_margin = sig * basket._inv_norm(min(max(p_live, 1e-6), 1 - 1e-6))
            err = sig_err[e["lg"]]
            err[0] += (pred_margin - (e["hs"] - e["as"])) ** 2
            err[1] += 1
        # update Elo — IDENTIQUE à build_basket_elo.py
        eh = expected(elo[h] + HOME_ADV_LIVE, elo[a])
        sh = 1.0 if wc == 1 else 0.0
        margin = abs(e["hs"] - e["as"])
        elo_diff = (elo[h] + HOME_ADV_LIVE - elo[a]) if sh == 1.0 else (elo[a] - elo[h] - HOME_ADV_LIVE)
        delta = K * mov_multiplier(margin, elo_diff) * (sh - eh)
        elo[h] += delta
        elo[a] -= delta
        games[h] += 1
        games[a] += 1

    best = min(HOME_ADV_GRID, key=lambda ha: m_ha[ha].ll / (m_ha[ha].n or 1))
    print(f"\n  Matchs évalués : {m_ha[best].n}  (réf. hasard : Brier 0.25 / logloss 0.69)\n")
    print("  === AVANTAGE TERRAIN (Elo) — log-loss mini = mieux ===")
    for ha in HOME_ADV_GRID:
        tag = "  ← meilleur" if ha == best else ("  (live)" if ha == HOME_ADV_LIVE else "")
        print(m_ha[ha].row(f"HOME_ADV={ha}") + tag)
    print(f"\n  Suggestion HOME_ADV : {best} (live actuel : {int(HOME_ADV_LIVE)})")
    print("\n  === CALIBRATION MARGE (sigma·invnorm(p) vs marge réelle) ===")
    for tid, (lname, sig) in LEAGUES.items():
        sse, n = sig_err[tid]
        if n:
            print(f"  {lname:5s}: RMSE marge = {math.sqrt(sse / n):5.2f} pts  "
                  f"(sigma live = {sig}, sur {n} matchs)")


def main():
    print("Backtest basket — Elo + avantage terrain (NBA & WNBA)")
    events = asyncio.run(collect())
    if len(events) < 200:
        print(f"\n⚠️ Trop peu de matchs ({len(events)}) — relance quand SofaScore répond "
              "(la collecte reprend où elle en est).")
        return
    run(events)


if __name__ == "__main__":
    main()
