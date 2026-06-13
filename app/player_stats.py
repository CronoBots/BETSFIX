"""Stats JOUEUR (props basket) — moyennes saison + forme récente, via ESPN (gratuit, sans clé).

Pour parier les PROPS joueur (points/rebonds/passes/contres/interceptions) avec des DONNÉES et non au
feeling : on résout le joueur par nom (recherche ESPN -> id numérique + ligue depuis l'uid
« …a:<ID>… », « l:46 » = NBA / « l:59 » = WNBA), puis on lit son game-log -> moyenne + 5 derniers
matchs par stat (les events sont du PLUS RÉCENT au plus ancien).

Best-effort STRICT : timeout court, toute panne -> {} (le dossier continue sans). Caches par processus.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0"}
_T = 12.0
_LEAGUE = {"46": "nba", "59": "wnba"}
# Mot-clé du marché Unibet (criterion, en minuscules) -> label de stat dans le game-log ESPN.
_STAT = {"points": "points", "rebonds": "totalRebounds", "passes": "assists",
         "contres": "blocks", "interceptions": "steals"}
_id_cache: dict = {}
_stat_cache: dict = {}


def _get(url: str):
    try:
        req = urllib.request.Request(url, headers=_UA)
        return json.loads(urllib.request.urlopen(req, timeout=_T).read().decode("utf-8", "replace"))
    except Exception:
        return None


def _lookup(name: str) -> tuple:
    """(id_numérique, slug_ligue) du joueur par nom (recherche ESPN). (None, None) si introuvable."""
    if name in _id_cache:
        return _id_cache[name]
    res = (None, None)
    j = _get("https://site.web.api.espn.com/apis/search/v2?limit=6&query=" + urllib.parse.quote(name))
    for r in (j or {}).get("results") or []:
        if r.get("type") != "player":
            continue
        c = (r.get("contents") or [{}])[0]
        uid = c.get("uid") or ""
        mid = re.search(r"a:(\d+)", uid)
        lg = re.search(r"l:(\d+)", uid)
        slug = _LEAGUE.get(lg.group(1)) if lg else c.get("defaultLeagueSlug")
        if mid and slug in ("nba", "wnba"):
            res = (mid.group(1), slug)
        break
    _id_cache[name] = res
    return res


def player_stats(name: str) -> dict:
    """{avg:{stat:moy}, last5:{stat:[v…]}, games:n, season:str} pour un joueur basket. {} si indispo.
    `stat` ∈ points/rebonds/passes/contres/interceptions. Moyenne sur la SAISON courante (régulière +
    playoffs), 5 derniers = les plus récents."""
    if name in _stat_cache:
        return _stat_cache[name]
    out: dict = {}
    pid, slug = _lookup(name)
    if pid:
        g = _get(f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/{slug}"
                 f"/athletes/{pid}/gamelog")
        if g:
            labels = g.get("names") or g.get("labels") or []
            idx = {lab: i for i, lab in enumerate(labels)}
            sts = g.get("seasonTypes") or []
            year = (sts[0].get("displayName") or "")[:7] if sts else ""    # ex. « 2025-26 »
            rows = []
            for st in sts:                       # saison courante : régulière + playoffs (récent d'abord)
                if year and (st.get("displayName") or "").startswith(year):
                    for c in st.get("categories") or []:
                        for e in c.get("events") or []:
                            if e.get("stats"):
                                rows.append(e["stats"])
            avg, last5 = {}, {}
            for key, lab in _STAT.items():
                i = idx.get(lab)
                if i is None:
                    continue
                vals = []
                for r in rows:
                    if i < len(r):
                        try:
                            vals.append(float(r[i]))
                        except (ValueError, TypeError):
                            pass
                if vals:
                    avg[key] = round(sum(vals) / len(vals), 1)
                    last5[key] = [int(v) if v == int(v) else round(v, 1) for v in vals[:5]]
            if avg:
                out = {"avg": avg, "last5": last5, "games": len(rows), "season": year}
    _stat_cache[name] = out
    return out


def props_block(players: list, max_players: int = 8) -> str:
    """Bloc « DONNÉES JOUEURS » prêt pour le dossier basket : moyenne saison + 5 derniers par stat,
    pour chaque joueur cité dans les props (au plus `max_players`). '' si rien trouvé."""
    lines = []
    for name in list(dict.fromkeys(p for p in players if p))[:max_players]:
        s = player_stats(name)
        if not s:
            continue
        parts = []
        for key in ("points", "rebonds", "passes", "contres", "interceptions"):
            if key in s["avg"]:
                last = s["last5"].get(key) or []
                lab = {"points": "pts", "rebonds": "reb", "passes": "passes",
                       "contres": "contres", "interceptions": "interc."}[key]
                parts.append(f"{lab} {s['avg'][key]} (5 der. {'/'.join(map(str, last))})")
        if parts:
            lines.append(f"- {name} [{s['games']} m. {s['season']}] : " + " ; ".join(parts))
    if not lines:
        return ""
    return ("\n\nDONNÉES JOUEURS (moyennes saison + 5 derniers matchs, ESPN — pour parier les PROPS "
            "joueur avec des chiffres ; compare la moyenne/forme à la ligne du marché) :\n" + "\n".join(lines))
