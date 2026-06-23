"""Source d'enrichissement n°N : feed GISMO de Sportradar (le moteur en amont des sites de stats).

Accès LIBRE (aucun token) : `https://lsc.fn.sportradar.com/common/{lang}/Etc:UTC/gismo/{endpoint}/{id}`.
On l'interroge en **français** (`fr`) pour que les noms d'équipes matchent ceux du scan (Unibet FR).

Rôle : 2e/3e source indépendante pour des FAITS concrets (forme récente, série en cours, H2H,
position au classement) — utile depuis la mort de SofaScore. Le module est TOLÉRANT : toute panne
réseau / format renvoie '' ou [] et n'élève jamais (ne doit jamais casser un scan).

Mapping match Unibet -> id Sportradar : la page StatsHub du sport liste les /match/{id} du jour ;
on lit `match_info` (FR) de chaque candidat et on matche par noms (déaccentués) + jour du coup d'envoi.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_GISMO = "https://lsc.fn.sportradar.com/common/fr/Etc:UTC/gismo"
_PAGE = "https://statshub.sportradar.com/unibet/en/sport"   # liste des /match/{id} (ids indép. de la langue)
_SPORT_ID = {"foot": 1, "tennis": 5, "basket": 2}
_UA = {"User-Agent": "Mozilla/5.0"}
_T = 12

# Caches mémoire (durée de vie = process scan) : évite de re-télécharger pendant un même scan.
_PAGE_IDS: dict[int, list[int]] = {}
_INFO: dict[int, dict] = {}
_RESOLVED: dict[tuple, int | None] = {}
_SEASON_TB: dict = {}     # seasonid -> doc classement (partagé par tous les matchs du championnat)
_SEASON_OU: dict = {}     # seasonid -> {uid: stats over/under} (idem)
_STREAKS: dict = {}       # uid -> doc streaks


def _deacc(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _toks(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", _deacc(s)) if len(w) >= 3}


def _overlap(a: str, b: str) -> bool:
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    if ta & tb:
        return True
    # repli préfixe 5 lettres (Norvège/Norway ne s'appliquent pas — on est en FR des 2 côtés)
    return any(len(x) >= 5 and len(y) >= 5 and x[:5] == y[:5] for x in ta for y in tb)


async def _gismo(client, endpoint: str, ident) -> dict | list | None:
    """GET GISMO best-effort -> doc[0].data, ou None (exception / réseau / format)."""
    try:
        r = await client.get(f"{_GISMO}/{endpoint}/{ident}", headers=_UA, timeout=_T)
        if r.status_code != 200:
            return None
        doc = (r.json() or {}).get("doc") or []
        if not doc:
            return None
        d = doc[0].get("data")
        if isinstance(d, dict) and d.get("_doc") == "exception":
            return None
        if doc[0].get("event") == "exception":
            return None
        return d
    except Exception:
        return None


async def _match_ids(client, sport: str) -> list[int]:
    sid = _SPORT_ID.get(sport)
    if not sid:
        return []
    if sid in _PAGE_IDS:
        return _PAGE_IDS[sid]
    ids: list[int] = []
    try:
        r = await client.get(f"{_PAGE}/{sid}", headers=_UA, timeout=20)
        if r.status_code == 200:
            seen = set()
            for m in re.findall(r"/match/(\d{6,9})", r.text):
                if m not in seen:
                    seen.add(m)
                    ids.append(int(m))
    except Exception:
        ids = []
    _PAGE_IDS[sid] = ids
    return ids


async def _info(client, mid: int) -> dict | None:
    if mid in _INFO:
        return _INFO[mid]
    d = await _gismo(client, "match_info", mid)
    m = (d or {}).get("match") if isinstance(d, dict) else None
    _INFO[mid] = m or {}
    return _INFO[mid]


async def _resolve(client, sport: str, home: str, away: str, start: str) -> int | None:
    """id de match Sportradar pour ce match Unibet (noms FR + jour), ou None."""
    key = (sport, _deacc(home), _deacc(away))
    if key in _RESOLVED:
        return _RESOLVED[key]
    target_day = None
    try:
        target_day = datetime.fromisoformat((start or "").replace("Z", "+00:00")).date()
    except ValueError:
        pass
    found = None
    for mid in (await _match_ids(client, sport))[:60]:
        m = await _info(client, mid)
        if not m:
            continue
        th = (m.get("teams") or {}).get("home", {}).get("name", "")
        ta = (m.get("teams") or {}).get("away", {}).get("name", "")
        # accepte les 2 orientations (matchs neutres : home/away parfois inversés entre sources)
        if not ((_overlap(home, th) and _overlap(away, ta))
                or (_overlap(home, ta) and _overlap(away, th))):
            continue
        if target_day:                                   # confirme par le jour du coup d'envoi (±1)
            try:
                uts = (m.get("_dt") or {}).get("uts")
                md = datetime.utcfromtimestamp(uts).date() if uts else None
                if md and abs((md - target_day).days) > 1:
                    continue
            except Exception:
                pass
        found = mid
        break
    _RESOLVED[key] = found
    return found


_FORM_FR = {"W": "V", "D": "N", "L": "D"}   # Win/Draw/Loss -> Victoire/Nul/Défaite


def _form_str(side: dict) -> str:
    seq = [_FORM_FR.get((f or {}).get("type"), "?") for f in (side.get("form") or [])][:5]
    return "".join(seq)


# Séries de pari (stats_team_streaks) -> libellés FR. value >= 3 pour être notable.
_STREAK_FR = {
    "nolosing": "matchs sans défaite", "nowin": "matchs sans victoire",
    "winning": "victoires de rang", "losing": "défaites de rang", "draw": "nuls de rang",
    "scored": "matchs à marquer", "notscored": "matchs sans marquer", "noscore": "matchs sans marquer",
    "conceded": "matchs à encaisser", "notconceded": "matchs sans encaisser (clean sheets)",
    "cleansheet": "clean sheets de rang", "bothscored": "matchs avec BTTS",
    "over15": "matchs à +1,5 but", "over25": "matchs à +2,5 buts", "under25": "matchs à −2,5 buts",
}
_STREAK_PRIO = ["winning", "losing", "nolosing", "nowin", "scored", "notconceded",
                "noscore", "bothscored", "over25", "under25", "conceded"]


async def _team_streaks(client, uid) -> list[str]:
    """Séries notables (≥3) d'une équipe : 'X matchs sans défaite', 'Y à marquer'…"""
    if uid in _STREAKS:
        d = _STREAKS[uid]
    else:
        d = await _gismo(client, "stats_team_streaks", uid)
        _STREAKS[uid] = d
    st = (d or {}).get("streaks") or {} if isinstance(d, dict) else {}
    out = []
    for key in _STREAK_PRIO:
        v = ((st.get(key) or {}).get("total") or {}).get("value")
        if v and v >= 3 and key in _STREAK_FR:
            out.append(f"{v} {_STREAK_FR[key]}")
        if len(out) >= 3:
            break
    return out


