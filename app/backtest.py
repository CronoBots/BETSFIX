"""BACKTEST / BAC À SABLE — rejouer un changement de seuil sur l'historique AVANT de l'appliquer.

100 % SIMULATION EN LECTURE SEULE : ne touche à RIEN en production (ni sélection, ni règlement, ni
affichage). On répond à « et si le seuil X valait Y ? » en rejouant la décision de prod sur les
prédictions déjà accumulées, puis on ne PROPOSE un changement que s'il améliore le ROI **hors échantillon**
de façon **statistiquement défendable** (intervalle de confiance) — jamais sur du bruit.

Univers de rejeu = les **prédictions fantômes** (`shadow`) : ~1700 pseudo-paris, chacun avec cote + proba
+ code marché + résultat réglé. C'est 20×+ plus de données que les paris réellement joués → seul moyen
d'estimer un seuil sans surapprendre. La décision rejouée reproduit EXACTEMENT la porte de prod
(`_recommend`) : proba recalibrée ≥ min_conf, cote < plafond, zone mid (1.70) exige mid_conf, puis EV ≥
plancher. Un self-test (`validate_against_prod`) vérifie que la porte par défaut reproduit bien prod.
"""
from __future__ import annotations

import glob
import json
import math
import os
from datetime import datetime

from app import analyses

