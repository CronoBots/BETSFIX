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

from app.netconst import UNIBET_B, UNIBET_PARAMS   # source unique (cf. app/netconst.py)

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


def rank_important(events: list, top_n: int = 10, within_hours: int | None = None,
                   always=None) -> list:
    """Depuis les items `events` d'un listView Unibet, renvoie le TOP N matchs. eSports exclus.
    Tri : compétitif d'abord (les amicaux relégués), puis profondeur de marché (`nonLiveBoCount`)
    décroissante. `within_hours` : ne garde QUE les matchs à venir dans cette fenêtre (coup d'envoi
    futur ≤ N h). `always(comp)` : prédicat -> inclut TOUS les matchs de ces compétitions dans la
    fenêtre, MÊME au-delà du top N (cas spécial « gros tournois » : Coupe du Monde…)."""
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
    top = rows[:top_n]
    if always:                                  # force TOUS les gros tournois de la fenêtre (hors top N)
        seen = {r["id"] for r in top}
        for r in rows[top_n:]:
            if r["id"] not in seen and always(r.get("comp") or ""):
                top.append(r)
                seen.add(r["id"])
    return top


import asyncio
import time as _time

_ODDS_CACHE: dict = {}   # sport -> (timestamp, {clé_noms: (o1, ox, o2)})
_LIVE_STATE_CACHE: dict = {}   # sport -> (timestamp, {clé_noms: liveData}) — score + horloge EN DIRECT
_META_CACHE: dict = {}   # sport -> (timestamp, {clé_noms: {circuit, comp, start}}) — Unibet path/group/heure
_ODDS_TTL = 25           # s : cotes Unibet rafraîchies au plus toutes les 25 s (gratuit, mais lean)
_ODDS_REFRESHING: set = set()   # sports avec un rafraîchissement live DÉJÀ en vol (anti-doublon de fetch)

# LIVE « COLLANT » : mémoire du DERNIER instant où un score live a été vu pour un match. Sert à ne PAS
# éjecter du direct un match RÉELLEMENT en cours lors d'un hoquet BREF du flux (score momentanément
# absent) : sans ça, dès qu'un match tourne depuis plus que le seuil `likely_finished`, la moindre
# coupure réseau le bascule en « terminé » et il DISPARAÎT du Live (bug vécu Auger-Aliassime–Djokovic,
# match commencé en retard puis évincé en plein 4e set). Fenêtre courte -> un match VRAIMENT fini finit
# bien par sortir du direct (le flux ne renvoie plus de score pendant > STICKY_S).
_LIVE_SEEN: dict = {}     # "sport:clé_noms" -> epoch du dernier score live observé
_LIVE_STICKY_S = 360      # 6 min de tolérance aux hoquets du flux live


def note_live(sport: str, home: str, away: str, has_score: bool) -> None:
    """Mémorise qu'un score live vient d'être vu pour ce match (pour le live « collant »)."""
    if has_score:
        _LIVE_SEEN[f"{sport}:{_okey(home, away)}"] = _time.time()


