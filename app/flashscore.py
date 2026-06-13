"""Règlement des marchés JEU PAR JEU (1er jeu de service tenu, etc.) via le feed gratuit Flashscore.

SofaScore (point-by-point) est bloqué par Cloudflare et le repli ESPN/FotMob ne donne que le score
final. Flashscore expose, lui, un feed public `df_mh` (point-par-point) avec, pour CHAQUE jeu : le
SERVEUR (`HG` : 1=domicile, 2=extérieur) et la séquence des points (`HL`) -> on en déduit le
vainqueur du jeu. Permet de régler « X remporte son 1er jeu de service » sans SofaScore, et
RÉTROACTIVEMENT (après le match), contrairement à une capture live.

Best-effort strict : timeout court, toute panne -> None (le règlement re-tente plus tard / via Sofa).
Aucune clé : le `x-fsign` est une constante publique (config mobile Flashscore) ; re-extractible si
un 401 survient un jour.
"""

from __future__ import annotations

import re
import urllib.request
from datetime import datetime, timezone

from app.sources import _start_dt, _teams_match, _tok   # réutilise le matching de noms robuste

_UA = {"User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")}
_FSIGN = "SW9D1eZo"                 # signature publique du feed (re-extractible si 401)
_PROJECT = "302"                    # id projet Flashscore tennis (stable)
_SEP_REC = "¬"                 # ¬  séparateur d'enregistrements
_SEP_FLD = "÷"                 # ÷  séparateur code/valeur
_POINT = {"0": 0, "15": 1, "30": 2, "40": 3, "a": 4, "ad": 4}

_SPORT_ID = {"football": 1, "foot": 1, "tennis": 2, "basket": 3, "basketball": 3}

_index_cache: dict[tuple, list] = {}  # (sport, offset_jour) -> [{id, home, away, home_score, away_score, league, start_ts}]
_games_cache: dict[str, list] = {}    # matchId -> games


def _get(url: str, headers: dict | None = None, timeout: float = 12.0) -> str | None:
    try:
        req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return None


def _clean_name(s: str) -> str:
    """Retire le suffixe pays « (Ger) » des noms du feed."""
    return re.sub(r"\s*\([A-Za-z]{2,3}\)\s*$", "", s or "").strip()


def _match_index(sport: str = "tennis", offset: int = 0) -> list:
    """Matchs d'un SPORT et d'un JOUR (offset : 0=aujourd'hui, -1=hier…) via le feed `f_{sport}_{jour}`.
    -> [{id, home, away, home_score, away_score, league, start_ts}]. 1 appel caché par (sport, offset)."""
    sid = _SPORT_ID.get(sport, 2)
    key = (sid, offset)
    if key in _index_cache:
        return _index_cache[key]
    feed = _feed_raw(f"f_{sid}_{offset}_3_en_1")
    out, league = [], None
    if feed:
        for blk in feed.split("~"):
            f = dict(re.findall(r"([A-Z]{2,3})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", blk))
            if "ZA" in f:                       # en-tête de compétition
                league = f["ZA"]
            mid = f.get("AA")
            if mid and f.get("AE") and f.get("AF"):
                try:
                    ts = int(f["AD"]) if f.get("AD") else None
                except ValueError:
                    ts = None
                out.append({"id": mid, "home": _clean_name(f["AE"]), "away": _clean_name(f["AF"]),
                            "home_score": f.get("AG") or None, "away_score": f.get("AH") or None,
                            "league": league, "start_ts": ts})
    _index_cache[key] = out
    return out


def _feed_raw(name: str) -> str | None:
    """Récupère un feed Flashscore générique par NOM complet (ex. f_1_0_3_en_1). None si KO."""
    return _get(f"https://global.flashscore.ninja/{_PROJECT}/x/feed/{name}",
                headers={"x-fsign": _FSIGN, "Referer": "https://www.flashscore.com/"})


def _day_offsets(start_iso: str | None) -> list:
    """Offsets de jour à interroger pour un match : le jour du coup d'envoi ±1 (fuseau FS),
    sinon aujourd'hui + hier. Bornés à [-10, 1] (archive récente)."""
    dt = _start_dt(start_iso) if start_iso else None
    if dt is None:
        return [0, -1]
    base = (dt.date() - datetime.now(timezone.utc).date()).days
    return [o for o in (base, base - 1, base + 1) if -10 <= o <= 1]


