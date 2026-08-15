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


def team_id(name: str):
    """ID FotMob de l'équipe `name` (caché). None si introuvable/panne."""
    key = _norm(name)
    if not key:
        return None
    c = _load()
    if key in c:
        return c[key] or None            # négatif caché possible (None)
    tid = None
    sname = _clean(name)                  # nom sans suffixe état/genre -> meilleure résolution FotMob
    skey = _norm(sname) or key
    try:
        r = httpx.get("https://apigw.fotmob.com/searchapi/suggest",
                      params={"term": sname or name, "lang": "en"}, headers=_UA, timeout=8)
        d = r.json()
        opts = [o.get("payload", {}) for g in d.get("matchSuggest", []) for o in g.get("options", [])]
        for p in opts:                   # 1) match EXACT du nom (original OU nettoyé)
            if _norm(p.get("homeName")) in (key, skey):
                tid = p.get("homeTeamId"); break
            if _norm(p.get("awayName")) in (key, skey):
                tid = p.get("awayTeamId"); break
        if not tid and len(skey) >= 4:   # 2) repli : le nom (nettoyé) est contenu, dans un sens ou l'autre
            for p in opts:
                _nh, _na = _norm(p.get("homeName")), _norm(p.get("awayName"))
                if _nh and (skey in _nh or _nh in skey):
                    tid = p.get("homeTeamId"); break
                if _na and (skey in _na or _na in skey):
                    tid = p.get("awayTeamId"); break
        if not tid:                      # 3) TOKEN distinctif partagé (≥6 lettres) : ex. « RB Bragantino »
            # (abrégé) vs « Red Bull Bragantino » (FotMob) -> le mot « bragantino » est commun. Dernier repli,
            # sur le mot le plus long (≥6) du nom nettoyé -> on prend le 1er résultat FotMob qui le contient
            # (déjà classé par pertinence à la recherche -> peu de faux positifs).
            _toks = sorted((_norm(w) for w in _re.split(r"[\s.\-]+", sname or name) if w), key=len, reverse=True)
            _big = next((t for t in _toks if len(t) >= 6), "")
            if _big:
                for p in opts:
                    if _big in _norm(p.get("homeName")):
                        tid = p.get("homeTeamId"); break
                    if _big in _norm(p.get("awayName")):
                        tid = p.get("awayTeamId"); break
    except Exception:
        return None                      # panne -> ne PAS cacher (re-tentera plus tard)
    with _LOCK:
        c[key] = tid
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            json.dump(c, open(_CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
    return tid


def logo_url(tid):
    return f"https://images.fotmob.com/image_resources/logo/teamlogo/{tid}.png" if tid else None
