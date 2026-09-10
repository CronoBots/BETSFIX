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
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
_T = 18.0          # timeout (s) par requête — tolérant (qualité > vitesse) mais jamais bloquant
_GAP = 0.35        # politesse entre 2 appels d'une même rafale (scoreboards datés)

_FOTMOB = "https://www.fotmob.com/api/data"

# ── MIGRATION API-FOOTBALL — enrichissement HYBRIDE (ARMÉ user 2026-09-09 — DÉFAUT ON) ──────────────
# Le bloc forme/xG/classement/H2H/arbitre/prédiction/over/série vient d'API-Football et remplace **Understat +
# Flashscore**. FotMob RESTE (blessés large / compos probables / météo) et **Sportradar RESTE** (GISMO gratuit,
# SANS proxy : séries de pari granulaires qui alimentent `_cool_conf` et qu'API-Football ne couvre pas aussi
# finement). Aucun de ces deux n'utilise iProyal. RÉVERSIBLE : BETSFIX_APIFOOTBALL_ENRICH=0.
_APIFOOTBALL_ENRICH = os.environ.get("BETSFIX_APIFOOTBALL_ENRICH", "1").strip().lower() not in ("0", "false", "no", "off")


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
    # Europe (manquants) — sélections nationales fréquentes en basket/foot
    "syrie": "syria", "roumanie": "romania", "malte": "malta", "armenie": "armenia",
    "georgie": "georgia", "azerbaidjan": "azerbaijan", "lettonie": "latvia",
    "lituanie": "lithuania", "estonie": "estonia", "bielorussie": "belarus",
    "russie": "russia", "moldavie": "moldova", "bulgarie": "bulgaria",
    "bosnie": "bosnia herzegovina", "bosnie-herzegovine": "bosnia herzegovina",
    "montenegro": "montenegro", "macedoine": "north macedonia macedonia",
    "macedoine du nord": "north macedonia", "albanie": "albania", "kosovo": "kosovo",
    "chypre": "cyprus", "luxembourg": "luxembourg", "portugal": "portugal",
    "france": "france", "roumanie du nord": "romania",
    "rep tcheque": "czech republic czechia", "rep.tcheque": "czech republic czechia",
    # abréviation jeton -> pays (résolue aussi par la traduction jeton-à-jeton)
    "tcheque": "czech republic czechia",
    # Proche/Moyen-Orient, Asie, Afrique, Amériques (manquants courants)
    "israel": "israel", "liban": "lebanon", "koweit": "kuwait", "bahrein": "bahrain",
    "oman": "oman", "emirats arabes unis": "united arab emirates uae",
    "inde": "india", "indonesie": "indonesia", "philippines": "philippines",
    "thailande": "thailand", "vietnam": "vietnam", "taiwan": "taiwan chinese taipei",
    "nigeria": "nigeria", "ghana": "ghana", "angola": "angola", "soudan": "sudan",
    "soudan du sud": "south sudan", "mali": "mali", "guinee": "guinea",
    "venezuela": "venezuela", "uruguay": "uruguay", "paraguay": "paraguay",
    "porto rico": "puerto rico", "republique dominicaine": "dominican republic",
    "costa rica": "costa rica", "panama": "panama",
}


# Villes/clubs francisés -> forme anglaise/locale (jeton à jeton)
_TOK_ALIAS = {"barcelone": "barcelona", "seville": "sevilla", "naples": "napoli",
              "turin": "torino", "rome": "roma", "lisbonne": "lisbon",
              "londres": "london", "genes": "genoa", "florence": "fiorentina",
              "anvers": "antwerp", "bruges": "brugge", "munich": "munchen",
              # Clubs BELGES à nom francisé (Unibet FR) -> nom international des sources de score
              # (règlement : cas Bruges-Courtrai « terminé mais pas réglé », user 2026-08-08).
              "courtrai": "kortrijk", "malines": "mechelen", "gantoise": "gent",
              "alost": "aalst", "ostende": "oostende", "louvain": "leuven",
              "trond": "truiden", "termonde": "dendermonde"}


