"""CLV (Closing Line Value) — mesure si nos paris BATTENT le marché.

Le CLV compare la cote PRISE (au moment du pari) à la cote de CLÔTURE (juste avant le coup d'envoi,
quand la ligne est la plus efficiente). Prendre systématiquement de MEILLEURES cotes que la clôture
(CLV > 0) est LE meilleur prédicteur de skill à long terme — ça prouve qu'on price mieux que le book
AVANT que le marché ne se corrige.

Ici : `price_pick` retrouve, dans les marchés Unibet d'un match, la cote ACTUELLE de l'issue d'un pari
(à partir de son CODE règlable), et `clv_pct` calcule le CLV. Capture forward-only (cf. mybets) : la
clôture doit être lue AVANT le coup d'envoi (après, le marché pré-match disparaît).

CLV = cote_prise / cote_clôture − 1   (> 0 = on a battu le marché).
"""

from __future__ import annotations


def clv_pct(taken: float, close: float) -> float | None:
    """CLV = cote_prise / cote_clôture − 1. None si entrée invalide."""
    try:
        if taken and close and close > 1:
            return round(taken / close - 1.0, 4)
    except (TypeError, ZeroDivisionError):
        pass
    return None


def _line_eq(outcome_line, want) -> bool:
    try:
        return abs(float(outcome_line) - float(want)) < 0.01
    except (TypeError, ValueError):
        return False


def price_pick(code: str, home: str, away: str, markets: dict | None) -> float | None:
    """Cote ACTUELLE de l'issue correspondant au CODE (WIN/1X2/OVER/UNDER/HCAP/DC/BTTS) dans les
    marchés Unibet `markets` (sortie de app.unibet.markets). None si non trouvée / code non géré.
    Best-effort : couvre les marchés principaux ; les exotiques renvoient None (pas de CLV)."""
    if not code or not markets:
        return None
    parts = code.upper().split()
    kind = parts[0]
    mks = markets.get("markets") or []

    def _find(crit_keywords, match_outcome):
        for m in mks:
            name = (m.get("name") or "").lower()
            if any(k in name for k in crit_keywords):
                for o in m.get("outcomes") or []:
                    if match_outcome(o):
                        return o.get("odds")
        return None

    def _is_home(o):
        lbl = (o.get("label") or "").strip().lower()
        part = (o.get("participant") or "").strip().lower()
        return lbl == "1" or (home and part == home.strip().lower())

    def _is_away(o):
        lbl = (o.get("label") or "").strip().lower()
        part = (o.get("participant") or "").strip().lower()
        return lbl == "2" or (away and part == away.strip().lower())

    if kind in ("WIN", "1X2") and len(parts) > 1:
        side = parts[1]
        keys = ("cotes du match", "temps réglementaire", "temps reglementaire", "vainqueur")
        if side in ("HOME", "1"):
            return _find(keys, _is_home)
        if side in ("AWAY", "2"):
            return _find(keys, _is_away)
        if side == "X":
            return _find(keys, lambda o: (o.get("label") or "").strip().upper() == "X")
        return None

    if kind in ("OVER", "UNDER") and len(parts) > 1:
        want = parts[1]
        pref = "plus" if kind == "OVER" else "moins"
        return _find(("total", "nombre total"),
                     lambda o: (o.get("label") or "").strip().lower().startswith(pref)
                     and _line_eq(o.get("line"), want))

    if kind == "HCAP" and len(parts) >= 3:
        side, want = parts[1], parts[2]
        side_ok = _is_home if side == "HOME" else _is_away
        return _find(("handicap",), lambda o: side_ok(o) and _line_eq(o.get("line"), want))

    if kind == "DC" and len(parts) > 1:
        want = parts[1]                      # 1X / 12 / X2
        return _find(("double chance",), lambda o: (o.get("label") or "").replace(" ", "").upper() == want)

    if kind == "BTTS":
        yes = len(parts) < 2 or parts[1] == "YES"
        target = "oui" if yes else "non"
        return _find(("les deux équipes marquent", "les deux equipes marquent", "btts"),
                     lambda o: (o.get("label") or "").strip().lower() == target)

    return None
