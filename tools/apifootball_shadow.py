"""Harnais SHADOW API-Football — accumule la preuve jour après jour SANS toucher la prod.

Pour chaque match BETSFIX ancré (sharp_map + omap) d'un jour, refait l'ancre Pinnacle dé-viggée et la carte
Unibet via API-Football, mesure les ÉCARTS vs le scrapé, et persiste un rapport. Objectif : décider la
bascule scraping -> API-Football sur des DONNÉES accumulées, jamais sur un coup d'essai. 100 % lecture seule
(ne modifie AUCUN sidecar, n'influence AUCUNE sélection).

⚠️ TIMING : lancer PEU APRÈS le scan (les cotes doivent être captées au même moment, sinon l'écart d'ancre
= line movement, pas une divergence de source). Idéal : après `scan_daily`/`scan_wave`.
⚠️ Clé = env `BETSFIX_APIFOOTBALL_KEY` (secret, jamais stockée).

Usage :  BETSFIX_APIFOOTBALL_KEY=... python tools/apifootball_shadow.py [--date 2026-09-09]
Sortie :  console + data/apifootball_shadow/<date>.json (1 rapport/jour, ré-écrasé si relancé).
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import analyses as A          # noqa: E402
from app import apifootball as AF      # noqa: E402


def _amax(vals):
    return max((abs(v) for v in vals), default=None)


# Marchés CŒUR = ce que BETSFIX joue réellement (1X2, double chance, totaux principaux). Les lignes profondes
# (Over 6.5, Under 0.5…) sont illiquides/bruitées -> exclues du chiffre « pire écart » qui pilote la décision.
_CORE_CODES = {"1X2 1", "1X2 X", "1X2 2", "DC 1X", "DC 12", "DC X2",
               "OVER 1.5", "UNDER 1.5", "OVER 2.5", "UNDER 2.5", "OVER 3.5", "UNDER 3.5"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    day = args.date

    if not AF.configured():
        print("✗ BETSFIX_APIFOOTBALL_KEY absent (clé secrète non stockée). Abandon.")
        return 2

    # 1) matchs BETSFIX ancrés du jour
    bfx = []
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("start") or "")[:10] != day:
            continue
        sm, om = d.get("sharp_map"), d.get("omap")
        if isinstance(sm, dict) and sm and isinstance(om, dict) and om:
            bfx.append(d)
    print(f"═══ SHADOW API-FOOTBALL — {day} · {len(bfx)} match(s) ancré(s) ═══")
    if not bfx:
        print("  (rien à comparer — slate pas encore analysé ?)")
        return 0

    with AF._client() as cl:
        # 2) fixtures du jour (1 requête) -> index pour matcher en local
        fx = AF._get(cl, "/fixtures", date=day).get("response", [])
        afx = [{"id": x["fixture"]["id"], "nh": AF._norm(x["teams"]["home"]["name"]),
                "na": AF._norm(x["teams"]["away"]["name"]), "ts": AF._ts(x["fixture"]["date"]),
                "lg": x["league"]["name"], "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"]}
               for x in fx]

        reports, sharp_maxes, omap_maxes, unmatched = [], [], [], []
        for d in sorted(bfx, key=lambda d: d.get("start") or ""):
            sm, om = d["sharp_map"], d["omap"]
            bts = AF._ts(d.get("start")); nh, na = AF._norm(d.get("home")), AF._norm(d.get("away"))
            best, bs = None, 0.0
            for a in afx:
                if bts and a["ts"] and abs(bts - a["ts"]) > 90 * 60:
                    continue
                s = (AF._ov(nh, a["nh"]) + AF._ov(na, a["na"])) / 2
                if s > bs:
                    bs, best = s, a
            if not best or bs < 0.5:
                unmatched.append(d.get("name"))
                print(f"  ✗ {str(d.get('name'))[:38]:38} — pas de fixture concordant")
                continue

            od = AF.raw_odds(cl, best["id"])
            af_sm = AF.sharp_map_1x2(od)
            af_om = AF.unibet_omap(od)
            # écarts ancre 1X2 (points de %) et cotes omap (sur codes communs)
            sd = {c: round((af_sm[c] - sm[c]) * 100, 1) for c in ("1X2 1", "1X2 X", "1X2 2")
                  if c in af_sm and c in sm}
            od_ = {c: round(af_om[c] - om[c], 2) for c in om if c in af_om}
            sharp_max = _amax(sd.values())
            omap_core_max = _amax([v for c, v in od_.items() if c in _CORE_CODES])   # marchés joués
            omap_all_max = _amax(od_.values())                                       # + lignes profondes (info)
            if sharp_max is not None:
                sharp_maxes.append(sharp_max)
            if omap_core_max is not None:
                omap_maxes.append(omap_core_max)
            reports.append({"name": d.get("name"), "mid": d.get("id"), "af_id": best["id"],
                            "league": best["lg"], "match_score": round(bs, 3),
                            "sharp_delta_pt": sd, "sharp_max_pt": sharp_max,
                            "omap_delta": od_, "omap_core_max": omap_core_max, "omap_all_max": omap_all_max,
                            "omap_codes": len(od_)})
            print(f"  ✓ {str(d.get('name'))[:34]:34} ancre Δmax {sharp_max if sharp_max is not None else '—'!s:>4} pt"
                  f" · omap cœur Δmax {omap_core_max if omap_core_max is not None else '—'!s:>5}"
                  f" (toutes lignes {omap_all_max}) · {best['lg'][:14]}")

    # 3) synthèse + persistance
    summary = {
        "date": day, "n_anchored": len(bfx), "n_matched": len(reports), "n_unmatched": len(unmatched),
        "sharp_med_pt": round(statistics.median(sharp_maxes), 2) if sharp_maxes else None,
        "sharp_worst_pt": round(max(sharp_maxes), 2) if sharp_maxes else None,
        "omap_core_med": round(statistics.median(omap_maxes), 3) if omap_maxes else None,
        "omap_core_worst": round(max(omap_maxes), 3) if omap_maxes else None,
        "unmatched": unmatched,
    }
    print("\n── Synthèse ──")
    print(f"  matchés : {summary['n_matched']}/{summary['n_anchored']}  (non trouvés : {summary['n_unmatched']})")
    print(f"  ancre 1X2 Δ (pt %)        : médiane {summary['sharp_med_pt']} · pire {summary['sharp_worst_pt']}")
    print(f"  cotes omap CŒUR Δ (joués) : médiane {summary['omap_core_med']} · pire {summary['omap_core_worst']}")
    print("  (Δ ancre élevé = probable line movement si le shadow ne tourne pas juste après le scan)")

    out_dir = os.path.join(os.path.dirname(A.DIR), "apifootball_shadow")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{day}.json")
    try:
        json.dump({"summary": summary, "matches": reports}, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n  rapport écrit : {out}")
    except Exception as exc:
        print(f"  (écriture rapport ignorée : {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
