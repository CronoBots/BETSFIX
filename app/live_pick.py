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
import math
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

# TOUS LES MARCHÉS MODÉLISABLES (user 2026-09-13 « tous les types de marché peuvent être pris en compte ») :
# buts (résultat/DC/handicap/totaux/BTTS) + ÉVÉNEMENTS COMPTÉS (corners/cartons/tirs/tirs cadrés) — ces derniers
# pricés grâce aux compteurs live API-Football (`_live_vals`). On garde bannis les marchés VRAIMENT non-
# modélisables (props JOUEUR, score exact, 1er but/buteur, mi-temps/périodes). Réversible : ALL_MARKETS_ON.
ALL_MARKETS_ON = True
_ALLOW_FAMILIES = frozenset({
    "Vainqueur", "Double chance", "Handicap",
    "Total Over", "Total Under", "Total équipe", "Les 2 marquent",
})
_ALLOW_COUNTED = frozenset({"Corners", "Cartons", "Tirs", "Tirs cadrés",
                            # user 2026-09-14 « tout le mesurable » : compteurs API-Football supplémentaires
                            "Fautes", "Hors-jeu", "Arrêts", "Passes", "Possession"})   # activés si ALL_MARKETS_ON

# BAN DUR par LIBELLÉ (mesuré sur données réelles 2026-09-13) : `_leg_metric` mal-parse certains marchés en
# total/handicap de BUTS (ex. « Pascal Gross - Marque au moins 3 buts » -> Total Under, EV +6500 %). On les
# rejette AVANT classification. Corners/cartons/tirs NE sont PLUS bannis (désormais pricés via compteurs live) —
# on ne bannit que les VRAIS non-modélisables : props JOUEUR (marque/buteur/passe/arrêts), mi-temps/périodes,
# scoreline, score exact, hors-jeu, coup franc. « marquent » (BTTS) épargné.
_BAN_TEXT_RE = re.compile(
    r"marque\s+au\s+moins|à\s+tout\s+moment|buteur|passe\s+d[ée]cisive|\bassist"
    r"|coup\s+franc|remplac|score\s+exact|mi-?temps|1[eè]re?\s|2[eè]me?\s|p[ée]riode"
    r"|3-?way|\(\s*\d+\s*-\s*\d+\s*\)"
    # PREMIÈRE/DERNIÈRE équipe à marquer · premier/prochain but (user 2026-09-14) : réglé sur la CHRONOLOGIE des
    # buts (1er/dernier buteur), qu'on ne stocke PAS (seulement score final + mi-temps) -> non réglable -> banni
    # (sinon lu « <équipe> vainqueur » et réglé au score final = faux).
    r"|premi[eè]re?\s+[ée]quipe|derni[eè]re?\s+[ée]quipe|marquer\s+en\s+premier|premier\s+but|prochain\s+but"
    # BAN DUR (bug user 2026-09-14) : marchés « … par intervalle Opta » (« 40:00-44:59 (Réglé selon les données
    # Opta) ») — non réglables à ce grain (API-Football donne le TOTAL, pas le découpage par tranche de 5 min).
    # Le TOTAL fautes/hors-jeu/arrêts, lui, est désormais AUTORISÉ (cf. _extra_metric) — d'où le ban ciblé
    # UNIQUEMENT sur l'intervalle mm:ss-mm:ss et la mention Opta, plus « mi-temps/période » (traité en stage 2).
    r"|donn[ée]es\s+opta|\d{1,3}:\d{2}\s*[-–]\s*\d{1,3}:\d{2}",
    re.I)

# STATS NON RÉCUPÉRABLES/NON RÉGLABLES en direct (on n'a pas la donnée fiable) : un marché qui les mentionne
# n'est JAMAIS un « <équipe> vainqueur / double chance ». Sans ce garde-fou, `_winner_side` voyait le nom
# d'équipe dans « Possession de Roma », « Coups francs Roma », « Dégagements Roma »… et classait le marché en
# « Vainqueur » -> réglé sur le SCORE final -> fausses victoires (bug user 2026-09-14). On ne pique QUE ce qu'on
# sait régler avec des stats réelles : score (buts/DC/handicap/BTTS/total) + compteurs live (corners/cartons/
# tirs/tirs cadrés). Tout le reste = rejeté. (corners/cartons/tirs NE figurent PAS ici : ils sont réglables.)
_UNSETTLEABLE_STAT_RE = re.compile(
    r"coups?\s*francs?|d[ée]gagements?|tacles?|interceptions?|touches?|centres?"
    r"|penalt|corners?\s+conc|but\s+contre",
    re.I)

# PROPS JOUEUR (bug user 2026-09-14) : « Tirs cadrés - Lautaro Martinez », « Cartons Dumfries », « X - +0.5 but »…
# On n'a QUE les stats d'ÉQUIPE (API-Football) -> un total/compteur au nom d'un JOUEUR serait réglé sur le total
# d'ÉQUIPE = faux. Détection sans base de joueurs : un marché total/équipe LÉGITIME ne laisse AUCUN token une
# fois retirés le vocabulaire de marché + les noms des 2 équipes ; s'il reste un token (= un nom), c'est un prop.
_PROP_STOP = set("""nombre total totaux de des du d l la le les au aux a à et ou par sur selon opta data
regle réglé reglee réglée regles réglés reglees réglées match matchs equipe équipe equipes équipes domicile
exterieur extérieur plus moins over under superieur supérieur inferieur inférieur egal égal exactement entre
corner corners kick carton cartons jaune jaunes rouge rouges tir tirs cadre cadré cadres cadrés shot shots
faute fautes foul fouls hors jeu horsjeu offside offsides arret arrêt arrets arrêts parade parades save saves
gardien gardiens passe passes pass but buts goal goals point points possession pourcentage pct
mi temps mitemps periode période premiere première seconde deuxieme deuxième 1re 1ere 2e 2eme oui non nul
ere ère eme ème ieme ième nde but""".split())   # fragments d'ordinaux (« 1ère »->« ère » après retrait du chiffre)
# familles réglées sur un TOTAL/COMPTEUR d'équipe -> exposées au piège du nom de joueur (résultat/DC/vainqueur non).
_PROP_GUARD_FAMILIES = frozenset(_ALLOW_COUNTED | {"Total Over", "Total Under", "Total équipe", "Total buts MT"})


def _looks_like_prop(text: str, home: str, away: str) -> bool:
    """Vrai si le libellé est probablement un PROP JOUEUR (nom résiduel après retrait du vocabulaire de marché
    + des 2 équipes). Réservé aux familles total/compteur (cf. `_PROP_GUARD_FAMILIES`)."""
    s = re.sub(r"\([^)]*\)", " ", text or "")               # retirer « (réglé selon Opta Data) »
    s = re.sub(r"[0-9]+(?:[.,][0-9]+)?%?", " ", s)           # retirer nombres / %
    s = re.sub(r"[+\-−:/.,–—]", " ", s)
    team = set()
    for nm in (home, away):
        team |= {t for t in re.split(r"\W+", (nm or "").lower()) if len(t) >= 3}
    return any(len(t) >= 3 and t not in _PROP_STOP and t not in team
              for t in re.split(r"\W+", s.lower()))

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

# OPTIMISATION 3 (2026-09-13) — SURCOTE DE FIN DE MATCH : fait FOOTBALLISTIQUE connu (pas un fit sur nos données)
# -> les buts sont ~25-30 % plus fréquents dans les ~20 dernières minutes. Le Poisson à rythme UNIFORME les
# sous-estime -> sur-confiance sur les Unders TARDIFS (la bande 90-100 % qui échoue). On majore le taux de buts
# à partir de LATE_FROM. Réversible : LATE_UPLIFT_ON. Priors issus de la littérature, non ajustés à l'échantillon.
LATE_UPLIFT_ON = True
LATE_UPLIFT = 0.28        # +28 % de rythme de buts à la 90e (interpolé linéairement depuis LATE_FROM)
LATE_FROM = 70            # minute à partir de laquelle la surcote monte (0 avant)


