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


# Clés FotMob (stables) -> stats de RÈGLEMENT (par équipe [home, away]).
_FM_STAT = {"ShotsOnTarget": ("sot_h", "sot_a"), "total_shots": ("shots_h", "shots_a"),
            "corners": ("corners_h", "corners_a"), "yellow_cards": ("yc_h", "yc_a"),
            "red_cards": ("rc_h", "rc_a")}


async def foot_match_stats(client, home: str, away: str, start_iso: str) -> dict | None:
    """STATS de match FOOT via FotMob (déjà source n°1 foot) : tirs cadrés / tirs / corners / cartons PAR
    ÉQUIPE -> {sot_h/a, shots_h/a, corners_h/a, cards_h/a}. Comble le règlement des marchés tirs (cadrés)
    là où Flashscore/GISMO ne couvrent pas. Cible le TOTAL du match (`content.stats.Periods.All`). None si
    introuvable. Tolérant (jamais d'exception)."""
    try:
        mid = await _fotmob_find(client, home, away, start_iso or "")
        if not mid:
            return None
        j = await _get_json(client, f"{_FOTMOB}/matchDetails?matchId={mid}")
        if not isinstance(j, dict):
            return None
        allp = (((j.get("content") or {}).get("stats") or {}).get("Periods") or {}).get("All") or {}
        out: dict = {}
        for grp in (allp.get("stats") or []):
            for it in (grp.get("stats") or []) if isinstance(grp, dict) else []:
                k, v = it.get("key"), it.get("stats")
                if k in _FM_STAT and isinstance(v, list) and len(v) == 2 and _FM_STAT[k][0] not in out:
                    try:
                        out[_FM_STAT[k][0]], out[_FM_STAT[k][1]] = int(v[0]), int(v[1])
                    except (TypeError, ValueError):
                        pass
        if "yc_h" in out:                                # marché CARTONS = jaunes + rouges
            out["cards_h"] = out.get("yc_h", 0) + out.get("rc_h", 0)
            out["cards_a"] = out.get("yc_a", 0) + out.get("rc_a", 0)
        # GARDE anti-faux-zéros : un match NON couvert par FotMob (ligues mineures, mauvais mid) renvoie une
        # structure vide -> toutes les stats à 0. Ne JAMAIS injecter ces zéros (ils écraseraient les vraies
        # stats du cache Flashscore/GISMO). Un vrai match a forcément des tirs -> si tirs tous nuls, on
        # considère la donnée ABSENTE. (Cas vécu : Ceará-Avaí, cartons réels 6 -> FotMob 0/0 aurait dé-réglé.)
        if (out.get("sot_h", 0) + out.get("sot_a", 0)
                + out.get("shots_h", 0) + out.get("shots_a", 0)) == 0:
            return None
        return out or None
    except Exception:
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
        _so = _side_of(fm_home, home, away)   # None si ambigu (derby/homonyme) -> on n'affiche PAS
        if _so is not None:
            fh, fa = (l0, l1) if _so == "home" else (l1, l0)   # FotMob inverse parfois home/away vs Unibet
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
        _so = _side_of(fm_home, home, away)   # None si ambigu -> on n'affiche pas le H2H (anti-inversion)
        if _so is not None:
            w, d, l = summ if _so == "home" else summ[::-1]
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
    # INSIGHTS OPTA (FotMob) : faits décisifs pré-match prêts à l'emploi (séries, H2H, formes, clean
    # sheets, BTTS récents…) -> contexte FORT pour l'analyse, sur TOUTES les ligues (≠ Understat top-5).
    _gen = (j or {}).get("general") or {}
    _id2lbl = {((_gen.get("homeTeam") or {}).get("id")): (home if _is_home(((_gen.get("homeTeam") or {})
               .get("name")) or home, home, away) else away),
               ((_gen.get("awayTeam") or {}).get("id")): (away if _is_home(((_gen.get("homeTeam") or {})
               .get("name")) or home, home, away) else home)}
    for ins in (mf.get("insights") or [])[:7]:
        txt = (ins.get("text") or "").strip()
        if not txt:
            continue
        lbl = _id2lbl.get(ins.get("teamId"))
        # préfixe l'équipe si l'insight la concerne mais ne la nomme pas dans le texte
        if lbl and lbl.split()[0].lower() not in txt.lower() and ins.get("type") == "team":
            txt = f"[{lbl}] {txt}"
        facts.append(f"Opta : {txt} (FotMob)")
    # BUTEUR CLÉ par équipe (top scorer du tournoi/saison) : buts + passes décisives + tirs cadrés.
    ts = mf.get("topScorers") or {}
    for key, label in (("homePlayer", home), ("awayPlayer", away)):
        p = ts.get(key) or {}
        sp = p.get("stats") or {}
        nm = p.get("fullName") or p.get("lastName")
        if nm and sp.get("goals") is not None:
            facts.append(f"Buteur clé [{label}] : {nm} — {sp.get('goals')} but(s), "
                         f"{sp.get('goalAssist', 0)} passe(s) déc., {sp.get('ontargetScoringAtt', 0)} "
                         f"tir(s) cadré(s) (FotMob)")
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


