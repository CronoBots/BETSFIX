"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

import asyncio
import html
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app import ace_markets, elo, flags, serve_return, set_markets, tendencies, tracking, web
from app.analysis import build_analysis, prob_from_rankings, remove_vig
from app.analysis import _match_winner_odds
from app.markets import (
    DEFAULT_SERVE, calibrate_to_market, evaluate_markets, extract_market_anchors,
    serve_win_pct,
)
from app.providers.unibet import _norm_name
from app.textutil import name_tokens, names_match
from app.dependencies import (
    get_livescore, get_provider, get_rankings, get_unibet, matches_with_fallback,
)
from app.routers.analysis import _gather_context
from app.providers.rankings import RankingsProvider
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(tags=["🖥️ Interface (pages HTML)"])

HORIZON_HOURS = 48


def _ts(iso: str | None) -> float | None:
    """Heure de début (epoch s) depuis l'ISO du store, pour le badge décompte."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_upcoming(rec: dict, now: datetime | None = None) -> bool:
    """Vrai si le match n'a pas encore commencé (donc encore 'pariable').

    Un pari sur un match déjà commencé n'est plus une 'valeur du jour' : la fenêtre
    est fermée et le résultat n'attend que d'être réglé. On le masque tout de suite,
    sans attendre la passe de règlement (boucle 3 h + cache stale-while-revalidate),
    sinon un match terminé reste affiché comme un pari à jouer pendant des heures."""
    st = rec.get("start_time")
    if not st:
        return True  # heure inconnue -> on n'exclut pas
    try:
        dt = datetime.fromisoformat(st)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt > (now or datetime.now(timezone.utc))


def _two_way_prob(rec: dict, v: dict) -> tuple[float | None, str | None]:
    """Proba modèle du côté parié + côté (home/away) pour tennis/basket (2 issues)."""
    mh = rec.get("model_home_prob")
    side = v.get("side")
    if mh is None or side not in ("home", "away"):
        return None, side
    return (mh if side == "home" else 1 - mh), side


def _foot_prob(rec: dict, v: dict) -> tuple[float | None, str | None]:
    """Proba modèle du côté parié + côté (home/draw/away) pour le foot (1-X-2)."""
    code = v.get("code")
    p = {"1": rec.get("p_home"), "X": rec.get("p_draw"), "2": rec.get("p_away")}.get(code)
    side = {"1": "home", "X": "draw", "2": "away"}.get(code)
    return p, side


def _all_sport_picks() -> list[dict]:
    """Value 'à venir' des 3 sports, normalisées et classées par edge (pour l'accueil)."""
    from app import basket, foot
    out = []

    def add(store, sport, icon, url_fn, bet_key, prob_fn):
        for rec in store.values():
            v = rec.get("value_pick")
            if rec.get("result") or not v or not _is_upcoming(rec):
                continue
            odds = v.get("odds")
            model_p, side = prob_fn(rec, v)
            # vote "public" persisté (si capté lors d'un snapshot) -> part du côté parié
            ph, pa = rec.get("public_home"), rec.get("public_away")
            community = ((ph if side == "home" else pa) / 100
                         if ph is not None and side in ("home", "away") else None)
            nh = (rec.get("home", "").split() or [""])[-1]
            na = (rec.get("away", "").split() or [""])[-1]
            out.append({
                "sport": sport, "icon": icon, "home": rec.get("home", ""),
                "away": rec.get("away", ""), "bet": v.get(bet_key) or v.get("player") or "",
                "odds": odds, "edge": v.get("edge"),
                "model_prob": model_p, "side": side, "community": community,
                "implied": (1 / odds) if odds else None,   # proba "officielle" (cote)
                "odds_cells": [(nh, rec.get("unibet_home_odds")), (na, rec.get("unibet_away_odds"))],
                "match_id": rec.get("match_id"),
                "time": web.fmt_local(rec.get("start_time"), with_date=True),
                "start_ts": _ts(rec.get("start_time")),
                "url": url_fn(rec),
            })

    # Tennis depuis le store (même source que l'onglet Tennis). Basket/foot viennent des
    # boards (board_resilient), agrégés à part dans home() -> cohérence accueil <-> onglets.
    add(tracking.load(), "Tennis", "🎾",
        lambda r: f'/app/match/{r["match_id"]}?tour={r.get("tour", "atp")}', "player", _two_way_prob)
    out.sort(key=lambda p: p.get("start_ts") or float("inf"))   # du plus proche au plus éloigné
    return out


def _fav_two_way(rec: dict):
    """Favori du modèle (tennis/basket) : (nom, proba, côté, cote)."""
    mh = rec.get("model_home_prob")
    if mh is None:
        return None
    if mh >= 0.5:
        return rec.get("home", ""), mh, "home", rec.get("unibet_home_odds")
    return rec.get("away", ""), 1 - mh, "away", rec.get("unibet_away_odds")


def _fav_foot(rec: dict):
    """Favori du modèle (foot, 1-X-2) : (nom, proba, côté, cote)."""
    ps = [rec.get("p_home"), rec.get("p_draw"), rec.get("p_away")]
    if any(p is None for p in ps):
        return None
    i = max(range(3), key=lambda k: ps[k])
    name = [rec.get("home", ""), "Match nul", rec.get("away", "")][i]
    odds = [rec.get("o1"), rec.get("ox"), rec.get("o2")][i]
    return name, ps[i], ["home", "draw", "away"][i], odds


CONF_MIN_PROB = 0.65   # "confiance" = favori NET (sinon ce n'est pas une vraie confiance)


def _confidence_picks() -> list[dict]:
    """Matchs où le modèle est le plus SÛR du résultat (favori net), tous sports.

    Différent des 'valeurs' : ici on cherche la proba la plus haute (favori), pas
    l'écart avec la cote. Souvent des favoris -> pas forcément une value."""
    from app import basket, foot
    out = []

    def add(store, sport, icon, url_fn, fav_fn):
        for rec in store.values():
            if rec.get("result") or not _is_upcoming(rec):
                continue
            fav = fav_fn(rec)
            if not fav or fav[1] is None or fav[1] < CONF_MIN_PROB:
                continue
            name, prob, side, odds = fav
            ph, pa = rec.get("public_home"), rec.get("public_away")
            community = ((ph if side == "home" else pa) / 100
                         if ph is not None and side in ("home", "away") else None)
            nh = (rec.get("home", "").split() or [""])[-1]
            na = (rec.get("away", "").split() or [""])[-1]
            out.append({
                "sport": sport, "icon": icon, "home": rec.get("home", ""),
                "away": rec.get("away", ""), "bet": name, "model_prob": prob, "side": side,
                "conf_pct": round(prob * 100), "odds": odds,
                "implied": (1 / odds) if odds else None, "community": community,
                "odds_cells": [(nh, rec.get("unibet_home_odds")), (na, rec.get("unibet_away_odds"))],
                "match_id": rec.get("match_id"),
                "time": web.fmt_local(rec.get("start_time"), with_date=True),
                "start_ts": _ts(rec.get("start_time")),
                "url": url_fn(rec),
            })

    # Tennis seul (basket/foot viennent des boards, agrégés dans home()).
    add(tracking.load(), "Tennis", "🎾",
        lambda r: f'/app/match/{r["match_id"]}?tour={r.get("tour", "atp")}', _fav_two_way)
    out.sort(key=lambda p: p.get("start_ts") or float("inf"))   # du plus proche au plus éloigné
    return out


def _enrich_picks_votes(picks: list[dict], provider) -> None:
    """Ajoute la proba 'communauté' (votes fans) depuis le cache UNIQUEMENT — aucun appel
    réseau, donc aucun risque de rafale 403 au rendu de la page. Les votes sont peuplés en
    fond par la boucle de suivi ; ici on ne fait que les lire s'ils sont déjà là."""
    for p in picks:
        if not (p.get("match_id") and p.get("side") in ("home", "away")):
            continue
        v = provider.get_votes_cached(p["match_id"])
        if v and v.home_percent is not None:
            p["community"] = (v.home_percent if p["side"] == "home" else v.away_percent) / 100


def _board_picks(rows: list[dict], sport: str, icon: str, url: str,
                 ndim: int) -> tuple[list[dict], list[dict]]:
    """(values, confiances) façon accueil depuis les rows d'une board (basket 2 issues /
    foot 3 issues). Garantit que l'accueil montre EXACTEMENT les picks de l'onglet."""
    values, confs = [], []
    for r in rows:
        vt = r.get("votes")

        def _comm(side):   # vote communauté du côté donné (home/away ou 1/2)
            if not vt or vt[0] is None:
                return None
            return vt[0] / 100 if side in ("home", "1") else (vt[1] / 100 if side in ("away", "2") else None)

        if ndim == 3:      # foot 1-X-2
            probs = r.get("probs")
            if not probs:
                continue
            imp = r.get("imp")
            names, codes = [r["home"], "Match nul", r["away"]], ["1", "X", "2"]
            fav_i = max(range(3), key=lambda k: probs[k])
            fav = (names[fav_i], probs[fav_i], codes[fav_i], imp[fav_i] if imp else None,
                   [r.get("o1"), r.get("ox"), r.get("o2")][fav_i])
            pk = r.get("pick")
            pk_data = None
            if pk:
                i = codes.index(pk["code"])
                pk_data = (pk["team"], pk["odds"], pk["edge"], probs[i], pk["code"], imp[i] if imp else None)
        else:              # basket 2 issues
            p = r.get("model_home")
            if p is None:
                continue
            hf = p >= 0.5
            imph = r.get("imp_home")
            fav = (r["home"] if hf else r["away"], p if hf else 1 - p, "home" if hf else "away",
                   (imph if hf else 1 - imph) if imph is not None else None,
                   r.get("oh") if hf else r.get("oa"))
            pk = r.get("pick")
            pk_data = None
            if pk:
                mp = p if pk["side"] == "home" else 1 - p
                pimp = (imph if pk["side"] == "home" else (1 - imph if imph is not None else None))
                pk_data = (pk["team"], pk["odds"], pk["edge"], mp, pk["side"], pimp)

        start = r.get("start")
        iso = datetime.fromtimestamp(start, tz=timezone.utc).isoformat() if start else None
        # cotes de TOUTES les issues (pour la barre claire) : 1-X-2 au foot, 2 issues sinon
        if ndim == 3:
            odds_cells = [(r["home"], r.get("o1")), ("Nul", r.get("ox")), (r["away"], r.get("o2"))]
        else:
            odds_cells = [(r["home"], r.get("oh")), (r["away"], r.get("oa"))]
        # drapeaux uniquement pour le foot (sélections nationales) ; basket = clubs -> pas de drapeau
        hflag = flags.flag(r["home"]) if ndim == 3 else ""
        aflag = flags.flag(r["away"]) if ndim == 3 else ""
        base = {"sport": sport, "icon": icon, "home": r["home"], "away": r["away"],
                "match_id": r.get("id"), "url": url, "female": r.get("female"),
                "live": r.get("status") == "inprogress", "odds_cells": odds_cells,
                "home_flag": hflag, "away_flag": aflag,
                "time": web.fmt_local(iso, with_date=True), "start_ts": start}
        if pk_data:
            team, odds, edge, mp, side, pimp = pk_data
            values.append({**base, "bet": team, "odds": odds, "edge": edge, "model_prob": mp,
                           "side": side, "implied": pimp, "community": _comm(side)})
        name, prob, side, implied, odds = fav
        if prob >= CONF_MIN_PROB:
            confs.append({**base, "bet": name, "model_prob": prob, "side": side,
                          "conf_pct": round(prob * 100), "odds": odds,
                          "implied": implied, "community": _comm(side)})
    return values, confs


async def _live_board_picks() -> tuple[list[dict], list[dict]]:
    """Picks basket + foot pour l'accueil, depuis les MÊMES boards que les onglets."""
    from app import basket, foot   # import local (cycle web <-> basket/foot)
    values, confs = [], []
    try:
        brows = await asyncio.wait_for(basket.board_resilient(), timeout=2.5)
        bv, bc = _board_picks(brows, "Basket", "🏀", "/basket", 2)
        values += bv
        confs += bc
    except (Exception, asyncio.TimeoutError):
        pass
    try:
        frows = await asyncio.wait_for(foot.board_resilient(), timeout=2.5)
        fv, fc = _board_picks(frows, "Foot", "⚽", "/foot", 3)
        values += fv
        confs += fc
    except (Exception, asyncio.TimeoutError):
        pass
    return values, confs


@router.get("/", response_class=HTMLResponse)
async def home(provider: SofaScoreProvider = Depends(get_provider),
               frag: int = 0) -> HTMLResponse:
    values = _all_sport_picks()              # tennis (store) — même source que l'onglet
    confidences = _confidence_picks()        # tennis (store)
    bv, bc = await _live_board_picks()       # basket + foot : MÊMES boards que les onglets
    inf = float("inf")
    # On ne montre PAS un pari du jour déjà commencé (live ou heure passée) : le pari
    # d'avant-match n'est plus jouable.
    now_ts = datetime.now(timezone.utc).timestamp()

    def _not_started(p: dict) -> bool:
        if p.get("live"):
            return False
        st = p.get("start_ts")
        return st is None or st > now_ts

    values = sorted([p for p in values + bv if _not_started(p)],
                    key=lambda p: p.get("start_ts") or inf)[:8]
    confidences = sorted([p for p in confidences + bc if _not_started(p)],
                         key=lambda p: p.get("start_ts") or inf)[:6]
    _enrich_picks_votes(values + confidences, provider)   # votes communauté (cache only)
    return HTMLResponse(web.render_home(
        tracking.report(tracking.load()), source=provider.breaker_status(),
        picks=values, conf_picks=confidences, frag=bool(frag)))


def _tennis_live_score(entry: dict, swapped: bool = False) -> str:
    """Score live (sets) d'un match tennis depuis le liveData Unibet -> « 2-6 6-4 0-0 ».
    Orienté selon le sens du match (home/away éventuellement inversés)."""
    sets = (((entry.get("liveData") or {}).get("statistics") or {}).get("sets") or {})
    sh, sa = sets.get("home") or [], sets.get("away") or []
    if swapped:
        sh, sa = sa, sh
    pairs = list(zip(sh, sa))
    if not pairs:
        return ""
    last = max((i for i, (h, a) in enumerate(pairs) if h or a), default=-1)
    show = pairs[:last + 2] if last >= 0 else pairs[:1]   # sets joués + le set en cours
    return " ".join(f"{h}-{a}" for h, a in show)


def _two_way_odds(entry: dict) -> tuple[float | None, float | None]:
    """Cotes décimales (home, away) du marché vainqueur d'un événement Unibet 2 issues."""
    for bo in entry.get("betOffers") or []:
        outs = bo.get("outcomes") or []
        if len(outs) == 2:
            def dec(o):
                v = o.get("odds")
                return round(v / 1000, 2) if isinstance(v, (int, float)) else None
            return dec(outs[0]), dec(outs[1])
    return None, None


async def _tennis_unibet_rows(unibet, store: dict, now, horizon) -> tuple[list, list]:
    """Liste tennis PILOTÉE PAR UNIBET (temps réel), analyse depuis le store (modèle complet
    SofaScore) matchée par nom + date. On ne montre que les matchs suivis (analyse dispo) :
    l'id SofaScore du store sert au détail et à l'enrichissement."""
    try:
        events = await unibet._events("tennis")
    except Exception:
        return [], []
    idx = []   # (home_tokens, away_tokens, date, rec) des matchs suivis non réglés
    for rec in store.values():
        if rec.get("result"):
            continue
        st = rec.get("start_time")
        try:
            d = datetime.fromisoformat(st).date() if st else None
        except ValueError:
            d = None
        idx.append((name_tokens(rec.get("home", "")), name_tokens(rec.get("away", "")), d, rec))
    rows, live, seen = [], [], set()
    for entry in events or []:
        ev = entry.get("event") or {}
        h, a = ev.get("homeName", ""), ev.get("awayName", "")
        try:
            start = datetime.fromisoformat(str(ev.get("start")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, TypeError):
            start = None
        if start is None or start > horizon:
            continue
        rh, ra, rd = name_tokens(h), name_tokens(a), start.date()
        rec, swapped = None, False
        for sht, sat, sd, srec in idx:
            if sd is not None and sd != rd:
                continue
            if names_match(rh, sht) and names_match(ra, sat):
                rec = srec
                break
            if names_match(rh, sat) and names_match(ra, sht):
                rec, swapped = srec, True   # Unibet home == joueur 'away' du store
                break
        if rec is None:                 # match Unibet non suivi -> pas d'analyse -> on saute
            continue
        mid = rec["match_id"]
        if mid in seen:
            continue
        seen.add(mid)
        hp = rec.get("model_home_prob")
        if hp is None:
            fav = favp = None
        elif hp >= 0.5:
            fav, favp = rec.get("home"), f"{round(hp * 100)}%"
        else:
            fav, favp = rec.get("away"), f"{round((1 - hp) * 100)}%"
        # Cotes : on prend celles du STORE (clôture pré-match, utilisées par le modèle) ;
        # à défaut (ex. live jamais snapshoté), on lit celles de l'événement Unibet courant
        # -> plus de « cotes Unibet à venir » alors qu'Unibet les affiche.
        uh, ua = _two_way_odds(entry)
        ev_oh, ev_oa = (ua, uh) if swapped else (uh, ua)
        oh = rec.get("unibet_home_odds") or ev_oh
        oa = rec.get("unibet_away_odds") or ev_oa
        devig = remove_vig(oh, oa)
        local_dt = web.to_local(start)
        is_live = start <= now
        row = {
            "id": mid, "tour": rec.get("tour", "atp"),
            "home": rec.get("home", ""), "away": rec.get("away", ""),
            "status": "inprogress" if is_live else "notstarted",
            "time": web.fmt_local(start.isoformat(), with_date=True),
            "score": _tennis_live_score(entry, swapped) if is_live else "",
            "fav": fav, "favp": favp, "confidence": rec.get("confidence"),
            "hp": hp, "implied": devig[0] if devig else None,
            "oh": oh, "oa": oa,
            "votes": ((rec.get("public_home"), rec.get("public_away"))
                      if rec.get("public_home") is not None else None),
            "start_ts": start.timestamp(),
            "_sort": local_dt or datetime.max.replace(tzinfo=timezone.utc),
        }
        (live if is_live else rows).append(row)   # en direct -> section « En direct »
    return rows, live


@router.get("/app", response_class=HTMLResponse)
async def matches_page(
    provider: SofaScoreProvider = Depends(get_provider),
    rankings: RankingsProvider = Depends(get_rankings),
    unibet: UnibetProvider = Depends(get_unibet),
    frag: int = 0,
) -> HTMLResponse:
    """Liste des matchs à venir (ATP+WTA). Source : Unibet (temps réel) + analyse du store
    (modèle complet SofaScore) ; repli SofaScore/LiveScore si Unibet ne donne rien."""
    store = tracking.load()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HORIZON_HOURS)
    local_now = web.to_local(now) or now
    today = local_now.date()
    fallback = False
    # Source PRIMAIRE : Unibet (temps réel) + analyse du store. Si rien (Unibet K.O. ou
    # aucun match suivi), on bascule sur l'ancien chemin SofaScore/LiveScore ci-dessous.
    try:
        rows, live = await asyncio.wait_for(
            _tennis_unibet_rows(unibet, store, now, horizon), timeout=3.0)
    except (Exception, asyncio.TimeoutError):
        rows, live = [], []
    for tour in ([] if (rows or live) else ("atp", "wta")):
        # Budget réseau borné : si la source traîne, on n'attend pas (le repli store
        # plus bas prend le relais) -> page rapide même quand SofaScore est lent.
        try:
            matches, src = await asyncio.wait_for(matches_with_fallback(tour), timeout=3.0)
        except (Exception, asyncio.TimeoutError):
            matches, src = [], "none"
        if src == "livescore":
            fallback = True
        for m in matches:
            if m.status not in ("notstarted", "inprogress"):
                continue
            if m.status == "notstarted" and m.start_time and m.start_time > horizon:
                continue
            rec = store.get(str(m.id), {})
            hp = rec.get("model_home_prob")
            if hp is None and m.home.ranking and m.away.ranking:
                hp = prob_from_rankings(m.home.ranking, m.away.ranking)
            # Repli classements officiels (par nom) : appels réseau LENTS -> uniquement
            # quand SofaScore est bloqué (sinon les rangs de l'événement suffisent).
            if hp is None and fallback:
                rh = await rankings.rank(tour, m.home.name)
                ra = await rankings.rank(tour, m.away.name)
                hp = prob_from_rankings(rh, ra)
            if hp is None:
                fav = favp = None
            elif hp >= 0.5:
                fav, favp = m.home.name, f"{round(hp*100)}%"
            else:
                fav, favp = m.away.name, f"{round((1-hp)*100)}%"
            local_dt = web.to_local(m.start_time)
            devig = remove_vig(rec.get("unibet_home_odds"), rec.get("unibet_away_odds"))
            votes = ((rec.get("public_home"), rec.get("public_away"))
                     if rec.get("public_home") is not None else None)
            row = {
                "id": m.id, "tour": tour, "home": m.home.name, "away": m.away.name,
                "status": m.status,
                "time": web.fmt_local(m.start_time, with_date=True),
                "score": web.fmt_score(m.home_score, m.away_score) if m.status == "inprogress" else "",
                "fav": fav, "favp": favp, "confidence": rec.get("confidence"),
                "hp": hp, "implied": devig[0] if devig else None, "votes": votes,
                "oh": rec.get("unibet_home_odds"), "oa": rec.get("unibet_away_odds"),
                "start_ts": m.start_time.timestamp() if m.start_time else None,
                "female": tour == "wta", "clickable": True,
                "_date": local_dt.date() if local_dt else None,
                "_sort": local_dt or datetime.max.replace(tzinfo=timezone.utc),
            }
            (live if m.status == "inprogress" else rows).append(row)

    # Repli : SofaScore en pause ET LiveScore indisponible (aucun match live) -> on montre
    # les matchs à venir DÉJÀ suivis (store), pour ne pas afficher un onglet vide alors que
    # ces matchs apparaissent dans les picks de l'accueil.
    if not rows and not live:
        for rec in store.values():
            if rec.get("result") or rec.get("value_pick"):   # value -> section dédiée
                continue
            st = rec.get("start_time")
            try:
                dt = datetime.fromisoformat(st) if st else None
            except ValueError:
                dt = None
            if dt is None or dt < now or dt > horizon:
                continue
            hp = rec.get("model_home_prob")
            if hp is None:
                fav = favp = None
            elif hp >= 0.5:
                fav, favp = rec.get("home"), f"{round(hp * 100)}%"
            else:
                fav, favp = rec.get("away"), f"{round((1 - hp) * 100)}%"
            devig = remove_vig(rec.get("unibet_home_odds"), rec.get("unibet_away_odds"))
            rows.append({
                "id": rec.get("match_id"), "tour": rec.get("tour", "atp"),
                "home": rec.get("home", ""), "away": rec.get("away", ""), "status": "notstarted",
                "time": web.fmt_local(st, with_date=True), "score": "",
                "fav": fav, "favp": favp, "confidence": rec.get("confidence"), "hp": hp,
                "implied": devig[0] if devig else None,
                "oh": rec.get("unibet_home_odds"), "oa": rec.get("unibet_away_odds"),
                "votes": ((rec.get("public_home"), rec.get("public_away"))
                          if rec.get("public_home") is not None else None),
                "start_ts": dt.timestamp(),
                "female": rec.get("tour") == "wta",
                "_sort": web.to_local(dt) or datetime.max.replace(tzinfo=timezone.utc),
            })

    live.sort(key=lambda r: r["_sort"])
    rows.sort(key=lambda r: r["_sort"])
    ev = html.escape

    def _fav_sub(r):
        # noms de famille (le matchup complet est déjà au-dessus) pour des cellules compactes ;
        # on surligne l'issue pronostiquée par BETSFIX (cohérent avec la barre), pas le favori book.
        nh = (r["home"].split() or [r["home"]])[-1]
        na = (r["away"].split() or [r["away"]])[-1]
        hp = r.get("hp")
        hi = (0 if hp >= 0.5 else 1) if hp is not None else None
        return web.odds_row([(nh, r.get("oh")), (na, r.get("oa"))], highlight_idx=hi)

    def _trow(r, sub, badge="", pick=False):
        labels = ((r["home"].split() or [""])[-1], (r["away"].split() or [""])[-1])
        return {"tour": r["tour"].upper(), "status": r["status"], "time": r.get("time") or "",
                "score": r.get("score") or "", "home": r["home"], "away": r["away"],
                "prob": r.get("hp"), "prob_labels": labels,
                "sub": sub, "badge": badge, "pick": pick,
                "start_ts": r.get("start_ts"), "female": r.get("female"),
                "url": f'/app/match/{r["id"]}?tour={r["tour"]}',
                **web.bars_two_way(r.get("hp"), r.get("implied"), r.get("votes"),
                                   r["home"], r["away"])}

    upcoming_rows = [_trow(r, _fav_sub(r)) for r in rows]
    live_rows = [_trow(r, _fav_sub(r)) for r in live]

    value_picks, finished = _picks_and_finished(store)
    value_rows = [{
        "tour": v["tour"].upper(), "status": "notstarted", "time": v.get("time") or "",
        "home": v["home"], "away": v["away"], "pick": True, "start_ts": v.get("start_ts"),
        "female": v.get("tour") == "wta",
        "badge": f'<span class="badge b-val">+{round((v.get("edge") or 0)*100,1)} pts</span>',
        "sub": (web.odds_row(v.get("odds_cells") or [],
                             highlight_idx={"home": 0, "away": 1}.get(v.get("side")))
                + f'<div class="dim">pari : <b class="pos">{ev(v.get("player") or "")}</b> '
                f'@{v.get("odds") or "—"} · mise '
                f'{v.get("stake") if v.get("stake") is not None else "—"}%</div>'),
        # 3 barres du côté parié (comme l'accueil)
        "model_prob": v.get("model_prob"), "implied": v.get("implied"),
        "community": v.get("community"), "bet": v.get("player"),
        "url": f'/app/match/{v["id"]}?tour={v["tour"]}'} for v in value_picks]
    finished_rows = [{
        "tour": f["tour"].upper(), "status": "finished", "score": f.get("score") or "terminé",
        "home": f["home"], "away": f["away"],
        "badge": ('<span class="pos">✓ modèle ok</span>' if f.get("ok")
                  else '<span class="neg">✗ raté</span>'),
        "sub": (f'<div class="dim">favori : {ev(f.get("fav") or "—")} {ev(f.get("favp") or "")} '
                f'· vainqueur : <b>{ev(f.get("winner_name") or "")}</b></div>'),
        "url": f'/app/match/{f["id"]}?tour={f["tour"]}'} for f in finished]

    intro = ('⚠️ SofaScore momentanément indisponible — scores via LiveScore (repli).'
             if fallback else
             'Touchez un match pour son analyse détaillée. Heures en fuseau belge.')
    return HTMLResponse(web.render_sport_matches(
        "tennis", "Matchs", value_rows, live_rows, upcoming_rows, finished_rows,
        intro=intro, frag=bool(frag)))


def _picks_and_finished(store: dict) -> tuple[list[dict], list[dict]]:
    """Extrait du suivi : paris de confiance (value non réglées) et matchs terminés."""
    value_picks, finished = [], []
    for rec in store.values():
        res = rec.get("result")
        if not res and rec.get("value_pick") and _is_upcoming(rec):
            v = rec["value_pick"]
            # 3 barres du côté PARIÉ (pas le favori) : modèle / cote dévig / public
            side = v.get("side")
            mh = rec.get("model_home_prob")
            devig = remove_vig(rec.get("unibet_home_odds"), rec.get("unibet_away_odds"))
            ph, pa = rec.get("public_home"), rec.get("public_away")
            model_prob = (mh if side == "home" else 1 - mh) if mh is not None and side in ("home", "away") else None
            implied = ((devig[0] if side == "home" else devig[1]) if devig and side in ("home", "away") else None)
            community = ((ph if side == "home" else pa) / 100
                         if ph is not None and side in ("home", "away") else None)
            nh = (rec.get("home", "").split() or [""])[-1]
            na = (rec.get("away", "").split() or [""])[-1]
            value_picks.append({
                "id": rec["match_id"], "tour": rec.get("tour", "atp"),
                "home": rec.get("home", ""), "away": rec.get("away", ""),
                "time": web.fmt_local(rec.get("start_time"), with_date=True),
                "start_ts": _ts(rec.get("start_time")),
                "player": v.get("player"), "odds": v.get("odds"),
                "edge": v.get("edge"), "stake": v.get("stake_pct"),
                "confidence": rec.get("confidence"),
                "model_prob": model_prob, "implied": implied, "community": community,
                "odds_cells": [(nh, rec.get("unibet_home_odds")), (na, rec.get("unibet_away_odds"))],
                "side": side, "_sort": rec.get("start_time") or "",
            })
        elif res and rec.get("model_home_prob") is not None:
            hp = rec["model_home_prob"]
            fav_home = hp >= 0.5
            finished.append({
                "id": rec["match_id"], "tour": rec.get("tour", "atp"),
                "home": rec.get("home", ""), "away": rec.get("away", ""),
                "fav": rec["home"] if fav_home else rec["away"],
                "favp": f"{round(max(hp, 1 - hp) * 100)}%",
                "winner_name": rec["home"] if res["winner"] == "home" else rec["away"],
                "ok": (res["winner"] == "home") == fav_home,
                "score": res.get("score"),
                "_sort": res.get("settled_at", ""),
            })
    value_picks.sort(key=lambda r: r["_sort"])
    finished.sort(key=lambda r: r["_sort"], reverse=True)
    return value_picks, finished[:8]


@router.get("/app/match/{match_id}", response_class=HTMLResponse)
async def match_detail(
    match_id: int,
    tour: str = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
    rankings: RankingsProvider = Depends(get_rankings),
) -> HTMLResponse:
    tour = "wta" if tour == "wta" else "atp"
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError:
        # SofaScore K.O. -> détail léger via LiveScore + classements officiels
        return await _light_detail(match_id, tour, unibet, rankings)

    hm, am, hs, as_, h2h, odds = await _gather_context(match, tour, provider, unibet)
    elo_home, elo_away = elo.ratings_for_match(match)
    sr_home, sr_away = serve_return.ratings_for_match(match)
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds, elo_home=elo_home, elo_away=elo_away,
        sr_home=sr_home, sr_away=sr_away,
    )
    winner_odds = _match_winner_odds(odds, match) if (odds and odds.matched) else (None, None)
    best_of = 5 if tour == "atp" else 3
    fav_prob = max(analysis.model_home_probability or 0.5, analysis.model_away_probability or 0.5)
    opp_ret_home, opp_ret_away = serve_return.return_rates_for_match(match)
    line_home, line_away = (_ace_lines(odds, match) if (odds and odds.matched) else (None, None))
    aces = tendencies.for_match(
        match, best_of, fav_prob, opp_ret_home=opp_ret_home, opp_ret_away=opp_ret_away,
        line_home=line_home, line_away=line_away)
    home_form = _recent_form(hm or [], match.home.id)
    away_form = _recent_form(am or [], match.away.id)
    h2h_rec = ({"home": h2h.home_wins, "away": h2h.away_wins} if h2h else None)
    score = (web.fmt_score(match.home_score, match.away_score)
             if match.status in ("inprogress", "finished") else "")
    votes = None
    try:   # pronostics des fans (provider caché, tolérant aux erreurs)
        v = await provider.get_votes(match_id)
        if v.home_percent is not None:
            votes = (v.home_percent, v.away_percent)
    except ProviderError:
        pass
    return HTMLResponse(web.render_match_detail(
        analysis, winner_odds, aces=aces, tour=tour,
        home_form=home_form, away_form=away_form, h2h=h2h_rec, score=score, votes=votes))


