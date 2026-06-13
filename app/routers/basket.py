"""Routeur Basket (WNBA).

- Page HTML : /basket (proba Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Basketball ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, compositions, h2h, stats d'équipe).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from app import analyses, basket, fragcache, match_analysis, match_select, sportcache, tracking, web
from app.config import get_settings
from app.dependencies import get_provider, get_unibet
from app.models import (
    MatchOdds,
    MatchStatistics,
    MatchStreaks,
    MatchVotes,
    PregameForm,
    Standings,
    TeamSeasonStatistics,
    UnibetOdds,
)
from app.providers.sofascore import ProviderError, SofaScoreProvider
from app.providers.unibet import UnibetProvider

router = APIRouter(tags=["🏀 Basketball"])

RENDER_NET_BUDGET = 2.5  # s max d'attente réseau au rendu d'une page (sinon -> store)


async def _season(provider: SofaScoreProvider, tournament_id: int, season_id: int | None) -> int:
    sid = season_id or await provider.get_current_season_id(tournament_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="Aucune saison trouvée pour cette compétition.")
    return sid


async def _analyst_rows() -> tuple[list[dict], list[dict]]:
    """(à-venir/en-cours, terminés) basket depuis les sidecars. Cotes Unibet rafraîchies à
    l'affichage (listView, gratuit) ; SofaScore jamais touché."""
    live = await match_select.fetch_live_odds("basket")
    rows, fin = [], []
    for d in analyses.list_for("basket"):
        st = analyses.status_of(d)
        dt = d.get("_start_dt")
        # STATUT + HEURE pilotés par UNIBET (temps réel) : le coup d'envoi du sidecar peut être PÉRIMÉ
        # -> match affiché « live » alors qu'il n'a pas commencé. Unibet a le score live ET l'heure fraîche.
        lf = web.live_fields(match_select.live_state_for("basket", d.get("home"), d.get("away")), "basket")
        st, usdt = match_select.fresh_status("basket", d.get("home"), d.get("away"), st,
                                             bool(lf.get("score")), start_iso=d.get("start"))
        if usdt is not None:
            dt = usdt
        fresh = match_select.live_odds_for(live, d.get("home"), d.get("away"))
        oh, oa = (fresh[0], fresh[2]) if fresh else (d.get("o1"), d.get("o2"))
        imp = basket._devig(oh, oa) if (oh and oa) else None
        sel, odds = analyses.pick_parts(d.get("pick") or "")
        perle = {"selection": sel, "odds": odds} if (sel and odds and odds >= 1.10) else None
        base = {
            "id": d.get("sofa_id") or d.get("id"), "league": (d.get("comp") or "").upper(),
            "home": d.get("home", ""), "away": d.get("away", ""),
            "model_home": None, "margin": None, "oh": oh, "oa": oa,
            "imp_home": imp[0] if imp else None, "pick": None,
            "start": dt.timestamp() if dt else None, "votes": analyses.votes_pct(d),
            "perle": perle, "perle2": None, "perle_value": None, "pick_kind": "confiance",
            "sofa_ok": True,
        }
        if st != "inprogress":
            lf = {}                                         # pas en cours -> aucun champ live affiché
        elif not lf.get("score"):                           # en cours SANS score Unibet -> REPLI SofaScore
            lf = await match_select.fetch_sofa_live("basket", d.get("sofa_id") or d.get("id")) or lf
        # Un « en cours » SANS score live Unibet : s'il a assez tourné (likely_finished) -> Terminés ;
        # sinon on le GARDE en « En cours » (sans scoreboard) pour qu'il ne DISPARAISSE pas.
        if st == "inprogress" and not lf.get("score") and analyses.likely_finished(d):
            st = "finished"
        if st == "finished":
            bdg, sco = analyses.result_chip(d)
            brd = analyses.result_board(d, "basket")     # score + détail par quart-temps (box-score)
            fin.append({**base, "status": "finished", "res_badge": bdg,
                        "res_score": brd["score"] or sco, "periods": brd["periods"]})
        else:
            rows.append({**base, "status": st, **lf})
    return rows, fin


@router.get("/basket", response_class=HTMLResponse, summary="Page Basket (HTML)")
async def basket_page(frag: int = 0) -> HTMLResponse:
    """Matchs ANALYSÉS (à venir / en cours / terminés) — l'ancien board Elo est retiré."""
    if frag:   # panneau partagé -> cache court anti-rafale (pré-chargement SPA + refresh 45s)
        cached = fragcache.get("panel/basket")
        if cached:
            return HTMLResponse(cached)
    rows, fin = await _analyst_rows()
    body = basket.render(rows, fin, paused=sportcache.blocked(), frag=bool(frag))
    if frag:
        fragcache.put("panel/basket", body, ttl=20)
    return HTMLResponse(body)


@router.get("/basket/match/{event_id}", response_class=HTMLResponse,
            summary="Fiche détaillée d'un match basket (prédiction + forme + H2H)")
