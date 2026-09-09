"""AUDIT de règlement via API-Football — vérifie les SCORES de tous les matchs réglés existants.

API-Football = source AUTORITATIVE (score temps réglementaire `score.fulltime` + détail par période).
`/fixtures?date` renvoie DÉJÀ le score de chaque match → 1 appel audite une journée entière (très économe).

Pour chaque match FOOT réglé de BETSFIX (avec `result.score`), compare au score API-Football et signale les
ÉCARTS, en indiquant l'ENJEU : pari joué (stat_bet) / jambe de combiné / abstention (affichage seul).
Cas vécu : FC Bruges-Aston Villa 5-3 (FotMob corrompu) vs 2-3 (réel) — jambe de combiné.

LECTURE SEULE par défaut. `--fix` corrige UNIQUEMENT le score d'affichage des ABSTENTIONS (aucun ROI en jeu) ;
les écarts sur pari joué / jambe combiné sont SIGNALÉS (correction manuelle prudente : ROI/stats en jeu).

Usage : python tools/apifootball_score_audit.py [--from 2026-08-01] [--fix]
"""
from __future__ import annotations

import argparse
import collections
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import analyses as A          # noqa: E402
from app import apifootball as AF      # noqa: E402
from app import combo_daily as CD      # noqa: E402


def _combo_leg_mids() -> dict:
    """{mid -> (variant, day, sel, result)} pour toute jambe de combiné (jour + soir)."""
    out = {}
    for variant in ("", "soir"):
        try:
            d = CD._load("foot", variant)
        except Exception:
            continue
        for day, cb in (d or {}).items():
            for lg in (cb.get("legs") or []):
                mid = str(lg.get("mid") or "")
                if mid:
                    out[mid] = (variant or "jour", day, lg.get("sel"), lg.get("result"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-08-01", help="date min (YYYY-MM-DD)")
    ap.add_argument("--fix", action="store_true", help="corrige le score d'affichage des ABSTENTIONS")
    args = ap.parse_args()
    if not AF.configured():
        print("✗ clé API-Football absente."); return 2

    leg_mids = _combo_leg_mids()

    # 1) matchs foot réglés avec score, groupés par date
    by_day = collections.defaultdict(list)
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        day = (d.get("start") or "")[:10]
        if not day or day < args.frm:
            continue
        r = d.get("result") or {}
        if A.is_settled(d) and r.get("score"):
            by_day[day].append((p, d))

    total = sum(len(v) for v in by_day.values())
    print(f"═══ AUDIT SCORES vs API-Football — {total} matchs réglés depuis {args.frm} ═══")

    n = miss = diff = fixed = 0
    diffs = []
    with AF._client() as cl:
        for day in sorted(by_day):
            fx = AF._get(cl, "/fixtures", date=day).get("response", [])
            afx = []
            for x in fx:
                # SCORE FINAL affiché = `goals` (INCLUT la prolongation pour un AET/PEN) — c'est ce que BETSFIX
                # affiche aussi (« 3-2 » / « 1-2 (a.p.) »). Comparer à `score.fulltime` (90 min) créait des FAUX
                # positifs sur les matchs de coupe allés en prolongation.
                gl = x.get("goals") or {}
                afx.append({"nh": AF._norm(x["teams"]["home"]["name"]), "na": AF._norm(x["teams"]["away"]["name"]),
                            "ts": AF._ts(x["fixture"]["date"]), "final": gl, "status": x["fixture"]["status"]["short"]})
            for p, d in by_day[day]:
                n += 1
                bts = AF._ts(d.get("start")); nh, na = AF._norm(d.get("home")), AF._norm(d.get("away"))
                best, bs = None, 0.0
                for a in afx:
                    if bts and a["ts"] and abs(bts - a["ts"]) > 5400:
                        continue
                    s = (AF._ov(nh, a["nh"]) + AF._ov(na, a["na"])) / 2
                    if s > bs:
                        bs, best = s, a
                if not best or bs < 0.6 or best["final"].get("home") is None:
                    miss += 1                     # seuil 0.6 : évite les homonymes (CSKA Sofia vs CSKA 1948…)
                    continue
                af = f"{best['final']['home']}-{best['final']['away']}"
                bf = str(d["result"]["score"]).split(" (")[0].strip()   # retire « (a.p.) » / suffixes
                if af == bf:
                    continue
                diff += 1
                mid = str(d.get("id"))
                played = bool(d.get("stat_bet"))
                in_combo = mid in leg_mids
                enjeu = "⚠ PARI JOUÉ" if played else ("⚠ JAMBE COMBINÉ" if in_combo else "abstention")
                diffs.append((d.get("name"), mid, bf, af, enjeu, in_combo and leg_mids[mid]))
                # correction auto : SEULEMENT l'affichage d'une abstention hors combiné (aucun ROI)
                if args.fix and not played and not in_combo:
                    h, a2 = best["final"]["home"], best["final"]["away"]   # score FINAL (cohérent avec `af`)
                    d["result"]["score"] = af
                    d["result"]["raw"] = {**(d["result"].get("raw") or {}), "home": h, "away": a2,
                                          "label": af, "src": "apifootball(audit)"}
                    tmp = p + ".tmp"; json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False); os.replace(tmp, p)
                    fixed += 1

    print(f"\nAudité {n} · non résolus API-Foot {miss} · ÉCARTS {diff}" + (f" · corrigés (abstentions) {fixed}" if args.fix else ""))
    if diffs:
        print("\n=== ÉCARTS DÉTECTÉS ===")
        for name, mid, bf, af, enjeu, leg in diffs:
            extra = f" · jambe: {leg[2]} = {leg[3]}" if leg else ""
            print(f"  {str(name)[:34]:34} BETSFIX {bf:5} vs API-Foot {af:5}  [{enjeu}] id {mid}{extra}")
        print("\n⚠ Les écarts « PARI JOUÉ » / « JAMBE COMBINÉ » ne sont PAS auto-corrigés (ROI/stats en jeu) :")
        print("  vérifier si le résultat du pari/de la jambe BASCULE avec le vrai score, puis corriger à la main.")
    else:
        print("  ✅ Aucun écart — tous les scores réglés concordent avec API-Football.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
