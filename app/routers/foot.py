"""Routeur Foot (Coupe du Monde + grandes compétitions).

- Page HTML : /foot (proba 1X2 Elo vs cotes Unibet).
- API JSON (visible dans /docs, tag « Football ») : tableau des matchs, terminés,
  et stats complètes SofaScore par match (statistiques, incidents, compositions, h2h,
  stats d'équipe par saison).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from app import analyses, flags, foot, fragcache, match_analysis, match_select, sofa_http, sportcache, tracking, web
from app.netconst import SOFA_B
from app.config import get_settings
from app.dependencies import get_provider, get_unibet
from app.models import (
    MatchIncidents,
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

router = APIRouter(tags=["⚽ Football"])

RENDER_NET_BUDGET = 2.5  # s max d'attente réseau au rendu d'une page (sinon -> store)


async def _season(provider: SofaScoreProvider, tournament_id: int, season_id: int | None) -> int:
    sid = season_id or await provider.get_current_season_id(tournament_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="Aucune saison trouvée pour cette compétition.")
    return sid


async def _analyst_rows(sport: str) -> tuple[list[dict], list[dict]]:
    """(à-venir/en-cours, terminés) depuis les SEULS matchs analysés (sidecars). Statut dérivé du
    coup d'envoi. Cotes Unibet RAFRAÎCHIES à l'affichage (listView, 1 appel, gratuit) — SofaScore
    n'est jamais touché ici."""
    live = await match_select.fetch_live_odds(sport)
    rows, fin = [], []
    for d in analyses.list_for(sport):
        st = analyses.status_of(d)
        dt = d.get("_start_dt")
        # STATUT + HEURE pilotés par UNIBET (temps réel) : le coup d'envoi du sidecar peut être PÉRIMÉ
        # -> match affiché « live » alors qu'il n'a pas commencé. Unibet a le score live ET l'heure fraîche.
        lf = web.live_fields(match_select.live_state_for("foot", d.get("home"), d.get("away")), "foot")
        st, usdt = match_select.fresh_status("foot", d.get("home"), d.get("away"), st, bool(lf.get("score")))
        if usdt is not None:
            dt = usdt
        fresh = match_select.live_odds_for(live, d.get("home"), d.get("away"))
        o1, ox, o2 = fresh if fresh else (d.get("o1"), d.get("ox"), d.get("o2"))
        sel, odds = analyses.pick_parts(d.get("pick") or "")
        perle = {"selection": sel, "odds": odds} if (sel and odds and odds >= 1.10) else None
        base = {
            "id": d.get("sofa_id") or d.get("id"), "comp": d.get("comp"),
            "home": d.get("home", ""), "away": d.get("away", ""),
            "probs": None, "goals": None, "o1": o1, "ox": ox, "o2": o2,
            "imp": foot._devig3(o1, ox, o2) if (o1 and ox and o2) else None,
            "pick": None, "start": dt.timestamp() if dt else None,
            "votes": analyses.votes_pct(d), "perle": perle, "perle2": None,
            "perle_value": None, "pick_kind": "confiance", "sofa_ok": True,
        }
        if st != "inprogress":
            lf = {}                                         # pas en cours -> aucun champ live affiché
        elif not lf.get("score"):                           # en cours SANS score Unibet -> REPLI SofaScore
            lf = await match_select.fetch_sofa_live("foot", d.get("sofa_id")) or lf
        # Un « en cours » SANS score live Unibet : s'il a assez tourné (likely_finished) -> Terminés
        # (résultat « en attente » si pas réglé) ; sinon on le GARDE en « En cours » (sans scoreboard)
        # pour qu'il ne DISPARAISSE pas entre le coup d'envoi et la fin estimée.
        if st == "inprogress" and not lf.get("score") and analyses.likely_finished(d):
            st = "finished"
        if st == "finished":
            bdg, sco = analyses.result_chip(d)
            fin.append({**base, "status": "finished", "res_badge": bdg, "res_score": sco})
        else:
            rows.append({**base, "status": st, **lf})
    return rows, fin


