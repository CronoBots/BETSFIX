"""Stats JOUEUR FOOT (props buteur/tirs) — stats saison via ESPN (gratuit, sans clé).

Pour parier les PROPS joueur foot (buteur, tirs, tirs cadrés…) avec des DONNÉES et non au feeling : on
résout le joueur par nom (recherche ESPN -> id numérique + compétition), puis on lit son overview saison.

Best-effort STRICT : timeout court, toute panne -> {} (le dossier continue sans). Caches par processus.

NB : les fonctions de props BASKET (player_stats/props_block/_lookup) ont été retirées le 2026-08-13
(app 100 % FOOT). Ce module ne sert plus qu'au foot (soccer_props_block, branché au scan).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0"}
_T = 12.0
_id_cache: dict = {}
_stat_cache: dict = {}


def _get(url: str):
    try:
        req = urllib.request.Request(url, headers=_UA)
        return json.loads(urllib.request.urlopen(req, timeout=_T).read().decode("utf-8", "replace"))
    except Exception:
        return None


def _lookup_soccer(name: str) -> tuple:
    """(id_numérique, slug_compétition) d'un joueur FOOT par nom (recherche ESPN). (None, None) sinon."""
    key = "S:" + name
    if key in _id_cache:
        return _id_cache[key]
    res = (None, None)
    j = _get("https://site.web.api.espn.com/apis/search/v2?limit=6&query=" + urllib.parse.quote(name))
    for r in (j or {}).get("results") or []:
        if r.get("type") != "player":
            continue
        c = (r.get("contents") or [{}])[0]
        if c.get("sport") != "soccer":
            break
        mid = re.search(r"a:(\d+)", c.get("uid") or "")
        if mid:
            res = (mid.group(1), c.get("defaultLeagueSlug") or "all")
        break
    _id_cache[key] = res
    return res


def soccer_player_stats(name: str) -> dict:
    """Stats SAISON d'un joueur foot (compétition la plus jouée) via ESPN : {comp, starts, goals,
    assists, shots, shots_on_target}. {} si indispo."""
    key = "soc:" + name
    if key in _stat_cache:
        return _stat_cache[key]
    out: dict = {}
    pid, slug = _lookup_soccer(name)
    if pid:
        j = _get(f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/{slug}"
                 f"/athletes/{pid}/overview")
        st = (j or {}).get("statistics") or {}
        names = st.get("names") or []
        idx = {n: i for i, n in enumerate(names)}

        def _v(sp, n):
            i = idx.get(n)
            vals = sp.get("stats") or []
            if i is not None and i < len(vals):
                try:
                    return float(vals[i])
                except (ValueError, TypeError):
                    return None
            return None

        best, bestn = None, 0.0          # split (compétition) avec le plus de titularisations
        for sp in st.get("splits") or []:
            s = _v(sp, "starts") or 0.0
            if s > bestn:
                best, bestn = sp, s
        if best and bestn > 0:
            def _i(n):
                v = _v(best, n)
                return int(v) if v is not None else None
            out = {"comp": best.get("displayName") or best.get("name"), "starts": int(bestn),
                   "goals": _i("totalGoals"), "assists": _i("goalAssists"),
                   "shots": _i("totalShots"), "shots_on_target": _i("shotsOnTarget")}
    _stat_cache[key] = out
    return out


def soccer_props_block(players: list, max_players: int = 8) -> str:
    """Bloc « DONNÉES JOUEURS » foot pour le dossier : buts/passes/tirs saison (+ par match) des joueurs
    cités dans les props. '' si rien trouvé."""
    lines = []
    for name in list(dict.fromkeys(p for p in players if p))[:max_players]:
        s = soccer_player_stats(name)
        st = s.get("starts")
        if not st:
            continue
        parts = []
        if s.get("goals") is not None:
            parts.append(f"{s['goals']} buts ({s['goals'] / st:.2f}/match)")
        if s.get("assists") is not None:
            parts.append(f"{s['assists']} passes déc.")
        if s.get("shots") is not None:
            parts.append(f"{s['shots']} tirs ({s['shots'] / st:.1f}/match)")
        if s.get("shots_on_target") is not None:
            parts.append(f"{s['shots_on_target']} cadrés")
        if parts:
            lines.append(f"- {name} [{st} match. {s['comp']}] : " + " ; ".join(parts))
    if not lines:
        return ""
    return ("\n\nDONNÉES JOUEURS (stats saison, ESPN — pour parier les props joueur foot : buteur, tirs… ; "
            "compare buts/tirs PAR MATCH à la ligne du marché) :\n" + "\n".join(lines))