# Politique de PROD (défaut) — miroir exact de _recommend + garde-fous de cote. ev_floor en POINTS (%).
DEFAULT_POLICY = {
    "min_conf": analyses._MIN_CONF,     # 65
    "ev_floor": 3.0,                    # EV ≥ +3 %
    "odds_cap": 2.00,                   # cote ≥ 2.00 exclue (les grosses cotes saignent)
    "mid_odds": 1.70,                   # zone 1.70–2.00…
    "mid_conf": 72,                     # …exige 72 % de confiance
    "use_calibrated": True,             # confiance RECALIBRÉE (comme prod)
    "apply_exclusions": True,           # marchés écartés per-sport
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _result_str(r):
    """Le résultat du pari analyste est stocké en DICT {'pick_result': ...} (≠ les fantômes, stockés en
    chaîne). Normalise vers 'won'/'lost'/'push' ou None."""
    if isinstance(r, dict):
        r = r.get("pick_result")
    return r if r in ("won", "lost", "push") else None


# Cache des marchés écartés PAR SPORT le temps d'un run (excluded_markets = calibration + perf_breakdown,
# trop coûteux à rappeler pour chacune des ~1700 prédictions × chaque balayage). Réinitialisé par analyze().
_EXC_CACHE: dict[str, set] = {}


def _reset_cache() -> None:
    _EXC_CACHE.clear()


def _excluded(sport: str) -> set:
    if sport not in _EXC_CACHE:
        _EXC_CACHE[sport] = analyses.excluded_markets(sport)
    return _EXC_CACHE[sport]


def decide_one(pred: dict, pol: dict) -> bool:
    """Reproduit la porte de prod pour UNE prédiction (play/abstain). Miroir de _recommend."""
    cote = _f(pred.get("cote"))
    prob = _f(pred.get("cprob") if pol.get("use_calibrated") else pred.get("prob"))
    if cote is None or prob is None:
        return False
    if pol.get("apply_exclusions") and pred.get("market") in _excluded(pred.get("sport")):
        return False
    if prob < pol["min_conf"]:
        return False
    if cote >= pol["odds_cap"]:
        return False
    if cote >= pol["mid_odds"] and prob < pol["mid_conf"]:
        return False
    ev = prob / 100.0 * cote - 1.0
    return ev * 100.0 >= pol["ev_floor"]


def collect(universe: str = "shadow") -> list[dict]:
    """Rassemble les pseudo-paris rejouables depuis les sidecars. Chaque item : sport, market, prob,
    cprob (recalibrée), cote, result, ts (coup d'envoi, pour le découpage temporel). universe :
    'shadow' (fantômes, gros volume) ou 'played' (le pari analyste réel, 1/match)."""
    out = []
    for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        sport = d.get("sport")
        start = d.get("start")
        ts = None
        if start:
            try:
                ts = datetime.fromisoformat(str(start).replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = None
        if universe == "shadow":
            preds = d.get("shadow") or []
            src = [(s.get("prob"), s.get("cote"), s.get("code"), s.get("result")) for s in preds
                   if isinstance(s, dict)]
        else:
            b = analyses.bets_of(sport, d.get("id"))
            src = []
            if b:
                bet = b[0]
                code = None
                try:
                    from app.settle_analyst import code_from_pick
                    code = code_from_pick(bet.get("sel", ""), sport, d.get("home", ""), d.get("away", ""))
                except Exception:
                    code = d.get("pick_code")
                src = [(bet.get("prob"), bet.get("cote"), code, _result_str(d.get("result")))]
        for prob, cote, code, result in src:
            if result not in ("won", "lost", "push") or not code:
                continue
            out.append({"sport": sport, "market": analyses.market_of(code),
                        "prob": _f(prob), "cprob": analyses.calibrated_conf(prob, sport, code),
                        "cote": _f(cote), "result": result, "ts": ts})
    return out


def _wilson(won: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return (0.0, 1.0)
    p = won / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _metrics(played: list[dict]) -> dict:
    """played = prédictions RETENUES par la politique. ROI mise plate 1u ; IC ROI analytique (±1.96·SE)."""
    n = len(played)
    if n == 0:
        return {"n": 0, "wins": 0, "hit_rate": None, "roi": None, "roi_lo": None, "roi_hi": None,
                "avg_odds": None}
    wins = sum(1 for x in played if x["result"] == "won")
    profits = [(x["cote"] - 1.0) if x["result"] == "won" else (0.0 if x["result"] == "push" else -1.0)
               for x in played]
    mean = sum(profits) / n
    var = sum((pf - mean) ** 2 for pf in profits) / n if n > 1 else 0.0
    se = math.sqrt(var / n) if n > 0 else 0.0
    wlo, whi = _wilson(wins, n)
    return {"n": n, "wins": wins, "hit_rate": round(100 * wins / n, 1),
            "hit_lo": round(100 * wlo, 1), "hit_hi": round(100 * whi, 1),
            "roi": round(100 * mean, 1), "roi_lo": round(100 * (mean - 1.96 * se), 1),
            "roi_hi": round(100 * (mean + 1.96 * se), 1),
            "avg_odds": round(sum(x["cote"] for x in played) / n, 2)}


def evaluate(preds: list[dict], pol: dict) -> dict:
    """Applique la politique et renvoie les métriques (global + découpage temporel train/test 70/30)."""
    dated = sorted((x for x in preds if x.get("ts")), key=lambda x: x["ts"])
    undated = [x for x in preds if not x.get("ts")]
    cut = int(len(dated) * 0.70)
    train_src, test_src = undated + dated[:cut], dated[cut:]
    overall = _metrics([x for x in preds if decide_one(x, pol)])
    train = _metrics([x for x in train_src if decide_one(x, pol)])
    test = _metrics([x for x in test_src if decide_one(x, pol)])
    return {"overall": overall, "train": train, "test": test}


def sweep(preds: list[dict], param: str, values: list, base: dict | None = None) -> list[dict]:
    """Fait varier UN paramètre (les autres au niveau prod) et renvoie la courbe (overall+test)."""
    base = dict(base or DEFAULT_POLICY)
    rows = []
    for v in values:
        pol = dict(base); pol[param] = v
        ev = evaluate(preds, pol)
        rows.append({"value": v, "overall": ev["overall"], "test": ev["test"]})
    return rows


def validate_against_prod(sample: int = 60) -> dict:
    """GARDE-FOU : la porte par défaut doit reproduire la décision de prod (retained_bet) sur les paris
    analyste réels. Compare decide_one(défaut) à « retenu par prod ? » match par match."""
    agree = total = 0
    mism = []
    for p in glob.glob(os.path.join(analyses.DIR, "*.json"))[:sample * 3]:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        b = analyses.bets_of(d.get("sport"), d.get("id"))
        if not b:                          # la décision de PORTE ne dépend pas du résultat (gate seul)
            continue
        bet = b[0]
        try:
            from app.settle_analyst import code_from_pick
            code = code_from_pick(bet.get("sel", ""), d.get("sport"), d.get("home", ""), d.get("away", ""))
        except Exception:
            code = d.get("pick_code")
        if not code:
            continue
        pred = {"sport": d.get("sport"), "market": analyses.market_of(code),
                "prob": _f(bet.get("prob")), "cprob": analyses.calibrated_conf(bet.get("prob"), d.get("sport"), code),
                "cote": _f(bet.get("cote")), "result": d.get("result")}
        mine = decide_one(pred, DEFAULT_POLICY)
        # Référence = la porte de PUBLICATION (for_history=False) : applique les exclusions per-sport,
        # exactement comme DEFAULT_POLICY (apply_exclusions=True). (for_history=True les IGNORE -> fausse
        # comparaison sur les marchés récemment écartés.)
        prod = analyses.retained_bet(d.get("sport"), d.get("id"), for_history=False) is not None
        total += 1
        if mine == prod:
            agree += 1
        elif len(mism) < 8:
            mism.append(f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')} : "
                        f"backtest={mine} prod={prod} (conf {pred['cprob']} cote {pred['cote']})")
        if total >= sample:
            break
    return {"agree": agree, "total": total, "pct": round(100 * agree / total, 1) if total else None,
            "mismatch": mism}


def analyze() -> dict:
    """Analyse standard : politique de PROD (référence) + balayages des 3 leviers clés (min_conf, ev_floor,
    odds_cap) + une recommandation PRUDENTE (ne propose un changement que si le ROI hors-échantillon
    s'améliore de façon significative : borne basse de l'IC candidat > ROI test de référence)."""
    _reset_cache()
    preds = collect("shadow")
    base = evaluate(preds, DEFAULT_POLICY)
    sweeps = {
        "min_conf": sweep(preds, "min_conf", [60, 62, 65, 68, 70, 72, 75]),
        "ev_floor": sweep(preds, "ev_floor", [0, 2, 3, 5, 8, 10]),
        "odds_cap": sweep(preds, "odds_cap", [1.70, 1.85, 2.00, 2.20, 2.50]),
    }
    ref_test_roi = (base["test"] or {}).get("roi")
    recs = []
    for param, rows in sweeps.items():
        cur = DEFAULT_POLICY[param]
        for r in rows:
            t = r["test"]
            if r["value"] == cur or not t or t.get("n", 0) < 25 or t.get("roi_lo") is None:
                continue
            # significatif : borne basse candidate > ROI test de référence (amélioration prouvée hors-éch.)
            if ref_test_roi is not None and t["roi_lo"] > ref_test_roi:
                recs.append({"param": param, "from": cur, "to": r["value"],
                             "test_roi": t["roi"], "test_roi_lo": t["roi_lo"], "test_n": t["n"],
                             "ref_test_roi": ref_test_roi,
                             "note": f"{param} {cur}→{r['value']} : ROI test {t['roi']:+}% "
                                     f"(IC bas {t['roi_lo']:+}%, n={t['n']}) > réf {ref_test_roi:+}%"})
    recs.sort(key=lambda x: -(x["test_roi_lo"]))
    return {"universe_n": len(preds), "baseline": base, "sweeps": sweeps,
            "validation": validate_against_prod(),
            "recommendations": recs,
            "verdict": ("changement proposé" if recs else
                        "garder la politique actuelle (aucun gain hors-échantillon significatif)")}
