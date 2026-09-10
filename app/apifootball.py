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

import logging
import os
import re
import time
import unicodedata
from datetime import datetime

import httpx

log = logging.getLogger("betsfix.apifootball")

HOST = "https://v3.football.api-sports.io"
BK_PINNACLE, BK_UNIBET = 4, 16                 # ids stables (/odds/bookmakers)
BET_1X2, BET_DC, BET_OU, BET_BTTS, BET_AH = 1, 12, 5, 8, 4   # Asian Handicap = 4
BET_TOT_HOME, BET_TOT_AWAY = 16, 17                          # totaux d'équipe PLEIN-MATCH ("Total - Home/Away")

# Limites par plan (doc « optimize quota ») — Pro = 7500/j · 300/min · 5/SECONDE. 0.3 s = 3.3/s & 200/min
# (marge sous les DEUX). Free = 100/j · 10/min -> mettre 6.5 en env. `_get` respecte aussi X-RateLimit-Remaining.
_THROTTLE = float(os.environ.get("BETSFIX_APIFOOTBALL_THROTTLE", "0.3"))
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
    """GET throttlé + backoff. ANTI-BLOCAGE FIREWALL (doc : dépasser la limite/MINUTE peut bannir la clé) :
    (1) retry sur HTTP 429 ; (2) lecture du champ `errors` de la réponse AVANT `response` — si `rateLimit`,
    on attend la fenêtre + retry (sinon on log l'erreur applicative, mauvais param/contexte, sans reboucler) ;
    (3) si `X-RateLimit-Remaining` (par minute) ≤ 1, on attend ~1 min. Quota/JOUR = `x-ratelimit-requests-
    remaining` (info)."""
    for attempt in range(3):
        r = cl.get(f"{HOST}{path}", params=params)
        if r.status_code == 429 and attempt < 2:
            time.sleep(62)
            continue
        r.raise_for_status()
        j = r.json()
        errs = j.get("errors")
        if errs:                                         # dict/list NON vide = erreur applicative
            if isinstance(errs, dict) and "rateLimit" in errs and attempt < 2:
                time.sleep(62)
                continue
            log.warning("API-Football %s -> errors=%s", path, errs)
        try:                                             # respecte la limite par MINUTE
            if int(r.headers.get("X-RateLimit-Remaining", "99")) <= 1:
                time.sleep(61)
                return j
        except (ValueError, TypeError):
            pass
        time.sleep(_THROTTLE)
        return j
    r.raise_for_status()
    return r.json()


def _state_from_fixture(r: dict) -> dict:
    """Construit l'état de règlement à partir d'un objet fixture (utilisé par match_state ET le batch)."""
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


def _tok_match(a: str, b: str) -> bool:
    """Deux tokens = même mot ? Exact, OU variante orthographique : 4+ lettres, longueurs proches (±2) et
    préfixe commun ≥ 4 (Bruges↔Brugge, Nurnberg↔Nuremberg…). Le seuil 4 évite les faux (Atletico↔Atlanta)."""
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and abs(len(a) - len(b)) <= 2:
        return len(os.path.commonprefix([a, b])) >= 4
    return False


def _ov(x: set, y: set) -> float:
    """Recouvrement d'ensembles de tokens, avec tolérance aux variantes orthographiques (`_tok_match`)."""
    if not x or not y:
        return 0.0
    small, big = (x, y) if len(x) <= len(y) else (y, x)
    m = sum(1 for a in small if any(_tok_match(a, b) for b in big))
    return m / len(small)


