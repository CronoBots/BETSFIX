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
from datetime import datetime, timedelta, timezone

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


def _esd_iso(esd) -> str | None:
    """Convertit un Esd LiveScore (« 20260613190000 », UTC) en ISO. None si invalide."""
    s = str(esd or "")
    if len(s) < 12 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _parse_events(raw: str | None) -> list:
    """Parse un feed LiveScore (date/live) Stages->Events -> [{id, home, away, league, status,
    home_score, away_score, start}]."""
    out = []
    if not raw:
        return out
    try:
        j = json.loads(raw)
    except Exception:
        return out
    for st in j.get("Stages") or []:
        league = st.get("Cnm") or st.get("Snm")
        for ev in st.get("Events") or []:
            t1 = ((ev.get("T1") or [{}])[0]).get("Nm")
            t2 = ((ev.get("T2") or [{}])[0]).get("Nm")
            if ev.get("Eid") and t1 and t2:
                out.append({"id": str(ev["Eid"]), "home": t1, "away": t2, "league": league,
                            "status": ev.get("Eps"), "home_score": ev.get("Tr1"),
                            "away_score": ev.get("Tr2"), "start": _esd_iso(ev.get("Esd"))})
    return out


def _index(ls_sport: str, ymd: str) -> list:
    """Agenda d'un jour pour un sport LiveScore (enrichi). 1 appel caché par (sport, jour)."""
    key = (ls_sport, ymd)
    if key not in _index_cache:
        _index_cache[key] = _parse_events(_get(f"{_BASE}/date/{ls_sport}/{ymd}/0"))
    return _index_cache[key]


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


# ================================================================== API publique (routeur)
def _ymd(day: int = 0) -> str:
    """Date AAAAMMJJ (UTC) décalée de `day` jours (0 = aujourd'hui)."""
    return (datetime.now(timezone.utc) + timedelta(days=day)).strftime("%Y%m%d")


def matches(sport: str, day: int = 0) -> list:
    """Agenda d'un jour (day : 0=aujourd'hui, -1=hier…) : [{id, home, away, league, status,
    home_score, away_score, start}]. [] si sport inconnu."""
    ls = _SPORT.get(sport)
    return _index(ls, _ymd(day)) if ls else []


def live(sport: str) -> list:
    """Matchs EN DIRECT d'un sport (même format que matches()). [] si sport inconnu / aucun live."""
    ls = _SPORT.get(sport)
    return _parse_events(_get(f"{_BASE}/live/{ls}/0")) if ls else []


def scoreboard(sport: str, event_id: str) -> dict | None:
    """Détail d'un match (statut + score par période) : {id, status, finished, home, away,
    home_score, away_score, periods}. None si introuvable. `periods` = mi-temps/quart-temps/jeux
    par set selon le sport. Contrairement à final_score(), renvoie aussi les matchs EN COURS."""
    ls = _SPORT.get(sport)
    if not ls:
        return None
    raw = _get(f"{_BASE}/scoreboard/{ls}/{event_id}")
    if not raw:
        return None
    try:
        sb = json.loads(raw)
    except Exception:
        return None
    periods: dict = {}
    if ls == "basketball":
        rng, fmt = range(1, 9), "Q{}"
    elif ls == "tennis":
        rng, fmt = range(1, 6), "S{}"
    else:                                    # soccer : mi-temps (1re période)
        rng, fmt = (), ""
    for i in rng:
        ph, pa = _num(sb.get(f"Tr1{fmt.format(i)}")), _num(sb.get(f"Tr2{fmt.format(i)}"))
        if ph is not None and pa is not None:
            periods[str(i)] = [ph, pa]
    if ls == "soccer":
        h1, a1 = _num(sb.get("Trh1")), _num(sb.get("Trh2"))
        if h1 is not None and a1 is not None:
            periods["1"] = [h1, a1]
    eps = (sb.get("Eps") or "")
    return {"id": str(event_id), "status": eps, "finished": eps.upper() in _FINISHED,
            "home": ((sb.get("T1") or [{}])[0]).get("Nm"),
            "away": ((sb.get("T2") or [{}])[0]).get("Nm"),
            "home_score": _num(sb.get("Tr1")), "away_score": _num(sb.get("Tr2")),
            "periods": periods}


def find_id(home: str, away: str, start_iso: str | None = None, sport: str = "foot") -> str | None:
    """Résout l'Eid LiveScore depuis les noms (+ sport + jour du coup d'envoi ±1). None si introuvable."""
    ls = _SPORT.get(sport)
    if not ls:
        return None
    ev = _find_event(home, away, start_iso, ls)
    return ev["id"] if ev else None
