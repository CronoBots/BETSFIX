# -*- coding: utf-8 -*-
"""POC LABO — CLAUDE AJOUTE-T-IL DE LA VALUE ? (isolé, LECTURE SEULE des sidecars prod, écrit rien.)
On teste les PROBAS de Claude sur ses propres prédictions historiques (les FANTÔMES = toutes les prédictions,
non biaisées par la sélection). Baseline = le MARCHÉ (la cote). Si les fantômes sont +ROI et si la value de
Claude prédit le ROI (monotonie), alors Claude bat le marché = il ajoute de la value au-delà des cotes.
NB : `1/cote` (marché) est gonflé par la marge -> le juge FINAL est le ROI (net de marge), pas le Brier."""
import io, os, sys, glob, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# LECTURE SEULE : on lit les sidecars via le même dossier que la prod, sans rien importer d'actif.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import analyses          # uniquement pour analyses.DIR (chemin) — aucune écriture

preds = []   # (prob0-1, cote, won, code, is_bet)
for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    for s in (d.get("shadow") or []):
        pr, co, res = s.get("prob"), s.get("cote"), s.get("result")
        if res in ("won", "lost") and pr and co and co > 1:
            preds.append((pr/100.0, float(co), res == "won", (s.get("code") or "?").upper(), False))
    for b in (d.get("bets") or []):
        pr, co, res = b.get("prob"), b.get("odds") or b.get("cote"), b.get("result")
        if res in ("won", "lost") and pr and co and co > 1:
            preds.append((pr/100.0, float(co), res == "won", (b.get("code") or "?").upper(), True))

n = len(preds)
print(f"Prédictions réglées (fantômes + paris) : {n}\n" + "="*70)

def roi(sub):
    if not sub: return (0, 0.0, 0.0, 0.0, 0.0)
    ret = sum(co for _, co, w, _, _ in sub if w)
    return (len(sub), sum(1 for x in sub if x[2])/len(sub)*100, sum(x[1] for x in sub)/len(sub),
            (ret-len(sub))/len(sub)*100, sum(x[0] for x in sub)/len(sub)*100)

# 1) GLOBAL
N, hit, om, r, ac = roi(preds)
print(f"\n### GLOBAL (mise 1u sur CHAQUE prédiction)")
print(f"  {N} prédictions · confiance moy {ac:.1f}% · réussite réelle {hit:.1f}% · cote moy {om:.2f} · ROI {r:+.1f}%")

# 2) CALIBRATION (confiance annoncée vs réussite réelle)
print(f"\n### CALIBRATION (la confiance Claude tient-elle ?)")
print("  confiance |  n  | réussite réelle")
for lo in range(40, 100, 10):
    sub = [x for x in preds if lo/100 <= x[0] < (lo+10)/100]
    if len(sub) >= 30:
        print(f"   {lo}-{lo+10}% | {len(sub):4d} | {sum(1 for x in sub if x[2])/len(sub)*100:5.1f}%")

# 3) BRIER : Claude vs marché (1/cote). Plus bas = meilleur prédicteur.
def brier(getp): return sum((getp(x) - (1.0 if x[2] else 0.0))**2 for x in preds)/n
b_claude = brier(lambda x: x[0])
b_market = brier(lambda x: 1.0/x[1])
print(f"\n### BRIER (qualité de proba, plus bas = mieux)")
print(f"  Claude : {b_claude:.4f}   ·   marché 1/cote (gonflé marge) : {b_market:.4f}")
print(f"  -> {'Claude meilleur' if b_claude < b_market else 'marché meilleur'} (le ROI reste le juge net de marge)")

# 4) VALUE DE CLAUDE -> prédit-elle le ROI ? (monotonie = signal réel)
print(f"\n### ROI PAR NIVEAU DE VALUE CLAUDE (value = proba×cote − 1)")
def val(x): return x[0]*x[1]-1
for lo, hi, name in [(-1,0,"value < 0"),(0,0.10,"0–10%"),(0.10,0.25,"10–25%"),(0.25,99,"> 25%")]:
    sub = [x for x in preds if lo <= val(x) < hi]
    sn, sh, so, sr, _ = roi(sub)
    if sn: print(f"  {name:>10} : {sn:5d} · réussite {sh:4.1f}% · cote {so:4.2f} · ROI {sr:+6.1f}%")

# 5) PAR TIER DE CONFIANCE (le phare = ≥75%)
print(f"\n### PAR TIER DE CONFIANCE")
for lo, hi, name in [(0,0.60,"< 60%"),(0.60,0.75,"60–75%"),(0.75,1.01,"≥ 75% (Confiance)")]:
    sub = [x for x in preds if lo <= x[0] < hi]
    sn, sh, so, sr, sac = roi(sub)
    if sn: print(f"  {name:>18} : {sn:5d} · conf {sac:4.1f}% · réussite {sh:4.1f}% · ROI {sr:+6.1f}%")