def _find_match_id(home: str, away: str, start_iso: str | None = None, sport: str = "tennis") -> str | None:
    """matchId Flashscore (correspondance de NOMS, robuste aux abréviations « Mannarino A. »), cherché
    dans l'index du SPORT au JOUR du match (±1)."""
    th, ta = _tok(home), _tok(away)
    for off in _day_offsets(start_iso):
        idx = _match_index(sport, off)
        for m in idx:
            if _teams_match(home, away, m["home"], m["away"]):
                return m["id"]
        for m in idx:                  # repli : un nom de famille commun de chaque côté
            fh, fa = _tok(m["home"]), _tok(m["away"])
            if (th & fh and ta & fa) or (th & fa and ta & fh):
                return m["id"]
    return None


def _game_winner(hl: str) -> str | None:
    """Vainqueur d'un jeu depuis la séquence de points HL ('0:15, 15:15, 40:40, A:40') : 'home'/'away'."""
    pts = [p.strip() for p in (hl or "").split(",") if p.strip()]
    if not pts:
        return None
    last = re.sub(r"\|[^|]*\|", "", pts[-1]).strip()      # retire les marqueurs |B1| (balle de break)
    m = re.match(r"([0-9aA]+)\s*:\s*([0-9aA]+)", last)
    if not m:
        return None
    hv, av = _POINT.get(m.group(1).lower()), _POINT.get(m.group(2).lower())
    if hv is None or av is None or hv == av:
        return None
    return "home" if hv > av else "away"


def _parse_games(feed: str) -> list:
    """Décode le feed `df_mh` -> [{'server':'home'/'away', 'winner':'home'/'away'}] ORDONNÉS. Un bloc
    par jeu (« ~HC÷… ») : HG=serveur (1/2), HK=vainqueur (1/2). Le 1er bloc (HC=0) EST le 1er jeu."""
    games: list = []
    for block in (feed or "").split("~HC" + _SEP_FLD)[1:]:
        flds = dict(re.findall(r"([A-Z]{2})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", block))
        hg, hk = flds.get("HG"), flds.get("HK")
        if hg not in ("1", "2"):
            continue
        w = ("home" if hk == "1" else "away" if hk == "2"   # HK = vainqueur (fiable)
             else _game_winner(flds.get("HL")))             # repli : lecture des points
        if w:
            games.append({"server": "home" if hg == "1" else "away", "winner": w})
    return games


def _games(match_id: str) -> list:
    """Jeux d'un match (cachés) — récupère le feed `df_mh` puis `_parse_games`. (Pas de découpage par
    set : HOLD1 ne porte que sur le 1er jeu de service.)"""
    if match_id in _games_cache:
        return _games_cache[match_id]
    feed = _get(f"https://global.flashscore.ninja/{_PROJECT}/x/feed/df_mh_1_{match_id}",
                headers={"x-fsign": _FSIGN, "Referer": "https://www.flashscore.com/"})
    games = _parse_games(feed) if feed else []
    _games_cache[match_id] = games
    return games


def _feed(code: str, match_id: str) -> str | None:
    """Récupère un feed Flashscore brut (`df_mh`/`df_su`/`df_st`/`df_hh`) pour un match. None si KO."""
    return _get(f"https://global.flashscore.ninja/{_PROJECT}/x/feed/{code}_1_{match_id}",
                headers={"x-fsign": _FSIGN, "Referer": "https://www.flashscore.com/"})


def matches(sport: str = "tennis", offset: int = 0) -> list:
    """Matchs d'un sport (football/tennis/basket) et d'un jour (offset : 0=aujourd'hui, -1=hier…)."""
    return _match_index(sport, offset)


def incidents(match_id: str) -> dict | None:
    """Déroulé d'un match FOOTBALL (depuis `df_in`) : buts, cartons, remplacements… en enregistrements
    décodés (codes Flashscore -> valeurs). None si indisponible (sport sans incidents)."""
    feed = _feed("df_in", match_id)
    if not feed or len(feed) < 30:
        return None
    rows = [{"code": c, "value": v}
            for c, v in re.findall(r"([A-Z]{2,3})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", feed)]
    return {"records": rows} if rows else None


def points(match_id: str) -> list:
    """Déroulé JEU PAR JEU d'un match : [{server, winner}] (du 1er au dernier jeu)."""
    return _games(match_id)


