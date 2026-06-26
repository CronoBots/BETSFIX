"""CLV (Closing Line Value) — brique LÉGÈRE et AUTONOME.

≠ l'ancien module mybets/bankroll (retiré le 2026-06-14, où le CLV n'était qu'un dommage collatéral
de la suppression de la simulation de bankroll). Ici : juste la mesure, rien d'autre.

CLV = est-ce qu'on a pris une MEILLEURE cote que le marché à la CLÔTURE (juste avant le coup d'envoi) ?
C'est le juge d'edge le plus rapide : battre la clôture en moyenne = skill réel, bien avant que le
ROI ne soit significatif. clv = cote_prise / cote_clôture − 1  (>0 = on a battu le marché).

AUCUNE capture réseau ici : on réutilise `odds_history` (déjà alimenté par `_odds_loop`, qui relève
la cote 1X2 jusqu'à ~10 min avant le coup d'envoi -> la DERNIÈRE relève ≈ la clôture). Couvre donc les
paris RÉSULTAT (1X2 / vainqueur / temps réglementaire) — nos marchés les plus joués."""

from __future__ import annotations

import glob
import json
import os
import re


def clv_pct(taken, close) -> float | None:
    """cote_prise / cote_clôture − 1. None si une cote manque/invalide. >0 = meilleure cote que la clôture."""
    try:
        t, c = float(taken), float(close)
    except (TypeError, ValueError):
        return None
    return (t / c - 1.0) if (t > 0 and c > 0) else None


def _result_side(code: str | None) -> str | None:
    """Code de pari RÉSULTAT -> côté 1X2 ('home'/'draw'/'away'). None si ce n'est pas un pari résultat
    (totaux, handicaps, doubles chances… : pas de cote 1X2 de clôture exploitable ici)."""
    p = (code or "").upper().split()
    if not p:
        return None
    if p[0] == "1X2" and len(p) > 1:
        return {"1": "home", "X": "draw", "2": "away"}.get(p[1])
    if p[0] in ("WIN", "REGTIME") and len(p) > 1:
        return {"HOME": "home", "AWAY": "away", "DRAW": "draw"}.get(p[1])
    return None


def _taken_odds(d: dict) -> float | None:
    """Cote PRISE = celle quotée par le pari au scan (ligne `pick` « … @cote »)."""
    m = re.search(r"@\s*([\d]+[.,][\d]+)", d.get("pick") or "")
    return float(m.group(1).replace(",", ".")) if m else None


def _norm(s: str | None) -> set:
    return {w for w in re.findall(r"[a-zà-ÿ0-9]+", (s or "").lower()) if len(w) >= 3}


def pick_clv(d: dict, mv: dict | None) -> float | None:
    """CLV du pari RÉSULTAT du sidecar `d`, étant donné `mv` = odds_history.movement() du match.
    L'orientation home/away est résolue par le NOM de l'équipe pariée (pas le slot), car l'historique
    peut stocker le match dans l'ordre inverse du sidecar. None si : pas un pari résultat, pas de cote
    de clôture (match non commencé / pas de relevé), équipe non retrouvée, ou cote prise introuvable."""
    side = _result_side(d.get("pick_code"))
    if side is None:
        return None
    if not mv or not mv.get("closed"):           # la cote de clôture n'existe qu'une fois le match lancé
        return None
    legs = mv.get("legs") or {}
    if side == "draw":
        leg = legs.get("draw")
    else:
        # côté parié = équipe HOME (1) ou AWAY (2) du SIDECAR -> retrouver son slot dans l'historique
        # par recouvrement de noms (gère l'ordre inversé entre Unibet et le sidecar).
        want = d.get("home") if side == "home" else d.get("away")
        wt = _norm(want)
        hh, ha = _norm(mv.get("home")), _norm(mv.get("away"))
        if wt and (wt & hh) and not (wt & ha):
            leg = legs.get("home")
        elif wt and (wt & ha) and not (wt & hh):
            leg = legs.get("away")
        else:
            leg = legs.get("home" if side == "home" else "away")   # repli : même ordre
    close = leg.get("now") if leg else None       # dernière relève (≤ ~10 min avant le coup d'envoi) = clôture
    return clv_pct(_taken_odds(d), close)


def clv_for_sidecar(d: dict) -> float | None:
    """CLV calculé EN LIVE depuis odds_history — à appeler AU RÈGLEMENT (tant que la cote de clôture
    est encore en mémoire : purge à 48 h). La recherche odds_history est sensible à l'ordre home/away
    -> on tente les deux (pick_clv ré-oriente par le nom). None si non calculable."""
    if _result_side(d.get("pick_code")) is None:
        return None
    from app import odds_history
    sp, h, a = d.get("sport"), d.get("home", ""), d.get("away", "")
    mv = odds_history.movement(sp, h, a) or odds_history.movement(sp, a, h)
    return pick_clv(d, mv)


def clv_stats() -> dict:
    """Bilan CLV sur tous les PRONOS RÉSULTAT réglés : {n, avg_pct, beat_pct}. Lit le CLV STOCKÉ
    `d['clv']` (figé au règlement -> persiste même après la purge d'odds_history). Mesure la qualité
    PRÉDICTIVE du modèle vs le marché (skill), pas seulement les paris joués -> remplit plus vite.
    avg_pct > 0 = on prend en moyenne de meilleures cotes que la clôture = edge réel (juge d'edge
    le plus rapide, bien avant que le ROI ne soit significatif)."""
    from app import analyses
    clvs: list[float] = []
    for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        c = d.get("clv")
        if isinstance(c, (int, float)):      # clv stocké => c'est déjà un prono résultat réglé
            clvs.append(float(c))
    if not clvs:
        return {"n": 0, "avg_pct": None, "beat_pct": None}
    return {"n": len(clvs),
            "avg_pct": round(100 * sum(clvs) / len(clvs), 1),
            "beat_pct": round(100 * sum(1 for c in clvs if c > 0) / len(clvs))}
