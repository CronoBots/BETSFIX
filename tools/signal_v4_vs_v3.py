# -*- coding: utf-8 -*-
"""COMPARAISON v4 vs v3 des SIGNAUX LIVE — le « rythme propre » de v4 a-t-il amélioré le modèle ? (LECTURE SEULE)

⚠️ PIÈGE ÉVITÉ (rigueur) : v4 (depuis 2026-09-17) ne diffère de v3 QUE sur les **marchés comptés**
(corners/cartons/tirs/fautes : projection bayésienne du restant `analyses._blend_count_rate90` au lieu du
taux-ligue fixe). Sur tous les autres marchés (buts/résultat/DC/handicap/BTTS), **v4 ≡ v3 par construction**.
Or les snapshots v3 et v4 sont DISJOINTS dans le temps (mv figé au moment du snap) → comparer « v4 vs v3 en
global » confondrait l'effet MODÈLE avec l'effet PÉRIODE (deux slates différents, deux tirages de variance).

Design honnête = DIFFERENCE-IN-DIFFERENCES :
  • marchés COMPTÉS  (corners/cartons/tirs) = là où v4 a réellement changé  → Δ = effet MODÈLE + effet PÉRIODE
  • marchés NON-COMPTÉS (buts/résultat/…)   = modèle identique v3=v4        → Δ = effet PÉRIODE SEUL (témoin)
  → effet v4 attribuable au MODÈLE ≈ Δ(comptés) − Δ(non-comptés).

Unité statistique = signaux DISTINCTS (1 par match×sélection = 1re détection), snapshots d'un même match
étant corrélés. Métriques par (version × classe de marché) : n · réussite · ROI · GAP de calibration
(proba modèle − réel) · CLV (dérive 1re détection → dernier snap).

⛔ N'applique/ne change RIEN : livre un verdict chiffré pour DÉCIDER (garder v4 / revenir v3 / neutre).
Usage :  python tools/signal_v4_vs_v3.py
"""
import io
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import live_pick  # noqa: E402

# Familles « comptées » = celles que le blend v4 modifie (corners/cartons/tirs/fautes/hors-jeu/arrêts).
_COUNTED_RE = re.compile(r"corner|carton|tir|faute|foul|hors|offside|arret|arrêt|save", re.I)


def _is_counted(fam: str) -> bool:
    return bool(_COUNTED_RE.search(fam or ""))


def _roi(rows):
    """rows = [{result, odds}]. ROI = mise plate 1u ; réussite sur les décidés."""
    n = len(rows)
    w = sum(1 for s in rows if s["result"] == "won")
    dec = sum(1 for s in rows if s["result"] in ("won", "lost"))
    ret = sum((s["odds"] - 1.0) if s["result"] == "won"
              else (-1.0 if s["result"] == "lost" else 0.0) for s in rows)
    return {"n": n, "dec": dec, "w": w,
            "wr": round(100.0 * w / dec, 1) if dec else 0.0,
            "roi": round(100.0 * ret / n, 1) if n else 0.0,
            "cote": round(sum(s["odds"] for s in rows) / n, 2) if n else 0.0}


def _gap(rows):
    """GAP de calibration = proba MODÈLE moyenne − réussite RÉELLE (pts). >0 = surconfiance."""
    dec = [s for s in rows if s["result"] in ("won", "lost")]
    if not dec:
        return None
    modp = 100.0 * sum(s["prob"] for s in dec) / len(dec)
    realp = 100.0 * sum(1 for s in dec if s["result"] == "won") / len(dec)
    return round(modp - realp, 1)


def collect():
    """Signaux DISTINCTS (1/match×sel) + CLV, groupés par version de modèle et classe de marché.
    Renvoie : dist[(ver, klass)] -> [rows], clv[(ver, klass)] -> [dérives], matches[(ver, klass)] -> set(mid)."""
    dist = defaultdict(list)
    clv = defaultdict(list)
    matches = defaultdict(set)
    for rec in live_pick._iter_records():
        mid = rec.get("id") or rec.get("mid") or id(rec)
        # regrouper les snaps réglés par (mv, sel) -> 1re détection = signal distinct
        by_ver_sel = {}
        allsnaps_by_sel = defaultdict(list)
        for s in rec.get("snaps", []):
            mv = s.get("mv", 1)
            ver = "v4" if mv >= 4 else "v3" if mv == 3 else "vieux"
            if ver == "vieux":
                continue
            sel = s.get("sel")
            allsnaps_by_sel[(ver, sel)].append(s)
            if s.get("result") in ("won", "lost", "push"):
                by_ver_sel.setdefault((ver, sel), s)   # 1re détection réglée
        for (ver, sel), s in by_ver_sel.items():
            klass = "comptés" if _is_counted(s.get("family", "")) else "non-comptés"
            dist[(ver, klass)].append({"result": s["result"], "odds": s.get("odds") or 0.0,
                                       "prob": s.get("prob", 0.0), "family": s.get("family", "?")})
            matches[(ver, klass)].add(mid)
        # CLV : 1re détection -> dernier snap du même (ver, sel)
        for (ver, sel), ss in allsnaps_by_sel.items():
            ss = sorted(ss, key=lambda x: x.get("minute", 0))
            if len(ss) < 2:
                continue
            o0, o1 = ss[0].get("odds"), ss[-1].get("odds")
            if o0 and o1:
                klass = "comptés" if _is_counted(ss[0].get("family", "")) else "non-comptés"
                clv[(ver, klass)].append((o1 - o0) / o0)
    return dist, clv, matches