async def _season_overunder(client, seasonid) -> dict:
    if seasonid in _SEASON_OU:
        return _SEASON_OU[seasonid]
    d = await _gismo(client, "stats_season_overunder", seasonid)
    stats = (d or {}).get("stats") or {} if isinstance(d, dict) else {}
    _SEASON_OU[seasonid] = stats
    return stats


def _ou_team(stats: dict, uid, sport: str) -> str:
    """'X.X marqués / Y.Y encaissés /match (n) [· +2,5 buts Z%]' pour une équipe, ou ''."""
    e = stats.get(str(uid)) or stats.get(uid)
    if not isinstance(e, dict):
        return ""
    gs = ((e.get("goalsscored") or {}).get("ft") or {}).get("average")
    gc = ((e.get("conceded") or {}).get("ft") or {}).get("average")
    n = e.get("matches")
    if gs is None and gc is None:
        return ""
    unit = "pts" if sport == "basket" else "buts"
    fmt = lambda x: (f"{round(x, 1):g}" if isinstance(x, (int, float)) else "?")
    s = f"{fmt(gs)} {unit} marqués / {fmt(gc)} encaissés /match" + (f" ({n})" if n else "")
    if sport == "foot":
        t = ((e.get("total") or {}).get("ft") or {}).get("2.5") or {}
        o, u = t.get("over") or 0, t.get("under") or 0
        if o + u:
            s += f" · +2,5 buts {round(100 * o / (o + u))}%"
    return s