@router.get("/foot", response_class=HTMLResponse, summary="Page Football (HTML)")
async def foot_page(frag: int = 0) -> HTMLResponse:
    """Matchs ANALYSÉS (à venir / en cours / terminés) — l'ancien board Elo est retiré."""
    if frag:   # panneau partagé -> cache court anti-rafale (pré-chargement SPA + refresh 45s)
        cached = fragcache.get("panel/foot")
        if cached:
            return HTMLResponse(cached)
    rows, fin = await _analyst_rows("foot")   # sidecars analysés + cotes Unibet fraîches
    body = foot.render(rows, fin, paused=sportcache.blocked(), frag=bool(frag))
    if frag:
        fragcache.put("panel/foot", body, ttl=20)
    return HTMLResponse(body)


async def _sofa(path: str):
    """GET SofaScore brut (curl_cffi) tolérant : renvoie le JSON ou None."""
    try:
        r = await sofa_http.get(SOFA_B + path)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _result_dot(wc_for_team: str) -> str:
    cls = {"W": "w", "L": "l", "D": "n"}[wc_for_team]   # 'n' = nul -> jaune (cf. légende)
    return f'<span class="dot {cls}">{ {"W":"V","L":"D","D":"N"}[wc_for_team] }</span>'


async def team_context(event_id: int, home: str, away: str, unit: str = "buts",
                       tf_home: dict | None = None, tf_away: dict | None = None) -> tuple[str, str]:
    """Renvoie (forme_html, classement_html) pour les 2 équipes (SofaScore).
    `forme_html` = section « 📈 Forme récente » FUSIONNÉE : note + 5 derniers DÉTAILLÉS
    (adversaire + score). `classement_html` = « 📊 Classement » (position/points/buts).
    `tf_home/away` = TeamForm.model_dump() (pour la note et le repli en pastilles).
    Générique foot/basket (`unit` = buts/points). Best-effort + concurrent."""
    ev = await _sofa(f"/event/{event_id}")
    e = (ev or {}).get("event") or {}
    hid = (e.get("homeTeam") or {}).get("id")
    aid = (e.get("awayTeam") or {}).get("id")
    ut = ((e.get("tournament") or {}).get("uniqueTournament") or {})
    tid, sid = ut.get("id"), (e.get("season") or {}).get("id")
    if not (hid and aid):
        return "", ""
    standings_data, h_last, a_last = await asyncio.gather(
        _sofa(f"/unique-tournament/{tid}/season/{sid}/standings/total") if (tid and sid) else _noop(),
        _sofa(f"/team/{hid}/events/last/0"),
        _sofa(f"/team/{aid}/events/last/0"))

    # Classement
    pos = {}
    for blk in (standings_data or {}).get("standings", []) or []:
        for row in blk.get("rows", []) or []:
            tid_r = (row.get("team") or {}).get("id")
            if tid_r in (hid, aid):
                pos[tid_r] = (row.get("position"), row.get("points"),
                              row.get("scoresFor"), row.get("scoresAgainst"))
    standings_html = ""
    if pos:
        def line(name, tid_):
            p = pos.get(tid_)
            if not p:
                return f'<div class="frow"><div class="fn">{web.html.escape(name)}</div><span class="dim">—</span></div>'
            position, pts, sf, sa = p
            return (f'<div class="frow"><div class="fn">{web.html.escape(name)}</div>'
                    f'<span class="dim">{position}<sup>e</sup> · {pts} pts · {sf} {unit} marqués, '
                    f'{sa} encaissés</span></div>')
        standings_html = ('<h2>📊 Classement</h2><div class="row">'
                          + line(home, hid) + line(away, aid) + '</div>')

    # 📈 Forme récente FUSIONNÉE : note (forme pré-match) + 5 derniers DÉTAILLÉS (adversaire + score).
    # Repli sur les pastilles compactes si SofaScore ne donne pas le détail des matchs.
    def team_block(name, tid_, data, tf):
        note = ""
        if (tf or {}).get("avg_rating"):
            note = (f' <span class="dim" style="font-weight:400;font-size:11px">· note '
                    f'<b>{round(tf["avg_rating"], 2)}</b>/10</span>')
        evs = [x for x in (data or {}).get("events", []) or []
               if (x.get("status") or {}).get("type") == "finished" and x.get("winnerCode") in (1, 2, 3)][-5:][::-1]
        rows = []
        for x in evs:
            ht, at = x.get("homeTeam") or {}, x.get("awayTeam") or {}
            is_home = ht.get("id") == tid_
            opp = (at if is_home else ht).get("name", "?")
            hs = (x.get("homeScore") or {}).get("current")
            as_ = (x.get("awayScore") or {}).get("current")
            wc = x.get("winnerCode")
            res = "D" if wc == 3 else ("W" if (wc == 1) == is_home else "L")
            sc = f'{hs}-{as_}' if is_home else f'{as_}-{hs}'
            rows.append(f'<div class="frow" style="padding:6px 0"><div class="ft">'
                        f'{_result_dot(res)}<span class="dim">{sc} vs {web.html.escape(opp)}</span></div></div>')
        body = "".join(rows) or (web.form_dots((tf or {}).get("form")) if (tf or {}).get("form") else "")
        if not body:
            return ""
        return (f'<div class="fm-name">{web.html.escape(name)}{note}</div>{body}')
    form_inner = team_block(home, hid, h_last, tf_home) + team_block(away, aid, a_last, tf_away)
    form_html = ""
    if form_inner:
        form_html = ('<h2>📈 Forme récente</h2>'
                     f'<div class="row">{form_inner}</div>')
    return form_html, standings_html


