# -*- coding: utf-8 -*-
"""POC LABO BETSFIX — 100% ISOLÉ (lab/, stdlib seul, aucun import du projet, aucune écriture hors lab/).
v3 : compare ligues EFFICACES (top-5 EU) vs PEU EFFICACES (2e divisions + secondaires) pour voir si l'edge
apparaît là où le sharp est moins tranchant (= le terrain réel de BETSFIX). CLV propre (de-vig 3 voies).
Chaîne : football-data.co.uk (cotes Pinnacle EARLY PS* + CLÔTURE PSC*) -> Elo maison walk-forward ->
modèle 1X2 -> éval out-of-sample par groupe (log-loss vs clôture, CLV, ROI early, ROI par tranche de cote)."""
import io, os, sys, csv, math, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "fd_cache"); os.makedirs(CACHE, exist_ok=True)

GROUPS = {
    "EFFICACES (top-5 EU)":   ["E0", "D1", "SP1", "I1", "F1"],
    "PEU EFFICACES (2e div/secondaires)": ["E1", "E2", "SC0", "B1", "N1", "P1", "T1", "G1"],
}
SEASONS = ["1516","1617","1718","1819","1920","2021","2122","2223","2324","2425"]

def fetch(season, lg):
    fn = os.path.join(CACHE, f"{season}_{lg}.csv")
    if os.path.exists(fn) and os.path.getsize(fn) > 1000:
        return open(fn, "rb").read()
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{lg}.csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
        open(fn, "wb").write(data); return data
    except Exception as e:
        print(f"  (skip {season}/{lg}: {e})"); return None

def parse_date(s):
    import datetime
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try: return datetime.datetime.strptime(s, fmt)
        except Exception: pass
    return None

def num(x):
    try: return float(x)
    except Exception: return None

def devig(oh, od, oa):
    ih, idr, ia = 1/oh, 1/od, 1/oa; s = ih + idr + ia
    return {"H": ih/s, "D": idr/s, "A": ia/s}

# --- 1) INGESTION (tag groupe) ---
rows = []
for gname, leagues in GROUPS.items():
    for season in SEASONS:
        for lg in leagues:
            raw = fetch(season, lg)
            if not raw: continue
            for r in csv.DictReader(io.StringIO(raw.decode("latin-1"))):
                d = parse_date((r.get("Date") or "").strip())
                h, a, ftr = r.get("HomeTeam"), r.get("AwayTeam"), r.get("FTR")
                if not (d and h and a and ftr in ("H", "D", "A")): continue
                oh = num(r.get("PSH")) or num(r.get("B365H"))
                od = num(r.get("PSD")) or num(r.get("B365D"))
                oa = num(r.get("PSA")) or num(r.get("B365A"))
                ohc = num(r.get("PSCH")) or num(r.get("B365CH"))
                odc = num(r.get("PSCD")) or num(r.get("B365CD"))
                oac = num(r.get("PSCA")) or num(r.get("B365CA"))
                if not (oh and od and oa and oh > 1 and od > 1 and oa > 1): continue
                hc = bool(ohc and odc and oac and ohc > 1 and odc > 1 and oac > 1)
                rows.append({"date": d, "grp": gname, "lg": lg, "h": h, "a": a, "ftr": ftr,
                             "oh": oh, "od": od, "oa": oa,
                             "ohc": ohc if hc else oh, "odc": odc if hc else od, "oac": oac if hc else oa})
rows.sort(key=lambda x: x["date"])

# --- 2) ELO maison walk-forward (par ligue) ---
K, HA = 20.0, 65.0
elo, burn = {}, {}
def get(lg, t): return elo.setdefault((lg, t), 1500.0)
for r in rows:
    lg = r["lg"]; eh, ea = get(lg, r["h"]), get(lg, r["a"])
    r["We"] = 1.0 / (1.0 + 10 ** (-(eh + HA - ea) / 400.0))
    r["seen"] = min(burn.get((lg, r["h"]), 0), burn.get((lg, r["a"]), 0))
    sh = 1.0 if r["ftr"] == "H" else (0.5 if r["ftr"] == "D" else 0.0)
    elo[(lg, r["h"])] = eh + K * (sh - r["We"]); elo[(lg, r["a"])] = ea + K * ((1 - sh) - (1 - r["We"]))
    burn[(lg, r["h"])] = burn.get((lg, r["h"]), 0) + 1; burn[(lg, r["a"])] = burn.get((lg, r["a"]), 0) + 1

