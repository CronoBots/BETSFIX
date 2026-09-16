"""Rapport « SIGNAUX LIVE » PAR MATCH — RUN à la demande (recherche silencieuse, LECTURE SEULE).

⚠️ L'unité statistique honnête = le MATCH (les snapshots d'un même match sont CORRÉLÉS -> pooler 6000 snaps
ment). Ce rapport réduit chaque match à quelques chiffres, puis teste les hypothèses d'optimisation en aveugle.

Ce qu'il sort (sur le modèle COURANT `live_pick.MODEL_VERSION` = v3) :
  1. Table PAR MATCH triée par ROI (+ colonne gap confiance↔réel, total de buts, compétition).
  2. ROI groupé par TOTAL DE BUTS du match (le seul facteur explicatif net trouvé le 2026-09-16).
  3. PAR FAMILLE : ROI (signaux distincts) + CLV (dérive de cote 1re détection -> dernier snap).
  4. Calibration par décile (modèle vs réel) + gap.
  5. VERDICT des 3 hypothèses pré-enregistrées (cf. mémoire live-phantom-track) :
       H1  sur-confiance sur les buts — l'écart modèle↔réel se resserre-t-il ?
       H2  restreindre aux familles RÉSULTAT — le ROI canonique (1/match) repasse-t-il positif ?
       H3  CLV par famille — une famille garde-t-elle un CLV ≥ 0 (condition NÉCESSAIRE d'un edge) ?

⛔ N'APPLIQUE RIEN et ne PROPOSE aucun seuil : livrer une optim reste une décision explicite, et seulement à
~60-80 matchs v3 (sinon on fitte sur du bruit — décision owner 2026-09-15). Le rapport dit juste OÙ on en est.

Usage :  python tools/live_signal_report.py          # rapport complet
         python tools/live_signal_report.py --quiet  # compteur matchs + verdict des 3 hypothèses seulement
"""
import io
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import live_pick  # noqa: E402

MV = live_pick.MODEL_VERSION
CANON_MIN = live_pick.MINUTE_CANON_MIN
RESULT_FAM = {"Vainqueur", "Double chance", "Handicap", "Résultat MT"}
RETRIGGER_LO, RETRIGGER_HI = 60, 80   # fenêtre de re-trigger train/test (mémoire live-phantom-track)


def _roi(rows):
    """rows = liste de dicts {result, odds}. ROI = mise plate 1u."""
    n = len(rows)
    w = sum(1 for s in rows if s["result"] == "won")
    l = sum(1 for s in rows if s["result"] == "lost")
    ret = sum((s["odds"] - 1.0) if s["result"] == "won"
              else (-1.0 if s["result"] == "lost" else 0.0) for s in rows)
    dec = w + l
    return {"n": n, "w": w, "l": l,
            "wr": round(100.0 * w / dec, 0) if dec else 0.0,
            "roi": round(100.0 * ret / n, 1) if n else 0.0,
            "cote": round(sum(s["odds"] for s in rows) / n, 2) if n else 0.0}


def _total_goals(final):
    try:
        a, b = str(final).replace(":", "-").split("-")[:2]
        return int(a) + int(b)
    except Exception:
        return None


