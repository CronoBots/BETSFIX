"""VALUE SCREENER v1 — crible EV LARGE (lecture seule, SANS LLM). Cf. docs/VALUE_SCREENER.md.

Étage 1 de l'entonnoir : pour TOUS les matchs du jour d'API-Football ayant une ancre Pinnacle + des cotes
Unibet, calcule l'EV par marché (`proba_sharp_dé-viggée × cote_Unibet − 1`) et flague les candidats value/
confiance. AUCUNE publication, aucun Claude, aucun sidecar touché. But : CHIFFRER combien de candidats existent
sur tout le programme vs notre petit slate scanné — le levier de couverture (docs/VALUE_SCREENER.md).

⚠️ Un candidat ici = « à investiguer » (proba = ancre sharp, pas encore l'analyse Claude), PAS un pari validé.

Usage : python tools/value_screener.py [--date 2026-09-09] [--max 80] [--all-status]
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import apifootball as AF      # noqa: E402

EV_MIN = 0.05
# marchés cœur criblés (mêmes codes que sharp_map/unibet_omap)
_CORE_PREFIX = ("1X2 ", "DC ", "OVER ", "UNDER ", "TEAMTOT ")


def _screen_fixture(odds: dict) -> tuple[list, list]:
    """Renvoie (value_candidats, conf_candidats) pour un match, depuis sharp (Pinnacle) ∩ omap (Unibet)."""
    sharp = AF.sharp_map(odds)              # {code: proba dé-viggée}
    omap = AF.unibet_omap(odds)             # {code: cote Unibet}
    val, conf = [], []
    for code, prob in sharp.items():
        if not any(code.startswith(p) for p in _CORE_PREFIX):
            continue
        cote = omap.get(code)
        if not cote or not prob:
            continue
        ev = prob * cote - 1.0
        # VALUE : Unibet bat le sharp (EV+), cote value, proba pas un longshot
        if ev >= EV_MIN and 1.40 <= cote <= 2.60 and prob >= 0.50:
            val.append((code, round(prob * 100), cote, round(ev * 100)))
        # CONFIANCE-type : favori sûr (sharp ≥80%) à cote courte 1.12-1.50
        if prob >= 0.80 and 1.12 <= cote <= 1.50:
            conf.append((code, round(prob * 100), cote))
    val.sort(key=lambda x: -x[3])           # meilleur EV d'abord
    conf.sort(key=lambda x: -x[1])
    return val, conf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--max", type=int, default=80, help="borne le nb de matchs sondés (quota)")
    ap.add_argument("--all-status", action="store_true", help="tous les matchs (sinon à venir seulement)")
    args = ap.parse_args()
    if not AF.configured():
        print("✗ clé API-Football absente."); return 2

    cl = AF._client()
    fx = AF._get(cl, "/fixtures", date=args.date).get("response", [])
    if not args.all_status:
        fx = [x for x in fx if x["fixture"]["status"]["short"] in ("NS", "TBD")]
    total = len(fx)
    print(f"═══ VALUE SCREENER v1 (EV sharp×Unibet, lecture seule) — {args.date} ═══")
    print(f"Matchs du jour {'à venir' if not args.all_status else '(tous)'} : {total} · sondage borné à {args.max}\n")

    screened = with_odds = 0
    val_matches = conf_matches = 0
    findings = []
    for x in fx[:args.max]:
        fid = x["fixture"]["id"]
        try:
            odds = AF.raw_odds(cl, fid)
        except Exception:
            continue
        screened += 1
        if AF.BK_PINNACLE not in odds or AF.BK_UNIBET not in odds:
            continue
        with_odds += 1
        val, conf = _screen_fixture(odds)
        if val or conf:
            nm = f"{x['teams']['home']['name']} - {x['teams']['away']['name']}"
            lg = x["league"]["name"]
            findings.append((nm, lg, val, conf))
            if val:
                val_matches += 1
            if conf:
                conf_matches += 1
    cl.close()

    print(f"Sondés : {screened} · avec Pinnacle+Unibet : {with_odds}")
    print(f"→ matchs avec un candidat VALUE (EV≥+{int(EV_MIN*100)}%) : {val_matches}")
    print(f"→ matchs avec un candidat CONFIANCE (sharp≥80%, cote 1.12-1.50) : {conf_matches}\n")
    for nm, lg, val, conf in findings[:25]:
        print(f"⚽ {nm[:38]:38} ({lg[:22]})")
        for code, p, c, ev in val[:2]:
            print(f"     💎 VALUE  {code:20} sharp {p}% · cote {c} · EV +{ev}%")
        for code, p, c in conf[:1]:
            print(f"     🎯 CONF   {code:20} sharp {p}% · cote {c}")
    if len(findings) > 25:
        print(f"  … +{len(findings)-25} autres matchs avec candidat(s)")
    print(f"\n⚠️ Candidats = à INVESTIGUER (proba = ancre sharp, pas l'analyse Claude). Étage 2 (Claude) confirmerait.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