def _late_factor(minute) -> float:
    """Multiplicateur du taux de buts pour la fin de match (1.0 avant LATE_FROM, jusqu'à 1+LATE_UPLIFT à 90')."""
    if not LATE_UPLIFT_ON:
        return 1.0
    m = minute or 0
    if m <= LATE_FROM:
        return 1.0
    return 1.0 + LATE_UPLIFT * min(1.0, (m - LATE_FROM) / max(1.0, 90.0 - LATE_FROM))


# OPTIMISATION 4 (2026-09-13, user) — TAUX DE BASE SPÉCIFIQUE AU MATCH : le prior « buts attendus » n'est plus la
# moyenne de LIGUE (2.7) mais l'espérance de buts PRICÉE PAR LE MARCHÉ pour CE match, dérivée des vraies cotes
# Over/Under de l'omap (dé-viggées -> λ Poisson qui reproduit P(over)). Le backtest a montré que le prior lazy 2.7
# est le maillon faible. ZÉRO appel API (omap déjà dans le sidecar). Réversible : PREMATCH_PRIOR_ON.
PREMATCH_PRIOR_ON = True
_PREMATCH_CACHE: dict = {}   # mid -> goals90|None (statique pré-match)


def _prematch_goals90(d: dict) -> float | None:
    """Espérance de buts/90 du MATCH selon le marché : dé-vig Over/Under (2.5 puis 3.5/1.5 en repli) de l'omap,
    puis résout le λ Poisson tel que P(total ≥ ligne+1) = proba dé-viggée. None si pas de ligne O/U exploitable."""
    if not PREMATCH_PRIOR_ON:
        return None
    mid = d.get("id")
    if mid in _PREMATCH_CACHE:
        return _PREMATCH_CACHE[mid]
    val = None
    om = d.get("omap") or {}
    for line in (2.5, 3.5, 1.5):
        oo, uo = om.get(f"OVER {line}"), om.get(f"UNDER {line}")
        if isinstance(oo, (int, float)) and isinstance(uo, (int, float)) and oo > 1 and uo > 1:
            po = (1.0 / oo) / (1.0 / oo + 1.0 / uo)     # P(over line) dé-viggée
            need = int(line) + 1                        # P(X ≥ need)
            lo, hi = 0.3, 6.5
            for _ in range(28):                         # bisection : λ tel que _poisson_sf(need, λ) = po
                m = (lo + hi) / 2.0
                if analyses._poisson_sf(need, m) < po:
                    lo = m
                else:
                    hi = m
            val = round((lo + hi) / 2.0, 3)
            break
    _PREMATCH_CACHE[mid] = val
    return val

# OPTIMISATION 2 (2026-09-13, user « injecter pour optimiser au max ») : PRESSION DE TIRS live d'API-Football.
# Les buts sont un signal RARE/bruité ; les TIRS (cadrés surtout) sont un signal DENSE de l'intensité offensive
# réelle -> meilleur estimateur du taux de buts que le score seul. On les convertit en xG-proxy et on les mélange
# au rythme de buts. ⚠️ xG live d'API-Football ÉCARTÉ (calculé ~post-match, peu fiable) -> on part des TIRS.
# Quota protégé : fixture id caché en permanence par match + taux caché 90 s/match. Réversible : STATS_INJECT_ON.
STATS_INJECT_ON = True
# Version du MODÈLE qui produit les suggestions : v2 = tempo (optim 1) + pression de tirs (optim 2), depuis
# 2026-09-13. Estampillée sur chaque snapshot (`mv`) -> on mesure la calibration du NOUVEAU modèle SÉPARÉMENT
# des vieux snapshots (v1 = taux-ligue), sinon la calibration reste polluée des semaines. Incrémenter à chaque
# changement de modèle qui invalide la calibration passée.
MODEL_VERSION = 3         # v3 (2026-09-13) = v2 (tempo+tirs+surcote-fin) + TAUX DE BASE pré-match (omap O/U)
_XG_PER_SOT = 0.32        # xG-proxy par tir CADRÉ (ordre de grandeur usuel)
_XG_PER_OFF = 0.04        # xG-proxy par tir NON cadré
_STATS_RATE_TTL = 180.0   # cache stats live 3 min (user 2026-09-14 « trop d'appels ») : les tirs/corners montent
#                           lentement -> 3 min de latence n'affecte quasi pas le modèle mais DIVISE PAR ~2 les
#                           appels API-Football (~1200/j -> ~600/j sur une journée chargée). Ajustable si besoin.
_STATS_RATE_CACHE: dict = {}   # mid -> (ts, rate90|None)
_FIXID_CACHE: dict = {}        # mid -> fixture_id API-Football (semi-statique)


_AF_STATS_CACHE: dict = {}     # mid -> (ts, stats{home,away}|None) : stats live brutes API-Football, cachées 180 s


def _af_live_stats(mid, home, away, ko, allow_fetch: bool = False):
    """Stats live BRUTES API-Football ({home,away: shots_on/shots_total/corners/fouls/offsides/saves/passes/
    possession…}). Cachées 180 s/match (_STATS_RATE_TTL) ; fixture id caché en permanence (quota). SEUL le fond
    (observe loop, hors event loop) fetch (`allow_fetch`) ;
    l'AFFICHAGE lit le cache (jamais d'appel réseau bloquant dans le rendu). None si indispo."""
    if not mid:
        return None
    import time as _t
    hit = _AF_STATS_CACHE.get(mid)
    if hit and (_t.time() - hit[0]) < _STATS_RATE_TTL:
        return hit[1]
    if not allow_fetch:
        return None
    ss = None
    try:
        from app import apifootball as _AF
        if _AF.configured() and home and away and ko:
            with _AF._client() as cl:
                fid = _FIXID_CACHE.get(mid)
                if fid is None:
                    f = _AF.resolve_fixture(cl, home, away, ko)
                    fid = _FIXID_CACHE[mid] = (f or {}).get("id") or 0
                if fid:
                    ss = (_AF.live_match_stats(cl, fid) or {}).get("stats") or None
    except Exception:
        ss = None
    _AF_STATS_CACHE[mid] = (_t.time(), ss)
    return ss


def _stats_rate90(mid, home, away, ko, minute, allow_fetch: bool = False) -> float | None:
    """goals/90 estimé par la PRESSION DE TIRS live (xG-proxy tirs cadrés/non cadrés des 2 équipes), ramené à 90'.
    None si indispo/flag off."""
    if not STATS_INJECT_ON or not mid or minute is None or minute < 1:
        return None
    ss = _af_live_stats(mid, home, away, ko, allow_fetch)
    if not ss:
        return None

    def _g(side, k):
        v = (ss.get(side) or {}).get(k)
        return v if isinstance(v, (int, float)) else 0
    sot = _g("home", "shots_on") + _g("away", "shots_on")
    tot = _g("home", "shots_total") + _g("away", "shots_total")
    if tot <= 0:
        return None
    off = max(0, tot - sot)
    return (sot * _XG_PER_SOT + off * _XG_PER_OFF) / max(0.05, minute / 90.0)


def _live_vals(mid, home, away, ko, allow_fetch: bool = False):
    """Compteurs live {corners_h/a, cards_h/a, sot_h/a, shots_h/a} depuis les stats API-Football -> pricer les
    marchés d'ÉVÉNEMENTS COMPTÉS (corners/cartons/tirs). None si indispo (-> ces marchés restent non pricés)."""
    ss = _af_live_stats(mid, home, away, ko, allow_fetch)
    if not ss:
        return None

    def _g(side, k):
        v = (ss.get(side) or {}).get(k)
        return int(v) if isinstance(v, (int, float)) else 0
    return {"corners_h": _g("home", "corners"), "corners_a": _g("away", "corners"),
            "cards_h": _g("home", "yellow") + _g("home", "red"),
            "cards_a": _g("away", "yellow") + _g("away", "red"),
            "sot_h": _g("home", "shots_on"), "sot_a": _g("away", "shots_on"),
            "shots_h": _g("home", "shots_total"), "shots_a": _g("away", "shots_total"),
            # Compteurs supplémentaires API-Football (user 2026-09-14 « tout le mesurable ») — mêmes clés que
            # `_af_live_stats`. Servent au pricing ET au règlement des marchés fautes/hors-jeu/arrêts/passes.
            "fouls_h": _g("home", "fouls"), "fouls_a": _g("away", "fouls"),
            "offsides_h": _g("home", "offsides"), "offsides_a": _g("away", "offsides"),
            "saves_h": _g("home", "saves"), "saves_a": _g("away", "saves"),
            "passes_h": _g("home", "passes"), "passes_a": _g("away", "passes"),
            "poss_h": _g("home", "possession"), "poss_a": _g("away", "possession")}


