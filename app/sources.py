"""Sources de stats GRATUITES alternatives à SofaScore (ESPN, FotMob, Understat).

Enrichit le DOSSIER de l'analyste au scan (tools/generate_analyses.py) avec des FAITS
indépendants : forme récente avec adversaire+score, classements frais, blessures détaillées
(NBA/WNBA), H2H, xG (top-5 ligues), météo. Méthodologie « ≥2 sources » : ce bloc fournit la
source indépendante n°2 quand SofaScore est bloqué.

Endpoints (tous testés, sans clé, depuis cette machine) :
- FotMob   : https://www.fotmob.com/api/data/{matches,matchDetails}   (foot, monde entier)
- ESPN     : https://site.api.espn.com/apis/...                        (tennis ATP/WTA, NBA/WNBA)
- Understat: https://understat.com/getLeagueData/{league}/{season}     (xG, top-5 ligues)

Best-effort STRICT : timeout court, toute exception -> bloc absent, le scan ne casse JAMAIS.
Caches par PROCESSUS (le scan est un one-shot) : 1 appel par (endpoint, jour/ligue) quel que
soit le nombre de matchs.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
_T = 12.0          # timeout (s) par requête — best-effort, jamais bloquant
_GAP = 0.35        # politesse entre 2 appels d'une même rafale (scoreboards datés)

_ESPN = "https://site.api.espn.com/apis"
_FOTMOB = "https://www.fotmob.com/api/data"
_UNDERSTAT = "https://understat.com"


# ------------------------------------------------------------------ correspondance de noms
# Unibet nomme les SÉLECTIONS NATIONALES en français, FotMob/ESPN en anglais -> table de
# traduction (sinon « Corée du Sud » ne matche jamais « South Korea »). Clés déjà déaccentuées.
_FR_EN = {
    "coree du sud": "south korea", "coree du nord": "north korea",
    "tchequie": "czechia czech republic", "republique tcheque": "czechia czech republic",
    "etats-unis": "usa united states", "etats unis": "usa united states",
    "allemagne": "germany", "espagne": "spain", "belgique": "belgium",
    "pays-bas": "netherlands", "pays bas": "netherlands", "angleterre": "england",
    "ecosse": "scotland", "pays de galles": "wales", "irlande": "ireland",
    "irlande du nord": "northern ireland", "norvege": "norway", "suede": "sweden",
    "danemark": "denmark", "finlande": "finland", "islande": "iceland",
    "suisse": "switzerland", "autriche": "austria", "pologne": "poland",
    "hongrie": "hungary", "croatie": "croatia", "serbie": "serbia",
    "slovenie": "slovenia", "slovaquie": "slovakia", "grece": "greece",
    "turquie": "turkey turkiye", "ukraine": "ukraine", "italie": "italy",
    "maroc": "morocco", "algerie": "algeria", "tunisie": "tunisia",
    "egypte": "egypt", "senegal": "senegal", "cote d'ivoire": "ivory coast",
    "cameroun": "cameroon", "afrique du sud": "south africa",
    "arabie saoudite": "saudi arabia", "japon": "japan", "chine": "china",
    "australie": "australia", "nouvelle-zelande": "new zealand",
    "nouvelle zelande": "new zealand", "mexique": "mexico", "bresil": "brazil",
    "argentine": "argentina", "chili": "chile", "colombie": "colombia",
    "perou": "peru", "equateur": "ecuador", "bolivie": "bolivia",
    "jordanie": "jordan", "ouzbekistan": "uzbekistan", "jamaique": "jamaica",
    "haiti": "haiti", "cap-vert": "cape verde", "cap vert": "cape verde",
    "irak": "iraq", "iran": "iran", "qatar": "qatar", "canada": "canada",
}


# Villes/clubs francisés -> forme anglaise/locale (jeton à jeton)
_TOK_ALIAS = {"barcelone": "barcelona", "seville": "sevilla", "naples": "napoli",
              "turin": "torino", "rome": "roma", "lisbonne": "lisbon",
              "londres": "london", "genes": "genoa", "florence": "fiorentina",
              "anvers": "antwerp", "bruges": "brugge", "munich": "munchen"}


def _deacc_low(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _tok(s: str) -> set:
    """Jetons significatifs d'un nom d'équipe/joueur (sans accents, ≥3 lettres) — enrichis de la
    traduction anglaise (sélections nationales françaises, villes francisées)."""
    base = _deacc_low(s)
    toks = {t for t in re.findall(r"[a-z]+", base) if len(t) >= 3}
    en = _FR_EN.get(base)
    if en:
        toks |= {t for t in re.findall(r"[a-z]+", en) if len(t) >= 3}
    toks |= {_TOK_ALIAS[t] for t in toks if t in _TOK_ALIAS}
    return toks


def _overlap(a: set, b: set) -> bool:
    """Recouvrement de jetons, TOLÉRANT aux flexions (Barcelone/Barcelona, Sevilla/Séville) :
    égalité exacte OU même préfixe de 5 lettres (jetons ≥5)."""
    if a & b:
        return True
    return any(len(x) >= 5 and len(y) >= 5 and x[:5] == y[:5] for x in a for y in b)


def _teams_match(h1: str, a1: str, h2: str, a2: str) -> bool:
    """Vrai si {h1,a1} = {h2,a2} par recouvrement de jetons (les 2 orientations)."""
    th1, ta1, th2, ta2 = _tok(h1), _tok(a1), _tok(h2), _tok(a2)
    if not (th1 and ta1 and th2 and ta2):
        return False
    return bool((_overlap(th1, th2) and _overlap(ta1, ta2))
                or (_overlap(th1, ta2) and _overlap(ta1, th2)))


def _start_dt(iso: str):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _get_json(client, url: str, headers: dict | None = None):
    """GET JSON best-effort (None si échec). `client` = httpx.AsyncClient du scan."""
    try:
        r = await client.get(url, headers={**UA, **(headers or {})}, timeout=_T)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _fmt_day(dt) -> str:
    return dt.strftime("%d/%m") if dt else ""


# ================================================================== FOOT — FotMob
_FM_DAY: dict[str, list] = {}      # 'YYYYMMDD' -> [(home, away, match_id, league)]


async def _fotmob_day(client, ymd: str) -> list:
    if ymd in _FM_DAY:
        return _FM_DAY[ymd]
    j = await _get_json(client, f"{_FOTMOB}/matches?date={ymd}")
    out = []
    for lg in (j or {}).get("leagues") or []:
        lname = lg.get("name") or ""
        for m in lg.get("matches") or []:
            h = (m.get("home") or {}).get("longName") or (m.get("home") or {}).get("name") or ""
            a = (m.get("away") or {}).get("longName") or (m.get("away") or {}).get("name") or ""
            out.append((h, a, m.get("id"), lname))
    _FM_DAY[ymd] = out
    return out


async def _fotmob_find(client, home: str, away: str, start_iso: str):
    """matchId FotMob du match (noms + jour du coup d'envoi, ±1 jour). None si introuvable."""
    dt = _start_dt(start_iso)
    if dt is None:
        return None
    for delta in (0, -1, 1):
        ymd = (dt + timedelta(days=delta)).strftime("%Y%m%d")
        for h, a, mid, _lg in await _fotmob_day(client, ymd):
            if _teams_match(home, away, h, a):
                return mid
        await asyncio.sleep(_GAP)
    return None


def _fm_form_lines(team_form, idx: int, label: str) -> str:
    """Forme 5 derniers d'un camp depuis content.matchFacts.teamForm : « V 2-1 vs X (date) »."""
    try:
        items = team_form[idx] or []
    except (IndexError, TypeError):
        return ""
    parts = []
    lt = _tok(label)
    for it in reversed(items[-5:]):                     # le plus récent d'abord
        tt = it.get("tooltipText") or {}
        rs = {"W": "V", "D": "N", "L": "D"}.get(it.get("resultString"), it.get("resultString") or "?")
        # score orienté DU POINT DE VUE de l'équipe (« V 2-1 vs X » même à l'extérieur)
        team_home = bool(_tok(tt.get("homeTeam") or "") & lt)
        opp = tt.get("awayTeam") if team_home else tt.get("homeTeam")
        ts, osc = ((tt.get("homeScore"), tt.get("awayScore")) if team_home
                   else (tt.get("awayScore"), tt.get("homeScore")))
        day = _fmt_day(_start_dt((tt.get("utcTime") or "")))
        sc = f" {ts}-{osc}" if ts is not None and osc is not None else ""
        parts.append(f"{rs}{sc} vs {opp}" + (f" ({day})" if day else ""))
    return " ; ".join(parts)


def _fm_unavailable(lineup_side: dict, label: str) -> str:
    """Joueurs indisponibles d'un camp (si FotMob les expose pour ce match)."""
    out = []
    for key in ("unavailablePlayers", "unavailable", "absentPlayers"):
        for p in (lineup_side or {}).get(key) or []:
            nm = p.get("name") or (p.get("player") or {}).get("name") or ""
            why = p.get("reason") or p.get("unavailability") or ""
            if isinstance(why, dict):                  # FotMob : {type, expectedReturn, ...}
                t = why.get("type") or ""
                ret = why.get("expectedReturn") or ""
                why = {"injury": "blessé", "suspension": "suspendu",
                       "suspended": "suspendu"}.get(t, t)
                if ret:
                    why += f", retour : {ret}"
            if nm:
                out.append(f"{nm}" + (f" ({why})" if why else ""))
    return ", ".join(out[:6])


async def _foot_extras(client, match: dict) -> list[str]:
    home, away = match.get("home", ""), match.get("away", "")
    mid = await _fotmob_find(client, home, away, match.get("start") or "")
    if not mid:
        return []
    j = await _get_json(client, f"{_FOTMOB}/matchDetails?matchId={mid}")
    if not j:
        return []
    c = j.get("content") or {}
    mf = c.get("matchFacts") or {}
    facts: list[str] = []
    # Forme 5 derniers AVEC adversaire + score (exactement ce que la méthodo exige)
    tf = mf.get("teamForm")
    if tf:
        # l'ordre des 2 listes suit home/away du match FotMob ; on étiquette par correspondance
        gen = j.get("general") or {}
        fm_home = ((gen.get("homeTeam") or {}).get("name")) or home
        fm_away = ((gen.get("awayTeam") or {}).get("name")) or away
        l0, l1 = _fm_form_lines(tf, 0, fm_home), _fm_form_lines(tf, 1, fm_away)
        if _tok(fm_home) & _tok(home):
            fh, fa = l0, l1
        else:                       # FotMob inverse home/away vs Unibet
            fh, fa = l1, l0
        if fh:
            facts.append(f"Forme [{home}] (5 derniers) : {fh} (FotMob)")
        if fa:
            facts.append(f"Forme [{away}] (5 derniers) : {fa} (FotMob)")
    # H2H : summary = [victoires_home, nuls, victoires_away]
    h2h = c.get("h2h") or {}
    summ = h2h.get("summary")
    if isinstance(summ, list) and len(summ) == 3 and any(summ):
        gen = j.get("general") or {}
        fm_home = ((gen.get("homeTeam") or {}).get("name")) or home
        if _tok(fm_home) & _tok(home):
            w, d, l = summ
        else:
            l, d, w = summ
        facts.append(f"H2H : {home} {w} victoire(s), {d} nul(s), {away} {l} victoire(s) (FotMob)")
    # Absents (si exposés)
    lu = c.get("lineup") or {}
    for side_key, label in (("homeTeam", home), ("awayTeam", away)):
        ua = _fm_unavailable(lu.get(side_key) or {}, label)
        if ua:
            facts.append(f"Absents [{label}] : {ua} (FotMob)")
    # Météo (utile totaux/conditions)
    w = c.get("weather") or {}
    if w.get("temperature") is not None:
        desc = w.get("description") or ""
        rain = w.get("precipChance")
        facts.append(f"Météo prévue : {w['temperature']}°C, {desc}"
                     + (f", {rain}% de pluie" if rain is not None else "") + " (FotMob)")
    # Classement (si tableau de ligue)
    try:
        teams = ((c.get("table") or {}).get("teams")) or []
        pos = {}
        for t in teams:
            nm = t.get("name") or ""
            if _tok(nm) & _tok(home):
                pos[home] = (t.get("idx") or t.get("position"), t.get("pts"))
            elif _tok(nm) & _tok(away):
                pos[away] = (t.get("idx") or t.get("position"), t.get("pts"))
        if len(pos) == 2:
            (p1, pt1), (p2, pt2) = pos[home], pos[away]
            facts.append(f"Classement : {home} {p1}e ({pt1} pts) / {away} {p2}e ({pt2} pts) (FotMob)")
    except Exception:
        pass
    return facts


# ------------------------------------------------------------------ FOOT — Understat (xG)
_US_LEAGUE = {"premier league": "EPL", "laliga": "La_liga", "la liga": "La_liga",
              "bundesliga": "Bundesliga", "serie a": "Serie_A", "ligue 1": "Ligue_1"}
_US_CACHE: dict[str, dict] = {}    # league -> {team_name: [matchs (d, xG, xGA, res)]}


def _us_season(start_iso: str) -> str:
    """Saison Understat (année de DÉBUT) du match : août-déc -> année courante, sinon année-1."""
    dt = _start_dt(start_iso) or datetime.now(timezone.utc)
    return str(dt.year if dt.month >= 8 else dt.year - 1)


async def _understat_league(client, league: str, season: str) -> dict:
    key = f"{league}/{season}"
    if key in _US_CACHE:
        return _US_CACHE[key]
    j = await _get_json(client, f"{_UNDERSTAT}/getLeagueData/{league}/{season}",
                        headers={"X-Requested-With": "XMLHttpRequest",
                                 "Referer": f"{_UNDERSTAT}/league/{league}/{season}"})
    teams = {}
    for t in ((j or {}).get("teams") or {}).values():
        hist = t.get("history") or []
        teams[t.get("title") or ""] = hist
    _US_CACHE[key] = teams
    return teams


async def _foot_xg(client, match: dict) -> list[str]:
    comp = (match.get("comp") or "").lower()
    league = next((v for k, v in _US_LEAGUE.items() if k in comp), None)
    if not league:
        return []
    teams = await _understat_league(client, league, _us_season(match.get("start") or ""))
    facts = []
    for label in (match.get("home", ""), match.get("away", "")):
        lt = _tok(label)
        hist = next((h for nm, h in teams.items() if _tok(nm) & lt), None)
        if not hist:
            continue
        last5 = hist[-5:]
        try:
            xg = sum(float(m.get("xG") or 0) for m in last5) / len(last5)
            xga = sum(float(m.get("xGA") or 0) for m in last5) / len(last5)
            facts.append(f"xG [{label}] (moy. 5 derniers) : {xg:.2f} créés / {xga:.2f} concédés (Understat)")
        except (TypeError, ZeroDivisionError):
            continue
    return facts


# ================================================================== TENNIS — ESPN
_RANKS: dict[str, dict] = {}       # tour -> {nom_affiché: rang}
_TENNIS_IDX: dict[str, dict] = {}  # tour -> {jeton_nom: [(date, won, opp, score, tournoi)]}


async def _espn_rankings(client, tour: str) -> dict:
    if tour in _RANKS:
        return _RANKS[tour]
    j = await _get_json(client, f"{_ESPN}/site/v2/sports/tennis/{tour}/rankings")
    out = {}
    for rk in (j or {}).get("rankings") or []:
        for r in rk.get("ranks") or []:
            nm = ((r.get("athlete") or {}).get("displayName")) or ""
            if nm and r.get("current"):
                out.setdefault(nm, r["current"])
        if out:
            break                  # le 1er classement (Singles) suffit
    _RANKS[tour] = out
    return out


def _rank_of(ranks: dict, player: str):
    pt = _tok(player)
    best = None
    for nm, rk in ranks.items():
        common = _tok(nm) & pt
        if common and (best is None or len(common) > best[0]):
            best = (len(common), rk, nm)
    return (best[1], best[2]) if best else (None, None)


async def _tennis_results_index(client, tour: str, days: int = 14) -> dict:
    """Index {jeton: [(date, won, opp, score, tournoi)]} des résultats des `days` derniers jours,
    bâti en UNE passe de scoreboards datés (partagé entre tous les matchs du scan)."""
    if tour in _TENNIS_IDX:
        return _TENNIS_IDX[tour]
    idx: dict = {}
    seen_cids: set = set()   # le scoreboard d'UN jour renvoie TOUT le tournoi -> dédup par id
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=days)).strftime("%Y%m%d")
    # On interroge 3 jours espacés (hier, J-5, J-10) : chaque réponse contient le tournoi ENTIER,
    # donc 3 sondes couvrent les ~14 derniers jours (au lieu de 14 requêtes redondantes).
    for back in (1, 5, 10):
        ymd_q = (today - timedelta(days=back)).strftime("%Y%m%d")
        j = await _get_json(client, f"{_ESPN}/site/v2/sports/tennis/{tour}/scoreboard?dates={ymd_q}")
        for ev in (j or {}).get("events") or []:
            tname = ev.get("shortName") or ev.get("name") or ""
            for grp in ev.get("groupings") or []:
                for comp in grp.get("competitions") or []:
                    cid = comp.get("id")
                    st = (((comp.get("status") or {}).get("type") or {}).get("name")) or ""
                    if st != "STATUS_FINAL" or cid in seen_cids:
                        continue
                    seen_cids.add(cid)
                    # vraie DATE du match (pas le jour interrogé)
                    md = _start_dt(comp.get("date") or comp.get("startDate") or "")
                    ymd = md.strftime("%Y%m%d") if md else ymd_q
                    if ymd < cutoff:
                        continue
                    cps = comp.get("competitors") or []
                    if len(cps) != 2:
                        continue
                    names = [((c.get("athlete") or {}).get("displayName")) or "" for c in cps]
                    sets = [[int(ls.get("value") or 0) for ls in (c.get("linescores") or [])]
                            for c in cps]
                    score = (" ".join(f"{x}-{y}" for x, y in zip(sets[0], sets[1]))
                             if sets[0] and len(sets[0]) == len(sets[1]) else "")
                    for i, c in enumerate(cps):
                        won = bool(c.get("winner"))
                        opp = names[1 - i]
                        for t in _tok(names[i]):
                            idx.setdefault(t, []).append((ymd, won, opp, score, tname))
        await asyncio.sleep(_GAP)
    _TENNIS_IDX[tour] = idx
    return idx


