"""Adaptateur API-Football (SHADOW — pas branché à la prod) — remplaçant candidat du scraping.

Objectif (migration hors-PC) : produire, depuis l'API REST API-Football (IP publique), les MÊMES structures
que le pipeline scrappé actuel, pour pouvoir un jour remplacer Pinnacle+Unibet+livescores SANS changer la
sélection :
  - `sharp_map_1x2(fid)`  -> {"1X2 1": p, "1X2 X": p, "1X2 2": p}  (proba Pinnacle DÉ-VIGGÉE, format sharp_map)
  - `unibet_omap(fid)`    -> {code BETSFIX -> cote Unibet réelle}   (format omap : 1X2 / DC / Over-Under / BTTS)
  - `live_score(fid)`     -> {home, away, elapsed, status, finished} (règlement)
  - `resolve_fixture(...)`-> id du fixture (matching nom + coup d'envoi ±90 min, désambiguïse senior/U19)

⚠️ SHADOW : rien ici n'est importé par le pipeline. Validation via `--compare`/`--reconcile` de
`tools/apifootball_probe.py` avant tout câblage. Décision SportMonks vs API-Football = sur preuve.
⚠️ CLÉ = SECRET : env `BETSFIX_APIFOOTBALL_KEY`, jamais en dur ni commitée.

Écarts connus vs BETSFIX : xG NON couvert (enrichissement top-5) ; handicaps/totaux d'ÉQUIPE pas encore
mappés (mapping incrémental, cf. _OMAP_TODO). Quota Free = 100/j + ~10/min (throttle intégré) ; le live à
l'échelle demande le plan Pro.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from datetime import datetime

import httpx

HOST = "https://v3.football.api-sports.io"
BK_PINNACLE, BK_UNIBET = 4, 16                 # ids stables (/odds/bookmakers)
BET_1X2, BET_DC, BET_OU, BET_BTTS, BET_AH = 1, 12, 5, 8, 4   # Asian Handicap = 4
BET_TOT_HOME, BET_TOT_AWAY = 16, 17                          # totaux d'équipe PLEIN-MATCH ("Total - Home/Away")

_THROTTLE = float(os.environ.get("BETSFIX_APIFOOTBALL_THROTTLE", "1.2"))   # s entre requêtes (Free ~10/min)
_TIMEOUT = 25


def _key() -> str:
    """Clé API-Football : env `BETSFIX_APIFOOTBALL_KEY` (runs CLI) puis `.env` via get_settings()
    (visible SYSTEM ET vince, comme les autres secrets). Jamais en dur, jamais commitée."""
    k = os.environ.get("BETSFIX_APIFOOTBALL_KEY")
    if k:
        return k.strip()
    try:
        from app.config import get_settings
        return (get_settings().apifootball_key or "").strip()
    except Exception:
        return ""


def configured() -> bool:
    return bool(_key())


def _client() -> httpx.Client:
    key = _key()
    if not key:
        raise RuntimeError("Clé API-Football absente (BETSFIX_APIFOOTBALL_KEY en env ou .env).")
    return httpx.Client(headers={"x-apisports-key": key}, timeout=_TIMEOUT)


def _get(cl: httpx.Client, path: str, **params) -> dict:
    """GET throttlé + 1 retry sur 429. ANTI-BLOCAGE FIREWALL (doc : dépasser la limite/MINUTE peut faire
    bannir la clé) : on lit l'en-tête `X-RateLimit-Remaining` (appels restants CETTE minute) et si ≤ 1 on
    attend la fenêtre. Le quota/JOUR est dans `x-ratelimit-requests-remaining` (info seule)."""
    for attempt in range(2):
        r = cl.get(f"{HOST}{path}", params=params)
        if r.status_code == 429 and attempt == 0:
            time.sleep(62)
            continue
        r.raise_for_status()
        try:                                             # respecte la limite par MINUTE -> jamais de ban
            if int(r.headers.get("X-RateLimit-Remaining", "99")) <= 1:
                time.sleep(61)
                return r.json()
        except (ValueError, TypeError):
            pass
        time.sleep(_THROTTLE)
        return r.json()
    r.raise_for_status()
    return r.json()


# Statuts de fixture (table officielle de la doc) — pour un RÈGLEMENT fiable.
FINISHED_STATUS = frozenset({"FT", "AET", "PEN"})            # terminé (FT = temps réglementaire ; AET/PEN au-delà)
INPLAY_STATUS = frozenset({"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"})
NOTPLAYED_STATUS = frozenset({"PST", "CANC", "ABD", "AWD", "WO", "TBD"})


# --- Matching nom + coup d'envoi (repris de la sonde, prouvé fiable senior/U19) ---------------------------
_STOP = {"fc", "cf", "sc", "ac", "cd", "ca", "afc", "if", "bk", "sk", "club", "de", "do", "da", "the",
         "united", "city", "calcio", "sad", "ii", "b", "u21", "u23", "u20", "u19", "reserve", "reserves"}


def _norm(s) -> set:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if t and t not in _STOP}


def _ov(x: set, y: set) -> float:
    return len(x & y) / max(1, min(len(x), len(y))) if x and y else 0.0


def _ts(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def resolve_fixture(cl: httpx.Client, home: str, away: str, ko_iso: str, min_score: float = 0.5) -> dict | None:
    """Retrouve le fixture API-Football d'un match BETSFIX par NOM + coup d'envoi (±90 min pour désambiguïser
    senior/U19 du même jour). Renvoie {id, home, away, league, ts} ou None."""
    kts = _ts(ko_iso)
    day = (ko_iso or "")[:10]
    if not day:
        return None
    nh, na = _norm(home), _norm(away)
    best, bs = None, 0.0
    for x in _get(cl, "/fixtures", date=day).get("response", []):
        xts = _ts(x["fixture"]["date"])
        if kts and xts and abs(kts - xts) > 90 * 60:
            continue
        s = (_ov(nh, _norm(x["teams"]["home"]["name"])) + _ov(na, _norm(x["teams"]["away"]["name"]))) / 2
        if s > bs:
            bs, best = s, x
    if best and bs >= min_score:
        return {"id": best["fixture"]["id"], "home": best["teams"]["home"]["name"],
                "away": best["teams"]["away"]["name"], "league": best["league"]["name"],
                "ts": _ts(best["fixture"]["date"]), "score": round(bs, 3)}
    return None


def raw_odds(cl: httpx.Client, fixture_id: int) -> dict:
    """{bookmaker_id: {bet_id: {value_label: cote(float)}}} pour un fixture."""
    out: dict = {}
    for r in _get(cl, "/odds", fixture=fixture_id).get("response", []):
        for bk in r.get("bookmakers", []):
            bkid = int(bk["id"])
            for b in bk.get("bets", []):
                out.setdefault(bkid, {})[int(b["id"])] = {
                    v["value"]: float(v["odd"]) for v in b.get("values", []) if v.get("odd")}
    return out


def _line(s: str) -> str:
    """Normalise une ligne (« 3.0 » -> « 3 », garde « 2.5/2.75/2.25 ») pour coller au format des codes BETSFIX."""
    s = s.strip()
    return s[:-2] if s.endswith(".0") else s


def sharp_map(odds: dict) -> dict:
    """Proba Pinnacle DÉ-VIGGÉE (marge retirée) au format `sharp_map` BETSFIX — LE PLUS COMPLET possible :
    1X2, Double chance (dérivée du 1X2), et totaux Over/Under (dé-viggés par ligne). {} si Pinnacle absent."""
    pin = odds.get(BK_PINNACLE) or {}
    out: dict = {}
    mw = pin.get(BET_1X2) or {}
    inv = {lab: 1.0 / mw[lab] for lab in ("Home", "Draw", "Away") if mw.get(lab)}
    if len(inv) == 3 and sum(inv.values()) > 0:
        tot = sum(inv.values())
        p1, px, p2 = inv["Home"] / tot, inv["Draw"] / tot, inv["Away"] / tot
        out.update({"1X2 1": round(p1, 4), "1X2 X": round(px, 4), "1X2 2": round(p2, 4),
                    "DC 1X": round(p1 + px, 4), "DC 12": round(p1 + p2, 4), "DC X2": round(px + p2, 4),
                    "WIN 1": round(p1, 4), "WIN 2": round(p2, 4),                       # alias vainqueur
                    "REGTIME HOME": round(p1, 4), "REGTIME DRAW": round(px, 4),         # alias temps réglementaire
                    "REGTIME AWAY": round(p2, 4)})
    lines: dict = {}                                                  # totaux buts, dé-vig PAR ligne
    for lab, odd in (pin.get(BET_OU) or {}).items():
        m = re.match(r"(Over|Under)\s+([0-9.]+)", lab)
        if m and odd:
            lines.setdefault(_line(m.group(2)), {})[m.group(1)] = odd
    for ln, oc in lines.items():
        if "Over" in oc and "Under" in oc:
            io, iu = 1.0 / oc["Over"], 1.0 / oc["Under"]; s = io + iu
            out[f"OVER {ln}"] = round(io / s, 4); out[f"UNDER {ln}"] = round(iu / s, 4)
    # Totaux d'ÉQUIPE plein-match dé-viggés par ligne (Pinnacle "Total - Home/Away" = bets 16/17).
    for betid, side in ((BET_TOT_HOME, "HOME"), (BET_TOT_AWAY, "AWAY")):
        tl: dict = {}
        for lab, odd in (pin.get(betid) or {}).items():
            m = re.match(r"(Over|Under)\s+([0-9.]+)", lab)
            if m and odd:
                tl.setdefault(_line(m.group(2)), {})[m.group(1)] = odd
        for ln, oc in tl.items():
            if "Over" in oc and "Under" in oc:
                io, iu = 1.0 / oc["Over"], 1.0 / oc["Under"]; s = io + iu
                out[f"TEAMTOT {side} OVER {ln}"] = round(io / s, 4)
                out[f"TEAMTOT {side} UNDER {ln}"] = round(iu / s, 4)
    return out


sharp_map_1x2 = sharp_map            # alias rétrocompat (l'ancien nom ne couvrait que le 1X2)

# Marchés BETSFIX NON disponibles sur API-Football (à combler autrement ou accepter) :
# TEAMTOT plein-match EST couvert via "Total - Home/Away" (bets 16/17, Unibet+Pinnacle). Reste :
_OMAP_GAPS = "xG (enrichissement top-5, pas un marché de pari) — seul gap restant."


def unibet_omap(odds: dict) -> dict:
    """Cotes Unibet réelles au format `omap` BETSFIX {code -> cote} — LE PLUS COMPLET : 1X2 (+ alias WIN /
    REGTIME), Double chance, Over/Under buts, BTTS, et HANDICAP asiatique (`HCAP HOME/AWAY <ligne>`).
    ⚠️ `TEAMTOT` plein-match non produit (indisponible côté API-Football — cf. _OMAP_GAPS)."""
    uni = odds.get(BK_UNIBET) or {}
    om: dict = {}
    mw = uni.get(BET_1X2) or {}
    # 1X2 + alias BETSFIX équivalents (WIN vainqueur, REGTIME résultat temps réglementaire) = mêmes cotes.
    for lab, codes in (("Home", ("1X2 1", "WIN 1", "REGTIME HOME")),
                       ("Draw", ("1X2 X", "REGTIME DRAW")),
                       ("Away", ("1X2 2", "WIN 2", "REGTIME AWAY"))):
        if mw.get(lab):
            for c in codes:
                om[c] = mw[lab]
    for lab, code in (("Home/Draw", "DC 1X"), ("Home/Away", "DC 12"), ("Draw/Away", "DC X2")):
        if (uni.get(BET_DC) or {}).get(lab):
            om[code] = uni[BET_DC][lab]
    for lab, cote in (uni.get(BET_OU) or {}).items():                # "Over 2.5" -> "OVER 2.5"
        m = re.match(r"(Over|Under)\s+([0-9.]+)", lab)
        if m:
            om[f"{m.group(1).upper()} {_line(m.group(2))}"] = cote
    for lab, code in (("Yes", "BTTS YES"), ("No", "BTTS NO")):
        if (uni.get(BET_BTTS) or {}).get(lab):
            om[code] = uni[BET_BTTS][lab]
    for lab, cote in (uni.get(BET_AH) or {}).items():                # "Home -2.5" -> "HCAP HOME -2.5"
        m = re.match(r"(Home|Away)\s+([+-]?[0-9.]+)", lab)
        if m and cote:
            om[f"HCAP {m.group(1).upper()} {_line(m.group(2))}"] = cote
    for betid, side in ((BET_TOT_HOME, "HOME"), (BET_TOT_AWAY, "AWAY")):   # "Total - Home/Away" -> TEAMTOT
        for lab, cote in (uni.get(betid) or {}).items():
            m = re.match(r"(Over|Under)\s+([0-9.]+)", lab)
            if m and cote:
                om[f"TEAMTOT {side} {m.group(1).upper()} {_line(m.group(2))}"] = cote
    return om


def match_state(cl: httpx.Client, fixture_id: int) -> dict | None:
    """État COMPLET d'un match pour le RÈGLEMENT (statut officiel + scores). None si introuvable.
    - `reg`  = score TEMPS RÉGLEMENTAIRE (`score.fulltime`) → régler les marchés 90 min (JAMAIS ET/penalty,
      cf. règle BETSFIX « règlement au temps réglementaire »).
    - `goals`= score courant/final (peut INCLURE les prolongations si le match est allé en AET/PEN).
    - `finished`/`in_play`/`not_played` = classification via la table de statuts officielle de la doc.
    ⚠️ 1 seul appel `/fixtures?id=` renvoie aussi events/lineups/stats/players si besoin (batch `ids=` ≤20)."""
    r = (_get(cl, "/fixtures", id=fixture_id).get("response") or [None])[0]
    if not r:
        return None
    st = (r.get("fixture") or {}).get("status") or {}
    short = st.get("short")
    sc = r.get("score") or {}
    ft = sc.get("fulltime") or {}
    g = r.get("goals") or {}
    return {"status": short, "elapsed": st.get("elapsed"),
            "finished": short in FINISHED_STATUS, "in_play": short in INPLAY_STATUS,
            "not_played": short in NOTPLAYED_STATUS,
            "goals": {"home": g.get("home"), "away": g.get("away")},
            "reg": {"home": ft.get("home"), "away": ft.get("away")},
            "halftime": sc.get("halftime"), "extratime": sc.get("extratime"), "penalty": sc.get("penalty")}


def live_score(cl: httpx.Client, fixture_id: int) -> dict | None:
    """Compat : score courant + statut. Pour le règlement 90 min, préférer `match_state(...)['reg']`."""
    ms = match_state(cl, fixture_id)
    if not ms:
        return None
    return {"home": ms["goals"]["home"], "away": ms["goals"]["away"],
            "elapsed": ms["elapsed"], "status": ms["status"], "finished": ms["finished"]}


if __name__ == "__main__":                     # self-test manuel : python -m app.apifootball "Home" "Away" "ISO"
    import sys
    if not configured():
        print("BETSFIX_APIFOOTBALL_KEY absent."); raise SystemExit(2)
    h, a, ko = (sys.argv[1:4] + ["", "", ""])[:3]
    with _client() as _cl:
        f = resolve_fixture(_cl, h, a, ko)
        print("fixture:", f)
        if f:
            od = raw_odds(_cl, f["id"])
            print("sharp_map (Pinnacle dé-viggé):", sharp_map_1x2(od))
            print("omap (Unibet):", unibet_omap(od))
            print("live:", live_score(_cl, f["id"]))