# --- MARCHÉS COMPTÉS SUPPLÉMENTAIRES (fautes / hors-jeu / arrêts / passes / possession) -------------------
# Décision user 2026-09-14 (« tout le mesurable ») : on price ET on règle tout ce qu'API-Football sait mesurer.
# Isolé ici (pas dans analyses._leg_metric, PARTAGÉ avec le pré-match) pour ne rien changer à la sélection
# confiance/value/combos. Modèle = même Poisson que les corners (rythme /90) ; possession = ratio (normale
# rétrécissante). Rates /90 = moyennes de ligue, LES 2 ÉQUIPES CUMULÉES.
_XCOUNT_RATE90 = {"fouls": 22.0, "offsides": 3.6, "saves": 5.4, "passes": 900.0}
_XCOUNT_BASE = {"fouls": "fouls", "offsides": "offsides", "saves": "saves", "passes": "passes",
                "corners": "corners", "cards": "cards", "sot": "sot", "shots": "shots"}
_XCOUNT_FAMILY = {"fouls": "Fautes", "offsides": "Hors-jeu", "saves": "Arrêts",
                  "passes": "Passes", "possession": "Possession"}
# libellé -> famille de compteur (pour le RÈGLEMENT, à partir du family stocké)
_FAM_BASE = {"Corners": "corners", "Cartons": "cards", "Tirs": "shots", "Tirs cadrés": "sot",
             "Fautes": "fouls", "Hors-jeu": "offsides", "Arrêts": "saves", "Passes": "passes"}
_XCOUNT_KW = [   # ordre : le plus spécifique d'abord (hors-jeu avant « jeu », arrêts avant tout)
    (re.compile(r"hors-?jeu|offsides?", re.I), "offsides"),
    (re.compile(r"arr[eê]ts?\s+(?:du\s+)?gardien|parades?|\bsaves?\b", re.I), "saves"),
    (re.compile(r"\bfautes?\b|\bfouls?\b", re.I), "fouls"),
    (re.compile(r"possession", re.I), "possession"),
    (re.compile(r"\bpasses?\b", re.I), "passes"),
]


def _team_distinct_tokens(home: str, away: str):
    """Jetons ≥3 lettres PROPRES à chaque équipe (le partagé est retiré). CRITIQUE (bug user 2026-09-14) :
    « Leeds United » vs « Newcastle United » partagent « united » -> sans ce retrait, « Newcastle United : …»
    matchait Leeds (1er testé) et attribuait les buts de Leeds au marché de Newcastle = FAUX « validé »."""
    ht = {t for t in re.split(r"\W+", (home or "").lower()) if len(t) >= 3}
    at = {t for t in re.split(r"\W+", (away or "").lower()) if len(t) >= 3}
    shared = ht & at
    return ht - shared, at - shared


def _side_of(text: str, home: str, away: str):
    """'HOME'/'AWAY'/None : quelle équipe le libellé cible (jetons PROPRES ≥3 lettres, comparés sur des MOTS
    ENTIERS), None si total/ambigu. Le match par MOT (pas sous-chaîne) évite « real » (Real Betis) reconnu
    DANS « villar-real » -> les 2 équipes vues -> None -> total = faux (bug user 2026-09-15)."""
    words = set(re.split(r"\W+", (text or "").lower()))
    ht, at = _team_distinct_tokens(home, away)
    h, a = any(t in words for t in ht), any(t in words for t in at)
    return "HOME" if (h and not a) else "AWAY" if (a and not h) else None


def _extra_metric(text: str, home: str, away: str):
    """Info d'un marché compté SUPPLÉMENTAIRE (fautes/hors-jeu/arrêts/passes/possession) ou None. Format
    aligné sur `_leg_metric` (metric/scope/dir/side/line/live_ok) pour réutiliser le pricing/règlement."""
    low = (text or "").lower()
    metric = next((m for rx, m in _XCOUNT_KW if rx.search(low)), None)
    if not metric:
        return None
    if "plus" in low or "over" in low or "au moins" in low or "supérieur" in low or "superieur" in low:
        over = True
    elif "moins" in low or "under" in low or "inférieur" in low or "inferieur" in low:
        over = False
    else:
        return None
    nm = re.search(r"(\d+(?:[.,]\d+)?)", low)
    if not nm:
        return None
    line = float(nm.group(1).replace(",", "."))
    side = _side_of(text, home, away)
    if metric == "possession" and side is None:        # possession = toujours par équipe, sinon inexploitable
        return None
    return {"metric": metric, "scope": "match", "dir": "OVER" if over else "UNDER",
            "side": side, "line": line, "live_ok": True}


def _sf(k: int, lam: float) -> float:
    """P(X>=k) Poisson ; approx normale (continuité) pour grand lambda (passes) où le CDF exact rame."""
    if k <= 0:
        return 1.0
    if lam > 60.0:
        z = (k - 0.5 - lam) / math.sqrt(lam)
        return max(0.0, min(1.0, 1.0 - analyses._norm_cdf(z)))
    return analyses._poisson_sf(k, lam)


def _extra_count_pct(info: dict, vals: dict, rem: float):
    """Proba modèle d'un marché compté supplémentaire vu le compteur live + le temps restant. None si indispo."""
    m = info.get("metric")
    if info.get("dir") not in ("OVER", "UNDER") or info.get("line") is None:
        return None
    line, over = info["line"], info["dir"] == "OVER"
    if m == "possession":
        side = info.get("side")
        cur = analyses._as_int((vals or {}).get("poss_h" if side == "HOME" else "poss_a"))
        if not cur:                                    # 0/None = stat absente -> non priçable
            return None
        sd = 4.0 + 8.0 * max(0.0, min(1.0, rem))       # incertitude sur la possession FINALE, rétrécit avec le temps
        p_over = 1.0 - analyses._norm_cdf((line - cur) / sd)
        return p_over if over else 1.0 - p_over
    base, rate = _XCOUNT_BASE.get(m), _XCOUNT_RATE90.get(m)
    if not base or not rate:
        return None
    ch = analyses._as_int((vals or {}).get(f"{base}_h"))
    ca = analyses._as_int((vals or {}).get(f"{base}_a"))
    if ch is None or ca is None:
        return None
    side = info.get("side")
    if side in ("HOME", "AWAY"):
        cur, lam = (ch if side == "HOME" else ca), (rate / 2.0) * rem
    else:
        cur, lam = ch + ca, rate * rem
    if cur > line:
        p_over = 1.0
    else:
        p_over = _sf(int(math.floor(line - cur)) + 1, lam)
    return p_over if over else 1.0 - p_over


# --- MI-TEMPS (1re période) — stage 2, user 2026-09-14 -------------------------------------------------------
# On ne traite QUE la 1re mi-temps et UNIQUEMENT avant la pause (minute < 43) : ainsi le score courant = score
# de 1re période, un signal MT reste FORWARD-LOOKING (on ne parie pas sur du déjà connu) et on réutilise les
# modèles buts existants avec la fenêtre de temps restante DE LA 1re MT. Réglé sur le VRAI score HT
# (`result.raw.periods['1']`, déjà persisté au règlement). 2e mi-temps NON gérée (grain/gate différents).
_HT_STRIP_RE = re.compile(
    r"\b(?:à\s+la\s+|en\s+|de\s+la\s+)?(?:1[eè]re?|premi[eè]re)?\s*mi-?temps\b"
    r"|\b(?:en\s+)?(?:1[eè]re?|premi[eè]re)\s+p[ée]riode\b"
    r"|\b1st\s+half\b|\b1re\b", re.I)