def _tennis_form(idx: dict, player: str) -> tuple[str, int]:
    """(ligne de forme « ✓ vs X (tournoi) ; … », nb de matchs sur 7 jours) pour un joueur."""
    pt = _tok(player)
    seen, results = set(), []
    for t in pt:
        for r in idx.get(t, []):
            key = (r[0], r[2])
            if key not in seen:
                seen.add(key)
                results.append(r)
    results.sort(key=lambda r: r[0], reverse=True)
    week_ago = (datetime.now(timezone.utc).date() - timedelta(days=7)).strftime("%Y%m%d")
    fatigue = sum(1 for r in results if r[0] >= week_ago)
    parts = [f"{'V' if won else 'D'} vs {opp}" + (f" ({trn})" if trn else "")
             for _d, won, opp, _sc, trn in results[:5]]
    return (" ; ".join(parts), fatigue)


async def _tennis_extras(client, match: dict) -> list[str]:
    home, away = match.get("home", ""), match.get("away", "")
    circuit = (match.get("circuit") or "").upper()
    tour = "wta" if "WTA" in circuit else "atp"
    facts = []
    ranks = await _espn_rankings(client, tour)
    rh, nh = _rank_of(ranks, home)
    ra, na = _rank_of(ranks, away)
    if rh or ra:
        facts.append(f"Classement {tour.upper()} (ESPN, à jour) : "
                     f"{home} #{rh or '?'} vs {away} #{ra or '?'}")
    idx = await _tennis_results_index(client, tour)
    for label in (home, away):
        form, fatigue = _tennis_form(idx, label)
        if form:
            facts.append(f"Forme [{label}] (14 derniers jours) : {form} (ESPN)")
        if fatigue >= 3:
            facts.append(f"Fatigue [{label}] : {fatigue} matchs sur les 7 derniers jours (ESPN)")
    return facts


