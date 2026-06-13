"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app import analyses, ace_markets, elo, fragcache, match_analysis, match_select, mybets, serve_return, set_markets, tendencies, tracking, web, window
from app.config import get_settings
from app.analysis import build_analysis, remove_vig
from app.analysis import _match_winner_odds
from app.markets import (
    DEFAULT_SERVE, calibrate_to_market, evaluate_markets, extract_market_anchors,
    serve_win_pct, tennis_perle_live_status,
)
from app.providers.unibet import _norm_name
from app.textutil import name_tokens, names_match
from app.dependencies import (
    get_livescore, get_provider, get_rankings, get_unibet,
)
from app.routers.analysis import _gather_context
from app.providers.rankings import RankingsProvider
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(tags=["🖥️ Interface (pages HTML)"])

# Fenêtre de récupération (tennis & accueil) : logique COMMUNE aux 3 sports (cf. app/window.py).
# Cache court (s) des panneaux de liste (partagés entre tous les visiteurs) : coupe les
# rafales d'appels Unibet/SofaScore au pré-chargement SPA et au refresh 45s. < refresh ->
# un utilisateur seul récupère quand même des données fraîches à chaque rafraîchissement.
PANEL_TTL = 20


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


def _split_2way(rec: dict) -> dict:
    """Champs des barres RÉPARTIES (home/away) pour un match 2 issues (tennis) depuis le store."""
    mh = rec.get("model_home_prob")
    model = (mh, None, (1 - mh) if mh is not None else None)
    rv = remove_vig(rec.get("unibet_home_odds"), rec.get("unibet_away_odds"))
    implied = (rv[0], None, rv[1]) if rv else None
    d = web.bars_split(model, implied)
    ph, pa = rec.get("public_home"), rec.get("public_away")
    if ph is not None and pa is not None:
        d["pub_home"], d["pub_away"] = ph / 100, pa / 100
    return d


def _all_sport_picks() -> list[dict]:
    """Value 'à venir' des 3 sports, normalisées et classées par edge (pour l'accueil)."""
    out = []

    def add(store, sport, icon, url_fn, bet_key, prob_fn):
        for rec in store.values():
            if (rec.get("result") or not _is_upcoming(rec)
                    or not window.within(rec.get("start_time"))):   # rien au-delà de la fenêtre 24 h
                continue
            nh = (rec.get("home", "").split() or [""])[-1]
            na = (rec.get("away", "").split() or [""])[-1]
            base = {"sport": sport, "icon": icon, "home": rec.get("home", ""),
                    "away": rec.get("away", ""),
                    "odds_cells": [(nh, rec.get("unibet_home_odds")), (na, rec.get("unibet_away_odds"))],
                    "match_id": rec.get("match_id"),
                    "time": web.fmt_local(rec.get("start_time"), with_date=True),
                    "start_ts": _ts(rec.get("start_time")), "url": url_fn(rec), **_split_2way(rec)}
            # 💎 VALUE = la PERLE au plus gros edge (tous marchés) quand elle existe
            pv = rec.get("perle_value")
            if isinstance(pv, dict) and pv.get("selection"):
                out.append({**base, "bet": pv["selection"], "odds": pv.get("odds"),
                            "edge": pv.get("edge"), "model_prob": pv.get("model_prob"),
                            "side": None, "implied": None, "community": None, "perle": pv,
                            "pick_kind": "value"})
                continue
            v = rec.get("value_pick")
            if not v:
                continue
            odds = v.get("odds")
            model_p, side = prob_fn(rec, v)
            ph, pa = rec.get("public_home"), rec.get("public_away")
            community = ((ph if side == "home" else pa) / 100
                         if ph is not None and side in ("home", "away") else None)
            out.append({**base, "bet": v.get(bet_key) or v.get("player") or "",
                        "odds": odds, "edge": v.get("edge"), "model_prob": model_p, "side": side,
                        "community": community, "implied": (1 / odds) if odds else None})

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
    out = []

    def add(store, sport, icon, url_fn, fav_fn):
        for rec in store.values():
            if (rec.get("result") or not _is_upcoming(rec)
                    or not window.within(rec.get("start_time"))):   # rien au-delà de la fenêtre 24 h
                continue
            nh = (rec.get("home", "").split() or [""])[-1]
            na = (rec.get("away", "").split() or [""])[-1]
            base = {"sport": sport, "icon": icon, "home": rec.get("home", ""),
                    "away": rec.get("away", ""),
                    "odds_cells": [(nh, rec.get("unibet_home_odds")), (na, rec.get("unibet_away_odds"))],
                    "match_id": rec.get("match_id"),
                    "time": web.fmt_local(rec.get("start_time"), with_date=True),
                    "start_ts": _ts(rec.get("start_time")), "url": url_fn(rec), **_split_2way(rec)}
            # 🎯 CONFIANCE = la PERLE la plus probable (tous marchés) quand elle existe
            perle = rec.get("perle")
            if isinstance(perle, dict) and perle.get("selection"):
                out.append({**base, "bet": perle["selection"], "model_prob": perle.get("model_prob"),
                            "conf_pct": round((perle.get("model_prob") or 0) * 100),
                            "odds": perle.get("odds"), "side": None, "implied": None,
                            "community": None, "perle": perle, "perle2": rec.get("perle2"),
                            "pick_kind": "confiance"})
                continue
            fav = fav_fn(rec)
            if not fav or fav[1] is None or fav[1] < CONF_MIN_PROB:
                continue
            name, prob, side, odds = fav
            ph, pa = rec.get("public_home"), rec.get("public_away")
            community = ((ph if side == "home" else pa) / 100
                         if ph is not None and side in ("home", "away") else None)
            out.append({**base, "bet": name, "model_prob": prob, "side": side,
                        "conf_pct": round(prob * 100), "odds": odds,
                        "implied": (1 / odds) if odds else None, "community": community})

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
            p["pub_home"], p["pub_away"] = v.home_percent / 100, v.away_percent / 100
            if v.draw_percent is not None:          # vote du nul (foot 1X2)
                p["pub_draw"] = v.draw_percent / 100