# SURFACE du tournoi (facteur n°1 au tennis, non fourni par les sources live) : table des tournois/lieux
# connus + repli mots-clés. La surface conditionne fortement le résultat (un spécialiste terre battue est
# diminué sur gazon) -> on la donne EXPLICITEMENT à l'analyste pour qu'il pondère la spécialisation.
_TENNIS_SURFACE = {
    # gazon
    "wimbledon": "Gazon", "halle": "Gazon", "queen": "Gazon", "hertogenbosch": "Gazon", "eastbourne": "Gazon",
    "mallorca": "Gazon", "newport": "Gazon", "nottingham": "Gazon", "birmingham": "Gazon", "bad homburg": "Gazon",
    # terre battue
    "roland": "Terre battue", "french open": "Terre battue", "monte": "Terre battue", "rome": "Terre battue",
    "madrid": "Terre battue", "barcelona": "Terre battue", "hamburg": "Terre battue", "munich": "Terre battue",
    "estoril": "Terre battue", "geneva": "Terre battue", "kitzbuhel": "Terre battue", "gstaad": "Terre battue",
    "bucharest": "Terre battue", "umag": "Terre battue", "bastad": "Terre battue", "cordoba": "Terre battue",
    "buenos aires": "Terre battue", "rio": "Terre battue", "santiago": "Terre battue", "stuttgart": "Terre battue",
    # dur (extérieur)
    "australian open": "Dur", "us open": "Dur", "indian wells": "Dur", "miami": "Dur", "cincinnati": "Dur",
    "shanghai": "Dur", "beijing": "Dur", "tokyo": "Dur", "dubai": "Dur", "doha": "Dur", "acapulco": "Dur",
    "toronto": "Dur", "montreal": "Dur", "washington": "Dur", "winston": "Dur", "chengdu": "Dur",
    # dur indoor
    "paris": "Dur (indoor)", "bercy": "Dur (indoor)", "vienna": "Dur (indoor)", "basel": "Dur (indoor)",
    "metz": "Dur (indoor)", "stockholm": "Dur (indoor)", "antwerp": "Dur (indoor)", "turin": "Dur (indoor)",
    "rotterdam": "Dur (indoor)", "marseille": "Dur (indoor)", "montpellier": "Dur (indoor)",
}


def _surface_hint(*names) -> str | None:
    """Surface déduite du nom du tournoi/ville. Repli mots-clés (grass/clay/hard…). None si inconnu."""
    for name in names:
        n = (name or "").lower()
        for kw, su in (("grass", "Gazon"), ("gazon", "Gazon"), ("clay", "Terre battue"),
                       ("terre", "Terre battue"), ("indoor hard", "Dur (indoor)"), ("hard", "Dur"), ("dur", "Dur")):
            if kw in n:
                return su
        for k, su in _TENNIS_SURFACE.items():
            if k in n:
                return su
    return None