def _ht_period(text: str):
    """'1h' si le marché porte sur la 1re mi-temps/période, None sinon (2e MT et combos MT/fin de match exclus)."""
    low = (text or "").lower()
    if re.search(r"2[eè]me?\b|seconde\s+(?:mi-?temps|p[ée]riode)|\b2e\s|second\s+half", low):
        return None
    if re.search(r"fin\s+de\s+match|temps\s+r[ée]glementaire", low):
        return None                                        # « MT / fin de match » = marché combiné, hors périmètre
    if re.search(r"mi-?temps|premi[eè]re\s+(?:mi-?temps|p[ée]riode)|1[eè]re?\s+(?:mi-?temps|p[ée]riode)"
                 r"|\b1re\b|1st\s+half", low):
        return "1h"
    return None


def _ht_pct(text, wside, info, hs, as_, minute, g90):
    """Proba modèle d'un marché de 1re MT (résultat/DC/total buts) vu le score courant + le temps restant DE LA
    1re MT. None si non modélisable / trop tard. `hs`/`as_` = score courant (= score 1re période, minute<43)."""
    rem1 = max(0.0, (45.0 - (minute or 0)) / 90.0)
    if rem1 <= 0.02:
        return None
    lam90 = (g90 if (isinstance(g90, (int, float)) and g90 > 0) else analyses._FOOT_GOALS_90) * 0.90
    if wside is not None:                                  # résultat / double chance à la MT
        return analyses._foot_result_pct(wside, hs, as_, rem1, goals90=lam90)
    if info.get("metric") in ("goals", "special") and info.get("dir") in ("OVER", "UNDER") \
            and info.get("line") is not None:
        side, line, over = info.get("side"), info["line"], info["dir"] == "OVER"
        if side in ("HOME", "AWAY"):
            cur, lam = (hs if side == "HOME" else as_), (lam90 / 2.0) * rem1
        else:
            cur, lam = hs + as_, lam90 * rem1
        p_over = 1.0 if cur > line else _sf(int(math.floor(line - cur)) + 1, lam)
        return p_over if over else 1.0 - p_over
    return None


def _settle_ht(snap: dict, ht, home: str = "", away: str = ""):
    """'won'/'lost'/'push'/None d'un marché de 1re MT réglé sur le SCORE À LA MI-TEMPS `ht`=(hh,ha).
    None (=void) si score HT indispo : jamais fabriqué."""
    if not ht:
        return None
    hh, ha = ht
    info = dict(snap.get("info") or {})
    wside, sel = snap.get("wside"), (snap.get("sel", "") or "")
    if "marquent" in (snap.get("family") or "") or analyses._is_btts(sel, ""):
        yes = "non" not in sel.lower()
        return "won" if ((hh >= 1 and ha >= 1) == yes) else "lost"
    resht = "home" if hh > ha else "away" if ha > hh else "draw"
    if wside in ("home", "away", "draw"):
        return "won" if wside == resht else "lost"
    if wside in ("1X", "12", "X2"):
        ok = ((wside == "1X" and resht in ("home", "draw"))
              or (wside == "12" and resht in ("home", "away"))
              or (wside == "X2" and resht in ("away", "draw")))
        return "won" if ok else "lost"
    if info.get("metric") in ("goals", "special") and info.get("dir") in ("OVER", "UNDER") \
            and info.get("line") is not None:
        side = _side_of(sel, home, away)               # côté robuste (jetons distincts), None = total MT
        line, over = info["line"], info["dir"] == "OVER"
        cur = (hh if side == "HOME" else ha) if side in ("HOME", "AWAY") else hh + ha
        if cur == line:
            return "push"
        win = (cur > line) if over else (cur < line)
        return "won" if win else "lost"
    return None


def _match_goals90(hs, as_, minute, mid=None, home="", away="", ko=None, allow_fetch: bool = False,
                   pre_g90=None) -> float | None:
    """Taux de buts/90 propre au match = mélange bayésien PRIOR (taux du MARCHÉ pour ce match si dispo, sinon
    taux-ligue) + observé. `observé` = buts RÉELS, enrichis (si dispo) de la PRESSION DE TIRS live (xG-proxy).
    None si TEMPO_BLEND_ON=False. `pre_g90` : espérance de buts pré-match (omap O/U). `allow_fetch` : cf. _stats_rate90."""
    if not TEMPO_BLEND_ON:
        return None
    f = max(0.05, min(1.0, (minute or 0) / 90.0))
    goals = (analyses._as_int(hs) or 0) + (analyses._as_int(as_) or 0)
    obs = float(goals)
    sr = _stats_rate90(mid, home, away, ko, minute, allow_fetch)   # xG-proxy /90 (None si indispo)
    if sr is not None:
        obs = 0.5 * goals + 0.5 * (sr * f)                 # buts réels + xG-proxy accumulé (moitié-moitié)
    prior = pre_g90 if (isinstance(pre_g90, (int, float)) and pre_g90 > 0) else analyses._FOOT_GOALS_90
    rate = (obs + _GOALS90_PRIOR_W * prior) / (f + _GOALS90_PRIOR_W)
    return rate * _late_factor(minute)                     # surcote de fin de match (buts plus fréquents tard)


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
    # Garde-fou racine : un marché de STAT non réglable (possession/fautes/coups francs/dégagements…) ne peut
    # PAS être un vainqueur/DC même si `_winner_side` y a repéré un nom d'équipe (sinon réglé sur le score = faux).
    if wside in ("home", "away", "draw", "1X", "12", "X2") and _UNSETTLEABLE_STAT_RE.search(text):
        return "Autre"
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
    # ÉVÉNEMENTS COMPTÉS (corners/cartons/tirs/tirs cadrés) — totaux match/équipe Plus/Moins (pricés via
    # compteurs live). Handicaps/mi-temps de ces métriques EXCLUS (dir OVER/UNDER + scope match uniquement).
    if metric in ("corners", "cards", "sot", "shots") and scope == "match" and dirn in ("OVER", "UNDER"):
        return {"corners": "Corners", "cards": "Cartons", "sot": "Tirs cadrés", "shots": "Tirs"}[metric]
    if analyses._is_signed_handicap(text) and metric in ("goals", "special"):
        return "Handicap"
    return "Autre"


def _info_lite(info: dict) -> dict:
    """Sous-ensemble JSON-sérialisable de `_leg_metric` nécessaire au RÈGLEMENT final (`_eval_leg`)."""
    return {k: info.get(k) for k in ("metric", "side", "dir", "line", "scope", "handicap", "live_ok")
            if info.get(k) is not None}


