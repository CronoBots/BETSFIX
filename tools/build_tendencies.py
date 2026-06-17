"""Construit les tendances de service par joueur -> data/player_tendencies.json.

Pour l'instant : le **taux d'aces par jeu de service** (global + terre battue), la
tendance individuelle la plus stable et prédictive (cf. tools/explore_aces.py :
corrélation 0.51 passé->futur). Réutilise le cache de stats (data/cache_aces.json)
déjà constitué par explore_aces ; ne re-télécharge que les listes d'événements
(pour relier chaque match aux joueurs + à la surface).

Format : {"<player_id>": {"name", "ace_rate", "ace_games", "ace_rate_clay",
"ace_games_clay"}}. ace_rate = aces / jeux de service (indépendant de la durée).

Lancement :  python tools/build_tendencies.py
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

from explore_aces import collect_events, collect_players, _load_cache  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, "data", "player_tendencies.json")


def main():
    print("Construction des tendances de service (aces) par joueur...")
    cache = _load_cache()
    have = sum(1 for v in cache.values() if v)
    print(f"  cache de stats : {have} matchs avec aces.")
    if have < 100:
        print("  ⚠️ cache d'aces quasi vide — lance d'abord tools/explore_aces.py.")
        return

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
               "Origin": "https://www.sofascore.com"}
    with httpx.Client(base_url="https://api.sofascore.com/api/v1",
                      headers=headers) as client:
        print("  Listes d'événements (clés joueur + surface)...")
        players = collect_players(client)
        events = collect_events(client, players)
    print(f"  {len(events)} matchs reliés.")

    # Agrégation par joueur : somme des aces et des jeux de service (global + terre)
    agg: dict[str, dict] = {}

    def bump(pid, name, aces, games, clay):
        if pid is None:
            return
        d = agg.setdefault(str(pid), {"name": name or "", "a": 0, "g": 0, "ca": 0, "cg": 0})
        if name:
            d["name"] = name
        d["a"] += aces
        d["g"] += games
        if clay:
            d["ca"] += aces
            d["cg"] += games

    used = 0
    for ev in events:
        st = cache.get(str(ev.get("id")))
        if not st:
            continue
        used += 1
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        clay = "clay" in ((ev.get("groundType")) or "").lower()
        bump(ht.get("id"), ht.get("name"), st["ha"], st["hg"], clay)
        bump(at.get("id"), at.get("name"), st["aa"], st["ag"], clay)

    store = {}
    for pid, d in agg.items():
        if d["g"] <= 0:
            continue
        store[pid] = {
            "name": d["name"],
            "ace_rate": round(d["a"] / d["g"], 4),
            "ace_games": d["g"],
            "ace_rate_clay": round(d["ca"] / d["cg"], 4) if d["cg"] > 0 else None,
            "ace_games_clay": d["cg"],
        }

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
    print(f"\n✓ {len(store)} joueurs notés (sur {used} matchs) -> {OUT_PATH}")

    top = sorted(store.items(), key=lambda kv: kv[1]["ace_rate"], reverse=True)[:10]
    print("\nTop 10 serveurs (aces / jeu de service) :")
    for pid, r in top:
        clay = f"{r['ace_rate_clay']:.2f}" if r["ace_rate_clay"] is not None else "  - "
        print(f"  {r['ace_rate']:.2f}  (terre {clay}, n={r['ace_games']:4d} jeux)  {r['name']}")


if __name__ == "__main__":
    main()
