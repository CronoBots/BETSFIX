# -*- coding: utf-8 -*-
"""POC LABO BETSFIX — OPTIMISATION HONNÊTE (isolé, stdlib seul, aucun import projet, écrit rien hors lab/).
Améliorations vs backtest_poc.py :
  • Elo pondéré par l'ÉCART DE BUTS (ratings plus fins).
  • Stratégies proches du produit BETSFIX : (A) back du FAVORI (cotes réelles) · (B) DOUBLE CHANCE 1X/X2.
  • Paramètres (seuil d'edge, bande de cote) OPTIMISÉS SUR LE TRAIN, évalués UNE FOIS sur le TEST (anti-overfit).
Groupe cible = ligues PEU EFFICACES (là où l'edge apparaît). Métriques : ROI + CLV propre (de-vig 3 voies).
⚠️ Les cotes DC sont un PROXY (dutch de home+draw : 1/(1/o_home+1/o_draw)) -> légèrement optimiste vs un vrai
marché DC (marge un peu plus forte) ; le CLV, lui, reste robuste."""
import io, os, sys, csv, math, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__)); CACHE = os.path.join(HERE, "fd_cache"); os.makedirs(CACHE, exist_ok=True)

SOFT = ["E1", "E2", "SC0", "B1", "N1", "P1", "T1", "G1"]     # ligues peu efficaces (terrain BETSFIX)
SEASONS = ["1516","1617","1718","1819","1920","2021","2122","2223","2324","2425"]

