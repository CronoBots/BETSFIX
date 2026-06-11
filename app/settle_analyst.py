"""Règlement automatique du pari « le plus sûr » des analyses, APRÈS match.

Score via SofaScore (Unibet ne garde pas les résultats finis) : `event/{id}` donne le score final,
les scores par set (period1/2/3 = jeux) et `firstToServe` ; `event/{id}/point-by-point` donne, jeu
par jeu, qui sert (`serving`) et qui gagne (`scoring`). Permet de régler aussi les marchés fins
(total jeux d'un set, 1er jeu de service tenu…). Réglé UNE fois puis caché dans le sidecar
(`result`). On ne règle que ce qu'on peut prouver — sinon « non vérifiable », jamais de devinette.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re

from app import analyses, sofa_http
from app.netconst import SOFA_B as _SOFA   # source unique (cf. app/netconst.py)

log = logging.getLogger("betsfix.settle")
_SPORT_PATH = {"foot": "football", "tennis": "tennis", "basket": "basketball"}
# Version de la LOGIQUE de règlement : à incrémenter quand de nouveaux marchés deviennent réglables
# (-> re-règlement unique des sidecars depuis `result.raw` caché, sans appel réseau). v2 = + handicap
# (HCAP), total d'équipe (TEAMTOT), score exact en sets (SETSCORE). v3 = parseur durci : abréviation
# « pts/pt » des points + désambiguïsation des noms partagés entre les 2 camps (ex. « Ironi », « Maria »).
# v4 = nettoyage du contexte entre parenthèses + jeux par set tous ordres + vainqueur « Set N : Nom ».
# v5 = camp lu AVANT la parenthèse (marché lu sur tout) + « au moins 1 set » (chiffre).
# v6 = total de sets + CARTONS/CORNERS réglés depuis event/{id}/statistics (jaunes/rouges/corners).
# v7 = stocke la confiance (prob) de chaque pari réglé -> alimente la page calibration.
# v8 = « premier à X points » réglé via event/{id}/incidents (FIRSTTO).
# v9 = handicap en SETS (tennis) réglé via SETHCAP (sur sets_home/away).
# v10 = handicap au moins Unicode (−) + « total de sets : moins de N » (SETSTOT).
_SETTLE_VERSION = 10


# --------------------------------------------------------------- règlement (pur, depuis le score)
def settle_pick(code: str, score: dict) -> str | None:
    """'won'/'lost'/'push' selon le CODE et le score. None = non réglable ici (cf. HOLD1 -> async).
    score = {home, away, sets_home, sets_away, periods:{n:(h,a)}}."""
    if not code or not score:
        return None
    parts = code.upper().split()
    kind = parts[0]
    h, a = score.get("home"), score.get("away")
    sh, sa = score.get("sets_home"), score.get("sets_away")
    periods = score.get("periods") or {}
    has_ha = h is not None and a is not None

    def _per(n):
        return periods.get(n) or periods.get(str(n))

    if kind in ("OVER", "UNDER") and has_ha and len(parts) > 1:
        try:
            line = float(parts[1])
        except ValueError:
            return None
        total = h + a
        return "push" if total == line else ("won" if ((total > line) == (kind == "OVER")) else "lost")
    if kind == "BTTS" and has_ha:
        both = h > 0 and a > 0
        return "won" if (both == (len(parts) < 2 or parts[1] == "YES")) else "lost"
    if kind == "1X2" and has_ha and len(parts) > 1:
        res = "1" if h > a else ("2" if a > h else "X")
        return "won" if parts[1] == res else "lost"
    if kind == "DC" and has_ha and len(parts) > 1:
        ok = {"1X": h >= a, "12": h != a, "X2": a >= h}.get(parts[1])
        return None if ok is None else ("won" if ok else "lost")
    if kind == "WIN" and len(parts) > 1:
        if sh is not None and sa is not None and (sh or sa):
            hwin = sh > sa
        elif has_ha:
            hwin = h > a
        else:
            return None
        return "won" if ((parts[1] == "HOME") == hwin) else "lost"
    if kind == "SET" and len(parts) > 1 and sh is not None and sa is not None:
        got = (sh >= 1) if parts[1] == "HOME" else (sa >= 1)
        return "won" if got else "lost"
    # --- marchés tennis fins (depuis les jeux par set) ---
    if kind == "SETGAMES" and len(parts) >= 4:          # SETGAMES <n> OVER/UNDER <ligne>
        p = _per(_int(parts[1]))
        if not p:
            return None
        try:
            line = float(parts[3])
        except ValueError:
            return None
        total = p[0] + p[1]
        return "push" if total == line else ("won" if ((total > line) == (parts[2] == "OVER")) else "lost")
    if kind == "TOTGAMES" and len(parts) >= 3 and periods:   # TOTGAMES OVER/UNDER <ligne> (match)
        try:
            line = float(parts[2])
        except ValueError:
            return None
        total = sum(x[0] + x[1] for x in periods.values())
        return "push" if total == line else ("won" if ((total > line) == (parts[1] == "OVER")) else "lost")
    if kind == "SETWIN" and len(parts) >= 3:            # SETWIN <n> HOME/AWAY
        p = _per(_int(parts[1]))
        if not p:
            return None
        return "won" if ((parts[2] == "HOME") == (p[0] > p[1])) else "lost"
    if kind == "SETSCORE" and len(parts) >= 3 and sh is not None and sa is not None:  # score exact en sets
        tsh, tsa = _int(parts[1]), _int(parts[2])
        if tsh is None or tsa is None:
            return None
        return "won" if (sh == tsh and sa == tsa) else "lost"
    # --- handicap & total d'équipe (depuis le score final h/a : basket points, foot buts) ---
    if kind == "HCAP" and has_ha and len(parts) >= 3:   # HCAP HOME/AWAY <ligne signée>
        try:
            line = float(parts[2])
        except ValueError:
            return None
        diff = (h + line - a) if parts[1] == "HOME" else (a + line - h)
        return "push" if diff == 0 else ("won" if diff > 0 else "lost")
    if kind == "SETHCAP" and sh is not None and sa is not None and len(parts) >= 3:  # handicap en SETS
        try:
            line = float(parts[2])
        except ValueError:
            return None
        diff = (sh + line - sa) if parts[1] == "HOME" else (sa + line - sh)
        return "push" if diff == 0 else ("won" if diff > 0 else "lost")
    if kind == "TEAMTOT" and has_ha and len(parts) >= 4:   # TEAMTOT HOME/AWAY OVER/UNDER <ligne>
        try:
            line = float(parts[3])
        except ValueError:
            return None
        val = h if parts[1] == "HOME" else a
        return "push" if val == line else ("won" if ((val > line) == (parts[2] == "OVER")) else "lost")
    if kind == "SETSTOT" and sh is not None and sa is not None and len(parts) >= 3:  # total de sets
        try:
            line = float(parts[2])
        except ValueError:
            return None
        total = sh + sa
        return "push" if total == line else ("won" if ((total > line) == (parts[1] == "OVER")) else "lost")
    # --- cartons / corners : depuis les STATS du match (event/{id}/statistics), cf. _event_stats ---
    if kind in ("CARDS", "REDCARDS", "CORNERS"):
        stats = score.get("stats") or {}
        kh, ka = {"CARDS": ("cards_h", "cards_a"), "REDCARDS": ("rc_h", "rc_a"),
                  "CORNERS": ("corners_h", "corners_a")}[kind]
        hv, av = stats.get(kh), stats.get(ka)
        if hv is None or av is None:
            return None                                   # stats pas encore récupérées -> on retentera
        rest = parts[1:]
        side = rest.pop(0) if (rest and rest[0] in ("HOME", "AWAY")) else None
        if len(rest) < 2 or rest[0] not in ("OVER", "UNDER"):
            return None
        try:
            line = float(rest[1])
        except ValueError:
            return None
        val = (hv if side == "HOME" else av) if side else (hv + av)
        return "push" if val == line else ("won" if ((val > line) == (rest[0] == "OVER")) else "lost")
    return None


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- dérivation de code (texte -> code)
def code_from_pick(pick: str, sport: str, home: str, away: str) -> str:
    """Déduit un code règlable du texte du pick (analyses sans `pick_code`). '' si ambigu."""
    t = (pick or "").lower()
    if not t:
        return ""
    # Le CAMP (équipe/joueur) se lit sur la partie AVANT la parenthèse : le contexte entre parenthèses
    # cite souvent l'AUTRE camp (« Eala remporte un set (Zhang gagne le match) ») et fausserait la
    # détection. Le MARCHÉ (over/under, double chance, ligne…) se lit sur le texte ENTIER `t`.
    t_side = t.split("(")[0]
    names = lambda s: [w for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if len(w) >= 4]
    h_all, a_all = names(home), names(away)
    # Désambiguïsation : ignore les jetons COMMUNS aux deux camps (ex. « Ironi » dans
    # « Elitzur Ironi Netanya » vs « Ironi Ness Ziona », ou « Maria » dans « Maria Sakkari »
    # vs « Tatjana Maria ») — sinon le pick matche les deux et le côté reste indéterminé.
    shared = set(h_all) & set(a_all)
    h = [w for w in h_all if w not in shared] or h_all
    a = [w for w in a_all if w not in shared] or a_all

    def which():
        hin = any(w in t_side for w in h)
        ain = any(w in t_side for w in a)
        return "HOME" if (hin and not ain) else ("AWAY" if (ain and not hin) else "")

    def side(kind, yesno=""):
        s = which()
        return f"{kind} {s}{(' ' + yesno) if (s and yesno) else ''}" if s else ""

    # 1er jeu de service tenu (Oui/Non)
    if "jeu de service" in t or ("1er jeu" in t and "service" in t):
        yn = "NO" if (" non" in t or "perd" in t) else "YES"
        return side("HOLD1", yn)
    # total jeux d'un set (gère les deux ordres, y compris « Set 1 — Plus de 8.5 jeux »)
    m = re.search(r"jeux?\s+(?:du\s+)?set\s*(\d)", t) or re.search(r"set\s*(\d).*?jeux", t)
    if m and ("plus" in t or "moins" in t):
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            return f"SETGAMES {m.group(1)} {'OVER' if ln.group(1)=='plus' else 'UNDER'} {ln.group(2).replace(',', '.')}"
    # vainqueur d'un set précis : « remporte/gagne le set N » OU « Set N : Nom » / « Set N - Nom »
    m = re.search(r"(?:remporte|gagne)\s+le\s+set\s*(\d)", t) or re.search(r"\bset\s*(\d)\s*[:\-–]", t)
    if m:
        s = which()
        return f"SETWIN {m.group(1)} {s}" if s else ""
    # au moins un set (« un » ou « 1 »)
    if re.search(r"au moins (?:un|1) set", t):
        return side("SET")
    # score exact en sets (tennis), ex. « pari de set 2-0 Kasatkina » -> sets du vainqueur nommé
    m = re.search(r"set\s*(\d)\s*[-–]\s*(\d)", t)
    if m and which():
        big, small = m.group(1), m.group(2)
        return f"SETSCORE {big} {small}" if which() == "HOME" else f"SETSCORE {small} {big}"
    # total jeux du match
    if "jeux" in t and ("plus" in t or "moins" in t) and "set" not in t:
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            return f"TOTGAMES {'OVER' if ln.group(1)=='plus' else 'UNDER'} {ln.group(2).replace(',', '.')}"
    # total de SETS du match (tennis) : « plus/moins de N sets » OU « (nombre) total de sets : moins de N »
    m = re.search(r"(plus|moins) de (\d+[.,]?\d*)\s*sets?\b", t)
    if not m and re.search(r"(?:total|nombre)[^.]{0,14}sets?", t):
        m = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
    if m:
        return f"SETSTOT {'OVER' if m.group(1)=='plus' else 'UNDER'} {m.group(2).replace(',', '.')}"
    # marché mi-temps -> non géré (segment court)
    if "mi-temps" in t:
        return ""
    # PREMIER À X POINTS (course au score) : 1re équipe à atteindre X points — réglé via les incidents
    # (event/{id}/incidents donne le score cumulé à chaque panier). Équipe nommée obligatoire.
    m = re.search(r"premi\w*\s+à\s+(\d+)\s*point", t) or re.search(r"first\s+to\s+(\d+)", t)
    if m and which():
        return f"FIRSTTO {which()} {m.group(1)}"
    # CARTONS / CORNERS : réglés depuis les STATS réelles du match (cf. _event_stats). Équipe nommée
    # -> total d'équipe ; sinon total du match. Carton ROUGE = marché oui/non (seuil 0.5).
    fam = "CARDS" if ("carton" in t or "card" in t) else ("CORNERS" if "corner" in t else "")
    if fam:
        sd = which()
        red = fam == "CARDS" and "rouge" in t
        base = (f"REDCARDS {sd}" if red else f"{fam} {sd}").strip()
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            return f"{base} {'OVER' if ln.group(1) == 'plus' else 'UNDER'} {ln.group(2).replace(',', '.')}"
        if red:   # marché binaire oui/non sans ligne -> seuil 0.5
            neg = any(w in t for w in ("aucun", "sans", " non", "pas de"))
            return f"{base} {'UNDER' if neg else 'OVER'} 0.5"
        return ""    # carton/corner sans ligne exploitable -> on s'abstient
    team = which()
    # total d'une ÉQUIPE (le score par équipe est connu) : « X marque +1.5 », « X +/- de N buts/pts »
    if team:
        mt = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
        mm = re.search(r"marque\w*\s*\+?\s*(\d+[.,]?\d*)", t)
        if mt:
            return f"TEAMTOT {team} {'OVER' if mt.group(1)=='plus' else 'UNDER'} {mt.group(2).replace(',', '.')}"
        if mm:
            return f"TEAMTOT {team} OVER {mm.group(1).replace(',', '.')}"
    # total du MATCH (sans équipe nommée) — accepte buts/points + abréviations « pt / pts »
    m = re.search(r"(plus|moins) de (\d+[.,]?\d*)\s*(?:buts?|points?|pts?)", t)
    if m and not team:
        return f"{'OVER' if m.group(1)=='plus' else 'UNDER'} {m.group(2).replace(',', '.')}"
    # handicap depuis le score final : « Équipe +X.X » / « Équipe -X.X » / « handicap Équipe +X.X »
    mh = re.search(r"([+\-−–]\s?\d+(?:[.,]\d+)?)", t)   # accepte le moins ASCII, Unicode (−) et tiret (–)
    if mh and team:
        # handicap en SETS (tennis, « X -1.5 set ») -> réglé sur les sets ; sinon points/buts.
        kind_h = "SETHCAP" if re.search(r"\bsets?\b", t) else "HCAP"
        val = (mh.group(1).replace(" ", "").replace(",", ".").replace("−", "-").replace("–", "-"))
        return f"{kind_h} {team} {val}"
    if "deux équipes marquent" in t or "btts" in t:
        return "BTTS NO" if "non" in t else "BTTS YES"
    if "double chance" in t:
        for k in ("1x", "12", "x2"):
            if k in t:
                return f"DC {k.upper()}"
    if any(x in t for x in ("vainqueur", "gagne", "victoire")):
        if sport == "foot":
            s = side("X")
            return "1X2 1" if s.endswith("HOME") else ("1X2 2" if s.endswith("AWAY") else "")
        return side("WIN")
    return ""


# --------------------------------------------------------------- récupération des données SofaScore
def _score_from_event(sport: str, ev: dict) -> dict | None:
    st = ((ev.get("status") or {}).get("type") or "").lower()
    if st not in ("finished", "afterextra", "penalties"):
        return None
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    hc, ac = hs.get("current"), as_.get("current")
    if hc is None or ac is None:
        return None
    periods = {}
    for i in range(1, 6):
        ph, pa = hs.get(f"period{i}"), as_.get(f"period{i}")
        if ph is not None and pa is not None:
            periods[i] = (ph, pa)
    base = {"periods": periods, "first_serve": ev.get("firstToServe")}
    if sport == "tennis":
        base.update({"home": None, "away": None, "sets_home": hc, "sets_away": ac,
                     "label": f"{hc}-{ac} (sets)"})
    else:
        base.update({"home": hc, "away": ac, "sets_home": None, "sets_away": None,
                     "label": f"{hc}-{ac}"})
    return base


async def _event_votes(sofa: str) -> tuple | None:
    """Votes communauté (%home, %away, %draw) via event/{id}/votes. None si indispo. Sert à FIGER le
    sentiment public dans le sidecar (pub_*) quand le scan n'a pas pu les capturer (SofaScore bloqué)."""
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/votes")
        if r.status_code != 200:
            return None
        vote = (r.json() or {}).get("vote") or {}
        v1, vx, v2 = vote.get("vote1"), vote.get("voteX"), vote.get("vote2")
        total = (v1 or 0) + (vx or 0) + (v2 or 0)
        if not total:
            return None
        pct = lambda v: round(100 * v / total, 1) if v is not None else None
        return (pct(v1), pct(v2), pct(vx))
    except Exception:
        return None


