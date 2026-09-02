# -*- coding: utf-8 -*-
"""BACKTEST / BAC À SABLE — rejouer un changement de seuil sur l'historique AVANT de l'appliquer.

100 % SIMULATION EN LECTURE SEULE : ne touche à RIEN en production (ni sélection, ni règlement, ni
affichage). On répond à « et si le seuil X valait Y ? » en rejouant la décision de prod sur les
prédictions déjà accumulées, puis on ne PROPOSE un changement que s'il améliore le ROI **hors échantillon**
de façon **statistiquement défendable** (intervalle de confiance) — jamais sur du bruit.

⚠️ RÉÉCRIT 2026-09-02 — le miroir rejoue désormais les SÉLECTEURS MÉCANIQUES RÉELS de prod
(`app.confidence_pick` + `app.value_pick`, refonte 2026-08-29/09-01), plus l'ancienne porte `_recommend`
qui n'est plus utilisée. L'ancien miroir divergeait de prod (~92 %) car il modélisait `_recommend` alors
que la prod joue le pari MÉCANIQUE (confiance prioritaire, sinon value). Leviers balayés = les VRAIS
paramètres des sélecteurs (bornes de confiance/cote/EV), pas les seuils morts de `_recommend`.

Modèle de rejeu (fidèle à la prod, PAR MATCH) : pour chaque match foot RÉGLÉ, on reconstruit le vivier
(fantômes `shadow` + pari retenu, dédup par code, cotes re-priceées omap), puis :
  1) pari de CONFIANCE prioritaire (`confidence_pick.pick_from_candidates`) ;
  2) sinon pari de VALUE (`value_pick.pick_from_candidates`) sur les matchs SANS confiance.
Le portefeuille rejoué = exactement ce que le produit joue (Confiance ∪ Value, 1 pari/match). Un
`validate_against_prod` compare le rejeu par défaut aux paris MÉCANIQUES FIGÉS (`confidence_bet`/
`value_bet`) : il DOIT reproduire prod (garde-fou ≥ 98 %).
"""
from __future__ import annotations

import contextlib
import glob
import json
import math
import os
from datetime import datetime

from app import analyses
from app import confidence_pick as _cp
from app import value_pick as _vp