# ================================================================== BASKET — ESPN
_BB_INJ: dict[str, dict] = {}      # league -> {équipe: [« Joueur (statut, blessure, retour) »]}
_BB_STAND: dict[str, dict] = {}    # league -> {équipe: ligne de classement}
_BB_IDX: dict[str, dict] = {}      # league -> {jeton: [(date, won, opp, score)]}


async def _bb_injuries(client, league: str) -> dict:
    if league in _BB_INJ:
        return _BB_INJ[league]
    j = await _get_json(client, f"{_ESPN}/site/v2/sports/basketball/{league}/injuries")
    out = {}
    for team in (j or {}).get("injuries") or []:
        rows = []
        for inj in team.get("injuries") or []:
            nm = ((inj.get("athlete") or {}).get("displayName")) or ""
            det = inj.get("details") or {}
            why = det.get("type") or ""
            ret = det.get("returnDate") or ""
            st = inj.get("status") or ""
            if nm:
                rows.append(f"{nm} ({st}" + (f", {why}" if why else "")
                            + (f", retour {ret}" if ret else "") + ")")
        if rows:
            out[team.get("displayName") or ""] = rows
    _BB_INJ[league] = out
    return out


async def _bb_standings(client, league: str) -> dict:
    if league in _BB_STAND:
        return _BB_STAND[league]
    j = await _get_json(client, f"{_ESPN}/v2/sports/basketball/{league}/standings")
    out = {}
    for conf in (j or {}).get("children") or []:
        for e in ((conf.get("standings") or {}).get("entries")) or []:
            nm = ((e.get("team") or {}).get("displayName")) or ""
            stats = {s.get("name"): s.get("displayValue") for s in e.get("stats") or []}
            if nm:
                out[nm] = (f"{stats.get('wins', '?')}-{stats.get('losses', '?')}"
                           + (f", série {stats.get('streak')}" if stats.get("streak") else "")
                           + f" ({conf.get('name', '')})")
    _BB_STAND[league] = out
    return out