def _cached_votes(provider, mid) -> tuple | None:
    """Votes communauté DÉJÀ en cache (sans appel réseau) -> (%home, %away, %draw). None sinon."""
    try:
        v = provider.get_votes_cached(int(mid))
        if v and v.home_percent is not None:
            return (v.home_percent, v.away_percent, v.draw_percent)
    except Exception:
        pass
    return None




def _home_stats() -> str:
    """Bloc stats accueil : bilan global (ROI/forme/série/drawdown) + perf par pari + détail par
    sport (lignes drill-down). SANS filtres (période/sport retirés) : toujours depuis le début,
    tous sports."""
    inner = web.render_stats(analyses.stats_full())
    return f'<div class="sx"><div class="sx-body">{inner}</div></div>' if inner else ""


@router.get("/stats/detail", response_class=HTMLResponse)
async def stats_detail(sport: str = "", pari: int = -1, since: str = "") -> HTMLResponse:
    """Fragment drill-down : liste des matchs réglés d'une catégorie (sport / pari / période)."""
    sp = sport if sport in ("foot", "tennis", "basket") else None
    pk = pari if pari in (0, 1, 2) else None
    days = {"7": 7, "30": 30}.get(since)
    return HTMLResponse(web.render_bet_detail(analyses.bet_detail(sp, pk, days)))


async def _home_match_rows() -> list:
    """TOUTES les rencontres analysées À VENIR / EN COURS (tous sports confondus), au format
    `_sport_row`, triées par coup d'envoi (le plus proche d'abord). Réutilise les constructeurs de
    lignes des onglets sport -> même rendu compact partout."""
    from app import foot as foot_mod, basket as basket_mod
    from app.routers import foot as foot_r, basket as basket_r
    out = []
    try:
        frows, _ffin = await foot_r._analyst_rows("foot")
        out += [foot_mod._card(r) for r in frows]
    except Exception:
        pass
    try:
        brows, _bfin = await basket_r._analyst_rows()
        out += [basket_mod._card(r) for r in brows]
    except Exception:
        pass
    try:                                                   # tennis
        live = await match_select.fetch_live_odds("tennis")
        for d in analyses.list_for("tennis"):
            st = analyses.status_of(d)
            # STATUT + HEURE pilotés par UNIBET (le sidecar peut être périmé -> faux « live »)
            lf0 = web.live_fields(match_select.live_state_for("tennis", d.get("home"), d.get("away")), "tennis")
            st, usdt = match_select.fresh_status("tennis", d.get("home"), d.get("away"), st,
                                                 bool(lf0.get("score")), start_iso=d.get("start"))
            if st not in ("notstarted", "inprogress"):
                continue
            dt = usdt or d.get("_start_dt")
            tour = (d.get("circuit") or ("WTA" if (d.get("comp") or "").upper() == "WTA" else "ATP")).lower()
            fresh = match_select.live_odds_for(live, d.get("home"), d.get("away"))
            o1, o2 = (fresh[0], fresh[2]) if fresh else (d.get("o1"), d.get("o2"))
            sel, odds = analyses.pick_parts(d.get("pick") or "")
            perle = {"selection": sel, "odds": odds} if (sel and odds and odds >= 1.10) else None
            bars = web.analyst_bars(o1, None, o2, analyses.votes_pct(d))
            r = {"id": d.get("id"), "tour": tour, "home": d.get("home", ""), "away": d.get("away", ""),
                 "status": st, "time": web.fmt_local(usdt or d.get("start"), with_date=True),
                 "score": "", "hp": None, "implied": None, "votes": None, "oh": o1, "oa": o2,
                 "start_ts": dt.timestamp() if dt else None, "female": False,
                 "perle": perle, "perle2": None, "pick_kind": "confiance"}
            if st == "inprogress":
                r.update(lf0)
                if not r.get("score"):   # REPLI SofaScore si Unibet n'a pas le live
                    r.update(await match_select.fetch_sofa_live("tennis", d.get("sofa_id") or d.get("id")) or {})
                # en cours sans score live : s'il a assez tourné -> il est en fait fini (Terminés du sport,
                # pas l'accueil) ; sinon on le GARDE (« En cours », sans scoreboard) pour qu'il reste visible.
                if not r.get("score") and analyses.likely_finished(d):
                    continue
            out.append({**_tennis_trow(r), **bars})
    except Exception:
        pass
    out.sort(key=lambda x: x.get("start_ts") or 0)         # coup d'envoi le plus proche d'abord
    return out


