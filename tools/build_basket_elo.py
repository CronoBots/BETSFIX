"""Construit l'Elo d'équipe WNBA -> data/basket_elo.json.

Source : historique de la saison WNBA en cours (SofaScore, unique-tournament 486).
On déroule un Elo classique d'équipe (base 1500, K=24) match par match dans l'ordre
chronologique. L'avantage du terrain est géré au moment de la prédiction (app/basket.py),
pas dans la note.

Lancement :  python tools/build_basket_elo.py
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

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
     "Origin": "https://www.sofascore.com"}
B = "https://api.sofascore.com/api/v1"
WNBA_TID = 486
BASE, K = 1500.0, 24.0

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, "data", "basket_elo.json")


def _get(c, path):
    try:
        r = c.get(path, timeout=25)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def _expected(a, b):
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def collect_finished(c) -> list[dict]:
    """Matchs WNBA terminés de la saison en cours, ordre chronologique croissant."""
    seasons = (_get(c, f"/unique-tournament/{WNBA_TID}/seasons") or {}).get("seasons", [])
    if not seasons:
        return []
    sid = seasons[0]["id"]
    events: dict[int, dict] = {}
    for page in range(12):
        data = _get(c, f"/unique-tournament/{WNBA_TID}/season/{sid}/events/last/{page}")
        if not data:
            break
        for ev in data.get("events", []) or []:
            st = (ev.get("status") or {}).get("type")
            if ev.get("id") and st == "finished" and ev.get("winnerCode") in (1, 2):
                events[ev["id"]] = ev
        if not data.get("hasNextPage"):
            break
    out = list(events.values())
    out.sort(key=lambda e: e.get("startTimestamp") or 0)
    return out


def main():
    print("Construction de l'Elo d'équipe WNBA...")
    store: dict = {}
    with httpx.Client(base_url=B, headers=H) as c:
        games = collect_finished(c)
    print(f"  {len(games)} matchs terminés collectés.")

    for ev in games:
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hid, aid = ht.get("id"), at.get("id")
        if hid is None or aid is None:
            continue
        hk, ak = str(hid), str(aid)
        h = store.setdefault(hk, {"name": ht.get("name", ""), "elo": BASE, "n": 0})
        a = store.setdefault(ak, {"name": at.get("name", ""), "elo": BASE, "n": 0})
        h["name"], a["name"] = ht.get("name", h["name"]), at.get("name", a["name"])
        sh = 1.0 if ev.get("winnerCode") == 1 else 0.0
        eh = _expected(h["elo"], a["elo"])
        h["elo"] += K * (sh - eh)
        a["elo"] += K * ((1 - sh) - (1 - eh))
        h["n"] += 1
        a["n"] += 1

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    os.replace(tmp, OUT_PATH)
    print(f"\n✓ {len(store)} équipes notées -> {OUT_PATH}")
    top = sorted(store.values(), key=lambda r: r["elo"], reverse=True)
    print("\nClassement Elo WNBA :")
    for r in top:
        print(f"  {r['elo']:6.0f}  (n={r['n']:2d})  {r['name']}")


if __name__ == "__main__":
    main()
