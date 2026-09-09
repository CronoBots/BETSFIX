"""COLLECTEUR SHADOW API-Football — assemble le bundle de données COMPLET par match, 100 % API-Football.

But : prouver que TOUT ce dont le pipeline BETSFIX a besoin (ancre sharp, cotes Unibet, règlement,
enrichissement) peut être collecté depuis API-Football en UN endroit, SANS toucher la prod. Écrit un
bundle `.af.json` par match dans data/apifootball_shadow/collect/<date>/. C'est la fondation de la
migration : demain `generate_analyses` appellera ça au lieu du scraping.

« Optimiser au max » : on capte aussi ce que le scraping ne donnait PAS (predictions Poisson, blessures
structurées, compos, H2H) → matière à enrichir l'analyse Claude une fois migré.

100 % lecture seule. Clé = env `BETSFIX_APIFOOTBALL_KEY` / `.env`. Économe en quota (fixtures 1 appel,
blessures batchées par `ids`, borné par --limit).

Usage : python tools/apifootball_collect.py [--date 2026-09-09] [--limit 6] [--h2h]
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import analyses as A          # noqa: E402
from app import apifootball as AF      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--limit", type=int, default=6, help="nb de matchs à collecter (quota Free)")
    ap.add_argument("--h2h", action="store_true", help="ajoute le H2H (1 appel/match en plus)")
    args = ap.parse_args()
    day = args.date

    if not AF.configured():
        print("✗ clé API-Football absente (BETSFIX_APIFOOTBALL_KEY / .env)."); return 2

    # 1) matchs BETSFIX analysés du jour
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
        bfx.append(d)
    bfx.sort(key=lambda d: d.get("start") or "")
    print(f"═══ COLLECTE SHADOW API-FOOTBALL — {day} · {len(bfx)} match(s) analysé(s), collecte de {min(args.limit, len(bfx))} ═══")
    if not bfx:
        print("  (rien à collecter)"); return 0

    out_dir = os.path.join(os.path.dirname(A.DIR), "apifootball_shadow", "collect", day)
    os.makedirs(out_dir, exist_ok=True)

    with AF._client() as cl:
        # 2) fixtures du jour (1 appel) -> index local pour matcher
        fx = AF._get(cl, "/fixtures", date=day).get("response", [])
        afx = [{"id": x["fixture"]["id"], "nh": AF._norm(x["teams"]["home"]["name"]),
                "na": AF._norm(x["teams"]["away"]["name"]), "ts": AF._ts(x["fixture"]["date"]),
                "lg": x["league"]["name"], "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"],
                "hid": x["teams"]["home"]["id"], "aid": x["teams"]["away"]["id"]} for x in fx]

        # 3) résoudre chaque match BETSFIX -> fixture (nom + KO ±90 min)
        matched = []
        for d in bfx[:args.limit]:
            bts = AF._ts(d.get("start")); nh, na = AF._norm(d.get("home")), AF._norm(d.get("away"))
            best, bs = None, 0.0
            for a in afx:
                if bts and a["ts"] and abs(bts - a["ts"]) > 90 * 60:
                    continue
                s = (AF._ov(nh, a["nh"]) + AF._ov(na, a["na"])) / 2
                if s > bs:
                    bs, best = s, a
            matched.append((d, best if bs >= 0.5 else None, round(bs, 3)))

        # 4) blessures BATCHÉES (1 appel pour ≤20 fixtures) -> data/apifootball économe
        ids = [str(b["id"]) for _, b, _ in matched if b]
        inj_by_fx: dict = {}
        if ids:
            try:
                for x in AF._get(cl, "/injuries", ids="-".join(ids[:20])).get("response", []):
                    fid = ((x.get("fixture") or {}).get("id"))
                    p = x.get("player") or {}
                    inj_by_fx.setdefault(fid, []).append(
                        {"player": p.get("name"), "team": (x.get("team") or {}).get("name"),
                         "type": p.get("type"), "reason": p.get("reason")})
            except Exception as exc:
                print(f"  (injuries batch ignoré : {exc})")

        # 5) bundle par match
        n_ok = 0
        omap_counts = []
        for d, af, score in matched:
            name = d.get("name")
            if not af:
                print(f"  ✗ {str(name)[:36]:36} — pas de fixture concordant (best {score:.0%})")
                continue
            fid = af["id"]
            try:
                od = AF.raw_odds(cl, fid)
                bundle = {
                    "mid": d.get("id"), "name": name, "start": d.get("start"),
                    "af_fixture": fid, "league": af["lg"], "match_score": score,
                    "sharp_map": AF.sharp_map(od), "omap": AF.unibet_omap(od),
                    "state": AF.match_state(cl, fid),
                    "injuries": inj_by_fx.get(fid, []),
                    "lineups": AF.lineups(cl, fid),
                    "predictions": AF.predictions(cl, fid),
                    "h2h": AF.h2h(cl, af["hid"], af["aid"], last=6) if args.h2h else None,
                    "collected_from": "api-football",
                }
                fp = os.path.join(out_dir, f"foot_{d.get('id')}.af.json")
                json.dump(bundle, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                n_ok += 1
                omap_counts.append(len(bundle["omap"]))
                pr = bundle["predictions"] or {}
                st = bundle["state"] or {}
                print(f"  ✓ {str(name)[:32]:32} omap {len(bundle['omap']):2}c · sharp {len(bundle['sharp_map']):2}c · "
                      f"inj {len(bundle['injuries'])} · compos {len(bundle['lineups'])} · "
                      f"pred {'oui' if pr.get('advice') else '—'} · {st.get('status') or '?'} · {af['lg'][:14]}")
            except Exception as exc:
                print(f"  ✗ {str(name)[:36]:36} — collecte KO : {exc}")

    avg = round(sum(omap_counts) / len(omap_counts), 1) if omap_counts else 0
    print(f"\n── {n_ok}/{len(matched)} bundles écrits dans {out_dir}  (omap moyen {avg} codes) ──")
    print("  Chaque .af.json = ancre + omap + règlement + blessures + compos + predictions (+ H2H) 100 % API-Football.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