# Politique de PROD (défaut) = les VRAIS leviers des sélecteurs mécaniques (lus à l'import, donc toujours
# alignés sur le code de prod). Chaque clé DOIT correspondre à une entrée de `sweeps` (cf. policy_backtest).
DEFAULT_POLICY = {
    "conf_prob_min":  _cp.PROB_MIN,     # confiance : proba brute mini (80)
    "conf_odds_hi":   _cp.COTE_HI,      # confiance : plafond de cote (1.50)
    "value_prob_min": _vp.PROB_MIN,     # value : proba brute mini (68)
    "value_odds_lo":  _vp.COTE_LO,      # value : plancher de cote (1.40)
    "value_odds_hi":  _vp.COTE_HI,      # value : plafond de cote (2.30)
    "value_ev_min":   _vp.EV_MIN,       # value : EV mini (0.05)
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@contextlib.contextmanager
def _policy(pol: dict):
    """Applique une politique en surchargeant TEMPORAIREMENT les globals des sélecteurs, puis restaure.
    Les sélecteurs lisent leurs seuils via ces globals -> les piloter suffit à rejouer une variante."""
    saved = (_cp.PROB_MIN, _cp.COTE_HI, _vp.PROB_MIN, _vp.COTE_LO, _vp.COTE_HI, _vp.EV_MIN)
    try:
        _cp.PROB_MIN = pol["conf_prob_min"]
        _cp.COTE_HI = pol["conf_odds_hi"]
        _vp.PROB_MIN = pol["value_prob_min"]
        _vp.COTE_LO = pol["value_odds_lo"]
        _vp.COTE_HI = pol["value_odds_hi"]
        _vp.EV_MIN = pol["value_ev_min"]
        yield
    finally:
        (_cp.PROB_MIN, _cp.COTE_HI, _vp.PROB_MIN, _vp.COTE_LO, _vp.COTE_HI, _vp.EV_MIN) = saved


def _ts_of(d: dict):
    start = d.get("start")
    if not start:
        return None
    try:
        return datetime.fromisoformat(str(start).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _frozen_pick(d: dict):
    """Pari MÉCANIQUE figé par prod au scan (référence de fidélité) : (tier, code) ou None (abstention)."""
    cb = d.get("confidence_bet")
    if isinstance(cb, dict) and cb.get("code"):
        return ("confiance", str(cb["code"]).strip())
    vb = d.get("value_bet")
    if isinstance(vb, dict) and vb.get("code"):
        return ("value", str(vb["code"]).strip())
    return None


def collect_matches() -> list[dict]:
    """Rassemble les matchs foot RÉGLÉS avec leurs viviers PRÉ-CALCULÉS (indépendants de la politique :
    seuls les PICKS dépendent des seuils, pas les candidats). Chaque item : ts, conf (vivier confiance
    DC/Handicap), val (vivier value tous marchés sauf bans), frozen (pari mécanique figé de prod)."""
    out = []
    for pth in glob.glob(os.path.join(analyses.DIR, "foot_*.json")):
        try:
            d = json.load(open(pth, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (d.get("result") or {}).get("pick_result"):
            continue                                     # non réglé -> hors univers
        out.append({"ts": _ts_of(d),
                    "conf": _cp.match_candidates(d),     # familles MARKETS (DC/Handicap), non balayées
                    "val": _vp.match_candidates(d),      # tous marchés sauf bans (Total Over inclus)
                    "frozen": _frozen_pick(d)})
    return out


def _pick_one(m: dict):
    """Rejoue la décision de prod sur UN match (viviers pré-calculés) sous la politique courante :
    confiance prioritaire, sinon value. Renvoie (tier, pick_dict) ou None (abstention)."""
    c = _cp.pick_from_candidates(m["conf"])
    if c:
        return ("confiance", c)
    v = _vp.pick_from_candidates(m["val"])
    if v:
        return ("value", v)
    return None


def _replay(matches: list[dict], pol: dict) -> list[dict]:
    """Portefeuille rejoué sous `pol` : la liste des paris JOUÉS réglés (tier/cote/result/ts)."""
    picks = []
    with _policy(pol):
        for m in matches:
            res = _pick_one(m)
            if not res:
                continue
            tier, p = res
            if p.get("result") not in ("won", "lost", "push"):
                continue                                 # jambe non réglée -> non scorable
            picks.append({"tier": tier, "cote": _f(p.get("cote")), "result": p["result"], "ts": m["ts"]})
    return picks


def _wilson(won: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return (0.0, 1.0)
    p = won / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / den
    return (max(0.0, c - h), min(1.0, c + h))


def _metrics(picks: list[dict]) -> dict:
    """picks = paris RETENUS par la politique. ROI mise plate 1u ; IC ROI analytique (±1.96·SE)."""
    n = len(picks)
    if n == 0:
        return {"n": 0, "wins": 0, "hit_rate": None, "roi": None, "roi_lo": None, "roi_hi": None,
                "avg_odds": None}
    wins = sum(1 for x in picks if x["result"] == "won")
    profits = [((x["cote"] or 0) - 1.0) if x["result"] == "won"
               else (0.0 if x["result"] == "push" else -1.0) for x in picks]
    mean = sum(profits) / n
    var = sum((pf - mean) ** 2 for pf in profits) / n if n > 1 else 0.0
    se = math.sqrt(var / n) if n > 0 else 0.0
    wlo, whi = _wilson(wins, n)
    return {"n": n, "wins": wins, "hit_rate": round(100 * wins / n, 1),
            "hit_lo": round(100 * wlo, 1), "hit_hi": round(100 * whi, 1),
            "roi": round(100 * mean, 1), "roi_lo": round(100 * (mean - 1.96 * se), 1),
            "roi_hi": round(100 * (mean + 1.96 * se), 1),
            "avg_odds": round(sum((x["cote"] or 0) for x in picks) / n, 2)}


def evaluate(matches: list[dict], pol: dict) -> dict:
    """Rejoue la politique et renvoie les métriques (global + découpage temporel train/test 70/30 par
    date de coup d'envoi — les paris sans date rejoignent le train)."""
    picks = _replay(matches, pol)
    dated = sorted((x for x in picks if x.get("ts")), key=lambda x: x["ts"])
    undated = [x for x in picks if not x.get("ts")]
    cut = int(len(dated) * 0.70)
    train, test = undated + dated[:cut], dated[cut:]
    return {"overall": _metrics(picks), "train": _metrics(train), "test": _metrics(test)}


def sweep(matches: list[dict], param: str, values: list, base: dict | None = None) -> list[dict]:
    """Fait varier UN levier (les autres au niveau prod) et renvoie la courbe (overall+test)."""
    base = dict(base or DEFAULT_POLICY)
    rows = []
    for v in values:
        pol = dict(base)
        pol[param] = v
        ev = evaluate(matches, pol)
        rows.append({"value": v, "overall": ev["overall"], "test": ev["test"]})
    return rows


def validate_against_prod(matches: list[dict] | None = None) -> dict:
    """GARDE-FOU : le rejeu par défaut doit reproduire le pari MÉCANIQUE FIGÉ de prod (confidence_bet/
    value_bet) — mêmes (tier, code). Ne compte que les matchs où prod a effectivement figé un pari
    mécanique (mesure la fidélité de l'INSTRUMENT, pas les matchs pré-refonte sans pari figé)."""
    if matches is None:
        matches = collect_matches()
    agree = total = 0
    mism = []
    with _policy(DEFAULT_POLICY):
        for m in matches:
            frozen = m["frozen"]
            if frozen is None:
                continue                                 # pas de pari mécanique figé -> hors mesure de fidélité
            res = _pick_one(m)
            rep = (res[0], str(res[1].get("code")).strip()) if res else None
            total += 1
            if rep == frozen:
                agree += 1
            elif len(mism) < 8:
                mism.append(f"figé={frozen} rejeu={rep}")
    return {"agree": agree, "total": total,
            "pct": round(100 * agree / total, 1) if total else None, "mismatch": mism}


# Plages de balayage par levier (les VRAIS paramètres des sélecteurs mécaniques).
_SWEEP_VALUES = {
    "conf_prob_min":  [75, 78, 80, 82, 85],
    "conf_odds_hi":   [1.30, 1.40, 1.50, 1.70, 2.00],
    "value_prob_min": [58, 62, 65, 68, 72, 75],
    "value_odds_lo":  [1.30, 1.40, 1.50, 1.60],
    "value_odds_hi":  [2.00, 2.30, 2.60, 3.00],
    "value_ev_min":   [0.0, 0.03, 0.05, 0.08, 0.10],
}


def analyze() -> dict:
    """Analyse standard : politique de PROD (référence) + balayage de chaque levier mécanique + une
    recommandation PRUDENTE (ne propose un changement que si le ROI hors-échantillon s'améliore de façon
    significative : borne basse de l'IC candidat > ROI test de référence, n test ≥ 25)."""
    matches = collect_matches()
    base = evaluate(matches, DEFAULT_POLICY)
    sweeps = {k: sweep(matches, k, v) for k, v in _SWEEP_VALUES.items()}
    ref_test_roi = (base["test"] or {}).get("roi")
    recs = []
    for param, rows in sweeps.items():
        cur = DEFAULT_POLICY[param]
        for r in rows:
            t = r["test"]
            if r["value"] == cur or not t or t.get("n", 0) < 25 or t.get("roi_lo") is None:
                continue
            if ref_test_roi is not None and t["roi_lo"] > ref_test_roi:
                recs.append({"param": param, "from": cur, "to": r["value"],
                             "test_roi": t["roi"], "test_roi_lo": t["roi_lo"], "test_n": t["n"],
                             "ref_test_roi": ref_test_roi,
                             "note": f"{param} {cur}→{r['value']} : ROI test {t['roi']:+}% "
                                     f"(IC bas {t['roi_lo']:+}%, n={t['n']}) > réf {ref_test_roi:+}%"})
    recs.sort(key=lambda x: -(x["test_roi_lo"]))
    return {"universe_n": len(matches), "baseline": base, "sweeps": sweeps,
            "validation": validate_against_prod(matches),
            "recommendations": recs,
            "verdict": ("changement proposé" if recs else
                        "garder la politique actuelle (aucun gain hors-échantillon significatif)")}
