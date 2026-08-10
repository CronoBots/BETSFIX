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
import time as _time
import urllib.error
import urllib.request

from app.sources import _tok   # tokenisation de noms robuste (réutilisée)

_BASE = "https://guest.api.arcadia.pinnacle.com/0.1/"
_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"          # clé publique du web Pinnacle (guest)
_H = {"User-Agent": "Mozilla/5.0", "x-api-key": _KEY, "Referer": "https://www.pinnacle.com/"}
_SPORT = {"foot": 29, "football": 29, "soccer": 29, "tennis": 33, "basket": 4, "basketball": 4}
_mu_cache: dict = {}     # sportId -> [{id, home, away}]

# REPLI PROXY (2026-07-28) : Cloudflare bloque désormais l'API guest Pinnacle depuis NOTRE IP (403 « you have
# been blocked », vérifié : le direct ET curl_cffi TLS sont bloqués, mais le PROXY résidentiel passe -> blocage
# par IP, pas global). On garde le direct GRATUIT en priorité ; sur un 403, on bascule sur le proxy résidentiel
# (`sofa_proxy`, mutualisé avec SofaScore) ET on met le direct en COOLDOWN : marteler une IP bloquée PROLONGE
# le blocage Cloudflare -> on arrête de taper, ce qui aide l'IP à se dé-flagger. Le direct est re-tenté après le
# cooldown -> récupération AUTOMATIQUE dès que l'IP est débloquée, sans consommer les Go inutilement.
_DIRECT_COOLDOWN = 1800     # s (30 min) : durée pendant laquelle on saute le direct 403 et on passe par proxy
_direct_ok_after = 0.0      # timestamp : avant lui, direct en cooldown -> proxy direct
_last_proxy_status = ""     # dernière raison d'échec du proxy (diagnostic santé) : ex. « 402 — crédit épuisé »


def _direct_blocked() -> bool:
    """True si le direct Pinnacle est en cooldown 403 (donc les appels passent par le proxy)."""
    return _time.time() < _direct_ok_after


def _proxy_url() -> str:
    from app.config import get_settings
    return (getattr(get_settings(), "sofa_proxy", "") or "").strip()


def _get_proxy(path: str):
    """GET via le proxy résidentiel (curl_cffi + impersonation Chrome). None si pas de proxy / échec.
    Enregistre la RAISON de l'échec dans `_last_proxy_status` (diagnostic santé) : un 402 sur le tunnel
    CONNECT = crédit du proxy résidentiel épuisé (iProyal à recharger), à distinguer d'un hoquet réseau."""
    global _last_proxy_status
    proxy = _proxy_url()
    if not proxy:
        _last_proxy_status = "aucun proxy configuré"
        return None
    try:
        from curl_cffi import requests as _cr
        r = _cr.get(_BASE + path, headers=_H, impersonate="chrome",
                    proxies={"http": proxy, "https": proxy}, timeout=20)
        if r.status_code == 200:
            _last_proxy_status = ""
            return json.loads(r.content.decode("utf-8", "replace"))
        _last_proxy_status = f"proxy HTTP {r.status_code}"
    except Exception as e:
        # curl (56) « CONNECT tunnel failed, response 402 » = crédit proxy épuisé (recharger iProyal).
        _last_proxy_status = ("402 — crédit proxy épuisé (recharger iProyal)"
                              if "402" in str(e) else f"proxy {type(e).__name__}")
    return None


def _get(path: str):
    """Cascade Pinnacle Go-consciente : DIRECT gratuit d'abord (sauf cooldown 403) -> repli PROXY résidentiel.
    Un 403 Cloudflare arme le cooldown (on cesse de marteler l'IP bloquée). None si tout échoue."""
    global _direct_ok_after
    now = _time.time()
    if now >= _direct_ok_after:                       # direct pas en cooldown -> on tente le gratuit
        try:
            req = urllib.request.Request(_BASE + path, headers=_H)
            return json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 403:                          # blocage Cloudflare par IP -> cooldown + proxy
                _direct_ok_after = now + _DIRECT_COOLDOWN
        except Exception:
            return None                                # panne réseau ponctuelle (pas un blocage) -> None
    return _get_proxy(path)                            # cooldown actif OU direct 403 -> proxy résidentiel


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


def _fair_pair(pa, pb) -> tuple | None:
    """Deux cotes décimales -> probas de-viggées (marge retirée). None si l'une manque."""
    da, db = _dec(pa), _dec(pb)
    if not da or not db:
        return None
    s = 1.0 / da + 1.0 / db
    if s <= 0:
        return None
    return round((1.0 / da) / s, 3), round((1.0 / db) / s, 3)


def sharp_markets(home: str, away: str, sport: str) -> dict | None:
    """Probas SHARP de-viggées PAR MARCHÉ au-delà du 1X2 (Pinnacle, période MATCH COMPLET = period 0) :
    - `totals`  : {ligne: proba que le TOTAL dépasse la ligne}  (ex. {2.5: 0.54} = 54 % de +2.5 buts)
    - `spreads` : {ligne_domicile: proba}  (handicap de NOTRE domicile, ex. {-1.5: 0.41})
    Aligné sur NOTRE home/away par noms. None si match/marchés introuvables. Best-effort strict.
    But : ancre sharp pour les paris hors-vainqueur (Over/Under, handicaps) — proba_sharp × cote_unibet − 1
    > 0 = value robuste, exactement comme le 1X2 sharp mais sur ces marchés."""
    m = _find(home, away, sport)
    if not m:
        return None
    od = _get(f"matchups/{m['id']}/markets/related/straight")
    if not od:
        return None
    hk_home = _overlap(home, m["home"]) >= _overlap(home, m["away"])   # Pinnacle 'home' == notre domicile ?
    totals: dict = {}
    spreads: dict = {}
    for mk in od:
        if mk.get("period") != 0:
            continue
        prices = mk.get("prices") or []
        t = mk.get("type")
        if t == "total":
            over = next((p for p in prices if p.get("designation") == "over"), None)
            under = next((p for p in prices if p.get("designation") == "under"), None)
            if not over or not under:
                continue
            pts = over.get("points")
            if pts is None:
                pts = mk.get("points")
            fair = _fair_pair(over.get("price"), under.get("price"))
            if pts is not None and fair:
                totals[float(pts)] = fair[0]           # proba de DÉPASSER la ligne (over)
        elif t == "spread":
            ph = next((p for p in prices if p.get("designation") == "home"), None)
            pa = next((p for p in prices if p.get("designation") == "away"), None)
            if not ph or not pa:
                continue
            fair = _fair_pair(ph.get("price"), pa.get("price"))
            line_h = ph.get("points")
            if fair is None or line_h is None:
                continue
            if hk_home:
                spreads[float(line_h)] = fair[0]        # ligne & proba de notre domicile
            else:
                spreads[-float(line_h)] = fair[1]       # Pinnacle home = notre extérieur -> on inverse
    if not totals and not spreads:
        return None
    return {"totals": totals, "spreads": spreads}
