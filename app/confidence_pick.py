# -*- coding: utf-8 -*-
"""Sélecteur MÉCANIQUE du « pari de confiance » foot (profil 93% trouvé par backtest 2026-08-29).

Contexte : le backtest exhaustif sur tout l'historique (tools/backtest_confidence.py, 311 matchs,
1 pari/match) a désigné comme MEILLEUR profil « réussite max » :
    marchés {Double chance, Handicap} · confiance BRUTE ≥ 80 % · cote 1.05-1.30 · le PLUS SÛR d'abord
  -> 93 % de réussite (107-8), série de 26, ROI +6 %, stable juin/juillet/août, test ≥ train.

⚠️ Ce profil ne PEUT PAS venir du pick committé par Claude : l'analyste ne commit qu'~1 pari/match
(table `bets` médiane = 1), et 105/117 de ces paris n'existent que dans les FANTÔMES (`shadow`). On
sélectionne donc mécaniquement dans le VIVIER COMPLET du match (shadow + pari retenu), exactement
comme le combiné du jour (`combo_daily._candidates_for_day`). Aucune analyse Claude ici.

Le module est PUR (aucun effet de bord) : il expose la sélection. Le câblage publication/affichage/
règlement se fait ailleurs. Réversible : le sélecteur n'est utilisé que si CONFIDENCE_PICK_ON.
"""
from __future__ import annotations

import glob
import json
import os

from app import analyses
from app.settle_analyst import code_from_pick

CONFIDENCE_PICK_ON = True

# Profil 93% (backtest). Bornes sur la proba BRUTE de l'analyste (comme le backtest, pas de fuite calib).
MARKETS = frozenset({"Double chance", "Handicap"})
PROB_MIN = 80.0          # confiance brute mini
COTE_LO = 1.05
COTE_HI = 1.30
# « DC 12 » bannie (double chance la plus faible, perd sur le nul — cohérent avec le combiné du jour).
_BLOCK_CODES = frozenset({"DC 12"})

# BUG DE FOND (trouvé 2026-08-29) : les paris de PÉRIODE (1ère mi-temps, quart-temps…) sont MAL codés par
# code_from_pick en total PLEIN MATCH (ex. « moins de 0.5 but 1ère MT » -> TEAMTOT AWAY UNDER 0.5) puis
# RÉGLÉS sur le match entier (résultat FAUX). market_of les classe alors « Total équipe » -> ils passent le
# filtre safe4. On les EXCLUT du vivier de sélection (confiance ET value) via le LIBELLÉ (seule info fiable,
# le code étant déjà corrompu). Ne touche pas le règlement des fantômes (calibration) — juste la SÉLECTION.
import re as _re
_PERIOD_RE = _re.compile(r"mi-?temps|\bmt\b|1[eè]re?\s*(?:p[ée]riode|mt|mi)|2[eè]me?\s*(?:p[ée]riode|mt|mi)"
                         r"|quart-?temps|\bhalf\b|1st\s*half|2nd\s*half", _re.I)


def _is_period_bet(sel: str) -> bool:
    return bool(_PERIOD_RE.search(sel or ""))


# Bans DURS + non-sélectionnables (miroir du harnais backtest) : jamais dans un vivier « tous marchés ».
_BAN_MARKETS = frozenset({"Corners", "Les 2 marquent", "Premier but", "Mi-temps",
                          "Score exact", "Props joueur", "Arrêts gardien"})


def match_candidates(d: dict, markets=None, exclude_markets=None) -> list[dict]:
    """Vivier de candidats d'un match (sidecar `d`) = fantômes `shadow` + pari retenu `bets`, dédup par
    (code), meilleure proba. Code RE-DÉRIVÉ du libellé (règlement à jour). `prob` en % (0-100).
    `markets` : familles AUTORISÉES (inclusion — défaut = MARKETS confiance DC/Handicap). Si `exclude_markets`
    est fourni, mode « tous marchés SAUF ceux-là » (value profil B utilise exclude=_BAN_MARKETS)."""
    _mk = None if exclude_markets is not None else (MARKETS if markets is None else markets)
    preds = list(d.get("shadow") or [])
    for b in (d.get("bets") or []):
        preds.append({"sel": b.get("sel"), "cote": b.get("odds") or b.get("cote"),
                      "prob": b.get("prob"), "code": b.get("code"), "result": b.get("result")})
    best: dict[str, dict] = {}
    for p in preds:
        if _is_period_bet(p.get("sel") or ""):
            continue                                   # pari de PÉRIODE mal codé/réglé -> jamais sélectionné
        code = code_from_pick(p.get("sel") or "", d.get("sport", "foot"),
                              d.get("home", ""), d.get("away", "")).strip()
        if not code or code in _BLOCK_CODES:
            continue
        _m = analyses.market_of(code)
        if _mk is not None and _m not in _mk:
            continue
        if exclude_markets is not None and _m in exclude_markets:
            continue
        pr, co = p.get("prob"), p.get("cote")
        if not isinstance(pr, (int, float)) or not isinstance(co, (int, float)):
            continue
        c = {"sel": p.get("sel"), "code": code, "market": analyses.market_of(code),
             "prob": float(pr), "cote": float(co), "result": p.get("result")}
        prev = best.get(code)
        if prev is None or c["prob"] > prev["prob"]:
            best[code] = c
    return list(best.values())


