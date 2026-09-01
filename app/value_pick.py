# -*- coding: utf-8 -*-
"""Sélecteur MÉCANIQUE du « pari de value » foot (profil « grassy » trouvé par backtest 2026-08-29).

Backtest (données PROPRES, sans paris de période) sur la population VALUE = les matchs foot SANS pari de
confiance (la Confiance 93% est PRIORITAIRE, cf. app.confidence_pick — 1 pari/match). Profil B retenu (user,
2026-08-29) — le plus rentable ET robuste hors-échantillon :
    TOUS marchés (sauf bans) · confiance BRUTE ≥ 58 · cote 1.40-2.30 · EV ≥ +5 % (vrai edge : proba > cote
    implicite) · départage = cote la plus HAUTE
  -> 75 % de réussite, ROI +21,9 %, cote moyenne 1.63, généralise (train +21 % / test/août +12,5 %). En
  pratique : Total Under/Over/équipe + Handicap + DC (le gate EV+cote écarte le junk cartons/tirs).

Distinct de la Confiance (cotes grasses 1.63 vs 1.14). Sélection MÉCANIQUE dans le VIVIER COMPLET
(fantômes+bets), pas le pick de Claude. Ne s'applique QUE si le match n'a PAS de pari de confiance.
Réversible : VALUE_PICK_ON.
"""
from __future__ import annotations

import glob
import json
import os

from app import analyses
from app import confidence_pick as _cp

VALUE_PICK_ON = True

# Profil value B (backtest données propres). Bornes sur la proba BRUTE. Marchés = TOUS sauf bans.
PROB_MIN = 58.0
COTE_LO = 1.40
COTE_HI = 2.30
EV_MIN = 0.05            # vrai edge value : proba × cote − 1 ≥ +5 %
MARKETS = None          # tous marchés (l'exclusion des bans se fait via _VALUE_BAN_MARKETS)
# EXCLUSION VALUE (user 2026-09-01, « les value perdent trop souvent ») : « Total Over » (Plus de X buts) est le
# SEUL marché PERDANT du value mesuré sur le forward réel (n=9 · 56 % · ROI −9,2 %), alors que tout le reste est
# gagnant (Total Under 67 %/+4,6 % · Total équipe 83 %/+31,8 % · résultat/handicap +). On l'EXCLUT du value
# (Confiance INCHANGÉE). Aligné avec la fragilité récurrente des TOTAUX (combinés, provisoires). Scopé FORWARD,
# réversible (retirer « Total Over » du set). n petit -> à re-mesurer, mais cut défendable + demandé par l'user.
_VALUE_BAN_MARKETS = _cp._BAN_MARKETS | frozenset({"Total Over"})


def match_candidates(d: dict) -> list[dict]:
    """Vivier value = TOUS marchés sauf bans + « Total Over » (réutilise confidence_pick, mode exclusion)."""
    return _cp.match_candidates(d, exclude_markets=_VALUE_BAN_MARKETS)


def resolve_result(d: dict, code: str) -> str | None:
    return _cp.resolve_result(d, code)


def pick_from_candidates(cands: list[dict]) -> dict | None:
    """Profil value B : prob ≥ 58, cote 1.40-2.30, EV ≥ +5 % -> la cote la plus HAUTE (départage prob).
    C'est le pari à EDGE : la value la plus grasse parmi les paris fiables. `cands` déjà sans bans. None si rien."""
    pool = [c for c in cands
            if c["prob"] >= PROB_MIN
            and COTE_LO <= c["cote"] <= COTE_HI
            and (c["prob"] / 100.0 * c["cote"] - 1.0) >= EV_MIN]
    if not pool:
        return None
    return max(pool, key=lambda c: (c["cote"], c["prob"]))


def pick_for_sidecar(d: dict) -> dict | None:
    """Le pari de value d'un match — SEULEMENT s'il n'a PAS de pari de confiance (priorité Confiance)."""
    if not VALUE_PICK_ON:
        return None
    if isinstance(d.get("confidence_bet"), dict) and d["confidence_bet"].get("code"):
        return None                                    # Confiance prioritaire -> pas de value sur ce match
    if _cp.pick_from_candidates(_cp.match_candidates(d)):
        return None                                    # un pari de confiance EXISTE (même pas encore posé)
    return pick_from_candidates(match_candidates(d))


def apply_to_sidecar(d: dict) -> bool:
    """Pose le pari de value FIGÉ dans `d["value_bet"]` (sel/code/prob/cote) si éligible ET pas de confiance.
    Idempotent : ne réécrit PAS un value_bet déjà posé. Retourne True si posé."""
    if d.get("sport") != "foot":
        return False
    if isinstance(d.get("value_bet"), dict) and d["value_bet"].get("code"):
        return False
    c = pick_for_sidecar(d)
    if not c:
        return False
    d["value_bet"] = {"sel": c["sel"], "code": c["code"], "prob": c["prob"], "cote": c["cote"]}
    return True


def apply_for_day(day: str) -> int:
    """Pose le pari de value sur chaque match foot À VENIR du jour sportif `day` (après la Confiance)."""
    n = 0
    for pth in glob.glob(os.path.join(analyses.DIR, "foot_*.json")):
        try:
            d = json.load(open(pth, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            if analyses.status_of(d) != "notstarted":
                continue
        except Exception:
            continue
        from datetime import datetime as _dt
        from app import web as _w
        try:
            _day = _w._sport_date(_w.to_local(_dt.fromisoformat(
                (d.get("start") or "").replace("Z", "+00:00")))).isoformat()
        except Exception:
            _day = (d.get("start") or "")[:10]
        if _day != day:
            continue
        if apply_to_sidecar(d):
            tmp = pth + ".tmp"
            try:
                json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
                os.replace(tmp, pth)
                n += 1
            except OSError:
                pass
    return n


def _validate_history() -> dict:
    """Rejoue le profil value sur les sidecars foot RÉGLÉS SANS confiance (sanity-check vs backtest ~+11 %)."""
    picks = []
    for pth in glob.glob(os.path.join(analyses.DIR, "foot_*.json")):
        try:
            d = json.load(open(pth, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (d.get("result") or {}).get("pick_result"):
            continue
        if _cp.pick_from_candidates(_cp.match_candidates(d)):
            continue                                   # match Confiance -> hors population value
        cands = [c for c in match_candidates(d) if c.get("result") in ("won", "lost", "push")]
        c = pick_from_candidates(cands)
        if c:
            picks.append((d.get("start", "")[:10], c))
    n = len(picks)
    wins = sum(1 for _, c in picks if c["result"] == "won")
    losses = sum(1 for _, c in picks if c["result"] == "lost")
    ret = sum((c["cote"] - 1.0) if c["result"] == "won" else (-1.0 if c["result"] == "lost" else 0.0)
              for _, c in picks)
    dec = wins + losses
    return {"n": n, "wins": wins, "losses": losses,
            "winrate": round(100.0 * wins / dec, 1) if dec else 0.0,
            "roi": round(100.0 * ret / n, 2) if n else 0.0,
            "avg_cote": round(sum(c["cote"] for _, c in picks) / n, 2) if n else 0.0}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(_validate_history(), ensure_ascii=False))
