"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import analyses, ace_markets, fragcache, match_analysis, match_select, serve_return, set_markets, tendencies, tracking, web, window
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


def _hero_card(full: dict, combo: dict) -> str:
    """HERO en tête de la page Stats : le CHIFFRE CLÉ (rentabilité globale, tous paris confondus) + profit
    total + une courbe d'équité GLOBALE (simples + combinés fusionnés par date). L'argument n°1, en un
    coup d'œil, avant le détail par catégorie/sport."""
    ov = full.get("overall") or {}
    cb = combo.get("overall") or combo or {}
    n_s, n_c = ov.get("settled") or 0, cb.get("n") or 0
    bets = n_s + n_c
    if not bets:
        return ""
    profit = (ov.get("profit") or 0) + (cb.get("profit") or 0)
    won = (ov.get("won") or 0) + (cb.get("won") or 0)
    roi = round(profit / bets * 100, 1)
    pct = round(won / bets * 100)

    def _deltas(pts, dts):                         # cumul -> deltas datés (pts[0]=0, pts[i+1] à dts[i])
        if not dts or not pts or len(pts) < 2:
            return []
        return [(dts[i], pts[i + 1] - pts[i]) for i in range(min(len(dts), len(pts) - 1))]
    evs = (_deltas(ov.get("points") or [], ov.get("dates") or [])
           + _deltas(cb.get("points") or [], cb.get("dates") or []))
    evs.sort(key=lambda x: x[0] or "")             # fusion chronologique simples + combinés
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
    try:
        _pb = os.path.getmtime(os.path.join(analyses._ROOT, "data", "sport_probation.json"))
    except OSError:
        _pb = 0.0
    return (len(files), max((os.path.getmtime(f) for f in files), default=0.0), _pb)


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
    _html = _home_stats_compute(since_days)
    _HOMESTATS_CACHE[since_days] = (_sig, _html)
    return _html


def _home_stats_compute(since_days: int | None = None) -> str:
    full = analyses.stats_full(since_days)
    if not (full.get("overall") or {}).get("settled"):
        return ""
    combo = analyses.combo_stats(since_days)
    cal = analyses.calibration(since_days)

    # Sections de DÉTAIL repliables (accordéon) pour raccourcir la page (demande user 2026-07-02) : la
    # VUE D'ENSEMBLE reste toujours ouverte, le reste se déplie à la demande.
    def _sec(label: str, sub: str, body: str, open: bool = False) -> str:
        return web.sx_section_collapsible(label, sub, body, open=open)

    # 2. OÙ EST L'EDGE : par sport puis par cote (mêmes données, granularité croissante).
    edge = web.render_sports_breakdown(full) + web.render_perf(analyses.perf_breakdown(since_days))
    inner = (
        _hero_card(full, combo)                                                    # 0. HERO : rentabilité globale
        + web.render_stats(full, combo_full=combo)                                 # 1. cadre FOOTBALL
        + _simulation_card()                                                       # 1b. cadres TENNIS/BASKET (juste sous le foot, demande user 2026-07-24)
        + _sec("Où se trouve l'edge", "performance par sport et par cote", edge)   # 2.
        + _sec("Fiabilité du modèle", "la confiance tient-elle ses promesses ?",   # 3.
               web.render_reliability(analyses.calibration_reliability(buckets=12))
               + web.render_calibration(cal))
        + _sec("Marchés écartés", "quels paris sont exclus, pourquoi, et quand ils reviennent",  # 3b.
               web.render_exclusions(analyses.exclusions_report()))
        + _sec("Transparence", "tout ce que le modèle a observé",                  # 4.
               web.render_volume(full, combo, cal) + web.render_volume_by_sport()))
    return f'<div class="sx"><div class="sx-body">{inner}</div></div>'


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
    if not is_past:                                        # aujourd'hui (ou futur) = vue « à venir »
        rows = [r for r in await _home_match_rows() if r.get("status") != "inprogress"]
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
async def home(provider: SofaScoreProvider = Depends(get_provider),
               frag: int = 0) -> HTMLResponse:
    """Accueil : stats principales + les matchs À VENIR uniquement (format compact, tous sports
    mélangés, par ordre de passage). Les matchs EN COURS vivent dans l'onglet 🟢 Live (demande
    utilisateur 2026-06-12 : pas de doublon accueil/live, et un live qui démarre n'a parfois pas
    encore de score -> badge « LIVE » nu peu lisible). La nav passe par le menu ☰."""
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
    rows = [r for r in all_rows if r.get("status") != "inprogress"]
    import datetime as _dt
    _today = web._sport_today().isoformat()    # jour sportif 06h→06h
    results = _past_day_cards(_today)          # paris TERMINÉS d'aujourd'hui (résultats) -> zone dédiée
    body = web.render_dashboard(rows, live_count=live_n, results=results,
                                frag=bool(frag), source=provider.breaker_status())
    if frag:
        fragcache.put("panel/home", body, ttl=PANEL_TTL)
    return HTMLResponse(body)


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
    """Section « 🔬 Simulation (arrière-plan) » : ROI SIMULÉ des sports mis en arrière-plan (tennis/basket) —
    analysés + picks tracés en continu MAIS cachés de la page des paris & non publiés (demande user
    2026-07-24). But : suivre leur ROI simulé pour décider quand les RÉINTÉGRER (réactivation manuelle).
    '' si aucun sport en arrière-plan."""
    bg = analyses.background_sports()
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
                hit_points=b.get("hit_points"), best_streak=b.get("best_streak"))   # record sur tout l'historique
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
                hit_points=c.get("hit_points"), best_streak=c.get("best_streak"))   # record sur tout l'historique
        curves = web._sport_tabs(_simple_g, _combos_g, web._prov_sport_graph(sp),   # + onglet Provisoires (user 2026-07-25)
                                 counts=(len(_pend_s), len(_pend_c), web._prov_pending_count(sp)))   # badges EN COURS
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


