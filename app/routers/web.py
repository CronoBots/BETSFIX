"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import accounts, analyses, ace_markets, fragcache, match_analysis, match_select, serve_return, set_markets, tendencies, tracking, web, window
from app.config import get_settings
from app.analysis import build_analysis, remove_vig
from app.analysis import _match_winner_odds
from app.markets import (
    DEFAULT_SERVE, calibrate_to_market, evaluate_markets, extract_market_anchors,
    serve_win_pct,
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


CONF_MIN_PROB = 0.65   # "confiance" = favori NET (sinon ce n'est pas une vraie confiance)


def _cached_votes(provider, mid) -> tuple | None:
    """Votes communauté DÉJÀ en cache (sans appel réseau) -> (%home, %away, %draw). None sinon."""
    try:
        v = provider.get_votes_cached(int(mid))
        if v and v.home_percent is not None:
            return (v.home_percent, v.away_percent, v.draw_percent)
    except Exception:
        pass
    return None


def _provisional_hero_stats() -> dict:
    """Contribution des PROVISOIRES (RÉSULTAT SEUL : 1X2/DC, PAS les totaux Under/Over) au ROI GLOBAL
    (demande user 2026-08-07 : les provisoires comptent au global, résultat seul). {n, won, lost, profit,
    points (cumul datés), dates}. Cohérent avec `provisional_shown` (totaux exclus)."""
    from app import provisional as _pv
    rows = []
    for p in (_pv.load() or {}).values():
        if not isinstance(p, dict) or (p.get("sport") or "foot") != "foot":
            continue
        if p.get("result") not in ("won", "lost"):
            continue
        _s = (p.get("sel") or "").lower()
        if "moins de" in _s or "plus de" in _s:      # totaux -> exclus (résultat seul)
            continue
        rows.append(p)
    rows.sort(key=lambda p: p.get("start") or "")
    won = lost = 0
    cum, points, dates = 0.0, [0.0], []
    for p in rows:
        c = p.get("cote") or 0
        if p["result"] == "won":
            won += 1
            cum += (c - 1)
        else:
            lost += 1
            cum -= 1
        points.append(round(cum, 2))
        dates.append(p.get("start"))
    return {"n": won + lost, "won": won, "lost": lost, "profit": round(cum, 2),
            "points": points, "dates": dates}


def _hero_card(full: dict, combo: dict) -> str:
    """HERO en tête de la page Stats : le CHIFFRE CLÉ (rentabilité globale, tous paris confondus) + profit
    total + une courbe d'équité GLOBALE (simples + combinés + PROVISOIRES résultat, fusionnés par date).
    L'argument n°1, en un coup d'œil, avant le détail par catégorie/sport."""
    ov = full.get("overall") or {}
    cb = combo.get("overall") or combo or {}
    # PROVISOIRES retirés du produit (user 2026-08-11 : « je ne veux plus de provisoires ») -> ils ne
    # comptent PLUS au ROI global. Gardés uniquement en fantômes pour la calibration. On neutralise leur
    # contribution au hero (sinon « X paris réglés » gonflait de ~51 paris qui n'existent plus).
    pv = _provisional_hero_stats() if analyses.PROVISOIRES_ON else {}
    n_s, n_c, n_p = ov.get("settled") or 0, cb.get("n") or 0, pv.get("n") or 0
    bets = n_s + n_c + n_p
    if not bets:
        return ""
    profit = (ov.get("profit") or 0) + (cb.get("profit") or 0) + (pv.get("profit") or 0)
    won = (ov.get("won") or 0) + (cb.get("won") or 0) + (pv.get("won") or 0)
    roi = round(profit / bets * 100, 1)
    pct = round(won / bets * 100)

    def _deltas(pts, dts):                         # cumul -> deltas datés (pts[0]=0, pts[i+1] à dts[i])
        if not dts or not pts or len(pts) < 2:
            return []
        return [(dts[i], pts[i + 1] - pts[i]) for i in range(min(len(dts), len(pts) - 1))]
    evs = (_deltas(ov.get("points") or [], ov.get("dates") or [])
           + _deltas(cb.get("points") or [], cb.get("dates") or [])
           + _deltas(pv.get("points") or [], pv.get("dates") or []))
    evs.sort(key=lambda x: x[0] or "")             # fusion chronologique simples + combinés + provisoires
    gpts, curv = [0.0], 0.0
    for _, dlt in evs:
        curv += dlt
        gpts.append(round(curv, 2))
    _chart = f'<div class="sx-chart">{web._hero_chart(gpts, uid="hero")}</div>' if len(gpts) >= 3 else ""
    _cls = "pos" if roi >= 0 else "neg"
    return (
        '<div class="sx-hero">'
        '<div class="sx-hero-lbl">Rentabilité globale · tous paris</div>'
        f'<div class="sx-hero-roi {_cls}">{"+" if roi >= 0 else ""}{roi}%</div>'
        f'<div class="sx-hero-sub"><b>{bets}</b> paris réglés · <b>{pct}%</b> de réussite</div>'
        + _chart + '</div>')


_HOMESTATS_CACHE: dict = {}   # since_days -> (signature_données, html) — évite ~1.4 s de recalcul par rendu


def _stats_signature() -> tuple:
    """Signature LÉGÈRE des sidecars (nb fichiers + mtime max) : change dès qu'un match est réglé (mtime)
    ou ajouté/supprimé (nb) -> invalide le cache des stats au bon moment, sans relire les 391 JSON."""
    import glob
    import os
    files = glob.glob(os.path.join(analyses.DIR, "*.json"))
    # + mtime de sport_probation.json : un pause/réactivation d'un sport change la section Simulation
    # (cadres tennis/basket) -> doit invalider le cache stats (demande user 2026-07-24).
    # + mtime des TRACKS hors-sidecar qui alimentent les stats : combiné du jour (combo_daily_track.json) et
    #   provisoires (provisional_track.json). Sans ça, le RÈGLEMENT du combiné/provisoire (qui n'écrit PAS de
    #   sidecar mais son track) N'INVALIDAIT PAS le cache -> graphe combiné périmé (bug user 2026-08-08 :
    #   « le double chance d'hier gagné n'est plus affiché dans le graphe »).
    def _mt(name):
        try:
            return os.path.getmtime(os.path.join(analyses._ROOT, "data", name))
        except OSError:
            return 0.0
    _pb = _mt("sport_probation.json")
    _tracks = (_mt("combo_daily_track.json"), _mt("provisional_track.json"))
    return (len(files), max((os.path.getmtime(f) for f in files), default=0.0), _pb) + _tracks


def _home_stats(since_days: int | None = None) -> str:
    """Page STATISTIQUES, organisée en SECTIONS du résumé au détail (hiérarchie pro) :
      1. VUE D'ENSEMBLE   — Simples + Combinés (gros ROI + courbe d'équité)            [render_stats]
      2. OÙ EST L'EDGE    — détail par sport + rendement par cote
      3. FIABILITÉ        — calibration (la confiance tient-elle ses promesses ?)
      4. TRANSPARENCE     — volume de données (matchs/paris vus, part des fantômes)    [tout en bas]
    `since_days` filtre toute la page sur la même fenêtre (Tout / 7 j / 30 j).
    CACHE signature-based : ces agrégats (stats_full+combo+calibration+edge…) re-parcourent chacun les 391
    sidecars (~1.4 s). On mémorise le HTML tant que les DONNÉES n'ont pas changé (le warmer rafraîchit le
    fragment toutes les 15 s -> on évite de tout recalculer à vide)."""
    _sig = _stats_signature()
    _c = _HOMESTATS_CACHE.get(since_days)
    if _c and _c[0] == _sig:
        return _c[1]
    _parts = _home_stats_compute(since_days)             # (bilan_html, analyse_html) — refonte 2026-07-27
    _HOMESTATS_CACHE[since_days] = (_sig, _parts)
    return _parts


def _home_stats_compute(since_days: int | None = None) -> tuple:
    """Renvoie (BILAN, ANALYSE) pour les 2 sous-onglets de Résultats (refonte user 2026-07-27) :
    BILAN = hero + cadres sport (ROI + courbes) ; ANALYSE = edge/fiabilité/marchés/transparence."""
    full = analyses.stats_full(since_days)
    if not (full.get("overall") or {}).get("settled"):
        return ("", "")
    combo = analyses.combo_stats(since_days)
    cal = analyses.calibration(since_days)

    # Sections de DÉTAIL repliables (accordéon) pour raccourcir la page (demande user 2026-07-02) : la
    # VUE D'ENSEMBLE reste toujours ouverte, le reste se déplie à la demande.
    def _sec(label: str, sub: str, body: str, open: bool = False) -> str:
        return web.sx_section_collapsible(label, sub, body, open=open)

    # 2. OÙ EST L'EDGE : par sport puis par cote (mêmes données, granularité croissante).
    edge = web.render_sports_breakdown(full) + web.render_perf(analyses.perf_breakdown(since_days))
    # BILAN (sous-onglet 1) : rentabilité globale + cadres sport (ROI + courbes).
    bilan = (
        _hero_card(full, combo)                                                    # 0. HERO : rentabilité globale
        + web.render_stats(full, combo_full=combo)                                 # 1. cadre FOOTBALL
        + _simulation_card())                                                      # 1b. cadres TENNIS/BASKET
    # ANALYSE (sous-onglet 2) : là où le modèle se prouve (edge, calibration, marchés écartés, transparence).
    analyse = (
        _sec("Où se trouve l'edge", "performance par sport et par cote", edge, open=True)   # 2.
        + _sec("Fiabilité du modèle", "la confiance tient-elle ses promesses ?",   # 3.
               web.render_reliability(analyses.calibration_reliability(buckets=12))
               + web.render_calibration(cal))
        + _sec("Marchés écartés", "quels paris sont exclus, pourquoi, et quand ils reviennent",  # 3b.
               web.render_exclusions(analyses.exclusions_report()))
        + _sec("Surveillance des marchés", "échantillon & fiabilité par sport et type de pari",  # 3c.
               web.render_market_watch((cal or {}).get("by_sport")))
        + _sec("Débrief des pertes", "pourquoi chaque pari perdu a perdu · mémoire évolutive",  # 3d.
               web.render_debrief(None))
        + _sec("Transparence", "tout ce que le modèle a observé",                  # 4.
               web.render_volume(full, combo, cal) + web.render_volume_by_sport()))
    return (f'<div class="sx"><div class="sx-body">{bilan}</div></div>',
            f'<div class="sx"><div class="sx-body">{analyse}</div></div>')


@router.get("/stats/detail", response_class=HTMLResponse)
async def stats_detail(sport: str = "", pari: int = -1, since: str = "") -> HTMLResponse:
    """Fragment drill-down : liste des matchs réglés d'une catégorie (sport / pari / période)."""
    sp = sport if sport in ("foot", "tennis", "basket") else None
    pk = pari if pari in (0, 1, 2) else None
    days = {"7": 7, "30": 30}.get(since)
    return HTMLResponse(web.render_bet_detail(analyses.bet_detail(sp, pk, days)))


_HMR_CACHE: dict = {"ts": 0.0, "rows": None}
_HMR_TTL = 15   # s : mémo COURTE des lignes tous-sports -> changer d'onglet de sport ne re-fetch pas tout


async def _home_match_rows() -> list:
    """TOUTES les rencontres analysées À VENIR / EN COURS (tous sports confondus), au format
    `_sport_row`, triées par coup d'envoi (le plus proche d'abord). Réutilise les constructeurs de
    lignes des onglets sport -> même rendu compact partout.
    MÉMO COURTE (demande user 2026-07-28 : « changer d'onglet de sport est très lent ») : la construction
    fetch les cotes/live des 3 sports ; le sélecteur de sport de Pronos rappelle cette fonction à CHAQUE
    changement (pour re-filtrer sur un sport) -> sans cache, chaque switch re-fetchait TOUS les sports. On
    réutilise donc le dernier build < _HMR_TTL s (les scores live bougent ~toutes les 15-30 s de toute façon)."""
    import time as _t
    _now = _t.time()
    if _HMR_CACHE["rows"] is not None and (_now - _HMR_CACHE["ts"]) < _HMR_TTL:
        return _HMR_CACHE["rows"]
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
        out += await _tennis_rows()
    except Exception:
        pass
    out.sort(key=lambda x: x.get("start_ts") or 0)         # coup d'envoi le plus proche d'abord
    _HMR_CACHE["ts"], _HMR_CACHE["rows"] = _now, out
    return out


async def _tennis_rows(include_background: bool = False) -> list:
    """Rows TENNIS (à-venir/en-cours) au format `_tennis_trow`, depuis les sidecars. Extrait de
    `_home_match_rows` pour être réutilisable par la vue mono-sport tennis de Pronos. `include_background=True`
    (tennis EXPLICITEMENT sélectionné) force la lecture malgré le statut arrière-plan (demande user 2026-07-29)."""
    out: list = []
    live = await match_select.fetch_live_odds("tennis")
    for d in analyses.list_for("tennis", include_background=include_background):
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
            # en cours sans score live : on ne le retire QUE s'il est RÉELLEMENT RÉGLÉ (sinon un pari live
            # disparaîtrait entre le coup d'envoi et son règlement — même correctif que foot, user 2026-08-02).
            if not r.get("score") and analyses.likely_finished(d) and analyses.is_settled(d):
                continue
        out.append({**_tennis_trow(r), **bars})
    return out


async def _bg_sport_rows(sp: str) -> list:
    """Rows d'un sport en ARRIÈRE-PLAN (basket/tennis) pour la vue mono-sport de Pronos — construits À LA
    DEMANDE (hors cache `_home_match_rows`, donc SANS impact sur l'accueil/vue « Tous »). Sert à afficher les
    PARIS JOUÉS de ce sport quand il est sélectionné, exactement comme son combiné du jour (demande user
    2026-07-29 : un pari à jouer basket doit apparaître dans Pronos). Simulé (hors ROI réel), inchangé."""
    if sp == "basket":
        from app import basket as basket_mod
        from app.routers import basket as basket_r
        brows, _ = await basket_r._analyst_rows(include_background=True)
        return [basket_mod._card(r) for r in brows]
    if sp == "tennis":
        return await _tennis_rows(include_background=True)
    return []


def _past_day_cards(date_iso: str) -> list:
    """Cartes d'un JOUR PASSÉ portant un VRAI pari (simple joué figé OU combiné réglé), construites
    DIRECTEMENT depuis les sidecars filtrés par date — SANS fetch d'odds live (matchs finis -> les cotes
    stockées suffisent) ni construction des ~200 autres cartes. Chargement d'un jour ~10× plus rapide
    (demande user 2026-07-19 : chargement des jours trop lent)."""
    from app import foot as foot_mod, basket as basket_mod
    out = []

    def _has_bet(d: dict) -> bool:
        return ((d.get("stat_bet") or {}).get("result") in ("won", "lost", "push")
                or (d.get("combo") or {}).get("result") in ("won", "lost", "void"))

    _bg = analyses.background_sports()                     # tennis/basket en arrière-plan -> jamais sur la page des paris
    for sport in ("foot", "basket", "tennis"):
        if sport in _bg:                                  # sport simulé -> pas dans les « Résultats du jour »
            continue
        for d in analyses.iter_meta(sport):               # brut (pas de retained_bet) : filtrage strict ci-dessous
            if d.get("roi_void"):                         # pari exclu du ROI/historique (correction) -> pas affiché
                continue
            dt = d.get("_start_dt")
            if dt is None:
                continue
            ld = web.to_local(dt)
            if ld is None or web._sport_date(ld).isoformat() != date_iso:   # jour sportif 06h→06h
                continue
            if not (analyses.is_settled(d) and _has_bet(d)):
                continue
            _bdg, _sco = analyses.result_chip(d)
            # État RÉSULTAT pour le bord gauche coloré de la carte (demande user 2026-07-25) : won/lost/push.
            _res0 = d.get("result") or {}
            _combo0 = d.get("combo") or {}
            _outcome = _combo0.get("result") if _combo0.get("legs") else _res0.get("pick_result")
            _cstate = {"won": "won", "lost": "lost", "push": "push", "void": "push"}.get(_outcome, "")
            ts = dt.timestamp()
            if sport == "foot":
                o1, ox, o2 = d.get("o1"), d.get("ox"), d.get("o2")
                out.append(foot_mod._card({
                    "id": d.get("sofa_id") or d.get("id"), "comp": d.get("comp"),
                    "home": d.get("home", ""), "away": d.get("away", ""), "probs": None, "goals": None,
                    "o1": o1, "ox": ox, "o2": o2,
                    "imp": foot_mod._devig3(o1, ox, o2) if (o1 and ox and o2) else None,
                    "pick": None, "start": ts, "votes": analyses.votes_pct(d), "perle": None,
                    "perle2": None, "perle_value": None, "pick_kind": "confiance", "sofa_ok": True,
                    "status": "finished", "res_badge": _bdg, "res_score": _sco}))
            elif sport == "basket":
                oh, oa = d.get("o1"), d.get("o2")
                imp = basket_mod._devig(oh, oa) if (oh and oa) else None
                out.append(basket_mod._card({
                    "id": d.get("sofa_id") or d.get("id"), "league": (d.get("comp") or "").upper(),
                    "home": d.get("home", ""), "away": d.get("away", ""), "model_home": None,
                    "margin": None, "oh": oh, "oa": oa, "imp_home": imp[0] if imp else None,
                    "pick": None, "start": ts, "votes": analyses.votes_pct(d), "perle": None,
                    "perle2": None, "perle_value": None, "pick_kind": "confiance", "sofa_ok": True,
                    "status": "finished", "res_badge": _bdg, "res_score": _sco}))
            else:                                          # tennis
                tour = (d.get("circuit") or ("WTA" if (d.get("comp") or "").upper() == "WTA" else "ATP")).lower()
                r = {"id": d.get("id"), "tour": tour, "home": d.get("home", ""), "away": d.get("away", ""),
                     "status": "finished", "time": web.fmt_local(d.get("start"), with_date=True),
                     "score": _sco or "", "hp": None, "implied": None, "votes": None,
                     "oh": d.get("o1"), "oa": d.get("o2"), "start_ts": ts, "female": False,
                     "perle": None, "perle2": None, "pick_kind": "confiance"}
                out.append({**_tennis_trow(r),
                            **web.analyst_bars(d.get("o1"), None, d.get("o2"), analyses.votes_pct(d))})
            if out:                                        # bord gauche coloré selon le résultat
                out[-1]["_state"] = _cstate
    for _c in out:                                         # déjà filtrées bet-only -> évite un re-check meta
        _c["_bet"] = True
    out.sort(key=lambda x: x.get("start_ts") or 0)
    return out


@router.get("/jour", response_class=HTMLResponse)
async def jour(date: str, sport: str = "", frag: int = 1) -> HTMLResponse:
    """Fragment d'un JOUR pour le calendrier « Pronos » (injecté dans #day-content). `date` = YYYY-MM-DD
    LOCAL. Aujourd'hui/futur -> les zones habituelles (à venir) ; un jour PASSÉ -> les paris proposés ce
    jour-là + leurs résultats + le bilan du jour. `sport` (foot/tennis/basket) = filtre sport (puces Pronos),
    "" = tous. Caché par (date, sport) — passé ~immuable TTL long ; aujourd'hui TTL court."""
    import datetime as _dt
    today_iso = web._sport_today().isoformat()   # jour sportif 06h→06h
    sp = sport if sport in ("foot", "tennis", "basket") else None
    is_past = date < today_iso
    ckey = f"panel/jour/{date}/{sp or 'all'}"
    cached = fragcache.get(ckey)
    if cached is not None:
        return HTMLResponse(cached)
    if not is_past:                                        # aujourd'hui (ou futur) = vue du jour
        # LIVE GARDÉS (bug user 2026-08-02 : « un pari du jour EN LIVE n'apparaît pas dans Pronos ») : on NE
        # filtre PLUS les `inprogress`. Un pari JOUÉ qui passe en direct doit rester dans « Paris du jour »
        # (_today_zones trie à-venir → live) jusqu'à son règlement. Les abstentions live sont déjà exclues en
        # amont par `list_for` (donc absentes de _home_match_rows) -> seuls les VRAIS paris joués live passent.
        rows = list(await _home_match_rows())
        # SPORT EN ARRIÈRE-PLAN (basket/tennis) EXPLICITEMENT sélectionné : ses paris joués ne passent pas
        # _home_match_rows (list_for=[]) -> on les construit à la demande pour CETTE vue mono-sport, comme son
        # combiné du jour (demande user 2026-07-29). Jamais en vue « Tous » (sp=None) -> politique inchangée.
        if sp in analyses.background_sports():
            rows += list(await _bg_sport_rows(sp))
        results = _past_day_cards(today_iso)               # paris terminés d'aujourd'hui -> zone dédiée
        body = web._today_zones(rows, sp, results)[0]
        fragcache.put(ckey, body, ttl=PANEL_TTL)           # jour courant : bouge -> TTL court
        return HTMLResponse(body)
    day_rows = _past_day_cards(date)                       # jour passé : cartes bet-only de cette date (rapide)
    body = web._day_view(date, day_rows, sp)
    fragcache.put(ckey, body, ttl=1800)                    # jour passé : ~immuable -> 30 min
    return HTMLResponse(body)


import time as _time                                       # throttle du « nudge » de règlement

_LAST_SETTLE_NUDGE = 0.0
_SETTLE_NUDGE_THROTTLE = 120.0                             # au PLUS 1 règlement / 2 min, GLOBAL (serveur)


def _nudge_settle() -> None:
    """RÈGLEMENT poussé À L'OUVERTURE d'une page (demande user 2026-07-26) : un match fini se règle en
    quelques secondes après une visite au lieu d'attendre le cron 10 min. SCALABILITÉ : le throttle est
    GLOBAL (un seul timestamp serveur PARTAGÉ, pas par visiteur) -> même avec des milliers de chargements,
    le règlement tourne AU PLUS 1 fois / _SETTLE_NUDGE_THROTTLE ; la quasi-totalité des requêtes ne fait
    qu'une comparaison de timestamp (coût ~0). Fire-and-forget (ne bloque JAMAIS le rendu) ; `settle_analyses`
    a son propre verrou (no-op si une passe tourne déjà). Best-effort, jamais bloquant/erreur remontée."""
    global _LAST_SETTLE_NUDGE
    now = _time.monotonic()
    if now - _LAST_SETTLE_NUDGE < _SETTLE_NUDGE_THROTTLE:
        return                                            # throttle GLOBAL -> l'immense majorité ne fait RIEN
    _LAST_SETTLE_NUDGE = now

    async def _bg() -> None:
        try:
            from app import settle_analyst
            await settle_analyst.settle_analyses()
        except Exception:
            pass

    try:
        asyncio.create_task(_bg())
    except RuntimeError:
        pass                                              # pas de boucle asyncio active -> ignoré


@router.get("/", response_class=HTMLResponse)
async def home(request: Request,
               provider: SofaScoreProvider = Depends(get_provider),
               frag: int = 0) -> HTMLResponse:
    """Accueil : stats principales + les matchs À VENIR uniquement (format compact, tous sports
    mélangés, par ordre de passage). Les matchs EN COURS vivent dans l'onglet 🟢 Live (demande
    utilisateur 2026-06-12 : pas de doublon accueil/live, et un live qui démarre n'a parfois pas
    encore de score -> badge « LIVE » nu peu lisible). La nav passe par le menu ☰."""
    # ACCUEIL = onglet SPA à part (/accueil) désormais -> `/` reste le dashboard PRONOS pour tous (le
    # gating d'onglets par abonnement viendra plus tard, demande user 2026-07-30). Le paywall masque
    # toujours les pronos aux non-abonnés dans le dashboard.
    _nudge_settle()   # ouverture de page -> pousse le règlement en arrière-plan (throttlé global, non bloquant)
    if frag:   # panneau partagé (pas de données par utilisateur) -> cache court anti-rafale
        cached = fragcache.get("panel/home")
        if cached:
            return HTMLResponse(cached)
    # Simulation de bankroll DÉSACTIVÉE (2026-06-14, demande utilisateur) : on garde les paris MIS EN
    # AVANT (⭐ moteur, indépendant) mais on n'enregistre plus de simulation/bankroll. ACCUEIL = paris
    # À VENIR + petit bandeau live (les stats vivent dans l'onglet 📊).
    all_rows = await _home_match_rows()
    live_n = sum(1 for r in all_rows if r.get("status") == "inprogress")
    # LIVE GARDÉS dans Pronos (bug user 2026-08-02 : « le pari du jour en live n'apparaît pas ») : la vue
    # Pronos par défaut EST cette route `/` (dashboard) -> on ne filtre PLUS les `inprogress`, sinon un pari
    # JOUÉ qui passe en direct disparaît de « Paris du jour ». _today_zones les place (à-venir → live) et les
    # abstentions live sont déjà exclues par list_for. `live_n` reste le total pour le badge de l'onglet Live.
    rows = list(all_rows)
    import datetime as _dt
    _today = web._sport_today().isoformat()    # jour sportif 06h→06h
    results = _past_day_cards(_today)          # paris TERMINÉS d'aujourd'hui (résultats) -> zone dédiée
    body = web.render_dashboard(rows, live_count=live_n, results=results,
                                frag=bool(frag), source=provider.breaker_status())
    if frag:
        fragcache.put("panel/home", body, ttl=PANEL_TTL)
    return HTMLResponse(body)


@router.get("/accueil", response_class=HTMLResponse)
async def accueil(frag: int = 0) -> HTMLResponse:
    """Onglet ACCUEIL = vitrine (relevé + méthode + transparence), PANNEAU SPA comme les autres onglets
    (demande user 2026-07-30). `frag=1` -> fragment seul (injecté dans le panneau) ; sinon coquille SPA
    complète (onglet 'accueil' actif). Accessible à TOUS ; cache partagé (pas de données par utilisateur)."""
    key = "panel/accueil-f" if frag else "panel/accueil"
    cached = fragcache.get(key)
    if cached:
        return HTMLResponse(cached)
    body = web.accueil_body(frag=bool(frag))
    page = body if frag else web.spa_shell("accueil", "Accueil", body)
    fragcache.put(key, page, ttl=600)
    return HTMLResponse(page)


@router.get("/paris")
async def paris_redirect() -> RedirectResponse:
    """Page « Paris à jouer » RETIRÉE (2026-06-12) : les paris retenus restent dans les analyses,
    marqués ⭐ (carte repliée + cadre déplié). Redirection douce pour les liens en cache mobile."""
    return RedirectResponse("/", status_code=308)


async def _system_health_html() -> str:
    """Panneau « Santé du système » (privé) : santé LIVE des sources (ping) + auto-audit d'intégrité, dans
    une section repliable de la page Stats. Rend visible d'un coup d'œil ce que surveillaient les CLI."""
    import html as _h
    from app import selfcheck, source_health
    sc = selfcheck.run(persist=False)
    sh = await source_health.check_all()
    lvlc = {"ok": "#22c55e", "info": "#38bdf8", "warn": "#f59e0b", "error": "#ef4444"}

    def _dot(color):
        return f'<span style="color:{color};font-size:1.05em">●</span>'

    def _row(left, right):
        return ('<div style="display:flex;justify-content:space-between;gap:10px;padding:4px 0;'
                'border-bottom:1px solid rgba(255,255,255,.06);font-size:.92em">'
                f'<span>{left}</span><span style="opacity:.72;text-align:right">{right}</span></div>')
    # « critique » = source PILIER (Unibet=cotes/sélection, FotMob=stats foot) : label de RÔLE, pas une
    # alerte. Rouge UNIQUEMENT si un pilier est DOWN (là c'est grave) ; en ligne -> teinte neutre/muette.
    src = "".join(
        _row(f'{_dot("#22c55e" if s["ok"] else "#ef4444")} {_h.escape(s["label"])}'
             + ((f' <span style="color:{"#f87171" if not s["ok"] else "#8a8a95"};font-weight:700;'
                 f'font-size:.85em" title="Source pilier — indispensable aux analyses">'
                 f'{"⚠ critique" if not s["ok"] else "critique"}</span>') if s["critical"] else ""),
             f'{s["latency_ms"]} ms · {_h.escape(str(s["detail"]))}')
        for s in sh["sources"])
    chk = "".join(
        _row(f'{_dot(lvlc.get(c["level"], "#999"))} {_h.escape(c["title"])}',
             _h.escape((c.get("detail") or "")[:64]))
        for c in sc["checks"])
    online = len(sh["sources"]) - len(sh["down"])
    # AUDIT : un contrôle « info » (ex. data_completeness) N'EST PAS un échec -> il compte comme sain.
    # On n'alerte que sur error/warn. Sinon un simple info faisait « 12/13 » et ressemblait à une panne.
    _alerts = sc["counts"].get("error", 0) + sc["counts"].get("warn", 0)
    _pass = len(sc["checks"]) - _alerts
    _audit_dot = lvlc["ok"] if _alerts == 0 else lvlc.get(sc["status"], "#999")
    _audit_mark = "✅" if _alerts == 0 else "⚠️"
    body = (f'<div style="margin:2px 0 8px;font-weight:600">{_dot(lvlc.get(sh["status"], "#999"))} '
            f'Sources — {online}/{len(sh["sources"])} en ligne</div>' + src
            + f'<div style="margin:16px 0 8px;font-weight:600">{_dot(_audit_dot)} '
            f'Auto-audit — {_pass}/{len(sc["checks"])} {_audit_mark}</div>' + chk)
    return web.sx_section_collapsible("🩺 Santé du système", "sources en ligne + auto-audit (privé)", body)


@router.get("/stats/health", response_class=HTMLResponse)
async def stats_health(request: Request) -> HTMLResponse:
    """Panneau santé — PRIVÉ (propriétaire uniquement) : il nomme les sources (avantage compétitif à
    masquer). Chaîne VIDE pour tout visiteur non-propriétaire -> le bloc reste invisible dans la page."""
    from app import accounts
    if not accounts.is_owner(request):
        return HTMLResponse("")
    return HTMLResponse(await _system_health_html())


# `_provisional_card` SUPPRIMÉ le 2026-07-25 (mort) : les provisoires sont affichés dans l'onglet
# « Provisoires » de chaque cadre sport (web._prov_sport_graph), plus dans une carte Stats séparée.

_combo_legs_html = web.combo_legs_html   # rendu UNIFIÉ (accueil/Stats/Live) — défini dans app/web.py


def _selectivity_card() -> str:
    """« Sélectivité du jour » — composition du PROGRAMME du scan de 09h (data/day_programme.json, la MÊME
    source que l'onglet « À venir ») : combien de matchs à venir donnent un PARI À JOUER (value, compté au
    ROI) vs un PARI PROVISOIRE (favori sans marge, indicatif hors ROI). Compté sur EXACTEMENT les mêmes
    items que l'onglet « À venir » -> les chiffres COÏNCIDENT avec son badge (fini le « 11 vs 10 », demande
    user 2026-07-17). Ne compte que les matchs ENCORE À VENIR (hors live/terminé, comme l'onglet). '' si le
    programme du jour est vide."""
    import datetime as _dt
    # PROVISOIRES : exactement les cartes provisoires de l'accueil (même fonction, même dédup combiné du
    # jour, même exclusion du live) -> le compteur ne peut plus diverger du badge « À venir ».
    prov = sum(1 for it in web._programme_items(framed=True)
               if it.get("_prov") and not it.get("_live"))
    # PARIS À JOUER (ROI) encore À VENIR : simples publiés/retenus (pending_roi_bets) au coup d'envoi non passé.
    _now = _dt.datetime.now(_dt.timezone.utc)

    def _upcoming(b) -> bool:
        try:
            return _dt.datetime.fromisoformat((b.get("start") or "").replace("Z", "+00:00")) > _now
        except (ValueError, AttributeError):
            return False

    a_jouer = sum(1 for b in analyses.pending_roi_bets() if _upcoming(b))
    tot = a_jouer + prov
    if tot == 0:
        return ""
    pct = round(100 * a_jouer / tot)
    _ont = "a" if a_jouer == 1 else "ont"
    _ps = "" if a_jouer == 1 else "s"
    if a_jouer == 0:
        _main = (f'Sur les <b>{tot} matchs à venir</b> du jour, <b>aucun</b> n\'a de <b>VALUE</b> '
                 '(proba ≥ 65 % ET marge réelle sur la cote) : <b>0 pari à jouer</b> pour l\'instant. '
                 f'Les <b>{prov}</b> sont des <b>paris provisoires</b> — le meilleur angle de chaque match, '
                 '<b>indicatif · hors ROI</b> (favoris sans marge sur lesquels on <b>s\'abstient</b>). '
                 f'C\'est la <b>discipline</b> qui protège le ROI. Ces {prov} provisoires sont dans l\'onglet '
                 '<b>« À venir »</b> (zone <i>Indicatif</i>) ; un <b>pari à jouer</b> s\'affichera dès qu\'un '
                 'match aura de la value.')
    else:
        _main = (f'Sur les <b>{tot} matchs à venir</b> du jour, <b>{a_jouer}</b> {_ont} de la <b>VALUE</b> '
                 f'(proba ≥ 65 % ET marge réelle sur la cote) → <b>pari{_ps} à jouer</b> (compté{_ps} au ROI, '
                 f'onglet <b>« À venir »</b>). Les <b>{prov}</b> autres = <b>paris provisoires</b> (favoris sans '
                 'marge, indicatif hors ROI). C\'est la <b>discipline</b> qui protège le ROI, pas un bug.')
    return (
        '<div class="sx-card"><div class="sx-h">🎯 Sélectivité du jour '
        '<span>= onglet À venir</span></div>'
        '<div class="sx-kpis sx-kpis3">'
        f'<div class="sx-kpi sx-pos"><b>{a_jouer}</b><span>à jouer</span></div>'
        f'<div class="sx-kpi"><b class="sx-gold">{prov}</b><span>provisoires</span></div>'
        f'<div class="sx-kpi"><b>{pct}%</b><span>de sélection</span></div>'
        '</div>'
        f'<div class="sx-data-note">{_main}</div></div>')


def _simulation_card() -> str:
    """FOOT SEUL (user 2026-08-07) : tennis/basket retirés du produit -> plus de cadre « 🔬 Simulation »
    (leur ROI simulé n'a plus de raison d'être affiché). Renvoie toujours ''."""
    return ""
    bg = analyses.background_sports()   # (mort — conservé si réactivation multi-sport un jour)
    if not bg:
        return ""
    full = analyses.stats_full()
    combo = analyses.combo_stats()
    _emo = {"tennis": "🎾", "basket": "🏀", "foot": "⚽"}
    _nom = {"tennis": "Tennis", "basket": "Basket", "foot": "Foot"}
    # UN CADRE PAR SPORT (demande user 2026-07-24) : chaque sport simulé = son `.sx-card` regroupant ses
    # simples ET ses combos.
    out = ""
    for sp in ("tennis", "basket", "foot"):
        if sp not in bg:
            continue
        _simple_g = _combos_g = ""
        # Paris À VENIR (en attente) du sport SIMULÉ -> intégrés aux listes « Derniers... » avec ⏳, EXACTEMENT
        # comme le football (demande user 2026-07-26 ; `sport=sp` contourne l'exclusion arrière-plan).
        _pend_s = analyses.pending_roi_bets(sport=sp)
        _pend_c = analyses.pending_roi_bets(combo=True, sport=sp)
        b = (full.get("by_sport") or {}).get(sp) or {}      # SIMPLES simulés du sport (MÊME emoji sport)
        if b.get("settled") or _pend_s:
            _simple_g = web.render_tracking_curve(
                emoji=_emo.get(sp, "🔬"), title="SIMPLE", roi=b.get("roi"), hit=b.get("pct"),
                n=b.get("settled") or 0, points=b.get("points"), dates=b.get("dates"),
                avg_cote=b.get("avg_odds"), uid=f"sim-{sp}", streak=b.get("streak"),
                form=web._form_streak(b.get("form_run") or b.get("form") or [])[0],   # ligne W/L
                recent=_pend_s + list(reversed(b.get("recent") or [])), more_label="Derniers simples",
                pending=len(_pend_s),                        # sabliers ⏳ des à venir (comme football)
                milestones=web._sport_milestones(sp), compact=True,   # disposition « ROI héros »
                hit_points=b.get("hit_points"), best_streak=b.get("best_streak"),   # record sur tout l'historique
                cote_points=b.get("cote_points"))            # 3e graphe : cote moyenne
        c = (combo.get("by_sport") or {}).get(sp) or {}     # COMBINÉS simulés du sport (MÊME emoji que le simple)
        if c.get("settled") or _pend_c:
            _combos_g = web.render_tracking_curve(
                emoji=_emo.get(sp, "🔬"), title="COMBINÉS", roi=c.get("roi"), hit=c.get("pct"),
                n=c.get("settled") or 0, points=c.get("points"), dates=c.get("dates"),
                avg_cote=c.get("avg_odds"), uid=f"simc-{sp}", streak=c.get("streak"),
                form=web._form_streak(c.get("form_run") or c.get("form") or [])[0],   # ligne W/L
                recent=_pend_c + list(reversed(c.get("recent") or [])), more_label="Derniers combinés",
                pending=len(_pend_c),
                milestones=web._sport_milestones(sp), compact=True,   # disposition « ROI héros »
                hit_points=c.get("hit_points"), best_streak=c.get("best_streak"),   # record sur tout l'historique
                cote_points=c.get("cote_points"))            # 3e graphe : cote moyenne
        curves = web._sport_tabs(_simple_g, _combos_g, web._prov_sport_graph(sp),   # + onglet Provisoires (user 2026-07-25)
                                 counts=(len(_pend_s), len(_pend_c), web._prov_pending_count(sp)),   # badges EN COURS
                                 rois=(b.get("roi"), c.get("roi"), web._prov_sport_roi(sp)))   # ROI discret par onglet (user 2026-08-02)
        if not curves:
            continue
        # En-tête = BANNIÈRE BETSFIX du sport + ligne « simulé · hors paris » sous l'image, IDENTIQUE pour
        # tous les sports simulés (couleur ambre, SANS « prêt à réactiver » — demande user 2026-07-24).
        out += (
            '<div class="sx-card">'
            + web._sport_banner(sp)
            + '<div class="stat-banner-sub">simulé · hors paris</div>'
            + curves + '</div>')
    return out


def _combo_safe_card() -> str:
    """Cadre « info seule » (Résultats-Bilan, groupe hors ROI) du COMBINÉ SÉCURITÉ FOOT — demande user
    2026-07-28 : combiné composé UNIQUEMENT de foot, la DOUBLE CHANCE la plus sûre par match, cote ~2.
    Même présentation que le combiné bonus (courbe P&L + réussite + historique dépliable). '' si aucun suivi."""
    from app import combo_safe as _cs
    d = _cs.load()
    _floor = analyses.first_stats_day()
    days = {k: v for k, v in d.items()
            if isinstance(v, dict) and v.get("legs") and not k.startswith("_")
            and (not _floor or k >= _floor)}
    if not days:
        return ""
    done = [cb for cb in days.values() if cb.get("result") in ("won", "lost")]
    won = sum(1 for cb in done if cb["result"] == "won")
    _pts, _acc = [0.0], 0.0
    for _day in sorted(days):
        cb = days[_day]
        if cb.get("result") not in ("won", "lost"):
            continue
        _acc += _cs._cd._combo_result_profit(cb)
        _pts.append(round(_acc, 2))
    pnl = round(_acc, 2)
    _hit = round(100 * won / len(done)) if done else None
    _roi = round(100 * pnl / len(done)) if done else None
    _avgc = round(sum(cb.get("cote") or 0 for cb in done) / len(done), 2) if done else None
    pend = len(days) - len(done)
    # HISTORIQUE présenté COMME les combinés des autres sports (jambes dépliables). On INCLUT le jour courant
    # (pas d'autre affichage plein ailleurs pour ce combiné). Du plus récent au plus ancien.
    _recent = []
    for _day in sorted(days, reverse=True):
        cb = days[_day]
        _lg = cb.get("legs") or []
        _recent.append({
            "result": cb.get("result") or "pending",
            "name": f"Combiné du jour ({len(_lg)} jambe{'s' if len(_lg) > 1 else ''})",
            "sel": "",
            "cote": cb.get("cote"),
            "start": (_lg[0].get("start") if _lg and _lg[0].get("start") else _day + "T12:00:00Z"),
            "legs": [{"name": f'{l.get("home", "?")} - {l.get("away", "?")}',
                      "sel": analyses.pretty_sel(str(l.get("sel") or ""), l.get("home", ""), l.get("away", "")),
                      "cote": l.get("cote"), "result": l.get("result")}
                     for l in _lg]})
    _form, _streak = web._form_streak(
        [days[d0].get("result") for d0 in sorted(days) if days[d0].get("result") in ("won", "lost")])
    _curve = web.render_tracking_curve(emoji="🛡️", title="Double chance", roi=_roi, hit=_hit,
                                       n=len(done), points=_pts, avg_cote=_avgc, uid="combosafe",
                                       recent=_recent, more_label="Derniers combinés",
                                       form=_form, pending=pend, streak=_streak, compact=True,
                                       hit_points=web._hit_curve([days[d0].get("result") for d0 in sorted(days)]))
    return '<div class="sx-card"><div class="sx-h">Combiné double chance</div>' + _curve + '</div>'


# `_combo_daily_card` SUPPRIMÉ le 2026-07-25 (mort) : le combiné du jour foot est ventilé dans le
# « Combinés » du cadre Football (combo_stats.by_sport), plus de carte Stats standalone.


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(frag: int = 0, since: str = "") -> HTMLResponse:
    """Onglet « Statistiques » (barre du bas) : synthèse + bilan + courbe + ROI + combinés + calibration.
    `since` ∈ {7,30,''} = fenêtre temporelle. Sert un FRAGMENT quand frag=1 (panneau SPA)."""
    since = since if since in ("7", "30") else ""
    days = {"7": 7, "30": 30}.get(since)
    ckey = f"panel/stats:{since or 'all'}"
    # Cache PARTAGÉ entre la charge directe (frag=0, enveloppée dans le shell) et le fragment SPA (frag=1) :
    # les deux servent EXACTEMENT le même corps -> plus de divergence « frais (reload) vs périmé (clic
    # onglet) » qui faisait « 1x sur 2 » les stats fausses. Le warmer (main.py) le garde chaud (≤15s).
    body = fragcache.get(ckey)
    if body is None:
        _bilan, _analyse = _home_stats(days)    # (BILAN, ANALYSE) — sous-onglets Résultats (refonte 2026-07-27)
        body = ('<div class="pg-h">Résultats</div>'
                + web._resultats_subnav()   # sous-nav Bilan | Analyse | Calendrier
                # SOUS-ONGLET 1 — BILAN : rentabilité globale + cadres sport + suivis indicatifs du jour.
                + '<div id="res-bilan" class="statsx">'    # scope : fond cyan sur TOUS les cadres
                + _bilan
                # SÉPARATEUR de groupe : tout ce qui suit est du JOUR / INDICATIF, distinct du ROI réel.
                + '<div class="sx-group">🧪 Le jour &amp; suivis indicatifs '
                  '<span>à titre informatif — hors ROI réel</span></div>'
                + _selectivity_card()     # ratio paris à jouer / abstentions du jour (rend la sélectivité visible)
                + _combo_safe_card()      # combiné sécurité foot (double chance la plus sûre ~2, hors ROI)
                # Panneau SANTÉ (privé) chargé en AJAX : servi UNIQUEMENT au propriétaire (is_owner).
                + '<div id="syshealth"></div>'
                + '<script>fetch("/stats/health").then(r=>r.text()).then(function(h){'
                  'if(h){document.getElementById("syshealth").innerHTML=h;}})'
                  '.catch(function(){});</script>'
                + '</div>'                       # fin #res-bilan
                # SOUS-ONGLET 2 — ANALYSE : edge / fiabilité / marchés écartés / transparence (masqué au départ).
                + f'<div id="res-analyse" class="statsx" hidden>{_analyse}</div>'
                # SOUS-ONGLET 3 — CALENDRIER : lazy-chargé depuis /calendrier?frag=1 au 1er clic.
                + '<div id="res-cal" hidden data-loaded="0"></div>')
        fragcache.put(ckey, body, ttl=PANEL_TTL)
    if frag:
        return HTMLResponse(body)
    return HTMLResponse(web.spa_shell("stats", "Statistiques", body))


@router.get("/calendrier", response_class=HTMLResponse)
async def calendrier_page(ym: str = "", frag: int = 0, cal: int = 0) -> HTMLResponse:
    """Onglet « Calendrier » (demande user 2026-07-25) : vue mensuelle, chaque jour teinté selon son ROI
    (bénéfice/perte), navigation ‹ mois ›, bilan du mois, détail des paris d'un jour au clic (/jour). Hors ROI.
    `cal=1` -> renvoie SEULEMENT la grille (rechargée par les flèches dans #cal-root)."""
    inner = web._render_calendar(ym)
    if cal:                              # navigation mensuelle : juste la grille
        return HTMLResponse(inner)
    body = f'<div class="pg-h">Calendrier</div><div id="cal-root">{inner}</div>'
    if frag:
        return HTMLResponse(body)
    return HTMLResponse(web.spa_shell("calendrier", "Calendrier", body))


@router.get("/montante", response_class=HTMLResponse)
async def montante_page(frag: int = 0) -> HTMLResponse:
    """Onglet « Montante » (fonctionnalité préparée 2026-07-24) : une montante quotidienne sur 1 pari, mise
    de départ 10 €, rejouée après chaque gain. Page premium prête à basculer sur les vraies données le jour
    de l'activation ; en attendant, elle explique le concept + affiche un aperçu (exemple). Hors ROI."""
    from app import montante as _mt
    # SIMULATION « meilleure montante » RETIRÉE (user 2026-08-07) : la page Montante ne montre QUE la montante
    # RÉELLE (et son historique réel), plus aucune vitrine simulée sur les simples foot.
    _st = _mt.state()
    # TITRE : « Montante en cours » quand une montante est active avec au moins un palier (user 2026-08-09 :
    # le badge « 🔥 Montante en cours » du hero est retiré -> l'info passe dans le titre de page).
    _mtitle = "Montante en cours" if (_st.get("active") and _st.get("palier", 0) > 0) else "Montante"
    body = (f'<div class="pg-h">{_mtitle}</div>'
            f'<div class="statsx">{web.render_montante(_st, _mt.example())}</div>')
    if frag:
        return HTMLResponse(body)
    return HTMLResponse(web.spa_shell("montante", "Montante", body))


# Page « Simulation bankroll » /mybets + tout le module mybets/CLV SUPPRIMÉS (2026-06-14) : le pari
# retenu est marqué d'une ⭐ sur les cadres (moteur d'analyse, intégré aux autres paris et aux stats).
# On garde juste la redirection douce vers l'accueil (liens /mybets encore en cache mobile -> pas de 404).
@router.get("/mybets")
async def my_bets_redirect() -> RedirectResponse:
    return RedirectResponse("/", status_code=308)


def _tennis_fav_sub(r: dict) -> str:
    # Barre « Bookmakers » RETIRÉE : la barre combinée « Cotes & chances » (web._pick_bars) porte
    # désormais les cotes ET le % de chance (total 100 %). Plus de sous-ligne dédiée.
    return ""


def _tennis_trow(r: dict, sub: str | None = None, badge: str = "", pick: bool = False) -> dict:
    """Dict _sport_row d'un match tennis (réutilisé par l'onglet Tennis ET Directs)."""
    labels = ((r["home"].split() or [""])[-1], (r["away"].split() or [""])[-1])
    # NB (audit 2026-08-10) : plus de live_won/live_lost (champs morts, jamais lus). Tennis dormant (foot-only).
    return {"tour": r["tour"].upper(), "sport": "Tennis", "icon": "🎾",
            "status": r["status"], "time": r.get("time") or "",
            "score": r.get("score") or "", "server": r.get("server"),
            "game_pts": r.get("game_pts"),
            "home": r["home"], "away": r["away"],
            "prob": r.get("hp"), "prob_labels": labels,
            "sub": _tennis_fav_sub(r) if sub is None else sub, "badge": badge, "pick": pick,
            "start_ts": r.get("start_ts"), "female": r.get("female"), "pick_kind": "confiance",
            "perle": r.get("perle"), "perle2": r.get("perle2"),
            "url": f'/app/match/{r["id"]}?tour={r["tour"]}',
            **web.bars_two_way(r.get("hp"), r.get("implied"), r.get("votes"), r["home"], r["away"])}


@router.get("/directs", response_class=HTMLResponse)
async def directs_page(
    unibet: UnibetProvider = Depends(get_unibet),
    frag: int = 0,
    sport: str = "",
) -> HTMLResponse:
    """Matchs EN DIRECT du SPORT sélectionné (sélecteur en tête, demande user 2026-07-28) — foot par défaut."""
    from app import basket, foot

    sp = sport if sport in ("foot", "tennis", "basket") else "foot"
    _nudge_settle()   # ouverture Live -> pousse le règlement en arrière-plan (throttlé global, non bloquant)
    if frag:
        cached = fragcache.get(f"panel/directs/{sp}")   # cache PAR SPORT (le sélecteur recharge par sport)
        if cached:
            return HTMLResponse(cached)

    # Live = matchs ANALYSÉS actuellement EN COURS (statut dérivé du coup d'envoi, sidecars).
    async def _live_cards(sport: str) -> list:
        out = []
        # include_background : les paris basket/tennis (arrière-plan) EN COURS doivent apparaître dans Live
        # (demande user 2026-07-29, cohérent avec « tout match de Pronos qui se joue est dans Live »). Le
        # sélecteur de sport de render_directs cantonne déjà chaque carte à son sport -> pas de fuite ailleurs.
        for d in analyses.list_for(sport, include_background=True):
            st = analyses.status_of(d)
            # STATUT piloté par UNIBET : un coup d'envoi sidecar périmé ne doit pas faire passer le
            # match en « live » s'il n'a pas commencé côté Unibet (heure fraîche / pas de score).
            lf = web.live_fields(match_select.live_state_for(sport, d.get("home"), d.get("away")), sport)
            match_select.note_live(sport, d.get("home"), d.get("away"), bool(lf.get("score")))
            # LIVE COLLANT : pendant un hoquet BREF du flux (score momentanément absent), si on a vu ce
            # match en direct il y a < 6 min et qu'il n'est PAS réglé, on le considère encore en cours
            # (sinon un match tournant depuis > seuil disparaît du Live à la moindre coupure réseau).
            has_sc = (bool(lf.get("score")) or (not analyses.is_settled(d)
                      and match_select.sticky_live(sport, d.get("home"), d.get("away"))))
            st, usdt = match_select.fresh_status(sport, d.get("home"), d.get("away"), st,
                                                 has_sc, start_iso=d.get("start"))
            if st != "inprogress":
                continue
            dt = usdt or d.get("_start_dt")
            start = dt.timestamp() if dt else None
            sid = d.get("sofa_id") or d.get("id")
            sel, odds = analyses.pick_parts(d.get("pick") or "")
            perle = {"selection": sel, "odds": odds} if (sel and odds and odds >= 1.10) else None
            if not lf.get("score"):                        # REPLI SofaScore (mort) puis LiveScore (vivant)
                lf = await match_select.fetch_sofa_live(sport, sid) or lf
                if not lf.get("score"):                    # LiveScore = notre source de scores live -> évite
                    # qu'un match démarré EN RETARD (Unibet sans feed) DISPARAISSE du Live (bug 2026-07-19
                    # Espagne-Argentine : live 80' 0-0 mais invisible car likely_finished + pas de score).
                    _lsl = await asyncio.to_thread(match_select.livescore_live_fields,
                                                   sport, d.get("home"), d.get("away"), d.get("start"))
                    if _lsl.get("score"):
                        lf = {**lf, **_lsl}
                match_select.note_live(sport, d.get("home"), d.get("away"), bool(lf.get("score")))
            # en cours sans score live : s'il a assez tourné -> il est en fait fini (Terminés du sport),
            # sinon on le GARDE en « En cours ». Sticky : un score vu très récemment évite l'éviction sur hoquet.
            if (not lf.get("score") and analyses.likely_finished(d)
                    and not match_select.sticky_live(sport, d.get("home"), d.get("away"))):
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
    # Provisoires EN COURS (demande user 2026-07-10) : ils n'ont pas de sidecar (absents de list_for) ->
    # on les ajoute au Live depuis le programme (cartes _html dorées « en cours », hors ROI). Le cache
    # live est chaud (fetch_live_odds ci-dessus) -> _programme_items détecte correctement _live.
    # GROUPÉ PAR TYPE DE PARI (comme Pronos, demande user 2026-07-20) : paris retenus en cours (play_live) et
    # provisoires en cours (prov_live), TOUS sports mélangés — le sport reste lisible via l'en-tête coloré.
    prov_live = [it for it in web._programme_items(set()) if it.get("_live")]
    play_live = ((await _live_cards("tennis")) + (await _live_cards("basket")) + (await _live_cards("foot")))
    body = web.render_directs(play_live, prov_live, sport=sp, frag=bool(frag))
    if frag:
        fragcache.put(f"panel/directs/{sp}", body, ttl=PANEL_TTL)
    return HTMLResponse(body)


@router.get("/app", response_class=HTMLResponse)
async def matches_page(
    provider: SofaScoreProvider = Depends(get_provider),
    rankings: RankingsProvider = Depends(get_rankings),
    unibet: UnibetProvider = Depends(get_unibet),
    frag: int = 0,
):
    """Onglet Tennis RETIRÉ (2026-07-20) : le filtre sport vit sur Pronos -> redirige vers l'accueil."""
    return RedirectResponse("/", status_code=307)
    # (code historique conservé mais inatteignable ; _analyst_rows tennis inline reste via _home_match_rows)
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
        match_select.note_live("tennis", d.get("home"), d.get("away"), bool(lf0.get("score")))
        has_sc = (bool(lf0.get("score")) or (not analyses.is_settled(d)
                  and match_select.sticky_live("tennis", d.get("home"), d.get("away"))))
        st, usdt = match_select.fresh_status("tennis", d.get("home"), d.get("away"), st, has_sc)
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
            match_select.note_live("tennis", d.get("home"), d.get("away"), bool(r.get("score")))
            # En cours SANS score live Unibet : s'il a assez tourné (likely_finished) -> Terminés ; sinon
            # GARDÉ en « En cours ». Sticky : un score vu très récemment évite l'éviction sur un hoquet du flux.
            if (not r.get("score") and analyses.likely_finished(d)
                    and not match_select.sticky_live("tennis", d.get("home"), d.get("away"))):
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
            "analysis": analyses.render("tennis", match_id, card_details=bool(frag)) or "",
            "streaks": amd.get("streaks"), "h2h": amd.get("h2h"),
            "form_html": "", "extra": "", "factors_html": "", "recos": "", "forms": None,
            "prediction": web.analyst_bars(o1, None, o2, votes, home=amd.get("home"), away=amd.get("away")),
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
    sr_home, sr_away = serve_return.ratings_for_match(match)
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds,
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
        deep = analyses.render("tennis", match_id, card_details=True)   # frag = dépli de carte -> épuré
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
    sr_home, sr_away = serve_return.ratings_for_match(match)
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds,
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
