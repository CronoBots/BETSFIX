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
import glob
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # racine repo -> import app.*

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


# --- RÉCONCILIATION : matcher les matchs BETSFIX (sidecars) aux fixtures API-Football par nom -------------
# Tokens de bruit à ignorer dans les noms d'équipe (suffixes club + suffixes régionaux BR/etc du scraping).
_STOP = {"fc", "cf", "sc", "ac", "cd", "ca", "afc", "if", "bk", "sk", "club", "de", "do", "da", "the",
         "united", "city", "calcio", "sad", "ii", "b", "u21", "u23", "u20", "u19", "reserve", "reserves",
         "rj", "sp", "mg", "ce", "go", "rs", "pe", "ba", "pr", "ma", "se", "ec", "se", "aa"}


def _norm(s) -> set:
    """Nom d'équipe -> ensemble de tokens significatifs (sans accents, ponctuation, ni tokens de bruit)."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return {t for t in s.split() if t and t not in _STOP}


def _ov(x: set, y: set) -> float:
    return len(x & y) / max(1, min(len(x), len(y))) if x and y else 0.0


def _match_score(h1, a1, h2, a2) -> float:
    """Score de correspondance d'un match (moyenne du recouvrement domicile↔domicile et extérieur↔extérieur)."""
    return (_ov(h1, h2) + _ov(a1, a2)) / 2


def _ts(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def reconcile(cl, day: str, req: dict) -> None:
    """DOUBLE-RUN : chaque match BETSFIX analysé (KO ce jour) est-il retrouvé sur API-Football, avec l'ancre
    Pinnacle + les marchés Unibet ? Répond à « mes picks bougeraient-ils si je migre ? »."""
    from app import analyses as A
    bfx = []
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("start") or "")[:10] != day:
            continue
        if not (d.get("bets") or d.get("shadow") or d.get("stat_bet") or d.get("abstained")):
            continue
        bfx.append({"name": d.get("name") or f"{d.get('home')} - {d.get('away')}",
                    "played": bool(d.get("stat_bet") or d.get("published_bet")),
                    "nh": _norm(d.get("home")), "na": _norm(d.get("away")),
                    "ts": _ts(d.get("start")), "afid": None})
    js = _get(cl, "/fixtures", date=day)
    afx = [{"id": x["fixture"]["id"], "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"],
            "nh": _norm(x["teams"]["home"]["name"]), "na": _norm(x["teams"]["away"]["name"]),
            "ts": _ts(x["fixture"]["date"]), "lg": x["league"]["name"]}
           for x in js.get("response", [])]

    played_total = sum(1 for b in bfx if b["played"])
    print(f"\n═══ RÉCONCILIATION — {len(bfx)} matchs BETSFIX analysés vs {len(afx)} fixtures API-Football ({day}) ═══")
    print("(désambiguïsation par HEURE de coup d'envoi : ±90 min — sinon senior/U19 se confondent)")
    matched = played_matched = 0
    for b in bfx:
        best, bs = None, 0.0
        for a in afx:
            # Gate temporel : même coup d'envoi à ±90 min (évite de matcher un senior sur un U19 du même jour).
            if b["ts"] and a["ts"] and abs(b["ts"] - a["ts"]) > 90 * 60:
                continue
            s = _match_score(b["nh"], b["na"], a["nh"], a["na"])
            if s > bs:
                bs, best = s, a
        tag = "⭐JOUÉ" if b["played"] else "  ·  "
        if best and bs >= 0.5:
            b["afid"] = best["id"]; matched += 1
            played_matched += b["played"]
            print(f"  {tag} ✓ {b['name'][:30]:30} → {best['home']}–{best['away']} · {best['lg'][:18]} [{bs:.0%}]")
        else:
            print(f"  {tag} ✗ {b['name'][:30]:30} → AUCUN fixture concordant (best {bs:.0%})")
    print(f"\nMatchés : {matched}/{len(bfx)}  ·  paris JOUÉS matchés : {played_matched}/{played_total}")

    # Pour les paris JOUÉS matchés : cotes réelles (Pinnacle 1X2 = ancre · marchés Unibet).
    _WANT = {1: "1X2", 12: "DC", 4: "AsianHcp", 9: "HcpRes", 5: "O/U", 8: "BTTS"}
    print("\nCotes réelles des paris JOUÉS (Pinnacle=ancre sharp · Unibet=marchés joués) :")
    any_played = False
    for b in bfx:
        if not (b["played"] and b["afid"]):
            continue
        any_played = True
        oj = _get(cl, "/odds", fixture=b["afid"])
        bks = {bk["id"]: bk for r in oj.get("response", []) for bk in r.get("bookmakers", [])}
        pin_bets = {bt["id"] for bt in bks[BK_PINNACLE]["bets"]} if BK_PINNACLE in bks else set()
        uni_bets = {bt["id"] for bt in bks[BK_UNIBET]["bets"]} if BK_UNIBET in bks else set()
        pin = "1X2 ✓" if 1 in pin_bets else "ABSENTE ❌"
        uni = ", ".join(v for k, v in _WANT.items() if k in uni_bets) or "aucun ❌"
        print(f"  {b['name'][:30]:30}  Pinnacle: {pin}  ·  Unibet: {uni}")
    if not any_played:
        print("  (aucun pari joué matché à ce jour)")