def _ace_lines(odds, match) -> tuple[float | None, float | None]:
    """Lignes Unibet 'Nombre total d'aces - <joueur>' (Plus de), par joueur."""
    home_tokens = _norm_name(match.home.name)
    lh = la = None
    for mk in odds.markets:
        label = mk.label or ""
        lab = label.lower()
        if "aces" not in lab or not ("nombre" in lab or " - " in label):
            continue
        over = next((o for o in mk.outcomes if "plus" in (o.label or "").lower()), None)
        if not over or over.line is None:
            continue
        if _norm_name(label) & home_tokens:
            lh = over.line
        else:
            la = over.line
    return lh, la


def _recent_form(matches: list, player_id: int | None, n: int = 6) -> list[dict]:
    """Derniers résultats (V/D) d'un joueur depuis son historique (récent -> ancien)."""
    if player_id is None:
        return []
    out = []
    for m in matches:
        if m.status != "finished" or m.winner not in ("home", "away"):
            continue
        if m.home.id == player_id:
            side, opp = "home", m.away
        elif m.away.id == player_id:
            side, opp = "away", m.home
        else:
            continue
        out.append({"win": m.winner == side, "opp": opp.name or ""})
        if len(out) >= n:
            break
    return out


def _vb_row(vb) -> dict:
    return {"market": "Vainqueur", "selection": vb.player, "odds": vb.odds,
            "model_p": vb.model_probability, "implied_p": vb.implied_probability,
            "edge": vb.edge, "value": vb.is_value, "line": None}