async def _bb_results_index(client, league: str, days: int = 10) -> dict:
    if league in _BB_IDX:
        return _BB_IDX[league]
    idx: dict = {}
    today = datetime.now(timezone.utc).date()
    for back in range(1, days + 1):
        ymd = (today - timedelta(days=back)).strftime("%Y%m%d")
        j = await _get_json(client,
                            f"{_ESPN}/site/v2/sports/basketball/{league}/scoreboard?dates={ymd}")
        for ev in (j or {}).get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            st = (((comp.get("status") or {}).get("type") or {}).get("name")) or ""
            if st != "STATUS_FINAL":
                continue
            cps = comp.get("competitors") or []
            if len(cps) != 2:
                continue
            names = [((c.get("team") or {}).get("displayName")) or "" for c in cps]
            scores = [c.get("score") for c in cps]
            for i, c in enumerate(cps):
                won = bool(c.get("winner"))
                sc = f"{scores[i]}-{scores[1 - i]}"
                for t in _tok(names[i]):
                    idx.setdefault(t, []).append((ymd, won, names[1 - i], sc))
        await asyncio.sleep(_GAP)
    _BB_IDX[league] = idx
    return idx


def _bb_team_rows(d: dict, team: str):
    tt = _tok(team)
    for nm, v in d.items():
        if _tok(nm) & tt:
            return v
    return None