async def _noop():
    return None


def _unibet_over(markets, line: float):
    """Proba implicite Unibet du « Plus de {line} buts » (total du match, labels FR ou EN)."""
    for m in markets:
        ml = (m.label or "").lower()
        if "total" not in ml and "but" not in ml:   # uniquement le total du match
            continue
        if "par " in ml:                              # exclut les totaux PAR équipe
            continue
        for o in (m.outcomes or []):
            ol = (o.label or "").lower()
            if o.line == line and (ol.startswith("over") or ol.startswith("plus")) and o.implied_probability:
                return o.implied_probability
    return None


def _unibet_btts(markets):
    """Proba implicite Unibet du « les 2 équipes marquent » (BTTS = Oui) — labels FR ou EN."""
    for m in markets:
        lbl = f'{m.label or ""} {m.type or ""}'.lower()
        if ("both" in lbl and "score" in lbl) or ("deux" in lbl and "marqu" in lbl):
            for o in (m.outcomes or []):
                if (o.label or "").lower() in ("yes", "oui") and o.implied_probability:
                    return o.implied_probability
    return None


def _market_compare(label: str, model_p: float, book_imp) -> str:
    """Ligne « marché : modèle % vs book % » + flag VALUE si le modèle dépasse nettement."""
    mp = round(model_p * 100)
    if book_imp is None:
        right = '<span class="dim">cote Unibet indispo</span>'
        val = ""
    else:
        bp = round(book_imp * 100)
        val = ' <span class="badge b-val">value</span>' if (model_p - book_imp) >= 0.08 else ""
        right = f'BETSFIX <b>{mp}%</b> · <span class="dim">Unibet {bp}%</span>'
    return f'<div class="formrow"><span class="fc"><b>{label}</b>{val}</span><span class="fc">{right}</span></div>'


@router.get("/foot/match/{event_id}", response_class=HTMLResponse,
            summary="Fiche détaillée d'un match foot (prédiction + forme + H2H)")
