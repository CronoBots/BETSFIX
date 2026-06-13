"""Génère les analyses « analyste » des matchs IMPORTANTS (top-N/sport par profondeur de marché)
en pilotant Claude en HEADLESS (`claude -p`) sur l'abonnement Pro Max.

⚠️ DOIT tourner en session `vince` (où Claude est connecté), PAS dans le service API (SYSTEM,
non authentifié). Écrit chaque analyse dans data/analyses/{sport}_{id}.md (cache 6 h).

Méthodo : confidence-first (classer les paris par CHANCE DE PASSER vs cotes Unibet réelles),
faits recherchés sur le web par Claude (≥2 sources), jamais inventés. Cf. mémoire projet.

Usage (RUN DE MESURE d'abord, petit) :
    python tools/generate_analyses.py --sport foot --top 1
    python tools/generate_analyses.py --sport foot,tennis,basket --top 10
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Console Windows en cp1252 : les ✓ / · / emojis des logs crasheraient (UnicodeEncodeError).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import httpx  # noqa: E402

from app import sources  # noqa: E402
from app import value  # noqa: E402
from app.match_select import UNIBET_B, UNIBET_PARAMS, fetch_important  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "analyses")
UA = {"User-Agent": "Mozilla/5.0"}
CACHE_HOURS = 6
# API locale (uvicorn SYSTEM, port 8000) : réutilise le chemin SofaScore qui marche déjà
# (curl_cffi anti-403 + repli RapidAPI) au lieu de ré-implémenter l'auth ici.
LOCAL_API = "http://127.0.0.1:8000"
# Cadence anti-blocage SofaScore (le repli RapidAPI est épuisé jusqu'au reset mensuel) :
SOFA_GAP = 1.2     # secondes entre 2 appels SofaScore d'un même match
SCAN_GAP = 2.0     # secondes entre 2 matchs (lisse la charge ; négligeable vs ~90s de Claude)

# Marchés à JETER du dossier (bruit) : on garde résultat/totaux/BTTS/handicaps/mi-temps.
NOISE = ("corner", "ntervalle", "ntervalle", "0:00", "10:00", "14:59", "Premier buteur",
         "Premier but", "Score exact", "Score Exact", "Asiatique", "Marque au moins",
         "Pari remboursé", "buteur", "2ème mi-temps", "2e mi-temps", "remboursé")
# Sélection des marchés Unibet pour le dossier : au plus _PER_CRIT lignes par TYPE de marché
# (sinon basket/tennis — des centaines de lignes Handicap/Total quasi identiques — noient le dossier
# sous un seul type ; l'analyste doit voir un ÉVENTAIL varié de marchés pour LES 3 SPORTS), et
# _MAX_MK_LINES lignes au total.
_PER_CRIT = 3
_MAX_MK_LINES = 28

METHODO = (
    "Tu es mon analyste paris sportifs PROFESSIONNEL. Objectif : des pronostics SÛRS et bien fondés, "
    "pas du volume. Mieux vaut 1 pari béton (ou aucun) que 3 moyens.\n\n"
    "RECHERCHE WEB OBLIGATOIRE d'abord (>=2 sources FIABLES et RÉCENTES, cite-les entre parenthèses ; "
    "n'invente JAMAIS — si une info manque ou est incertaine, écris-le explicitement). Vérifie selon "
    "le sport :\n"
    "• FOOT : composition probable / titulaires & ABSENTS (blessés, suspendus, repos), enjeu réel "
    "(amical vs compétition, rotation), forme des 5 derniers AVEC adversaire+score, dynamique "
    "domicile/extérieur, météo si pertinent, contexte (derby, fin de saison…).\n"
    "• TENNIS : SURFACE (et bilan du joueur SUR cette surface), classement ATP/WTA et écart, forme "
    "récente sur surface, fatigue/calendrier (match la veille, voyages), historique H2H, abandons "
    "récents, conditions (indoor/outdoor). ⚠️ DÉBUT de tournoi / CHANGEMENT de surface = upsets "
    "fréquents : un favori court SANS preuve de forme récente sur LA surface (cf. forme 14 jours des "
    "DONNÉES MULTI-SOURCES) n'est PAS un pari sûr — baisse ta proba ou SKIP.\n"
    "• BASKET : absents/blessés clés & temps de repos (back-to-back), rythme (pace) des 2 équipes, "
    "forme à domicile/extérieur, enjeu (tanking, playoffs). PROPS JOUEUR : compare la LIGNE du marché "
    "(ex. « passes +5.5 ») à la MOYENNE SAISON et à la FORME 5 derniers du bloc DONNÉES JOUEURS — ne "
    "joue un prop QUE si moyenne ET forme récente dépassent nettement la ligne (ou sont nettement en "
    "dessous pour un « Moins »), et tiens compte des absents (rôle/minutes qui changent).\n"
    "Les cotes ci-dessous sont celles d'UNIBET (réelles) : ne les invente jamais.\n\n"
    "DONNÉES FOURNIES CI-DESSOUS — ce sont des FAITS, sers-t'en en PRIORITÉ sur le narratif :\n"
    "• SÉRIES SOFASCORE (forme récente factuelle, déjà mappées aux marchés). Base principale. Si une "
    "série contredit ton intuition web, suis la série.\n"
    "• DONNÉES MULTI-SOURCES (ESPN / FotMob / Understat) : forme avec adversaires+scores, classements "
    "À JOUR, blessés/absents nominatifs, H2H, xG, météo, fatigue/back-to-back. Source indépendante "
    "n°2 : un fait présent ici ET confirmé par ta recherche web (ou SofaScore) = 2 sources "
    "concordantes. Les BLESSÉS listés ici sont fiables et récents — intègre-les TOUJOURS.\n"
    "• SENTIMENT (votes communauté) : signal d'appoint, jamais décisif seul.\n"
    "• H2H (confrontations directes) quand fourni.\n\n"
    "RÈGLE DE SÉLECTION (clé) : ne retiens un pari QUE s'il est soutenu par AU MOINS 2 éléments "
    "factuels CONCORDANTS (ex. série + contexte, ou stat + forme + H2H). Un seul argument = pas assez. "
    "Classe par CHANCE DE PASSER (probabilité réelle), PAS par edge contrarien. Sois HONNÊTE sur la "
    "proba (pas de gonflage). Un match incertain = moins de paris, voire SKIP.\n\n"
    "ANCRE SHARP (Pinnacle) : quand un « CONSENSUS SHARP » est fourni, c'est la proba la PLUS proche du "
    "VRAI (book sharp à faible marge). Sers-t'en comme ancre PRIORITAIRE : si TA proba et Pinnacle "
    "convergent et que la cote Unibet les BAT (EV+ indiqué), c'est le signal de value le plus fiable ; "
    "si tu diverges FORTEMENT de Pinnacle sans raison factuelle solide, c'est probablement TOI qui as "
    "tort -> prudence ou SKIP.\n\n"
    "VALUE — DÉTECTION SYSTÉMATIQUE (clé du ROI) : chaque issue du bloc COTES porte sa PROBA JUSTE "
    "« (jXX%) » = la proba du marché MARGE RETIRÉE (de-vig), et chaque marché sa « [marge X%] ». "
    "Procédure pour CHAQUE pari envisagé : (1) estime TA proba à partir des FAITS ; (2) compare-la à la "
    "proba juste jXX% de cette issue ; il y a VALUE si TA proba dépasse jXX% d'AU MOINS ~5 points. "
    "(3) Ne retiens que des paris à la fois SÛRS (ta proba ≥ 65 %) ET porteurs de value (ta proba > jXX%). "
    "Si ta proba ≤ jXX%, le marché te paie MOINS que le risque -> écarte. Balaie TOUS les marchés fournis "
    "(vainqueur, totaux, handicaps, sets/jeux, props joueur…), pas seulement le 1X2, et garde les "
    "meilleures value. PRIVILÉGIE les marchés à FAIBLE marge (lignes principales, ~3-6 % : plus efficients "
    "et fiables) ; MÉFIE-TOI des marges élevées (≥8 %, souvent props/exotiques : le book s'y protège). "
    "Indique la value dans l'explication (ex. « ma proba ~72 % vs juste 64 % -> value +12 % d'EV »).\n\n"
    "Rends ensuite ton analyse en respectant EXACTEMENT la structure ci-dessous — MÊMES titres, MÊME "
    "ordre, MÊME tableau — pour TOUS les sports (affiché tel quel dans l'app). AUCUN titre en haut, "
    "AUCUNE autre section.\n\n"
    "## 📋 Les faits\n"
    "Puces CONCISES et SOURCÉES. Pour CHAQUE équipe/joueur : forme (5 derniers : V/N/D + adversaire + "
    "score), absents/blessés clés, contexte (domicile/neutre, surface, enjeu), séries marquantes "
    "fournies. Sources entre parenthèses. Termine par H2H + le facteur DÉCISIF du match.\n\n"
    "## 📊 Paris classés par chance de passer\n"
    "Tableau markdown avec EXACTEMENT ces colonnes, du PLUS PROBABLE au moins probable. **N'AJOUTE PAS "
    "de pari faible juste pour remplir** : mets UNIQUEMENT les paris réellement intéressants — 1, 2 ou 3 "
    "lignes (souvent 1-2), et ZÉRO ligne si le match n'offre rien de solide.\n"
    "RÈGLES DURES (notre historique RÉEL de règlements l'exige) :\n"
    "• Ne liste AUCUN pari dont ta proba honnête est < 65 % — sur notre historique, les paris annoncés "
    "50-64 % ne passent qu'à ~50 % : ils ruinent le taux de réussite. S'il ne reste qu'1 pari, c'est "
    "très bien. S'il n'en reste aucun, tableau VIDE et SKIP.\n"
    "• Cote ≥ 1.70 : zone TRÈS dangereuse (39 % de réussite réelle chez nous). N'en liste un QUE si ta "
    "proba ≥ 70 % ET ≥ 3 éléments factuels concordants dont AU MOINS UN des DONNÉES MULTI-SOURCES "
    "(blessure adverse majeure, fatigue/back-to-back, série limpide). Sinon, ne le liste pas.\n"
    "| Pari | Cote | Proba | Risque |\n"
    "(Proba = TON estimation honnête en %, juste le nombre + %. Risque = EXACTEMENT 🟢 sûr ou 🟠 "
    "moyen. JAMAIS de 🔴 risqué (un pari risqué n'est pas une reco). Écarte les marchés inexploitables "
    "(props sans nom, cotes 0.00) et IGNORE toute cote < 1.10 — gain négligeable, jamais un pari.)\n\n"
    "## 🎯 Verdict\n"
    "IMPORTANT : écris UNE puce PAR pari du tableau ci-dessus, DANS LE MÊME ORDRE et avec EXACTEMENT la "
    "même sélection (chaque pari DOIT avoir son explication — n'en oublie aucun). Format EXACT de chaque "
    "puce :\n"
    "`- **<Rôle> :** <Sélection exacte du tableau> @<cote> — <explication>`\n"
    "où <Rôle> = « Pari 1 » pour la 1re ligne (la plus probable), puis « Pari 2 », « Pari 3 » s'il y en a. "
    "L'<explication> doit être LA PLUS COMPLÈTE, PRÉCISE et UTILE possible POUR CE PARI PRÉCIS (pas un "
    "commentaire général du match) : 2 à 4 phrases factuelles qui justifient CETTE sélection — les "
    "éléments concordants chiffrés (forme récente, % / moyennes, surface, classement, repos/calendrier, "
    "absents, H2H), POURQUOI ça passe, le risque principal, ET la value (proba estimée vs cote implicite). "
    "Français impeccable : MAJUSCULE en début de chaque phrase, ponctuation correcte, AUCUN remplissage.\n"
    "- **À éviter / SKIP :** ce qui est piégeux ; si le match est un coin-flip, dis-le et recommande "
    "de SKIP (ne rien jouer est une décision gagnante).\n\n"
    "## 💰 Mise\n"
    "Une phrase : mise PLATE et petite EXPRIMÉE EN % DE BANKROLL (ex. « 1 à 2 % de la bankroll »), "
    "JAMAIS en « unités »/« u » ni en €, JAMAIS de combiné, 1 à 2 paris max par jour. Factuel, en français.\n\n"
    "Enfin, AJOUTE EN DERNIÈRE LIGNE, pour le règlement auto, au format EXACT `PICK: <CODE>` "
    "correspondant à TON Pari 1 (le plus probable). HOME = 1re équipe/joueur, AWAY = 2e. UNIQUEMENT un "
    "de ces codes (privilégie un marché RÉGLABLE pour ton Pari 1) :\n"
    "- Total buts/points : `OVER 2.5` / `UNDER 3.5`\n"
    "- Total d'une ÉQUIPE : `TEAMTOT HOME OVER 1.5` / `TEAMTOT AWAY UNDER 85.5`\n"
    "- Handicap : `HCAP HOME -1.5` / `HCAP AWAY +10.5` (ligne signée)\n"
    "- Les deux marquent : `BTTS YES` / `BTTS NO`\n"
    "- Résultat 1X2 (foot) : `1X2 1` / `1X2 X` / `1X2 2`\n"
    "- Double chance (foot) : `DC 1X` / `DC 12` / `DC X2`\n"
    "- Vainqueur (tennis/basket) : `WIN HOME` / `WIN AWAY`\n"
    "- Au moins un set (tennis) : `SET HOME` / `SET AWAY`\n"
    "- Vainqueur d'un set : `SETWIN 1 HOME` / `SETWIN 2 AWAY`\n"
    "- Score exact en sets (tennis) : `SETSCORE 2 0` (sets HOME-AWAY)\n"
    "- Total jeux d'un set : `SETGAMES 1 OVER 7.5` / `SETGAMES 1 UNDER 9.5`\n"
    "- Total jeux du match : `TOTGAMES OVER 20.5` / `TOTGAMES UNDER 22.5`\n"
    "- 1er jeu de service tenu : `HOLD1 HOME YES` / `HOLD1 AWAY NO`\n"
    "Si ton Pari 1 n'entre dans AUCUN code, écris `PICK: NONE`. Une seule ligne.\n"
)


STORE_FILE = {"foot": "tracking_foot.json", "tennis": "tracking_tennis.json",
              "basket": "tracking_basket.json"}
_LEGACY = {"tennis": "tracking.json"}   # ancien nom, repli avant migration


def _load_store(sport: str) -> dict:
    for fn in (STORE_FILE.get(sport), _LEGACY.get(sport)):
        if not fn:
            continue
        try:
            with open(os.path.join(ROOT, "data", fn), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def _norm(s: str) -> set:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z]+", s) if len(t) >= 3}


def _fiche_id(sport: str, match: dict, store: dict) -> str | None:
    """ID utilisé par la FICHE pour lier l'analyse. Foot : id Unibet (= clé du store, mappée via
    match_id côté fiche). Tennis/basket : id Sofa (clé du store), retrouvé par correspondance de
    noms UNIQUE (sinon None -> on ne génère pas, jamais de mauvaise liaison)."""
    if sport == "foot":
        return str(match["id"])
    mh, ma = _norm(match.get("home")), _norm(match.get("away"))
    if not mh or not ma:
        return None
    hits = []
    for k, r in store.items():
        rh, ra = _norm(r.get("home")), _norm(r.get("away"))
        if (rh & mh and ra & ma) or (rh & ma and ra & mh):
            hits.append(str(k))
    return hits[0] if len(hits) == 1 else None


def _purge_duplicates(sport: str, fid: str, m: dict) -> None:
    """REMPLACE l'ancien scan d'un MÊME match re-publié sous un AUTRE id (Unibet qui re-liste,
    reprogrammation pluie, id Sofa résolu différemment) : supprime les sidecars du même sport aux
    MÊMES équipes dont le coup d'envoi est à ≤ 30 h du nouveau — SAUF s'ils sont déjà RÉGLÉS
    (un match d'une série de playoffs déjà joué = de l'historique, on n'y touche jamais)."""
    new_ts = _kickoff_ts(m.get("start") or "")
    mh, ma = _norm(m.get("home", "")), _norm(m.get("away", ""))
    if new_ts is None or not mh or not ma:
        return
    for p in glob.glob(os.path.join(OUT, f"{sport}_*.json")):
        oid = os.path.basename(p)[len(sport) + 1:-5]
        if oid == str(fid):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        oh, oa = _norm(d.get("home", "")), _norm(d.get("away", ""))
        if not ((oh & mh and oa & ma) or (oh & ma and oa & mh)):
            continue
        ots = _kickoff_ts(d.get("start") or "")
        if ots is None or abs(ots - new_ts) > 30 * 3600:
            continue                       # trop éloigné = autre manche de la série, on garde
        settled = bool((d.get("result") or {}).get("score")) or any(
            b.get("result") for b in (d.get("bets") or []))
        if settled:
            continue                       # déjà réglé = historique/track record, intouchable
        for ext in (".json", ".md"):
            try:
                os.remove(p[:-5] + ext)
            except OSError:
                pass
        print(f"  · doublon remplacé : {sport}_{oid} ({d.get('name', '?')}) -> {sport}_{fid}")


def _fresh(path: str) -> bool:
    try:
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        return age_h < CACHE_HOURS
    except OSError:
        return False


_SOFA_SPORT = {"foot": "football", "tennis": "tennis", "basket": "basketball"}
_SCHED_CACHE: dict = {}   # (sport, jour) -> events : 1 SEUL appel scheduled-events par sport/jour


async def _scheduled(sport: str, day: str) -> list:
    """Planning SofaScore d'un sport pour un jour, MIS EN CACHE (1 appel par sport/jour au lieu
    d'un par match). Cache aussi l'échec (liste vide) pour ne pas re-marteler un endpoint bloqué."""
    key = (sport, day)
    if key in _SCHED_CACHE:
        return _SCHED_CACHE[key]
    from app import sofa_http
    path = _SOFA_SPORT.get(sport)
    evs = []
    if path and day:
        try:
            r = await sofa_http.get(
                f"https://api.sofascore.com/api/v1/sport/{path}/scheduled-events/{day}")
            if r.status_code == 200:
                evs = (r.json() or {}).get("events") or []
        except Exception:
            evs = []
    _SCHED_CACHE[key] = evs
    return evs


def _kickoff_ts(start: str):
    """Timestamp (s) du coup d'envoi Unibet (ISO) — pour départager les manches d'une série."""
    try:
        return datetime.fromisoformat((start or "").replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


async def _resolve_sofa(sport: str, match: dict) -> str | None:
    """Résout l'id SofaScore d'un match (noms + DATE/HEURE). On scanne le planning de jour-1/jour/
    jour+1 (un match à 00:30 UTC peut être listé la VEILLE côté SofaScore) et, parmi les events aux
    BONS NOMS, on garde celui dont le coup d'envoi est le PLUS PROCHE de l'heure Unibet -> départage
    les SÉRIES de playoffs (mêmes équipes plusieurs fois). Repli : API de recherche. None si rien."""
    start = match.get("start") or ""
    day = start[:10]
    if not day or sport not in _SOFA_SPORT:
        return None
    mh, ma = _norm(match.get("home", "")), _norm(match.get("away", ""))
    if not mh or not ma:
        return None
    target = _kickoff_ts(start)
    days = {day}
    try:
        d0 = datetime.fromisoformat(day)
        days |= {(d0 - timedelta(days=1)).strftime("%Y-%m-%d"),
                 (d0 + timedelta(days=1)).strftime("%Y-%m-%d")}
    except ValueError:
        pass
    cand = []   # (id, startTimestamp) des events aux bons noms (sur jour-1/jour/jour+1)
    for dd in days:
        for ev in await _scheduled(sport, dd):
            h = _norm(((ev.get("homeTeam") or {}).get("name") or ""))
            a = _norm(((ev.get("awayTeam") or {}).get("name") or ""))
            if (h & mh and a & ma) or (h & ma and a & mh):
                cand.append((str(ev.get("id")), ev.get("startTimestamp")))
    if cand:
        if target is not None:        # le coup d'envoi le plus PROCHE (≠ une autre manche de la série)
            cand.sort(key=lambda c: abs((c[1] or 0) - target))
        return cand[0][0]
    return await _resolve_sofa_search(sport, match, day, mh, ma, target)   # repli recherche


async def _resolve_sofa_search(sport: str, match: dict, day: str, mh: set, ma: set,
                               target=None) -> str | None:
    """Repli : /search/all?q=... -> événement du BON sport, ≥1 équipe en commun, dont le coup d'envoi
    est le PLUS PROCHE de l'heure Unibet (±36 h max -> écarte les autres manches)."""
    from app import sofa_http
    import urllib.parse
    q = urllib.parse.quote(f"{match.get('home', '')} {match.get('away', '')}".strip())
    try:
        r = await sofa_http.get(f"https://api.sofascore.com/api/v1/search/all?q={q}")
        results = (r.json() or {}).get("results") or [] if r.status_code == 200 else []
    except Exception:
        return None
    want = _SOFA_SPORT.get(sport)
    best = None   # (écart_temps, id)
    for res in results:                                  # résultats déjà classés par pertinence
        if res.get("type") != "event":
            continue
        ent = res.get("entity") or {}
        sp = (((ent.get("tournament") or {}).get("category") or {}).get("sport") or {}).get("slug")
        if want and sp and sp != want:                   # bon sport
            continue
        h = _norm(((ent.get("homeTeam") or {}).get("name") or ""))
        a = _norm(((ent.get("awayTeam") or {}).get("name") or ""))
        if not (h & mh or h & ma or a & mh or a & ma):   # au moins UNE équipe en commun
            continue
        ts = ent.get("startTimestamp")
        if target is not None and ts:
            gap = abs(ts - target)
            if gap > 36 * 3600:                          # trop loin -> autre manche, on écarte
                continue
            if best is None or gap < best[0]:
                best = (gap, str(ent.get("id")))
            continue
        if ts and day:                                   # pas d'heure cible -> au moins la bonne date
            try:
                if datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") != day:
                    continue
            except (ValueError, OSError, OverflowError):
                pass
        return str(ent.get("id"))
    return best[1] if best else None


async def _tennis_extras(client: httpx.AsyncClient, sofa_id: str, home: str, away: str):
    """Données SofaScore TENNIS pour le dossier : classement ATP/WTA + écart, surface (et son poids),
    H2H, votes. (La forme via /events/last est bloquée 403 -> on s'appuie sur le web pour ça.)"""
    from app import sofa_http
    base = "https://api.sofascore.com/api/v1"
    try:
        r = await sofa_http.get(f"{base}/event/{sofa_id}")
        ev = (r.json() or {}).get("event") or {} if r.status_code == 200 else {}
    except Exception:
        ev = {}
    ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
    rh, ra = ht.get("ranking"), at.get("ranking")
    surface = ev.get("groundType") or (ev.get("tournament") or {}).get("groundType")
    tour = (ev.get("tournament") or {}).get("name")
    # CIRCUIT (WTA/ATP) : catégorie du tournoi si explicite, sinon GENRE des joueurs (M->ATP, F->WTA).
    cat = (((ev.get("tournament") or {}).get("category") or {}).get("name") or "").upper()
    g = (ht.get("gender") or at.get("gender") or "").upper()
    circuit = ("WTA" if "WTA" in cat else "ATP" if "ATP" in cat
               else "WTA" if g == "F" else "ATP" if g == "M" else "")
    await asyncio.sleep(SOFA_GAP)
    hw = aw = None
    try:
        h = await sofa_http.get(f"{base}/event/{sofa_id}/h2h")
        td = (h.json() or {}).get("teamDuel") or {} if h.status_code == 200 else {}
        hw, aw = td.get("homeWins"), td.get("awayWins")
    except Exception:
        pass
    votes = await _fetch_votes(client, "tennis", sofa_id)
    facts, sx = [], {}
    if circuit:
        sx["circuit"] = circuit
    if rh or ra:
        facts.append(f"Classement officiel : {home} #{rh or '?'} vs {away} #{ra or '?'}")
    if surface:
        facts.append(f"Surface : {surface} (le bilan SUR cette surface prime — vérifie-le sur le web)")
    if tour:
        facts.append(f"Tournoi : {tour}")
    if hw is not None and aw is not None and (hw or aw):
        facts.append(f"H2H (confrontations directes) : {home} {hw}-{aw} {away}")
        sx["h2h"] = {"home_wins": hw, "away_wins": aw, "draws": 0}
    if votes and votes[0] is not None:
        facts.append(f"Sentiment (votes communauté, appoint) : {home} {votes[0]}% / {away} {votes[1]}%")
    txt = ("\n\nDONNÉES SOFASCORE TENNIS (factuel — base à croiser avec ta recherche web "
           "forme/surface) :\n- " + "\n- ".join(facts)) if facts else ""
    return txt, sx


async def _sofa_extras(client: httpx.AsyncClient, sport: str, sofa_id: str | None,
                       home: str, away: str) -> str:
    """Séries SofaScore + H2H + votes, récupérés via l'API locale (best-effort, "" si indispo).

    Réutilise les endpoints existants (/foot|/basket/match/{id}/{streaks,h2h,votes}) qui passent
    déjà par curl_cffi anti-403 + repli RapidAPI. Tennis : classement + surface + H2H + votes via
    SofaScore direct (cf. `_tennis_extras`). Renvoie (texte_dossier, meta_structurée)."""
    if not sofa_id:
        return "", {}
    if sport == "tennis":
        return await _tennis_extras(client, sofa_id, home, away)
    if sport not in ("foot", "basket"):
        return "", {}

    async def _get(ep: str):
        try:
            r = await client.get(f"{LOCAL_API}/{sport}/match/{sofa_id}/{ep}", timeout=20)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    # Appels SÉQUENTIELS (pas de rafale de 3 simultanés) + court délai : on ménage SofaScore
    # (plus de filet RapidAPI ce mois-ci → éviter de re-déclencher le blocage IP).
    streaks = await _get("streaks")
    await asyncio.sleep(SOFA_GAP)
    h2h = await _get("h2h")
    await asyncio.sleep(SOFA_GAP)
    votes = await _fetch_votes(client, sport, sofa_id)   # caché : partagé avec le sidecar
    out = []
    sx = {}   # données STRUCTURÉES pour le sidecar -> la fiche les rend sans rappeler SofaScore
    if streaks and (streaks.get("general") or streaks.get("head_to_head")):
        def _side(items, side):
            return " ; ".join(f"{s['name']}: {s['value']}" for s in items
                              if s.get("side") == side and s.get("value"))
        gen = streaks.get("general") or []
        sh, sa = _side(gen, "home"), _side(gen, "away")
        lines = ["SÉRIES SOFASCORE (forme récente factuelle, mappées aux marchés — base PRINCIPALE "
                 "pour classer les paris) :"]
        if sh:
            lines.append(f"- [{home}] {sh}")
        if sa:
            lines.append(f"- [{away}] {sa}")
        hh = streaks.get("head_to_head") or []
        if hh:
            lines.append("- [H2H] " + " ; ".join(f"{s['name']}: {s['value']}" for s in hh
                                                  if s.get("value")))
        if len(lines) > 1:
            out.append("\n".join(lines))
        # structuré (listes JSON-sérialisables) pour le bloc Tendances de la fiche
        sx["streaks"] = {
            "home": [[s.get("name"), s.get("value")] for s in gen if s.get("side") == "home" and s.get("value")],
            "away": [[s.get("name"), s.get("value")] for s in gen if s.get("side") == "away" and s.get("value")],
            "h2h": [[s.get("name"), s.get("value")] for s in hh if s.get("value")],
        }
    if h2h and any(h2h.get(k) is not None for k in ("homeWins", "awayWins", "draws")):
        out.append(f"H2H (confrontations directes) : {home} {h2h.get('homeWins', 0)} - "
                   f"{h2h.get('awayWins', 0)} {away} ({h2h.get('draws', 0)} nuls)")
        sx["h2h"] = {"home_wins": h2h.get("homeWins"), "away_wins": h2h.get("awayWins"),
                     "draws": h2h.get("draws")}
    if votes and votes[0] is not None:
        nul = f" / nul {votes[2]}%" if votes[2] is not None else ""
        out.append(f"SENTIMENT (votes communauté, signal d'appoint) : {home} {votes[0]}%"
                   f"{nul} / {away} {votes[1]}%")
    return (("\n\n" + "\n".join(out)) if out else ""), sx


async def build_dossier(client: httpx.AsyncClient, match: dict, sport: str = "foot",
                        sofa_id: str | None = None) -> str | None:
    """Dossier compact : marchés Unibet utiles (hors bruit) + séries/H2H/votes SofaScore. None si indispo."""
    try:
        r = await client.get(f"{UNIBET_B}/betoffer/event/{match['id']}.json",
                             params=UNIBET_PARAMS, headers=UA)
        bo = r.json()
    except Exception:
        return None
    by_crit: dict = {}   # type de marché -> [variantes] (préserve l'ordre Unibet)
    for b in bo.get("betOffers", []) or []:
        crit = (b.get("criterion") or {}).get("label", "")
        if not crit or any(s in crit for s in NOISE):
            continue
        ocs = [o for o in (b.get("outcomes") or []) if o.get("odds")]
        # DE-VIG : proba JUSTE (marge retirée) par issue + marge du marché -> ancre de value pour
        # l'analyste (value = SA proba > proba juste « j% »). Calcul sur TOUTES les issues cotées.
        _, margin = value.annotate([{"odds": o["odds"] / 1000} for o in ocs])
        fair = value.devig([o["odds"] / 1000 for o in ocs])[0]
        outs = []
        for o, p in zip(ocs, fair):
            od = o["odds"] / 1000
            if od < 1.10:          # cote < 1.10 = gain négligeable -> jamais un pari, on l'écarte
                continue
            lbl = o.get("label") or o.get("englishLabel") or "?"
            ln = o.get("line")
            lns = f" {ln / 1000:g}" if ln is not None else ""
            outs.append(f"{lbl}{lns}={od:.2f} (j{p * 100:.0f}%)")
        if outs:
            by_crit.setdefault(crit, []).append(" | ".join(outs) + f"  [marge {margin * 100:.0f}%]")
    # Diversité (cf. _PER_CRIT/_MAX_MK_LINES) : éventail varié de marchés pour les 3 sports.
    lines = []
    for crit, variants in by_crit.items():
        for v in variants[:_PER_CRIT]:
            lines.append(f"- {crit}: {v}")
        if len(lines) >= _MAX_MK_LINES:
            break
    if not lines:
        return None
    home, away = match.get("home", ""), match.get("away", "")
    odds = _result_odds(bo)
    imp = ""   # proba IMPLICITE du marché (marge retirée) = ancre de calibrage + détection de value
    o1, ox, o2 = odds if odds else (None, None, None)
    if o1 and o2:
        inv = [1 / o1, (1 / ox if ox else 0.0), 1 / o2]
        s = sum(inv)
        if s > 0:
            ph, pd, pa = inv[0] / s, inv[1] / s, inv[2] / s
            parts = [f"{home} {ph * 100:.0f}%"] + ([f"nul {pd * 100:.0f}%"] if ox else []) + [f"{away} {pa * 100:.0f}%"]
            imp = ("\nPROBA IMPLICITE DU MARCHÉ (vainqueur, marge retirée) : " + " / ".join(parts)
                   + " — CALIBRE ta proba là-dessus : nettement AU-DESSUS = value (signale-la) ; "
                   "en dessous = écarte le pari.")
    # CONSENSUS SHARP (Pinnacle) : proba la PLUS proche du vrai (book sharp, faible marge). EV au prix
    # Unibet (proba_sharp × cote_unibet − 1) -> une EV+ ici = la cote Unibet bat le sharp = VALUE FORTE.
    sharp = ""
    try:
        from app import pinnacle
        sp = await asyncio.to_thread(pinnacle.sharp_probs, home, away, sport)
    except Exception:
        sp = None
    if sp and o1 and o2:
        seg = [f"{home} {sp['home'] * 100:.0f}%"] \
            + ([f"nul {sp['draw'] * 100:.0f}%"] if sp.get("draw") else []) \
            + [f"{away} {sp['away'] * 100:.0f}%"]
        evh, eva = sp["home"] * o1 - 1, sp["away"] * o2 - 1
        evseg = [f"{home} {evh * 100:+.0f}%", f"{away} {eva * 100:+.0f}%"]
        sharp = ("\nCONSENSUS SHARP (Pinnacle, book de référence — proba la PLUS proche du vrai) : "
                 + " / ".join(seg) + ". EV au prix Unibet : " + " / ".join(evseg)
                 + " — une EV+ ICI = la cote Unibet BAT le sharp = VALUE FORTE ; ancre n°1 pour calibrer "
                   "(si ta proba et Pinnacle convergent contre Unibet, c'est le meilleur signal).")
    extras, sx = await _sofa_extras(client, sport, sofa_id, home, away)
    # Sources GRATUITES indépendantes (ESPN/FotMob/Understat) : forme+scores, classements frais,
    # blessés, H2H, xG, météo — la source n°2 de la méthodo quand SofaScore est bloqué.
    alt = await sources.extras(client, sport, match)
    # DONNÉES JOUEURS (basket) : moyennes saison + forme des joueurs cités dans les PROPS -> parier
    # les props (points/rebonds/passes…) avec des chiffres. Joueurs lus dans `participant` des marchés.
    pblock = ""
    if sport == "basket":
        players = [o.get("participant") for b in bo.get("betOffers", []) or []
                   if "joueur" in ((b.get("criterion") or {}).get("label") or "").lower()
                   for o in (b.get("outcomes") or []) if o.get("participant")]
        if players:
            from app import player_stats
            pblock = await asyncio.to_thread(player_stats.props_block, players)
    text = (f"MATCH: {match['name']} ({match['comp']}, coup d'envoi {match['start']})\n"
            "COTES UNIBET BELGIQUE REELLES (n'invente AUCUNE cote) — chaque issue porte sa PROBA JUSTE "
            "« (jXX%) » (marge retirée) et chaque marché sa « [marge X%] ». VALUE = ta proba > jXX% "
            "(détaille la procédure value plus haut) :\n" + "\n".join(lines)
            + imp + sharp + extras + alt + pblock)
    meta = {"odds": odds, **sx}   # odds + streaks/h2h structurés -> sidecar
    return text, meta


def run_claude(prompt: str, timeout: int = 360) -> str:
    """Lance Claude en headless sur l'abonnement et renvoie l'analyse (stdout)."""
    exe = shutil.which("claude") or "claude"
    p = subprocess.run([exe, "-p", "--dangerously-skip-permissions"], input=prompt,
                       text=True, capture_output=True, timeout=timeout, encoding="utf-8")
    return (p.stdout or "").strip()


def _result_odds(bo: dict) -> tuple:
    """(o1, ox, o2) du marché VAINQUEUR DU MATCH, par type d'issue (OT_ONE/OT_CROSS/OT_TWO) —
    robuste pour les 3 sports : « Temps réglementaire » (foot) / « Cotes du match » (tennis/basket,
    y compris « - Prolongations incluses »). (None,None,None) sinon."""
    for b in bo.get("betOffers", []) or []:
        crit = ((b.get("criterion") or {}).get("label") or "").lower()
        if "cotes du match" in crit or "temps réglementaire" in crit or "temps reglementaire" in crit:
            o = {oc.get("type"): oc.get("odds", 0) / 1000 for oc in (b.get("outcomes") or [])}
            o1, ox, o2 = o.get("OT_ONE"), o.get("OT_CROSS"), o.get("OT_TWO")
            if o1 and o2:
                return (o1, ox, o2)
    return (None, None, None)


def _parse_pick(analysis: str) -> str:
    """Extrait le CODE de la ligne technique `PICK: <CODE>` (pour le règlement auto). '' sinon."""
    m = re.search(r"^\s*PICK:\s*(.+?)\s*$", analysis, re.M)
    if not m:
        return ""
    code = re.sub(r"[`*]", "", m.group(1)).strip().upper()
    return "" if code in ("", "NONE") else code


def _safe_pick(analysis: str) -> str:
    """Extrait le Pari 1 (le plus probable) du Verdict : « sélection @ cote ». '' sinon. Gère le
    nouveau libellé « Pari 1 » ET l'ancien « Le plus sûr » (analyses déjà générées)."""
    m = re.search(r"(?:Pari\s*1|Le plus s[ûu]r)\s*:?\**\s*(.+)", analysis)
    if not m:
        return ""
    txt = re.sub(r"\*\*|\*", "", m.group(1)).strip()
    mm = re.search(r"(.+?@\s*[\d]+[.,][\d]+)", txt)   # garde tout jusqu'à « @ cote » inclus
    if mm:
        return mm.group(1).strip()
    txt = re.split(r"\s[—–-]\s", txt)[0].strip()       # sinon coupe à la justification (—/–/-)
    return txt[:90]


_VOTES_CACHE: dict = {}   # (sport, sofa_id) -> votes : évite de récupérer les votes 2× par match


async def _fetch_votes(client: httpx.AsyncClient, sport: str, sofa_id: str | None):
    """Votes communauté (%home, %away, %draw) via l'API locale, MIS EN CACHE (dossier + sidecar
    partagent le même appel). None sinon. Tennis : endpoint /matches/{id}/votes (préfixe distinct)."""
    if sport not in ("foot", "basket", "tennis") or not sofa_id:
        return None
    key = (sport, str(sofa_id))
    if key in _VOTES_CACHE:
        return _VOTES_CACHE[key]
    url = (f"{LOCAL_API}/matches/{sofa_id}/votes" if sport == "tennis"
           else f"{LOCAL_API}/{sport}/match/{sofa_id}/votes")
    res = None
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200:
            j = r.json()
            if j.get("home_percent") is not None:
                res = (j["home_percent"], j.get("away_percent"), j.get("draw_percent"))
    except Exception:
        res = None
    _VOTES_CACHE[key] = res
    return res


async def _sofa_url(sofa_id) -> str | None:
    """URL publique SofaScore du match (customId + slug). None si id non exploitable."""
    sid = str(sofa_id or "")
    if not sid.isdigit() or len(sid) > 8:
        return None
    from app import sofa_http
    try:
        r = await sofa_http.get(f"https://api.sofascore.com/api/v1/event/{sid}")
        ev = (r.json() or {}).get("event") or {} if r.status_code == 200 else {}
    except Exception:
        return None
    cid, slug = ev.get("customId"), ev.get("slug")
    return f"https://www.sofascore.com/{slug}/{cid}" if (cid and slug) else None


def _write_sidecar(sport: str, fid: str, sofa_id: str, m: dict, meta: dict, analysis: str,
                   votes=None, sofa_url: str | None = None) -> None:
    """Métadonnées de l'analyse (équipes, compétition, coup d'envoi, cotes 1X2, pick, votes, +
    séries/H2H STRUCTURÉS + liens SofaScore/Unibet) -> sidecar JSON. La fiche rend tout depuis ce
    fichier, SANS rappeler SofaScore (une fois analysé, plus aucune raison d'appeler SofaScore)."""
    o1, ox, o2 = (meta.get("odds") if meta else None) or (None, None, None)
    side = {"sport": sport, "id": str(fid), "sofa_id": str(sofa_id),
            "home": m.get("home", ""), "away": m.get("away", ""),
            "name": m.get("name", ""), "comp": m.get("comp", ""), "start": m.get("start", ""),
            "o1": o1, "ox": ox, "o2": o2, "pick": _safe_pick(analysis),
            "pick_code": _parse_pick(analysis),   # code technique pour le règlement auto après match
            "unibet_url": (f"https://fr.unibetsports.be/betting/sports/event/{m.get('id')}"
                           if m.get("id") else None),
            "sofa_url": sofa_url,
            "generated": datetime.now(timezone.utc).isoformat()}
    if meta and meta.get("streaks"):
        side["streaks"] = meta["streaks"]
    if meta and meta.get("h2h"):
        side["h2h"] = meta["h2h"]
    circuit = m.get("circuit") or (meta.get("circuit") if meta else None)   # Unibet (path) prioritaire
    if circuit:
        side["circuit"] = circuit
    if votes and votes[0] is not None:
        side["pub_home"], side["pub_away"] = votes[0] / 100, votes[1] / 100
        if len(votes) > 2 and votes[2] is not None:
            side["pub_draw"] = votes[2] / 100
    with open(os.path.join(OUT, f"{sport}_{fid}.json"), "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="foot", help="foot,tennis,basket (séparés par virgule)")
    ap.add_argument("--top", type=int, default=3,
                    help="top N matchs par sport/jour (défaut lean : 3 — qualité > quantité)")
    ap.add_argument("--hours", type=int, default=24,
                    help="fenêtre : ne scanner que les matchs à venir dans N heures (défaut 24)")
    ap.add_argument("--force", action="store_true", help="ignore le cache 6 h")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    # Le scan AUTORISE les gros endpoints (scheduled-events) via proxy : il les met en cache
    # (1/sport/jour), donc la conso reste minime — contrairement à l'app live qui les refuse.
    from app import sofa_http
    sofa_http.allow_bulk_proxy = True
    sports = [s.strip() for s in args.sport.split(",") if s.strip()]
    total_t0 = time.time()
    n_gen = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for sport in sports:
            try:
                top = await fetch_important(sport, args.top, client, within_hours=args.hours)
            except Exception as e:
                print(f"[{sport}] sélection échouée : {e}")
                continue
            store = _load_store(sport)
            print(f"[{sport}] {len(top)} matchs sélectionnés (profondeur de marché).")
            for m in top:
                fid = _fiche_id(sport, m, store)   # id que la fiche utilise pour lier l'analyse
                if not fid and sport in ("tennis", "basket"):
                    # AUTONOME : pas dans le store -> on résout l'id SofaScore par noms + date
                    # (scheduled-events), au lieu de sauter le match.
                    fid = await _resolve_sofa(sport, m)
                # REPLI quand SofaScore est indisponible (API verrouillée côté SofaScore) : on NE SAUTE
                # PLUS le match. On prend l'id UNIBET comme clé de fiche (comme le foot le fait déjà) :
                # l'analyse est générée depuis Unibet et s'affiche normalement (la fiche se lie aux cotes
                # par NOM, pas par id Sofa). `sofa_id` restera vide -> le match ne se règle pas auto tant
                # qu'un id Sofa n'est pas résolu, mais l'OPPORTUNITÉ n'est pas perdue.
                unibet_fallback = False
                if not fid:
                    fid = str(m.get("id") or "")
                    if not fid:
                        print(f"  · {m['name']} : aucun id exploitable (ni Sofa ni Unibet), on saute.")
                        continue
                    unibet_fallback = True
                    print(f"  · {m['name']} : id SofaScore introuvable -> repli id Unibet (réglage différé).")
                path = os.path.join(OUT, f"{sport}_{fid}.md")
                if not args.force and _fresh(path):
                    print(f"  · {m['name']} : analyse fraîche en cache, on saute.")
                    continue
                # id SofaScore pour les séries/H2H/votes. tennis/basket : la clé du store EST l'id
                # Sofa. foot : champ match_id du store si présent, sinon résolution autonome
                # (scheduled-events), sinon repli sur l'id Unibet (séries indispo mais analyse OK).
                if unibet_fallback:
                    sofa_id = ""                 # pas d'id Sofa fiable -> règlement différé (pas de faux id)
                elif sport == "foot":
                    sofa_id = str((store.get(fid) or {}).get("match_id") or "")
                    if not sofa_id or sofa_id == fid:
                        sofa_id = await _resolve_sofa(sport, m) or fid
                else:
                    sofa_id = fid
                built = await build_dossier(client, m, sport=sport, sofa_id=sofa_id)
                if not built:
                    print(f"  · {m['name']} : pas de marchés exploitables, on saute.")
                    continue
                doss, meta = built
                t0 = time.time()
                try:
                    analysis = run_claude(METHODO + doss)
                except subprocess.TimeoutExpired:
                    print(f"  ✗ {m['name']} : timeout Claude.")
                    continue
                dt = time.time() - t0
                if not analysis:
                    print(f"  ✗ {m['name']} : sortie vide.")
                    continue
                # MODE STRICT : tableau de paris VIDE (aucun pari ≥ seuil) -> match NON RETENU.
                # On n'écrit RIEN (ni .md ni sidecar) et on RETIRE un éventuel scan précédent du
                # même match s'il n'est pas réglé. Le match pourra être ré-analysé au scan suivant
                # (compos/blessures publiées entre-temps peuvent débloquer un pari fiable).
                from app import analyses as _an
                if not _an._parse_bets(_an._bets_section(analysis) or ""):
                    print(f"  · {m['name']} : aucun pari ≥ seuil -> match écarté (non retenu, {dt:.0f}s).")
                    side_p = os.path.join(OUT, f"{sport}_{fid}.json")
                    try:
                        old = json.load(open(side_p, encoding="utf-8"))
                        settled = (bool((old.get("result") or {}).get("score"))
                                   or any(b.get("result") for b in (old.get("bets") or [])))
                    except (OSError, ValueError):
                        settled = False
                    if not settled:                # jamais toucher un match réglé (historique)
                        for ext in (".json", ".md"):
                            try:
                                os.remove(os.path.join(OUT, f"{sport}_{fid}{ext}"))
                            except OSError:
                                pass
                    continue
                # Pas d'entête « # {nom} » : la fiche affiche déjà le nom du match (doublon évité).
                header = f"<!-- généré {datetime.now(timezone.utc).isoformat()} · {dt:.0f}s -->\n\n"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header + analysis + "\n")
                votes = await _fetch_votes(client, sport, sofa_id)
                surl = await _sofa_url(sofa_id)
                _write_sidecar(sport, fid, sofa_id, m, meta, analysis, votes, surl)   # méta -> board
                _purge_duplicates(sport, fid, m)   # le scan le plus récent REMPLACE l'ancien
                n_gen += 1
                print(f"  ✓ {m['name']} : {len(analysis)} car. en {dt:.0f}s -> {os.path.basename(path)}")
                await asyncio.sleep(SCAN_GAP)   # lisse la charge SofaScore entre 2 matchs
    print(f"\nTerminé : {n_gen} analyse(s) générée(s) en {time.time() - total_t0:.0f}s. Dossier : {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
