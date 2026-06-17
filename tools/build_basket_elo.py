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

from app.elo_math import expected, mov_multiplier, regress_to_mean

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
     "Origin": "https://www.sofascore.com"}
B = "https://api.sofascore.com/api/v1"
WNBA_TID = 486
NBA_TID = 132
LEAGUES = {"NBA": NBA_TID, "WNBA": WNBA_TID}  # un seul fichier : ids d'équipe uniques
BASE, K = 1500.0, 24.0
HOME_ADV = 65.0           # avantage du terrain, intégré DÈS l'apprentissage (cohérent avec la prédiction)
SEASONS_BACK = 1          # on démarre la saison courante depuis l'Elo de la précédente (régressé)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, "data", "basket_elo.json")


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


def collect_finished(c, tid: int, season_index: int = 0) -> list[dict]:
    """Matchs terminés d'une saison (0 = courante, 1 = précédente), ordre chronologique."""
    seasons = (_get(c, f"/unique-tournament/{tid}/seasons") or {}).get("seasons", [])
    if len(seasons) <= season_index:
        return []
    sid = seasons[season_index]["id"]
    events: dict[int, dict] = {}
    for page in range(20):
        data = _get(c, f"/unique-tournament/{tid}/season/{sid}/events/last/{page}")
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


def _play(games: list[dict], league: str, store: dict) -> None:
    """Déroule l'Elo sur une liste de matchs (avantage terrain + marge de victoire)."""
    for ev in games:
        ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hid, aid = ht.get("id"), at.get("id")
        if hid is None or aid is None:
            continue
        hk, ak = str(hid), str(aid)
        h = store.setdefault(hk, {"name": ht.get("name", ""), "league": league, "elo": BASE, "n": 0})
        a = store.setdefault(ak, {"name": at.get("name", ""), "league": league, "elo": BASE, "n": 0})
        h["name"], a["name"] = ht.get("name", h["name"]), at.get("name", a["name"])
        sh = 1.0 if ev.get("winnerCode") == 1 else 0.0
        eh = expected(h["elo"] + HOME_ADV, a["elo"])      # avantage terrain DANS l'apprentissage
        margin = _margin(ev)
        if margin is not None and margin != 0:
            # écart Elo du point de vue du GAGNANT (avantage terrain inclus)
            elo_diff = (h["elo"] + HOME_ADV - a["elo"]) if sh == 1.0 else (a["elo"] - h["elo"] - HOME_ADV)
            mult = mov_multiplier(abs(margin), elo_diff)
        else:
            mult = 1.0
        delta = K * mult * (sh - eh)
        h["elo"] += delta
        a["elo"] -= delta                                  # somme nulle
        h["n"] += 1
        a["n"] += 1


def build_league(c, league: str, tid: int, store: dict) -> int:
    """Saison précédente -> régression vers 1500 -> saison courante. Retourne le nb de matchs."""
    prev = collect_finished(c, tid, season_index=SEASONS_BACK) if SEASONS_BACK else []
    cur = collect_finished(c, tid, season_index=0)
    _play(prev, league, store)
    if prev:                                               # nouvelle saison : effectifs renouvelés
        for r in store.values():
            if r.get("league") == league:
                r["elo"] = regress_to_mean(r["elo"])
    _play(cur, league, store)
    print(f"  {league}: {len(prev)} (saison N-1) + {len(cur)} (courante) matchs.")
    return len(prev) + len(cur)


def main():
    print("Construction de l'Elo d'équipe NBA + WNBA (marge de victoire + régression saison)...")
    store: dict = {}
    with httpx.Client(base_url=B, headers=H) as c:
        for league, tid in LEAGUES.items():
            build_league(c, league, tid, store)

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
    print(f"\nOK {len(store)} équipes notées -> {OUT_PATH}")
    for league in LEAGUES:
        top = sorted((r for r in store.values() if r.get("league") == league),
                     key=lambda r: r["elo"], reverse=True)
        print(f"\nClassement Elo {league} :")
        for r in top:
            print(f"  {r['elo']:6.0f}  (n={r['n']:2d})  {r['name']}")


if __name__ == "__main__":
    main()
