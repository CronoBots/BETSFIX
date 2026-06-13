"""Scores LiveScore (gratuit, JSON, sans clé) — voie de RÈGLEMENT de secours, indépendante de SofaScore.

`prod-public-api.livescore.com` expose, pour les 3 sports, l'agenda d'un jour + le scoreboard détaillé
d'un match : foot = score + mi-temps, basket = score + quart-temps, tennis = sets + jeux par set
(+ tie-breaks). On s'en sert pour RÉGLER les paris quand SofaScore est bloqué et qu'ESPN/FotMob n'a
pas trouvé le match — JSON propre, donc plus simple/robuste que les feeds encodés de Flashscore.

Best-effort STRICT : timeout court, toute panne -> None (le règlement re-tente / via une autre source).
On ne règle QUE sur un statut de fin PROPRE (FT/AET/AP/AOT) : jamais un match en cours, et on écarte
walkover/abandon/forfait (scores partiels) pour éviter tout mauvais règlement.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import timedelta

from app.sources import _start_dt, _teams_match, _tok   # matching de noms robuste (réutilisé)

_BASE = "https://prod-public-api.livescore.com/v1/api/app"
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
       "Referer": "https://www.livescore.com/"}
_SPORT = {"foot": "soccer", "football": "soccer", "soccer": "soccer",
          "tennis": "tennis", "basket": "basketball", "basketball": "basketball"}
# Statuts « match terminé PROPREMENT » : temps plein / prolongation / tirs au but / après OT (basket).
_FINISHED = {"FT", "AET", "AP", "AOT", "AFTER ET", "AFTER PEN.", "FINISHED", "FINAL"}

_index_cache: dict = {}    # (ls_sport, ymd) -> [{id, home, away, status}]


def _get(url: str, timeout: float = 12.0) -> str | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return None


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _index(ls_sport: str, ymd: str) -> list:
    """Agenda d'un jour pour un sport LiveScore : [{id, home, away, status}]. 1 appel caché par jour."""
    key = (ls_sport, ymd)
    if key in _index_cache:
        return _index_cache[key]
    out = []
    raw = _get(f"{_BASE}/date/{ls_sport}/{ymd}/0")
    if raw:
        try:
            j = json.loads(raw)
        except Exception:
            j = {}
        for st in j.get("Stages") or []:
            for ev in st.get("Events") or []:
                t1 = ((ev.get("T1") or [{}])[0]).get("Nm")
                t2 = ((ev.get("T2") or [{}])[0]).get("Nm")
                if ev.get("Eid") and t1 and t2:
                    out.append({"id": str(ev["Eid"]), "home": t1, "away": t2, "status": ev.get("Eps")})
    _index_cache[key] = out
    return out


def _find_event(home: str, away: str, start_iso: str | None, ls_sport: str) -> dict | None:
    """Trouve l'événement LiveScore par NOMS, sur le JOUR du coup d'envoi (±1, fuseaux). None sinon."""
    dt = _start_dt(start_iso or "")
    if not dt:
        return None
    th, ta = _tok(home), _tok(away)
    for k in (0, -1, 1):
        idx = _index(ls_sport, (dt + timedelta(days=k)).strftime("%Y%m%d"))
        for e in idx:
            if _teams_match(home, away, e["home"], e["away"]):
                return e
        for e in idx:                       # repli : un mot fort commun de chaque côté
            fh, fa = _tok(e["home"]), _tok(e["away"])
            if (th & fh and ta & fa) or (th & fa and ta & fh):
                return e
    return None


def final_score(sport: str, d: dict) -> dict | None:
    """Score FINAL d'un match (sidecar : home/away/start) via LiveScore, au FORMAT du règlement
    (cf. settle_analyst._score_from_event) : {home, away, sets_home, sets_away, periods, label, src}.
    None si introuvable ou pas terminé proprement."""
    ls = _SPORT.get(sport)
    if not ls:
        return None
    home, away = d.get("home", ""), d.get("away", "")
    if not (home and away):
        return None
    ev = _find_event(home, away, d.get("start"), ls)
    if not ev:
        return None
    raw = _get(f"{_BASE}/scoreboard/{ls}/{ev['id']}")
    if not raw:
        return None
    try:
        sb = json.loads(raw)
    except Exception:
        return None
    return _parse_scoreboard(ls, sb, fallback_status=ev.get("status"))


def _parse_scoreboard(ls: str, sb: dict, fallback_status: str | None = None) -> dict | None:
    """Parse PUR (sans réseau) d'un scoreboard LiveScore -> score au format du règlement. None si
    le match n'est pas terminé proprement ou si le score est absent. Voir final_score()."""
    eps = (sb.get("Eps") or fallback_status or "").upper()
    if eps not in _FINISHED:
        return None                          # en cours / forfait / abandon -> on ne règle PAS
    hc, ac = _num(sb.get("Tr1")), _num(sb.get("Tr2"))
    if hc is None or ac is None:
        return None
    periods: dict = {}
    if ls == "basketball":
        for i in range(1, 9):                # quart-temps (Q1..Q4, + prolongations éventuelles)
            ph, pa = _num(sb.get(f"Tr1Q{i}")), _num(sb.get(f"Tr2Q{i}"))
            if ph is not None and pa is not None:
                periods[i] = (ph, pa)
    elif ls == "tennis":
        for i in range(1, 6):                # jeux par set (S1..S5)
            ph, pa = _num(sb.get(f"Tr1S{i}")), _num(sb.get(f"Tr2S{i}"))
            if ph is not None and pa is not None:
                periods[i] = (ph, pa)
    elif ls == "soccer":
        h1, a1 = _num(sb.get("Trh1")), _num(sb.get("Trh2"))   # mi-temps (1re période)
        if h1 is not None and a1 is not None:
            periods[1] = (h1, a1)
            periods[2] = (hc - h1, ac - a1)
    base = {"periods": periods, "src": "livescore"}
    if ls == "tennis":                       # Tr1/Tr2 = sets gagnés
        base.update({"home": None, "away": None, "sets_home": hc, "sets_away": ac,
                     "label": f"{hc}-{ac} (sets)"})
    else:
        base.update({"home": hc, "away": ac, "sets_home": None, "sets_away": None,
                     "label": f"{hc}-{ac}"})
    return base
