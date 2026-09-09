"""SHADOW d'ENRICHISSEMENT — API-Football vs scraping (FotMob/Flashscore/Sportradar/Understat).

But : PROUVER, en lecture seule, qu'API-Football produit un enrichissement ÉQUIVALENT (forme, xG, blessés,
H2H, prédiction, classement) à la pile de scraping actuelle, AVANT de brancher quoi que ce soit dans le scan
(qui alimente l'analyse du phare). Ne touche AUCUN sidecar.

Pour chaque match foot analysé d'un jour :
  - assemble le bloc de faits API-Football (`_af_enrich_facts`) ;
  - lance le vrai `sources.extras` (scraping) sur le MÊME match ;
  - écrit les deux CÔTE À CÔTE dans `data/apifootball_shadow/enrich/<date>/foot_<mid>.txt` ;
  - imprime une synthèse (nb de faits par source, couverture des blocs).

Usage : python tools/apifootball_enrich_shadow.py [--date 2026-09-09] [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx                                   # noqa: E402
from app import analyses as A                  # noqa: E402
from app import apifootball as AF              # noqa: E402
from app import sources as S                   # noqa: E402

OUT_ROOT = os.path.join("data", "apifootball_shadow", "enrich")


def _af_enrich_facts(cl, d: dict) -> tuple[list[str], dict]:
    """Faits API-Football du match — DÉLÈGUE à `apifootball.enrich_facts` (source unique, partagée avec la prod)."""
    return AF.enrich_facts(cl, d.get("home"), d.get("away"), d.get("start"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="jour sportif (défaut = aujourd'hui)")
    ap.add_argument("--limit", type=int, default=0, help="limiter à N matchs")
    args = ap.parse_args()
    if not AF.configured():
        print("✗ clé API-Football absente (.env)."); return 2

    # matchs foot du jour visé (par jour sportif, comme l'app)
    day = args.date
    picks = []
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        sd = (d.get("start") or "")[:10]       # jour = préfixe date ISO (suffisant pour un shadow)
        if not sd:
            continue
        if day and sd != day:
            continue
        picks.append((sd, p, d))
    if not day and picks:
        day = max(sd for sd, _, _ in picks)
        picks = [x for x in picks if x[0] == day]
    picks.sort(key=lambda x: (x[2].get("start") or ""))
    if args.limit:
        picks = picks[:args.limit]
    if not picks:
        print(f"Aucun match foot pour le jour {day or '(auto)'}."); return 0

    out_dir = os.path.join(OUT_ROOT, day)
    os.makedirs(out_dir, exist_ok=True)
    print(f"═══ SHADOW ENRICHISSEMENT — {len(picks)} matchs · jour {day} ═══\n")

    async def run():
        summary = []
        async with httpx.AsyncClient(timeout=30) as hc:
            with AF._client() as cl:
                for _, p, d in picks:
                    mid = str(d.get("id"))
                    mt = {"home": d.get("home"), "away": d.get("away"),
                          "comp": d.get("comp") or "", "start": d.get("start")}
                    # API-Football (sync) dans un thread pour ne pas bloquer la boucle
                    af_facts, cov = await asyncio.to_thread(_af_enrich_facts, cl, d)
                    # scraping réel (le producteur actuel)
                    prov: dict = {}
                    try:
                        scr_block = await S.extras(hc, "foot", mt, prov)
                    except Exception as e:
                        scr_block = f"(échec scraping: {e})"
                    scr_lines = [ln[2:] for ln in (scr_block or "").splitlines() if ln.startswith("- ")]
                    blocs = [k for k, v in cov.items() if v and k != "resolve"]
                    txt = (f"# {d.get('name')}  ({d.get('comp')})\n"
                           f"# jour {day} · fixture résolu: {cov['resolve']} · blocs API-Foot: {', '.join(blocs) or '—'}\n\n"
                           f"═══ API-FOOTBALL ({len(af_facts)} faits) ═══\n- " + "\n- ".join(af_facts or ["(rien)"]) +
                           f"\n\n═══ SCRAPING actuel ({len(scr_lines)} faits · sources={','.join(sorted(prov)) or '—'}) ═══\n"
                           + (scr_block or "(rien)"))
                    open(os.path.join(out_dir, f"foot_{mid}.txt"), "w", encoding="utf-8").write(txt)
                    print(f"  {str(d.get('name'))[:34]:34} API-Foot {len(af_facts):2} faits [{', '.join(blocs) or '—'}]"
                          f"  ·  scraping {len(scr_lines):2} faits [{','.join(sorted(prov)) or '—'}]")
                    summary.append((cov, len(af_facts), len(scr_lines)))
        # bilan couverture par bloc
        n = len(summary)
        print(f"\n=== COUVERTURE API-FOOTBALL sur {n} matchs ===")
        for k in ("resolve", "referee", "form", "xg", "injuries_covered", "injuries",
                  "h2h", "predictions", "standings"):
            c = sum(1 for cov, _, _ in summary if cov.get(k))
            print(f"  {k:12} {c}/{n}")
        print(f"\nÉcrit dans {out_dir}/")
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
