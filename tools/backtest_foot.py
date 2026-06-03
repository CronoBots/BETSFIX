"""Backtest du modèle foot : Elo seul vs Elo + forme (+ H2H).

But : mesurer SI ajouter la forme récente / les confrontations directes au modèle de
probabilité 1-X-2 AMÉLIORE vraiment la prédiction (log-loss / Brier / précision), avant de
toucher au modèle live. On rejoue l'historique international chronologiquement : pour chaque
match, on prédit AVANT de connaître le résultat (Elo + form/H2H vus jusque-là), puis on met à
jour Elo/forme/H2H. Aucune fuite du futur.

Lancement :  python tools/backtest_foot.py
Les matchs collectés sont mis en cache (data/foot_backtest_events.json) -> reruns instantanés.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from app import foot, sofa_http
from app.elo_math import expected, mov_multiplier

sofa_http.allow_rapid = False   # le backtest (gros volume) n'use PAS le quota RapidAPI -> live only

B = "https://api.sofascore.com/api/v1"
WC_TID = 16
BASE, K, HOME_ADV = 1500.0, 28.0, 35.0
PAGES = 4
MIN_GAMES = 5          # on n'évalue un match que si les 2 équipes ont déjà ≥5 matchs (Elo/forme fiables)
FORM_K = 6             # fenêtre de forme (derniers matchs)
H2H_MAX = 6            # nb de confrontations directes prises en compte

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(_ROOT, "data", "foot_backtest_events.json")


# ----------------------------------------------------------------- collecte (curl_cffi)
async def _get(path):
    try:
        r = await sofa_http.get(B + path)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


PROGRESS = os.path.join(_ROOT, "data", "foot_backtest_progress.json")
PACE = 0.4               # espacement entre requêtes (doux, anti-rate-limit)


async def _get_retry(path, tries=3):
    """GET avec quelques essais + back-off (le rate-limit de volume retombe vite)."""
    for k in range(tries):
        d = await _get(path)
        if d is not None:
            return d
        await asyncio.sleep(1.5 * (k + 1))
    return None


async def collect():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            evs = json.load(f)
        print(f"  (cache) {len(evs)} matchs.")
        return evs
    # REPRENABLE : on stocke {ids, done (team ids traités), events}. Chaque run avance ; quand
    # toutes les équipes sont faites, on fige le cache final. Tolérant au rate-limit intermittent.
    prog = {"ids": [], "done": [], "events": {}}
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding="utf-8") as f:
            prog = json.load(f)
        print(f"  (reprise) {len(prog['done'])}/{len(prog['ids'])} équipes, {len(prog['events'])} matchs.")

    if not prog["ids"]:
        seasons = (await _get_retry(f"/unique-tournament/{WC_TID}/seasons") or {}).get("seasons", [])
        if not seasons:
            print("  ⚠️ SofaScore en rate-limit — réessaie dans ~15 min (la collecte reprendra où elle en est).")
            return []
        sid = seasons[0]["id"]
        seen = []
        for direction in ("next", "last"):
            for page in range(4):
                data = await _get_retry(f"/unique-tournament/{WC_TID}/season/{sid}/events/{direction}/{page}")
                if not data:
                    break
                for ev in data.get("events", []) or []:
                    for side in ("homeTeam", "awayTeam"):
                        tid = (ev.get(side) or {}).get("id")
                        if tid and tid not in seen:
                            seen.append(tid)
                if not data.get("hasNextPage"):
                    break
                await asyncio.sleep(PACE)
        prog["ids"] = seen
        _save(PROGRESS, prog)
        print(f"  {len(seen)} sélections à parcourir.")

    done = set(prog["done"])
    for i, tid in enumerate(prog["ids"]):
        if tid in done:
            continue
        ok = True
        for page in range(PAGES):
            data = await _get_retry(f"/team/{tid}/events/last/{page}")
            if data is None:                # rate-limit -> on arrête, on reprendra cette équipe
                ok = False
                break
            for ev in data.get("events", []) or []:
                eid = ev.get("id")
                st = (ev.get("status") or {}).get("type")
                if eid and st == "finished" and ev.get("winnerCode") in (1, 2, 3):
                    ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
                    prog["events"][str(eid)] = {
                        "id": eid, "t": ev.get("startTimestamp") or 0,
                        "h": ht.get("id"), "a": at.get("id"),
                        "hn": ht.get("name", ""), "an": at.get("name", ""),
                        "wc": ev.get("winnerCode"),
                        "hs": (ev.get("homeScore") or {}).get("current"),
                        "as": (ev.get("awayScore") or {}).get("current"),
                    }
            if not data.get("hasNextPage"):
                break
            await asyncio.sleep(PACE)
        if not ok:
            _save(PROGRESS, prog)
            print(f"  ⏸️ rate-limit à l'équipe {i}/{len(prog['ids'])} — progrès sauvé "
                  f"({len(prog['events'])} matchs). Relance pour continuer.")
            return []
        prog["done"].append(tid)
        if i % 8 == 0:
            _save(PROGRESS, prog)
            print(f"   ...équipe {i}/{len(prog['ids'])} ({len(prog['events'])} matchs)")

    evs = [e for e in prog["events"].values() if e["h"] and e["a"]]
    _save(CACHE, evs)
    print(f"  ✓ {len(evs)} matchs collectés et mis en cache.")
    return evs


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


# ----------------------------------------------------------------- métriques
class Metric:
    def __init__(self):
        self.ll = self.brier = 0.0
        self.correct = self.n = 0

    def add(self, probs, y):
        p = [min(max(x, 1e-9), 1.0) for x in probs]
        self.ll += -math.log(p[y])
        self.brier += sum((p[k] - (1.0 if k == y else 0.0)) ** 2 for k in range(3))
        self.correct += 1 if max(range(3), key=lambda k: p[k]) == y else 0
        self.n += 1

    def row(self, label):
        n = self.n or 1
        return f"  {label:22} log-loss {self.ll/n:.4f} · Brier {self.brier/n:.4f} · précision {100*self.correct/n:4.1f}%"


def _probs(eh, ea, fh=0.0, fa=0.0):
    """Proba 1-X-2 du modèle live avec un éventuel décalage Elo de forme/H2H."""
    return foot.outcome_probs(eh + fh, ea + fa, neutral=False)


# ----------------------------------------------------------------- backtest
def run(events):
    events.sort(key=lambda e: e["t"])
    elo: dict = defaultdict(lambda: BASE)
    games: dict = defaultdict(int)
    last_ppg: dict = defaultdict(lambda: deque(maxlen=FORM_K))   # points (3/1/0) récents par équipe
    h2h: dict = defaultdict(lambda: deque(maxlen=H2H_MAX))       # résultats directs : +1 home, 0 nul, -1 away

    # grilles de poids à tester (0 = baseline). Elo-points par unité de signal.
    FORM_W = [0, 10, 20, 30, 45, 60]     # points Elo par (ppg - 1.5)
    H2H_W = [0, 15, 30, 50]              # points Elo par solde h2h moyen
    m_form = {w: Metric() for w in FORM_W}
    m_h2h = {w: Metric() for w in H2H_W}
    m_combo = Metric()                   # meilleure forme + meilleur h2h (rempli après coup)
    best_fw = best_hw = 0

    # 1re passe : trouver les meilleurs poids forme & h2h séparément
    def features(h, a):
        ppg_h = (sum(last_ppg[h]) / len(last_ppg[h])) if last_ppg[h] else 1.5
        ppg_a = (sum(last_ppg[a]) / len(last_ppg[a])) if last_ppg[a] else 1.5
        duels = h2h[(min(h, a), max(h, a))]
        # solde du point de vue 'home' courant : il faut réorienter selon l'ordre stocké
        bal = 0.0
        if duels:
            bal = sum(duels) / len(duels)
        return ppg_h, ppg_a, bal

    def update(e):
        h, a, wc = e["h"], e["a"], e["wc"]
        sh = 1.0 if wc == 1 else (0.0 if wc == 2 else 0.5)
        eh, ea = elo[h], elo[a]
        eexp = expected(eh + HOME_ADV, ea)
        margin = None
        if isinstance(e["hs"], (int, float)) and isinstance(e["as"], (int, float)):
            margin = e["hs"] - e["as"]
        if margin is not None and margin != 0 and sh != 0.5:
            ediff = (eh + HOME_ADV - ea) if sh == 1.0 else (ea - eh - HOME_ADV)
            mult = mov_multiplier(abs(margin), ediff)
        else:
            mult = 1.0
        delta = K * mult * (sh - eexp)
        elo[h] += delta
        elo[a] -= delta
        games[h] += 1
        games[a] += 1
        last_ppg[h].append(3 if wc == 1 else (1 if wc == 3 else 0))
        last_ppg[a].append(3 if wc == 2 else (1 if wc == 3 else 0))
        key = (min(h, a), max(h, a))
        # +1 si l'équipe 'min(id)' gagne, -1 si elle perd, 0 nul (stockage orienté min-id)
        if wc == 3:
            h2h[key].append(0)
        else:
            winner = h if wc == 1 else a
            h2h[key].append(1 if winner == key[0] else -1)

    for e in events:
        h, a, wc = e["h"], e["a"], e["wc"]
        y = 0 if wc == 1 else (1 if wc == 3 else 2)
        if games[h] >= MIN_GAMES and games[a] >= MIN_GAMES:
            eh, ea = elo[h], elo[a]
            ppg_h, ppg_a, bal_min = features(h, a)
            # solde h2h réorienté vers le 'home' courant
            bal_home = bal_min if h < a else -bal_min
            base = foot.outcome_probs(eh, ea, neutral=False)
            if base:
                for w in FORM_W:
                    p = _probs(eh, ea, w * (ppg_h - 1.5), w * (ppg_a - 1.5))
                    if p:
                        m_form[w].add(p, y)
                for w in H2H_W:
                    p = _probs(eh, ea, w * bal_home, 0.0)
                    if p:
                        m_h2h[w].add(p, y)
        update(e)

    # meilleurs poids (log-loss mini)
    best_fw = min(FORM_W, key=lambda w: m_form[w].ll / (m_form[w].n or 1))
    best_hw = min(H2H_W, key=lambda w: m_h2h[w].ll / (m_h2h[w].n or 1))

    print(f"\n  Matchs évalués : {m_form[0].n}\n")
    print("  === FORME (décalage Elo = W × (ppg_récent − 1.5)) ===")
    for w in FORM_W:
        tag = "  ← meilleur" if w == best_fw else ("  (baseline)" if w == 0 else "")
        print(m_form[w].row(f"W_form={w}") + tag)
    print("\n  === H2H (décalage Elo = W × solde confrontations) ===")
    for w in H2H_W:
        tag = "  ← meilleur" if w == best_hw else ("  (baseline)" if w == 0 else "")
        print(m_h2h[w].row(f"W_h2h={w}") + tag)

    base_ll = m_form[0].ll / (m_form[0].n or 1)
    best_ll = m_form[best_fw].ll / (m_form[best_fw].n or 1)
    gain = (base_ll - best_ll) / base_ll * 100
    print(f"\n  Gain log-loss forme vs baseline : {gain:+.2f}%  "
          f"({'AMÉLIORE' if gain > 0.3 else 'négligeable'})")


def main():
    print("Backtest foot — Elo seul vs Elo + forme/H2H")
    events = asyncio.run(collect())
    if len(events) < 200:
        print(f"\n⚠️ Trop peu de matchs ({len(events)}) — relance quand SofaScore répond.")
        return
    run(events)


if __name__ == "__main__":
    main()
