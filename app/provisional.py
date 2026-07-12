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
    # DÉDUP (demande user 2026-07-11 / élargie 2026-07-12) : si le match a DÉJÀ un pari RETENU (combiné ou
    # simple) OU s'il est une JAMBE DU COMBINÉ DU JOUR, il ne doit PAS être suivi EN DOUBLE comme provisoire
    # — sinon une seule erreur se répercute aux deux endroits, avec deux résultats possibles pour un seul
    # match. On n'enregistre pas (et on retire une entrée NON réglée).
    from app import analyses, combo_daily
    if (analyses.has_combo(sport, mid) or analyses.retained_bet(sport, mid) is not None
            or mid in combo_daily.leg_ids()):
        if isinstance(prev, dict) and prev.get("result") is None:
            d.pop(mid, None)
            _save(d)
        return
    d[mid] = {"sport": sport, "id": mid, "home": home, "away": away, "start": start,
              "name": name, "comp": comp, "sel": sel, "cote": cote, "code": code,
              "result": (prev or {}).get("result")}
    _save(d)


def prune_retained() -> int:
    """Retire du suivi les provisoires NON ENCORE RÉGLÉS dont le match a désormais un PARI RETENU (combiné
    ou simple). Un match ne doit être suivi que par UN SEUL type de pari (dédup, demande user 2026-07-11) :
    sinon la même erreur se répercute à deux endroits, avec deux résultats contradictoires possibles pour un
    seul match. Ne touche JAMAIS un provisoire déjà réglé (compteur monotone préservé). Renvoie le nb retiré."""
    from app import analyses, combo_daily
    d = _load()
    _daily_legs = combo_daily.leg_ids()
    removed = 0
    for mid in list(d.keys()):
        p = d.get(mid)
        if not isinstance(p, dict) or p.get("result") in ("won", "lost", "push"):
            continue                              # réglé = figé, jamais retiré (monotone)
        sport = p.get("sport")
        if (analyses.has_combo(sport, mid) or analyses.retained_bet(sport, mid) is not None
                or mid in _daily_legs):
            d.pop(mid, None)
            removed += 1
    if removed:
        _save(d)
    return removed


def settle_pending() -> int:
    """Règle les provisoires en attente dont le match est terminé, via Flashscore (couverture universelle,
    repli LiveScore) + `settle_pick`. Score PARTIEL -> on n'écrit RIEN (jamais de règlement sur du live).
    Renvoie le nombre nouvellement réglé. Sûr à rejouer (idempotent : ne retouche pas un déjà réglé)."""
    from app import flashscore, livescore
    from app.settle_analyst import settle_pick
    prune_retained()          # DÉDUP d'abord : un match devenu retenu (combiné/simple) sort du suivi provisoire
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


def load() -> dict:
    """Snapshot du suivi provisoire (dict brut). Sert à dériver `stats()` ET `entries()` du MÊME état pour
    garantir que le compteur (n/réglés/en attente) et la liste affichée soient TOUJOURS cohérents — sinon
    deux `_load()` séparés peuvent tomber de part et d'autre d'une écriture (scan/règlement) et diverger
    (bug vécu : compteur « 7 » vs liste de 11). Cf. `app/routers/web.py:_provisional_card`."""
    return _load()


def entries(d: dict | None = None) -> list:
    """Liste des provisoires suivis, PLUS RÉCENT (coup d'envoi) en premier : {name, sel, cote, result,
    start, sport}. `result` = None => EN ATTENTE (match pas encore réglé). Sert à AFFICHER le détail (au
    clic sur le bloc) : sinon un provisoire « en attente » n'est visible nulle part une fois le match
    commencé (il a quitté « À venir »). Demande user 2026-07-10. `d` = snapshot partagé (cf. `load()`)."""
    d = _load() if d is None else d
    out = [{"name": p.get("name"), "sel": p.get("sel"), "cote": p.get("cote"),
            "result": p.get("result"), "start": p.get("start"), "sport": p.get("sport")}
           for p in d.values() if isinstance(p, dict)]
    out.sort(key=lambda x: x.get("start") or "", reverse=True)
    return out


def equity_curve(d: dict | None = None) -> list:
    """Série du PROFIT CUMULÉ (unités, mise à plat 1 u) des provisoires RÉGLÉS, ordonnée par coup
    d'envoi, commençant à 0 — pour le graphe d'équité « info seule ». Snapshot partagé avec stats()."""
    d = _load() if d is None else d
    settled = sorted((p for p in d.values()
                      if isinstance(p, dict) and p.get("result") in ("won", "lost")),
                     key=lambda p: p.get("start") or "")
    cur, out = 0.0, [0.0]
    for p in settled:
        c = p.get("cote")
        cur += (c - 1) if (p.get("result") == "won" and isinstance(c, (int, float))) else -1.0
        out.append(round(cur, 2))
    return out


def stats(d: dict | None = None) -> dict:
    """Agrégat INFO-SEULE : {n, settled, won, lost, pending, hit_rate, roi_pct, profit_units, avg_cote}.
    Mise à plat 1 unité. ROI = profit / n_réglés × 100. {} si aucun provisoire suivi. `d` = snapshot
    partagé avec `entries()` (cf. `load()`) → compteur et liste TOUJOURS cohérents."""
    d = _load() if d is None else d
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