def fetch(season, lg):
    fn = os.path.join(CACHE, f"{season}_{lg}.csv")
    if os.path.exists(fn) and os.path.getsize(fn) > 1000: return open(fn, "rb").read()
    try:
        req = urllib.request.Request(f"https://www.football-data.co.uk/mmz4281/{season}/{lg}.csv",
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = urllib.request.urlopen(req, timeout=30).read(); open(fn, "wb").write(d); return d
    except Exception: return None

def pdate(s):
    import datetime
    for f in ("%d/%m/%Y", "%d/%m/%y"):
        try: return datetime.datetime.strptime(s, f)
        except Exception: pass
    return None
def num(x):
    try: return float(x)
    except Exception: return None
def devig(oh, od, oa):
    ih, idr, ia = 1/oh, 1/od, 1/oa; s = ih+idr+ia; return {"H": ih/s, "D": idr/s, "A": ia/s}

# --- INGESTION ---
rows = []
for season in SEASONS:
    for lg in SOFT:
        raw = fetch(season, lg)
        if not raw: continue
        for r in csv.DictReader(io.StringIO(raw.decode("latin-1"))):
            d = pdate((r.get("Date") or "").strip()); h, a, ftr = r.get("HomeTeam"), r.get("AwayTeam"), r.get("FTR")
            hg, ag = num(r.get("FTHG")), num(r.get("FTAG"))
            if not (d and h and a and ftr in ("H","D","A") and hg is not None and ag is not None): continue
            ph, pd_, pa = num(r.get("PSH")), num(r.get("PSD")), num(r.get("PSA"))          # Pinnacle EARLY (= ancre sharp)
            bh, bd, ba = num(r.get("B365H")), num(r.get("B365D")), num(r.get("B365A"))      # Bet365 EARLY (= book MOU, proxy Unibet)
            oh, od, oa = ph or bh, pd_ or bd, pa or ba
            ohc, odc, oac = num(r.get("PSCH")) or num(r.get("B365CH")), num(r.get("PSCD")) or num(r.get("B365CD")), num(r.get("PSCA")) or num(r.get("B365CA"))
            if not (oh and od and oa and oh>1 and od>1 and oa>1): continue
            hc = bool(ohc and odc and oac and ohc>1 and odc>1 and oac>1)
            has_pb = bool(ph and pd_ and pa and bh and bd and ba and min(ph,pd_,pa,bh,bd,ba) > 1)
            rows.append({"date": d, "lg": lg, "h": h, "a": a, "ftr": ftr, "hg": hg, "ag": ag,
                         "oh": oh, "od": od, "oa": oa, "ohc": ohc if hc else oh, "odc": odc if hc else od, "oac": oac if hc else oa,
                         "ph": ph, "pd": pd_, "pa": pa, "bh": bh, "bd": bd, "ba": ba, "has_pb": has_pb})
rows.sort(key=lambda x: x["date"])

# --- ELO pondéré ÉCART DE BUTS ---
K, HA = 20.0, 65.0
elo, burn = {}, {}
def g(lg, t): return elo.setdefault((lg, t), 1500.0)
for r in rows:
    lg = r["lg"]; eh, ea = g(lg, r["h"]), g(lg, r["a"])
    r["We"] = 1.0/(1.0+10**(-(eh+HA-ea)/400.0)); r["seen"] = min(burn.get((lg,r["h"]),0), burn.get((lg,r["a"]),0))
    sh = 1.0 if r["ftr"]=="H" else (0.5 if r["ftr"]=="D" else 0.0)
    gd = abs(r["hg"]-r["ag"]); G = 1.0 if gd<=1 else (1.5 if gd==2 else (11+gd)/8.0)   # multiplicateur écart de buts
    elo[(lg,r["h"])] = eh + K*G*(sh-r["We"]); elo[(lg,r["a"])] = ea + K*G*((1-sh)-(1-r["We"]))
    burn[(lg,r["h"])] = burn.get((lg,r["h"]),0)+1; burn[(lg,r["a"])] = burn.get((lg,r["a"]),0)+1

def probs(We_, c):
    pd = max(0.02, min(0.6, c*(1-abs(2*We_-1)))); rem = 1-pd; return {"H": rem*We_, "D": pd, "A": rem*(1-We_)}
def logloss(s, c): return -sum(math.log(max(probs(r["We"],c)[r["ftr"]],1e-9)) for r in s)/len(s)

data = [r for r in rows if r["seen"] >= 10]
cut = int(len(data)*0.60); TRAIN, TEST = data[:cut], data[cut:]
c = min((x/100 for x in range(5,56)), key=lambda cc: logloss(TRAIN, cc))   # param nul sur train
print(f"Ligues peu efficaces · {len(data)} matchs · TRAIN {len(TRAIN)} / TEST {len(TEST)} · c={c:.2f}\n")

# --- STRATÉGIES : renvoient la liste des paris (cote_jouée, gagné, clv%) ---
def strat_fav(sample, edge, lo, hi):
    """Back du FAVORI (issue à cote mini) si value modèle > edge et cote dans [lo,hi)."""
    out = []
    for r in sample:
        pm = probs(r["We"], c)
        sel = min(("H","D","A"), key=lambda s: {"H":r["oh"],"D":r["od"],"A":r["oa"]}[s])
        oe = {"H":r["oh"],"D":r["od"],"A":r["oa"]}[sel]
        if lo <= oe < hi and pm[sel]*oe-1 > edge:
            clv = oe*devig(r["ohc"],r["odc"],r["oac"])[sel]-1
            out.append((oe, r["ftr"]==sel, clv*100))
    return out
def strat_dc(sample, edge, lo, hi):
    """DOUBLE CHANCE 1X ou X2 (cote proxy dutch) si value modèle > edge et cote dans [lo,hi)."""
    out = []
    for r in sample:
        pm = probs(r["We"], c)
        for legs, keep in ((("H","D"), ("H","D")), (("D","A"), ("D","A"))):
            o1, o2 = {"H":r["oh"],"D":r["od"],"A":r["oa"]}[legs[0]], {"H":r["oh"],"D":r["od"],"A":r["oa"]}[legs[1]]
            odc = 1.0/(1.0/o1+1.0/o2); pdc = pm[legs[0]]+pm[legs[1]]
            if lo <= odc < hi and pdc*odc-1 > edge:
                dv = devig(r["ohc"],r["odc"],r["oac"]); pcl = dv[legs[0]]+dv[legs[1]]
                clv = odc*pcl-1
                out.append((odc, r["ftr"] in keep, clv*100)); break
    return out
def strat_anchor(sample, edge, lo, hi):
    """MÉTHODE BETSFIX : ancre sharp = Pinnacle EARLY (dé-viggée) ; on PARIE au book MOU (Bet365) l'issue dont
    la cote molle offre de la value vs la proba sharp. Settle à la cote Bet365. C'est du line-shopping : pas un
    edge de MODÈLE, mais l'exploitation d'un book plus tendre que le sharp (comme parier Unibet ancré Pinnacle)."""
    out = []
    for r in sample:
        if not r["has_pb"]: continue
        psharp = devig(r["ph"], r["pd"], r["pa"])                 # vérité sharp
        for sel in ("H","D","A"):
            ob = {"H":r["bh"],"D":r["bd"],"A":r["ba"]}[sel]        # cote au book mou
            if lo <= ob < hi and psharp[sel]*ob - 1 > edge:
                clv = ob*devig(r["ohc"],r["odc"],r["oac"])[sel]-1  # la cote molle bat-elle la clôture ?
                out.append((ob, r["ftr"]==sel, clv*100)); break
    return out

def summ(b):
    if not b: return None
    ret = sum(o for o,w,_ in b if w)
    return {"n": len(b), "hit": sum(1 for _,w,_ in b if w)/len(b)*100, "om": sum(o for o,_,_ in b)/len(b),
            "roi": (ret-len(b))/len(b)*100, "clv": sum(cl for _,_,cl in b)/len(b), "beat": sum(1 for _,_,cl in b if cl>0)/len(b)*100}

# --- OPTIMISATION SUR LE TRAIN, ÉVAL SUR LE TEST ---
EDGES = [0.0, 0.01, 0.02, 0.04, 0.06, 0.08]
BANDS = [(1.0,1.4),(1.0,1.6),(1.0,1.8),(1.2,1.7),(1.0,2.2),(1.15,1.6),(1.0,3.0)]
for name, fn, min_n in (("BACK DU FAVORI (modèle Elo, cotes réelles)", strat_fav, 150),
                        ("DOUBLE CHANCE 1X/X2 (modèle Elo, proxy)", strat_dc, 150),
                        ("MÉTHODE BETSFIX : ancre Pinnacle -> parier Bet365 (book mou)", strat_anchor, 60)):
    best, cfg = None, None
    for e in EDGES:
        for lo, hi in BANDS:
            s = summ(fn(TRAIN, e, lo, hi))
            if s and s["n"] >= min_n and (best is None or s["roi"] > best["roi"]):
                best, cfg = s, (e, lo, hi)
    print(f"### {name}")
    if not cfg:
        print("  (aucune config avec assez de paris)\n"); continue
    e, lo, hi = cfg
    tr, te = summ(fn(TRAIN, e, lo, hi)), summ(fn(TEST, e, lo, hi))
    print(f"  config optimale (sur TRAIN) : edge>{e*100:.0f}% · cote [{lo}-{hi})")
    print(f"  TRAIN : {tr['n']} paris · réussite {tr['hit']:.1f}% · cote {tr['om']:.2f} · ROI {tr['roi']:+.1f}% · CLV {tr['clv']:+.2f}%")
    if te:
        print(f"  TEST  : {te['n']} paris · réussite {te['hit']:.1f}% · cote {te['om']:.2f} · ROI {te['roi']:+.1f}% · "
              f"CLV {te['clv']:+.2f}% · CLV+ {te['beat']:.0f}%   <-- OUT-OF-SAMPLE")
    print()
print("(POC isolé — aucune écriture hors lab/, aucun code prod touché. Cotes DC = proxy dutch.)")