async def _event_data(sport: str, sofa: str) -> dict | None:
    """Score complet (final + jeux par set + firstToServe) via event/{id}. None si pas fini."""
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}")
        if r.status_code != 200:
            return None
        return _score_from_event(sport, (r.json() or {}).get("event") or {})
    except Exception:
        return None


def _statnum(v):
    """Extrait l'entier en tête d'une valeur de stat SofaScore (ex. '5', '5 (60%)'). None sinon."""
    m = re.search(r"-?\d+", str(v if v is not None else ""))
    return int(m.group()) if m else None


async def _event_stats(sofa: str) -> dict | None:
    """Corners & cartons (jaunes/rouges) RÉELS d'un match terminé via event/{id}/statistics — dispo
    même après le match. Renvoie {corners_h/a, yc_h/a, rc_h/a, cards_h/a} ou None si indisponible."""
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/statistics")
        if r.status_code != 200:
            return None
        st = r.json() or {}
    except Exception:
        return None
    out: dict = {}
    for grp in st.get("statistics", []) or []:
        if grp.get("period") != "ALL":
            continue
        for sub in grp.get("groups", []) or []:
            for it in sub.get("statisticsItems", []) or []:
                nm = (it.get("name") or "").lower()
                if "corner" in nm:
                    out["corners_h"], out["corners_a"] = _statnum(it.get("home")), _statnum(it.get("away"))
                elif nm in ("yellow cards", "cartons jaunes"):
                    out["yc_h"], out["yc_a"] = _statnum(it.get("home")), _statnum(it.get("away"))
                elif nm in ("red cards", "cartons rouges"):
                    out["rc_h"], out["rc_a"] = _statnum(it.get("home")), _statnum(it.get("away"))
    if not out:
        return None
    # Stats présentes (match fini) mais pas de ligne « rouges » -> 0 carton rouge (et non « inconnu »).
    out.setdefault("rc_h", 0)
    out.setdefault("rc_a", 0)
    out["cards_h"] = (out.get("yc_h") or 0) + out["rc_h"]
    out["cards_a"] = (out.get("yc_a") or 0) + out["rc_a"]
    return out