async def facts(client, sport: str, home: str, away: str, start: str) -> list[str]:
    """Faits Sportradar (forme, séries, H2H, classement, moyennes buts) pour ce match. [] si rien."""
    mid = await _resolve(client, sport, home, away, start)
    if not mid:
        return []
    m = await _info(client, mid) or {}
    teams = m.get("teams") or {}
    sr_h = teams.get("home") or {}
    sr_a = teams.get("away") or {}
    # Aligne l'orientation Sportradar sur la requête (par nom) -> libellés home/away toujours corrects.
    if _overlap(home, sr_a.get("name", "")) and not _overlap(home, sr_h.get("name", "")):
        sr_h, sr_a = sr_a, sr_h
    hid, aid = sr_h.get("_id"), sr_a.get("_id")
    uh, ua = sr_h.get("uid"), sr_a.get("uid")
    seasonid = m.get("_seasonid")
    out: list[str] = []
    # --- forme 5 derniers (stats_match_form) — côté sélectionné par _id (orientation sûre) ---
    fm = await _gismo(client, "stats_match_form", mid)
    if isinstance(fm, dict):
        _fsides = [(fm.get("teams") or {}).get("home") or {}, (fm.get("teams") or {}).get("away") or {}]
        _by_id = {(s.get("team") or {}).get("_id"): s for s in _fsides}
        fh = _form_str(_by_id.get(hid) or {})
        fa = _form_str(_by_id.get(aid) or {})
        if fh or fa:
            out.append(f"Forme Sportradar (5 derniers, V/N/D) — {home} : {fh or '?'} · {away} : {fa or '?'}")
    # --- séries de pari (stats_team_streaks) — foot & basket ---
    if sport in ("foot", "basket") and uh and ua:
        sh, sa = await _team_streaks(client, uh), await _team_streaks(client, ua)
        ser = " · ".join(x for x in (f"{home} : {', '.join(sh)}" if sh else "",
                                     f"{away} : {', '.join(sa)}" if sa else "") if x)
        if ser:
            out.append(f"Séries Sportradar — {ser}")
    # --- H2H : confrontations directes (stats_team_versus par uid) ---
    if uh and ua:
        vs = await _gismo(client, "stats_team_versus", f"{uh}/{ua}")
        rec = _h2h_record(vs, hid, aid, home, away) if isinstance(vs, dict) else ""
        if rec:
            out.append(rec)
    # --- position au classement (stats_season_tables, caché par saison) ---
    if seasonid:
        if seasonid in _SEASON_TB:
            tb = _SEASON_TB[seasonid]
        else:
            tb = await _gismo(client, "stats_season_tables", seasonid)
            _SEASON_TB[seasonid] = tb
        pos = _table_pos(tb, home, away) if isinstance(tb, dict) else ""
        if pos:
            out.append(pos)
    # --- moyennes marqués/encaissés + over 2,5 (stats_season_overunder) — foot & basket ---
    if sport in ("foot", "basket") and seasonid and uh and ua:
        ou = await _season_overunder(client, seasonid)
        oh, oa = _ou_team(ou, uh, sport), _ou_team(ou, ua, sport)
        line = " · ".join(x for x in (f"{home} : {oh}" if oh else "",
                                      f"{away} : {oa}" if oa else "") if x)
        if line:
            out.append(f"Moyennes saison Sportradar — {line}")
    # --- compositions confirmées (match_squads) — foot, si dispo (proche du coup d'envoi) ---
    if sport == "foot":
        sq = await _gismo(client, "match_squads", mid)
        fl = _formations(sq, hid, aid, home, away) if isinstance(sq, dict) else ""
        if fl:
            out.append(fl)
    return out