async def _tennis_extras(client, match: dict) -> list[str]:
    home, away = match.get("home", ""), match.get("away", "")
    # Le circuit (WTA/ATP) n'est plus fourni de façon fiable (champ 'circuit' ex-SofaScore
    # vide, 'comp' = ville type « Berlin »). On le DÉDUIT : on cherche les joueurs dans les
    # DEUX classements ESPN (cachés -> gratuit) et on garde celui qui les place.
    hint = (match.get("circuit") or match.get("comp") or "").upper()
    cand = ["wta"] if "WTA" in hint else ["atp"] if "ATP" in hint else ["wta", "atp"]
    tour, ranks, rh, ra = None, {}, None, None
    for t in cand:
        rk = await _espn_rankings(client, t)
        a, _ = _rank_of(rk, home)
        b, _ = _rank_of(rk, away)
        if a or b:
            tour, ranks, rh, ra = t, rk, a, b
            break
    if tour is None:                       # aucun joueur placé : repli sur le 1er candidat
        tour = cand[0]
        ranks = await _espn_rankings(client, tour)
        rh, _ = _rank_of(ranks, home)
        ra, _ = _rank_of(ranks, away)
    facts = []
    if rh or ra:
        facts.append(f"Classement {tour.upper()} (ESPN, à jour) : "
                     f"{home} #{rh or '?'} vs {away} #{ra or '?'}")
    idx = await _tennis_results_index(client, tour)
    # SURFACE (facteur n°1) : déduite du NOM DE TOURNOI des matchs récents ESPN (fiable, ≠ la ville 'comp'
    # ambiguë : « Londres » = Wimbledon/gazon). L'analyste pondère alors la spécialisation surface.
    _trns = [r[4] for label in (home, away) for t in _tok(label)
             for r in idx.get(t, []) if len(r) > 4 and r[4]]
    surf = _surface_hint(*_trns, match.get("comp", ""), match.get("name", ""))
    if surf:
        head = [f"Surface : {surf} — pondérer la SPÉCIALISATION surface des joueurs (un même joueur peut "
                f"être bien plus fort/faible sur cette surface que ne le dit son classement)."]
        # BILAN PAR SURFACE des 2 joueurs (TennisExplorer — gratuit, à jour) : le vrai niveau surface.
        try:
            from app import tennisexplorer
            head += await tennisexplorer.surface_facts(client, home, away, surf)
        except Exception:
            pass
        facts = head + facts       # surface + bilans surface en TÊTE (facteur n°1 au tennis)
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


async def first_goal_side(d: dict) -> str | None:
    """Côté ayant marqué le PREMIER but du match (FotMob events) : 'HOME' / 'AWAY', ou '' si AUCUN but
    (0-0). None si indisponible -> le règlement re-tentera (jamais de devinette). Sert au marché
    « Premier but <équipe> »."""
    import httpx
    home, away = d.get("home", ""), d.get("away", "")
    if not (home and away):
        return None
    try:
        async with httpx.AsyncClient(timeout=_T) as client:
            mid = await _fotmob_find(client, home, away, d.get("start") or "")
            if not mid:
                return None
            j = await _get_json(client, f"{_FOTMOB}/matchDetails?matchId={mid}")
    except Exception:
        return None
    ev = (((j or {}).get("content") or {}).get("matchFacts") or {}).get("events") or {}
    evs = ev.get("events") if isinstance(ev, dict) else ev
    if not isinstance(evs, list):
        return None
    goals = [e for e in evs if isinstance(e, dict) and e.get("type") == "Goal" and e.get("isHome") is not None]
    if not goals:
        return ""                       # aucun but -> 0-0 (le caller vérifie le score final pour confirmer)
    goals.sort(key=lambda e: (e.get("time") if isinstance(e.get("time"), (int, float)) else 999))
    # FotMob : home/away des events suit l'ordre FotMob -> on réaligne sur Unibet (home/away du sidecar).
    gen = (j or {}).get("general") or {}
    fm_home = ((gen.get("homeTeam") or {}).get("name")) or home
    first_is_home = bool(goals[0].get("isHome"))
    if not _is_home(fm_home, home, away):      # FotMob inverse home/away vs Unibet
        first_is_home = not first_is_home
    return "HOME" if first_is_home else "AWAY"


_BB_COMBO = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",),         # combinés points/rebonds/passes
             "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"),
             "PRA": ("PTS", "REB", "AST")}


def _bb_player_stat(summary: dict, qtok: set, stat: str):
    """Valeur d'une stat (PTS/REB/AST OU combiné PR/PA/RA/PRA = SOMME) pour LE joueur dont les jetons
    `qtok` sont TOUS dans le nom. Matching STRICT : valeur SEULEMENT si UN SEUL joueur correspond (sinon
    None -> jamais de faux règlement sur une homonymie)."""
    wanted = _BB_COMBO.get(stat, (stat,))
    bx = (summary or {}).get("boxscore") or {}
    per_player: dict = {}
    for team in bx.get("players") or []:
        for grp in team.get("statistics") or []:
            labels = grp.get("labels") or grp.get("names") or []
            idxs = {lb: labels.index(lb) for lb in wanted if lb in labels}
            if len(idxs) != len(wanted):
                continue
            for ath in grp.get("athletes") or []:
                nm = ((ath.get("athlete") or {}).get("displayName")) or ""
                if not (qtok and qtok <= _tok(nm)):
                    continue
                stats = ath.get("stats") or []
                tot, ok = 0, True
                for lb in wanted:
                    try:
                        tot += int(str(stats[idxs[lb]]).strip())
                    except (ValueError, TypeError, IndexError):
                        ok = False
                if ok:
                    per_player[nm] = tot
    return list(per_player.values())[0] if len(per_player) == 1 else None