def _ts(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# Cache des fixtures d'un JOUR (métadonnées de résolution : id/noms/logos/ligue/heure — STABLES intra-journée).
# resolve_fixture est appelé par sharp + cotes + logos + règlement -> sans ce cache, chaque match re-fetchait
# `/fixtures?date` (même grosse réponse) : ~28 fetches identiques/scan. TTL court (120 s) = sûr (résolution NS
# stable) et NE cache QUE la RÉSOLUTION (jamais /odds ni les scores : le règlement garde son propre cache frais).
_DAY_FIXTURES_CACHE: dict = {}      # day -> (expire_ts, [fixtures])
_DAY_FIXTURES_TTL = 120


def _day_fixtures(cl: httpx.Client, day: str) -> list:
    hit = _DAY_FIXTURES_CACHE.get(day)
    if hit and hit[0] > time.time():
        return hit[1]
    resp = _get(cl, "/fixtures", date=day).get("response", []) or []
    _DAY_FIXTURES_CACHE[day] = (time.time() + _DAY_FIXTURES_TTL, resp)
    return resp


def resolve_fixture(cl: httpx.Client, home: str, away: str, ko_iso: str, min_score: float = 0.5) -> dict | None:
    """Retrouve le fixture API-Football d'un match BETSFIX par NOM + coup d'envoi (±90 min pour désambiguïser
    senior/U19 du même jour). Renvoie {id, home, away, home_id, away_id, home_logo, away_logo, league, ts} ou None.
    Utilise le cache jour (`_day_fixtures`) -> 1 seul fetch `/fixtures?date` partagé par tous les matchs du scan."""
    kts = _ts(ko_iso)
    day = (ko_iso or "")[:10]
    if not day:
        return None
    nh, na = _norm(home), _norm(away)
    best, bs = None, 0.0
    for x in _day_fixtures(cl, day):
        xts = _ts(x["fixture"]["date"])
        if kts and xts and abs(kts - xts) > 90 * 60:
            continue
        s = (_ov(nh, _norm(x["teams"]["home"]["name"])) + _ov(na, _norm(x["teams"]["away"]["name"]))) / 2
        if s > bs:
            bs, best = s, x
    if best and bs >= min_score:
        return {"id": best["fixture"]["id"], "home": best["teams"]["home"]["name"],
                "away": best["teams"]["away"]["name"],
                "home_id": best["teams"]["home"]["id"], "away_id": best["teams"]["away"]["id"],
                "home_logo": (best["teams"]["home"] or {}).get("logo"),
                "away_logo": (best["teams"]["away"] or {}).get("logo"),
                "league": best["league"]["name"], "league_id": best["league"]["id"],
                "season": best["league"]["season"], "ts": _ts(best["fixture"]["date"]),
                "status": (best["fixture"] or {}).get("status", {}).get("short"),
                "referee": (best["fixture"] or {}).get("referee"), "score": round(bs, 3)}
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
        if not (m and cote):
            continue
        side = m.group(1).upper()
        v = float(m.group(2))
        # ⚠️ CONVENTION HANDICAP EXTÉRIEUR : API-Football et BETSFIX ont des SIGNES OPPOSÉS côté AWAY (vérifié
        # par appariement des cotes : API « Away +0.5 »@1.92 = BETSFIX « HCAP AWAY -0.5 »@1.92). Home identique.
        # Sans cette inversion, le sélecteur Confiance voyait un handicap favorable à une cote de longshot ->
        # picks faux (bug attrapé par le pick-shadow, sinon on aurait cassé le phare à la bascule des cotes).
        if side == "AWAY":
            v = -v
        om[f"HCAP {side} {'+' if v >= 0 else '-'}{_line(f'{abs(v):g}')}"] = cote
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
    return _state_from_fixture(r) if r else None


def match_states_batch(cl: httpx.Client, fixture_ids) -> dict:
    """Règlement de PLUSIEURS matchs en UN appel (`/fixtures?ids=`, ≤20 — doc « optimize quota »). Énorme
    économie pour le règlement/live (20 matchs = 1 requête au lieu de 20). Renvoie {fixture_id: état}."""
    ids = [str(x) for x in fixture_ids if x][:20]
    if not ids:
        return {}
    out = {}
    for r in _get(cl, "/fixtures", ids="-".join(ids)).get("response", []):
        fid = (r.get("fixture") or {}).get("id")
        if fid is not None:
            out[fid] = _state_from_fixture(r)
    return out


_COVERAGE_CACHE: dict = {}


def coverage(cl: httpx.Client, league_id: int, season: int) -> dict | None:
    """Ce que couvre une ligue/saison (odds, predictions, lineups, statistics…) — pour NE PAS appeler ce qui
    n'existe pas (doc « optimize quota »). Semi-statique → mis en cache mémoire. None si introuvable."""
    k = (league_id, season)
    if k in _COVERAGE_CACHE:
        return _COVERAGE_CACHE[k]
    cov = None
    r = (_get(cl, "/leagues", id=league_id, season=season).get("response") or [None])[0]
    if r:
        for s in r.get("seasons", []):
            if s.get("year") == season:
                cov = s.get("coverage")
                break
    _COVERAGE_CACHE[k] = cov
    return cov


def live_score(cl: httpx.Client, fixture_id: int) -> dict | None:
    """Compat : score courant + statut. Pour le règlement 90 min, préférer `match_state(...)['reg']`."""
    ms = match_state(cl, fixture_id)
    if not ms:
        return None
    return {"home": ms["goals"]["home"], "away": ms["goals"]["away"],
            "elapsed": ms["elapsed"], "status": ms["status"], "finished": ms["finished"]}


def final_score(sport: str, d: dict, allow_live: bool = False, cl: httpx.Client | None = None) -> dict | None:
    """Score FINAL d'un match via API-Football — **drop-in de `flashscore.final_score`** (même signature +
    même forme de retour) pour RÉGLER SANS SCRAPING. FOOT uniquement (BETSFIX 100 % foot ; autre sport ou
    non-configuré -> None -> repli scraping conservé). Résolution nom+KO (seuil 0.6, anti-homonyme).
    `home`/`away` = TEMPS RÉGLEMENTAIRE (`score.fulltime`, = règlement des marchés 90 min, même si le match
    est allé en prolongation) ; `periods` = {1: mi-temps, 2: 2e MT}. None si non résolu / PAS terminé (on ne
    règle JAMAIS sur un score partiel), SAUF `allow_live=True` (règlement LIVE d'un OVER irréversible, cf.
    combo_daily) -> score partiel du match EN COURS avec `live=True`. Statuts non-joués (PST/CANC/ABD/WO) ->
    None (le rare walkover reste géré par le repli flashscore)."""
    if sport != "foot" or not configured():
        return None
    home, away, start = d.get("home", ""), d.get("away", ""), d.get("start")
    own = cl is None
    try:
        cl = cl or _client()
        f = resolve_fixture(cl, home, away, start or "", min_score=0.6)
        if not f:
            return None
        ms = match_state(cl, f["id"])
    except Exception:
        return None
    finally:
        if own and cl is not None:
            try:
                cl.close()
            except Exception:
                pass
    if not ms:
        return None

    def _i(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    reg, goals, ht = ms.get("reg") or {}, ms.get("goals") or {}, ms.get("halftime") or {}
    if ms.get("finished"):
        rh, ra = _i(reg.get("home")), _i(reg.get("away"))
        if rh is None or ra is None:                 # repli goals si fulltime absent (rare)
            rh, ra = _i(goals.get("home")), _i(goals.get("away"))
        if rh is None or ra is None:
            return None
        periods = {}
        hh, ha = _i(ht.get("home")), _i(ht.get("away"))
        if hh is not None and ha is not None:
            periods[1] = (hh, ha)
            periods[2] = (rh - hh, ra - ha)          # 2e MT = final réglementaire − mi-temps
        return {"home": rh, "away": ra, "sets_home": None, "sets_away": None,
                "label": f"{rh}-{ra}", "live": False, "src": "apifootball", "periods": periods}
    if allow_live and ms.get("in_play"):             # OVER live irréversible uniquement
        gh, ga = _i(goals.get("home")), _i(goals.get("away"))
        if gh is None or ga is None:
            return None
        return {"home": gh, "away": ga, "sets_home": None, "sets_away": None,
                "label": f"{gh}-{ga} (live)", "live": True, "src": "apifootball", "periods": {}}
    return None


_LIVE_ALL_CACHE: dict = {}      # -> (expire_ts, [matchs live]) : 1 appel /fixtures?live=all partagé
_LIVE_ALL_TTL = 12
# Statut API-Football -> période façon Unibet matchClock (drop-in pour web.live_fields/live_clock).
_PERIOD_ID = {"1H": "FIRST_HALF", "HT": "FIRST_HALF", "2H": "SECOND_HALF",
              "ET": "EXTRA_TIME", "BT": "EXTRA_TIME", "P": "PENALTIES", "LIVE": "SECOND_HALF"}
_RUNNING = frozenset({"1H", "2H", "ET", "LIVE"})


def live_all(cl: httpx.Client) -> list:
    """TOUS les matchs EN DIRECT en UN appel `/fixtures?live=all` (score + minute + statut). Caché ~12 s.
    Alimente le score/minute live de l'affichage (remplace le liveData Unibet). Best-effort -> [] si KO."""
    hit = _LIVE_ALL_CACHE.get("all")
    if hit and hit[0] > time.time():
        return hit[1]
    out = []
    try:
        for x in _get(cl, "/fixtures", live="all").get("response", []) or []:
            fxt = x.get("fixture") or {}
            st = fxt.get("status") or {}
            g = x.get("goals") or {}
            tm = x.get("teams") or {}
            out.append({"home": (tm.get("home") or {}).get("name"), "away": (tm.get("away") or {}).get("name"),
                        "ko": fxt.get("date"),
                        "gh": g.get("home"), "ga": g.get("away"),
                        "elapsed": st.get("elapsed"), "short": st.get("short"),
                        "extra": st.get("extra") if isinstance(st.get("extra"), int) else None,
                        # timestamps epoch du coup d'envoi de chaque mi-temps -> horloge À LA SECONDE
                        # reconstructible (maintenant − début_mi-temps), API-Football ne donne pas les secondes.
                        "periods": fxt.get("periods") or {}})
    except Exception:
        out = (hit[1] if hit else [])
    _LIVE_ALL_CACHE["all"] = (time.time() + _LIVE_ALL_TTL, out)
    return out


def live_clockdata(home: str, away: str, ko_iso: str, live_list: list) -> dict | None:
    """Trouve le match EN DIRECT API-Football correspondant à un match BETSFIX (nom + KO ±90 min) dans
    `live_list` (cf. live_all) et renvoie un objet AU FORMAT `liveData` Unibet (drop-in pour web.live_fields /
    match_select.live_clock) : {score:{home,away}, matchClock:{minute,second,running,periodId}, _af_*}.
    `second`=0 (API-Football ne donne QUE la minute → affichage « 46' », choix user 2026-09-09). None si pas de
    match live correspondant (→ l'appelant garde le liveData Unibet en repli, aucun match ne disparaît)."""
    nh, na, kts = _norm(home), _norm(away), _ts(ko_iso)
    best, bs = None, 0.0
    for x in live_list or []:
        xts = _ts(x.get("ko"))
        if kts and xts and abs(kts - xts) > 90 * 60:
            continue
        s = (_ov(nh, _norm(x.get("home"))) + _ov(na, _norm(x.get("away")))) / 2
        if s > bs:
            bs, best = s, x
    if not best or bs < 0.5:
        return None
    short = best.get("short") or ""
    minute = best["elapsed"] if isinstance(best.get("elapsed"), int) else 0
    second = 0
    # HORLOGE À LA SECONDE reconstruite depuis le timestamp de la mi-temps EN COURS (API-Football ne donne
    # que la minute) : total = maintenant − début_mi-temps (+45 min en 2e MT). Le ticker JS fait défiler ;
    # resync à chaque refresh (cache 12 s). Repli sur `elapsed` (minute, sec 0) si pas de timestamp / dérive
    # aberrante (>3 min d'écart = donnée douteuse) -> jamais d'horloge fantaisiste.
    per = best.get("periods") or {}
    p1, p2 = per.get("first"), per.get("second")
    anchor = p2 if (short == "2H" and p2) else (p1 if short == "1H" else None)
    if anchor:
        tot = int(time.time()) - int(anchor) + (45 * 60 if short == "2H" else 0)
        if tot >= 0 and abs(tot // 60 - minute) <= 3:      # cohérent avec elapsed -> on prend la seconde
            minute, second = tot // 60, tot % 60
    return {"score": {"home": best.get("gh"), "away": best.get("ga")},
            "matchClock": {"minute": minute, "second": second, "running": short in _RUNNING,
                           "periodId": _PERIOD_ID.get(short, "")},
            "_af": True, "_af_status": short, "_af_finished": short in FINISHED_STATUS,
            "_af_extra": best.get("extra")}


# Types de stats API-Football -> clés normalisées du « Live Match Center » (§5bis docs/LIVE_DETECTOR.md).
_LIVE_STAT_MAP = {
    "Ball Possession": "possession", "expected_goals": "xg", "Total Shots": "shots_total",
    "Shots on Goal": "shots_on", "Shots off Goal": "shots_off", "Blocked Shots": "shots_blocked",
    "Shots insidebox": "shots_inbox", "Shots outsidebox": "shots_outbox", "Corner Kicks": "corners",
    "Fouls": "fouls", "Offsides": "offsides", "Goalkeeper Saves": "saves", "Yellow Cards": "yellow",
    "Red Cards": "red", "Total passes": "passes", "Passes accurate": "passes_acc",
    "Passes %": "passes_pct", "goals_prevented": "goals_prevented",
}


def _stat_val(v):
    """Normalise une valeur de stat API-Football : « 54% »->54 (int) · « 1.23 »->1.23 (float) ·
    entier->int · None->None. Garde la chaîne si non numérique."""
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s.endswith("%"):
        try:
            return int(s[:-1])
        except ValueError:
            return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else round(f, 2)
    except ValueError:
        return s or None


def live_match_stats(cl: httpx.Client, fixture_id: int) -> dict | None:
    """« LIVE MATCH CENTER » (§5bis docs/LIVE_DETECTOR.md) — payload COMPLET d'un match pour l'affichage
    premium façon SofaScore. 100 % READ-ONLY (aucune écriture, aucun impact picks/règlement).

    UN SEUL appel `/fixtures?id=` renvoie fixture + teams + goals + events + statistics + players inline
    (doc « optimize quota »). None si fixture introuvable. Structure :
      status/elapsed/finished/in_play · score {home,away} · halftime
      teams {home,away:{id,name,logo}}
      stats {home,away: possession/xg/shots_*/corners/fouls/offsides/saves/cartons/passes...}
      ratings {home,away} = note MOYENNE des joueurs (comme SofaScore) · has_xg (top-5 seulement)
      events [{minute,extra,team,type,detail,player,assist}] triés (buts/cartons/remplacements)
    ⚠️ Les stats API-Football sont MATCH COMPLET (pas de split mi-temps côté stats ; seuls les EVENTS
    portent la minute -> le toggle Tout/1ʳᵉ/2ᵉ du front se fait sur les events). xG dispo top-5 seulement."""
    r = (_get(cl, "/fixtures", id=fixture_id).get("response") or [None])[0]
    if not r:
        return None
    state = _state_from_fixture(r)
    tm = r.get("teams") or {}
    th_id = (tm.get("home") or {}).get("id")
    ta_id = (tm.get("away") or {}).get("id")

    def _side(team_id):
        return "home" if team_id == th_id else "away" if team_id == ta_id else None

    stats = {"home": {}, "away": {}}
    has_xg = False
    for block in (r.get("statistics") or []):
        side = _side((block.get("team") or {}).get("id"))
        if not side:
            continue
        for st in (block.get("statistics") or []):
            key = _LIVE_STAT_MAP.get(st.get("type"))
            if not key:
                continue
            val = _stat_val(st.get("value"))
            stats[side][key] = val
            if key == "xg" and val not in (None, 0):
                has_xg = True

    ratings = {"home": None, "away": None}
    for block in (r.get("players") or []):
        side = _side((block.get("team") or {}).get("id"))
        if not side:
            continue
        rs = []
        for p in (block.get("players") or []):
            g = ((p.get("statistics") or [{}])[0] or {}).get("games") or {}
            try:
                rs.append(float(g.get("rating")))
            except (TypeError, ValueError):
                pass
        if rs:
            ratings[side] = round(sum(rs) / len(rs), 2)

    events = []
    for e in (r.get("events") or []):
        t = e.get("time") or {}
        events.append({"minute": t.get("elapsed"), "extra": t.get("extra"),
                       "team": _side((e.get("team") or {}).get("id")),
                       "type": e.get("type"), "detail": e.get("detail"),
                       "player": (e.get("player") or {}).get("name"),
                       "assist": (e.get("assist") or {}).get("name")})
    events.sort(key=lambda x: ((x["minute"] or 0), (x["extra"] or 0)))

    return {"fixture_id": fixture_id, "status": state["status"], "elapsed": state["elapsed"],
            "finished": state["finished"], "in_play": state["in_play"],
            "score": state["goals"], "halftime": state["halftime"],
            "teams": {"home": {"id": th_id, "name": (tm.get("home") or {}).get("name"),
                               "logo": (tm.get("home") or {}).get("logo")},
                      "away": {"id": ta_id, "name": (tm.get("away") or {}).get("name"),
                               "logo": (tm.get("away") or {}).get("logo")}},
            "stats": stats, "ratings": ratings, "has_xg": has_xg, "events": events}


# --- PREMIER BUT / PREMIER BUTEUR (events API-Football) — drop-in de sources.first_goal_side/first_scorer ---
def _goal_events(d: dict, cl: httpx.Client | None = None) -> list | None:
    """Buts d'un match (events `/fixtures?id=`) triés chronologiquement -> [{side,player,own}].
    None si match introuvable/indispo (le règlement re-tentera) ; [] si aucun but (0-0). Le but contre son
    camp (`Own Goal`) est attribué à l'équipe qui EN PROFITE ; les penalties ratés sont ignorés."""
    if not configured():
        return None
    home, away, start = d.get("home", ""), d.get("away", ""), d.get("start")
    if not (home and away):
        return None
    own = cl is None
    try:
        cl = cl or _client()
        f = resolve_fixture(cl, home, away, start or "", min_score=0.6)
        if not f:
            return None
        r = (_get(cl, "/fixtures", id=f["id"]).get("response") or [None])[0]
    except Exception:
        return None
    finally:
        if own and cl is not None:
            try:
                cl.close()
            except Exception:
                pass
    if not r:
        return None
    tm = r.get("teams") or {}
    th_id, ta_id = (tm.get("home") or {}).get("id"), (tm.get("away") or {}).get("id")
    goals = []
    for e in (r.get("events") or []):
        if e.get("type") != "Goal":
            continue
        detail = e.get("detail") or ""
        if detail == "Missed Penalty":          # penalty raté ≠ but
            continue
        tid = (e.get("team") or {}).get("id")
        side = "HOME" if tid == th_id else "AWAY" if tid == ta_id else None
        if side is None:
            continue
        if detail == "Own Goal":                 # compté pour l'ADVERSAIRE
            side = "AWAY" if side == "HOME" else "HOME"
        t = e.get("time") or {}
        goals.append({"order": (t.get("elapsed") or 0) * 100 + (t.get("extra") or 0), "side": side,
                      "player": (e.get("player") or {}).get("name") or "", "own": detail == "Own Goal"})
    goals.sort(key=lambda g: g["order"])
    return goals


def first_goal_side(d: dict, cl: httpx.Client | None = None) -> str | None:
    """Côté du PREMIER but du match : 'HOME'/'AWAY', '' si 0-0, None si indispo (règlement re-tente)."""
    goals = _goal_events(d, cl)
    if goals is None:
        return None
    return goals[0]["side"] if goals else ""


def first_scorer(d: dict, cl: httpx.Client | None = None) -> str | None:
    """Nom du PREMIER BUTEUR du match (matching STRICT côté caller) : '' si 0-0, None si indispo. Un but
    contre son camp reste le 1er but chronologique -> le pari sur un joueur nommé perd (token mismatch)."""
    goals = _goal_events(d, cl)
    if goals is None:
        return None
    return goals[0]["player"] if goals else ""


# --- ENRICHISSEMENT (remplace FotMob / Flashscore / Sportradar) — fixture-scopé, marche sur tous les plans ---
def injuries(cl: httpx.Client, fixture_id: int) -> list:
    """Joueurs ABSENTS/INCERTAINS d'un match (remplace « blessés » FotMob). `type` = 'Missing Fixture'
    (ne jouera pas) ou 'Questionable' (peut-être). Dispo depuis avril 2021, MAJ /4 h."""
    out = []
    for x in _get(cl, "/injuries", fixture=fixture_id).get("response", []):
        p = x.get("player") or {}
        t = x.get("team") or {}
        out.append({"player": p.get("name"), "team": t.get("name"), "team_id": t.get("id"),
                    "type": p.get("type"), "reason": p.get("reason")})
    return out


def lineups(cl: httpx.Client, fixture_id: int) -> list:
    """Compositions (formation, coach, XI, remplaçants) — remplace les compos Flashscore/FotMob.
    Dispo 20-40 min avant le KO (selon coverage de la ligue)."""
    out = []
    for x in _get(cl, "/fixtures/lineups", fixture=fixture_id).get("response", []):
        out.append({"team": (x.get("team") or {}).get("name"), "formation": x.get("formation"),
                    "coach": (x.get("coach") or {}).get("name"),
                    "xi": [(e.get("player") or {}).get("name") for e in (x.get("startXI") or [])],
                    "subs": [(e.get("player") or {}).get("name") for e in (x.get("substitutes") or [])]})
    return out


def predictions(cl: httpx.Client, fixture_id: int) -> dict | None:
    """Prédiction maison API-Football (Poisson + forme + stats + H2H ; N'utilise PAS les cotes) : gagnant,
    win-or-draw, conseil, %, over/under, comparaison (form/att/def/poisson/h2h/goals). BONUS d'enrichissement,
    dispo 21 j avant le match. À traiter comme un SIGNAL informatif, PAS comme l'ancre sharp."""
    r = (_get(cl, "/predictions", fixture=fixture_id).get("response") or [None])[0]
    if not r:
        return None
    pr = r.get("predictions") or {}
    cmp = r.get("comparison") or {}
    return {"winner": (pr.get("winner") or {}).get("name"), "win_or_draw": pr.get("win_or_draw"),
            "advice": pr.get("advice"), "percent": pr.get("percent"),
            "under_over": pr.get("under_over"), "goals": pr.get("goals"),
            "comparison": {k: cmp.get(k) for k in ("form", "att", "def", "poisson_distribution", "h2h", "goals", "total")}}


def team_stats(cl: httpx.Client, team_id: int, league_id: int, season: int) -> dict | None:
    """Stats d'une équipe sur la saison (forme, buts pour/contre, clean sheets, séries) — remplace la forme
    Sportradar/FotMob. Nécessite plan Pro+ (saison courante). Renvoie form / goals / clean_sheet / fixtures."""
    r = _get(cl, "/teams/statistics", team=team_id, league=league_id, season=season).get("response") or {}
    if not r:
        return None
    goals = r.get("goals") or {}
    # % over 2.5 côté « pour » = tendance offensive/tempo (comparable au « +2,5 buts X% » de Sportradar)
    ou = (((goals.get("for") or {}).get("under_over")) or {}).get("2.5") or {}
    tot = (ou.get("over") or 0) + (ou.get("under") or 0)
    over25 = round(100 * ou["over"] / tot) if tot else None
    return {"form": r.get("form"), "goals": goals, "fixtures": r.get("fixtures"),
            "clean_sheet": r.get("clean_sheet"), "failed_to_score": r.get("failed_to_score"),
            "streak": (r.get("biggest") or {}).get("streak"), "over25": over25}


def standings(cl: httpx.Client, league_id: int, season: int) -> list:
    """Classement d'une ligue (rang, points, forme, buts) — remplace le classement Sportradar/Flashscore.
    Nécessite plan Pro+ (saison courante). Renvoie [{rank, team, points, goalsDiff, form}]."""
    resp = _get(cl, "/standings", league=league_id, season=season).get("response") or []
    if not resp:
        return []
    out = []
    for group in (resp[0].get("league") or {}).get("standings") or []:
        for row in group:
            out.append({"rank": row.get("rank"), "team": (row.get("team") or {}).get("name"),
                        "team_id": (row.get("team") or {}).get("id"), "points": row.get("points"),
                        "goalsDiff": row.get("goalsDiff"), "form": row.get("form"),
                        "status": row.get("status"), "description": row.get("description"),
                        "home": row.get("home"), "away": row.get("away")})   # splits dom/ext = signal paris
    return out


def h2h(cl: httpx.Client, home_id: int, away_id: int, last: int = 6) -> list:
    """Historique des confrontations directes (remplace le H2H FotMob/Flashscore). Derniers `last` matchs."""
    out = []
    for x in _get(cl, "/fixtures/headtohead", h2h=f"{home_id}-{away_id}", last=last).get("response", []):
        g = x.get("goals") or {}
        tm = x.get("teams") or {}
        out.append({"date": (x.get("fixture") or {}).get("date"),
                    "home": (tm.get("home") or {}).get("name"), "away": (tm.get("away") or {}).get("name"),
                    "score": f"{g.get('home')}-{g.get('away')}"})
    return out


def team_xg_form(cl: httpx.Client, team_id: int, n: int = 5) -> dict | None:
    """Moyenne xG CRÉÉS / CONCÉDÉS sur les N derniers matchs FINIS d'une équipe — REMPLACE Understat.

    xG post-match via `/fixtures/statistics` (type « expected_goals », dispo top-5, délai ~qq h post-FT).
    Économe : 1 appel pour la liste des N derniers + 1 appel batch `ids=` (stats inline). Renvoie
    {xg, xga, n} (moyennes) ou None si aucun xG dispo (ligue hors top-5 / matchs trop récents).
    Métrique = moyenne xG des 5 derniers (a remplacé l'ancien xG Understat, retiré 2026-09-10).
    """
    resp = _get(cl, "/fixtures", team=team_id, last=n).get("response") or []
    fids = [str((x.get("fixture") or {}).get("id")) for x in resp if (x.get("fixture") or {}).get("id")]
    if not fids:
        return None
    xgs, xgas = [], []
    for x in _get(cl, "/fixtures", ids="-".join(fids[:20])).get("response") or []:
        by_team = {}
        for s in (x.get("statistics") or []):
            tid = (s.get("team") or {}).get("id")
            xg = next((v.get("value") for v in (s.get("statistics") or [])
                       if "expected" in str(v.get("type") or "").lower()), None)
            if tid is not None and xg is not None:
                try:
                    by_team[tid] = float(xg)
                except (TypeError, ValueError):
                    pass
        if team_id in by_team:
            xgs.append(by_team[team_id])
            opp = next((t for t in by_team if t != team_id), None)   # xGA = xG de l'adversaire
            if opp is not None:
                xgas.append(by_team[opp])
    if not xgs:
        return None
    return {"xg": round(sum(xgs) / len(xgs), 2),
            "xga": round(sum(xgas) / len(xgas), 2) if xgas else None, "n": len(xgs)}


def sharp_anchor(cl: httpx.Client, home: str, away: str, ko_iso: str) -> dict | None:
    """Ancre SHARP Pinnacle via API-Football, aux FORMATS `pinnacle.sharp_probs`/`sharp_markets` (drop-in).
    Renvoie {"sp": {home,draw,away,margin}, "smk": {totals:{ligne:proba_over}, spreads:{}}} ou None.
    Même dé-vig (normalisation multiplicative) → PROUVÉ identique à iProyal au MÊME instant (2026-09-09,
    écart 0/0 pt sur 6 matchs ; les écarts historiques ~23 pt = pur timing/line-movement, pas un bug).
    ⚠️ Orientation : le fixture API-Football a home=vrai domicile = notre home → alignement naturel."""
    f = resolve_fixture(cl, home, away, ko_iso)
    if not f:
        return None
    pin = (raw_odds(cl, f["id"]).get(BK_PINNACLE)) or {}
    mw = pin.get(BET_1X2) or {}
    inv = {k: 1.0 / mw[k] for k in ("Home", "Draw", "Away") if mw.get(k)}
    sp = None
    if len(inv) == 3 and sum(inv.values()) > 0:
        s = sum(inv.values())
        sp = {"home": round(inv["Home"] / s, 3), "draw": round(inv["Draw"] / s, 3),
              "away": round(inv["Away"] / s, 3), "margin": round(s - 1.0, 4)}
    totals: dict = {}
    lines: dict = {}
    for lab, odd in (pin.get(BET_OU) or {}).items():
        m = re.match(r"(Over|Under)\s+([0-9.]+)", lab)
        if m and odd:
            lines.setdefault(float(m.group(2)), {})[m.group(1)] = odd
    for ln, oc in lines.items():
        if oc.get("Over") and oc.get("Under"):
            io, iu = 1.0 / oc["Over"], 1.0 / oc["Under"]
            totals[ln] = round(io / (io + iu), 3)
    if sp is None and not totals:
        return None
    return {"sp": sp, "smk": {"totals": totals, "spreads": {}}}


def enrich_facts(cl: httpx.Client, home: str, away: str, ko_iso: str) -> tuple[list[str], dict]:
    """Bloc de faits d'ENRICHISSEMENT API-Football pour un match (forme + moy buts + over% + série + xG +
    H2H + arbitre + prédiction Poisson + classement). REMPLACE Flashscore/Sportradar/Understat.
    ⚠️ NE remplace PAS FotMob (blessés large / compos probables / météo = gardés en hybride).
    Renvoie (facts, coverage) ; coverage {bloc: bool} = traçabilité de ce qui a répondu.
    Blessés inclus SEULEMENT si `coverage.injuries` de la ligue = True (Belgique/Amérique du Sud = False)."""
    facts: list[str] = []
    cov = {"resolve": False, "referee": False, "form": False, "xg": False, "injuries_covered": False,
           "injuries": False, "h2h": False, "predictions": False, "standings": False}
    f = resolve_fixture(cl, home, away, ko_iso)
    if not f:
        return facts, cov
    cov["resolve"] = True
    fid, lid, season = f["id"], f.get("league_id"), f.get("season")
    th, ta = f.get("home_id"), f.get("away_id")

    if f.get("referee"):
        facts.append(f"Arbitre : {f['referee']} (API-Football)")
        cov["referee"] = True

    if lid and season:
        for tid, label in ((th, home), (ta, away)):
            st = team_stats(cl, tid, lid, season) if tid else None
            if st and st.get("form"):
                g = st.get("goals") or {}
                gf = ((g.get("for") or {}).get("average") or {}).get("total")
                ga = ((g.get("against") or {}).get("average") or {}).get("total")
                ov = f" · over 2.5 {st['over25']}%" if st.get("over25") is not None else ""
                sk = st.get("streak") or {}
                skf = (f" · série {sk['wins']}V" if sk.get("wins") else
                       f" · série {sk['draws']}N" if sk.get("draws") else "")
                facts.append(f"Forme [{label}] : {st['form'][-6:]} · buts {gf}/match créés, {ga} concédés{ov}{skf} (API-Football)")
                cov["form"] = True

    for tid, label in ((th, home), (ta, away)):
        xg = team_xg_form(cl, tid) if tid else None
        if xg:
            facts.append(f"xG [{label}] (moy. {xg['n']} derniers) : {xg['xg']} créés / {xg['xga']} concédés (API-Football)")
            cov["xg"] = True

    covg = coverage(cl, lid, season) if lid and season else None
    if covg and covg.get("injuries"):
        cov["injuries_covered"] = True
        inj = injuries(cl, fid)
        if inj:
            # SPLIT PAR ÉQUIPE + DÉDUP (l'API répète chaque joueur, cf. 14 entrées = 7 uniques ×2). L'absence
            # d'un titulaire est un signal FORT et TEAM-SPÉCIFIQUE : Claude doit savoir QUELLE équipe est
            # affaiblie. Fusionner les 2 camps (ancien comportement) rendait le fait inexploitable.
            for tid, label in ((th, home), (ta, away)):
                seen, names = set(), []
                for i in inj:
                    nm, ti = i.get("player"), i.get("team_id")
                    if not nm or nm in seen:
                        continue
                    if tid and ti:                          # rattachement fiable par ID d'équipe
                        if ti != tid:
                            continue
                    elif _ov(_norm(i.get("team")), _norm(label)) < 0.5:   # repli : match du NOM d'équipe
                        continue
                    seen.add(nm)
                    r = i.get("reason")
                    names.append(f"{nm} ({r})" if r else nm)
                if names:
                    facts.append(f"Absents [{label}] : {', '.join(names[:8])} (API-Football)")
                    cov["injuries"] = True

    if th and ta:
        hh = h2h(cl, th, ta, last=5)
        if hh:
            facts.append("H2H (5 derniers) : " + " · ".join(x["score"] for x in hh) + " (API-Football)")
            cov["h2h"] = True

    # PRÉDICTION `/predictions` NON INJECTÉE dans le dossier Claude (décision user 2026-09-10) : c'est un
    # MODÈLE MAISON FAIBLE d'API-Football (win% relatif au championnat) qui DÉGÉNÈRE sur tout match
    # inter-championnat (0%/50/50 en Ligue des Champions — axes forme/att/def/poisson tous à 0% faute de table
    # commune) et n'apporte RIEN que Claude n'ait déjà en plus fiable (forme/xG/H2H/classement/blessés BRUTS,
    # dont il tire son PROPRE jugement = la moat). L'injecter = risque d'ANCRAGE sur du bruit (value screener :
    # « Poisson = bruit »). La fonction predictions() reste dispo pour le shadow/analyse, juste pas dans les faits.

    if lid and season:
        rk = {r["team_id"]: r for r in standings(cl, lid, season)}
        for tid, label in ((th, home), (ta, away)):
            r = rk.get(tid)
            if r:
                facts.append(f"Classement [{label}] : {r['rank']}e, {r['points']} pts, forme {r.get('form')} (API-Football)")
                cov["standings"] = True

    return facts, cov


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
