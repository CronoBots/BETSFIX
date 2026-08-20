# -*- coding: utf-8 -*-
"""LABO — OPTIMISER SÉLECTION & CHOIX DU PARI (isolé, LECTURE SEULE des sidecars, écrit rien).

Répond aux 3 questions :
  (1) ROI par bucket de RANG DE SÉLECTION  -> data indisponible (profondeur `nonLiveBoCount` non stockée) :
      on le signale + proxy CLV.
  (2) DIVERGENCE Unibet vs Pinnacle (edge sharp) -> prédit-elle le ROI ?  (sharp_map, échantillon limité)
  (3) ROI par MARCHÉ × BANDE DE COTE  -> teste le garde-fou global (_recommend : 1.70-2.00 exige 72%, ≥2.00 exclu).
  (+) CLV : distribution, joué vs fantôme.

Complète `claude_edge.py` (qui fait déjà value→ROI/calibration/Brier/marché). NB : les fantômes = TOUTES les
prédictions (non biaisées par la sélection) -> le ROI conditionnel par bucket dit « si on jouait ce bucket ».
Mise plate 1u : ROI = (retour − n)/n. Le ROI est le juge (net de marge), pas le Brier."""
import io, os, sys, glob, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import analyses          # analyses.DIR seulement (chemin) — AUCUNE écriture

# ---- chargement : une ligne par prédiction réglée (fantôme + pari), + sharp/clv au niveau match ----
preds = []   # dict par prédiction
n_sm = n_clv = 0
clv_rows = []   # (clv, is_played, sport)
for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    if d.get("sport") != "foot":
        continue
    sm = d.get("sharp_map") or {}
    clv = d.get("clv")
    depth = d.get("markets")          # profondeur Unibet figée (instrumentation 2026-08-20) — None avant
    sedge = d.get("sel_edge")         # meilleur edge sharp figé au scan (instrumentation) — None avant
    if sm: n_sm += 1
    if isinstance(clv, (int, float)):
        n_clv += 1
        clv_rows.append((clv, bool(d.get("bets")), d.get("sport")))
    def _push(code, cote, prob, res, played):
        if res not in ("won", "lost") or not cote or not prob or cote <= 1:
            return
        code = (code or "?").upper()
        preds.append({"prob": prob / 100.0, "cote": float(cote), "won": res == "won",
                      "code": code, "fam": code.split()[0] if code.split() else code,
                      "played": played, "sharp": sm.get(code),
                      "depth": depth if isinstance(depth, (int, float)) else None,
                      "sedge": sedge if isinstance(sedge, (int, float)) else None})
    for s in (d.get("shadow") or []):
        _push(s.get("code"), s.get("cote"), s.get("prob"), s.get("result"), False)
    for b in (d.get("bets") or []):
        _push(b.get("code"), b.get("odds") or b.get("cote"), b.get("prob"), b.get("result"), True)

N = len(preds)


def roi(sub):
    if not sub:
        return (0, 0.0, 0.0, 0.0)
    ret = sum(x["cote"] for x in sub if x["won"])
    hit = sum(1 for x in sub if x["won"]) / len(sub) * 100
    om = sum(x["cote"] for x in sub) / len(sub)
    return (len(sub), hit, om, (ret - len(sub)) / len(sub) * 100)


def line(label, sub, extra=""):
    n, hit, om, r = roi(sub)
    flag = "  ⚠️faible n" if 0 < n < 40 else ""
    print(f"  {label:>20} | n={n:5d} | réuss {hit:5.1f}% | cote {om:4.2f} | ROI {r:+6.1f}%{flag}{extra}")


print(f"Prédictions foot réglées : {N}  ·  sidecars avec sharp_map : {n_sm}  ·  avec CLV : {n_clv}")
print("=" * 78)

# =========================================================================================
# (3) MARCHÉ × BANDE DE COTE  — le levier « choix du pari » (grand échantillon, robuste)
# =========================================================================================
BANDS = [(1.0, 1.30, "<1.30"), (1.30, 1.50, "1.30-1.50"), (1.50, 1.70, "1.50-1.70"),
         (1.70, 2.00, "1.70-2.00"), (2.00, 2.50, "2.00-2.50"), (2.50, 99, "≥2.50")]


def in_band(x, lo, hi):
    return lo <= x["cote"] < hi


print("\n### (3) ROI PAR BANDE DE COTE — TOUT vs CE QU'ON JOUERAIT (conf≥65% & value>0)")
print("     [le garde-fou _recommend : cote<1.70 libre · 1.70-2.00 exige 72% · ≥2.00 exclu]")
for lo, hi, name in BANDS:
    allb = [x for x in preds if in_band(x, lo, hi)]
    play = [x for x in allb if x["prob"] >= 0.65 and x["prob"] * x["cote"] - 1 > 0]   # ~ éligible _recommend
    na, ha, oa, ra = roi(allb)
    npl, hpl, opl, rpl = roi(play)
    print(f"  {name:>10} | tout n={na:5d} ROI {ra:+6.1f}% | jouable n={npl:4d} réuss {hpl:4.1f}% ROI {rpl:+6.1f}%")

