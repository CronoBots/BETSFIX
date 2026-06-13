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

_index_cache: dict[int, list] = {}  # offset de jour (0=aujourd'hui, -1=hier…) -> [(matchId, home, away)]
_games_cache: dict[str, list] = {}  # matchId -> games


def _get(url: str, headers: dict | None = None, timeout: float = 12.0) -> str | None:
    try:
        req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return None


def _match_index(offset: int = 0) -> list:
    """[(matchId, home, away)] des matchs tennis d'un JOUR (offset : 0=aujourd'hui, -1=hier…).
    Flashscore mobile accepte `?d=<offset>`. 1 appel mis en cache par offset."""
    if offset in _index_cache:
        return _index_cache[offset]
    suffix = f"?d={offset}" if offset else ""
    html = _get("https://www.flashscore.mobi/tennis/" + suffix)
    out = []
    if html:
        # « …</span>Nom A (Pays) - Nom B (Pays) <a href="/match/{id}/[?d=…]"… » (noms AVANT le lien ;
        # sur les pages datées le lien porte un suffixe « ?d=-5 » -> rendu optionnel)
        for home_raw, away_raw, mid in re.findall(
                r'>([^<>]+?)\s+-\s+([^<>]+?)\s*<a href="/match/([A-Za-z0-9]{8})/(?:\?[^"]*)?"', html):
            home = re.sub(r"\s*\([A-Za-z]{2,3}\)\s*$", "", home_raw).strip()
            away = re.sub(r"\s*\([A-Za-z]{2,3}\)\s*$", "", away_raw).strip()
            if home and away:
                out.append((mid, home, away))
    _index_cache[offset] = out
    return out


def _day_offsets(start_iso: str | None) -> list:
    """Offsets de jour à interroger pour un match : le jour du coup d'envoi ±1 (fuseau FS),
    sinon aujourd'hui + hier. Bornés à [-10, 1] (archive récente)."""
    dt = _start_dt(start_iso) if start_iso else None
    if dt is None:
        return [0, -1]
    base = (dt.date() - datetime.now(timezone.utc).date()).days
    return [o for o in (base, base - 1, base + 1) if -10 <= o <= 1]


def _find_match_id(home: str, away: str, start_iso: str | None = None) -> str | None:
    """matchId Flashscore du match (correspondance de NOMS, robuste aux abréviations « Mannarino A. »),
    cherché dans l'index du JOUR du match (±1)."""
    th, ta = _tok(home), _tok(away)
    for off in _day_offsets(start_iso):
        idx = _match_index(off)
        for mid, h, a in idx:
            if _teams_match(home, away, h, a):
                return mid
        for mid, h, a in idx:          # repli : un nom de famille commun de chaque côté
            fh, fa = _tok(h), _tok(a)
            if (th & fh and ta & fa) or (th & fa and ta & fh):
                return mid
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


def matches(offset: int = 0) -> list:
    """Matchs tennis d'un jour (offset : 0=aujourd'hui, -1=hier…) : [{id, home, away}]."""
    return [{"id": m, "home": h, "away": a} for m, h, a in _match_index(offset)]


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


def find_id(home: str, away: str, start_iso: str | None = None) -> str | None:
    """Expose la résolution du matchId Flashscore par noms (+ jour) — pour l'API."""
    return _find_match_id(home, away, start_iso)


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
