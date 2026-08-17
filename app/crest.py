"""Logos de clubs (test « carte façon Bull », user 2026-08-15).

Nom d'équipe -> ID FotMob (via l'API de recherche `apigw.fotmob.com/searchapi/suggest`, CACHÉ disque),
puis URL du logo sur le CDN FotMob (`images.fotmob.com/.../teamlogo/{id}.png`, chargeable direct sans auth).
La route `/crest?name=X` redirige (302) vers le logo ; si introuvable -> 404 -> la carte retombe sur le
MONOGRAMME (repli). Best-effort STRICT : toute panne réseau -> None -> monogramme (jamais d'erreur visible).

Cache 2 niveaux : négatifs cachés aussi (on ne re-cherche pas un nom déjà tenté), sauf sur panne réseau
(là on ne cache pas -> re-tentera). `data/crest_cache.json` = { nom_normalisé: id_ou_null }.
"""
from __future__ import annotations

import json
import os
import threading
import unicodedata

import httpx

_CACHE_FILE = os.path.join("data", "crest_cache.json")
_UA = {"User-Agent": "Mozilla/5.0"}
_LOCK = threading.Lock()
_CACHE: dict | None = None
_NEG: set = set()   # échecs de résolution vus CETTE session (mémoire seule, jamais figés -> re-tentés au boot)


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.load(open(_CACHE_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            _CACHE = {}
    return _CACHE


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


import re as _re

# Suffixe de code d'état/région (« -SC », « -GO », « -PE »…) ou genre (« (F) ») en fin de nom : FotMob
# indexe l'équipe SANS -> on le retire pour la recherche/le matching (ex. « Criciúma-SC » -> « Criciúma »).
_SUFFIX = _re.compile(r"\s*[-–]\s*[A-Za-z]{2,3}\.?\s*$|\s*\((?:F|W)\)\s*$")


def _clean(name) -> str:
    return _SUFFIX.sub("", str(name or "")).strip()


# ALIAS d'ABRÉVIATIONS que FotMob ne résout PAS seul (recherche « OM » -> Omaha, « OL » -> Olympiacos…).
# Clé = nom NORMALISÉ (sans accents/espaces) tel qu'Unibet l'écrit -> nom complet cherchable sur FotMob.
# Extensible : ajouter un couple dès qu'un club à sigle apparaît sans logo.
_ALIAS = {
    "psg": "Paris Saint-Germain", "om": "Marseille", "ol": "Lyon", "asse": "Saint-Etienne",
    "losc": "Lille", "rcsa": "Strasbourg", "ogcnice": "Nice", "tfc": "Toulouse", "asm": "Monaco",
    "rcl": "Lens", "scb": "Bastia", "fcgb": "Bordeaux",
}


def _fetch(term: str) -> list:
    """Résultats FotMob (suggest) pour un libellé de recherche. Peut lever (panne réseau)."""
    r = httpx.get("https://apigw.fotmob.com/searchapi/suggest",
                  params={"term": term, "lang": "en"}, headers=_UA, timeout=8)
    return [o.get("payload", {}) for g in r.json().get("matchSuggest", []) for o in g.get("options", [])]


def _match(opts: list, key: str, skey: str, sname: str):
    """Trouve l'ID d'équipe dans `opts` par 4 niveaux : (1) nom EXACT, (2) contenu bidirectionnel,
    (3) TOKEN distinctif ≥6 (« RB Bragantino » vs « Red Bull Bragantino »), (4) INITIALES pour un sigle
    court (« PSG » = Paris Saint-Germain). None si aucun."""
    for p in opts:                       # 1) exact
        if _norm(p.get("homeName")) in (key, skey):
            return p.get("homeTeamId")
        if _norm(p.get("awayName")) in (key, skey):
            return p.get("awayTeamId")
    if len(skey) >= 4:                   # 2) contenu
        for p in opts:
            _nh, _na = _norm(p.get("homeName")), _norm(p.get("awayName"))
            if _nh and (skey in _nh or _nh in skey):
                return p.get("homeTeamId")
            if _na and (skey in _na or _na in skey):
                return p.get("awayTeamId")
    _toks = sorted((_norm(w) for w in _re.split(r"[\s.\-]+", sname) if w), key=len, reverse=True)
    _big = next((t for t in _toks if len(t) >= 6), "")
    if _big:                             # 3) token distinctif
        for p in opts:
            if _big in _norm(p.get("homeName")):
                return p.get("homeTeamId")
            if _big in _norm(p.get("awayName")):
                return p.get("awayTeamId")
    if 2 <= len(key) <= 4:               # 4) initiales (sigle)
        def _ini(nm):
            return "".join(w[0] for w in _re.split(r"[\s.\-]+", nm or "") if w).lower()
        for p in opts:
            if _ini(p.get("homeName")) == key:
                return p.get("homeTeamId")
            if _ini(p.get("awayName")) == key:
                return p.get("awayTeamId")
    return None


def team_id(name: str):
    """ID FotMob de l'équipe `name` (caché). None si introuvable/panne."""
    key = _norm(name)
    if not key:
        return None
    c = _load()
    if c.get(key):                       # POSITIF caché -> renvoie direct (jamais re-cherché)
        return c[key]
    if key in _NEG:                      # négatif DÉJÀ vu CETTE session -> pas de re-recherche, mais NON figé
        return None                      # sur disque -> re-tenté au prochain démarrage (capte les fixes de résolution)
    tid = None
    sname = _clean(name)                  # nom sans suffixe état/genre -> meilleure résolution FotMob
    skey = _norm(sname) or key
    _al = _ALIAS.get(key) or _ALIAS.get(skey)   # sigle connu -> nom complet (recherche + matching)
    if _al:
        sname, skey = _al, _norm(_al)
    # FotMob suggest est SENSIBLE au libellé exact (« AS Monaco » -> 0 résultat, « Monaco » -> OK). On essaie
    # donc le nom complet PUIS les mots DISTINCTIFS (les plus longs) jusqu'à trouver. Chaque essai est matché
    # par _match (exact/contenu/token/initiales). Bornes 3 essais -> peu d'appels, résout les libellés à préfixe.
    _terms = [sname or name]
    for _w in sorted((w for w in _re.split(r"[\s.\-]+", sname or name) if len(_norm(w)) >= 5),
                     key=len, reverse=True):
        if _norm(_w) not in {_norm(t) for t in _terms}:
            _terms.append(_w)
    try:
        for _term in _terms[:3]:
            tid = _match(_fetch(_term), key, skey, sname or name)
            if tid:
                break
    except Exception:
        return None                      # panne -> ne PAS cacher (re-tentera plus tard)
    if not tid:                          # ÉCHEC -> mémoire seule (jamais figé disque) : re-tenté au redémarrage
        _NEG.add(key)                    # -> un club sans logo aujourd'hui peut en avoir un demain (fix/FotMob)
        return None
    with _LOCK:                          # SUCCÈS -> persiste le positif (jamais re-cherché)
        c[key] = tid
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            json.dump(c, open(_CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
    return tid


def logo_url(tid):
    return f"https://images.fotmob.com/image_resources/logo/teamlogo/{tid}.png" if tid else None
