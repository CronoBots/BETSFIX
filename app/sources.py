"""Sources de stats GRATUITES alternatives à SofaScore (ESPN, FotMob, Understat, Flashscore).

Enrichit le DOSSIER de l'analyste au scan (tools/generate_analyses.py) avec des FAITS
indépendants : forme récente avec adversaire+score, classements frais, blessures détaillées
(NBA/WNBA), H2H, xG (top-5 ligues), météo. Méthodologie « ≥2 sources » : ESPN/FotMob/Understat
fournissent la source n°2 et Flashscore (forme + face-à-face, foot/tennis/basket) la source n°3,
indépendantes, quand SofaScore est bloqué.

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
import re
import time
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


def _ov(a: str, b: str) -> int:
    """Nombre de jetons communs entre 2 noms (départage robuste, mieux que « ≥1 jeton »)."""
    return len(_tok(a) & _tok(b))


def _is_home(name: str, home: str, away: str) -> bool:
    """`name` correspond-il PLUTÔT à `home` qu'à `away` ? (meilleur recouvrement). Évite la confusion
    sur les derbies/villes partagées (Man Utd/City, Real/Atletico Madrid) où « ≥1 jeton commun » suffit
    à matcher le MAUVAIS camp."""
    return _ov(name, home) >= _ov(name, away)


def _side_of(name: str, home: str, away: str) -> str | None:
    """'home'/'away' selon le camp au PLUS de jetons communs ; None si égalité/aucun (ambigu -> on
    n'assigne pas plutôt que d'assigner au mauvais camp)."""
    sh, sa = _ov(name, home), _ov(name, away)
    return "home" if sh > sa else ("away" if sa > sh else None)


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
    for it in reversed(items[-5:]):                     # le plus récent d'abord
        tt = it.get("tooltipText") or {}
        rs = {"W": "V", "D": "N", "L": "D"}.get(it.get("resultString"), it.get("resultString") or "?")
        # score orienté DU POINT DE VUE de l'équipe (« V 2-1 vs X » même à l'extérieur). Meilleur
        # recouvrement (pas « ≥1 jeton ») -> pas de flip sur un derby dans l'historique.
        team_home = _ov(tt.get("homeTeam") or "", label) >= _ov(tt.get("awayTeam") or "", label)
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
        if _is_home(fm_home, home, away):
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
        if _is_home(fm_home, home, away):
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
            side = _side_of(t.get("name") or "", home, away)
            if side == "home":
                pos[home] = (t.get("idx") or t.get("position"), t.get("pts"))
            elif side == "away":
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
        # MEILLEUR recouvrement (pas le 1er « ≥1 jeton ») : sinon « Manchester City » pouvait renvoyer
        # « Manchester United », ou un Madrid l'autre. On prend l'équipe Understat la plus proche.
        hist, best = None, 0
        for nm, h in teams.items():
            sc = _ov(nm, label)
            if sc > best:
                hist, best = h, sc
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


def _nick(name: str) -> str:
    """Surnom DISTINCTIF d'une équipe = dernier mot (Knicks, Pelicans, Spurs, Aces…). Unique en
    NBA/WNBA -> évite la confusion « New York » / « New Orleans » (qui partagent « new »). Retire les
    suffixes parenthésés (« (F) » WNBA) pour ne pas prendre « (F) » comme surnom."""
    cleaned = re.sub(r"\([^)]*\)", " ", name or "")
    words = [w for w in re.split(r"\s+", cleaned.strip()) if w]
    return words[-1].lower() if words else ""


def _bb_team_rows(d: dict, team: str):
    """Lignes d'une équipe par SURNOM (et non par n'importe quel token commun : sinon « New York
    Knicks » matche « New Orleans Pelicans » via « new »). None si pas trouvé."""
    nick = _nick(team)
    if not nick:
        return None
    for nm, v in d.items():               # match exact du surnom (distinctif)
        if _nick(nm) == nick:
            return v
    for nm, v in d.items():               # repli : surnom présent dans le nom complet
        if nick in _tok(nm):
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
            # Lookup par SURNOM uniquement (pas tous les tokens) : « new » mélangeait New York &
            # New Orleans dans la forme. Le surnom (knicks/pelicans…) est distinctif.
            seen, results = set(), []
            for r in idx.get(_nick(label), []):
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


# ================================================================== RÈGLEMENT DE SECOURS
# Score FINAL d'un match terminé via les sources gratuites, au FORMAT EXACT attendu par
# settle_analyst.settle_pick (mêmes clés que _score_from_event). Utilisé quand SofaScore est
# bloqué : les paris se règlent quand même (sauf marchés stats : cartons/corners/HOLD1/FIRSTTO).
_SCORE_TTL = 600.0          # un appel par (source, jour) max toutes les 10 min côté app
_SCORE_CACHE: dict = {}     # clé -> (ts, data)


async def _score_cached(key, fetch):
    hit = _SCORE_CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _SCORE_TTL:
        return hit[1]
    data = await fetch()
    _SCORE_CACHE[key] = (now, data)
    return data


def _orient(n0: str, n1: str, home: str, away: str) -> int | None:
    """Indice (0/1) du compétiteur correspondant à `home`, par MEILLEUR score de jetons exacts sur
    les DEUX affectations — robuste aux noms partagés (« Tatjana Maria » vs « Maria Sakkari »).
    None si ambigu (mieux vaut NE PAS régler que régler à l'envers)."""
    t0, t1, th, ta = _tok(n0), _tok(n1), _tok(home), _tok(away)
    direct = len(t0 & th) + len(t1 & ta)
    flipped = len(t0 & ta) + len(t1 & th)
    if direct == flipped:
        return None
    return 0 if direct > flipped else 1


def _fm_score_from_match(m: dict, home: str, away: str) -> dict | None:
    """Score settle_pick depuis un match FotMob FINI (orienté selon home/away du sidecar)."""
    h, a = m.get("home") or {}, m.get("away") or {}
    if not (m.get("status") or {}).get("finished"):
        return None
    fh, fa = h.get("longName") or h.get("name") or "", a.get("longName") or a.get("name") or ""
    if not _teams_match(home, away, fh, fa):
        return None
    hs, as_ = h.get("score"), a.get("score")
    if hs is None or as_ is None:
        return None
    i_h = _orient(fh, fa, home, away)
    if i_h is None:                                  # ambigu -> on ne règle pas (jamais à l'envers)
        return None
    if i_h == 1:                                     # FotMob inverse home/away vs le sidecar
        hs, as_ = as_, hs
    return {"home": hs, "away": as_, "sets_home": None, "sets_away": None,
            "periods": {}, "first_serve": None, "label": f"{hs}-{as_}", "src": "fotmob"}


def _bb_score_from_event(ev: dict, home: str, away: str) -> dict | None:
    """Score settle_pick depuis un event basket ESPN FINAL (totaux + points par quart-temps)."""
    comp = (ev.get("competitions") or [{}])[0]
    st = (((comp.get("status") or {}).get("type") or {}).get("name")) or ""
    if st != "STATUS_FINAL":
        return None
    cps = comp.get("competitors") or []
    if len(cps) != 2:
        return None
    names = [((c.get("team") or {}).get("displayName")) or "" for c in cps]
    if not _teams_match(home, away, names[0], names[1]):
        return None
    i_h = _orient(names[0], names[1], home, away)            # oriente sur le sidecar
    if i_h is None:
        return None
    try:
        hs, as_ = int(cps[i_h].get("score")), int(cps[1 - i_h].get("score"))
    except (TypeError, ValueError):
        return None
    periods = {}
    lh = [int(x.get("value") or 0) for x in (cps[i_h].get("linescores") or [])]
    la = [int(x.get("value") or 0) for x in (cps[1 - i_h].get("linescores") or [])]
    for i, (ph, pa) in enumerate(zip(lh, la), start=1):
        periods[i] = (ph, pa)
    return {"home": hs, "away": as_, "sets_home": None, "sets_away": None,
            "periods": periods, "first_serve": None, "label": f"{hs}-{as_}", "src": "espn"}


def _tennis_score_from_comp(cps: list, home: str, away: str) -> dict | None:
    """Score settle_pick depuis une rencontre tennis ESPN FINALE (sets + jeux par set)."""
    if len(cps) != 2:
        return None
    names = [((c.get("athlete") or {}).get("displayName")) or "" for c in cps]
    if not _teams_match(home, away, names[0], names[1]):
        return None
    i_h = _orient(names[0], names[1], home, away)
    if i_h is None:
        return None
    gh = [int(x.get("value") or 0) for x in (cps[i_h].get("linescores") or [])]
    ga = [int(x.get("value") or 0) for x in (cps[1 - i_h].get("linescores") or [])]
    if not gh or len(gh) != len(ga):
        return None
    periods, sh, sa = {}, 0, 0
    for i, (g1, g2) in enumerate(zip(gh, ga), start=1):
        periods[i] = (g1, g2)
        if g1 > g2:
            sh += 1
        elif g2 > g1:
            sa += 1
    if sh == sa:                                     # pas de vainqueur lisible -> on n'invente pas
        return None
    return {"home": None, "away": None, "sets_home": sh, "sets_away": sa,
            "periods": periods, "first_serve": None,
            "label": f"{sh}-{sa} (sets)", "src": "espn"}


async def final_score(sport: str, d: dict) -> dict | None:
    """Règlement de SECOURS : score final du match `d` (sidecar : home/away/start/circuit) via
    FotMob (foot) ou ESPN (tennis ATP+WTA, basket NBA/WNBA). None si introuvable ou pas fini —
    le règlement re-tentera (SofaScore reste la voie n°1)."""
    import httpx
    home, away = d.get("home", ""), d.get("away", "")
    dt = _start_dt(d.get("start") or "")
    if not (home and away and dt):
        return None
    days = [(dt + timedelta(days=k)).strftime("%Y%m%d") for k in (0, 1, -1)]
    try:
        async with httpx.AsyncClient(timeout=_T) as client:
            if sport == "foot":
                for ymd in days:
                    j = await _score_cached(("fm", ymd),
                                            lambda y=ymd: _get_json(client, f"{_FOTMOB}/matches?date={y}"))
                    for lg in (j or {}).get("leagues") or []:
                        for m in lg.get("matches") or []:
                            sc = _fm_score_from_match(m, home, away)
                            if sc:
                                return sc
            elif sport == "basket":
                for league in ("wnba", "nba"):
                    for ymd in days:
                        j = await _score_cached(("bb", league, ymd), lambda l=league, y=ymd: _get_json(
                            client, f"{_ESPN}/site/v2/sports/basketball/{l}/scoreboard?dates={y}"))
                        for ev in (j or {}).get("events") or []:
                            sc = _bb_score_from_event(ev, home, away)
                            if sc:
                                return sc
            elif sport == "tennis":
                circuit = (d.get("circuit") or "").upper()
                tours = ("wta", "atp") if "WTA" in circuit else ("atp", "wta") if "ATP" in circuit \
                    else ("atp", "wta")
                for tour in tours:
                    for ymd in days[:2]:             # le scoreboard d'un jour couvre tout le tournoi
                        j = await _score_cached(("tn", tour, ymd), lambda t=tour, y=ymd: _get_json(
                            client, f"{_ESPN}/site/v2/sports/tennis/{t}/scoreboard?dates={y}"))
                        for ev in (j or {}).get("events") or []:
                            for grp in ev.get("groupings") or []:
                                for comp in grp.get("competitions") or []:
                                    st = (((comp.get("status") or {}).get("type") or {}).get("name")) or ""
                                    if st != "STATUS_FINAL":
                                        continue
                                    sc = _tennis_score_from_comp(comp.get("competitors") or [],
                                                                 home, away)
                                    if sc:
                                        return sc
    except Exception:
        return None
    return None


async def world_cup_extras(client, match: dict) -> str:
    """Contexte COUPE DU MONDE (ESPN `fifa.world`) : ARBITRE désigné + phase/groupe + classement du
    groupe (points, qualifs des 2 équipes). '' si match non trouvé. Sert à la méthodo CdM (cartons
    selon l'arbitre, enjeux selon le classement/qualification)."""
    home, away = match.get("home", ""), match.get("away", "")
    dt = _start_dt(match.get("start") or "")
    if not (home and away and dt):
        return ""
    base = f"{_ESPN}/site/v2/sports/soccer/fifa.world"
    eid = None
    for ymd in [(dt + timedelta(days=k)).strftime("%Y%m%d") for k in (0, -1, 1)]:
        j = await _get_json(client, f"{base}/scoreboard?dates={ymd}")
        for ev in (j or {}).get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            nm = [((c.get("team") or {}).get("displayName")) or "" for c in (comp.get("competitors") or [])]
            if len(nm) == 2 and _teams_match(home, away, nm[0], nm[1]):
                eid = ev.get("id")
                break
        if eid:
            break
    if not eid:
        return ""
    facts = []
    summ = await _get_json(client, f"{base}/summary?event={eid}")
    # phase / tour
    note = (((summ or {}).get("header") or {}).get("competitions") or [{}])[0].get("notes") or []
    phase = note[0].get("headline") if note else None
    # arbitre
    refs = ((summ or {}).get("gameInfo") or {}).get("officials") or []
    ref = next((o.get("displayName") for o in refs
                if "referee" in ((o.get("position") or {}).get("name") or "").lower()), None)
    ref = ref or (refs[0].get("displayName") if refs else None)
    # classement du groupe (l'équipe qui matche -> bon groupe)
    sj = await _get_json(client, f"{_ESPN}/v2/sports/soccer/fifa.world/standings")
    th, ta = _tok(home), _tok(away)
    for g in (sj or {}).get("children") or []:
        entries = ((g.get("standings") or {}).get("entries")) or []
        names = [((e.get("team") or {}).get("displayName")) or "" for e in entries]
        if any(_overlap(th, _tok(n)) or _overlap(ta, _tok(n)) for n in names):
            facts.append(f"Phase : {phase or g.get('name') or 'phase de groupes'} (Coupe du Monde)")
            rows = []
            for e in entries:
                v = {s.get("abbreviation"): s.get("displayValue") for s in e.get("stats", [])}
                rows.append(f"{((e.get('team') or {}).get('displayName'))} {v.get('P', '?')} pts "
                            f"(J{v.get('GP', '?')}, {v.get('W', '?')}V-{v.get('D', '?')}N-{v.get('L', '?')}D)")
            facts.append("Classement du groupe : " + " ; ".join(rows))
            break
    if not facts and phase:
        facts.append(f"Phase : {phase} (Coupe du Monde)")
    if ref:
        facts.append(f"ARBITRE désigné : {ref} (RECHERCHE sa moyenne de CARTONS/match — décisif pour "
                     f"le marché cartons)")
    if not facts:
        return ""
    return "\n\nCONTEXTE COUPE DU MONDE (ESPN) :\n- " + "\n- ".join(facts)


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
    out = ""
    if facts:
        out += ("\n\nDONNÉES MULTI-SOURCES (ESPN / FotMob / Understat — source indépendante n°2, "
                "à CROISER avec ta recherche web ; un fait présent ici ET confirmé ailleurs = 2 sources) :\n- "
                + "\n- ".join(facts))
    out += await _flashscore_block(sport, match)
    return out


async def _flashscore_block(sport: str, match: dict) -> str:
    """Bloc FLASHSCORE (forme récente + face-à-face direct) — source INDÉPENDANTE n°3.
    Best-effort : urllib synchrone déporté hors de la boucle ; toute panne -> ''."""
    fs_sport = {"foot": "football", "tennis": "tennis", "basket": "basket"}.get(sport)
    if not fs_sport:
        return ""
    h, a, st = match.get("home", ""), match.get("away", ""), match.get("start")
    try:
        from app import flashscore
        facts = await asyncio.to_thread(flashscore.prematch_facts, h, a, st, fs_sport)
        if sport == "tennis":                  # + stats de SERVICE des 2 joueurs (aces, 1er service…)
            facts = facts + await asyncio.to_thread(flashscore.serve_facts, h, a, st)
        elif sport == "foot":                  # + compositions/formations si dispo (~1 h avant le coup d'envoi)
            facts = facts + await asyncio.to_thread(flashscore.lineup_facts, h, a, st)
    except Exception:
        return ""
    if not facts:
        return ""
    return ("\n\nDONNÉES FLASHSCORE (forme, face-à-face & service — source indépendante n°3, à CROISER "
            "avec le bloc ci-dessus et ta recherche web) :\n- " + "\n- ".join(facts))