@router.get("/", response_class=HTMLResponse)
async def home(provider: SofaScoreProvider = Depends(get_provider),
               frag: int = 0) -> HTMLResponse:
    """Accueil : stats principales + les matchs À VENIR uniquement (format compact, tous sports
    mélangés, par ordre de passage). Les matchs EN COURS vivent dans l'onglet 🟢 Live (demande
    utilisateur 2026-06-12 : pas de doublon accueil/live, et un live qui démarre n'a parfois pas
    encore de score -> badge « LIVE » nu peu lisible). La nav passe par le menu ☰."""
    if frag:   # panneau partagé (pas de données par utilisateur) -> cache court anti-rafale
        cached = fragcache.get("panel/home")
        if cached:
            return HTMLResponse(cached)
    # Track record SILENCIEUX : on continue d'enregistrer chaque pari retenu (calibration future).
    # ACCUEIL = paris À VENIR + petit bandeau live (les stats vivent dans l'onglet 📊, 2026-06-13).
    mybets.sync_simulation()
    all_rows = await _home_match_rows()
    live_n = sum(1 for r in all_rows if r.get("status") == "inprogress")
    rows = [r for r in all_rows if r.get("status") != "inprogress"]
    body = web.render_dashboard(rows, live_count=live_n,
                                frag=bool(frag), source=provider.breaker_status())
    if frag:
        fragcache.put("panel/home", body, ttl=PANEL_TTL)
    return HTMLResponse(body)


@router.get("/paris")
async def paris_redirect() -> RedirectResponse:
    """Page « Paris à jouer » RETIRÉE (2026-06-12) : les paris retenus restent dans les analyses,
    marqués ⭐ (carte repliée + cadre déplié). Redirection douce pour les liens en cache mobile."""
    return RedirectResponse("/", status_code=308)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(frag: int = 0) -> HTMLResponse:
    """Onglet « Statistiques » (barre du bas) : bilan global + courbe multi-sports avec jalons +
    détail par sport + calibration. Sert un FRAGMENT quand frag=1 (panneau SPA)."""
    if frag:
        cached = fragcache.get("panel/stats")
        if cached:
            return HTMLResponse(cached)
    body = ('<div class="pg-h">Statistiques</div>'
            '<div class="pg-sub">Performance du système depuis le début · tous sports · ROI.</div>'
            + _home_stats()
            + web.render_calibration(analyses.calibration()))
    if frag:
        fragcache.put("panel/stats", body, ttl=PANEL_TTL)
        return HTMLResponse(body)
    return HTMLResponse(web.spa_shell("stats", "Statistiques", body))


# Page « Simulation bankroll » /mybets RETIRÉE le 2026-06-12 à la demande : le pari retenu est
# désormais marqué d'une ⭐ sur les cadres de paris ; l'enregistrement du track record continue
# en silence via mybets.sync_simulation pour la calibration. REDIRECTION douce vers l'accueil :
# les pages encore en cache côté mobile gardent des liens /mybets -> jamais de 404 brut.
@router.get("/mybets")
async def my_bets_redirect() -> RedirectResponse:
    return RedirectResponse("/", status_code=308)


