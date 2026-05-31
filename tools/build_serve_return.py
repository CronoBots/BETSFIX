"""Construit les notes de domination SERVICE+RETOUR par joueur -> data/serve_return.json.

Domination = tenue de service (1 - taux de break subi) + taux de break réalisé. Validé
comme prédicteur du vainqueur au niveau de l'Elo (tools/explore_serve_return.py : 61.4%).
Pondéré par récence (les matchs récents pèsent plus), global + terre battue.

Réutilise le cache de stats (data/cache_stats.json, rempli par explore_breaks) ; ne
re-télécharge que les listes d'événements (ids + surface + chronologie).

Format : {"<player_id>": {"name", "dom", "dom_n", "dom_clay", "dom_clay_n"}}.

Lancement :  python tools/build_serve_return.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

import httpx  # noqa: E402

from app import elo  # is_clay  # noqa: E402
from explore_breaks import _load_cache, collect_events, collect_players  # noqa: E402
from explore_serve_return import per_match  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, "data", "serve_return.json")

DECAY = 0.97             # poids de récence : match i (du + récent) pèse 0.97^i


def main():
    print("Construction des notes service+retour par joueur...")
    cache = _load_cache()
    have = sum(1 for v in cache.values() if v)
    print(f"  cache de stats : {have} matchs.")
    if have < 200:
        print("  ⚠️ cache vide — lance d'abord tools/explore_breaks.py.")
        return
    with httpx.Client(base_url="https://api.sofascore.com/api/v1",
                      headers={"User-Agent": "Mozilla/5.0",
                               "Referer": "https://www.sofascore.com/",
                               "Origin": "https://www.sofascore.com"}) as client:
        print("  Listes d'événements...")
        players = collect_players(client)
        events = collect_events(client, players)
    print(f"  {len(events)} matchs reliés.")

    # Récolte par joueur : (timestamp, dominance, taux de break/retour, clay)
    by_player: dict[str, dict] = {}
    tot_br = tot_rg = 0
    for ev in events:
        st = cache.get(str(ev.get("id")))
        if not st:
            continue
        doms = per_match(st)
        if doms is None:
            continue
        clay = elo.is_clay(ev.get("groundType"))
        ts = ev.get("startTimestamp") or 0
        # Taux de break (force de retour) de chaque joueur ce match-là.
        br_h = st["bch"] / st["rgh"] if st.get("rgh") else None
        br_a = st["bca"] / st["rga"] if st.get("rga") else None
        tot_br += (st.get("bch") or 0) + (st.get("bca") or 0)
        tot_rg += (st.get("rgh") or 0) + (st.get("rga") or 0)
        for side, (team_key) in (("home", "homeTeam"), ("away", "awayTeam")):
            t = ev.get(team_key) or {}
            pid = t.get("id")
            if pid is None:
                continue
            rec = by_player.setdefault(str(pid), {"name": t.get("name", ""), "obs": []})
            if t.get("name"):
                rec["name"] = t["name"]
            rec["obs"].append((ts, doms[0] if side == "home" else doms[1], clay,
                               br_h if side == "home" else br_a))
    avg_ret = tot_br / tot_rg if tot_rg else 0.19
    print(f"  taux de break moyen (force de retour) : {avg_ret:.3f}")

    # Moyenne pondérée par récence (global + terre) + force de retour
    store = {}
    for pid, rec in by_player.items():
        obs = sorted(rec["obs"], key=lambda o: o[0], reverse=True)   # + récent d'abord
        ws = wd = cws = cwd = rws = rwd = 0.0
        cn = 0
        for i, (_, dom, clay, br) in enumerate(obs):
            w = DECAY ** i
            ws += w
            wd += w * dom
            if br is not None:
                rws += w
                rwd += w * br
            if clay:
                cws += w
                cwd += w * dom
                cn += 1
        if ws <= 0:
            continue
        store[pid] = {
            "name": rec["name"],
            "dom": round(wd / ws, 4),
            "dom_n": len(obs),
            "dom_clay": round(cwd / cws, 4) if cws > 0 else None,
            "dom_clay_n": cn,
            # Force de retour (taux de break) : sert à ajuster les aces de l'adversaire.
            "ret": round(rwd / rws, 4) if rws > 0 else None,
        }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    os.replace(tmp, OUT_PATH)
    print(f"\n✓ {len(store)} joueurs notés -> {OUT_PATH}")

    top = sorted([s for s in store.values() if s["dom_n"] >= 10],
                 key=lambda s: s["dom"], reverse=True)[:10]
    print("\nTop 10 domination service+retour (tenue + break) :")
    for r in top:
        clay = f"{r['dom_clay']:.2f}" if r["dom_clay"] is not None else "  - "
        print(f"  {r['dom']:.2f}  (terre {clay}, n={r['dom_n']:3d})  {r['name']}")


if __name__ == "__main__":
    main()