def _deacc_low(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _tok(s: str) -> set:
    """Jetons significatifs d'un nom d'équipe/joueur (sans accents, ≥3 lettres) — enrichis de la
    traduction anglaise (sélections nationales françaises, villes francisées)."""
    base = _deacc_low(s)
    toks = {t for t in re.findall(r"[a-z]+", base) if len(t) >= 3}
    # traduction FR->EN sur le NOM ENTIER (ex. "cote d'ivoire" -> "ivory coast")
    en = _FR_EN.get(base)
    if en:
        toks |= {t for t in re.findall(r"[a-z]+", en) if len(t) >= 3}
    # ET jeton à jeton (ex. "Malte (F)" -> "malte" -> "malta", "Rép.Tchèque" -> "tcheque" -> "czech")
    # — indispensable quand un suffixe (F)/(W) ou une abréviation casse le lookup nom-entier.
    for t in list(toks):
        en2 = _FR_EN.get(t)
        if en2:
            toks |= {x for x in re.findall(r"[a-z]+", en2) if len(x) >= 3}
    toks |= {_TOK_ALIAS[t] for t in toks if t in _TOK_ALIAS}
    return toks


def _name_tok_match(qtok: set, ntok: set) -> bool:
    """Vrai si CHAQUE jeton de la requête `qtok` correspond à un jeton du nom `ntok`, en tolérant la
    TRONCATURE (un `who` figé dans un code fantôme peut avoir perdu une lettre finale, ex. « Rees » pour
    « Reese », « Ogunbowal » pour « Ogunbowale »). Correspondance = égalité OU l'un préfixe de l'autre
    (≥4 lettres pour éviter les faux positifs). La sécurité « un seul joueur => un règlement » reste
    portée par l'appelant (jamais de règlement sur homonymie)."""
    if qtok <= ntok:
        return True
    for qt in qtok:
        if qt in ntok:
            continue
        if not any(len(qt) >= 4 and len(nt) >= 4 and (nt.startswith(qt) or qt.startswith(nt))
                   for nt in ntok):
            return False
    return True


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


async def _get_json(client, url: str, headers: dict | None = None, tries: int = 3):
    """GET JSON best-effort (None si échec). RE-ESSAIE en cas de hoquet TRANSITOIRE (timeout, coupure
    réseau, 429/5xx, JSON tronqué) pour ne PAS perdre silencieusement une source utile -> analyses
    complètes, TOUTES les sources exploitées (demande user 2026-07-08). Un 4xx définitif (hors 429)
    n'est PAS retenté. `client` = httpx.AsyncClient du scan."""
    for i in range(max(1, tries)):
        try:
            r = await client.get(url, headers={**UA, **(headers or {})}, timeout=_T)
            if r.status_code == 200:
                return r.json()                       # JSON tronqué -> lève -> retry (except ci-dessous)
            if r.status_code < 500 and r.status_code != 429:
                return None                           # 4xx définitif (introuvable/interdit) -> inutile de retenter
        except Exception:
            pass                                      # transitoire -> on retente
        if i + 1 < tries:
            await asyncio.sleep(0.7 * (i + 1))        # petit backoff progressif
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
            out.append((h, a, m.get("id"), lname, (m.get("status") or {}).get("utcTime")))
    _FM_DAY[ymd] = out
    return out


# Écart MAX toléré (s) entre le coup d'envoi cible et celui du candidat FotMob — même garde que LiveScore
# (`livescore._MAX_KICKOFF_GAP_S`) contre la collision équipe 1re / réserve « II » de noms proches.
_FM_MAX_KICKOFF_GAP_S = 6 * 3600


async def _fotmob_find(client, home: str, away: str, start_iso: str):
    """matchId FotMob du match (noms + jour du coup d'envoi, ±1 jour). Parmi les candidats par NOMS, garde
    celui dont le COUP D'ENVOI est le plus proche de l'heure cible et rejette s'il est trop loin (>6 h) —
    sinon un match entre équipes de noms proches (1re vs réserve « II ») à une autre heure est pris à tort
    (bug 2026-07-27 : Portland Timbers–Real Salt Lake 2-1 @02:30 renvoyé pour Portland Timbers II–Real
    Monarchs 1-0 @20:00). None si introuvable."""
    dt = _start_dt(start_iso)
    if dt is None:
        return None
    best, best_gap = None, None
    for delta in (0, -1, 1):
        ymd = (dt + timedelta(days=delta)).strftime("%Y%m%d")
        for h, a, mid, _lg, utc in await _fotmob_day(client, ymd):
            if not _teams_match(home, away, h, a):
                continue
            edt = _start_dt(utc or "")
            gap = abs((edt - dt).total_seconds()) if edt else 1e12
            if best_gap is None or gap < best_gap:
                best, best_gap = mid, gap
        await asyncio.sleep(_GAP)
    if best is not None and best_gap is not None and best_gap > _FM_MAX_KICKOFF_GAP_S:
        return None                                       # meilleur candidat trop loin -> mauvais match
    return best


# Clés FotMob (stables) -> stats de RÈGLEMENT (par équipe [home, away]).
_FM_STAT = {"ShotsOnTarget": ("sot_h", "sot_a"), "total_shots": ("shots_h", "shots_a"),
            "corners": ("corners_h", "corners_a"), "yellow_cards": ("yc_h", "yc_a"),
            "red_cards": ("rc_h", "rc_a")}


def _parse_fm_period(period_stats: dict, suffix: str, out: dict) -> None:
    """Parse un bloc de période FotMob (`Periods.All` ou `Periods.1stHalf`) -> clés du règlement, avec
    `suffix` (« » pour le match, « _1h » pour la 1ère MT). N'écrase pas une clé déjà présente."""
    for grp in (period_stats.get("stats") or []):
        for it in (grp.get("stats") or []) if isinstance(grp, dict) else []:
            k, v = it.get("key"), it.get("stats")
            hk = f"{_FM_STAT[k][0]}{suffix}" if k in _FM_STAT else None
            if hk and isinstance(v, list) and len(v) == 2 and hk not in out:
                try:
                    out[hk], out[f"{_FM_STAT[k][1]}{suffix}"] = int(v[0]), int(v[1])
                except (TypeError, ValueError):
                    pass


async def foot_match_stats(client, home: str, away: str, start_iso: str) -> dict | None:
    """STATS de match FOOT via FotMob (déjà source n°1 foot) : tirs cadrés / tirs / corners / cartons PAR
    ÉQUIPE -> {sot_h/a, shots_h/a, corners_h/a, cards_h/a}. Comble le règlement des marchés tirs (cadrés)
    là où Flashscore/GISMO ne couvrent pas. Cible le TOTAL du match (`content.stats.Periods.All`) ET la
    1ère MT (`Periods.1stHalf` -> clés `*_1h`) pour alimenter aussi les marchés MI-TEMPS (fin de
    l'asymétrie où seul Flashscore couvrait la 1ère MT). None si introuvable. Tolérant (jamais d'exception)."""
    try:
        mid = await _fotmob_find(client, home, away, start_iso or "")
        if not mid:
            return None
        j = await _get_json(client, f"{_FOTMOB}/matchDetails?matchId={mid}")
        if not isinstance(j, dict):
            return None
        periods = (((j.get("content") or {}).get("stats") or {}).get("Periods") or {})
        out: dict = {}
        _parse_fm_period(periods.get("All") or {}, "", out)
        _parse_fm_period(periods.get("1stHalf") or {}, "_1h", out)   # 1ère MT (best-effort)
        for sfx in ("", "_1h"):                          # marché CARTONS = jaunes + rouges (match + 1ère MT)
            if f"yc_h{sfx}" in out:
                out[f"cards_h{sfx}"] = out.get(f"yc_h{sfx}", 0) + out.get(f"rc_h{sfx}", 0)
                out[f"cards_a{sfx}"] = out.get(f"yc_a{sfx}", 0) + out.get(f"rc_a{sfx}", 0)
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


def _fm_h2h_recent(matches, limit: int = 3) -> list[str]:
    """Derniers face-à-face FotMob AVEC score (au-delà du bilan W-D-L) : [« Lyon 2-1 Paris », …], le plus
    récent d'abord. Best-effort : ignore toute entrée non parsable (structure FotMob variable)."""
    rows = []
    for m in matches or []:
        if not isinstance(m, dict):
            continue
        hn = ((m.get("home") or {}).get("name")) or ""
        an = ((m.get("away") or {}).get("name")) or ""
        st = m.get("status") or {}
        score = st.get("scoreStr") or st.get("score")
        if not score:                                    # repli : score porté par home/away
            hs, asc = (m.get("home") or {}).get("score"), (m.get("away") or {}).get("score")
            if hs is not None and asc is not None:
                score = f"{hs} - {asc}"
        if not (hn and an and score):
            continue
        ut = st.get("utcTime") or m.get("time")
        if isinstance(ut, (int, float)):
            ts = float(ut)
        elif isinstance(ut, str):
            dt = _start_dt(ut)
            ts = dt.timestamp() if dt else 0.0
        else:
            ts = 0.0
        rows.append((ts, f"{hn} {str(score).replace(' ', '')} {an}"))
    rows.sort(key=lambda r: r[0], reverse=True)           # plus récent d'abord
    return [r[1] for r in rows[:limit]]


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
    # H2H DÉTAILLÉ : derniers face-à-face AVEC score (tendance récente, au-delà du bilan W-D-L). Noms +
    # scores réels -> pas besoin d'orientation home/away (anti-inversion). Best-effort (matches absent = rien).
    recent = _fm_h2h_recent(h2h.get("matches"))
    if recent:
        facts.append("H2H récents : " + " ; ".join(recent) + " (FotMob)")
    # ARBITRE désigné (infoBox) : repère de DISCIPLINE pour les marchés cartons (certains arbitres sifflent
    # beaucoup plus). Dispo pré-match. Best-effort (structure variable).
    ib = mf.get("infoBox") or {}
    ref = ib.get("Referee")
    ref = ref.get("text") if isinstance(ref, dict) else (ref if isinstance(ref, str) else None)
    if ref:
        facts.append(f"Arbitre : {ref} (FotMob) — repère discipline/cartons")
    # Absents (si exposés)
    lu = c.get("lineup") or {}
    for side_key, label in (("homeTeam", home), ("awayTeam", away)):
        ua = _fm_unavailable(lu.get(side_key) or {}, label)
        if ua:
            facts.append(f"Absents [{label}] : {ua} (FotMob)")
    # COMPOSITIONS (titulaires + formation) — data FORTE, tombe ~1 h avant le coup d'envoi. FotMob
    # (enetpulse) publie les 11 starters + la formation ; on les injecte pour que l'analyse pivote sur le
    # onze réel (mandat proactif 2026-07-14 : la donnée existe, on l'exploite). « confirmée » si FotMob la
    # marque officielle, sinon « probable » (onze annoncé mais non verrouillé) — honnêteté sur la fiabilité.
    _lt = str(lu.get("lineupType") or "").lower()
    _conf = "confirmée" if _lt in ("confirmed", "lineup", "confirmedlineup") else "probable"
    for side_key, label in (("homeTeam", home), ("awayTeam", away)):
        sd = lu.get(side_key) or {}
        starters = sd.get("starters") or []
        if len(starters) >= 7:                       # onze (quasi) complet publié
            names = []
            for p in starters:
                nm = p.get("name") or (p.get("player") or {}).get("name") or ""
                if nm:
                    names.append(nm.split()[-1] if " " in nm else nm)   # nom court (famille)
            form = sd.get("formation") or ""
            if names:
                facts.append(f"Compo {_conf} [{label}]" + (f" ({form})" if form else "")
                             + " : " + ", ".join(names) + " (FotMob)")
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


# ------------------------------------------------------------------ FOOT — xG
# Understat RETIRÉ 2026-09-10 (user) : l'xG vient d'API-Football (`apifootball.team_xg_form`, top-5)
# via le bloc hybride `enrich_facts`. Understat (scraping) n'était plus appelé (hybride ON) — nettoyé.

# (code TENNIS + BASKET ESPN RETIRÉ 2026-09-11 : BETSFIX = 100% foot depuis 2026-08-07)

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


# Parsers de score FotMob/ESPN/tennis RETIRÉS 2026-09-10 (règlement -> API-Football).


# (first_goal_side / first_scorer FotMob RETIRÉS 2026-09-11 : premier but/buteur repris par apifootball.first_goal_side/first_scorer via les events)

# (props joueur BASKET ESPN RETIRÉS 2026-09-11)

# (foot_player_stat / player_scored_or_assisted / _FB_STAT_KEYS FotMob RETIRÉS 2026-09-11 :
#  props joueur PLAYERFB / GKSAVES / SCOREASSIST réglés via apifootball.player_match_stat /
#  player_scored_or_assisted (endpoint /fixtures/players). Un scraping FotMob de moins.)

async def extras(client, sport: str, match: dict, prov: dict | None = None) -> str:
    """Bloc « DONNÉES MULTI-SOURCES » prêt à coller dans le dossier de l'analyste.
    '' si rien trouvé / tout en échec (le scan continue sans).
    `prov` (dict optionnel) est REMPLI avec les sources ayant réellement répondu (traçabilité de la
    COMPLÉTUDE des données : {'fotmob':True,...}) -> écrit dans le sidecar (`sources`/`data_score`) pour
    savoir a posteriori si l'analyse a été faite sur données riches ou dégradées. Non-cassant (défaut None)."""
    tracker = prov if prov is not None else {}

    async def _safe(coro, key):
        """Une sous-source qui échoue ne doit JAMAIS jeter les faits des autres déjà collectés.
        Marque `tracker[key]` dès qu'elle a réellement renvoyé des faits (traçabilité provenance)."""
        try:
            r = await coro or []
        except Exception:
            r = []
        if r:
            tracker[key] = True
        return r
    # HYBRIDE API-Football : bloc forme/xG/classement/H2H/arbitre/prédiction remplace Understat+Flashscore+
    # Sportradar ; FotMob reste pour blessés/compos/météo. Flag OFF par défaut (shadow-first).
    af_on = _APIFOOTBALL_ENRICH and sport == "foot"
    try:
        from app import apifootball as _AF
        af_on = af_on and _AF.configured()
    except Exception:
        af_on = False

    facts: list = []
    if sport == "foot":
        facts += await _safe(_foot_extras(client, match), "fotmob")
        if af_on:                                                    # API-Football = source d'enrichissement PRIMAIRE
            # 100% API-FOOTBALL (user 2026-09-09 : « doit être utilisé pour CHAQUE match ») : API-Football
            # résout ~100% des matchs (mesuré 12/12). Si la 1re passe revient VIDE (hoquet réseau / cache jour
            # raté), on RETENTE une fois -> on ne perd jamais l'enrichissement API-Football sur un simple hoquet.
            af_facts = await _safe(_af_enrich(match), "apifootball")
            if not af_facts:
                af_facts = await _safe(_af_enrich(match), "apifootball")
            if not af_facts:                                         # diagnostic : gap réel à surveiller (rare)
                print(f"  ⚠️ API-FOOTBALL ENRICH VIDE : {match.get('name', '?')} "
                      f"(fixture non résolu / fetch KO) — enrichissement API-Football manquant.")
            facts += af_facts
        # (revert enrich OFF, af_on False : plus de source xG — Understat retiré ; FotMob + Flashscore +
        #  Sportradar restent via les blocs plus bas. Mode de secours dégradé, jamais le chemin par défaut.)
    # (branches enrichissement tennis/basket RETIRÉES 2026-09-11 : BETSFIX = 100% foot)
    out = ""
    if facts:
        _src = "ESPN / FotMob / API-Football" if af_on else "ESPN / FotMob"
        out += (f"\n\nDONNÉES MULTI-SOURCES ({_src} — source indépendante n°2, "
                "à CROISER avec ta recherche web ; un fait présent ici ET confirmé ailleurs = 2 sources) :\n- "
                + "\n- ".join(facts))
    if not af_on:                          # Flashscore remplacé par le bloc API-Football (Understat aussi, + haut)
        fb = await _flashscore_block(sport, match)
        if fb and fb.strip():
            tracker["flashscore"] = True
        out += fb
    try:                                   # Sportradar (GISMO, GRATUIT, SANS proxy) — TOUJOURS gardé : séries de
        from app import sportradar         # pari granulaires (alimentent `_cool_conf`) qu'API-Football ne couvre
        sb = await sportradar.block(client, sport, match)   # pas aussi finement. Ne dépend PAS d'iProyal.
        if sb and sb.strip():
            tracker["sportradar"] = True
        out += sb
    except Exception:
        pass
    return out


async def _af_enrich(match: dict) -> list[str]:
    """Faits d'enrichissement API-Football (adaptateur sync appelé hors boucle). [] si non configuré/non résolu."""
    from app import apifootball as _AF
    if not _AF.configured():
        return []

    def _work():
        with _AF._client() as cl:
            facts, _ = _AF.enrich_facts(cl, match.get("home"), match.get("away"), match.get("start"))
            return facts
    try:
        return await asyncio.to_thread(_work)
    except Exception:
        return []


async def _flashscore_block(sport: str, match: dict) -> str:
    """Bloc FLASHSCORE (forme récente + face-à-face direct) — source INDÉPENDANTE n°3.
    Best-effort : urllib synchrone déporté hors de la boucle ; toute panne -> ''."""
    fs_sport = {"foot": "football", "tennis": "tennis", "basket": "basket"}.get(sport)
    if not fs_sport:
        return ""
    h, a, st = match.get("home", ""), match.get("away", ""), match.get("start")
    try:
        from app import flashscore
    except Exception:
        return ""

    async def _fetch(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args) or []
        except Exception:
            return []

    facts = await _fetch(flashscore.prematch_facts, h, a, st, fs_sport)
    # 1 RETRY sur vide (user 2026-09-03) : les ratés de Flashscore au scan sont surtout TRANSITOIRES (hoquet
    # réseau/blocage momentané) — vérifié KAA Gent (vide au scan, 620 c au re-run). Coûte 0,8 s SEULEMENT quand
    # la 1re tentative échoue (chemin nominal inchangé) ; évite les analyses « 1 source » par malchance de timing.
    if not facts:
        await asyncio.sleep(0.8)
        facts = await _fetch(flashscore.prematch_facts, h, a, st, fs_sport)
    if sport == "tennis":                  # + stats de SERVICE des 2 joueurs (aces, 1er service…) — non destructif
        facts = facts + await _fetch(flashscore.serve_facts, h, a, st)
    elif sport == "foot":                  # + compositions/formations si dispo (~1 h avant le coup d'envoi)
        facts = facts + await _fetch(flashscore.lineup_facts, h, a, st)
    if not facts:
        return ""
    return ("\n\nDONNÉES FLASHSCORE (forme, face-à-face & service — source indépendante n°3, à CROISER "
            "avec le bloc ci-dessus et ta recherche web) :\n- " + "\n- ".join(facts))