async def _basket_extras(client, match: dict) -> list[str]:
    home, away = match.get("home", ""), match.get("away", "")
    comp = (match.get("comp") or "").upper()
    leagues = ["wnba", "nba"] if "WNBA" in comp else ["nba", "wnba"]
    facts = []
    for league in leagues:
        stand = await _bb_standings(client, league)
        s_h, s_a = _bb_team_rows(stand, home), _bb_team_rows(stand, away)
        if not (s_h or s_a):
            continue                                  # mauvaise ligue -> essaie l'autre
        lg = league.upper()
        if s_h and s_a:
            facts.append(f"Bilan {lg} (ESPN) : {home} {s_h} / {away} {s_a}")
        inj = await _bb_injuries(client, league)
        for label in (home, away):
            rows = _bb_team_rows(inj, label)
            if rows:
                facts.append(f"Blessés [{label}] : {' ; '.join(rows[:5])} (ESPN)")
        idx = await _bb_results_index(client, league)
        start_dt = _start_dt(match.get("start") or "")
        yday = (start_dt - timedelta(days=1)).strftime("%Y%m%d") if start_dt else ""
        for label in (home, away):
            tt = _tok(label)
            seen, results = set(), []
            for t in tt:
                for r in idx.get(t, []):
                    if (r[0], r[2]) not in seen:
                        seen.add((r[0], r[2]))
                        results.append(r)
            results.sort(key=lambda r: r[0], reverse=True)
            if results:
                line = " ; ".join(f"{'V' if w else 'D'} {sc} vs {opp}"
                                  for _d, w, opp, sc in results[:5])
                facts.append(f"Forme [{label}] (10 derniers jours) : {line} (ESPN)")
            if yday and any(r[0] == yday for r in results):
                facts.append(f"⚠️ Back-to-back [{label}] : a joué HIER (fatigue) (ESPN)")
        break
    return facts


# ================================================================== API publique
async def extras(client, sport: str, match: dict) -> str:
    """Bloc « DONNÉES MULTI-SOURCES » prêt à coller dans le dossier de l'analyste.
    '' si rien trouvé / tout en échec (le scan continue sans)."""
    try:
        if sport == "foot":
            facts = await _foot_extras(client, match)
            facts += await _foot_xg(client, match)
        elif sport == "tennis":
            facts = await _tennis_extras(client, match)
        elif sport == "basket":
            facts = await _basket_extras(client, match)
        else:
            facts = []
    except Exception:
        return ""
    if not facts:
        return ""
    return ("\n\nDONNÉES MULTI-SOURCES (ESPN / FotMob / Understat — source indépendante n°2, "
            "à CROISER avec ta recherche web ; un fait présent ici ET confirmé ailleurs = 2 sources) :\n- "
            + "\n- ".join(facts))
