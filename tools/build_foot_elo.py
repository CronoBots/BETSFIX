"""Construit l'Elo des sélections (foot) -> data/foot_elo.json.

Cible : les équipes de la Coupe du Monde 2026 (SofaScore unique-tournament 16). Comme
le tournoi n'a pas commencé, on construit leur Elo à partir de leur **historique
international récent** (Nations League, Euro, qualifs, amicaux) via /team/{id}/events.

Elo classique d'équipe (base 1500, K=28 — un peu plus réactif, peu de matchs internationaux).
L'avantage du terrain est géré à la prédiction (app/foot.py).

Lancement :  python tools/build_foot_elo.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

import httpx

from app.elo_math import expected, mov_multiplier

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
     "Origin": "https://www.sofascore.com"}
B = "https://api.sofascore.com/api/v1"
WC_TID = 16               # FIFA World Cup
BASE, K = 1500.0, 28.0
HOME_ADV = 35.0           # avantage du terrain, intégré DÈS l'apprentissage (cohérent avec la prédiction)
PLAYER_PAGES = 4          # ~4 pages d'historique par sélection

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, "data", "foot_elo.json")


def _get(c, path):
    try:
        r = c.get(path, timeout=25)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def _margin(ev) -> int | None:
    hs = (ev.get("homeScore") or {}).get("current")
    as_ = (ev.get("awayScore") or {}).get("current")
    return (hs - as_) if isinstance(hs, (int, float)) and isinstance(as_, (int, float)) else None


def wc_team_ids(c) -> list[int]:
    """Ids des sélections de la CdM 2026 (depuis ses matchs à venir + joués)."""
    seasons = (_get(c, f"/unique-tournament/{WC_TID}/seasons") or {}).get("seasons", [])
    if not seasons:
        return []
    sid = seasons[0]["id"]
    ids, seen = [], set()
    for direction in ("next", "last"):
        for page in range(4):
            data = _get(c, f"/unique-tournament/{WC_TID}/season/{sid}/events/{direction}/{page}")
            if not data:
                break
            for ev in data.get("events", []) or []:
                for side in ("homeTeam", "awayTeam"):
                    tid = (ev.get(side) or {}).get("id")
                    if tid and tid not in seen:
                        seen.add(tid)
                        ids.append(tid)
            if not data.get("hasNextPage"):
                break
    return ids


def collect_events(c, team_ids) -> list[dict]:
    """Matchs internationaux terminés (uniques) des sélections ciblées."""
    events: dict[int, dict] = {}
    for i, tid in enumerate(team_ids):
        if i % 12 == 0:
            print(f"  ...équipe {i}/{len(team_ids)} ({len(events)} matchs)")
        for page in range(PLAYER_PAGES):
            data = _get(c, f"/team/{tid}/events/last/{page}")
            if not data:
                break
            for ev in data.get("events", []) or []:
                eid = ev.get("id")
                st = (ev.get("status") or {}).get("type")
                if eid and eid not in events and st == "finished" and ev.get("winnerCode") in (1, 2, 3):
                    events[eid] = ev
            if not data.get("hasNextPage"):
                break
    return list(events.values())


def main():
    print("Construction de l'Elo des sélections (Coupe du Monde 2026)...")
    store: dict = {}
    with httpx.Client(base_url=B, headers=H) as c:
        teams = wc_team_ids(c)
        print(f"  {len(teams)} sélections CdM.")
        events = collect_events(c, teams)
    print(f"  {len(events)} matchs internationaux collectés.")

    events.sort(key=lambda e: e.get("startTimestamp") or 0)
    for ev in events:
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hid, aid = ht.get("id"), at.get("id")
        if hid is None or aid is None:
            continue
        hk, ak = str(hid), str(aid)
        h = store.setdefault(hk, {"name": ht.get("name", ""), "elo": BASE, "n": 0})
        a = store.setdefault(ak, {"name": at.get("name", ""), "elo": BASE, "n": 0})
        h["name"], a["name"] = ht.get("name", h["name"]), at.get("name", a["name"])
        wc = ev.get("winnerCode")
        sh = 1.0 if wc == 1 else (0.0 if wc == 2 else 0.5)   # 3 = match nul
        eh = expected(h["elo"] + HOME_ADV, a["elo"])         # avantage terrain DANS l'apprentissage
        margin = _margin(ev)
        if margin is not None and margin != 0 and sh != 0.5:  # but d'écart sur un résultat décisif
            elo_diff = (h["elo"] + HOME_ADV - a["elo"]) if sh == 1.0 else (a["elo"] - h["elo"] - HOME_ADV)
            mult = mov_multiplier(abs(margin), elo_diff)
        else:
            mult = 1.0                                        # nul : ajustement standard
        delta = K * mult * (sh - eh)
        h["elo"] += delta
        a["elo"] -= delta                                    # somme nulle
        h["n"] += 1
        a["n"] += 1

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    # Garde-fou anti-perte : ne pas ecraser un fichier de notes existant par un
    # resultat VIDE (source indisponible -> 0 collecte). Cf. incident 2026-06-17.
    if not store and os.path.exists(OUT_PATH) and os.path.getsize(OUT_PATH) > 2:
        print(f"  [!] 0 entree collectee -> {OUT_PATH} CONSERVE (pas d'ecrasement)")
        return
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    os.replace(tmp, OUT_PATH)
    print(f"\n✓ {len(store)} équipes notées -> {OUT_PATH}")
    top = sorted([r for r in store.values() if r["n"] >= 5],
                 key=lambda r: r["elo"], reverse=True)[:12]
    print("\nTop 12 Elo sélections :")
    for r in top:
        print(f"  {r['elo']:6.0f}  (n={r['n']:2d})  {r['name']}")


if __name__ == "__main__":
    main()