def _tennis_live_score(entry: dict, swapped: bool = False) -> str:
    """Score live (sets) d'un match tennis depuis le liveData Unibet -> « 2-6 6-4 0-0 ».
    Orienté selon le sens du match (home/away éventuellement inversés)."""
    sets = (((entry.get("liveData") or {}).get("statistics") or {}).get("sets") or {})
    sh, sa = sets.get("home") or [], sets.get("away") or []
    if swapped:
        sh, sa = sa, sh
    # Unibet met -1 aux sets NON joués (ex. [1,-1,-1]) -> on ne garde que les sets réels
    # (les deux scores >= 0). Corrige l'affichage « 1-5 -1--1 -1--1 ».
    pairs = [(h, a) for h, a in zip(sh, sa)
             if isinstance(h, int) and isinstance(a, int) and h >= 0 and a >= 0]
    return " ".join(f"{h}-{a}" for h, a in pairs)


def _tennis_live_server(entry: dict, swapped: bool = False) -> str | None:
    """Qui SERT actuellement ('home'/'away'/None) depuis le liveData Unibet : champ
    `statistics.sets.homeServe` (booléen : True = home sert)."""
    sets = (((entry.get("liveData") or {}).get("statistics") or {}).get("sets") or {})
    hs = sets.get("homeServe")
    if hs is None:
        return None
    side = "home" if hs else "away"
    if swapped:
        side = "away" if side == "home" else "home"
    return side


def _tennis_live_points(entry: dict, swapped: bool = False) -> tuple[str, str] | None:
    """Points du JEU en cours (0/15/30/40/AD) depuis `liveData.score.home`/`away` -> colonne 🎾."""
    sc = (entry.get("liveData") or {}).get("score") or {}
    h, a = sc.get("home"), sc.get("away")
    if h is None and a is None:
        return None
    if swapped:
        h, a = a, h
    return (str(h if h is not None else ""), str(a if a is not None else ""))


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
        local_dt = web.to_local(start)
        is_live = start <= now
        # Cotes : celles de l'ÉVÉNEMENT Unibet courant en priorité (à jour, LIVE pendant le
        # match) ; à défaut, celles du store (clôture pré-match). Corrige les cotes figées
        # pendant les directs (ex. 1.56/2.33 affiché alors qu'Unibet est à 3.95/1.23).
        uh, ua = _two_way_odds(entry)
        ev_oh, ev_oa = (ua, uh) if swapped else (uh, ua)
        oh = ev_oh or rec.get("unibet_home_odds")
        oa = ev_oa or rec.get("unibet_away_odds")
        devig = remove_vig(oh, oa)
        row = {
            "id": mid, "tour": rec.get("tour", "atp"),
            "home": rec.get("home", ""), "away": rec.get("away", ""),
            "status": "inprogress" if is_live else "notstarted",
            "time": web.fmt_local(start.isoformat(), with_date=True),
            "score": _tennis_live_score(entry, swapped) if is_live else "",
            "server": _tennis_live_server(entry, swapped) if is_live else None,
            "game_pts": _tennis_live_points(entry, swapped) if is_live else None,
            "fav": fav, "favp": favp, "confidence": rec.get("confidence"),
            "hp": hp, "implied": devig[0] if devig else None,
            "oh": oh, "oa": oa, "perle": rec.get("perle"), "perle2": rec.get("perle2"),
            "votes": ((rec.get("public_home"), rec.get("public_away"))
                      if rec.get("public_home") is not None else None),
            "start_ts": start.timestamp(),
            "_sort": local_dt or datetime.max.replace(tzinfo=timezone.utc),
        }
        (live if is_live else rows).append(row)   # en direct -> section « En direct »
    return rows, live


def _tennis_fav_sub(r: dict) -> str:
    # Barre « Bookmakers » RETIRÉE : la barre combinée « Cotes & chances » (web._pick_bars) porte
    # désormais les cotes ET le % de chance (total 100 %). Plus de sous-ligne dédiée.
    return ""