def price_catalog(catalog: list, home: str, away: str, hs: int, as_: int, minute,
                  mid=None, ko=None, allow_fetch: bool = False, pre_g90=None) -> list[dict]:
    """Croise chaque marché FIABLE du catalogue live avec le modèle : renvoie [{sel, family, wside, info,
    prob (0-1), odds, ev}] pour les marchés modélisables NON encore verrouillés. Lecture pure (0 réseau)."""
    out: list[dict] = []
    seen: set[str] = set()
    g90 = _match_goals90(hs, as_, minute, mid, home, away, ko, allow_fetch, pre_g90)   # prior marché + tempo + tirs
    vals = _live_vals(mid, home, away, ko, allow_fetch) if ALL_MARKETS_ON else None    # compteurs live (corners/cartons/tirs)
    for e in (catalog or []):
        text = (e.get("text") or "").strip()
        od = e.get("odds")
        if not text or text in seen:
            continue
        if not (isinstance(od, (int, float)) and od > 1):
            continue
        htp = _ht_period(text)                         # marché de 1re mi-temps ? (routé AVANT le ban)
        if not htp and _BAN_TEXT_RE.search(text):      # props/scoreline/2e-MT/période -> jetés
            continue
        # MARCHÉ DE 1re MI-TEMPS (bloc autonome) : émis UNIQUEMENT avant la pause (score courant = score MT ->
        # forward-looking), pricé sur la fenêtre restante de la 1re MT, réglé sur le score de mi-temps. On RETIRE
        # les mots de période pour lire le marché de base (résultat/DC/total buts), que `_leg_metric` classerait
        # sinon « Autre » (scope 1h) et que `_winner_side` ne lirait pas.
        if htp:
            if minute is None or minute >= 43 or text in seen:
                continue
            base = _HT_STRIP_RE.sub(" ", text).strip(" -–—:")
            binfo = analyses._leg_metric({"sel": base}, home, away)
            # DOUBLE CHANCE d'abord (sinon `_winner_side` lit « nul » et renvoie « draw » -> DC mal pricée) :
            _mdc = re.search(r"\b(1X|12|X2)\b", base)
            if _mdc:
                bws = _mdc.group(1)
            elif re.search(r"\bou\s+(?:match\s+)?nul\b", base.lower()):
                _sd = _side_of(base, home, away)
                bws = "1X" if _sd == "HOME" else "X2" if _sd == "AWAY" else None
            else:
                bws = analyses._winner_side(base, "", home, away, "foot") or _dc_pair(base, home, away)
            if analyses._is_btts(base, ""):
                fam = "Les 2 marquent MT"
            elif bws is not None:
                fam = "Résultat MT"
            elif binfo.get("metric") in ("goals", "special") and binfo.get("dir") in ("OVER", "UNDER") \
                    and binfo.get("line") is not None:
                if _looks_like_prop(base, home, away):
                    continue                           # « X marque en 1re MT » = prop joueur -> jeté
                fam = "Total buts MT"
            else:
                continue                               # marché MT non modélisable (compteur MT, etc.)
            prob = _ht_pct(base, bws, binfo, hs, as_, minute, g90)
            if prob is None:
                continue
            seen.add(text)
            _il = _info_lite(binfo)
            _il["period"] = "1h"                       # -> règlement sur le score de MI-TEMPS (result.raw.periods['1'])
            out.append({"sel": text, "family": fam, "wside": bws, "info": _il,
                        "prob": float(prob), "odds": float(od), "ev": float(prob) * float(od) - 1.0})
            continue
        # Marché compté SUPPLÉMENTAIRE (fautes/hors-jeu/arrêts/passes/possession) — détecté/pricé/réglé LOCALEMENT
        # (jamais via analyses._leg_metric, partagé avec le pré-match). Prioritaire : un « Possession de Roma »
        # n'est PAS un vainqueur (c'est le bug 2026-09-14).
        xinfo = _extra_metric(text, home, away) if ALL_MARKETS_ON else None
        if xinfo:
            info, wside, fam = xinfo, None, _XCOUNT_FAMILY[xinfo["metric"]]
        else:
            info = analyses._leg_metric({"sel": text}, home, away)
            wside = analyses._winner_side(text, "", home, away, "foot")
            if wside is None:                          # DC par NOM (catalogue sans jeton « 1X ») -> résolue ici
                wside = _dc_pair(text, home, away)
            fam = _family(info, wside, text)
            # SIDE robuste (bug user 2026-09-15) : `_leg_metric` rate parfois l'équipe d'un TOTAL de buts
            # (« par Villarreal ») -> side=None -> marché d'équipe classé/réglé comme un total = faux. On relit
            # via `_side_of` (jetons distincts) pour figer le bon côté + la bonne famille dès le pricing.
            if wside is None and info.get("metric") in ("goals", "special") and info.get("dir") in ("OVER", "UNDER"):
                _sd = _side_of(text, home, away)
                if _sd and info.get("side") != _sd:
                    info["side"] = _sd
                    if fam in ("Total Over", "Total Under"):
                        fam = "Total équipe"
        if fam not in _ALLOW_FAMILIES and not (ALL_MARKETS_ON and fam in _ALLOW_COUNTED):
            continue
        # PROP JOUEUR (bug user 2026-09-14) : un total/compteur au nom d'un joueur serait réglé sur le total
        # d'ÉQUIPE = faux. On n'a pas la donnée par joueur -> jeté.
        if fam in _PROP_GUARD_FAMILIES and _looks_like_prop(text, home, away):
            continue
        if xinfo:
            prob = _extra_count_pct(xinfo, vals, analyses._foot_remaining(minute))
        else:
            # Déjà tranché par le direct (total franchi / BTTS acquis) = plus une OPPORTUNITÉ de pari live -> skip.
            if analyses._live_locked("foot", text, "", info, hs, as_, vals) in ("won", "lost"):
                continue
            prob = analyses._live_model_pct("foot", text, "", info, wside, hs, as_, minute, vals, goals90=g90)
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
    pre = _prematch_goals90(d)                             # taux de base pré-match (marché O/U de l'omap)
    qual = [p for p in price_catalog(catalog, home, away, hs, as_, minute, mid, d.get("start"),
                                     allow_fetch=True, pre_g90=pre)
            if PROB_MIN <= p["prob"] <= PROB_MAX and EV_MIN <= p["ev"] <= EV_MAX]
    if not qual:
        return 0
    rec = _load("foot", mid) or {"sport": "foot", "match_id": mid, "home": home, "away": away,
                                 "comp": d.get("comp", ""), "start": d.get("start"),
                                 "snaps": [], "settled": False}
    # taux de tirs live utilisé (xG-proxy /90) — LOGGÉ dans chaque snapshot pour pouvoir BACKTESTER l'effet des
    # tirs plus tard (le cache est déjà chaud : price_catalog l'a rempli). None si stats indispo.
    srate = _stats_rate90(mid, home, away, d.get("start"), minute)
    added = 0
    for p in qual:
        last = max((s["minute"] for s in rec["snaps"] if s.get("sel") == p["sel"]), default=None)
        if last is not None and (minute - last) < LOG_GAP_MIN:
            continue                                   # throttle : même pari re-loggé trop tôt
        rec["snaps"].append({
            "minute": minute, "score": f"{hs}-{as_}", "hs": hs, "as": as_,
            "sel": p["sel"], "family": p["family"], "wside": p["wside"], "info": p["info"],
            "prob": round(p["prob"], 4), "odds": round(p["odds"], 3), "ev": round(p["ev"], 4),
            "result": None, "mv": MODEL_VERSION,       # version du modèle -> mesurer le nouveau modèle proprement
            "srate": round(srate, 3) if isinstance(srate, (int, float)) else None,   # tirs live (backtest futur)
        })
        added += 1
    if added:
        _save(rec)
    return added