def _formations(sq: dict, hid, aid, hn: str, an: str) -> str:
    """'Compositions confirmées — home 4-3-3 · away 4-2-3-1' si les compos sont posées (sinon '')."""
    sq_home_id = (((sq.get("match") or {}).get("teams") or {}).get("home") or {}).get("_id")

    def form(tid):
        side = "home" if sq_home_id == tid else "away"
        return ((sq.get(side) or {}).get("startinglineup") or {}).get("formation")

    fh, fa = form(hid), form(aid)
    parts = [x for x in (f"{hn} {fh}" if fh else "", f"{an} {fa}" if fa else "") if x]
    return "Compositions confirmées (Sportradar) — " + " · ".join(parts) if parts else ""


def _h2h_record(vs: dict, hid, aid, hn: str, an: str) -> str:
    """Bilan H2H depuis la liste `matches` des confrontations (avec résultats)."""
    hw = aw = dr = 0
    for mt in vs.get("matches") or []:
        res = mt.get("result") or {}
        rh, ra = res.get("home"), res.get("away")
        if rh is None or ra is None:
            continue
        mt_teams = mt.get("teams") or {}
        mh = (mt_teams.get("home") or {}).get("_id")
        ma = (mt_teams.get("away") or {}).get("_id")
        if rh == ra:
            dr += 1
            continue
        win = mh if rh > ra else ma
        if win == hid:
            hw += 1
        elif win == aid:
            aw += 1
    tot = hw + aw + dr
    if not tot:
        return ""
    return f"H2H Sportradar ({tot} confrontation(s)) — {hn} {hw}, {an} {aw}, {dr} nul(s)"


def _table_pos(tb: dict, hn: str, an: str) -> str:
    """Position + points de chaque équipe dans les tables du classement (ligues & groupes de coupe)."""
    rows: list = []

    def walk(o):
        if isinstance(o, dict):
            rws = o.get("tablerows") or o.get("tablerow")
            if isinstance(rws, list):
                rows.extend(rws)
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(tb)

    def find(name):
        for r in rows:
            if _overlap(name, (r.get("team") or {}).get("name", "")):
                pos = r.get("pos") or r.get("position")
                pts = r.get("pointsTotal") if r.get("pointsTotal") is not None else r.get("points")
                won = r.get("winner") if r.get("winner") is not None else r.get("won")
                lost = r.get("loser") if r.get("loser") is not None else r.get("lost")
                return pos, pts, won, lost
        return None

    def fmt(name, p):
        if not (p and p[0]):
            return ""
        pos, pts, won, lost = p
        if pts is not None:                          # ligues à points (foot)
            return f"{name} {pos}e ({pts} pts)"
        if won is not None and lost is not None:     # basket : bilan victoires-défaites
            return f"{name} {pos}e ({won}-{lost})"
        return f"{name} {pos}e"

    parts = [x for x in (fmt(hn, find(hn)), fmt(an, find(an))) if x]
    return "Classement Sportradar — " + " · ".join(parts) if parts else ""


# --- API publique (utilisée par le routeur app/routers/sportradar.py et le scan) ---
async def resolve(client, sport: str, home: str, away: str, start: str) -> int | None:
    return await _resolve(client, sport, home, away, start)


async def info(client, mid: int) -> dict | None:
    return await _info(client, mid)


async def gismo(client, endpoint: str, ident) -> dict | list | None:
    """Passerelle brute vers le feed GISMO (ex. endpoint='stats_season_tables', ident=101177)."""
    return await _gismo(client, endpoint, ident)


async def block(client, sport: str, match: dict) -> str:
    """Bloc texte 'SPORTRADAR' à coller dans le dossier de l'analyste. '' si rien."""
    try:
        f = await facts(client, sport, match.get("home", ""), match.get("away", ""),
                        match.get("start", ""))
    except Exception:
        return ""
    if not f:
        return ""
    return ("\n\nSPORTRADAR (source indépendante — faits à CROISER avec le reste ; "
            "présent ici ET confirmé ailleurs = 2 sources) :\n- " + "\n- ".join(f))
