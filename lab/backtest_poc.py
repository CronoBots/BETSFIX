# -*- coding: utf-8 -*-
"""POC LABO BETSFIX — 100% ISOLÉ (scratchpad, stdlib seul, aucun import du projet, aucune écriture dans data/).
Démontre la chaîne : données gratuites (football-data.co.uk, cotes de CLÔTURE Pinnacle) -> Elo maison
walk-forward -> modèle 1X2 -> backtest value vs la clôture sharp, train/test séparés chronologiquement.
But : montrer des CHIFFRES réels (calibration, ROI, log-loss vs marché) sans toucher à la prod."""
import io, os, sys, csv, math, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "fd_cache")
os.makedirs(CACHE, exist_ok=True)

LEAGUES = ["E0", "D1", "SP1", "I1", "F1"]                 # PL, Bundesliga, Liga, Serie A, Ligue 1
SEASONS = ["1516","1617","1718","1819","1920","2021","2122","2223","2324","2425"]

def fetch(season, lg):
    fn = os.path.join(CACHE, f"{season}_{lg}.csv")
    if os.path.exists(fn) and os.path.getsize(fn) > 1000:
        return open(fn, "rb").read()
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{lg}.csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
        open(fn, "wb").write(data)
        return data
    except Exception as e:
        print(f"  (skip {season}/{lg}: {e})")
        return None

def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            import datetime; return datetime.datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def num(x):
    try: return float(x)
    except Exception: return None

# --- 1) INGESTION ---
rows = []
for season in SEASONS:
    for lg in LEAGUES:
        raw = fetch(season, lg)
        if not raw: continue
        txt = raw.decode("latin-1")
        rd = csv.DictReader(io.StringIO(txt))
        for r in rd:
            d = parse_date((r.get("Date") or "").strip())
            h, a, ftr = r.get("HomeTeam"), r.get("AwayTeam"), r.get("FTR")
            if not (d and h and a and ftr in ("H", "D", "A")): continue
            # cotes EARLY (à l'ouverture ~) = Pinnacle PS*, repli Bet365 B365*
            oh = num(r.get("PSH")) or num(r.get("B365H"))
            od = num(r.get("PSD")) or num(r.get("B365D"))
            oa = num(r.get("PSA")) or num(r.get("B365A"))
            # cotes de CLÔTURE = Pinnacle PSC*, repli Bet365 clôture B365C*
            ohc = num(r.get("PSCH")) or num(r.get("B365CH"))
            odc = num(r.get("PSCD")) or num(r.get("B365CD"))
            oac = num(r.get("PSCA")) or num(r.get("B365CA"))
            if not (oh and od and oa and oh > 1 and od > 1 and oa > 1): continue
            has_close = bool(ohc and odc and oac and ohc > 1 and odc > 1 and oac > 1)
            rows.append({"date": d, "lg": lg, "season": season, "h": h, "a": a, "ftr": ftr,
                         "oh": oh, "od": od, "oa": oa,
                         "ohc": ohc if has_close else oh, "odc": odc if has_close else od,
                         "oac": oac if has_close else oa, "has_close": has_close})
rows.sort(key=lambda x: x["date"])
print(f"Matchs ingérés (avec cotes) : {len(rows)}  ·  {len(SEASONS)} saisons × {len(LEAGUES)} ligues")

# --- 2) ELO MAISON walk-forward (par ligue, home advantage) ---
K, HA = 20.0, 65.0          # paramètres Elo standards (pas d'optimisation -> honnête)
elo = {}                    # (lg, team) -> rating
def get(lg, t): return elo.setdefault((lg, t), 1500.0)
def We(dr): return 1.0 / (1.0 + 10 ** (-dr / 400.0))   # score attendu domicile

burn = {}                   # nb de matchs vus par équipe (burn-in)
for r in rows:
    lg = r["lg"]
    eh, ea = get(lg, r["h"]), get(lg, r["a"])
    dr = eh + HA - ea
    r["We"] = We(dr)
    r["seen"] = min(burn.get((lg, r["h"]), 0), burn.get((lg, r["a"]), 0))
    sh = 1.0 if r["ftr"] == "H" else (0.5 if r["ftr"] == "D" else 0.0)
    exp = r["We"]
    elo[(lg, r["h"])] = eh + K * (sh - exp)
    elo[(lg, r["a"])] = ea + K * ((1 - sh) - (1 - exp))
    burn[(lg, r["h"])] = burn.get((lg, r["h"]), 0) + 1
    burn[(lg, r["a"])] = burn.get((lg, r["a"]), 0) + 1