def _edge_row(me) -> dict:
    return {"market": me.market, "selection": me.selection, "odds": me.odds,
            "model_p": me.model_probability, "implied_p": me.implied_probability,
            "edge": me.edge, "value": me.is_value, "line": me.line}


@router.get("/app/match/{match_id}/paris", response_class=HTMLResponse)
async def markets_page(
    match_id: int,
    tour: str = Query("atp"),
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> HTMLResponse:
    """Outil 'Tous les paris' : modèle vs book sur tous les marchés Unibet du match."""
    tour = "wta" if tour == "wta" else "atp"
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError:
        return HTMLResponse(web.layout(
            "Tous les paris", "tennis",
            '<div class="banner">Analyse momentanément indisponible (SofaScore bloqué).</div>'
            '<a class="dim" href="/app">← Retour</a>'))

    hm, am, hs, as_, h2h, odds = await _gather_context(match, tour, provider, unibet)
    elo_home, elo_away = elo.ratings_for_match(match)
    sr_home, sr_away = serve_return.ratings_for_match(match)
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds, elo_home=elo_home, elo_away=elo_away,
        sr_home=sr_home, sr_away=sr_away,
    )
    odds_matched = bool(odds and odds.matched)
    winner_rows, ace_rows, set_rows, sim_rows = [], [], [], []
    if odds_matched:
        best_of = 5 if tour == "atp" else 3
        winner_rows = [_vb_row(vb) for vb in analysis.value_bets]

        # Sets (au moins un set / handicap ±2.5) : dérivés de la proba de vainqueur, calibrés
        set_rows = [_edge_row(me) for me in set_markets.evaluate(
            match, odds, best_of,
            analysis.model_home_probability, analysis.model_away_probability)]

        # Aces : tendances spécifiques à la surface du match
        store = tendencies.load_cached()
        fav_prob = max(analysis.model_home_probability or 0.5,
                       analysis.model_away_probability or 0.5)
        rh = tendencies.ace_rate(store.get(str(match.home.id)), match.ground_type)
        ra = tendencies.ace_rate(store.get(str(match.away.id)), match.ground_type)
        ace_rows = [_edge_row(me) for me in
                    ace_markets.evaluate(match, odds, best_of, rh, ra, fav_prob)]

        # Simulateur (jeux/sets/breaks…), calé sur le marché — comme /analysis/markets
        levels = [v for v in (serve_win_pct(hs), serve_win_pct(as_)) if v is not None]
        serve_level = sum(levels) / len(levels) if levels else DEFAULT_SERVE[tour]
        home_tokens = _norm_name(match.home.name)
        mkt_win, games_line, games_over = extract_market_anchors(odds, home_tokens)
        model_p = analysis.model_home_probability
        if mkt_win is not None and model_p is not None:
            target_win = 0.7 * mkt_win + 0.3 * model_p
        else:
            target_win = mkt_win if mkt_win is not None else (model_p or 0.5)
        sim = calibrate_to_market(target_win, games_line, games_over, serve_level,
                                  best_of, seed=match_id)
        sim_edges = sorted(evaluate_markets(match, odds, sim),
                           key=lambda e: abs(e.edge or 0), reverse=True)
        sim_rows = [_edge_row(me) for me in sim_edges[:15]]   # top 15 par |écart|

    return HTMLResponse(web.render_markets(
        match, winner_rows, ace_rows, sim_rows, odds_matched, tour=tour,
        set_rows=set_rows))