async def basket_match(event_id: int, frag: int = 0, pk: str = "",
                       provider: SofaScoreProvider = Depends(get_provider),
                       unibet: UnibetProvider = Depends(get_unibet)) -> HTMLResponse:
    """Fiche : prédiction (issue du suivi) + analyse SofaScore (forme des 2 équipes, H2H).
    `pk` = type de pari de la carte tapée ('value' -> analyse sur la perle value, sinon confiance)."""
    if frag:
        cached = fragcache.get(f"basket/{event_id}/{pk}")
        if cached:
            return HTMLResponse(cached)
    store = tracking.load(basket.BASKET_TRACK_PATH)
    rec = next((r for r in store.values() if str(r.get("match_id")) == str(event_id)), None)
    amd = analyses.meta("basket", event_id) if not rec else None   # match analysé hors store ?
    home = away = ""
    prediction = odds_cells = when = None
    oh = oa = None
    comp = "Basket"
    if rec:
        home, away, comp = rec.get("home", ""), rec.get("away", ""), (rec.get("tour") or "Basket").upper()
        when = web.fmt_local(rec.get("start_time"), with_date=True)
        oh, oa = rec.get("unibet_home_odds"), rec.get("unibet_away_odds")
        odds_cells = [(home, oh), (away, oa)]
        mh = rec.get("model_home_prob")
        if mh is not None:
            votes = ((rec.get("public_home"), rec.get("public_away"))
                     if rec.get("public_home") is not None else None)
            prediction = web.bars_two_way(mh, (basket._devig(oh, oa) or (None, None))[0], votes, home, away)
    elif amd:   # match analysé absent du store : métadonnées du sidecar
        home, away = amd.get("home", ""), amd.get("away", "")
        comp = (amd.get("comp") or "Basket").upper()
        when = web.fmt_local(amd.get("start"), with_date=True)
        oh, oa = amd.get("o1"), amd.get("o2")
        if oh and oa:
            odds_cells = [(home, oh), (away, oa)]
    # AUCUN appel SofaScore : séries + H2H viennent du SIDECAR (capturés au scan).
    msc = analyses.meta("basket", event_id) or {}
    streaks = msc.get("streaks")
    h2h = msc.get("h2h")
    forms = None
    # Cotes Unibet FRAÎCHES à l'affichage (listView, gratuit ; SofaScore jamais touché).
    fresh = match_select.live_odds_for(await match_select.fetch_live_odds("basket"), home, away)
    if fresh:
        oh, oa = fresh[0], fresh[2]
        odds_cells = [(home, oh), (away, oa)]
    if oh and oa:                     # barres fiche : Unibet (fraîche) + Public (votes)
        pubv = ((rec.get("public_home"), rec.get("public_away"))
                if rec and rec.get("public_home") is not None else analyses.votes_pct(msc))
        prediction = web.analyst_bars(oh, None, oa, pubv, home=home, away=away)
    # Squelette commun aux 3 sports : 🧠 analyse, 📊 ce qui pèse (facteurs), 🎯 reco (page pleine),
    # puis contexte (écart de points + classement + 5 derniers).
    analysis_html = recos = factors_html = ""
    context = ""
    deep = analyses.render("basket", amd.get("id") if amd else event_id)   # store OU sidecar
    if deep:
        analysis_html = deep
    mh = (rec or {}).get("model_home_prob")
    if rec and mh is not None:
        # COHÉRENCE carte/analyse : carte VALUE -> analyse sur la perle value (sinon confiance).
        pv = rec.get("perle_value")
        perle = (pv if (pk == "value" and isinstance(pv, dict) and pv.get("selection"))
                 else rec.get("perle"))
        recos = web.perle_advice(perle)        # affiché en PAGE PLEINE uniquement (cf. renderer)
        p_fav = max(mh, 1 - mh)
        # 🧠 Analyse rédigée (gratuite, ou Claude si clé) — verdict piloté par la perle
        fav_h = mh >= 0.5
        _m = rec.get("margin")
        brief = {
            "sport": "basket", "home": home, "away": away,
            "favorite": home if fav_h else away, "underdog": away if fav_h else home,
            "fav_prob": p_fav,
            "fav_odds": rec.get("unibet_home_odds") if fav_h else rec.get("unibet_away_odds"),
            "confidence": rec.get("confidence"), "perle": perle,
            "value": None,
            "margin": abs(_m) if _m else None,
            "h2h_fav": (h2h.get("home_wins") if fav_h else h2h.get("away_wins")) if h2h else None,
            "h2h_opp": (h2h.get("away_wins") if fav_h else h2h.get("home_wins")) if h2h else None,
            "public_fav": (rec.get("public_home") / 100 if fav_h and rec.get("public_home") is not None
                           else rec.get("public_away") / 100 if not fav_h and rec.get("public_away") is not None
                           else None),
            "match_id": int(event_id),
        }
        # Analyse analyste déjà chargée plus haut (deep) ; sinon repli rédigé standard.
        if not analysis_html:
            analysis_html = await match_analysis.write_analysis(brief, get_settings())
    # Facteurs Elo + forme/classement live SofaScore retirés : la fiche s'appuie sur l'analyste
    # (forme/H2H dans « Les faits ») + le bloc Tendances vient du sidecar. Aucun appel SofaScore.
    form_html = ""
    ctx = {"home": home or "Match", "away": away, "home_flag": "", "away_flag": "",
           "comp": comp, "when": when, "prediction": prediction, "odds_cells": odds_cells,
           "forms": forms, "h2h": h2h, "back_url": "/basket", "form_html": form_html,
           "analysis": analysis_html, "factors_html": factors_html, "recos": recos, "extra": context,
           "streaks": streaks, "back_label": "Basket", "sport_key": "basket",
           "links": analyses.links_html("basket", amd.get("id") if amd else event_id),
           "odds_move": web.odds_move_for("basket", home, away)}
    html = web.render_sport_match_detail(ctx, frag=bool(frag))
    if frag and (form_html or h2h or analysis_html or factors_html or context):
        fragcache.put(f"basket/{event_id}/{pk}", html)
    return HTMLResponse(html)



