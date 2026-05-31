"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app import ace_markets, elo, serve_return, set_markets, tendencies, tracking, web
from app.analysis import build_analysis, prob_from_rankings
from app.analysis import _match_winner_odds
from app.markets import (
    DEFAULT_SERVE, calibrate_to_market, evaluate_markets, extract_market_anchors,
    serve_win_pct,
)
from app.providers.unibet import _norm_name
from app.dependencies import (
    get_livescore, get_provider, get_rankings, get_unibet, matches_with_fallback,
)
from app.routers.analysis import _gather_context
from app.providers.rankings import RankingsProvider
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(tags=["Plateforme"], include_in_schema=False)

HORIZON_HOURS = 48


@router.get("/", response_class=HTMLResponse)
async def home(provider: SofaScoreProvider = Depends(get_provider)) -> HTMLResponse:
    store = tracking.load()
    picks, _ = _picks_and_finished(store)
    picks.sort(key=lambda v: v.get("edge") or 0, reverse=True)   # meilleurs edges d'abord
    return HTMLResponse(web.render_home(tracking.report(store),
                                        source=provider.breaker_status(), picks=picks[:6]))


@router.get("/app", response_class=HTMLResponse)
async def matches_page(
    provider: SofaScoreProvider = Depends(get_provider),
    rankings: RankingsProvider = Depends(get_rankings),
) -> HTMLResponse:
    """Liste des matchs à venir (ATP+WTA). Favori via SofaScore ou, à défaut, via
    les classements officiels (fonctionne même quand SofaScore bloque)."""
    store = tracking.load()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HORIZON_HOURS)
    local_now = web.to_local(now) or now
    today = local_now.date()
    fallback = False
    rows, live = [], []
    for tour in ("atp", "wta"):
        matches, src = await matches_with_fallback(tour)
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
            if hp is None:  # repli -> classements officiels par nom
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
            row = {
                "id": m.id, "tour": tour, "home": m.home.name, "away": m.away.name,
                "status": m.status,
                "time": web.fmt_local(m.start_time, with_date=False),
                "score": web.fmt_score(m.home_score, m.away_score) if m.status == "inprogress" else "",
                "fav": fav, "favp": favp, "confidence": rec.get("confidence"),
                "clickable": True,
                "_date": local_dt.date() if local_dt else None,
                "_sort": local_dt or datetime.max.replace(tzinfo=timezone.utc),
            }
            (live if m.status == "inprogress" else rows).append(row)

    live.sort(key=lambda r: r["_sort"])
    # Matchs à venir groupés par DATE (Aujourd'hui / Demain / …), triés par heure
    rows.sort(key=lambda r: r["_sort"])
    groups, seen = [], {}
    for r in rows:
        key = r["_date"]
        label = web.day_label(key, today) if key else "Date à confirmer"
        if label not in seen:
            seen[label] = []
            groups.append((label, seen[label]))
        seen[label].append(r)

    value_picks, finished = _picks_and_finished(store)
    return HTMLResponse(web.render_matches(
        groups, live=live, finished=finished, value_picks=value_picks, fallback=fallback))


def _picks_and_finished(store: dict) -> tuple[list[dict], list[dict]]:
    """Extrait du suivi : paris de confiance (value non réglées) et matchs terminés."""
    value_picks, finished = [], []
    for rec in store.values():
        res = rec.get("result")
        if not res and rec.get("value_pick"):
            v = rec["value_pick"]
            value_picks.append({
                "id": rec["match_id"], "tour": rec.get("tour", "atp"),
                "home": rec.get("home", ""), "away": rec.get("away", ""),
                "time": web.fmt_local(rec.get("start_time"), with_date=True),
                "player": v.get("player"), "odds": v.get("odds"),
                "edge": v.get("edge"), "stake": v.get("stake_pct"),
                "confidence": rec.get("confidence"),
                "_sort": rec.get("start_time") or "",
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
    return HTMLResponse(web.render_match_detail(
        analysis, winner_odds, aces=aces, tour=tour,
        home_form=home_form, away_form=away_form, h2h=h2h_rec, score=score))


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
            "Tous les paris", "matches",
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
        return HTMLResponse(web.layout("Indisponible", "matches",
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