def pick_from_candidates(cands: list[dict]) -> dict | None:
    """Applique le profil 93% : marché autorisé, prob ≥ 80, cote 1.05-1.30 -> le PLUS SÛR (prob max,
    départage cote la plus basse = favori le plus net). None si rien n'est éligible."""
    pool = [c for c in cands
            if c["market"] in MARKETS
            and c["prob"] >= PROB_MIN
            and COTE_LO <= c["cote"] <= COTE_HI]
    if not pool:
        return None
    return max(pool, key=lambda c: (c["prob"], -c["cote"]))


def pick_for_sidecar(d: dict) -> dict | None:
    """Le pari de confiance d'un match (sidecar déjà chargé), ou None."""
    if not CONFIDENCE_PICK_ON:
        return None
    return pick_from_candidates(match_candidates(d))


def pick_for_match(sport: str, match_id) -> dict | None:
    if sport != "foot":
        return None
    d = analyses.meta(sport, match_id)
    return pick_for_sidecar(d) if d else None


def resolve_result(d: dict, code: str) -> str | None:
    """Résultat (won/lost/push) d'un pari de code donné : lu depuis le fantôme/bets de MÊME code (règlé
    normalement au règlement du match). AGNOSTIQUE au marché (sert Confiance ET Value). None si pas réglé."""
    preds = list(d.get("shadow") or [])
    for b in (d.get("bets") or []):
        preds.append({"sel": b.get("sel"), "result": b.get("result")})
    for p in preds:
        if p.get("result") not in ("won", "lost", "push"):
            continue
        c = code_from_pick(p.get("sel") or "", d.get("sport", "foot"),
                           d.get("home", ""), d.get("away", "")).strip()
        if c == code:
            return p["result"]
    return None


def apply_to_sidecar(d: dict) -> bool:
    """Pose le pari de confiance FIGÉ dans `d["confidence_bet"]` (sel/code/prob/cote) si un candidat 93%
    existe. Ne touche à RIEN d'autre (le pick value de Claude reste intact pour l'affichage/value futur).
    Idempotent : ne réécrit PAS un confidence_bet déjà posé (gel du prix envoyé). Retourne True si (re)posé."""
    if not CONFIDENCE_PICK_ON or d.get("sport") != "foot":
        return False
    if isinstance(d.get("confidence_bet"), dict) and d["confidence_bet"].get("code"):
        return False                                   # déjà figé -> jamais re-prixé (comme published_bet)
    c = pick_from_candidates(match_candidates(d))
    if not c:
        return False
    d["confidence_bet"] = {"sel": c["sel"], "code": c["code"],
                           "prob": c["prob"], "cote": c["cote"]}
    return True


def apply_for_day(day: str) -> int:
    """Pose le pari de confiance sur chaque match foot À VENIR du jour sportif `day`. Renvoie le nombre posé.
    À appeler au scan (après le programme), comme le combiné du jour. FORWARD only (matchs non commencés)."""
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
    """Rejoue le profil sur TOUS les sidecars foot RÉGLÉS (candidats = shadow réglés) et renvoie les
    métriques — DOIT reproduire le backtest (~117 paris, ~93 %). Sanity-check du sélecteur vs backtest."""
    picks = []
    for pth in glob.glob(os.path.join(analyses.DIR, "foot_*.json")):
        try:
            d = json.load(open(pth, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (d.get("result") or {}).get("pick_result"):
            continue
        cands = [c for c in match_candidates(d) if c.get("result") in ("won", "lost", "push")]
        c = pick_from_candidates(cands)
        if c:
            picks.append((d.get("start", "")[:10], c))
    n = len(picks)
    wins = sum(1 for _, c in picks if c["result"] == "won")
    losses = sum(1 for _, c in picks if c["result"] == "lost")
    ret = sum((c["cote"] - 1.0) if c["result"] == "won" else (-1.0 if c["result"] == "lost" else 0.0)
              for _, c in picks)
    streak = best = 0
    for _, c in sorted(picks, key=lambda x: x[0]):
        if c["result"] == "won":
            streak += 1; best = max(best, streak)
        elif c["result"] == "lost":
            streak = 0
    dec = wins + losses
    return {"n": n, "wins": wins, "losses": losses,
            "winrate": round(100.0 * wins / dec, 1) if dec else 0.0,
            "roi": round(100.0 * ret / n, 2) if n else 0.0,
            "avg_cote": round(sum(c["cote"] for _, c in picks) / n, 2) if n else 0.0,
            "max_streak": best}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(_validate_history(), ensure_ascii=False))
