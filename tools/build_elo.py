"""Construit les notes Elo (global + terre battue) -> data/elo_ratings.json.

Pourquoi pas le classement ATP/WTA ? Il retarde et ignore la qualité des
adversaires. L'Elo se construit match par match, pondéré par la force de l'adversaire,
et un Elo *terre battue* distinct capture les spécialistes que le rang brut rate.

Stratégie de collecte (auto-suffisante, centrée sur les joueurs pertinents) :
  1. on récupère les joueurs des dernières éditions de Roland-Garros (ATP+WTA) ;
  2. pour chacun, on récupère son historique de matchs TOUTES surfaces (SofaScore) ;
  3. on dédoublonne, on trie par ordre chronologique, puis on déroule l'Elo.

Lancement :  python tools/build_elo.py
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app import elo

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
     "Origin": "https://www.sofascore.com"}
B = "https://api.sofascore.com/api/v1"
TOURNAMENTS = {"atp": 2480, "wta": 2577}

MAX_PLAYERS = 400        # garde-fou sur le volume de requêtes
PLAYER_PAGES = 4         # ~ 4 pages d'historique par joueur (récents -> plus anciens)
SEED_SEASONS = 4         # éditions de RG d'où l'on tire les joueurs


def _get(client, path):
    try:
        r = client.get(path, timeout=25)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def _is_singles(ev) -> bool:
    """Exclut le double : équipes à deux joueurs ou libellé 'X / Y'."""
    for side in ("homeTeam", "awayTeam"):
        t = ev.get(side) or {}
        if t.get("subTeams") or "/" in (t.get("name") or ""):
            return False
    return True


def collect_player_ids(client) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for tid in TOURNAMENTS.values():
        seasons = (_get(client, f"/unique-tournament/{tid}/seasons") or {}).get("seasons", [])
        for s in seasons[:SEED_SEASONS]:
            for page in range(6):
                data = _get(client, f"/unique-tournament/{tid}/season/{s['id']}/events/last/{page}")
                if not data:
                    break
                for ev in data.get("events", []) or []:
                    if not _is_singles(ev):
                        continue
                    for side in ("homeTeam", "awayTeam"):
                        pid = (ev.get(side) or {}).get("id")
                        if pid and pid not in seen:
                            seen.add(pid)
                            ids.append(pid)
                if not data.get("hasNextPage"):
                    break
    return ids[:MAX_PLAYERS]


def collect_events(client, player_ids) -> list[dict]:
    """Événements terminés uniques (toutes surfaces) des joueurs ciblés."""
    events: dict[int, dict] = {}
    for i, pid in enumerate(player_ids):
        if i % 25 == 0:
            print(f"  ...joueur {i}/{len(player_ids)} ({len(events)} matchs collectés)")
        for page in range(PLAYER_PAGES):
            data = _get(client, f"/team/{pid}/events/last/{page}")
            if not data:
                break
            for ev in data.get("events", []) or []:
                eid = ev.get("id")
                st = (ev.get("status") or {}).get("type")
                wc = ev.get("winnerCode")
                if eid and eid not in events and st == "finished" and wc in (1, 2) \
                        and _is_singles(ev):
                    events[eid] = ev
            if not data.get("hasNextPage"):
                break
    return list(events.values())


def main():
    print("Collecte des notes Elo (peut prendre quelques minutes)...")
    store: dict = {}
    with httpx.Client(base_url=B, headers=H) as client:
        print("1) Joueurs des dernières éditions de Roland-Garros...")
        players = collect_player_ids(client)
        print(f"   {len(players)} joueurs.")
        print("2) Historique de matchs (toutes surfaces)...")
        events = collect_events(client, players)
        print(f"   {len(events)} matchs uniques.")

        # Chronologie croissante : l'Elo se déroule du plus ancien au plus récent.
        events.sort(key=lambda ev: ev.get("startTimestamp") or 0)
        for ev in events:
            ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
            on_clay = elo.is_clay(ev.get("groundType"))
            elo.update_ratings(
                store, ht.get("id"), at.get("id"),
                home_won=(ev.get("winnerCode") == 1), on_clay=on_clay,
                home_name=ht.get("name", ""), away_name=at.get("name", ""),
            )

    # Garde-fou anti-perte : ne pas ecraser elo_ratings.json par un resultat VIDE
    # (SofaScore indisponible -> 0 collecte). Cf. incident 2026-06-17.
    if not store and os.path.exists(elo.RATINGS_PATH) and os.path.getsize(elo.RATINGS_PATH) > 2:
        print(f"  [!] 0 joueur collecte -> {elo.RATINGS_PATH} CONSERVE (pas d'ecrasement)")
        return
    elo.save(store)
    print(f"\n✓ {len(store)} joueurs notés -> {elo.RATINGS_PATH}")
    # Aperçu : top 10 Elo global
    top = sorted(store.items(), key=lambda kv: kv[1]["overall"], reverse=True)[:10]
    print("\nTop 10 Elo global :")
    for pid, r in top:
        print(f"  {r['overall']:6.0f}  (terre {r['clay']:5.0f}, n={r['overall_n']:3d})  {r['name']}")


if __name__ == "__main__":
    main()
