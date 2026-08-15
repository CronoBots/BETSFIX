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


def team_id(name: str):
    """ID FotMob de l'équipe `name` (caché). None si introuvable/panne."""
    key = _norm(name)
    if not key:
        return None
    c = _load()
    if key in c:
        return c[key] or None            # négatif caché possible (None)
    tid = None
    try:
        r = httpx.get("https://apigw.fotmob.com/searchapi/suggest",
                      params={"term": name, "lang": "en"}, headers=_UA, timeout=8)
        d = r.json()
        opts = [o.get("payload", {}) for g in d.get("matchSuggest", []) for o in g.get("options", [])]
        for p in opts:                   # 1) match EXACT du nom
            if _norm(p.get("homeName")) == key:
                tid = p.get("homeTeamId"); break
            if _norm(p.get("awayName")) == key:
                tid = p.get("awayTeamId"); break
        if not tid:                      # 2) repli : le nom est contenu
            for p in opts:
                if key and key in _norm(p.get("homeName")):
                    tid = p.get("homeTeamId"); break
                if key and key in _norm(p.get("awayName")):
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
