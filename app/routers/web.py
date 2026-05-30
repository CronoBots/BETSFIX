"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app import elo, tracking, web
from app.analysis import build_analysis, prob_from_rankings
from app.analysis import _match_winner_odds
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
    return HTMLResponse(web.render_home(tracking.report(tracking.load()),
                                        source=provider.breaker_status()))


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
    analysis = build_analysis(
        match=match, home_matches=hm or [], away_matches=am or [],
        home_stats=hs, away_stats=as_,
        home_wins_h2h=h2h.home_wins if h2h else None,
        away_wins_h2h=h2h.away_wins if h2h else None,
        unibet=odds, elo_home=elo_home, elo_away=elo_away,
    )
    winner_odds = _match_winner_odds(odds, match) if (odds and odds.matched) else (None, None)
    return HTMLResponse(web.render_match_detail(analysis, winner_odds))


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