async def _light_detail(match_id, tour, unibet, rankings) -> HTMLResponse:
    """Détail réduit quand SofaScore bloque : favori par classement + cotes Unibet."""
    ls = get_livescore()
    match = None
    try:
        for m in await ls.get_matches(tour):
            if m.id == match_id:
                match = m
                break
    except Exception:
        match = None
    if match is None:
        return HTMLResponse(web.layout("Indisponible", "tennis",
                            '<div class="banner">Analyse momentanément indisponible '
                            '(SofaScore bloqué et match introuvable côté secours).</div>'
                            '<a class="dim" href="/app">← Retour</a>'))
    match.home.ranking = await rankings.rank(tour, match.home.name)
    match.away.ranking = await rankings.rank(tour, match.away.name)
    odds = await unibet.find_odds(match)
    analysis = build_analysis(match, [], [], None, None, None, None, odds)
    winner_odds = _match_winner_odds(odds, match) if (odds and odds.matched) else (None, None)
    html = web.render_match_detail(analysis, winner_odds)
    note = ('<div class="banner">⚠️ SofaScore indisponible : analyse réduite (favori '
            'par classement + cotes). Stats/forme/h2h reviendront dès le rétablissement.</div>')
    return HTMLResponse(html.replace("</h1>", "</h1>" + note, 1))