def score(match_id: str) -> dict | None:
    """Score d'un match (depuis `df_su`) : {sets:[{home,away,tiebreak}], home_sets, away_sets,
    duration, winner}. None si indisponible."""
    feed = _feed("df_su", match_id)
    if not feed:
        return None
    recs = re.findall(r"([A-Z]{2,3})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", feed)
    first = {}
    for code, val in recs:
        first.setdefault(code, val)
    bvals = [first[c] for c in sorted(first) if re.fullmatch(r"B[A-Z]", c)]   # BA,BB,BC,BD… jeux/set
    dvals = {c: first[c] for c in first if re.fullmatch(r"D[A-Z]", c)}        # tie-breaks
    dletters = sorted(dvals)
    sets, hs, as_ = [], 0, 0
    for i in range(0, len(bvals) - 1, 2):
        try:
            h, a = int(bvals[i]), int(bvals[i + 1])
        except ValueError:
            continue
        tb = None
        di = i  # DA aligné sur BA, DB sur BB…
        if di < len(dletters) and (di + 1) < len(dletters):
            try:
                tb = [int(dvals[dletters[di]]), int(dvals[dletters[di + 1]])]
            except ValueError:
                tb = None
        sets.append({"home": h, "away": a, "tiebreak": tb})
        hs += h > a
        as_ += a > h
    return {"sets": sets, "home_sets": hs, "away_sets": as_,
            "duration": first.get("RB"), "winner": ("home" if hs > as_ else "away" if as_ > hs else None)}


def periods(match_id: str) -> dict | None:
    """Score par PÉRIODE d'un match foot (depuis `df_su`) : {periods:[{name,home,away}], home, away}.
    Format Flashscore foot : `AC÷1st Half IG÷1 IH÷0`. None si indisponible."""
    feed = _feed("df_su", match_id)
    if not feed:
        return None
    out = []
    # Une période = un BLOC « ~ » regroupant les enregistrements ¬ AC (nom) / IG (dom) / IH (ext).
    for blk in feed.split("~"):
        f = dict(re.findall(r"([A-Z]{2,3})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", blk))
        if f.get("AC") and ("IG" in f or "IH" in f):
            out.append({"name": f["AC"].strip(), "home": (f.get("IG") or "").strip(),
                        "away": (f.get("IH") or "").strip()})
    if not out:
        return None

    def _sum(key):
        tot = 0
        for p in out:
            try:
                tot += int(p[key])
            except (ValueError, TypeError):
                pass
        return tot

    # Score final = SOMME des mi-temps (les valeurs IG/IH sont par période, pas cumulées).
    return {"periods": out, "home": _sum("home"), "away": _sum("away")}


def statistics(match_id: str) -> dict | None:
    """Statistiques d'un match (depuis `df_st`), groupées par SECTION (Match / Set 1 / Set 2…) :
    {sections:[{name, categories:[{name, items:[{name,home,away}]}]}]}. Aces, doubles fautes,
    % 1er service, balles de break, winners… None si indisponible."""
    feed = _feed("df_st", match_id)
    if not feed:
        return None
    sections, sec, cat = [], None, None
    for rec in feed.split(_SEP_REC):
        code, _, val = rec.partition(_SEP_FLD)
        code = code.lstrip("~ ")
        if code == "SE":                       # section (Match / Set 1 / Set 2…)
            sec = {"name": val, "categories": []}
            sections.append(sec)
            cat = None
        elif code == "SF" and sec is not None:  # catégorie (Service / Return / Points / Games)
            cat = {"name": val, "items": []}
            sec["categories"].append(cat)
        elif code == "SG" and cat is not None:  # nom de la stat -> SH (home), SI (away)
            cat["items"].append({"name": val, "home": None, "away": None})
        elif code == "SH" and cat and cat["items"]:
            cat["items"][-1]["home"] = val
        elif code == "SI" and cat and cat["items"]:
            cat["items"][-1]["away"] = val
    return {"sections": sections} if sections else None