async def basket_player_stat(d: dict, player_query: str, label: str):
    """Stat (PTS/REB/AST) d'un JOUEUR de basket via le box-score ESPN (WNBA/NBA). None si match non
    fini/introuvable OU joueur ambigu (matching STRICT). Sert aux props joueur (« X plus de 25.5 points »)."""
    import httpx
    home, away = d.get("home", ""), d.get("away", "")
    dt = _start_dt(d.get("start") or "")
    qtok = _tok(player_query)
    if not (home and away and dt and qtok):
        return None
    days = [(dt + timedelta(days=k)).strftime("%Y%m%d") for k in (0, 1, -1)]
    try:
        async with httpx.AsyncClient(timeout=_T) as cl:
            for league in ("wnba", "nba"):
                for ymd in days:
                    j = await _get_json(cl, f"{_ESPN}/site/v2/sports/basketball/{league}/scoreboard?dates={ymd}")
                    for ev in (j or {}).get("events") or []:
                        comp = (ev.get("competitions") or [{}])[0]
                        nm = [((c.get("team") or {}).get("displayName")) or "" for c in (comp.get("competitors") or [])]
                        if len(nm) == 2 and _teams_match(home, away, nm[0], nm[1]):
                            st = (((ev.get("status") or {}).get("type") or {}).get("name")) or ""
                            if st != "STATUS_FINAL":
                                return None
                            s = await _get_json(cl,
                                                f"{_ESPN}/site/v2/sports/basketball/{league}/summary?event={ev.get('id')}")
                            return _bb_player_stat(s, qtok, label)
    except Exception:
        return None
    return None


_FB_STAT_KEYS = {                       # stat analyste -> clé(s) FotMob playerStats (Opta) à SOMMER
    "SAVES": ("saves",), "ASSISTS": ("assists",), "SOT": ("ShotsOnTarget",),
    "SHOTS": ("ShotsOnTarget", "ShotsOffTarget"), "TACKLES": ("matchstats.headers.tackles",),
    "FOULS": ("fouls",), "PASSES": ("AccuratePasses", "accurate_passes"),
}


async def foot_player_stat(d: dict, player_query: str, stat: str, side: str | None = None):
    """Stat OPTA d'un JOUEUR de foot via FotMob playerStats (gratuit, Opta-powered). stat ∈ _FB_STAT_KEYS.
    `side` (HOME/AWAY) : si fourni SANS nom de joueur -> agrège l'équipe (ex. arrêts du GARDIEN de l'équipe).
    Matching de nom STRICT sinon (un seul joueur -> valeur ; ambigu/introuvable -> None, jamais de faux)."""
    import httpx
    home, away = d.get("home", ""), d.get("away", "")
    want = _FB_STAT_KEYS.get(stat)
    qtok = _tok(player_query) if player_query else set()
    if not (home and away and want and (qtok or side)):
        return None
    try:
        async with httpx.AsyncClient(timeout=_T) as cl:
            mid = await _fotmob_find(cl, home, away, d.get("start") or "")
            if not mid:
                return None
            j = await _get_json(cl, f"{_FOTMOB}/matchDetails?matchId={mid}")
    except Exception:
        return None
    cont = (j or {}).get("content") or {}
    ps = cont.get("playerStats") or {}
    if not isinstance(ps, dict):
        return None

    def _player_side(pl):                                # équipe du joueur via teamName (fiable)
        tn = pl.get("teamName") or ""
        oh, oa = _ov(tn, home), _ov(tn, away)
        return "HOME" if oh > oa else ("AWAY" if oa > oh else None)

    def _val(pl):
        tot, got = 0, False
        for g in pl.get("stats") or []:
            items = g.get("stats") or {}
            for st in (items.values() if isinstance(items, dict) else []):
                if isinstance(st, dict) and st.get("key") in want:
                    v = (st.get("stat") or {}).get("value")
                    if isinstance(v, (int, float)):
                        tot += v; got = True
        return tot if got else None

    found = []
    for pid, pl in ps.items():
        nm = pl.get("name") or ""
        if qtok:
            if not (qtok <= _tok(nm)):
                continue
        elif side:
            if _player_side(pl) != side:
                continue
        v = _val(pl)
        if v is not None:
            found.append(v)
    if not found:
        return None
    if qtok:
        return found[0] if len(found) == 1 else None     # nom STRICT : un seul joueur
    return sum(found)                                     # agrégation équipe (gardien/total)