@router.get(
    "/basket/match/{event_id}/statistics",
    summary="Statistiques d'un match (points, rebonds, 3pts, lancers, turnovers…)",
    response_model=MatchStatistics,
)
async def basket_statistics(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStatistics:
    try:
        return await provider.get_event_statistics(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/lineups",
    summary="Effectifs / cinq de départ d'un match",
)
async def basket_lineups(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_lineups(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/h2h",
    summary="Confrontations directes des deux équipes",
)
async def basket_h2h(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_h2h(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/incidents",
    summary="Scores par quart-temps + déroulé du match (brut SofaScore)",
)
async def basket_incidents(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_incidents_raw(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/pregame-form",
    summary="Forme d'avant-match : position, note, 5 derniers résultats des 2 équipes",
    response_model=PregameForm,
)
async def basket_pregame_form(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> PregameForm:
    try:
        return await provider.get_event_pregame_form(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/momentum",
    summary="Graphe de momentum / pression du match",
)
async def basket_momentum(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_momentum(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/odds",
    summary="Cotes SofaScore d'un match (cross-check du marché)",
    response_model=MatchOdds,
)
async def basket_odds(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchOdds:
    try:
        return await provider.get_odds(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/odds/unibet",
    summary="Cotes Unibet Belgique (tous les marchés) d'un match",
    response_model=UnibetOdds,
)
async def basket_odds_unibet(
    event_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> UnibetOdds:
    """Cotes Unibet Belgique (Kambi) pour un match de basket, tous marchés confondus.
    Matché par noms d'équipes + date. Disponible pour les matchs à venir / en cours."""
    try:
        m = await provider.get_match("basketball", event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return await unibet.find_event_odds(
        "basketball", m.home.name, m.away.name, event_id, m.start_time)


@router.get(
    "/basket/match/{event_id}/votes",
    summary="Pronostics des fans",
    response_model=MatchVotes,
)
async def basket_votes(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchVotes:
    try:
        return await provider.get_votes(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/match/{event_id}/streaks",
    summary="Séries en cours des deux équipes",
    response_model=MatchStreaks,
)
async def basket_streaks(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStreaks:
    try:
        return await provider.get_streaks(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/competition/{tournament_id}/standings",
    summary="Classement d'une compétition (V/D, position)",
    response_model=Standings,
)
async def basket_standings(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> Standings:
    try:
        return await provider.get_standings(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/competition/{tournament_id}/top-players",
    summary="Meilleurs joueurs (points, rebonds, passes…) par catégorie",
)
async def basket_top_players(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_players(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/competition/{tournament_id}/top-teams",
    summary="Meilleures équipes (attaque, défense…) par catégorie",
)
async def basket_top_teams(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_teams(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/team/{team_id}/statistics",
    summary="Statistiques d'une équipe WNBA sur la saison",
    response_model=TeamSeasonStatistics,
)
async def basket_team_statistics(
    team_id: int,
    tournament_id: int = Query(basket.WNBA_TID, description="Id SofaScore de la compétition (486 = WNBA)"),
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> TeamSeasonStatistics:
    try:
        return await provider.get_team_season_statistics(
            team_id, tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/team/{team_id}/squad",
    summary="Effectif d'une équipe (joueurs + postes)",
)
async def basket_squad(
    team_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_team_squad(team_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/basket/player/{player_id}",
    summary="Fiche d'un joueur (poste, équipe, taille…)",
)
async def basket_player(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_player_overview(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

# NB : pas d'agrégat saison fiable par joueur en basket chez SofaScore. Les box scores
# (points, rebonds, passes, 3pts…) sont dispos match par match via .../lineups.


@router.get(
    "/basket/player/{player_id}/image",
    summary="Photo d'un joueur",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
async def basket_player_image(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> Response:
    try:
        content, ctype = await provider.get_player_portrait(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return Response(content=content, media_type=ctype)