def prematch(match_id: str) -> dict | None:
    """Faits PRÉ-MATCH depuis le feed `df_hh` : forme récente de chaque camp + face-à-face direct.
    -> {home_form:[{res,score}], away_form:[…], h2h:[{score, winner_name, a, b}]} (plus récent d'abord).
    `res` = w/l/d (perspective du camp). En H2H le vainqueur est marqué par « * » (pas de WIS).
    None si indisponible. Sections « filtrées par lieu » (répétées après le 1er H2H) ignorées."""
    feed = _feed("df_hh", match_id)
    if not feed:
        return None
    return _parse_prematch(feed)


def _goals_for_against(kl: str | None, ks: str | None) -> tuple:
    """(buts marqués, buts encaissés) par le SUJET dans un match, depuis KL « h:a » + KS (home/away)."""
    m = re.match(r"\s*(\d+)\s*:\s*(\d+)", kl or "")
    if not m:
        return (None, None)
    h, a = int(m.group(1)), int(m.group(2))
    return (h, a) if ks == "home" else (a, h)


def _parse_prematch(feed: str) -> dict | None:
    """Parse PUR (sans réseau) du feed `df_hh` -> {home_form, away_form, h2h}. Voir prematch()."""
    home_f, away_f, h2h = [], [], []
    phase, labels, got_h2h = None, [], False
    for blk in feed.split("~"):
        f = dict(re.findall(r"([A-Z]{2,4})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", blk))
        kb = f.get("KB")
        if kb is not None:                          # changement de section
            if kb.startswith("Head-to-head"):
                phase = "done" if got_h2h else "h2h"
                got_h2h = True
            elif kb.startswith("Last matches"):
                if kb not in labels:
                    labels.append(kb)
                phase = "done" if got_h2h else {1: "home", 2: "away"}.get(len(labels), "done")
            else:
                phase = "done"
            continue
        if phase == "home" and "WIS" in f and len(home_f) < 10:
            gf, ga = _goals_for_against(f.get("KL"), f.get("KS"))
            home_f.append({"res": f["WIS"], "score": f.get("KL"), "gf": gf, "ga": ga})
        elif phase == "away" and "WIS" in f and len(away_f) < 10:
            gf, ga = _goals_for_against(f.get("KL"), f.get("KS"))
            away_f.append({"res": f["WIS"], "score": f.get("KL"), "gf": gf, "ga": ga})
        elif phase == "h2h" and "KL" in f and len(h2h) < 8:
            kj, kk = f.get("KJ", ""), f.get("KK", "")
            winner = kj[1:] if kj.startswith("*") else kk[1:] if kk.startswith("*") else None
            h2h.append({"score": f["KL"], "winner_name": winner,
                        "a": kj.lstrip("*"), "b": kk.lstrip("*")})
    if not (home_f or away_f or h2h):
        return None
    return {"home_form": home_f, "away_form": away_f, "h2h": h2h}


def _recent_match_ids(feed: str) -> tuple:
    """[(KP, side)] des matchs récents de CHAQUE joueur depuis df_hh (KP = id du match, KS = côté du
    joueur DANS ce match). -> (home_ids, away_ids). Mêmes sections que _parse_prematch."""
    home_ids, away_ids, phase, labels, got_h2h = [], [], None, [], False
    for blk in feed.split("~"):
        f = dict(re.findall(r"([A-Z]{2,4})" + _SEP_FLD + r"([^" + _SEP_REC + r"]*)", blk))
        kb = f.get("KB")
        if kb is not None:
            if kb.startswith("Head-to-head"):
                phase, got_h2h = ("done" if got_h2h else "h2h"), True
            elif kb.startswith("Last matches"):
                if kb not in labels:
                    labels.append(kb)
                phase = "done" if got_h2h else {1: "home", 2: "away"}.get(len(labels), "done")
            else:
                phase = "done"
            continue
        if f.get("KP") and f.get("KS") in ("home", "away"):
            if phase == "home" and len(home_ids) < 4:
                home_ids.append((f["KP"], f["KS"]))
            elif phase == "away" and len(away_ids) < 4:
                away_ids.append((f["KP"], f["KS"]))
    return home_ids, away_ids


def _serve_item(stats: dict, name_key: str, side: str):
    """Valeur brute d'une stat de SERVICE (par nom) pour `side` (home/away) dans un df_st décodé."""
    for sec in (stats or {}).get("sections", []):
        if (sec.get("name") or "").lower() not in ("match", "full time", ""):
            continue
        for cat in sec.get("categories", []):
            if "service" not in (cat.get("name") or "").lower():
                continue
            for it in cat.get("items", []):
                if name_key in (it.get("name") or ""):
                    return it.get(side)
    return None