def sticky_live(sport: str, home: str, away: str) -> bool:
    """Un score live a-t-il été vu pour ce match il y a MOINS de _LIVE_STICKY_S ? (tolérance aux hoquets)."""
    ts = _LIVE_SEEN.get(f"{sport}:{_okey(home, away)}")
    return ts is not None and (_time.time() - ts) < _LIVE_STICKY_S


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
                 has_live_score: bool, now=None, start_iso: str | None = None) -> tuple:
    """Statut + heure de début EFFECTIFS, PILOTÉS PAR UNIBET (temps réel) plutôt que par l'heuristique
    de temps sur le sidecar (qui peut être PÉRIMÉE -> match affiché « live » alors qu'il n'a pas
    commencé). Renvoie (status, start_dt_unibet|None) :
    - score live Unibet -> 'inprogress' ; coup d'envoi Unibet FUTUR -> 'notstarted' ; sinon le sidecar.
    `start_iso` (coup d'envoi du SIDECAR) : si Unibet annonce un coup d'envoi à PLUS DE 12 H de
    celui du sidecar, c'est un AUTRE match de la même affiche (série de playoffs : mêmes équipes
    plusieurs fois) -> on IGNORE les données live Unibet (sinon le match du 10 « vole » le score
    live de celui du 12 et un terminé remonte en En direct, bug vécu 2026-06-12)."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    um = unibet_meta_for(sport, home, away) or {}
    sdt = _start_dt(um.get("start")) if um.get("start") else None
    own = _start_dt(start_iso) if start_iso else None
    if sdt is not None and own is not None and abs((sdt - own).total_seconds()) > 12 * 3600:
        return sidecar_status, None       # le live Unibet appartient à un autre match de la série
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
    (Unibet est gratuit ; SofaScore n'est jamais touché ici). {} si indispo (on garde le sidecar).

    STALE-WHILE-REVALIDATE : renvoie le DERNIER cache connu IMMÉDIATEMENT (l'internaute n'attend JAMAIS
    l'appel réseau Unibet) et déclenche un rafraîchissement en tâche de fond quand le cache a dépassé le
    TTL. Seul le TOUT PREMIER chargement (cache encore vide, ex. juste après un reboot/reload) attend le
    fetch. Fix lenteur 2026-07-15 : le rendu awaitait 3 listView séquentiels (foot ~5-6 s) -> pages à
    5-12 s dès que le cache 25 s expirait. Aucun impact ROI/stats/calibration (pure couche affichage)."""
    now = _time.time()
    hit = _ODDS_CACHE.get(sport)
    if hit and now - hit[0] < _ODDS_TTL:
        return hit[1]                               # frais -> tel quel
    if hit is not None:                             # périmé mais connu -> stale immédiat + refresh de fond
        if sport not in _ODDS_REFRESHING:
            _ODDS_REFRESHING.add(sport)

            async def _bg() -> None:
                try:
                    await _fetch_live_odds_now(sport)   # son PROPRE client (client=None) -> jamais un client fermé
                finally:
                    _ODDS_REFRESHING.discard(sport)

            asyncio.create_task(_bg())
        return hit[1]                               # ancien tout de suite (0 attente réseau)
    return await _fetch_live_odds_now(sport, client)   # tout premier chargement (cache vide) seulement


async def _fetch_live_odds_now(sport: str, client=None) -> dict:
    """Vrai appel listView Unibet qui (re)remplit _ODDS_CACHE / _LIVE_STATE_CACHE / _META_CACHE. Bloquant
    côté réseau (httpx async). Appelé soit au premier chargement, soit en tâche de fond (cf. fetch_live_odds)."""
    now = _time.time()
    hit = _ODDS_CACHE.get(sport)
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


def live_win_odds(sport: str, home: str, away: str) -> tuple | None:
    """Cotes vainqueur live (o1,ox,o2) du match depuis le cache `_ODDS_CACHE` rempli par
    `fetch_live_odds` (AUCUN appel réseau). Alimente la barre « % live » (proba dé-margée). None si absent."""
    hit = _ODDS_CACHE.get(sport)
    return hit[1].get(_okey(home, away)) if hit else None


def live_minute(ld: dict | None) -> int | None:
    """Minute écoulée (foot) depuis le `matchClock` d'un `liveData` Unibet, ou None. Sert au repli
    modèle de la barre « % live » (temps restant)."""
    mc = (ld or {}).get("matchClock") if isinstance(ld, dict) else None
    m = mc.get("minute") if isinstance(mc, dict) else None
    return m if isinstance(m, int) else None


def basket_frac(ld: dict | None, comp: str = "") -> float | None:
    """Fraction de match RÉGLEMENTAIRE écoulée (0,1] au basket, depuis le `matchClock` Unibet (quart +
    temps restant). `comp` sert à choisir la durée d'un quart : WNBA = 10 min, sinon 12 (NBA). None si
    l'horloge est absente/illisible. Alimente le modèle de direct basket de la barre « Chance live »."""
    mc = (ld or {}).get("matchClock") if isinstance(ld, dict) else None
    if not isinstance(mc, dict):
        return None
    pid = (mc.get("periodId") or "").upper()
    if "OVERTIME" in pid or "OT" in pid:                   # prolongation -> quasiment fini (réglementaire)
        return 0.98
    digits = "".join(ch for ch in pid if ch.isdigit())
    q = int(digits) if digits else None
    ml, sl = mc.get("minutesLeftInPeriod"), mc.get("secondsLeftInMinute")
    if q is None or ml is None:
        return None
    if q > 4:
        return 0.98
    qlen = 10 if "WNBA" in (comp or "").upper() else 12
    t_left = ml + (sl or 0) / 60.0
    elapsed = (q - 1) * qlen + (qlen - t_left)
    return max(0.02, min(0.999, elapsed / (qlen * 4)))