async def _settle_hold1(sofa: str, code: str, score: dict) -> str | None:
    """Règle « X tient son 1er jeu de service (Oui/Non) » via le point-by-point. NB : `firstToServe`
    est peu fiable -> on cherche directement le PREMIER jeu du set 1 où CE joueur sert."""
    parts = code.upper().split()
    if len(parts) < 3:
        return None
    side = 1 if parts[1] == "HOME" else 2
    want_yes = parts[2] != "NO"
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/point-by-point")
        pbp = (r.json() or {}).get("pointByPoint") or [] if r.status_code == 200 else []
    except Exception:
        return None
    set1 = next((s for s in pbp if s.get("set") == 1), None)
    if not set1:
        return None
    games = sorted(set1.get("games") or [], key=lambda g: g.get("game") or 0)
    mine = next((g for g in games if (g.get("score") or {}).get("serving") == side), None)
    sc = (mine or {}).get("score") or {}
    if sc.get("scoring") not in (1, 2):
        return None
    held = sc.get("scoring") == side                # le serveur gagne son jeu = il le tient
    return "won" if (held == want_yes) else "lost"


async def _settle_firstto(sofa: str, code: str) -> str | None:
    """Règle « premier à X points » via event/{id}/incidents (score CUMULÉ à chaque panier). code =
    'FIRSTTO HOME 10' / 'FIRSTTO AWAY 10'. won si le camp nommé atteint X le PREMIER. None si indispo."""
    parts = code.split()
    if len(parts) < 3:
        return None
    want, n = parts[1], int(parts[2])
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/incidents")
        inc = (r.json() or {}).get("incidents") or [] if r.status_code == 200 else []
    except Exception:
        return None
    seq = [i for i in inc if i.get("homeScore") is not None and i.get("awayScore") is not None]
    if not seq:
        return None
    seq.sort(key=lambda i: i.get("id") or 0)            # ordre chronologique (l'API renvoie décroissant)
    for i in seq:
        h, a = i.get("homeScore"), i.get("awayScore")
        if h >= n or a >= n:                            # 1 panier/équipe -> 1 seul camp franchit X ici
            winner = "HOME" if (h >= n and h >= a) else "AWAY"
            return "won" if winner == want else "lost"
    return None