def _tennis_trow(r: dict, sub: str | None = None, badge: str = "", pick: bool = False) -> dict:
    """Dict _sport_row d'un match tennis (réutilisé par l'onglet Tennis ET Directs)."""
    labels = ((r["home"].split() or [""])[-1], (r["away"].split() or [""])[-1])
    # 🟢/🔴 Halo gagné/perdu en LIVE (ex. « au moins un set » dès qu'un set est remporté)
    sp = sp2 = None
    if r.get("status") == "inprogress" and r.get("score"):
        sp = tennis_perle_live_status(r.get("perle"), r["score"], r["home"], r["away"])
        sp2 = tennis_perle_live_status(r.get("perle2"), r["score"], r["home"], r["away"])
    lw, lw2 = sp == "won", sp2 == "won"
    ll, ll2 = sp == "lost", sp2 == "lost"
    return {"tour": r["tour"].upper(), "sport": "Tennis", "icon": "🎾",
            "status": r["status"], "time": r.get("time") or "",
            "score": r.get("score") or "", "server": r.get("server"),
            "game_pts": r.get("game_pts"),
            "home": r["home"], "away": r["away"],
            "prob": r.get("hp"), "prob_labels": labels,
            "sub": _tennis_fav_sub(r) if sub is None else sub, "badge": badge, "pick": pick,
            "start_ts": r.get("start_ts"), "female": r.get("female"), "pick_kind": "confiance",
            "perle": r.get("perle"), "perle2": r.get("perle2"),
            "live_won": lw, "live_won2": lw2, "live_lost": ll, "live_lost2": ll2,
            "url": f'/app/match/{r["id"]}?tour={r["tour"]}',
            **web.bars_two_way(r.get("hp"), r.get("implied"), r.get("votes"), r["home"], r["away"])}


@router.get("/directs", response_class=HTMLResponse)
async def directs_page(
    unibet: UnibetProvider = Depends(get_unibet),
    frag: int = 0,
) -> HTMLResponse:
    """Tous les matchs EN DIRECT regroupés par sport (ils restent dans leur onglet)."""
    from app import basket, foot

    if frag:
        cached = fragcache.get("panel/directs")
        if cached:
            return HTMLResponse(cached)

    # Live = matchs ANALYSÉS actuellement EN COURS (statut dérivé du coup d'envoi, sidecars).
    async def _live_cards(sport: str) -> list:
        out = []
        for d in analyses.list_for(sport):
            st = analyses.status_of(d)
            # STATUT piloté par UNIBET : un coup d'envoi sidecar périmé ne doit pas faire passer le
            # match en « live » s'il n'a pas commencé côté Unibet (heure fraîche / pas de score).
            lf = web.live_fields(match_select.live_state_for(sport, d.get("home"), d.get("away")), sport)
            st, usdt = match_select.fresh_status(sport, d.get("home"), d.get("away"), st,
                                                 bool(lf.get("score")), start_iso=d.get("start"))
            if st != "inprogress":
                continue
            dt = usdt or d.get("_start_dt")
            start = dt.timestamp() if dt else None
            sid = d.get("sofa_id") or d.get("id")
            sel, odds = analyses.pick_parts(d.get("pick") or "")
            perle = {"selection": sel, "odds": odds} if (sel and odds and odds >= 1.10) else None
            if not lf.get("score"):                        # REPLI SofaScore si Unibet n'a pas le live
                lf = await match_select.fetch_sofa_live(sport, sid) or lf
            # en cours sans score live : s'il a assez tourné -> il est en fait fini (Terminés du sport),
            # sinon on le GARDE en « En cours » (sans scoreboard) pour qu'il reste visible.
            if not lf.get("score") and analyses.likely_finished(d):
                continue
            if sport == "foot":
                o1, ox, o2 = d.get("o1"), d.get("ox"), d.get("o2")
                out.append(foot._card({
                    "id": sid, "status": "inprogress", "comp": d.get("comp"),
                    "home": d.get("home", ""), "away": d.get("away", ""), "probs": None,
                    "goals": None, "o1": o1, "ox": ox, "o2": o2,
                    "imp": foot._devig3(o1, ox, o2) if (o1 and ox and o2) else None,
                    "pick": None, "start": start, "votes": analyses.votes_pct(d),
                    "perle": perle, "perle2": None, "perle_value": None,
                    "pick_kind": "confiance", "sofa_ok": True, **lf}))
            elif sport == "basket":
                oh, oa = d.get("o1"), d.get("o2")
                imp = basket._devig(oh, oa) if (oh and oa) else None
                out.append(basket._card({
                    "id": sid, "league": (d.get("comp") or "").upper(), "status": "inprogress",
                    "home": d.get("home", ""), "away": d.get("away", ""), "model_home": None,
                    "margin": None, "oh": oh, "oa": oa, "imp_home": imp[0] if imp else None,
                    "pick": None, "start": start, "votes": analyses.votes_pct(d),
                    "perle": perle, "perle2": None, "perle_value": None,
                    "pick_kind": "confiance", "sofa_ok": True, **lf}))
            else:   # tennis
                tour = (d.get("circuit") or ("WTA" if (d.get("comp") or "").upper() == "WTA" else "ATP")).lower()
                card = _tennis_trow({
                    "id": d.get("id"), "tour": tour, "home": d.get("home", ""),
                    "away": d.get("away", ""), "status": "inprogress",
                    "time": web.fmt_local(d.get("start"), with_date=True),
                    "hp": None, "implied": None, "votes": None,
                    "oh": d.get("o1"), "oa": d.get("o2"), "start_ts": start,
                    "female": False, "perle": perle, "perle2": None, "pick_kind": "confiance", **lf})
                card.update(web.analyst_bars(d.get("o1"), None, d.get("o2"), analyses.votes_pct(d)))
                out.append(card)
        return out

    for _sp in ("tennis", "basket", "foot"):   # peuple le cache score/horloge live (1 listView/sport)
        await match_select.fetch_live_odds(_sp)
    sections = [("Tennis", "🎾", await _live_cards("tennis")),
                ("Basket", "🏀", await _live_cards("basket")),
                ("Foot", "⚽", await _live_cards("foot"))]
    body = web.render_directs(sections, frag=bool(frag))
    if frag:
        fragcache.put("panel/directs", body, ttl=PANEL_TTL)
    return HTMLResponse(body)