# --- suggestions COURANTES (affichage seul, sans écriture ni throttle) ------------------------------------
def current_picks(d: dict, top: int = 50) -> list[dict]:
    """Suggestions live ACTUELLES d'un match EN COURS (mêmes marchés/gates que observe_match, mais SANS
    écriture ni throttle) — pour l'AFFICHAGE. Triées par EV décroissant, `top` max (défaut ~« tous » : user
    2026-09-15 veut TOUS les signaux du match, pas un top 3). [] si pas de live/catalogue.
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
    picks = [p for p in price_catalog(catalog, home, away, hs, as_, minute, d.get("id"), d.get("start"),
                                      pre_g90=_prematch_goals90(d))
             if PROB_MIN <= p["prob"] <= PROB_MAX and EV_MIN <= p["ev"] <= EV_MAX]
    picks.sort(key=lambda p: p["ev"], reverse=True)
    return picks[:max(1, top)]


import re as _re
_NUM_RE = _re.compile(r"(\d+(?:[.,]\d+)?)")


def _live_signal_status(sel: str, family: str, hs, as_, home: str, away: str, counts=None) -> str:
    """Statut IRRÉVERSIBLE d'un signal au score/aux stats du direct : 'won' (validé) / 'lost' (tombé) / 'open'.
    PRINCIPE : on ne tranche QUE les métriques MONOTONES (ne redescendent jamais) dispo en match — BUTS (score),
    CORNERS, CARTONS, TIRS, TIRS CADRÉS — en TOTAL comme PAR ÉQUIPE, plus BTTS oui/non. Pour ces marchés :
    Over/Plus acquis dès que la valeur > ligne ; Under/Moins tombé dès que la valeur > ligne ; « marque » acquis
    dès valeur ≥ 1. Tout le reste (résultat / double chance / handicap / vainqueur / mi-temps-période) N'EST PAS
    monotone (peut encore basculer) -> 'open'. `counts` = dict live {corners_h/a, cards_h/a, sot_h/a, shots_h/a}
    (None si stats indispo -> les marchés comptés restent 'open'). Pur calcul, lecture seule."""
    if hs is None or as_ is None:
        return "open"
    low = (sel or "").lower()
    m = _NUM_RE.search(low)
    thr = float(m.group(1).replace(",", ".")) if m else None
    # PÉRIODES (mi-temps / quart) : jamais tranchées ici (métrique de période, pas du match entier).
    if _re.search(r"mi-temps|1[eè]re|2[eè]\b|quart|p[ée]riode", low):
        return "open"

    def _team_val(hv, av):
        """Valeur de l'ÉQUIPE citée dans le libellé (jetons PROPRES ≥3 c, comparés sur des MOTS ENTIERS : le
        partagé « United » retiré + « real » n'est pas reconnu dans « villarreal »), sinon None (= total)."""
        ht, at = _team_distinct_tokens(home, away)
        _w = set(_re.split(r"\W+", low))
        if any(t in _w for t in ht):
            return hv
        if any(t in _w for t in at):
            return av
        return None

    # BTTS (les deux équipes marquent) — oui : acquis dès que les 2 ont marqué ; non : tombé dès que les 2 ont marqué.
    if family == "Les 2 marquent" or ("deux" in low and "marqu" in low) or "btts" in low:
        both = hs > 0 and as_ > 0
        if _re.search(r"\bnon\b|ne\s+marqu|\bpas\b", low):
            return "lost" if both else "open"
        return "won" if both else "open"

    # MÉTRIQUE : objet compté (corners/cartons/tirs + fautes/hors-jeu/arrêts/passes) sinon BUTS. Possession =
    # ratio NON monotone -> pas ici (reste 'open' jusqu'au règlement final). Ordre : « cadr » avant « tir ».
    obj = ("corners" if "corner" in low else "cards" if "carton" in low
           else "sot" if "cadr" in low else "shots" if "tir" in low
           else "offsides" if _re.search(r"hors-?jeu|offside", low) else "fouls" if "faute" in low
           else "saves" if _re.search(r"arr[eê]t|parade|\bsave", low) else "passes" if "passe" in low
           else None)
    if obj is not None:
        if not counts:
            return "open"                                  # pas de stats live -> indéterminable
        hv, av = counts.get(obj + "_h"), counts.get(obj + "_a")
        if hv is None and av is None:
            return "open"
        tv = _team_val(hv or 0, av or 0)
        val = tv if tv is not None else (hv or 0) + (av or 0)
    else:
        # BUTS uniquement : il faut un marché de buts MONOTONE (dit « but(s) », ou famille total, ou « marque »).
        # Sinon = handicap / double chance / vainqueur / résultat -> NON monotone -> 'open'.
        goals_mkt = bool(_re.search(r"\bbuts?\b", low)) or family in ("Total Over", "Total Under", "Total équipe")
        marks = "marqu" in low or "scores" in low
        if not goals_mkt and not marks:
            return "open"
        tv = _team_val(hs, as_)
        val = tv if tv is not None else hs + as_
        # « <équipe> marque » (sans seuil) = équipe Over 0.5 -> acquis dès 1 but.
        if thr is None and tv is not None and marks:
            return "won" if tv >= 1 else "open"

    if thr is None:
        return "open"
    # Sens : Over/Plus -> acquis quand dépassé ; Under/Moins -> tombé quand dépassé. (Under jamais « won » avant FT.)
    if "plus" in low or "over" in low:
        return "won" if val > thr else "open"
    if "moins" in low or "under" in low:
        return "lost" if val > thr else "open"
    return "open"


def _signal_current(sel: str, family: str, hs, as_, home: str, away: str, counts=None) -> str | None:
    """Valeur COURANTE de la métrique d'un signal (nb déjà obtenu) pour l'afficher à côté d'un signal EN COURS :
    « 7 corners », « 0 but », « 3 cartons »… Total ou par équipe selon le libellé. None si marché NON compté
    (résultat/DC/handicap/périodes) ou stats indispo. Pur calcul, lecture seule."""
    if hs is None or as_ is None:
        return None
    low = (sel or "").lower()
    if _re.search(r"mi-temps|1[eè]re|2[eè]\b|quart|p[ée]riode", low):
        return None

    def _tv(hv, av):
        ht, at = _team_distinct_tokens(home, away)          # jetons PROPRES, comparés sur des MOTS ENTIERS
        _w = set(_re.split(r"\W+", low))
        if any(t in _w for t in ht):
            return hv
        if any(t in _w for t in at):
            return av
        return None
    poss = "possession" in low
    obj = ("corners" if "corner" in low else "cards" if "carton" in low
           else "sot" if "cadr" in low else "shots" if "tir" in low
           else "offsides" if _re.search(r"hors-?jeu|offside", low) else "fouls" if "faute" in low
           else "saves" if _re.search(r"arr[eê]t|parade|\bsave", low) else "passes" if "passe" in low
           else None)
    _m = _NUM_RE.search(low)
    line = float(_m.group(1).replace(",", ".")) if _m else None

    def _fmt(val, sing, plur):
        # unité accordée à la LIGNE (« 0.5 carton », « 16.5 corners ») ; « courant / ligne » si ligne connue.
        u = sing if (line is not None and line < 2) else plur
        return f"{val} / {line:g} {u}" if line is not None else f"{val} {plur if val != 1 else sing}"
    if poss:                                               # possession = % par équipe (pas de total additif)
        if not counts:
            return None
        pv = _tv(counts.get("poss_h"), counts.get("poss_a"))
        if not pv:
            return None
        return f"{pv}% / {line:g}%" if line is not None else f"{pv}%"
    if obj is not None:
        if not counts:
            return None
        hv, av = counts.get(obj + "_h"), counts.get(obj + "_a")
        if hv is None and av is None:
            return None
        tv = _tv(hv or 0, av or 0)
        val = tv if tv is not None else (hv or 0) + (av or 0)
        _u = {"corners": ("corner", "corners"), "cards": ("carton", "cartons"),
              "sot": ("tir cadré", "tirs cadrés"), "shots": ("tir", "tirs"),
              "fouls": ("faute", "fautes"), "offsides": ("hors-jeu", "hors-jeu"),
              "saves": ("arrêt", "arrêts"), "passes": ("passe", "passes")}[obj]
        return _fmt(val, _u[0], _u[1])
    # BUTS uniquement (pas handicap/DC/vainqueur)
    if not (_re.search(r"\bbuts?\b", low) or family in ("Total Over", "Total Under", "Total équipe")
            or "marqu" in low or "scores" in low):
        return None
    tv = _tv(hs, as_)
    val = tv if tv is not None else hs + as_
    return _fmt(val, "but", "buts")


def enriched_signals(d: dict, top: int = 50) -> list[dict]:
    """Signaux live d'un match + TRI PAR STATUT au score courant (user 2026-09-14) : chaque pick porte
    `status` = 'won' (validé, acquis en direct) / 'open' (en cours) / 'lost' (tombé). Liste triée : validés
    d'abord, puis en cours (meilleur EV), puis tombés. Les validés/tombés viennent des signaux DÉJÀ déclenchés
    (store) qui ont quitté la bande EV ; les 'open' sont les suggestions courantes. 100 % lecture (0 réseau)."""
    if not LIVE_PICK_ON or d.get("sport") != "foot":
        return []
    from app import match_select
    home, away = d.get("home", ""), d.get("away", "")
    ld = match_select.live_state_for("foot", home, away)
    sc = (ld or {}).get("score") or {}
    hs, as_ = analyses._as_int(sc.get("home")), analyses._as_int(sc.get("away"))
    minute = match_select.live_minute(ld)
    if hs is None or as_ is None or minute is None:
        return []
    open_picks = current_picks(d, top=top)
    open_sels = {p["sel"] for p in open_picks}
    rec = _load("foot", d.get("id")) or {}
    first, last = {}, {}
    for s in rec.get("snaps", []):
        k, mn = s.get("sel"), s.get("minute")
        if k is None:
            continue
        if isinstance(mn, int) and (k not in first or mn < first[k]):
            first[k] = mn
        last[k] = s                                        # snaps ordonnés dans le temps -> dernier gagne
    # compteurs live PAR ÉQUIPE (corners/cartons/tirs) depuis le CACHE (allow_fetch=False -> 0 réseau au rendu)
    # pour trancher les marchés comptés en direct (total ET par équipe).
    counts = _live_vals(d.get("id"), home, away, d.get("start"), allow_fetch=False)
    for p in open_picks:
        p["status"] = "open"
        p["first_min"] = first.get(p["sel"], minute)
        # valeur COURANTE de la métrique (nb déjà obtenu) à afficher à côté du signal en cours (user 2026-09-14).
        p["cur"] = _signal_current(p.get("sel"), p.get("family"), hs, as_, home, away, counts)
    won, lost = [], []
    for sel, s in last.items():
        if sel in open_sels:
            continue
        st = _live_signal_status(sel, s.get("family"), hs, as_, home, away, counts)
        if st in ("won", "lost"):
            rec_p = {"sel": sel, "ev": s.get("ev"), "prob": s.get("prob"), "odds": s.get("odds"),
                     "family": s.get("family"), "status": st, "first_min": first.get(sel, minute)}
            (won if st == "won" else lost).append(rec_p)
    # TOUS les signaux du match (user 2026-09-15 : « affiche tout les signaux, pas seulement 3 »), classés par
    # statut : validés · en cours · tombés. Plus de cap de lisibilité — la carte re-trie par statut de toute façon.
    won.sort(key=lambda p: p.get("first_min") or 0)
    lost.sort(key=lambda p: p.get("first_min") or 0)
    return won + open_picks + lost