# --- 3) MODÈLE 1X2 (1 param de nul, ajusté par groupe sur le train) ---
def probs(We_, c):
    pd = max(0.02, min(0.6, c * (1 - abs(2 * We_ - 1)))); rem = 1 - pd
    return {"H": rem * We_, "D": pd, "A": rem * (1 - We_)}

def logloss(sample, c):
    s = 0.0
    for r in sample:
        p = probs(r["We"], c)[r["ftr"]]; s -= math.log(max(p, 1e-9))
    return s / len(sample)

def value_bets(sample, c, edge):
    out = []
    for r in sample:
        pm = probs(r["We"], c); best = None
        for sel in ("H", "D", "A"):
            ev = pm[sel] * r["oh" if sel=="H" else "od" if sel=="D" else "oa"] - 1
            oe = r["oh" if sel=="H" else "od" if sel=="D" else "oa"]
            if ev > edge and (best is None or ev > best[-1]):
                best = (sel, oe, r["ftr"] == sel, {"H": r["ohc"], "D": r["odc"], "A": r["oac"]}[sel], ev)
        if best: out.append(best)   # (sel, cote_early, gagné, cote_clôture, ev)
    return out

def roi(bets):
    if not bets: return (0, 0.0, 0.0, 0.0)
    ret = sum(b[1] for b in bets if b[2])
    return (len(bets), sum(1 for b in bets if b[2])/len(bets)*100, sum(b[1] for b in bets)/len(bets), (ret-len(bets))/len(bets)*100)

def fit_c(train):
    bestc, bl, c = 0.25, 9e9, 0.05
    while c <= 0.55:
        l = logloss(train, c)
        if l < bl: bl, bestc = l, c
        c += 0.01
    return bestc

def eval_bets(test, c, edge):
    """Paris value au prix EARLY (edge vs early). Renvoie liste (sel, cote_early, gagné, clv%)
    avec CLV PROPRE = cote_early × proba_clôture_dé-viggée − 1 (>0 = on a battu la clôture)."""
    out = []
    for r in test:
        pm = probs(r["We"], c); best = None
        for sel in ("H", "D", "A"):
            oe = {"H": r["oh"], "D": r["od"], "A": r["oa"]}[sel]
            ev = pm[sel] * oe - 1
            if ev > edge and (best is None or ev > best[-1]):
                best = (sel, oe, r["ftr"] == sel, r, ev)
        if best:
            sel, oe, won, r, ev = best
            clv = oe * devig(r["ohc"], r["odc"], r["oac"])[sel] - 1
            out.append((sel, oe, won, clv * 100))
    return out

# --- 4) ÉVALUATION PAR GROUPE ---
print(f"Total matchs (avec cotes) : {len(rows)}\n" + "=" * 78)
for gname in GROUPS:
    data = [r for r in rows if r["grp"] == gname and r["seen"] >= 10]
    cut = int(len(data) * 0.60); train, test = data[:cut], data[cut:]
    c = fit_c(train)
    llm = logloss(test, c)
    llc = -sum(math.log(max(devig(r["ohc"], r["odc"], r["oac"])[r["ftr"]], 1e-9)) for r in test) / len(test)
    print(f"\n### {gname}")
    print(f"  test : {len(test)} matchs ({test[0]['date'].date()} → {test[-1]['date'].date()}) · c={c:.2f}")
    print(f"  log-loss : modèle {llm:.4f}  vs  clôture {llc:.4f}   (écart {llm-llc:+.4f} · <0 = on bat le sharp)")
    bets = eval_bets(test, c, 0.05)
    n, hit, om, r_early = roi([(b[0], b[1], b[2]) for b in bets])
    if not n:
        print("  value edge>5% : aucun pari"); continue
    clvs = [b[3] for b in bets]; beat = sum(1 for x in clvs if x > 0)
    print(f"  value edge>5% : {n} paris · réussite {hit:.1f}% · cote moy {om:.2f} · ROI early {r_early:+.1f}%")
    print(f"     CLV propre (de-vig 3 voies) : moyen {sum(clvs)/len(clvs):+.2f}%  ·  paris à CLV+ {beat/len(clvs)*100:.1f}%")
    for lo, hi, name in [(1,1.8,"favoris <1.8"),(1.8,2.5,"1.8-2.5"),(2.5,4,"2.5-4"),(4,99,"outsiders >4")]:
        sub = [(b[0], b[1], b[2]) for b in bets if lo <= b[1] < hi]; sn, sh, so, sr = roi(sub)
        if sn: print(f"     {name:>13}: {sn:4d} paris · {sh:4.1f}% · ROI {sr:+6.1f}%")
print("\n" + "=" * 78 + "\n(POC isolé — aucune écriture hors lab/, aucun code prod touché.)")
