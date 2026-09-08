"""Sonde JETABLE de couverture API-Football (évaluation migration hors-scraping, user 2026-09-08).

But : mesurer, sur un jour donné, si API-Football couvre le socle BETSFIX (ancre sharp Pinnacle,
cotes Unibet, marchés Confiance/Value/combiné, live pour le règlement) — SANS rien migrer. On tourne
en DOUBLE-RUN à côté du PC pendant quelques jours et on compare à /health/sources + aux picks réels.

⚠️ CLÉ = SECRET : lue dans l'env `BETSFIX_APIFOOTBALL_KEY`, JAMAIS écrite sur disque ni commitée.
Usage :
    set BETSFIX_APIFOOTBALL_KEY=xxated   (PowerShell : $env:BETSFIX_APIFOOTBALL_KEY="xxx")
    python tools/apifootball_probe.py [--date 2026-09-08]

Économe en requêtes (plan Free = 100/j) : imprime le nb de requêtes consommées.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date

import httpx

HOST = "https://v3.football.api-sports.io"
# Bookmakers clés (ids stables, cf. /odds/bookmakers) : Pinnacle = ancre sharp, Unibet = sélection/omap.
BK_PINNACLE, BK_UNIBET = 4, 16
# Marchés BETSFIX (ids /odds/bets) : 1X2, Double chance, handicaps, over/under, BTTS, team totals mi-temps.
BETS = {1: "Match Winner", 12: "Double Chance", 4: "Asian Handicap",
        5: "Goals Over/Under", 8: "Both Teams Score"}

_N = 0            # compteur de requêtes
_THROTTLE = 7.0   # s entre requêtes : le plan Free limite à ~10 req/min (429 sinon)


def _get(client: httpx.Client, path: str, **params):
    """GET throttlé + 1 retry sur 429 (limite par minute du plan Free)."""
    global _N
    for attempt in range(2):
        _N += 1
        r = client.get(f"{HOST}{path}", params=params, timeout=25)
        if r.status_code == 429 and attempt == 0:
            time.sleep(62)                     # fenêtre par minute -> on attend qu'elle se libère
            continue
        r.raise_for_status()
        time.sleep(_THROTTLE)
        return r.json()
    r.raise_for_status()
    return r.json()


def _pages(js) -> int:
    try:
        return int(js.get("paging", {}).get("total") or 0)
    except (ValueError, TypeError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="jour ISO (défaut : aujourd'hui)")
    args = ap.parse_args()

    key = os.environ.get("BETSFIX_APIFOOTBALL_KEY")
    if not key:
        print("✗ BETSFIX_APIFOOTBALL_KEY absent de l'environnement (clé secrète, non stockée). Abandon.")
        return 2
    day = args.date
    headers = {"x-apisports-key": key}

    with httpx.Client(headers=headers) as cl:
        st = _get(cl, "/status").get("response", {})
        sub = st.get("subscription", {}); req = st.get("requests", {})
        print(f"═══ SONDE API-FOOTBALL — {day} ═══")
        print(f"Compte : plan {sub.get('plan')} (actif {sub.get('active')}) · quota {req.get('current')}/{req.get('limit_day')}/j")

        fx = _get(cl, "/fixtures", date=day)
        print(f"\nFixtures ce jour : {fx.get('results')}")

        # Couverture ancre sharp + Unibet, marché par marché (1 requête par (bookmaker,bet), page 1 seule
        # -> on lit paging.total = nb de PAGES de 10 fixtures = proxy du volume couvert).
        print("\nCouverture par marché (≈ nb de fixtures = pages×10) :")
        print(f"  {'marché':22} {'Pinnacle':>10} {'Unibet':>10}")
        for bid, bname in BETS.items():
            p = _pages(_get(cl, "/odds", date=day, bookmaker=BK_PINNACLE, bet=bid))
            u = _pages(_get(cl, "/odds", date=day, bookmaker=BK_UNIBET, bet=bid))
            print(f"  {bname[:22]:22} {p*10:>8}~  {u*10:>8}~")

        live = _get(cl, "/fixtures", live="all")
        print(f"\nLive en cours (règlement) : {live.get('results')} match(s)")

    print(f"\n⚙  {_N} requête(s) consommée(s) · reste ~{max(0, int(req.get('limit_day', 100)) - int(req.get('current', 0)) - _N)} aujourd'hui")
    print("\nRappel : ancre sharp = Pinnacle · sélection/omap = Unibet · xG = NON couvert (gap connu, enrichissement top-5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
