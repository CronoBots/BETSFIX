"""COLLECTEUR de snapshots LIVE (API-Football) — construit le jeu de données pour un futur DÉTECTEUR LIVE.

La donnée in-play est PÉRISSABLE : API-Football ne garde pas l'historique seconde-par-seconde des cotes/stats
in-play. Sans capture forward, on n'aura JAMAIS de quoi backtester un détecteur live. Ce collecteur (lecture
SEULE, best-effort) capte périodiquement, pour les matchs live des ligues majeures : score + minute + stats live
+ cotes in-play (feed AGRÉGÉ `/odds/live` — pas de sharp par book en live). Append JSONL `data/live_snapshots/<date>.jsonl`.

⚠️ Ne touche RIEN d'autre, ne publie rien. But = accumuler des données pour `docs/LIVE_DETECTOR.md`.
Usage : python tools/live_snapshot_collector.py [--once] [--minutes N] [--interval SECONDES] [--all-leagues]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import apifootball as AF      # noqa: E402

# Ligues « qui comptent » (bonne couverture data + celles que BETSFIX joue) — borne le quota.
MAJOR = {39, 140, 135, 78, 61, 2, 3, 848, 88, 94, 144, 71, 128, 253, 262, 39,
         140, 179, 180, 197, 203, 218, 235, 106, 119, 103, 113, 40, 41, 62, 79, 136}
OUT_DIR = os.path.join("data", "live_snapshots")
KEEP_STATS = {"expected_goals", "Total Shots", "Shots on Goal", "Shots off Goal", "Blocked Shots",
              "Ball Possession", "Corner Kicks", "Dangerous Attacks", "Attacks", "Goalkeeper Saves",
              "Yellow Cards", "Red Cards"}


def _snap(cl, all_leagues: bool) -> int:
    """Un passage : capte tous les matchs live pertinents. Renvoie le nb de snapshots écrits."""
    try:
        live = AF._get(cl, "/fixtures", live="all").get("response", [])
    except Exception:
        return 0
    if not live:
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    day = (live[0]["fixture"]["date"] or "")[:10]
    path = os.path.join(OUT_DIR, f"{day}.jsonl")
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for x in live:
            lg = x["league"]["id"]
            if not all_leagues and lg not in MAJOR:
                continue
            fid = x["fixture"]["id"]
            rec = {"ts": x["fixture"]["timestamp"], "captured": int(time.time()),
                   "fid": fid, "league_id": lg, "league": x["league"]["name"],
                   "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"],
                   "minute": x["fixture"]["status"].get("elapsed"), "status": x["fixture"]["status"]["short"],
                   "score": {"h": x["goals"]["home"], "a": x["goals"]["away"]},
                   "ht": x.get("score", {}).get("halftime")}
            # stats live (compactées aux types utiles)
            try:
                st = AF._get(cl, "/fixtures/statistics", fixture=fid).get("response", [])
                stats = {}
                for team in st:
                    side = "h" if team["team"]["id"] == x["teams"]["home"]["id"] else "a"
                    stats[side] = {s["type"]: s["value"] for s in team.get("statistics", [])
                                   if s["type"] in KEEP_STATS}
                rec["stats"] = stats
            except Exception:
                rec["stats"] = {}
            # cotes in-play (feed agrégé) — marchés clés seulement pour limiter la taille
            try:
                ol = AF._get(cl, "/odds/live", fixture=fid).get("response", [])
                if ol:
                    b0 = ol[0]
                    rec["odds_status"] = b0.get("status")        # stopped/blocked/finished -> bettable ?
                    keep = {}
                    for m in (b0.get("odds") or []):
                        if m.get("id") in (59, 25, 36, 21, 33, 5):   # Fulltime Result, Match Goals, O/U, handicaps, BTTS
                            keep[m.get("name")] = [{"v": v.get("value"), "o": v.get("odd"),
                                                    "h": v.get("handicap")} for v in (m.get("values") or [])]
                    rec["odds"] = keep
            except Exception:
                rec["odds"] = {}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="un seul passage puis stop")
    ap.add_argument("--minutes", type=int, default=420, help="durée totale (défaut 7h)")
    ap.add_argument("--interval", type=int, default=180, help="secondes entre passages (défaut 180)")
    ap.add_argument("--all-leagues", action="store_true", help="toutes ligues (sinon MAJOR seulement)")
    args = ap.parse_args()
    if not AF.configured():
        print("✗ clé API-Football absente."); return 2

    if args.once:
        with AF._client() as cl:
            n = _snap(cl, args.all_leagues)
        print(f"snapshot: {n} match(s) live capté(s) -> {OUT_DIR}/")
        return 0

    deadline = time.time() + args.minutes * 60
    total = passes = 0
    while time.time() < deadline:
        with AF._client() as cl:
            n = _snap(cl, args.all_leagues)
        total += n; passes += 1
        print(f"[{time.strftime('%H:%M:%S')}] passe {passes}: {n} match(s) live (cumul {total})", flush=True)
        if time.time() + args.interval >= deadline:
            break
        time.sleep(args.interval)
    print(f"FIN — {passes} passes, {total} snapshots cumulés dans {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
