"""Cotes Pinnacle via THE ODDS API (the-odds-api.com) — book « sharp » de RÉFÉRENCE PRIMAIRE. Remplace le
scraping Pinnacle-via-proxy iProyal (mort/fragile) par une VRAIE API self-service : palier GRATUIT
500 crédits/mois, Pinnacle inclus, football.

Interface IDENTIQUE à app/pinnacle.py (drop-in) :
  - sharp_probs(home, away, sport, comp="")   -> {home, away, draw, margin}  (1X2 de-viggé)
  - sharp_markets(home, away, sport, comp="") -> {"totals": {ligne: proba_over}, "spreads": {}}

⚠️ MODÈLE DE COÛT (crédits) — pourquoi le mapping ligue + cache sont VITAUX :
  The Odds API découpe le football en ~44 clés de LIGUE (soccer_epl, soccer_spain_la_liga…). 1 appel =
  1 ligue = 2 crédits (h2h + totals), et il renvoie TOUS les matchs de la ligue d'un coup. Le palier gratuit
  = 500 crédits/mois = ~250 appels-ligue/mois = ~8 ligues/jour. Donc on NE fetch PAS à l'aveugle : on mappe
  chaque match vers SA ligue (`comp` Unibet -> sport_key) et on ne fetch QUE les ligues au programme, avec
  un CACHE fichier par ligue (TTL 12 h) partagé entre les scans matin/soir. Les ligues NON mappées (niches
  hors couverture) coûtent 0 -> repli propre (garde-fou scan = pas d'Over/Under à l'aveugle sans ancre).

Config (.env) : ODDS_API_KEY = <clé the-odds-api.com>  (vide = module inactif -> repli scraping Pinnacle).
Best-effort STRICT : clé manquante / panne / ligue non mappée / match introuvable -> None.
"""

from __future__ import annotations

import json
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from app.sources import _deacc_low, _tok   # normalisation accents + tokenisation robuste (FR->EN)

_BASE = "https://api.the-odds-api.com/v4"
_REGIONS = "eu"                      # région contenant les books sharp (Pinnacle, Betfair Exchange…)
_MARKETS = "h2h,totals"              # 1X2 + totaux buts = 2 crédits/appel
# On récupère TOUS les books eu (même coût : crédits = marchés × régions, INDÉPENDANT du nb de books) pour
# MAXIMISER la couverture : un match que Pinnacle ne cote pas est souvent coté ailleurs. Préférence sharp :
# Pinnacle -> Betfair Exchange -> CONSENSUS médian dé-viggé de tous les books (repli robuste).
_SHARP_BOOKS = ("pinnacle", "betfair_ex")
_CACHE_DIR = os.path.join("data", "sharp_odds_cache")
_TTL = 3 * 3600                      # cache par ligue : 3 h (user 2026-08-14, wave-first) -> l'ancre de REPLI
                                     # reste FRAÎCHE (≤3 h avant le KO), pas un cache de la veille. L'ancre
                                     # PRIMAIRE (Pinnacle) est déjà live par analyse ; ceci ne concerne QUE le
                                     # fallback The Odds API (rare -> surcoût crédits marginal, plancher 15 protège).
_SOFT_FLOOR = 15                     # sous ce reste de crédits, on ARRÊTE de fetcher (garde-fou anti-overage)
_QUOTA_FILE = os.path.join(_CACHE_DIR, "_quota.json")

_last_status = ""                    # diagnostic santé (dernière raison d'échec / voie)
_last_remaining: int | None = None   # crédits restants (dernier header x-requests-remaining vu)