# --------------------------------------------------------------- repli scheduled-events (foot non résolu)
def _toks(s: str) -> set:
    return {w for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if len(w) >= 3}


async def _schedule_scores(sport: str, day: str):
    path = _SPORT_PATH.get(sport)
    by_id, by_name = {}, []
    if not path or not day:
        return by_id, by_name
    try:
        r = await sofa_http.get(f"{_SOFA}/sport/{path}/scheduled-events/{day}")
        evs = (r.json() or {}).get("events") or [] if r.status_code == 200 else []
    except Exception:
        evs = []
    for ev in evs:
        sc = _score_from_event(sport, ev)
        if not sc:
            continue
        eid = str(ev.get("id"))
        by_id[eid] = sc
        by_name.append((_toks((ev.get("homeTeam") or {}).get("name")),
                        _toks((ev.get("awayTeam") or {}).get("name")), sc, eid))
    return by_id, by_name


def _find_score(sched, d: dict) -> tuple:
    """(score, sofa_id) du match dans scheduled-events : par id, sinon par NOMS (retourne aussi
    l'id SofaScore résolu -> permet de FIGER le vrai sofa_id quand le scan ne l'avait pas)."""
    by_id, by_name = sched
    sofa = str(d.get("sofa_id") or "")
    if sofa in by_id:
        return by_id[sofa], sofa
    mh, ma = _toks(d.get("home", "")), _toks(d.get("away", ""))
    if not mh or not ma:
        return None, None
    for h, a, sc, eid in by_name:
        if (h & mh and a & ma) or (h & ma and a & mh):
            return sc, eid
    return None, None