# 6) PAR MARCHÉ (où vit l'edge ?) — top marchés par volume
print(f"\n### PAR MARCHÉ (≥120 prédictions)")
codes = {}
for x in preds: codes.setdefault(x[3], []).append(x)
for code, sub in sorted(codes.items(), key=lambda kv: -len(kv[1])):
    sn, sh, so, sr, _ = roi(sub)
    if sn >= 120:
        print(f"  {code:>14} : {sn:5d} · réussite {sh:4.1f}% · cote {so:4.2f} · ROI {sr:+6.1f}%")

# 7) VÉRIFICATION DES MARCHÉS ÉCARTÉS (bannis) — ROI sur TOUT vs sur la VALUE seule (ce qu'on jouerait)
def famille(code):
    c = code.lower()
    if "corner" in c: return "CORNERS (banni)"
    if "card" in c or "redcard" in c: return "CARTONS (banni)"
    if "btts" in c: return "BTTS (banni)"
    if "firstgoal" in c: return "1ER BUT (banni)"
    if c.startswith("half") or "1h" in c or "2h" in c: return "MI-TEMPS (banni)"
    if "shotsot" in c: return "TIRS CADRÉS (privilégié)"
    if c.startswith("dc "): return "DOUBLE CHANCE (privilégié)"
    if c.startswith("1x2"): return "RÉSULTAT 1X2 (privilégié)"
    if c.startswith("over") or c.startswith("under"): return "OVER/UNDER (privilégié)"
    if "teamtot" in c: return "ÉQUIPE MARQUE (privilégié)"
    return "autres"

def roi_val(sub):                                   # ROI sur les seules prédictions à value>0 (jouées)
    return roi([x for x in sub if x[0]*x[1]-1 > 0])

fams = {}
for x in preds: fams.setdefault(famille(x[3]), []).append(x)
print(f"\n### VÉRIFICATION DES MARCHÉS ÉCARTÉS vs PRIVILÉGIÉS")
print(f"  {'famille':>26} | {'n':>4} | ROI tout | value>0 (n) | ROI value")
order = ["CORNERS (banni)","CARTONS (banni)","BTTS (banni)","1ER BUT (banni)","MI-TEMPS (banni)",
         "TIRS CADRÉS (privilégié)","DOUBLE CHANCE (privilégié)","RÉSULTAT 1X2 (privilégié)",
         "OVER/UNDER (privilégié)","ÉQUIPE MARQUE (privilégié)"]
for f in order:
    sub = fams.get(f)
    if not sub: continue
    n1, _, _, r1, _ = roi(sub)
    nv, _, _, rv, _ = roi_val(sub)
    warn = "  ⚠️ échantillon faible" if n1 < 40 else ""
    print(f"  {f:>26} | {n1:4d} | {r1:+6.1f}% | {nv:4d} paris | {rv:+6.1f}%{warn}")

# 8) APERÇU INTELLIGENT PAR MARCHÉ — ventilé 👻 FANTÔME · 💎 VALUE · ⭐ CONFIANCE (≥75%), Under/Over SÉPARÉS
def rr(s):                                            # (n, ROI%) d'un sous-ensemble, ou None
    return None if not s else (len(s), (sum(o for _, o, w, _, _ in s if w) - len(s)) / len(s) * 100)
def cell_v(sub):                                      # value>0
    return rr([x for x in sub if x[0]*x[1]-1 > 0])
def cell_c(sub):                                      # value>0 ET confiance ≥75%
    return rr([x for x in sub if x[0]*x[1]-1 > 0 and x[0] >= 0.75])
def fmt(t): return f"{t[0]:3d}·{t[1]:+5.1f}%" if t else "   —     "

by_code = {}
for x in preds: by_code.setdefault(x[3], []).append(x)
print(f"\n### APERÇU INTELLIGENT PAR MARCHÉ (Under/Over séparés ; ≥60 fantômes)")
print(f"  {'marché':>22} | 👻 fantôme (réuss./conf) | 💎 value | ⭐ confiance≥75%")
for code in sorted(by_code, key=lambda c: (c.split()[0] if c.split() else c, c)):
    sub = by_code[code]
    if len(sub) < 60: continue
    win = sum(1 for x in sub if x[2]) / len(sub) * 100
    conf = sum(x[0] for x in sub) / len(sub) * 100
    print(f"  {code:>22} | {len(sub):4d}  {win:4.1f}% / {conf:4.1f}%    | {fmt(cell_v(sub))} | {fmt(cell_c(sub))}")

# Focus explicite OVER vs UNDER agrégés (la question : bien séparés ?)
print(f"\n### OVER vs UNDER (agrégé, tous seuils)")
for fam_lbl, pref in (("OVER (tous seuils)", "OVER"), ("UNDER (tous seuils)", "UNDER")):
    sub = [x for x in preds if x[3].startswith(pref)]
    if not sub: continue
    win = sum(1 for x in sub if x[2]) / len(sub) * 100
    n_all, _, om, r_all, _ = roi(sub)
    print(f"  {fam_lbl:>18} : {n_all:4d} fantômes · réuss {win:4.1f}% · cote {om:4.2f} · ROI tout {r_all:+.1f}% "
          f"· 💎 value {fmt(cell_v(sub))} · ⭐ conf {fmt(cell_c(sub))}")

print("\n" + "="*70 + "\n(LECTURE SEULE des sidecars, aucune écriture, aucun code prod modifié.)")
