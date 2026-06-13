"""Cotes Pinnacle (book « SHARP », marge ~2 %) — la proba la plus proche du VRAI, via l'API guest gratuite.

Pinnacle est LA référence des books sharp : très faible marge, lignes ultra-efficientes (l'argent
intelligent y va). Sa proba de-viggée est le meilleur proxy de la « vraie » proba d'un match -> ancre
de calibrage + détection de VALUE FORTE : si la cote Unibet d'une issue BAT la proba sharp Pinnacle
(EV = proba_sharp × cote_unibet − 1 > 0), c'est de la value robuste (Unibet est en retard sur le sharp).

API guest publique (clé constante, re-extractible du web Pinnacle). Cotes en format AMÉRICAIN.
Best-effort STRICT : timeout court, toute panne -> None.
"""

from __future__ import annotations

import json
import urllib.request

from app.sources import _tok   # tokenisation de noms robuste (réutilisée)

_BASE = "https://guest.api.arcadia.pinnacle.com/0.1/"
_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"          # clé publique du web Pinnacle (guest)
_H = {"User-Agent": "Mozilla/5.0", "x-api-key": _KEY, "Referer": "https://www.pinnacle.com/"}
_SPORT = {"foot": 29, "football": 29, "soccer": 29, "tennis": 33, "basket": 4, "basketball": 4}
_mu_cache: dict = {}     # sportId -> [{id, home, away}]


def _get(path: str):
    try:
        req = urllib.request.Request(_BASE + path, headers=_H)
        return json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace"))
    except Exception:
        return None


def _dec(american) -> float | None:
    """Cote AMÉRICAINE -> cote décimale. None si invalide."""
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return round(a / 100 + 1, 4) if a > 0 else round(100 / abs(a) + 1, 4)


def _matchups(sport: str) -> list:
    sid = _SPORT.get(sport)
    if not sid:
        return []
    if sid in _mu_cache:
        return _mu_cache[sid]
    out = []
    for m in _get(f"sports/{sid}/matchups") or []:
        ps = m.get("participants") or []
        h = next((p.get("name") for p in ps if p.get("alignment") == "home"), None)
        a = next((p.get("name") for p in ps if p.get("alignment") == "away"), None)
        if m.get("id") and h and a:
            out.append({"id": m["id"], "home": h, "away": a})
    _mu_cache[sid] = out
    return out


def _overlap(a: str, b: str) -> int:
    return len(_tok(a) & _tok(b))


def _find(home: str, away: str, sport: str) -> dict | None:
    """Matchup Pinnacle correspondant (par NOMS). None sinon."""
    for m in _matchups(sport):
        sh = _overlap(home, m["home"]) + _overlap(away, m["away"])      # même orientation
        sx = _overlap(home, m["away"]) + _overlap(away, m["home"])      # orientation inversée
        if sh >= 2 or sx >= 2:                                          # ≥1 mot fort de chaque côté
            return m
    return None


def sharp_probs(home: str, away: str, sport: str) -> dict | None:
    """Probas SHARP de-viggées du VAINQUEUR via Pinnacle : {home, away, draw, margin}, alignées sur
    NOTRE home/away (par noms). None si match/cote introuvable. draw=None hors foot."""
    m = _find(home, away, sport)
    if not m:
        return None
    od = _get(f"matchups/{m['id']}/markets/related/straight")
    if not od:
        return None
    ml = next((x for x in od if x.get("type") == "moneyline" and x.get("period") == 0), None)
    if not ml:
        return None
    prices = {p.get("designation"): _dec(p.get("price")) for p in (ml.get("prices") or [])}
    order = [d for d in ("home", "draw", "away") if prices.get(d)]
    inv = [1.0 / prices[d] for d in order]
    s = sum(inv)
    if s <= 0:
        return None
    fair = {d: inv[i] / s for i, d in enumerate(order)}
    # Aligne le « home » Pinnacle sur NOTRE domicile (les équipes peuvent être listées dans l'autre sens).
    hk = "home" if _overlap(home, m["home"]) >= _overlap(home, m["away"]) else "away"
    ak = "away" if hk == "home" else "home"
    return {"home": round(fair.get(hk, 0.0), 3), "away": round(fair.get(ak, 0.0), 3),
            "draw": round(fair["draw"], 3) if "draw" in fair else None,
            "margin": round(s - 1.0, 4)}