# --------------------------------------------------------------- passe de règlement
async def settle_analyses() -> int:
    """Règle TOUS les matchs analysés terminés. Code = `pick_code` sinon dérivé. Score via
    event/{id} (id Sofa valide : donne aussi jeux par set + 1er service) ; repli scheduled-events
    par noms (foot non résolu). HOLD1 -> point-by-point. Renvoie le nombre de sidecars écrits."""
    pending = []
    for side in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(side, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        res = d.get("result")
        # On re-règle aussi : (a) les matchs d'une VERSION de logique antérieure (nouveaux marchés :
        # handicap, total d'équipe, score exact…) ; (b) ceux dont le sentiment public (pub_*) manque
        # ET qui ont un VRAI sofa_id (≤ 8 chiffres) -> on retente le backfill des votes jusqu'à
        # capture. Le règlement réutilise `result.raw` caché (0 réseau) ; seul le backfill appelle le
        # réseau. (Les matchs sans sofa_id exploitable ne sont PAS retentés en boucle.)
        # On retente le backfill des votes tant que le public manque (borné à 3 essais : la résolution
        # de l'id SofaScore par noms peut échouer si SofaScore est temporairement bloqué).
        votes_pending = (d.get("pub_home") is None and (d.get("votes_tries") or 0) < 3)
        if (res and res.get("pick_result") is not None
                and d.get("settle_v") == _SETTLE_VERSION
                and not votes_pending):
            continue
        # On tente le règlement dès que le match est PROBABLEMENT fini (`likely_finished`), pas seulement
        # à la fin de la fenêtre `status_of` (souvent trop longue, ex. tennis 210 min) : SofaScore
        # `event/{id}` GÈRE le filtre (score lu UNIQUEMENT si l'event est réellement terminé) -> un match
        # pas encore fini est simplement re-tenté à la boucle suivante. Évite le « terminé » sans score.
        if analyses.status_of(d) == "finished" or analyses.likely_finished(d):
            pending.append((side, d))
    if not pending:
        return 0
    n = 0
    sched_cache: dict = {}
    prev_bulk = sofa_http.allow_bulk_proxy
    sofa_http.allow_bulk_proxy = True   # autorise scheduled-events (repli) pendant le règlement
    try:
        for side, d in pending:
            sport = d.get("sport")
            code = (d.get("pick_code")
                    or code_from_pick(d.get("pick", ""), sport, d.get("home", ""), d.get("away", "")))
            sofa = str(d.get("sofa_id") or "")
            score = (d.get("result") or {}).get("raw")
            if not score:
                if sofa and len(sofa) <= 8:
                    score = await _event_data(sport, sofa)
                if not score:                       # repli : scheduled-events du jour, par noms
                    day = (d.get("start") or "")[:10]
                    if day:
                        if (sport, day) not in sched_cache:
                            sched_cache[(sport, day)] = await _schedule_scores(sport, day)
                        score, _ = _find_score(sched_cache[(sport, day)], d)
                if not score:
                    continue
            # Pré-calcule les codes de TOUS les paris affichés -> on sait si on a besoin des STATS du
            # match (cartons/corners). SofaScore les expose même APRÈS le match (event/{id}/statistics).
            mid = os.path.basename(side)[len(sport) + 1:-5]    # {sport}_{id}.json -> id
            bet_list = analyses.bets_of(sport, mid)
            bet_codes = [code_from_pick(b["sel"], sport, d.get("home", ""), d.get("away", ""))
                         for b in bet_list]
            if (any(c.startswith(("CARDS", "REDCARDS", "CORNERS")) for c in [code, *bet_codes])
                    and not score.get("stats") and sofa and len(sofa) <= 8):
                st = await _event_stats(sofa)
                if st:
                    score["stats"] = st

            async def _settle_one(c):
                if not c:
                    return None
                if c.startswith("HOLD1") and sofa and len(sofa) <= 8:
                    return await _settle_hold1(sofa, c, score)
                if c.startswith("FIRSTTO") and sofa and len(sofa) <= 8:
                    return await _settle_firstto(sofa, c)   # premier à X points -> incidents SofaScore
                return settle_pick(c, score)

            pr = await _settle_one(code)
            d["result"] = {"score": score.get("label"), "pick_result": pr, "raw": score}
            # Règle CHAQUE pari affiché séparément (stats par pari 1/2/3, cadres verts/rouges).
            bets_out = []
            for b, bc in zip(bet_list, bet_codes):
                if not bc:   # pari d'un match FINI qu'on ne sait pas régler -> à corriger (pas silencieux)
                    log.warning("règlement impossible (code vide) : %s_%s · %r", sport, mid, b.get("sel"))
                br = await _settle_one(bc)
                bets_out.append({"sel": b["sel"], "odds": b["cote"], "code": bc, "result": br,
                                 "prob": b.get("prob")})   # confiance annoncée -> page calibration
            d["bets"] = bets_out
            d["settle_v"] = _SETTLE_VERSION
            # Backfill du sentiment public (barre « Public ») si le scan ne l'a pas capturé (SofaScore
            # bloqué au scan) -> FIGÉ une fois dans le sidecar, ne bouge plus ensuite. Si le sofa_id
            # n'est pas exploitable (résolution échouée au scan), on le RÉSOUT par noms via
            # scheduled-events (et on le fige) pour pouvoir lire les votes.
            if d.get("pub_home") is None:
                vsofa = sofa if (sofa.isdigit() and len(sofa) <= 8) else None
                if not vsofa:
                    day = (d.get("start") or "")[:10]
                    if day:
                        if (sport, day) not in sched_cache:
                            sched_cache[(sport, day)] = await _schedule_scores(sport, day)
                        _, rid = _find_score(sched_cache[(sport, day)], d)
                        if rid and rid.isdigit() and len(rid) <= 8:
                            vsofa = rid
                            d["sofa_id"] = rid          # on fige l'id SofaScore résolu
                if vsofa:
                    v = await _event_votes(vsofa)
                    if v and v[0] is not None:
                        d["pub_home"], d["pub_away"] = v[0] / 100, v[1] / 100
                        if len(v) > 2 and v[2] is not None:
                            d["pub_draw"] = v[2] / 100
                d["votes_tries"] = (d.get("votes_tries") or 0) + 1
            try:
                json.dump(d, open(side, "w", encoding="utf-8"), ensure_ascii=False)
                n += 1
            except OSError:
                pass
    finally:
        sofa_http.allow_bulk_proxy = prev_bulk
    return n