def current_all(sport: str = "foot", top: int = 50) -> list[dict]:
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
        # INCLUSION = tout match EN COURS (dans la fenêtre) — on n'exige PLUS le catalogue de cotes live
        # (user 2026-09-13 « Signaux Live apparaît bien plus tard ») : le catalogue met ~25 s à se réchauffer
        # (boucle de fond) -> attendre le faisait apparaître en retard. Le match s'affiche TOUT DE SUITE ; sans
        # catalogue, `current_picks` renvoie [] -> placeholder « ⏳ cotes live en cours… » et les pronos se
        # remplissent dès que le catalogue est chaud. Reste persistant tant que le match est en direct.
        ld = match_select.live_state_for(sport, d.get("home", ""), d.get("away", ""))
        sc = (ld or {}).get("score") or {}
        hs, as_ = analyses._as_int(sc.get("home")), analyses._as_int(sc.get("away"))
        minute = match_select.live_minute(ld)
        score = f"{hs}-{as_}" if (hs is not None and as_ is not None) else ""
        # Signaux + STATUT (validé / en cours / tombé), triés — `first_min` déjà posé par enriched_signals.
        picks = enriched_signals(d, top=top)
        out.append({"home": d.get("home", ""), "away": d.get("away", ""), "comp": d.get("comp", ""),
                    "mid": d.get("id"), "minute": minute, "score": score, "picks": picks,
                    "has_catalog": bool(analyses.live_catalog(d.get("id")))})   # cotes live chaudes ou non
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


def _settle_count(snap: dict, vals: dict):
    """'won'/'lost'/'push'/None pour un marché COMPTÉ (corners/cartons/tirs/fautes/hors-jeu/arrêts/passes/
    possession), réglé sur le TOTAL FINAL RÉEL (`vals` = _live_vals en fin de match). None (=void) si la stat
    finale manque : on NE FABRIQUE JAMAIS un résultat (règle user 2026-09-14)."""
    info = dict(snap.get("info") or {})
    fam = snap.get("family")
    line, dirn, side = info.get("line"), info.get("dir"), info.get("side")
    if line is None or dirn not in ("OVER", "UNDER"):
        return None
    over = dirn == "OVER"
    if fam == "Possession":
        cur = analyses._as_int((vals or {}).get("poss_h" if side == "HOME" else "poss_a"))
        if not cur:
            return None
    else:
        base = _FAM_BASE.get(fam)
        ch = analyses._as_int((vals or {}).get(f"{base}_h")) if base else None
        ca = analyses._as_int((vals or {}).get(f"{base}_a")) if base else None
        if ch is None or ca is None:
            return None
        cur = (ch if side == "HOME" else ca) if side in ("HOME", "AWAY") else ch + ca
    if cur == line:
        return "push"
    win = (cur > line) if over else (cur < line)
    return "won" if win else "lost"


def _settle_snap(snap: dict, fh: int, fa: int, vals: dict | None = None, ht=None, home: str = "", away: str = ""):
    """'won'/'lost'/'push'/None pour un snapshot vu le score FINAL (+ totaux finaux `vals` pour les compteurs,
    + score mi-temps `ht` pour les marchés 1re MT). Résultat/DC à la main ; totaux/handicap buts via `_eval_leg` ;
    BTTS sur les deux scores ; compteurs sur leur vrai total final ; marchés MT sur le score de mi-temps."""
    fam, wside = snap.get("family"), snap.get("wside")
    sel = snap.get("sel", "") or ""
    # MI-TEMPS (1re période) : réglé sur le SCORE À LA MI-TEMPS, jamais sur le score final.
    if (snap.get("info") or {}).get("period") == "1h":
        return _settle_ht(snap, ht, home, away)
    # DÉFENSE (bug user 2026-09-14) : ne JAMAIS régler sur le score un marché qui parle d'une stat non réglable
    # (coups francs/dégagements/tacles…), même s'il a été mal classé « Vainqueur/DC » jadis. -> void.
    if _BAN_TEXT_RE.search(sel) or (wside and _UNSETTLEABLE_STAT_RE.search(sel)):
        return None
    # PROP JOUEUR : jamais réglable sur une stat d'équipe -> void (même défense qu'au pricing).
    if fam in _PROP_GUARD_FAMILIES and _looks_like_prop(sel, home, away):
        return None
    # COMPTEURS : réglés sur le total FINAL réel (jamais sur les buts).
    if fam in _FAM_BASE or fam == "Possession":
        return _settle_count(snap, vals or {})
    info0 = dict(snap.get("info") or {})
    # HANDICAP buts : la ligne signée (+0.5, -1.5) N'EST PAS stockée dans l'info (metric 'special') -> relue du
    # libellé (bug : 0/85 réglés). Réglé sur le score final ajusté du handicap. Push si nul après ajustement.
    if fam == "Handicap" or info0.get("handicap"):
        ln = info0.get("line")
        if ln is None:
            ln = analyses._signed_line(sel)
        side = info0.get("side")
        if ln is not None and side in ("HOME", "AWAY"):
            ah, aa = fh + (ln if side == "HOME" else 0.0), fa + (ln if side == "AWAY" else 0.0)
            if ah == aa:
                return "push"
            return "won" if (side == "HOME") == (ah > aa) else "lost"
        return None
    if fam == "Les 2 marquent":
        yes = "non" not in sel.lower()
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
    # SIDE robuste (bug user 2026-09-15, Villarreal 1-2) : `analyses._leg_metric` rate parfois l'équipe
    # (« par Villarreal ») -> side=None -> marché d'ÉQUIPE réglé sur le TOTAL = faux. On RELIT le côté du
    # libellé via `_side_of` (jetons distincts) ; None = vrai total du match.
    if info.get("metric") in ("goals", "special") and info.get("dir") in ("OVER", "UNDER"):
        _sd = _side_of(sel, home, away)
        info["side"] = _sd if _sd in ("HOME", "AWAY") else None
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
        # Totaux FINAUX réels (corners/fautes/hors-jeu/arrêts/passes/possession) pour régler les compteurs —
        # les stats API-Football persistent après le coup de sifflet final (même appel `/fixtures?id=`).
        need_counts = any((s.get("family") in _FAM_BASE or s.get("family") == "Possession")
                          and s.get("result") not in ("won", "lost", "push") for s in rec.get("snaps", []))
        fvals = (_live_vals(rec.get("match_id"), rec.get("home", ""), rec.get("away", ""),
                            rec.get("start"), allow_fetch=True) if need_counts else None)
        # Score MI-TEMPS pour régler les marchés 1re période (déjà persisté au règlement : result.raw.periods['1']).
        ht = None
        _p1 = (((d.get("result") or {}).get("raw") or {}).get("periods") or {}).get("1")
        if isinstance(_p1, (list, tuple)) and len(_p1) == 2 and _p1[0] is not None and _p1[1] is not None:
            ht = (int(_p1[0]), int(_p1[1]))
        for s in rec.get("snaps", []):
            if s.get("result") in ("won", "lost", "push"):
                continue
            s["result"] = _settle_snap(s, fh, fa, fvals, ht, rec.get("home", ""), rec.get("away", ""))
        rec["settled"] = True
        rec["final"] = f"{fh}-{fa}"                     # score final -> affichage « terminés »
        _save(rec)
        n += 1
    return n


