"""Sélection des matchs IMPORTANTS à analyser en profondeur — AVANT toute analyse coûteuse.

Deux critères, dans l'ordre :
1. **Prévisibilité (enjeu)** : on RELÈGUE les amicaux (« Amicaux », friendlies). Énorme profondeur
   de marché mais ~0 prévisibilité (rotations, motivation aléatoire) = quasi-injouables. On préfère
   les compétitions à enjeu (ligues, coupes, playoffs, tournois), qui sont riches en données.
2. **Profondeur de marché Unibet** (`nonLiveBoCount` du listView) = l'importance selon le book,
   utilisée comme tri SECONDAIRE à l'intérieur de chaque niveau.
On EXCLUT l'eSports et on garde le **top N par sport** (défaut 10/jour). Les amicaux ne remontent
qu'en REPLI, si pas assez de matchs compétitifs pour remplir le top N.

Volontairement indépendant de l'Elo du modèle (qui se trompe) : on se fie au marché + au volume
d'offres, dispo dans le listView SANS appel réseau par match. Fonctions pures + un fetch async.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UNIBET_B = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
UNIBET_PARAMS = {"lang": "fr_BE", "market": "BE", "client_id": "2", "channel_id": "1"}
# listView Unibet par sport de l'app.
LISTVIEW = {"foot": "football", "tennis": "tennis", "basket": "basketball"}


def _is_esport(group: str, path_names: list) -> bool:
    """Vrai si l'évènement est de l'eSports (à exclure) — détecté sur le nom de groupe/chemin."""
    blob = (group + " " + " ".join(path_names)).lower()
    return "esport" in blob or "e-sport" in blob or "cyber" in blob


def _is_friendly(group: str, path_names: list) -> bool:
    """Vrai si l'évènement est un AMICAL (à reléguer) — « Amicaux », « Amical », friendly/friendlies."""
    blob = (group + " " + " ".join(path_names)).lower()
    return "amica" in blob or "friendly" in blob or "friendlies" in blob


