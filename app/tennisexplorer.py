"""TennisExplorer — source GRATUITE et À JOUR pour le BILAN PAR SURFACE au tennis (le facteur n°1).

Comble le trou laissé par SofaScore (mort) et les archives Elo périmées (Ultimate Tennis Statistics /
Sackmann arrêtés fin 2024). TennisExplorer, lui, est à jour (données 2026) et publie le bilan Victoires/
Défaites de chaque joueur PAR SURFACE (Terre/Dur/Indoor/Gazon), en carrière ET sur l'année en cours — ce
qu'un classement ATP/WTA brut ne dit pas (un top-30 peut être 7-9 sur gazon).

Scrape HTML léger (pas d'API), 100 % tolérant (jamais d'exception vers l'appelant), avec cache mémoire.
"""
from __future__ import annotations

import re
import unicodedata

_BASE = "https://www.tennisexplorer.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
# colonnes du tableau « Summary » de TennisExplorer, dans l'ordre du site :
_COLS = ("total", "clay", "hard", "indoors", "grass", "notset")
# surface (libellé _surface_hint) -> colonne du bilan
_SURF_COL = {"Terre battue": "clay", "Dur": "hard", "Dur (indoor)": "indoors", "Gazon": "grass"}
_CACHE: dict = {}


def _deacc(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()


def _toks(s: str) -> set:
    return {w for w in re.split(r"[^a-z0-9]+", _deacc(s)) if len(w) > 1}


async def _slug(client, name: str) -> str | None:
    """Nom joueur -> slug TennisExplorer (meilleur recouvrement de tokens, singles uniquement)."""
    try:
        r = await client.get(f"{_BASE}/list-players/", params={"search-text-pl": name},
                             headers={"User-Agent": _UA}, timeout=15)
        if r.status_code != 200:
            return None
    except Exception:
        return None
    want = _toks(name)
    best, best_sc = None, 0
    for slug, disp in re.findall(r'/player/([a-z0-9-]+)/"[^>]*>([^<]+)</a>', r.text):
        sc = len(want & _toks(disp))
        if sc > best_sc:
            best, best_sc = slug, sc
    return best if best_sc >= 1 else None


def _parse_summary(html: str) -> dict | None:
    """Renvoie {'career': {col: (w,l)}, 'year': {col: (w,l)}} depuis le tableau « Summary »."""
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"\s+", " ", txt)
    row = r"((?:\d+/\d+|-)(?:\s+(?:\d+/\d+|-)){5})"           # 6 cellules « W/L » ou « - »
    out = {}
    m = re.search(r"Summary:\s*" + row, txt)
    if m:
        out["career"] = _cells(m.group(1))
    my = re.search(r"\b(20\d\d)\s+" + row, txt)               # 1re ligne annuelle = année en cours
    if my:
        out["year"] = _cells(my.group(2))
        out["year_label"] = my.group(1)
    return out or None


def _cells(s: str) -> dict:
    vals = s.split()
    out = {}
    for col, v in zip(_COLS, vals):
        if "/" in v:
            w, l = v.split("/")
            out[col] = (int(w), int(l))
    return out


async def surface_record(client, name: str) -> dict | None:
    """{col: (w,l)} carrière + année pour un joueur. None si introuvable. Caché en mémoire."""
    key = _deacc(name)
    if key in _CACHE:
        return _CACHE[key]
    rec = None
    try:
        slug = await _slug(client, name)
        if slug:
            r = await client.get(f"{_BASE}/player/{slug}/", headers={"User-Agent": _UA}, timeout=15)
            if r.status_code == 200:
                rec = _parse_summary(r.text)
    except Exception:
        rec = None
    _CACHE[key] = rec
    return rec


def _fmt(rec: dict, col: str) -> str:
    """« 7-9 en carrière (2-2 en 2026) » pour une colonne surface, ou '' si pas de données."""
    car = (rec.get("career") or {}).get(col)
    yr = (rec.get("year") or {}).get(col)
    if not car:
        return ""
    s = f"{car[0]}-{car[1]} en carrière"
    if yr and (yr[0] or yr[1]):
        s += f" ({yr[0]}-{yr[1]} en {rec.get('year_label', 'cette année')})"
    return s


async def surface_facts(client, home: str, away: str, surface: str) -> list[str]:
    """Faits « bilan sur <surface> » des 2 joueurs (TennisExplorer) pour la surface du match. [] si rien."""
    col = _SURF_COL.get(surface)
    if not col:
        return []
    facts = []
    for label in (home, away):
        rec = await surface_record(client, label)
        if rec:
            b = _fmt(rec, col)
            if b:
                facts.append(f"Bilan {surface.lower()} [{label}] : {b} (TennisExplorer) — pondère fortement "
                             f"le niveau réel sur cette surface, au-delà du classement.")
    return facts
