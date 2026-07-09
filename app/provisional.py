"""Suivi SÉPARÉ (info seule) des PARIS PROVISOIRES — demande user 2026-07-09.

Un « provisoire » = le pari le plus probable affiché sur une ABSTENTION (aucun pari de value retenu).
On ne le joue PAS (value négative/marginale par construction), mais on veut MESURER, chiffres à l'appui,
ce que « jouer chaque provisoire » donnerait — pour VALIDER la discipline d'abstention par les données.

⚠️ TOTALEMENT ISOLÉ du ROI/stats réels : ce module écrit UNIQUEMENT dans `data/provisional_track.json`,
ne touche JAMAIS aux sidecars, à `stat_bet`, à la calibration ni à `list_for`. Mise à plat de 1 unité par
provisoire ; ROI = Σ(cote−1 si gagné, −1 si perdu) / n_réglés.
"""
from __future__ import annotations

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK_PATH = os.path.join(_ROOT, "data", "provisional_track.json")


def _load() -> dict:
    try:
        with open(TRACK_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    tmp = TRACK_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(TRACK_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, TRACK_PATH)
    except OSError:
        pass


def record(sport: str, match_id, home: str, away: str, start: str, name: str,
           comp: str, sel: str, cote) -> None:
    """Enregistre (ou met à jour tant que non réglé) un pari provisoire. Ne garde QUE les paris dont le
    code de règlement est CALCULABLE (sinon impossible à régler -> inutile à suivre). No-op si déjà réglé
    (on ne réécrit pas un résultat figé). Appelé par le scan quand un provisoire est posé."""
    from app.settle_analyst import code_from_pick
    code = code_from_pick(sel or "", sport, home or "", away or "")
    if not code:                                  # non réglable -> on ne le suit pas
        return
    mid = str(match_id)
    d = _load()
    prev = d.get(mid)
    if isinstance(prev, dict) and prev.get("result") in ("won", "lost", "push"):
        return                                    # déjà réglé -> figé (jamais réécrit)
    d[mid] = {"sport": sport, "id": mid, "home": home, "away": away, "start": start,
              "name": name, "comp": comp, "sel": sel, "cote": cote, "code": code,
              "result": (prev or {}).get("result")}
    _save(d)


def settle_pending() -> int:
    """Règle les provisoires en attente dont le match est terminé, via Flashscore (couverture universelle,
    repli LiveScore) + `settle_pick`. Score PARTIEL -> on n'écrit RIEN (jamais de règlement sur du live).
    Renvoie le nombre nouvellement réglé. Sûr à rejouer (idempotent : ne retouche pas un déjà réglé)."""
    from app import flashscore, livescore
    from app.settle_analyst import settle_pick
    d = _load()
    n = 0
    for mid, p in list(d.items()):
        if not isinstance(p, dict) or p.get("result") in ("won", "lost", "push"):
            continue
        sport = p.get("sport")
        q = {"home": p.get("home", ""), "away": p.get("away", ""), "start": p.get("start"),
             "sofa_id": ""}
        score = None
        try:
            score = flashscore.final_score(sport, q) or livescore.final_score(sport, q)
        except Exception:
            score = None
        if not score:
            continue                              # pas de score final fiable -> on retente plus tard
        try:
            res = settle_pick(p.get("code", ""), score)
        except Exception:
            res = None
        if res in ("won", "lost", "push"):
            p["result"] = res
            p["score"] = score.get("label") or ""
            n += 1
    if n:
        _save(d)
    return n


def stats() -> dict:
    """Agrégat INFO-SEULE : {n, settled, won, lost, pending, hit_rate, roi_pct, profit_units, avg_cote}.
    Mise à plat 1 unité. ROI = profit / n_réglés × 100. {} si aucun provisoire suivi."""
    d = _load()
    if not d:
        return {}
    won = lost = push = pending = 0
    profit = 0.0
    cotes = []
    for p in d.values():
        if not isinstance(p, dict):
            continue
        r = p.get("result")
        c = p.get("cote")
        if r == "won":
            won += 1
            if isinstance(c, (int, float)):
                profit += c - 1
                cotes.append(c)
        elif r == "lost":
            lost += 1
            profit -= 1
            if isinstance(c, (int, float)):
                cotes.append(c)
        elif r == "push":
            push += 1
        else:
            pending += 1
    settled = won + lost + push
    graded = won + lost                            # réglés à cote (hors push) = base du ROI
    return {
        "n": len([p for p in d.values() if isinstance(p, dict)]),
        "settled": settled, "won": won, "lost": lost, "push": push, "pending": pending,
        "hit_rate": round(won / graded * 100) if graded else None,
        "roi_pct": round(profit / graded * 100, 1) if graded else None,
        "profit_units": round(profit, 2),
        "avg_cote": round(sum(cotes) / len(cotes), 2) if cotes else None,
    }
