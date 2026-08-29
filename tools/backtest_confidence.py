#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Backtest EXHAUSTIF de la sélection « pari de confiance » foot sur TOUT l'historique.

Objectif (user 2026-08-29) : trouver LE meilleur combo de knobs de sélection (seuil de confiance,
bande de cote, marchés autorisés, règle de départage, plancher d'EV) qui maximise
réussite / ROI / série de victoires / cote — SANS surapprentissage.

Vivier HONNÊTE = 1 pari/match. Pour chaque match RÉGLÉ, les candidats = les `shadow[]` (fantômes)
avec leur proba BRUTE de l'analyste, cote, code marché et résultat won/lost. La sélection choisit
AU PLUS 1 candidat par match (comme en prod). Aucune fuite de calibration (on backteste sur la
proba BRUTE, directement réglable comme knob ; la prod seuille sur la confiance calibrée qui suit
la brute + un ajustement appris).

Garde-fous anti-surapprentissage :
  • volume minimum (n_bets ≥ MIN_N) — un « 100% sur 8 paris » ne gagne pas ;
  • split TEMPOREL train/test (date de coupure) → on juge un combo sur sa tenue OOS ;
  • métriques par moitié rapportées pour repérer l'instabilité.

Usage :
  python tools/backtest_confidence.py build                # (re)construit le cache dataset
  python tools/backtest_confidence.py grid --objective roi --min-n 60 --top 25 [--slice i/N]
                                                           # grille -> JSON trié (stdout)
  python tools/backtest_confidence.py eval --params '<json>'   # évalue UN combo (full + splits)
"""
import argparse
import glob
import itertools
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # anti-crash cp1252 Windows
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.analyses import market_of  # noqa: E402

ANALYSES_DIR = os.path.join(ROOT, "data", "analyses")
CACHE = os.path.join(ROOT, "data", "backtest", "confidence_candidates.json")

# Bans DURS (décision user, jamais un pari joué) — restent hors de TOUT whitelist.
HARD_BAN = {"Corners", "Les 2 marquent"}
# Familles NON réglables/prop douteuses -> exclues du vivier de sélection (bruit).
NON_SELECTABLE = {"Premier but", "Mi-temps", "Score exact", "Props joueur", "Arrêts gardien"}


# --------------------------------------------------------------------------- dataset
def build_dataset() -> list:
    """Un enregistrement par match RÉGLÉ : {id, date, cands:[{prob,cote,code,market,result}]}."""
    out = []
    for p in glob.glob(os.path.join(ANALYSES_DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (d.get("result") or {}).get("pick_result"):
            continue
        sh = d.get("shadow")
        if not isinstance(sh, list) or not sh:
            continue
        cands, seen = [], set()
        for s in sh:
            if not isinstance(s, dict):
                continue
            if s.get("result") not in ("won", "lost", "push"):
                continue
            cote, prob, code = s.get("cote"), s.get("prob"), s.get("code") or ""
            if not cote or prob is None or cote < 1.01:
                continue
            from app.confidence_pick import _is_period_bet
            if _is_period_bet(s.get("sel") or ""):
                continue                              # pari de PÉRIODE mal codé/réglé (1ère MT…) -> hors vivier
            mk = market_of(code)
            if mk in HARD_BAN or mk in NON_SELECTABLE:
                continue
            key = (round(float(prob)), round(float(cote), 2), code)
            if key in seen:
                continue
            seen.add(key)
            cands.append({"prob": float(prob), "cote": float(cote), "code": code,
                          "market": mk, "result": s.get("result")})
        if not cands:
            continue
        out.append({"id": str(d.get("id")), "date": (d.get("start") or "")[:10], "cands": cands})
    out.sort(key=lambda m: (m["date"], m["id"]))
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(out, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def load_dataset() -> list:
    ds = build_dataset() if not os.path.exists(CACHE) else json.load(open(CACHE, encoding="utf-8"))
    _index(ds)
    return ds


def _index(dataset):
    """Pré-calcul (1×) pour un backtest RAPIDE : EV par candidat + candidats PRÉ-TRIÉS par départage
    (le select d'un combo devient un early-exit sur la liste triée : 1er candidat qui passe le filtre)."""
    for m in dataset:
        for c in m["cands"]:
            c["ev"] = c["prob"] / 100.0 * c["cote"] - 1.0
        m["ord"] = {
            "prob": sorted(m["cands"], key=lambda c: (-c["prob"], -c["ev"])),
            "ev": sorted(m["cands"], key=lambda c: (-c["ev"], -c["prob"])),
            "cote_lo": sorted(m["cands"], key=lambda c: (c["cote"], -c["prob"])),
            "cote_hi": sorted(m["cands"], key=lambda c: (-c["cote"], -c["prob"])),
        }


# --------------------------------------------------------------------------- selection
# Profil CONFIANCE DÉPLOYÉ (93%) — sert à EXCLURE de la recherche VALUE les matchs déjà couverts par la
# Confiance (elle est PRIORITAIRE : 1 pari/match, confiance d'abord). La value ne se mesure donc QUE sur les
# matchs SANS pari de confiance -> volume/ROI value RÉALISTES (pas de double comptage du même match).
_CONF_MARKETS = frozenset({"Double chance", "Handicap"})
_CONF_PROB_MIN = 80.0
_CONF_COTE_LO, _CONF_COTE_HI = 1.05, 1.30


def _confidence_covered(m) -> bool:
    """True si le match a un candidat du profil CONFIANCE 93% (DC/Handicap, prob≥80, cote 1.05-1.30)."""
    return any(c["market"] in _CONF_MARKETS and c["prob"] >= _CONF_PROB_MIN
               and _CONF_COTE_LO <= c["cote"] <= _CONF_COTE_HI for c in m["cands"])


def select_fast(m, prob_min, cote_lo, cote_hi, markets, ev_floor, tiebreak):
    """1er candidat (dans l'ordre du départage pré-trié) qui passe le filtre. None si aucun."""
    for c in m["ord"][tiebreak]:
        if (c["prob"] >= prob_min and cote_lo <= c["cote"] <= cote_hi
                and (markets is None or c["market"] in markets) and c["ev"] >= ev_floor):
            return c
    return None


def _metrics(picks):
    """picks = liste chronologique de candidats choisis. -> dict de métriques."""
    n = len(picks)
    if not n:
        return {"n": 0}
    wins = sum(1 for c in picks if c["result"] == "won")
    losses = sum(1 for c in picks if c["result"] == "lost")
    ret = 0.0
    for c in picks:
        if c["result"] == "won":
            ret += c["cote"] - 1.0
        elif c["result"] == "lost":
            ret -= 1.0
        # push -> 0
    streak = best = 0
    equity = 0.0; peak = 0.0; maxdd = 0.0
    for c in picks:
        if c["result"] == "won":
            streak += 1; best = max(best, streak); equity += c["cote"] - 1.0
        elif c["result"] == "lost":
            streak = 0; equity -= 1.0
        peak = max(peak, equity); maxdd = min(maxdd, equity - peak)
    dec = wins + losses
    return {"n": n, "wins": wins, "losses": losses,
            "winrate": round(100.0 * wins / dec, 1) if dec else 0.0,
            "roi": round(100.0 * ret / n, 2),
            "avg_cote": round(sum(c["cote"] for c in picks) / n, 2),
            "max_streak": best, "max_dd": round(maxdd, 1),
            "profit": round(ret, 1)}


def evaluate(dataset, params, split_date=None, exclude_conf=False):
    """Évalue un combo sur tout le dataset + splits train/test si split_date fourni. `exclude_conf` :
    ignore les matchs déjà couverts par la Confiance (population VALUE réaliste)."""
    picks = []
    for m in dataset:
        if exclude_conf and _confidence_covered(m):
            continue
        c = select_fast(m, params["prob_min"], params["cote_lo"], params["cote_hi"],
                        params.get("markets"), params["ev_floor"], params["tiebreak"])
        if c:
            picks.append({**c, "date": m["date"]})
    res = {"params": {k: v for k, v in params.items() if k != "markets"}, "all": _metrics(picks)}
    if split_date:
        res["train"] = _metrics([p for p in picks if p["date"] < split_date])
        res["test"] = _metrics([p for p in picks if p["date"] >= split_date])
    # stabilité par mois
    months = sorted({p["date"][:7] for p in picks})
    res["by_month"] = {mo: _metrics([p for p in picks if p["date"][:7] == mo]) for mo in months}
    return res


# --------------------------------------------------------------------------- grid
# Espace de recherche (knobs). Whitelists de marchés = presets nommés.
PROB_MIN = [58, 60, 62, 65, 68, 70, 72, 75, 78, 80, 82, 85]
COTE_LO = [1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.40]
COTE_HI = [1.30, 1.40, 1.50, 1.60, 1.70, 1.85, 2.00, 2.30]
EV_FLOOR = [-0.15, -0.08, -0.05, -0.02, 0.0, 0.03]
TIEBREAK = ["prob", "ev", "cote_lo", "cote_hi"]
MARKET_PRESETS = {
    "all": None,
    "proven6": {"Vainqueur", "Double chance", "Total Under", "Total Over", "Total équipe", "Handicap"},
    "safe4": {"Double chance", "Total Under", "Total équipe", "Handicap"},
    "dc_only": {"Double chance"},
    "dc_under": {"Double chance", "Total Under"},
    "dc_hcap": {"Double chance", "Handicap"},
    "dc_teamtot": {"Double chance", "Total équipe"},
    "under_teamtot": {"Total Under", "Total équipe"},
    "no_over_no_1x2": {"Double chance", "Total Under", "Total équipe", "Handicap", "Tirs cadrés"},
    "shots_dc": {"Double chance", "Tirs cadrés", "Total équipe"},
}


def _score(m, objective):
    """Score d'un combo (sur la métrique ALL) pour classement. Pénalise le faible volume."""
    if m["n"] == 0:
        return -1e9
    import math
    vol = math.sqrt(m["n"])
    if objective == "roi":
        return m["roi"] * vol
    if objective == "winrate":
        return m["winrate"] * vol
    if objective == "streak":
        return m["max_streak"] + m["winrate"] / 100.0
    if objective == "sharpe":          # ROI ajusté au volume, bonus winrate
        return m["roi"] * vol / 10.0 + m["winrate"]
    if objective == "profit":
        return m["profit"]
    return m["roi"] * vol


def iter_space():
    for pm, clo, chi, ev, tb, mp in itertools.product(
            PROB_MIN, COTE_LO, COTE_HI, EV_FLOOR, TIEBREAK, MARKET_PRESETS):
        if clo >= chi:
            continue
        yield {"prob_min": pm, "cote_lo": clo, "cote_hi": chi, "ev_floor": ev,
               "tiebreak": tb, "market_preset": mp, "markets": MARKET_PRESETS[mp]}


def run_grid(dataset, objective, min_n, top, split_date, slice_spec=None, exclude_conf=False):
    combos = list(iter_space())
    if slice_spec:
        i, nsl = (int(x) for x in slice_spec.split("/"))
        combos = [c for k, c in enumerate(combos) if k % nsl == i]
    _ds = [m for m in dataset if not (exclude_conf and _confidence_covered(m))] if exclude_conf else dataset
    scored = []
    for params in combos:
        picks = []
        for m in _ds:
            c = select_fast(m, params["prob_min"], params["cote_lo"], params["cote_hi"],
                            params["markets"], params["ev_floor"], params["tiebreak"])
            if c:
                picks.append({**c, "date": m["date"]})
        allm = _metrics(picks)
        if allm["n"] < min_n:
            continue
        tr = _metrics([p for p in picks if p["date"] < split_date]) if split_date else {}
        te = _metrics([p for p in picks if p["date"] >= split_date]) if split_date else {}
        scored.append({"params": {k: params[k] for k in
                                  ("prob_min", "cote_lo", "cote_hi", "ev_floor", "tiebreak", "market_preset")},
                       "all": allm, "train": tr, "test": te, "score": round(_score(allm, objective), 2)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return {"objective": objective, "min_n": min_n, "n_combos": len(combos),
            "n_qualified": len(scored), "top": scored[:top]}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    g = sub.add_parser("grid")
    g.add_argument("--objective", default="roi",
                   choices=["roi", "winrate", "streak", "sharpe", "profit"])
    g.add_argument("--min-n", type=int, default=60)
    g.add_argument("--top", type=int, default=25)
    g.add_argument("--split", default="2026-08-01")   # train < split <= test
    g.add_argument("--slice", default=None)
    g.add_argument("--exclude-conf", action="store_true")   # population VALUE (hors matchs Confiance)
    e = sub.add_parser("eval")
    e.add_argument("--params", required=True)
    e.add_argument("--split", default="2026-08-01")
    e.add_argument("--exclude-conf", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        ds = build_dataset()
        print(json.dumps({"matches": len(ds),
                          "candidates": sum(len(m["cands"]) for m in ds),
                          "date_min": ds[0]["date"], "date_max": ds[-1]["date"]}, ensure_ascii=False))
        return
    ds = load_dataset()
    if a.cmd == "grid":
        print(json.dumps(run_grid(ds, a.objective, a.min_n, a.top, a.split, a.slice,
                                  exclude_conf=a.exclude_conf), ensure_ascii=False))
    elif a.cmd == "eval":
        params = json.loads(a.params)
        params["markets"] = MARKET_PRESETS.get(params.get("market_preset"), params.get("markets"))
        print(json.dumps(evaluate(ds, params, a.split, exclude_conf=a.exclude_conf), ensure_ascii=False))


if __name__ == "__main__":
    main()