def _start_dt(s):
    """Parse le `start` Unibet (ISO 8601 ou epoch ms/s) -> datetime aware UTC, ou None."""
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(s / 1000 if s > 1e12 else s, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def rank_important(events: list, top_n: int = 10, within_hours: int | None = None) -> list:
    """Depuis les items `events` d'un listView Unibet, renvoie le TOP N matchs. eSports exclus.
    Tri : compétitif d'abord (les amicaux relégués), puis profondeur de marché (`nonLiveBoCount`)
    décroissante. `within_hours` : ne garde QUE les matchs à venir dans cette fenêtre (coup d'envoi
    futur ≤ N h) — sinon on tomberait sur des matchs lointains (sélections nationales à 4-6 jours)."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=within_hours) if within_hours else None
    rows = []
    for it in events or []:
        ev = it.get("event", it) if isinstance(it, dict) else {}
        group = ev.get("group") or ""
        path_names = [p.get("name", "") for p in (ev.get("path") or [])]
        if _is_esport(group, path_names):
            continue
        if horizon is not None:                          # fenêtre : futur uniquement (jamais commencé)
            dt = _start_dt(ev.get("start"))
            if dt is None or dt <= now or dt > horizon:
                continue
        rows.append({
            "id": ev.get("id"),
            "name": ev.get("name", ""),
            "home": ev.get("homeName", ""),
            "away": ev.get("awayName", ""),
            "comp": group,
            "circuit": _circuit_of(path_names),          # tennis : ATP/WTA/Challenger (depuis le chemin)
            "markets": ev.get("nonLiveBoCount", 0) or 0,
            "start": ev.get("start"),
            "friendly": _is_friendly(group, path_names),
        })
    # Compétitif (non-amical) AVANT amical ; profondeur de marché en tri secondaire.
    rows.sort(key=lambda r: (not r["friendly"], r["markets"]), reverse=True)
    return rows[:top_n]


import time as _time

_ODDS_CACHE: dict = {}   # sport -> (timestamp, {clé_noms: (o1, ox, o2)})
_LIVE_STATE_CACHE: dict = {}   # sport -> (timestamp, {clé_noms: liveData}) — score + horloge EN DIRECT
_META_CACHE: dict = {}   # sport -> (timestamp, {clé_noms: {circuit, comp, start}}) — Unibet path/group/heure
_ODDS_TTL = 25           # s : cotes Unibet rafraîchies au plus toutes les 25 s (gratuit, mais lean)


def _okey(home: str, away: str) -> str:
    return f"{(home or '').strip().lower()}|{(away or '').strip().lower()}"


def _circuit_of(path_names: list) -> str:
    """Circuit tennis (ATP/WTA/Challenger/ITF) depuis le chemin Unibet (`path`), ex.
    ['Tennis','ATP','s-Hertogenbosch'] -> 'ATP'. '' si non-tennis / introuvable."""
    for p in path_names or []:
        u = (p or "").upper()
        if any(k in u for k in ("WTA", "ATP", "CHALLENGER", "ITF")):
            return (p or "").strip()
    return ""


def unibet_meta_for(sport: str, home: str, away: str) -> dict | None:
    """Métadonnées Unibet FRAÎCHES du match (circuit, tournoi/`group`, heure de début), depuis le
    cache rempli par `fetch_live_odds` (0 appel en plus). None si absent (-> repli sur le sidecar)."""
    hit = _META_CACHE.get(sport)
    return hit[1].get(_okey(home, away)) if hit else None


def fresh_status(sport: str, home: str, away: str, sidecar_status: str,
                 has_live_score: bool, now=None) -> tuple:
    """Statut + heure de début EFFECTIFS, PILOTÉS PAR UNIBET (temps réel) plutôt que par l'heuristique
    de temps sur le sidecar (qui peut être PÉRIMÉE -> match affiché « live » alors qu'il n'a pas
    commencé). Renvoie (status, start_dt_unibet|None) :
    - score live Unibet -> 'inprogress' ; coup d'envoi Unibet FUTUR -> 'notstarted' ; sinon le sidecar."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    um = unibet_meta_for(sport, home, away) or {}
    sdt = _start_dt(um.get("start")) if um.get("start") else None
    if has_live_score:
        return "inprogress", sdt
    if sdt is not None and sdt > now:
        return "notstarted", sdt          # Unibet : coup d'envoi futur -> PAS commencé
    return sidecar_status, sdt


def _winner_odds(betoffers) -> tuple | None:
    """(o1, ox, o2) du marché VAINQUEUR depuis les betOffers d'un event (par type d'issue)."""
    for b in betoffers or []:
        crit = ((b.get("criterion") or {}).get("label") or "").lower()
        if "cotes du match" in crit or "temps réglementaire" in crit or "temps reglementaire" in crit:
            o = {oc.get("type"): oc.get("odds", 0) / 1000 for oc in (b.get("outcomes") or [])}
            if o.get("OT_ONE") and o.get("OT_TWO"):
                return (o.get("OT_ONE"), o.get("OT_CROSS"), o.get("OT_TWO"))
    return None


async def fetch_live_odds(sport: str, client=None) -> dict:
    """Cotes Unibet FRAÎCHES (vainqueur du match) pour tout un sport, en UN appel listView,
    clé = noms d'équipes. Mis en cache 25 s. Sert à actualiser les cotes affichées à chaque page
    (Unibet est gratuit ; SofaScore n'est jamais touché ici). {} si indispo (on garde le sidecar)."""
    now = _time.time()
    hit = _ODDS_CACHE.get(sport)
    if hit and now - hit[0] < _ODDS_TTL:
        return hit[1]
    import httpx
    path = LISTVIEW.get(sport, "football")
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    out, states, metas = {}, {}, {}
    try:
        r = await client.get(f"{UNIBET_B}/listView/{path}.json", params=UNIBET_PARAMS,
                             headers={"User-Agent": "Mozilla/5.0"})
        for it in (r.json() or {}).get("events") or []:
            ev = it.get("event", it)
            key = _okey(ev.get("homeName"), ev.get("awayName"))
            wo = _winner_odds(it.get("betOffers"))
            if wo:
                out[key] = wo
            ld = it.get("liveData")        # score + horloge EN DIRECT (même réponse, 0 appel en plus)
            if ld:
                states[key] = ld
            pn = [p.get("name", "") for p in (ev.get("path") or [])]
            metas[key] = {"circuit": _circuit_of(pn), "comp": ev.get("group") or "",
                          "start": ev.get("start")}
    except Exception:
        out = hit[1] if hit else {}        # repli sur le dernier cache si le fetch échoue
        states = metas = None              # ne pas écraser le dernier état/méta connu
    finally:
        if own:
            await client.aclose()
    _ODDS_CACHE[sport] = (now, out)
    if states is not None:
        _LIVE_STATE_CACHE[sport] = (now, states)
    if metas is not None:
        _META_CACHE[sport] = (now, metas)
    return out


def live_odds_for(live_map: dict, home: str, away: str) -> tuple | None:
    """Cotes fraîches (o1,ox,o2) pour ce match dans la map, ou None (-> garder le sidecar)."""
    return (live_map or {}).get(_okey(home, away))


def live_state_for(sport: str, home: str, away: str) -> dict | None:
    """`liveData` Unibet (score + horloge en direct) du match, depuis le cache rempli par
    `fetch_live_odds` (même réponse listView, donc AUCUN appel réseau en plus). None si absent."""
    hit = _LIVE_STATE_CACHE.get(sport)
    return hit[1].get(_okey(home, away)) if hit else None


_SOFA_LIVE_CACHE: dict = {}   # sofa_id -> (ts, fields) : score live SofaScore (repli quand Unibet manque)
_SOFA_LIVE_TTL = 30           # s : repli SofaScore caché 30 s (best-effort, petit endpoint event/{id})


def _sofa_live_fields(ev: dict, sport: str) -> dict:
    """Champs de scoreboard (même format que web.live_fields) à partir d'un event SofaScore EN COURS."""
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    hc, ac = hs.get("current"), as_.get("current")
    if hc is None or ac is None:
        return {}
    if sport == "tennis":                                  # jeux par set : period1..N
        sets = []
        for i in range(1, 6):
            ph, pa = hs.get(f"period{i}"), as_.get(f"period{i}")
            if ph is not None and pa is not None:
                sets.append(f"{ph}-{pa}")
        return {"score": " ".join(sets) if sets else f"{hc}-{ac}", "server": None, "game_pts": None}
    out = {"score": f"{hc}-{ac}", "live_time": "", "home_pts": hc, "away_pts": ac}
    if sport == "basket":                                  # quart-temps terminés (box-score)
        per = []
        for i in range(1, 8):
            ph, pa = hs.get(f"period{i}"), as_.get(f"period{i}")
            if ph is not None and pa is not None:
                per.append((ph, pa))
        if per:
            out["periods"] = per
    return out


async def fetch_sofa_live(sport: str, sofa_id) -> dict:
    """REPLI quand Unibet ne fournit PAS le score live : lit le score sur SofaScore (event/{id}, petit
    endpoint, PAS du bulk). Best-effort, caché 30 s par match. {} si indispo / non en cours."""
    if not sofa_id:
        return {}
    sid = str(sofa_id)
    if not (sid.isdigit() and len(sid) <= 8):
        return {}
    now = _time.time()
    hit = _SOFA_LIVE_CACHE.get(sid)
    if hit and now - hit[0] < _SOFA_LIVE_TTL:
        return hit[1]
    from app import sofa_http
    fields = {}
    try:
        r = await sofa_http.get(f"https://api.sofascore.com/api/v1/event/{sid}")
        ev = (r.json() or {}).get("event") or {} if r.status_code == 200 else {}
        if ((ev.get("status") or {}).get("type") or "").lower() == "inprogress":
            fields = _sofa_live_fields(ev, sport)
    except Exception:
        fields = {}
    _SOFA_LIVE_CACHE[sid] = (now, fields)
    return fields


async def fetch_event_offers(unibet_id, client=None) -> list:
    """TOUS les marchés (betOffers) LIVE d'un match Unibet (vainqueur, totaux, handicaps…). Utilisé
    pour la couverture « assurance » : trouver la cote live du pari opposé. [] si indispo."""
    if not unibet_id:
        return []
    import httpx
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(f"{UNIBET_B}/betoffer/event/{unibet_id}.json", params=UNIBET_PARAMS,
                             headers={"User-Agent": "Mozilla/5.0"})
        return (r.json() or {}).get("betOffers") or [] if r.status_code == 200 else []
    except Exception:
        return []
    finally:
        if own:
            await client.aclose()


async def fetch_events_with_odds(sport: str, client=None, within_hours: int = 48) -> list:
    """UN appel listView -> liste des matchs À VENIR (coup d'envoi futur ≤ within_hours) avec leurs
    cotes vainqueur : [{id, home, away, comp, start(ISO), odds:(o1,ox,o2)}]. eSports exclus, matchs
    sans cote ignorés. Sert au SUIVI DES VARIATIONS de cote (gratuit, Unibet ; SofaScore jamais touché)."""
    import httpx
    path = LISTVIEW.get(sport, "football")
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=within_hours)
    out = []
    try:
        r = await client.get(f"{UNIBET_B}/listView/{path}.json", params=UNIBET_PARAMS,
                             headers={"User-Agent": "Mozilla/5.0"})
        events = (r.json() or {}).get("events") or []
    except Exception:
        events = []
    finally:
        if own:
            await client.aclose()
    for it in events:
        ev = it.get("event", it)
        group = ev.get("group") or ""
        path_names = [p.get("name", "") for p in (ev.get("path") or [])]
        if _is_esport(group, path_names):
            continue
        dt = _start_dt(ev.get("start"))
        if dt is None or dt <= now or dt > horizon:        # à venir uniquement, dans la fenêtre
            continue
        wo = _winner_odds(it.get("betOffers"))
        if not wo:
            continue
        out.append({"id": ev.get("id"), "home": ev.get("homeName", ""),
                    "away": ev.get("awayName", ""), "comp": group,
                    "start": dt.isoformat(), "odds": wo})
    return out


async def fetch_important(sport: str, top_n: int = 10, client=None,
                          within_hours: int | None = None) -> list:
    """Récupère le listView Unibet du sport et renvoie le top N matchs importants à venir dans
    `within_hours` (cf. rank_important)."""
    import httpx
    path = LISTVIEW.get(sport, "football")
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(f"{UNIBET_B}/listView/{path}.json", params=UNIBET_PARAMS,
                             headers={"User-Agent": "Mozilla/5.0"})
        events = (r.json() or {}).get("events") or []
    finally:
        if own:
            await client.aclose()
    return rank_important(events, top_n, within_hours)