def _agg_serve(ids_sides: list) -> dict | None:
    """Moyenne des stats de service (aces, doubles fautes, 1er service %) sur les matchs `ids_sides`
    = [(match_id, side)]. None si rien d'exploitable."""
    aces, dfs, first = [], [], []
    for mid, side in ids_sides:
        st = statistics(mid)
        if not st:
            continue
        a = _serve_item(st, "Aces", side)
        d = _serve_item(st, "Double Faults", side)
        fp = _serve_item(st, "1st serve percentage", side)
        if a and a.isdigit():
            aces.append(int(a))
        if d and d.isdigit():
            dfs.append(int(d))
        if fp:
            m = re.match(r"(\d+)", fp)
            if m:
                first.append(int(m.group(1)))
    out = {}
    if aces:
        out["aces"] = round(sum(aces) / len(aces), 1)
    if dfs:
        out["double_faults"] = round(sum(dfs) / len(dfs), 1)
    if first:
        out["first_serve_pct"] = round(sum(first) / len(first))
    out["matches"] = len(ids_sides)
    return out if (aces or dfs or first) else None


def foot_match_stats(match_id: str) -> dict | None:
    """Stats de match FOOT (depuis df_st) au format du règlement : {corners_h/a, yc_h/a, rc_h/a,
    cards_h/a}. Remplace SofaScore (bloqué) pour régler cartons/corners. None si indisponible."""
    st = statistics(match_id)
    if not st:
        return None

    def _num(v):
        m = re.search(r"\d+", str(v or ""))
        return int(m.group()) if m else None

    out: dict = {}
    for sec in st.get("sections", []):
        if (sec.get("name") or "").lower() not in ("match", "full time", ""):
            continue
        for cat in sec.get("categories", []):
            for it in cat.get("items", []):
                nm = (it.get("name") or "").lower()
                if "corner" in nm:
                    out["corners_h"], out["corners_a"] = _num(it.get("home")), _num(it.get("away"))
                elif "yellow card" in nm:
                    out["yc_h"], out["yc_a"] = _num(it.get("home")), _num(it.get("away"))
                elif "red card" in nm:
                    out["rc_h"], out["rc_a"] = _num(it.get("home")), _num(it.get("away"))
    if not out:
        return None
    out.setdefault("rc_h", 0)
    out.setdefault("rc_a", 0)
    out["cards_h"] = (out.get("yc_h") or 0) + out["rc_h"]
    out["cards_a"] = (out.get("yc_a") or 0) + out["rc_a"]
    return out


def foot_match_stats_by_names(home: str, away: str, start_iso: str | None = None) -> dict | None:
    """Stats cartons/corners d'un match foot retrouvé par NOMS (+ jour). None si introuvable."""
    mid = _find_match_id(home, away, start_iso, "football")
    return foot_match_stats(mid) if mid else None


def serve_stats(match_id: str) -> dict | None:
    """Stats de SERVICE moyennes (3-4 derniers matchs) des 2 joueurs d'un match tennis, via le df_hh
    (ids des matchs récents) + df_st de chacun : {home:{aces, double_faults, first_serve_pct, matches},
    away:{…}}. None si indisponible. Sert à parier les props service (aces, total jeux) + la fiche."""
    feed = _feed("df_hh", match_id)
    if not feed:
        return None
    home_ids, away_ids = _recent_match_ids(feed)
    h = _agg_serve(home_ids[:3])
    a = _agg_serve(away_ids[:3])
    if not (h or a):
        return None
    return {"home": h, "away": a}


def serve_facts(home: str, away: str, start_iso: str | None = None) -> list:
    """Puces FR « stats de service » des 2 joueurs pour le dossier tennis. [] si indisponible."""
    mid = _find_match_id(home, away, start_iso, "tennis")
    if not mid:
        return []
    s = serve_stats(mid)
    if not s:
        return []

    def _line(name, d):
        if not d:
            return None
        parts = []
        if "aces" in d:
            parts.append(f"{d['aces']} aces/match")
        if "first_serve_pct" in d:
            parts.append(f"1er service {d['first_serve_pct']}%")
        if "double_faults" in d:
            parts.append(f"{d['double_faults']} doubles fautes")
        return f"Service {name} ({d.get('matches', 0)} derniers matchs) : " + ", ".join(parts) if parts else None

    return [x for x in (_line(home, s.get("home")), _line(away, s.get("away"))) if x]