@router.get("/app", response_class=HTMLResponse)
async def matches_page(
    provider: SofaScoreProvider = Depends(get_provider),
    rankings: RankingsProvider = Depends(get_rankings),
    unibet: UnibetProvider = Depends(get_unibet),
    frag: int = 0,
) -> HTMLResponse:
    """Liste des matchs à venir (ATP+WTA). Source : Unibet (temps réel) + analyse du store
    (modèle complet SofaScore) ; repli SofaScore/LiveScore si Unibet ne donne rien."""
    if frag:
        cached = fragcache.get("panel/tennis")
        if cached:
            return HTMLResponse(cached)
    # Onglet Tennis = matchs ANALYSÉS uniquement (sidecars). Court-circuite l'ancien chemin modèle.
    # On garde les sections À venir / En cours / Terminés (statut dérivé du coup d'envoi).
    live = await match_select.fetch_live_odds("tennis")   # cotes Unibet fraîches (1 appel, gratuit)
    arows, a_live, a_fin = [], [], []
    for d in analyses.list_for("tennis"):
        st = analyses.status_of(d)
        # STATUT + HEURE pilotés par UNIBET (le sidecar peut être périmé -> faux « live »)
        lf0 = web.live_fields(match_select.live_state_for("tennis", d.get("home"), d.get("away")), "tennis")
        st, usdt = match_select.fresh_status("tennis", d.get("home"), d.get("away"), st, bool(lf0.get("score")))
        dt = usdt or d.get("_start_dt")
        tour = (d.get("circuit") or ("WTA" if (d.get("comp") or "").upper() == "WTA" else "ATP")).lower()
        fresh = match_select.live_odds_for(live, d.get("home"), d.get("away"))
        o1, o2 = (fresh[0], fresh[2]) if fresh else (d.get("o1"), d.get("o2"))
        sel, odds = analyses.pick_parts(d.get("pick") or "")
        perle = {"selection": sel, "odds": odds} if (sel and odds and odds >= 1.10) else None
        bars = web.analyst_bars(o1, None, o2,
                                analyses.votes_pct(d) or _cached_votes(provider, d.get("id")))
        r = {
            "id": d.get("id"), "tour": tour, "home": d.get("home", ""), "away": d.get("away", ""),
            "status": st, "time": web.fmt_local(usdt or d.get("start"), with_date=True),
            "score": "", "hp": None, "implied": None, "votes": None,
            "oh": o1, "oa": o2, "start_ts": dt.timestamp() if dt else None, "female": False,
            "perle": perle, "perle2": None, "pick_kind": "confiance", "_bars": bars,
        }
        if st == "inprogress":   # score (jeux/sets) + serveur + points EN DIRECT depuis Unibet
            r.update(lf0)
            if not r.get("score"):   # REPLI SofaScore si Unibet n'a pas le live
                r.update(await match_select.fetch_sofa_live("tennis", d.get("sofa_id") or d.get("id")) or {})
            # En cours SANS score live Unibet : s'il a assez tourné (likely_finished) -> Terminés ;
            # sinon on le GARDE en « En cours » (sans scoreboard) pour qu'il ne DISPARAISSE pas.
            if not r.get("score") and analyses.likely_finished(d):
                st = "finished"
                r["status"] = "finished"
        if st == "finished":
            bdg, sco = analyses.result_chip(d)
            brd = analyses.result_board(d, "tennis")   # détail set-par-set (« 6-4 3-6 6-2 »)
            card = {**_tennis_trow(r), **bars}
            card["score"] = brd["score"] or sco or "terminé"   # score réel + détail des sets
            card["badge"] = bdg                 # ✅/❌
            a_fin.append(card)
        else:
            (a_live if st == "inprogress" else arows).append(r)
    arows.sort(key=lambda r: r["start_ts"] or 0)
    a_live.sort(key=lambda r: r["start_ts"] or 0)
    # Cartes COMPLÈTES (barres + perle « à jouer ») dans chaque section ; plus de section Confiances.
    a_up = [{**_tennis_trow(r), **r["_bars"]} for r in arows]
    a_livec = [{**_tennis_trow(r), **r["_bars"]} for r in a_live]
    a_intro = ('🎾 <b>Tennis</b> — matchs analysés par l\'analyste. Touchez un match pour '
               'l\'analyse complète (Verdict, paris classés, faits, sources).')
    a_body = web.render_sport_matches("tennis", "Matchs", [], a_livec, a_up, a_fin,
                                      intro=a_intro, frag=bool(frag), confidences=[])
    if frag:
        fragcache.put("panel/tennis", a_body, ttl=PANEL_TTL)
    return HTMLResponse(a_body)