def _betmines_card() -> str:
    """Cadre « suivi EXTERNE » (onglet Stats) : le Double quotidien de Betmines, MESURÉ par NOS règlements —
    demande user 2026-07-23 (« même présentation que le combiné du jour »). But : vérifier leur taux de
    réussite réel avant de s'en inspirer. INFO SEULE, hors ROI — jamais mêlé à nos pronos. '' si aucun suivi."""
    import json as _json
    import os as _os
    _p = _os.path.join(web.analyses._ROOT, "data", "betmines_track.json")
    try:
        with open(_p, encoding="utf-8") as f:
            _d = _json.load(f)
    except (OSError, ValueError):
        return ""
    # DÉBUT aligné sur le premier jour des stats du site (demande user 2026-07-24 : « à partir de la première
    # stat récupérée pour les autres paris ») -> la courbe Betmines démarre à la même date que simples/combinés.
    _floor = analyses.first_stats_day()
    days = {k: v for k, v in _d.items()
            if isinstance(v, dict) and v.get("legs") and not k.startswith("_")
            and (not _floor or k >= _floor)}
    if not days:
        return ""
    import html as _h

    def _sel(leg: dict) -> str:
        ln = leg.get("line")
        if isinstance(ln, (int, float)) and "but" in str(leg.get("market", "")).lower():
            return f"Plus de {ln:g} buts" if ln > 0 else f"Moins de {abs(ln):g} buts"
        return f'{leg.get("market", "Pari")} {ln:+g}' if isinstance(ln, (int, float)) else str(leg.get("market", ""))

    # BILAN mesuré (mise plate 1, cote totale du Double).
    done = [c for c in days.values() if c.get("result") in ("won", "lost")]
    won = sum(1 for c in done if c["result"] == "won")
    pnl = sum((c.get("total_odds") or 0) - 1 if c["result"] == "won" else -1 for c in done)
    pend = len(days) - len(done)
    _pcls = " sx-pos" if pnl > 0 else (" sx-neg" if pnl < 0 else "")
    # COURBE + STATS construites EXACTEMENT comme les 2 premiers graphiques (simples/combinés) — demande user
    # 2026-07-24. Courbe de P&L CUMULÉ (points = [0, cum1, cum2, …], chronologique), ROI/réussite/cote moyenne
    # sur les Doubles RÉGLÉS. Info seule (hors ROI), comme tout le suivi Betmines.
    _pts, _acc = [0.0], 0.0
    for _day in sorted(days):
        _c = days[_day]
        if _c.get("result") not in ("won", "lost"):
            continue
        _acc += ((_c.get("total_odds") or 0) - 1) if _c["result"] == "won" else -1
        _pts.append(round(_acc, 2))
    _hit = round(100 * won / len(done)) if done else None
    _roi = round(100 * pnl / len(done)) if done else None
    _avgc = round(sum(_c.get("total_odds") or 0 for _c in done) / len(done), 2) if done else None
    # HISTORIQUE des Doubles présenté COMME les simples/combinés (demande user 2026-07-24) : liste au format
    # `_recent_bets_html`, dépliée au clic sur la courbe (bouton « Derniers Doubles ▾ »). Un Double = une
    # ligne (nom = les 2 affiches, sel = les 2 marchés, cote = cote totale). Le Double du JOUR est déjà
    # affiché en entier plus bas -> exclu de la liste. Du plus récent au plus ancien.
    _last = max(days)
    _recent = []
    for _day in sorted(days, reverse=True):
        if _day == _last:
            continue
        _c = days[_day]
        _lg = _c.get("legs") or []
        _recent.append({
            "result": _c.get("result") or "pending",
            "name": " + ".join(f'{l.get("home", "?")}–{l.get("away", "?")}' for l in _lg),
            "sel": " · ".join(_sel(l) for l in _lg),
            "cote": _c.get("total_odds"),
            "start": (_lg[0].get("start") if _lg and _lg[0].get("start") else _day + "T12:00:00Z")})
    # LIGNE W/L (chronologique) + série + sabliers ⏳ des Doubles en attente.
    _form, _streak = web._form_streak(
        [days[d].get("result") for d in sorted(days) if days[d].get("result") in ("won", "lost")])
    _curve = web.render_tracking_curve(emoji="🎲", title="Betmines", roi=_roi, hit=_hit,
                                       n=len(done), points=_pts, avg_cote=_avgc, uid="betmines",
                                       recent=_recent, more_label="Derniers Doubles",
                                       form=_form, pending=pend, streak=_streak,
                                       compact=True,   # style cadre sport (demande user 2026-07-24)
                                       hit_points=web._hit_curve([days[d].get("result") for d in sorted(days)]))
    # Le DÉTAIL du Double du jour (jambes) n'est PLUS répété ici : il est déjà affiché dans l'onglet Pronos
    # (demande user 2026-07-24 : éviter le doublon). L'onglet Stats ne garde que le SUIVI (courbe + taux + P&L).
    return (
        '<div class="sx-card"><div class="sx-h">Combiné Betmines '
        '<span>suivi externe · info seule, hors ROI</span></div>'
        '<div class="sx-data-note">Le « Double » quotidien de <b>Betmines</b> (2 jambes sûres, overs de '
        'buts sur ligues secondaires), capturé et <b>réglé par NOS sources</b> — on mesure leur taux de '
        'réussite réel avant de s\'y intéresser. Aucun impact sur nos pronos ni notre ROI.</div>'
        + _curve +
        '<div class="sx-kpis sx-kpis3">'
        f'<div class="sx-kpi"><b>{len(days)}</b><span>Doubles suivis</span></div>'
        f'<div class="sx-kpi"><b>{won}/{len(done)}</b><span>gagnés</span></div>'
        f'<div class="sx-kpi{_pcls}"><b>{pnl:+.2f}</b><span>P&amp;L simulé (mise 1)</span></div>'
        '</div></div>')


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
        body = ('<div class="pg-h">Statistiques</div>'
                '<div class="statsx">'    # scope : fond cyan (comme les onglets sport) sur TOUS les cadres
                # Filtres de période (7 / 30 / Tout) RETIRÉS (demande user 2026-07-11) : les stats affichent
                # toujours TOUT l'historique (since="" -> days=None). Vue unique, plus simple.
                + _home_stats(days)       # Football + Tennis/Basket (simulation, juste sous) + edge + calibration
                # SÉPARATEUR de groupe : tout ce qui suit est du JOUR / INDICATIF, distinct du ROI réel.
                + '<div class="sx-group">🧪 Le jour &amp; suivis indicatifs '
                  '<span>à titre informatif — hors ROI réel</span></div>'
                + _selectivity_card()     # ratio paris à jouer / abstentions du jour (rend la sélectivité visible)
                # Combiné du jour (football) DÉPLACÉ dans le « Combinés » du cadre sport concerné (foot) via
                # combo_stats.by_sport (demande user 2026-07-25) -> plus de carte/stats standalone ici.
                # Provisoires DÉPLACÉS en 3e onglet de chaque cadre sport (demande user 2026-07-25).
                + _betmines_card()        # suivi EXTERNE : le Double Betmines mesuré par nos règlements
                # Panneau SANTÉ (privé) chargé en AJAX : hors du cache commun (le fragment est mutualisé) et
                # servi UNIQUEMENT au propriétaire (route /stats/health, is_owner) -> pas de fuite du stack de
                # sources. Vide (donc invisible) pour les visiteurs. Données LIVE (ping sources) sans bloquer.
                + '<div id="syshealth"></div>'
                + '<script>fetch("/stats/health").then(r=>r.text()).then(function(h){'
                  'if(h){document.getElementById("syshealth").innerHTML=h;}})'
                  '.catch(function(){});</script>'
                + '</div>')
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
    _st = _mt.state()
    _sim = None
    if _st.get("active"):                # activée -> vraie montante + VITRINE de la meilleure (simulation)
        _sim = _mt.simulate()
    else:                                # pas activée -> la SIMULATION sur les simples foot fait la page
        _st = _mt.simulate()
    body = (f'<div class="pg-h">Montante</div>'
            f'<div class="statsx">{web.render_montante(_st, _mt.example(), sim_state=_sim)}</div>')
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

    _nudge_settle()   # ouverture Live -> pousse le règlement en arrière-plan (throttlé global, non bloquant)
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
    body = web.render_directs(play_live, prov_live, frag=bool(frag))
    if frag:
        fragcache.put("panel/directs", body, ttl=PANEL_TTL)
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
