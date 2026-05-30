"""Backtest EXPLORATOIRE : la tendance d'aces d'un joueur est-elle exploitable ?

Avant de construire un marché « aces » et de te faire parier dessus, on répond à la
seule question qui compte : **la tendance passée d'un joueur prédit-elle ses aces
futurs, mieux qu'une moyenne globale ?** Si oui, le signal existe et on continue ; si
non, le book a raison et on s'arrête là.

Méthode (walk-forward, sans fuite) :
  1. on collecte un échantillon de matchs (ATP+WTA) et, pour chacun, les stats réelles
     (aces + jeux de service par joueur) — mises en cache sur disque ;
  2. on parcourt les matchs dans l'ordre. Pour chaque joueur ayant assez d'historique,
     on PRÉDIT son taux d'aces (aces / jeu de service) par sa moyenne passée, puis on
     compare au taux RÉEL du match ;
  3. on mesure l'erreur (MAE) du modèle « joueur » vs une baseline « moyenne globale »,
     et la corrélation entre taux passé et taux réalisé.

Le taux par jeu de service est indépendant de la durée du match (un match en 5 sets a
mécaniquement plus d'aces) — c'est la vraie tendance individuelle.

Lancement :  python tools/explore_aces.py
"""

from __future__ import annotations

import json
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

from build_elo import _get, _is_singles, B, H, TOURNAMENTS  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_ROOT, "data", "cache_aces.json")

MAX_PLAYERS = 70         # garde-fou sur le volume réseau (échantillon exploratoire)
PLAYER_PAGES = 3
SEED_SEASONS = 3
MIN_HISTORY = 4          # nb de matchs d'historique requis pour prédire un joueur


def _to_int(v):
    """Parse '5' ou '104/166 (63%)' -> premier entier, sinon None."""
    if v is None:
        return None
    try:
        return int(str(v).split("/")[0].split()[0].strip())
    except (ValueError, IndexError):
        return None


def parse_stats(js) -> tuple[int, int, int, int] | None:
    """(aces_home, aces_away, jeux_service_home, jeux_service_away) ou None."""
    try:
        period = js["statistics"][0]
    except (KeyError, IndexError, TypeError):
        return None
    h_a = a_a = h_g = a_g = None
    for g in period.get("groups", []):
        for it in g.get("statisticsItems", []):
            n = (it.get("name") or "").lower()
            if n == "aces":
                h_a, a_a = _to_int(it.get("home")), _to_int(it.get("away"))
            elif n == "service games played":
                h_g, a_g = _to_int(it.get("home")), _to_int(it.get("away"))
    if None in (h_a, a_a, h_g, a_g) or h_g <= 0 or a_g <= 0:
        return None
    return h_a, a_a, h_g, a_g


# ----------------------------------------------------------------- collecte
def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE_PATH)


def collect_players(client) -> list[int]:
    ids, seen = [], set()
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


def collect_events(client, players) -> list[dict]:
    events: dict[int, dict] = {}
    for pid in players:
        for page in range(PLAYER_PAGES):
            data = _get(client, f"/team/{pid}/events/last/{page}")
            if not data:
                break
            for ev in data.get("events", []) or []:
                eid = ev.get("id")
                st = (ev.get("status") or {}).get("type")
                if eid and eid not in events and st == "finished" and _is_singles(ev):
                    events[eid] = ev
            if not data.get("hasNextPage"):
                break
    return list(events.values())


def gather(client, events, cache) -> list[dict]:
    """Pour chaque match, récupère aces+jeux (cache disque). Renvoie les samples utiles."""
    events.sort(key=lambda ev: ev.get("startTimestamp") or 0)
    rows, fetched = [], 0
    for i, ev in enumerate(events):
        eid = str(ev.get("id"))
        if eid in cache:
            st = cache[eid]
        else:
            js = _get(client, f"/event/{eid}/statistics")
            parsed = parse_stats(js) if js else None
            st = None
            if parsed:
                h_a, a_a, h_g, a_g = parsed
                st = {"ha": h_a, "aa": a_a, "hg": h_g, "ag": a_g}
            cache[eid] = st or {}            # mémorise même l'absence (évite de refetch)
            fetched += 1
            if fetched % 50 == 0:
                print(f"  ...{fetched} stats récupérées ({i}/{len(events)} matchs)")
                _save_cache(cache)
        if not st:
            continue
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        if ht.get("id") and at.get("id"):
            rows.append({
                "hid": ht["id"], "aid": at["id"],
                "ha": st["ha"], "aa": st["aa"], "hg": st["hg"], "ag": st["ag"],
                "ts": ev.get("startTimestamp") or 0,
            })
    _save_cache(cache)
    return rows


