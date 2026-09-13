# -*- coding: utf-8 -*-
"""Track FANTÔME du « pari LIVE » foot — EXPÉRIMENTAL, JAMAIS PUBLIÉ.

Pourquoi un track fantôme et PAS un backtest : les cotes live (catalogue Bet Builder, cache mémoire TTL
45 s) et la TRAJECTOIRE de score minute par minute ne sont persistées NULLE PART. Les sidecars ne gardent
que le score FINAL. Un « backtest sur les matchs récents » exigerait donc de reconstituer des cotes live
jamais captées et un score-à-la-minute qu'on n'a pas -> chiffres inventés, l'inverse de « tout est mesuré ».
La SEULE voie de mesure honnête est FORWARD : on logge, on règle, on mesure la calibration + le ROI sur
plusieurs semaines. Rien n'est publié tant que l'edge n'est pas prouvé (décision proprio 2026-09-13).

Mécanique (jumeau LIVE de `value_pick`) : pour CHAQUE match foot en cours, on price le catalogue Bet Builder
LIVE (vraies cotes offertes de tous les marchés listés) et on croise, marché par marché,
    proba MODÈLE live (`analyses._live_model_pct`, Poisson sur les événements restants, indépendant de la cote)
    × cote live offerte − 1 = EV.
On LOGGE (log CONTINU riche, throttlé) tout pari qui dépasse le plancher de proba ET le plancher d'EV, sur
les marchés FIABLES seulement (allowlist : résultat / double chance / handicap buts / totaux buts / BTTS ;
bans corners/cartons/tirs/mi-temps/props/score exact comme au combiné). La métrique-titre se lit ensuite sur
UN pick canonique par match (le 1er qualifiant ≥ MINUTE_CANON_MIN), la calibration sur tous les snapshots.

ISOLATION TOTALE : les snapshots vivent dans un store SÉPARÉ `data/live_shadow/<sport>_<id>.json`, JAMAIS dans
le sidecar du match. Zéro contact avec `bets`/`stat_bet`/`shadow`/la calibration -> aucune contamination du
ROI publié ni des fantômes de calibration. Réversible : LIVE_PICK_ON.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time

from app import analyses

LIVE_PICK_ON = True
# AFFICHAGE SUR LE SITE (onglet Live) : le user est le SEUL utilisateur pour l'instant (aucun abonné) et veut
# voir les suggestions live directement sur le site, pas seulement sur /monitor. Clairement badgé « TEST / non
# publié ». ⚠️ À REMETTRE À False (ou gater par rôle owner) le jour où il y a de vrais abonnés — ce n'est PAS un
# produit validé (edge non prouvé). Reste toujours HORS Telegram/push et HORS stats/ROI Confiance-Value.
SHOW_ON_SITE = True

# --- GATES (départ NON backtesté — à tuner sur les données collectées ; cf. docstring : aucun backtest
#     possible). Forward, fantôme, jamais publié. -----------------------------------------------------------
EV_MIN = 0.05            # edge live minimal : proba modèle × cote − 1 ≥ +5 %
EV_MAX = 0.30            # au-DELÀ, la cote Bet Builder est quasi sûrement PÉRIMÉE/mal attribuée (les books
#                          live sont sharp : un edge réel dépasse rarement +10 %). On JETTE = data error, pas
#                          un pari (sinon on logge du bruit +2300 % venu des props/handicaps de scoreline).
PROB_MIN = 0.60          # plancher de proba MODÈLE (fraction 0-1)
PROB_MAX = 0.95          # au-dessus = pari quasi ACQUIS (cote minuscule) : pas l'edge qu'on mesure + modèle
#                          peu fiable aux extrêmes -> écarté (on veut la VRAIE value live, pas des certitudes).
MINUTE_LOG_MIN = 15      # ne rien logger avant la 15e minute (bruit d'ouverture du modèle)
MINUTE_CANON_MIN = 45    # « pick canonique » (métrique-titre, 1/match) = 1er qualifiant à/après cette minute
LOG_GAP_MIN = 5          # throttle : re-log d'un MÊME pari espacé d'au moins N minutes de match

# Marchés FIABLES autorisés (allowlist, PAS le banlist confiance qui exclut BTTS). Familles = `market_of`.
_ALLOW_FAMILIES = frozenset({
    "Vainqueur", "Double chance", "Handicap",
    "Total Over", "Total Under", "Total équipe", "Les 2 marquent",
})

# BAN DUR par LIBELLÉ (mesuré sur données réelles 2026-09-13) : `_leg_metric` mal-parse certains marchés
# exotiques en total/handicap de BUTS (ex. « Pascal Gross - Marque au moins 3 buts » -> Total Under, EV +6500 %,
# ou « 3-Way Handicap (2-0) » -> Handicap p=1.00). On les rejette AVANT toute classification. « marquent » (BTTS)
# n'est PAS touché (on ne bannit que « marque au moins » / buteur / props / scoreline / mi-temps / événements).
_BAN_TEXT_RE = re.compile(
    r"marque\s+au\s+moins|à\s+tout\s+moment|buteur|passe\s+d[ée]cisive|\bassist"
    r"|carton|corner|\btirs?\b|cadr|arr[eê]t|hors-?jeu|coup\s+franc"
    r"|score\s+exact|mi-?temps|1[eè]re?\s|2[eè]me?\s|p[ée]riode|3-?way|\(\s*\d+\s*-\s*\d+\s*\)",
    re.I)

_STORE = os.path.join(os.path.dirname(analyses.DIR), "live_shadow")

# Caches courts (monotonic) pour l'AFFICHAGE (l'onglet Live rend + auto-refresh 20 s) : les données ne bougent
# qu'au rythme de l'observe loop (25 s) / du règlement (10 min) -> un cache ~10-15 s est transparent et évite de
# recalculer à chaque rendu (perf : current_all globait 900 sidecars = ~184 ms/rendu).
_CURRENT_ALL_CACHE: dict = {}
_CURRENT_ALL_TTL = 10.0
_SUMMARY_CACHE: dict = {}
_SUMMARY_TTL = 15.0
_SETTLED_CACHE: dict = {}
_SETTLED_TTL = 15.0

# OPTIMISATION 1 (2026-09-13) : taux de buts SPÉCIFIQUE au match au lieu du taux-ligue fixe (2,7/90). La
# calibration mesurée montrait le Poisson à taux-ligue SUR-confiant sur les probas hautes (Unders dans les
# matchs ouverts) et SOUS-confiant sur les probas basses. On mélange (Bayes) le taux-ligue (a priori) avec le
# rythme RÉALISÉ du match (buts / temps écoulé) -> un match qui s'emballe projette plus de buts restants
# (Unders moins probables), un match fermé en projette moins. ZÉRO appel API en plus (score+minute déjà là).
# Réversible : TEMPO_BLEND_ON. À VALIDER par la calibration une fois assez de données.
TEMPO_BLEND_ON = True
_GOALS90_PRIOR_W = 1.0    # poids de l'a priori (taux-ligue), en « matchs complets » (Bayes). Plus haut = plus lisse.

# OPTIMISATION 2 (2026-09-13, user « injecter pour optimiser au max ») : PRESSION DE TIRS live d'API-Football.
# Les buts sont un signal RARE/bruité ; les TIRS (cadrés surtout) sont un signal DENSE de l'intensité offensive
# réelle -> meilleur estimateur du taux de buts que le score seul. On les convertit en xG-proxy et on les mélange
# au rythme de buts. ⚠️ xG live d'API-Football ÉCARTÉ (calculé ~post-match, peu fiable) -> on part des TIRS.
# Quota protégé : fixture id caché en permanence par match + taux caché 90 s/match. Réversible : STATS_INJECT_ON.
STATS_INJECT_ON = True
_XG_PER_SOT = 0.32        # xG-proxy par tir CADRÉ (ordre de grandeur usuel)
_XG_PER_OFF = 0.04        # xG-proxy par tir NON cadré
_STATS_RATE_TTL = 90.0
_STATS_RATE_CACHE: dict = {}   # mid -> (ts, rate90|None)
_FIXID_CACHE: dict = {}        # mid -> fixture_id API-Football (semi-statique)


def _stats_rate90(mid, home, away, ko, minute, allow_fetch: bool = False) -> float | None:
    """goals/90 estimé par la PRESSION DE TIRS live (xG-proxy des tirs cadrés/non cadrés des 2 équipes),
    ramené à 90'. None si indispo/flag off. Caché 90 s/match ; fixture id caché en permanence (quota).
    `allow_fetch` : SEUL le fond (observe loop, hors event loop) déclenche l'appel API ; l'AFFICHAGE lit le
    cache uniquement (jamais d'appel réseau bloquant dans le rendu — le fond garde le cache chaud toutes les ~25 s)."""
    if not STATS_INJECT_ON or not mid or minute is None or minute < 1:
        return None
    import time as _t
    hit = _STATS_RATE_CACHE.get(mid)
    if hit and (_t.time() - hit[0]) < _STATS_RATE_TTL:
        return hit[1]
    if not allow_fetch:
        return None                                        # rendu : pas d'appel réseau -> repli tempo-buts seul
    rate = None
    try:
        from app import apifootball as _AF
        if _AF.configured() and home and away and ko:
            with _AF._client() as cl:
                fid = _FIXID_CACHE.get(mid)
                if fid is None:
                    f = _AF.resolve_fixture(cl, home, away, ko)
                    fid = _FIXID_CACHE[mid] = (f or {}).get("id") or 0
                if fid:
                    ss = (_AF.live_match_stats(cl, fid) or {}).get("stats") or {}

                    def _g(side, k):
                        v = (ss.get(side) or {}).get(k)
                        return v if isinstance(v, (int, float)) else 0
                    sot = _g("home", "shots_on") + _g("away", "shots_on")
                    tot = _g("home", "shots_total") + _g("away", "shots_total")
                    if tot > 0:                            # au moins des tirs comptés -> signal exploitable
                        off = max(0, tot - sot)
                        xg = sot * _XG_PER_SOT + off * _XG_PER_OFF
                        rate = xg / max(0.05, minute / 90.0)   # xG-proxy accumulé -> ramené à /90
    except Exception:
        rate = None
    _STATS_RATE_CACHE[mid] = (_t.time(), rate)
    return rate


def _match_goals90(hs, as_, minute, mid=None, home="", away="", ko=None, allow_fetch: bool = False) -> float | None:
    """Taux de buts/90 propre au match = mélange bayésien taux-ligue (a priori) + observé. `observé` = buts
    RÉELS, enrichis (si dispo) de la PRESSION DE TIRS live (xG-proxy) -> signal plus dense/prédictif. None si
    TEMPO_BLEND_ON=False. f = fraction de match écoulée. `allow_fetch` : cf. _stats_rate90 (fond only)."""
    if not TEMPO_BLEND_ON:
        return None
    f = max(0.05, min(1.0, (minute or 0) / 90.0))
    goals = (analyses._as_int(hs) or 0) + (analyses._as_int(as_) or 0)
    obs = float(goals)
    sr = _stats_rate90(mid, home, away, ko, minute, allow_fetch)   # xG-proxy /90 (None si indispo)
    if sr is not None:
        obs = 0.5 * goals + 0.5 * (sr * f)                 # buts réels + xG-proxy accumulé (moitié-moitié)
    return (obs + _GOALS90_PRIOR_W * analyses._FOOT_GOALS_90) / (f + _GOALS90_PRIOR_W)


# --- store séparé (append-only par match) -----------------------------------------------------------------
def _store_path(sport: str, mid) -> str:
    return os.path.join(_STORE, f"{sport}_{mid}.json")


def _load(sport: str, mid) -> dict | None:
    try:
        return json.load(open(_store_path(sport, mid), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(rec: dict) -> None:
    os.makedirs(_STORE, exist_ok=True)
    pth = _store_path(rec["sport"], rec["match_id"])
    tmp = pth + ".tmp"
    try:
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, pth)
    except OSError:
        pass


def _iter_records():
    for pth in sorted(glob.glob(os.path.join(_STORE, "*.json"))):
        try:
            yield json.load(open(pth, encoding="utf-8"))
        except (OSError, ValueError):
            continue


# --- classification / pricing ----------------------------------------------------------------------------
def _covers_team(text_lc: str, name: str) -> bool:
    """Vrai si le libellé cite l'équipe `name` (n'importe quel mot ≥ 4 lettres du nom présent dans le
    texte). Tolère les libellés partiels du catalogue Unibet (« Lyon » pour « Olympique Lyonnais »)."""
    toks = [w for w in re.split(r"[^0-9A-Za-zÀ-ÿ]+", (name or "").lower()) if len(w) >= 4]
    return any(w in text_lc for w in toks)


def _dc_pair(text: str, home: str, away: str):
    """Paire de double chance (« 1X »/« 12 »/« X2 ») déduite du LIBELLÉ par NOM (le catalogue n'écrit pas
    le jeton « 1X »). Ex. « <dom> ou match nul » -> 1X. None si indéterminable (ne devine jamais)."""
    t = (text or "").lower()
    if "double chance" not in t:
        return None
    draw = bool(re.search(r"\bnul\b", t)) or "match nul" in t
    h, a = _covers_team(t, home), _covers_team(t, away)
    if h and draw:
        return "1X"
    if a and draw:
        return "X2"
    if h and a:
        return "12"
    return None


def _family(info: dict, wside, text: str) -> str:
    """Famille FIABLE d'un marché du catalogue live (dérivée de `_leg_metric`/`_winner_side`), ou 'Autre'
    (rejeté). On ne s'appuie PAS sur un code de règlement (le catalogue n'en a pas) mais sur la métrique."""
    if analyses._is_btts(text, ""):
        return "Les 2 marquent"
    if wside in ("home", "away", "draw"):
        return "Vainqueur"
    if wside in ("1X", "12", "X2"):
        return "Double chance"
    metric, scope, dirn, side = (info.get("metric"), info.get("scope"),
                                 info.get("dir"), info.get("side"))
    if metric == "goals" and scope == "match":
        if dirn == "HCAP" or info.get("handicap"):
            return "Handicap"
        if dirn == "OVER":
            return "Total équipe" if side in ("HOME", "AWAY") else "Total Over"
        if dirn == "UNDER":
            return "Total équipe" if side in ("HOME", "AWAY") else "Total Under"
    if analyses._is_signed_handicap(text) and metric in ("goals", "special"):
        return "Handicap"
    return "Autre"


def _info_lite(info: dict) -> dict:
    """Sous-ensemble JSON-sérialisable de `_leg_metric` nécessaire au RÈGLEMENT final (`_eval_leg`)."""
    return {k: info.get(k) for k in ("metric", "side", "dir", "line", "scope", "handicap", "live_ok")
            if info.get(k) is not None}


def price_catalog(catalog: list, home: str, away: str, hs: int, as_: int, minute,
                  mid=None, ko=None, allow_fetch: bool = False) -> list[dict]:
    """Croise chaque marché FIABLE du catalogue live avec le modèle : renvoie [{sel, family, wside, info,
    prob (0-1), odds, ev}] pour les marchés modélisables NON encore verrouillés. Lecture pure (0 réseau)."""
    out: list[dict] = []
    seen: set[str] = set()
    g90 = _match_goals90(hs, as_, minute, mid, home, away, ko, allow_fetch)   # taux/90 (tempo + tirs live)
    for e in (catalog or []):
        text = (e.get("text") or "").strip()
        od = e.get("odds")
        if not text or text in seen:
            continue
        if not (isinstance(od, (int, float)) and od > 1):
            continue
        if _BAN_TEXT_RE.search(text):                  # props/scoreline/événements mal-parsés en buts -> jetés
            continue
        info = analyses._leg_metric({"sel": text}, home, away)
        wside = analyses._winner_side(text, "", home, away, "foot")
        if wside is None:                              # DC par NOM (catalogue sans jeton « 1X ») -> résolue ici
            wside = _dc_pair(text, home, away)
        fam = _family(info, wside, text)
        if fam not in _ALLOW_FAMILIES:
            continue
        # Déjà tranché par le direct (total franchi / BTTS acquis) = plus une OPPORTUNITÉ de pari live -> skip.
        if analyses._live_locked("foot", text, "", info, hs, as_, None) in ("won", "lost"):
            continue
        prob = analyses._live_model_pct("foot", text, "", info, wside, hs, as_, minute, None, goals90=g90)
        if prob is None:
            continue
        seen.add(text)
        out.append({"sel": text, "family": fam, "wside": wside, "info": _info_lite(info),
                    "prob": float(prob), "odds": float(od), "ev": float(prob) * float(od) - 1.0})
    return out


# --- observation (1 passe live sur 1 match) ---------------------------------------------------------------
def observe_match(d: dict) -> int:
    """Une passe LIVE sur un match EN COURS : price le catalogue, logge les picks qualifiants (proba ≥
    PROB_MIN ET EV ≥ EV_MIN), throttlés par pari. Retourne le nb de snapshots ajoutés. Best-effort, lecture
    seule des caches live (0 réseau). N'écrit QUE dans le store séparé (jamais le sidecar)."""
    if not LIVE_PICK_ON or d.get("sport") != "foot":
        return 0
    mid = d.get("id")
    home, away = d.get("home", ""), d.get("away", "")
    from app import match_select
    ld = match_select.live_state_for("foot", home, away)
    sc = (ld or {}).get("score") or {}
    hs, as_ = analyses._as_int(sc.get("home")), analyses._as_int(sc.get("away"))
    minute = match_select.live_minute(ld)
    if hs is None or as_ is None or minute is None or minute < MINUTE_LOG_MIN:
        return 0
    catalog = analyses.live_catalog(mid)
    if not catalog:
        return 0
    qual = [p for p in price_catalog(catalog, home, away, hs, as_, minute, mid, d.get("start"), allow_fetch=True)
            if PROB_MIN <= p["prob"] <= PROB_MAX and EV_MIN <= p["ev"] <= EV_MAX]
    if not qual:
        return 0
    rec = _load("foot", mid) or {"sport": "foot", "match_id": mid, "home": home, "away": away,
                                 "comp": d.get("comp", ""), "start": d.get("start"),
                                 "snaps": [], "settled": False}
    added = 0
    for p in qual:
        last = max((s["minute"] for s in rec["snaps"] if s.get("sel") == p["sel"]), default=None)
        if last is not None and (minute - last) < LOG_GAP_MIN:
            continue                                   # throttle : même pari re-loggé trop tôt
        rec["snaps"].append({
            "minute": minute, "score": f"{hs}-{as_}", "hs": hs, "as": as_,
            "sel": p["sel"], "family": p["family"], "wside": p["wside"], "info": p["info"],
            "prob": round(p["prob"], 4), "odds": round(p["odds"], 3), "ev": round(p["ev"], 4),
            "result": None,
        })
        added += 1
    if added:
        _save(rec)
    return added


# --- suggestions COURANTES (affichage seul, sans écriture ni throttle) ------------------------------------
def current_picks(d: dict, top: int = 3) -> list[dict]:
    """Suggestions live ACTUELLES d'un match EN COURS (mêmes marchés/gates que observe_match, mais SANS
    écriture ni throttle) — pour l'AFFICHAGE. Triées par EV décroissant, `top` max. [] si pas de live/catalogue.
    100 % lecture des caches (0 réseau). N'écrit rien : ne peut pas contaminer le store ni les stats."""
    if not LIVE_PICK_ON or d.get("sport") != "foot":
        return []
    from app import match_select
    home, away = d.get("home", ""), d.get("away", "")
    ld = match_select.live_state_for("foot", home, away)
    sc = (ld or {}).get("score") or {}
    hs, as_ = analyses._as_int(sc.get("home")), analyses._as_int(sc.get("away"))
    minute = match_select.live_minute(ld)
    if hs is None or as_ is None or minute is None or minute < MINUTE_LOG_MIN:
        return []
    catalog = analyses.live_catalog(d.get("id"))
    if not catalog:
        return []
    picks = [p for p in price_catalog(catalog, home, away, hs, as_, minute, d.get("id"), d.get("start"))
             if PROB_MIN <= p["prob"] <= PROB_MAX and EV_MIN <= p["ev"] <= EV_MAX]
    picks.sort(key=lambda p: p["ev"], reverse=True)
    return picks[:max(1, top)]


def current_all(sport: str = "foot", top: int = 3) -> list[dict]:
    """Pour l'onglet Live : [{home, away, comp, minute, score, picks:[...]}] de TOUS les matchs EN COURS
    pour lesquels on a la donnée live (score + minute + catalogue de cotes). `picks` PEUT être vide (aucune
    value live à cet instant) -> la zone reste PERSISTANTE (ne clignote plus quand rien ne qualifie
    momentanément). Lecture seule (caches + sidecars mémoïsés). Matchs sans donnée live = exclus (rien à dire)."""
    import time as _t
    _now_m = _t.monotonic()
    _hit = _CURRENT_ALL_CACHE.get(sport)
    if _hit and (_now_m - _hit[0]) < _CURRENT_ALL_TTL:
        return _hit[1]                                 # cache court -> l'onglet Live ne recalcule pas à chaque rendu
    from app import match_select
    import datetime as _dt
    try:
        _now = _dt.datetime.now(_dt.timezone.utc)
    except Exception:
        _now = None
    out = []
    # PERF (user 2026-09-13 « test live lent à s'afficher ») : on itère `iter_meta` (cache 2 s PARTAGÉ avec tout
    # le rendu) au lieu de globber+charger les ~900 sidecars nous-mêmes (184 ms/rendu), + PRÉ-FILTRE par heure de
    # coup d'envoi (fenêtre ~3 h) -> status_of/live-cache seulement sur les rares matchs plausiblement en cours.
    for d in analyses.iter_meta(sport):
        sdt = d.get("_start_dt")
        if _now is not None and sdt is not None:
            try:
                if not (_dt.timedelta(0) <= (_now - sdt) <= _dt.timedelta(hours=3)):
                    continue
            except Exception:
                pass
        if analyses.status_of(d) != "inprogress":
            continue
        # INCLUSION gatée sur le CATALOGUE de cotes live (signal FIABLE = match live + pricable). Le score/
        # minute (`liveData` Unibet) est FLAKY (parfois None un instant) -> on ne l'exige PAS pour l'inclusion,
        # sinon la zone CLIGNOTE (disparaît quand le score saute). Sans score, `current_picks` renvoie [] et on
        # affiche un placeholder « données en cours » -> la zone reste PERSISTANTE tant que le match est en direct.
        if not analyses.live_catalog(d.get("id")):
            continue
        ld = match_select.live_state_for(sport, d.get("home", ""), d.get("away", ""))
        sc = (ld or {}).get("score") or {}
        hs, as_ = analyses._as_int(sc.get("home")), analyses._as_int(sc.get("away"))
        minute = match_select.live_minute(ld)
        score = f"{hs}-{as_}" if (hs is not None and as_ is not None) else ""
        picks = current_picks(d, top=top)
        # 1re minute où CHAQUE suggestion a été proposée (loggée) -> « dès X' » même en live. Depuis le store.
        rec = _load(sport, d.get("id")) or {}
        first = {}
        for s in rec.get("snaps", []):
            k, mn = s.get("sel"), s.get("minute")
            if k is not None and isinstance(mn, int) and (k not in first or mn < first[k]):
                first[k] = mn
        for p in picks:
            p["first_min"] = first.get(p.get("sel"), minute)   # repli = minute courante (pas encore loggé)
        out.append({"home": d.get("home", ""), "away": d.get("away", ""), "comp": d.get("comp", ""),
                    "minute": minute, "score": score, "picks": picks})
    out.sort(key=lambda m: m.get("minute") or 0, reverse=True)
    _CURRENT_ALL_CACHE[sport] = (_now_m, out)
    return out


# --- règlement (au score FINAL) ---------------------------------------------------------------------------
_SCORE_RE = re.compile(r"(\d+)\D+(\d+)")


def _final_goals(d: dict):
    """(buts_dom, buts_ext) FINAUX d'un match réglé, depuis `d['result']['score']` (label « 2-1 »). None si
    absent/illisible. Temps réglementaire : le label écrit au règlement suit déjà la règle 90 min du produit."""
    lab = ((d.get("result") or {}).get("score") or "")
    m = _SCORE_RE.search(str(lab))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _settle_snap(snap: dict, fh: int, fa: int):
    """'won'/'lost'/'push'/None pour un snapshot vu le score FINAL. Résultat/DC résolus à la main ; totaux/
    handicap buts via `analyses._eval_leg` (final) ; BTTS sur les deux scores."""
    fam, wside = snap.get("family"), snap.get("wside")
    if fam == "Les 2 marquent":
        yes = "non" not in (snap.get("sel", "") or "").lower()
        return "won" if ((fh >= 1 and fa >= 1) == yes) else "lost"
    res = "home" if fh > fa else "away" if fa > fh else "draw"
    if wside in ("home", "away", "draw"):
        return "won" if wside == res else "lost"
    if wside in ("1X", "12", "X2"):
        ok = ((wside == "1X" and res in ("home", "draw"))
              or (wside == "12" and res in ("home", "away"))
              or (wside == "X2" and res in ("away", "draw")))
        return "won" if ok else "lost"
    info = dict(snap.get("info") or {})
    info.setdefault("live_ok", True)                   # marché buts match-scope -> réglable au score final
    st, _ = analyses._eval_leg(info, {"goals_h": fh, "goals_a": fa}, final=True)
    return st if st in ("won", "lost", "push") else None


def settle_all() -> int:
    """Règle les snapshots des matchs FINIS (score final présent). Idempotent (rec['settled']). Renvoie le
    nb de matchs nouvellement réglés. À appeler depuis la boucle de règlement (hors event loop)."""
    n = 0
    for rec in list(_iter_records()):
        if rec.get("settled"):
            continue
        d = analyses.meta(rec.get("sport", "foot"), rec.get("match_id"))
        if not d or analyses.status_of(d) != "finished":
            continue
        goals = _final_goals(d)
        if goals is None:
            continue
        fh, fa = goals
        for s in rec.get("snaps", []):
            if s.get("result") in ("won", "lost", "push"):
                continue
            s["result"] = _settle_snap(s, fh, fa)
        rec["settled"] = True
        rec["final"] = f"{fh}-{fa}"                     # score final -> affichage « terminés »
        _save(rec)
        n += 1
    return n


def recent_settled(sport: str = "foot", hours: int = 48, limit: int = 8) -> list[dict]:
    """Pour l'affichage « Test live — terminés » : matchs RÉGLÉS récents (≤ `hours`) avec, par match, les
    suggestions DISTINCTES (dédupées par libellé) et leur résultat won/lost/push -> répond à « ce qui avait
    été proposé est-il passé ? ». Plus récents d'abord, `limit` max. Lecture seule (cache court ~15 s)."""
    import time as _t
    _now_m = _t.monotonic()
    _k = (sport, hours, limit)
    _hit = _SETTLED_CACHE.get(_k)
    if _hit and (_now_m - _hit[0]) < _SETTLED_TTL:
        return _hit[1]
    import datetime as _dt
    try:
        now = _dt.datetime.now(_dt.timezone.utc)
    except Exception:
        now = None
    out = []
    for rec in _iter_records():
        if rec.get("sport") != sport or not rec.get("settled"):
            continue
        st = rec.get("start")
        if now and st:
            try:
                dtv = _dt.datetime.fromisoformat(str(st).replace("Z", "+00:00"))
                if (now - dtv) > _dt.timedelta(hours=hours):
                    continue
            except Exception:
                pass
        seen = {}
        for s in rec.get("snaps", []):
            if s.get("result") not in ("won", "lost", "push"):
                continue
            k = s.get("sel")
            if k not in seen:                          # 1re occurrence (minute la plus basse = 1re proposition)
                seen[k] = {"sel": k, "family": s.get("family"), "result": s["result"],
                           "odds": s.get("odds"), "prob": s.get("prob"), "ev": s.get("ev"),
                           "minute": s.get("minute")}
        if not seen:
            continue
        out.append({"home": rec.get("home", ""), "away": rec.get("away", ""), "comp": rec.get("comp", ""),
                    "final": rec.get("final", ""), "start": st, "picks": list(seen.values())})
    out.sort(key=lambda m: m.get("start") or "", reverse=True)
    _SETTLED_CACHE[_k] = (_now_m, out[:limit])
    return out[:limit]


# --- agrégation pour /monitor -----------------------------------------------------------------------------
def summary() -> dict:
    """Métriques du track fantôme LIVE (EXPÉRIMENTAL, non publié). Métrique-titre = UN pick CANONIQUE par
    match (1er qualifiant réglé ≥ MINUTE_CANON_MIN, indépendant entre matchs). Calibration = TOUS les
    snapshots réglés won/lost, en déciles de proba modèle (attention : plusieurs snapshots d'un même match
    sont corrélés -> la calibration est indicative, pas un test d'indépendance). Cache court ~15 s."""
    import time as _t
    _now_m = _t.monotonic()
    _hit = _SUMMARY_CACHE.get("all")
    if _hit and (_now_m - _hit[0]) < _SUMMARY_TTL:
        return _hit[1]
    canon, allsnaps = [], []
    matches = pending = 0
    for rec in _iter_records():
        matches += 1
        settled = [s for s in rec.get("snaps", []) if s.get("result") in ("won", "lost", "push")]
        pending += sum(1 for s in rec.get("snaps", []) if s.get("result") not in ("won", "lost", "push"))
        allsnaps.extend(settled)
        cs = sorted((s for s in settled if s.get("minute", 0) >= MINUTE_CANON_MIN),
                    key=lambda s: (s["minute"], s.get("sel", "")))
        if cs:
            canon.append(cs[0])

    def _roi(rows):
        n = len(rows)
        wins = sum(1 for s in rows if s["result"] == "won")
        losses = sum(1 for s in rows if s["result"] == "lost")
        ret = sum((s["odds"] - 1.0) if s["result"] == "won"
                  else (-1.0 if s["result"] == "lost" else 0.0) for s in rows)
        dec = wins + losses
        return {"n": n, "wins": wins, "losses": losses,
                "winrate": round(100.0 * wins / dec, 1) if dec else 0.0,
                "roi": round(100.0 * ret / n, 2) if n else 0.0,
                "avg_cote": round(sum(s["odds"] for s in rows) / n, 2) if n else 0.0,
                "avg_ev": round(100.0 * sum(s["ev"] for s in rows) / n, 2) if n else 0.0,
                "avg_min": round(sum(s["minute"] for s in rows) / n, 1) if n else 0.0}

    buckets: dict[int, list] = {}
    for s in allsnaps:
        if s["result"] not in ("won", "lost"):
            continue
        b = min(9, int(s.get("prob", 0) * 10))
        buckets.setdefault(b, [0, 0, 0.0])
        buckets[b][0] += 1
        buckets[b][1] += 1 if s["result"] == "won" else 0
        buckets[b][2] += s.get("prob", 0.0)
    calib = [{"bucket": f"{b*10}-{b*10+10}%", "n": v[0],
              "model": round(100.0 * v[2] / v[0], 1) if v[0] else 0.0,
              "real": round(100.0 * v[1] / v[0], 1) if v[0] else 0.0}
             for b, v in sorted(buckets.items())]

    by_fam: dict[str, list] = {}
    for s in canon:
        by_fam.setdefault(s.get("family", "?"), []).append(s)

    _res = {
        "matches": matches, "snaps_total": len(allsnaps) + pending, "snaps_pending": pending,
        "canonical": _roi(canon),
        "all_settled": _roi([s for s in allsnaps if s["result"] in ("won", "lost", "push")]),
        "by_family": {fam: _roi(rows) for fam, rows in sorted(by_fam.items())},
        "calibration": calib,
        "gates": {"ev_min": EV_MIN, "ev_max": EV_MAX, "prob_min": PROB_MIN, "prob_max": PROB_MAX,
                  "minute_log_min": MINUTE_LOG_MIN, "minute_canon_min": MINUTE_CANON_MIN,
                  "log_gap_min": LOG_GAP_MIN},
    }
    _SUMMARY_CACHE["all"] = (_now_m, _res)
    return _res


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