# ── comp Unibet (normalisé, sans accents) -> sport_key The Odds API ───────────────────────────────────
# Liste ORDONNÉE : motif le plus SPÉCIFIQUE d'abord (ex. « brasileirao serie a » avant « serie a », « la
# liga 2 » avant « la liga », « bundesliga 2 » avant « bundesliga »). Seules les ligues RÉELLEMENT couvertes
# par l'API sont mappées ; les autres -> None (repli). Match par sous-chaîne sur le libellé normalisé.
_COMP_MAP: list[tuple[str, str]] = [
    # ── EXCLUSIONS EXPLICITES d'abord (collision-prone OU non couvertes) : '' -> None, 0 crédit ──
    # Sans elles, le motif générique plus bas capterait à tort (ex. « ligue des champions afc » -> UCL).
    ("ligue des champions afc", ""),          # AFC (Asie) ≠ UEFA
    ("champions league afc", ""),
    ("conference league qualification", ""),  # qualif Conference : PAS de clé (seule la phase de groupes existe)
    ("qualif. ligue conference", ""),
    ("qualif. ligue europa", ""),             # qualif Europa : PAS de clé
    ("europa league qualification", ""),
    ("qualif. coupe du monde", ""),           # découpé par région chez l'API, libellé Unibet ambigu -> skip
    ("mls next", ""),                         # MLS Next Pro ≠ MLS
    ("eerste divisie", ""),                   # D2 néerlandaise ≠ Eredivisie
    ("brasileirao serie c", ""),              # pas de clé (≠ A/B)
    ("primera b nacional", ""),               # D2 argentine (≠ Primera) — non couverte

    # ── AMÉRIQUES ──
    ("brasileirao serie a", "soccer_brazil_campeonato"),
    ("brasileirao serie b", "soccer_brazil_serie_b"),
    ("liga profesional argentina", "soccer_argentina_primera_division"),
    ("copa libertadores", "soccer_conmebol_copa_libertadores"),
    ("libertadores", "soccer_conmebol_copa_libertadores"),
    ("copa sudamericana", "soccer_conmebol_copa_sudamericana"),
    ("sud-americaine", "soccer_conmebol_copa_sudamericana"),
    ("sudamericaine", "soccer_conmebol_copa_sudamericana"),
    ("copa america", "soccer_conmebol_copa_america"),
    ("leagues cup", "soccer_concacaf_leagues_cup"),               # (AVANT « league cup » anglaise)
    ("liga mx", "soccer_mexico_ligamx"),
    ("mls", "soccer_usa_mls"),
    ("primera division - chile", "soccer_chile_campeonato"),
    ("primera division", "soccer_chile_campeonato"),              # libellé nu -> Chili (best-effort)

    # ── ANGLETERRE ──
    ("premier league - russia", "soccer_russia_premier_league"),  # (AVANT « premier league » anglaise)
    ("premier league russie", "soccer_russia_premier_league"),
    ("premier league", "soccer_epl"),
    ("epl", "soccer_epl"),
    ("championship", "soccer_efl_champ"),
    ("league one", "soccer_england_league1"),
    ("league 1", "soccer_england_league1"),
    ("league two", "soccer_england_league2"),
    ("league 2", "soccer_england_league2"),
    ("efl cup", "soccer_england_efl_cup"),
    ("carabao", "soccer_england_efl_cup"),
    ("league cup", "soccer_england_efl_cup"),
    ("fa cup", "soccer_fa_cup"),
    ("coupe d'angleterre", "soccer_fa_cup"),
    ("premiership", "soccer_spl"),                               # Écosse (« Premiership d'Ecosse »)

    # ── ESPAGNE ──
    ("la liga 2", "soccer_spain_segunda_division"),
    ("segunda division", "soccer_spain_segunda_division"),
    ("la liga", "soccer_spain_la_liga"),
    ("copa del rey", "soccer_spain_copa_del_rey"),
    ("coupe du roi", "soccer_spain_copa_del_rey"),

    # ── ITALIE ── (après « brasileirao serie a/b » plus haut)
    ("serie a - italy", "soccer_italy_serie_a"),
    ("serie b - italy", "soccer_italy_serie_b"),
    ("serie a", "soccer_italy_serie_a"),
    ("serie b", "soccer_italy_serie_b"),
    ("coppa italia", "soccer_italy_coppa_italia"),
    ("coupe d'italie", "soccer_italy_coppa_italia"),

    # ── ALLEMAGNE ── (Autriche a son libellé propre plus bas)
    ("bundesliga 2", "soccer_germany_bundesliga2"),
    ("2. bundesliga", "soccer_germany_bundesliga2"),
    ("liga3", "soccer_germany_liga3"),
    ("3. liga", "soccer_germany_liga3"),
    ("dfb-pokal", "soccer_germany_dfb_pokal"),
    ("coupe d'allemagne", "soccer_germany_dfb_pokal"),
    ("bundesliga", "soccer_germany_bundesliga"),

    # ── FRANCE ──
    ("ligue 1", "soccer_france_ligue_one"),
    ("ligue 2", "soccer_france_ligue_two"),
    ("coupe de france", "soccer_france_coupe_de_france"),

    # ── AUTRES CHAMPIONNATS EUROPÉENS ──
    ("eredivisie", "soccer_netherlands_eredivisie"),
    ("jupiler", "soccer_belgium_first_div"),                     # Belgique (PAS « pro league » nu -> collision Saoudienne)
    ("primeira liga", "soccer_portugal_primeira_liga"),
    ("liga portugal", "soccer_portugal_primeira_liga"),
    ("superligaen", "soccer_denmark_superliga"),
    ("superliga", "soccer_denmark_superliga"),
    ("eliteserien", "soccer_norway_eliteserien"),
    ("allsvenskan", "soccer_sweden_allsvenskan"),
    ("superettan", "soccer_sweden_superettan"),
    ("veikkausliiga", "soccer_finland_veikkausliiga"),
    ("ekstraklasa", "soccer_poland_ekstraklasa"),
    ("super league - greece", "soccer_greece_super_league"),
    ("super league grece", "soccer_greece_super_league"),
    ("austrian", "soccer_austria_bundesliga"),
    ("autriche", "soccer_austria_bundesliga"),
    ("swiss super", "soccer_switzerland_superleague"),
    ("super league suisse", "soccer_switzerland_superleague"),
    ("league of ireland", "soccer_league_of_ireland"),

    # ── UEFA / INTERNATIONAL ── (skips AFC/qualif déjà en tête)
    ("qualif. ligue des champions", "soccer_uefa_champs_league_qualification"),
    ("champions league qualif", "soccer_uefa_champs_league_qualification"),
    ("ligue des champions", "soccer_uefa_champs_league"),        # phase de groupes (bare, après AFC/qualif)
    ("ligue europa", "soccer_uefa_europa_league"),              # (qualif déjà exclu en tête)
    ("conference league", "soccer_uefa_europa_conference_league"),
    ("ligue conference", "soccer_uefa_europa_conference_league"),
    ("ligue des nations", "soccer_uefa_nations_league"),
    ("nations league", "soccer_uefa_nations_league"),
    ("championnat d'europe", "soccer_uefa_european_championship"),
    ("coupe du monde des clubs", "soccer_fifa_club_world_cup"),
    ("club world cup", "soccer_fifa_club_world_cup"),
    ("coupe du monde", "soccer_fifa_world_cup"),
    ("coupe d'afrique", "soccer_africa_cup_of_nations"),
    ("africa cup", "soccer_africa_cup_of_nations"),

    # ── RESTE DU MONDE ──
    ("saudi", "soccer_saudi_arabia_pro_league"),
    ("saoudienne", "soccer_saudi_arabia_pro_league"),
    ("ligue professionnelle", "soccer_saudi_arabia_pro_league"),  # libellé Unibet du championnat saoudien (best-effort)
    ("turkey", "soccer_turkey_super_league"),
    ("turquie", "soccer_turkey_super_league"),
    ("super lig", "soccer_turkey_super_league"),
    ("j league", "soccer_japan_j_league"),
    ("j-league", "soccer_japan_j_league"),
    ("k league", "soccer_korea_kleague1"),
    ("super league - china", "soccer_china_superleague"),
    ("super league chine", "soccer_china_superleague"),
    ("a-league", "soccer_australia_aleague"),
    ("a league", "soccer_australia_aleague"),
]