# ----------------------------------------------------------------- analyse
def walk_forward(rows):
    """Renvoie (samples, taux_global). Chaque sample : (taux_predit_joueur, taux_reel)."""
    rows.sort(key=lambda r: r["ts"])
    hist: dict[int, list[float]] = {}        # player_id -> [taux passés]
    samples = []                             # (pred_player_rate, actual_rate)
    glob_aces = glob_games = 0
    for r in rows:
        for pid, aces, games in ((r["hid"], r["ha"], r["hg"]),
                                 (r["aid"], r["aa"], r["ag"])):
            actual = aces / games
            past = hist.get(pid, [])
            if len(past) >= MIN_HISTORY:
                samples.append((sum(past) / len(past), actual))
            hist.setdefault(pid, []).append(actual)
        glob_aces += r["ha"] + r["aa"]
        glob_games += r["hg"] + r["ag"]
    glob_rate = glob_aces / glob_games if glob_games else 0.0
    return samples, glob_rate


def _mae(pairs):
    return sum(abs(p - a) for p, a in pairs) / len(pairs)


def _corr(pairs):
    n = len(pairs)
    mp = sum(p for p, _ in pairs) / n
    ma = sum(a for _, a in pairs) / n
    cov = sum((p - mp) * (a - ma) for p, a in pairs)
    vp = math.sqrt(sum((p - mp) ** 2 for p, _ in pairs))
    va = math.sqrt(sum((a - ma) ** 2 for _, a in pairs))
    return cov / (vp * va) if vp and va else 0.0


def main():
    print("Backtest exploratoire : signal 'aces' (tendance joueur).")
    print("Collecte échantillon + stats (cache disque, 1er run un peu long)...\n")
    cache = _load_cache()
    with httpx.Client(base_url=B, headers=H) as client:
        players = collect_players(client)
        print(f"  {len(players)} joueurs.")
        events = collect_events(client, players)
        print(f"  {len(events)} matchs. Récupération des aces...")
        rows = gather(client, events, cache)
    print(f"  {len(rows)} matchs avec stats d'aces exploitables.\n")

    samples, glob = walk_forward(rows)
    if len(samples) < 50:
        print(f"Trop peu d'échantillons ({len(samples)}). Augmente MAX_PLAYERS/PLAYER_PAGES.")
        return

    player_pairs = samples                                  # (taux passé du joueur, réel)
    naive_pairs = [(glob, a) for _, a in samples]           # baseline : moyenne globale

    mae_player = _mae(player_pairs)
    mae_naive = _mae(naive_pairs)
    corr = _corr(player_pairs)
    gain = (mae_naive - mae_player) / mae_naive * 100 if mae_naive else 0

    print("=== Résultat (taux d'aces par jeu de service) ===")
    print(f"  échantillons évalués       : {len(samples)}")
    print(f"  taux d'aces moyen (global) : {glob:.3f} ace / jeu de service")
    print(f"  MAE modèle 'joueur'        : {mae_player:.4f}")
    print(f"  MAE baseline 'moyenne'     : {mae_naive:.4f}")
    print(f"  gain du modèle joueur      : {gain:+.1f}%   (>0 = la tendance individuelle aide)")
    print(f"  corrélation passé/réel     : {corr:.3f}   (proche de 0 = aucun signal)")
    print()
    if gain > 8 and corr > 0.25:
        print(">>> SIGNAL CLAIR : la tendance d'aces d'un joueur est prédictive.")
        print("    -> ça vaut le coup de construire le marché aces (modèle + value Unibet).")
    elif gain > 3 and corr > 0.12:
        print(">>> Signal FAIBLE mais présent. À creuser (ajuster par adversaire/surface)")
        print("    avant d'en faire un marché à parier.")
    else:
        print(">>> PAS de signal exploitable : la moyenne globale prédit aussi bien.")
        print("    -> ne pas construire ce marché ; tester plutôt total jeux / sets.")


if __name__ == "__main__":
    main()