# garder seulement les matchs avec assez d'historique (Elo stabilisé)
data = [r for r in rows if r["seen"] >= 10]
print(f"Matchs après burn-in (Elo stabilisé, ≥10 matchs/équipe) : {len(data)}")

# --- 3) MODÈLE 1X2 : Elo -> (p_home, p_draw, p_away) via 1 paramètre de nul, AJUSTÉ SUR LE TRAIN ---
cut = int(len(data) * 0.60)
train, test = data[:cut], data[cut:]
print(f"Split chrono -> TRAIN {len(train)} · TEST {len(test)}  (test = {test[0]['date'].date()} → {test[-1]['date'].date()})")

def probs(We_, c):
    pdraw = max(0.02, min(0.6, c * (1 - abs(2 * We_ - 1))))   # nul max quand équilibré, ~0 si écart fort
    rem = 1 - pdraw
    return rem * We_, pdraw, rem * (1 - We_)

def logloss(sample, c):
    s = 0.0
    for r in sample:
        ph, pd, pa = probs(r["We"], c)
        p = ph if r["ftr"] == "H" else (pd if r["ftr"] == "D" else pa)
        s -= math.log(max(p, 1e-9))
    return s / len(sample)

# recherche 1D du meilleur c (paramètre de nul) sur le TRAIN
best_c, best = 0.25, 9e9
cc = 0.05
while cc <= 0.55:
    ll = logloss(train, cc)
    if ll < best: best, best_c = ll, cc
    cc += 0.01
print(f"Paramètre de nul ajusté sur le train : c={best_c:.2f}  (log-loss train {best:.4f})")

def devig(oh, od, oa):
    ih, idr, ia = 1/oh, 1/od, 1/oa
    s = ih + idr + ia
    return ih/s, idr/s, ia/s     # probas marché dé-viggées

# --- 4) ÉVALUATION SUR LE TEST (out-of-sample) ---
# 4a) log-loss modèle vs marché (le modèle rivalise-t-il avec la CLÔTURE Pinnacle = le sharp le plus dur ?)
ll_model = logloss(test, best_c)
ll_mkt = 0.0
for r in test:
    mh, md, ma = devig(r["ohc"], r["odc"], r["oac"])
    p = mh if r["ftr"] == "H" else (md if r["ftr"] == "D" else ma)
    ll_mkt -= math.log(max(p, 1e-9))
ll_mkt /= len(test)
print(f"\n=== QUALITÉ DE PROBA (test, log-loss, plus bas = mieux) ===")
print(f"  Modèle Elo maison : {ll_model:.4f}")
print(f"  Clôture Pinnacle  : {ll_mkt:.4f}   <- le sharp (référence)")
print(f"  -> {'le marché reste meilleur' if ll_mkt < ll_model else 'le modèle bat le marché'} "
      f"(écart {ll_model-ll_mkt:+.4f})")

# 4b) calibration de P(domicile)
print(f"\n=== CALIBRATION P(domicile) sur le test ===")
bins = [[0,0,0.0] for _ in range(10)]   # [n, wins, sum_p]
for r in test:
    ph, pd, pa = probs(r["We"], best_c)
    b = min(9, int(ph*10)); bins[b][0]+=1; bins[b][2]+=ph
    if r["ftr"]=="H": bins[b][1]+=1
print("  proba prédite | fréquence réelle | n")
for i,(n,w,sp) in enumerate(bins):
    if n>=20:
        print(f"   {sp/n*100:5.1f}%       |    {w/n*100:5.1f}%      | {n}")