def collect():
    """Réduit le store aux structures par match / distinct / canonique, modèle COURANT seulement."""
    matches, distinct, canon, canon_res = [], [], [], []
    clv = defaultdict(list)   # famille -> [dérives]
    for rec in live_pick._iter_records():
        settled = [s for s in rec.get("snaps", [])
                   if s.get("mv", 1) >= MV and s.get("result") in ("won", "lost", "push")]
        if not settled:
            continue
        # signaux DISTINCTS : 1 par (match, sel) = 1re détection
        seen = {}
        for s in settled:
            seen.setdefault(s.get("sel"), s)
        dv = list(seen.values())
        distinct.extend(dv)
        # CLV par famille : odds 1re détection -> dernier snap du MÊME signal
        by_sel = defaultdict(list)
        for s in rec.get("snaps", []):
            if s.get("mv", 1) >= MV:
                by_sel[s.get("sel")].append(s)
        for sel, ss in by_sel.items():
            ss = sorted(ss, key=lambda x: x.get("minute", 0))
            if len(ss) < 2:
                continue
            o0, o1 = ss[0].get("odds"), ss[-1].get("odds")
            if o0 and o1:
                clv[ss[0].get("family", "?")].append((o1 - o0) / o0)
        # pick canonique (1/match) — global + restreint résultat
        cs = sorted((s for s in settled if s.get("minute", 0) >= CANON_MIN),
                    key=lambda s: (s["minute"], s.get("sel", "")))
        if cs:
            canon.append(cs[0])
        csr = [s for s in cs if s.get("family") in RESULT_FAM]
        if csr:
            canon_res.append(csr[0])
        # résumé du match
        dec = [s for s in settled if s["result"] in ("won", "lost")]
        modp = sum(s["prob"] for s in dec) / len(dec) if dec else 0.0
        realp = sum(1 for s in dec if s["result"] == "won") / len(dec) if dec else 0.0
        r = _roi(settled)
        matches.append({"m": f"{(rec.get('home') or '?')[:12]}-{(rec.get('away') or '?')[:12]}",
                        "comp": (rec.get("comp") or "?")[:22], "n": r["n"], "wr": r["wr"],
                        "roi": r["roi"], "tg": _total_goals(rec.get("final")),
                        "gap": round(100.0 * (modp - realp), 0), "fin": rec.get("final") or ""})
    return matches, distinct, canon, canon_res, clv


def _calib(rows):
    b = defaultdict(lambda: [0, 0, 0.0])
    for s in rows:
        if s["result"] not in ("won", "lost"):
            continue
        k = min(9, int(s.get("prob", 0) * 10))
        b[k][0] += 1
        b[k][1] += 1 if s["result"] == "won" else 0
        b[k][2] += s.get("prob", 0.0)
    out = []
    for k in sorted(b):
        n, w, p = b[k]
        model = 100.0 * p / n if n else 0.0
        real = 100.0 * w / n if n else 0.0
        out.append({"band": f"{k*10}-{k*10+10}%", "n": n,
                    "model": round(model, 1), "real": round(real, 1), "gap": round(model - real, 1)})
    return out