async def foot_match(event_id: int, frag: int = 0, pk: str = "",
                     provider: SofaScoreProvider = Depends(get_provider),
                     unibet: UnibetProvider = Depends(get_unibet)) -> HTMLResponse:
    """Fiche : prédiction (issue du suivi) + analyse SofaScore (forme des 2 équipes, H2H).
    `pk` = type de pari de la carte tapée ('value' -> analyse sur la perle value, sinon confiance)."""
    if frag:   # cache partagé : même match ouvert N fois = 1 récupération
        cached = fragcache.get(f"foot/{event_id}/{pk}")
        if cached:
            return HTMLResponse(cached)
    store = tracking.load(foot.FOOT_TRACK_PATH)
    uid, rec = next(((k, r) for k, r in store.items()
                     if str(r.get("match_id")) == str(event_id)), (None, None))
    amd = analyses.meta("foot", event_id) if not rec else None   # match analysé hors store ?
    aid = uid if rec else (amd.get("id") if amd else None)        # id de l'analyse à charger
    home = away = ""
    prediction = odds_cells = when = None
    o1 = ox = o2 = None
    comp = "Football"
    if rec:
        home, away, comp = rec.get("home", ""), rec.get("away", ""), rec.get("comp") or "Football"
        when = web.fmt_local(rec.get("start_time"), with_date=True)
        o1, ox, o2 = rec.get("o1"), rec.get("ox"), rec.get("o2")
        odds_cells = [(home, o1), ("Nul", ox), (away, o2)]
        probs = [rec.get("p_home"), rec.get("p_draw"), rec.get("p_away")]
        if all(p is not None for p in probs):
            votes = ((rec.get("public_home"), rec.get("public_away"))
                     if rec.get("public_home") is not None else None)
            prediction = web.bars_foot(probs, foot._devig3(o1, ox, o2), votes, home, away)
    elif amd:   # match analysé absent du store : on prend les métadonnées du sidecar
        home, away = amd.get("home", ""), amd.get("away", "")
        comp = amd.get("comp") or "Football"
        when = web.fmt_local(amd.get("start"), with_date=True)
        o1, ox, o2 = amd.get("o1"), amd.get("ox"), amd.get("o2")
        if o1 and ox and o2:
            odds_cells = [(home, o1), ("Nul", ox), (away, o2)]
    # AUCUN appel SofaScore : une fois le match analysé, séries + H2H viennent du SIDECAR
    # (capturés au scan). La fiche est donc 100 % hors-ligne (ni SofaScore ni Unibet au refresh).
    msc = analyses.meta("foot", event_id) or {}
    streaks = msc.get("streaks")      # {home:[[name,val]…], away:[…], h2h:[…]} ou None
    h2h = msc.get("h2h")              # {home_wins, away_wins, draws} ou None
    forms = None
    # Cotes Unibet FRAÎCHES à l'affichage (listView, gratuit ; SofaScore jamais touché).
    fresh = match_select.live_odds_for(await match_select.fetch_live_odds("foot"), home, away)
    if fresh:
        o1, ox, o2 = fresh
        odds_cells = [(home, o1), ("Nul", ox), (away, o2)]
    if o1 and o2:                     # barres fiche : Unibet (fraîche) + Public (votes)
        pubv = ((rec.get("public_home"), rec.get("public_away"), rec.get("public_draw"))
                if rec and rec.get("public_home") is not None else analyses.votes_pct(msc))
        prediction = web.analyst_bars(o1, ox, o2, pubv)
    # Squelette commun aux 3 sports : 🧠 analyse, 📊 ce qui pèse (facteurs), 🎯 reco (page pleine),
    # puis contexte (classement + 5 derniers). Forme/face-à-face sont rendus par render_sport_match_detail.
    analysis_html = recos = factors_html = ""
    context = ""
    deep = analyses.render("foot", aid)           # analyse analyste (store OU sidecar)
    if deep:
        analysis_html = deep
    if rec:
        vp = rec.get("value_pick")
        value = (vp["team"], vp["odds"], vp["edge"]) if vp and vp.get("odds") else None
        probs = [rec.get("p_home"), rec.get("p_draw"), rec.get("p_away")]
        # COHÉRENCE carte/analyse : carte VALUE -> analyse sur la perle value (sinon confiance).
        pv = rec.get("perle_value")
        perle = (pv if (pk == "value" and isinstance(pv, dict) and pv.get("selection"))
                 else rec.get("perle"))
        recos = web.perle_advice(perle)        # affiché en PAGE PLEINE uniquement (cf. renderer)
        # 🧠 Analyse rédigée (gratuite, ou Claude si clé) — contexte 1X2 + verdict perle
        if all(p is not None for p in probs):
            idx = max(range(3), key=lambda k: probs[k])
            fav_h, fav_a = idx == 0, idx == 2
            brief = {
                "sport": "foot", "home": home, "away": away,
                "favorite": home if fav_h else (away if fav_a else "le match nul"),
                "underdog": away if fav_h else (home if fav_a else ""),
                "fav_prob": probs[idx],
                "fav_odds": [rec.get("o1"), rec.get("ox"), rec.get("o2")][idx],
                "confidence": rec.get("confidence"),
                "perle": perle,
                "value": ({"name": value[0], "odds": value[1], "edge": value[2]} if value else None),
                "h2h_fav": (h2h.get("home_wins") if fav_h else h2h.get("away_wins")) if (h2h and idx != 1) else None,
                "h2h_opp": (h2h.get("away_wins") if fav_h else h2h.get("home_wins")) if (h2h and idx != 1) else None,
                "public_fav": (rec.get("public_home") / 100 if fav_h and rec.get("public_home") is not None
                               else rec.get("public_away") / 100 if fav_a and rec.get("public_away") is not None
                               else None),
                "match_id": int(event_id),
            }
            # Analyse analyste déjà chargée plus haut (deep) ; sinon repli rédigé standard.
            if not analysis_html:
                analysis_html = await match_analysis.write_analysis(brief, get_settings())
    # Facteurs Elo + forme/classement live SofaScore retirés (la fiche s'appuie sur l'analyste,
    # qui contient déjà forme/H2H dans « Les faits » ; le bloc Tendances vient du sidecar).
    form_html = ""
    ctx = {"home": home or "Match", "away": away, "home_flag": flags.flag(home),
           "away_flag": flags.flag(away), "comp": comp, "when": when,
           "analysis": analysis_html, "factors_html": factors_html, "recos": recos,
           "form_html": form_html, "extra": context, "streaks": streaks,
           "prediction": prediction, "odds_cells": odds_cells, "forms": forms, "h2h": h2h,
           "back_url": "/foot", "back_label": "Foot", "sport_key": "foot",
           "links": analyses.links_html("foot", aid),
           "odds_move": web.odds_move_for("foot", home, away)}
    html = web.render_sport_match_detail(ctx, frag=bool(frag))
    if frag and (form_html or h2h or analysis_html or factors_html or context):   # cache si contenu utile
        fragcache.put(f"foot/{event_id}/{pk}", html)
    return HTMLResponse(html)