# 4c) BACKTEST VALUE — au prix EARLY vs au prix CLÔTURE (le TIMING change-t-il tout ?)
def value_bets(sample, edge, use_close):
    """Renvoie la liste des paris value : (sel, cote_jouée, gagné?, cote_close, cote_early)."""
    out = []
    for r in sample:
        ph, pd, pa = probs(r["We"], best_c)
        legs = ((("H", ph, r["ohc"] if use_close else r["oh"]),
                 ("D", pd, r["odc"] if use_close else r["od"]),
                 ("A", pa, r["oac"] if use_close else r["oa"])))
        best = None
        for sel, p, o in legs:
            ev = p * o - 1
            if ev > edge and (best is None or ev > best[3]):
                best = (sel, o, r["ftr"] == sel, ev)
        if best:
            oc = {"H": r["ohc"], "D": r["odc"], "A": r["oac"]}[best[0]]
            oe = {"H": r["oh"],  "D": r["od"],  "A": r["oa"]}[best[0]]
            out.append((best[0], best[1], best[2], oc, oe))
    return out

def roi_of(bets):
    if not bets: return (0, 0.0, 0.0, 0.0)
    ret = sum(o for _, o, w, _, _ in bets if w)
    return (len(bets), sum(1 for b in bets if b[2]) / len(bets) * 100,
            sum(b[1] for b in bets) / len(bets), (ret - len(bets)) / len(bets) * 100)

print(f"\n=== BACKTEST VALUE : jouer au prix EARLY vs au prix CLÔTURE (test, mise 1u) ===")
print(f"  {'seuil':>6} | {'prix joué':>9} | {'paris':>5} | réussite | cote moy |   ROI")
for edge in (0.05, 0.10):
    for use_close, lbl in ((False, "EARLY"), (True, "CLÔTURE")):
        n, hit, om, roi = roi_of(value_bets(test, edge, use_close))
        print(f"  >{edge*100:>3.0f}% | {lbl:>9} | {n:5d} | {hit:6.1f}% | {om:6.2f}  | {roi:+5.1f}%")

# 5) CLV — les picks value (au prix EARLY) sont-ils CONFIRMÉS par le mouvement du sharp jusqu'à la clôture ?
#    CLV>0 = le marché a bougé VERS notre pick (proba clôture > proba d'entrée) -> signal prédictif réel,
#    même si le ROI top-5 est mince (sur des books plus mous / ligues moins efficaces, ce CLV se monétise).
print(f"\n=== CLV des picks value au prix EARLY (test) ===")
for edge in (0.05, 0.10):
    bets = [b for b in value_bets(test, edge, use_close=False)]
    if not bets:
        print(f"  edge>{edge*100:>3.0f}% : aucun pari"); continue
    clvs, beat = [], 0
    for sel, o_played, won, oc, oe in bets:
        # proba dé-viggée de NOTRE sélection, tôt vs clôture (besoin des 3 cotes -> approx via 1/cote normalisé)
        pe = 1.0 / oe; pc = 1.0 / oc                  # proxy simple (avant normalisation identique des 2 côtés)
        clv = pc - pe                                 # >0 = la cote a raccourci = le marché est venu vers nous
        clvs.append(clv * 100)
        if oc < oe: beat += 1                          # on a obtenu une meilleure cote que la clôture
    print(f"  edge>{edge*100:>3.0f}% : {len(bets):4d} paris · CLV moyen {sum(clvs)/len(clvs):+5.2f} pp · "
          f"battent la clôture {beat/len(bets)*100:4.1f}%")

# 6) EDGE PAR TRANCHE DE COTE — où le modèle gagne (favoris ?) vs perd (outsiders ?)
print(f"\n=== ROI PAR TRANCHE DE COTE (edge>5%, prix EARLY, test) ===")
brackets = [(1.0, 1.8, "favoris <1.8"), (1.8, 2.5, "1.8–2.5"),
            (2.5, 4.0, "2.5–4.0"), (4.0, 99, "outsiders >4")]
bets = value_bets(test, 0.05, use_close=False)
for lo, hi, name in brackets:
    sub = [b for b in bets if lo <= b[1] < hi]
    n, hit, om, roi = roi_of(sub)
    if n:
        print(f"  {name:>14} : {n:4d} paris · réussite {hit:4.1f}% · cote moy {om:4.2f} · ROI {roi:+6.1f}%")

print("\n(POC isolé — aucune écriture hors lab/, aucun code prod touché.)")