def _clv_mean(ds):
    return round(100.0 * sum(ds) / len(ds), 1) if ds else None


def _fam_family(rows_by_fam, ver, fam):
    return rows_by_fam.get((ver, fam), [])


def main() -> int:
    import statistics as st
    dist, clv, matches = collect()

    print("╔══ SIGNAUX LIVE — v4 vs v3 (difference-in-differences) ══╗")
    print("  v4 = v3 + rythme propre au match sur les MARCHÉS COMPTÉS (corners/cartons/tirs). Ailleurs v4≡v3.")
    print("  Les marchés NON-COMPTÉS servent de TÉMOIN (tout écart y = période, pas modèle).\n")

    # ── table principale : (version × classe) ──
    print("── PAR VERSION × CLASSE DE MARCHÉ (signaux distincts) ──")
    print(f"  {'ver':4} {'classe':12} {'matchs':>6} {'n':>5} {'réussite':>8} {'ROI':>8} {'GAP calib':>10} {'CLV':>8}")
    cell = {}
    for ver in ("v3", "v4"):
        for klass in ("comptés", "non-comptés"):
            rows = dist.get((ver, klass), [])
            r = _roi(rows)
            g = _gap(rows)
            c = _clv_mean(clv.get((ver, klass), []))
            cell[(ver, klass)] = {"r": r, "gap": g, "clv": c, "m": len(matches.get((ver, klass), set()))}
            gs = f"{g:+.1f}" if g is not None else "  —"
            cs = f"{c:+.1f}%" if c is not None else "   —"
            print(f"  {ver:4} {klass:12} {cell[(ver,klass)]['m']:>6} {r['n']:>5} "
                  f"{r['wr']:>7.1f}% {r['roi']:>7.1f}% {gs:>10} {cs:>8}")

    # ── détail par famille comptée (là où v4 agit) ──
    print("\n── DÉTAIL des MARCHÉS COMPTÉS par famille (v3 vs v4) ──")
    fam_rows = defaultdict(list)
    for (ver, klass), rows in dist.items():
        if klass != "comptés":
            continue
        for row in rows:
            fam_rows[(ver, row["family"])].append(row)
    fams = sorted({f for (_, f) in fam_rows})
    print(f"  {'famille':16} {'n v3':>5} {'WR v3':>6} {'ROI v3':>7} {'GAP v3':>7}   "
          f"{'n v4':>5} {'WR v4':>6} {'ROI v4':>7} {'GAP v4':>7}")
    for fam in fams:
        r3, r4 = _roi(fam_rows.get(("v3", fam), [])), _roi(fam_rows.get(("v4", fam), []))
        g3, g4 = _gap(fam_rows.get(("v3", fam), [])), _gap(fam_rows.get(("v4", fam), []))
        g3s = f"{g3:+.1f}" if g3 is not None else "  —"
        g4s = f"{g4:+.1f}" if g4 is not None else "  —"
        print(f"  {fam:16} {r3['n']:>5} {r3['wr']:>5.0f}% {r3['roi']:>6.1f}% {g3s:>7}   "
              f"{r4['n']:>5} {r4['wr']:>5.0f}% {r4['roi']:>6.1f}% {g4s:>7}")

    # ── DIFFERENCE-IN-DIFFERENCES ──
    print("\n═══ DIFFERENCE-IN-DIFFERENCES (isole l'effet MODÈLE de v4) ═══")

    def _delta(metric):
        """Δ v4−v3 sur une métrique, par classe. metric(cellROI/gap)."""
        out = {}
        for klass in ("comptés", "non-comptés"):
            a, b = cell.get(("v4", klass)), cell.get(("v3", klass))
            va, vb = metric(a), metric(b)
            out[klass] = (va - vb) if (va is not None and vb is not None) else None
        return out

    for label, metric, unit in (
        ("GAP de calibration (proba−réel ; ↓ = mieux)", lambda c: c["gap"], "pts"),
        ("ROI (mise plate ; ↑ = mieux)", lambda c: c["r"]["roi"], "%"),
    ):
        d = _delta(metric)
        dc, dn = d["comptés"], d["non-comptés"]
        print(f"\n  {label}")
        print(f"    Δ comptés    (v4−v3) = {('%+.1f' % dc) if dc is not None else '—'} {unit}")
        print(f"    Δ non-comptés(v4−v3) = {('%+.1f' % dn) if dn is not None else '—'} {unit}  (témoin période)")
        if dc is not None and dn is not None:
            did = dc - dn
            print(f"    → DiD (effet MODÈLE v4) = {did:+.1f} {unit}")

    # ── verdict ──
    gc = cell.get(("v4", "comptés"), {}).get("gap")
    g3c = cell.get(("v3", "comptés"), {}).get("gap")
    nc4 = cell.get(("v4", "comptés"), {}).get("r", {}).get("n", 0)
    nc3 = cell.get(("v3", "comptés"), {}).get("r", {}).get("n", 0)
    print("\n═══ LECTURE ═══")
    print(f"  Échantillon comptés : v3 n={nc3} · v4 n={nc4} (signaux distincts).")
    print("  Le DiD > 0 sur le GAP = v4 a AGGRAVÉ la surconfiance sur les comptés vs le simple effet période ;")
    print("  DiD < 0 = v4 a AMÉLIORÉ la calibration des comptés. Idem ROI (DiD > 0 = v4 a aidé le ROI).")
    print("  ⛔ Ne rien changer sur ce seul run : c'est un descriptif. Croiser avec un train/test par match si le DiD est net.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