@router.get("/app/match/{match_id}", response_class=HTMLResponse)
async def match_detail(
    match_id: int,
    tour: str = Query("atp"),
    frag: int = 0,
    pk: str = Query(""),   # type de pari de la carte tapée : 'value' -> analyse sur la perle value
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
    rankings: RankingsProvider = Depends(get_rankings),
) -> HTMLResponse:
    tour = "wta" if tour == "wta" else "atp"
    if frag:
        cached = fragcache.get(f"tennis/{match_id}/{pk}")
        if cached:
            return HTMLResponse(cached)
    # Match ANALYSÉ -> fiche 100 % hors-ligne (sidecar + analyse), AUCUN appel SofaScore : même
    # renderer que foot/basket. (Une fois analysé, plus aucune raison d'appeler SofaScore.)
    amd = analyses.meta("tennis", match_id)
    if amd:
        live = await match_select.fetch_live_odds("tennis")   # cotes Unibet fraîches
        fresh = match_select.live_odds_for(live, amd.get("home"), amd.get("away"))
        o1, o2 = (fresh[0], fresh[2]) if fresh else (amd.get("o1"), amd.get("o2"))
        votes = analyses.votes_pct(amd) or _cached_votes(provider, match_id)
        ctx = {
            "home": amd.get("home", ""), "away": amd.get("away", ""),
            "home_flag": "", "away_flag": "", "comp": amd.get("comp") or "Tennis",
            "when": web.fmt_local(amd.get("start"), with_date=True),
            "analysis": analyses.render("tennis", match_id) or "",
            "streaks": amd.get("streaks"), "h2h": amd.get("h2h"),
            "form_html": "", "extra": "", "factors_html": "", "recos": "", "forms": None,
            "prediction": web.analyst_bars(o1, None, o2, votes),
            "odds_cells": [(amd.get("home", ""), o1), (amd.get("away", ""), o2)] if (o1 and o2) else None,
            "back_url": "/app", "back_label": "Tennis", "sport_key": "tennis",
            "links": analyses.links_html("tennis", match_id),
            "odds_move": web.odds_move_for("tennis", amd.get("home", ""), amd.get("away", "")),
        }
        html = web.render_sport_match_detail(ctx, frag=bool(frag))
        if frag:
            fragcache.put(f"tennis/{match_id}/{pk}", html)
        return HTMLResponse(html)
    try:
        match = await provider.get_match(tour, match_id)
    except ProviderError:
        # SofaScore en pause : en accordéon, on montre quand même la reco (store) + TOUS les
        # paris Unibet (qui ne dépendent pas de SofaScore). Sinon, détail léger pleine page.
        if frag:
            return await _tennis_light_frag(match_id, tour, unibet)
        return await _light_detail(match_id, tour, unibet, rankings, frag=bool(frag))

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
    # « 🎯 Paris conseillés » depuis le SUIVI (cohérent avec la carte), comme foot/basket.
    recos = ""
    analysis_html = ""
    if frag:
        rec = tracking.load().get(str(match_id))
        # COHÉRENCE carte/analyse : si la carte tapée est une VALUE, l'analyse parle de la perle
        # VALUE (sinon de la confiance) -> plus de « l'analyse joue un autre pari que la carte ».
        pv = rec.get("perle_value") if rec else None
        perle = (pv if (pk == "value" and isinstance(pv, dict) and pv.get("selection"))
                 else (rec.get("perle") if rec else None))
        if rec:
            recos = web.perle_advice(perle)   # 🎯 Paris conseillés = la perle (tous marchés)
        # 🧠 Analyse rédigée (gratuite, ou prose Claude si une clé API est configurée)
        ground = (analysis.ground_type or "").lower()
        surface = ("terre" if "clay" in ground else "gazon" if "grass" in ground
                   else "dur" if "hard" in ground else None)
        # COHÉRENCE carte/analyse : on prend la proba du SUIVI (celle des barres de la carte),
        # pas le recalcul à la volée -> plus de « 53/47 sur la carte, 50/50 dans le texte ».
        mh = (rec or {}).get("model_home_prob")
        if mh is None:
            mh = analysis.model_home_probability or 0.5
        fav_home = mh >= 0.5
        fav_prob_disp = max(mh, 1 - mh)
        fform = home_form if fav_home else away_form
        surf_edge = any(f.name == "surface" and ((f.home if fav_home else f.away) or 0) >= 0.55
                        for f in (analysis.factors or []))
        brief = {
            "sport": "tennis", "home": match.home.name, "away": match.away.name,
            "favorite": match.home.name if fav_home else match.away.name,
            "underdog": match.away.name if fav_home else match.home.name,
            "fav_prob": fav_prob_disp,
            "fav_odds": winner_odds[0] if fav_home else winner_odds[1],
            "confidence": analysis.confidence, "perle": perle, "value": None,
            "surface": surface, "surface_edge": surf_edge,
            "fav_rank": (match.home.ranking if fav_home else match.away.ranking),
            "dog_rank": (match.away.ranking if fav_home else match.home.ranking),
            "fav_form_wins": sum(1 for x in (fform or []) if x.get("win")),
            "fav_form_n": len(fform or []),
            "h2h_fav": (h2h_rec or {}).get("home" if fav_home else "away"),
            "h2h_opp": (h2h_rec or {}).get("away" if fav_home else "home"),
            "public_fav": ((votes[0] if fav_home else votes[1]) / 100 if votes else None),
            "match_id": match_id,
        }
        # Priorité à l'analyse « analyste » pré-générée (Claude headless) si elle existe.
        deep = analyses.render("tennis", match_id)
        analysis_html = deep or await match_analysis.write_analysis(brief, get_settings())
    # Marchés Unibet UTILISÉS pour la perle (snapshot) mais plus AFFICHÉS dans la fiche.
    markets_html = ""
    html = web.render_match_detail(
        analysis, winner_odds, aces=aces, tour=tour,
        home_form=home_form, away_form=away_form, h2h=h2h_rec, score=score, votes=votes,
        frag=bool(frag), recos=recos, markets_html=markets_html)
    if frag:
        html = analysis_html + html      # 🧠 l'analyse rédigée en tête de l'accordéon
        fragcache.put(f"tennis/{match_id}/{pk}", html)
    return HTMLResponse(html)


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