def prematch_facts(home: str, away: str, start_iso: str | None = None, sport: str = "tennis") -> list:
    """Faits pré-match prêts pour l'analyste (liste de puces FR) : forme des 5 derniers matchs de chaque
    camp + bilan du face-à-face direct. [] si match introuvable ou aucune donnée."""
    mid = _find_match_id(home, away, start_iso, sport)
    if not mid:
        return []
    data = prematch(mid)
    if not data:
        return []
    facts = []

    def _form_str(rows):                     # res = w/l/d (foot/tennis) ou wo/lo (basket) -> 1re lettre
        return " ".join((r["res"] or "?")[:1].upper() for r in rows[:5])

    if data["home_form"]:
        facts.append(f"Forme {home} (5 derniers, + récent à gauche) : {_form_str(data['home_form'])}")
    if data["away_form"]:
        facts.append(f"Forme {away} (5 derniers, + récent à gauche) : {_form_str(data['away_form'])}")
    if data["h2h"]:
        th, ta = _tok(home), _tok(away)
        wh = sum(1 for m in data["h2h"] if m["winner_name"] and _tok(m["winner_name"]) & th)
        wa = sum(1 for m in data["h2h"] if m["winner_name"] and _tok(m["winner_name"]) & ta)
        n = len(data["h2h"])
        last = data["h2h"][0]
        facts.append(f"Face-à-face direct ({n} derniers) : {home} {wh} – {wa} {away} "
                     f"(dernier : {last['a']} {last['score']} {last['b']})")
    # TENDANCES SAISON (calculées sur les ~10 derniers résultats) : buts/points marqués & encaissés
    # par match + % +2.5 buts / % BTTS (foot) — ce que SofaScore donnait pour bâtir l'analyse.
    for name, rows in ((home, data["home_form"]), (away, data["away_form"])):
        t = _tendencies(rows, sport)
        if t:
            facts.append(f"Tendances {name} {t}")
    return facts


def _tendencies(rows: list, sport: str) -> str | None:
    """Tendances chiffrées d'une équipe depuis ses derniers résultats (gf/ga) : moyennes pour/contre
    + % +2.5 buts / % BTTS (foot) ou total moyen (basket). None si <3 matchs exploitables."""
    vals = [(r["gf"], r["ga"]) for r in rows if r.get("gf") is not None and r.get("ga") is not None]
    if len(vals) < 3:
        return None
    n = len(vals)
    fr = sum(v[0] for v in vals) / n
    ag = sum(v[1] for v in vals) / n
    if sport in ("football", "foot"):
        over = round(100 * sum(1 for v in vals if v[0] + v[1] >= 3) / n)
        btts = round(100 * sum(1 for v in vals if v[0] > 0 and v[1] > 0) / n)
        return (f"({n} matchs) : {fr:.1f} buts marqués/match, {ag:.1f} encaissés, "
                f"{over}% +2.5 buts, {btts}% BTTS")
    if sport in ("basket", "basketball"):
        return f"({n} matchs) : {fr:.0f} pts marqués/match, {ag:.0f} encaissés, total moyen {fr + ag:.0f}"
    return None


def find_id(home: str, away: str, start_iso: str | None = None, sport: str = "tennis") -> str | None:
    """Expose la résolution du matchId Flashscore par noms (+ sport + jour) — pour l'API."""
    return _find_match_id(home, away, start_iso, sport)


def settle_hold1(home: str, away: str, side: str, start_iso: str | None = None) -> str | None:
    """Règle « 1er jeu de service TENU » via Flashscore. `side` = 'HOME'/'AWAY' (le joueur concerné).
    Renvoie 'won'/'lost' ou None si données indisponibles. Le 1er jeu de service d'un joueur = le
    PREMIER jeu où c'est LUI qui sert ; gagné = il l'a remporté (il a tenu son service)."""
    if not (home and away):
        return None
    mid = _find_match_id(home, away, start_iso)
    if not mid:
        return None
    want = "home" if side.upper() == "HOME" else "away"
    for g in _games(mid):
        if g["server"] == want:                # 1er jeu où le joueur sert
            return "won" if g["winner"] == want else "lost"
    return None
