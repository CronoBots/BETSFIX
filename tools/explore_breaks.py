"""Backtest EXPLORATOIRE : la tendance de BREAK d'un joueur est-elle exploitable ?

Comme pour les aces (tools/explore_aces.py), on mesure d'abord si le signal existe
avant de construire le marché. Nuance importante : un break dépend du **retour** du
joueur ET du **service adverse** (interaction), donc le signal individuel peut être
plus faible que les aces — c'est justement ce qu'on teste.

Métrique : taux de break = balles de break converties / jeux de retour joués
(combien de fois on breake par jeu de retour). Walk-forward, sans fuite.

Bonus : on remplit un cache de stats ENRICHI (data/cache_stats.json : aces, jeux de
service, breaks, jeux de retour, doubles fautes) réutilisable par build_tendencies.

Lancement :  python tools/explore_breaks.py
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

from sofa_client import _get, _is_singles, B, H, TOURNAMENTS  # noqa: E402
from explore_aces import _to_int  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_ROOT, "data", "cache_stats.json")

MAX_PLAYERS = 70
PLAYER_PAGES = 3
SEED_SEASONS = 3
MIN_HISTORY = 4


def parse_rich(js) -> dict | None:
    """Extrait aces, jeux service, breaks convertis, jeux retour, doubles fautes."""
    try:
        period = js["statistics"][0]
    except (KeyError, IndexError, TypeError):
        return None
    g = {"ha": None, "aa": None, "hg": None, "ag": None, "bch": None, "bca": None,
         "rgh": None, "rga": None, "dfh": None, "dfa": None}
    for grp in period.get("groups", []):
        for it in grp.get("statisticsItems", []):
            n = (it.get("name") or "").lower()
            h, a = _to_int(it.get("home")), _to_int(it.get("away"))
            if n == "aces":
                g["ha"], g["aa"] = h, a
            elif n == "service games played":
                g["hg"], g["ag"] = h, a
            elif n == "double faults":
                g["dfh"], g["dfa"] = h, a
            elif n == "break points converted":
                g["bch"], g["bca"] = h, a
            elif n == "return games played":
                g["rgh"], g["rga"] = h, a
    # Pour l'analyse des breaks, on exige breaks convertis + jeux de retour.
    if None in (g["bch"], g["bca"], g["rgh"], g["rga"]) or g["rgh"] <= 0 or g["rga"] <= 0:
        return None
    return g


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
    events.sort(key=lambda ev: ev.get("startTimestamp") or 0)
    rows, fetched = [], 0
    for i, ev in enumerate(events):
        eid = str(ev.get("id"))
        if eid in cache:
            st = cache[eid]
        else:
            js = _get(client, f"/event/{eid}/statistics")
            st = parse_rich(js) if js else None
            cache[eid] = st or {}
            fetched += 1
            if fetched % 50 == 0:
                print(f"  ...{fetched} stats récupérées ({i}/{len(events)})")
                _save_cache(cache)
        if not st or st.get("rgh") is None:
            continue
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        if ht.get("id") and at.get("id"):
            rows.append({"hid": ht["id"], "aid": at["id"],
                         "bch": st["bch"], "bca": st["bca"],
                         "rgh": st["rgh"], "rga": st["rga"],
                         "ts": ev.get("startTimestamp") or 0})
    _save_cache(cache)
    return rows


def walk_forward(rows):
    rows.sort(key=lambda r: r["ts"])
    hist: dict[int, list[float]] = {}
    samples = []
    g_brk = g_rg = 0
    for r in rows:
        for pid, brk, rg in ((r["hid"], r["bch"], r["rgh"]),
                             (r["aid"], r["bca"], r["rga"])):
            actual = brk / rg
            past = hist.get(pid, [])
            if len(past) >= MIN_HISTORY:
                samples.append((sum(past) / len(past), actual))
            hist.setdefault(pid, []).append(actual)
        g_brk += r["bch"] + r["bca"]
        g_rg += r["rgh"] + r["rga"]
    return samples, (g_brk / g_rg if g_rg else 0.0)


def _mae(p):
    return sum(abs(a - b) for a, b in p) / len(p)


def _corr(p):
    n = len(p)
    mp = sum(a for a, _ in p) / n
    ma = sum(b for _, b in p) / n
    cov = sum((a - mp) * (b - ma) for a, b in p)
    vp = math.sqrt(sum((a - mp) ** 2 for a, _ in p))
    va = math.sqrt(sum((b - ma) ** 2 for _, b in p))
    return cov / (vp * va) if vp and va else 0.0


def main():
    print("Backtest exploratoire : signal 'break' (tendance joueur).")
    print("Cache enrichi (cache_stats.json). 1er run long (re-télécharge les stats).\n")
    cache = _load_cache()
    with httpx.Client(base_url=B, headers=H) as client:
        players = collect_players(client)
        print(f"  {len(players)} joueurs.")
        events = collect_events(client, players)
        print(f"  {len(events)} matchs. Récupération des stats...")
        rows = gather(client, events, cache)
    print(f"  {len(rows)} matchs avec stats de break exploitables.\n")

    samples, glob = walk_forward(rows)
    if len(samples) < 50:
        print(f"Trop peu d'échantillons ({len(samples)}).")
        return
    mae_p = _mae(samples)
    mae_n = _mae([(glob, a) for _, a in samples])
    corr = _corr(samples)
    gain = (mae_n - mae_p) / mae_n * 100 if mae_n else 0
    print("=== Résultat (taux de break par jeu de retour) ===")
    print(f"  échantillons              : {len(samples)}")
    print(f"  taux de break moyen       : {glob:.3f} break / jeu de retour")
    print(f"  MAE modèle 'joueur'       : {mae_p:.4f}")
    print(f"  MAE baseline 'moyenne'    : {mae_n:.4f}")
    print(f"  gain du modèle joueur     : {gain:+.1f}%")
    print(f"  corrélation passé/réel    : {corr:.3f}")
    print()
    if gain > 8 and corr > 0.25:
        print(">>> SIGNAL CLAIR : construire le marché breaks.")
    elif gain > 3 and corr > 0.12:
        print(">>> Signal FAIBLE mais présent. À creuser (interaction retour/service).")
    else:
        print(">>> PAS de signal exploitable : ne pas construire (interaction trop forte).")


if __name__ == "__main__":
    main()
