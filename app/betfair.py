"""Cotes Betfair EXCHANGE — book « sharp » de RÉFÉRENCE (un VRAI marché d'échange, plus efficient encore
que Pinnacle). Remplace le scraping Pinnacle-via-proxy (iProyal, fragile) par une VRAIE API : pas de proxy,
pas de scraping, gratuit avec un compte Betfair + une « application key ».

Interface IDENTIQUE à app/pinnacle.py (drop-in) :
  - sharp_probs(home, away, sport)   -> {home, away, draw, margin}  (1X2 de-viggé)
  - sharp_markets(home, away, sport) -> {"totals": {ligne: proba_over}, "spreads": {...}}

Config (.env) :
  BETFAIR_APP_KEY = <ta clé applicative Betfair>
  BETFAIR_USER    = <identifiant Betfair>
  BETFAIR_PASS    = <mot de passe Betfair>

Best-effort STRICT : creds manquants OU toute panne -> None (le scan dégrade proprement, cf. garde-fou).
⚠️ 1re activation : vérifier le login (certains comptes exigent le login CERTIFICAT -> erreur
   CERT_AUTH_REQUIRED ; dans ce cas passer par identitysso-cert + un certificat client, à brancher au besoin).
"""

from __future__ import annotations

import json
import re
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from app.sources import _tok   # tokenisation de noms robuste (mutualisée avec pinnacle/sources)

_IDENTITY = "https://identitysso.betfair.com/api/login"          # login interactif -> session token
_BETTING = "https://api.betfair.com/exchange/betting/rest/v1.0/"  # API paris (lecture des cotes)
_SOCCER = "1"                                                     # eventTypeId Betfair : Football
_SESSION_TTL = 3 * 3600                                           # re-login après ~3 h (token expire)
_session = {"token": "", "exp": 0.0}
_last_status = ""                                                # diagnostic santé (raison d'échec)


def _cfg() -> tuple[str, str, str]:
    from app.config import get_settings
    s = get_settings()
    return ((getattr(s, "betfair_app_key", "") or "").strip(),
            (getattr(s, "betfair_user", "") or "").strip(),
            (getattr(s, "betfair_pass", "") or "").strip())


def configured() -> bool:
    """True si les 3 identifiants Betfair sont renseignés."""
    return all(_cfg())


def _login() -> str:
    """Session token Betfair (login interactif), caché ~3 h. '' si creds manquants / échec."""
    global _last_status
    if _session["token"] and _time.time() < _session["exp"]:
        return _session["token"]
    appkey, user, pw = _cfg()
    if not (appkey and user and pw):
        _last_status = "identifiants Betfair absents (.env)"
        return ""
    try:
        body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
        req = urllib.request.Request(_IDENTITY, data=body, headers={
            "X-Application": appkey, "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace"))
        if r.get("status") == "SUCCESS" and r.get("token"):
            _session["token"], _session["exp"] = r["token"], _time.time() + _SESSION_TTL
            _last_status = ""
            return _session["token"]
        _last_status = f"login {r.get('status')}/{r.get('error') or '?'}"   # ex. CERT_AUTH_REQUIRED
    except Exception as e:
        _last_status = f"login {type(e).__name__}"
    return ""