def _bet_values(bks: dict, bk_id: int, bet_id: int) -> dict:
    """{value_label -> cote} pour un (bookmaker, marché) donné, ou {} si absent."""
    bk = bks.get(bk_id)
    if not bk:
        return {}
    for b in bk.get("bets", []):
        if int(b.get("id", -1)) == bet_id:
            return {v["value"]: float(v["odd"]) for v in b.get("values", []) if v.get("odd")}
    return {}


def compare(cl, day: str, limit: int) -> None:
    """VALEURS de cotes : pour les matchs BETSFIX ancrés (sharp_map+omap) du jour, compare la proba Pinnacle
    dé-viggée (API-Football) à `sharp_map` (1X2) et les cotes Unibet à `omap` (1X2 + DC). Répond à « les
    valeurs — donc l'EV/les picks — seraient-elles les mêmes ? ». ⚠️ écarts attendus = LINE MOVEMENT (BETSFIX
    fige au scan ; l'instantané API-Football peut être plus tardif) → capter au MÊME moment aligne tout."""
    from app import analyses as A
    rows = []
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("start") or "")[:10] != day:
            continue
        sm, om = d.get("sharp_map"), d.get("omap")
        if isinstance(sm, dict) and sm and isinstance(om, dict) and om:
            rows.append(d)
    rows.sort(key=lambda d: d.get("start") or "")
    fx = _get(cl, "/fixtures", date=day).get("response", [])
    print(f"\n═══ COMPARAISON DES VALEURS — {min(limit, len(rows))}/{len(rows)} matchs ancrés ({day}) ═══")
    for d in rows[:limit]:
        sm, om = d["sharp_map"], d["omap"]
        bts = _ts(d.get("start")); nh, na = _norm(d.get("home")), _norm(d.get("away"))
        best, bs = None, 0.0
        for x in fx:
            if bts and _ts(x["fixture"]["date"]) and abs(bts - _ts(x["fixture"]["date"])) > 90 * 60:
                continue
            s = _match_score(nh, na, _norm(x["teams"]["home"]["name"]), _norm(x["teams"]["away"]["name"]))
            if s > bs:
                bs, best = s, x
        print(f"\n• {d.get('name')} [{bs:.0%}]")
        if not best or bs < 0.5:
            print("    pas de fixture concordant"); continue
        bks = {int(bk["id"]): bk for r in _get(cl, "/odds", fixture=best["fixture"]["id"]).get("response", [])
               for bk in r.get("bookmakers", [])}
        pin = _bet_values(bks, BK_PINNACLE, 1)            # Pinnacle Match Winner
        if pin and all(k in pin for k in ("Home", "Draw", "Away")):
            inv = {k: 1 / pin[k] for k in ("Home", "Draw", "Away")}; tot = sum(inv.values())
            for code, lab in (("1X2 1", "Home"), ("1X2 X", "Draw"), ("1X2 2", "Away")):
                bf = sm.get(code)
                if bf is not None:
                    af = inv[lab] / tot
                    print(f"    ancre {code:6} BETSFIX {bf*100:4.1f}%  vs API-Foot {af*100:4.1f}%  Δ {(af-bf)*100:+.1f} pt")
        uni, udc = _bet_values(bks, BK_UNIBET, 1), _bet_values(bks, BK_UNIBET, 12)
        for code, lab, src in (("1X2 1", "Home", uni), ("1X2 X", "Draw", uni), ("1X2 2", "Away", uni),
                               ("DC 1X", "Home/Draw", udc), ("DC 12", "Home/Away", udc), ("DC X2", "Draw/Away", udc)):
            bf = om.get(code); af = src.get(lab)
            if bf is not None and af is not None:
                print(f"    cote  {code:6} BETSFIX {bf:5.2f}   vs API-Foot {af:5.2f}   Δ {af-bf:+.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="jour ISO (défaut : aujourd'hui)")
    ap.add_argument("--reconcile", action="store_true",
                    help="DOUBLE-RUN : compare les matchs BETSFIX analysés du jour à API-Football "
                         "(fixture retrouvé + Pinnacle + marchés Unibet). Économe (~1 req/match joué).")
    ap.add_argument("--compare", action="store_true",
                    help="VALEURS : proba Pinnacle dé-viggée vs sharp_map + cotes Unibet vs omap (écarts = "
                         "line movement). ~1 req/match, limité par --limit.")
    ap.add_argument("--limit", type=int, default=3, help="nb de matchs pour --compare (défaut 3, quota Free).")
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

        if args.compare:
            compare(cl, day, args.limit)
        elif args.reconcile:
            reconcile(cl, day, req)
        else:
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