_SOFA_LIVE_CACHE: dict = {}   # sofa_id -> (ts, fields) : score live SofaScore (repli quand Unibet manque)
_SOFA_LIVE_TTL = 30           # s : repli SofaScore caché 30 s (best-effort, petit endpoint event/{id})
_SOFA_LIVE_REFRESHING: set = set()   # sofa_id avec une tentative SofaScore DÉJÀ en vol (anti-doublon)


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
    endpoint, PAS du bulk). Best-effort, caché 30 s par match. {} si indispo / non en cours.

    NE BLOQUE JAMAIS LE RENDU : SofaScore est MORT (cf. mémoire build-sofascore-dead) et cet appel est
    PAR MATCH -> une connexion qui traîne sur le challenge Cloudflare (curl_cffi timeout 20 s) gelait la
    page de plusieurs secondes, par match tennis live sans score Unibet, pour finir sur {}. On sert donc
    le dernier connu ({} si jamais vu) IMMÉDIATEMENT et on (re)tente en tâche de fond : si la source
    ressuscitait, le prochain rendu en profiterait. Fix lenteur 2026-07-15 (pic ~8 s résiduel)."""
    if not sofa_id:
        return {}
    sid = str(sofa_id)
    if not (sid.isdigit() and len(sid) <= 8):
        return {}
    now = _time.time()
    hit = _SOFA_LIVE_CACHE.get(sid)
    if hit and now - hit[0] < _SOFA_LIVE_TTL:
        return hit[1]
    if sid not in _SOFA_LIVE_REFRESHING:            # une seule tentative de fond en vol par match
        _SOFA_LIVE_REFRESHING.add(sid)

        async def _bg() -> None:
            try:
                await _fetch_sofa_live_now(sport, sid)
            finally:
                _SOFA_LIVE_REFRESHING.discard(sid)

        asyncio.create_task(_bg())
    return hit[1] if hit else {}                    # dernier connu tout de suite (0 attente réseau)


_LS_LIVE_CACHE: dict = {}          # (sport, home, away) -> (ts, {score, live_time})
_LS_LIVE_TTL = 20                   # 20 s : score live frais sans marteler LiveScore


def livescore_live_fields(sport: str, home: str, away: str, start: str | None = None) -> dict:
    """REPLI LiveScore (SofaScore étant MORT) quand Unibet ne remonte PAS le score live d'un match EN COURS
    — ex. match démarré EN RETARD dont Unibet n'a pas encore le feed (cas Espagne-Argentine CdM 2026-07-19 :
    live 80' 0-0 mais invisible -> combiné disparu). Renvoie {score, live_time} prêts pour le scoreboard, ou
    {} si LiveScore n'a pas ce match EN COURS. LiveScore = notre source de scores live (cf. carte des
    sources). find_id gère le matching des noms FR->EN. Caché 20 s/match (bloquant réseau borné)."""
    key = (sport, home, away)
    now = _time.time()
    hit = _LS_LIVE_CACHE.get(key)
    if hit and now - hit[0] < _LS_LIVE_TTL:
        return hit[1]
    res: dict = {}
    try:
        from app import livescore
        eid = livescore.find_id(home or "", away or "", start, sport)
        if eid:
            sb = livescore.scoreboard(sport, eid)
            if (sb and not sb.get("finished")
                    and sb.get("home_score") is not None and sb.get("away_score") is not None):
                res = {"score": f"{sb['home_score']}-{sb['away_score']}",
                       "live_time": str(sb.get("status") or "")}
    except Exception:
        res = {}
    _LS_LIVE_CACHE[key] = (now, res)
    return res


async def _fetch_sofa_live_now(sport: str, sid: str) -> dict:
    """Vrai appel SofaScore event/{id} (remplit _SOFA_LIVE_CACHE). Bloquant côté réseau -> UNIQUEMENT en
    tâche de fond (cf. fetch_sofa_live). Best-effort : {} si la source ne répond pas / match non en cours."""
    now = _time.time()
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
                          within_hours: int | None = None, always=None) -> list:
    """Récupère le listView Unibet du sport et renvoie le top N matchs importants à venir dans
    `within_hours` (cf. rank_important). `always(comp)` force l'inclusion des gros tournois."""
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
    return rank_important(events, top_n, within_hours, always=always)