async def first_scorer(d: dict) -> str | None:
    """Nom du PREMIER BUTEUR du match (FotMob events). '' si AUCUN but (0-0). None si indisponible ->
    le règlement re-tentera. Sert au marché « Premier buteur <joueur> » (matching STRICT côté caller)."""
    import httpx
    home, away = d.get("home", ""), d.get("away", "")
    if not (home and away):
        return None
    try:
        async with httpx.AsyncClient(timeout=_T) as cl:
            mid = await _fotmob_find(cl, home, away, d.get("start") or "")
            if not mid:
                return None
            j = await _get_json(cl, f"{_FOTMOB}/matchDetails?matchId={mid}")
    except Exception:
        return None
    ev = (((j or {}).get("content") or {}).get("matchFacts") or {}).get("events") or {}
    evs = ev.get("events") if isinstance(ev, dict) else ev
    if not isinstance(evs, list):
        return None
    goals = [e for e in evs if isinstance(e, dict) and e.get("type") == "Goal"]
    if not goals:
        return ""
    goals.sort(key=lambda e: (e.get("time") if isinstance(e.get("time"), (int, float)) else 999))
    return ((goals[0].get("player") or {}).get("name")) or None


async def player_scored_or_assisted(d: dict, player_query: str) -> str | None:
    """« <joueur> marque OU passe décisive » via les events FotMob (buts = buteur `player.name` +
    passeur `assistStr`). 'won' si le joueur a marqué ou passé, 'lost' sinon (0 but OU non impliqué),
    None si events indisponibles -> le règlement re-tentera. Matching STRICT par jetons (aucun faux)."""
    import httpx
    home, away = d.get("home", ""), d.get("away", "")
    qtok = _tok(player_query) if player_query else set()
    if not (home and away and qtok):
        return None
    try:
        async with httpx.AsyncClient(timeout=_T) as cl:
            mid = await _fotmob_find(cl, home, away, d.get("start") or "")
            if not mid:
                return None
            j = await _get_json(cl, f"{_FOTMOB}/matchDetails?matchId={mid}")
    except Exception:
        return None
    ev = (((j or {}).get("content") or {}).get("matchFacts") or {}).get("events") or {}
    evs = ev.get("events") if isinstance(ev, dict) else ev
    if not isinstance(evs, list):
        return None                                        # events indispo -> retente
    names = set()                                          # buteurs + passeurs du match
    for e in evs:
        if not isinstance(e, dict) or e.get("type") != "Goal" or e.get("ownGoal"):
            continue
        sc = (e.get("player") or {}).get("name") or e.get("nameStr")
        if sc:
            names.add(sc)
        a = e.get("assistStr")
        if isinstance(a, str) and a:
            names.add(a)
    return "won" if any(qtok <= _tok(n) for n in names if n) else "lost"


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
                    for ymd in days:                 # J, J+1 ET J-1 (comme foot/basket) : un match de
                        #                              nuit fini après minuit UTC reste retrouvable
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
    async def _safe(coro):
        """Une sous-source qui échoue ne doit JAMAIS jeter les faits des autres déjà collectés."""
        try:
            return await coro or []
        except Exception:
            return []
    facts: list = []
    if sport == "foot":
        facts += await _safe(_foot_extras(client, match))
        facts += await _safe(_foot_xg(client, match))      # un échec xG ne détruit plus les faits FotMob
    elif sport == "tennis":
        facts += await _safe(_tennis_extras(client, match))
    elif sport == "basket":
        facts += await _safe(_basket_extras(client, match))
    out = ""
    if facts:
        out += ("\n\nDONNÉES MULTI-SOURCES (ESPN / FotMob / Understat — source indépendante n°2, "
                "à CROISER avec ta recherche web ; un fait présent ici ET confirmé ailleurs = 2 sources) :\n- "
                + "\n- ".join(facts))
    out += await _flashscore_block(sport, match)
    try:                                   # Sportradar (GISMO) : forme/série/H2H/classement
        from app import sportradar
        out += await sportradar.block(client, sport, match)
    except Exception:
        pass
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