print("\n### (3b) BANDE DE COTE × MARCHÉ (familles clés, sous-ensemble JOUABLE conf≥65 & value>0)")
FAMS = ["DC", "1X2", "WIN", "OVER", "UNDER", "TEAMTOT"]
for fam in FAMS:
    fp = [x for x in preds if x["fam"] == fam]
    if len(fp) < 40:
        continue
    print(f"  — {fam} (n={len(fp)}) —")
    for lo, hi, name in BANDS:
        play = [x for x in fp if in_band(x, lo, hi) and x["prob"] >= 0.65 and x["prob"] * x["cote"] - 1 > 0]
        if play:
            line(name, play)

# =========================================================================================
# (2) DIVERGENCE UNIBET vs PINNACLE (edge sharp = sharp_prob − 1/cote) -> prédit le ROI ?
# =========================================================================================
print("\n### (2) DIVERGENCE UNIBET vs PINNACLE (edge sharp) -> ROI RÉALISÉ")
print("     edge = proba Pinnacle dé-viggée − proba implicite Unibet (1/cote)")
sharp = [x for x in preds if isinstance(x.get("sharp"), (int, float))]
print(f"     prédictions avec ancre sharp appariée : {len(sharp)}")
if sharp:
    for lo, hi, name in [(-9, -0.05, "edge < -5%"), (-0.05, 0.0, "-5%..0"),
                         (0.0, 0.05, "0..+5%"), (0.05, 0.10, "+5..+10%"), (0.10, 9, "> +10%")]:
        sub = [x for x in sharp if lo <= (x["sharp"] - 1.0 / x["cote"]) < hi]
        if sub:
            line(name, sub)
    # corrélation de rang simple : edge sharp vs gain (Spearman approx par signe)
    pos = [x for x in sharp if x["sharp"] - 1.0 / x["cote"] > 0]
    neg = [x for x in sharp if x["sharp"] - 1.0 / x["cote"] <= 0]
    _, _, _, rpos = roi(pos)
    _, _, _, rneg = roi(neg)
    print(f"     -> edge sharp > 0 : ROI {rpos:+.1f}% (n={len(pos)})  vs  edge ≤ 0 : ROI {rneg:+.1f}% (n={len(neg)})")

# =========================================================================================
# (+) CLV — distribution, joué vs fantôme
# =========================================================================================
print("\n### (+) CLV (closing line value) — stocké sur les scans récents")
if clv_rows:
    allc = [c for c, _, _ in clv_rows]
    played = [c for c, pl, _ in clv_rows if pl]
    ghost = [c for c, pl, _ in clv_rows if not pl]
    def stats(v):
        return (len(v), sum(v) / len(v) * 100, sum(1 for x in v if x > 0) / len(v) * 100) if v else (0, 0, 0)
    for lbl, v in (("tous", allc), ("joués (bets)", played), ("fantômes seuls", ghost)):
        nn, mean, posr = stats(v)
        if nn:
            print(f"  {lbl:>16} : n={nn:4d} · CLV moyen {mean:+.2f}% · % CLV>0 {posr:4.1f}%")
    print("  (CLV>0 = on a battu la ligne de clôture = edge structurel ; c'est LE prédicteur long terme)")
else:
    print("  (aucun CLV stocké pour l'instant)")

# =========================================================================================
# (1) RANG DE SÉLECTION (profondeur) + EDGE FIGÉ — dès que l'instrumentation 2026-08-20 s'accumule
# =========================================================================================
print("\n### (1) ROI PAR PROFONDEUR DE MARCHÉ (`markets`) — instrumentation depuis le 20/08")
dp = [x for x in preds if x.get("depth") is not None]
print(f"     prédictions avec profondeur figée : {len(dp)}")
if len(dp) >= 40:
    qs = sorted(x["depth"] for x in dp)
    t1, t2 = qs[len(qs) // 3], qs[2 * len(qs) // 3]
    for name, lo, hi in [("profond (top liq.)", t2, 1e9), ("médian", t1, t2), ("mince (peu liq.)", -1, t1)]:
        line(name, [x for x in dp if lo <= x["depth"] < hi] if hi < 1e9 else [x for x in dp if x["depth"] >= lo])
else:
    print("     -> en attente d'accumulation (mesurable ~2-3 semaines après le 20/08).")

print("\n### (1b) ROI PAR EDGE SHARP FIGÉ À LA SÉLECTION (`sel_edge`) — instrumentation depuis le 20/08")
se = [x for x in preds if x.get("sedge") is not None]
print(f"     prédictions avec edge figé : {len(se)}")
if len(se) >= 40:
    for name, lo, hi in [("edge ≤ 0", -9, 0.0), ("0..+3%", 0.0, 0.03), ("> +3%", 0.03, 9)]:
        line(name, [x for x in se if lo <= x["sedge"] < hi])
else:
    print("     -> en attente d'accumulation (mesurable ~2-3 semaines après le 20/08).")

print("\n" + "=" * 78)
print("(LECTURE SEULE des sidecars — aucune écriture, aucun code prod touché.)")