@router.get(
    "/foot/competitions",
    summary="Grandes compétitions suivies (id SofaScore -> nom)",
)
async def foot_competitions() -> dict[int, str]:
    """Liste des compétitions prises en compte (Coupe du Monde + grands championnats + C1/C3)."""
    return foot.MAJOR_TIDS


@router.get(
    "/foot/match/{event_id}/statistics",
    summary="Statistiques d'un match (possession, tirs, xG, passes, duels…)",
    response_model=MatchStatistics,
)
async def foot_statistics(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStatistics:
    try:
        return await provider.get_event_statistics(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/incidents",
    summary="Fil du match : buts, cartons, remplacements, VAR",
    response_model=MatchIncidents,
)
async def foot_incidents(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchIncidents:
    try:
        return await provider.get_event_incidents(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/lineups",
    summary="Compositions d'un match (titulaires, remplaçants, notes)",
)
async def foot_lineups(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_lineups(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/h2h",
    summary="Confrontations directes des deux équipes",
)
async def foot_h2h(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_h2h(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/pregame-form",
    summary="Forme d'avant-match : position, note, 5 derniers résultats des 2 équipes",
    response_model=PregameForm,
)
async def foot_pregame_form(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> PregameForm:
    try:
        return await provider.get_event_pregame_form(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/shotmap",
    summary="Carte des tirs avec xG par tir",
)
async def foot_shotmap(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_shotmap(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/win-probability",
    summary="Probabilité de victoire dans le temps (modèle live SofaScore)",
)
async def foot_win_probability(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_win_probability(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/momentum",
    summary="Graphe de momentum / pression du match",
)
async def foot_momentum(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_momentum(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/best-players",
    summary="Notes des joueurs + homme du match",
)
async def foot_best_players(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_event_best_players(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/odds",
    summary="Cotes SofaScore d'un match (cross-check du marché)",
    response_model=MatchOdds,
)
async def foot_odds(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchOdds:
    try:
        return await provider.get_odds(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/odds/unibet",
    summary="Cotes Unibet Belgique (tous les marchés) d'un match",
    response_model=UnibetOdds,
)
async def foot_odds_unibet(
    event_id: int,
    provider: SofaScoreProvider = Depends(get_provider),
    unibet: UnibetProvider = Depends(get_unibet),
) -> UnibetOdds:
    """Cotes Unibet Belgique (Kambi) pour un match de foot, tous marchés confondus
    (1X2, double chance, BTTS, totaux, handicaps…). Matché par noms d'équipes + date.
    Disponible pour les matchs à venir / en cours."""
    try:
        m = await provider.get_match("football", event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return await unibet.find_event_odds(
        "football", m.home.name, m.away.name, event_id, m.start_time)


@router.get(
    "/foot/match/{event_id}/votes",
    summary="Pronostics des fans (1-X-2)",
    response_model=MatchVotes,
)
async def foot_votes(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchVotes:
    try:
        return await provider.get_votes(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/match/{event_id}/streaks",
    summary="Séries en cours des deux équipes",
    response_model=MatchStreaks,
)
async def foot_streaks(
    event_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> MatchStreaks:
    try:
        return await provider.get_streaks(event_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/competition/{tournament_id}/standings",
    summary="Classement d'une compétition (forme, position, points)",
    response_model=Standings,
)
async def foot_standings(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> Standings:
    try:
        return await provider.get_standings(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/competition/{tournament_id}/top-players",
    summary="Meilleurs joueurs (buts, passes, notes, xG…) par catégorie",
)
async def foot_top_players(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_players(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/competition/{tournament_id}/top-teams",
    summary="Meilleures équipes (attaque, défense, possession…) par catégorie",
)
async def foot_top_teams(
    tournament_id: int,
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_top_teams(tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/team/{team_id}/statistics",
    summary="Statistiques d'une équipe sur une compétition (saison courante par défaut)",
    response_model=TeamSeasonStatistics,
)
async def foot_team_statistics(
    team_id: int,
    tournament_id: int = Query(..., description="Id SofaScore de la compétition (ex: 17 = Premier League)"),
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> TeamSeasonStatistics:
    try:
        return await provider.get_team_season_statistics(
            team_id, tournament_id, await _season(provider, tournament_id, season_id))
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/team/{team_id}/squad",
    summary="Effectif d'une équipe (joueurs + postes)",
)
async def foot_squad(
    team_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_team_squad(team_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/player/{player_id}",
    summary="Fiche d'un joueur (poste, équipe, taille, valeur…)",
)
async def foot_player(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> dict:
    try:
        return await provider.get_player_overview(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/player/{player_id}/statistics",
    summary="Statistiques d'un joueur sur une saison (buts, passes, xG, duels…)",
)
async def foot_player_statistics(
    player_id: int,
    tournament_id: int | None = Query(None, description="Compétition (par défaut : la plus récente avec stats)"),
    season_id: int | None = Query(None, description="Saison (par défaut : la plus récente)"),
    provider: SofaScoreProvider = Depends(get_provider),
) -> dict:
    try:
        return await provider.get_player_overall_statistics(player_id, tournament_id, season_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get(
    "/foot/player/{player_id}/image",
    summary="Photo d'un joueur",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
)
async def foot_player_image(
    player_id: int, provider: SofaScoreProvider = Depends(get_provider)
) -> Response:
    try:
        content, ctype = await provider.get_player_portrait(player_id)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return Response(content=content, media_type=ctype)