def _api(method: str, params: dict):
    """POST vers l'API paris (JSON-RPC REST). None si non loggué / panne."""
    global _last_status
    appkey, _, _ = _cfg()
    token = _login()
    if not (appkey and token):
        return None
    try:
        req = urllib.request.Request(
            _BETTING + method + "/", data=json.dumps(params).encode(),
            headers={"X-Application": appkey, "X-Authentication": token,
                     "Content-Type": "application/json", "Accept": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace"))
    except Exception as e:
        _last_status = f"{method} {type(e).__name__}"
        return None


def _overlap(a: str, b: str) -> int:
    return len(_tok(a) & _tok(b))


def _find_event(home: str, away: str, sport: str) -> dict | None:
    """Évènement Betfair correspondant (par NOMS d'équipes). None sinon."""
    if sport != "foot":
        return None
    evs = _api("listEvents", {"filter": {"eventTypeIds": [_SOCCER],
                                         "textQuery": f"{home} {away}"}}) or []
    best, bestsc = None, 1
    for e in evs:
        ev = e.get("event") or {}
        sc = _overlap(home, ev.get("name") or "") + _overlap(away, ev.get("name") or "")
        if sc > bestsc:                                          # ≥1 mot fort de chaque côté
            best, bestsc = ev, sc
    return best


def _best_back(runner: dict) -> float | None:
    """Meilleure cote BACK dispo d'un runner (le prix auquel on peut parier)."""
    atb = ((runner or {}).get("ex") or {}).get("availableToBack") or []
    return atb[0].get("price") if (atb and atb[0].get("price")) else None


def _prices(market_id: str) -> dict:
    """{selectionId: meilleure cote back} pour un marché."""
    book = _api("listMarketBook", {"marketIds": [market_id],
                                   "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}}) or []
    if not book:
        return {}
    out = {}
    for r in book[0].get("runners") or []:
        p = _best_back(r)
        if p:
            out[r.get("selectionId")] = p
    return out


def sharp_probs(home: str, away: str, sport: str) -> dict | None:
    """Probas 1X2 de-viggées (marge retirée) via Betfair Exchange, alignées sur NOTRE home/away.
    {home, away, draw, margin}. draw=None hors foot. None si match/cote introuvable."""
    ev = _find_event(home, away, sport)
    if not ev:
        return None
    cat = _api("listMarketCatalogue", {
        "filter": {"eventIds": [ev["id"]], "marketTypeCodes": ["MATCH_ODDS"]},
        "maxResults": 1, "marketProjection": ["RUNNER_DESCRIPTION"]}) or []
    if not cat:
        return None
    mk = cat[0]
    names = {r.get("selectionId"): (r.get("runnerName") or "") for r in mk.get("runners") or []}
    px = _prices(mk.get("marketId"))
    slots: dict = {}
    for sid, price in px.items():
        nm = names.get(sid, "")
        if "draw" in nm.lower():
            slots["draw"] = price
        elif _overlap(home, nm) >= _overlap(away, nm):
            slots["home"] = price
        else:
            slots["away"] = price
    order = [k for k in ("home", "draw", "away") if k in slots]
    if "home" not in slots or "away" not in slots:
        return None
    inv = [1.0 / slots[k] for k in order]
    s = sum(inv)
    if s <= 0:
        return None
    fair = {k: inv[i] / s for i, k in enumerate(order)}
    return {"home": round(fair.get("home", 0.0), 3), "away": round(fair.get("away", 0.0), 3),
            "draw": round(fair["draw"], 3) if "draw" in fair else None,
            "margin": round(s - 1.0, 4)}


def sharp_markets(home: str, away: str, sport: str) -> dict | None:
    """Probas SHARP de-viggées des TOTAUX buts (Over/Under) via Betfair Exchange. Ancre value hors-1X2.
    {"totals": {ligne: proba de DÉPASSER}, "spreads": {}}. None si introuvable."""
    ev = _find_event(home, away, sport)
    if not ev:
        return None
    codes = ["OVER_UNDER_05", "OVER_UNDER_15", "OVER_UNDER_25", "OVER_UNDER_35", "OVER_UNDER_45"]
    cat = _api("listMarketCatalogue", {
        "filter": {"eventIds": [ev["id"]], "marketTypeCodes": codes},
        "maxResults": 25, "marketProjection": ["RUNNER_DESCRIPTION"]}) or []
    if not cat:
        return None
    totals: dict = {}
    for mk in cat:
        names = {r.get("selectionId"): (r.get("runnerName") or "") for r in mk.get("runners") or []}
        px = _prices(mk.get("marketId"))
        over_p = under_p = line = None
        for sid, price in px.items():
            nm = (names.get(sid, "") or "").lower()
            m = re.search(r"([0-9]+\.?[0-9]*)", nm)
            if m:
                line = float(m.group(1))
            if nm.startswith("over"):
                over_p = price
            elif nm.startswith("under"):
                under_p = price
        if over_p and under_p and line is not None:
            io, iu = 1.0 / over_p, 1.0 / under_p
            s = io + iu
            if s > 0:
                totals[line] = round(io / s, 3)               # proba de DÉPASSER la ligne (over)
    if not totals:
        return None
    return {"totals": totals, "spreads": {}}
