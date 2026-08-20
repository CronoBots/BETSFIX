"""Plateforme de visionnage : pages HTML (accueil, matchs, détail match)."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

# App 100 % FOOT (tennis/basket retirés 2026-08-13) : les modules d'analyse tennis (analysis,
# ace_markets, set_markets, tendencies, serve_return, markets tennis, rankings) ne sont plus importés.
from app import accounts, analyses, fragcache, match_select, web
from app.dependencies import get_provider, get_unibet
from app.providers.sofascore import SofaScoreProvider
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

    # 2. OÙ EST L'EDGE : par sport, par TIER (confiance/value/montante), puis par cote & confiance.
    edge = (web.render_sports_breakdown(full)
            + web.render_tier_compare(full)
            + web.render_perf(analyses.perf_breakdown(since_days)))
    # BILAN (sous-onglet 1) : rentabilité globale + cadres sport (ROI + courbes).
    bilan = (
        _hero_card(full, combo)                                                    # 0. HERO : rentabilité globale
        + web.render_stats(full, combo_full=combo)                                 # 1. cadre FOOTBALL
        + _simulation_card())                                                      # 1b. cadres TENNIS/BASKET
    # ANALYSE (sous-onglet 2) : là où le modèle se prouve (edge, calibration, marchés écartés, transparence).
    analyse = (
        web.render_analysis_verdict(full)                                            # 0. VERDICT en tête (actionnable)
        + _sec("Où se trouve l'edge", "notre rendement selon la ligue et la cote jouée", edge, open=True)   # 2.
        + _sec("Fiabilité du modèle", "la confiance annoncée se vérifie-t-elle vraiment ?",   # 3.
               web.render_reliability(analyses.calibration_reliability(buckets=12))
               + web.render_calibration(cal))
        + _sec("Marchés écartés", "quels types de paris sont mis de côté, et pourquoi",  # 3b.
               web.render_exclusions(analyses.exclusions_report()))
        + _sec("Surveillance des marchés", "fiabilité et taille d'échantillon par type de pari",  # 3c.
               web.render_market_watch((cal or {}).get("by_sport")))
        + _sec("Aperçu par marché", "réussite, value et confiance par seuil (Over/Under séparés)",  # 3c-bis
               web.render_market_overview(analyses.market_overview()))
        + _sec("Débrief des pertes", "pourquoi chaque pari perdu a perdu",  # 3d.
               web.render_debrief(None))
        + _sec("Transparence", "tout ce que le modèle a observé, chiffres bruts",                  # 4.
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
    from app import foot as foot_mod                       # app 100 % FOOT (tennis/basket retirés 2026-08-13)
    from app.routers import foot as foot_r
    out = []
    try:
        frows, _ffin = await foot_r._analyst_rows("foot")
        out += [foot_mod._card(r) for r in frows]
    except Exception:
        pass
    out.sort(key=lambda x: x.get("start_ts") or 0)         # coup d'envoi le plus proche d'abord
    _HMR_CACHE["ts"], _HMR_CACHE["rows"] = _now, out
    return out


def _past_day_cards(date_iso: str) -> list:
    """Cartes d'un JOUR PASSÉ portant un VRAI pari (simple joué figé OU combiné réglé), construites
    DIRECTEMENT depuis les sidecars filtrés par date — SANS fetch d'odds live (matchs finis -> les cotes
    stockées suffisent) ni construction des ~200 autres cartes. Chargement d'un jour ~10× plus rapide
    (demande user 2026-07-19 : chargement des jours trop lent)."""
    from app import foot as foot_mod
    out = []

    def _has_bet(d: dict) -> bool:
        return ((d.get("stat_bet") or {}).get("result") in ("won", "lost", "push")
                or (d.get("combo") or {}).get("result") in ("won", "lost", "void"))

    _bg = analyses.background_sports()                     # sports en arrière-plan -> jamais sur la page des paris
    for sport in ("foot",):                                # app 100 % FOOT (tennis/basket retirés 2026-08-13)
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
    sp = sport if sport in ("foot",) else None    # app 100 % FOOT (tennis/basket retirés 2026-08-13)
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


@router.get("/crest", include_in_schema=False)
async def crest_route(name: str = ""):
    """Logo de club (test carte façon Bull) : nom -> ID FotMob (caché) -> redirection 302 vers le logo
    FotMob. 404 si introuvable/panne -> la carte retombe sur le monogramme (repli). Résolution en thread
    (httpx sync) pour ne pas bloquer la boucle asyncio."""
    from fastapi.responses import Response
    from app import crest as _crest
    tid = await asyncio.to_thread(_crest.team_id, name)
    url = _crest.logo_url(tid)
    return RedirectResponse(url, status_code=302) if url else Response(status_code=404)


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
    return '<div class="sx-card"><div class="sx-h">Combiné 1X/X2</div>' + _curve + '</div>'


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
                # SECTION « suivis indicatifs » et panneau Santé RETIRÉS du Bilan (user 2026-08-11) : le Bilan
                # ne garde que la rentabilité + les cadres. La SANTÉ DU SYSTÈME est déplacée dans ANALYSE (avec
                # les autres sections techniques : transparence, surveillance…), plus logique.
                + '</div>'                       # fin #res-bilan
                # SOUS-ONGLET 2 — ANALYSE : edge / fiabilité / marchés / transparence + SANTÉ DU SYSTÈME (privé,
                # propriétaire, chargée en AJAX). Masqué au départ.
                + f'<div id="res-analyse" class="statsx" hidden>{_analyse}'
                + '<div id="syshealth"></div>'
                + '<script>fetch("/stats/health").then(r=>r.text()).then(function(h){'
                  'if(h){document.getElementById("syshealth").innerHTML=h;}})'
                  '.catch(function(){});</script>'
                + '</div>'                       # fin #res-analyse
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


@router.get("/montante")
async def montante_page() -> RedirectResponse:
    """L'onglet « Montante » a été INTÉGRÉ AUX RÉSULTATS (user 2026-08-19) : le bilan montante (multiplicateur
    + courbe de capital + échelle des paliers) est désormais un ONGLET de /stats, comme Confiance/Value/Combiné
    (son pari du jour reste dans Pronos). On redirige les anciens liens/favoris /montante vers /stats."""
    return RedirectResponse("/stats", status_code=308)


# Page « Simulation bankroll » /mybets + tout le module mybets/CLV SUPPRIMÉS (2026-06-14) : le pari
# retenu est marqué d'une ⭐ sur les cadres (moteur d'analyse, intégré aux autres paris et aux stats).
# On garde juste la redirection douce vers l'accueil (liens /mybets encore en cache mobile -> pas de 404).
@router.get("/mybets")
async def my_bets_redirect() -> RedirectResponse:
    return RedirectResponse("/", status_code=308)


# ---- NOTIFICATIONS PUSH (PWA) « nouveau prono » (user 2026-08-16) ----------------------------------
_SW_JS = (
    "self.addEventListener('install',function(e){self.skipWaiting();});"
    "self.addEventListener('activate',function(e){e.waitUntil(self.clients.claim());});"
    "self.addEventListener('push',function(e){var d={};try{d=e.data.json();}catch(_){"
    "d={title:'BETSFIX',body:e.data?e.data.text():''};}"
    "e.waitUntil(self.registration.showNotification(d.title||'BETSFIX',{"
    "body:d.body||'',icon:'/static/icon-180.png',badge:'/static/icon-180.png',"
    "tag:d.tag||'prono',data:{url:d.url||'/'},vibrate:[80,40,80]}));});"
    "self.addEventListener('notificationclick',function(e){e.notification.close();"
    "var u=(e.notification.data&&e.notification.data.url)||'/';"
    "e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(function(cl){"
    "for(var i=0;i<cl.length;i++){if('focus' in cl[i])return cl[i].focus();}"
    "if(clients.openWindow)return clients.openWindow(u);}));});"
)


@router.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service worker (racine -> scope « / ») : reçoit les push et affiche la notification."""
    from fastapi.responses import Response
    return Response(_SW_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/push/vapid", include_in_schema=False)
async def push_vapid():
    """Clé PUBLIQUE VAPID (applicationServerKey) pour l'abonnement côté client."""
    from app import push as _push
    return {"key": _push.public_key()}


@router.post("/push/subscribe", include_in_schema=False)
async def push_subscribe(request: Request):
    from app import push as _push
    try:
        sub = await request.json()
    except Exception:
        return {"ok": False}
    return {"ok": _push.add_sub(sub or {})}


@router.post("/push/unsubscribe", include_in_schema=False)
async def push_unsubscribe(request: Request):
    from app import push as _push
    try:
        body = await request.json()
    except Exception:
        body = {}
    _push.remove_sub((body or {}).get("endpoint", ""))
    return {"ok": True}


@router.get("/directs", response_class=HTMLResponse)
async def directs_page(
    unibet: UnibetProvider = Depends(get_unibet),
    frag: int = 0,
    sport: str = "",
) -> HTMLResponse:
    """Matchs EN DIRECT du SPORT sélectionné (sélecteur en tête, demande user 2026-07-28) — foot par défaut."""
    from app import foot                                    # app 100 % FOOT (tennis/basket retirés 2026-08-13)

    sp = sport if sport in ("foot",) else "foot"
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
            o1, ox, o2 = d.get("o1"), d.get("ox"), d.get("o2")
            out.append(foot._card({
                "id": sid, "status": "inprogress", "comp": d.get("comp"),
                "home": d.get("home", ""), "away": d.get("away", ""), "probs": None,
                "goals": None, "o1": o1, "ox": ox, "o2": o2,
                "imp": foot._devig3(o1, ox, o2) if (o1 and ox and o2) else None,
                "pick": None, "start": start, "votes": analyses.votes_pct(d),
                "perle": perle, "perle2": None, "perle_value": None,
                "pick_kind": "confiance", "sofa_ok": True, **lf}))
        return out

    await match_select.fetch_live_odds("foot")   # peuple le cache score/horloge live foot
    # Provisoires EN COURS (demande user 2026-07-10) : ils n'ont pas de sidecar (absents de list_for) ->
    # on les ajoute au Live depuis le programme (cartes _html dorées « en cours », hors ROI). Le cache
    # live est chaud (fetch_live_odds ci-dessus) -> _programme_items détecte correctement _live.
    # GROUPÉ PAR TYPE DE PARI (comme Pronos, demande user 2026-07-20) : paris retenus en cours (play_live) et
    # provisoires en cours (prov_live), TOUS sports mélangés — le sport reste lisible via l'en-tête coloré.
    # PROVISOIRES retirés du produit (user 2026-08-11) : plus construits ni comptés (sinon un provisoire
    # d'hier resté « en cours » gonflait le badge Live sans rien afficher). Réversible via PROVISOIRES_ON.
    prov_live = ([it for it in web._programme_items(set()) if it.get("_live")]
                 if analyses.PROVISOIRES_ON else [])
    play_live = await _live_cards("foot")
    # PROCHAINS MATCHS À VENIR : construits DANS render_directs depuis les paris (combo/montante/simples), en
    # cartes de prono classées par type (user 2026-08-19) -> plus besoin de passer une liste `upcoming` ici.
    body = web.render_directs(play_live, prov_live, sport=sp, frag=bool(frag))
    if frag:
        fragcache.put(f"panel/directs/{sp}", body, ttl=PANEL_TTL)
    return HTMLResponse(body)