_PM_TIER_CACHE: dict = {}       # mid -> (ts, tier|None) : tier du pari pré-match d'un match, caché ~120 s


def _prematch_tier(mid):
    """Tier du pari pré-match JOUÉ (confiance/value) du match, ou None. Sert à MARQUER les signaux d'un match
    déjà couvert par un pari pré-match (user 2026-09-14 : transparence du doublon). Caché ~120 s. Lecture seule."""
    if not mid:
        return None
    import time as _t
    hit = _PM_TIER_CACHE.get(mid)
    if hit and (_t.time() - hit[0]) < 120.0:
        return hit[1]
    tier = None
    try:
        d = analyses.meta("foot", mid)
        if d and d.get("stat_bet"):
            tier = analyses.tier_of(d)
    except Exception:
        tier = None
    _PM_TIER_CACHE[mid] = (_t.time(), tier)
    return tier


# familles dont le RÈGLEMENT est connu (source réelle) — toute autre famille réglée = anomalie à investiguer.
_KNOWN_SETTLE_FAMILIES = (set(_FAM_BASE) | {
    "Possession", "Vainqueur", "Double chance", "Handicap", "Total Over", "Total Under",
    "Total équipe", "Les 2 marquent", "Résultat MT", "Total buts MT", "Les 2 marquent MT"})


def audit() -> dict:
    """SUIVI CONTINU de l'intégrité du track live (user 2026-09-14). Compte, sur les signaux RÉGLÉS, les
    anomalies qui trahiraient un règlement fabriqué : prop joueur réglé sur un total d'équipe, stat non réglable
    réglée sur le score, compteur sans ligne, famille de règlement inconnue. 0 partout = sain. Lecture seule."""
    settled = bad_prop = bad_unsettleable = bad_noline = unknown_family = 0
    for rec in _iter_records():
        h, a = rec.get("home", ""), rec.get("away", "")
        for s in rec.get("snaps", []):
            if s.get("result") not in ("won", "lost", "push"):
                continue
            settled += 1
            fam, sel, info = s.get("family", ""), (s.get("sel", "") or ""), (s.get("info") or {})
            if fam not in _KNOWN_SETTLE_FAMILIES:
                unknown_family += 1
            if fam in _PROP_GUARD_FAMILIES and _looks_like_prop(sel, h, a):
                bad_prop += 1
            if _UNSETTLEABLE_STAT_RE.search(sel):
                bad_unsettleable += 1
            if fam in _FAM_BASE and info.get("line") is None:
                bad_noline += 1
    return {"settled": settled, "bad_prop": bad_prop, "bad_unsettleable": bad_unsettleable,
            "bad_noline": bad_noline, "unknown_family": unknown_family}


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
                    "mid": rec.get("match_id"), "final": rec.get("final", ""), "start": st,
                    "picks": list(seen.values())})
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
    canon, allsnaps, distinct = [], [], []
    dist_with, dist_without = [], []          # signaux distincts : match AVEC vs SANS pari pré-match (doublon)
    matches = pending = 0
    for rec in _iter_records():
        matches += 1
        settled = [s for s in rec.get("snaps", []) if s.get("result") in ("won", "lost", "push")]
        pending += sum(1 for s in rec.get("snaps", []) if s.get("result") not in ("won", "lost", "push"))
        allsnaps.extend(settled)
        # DISTINCT : 1 signal par (match, sel) = 1re détection (les snapshots d'un même signal sont corrélés ->
        # ne pas les compter N fois). Sert au détail PAR MARCHÉ (tous les types séparés, stats justes).
        _seen: dict = {}
        for s in settled:
            _seen.setdefault(s.get("sel"), s)
        _dv = list(_seen.values())
        distinct.extend(_dv)
        # DOUBLON (user 2026-09-14) : ce match a-t-il déjà un pari pré-match confiance/value ? -> split transparence.
        (dist_with if _prematch_tier(str(rec.get("match_id"))) else dist_without).extend(_dv)
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

    def _calib(snaps):
        buckets: dict[int, list] = {}
        for s in snaps:
            if s["result"] not in ("won", "lost"):
                continue
            b = min(9, int(s.get("prob", 0) * 10))
            buckets.setdefault(b, [0, 0, 0.0])
            buckets[b][0] += 1
            buckets[b][1] += 1 if s["result"] == "won" else 0
            buckets[b][2] += s.get("prob", 0.0)
        return [{"bucket": f"{b*10}-{b*10+10}%", "n": v[0],
                 "model": round(100.0 * v[2] / v[0], 1) if v[0] else 0.0,
                 "real": round(100.0 * v[1] / v[0], 1) if v[0] else 0.0}
                for b, v in sorted(buckets.items())]

    by_fam: dict[str, list] = {}
    for s in canon:
        by_fam.setdefault(s.get("family", "?"), []).append(s)
    # PAR MARCHÉ (TOUS les types séparés, user 2026-09-14) — sur les signaux DISTINCTS (pas seulement le pick
    # canonique) : chaque famille (Vainqueur / DC / Total Over / Under / Total équipe / BTTS / Corners / Cartons /
    # Tirs / Tirs cadrés…) a sa ligne dès qu'elle a ≥1 signal réglé.
    by_fam_all: dict[str, list] = {}
    for s in distinct:
        by_fam_all.setdefault(s.get("family", "?"), []).append(s)

    # MODÈLE COURANT (v2 = tempo+tirs) mesuré À PART des vieux snapshots (v1 = taux-ligue) -> calibration non
    # polluée. `mv` absent = legacy v1.
    _cur = [s for s in allsnaps if s.get("mv", 1) >= MODEL_VERSION]
    _cur_canon = [c for c in canon if c.get("mv", 1) >= MODEL_VERSION]
    _by_mv: dict = {}
    for s in allsnaps:
        _by_mv[s.get("mv", 1)] = _by_mv.get(s.get("mv", 1), 0) + 1

    # AGRÉGAT GLOBAL sur les signaux DISTINCTS (1 par match×marché) + ventilations par COTE / par MINUTE de
    # création (user 2026-09-14 « stats complètes visibles »). `distinct` = déjà 1 signal réglé par (match, sel).
    _COTE_BANDS = [(1.0, 1.2), (1.2, 1.4), (1.4, 1.6), (1.6, 2.0), (2.0, 3.0), (3.0, 99.0)]
    _MIN_BANDS = [(0, 15), (15, 30), (30, 45), (45, 60), (60, 75), (75, 130)]
    by_cote = {f"{lo:.2f}–{hi:.2f}": _roi([s for s in distinct if lo <= s.get("odds", 0) < hi])
               for lo, hi in _COTE_BANDS}
    by_minute = {f"{lo}–{hi}'": _roi([s for s in distinct if lo <= s.get("minute", 0) < hi])
                 for lo, hi in _MIN_BANDS}

    _res = {
        "matches": matches, "snaps_total": len(allsnaps) + pending, "snaps_pending": pending,
        "settled_by_model": _by_mv, "model_version": MODEL_VERSION,
        "canonical": _roi(canon),
        "distinct": _roi(distinct),                    # TOUS les signaux distincts réglés (pas juste 1/match)
        "by_cote": by_cote, "by_minute": by_minute,
        # DOUBLON pré-match (transparence user 2026-09-14) : signaux sur matchs AVEC vs SANS pari confiance/value.
        "by_prematch": {"Match déjà en Confiance/Value": _roi(dist_with),
                        "Match sans pari pré-match": _roi(dist_without)},
        "all_settled": _roi([s for s in allsnaps if s["result"] in ("won", "lost", "push")]),
        "by_family": {fam: _roi(rows) for fam, rows in sorted(by_fam.items())},
        # tous les marchés séparés, triés par volume décroissant (le plus « travaillé » en tête).
        "by_family_all": {fam: _roi(rows) for fam, rows
                          in sorted(by_fam_all.items(), key=lambda kv: -len(kv[1]))},
        "calibration": _calib(allsnaps),
        # mesure du NOUVEAU modèle SEULEMENT (démarre à ~0, se remplit au fil des matchs post-optim) :
        "current_model": {"n_settled": len(_cur), "canonical": _roi(_cur_canon), "calibration": _calib(_cur)},
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