def _key() -> str:
    from app.config import get_settings
    return (getattr(get_settings(), "odds_api_key", "") or "").strip()


def configured() -> bool:
    """True si une clé The Odds API est renseignée (.env : ODDS_API_KEY)."""
    return bool(_key())


def _sport_key_for(comp: str) -> str | None:
    """Mappe un libellé de compétition Unibet vers une clé de ligue The Odds API. None si non couverte
    (repli propre). '' interne = ligne « skip explicite » (ex. MLS Next Pro) -> traité comme None."""
    n = _deacc_low(comp or "")
    if not n:
        return None
    for pat, key in _COMP_MAP:
        if pat in n:
            return key or None
    return None


def _load_quota() -> None:
    global _last_remaining
    try:
        _last_remaining = int(json.load(open(_QUOTA_FILE, encoding="utf-8")).get("remaining"))
    except Exception:
        pass


def _save_quota() -> None:
    if _last_remaining is None:
        return
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        json.dump({"remaining": _last_remaining, "ts": int(_time.time())},
                  open(_QUOTA_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def _fetch_league(sport_key: str) -> list | None:
    """Toutes les cotes (h2h+totals) d'une ligue, TOUS books eu confondus. Cache fichier par ligue (TTL 18 h)
    -> 1 appel = 2 crédits, partagé entre matchs de la même ligue ET entre scans. None si panne. Respecte un
    plancher de crédits (arrêt anti-overage)."""
    global _last_status, _last_remaining
    path = os.path.join(_CACHE_DIR, f"{sport_key}.json")
    try:                                                          # cache frais ?
        if os.path.getmtime(path) > _time.time() - _TTL:
            return json.load(open(path, encoding="utf-8"))
    except Exception:
        pass
    key = _key()
    if not key:
        _last_status = "clé absente (.env : ODDS_API_KEY)"
        return None
    if _last_remaining is None:
        _load_quota()
    if _last_remaining is not None and _last_remaining < _SOFT_FLOOR:   # garde-fou : ne pas épuiser le quota
        _last_status = f"quota bas ({_last_remaining}) — fetch suspendu (repli garde-fou)"
        return None
    url = (f"{_BASE}/sports/{sport_key}/odds/?" + urllib.parse.urlencode(
        {"apiKey": key, "regions": _REGIONS, "markets": _MARKETS, "oddsFormat": "decimal"}))
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            rem = r.headers.get("x-requests-remaining")
            if rem is not None:
                try:
                    _last_remaining = int(float(rem))
                    _save_quota()
                except Exception:
                    pass
            data = json.loads(r.read().decode("utf-8", "replace"))
        if not isinstance(data, list):
            _last_status = "réponse inattendue (pas une liste)"
            return None
        os.makedirs(_CACHE_DIR, exist_ok=True)
        json.dump(data, open(path, "w", encoding="utf-8"))
        _last_status = ""
        return data
    except urllib.error.HTTPError as e:
        # 401 clé invalide · 422 ligue hors saison/inconnue · 429 quota épuisé
        _last_status = f"HTTP {e.code} sur {sport_key}"
        if e.code == 429:
            _last_remaining = 0
            _save_quota()
        return None
    except Exception as e:
        _last_status = f"{type(e).__name__} sur {sport_key}"
        return None


def _parse_iso(s: str | None) -> float | None:
    """Horodatage ISO -> epoch (s). Gère le suffixe 'Z' et le naïf (supposé UTC). None si illisible."""
    if not s:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _find_event(events: list, home: str, away: str, ko: str | None = None) -> dict | None:
    """Évènement correspondant à NOTRE home/away, par recouvrement de jetons (_tok gère FR->EN). Résolution
    FORTE = ≥1 jeton de CHAQUE côté (bestsc≥2). REPLI par COUP D'ENVOI (si `ko` fourni) : quand un seul côté
    matche (ex. translittération arabe « Al Qadisiya » vs « Al-Qadsiah »), on prend l'évènement de la ligue
    dont le coup d'envoi est le plus proche (fenêtre ±12 h) — dans une ligue, une équipe ne joue qu'une fois,
    donc c'est sûr. None si rien."""
    th, ta = _tok(home), _tok(away)
    if not th or not ta:
        return None
    ko_ts = _parse_iso(ko)
    best, bestsc = None, 0
    fb_best, fb_gap = None, None                              # repli coup d'envoi (1 seul côté + KO proche)
    for ev in events or []:
        eh, ea = _tok(ev.get("home_team", "")), _tok(ev.get("away_team", ""))
        direct = len(th & eh) + len(ta & ea)
        swap = len(th & ea) + len(ta & eh)
        sc = max(direct, swap)
        if sc > bestsc and (len(th & eh) or len(th & ea)) and (len(ta & ea) or len(ta & eh)):
            best, bestsc = ev, sc
        if ko_ts is not None and (th & eh or th & ea or ta & ea or ta & eh):   # ≥1 côté matche
            ev_ts = _parse_iso(ev.get("commence_time"))
            if ev_ts is not None:
                gap = abs(ev_ts - ko_ts)
                if gap <= 12 * 3600 and (fb_gap is None or gap < fb_gap):
                    fb_best, fb_gap = ev, gap
    return best if bestsc >= 2 else fb_best


def _market_outcomes(ev: dict, market_key: str) -> list:
    """Outcomes d'un marché en préférant le book le plus SHARP : Pinnacle, sinon Betfair Exchange, sinon
    CONSENSUS = médiane des prix par issue (nom, ligne) sur TOUS les books eu. Maximise la couverture (un
    match non coté par Pinnacle l'est souvent ailleurs) sans surcoût. [] si aucun book ne cote ce marché."""
    books = ev.get("bookmakers") or []

    def _book(bk_key):
        for bk in books:
            if bk.get("key") == bk_key:
                for m in bk.get("markets") or []:
                    if m.get("key") == market_key:
                        return m.get("outcomes") or []
        return None
    for bk_key in _SHARP_BOOKS:                              # 1) book sharp direct si présent
        o = _book(bk_key)
        if o:
            return o
    from statistics import median                            # 2) consensus médian dé-viggé plus bas
    agg: dict = {}
    for bk in books:
        for m in bk.get("markets") or []:
            if m.get("key") != market_key:
                continue
            for o in m.get("outcomes") or []:
                if o.get("price"):
                    agg.setdefault((o.get("name"), o.get("point")), []).append(o["price"])
    return [{"name": k[0], "point": k[1], "price": round(median(v), 3)} for k, v in agg.items()]


def sharp_probs(home: str, away: str, sport: str, comp: str = "", ko: str | None = None) -> dict | None:
    """Probas 1X2 de-viggées (marge retirée) via le book le plus sharp de The Odds API (Pinnacle -> Betfair
    -> consensus), alignées sur NOTRE home/away. `ko` = coup d'envoi (repli de résolution). {home, away,
    draw, margin}. None si sport≠foot, ligue non mappée, ou match/cote introuvable."""
    if sport != "foot" or not configured():
        return None
    sk = _sport_key_for(comp)
    if not sk:
        return None
    ev = _find_event(_fetch_league(sk) or [], home, away, ko)
    if not ev:
        return None
    outs = _market_outcomes(ev, "h2h")
    if not outs:
        return None
    eh, ea = _tok(ev.get("home_team", "")), _tok(ev.get("away_team", ""))
    th, ta = _tok(home), _tok(away)
    slots: dict = {}
    for o in outs:
        nm, price = o.get("name", ""), o.get("price")
        if not price:
            continue
        low = _deacc_low(nm)
        if "draw" in low or low in ("nul", "match nul", "x"):
            slots["draw"] = price
        else:
            nt = _tok(nm)
            # rattache l'outcome à NOTRE home/away via l'équipe Pinnacle la plus proche
            to_home = len(nt & eh) + len(nt & th)
            to_away = len(nt & ea) + len(nt & ta)
            slots["home" if to_home >= to_away else "away"] = price
    if "home" not in slots or "away" not in slots:
        return None
    order = [k for k in ("home", "draw", "away") if k in slots]
    inv = [1.0 / slots[k] for k in order]
    s = sum(inv)
    if s <= 0:
        return None
    fair = {k: inv[i] / s for i, k in enumerate(order)}
    return {"home": round(fair.get("home", 0.0), 3), "away": round(fair.get("away", 0.0), 3),
            "draw": round(fair["draw"], 3) if "draw" in fair else None,
            "margin": round(s - 1.0, 4)}


def sharp_markets(home: str, away: str, sport: str, comp: str = "", ko: str | None = None) -> dict | None:
    """Probas SHARP de-viggées des TOTAUX buts (Over/Under) via le book le plus sharp (Pinnacle -> Betfair ->
    consensus). Ancre value hors-1X2. `ko` = coup d'envoi (repli de résolution).
    {"totals": {ligne: proba de DÉPASSER}, "spreads": {}}. None si introuvable."""
    if sport != "foot" or not configured():
        return None
    sk = _sport_key_for(comp)
    if not sk:
        return None
    ev = _find_event(_fetch_league(sk) or [], home, away, ko)
    if not ev:
        return None
    outs = _market_outcomes(ev, "totals")
    over = {o.get("point"): o.get("price") for o in outs if _deacc_low(o.get("name", "")).startswith("over")}
    under = {o.get("point"): o.get("price") for o in outs if _deacc_low(o.get("name", "")).startswith("under")}
    totals: dict = {}
    for line in over:
        op, up = over.get(line), under.get(line)
        if op and up and line is not None:
            io, iu = 1.0 / op, 1.0 / up
            s = io + iu
            if s > 0:
                totals[float(line)] = round(io / s, 3)            # proba de DÉPASSER la ligne
    if not totals:
        return None
    return {"totals": totals, "spreads": {}}
