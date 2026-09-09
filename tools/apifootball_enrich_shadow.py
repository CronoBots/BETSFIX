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
    """Bloc de faits API-Football pour un match (forme/xG/blessés/H2H/prédiction/classement).
    Renvoie (facts, coverage) où coverage = {bloc: bool} pour tracer ce qui a réellement répondu."""
    facts: list[str] = []
    cov = {"resolve": False, "referee": False, "form": False, "xg": False, "injuries": False,
           "injuries_covered": False, "h2h": False, "predictions": False, "standings": False}
    f = AF.resolve_fixture(cl, d.get("home"), d.get("away"), d.get("start"))
    if not f:
        return facts, cov
    cov["resolve"] = True
    fid, lid, season = f["id"], f.get("league_id"), f.get("season")
    th, ta = f.get("home_id"), f.get("away_id")
    hname, aname = d.get("home"), d.get("away")

    if f.get("referee"):
        facts.append(f"Arbitre : {f['referee']} (API-Football)")
        cov["referee"] = True

    # forme + moyennes de buts saison + série + over% (remplace Sportradar/FotMob/Flashscore)
    if lid and season:
        for tid, label in ((th, hname), (ta, aname)):
            st = AF.team_stats(cl, tid, lid, season) if tid else None
            if st and st.get("form"):
                g = st.get("goals") or {}
                gf = ((g.get("for") or {}).get("average") or {}).get("total")
                ga = ((g.get("against") or {}).get("average") or {}).get("total")
                ov = f" · over 2.5 {st['over25']}%" if st.get("over25") is not None else ""
                sk = st.get("streak") or {}
                skf = (f" · série {sk['wins']}V" if sk.get("wins") else
                       f" · série {sk['draws']}N" if sk.get("draws") else "")
                facts.append(f"Forme [{label}] : {st['form'][-6:]} · buts {gf}/match créés, {ga} concédés{ov}{skf} (API-Football)")
                cov["form"] = True

    # xG (remplace Understat)
    for tid, label in ((th, hname), (ta, aname)):
        xg = AF.team_xg_form(cl, tid) if tid else None
        if xg:
            facts.append(f"xG [{label}] (moy. {xg['n']} derniers) : {xg['xg']} créés / {xg['xga']} concédés (API-Football)")
            cov["xg"] = True

    # blessés (remplace FotMob) — MAIS coverage varie par ligue (Belgique/Amérique du Sud = False)
    covg = AF.coverage(cl, lid, season) if lid and season else None
    if covg and covg.get("injuries"):
        cov["injuries_covered"] = True
        inj = AF.injuries(cl, fid)
        if inj:
            names = ", ".join(f"{i.get('player')} ({i.get('reason')})" for i in inj[:8] if i.get("player"))
            if names:
                facts.append(f"Absents/incertains : {names} (API-Football)")
                cov["injuries"] = True

    # H2H (remplace FotMob/Flashscore)
    if th and ta:
        hh = AF.h2h(cl, th, ta, last=5)
        if hh:
            recap = " · ".join(f"{x['score']}" for x in hh)
            facts.append(f"H2H (5 derniers) : {recap} (API-Football)")
            cov["h2h"] = True

    # prédiction Poisson (données NEUVES, pas dans le scraping)
    pr = AF.predictions(cl, fid)
    if pr and (pr.get("percent") or {}).get("home"):
        pc = pr["percent"]
        facts.append(f"Prédiction API-Football (Poisson) : {pc.get('home')} / {pc.get('draw')} / {pc.get('away')} · advice: {pr.get('advice')}")
        cov["predictions"] = True

    # classement (remplace Sportradar/Flashscore)
    if lid and season:
        tbl = AF.standings(cl, lid, season)
        rk = {r["team_id"]: r for r in tbl}
        for tid, label in ((th, hname), (ta, aname)):
            r = rk.get(tid)
            if r:
                facts.append(f"Classement [{label}] : {r['rank']}e, {r['points']} pts, forme {r.get('form')} (API-Football)")
                cov["standings"] = True

    return facts, cov


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