async def _tennis_light_frag(match_id, tour, unibet) -> HTMLResponse:
    """Accordéon tennis quand SofaScore est en pause : reco (depuis le suivi) + TOUS les paris
    Unibet (qui ne dépendent pas de SofaScore). Plus de « analyse indisponible » sec."""
    rec = tracking.load().get(str(match_id)) or {}
    parts = []
    if rec:
        parts.append(web.perle_advice(rec.get("perle")))   # 🎯 la perle (depuis le suivi)
    parts.append('<div class="banner">Stats détaillées (forme, face-à-face, facteurs) '
                 'momentanément indisponibles — source en pause. La prédiction (carte) reste '
                 'à jour.</div>')
    return HTMLResponse("".join(parts) or '<div class="dim">Analyse indisponible pour le moment.</div>')


async def _light_detail(match_id, tour, unibet, rankings, frag: bool = False) -> HTMLResponse:
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
        msg = ('<div class="banner">Analyse momentanément indisponible '
               '(SofaScore bloqué et match introuvable côté secours).</div>')
        return HTMLResponse(msg if frag else web.layout(
            "Indisponible", "tennis", msg + '<a class="dim" href="/app">← Retour</a>'))
    match.home.ranking = await rankings.rank(tour, match.home.name)
    match.away.ranking = await rankings.rank(tour, match.away.name)
    odds = await unibet.find_odds(match)
    analysis = build_analysis(match, [], [], None, None, None, None, odds)
    winner_odds = _match_winner_odds(odds, match) if (odds and odds.matched) else (None, None)
    note = ('<div class="banner">⚠️ SofaScore indisponible : analyse réduite (favori '
            'par classement + cotes). Stats/forme/h2h reviendront dès le rétablissement.</div>')
    html = web.render_match_detail(analysis, winner_odds, frag=frag)
    if frag:
        return HTMLResponse(note + html)
    return HTMLResponse(html.replace("</h1>", "</h1>" + note, 1))