def main() -> int:
    quiet = "--quiet" in sys.argv
    import statistics as st
    matches, distinct, canon, canon_res, clv = collect()
    n_matches = len(matches)

    # --- verdict re-trigger ---
    if n_matches < RETRIGGER_LO:
        phase = (f"COLLECTE ({n_matches}/{RETRIGGER_LO} matchs) — ⛔ ne rien coder, laisser tourner "
                 f"(fit à ce stade = sur-ajustement)")
    elif n_matches <= RETRIGGER_HI:
        phase = (f"RE-TRIGGER ATTEINT ({n_matches} matchs, fenêtre {RETRIGGER_LO}-{RETRIGGER_HI}) — "
                 f"refaire le train/test split par match")
    else:
        phase = f"{n_matches} matchs (> {RETRIGGER_HI}) — train/test dû"

    # --- H1 : sur-confiance (gap moyen pondéré sur les déciles 60-90) ---
    cal = _calib(distinct)
    mid = [c for c in cal if c["band"] in ("60-70%", "70-80%", "80-90%")]
    gap_mid = round(sum(c["gap"] * c["n"] for c in mid) / sum(c["n"] for c in mid), 1) if mid else 0.0

    # --- H2 : restreindre aux familles résultat, unité HONNÊTE = canonique 1/match ---
    roi_canon = _roi(canon)
    roi_canon_res = _roi(canon_res)

    # --- H3 : CLV par famille ---
    clv_mean = {f: round(100.0 * st.mean(ds), 1) for f, ds in clv.items() if ds}
    clv_ok = {f: v for f, v in clv_mean.items() if v >= 0}

    if not quiet:
        print(f"╔══ SIGNAUX LIVE — RAPPORT PAR MATCH (modèle v{MV}) ══╗")
        print(f"  {n_matches} matchs réglés · {len(distinct)} signaux distincts · canonique 1/match")
        print(f"  PHASE : {phase}\n")

        print("── PAR MATCH (trié pire → meilleur ROI) ──")
        print(f"  {'match':26s} {'comp':22s} {'n':>4} {'wr':>4} {'roi':>7} {'buts':>4} {'gap':>4}  score")
        for r in sorted(matches, key=lambda x: x["roi"]):
            print(f"  {r['m']:26s} {r['comp']:22s} {r['n']:>4} {r['wr']:>4.0f} {r['roi']:>6.1f}% "
                  f"{str(r['tg']):>4} {r['gap']:>4.0f}  {r['fin']}")

        print("\n── ROI par TOTAL DE BUTS du match ──")
        byg = defaultdict(list)
        for r in matches:
            if r["tg"] is None:
                continue
            band = "0-1 but " if r["tg"] <= 1 else "2-3 buts" if r["tg"] <= 3 else "4+ buts "
            byg[band].append(r["roi"])
        for b in ("0-1 but ", "2-3 buts", "4+ buts "):
            v = byg[b]
            if v:
                print(f"  {b}  {len(v):>2} matchs  ROI moyen {st.mean(v):+6.1f}%")

        print("\n── PAR FAMILLE (signaux distincts) — ROI + CLV ──")
        byf = defaultdict(list)
        for s in distinct:
            byf[s.get("family", "?")].append(s)
        print(f"  {'famille':16s} {'n':>4} {'wr':>4} {'roi':>8} {'CLV':>8}")
        for fam, rows in sorted(byf.items(), key=lambda kv: -len(kv[1])):
            r = _roi(rows)
            c = clv_mean.get(fam)
            cstr = f"{c:+.1f}%" if c is not None else "  —  "
            print(f"  {fam:16s} {r['n']:>4} {r['wr']:>4.0f} {r['roi']:>6.1f}% {cstr:>8}")

        print("\n── CALIBRATION (signaux distincts) ──")
        print(f"  {'bande':10s} {'n':>5} {'modèle':>7} {'réel':>6} {'gap':>6}")
        for c in cal:
            print(f"  {c['band']:10s} {c['n']:>5} {c['model']:>6.1f}% {c['real']:>5.1f}% {c['gap']:>+5.1f}")
        print()

    print("═══ VERDICT DES 3 HYPOTHÈSES ═══")
    print(f"  H1 sur-confiance buts   : gap moyen 60-90% = {gap_mid:+.1f} pts "
          f"({'se resserre' if abs(gap_mid) < 10 else 'toujours FORT'} ; vise ~0)")
    print(f"  H2 restreindre RÉSULTAT : canonique global ROI {roi_canon['roi']:+.1f}% (n={roi_canon['n']}) "
          f"→ RÉSULTAT seul ROI {roi_canon_res['roi']:+.1f}% (n={roi_canon_res['n']}) "
          f"{'✅ positif' if roi_canon_res['roi'] > 0 else '❌ toujours négatif'}")
    if clv_ok:
        print(f"  H3 CLV par famille      : familles à CLV ≥ 0 → "
              + ", ".join(f"{f} {v:+.1f}%" for f, v in sorted(clv_ok.items(), key=lambda kv: -kv[1])))
    else:
        print("  H3 CLV par famille      : ❌ AUCUNE famille à CLV ≥ 0 — mur structurel, edge improbable")

    if n_matches < RETRIGGER_LO:
        print(f"\n  → CONCLUSION : {n_matches}/{RETRIGGER_LO} matchs. Collecte en cours, NE RIEN CODER.")
    else:
        print(f"\n  → CONCLUSION : {n_matches} matchs atteints. Lancer le train/test split par match ; "
              f"livrer une optim SEULEMENT si H1 tient ET (H2 ✅ OU H3 a une famille CLV ≥ 0).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
