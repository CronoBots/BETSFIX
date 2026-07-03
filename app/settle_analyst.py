"""Règlement automatique du pari « le plus sûr » des analyses, APRÈS match.

Score via SofaScore (Unibet ne garde pas les résultats finis) : `event/{id}` donne le score final,
les scores par set (period1/2/3 = jeux) et `firstToServe` ; `event/{id}/point-by-point` donne, jeu
par jeu, qui sert (`serving`) et qui gagne (`scoring`). Permet de régler aussi les marchés fins
(total jeux d'un set, 1er jeu de service tenu…). Réglé UNE fois puis caché dans le sidecar
(`result`). On ne règle que ce qu'on peut prouver — sinon « non vérifiable », jamais de devinette.
"""

from __future__ import annotations

import asyncio
import glob
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from app import analyses, sofa_http
from app.netconst import SOFA_B as _SOFA   # source unique (cf. app/netconst.py)

log = logging.getLogger("betsfix.settle")
_SPORT_PATH = {"foot": "football", "tennis": "tennis", "basket": "basketball"}
# Date courte FR pour les cartes Telegram (« sam. 21 juin »).
_FR_J = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_FR_M = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def _fr_date(dt) -> str:
    return f"{_FR_J[dt.weekday()]} {dt.day} {_FR_M[dt.month - 1]}"
# Version de la LOGIQUE de règlement : à incrémenter quand de nouveaux marchés deviennent réglables
# (-> re-règlement unique des sidecars depuis `result.raw` caché, sans appel réseau). v2 = + handicap
# (HCAP), total d'équipe (TEAMTOT), score exact en sets (SETSCORE). v3 = parseur durci : abréviation
# « pts/pt » des points + désambiguïsation des noms partagés entre les 2 camps (ex. « Ironi », « Maria »).
# v4 = nettoyage du contexte entre parenthèses + jeux par set tous ordres + vainqueur « Set N : Nom ».
# v5 = camp lu AVANT la parenthèse (marché lu sur tout) + « au moins 1 set » (chiffre).
# v6 = total de sets + CARTONS/CORNERS réglés depuis event/{id}/statistics (jaunes/rouges/corners).
# v7 = stocke la confiance (prob) de chaque pari réglé -> alimente la page calibration.
# v8 = « premier à X points » réglé via event/{id}/incidents (FIRSTTO).
# v9 = handicap en SETS (tennis) réglé via SETHCAP (sur sets_home/away).
# v10 = handicap au moins Unicode (−) + « total de sets : moins de N » (SETSTOT).
_settle_lock = asyncio.Lock()   # sérialise les passes de règlement dans un même process (anti double-notif)
# Void = ULTIME RECOURS : on ne « tranche nul » une jambe indéterminable QUE si le match est fini depuis
# plus de N jours (le score/les stats arrivent souvent en retard -> d'ici là on continue de RÉGLER pour
# de vrai). En pratique quasi tout se règle bien avant -> void rarissime (donnée réellement morte).
_VOID_AFTER_DAYS = 3.0


def _merge_stats(cur: dict | None, new: dict | None) -> dict:
    """Fusionne des stats de match en COMBLANT `cur` sans jamais l'écraser, et en IGNORANT les faux zéros
    de tirs d'une source qui ne couvre pas le match : sot/shots TOUS nuls = donnée ABSENTE (pas un vrai 0)
    -> injectés, ils dé-régleraient une jambe « tirs cadrés » (0 < seuil = LOST à tort). Garde généralisée
    à TOUTES les sources de stats (FotMob/Flashscore/GISMO). `cur` (cache fiable) reste prioritaire."""
    cur = cur or {}
    if not new:
        return cur
    new = dict(new)
    if (new.get("sot_h", 0) + new.get("sot_a", 0)
            + new.get("shots_h", 0) + new.get("shots_a", 0)) == 0:
        for k in ("sot_h", "sot_a", "shots_h", "shots_a"):
            new.pop(k, None)
    return {**new, **cur}   # cur (cache) prioritaire -> comble sans écraser


def _match_age_days(d: dict) -> float:
    """Âge du match en jours depuis le coup d'envoi (`start` = ISO ou epoch). 0 si inconnu/futur."""
    start = d.get("start")
    if not start:
        return 0.0
    try:
        if isinstance(start, (int, float)):
            dt = datetime.fromtimestamp(start, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except (ValueError, OverflowError, OSError):
        return 0.0


def _score_incomplete(sc: dict | None, sport: str) -> bool:
    """Un score CACHÉ (result.raw) capté quand le match était encore EN COURS (0-0 / vide) doit être
    RE-FETCHÉ, sinon il masque le vrai score final. Bug vécu 2026-07-02 : un cache tennis « 0-0 sets »
    (pris avant le 1er set) figeait le vainqueur -> WIN/SETWIN jamais réglés alors que Flashscore
    donnait 2-0. Un VRAI 0-0 foot final a home=0/away=0 (≠ None) -> considéré COMPLET (non re-fetché)."""
    if not sc:
        return True
    sh, sa = sc.get("sets_home") or 0, sc.get("sets_away") or 0
    if sport == "tennis":
        return sh == 0 and sa == 0                     # aucun set gagné -> capture en cours
    h, a = sc.get("home"), sc.get("away")
    return h is None and a is None and sh == 0 and sa == 0   # aucun score numérique capté
_SETTLE_VERSION = 44   # v44 : PÉRIODES via Sportradar GISMO (repli) — jeux/sets/tie-breaks tennis &
#                              quart-temps basket enfin réglables quand LiveScore/Flashscore échouent.
# (v45 tirs/BOTHHALVES ANNULÉ le 2026-07-03 : le re-règlement de masse dé-réglait des combos historiques
#  faute de source de tirs sur les vieux matchs. Le mapping code_from_pick reste — forward only.)
# v43 : jambes à CODE VIDE débloquées — nom d'équipe seul = moneyline (WIN/1X2)
#                              + « Plus de X » sans unité = total du match (combinés WNBA coincés).
# v42 : WALKOVER/forfait (le joueur qui avance gagne -> pari sur lui = gagné),
#                              détecté via Flashscore (champ AM « withdrawn/retired »). Jamais de void.
# v41 : FIX « gagne au moins une mi-temps NON » (WINHALF NO, était réglé comme
#                              « Oui » -> faux) + FLASHSCORE branché en repli (couverture universelle
#                              des ligues -> règle les tickets obscurs). Re-règle tout.
# v40 : FIX combiné resté « en attente » à vie alors qu'une jambe avait perdu
#                              (off-by-one : finalisation skippée au 8e essai) -> re-règle les coincés.
# v39 : VAINQUEUR d'une mi-temps (HALFRES, « Mi-temps <équipe> ») via les périodes +
#                              HANDICAP 3 voies (HCAP3, « 3-Way Handicap (X-Y) ») via le score final ajusté.
# v38 : « Temps réglementaire <équipe>/nul » (REGTIME) réglé sur les 90 min.
# v37 : BTTS par mi-temps (BTTSHALF) via les périodes.
# v36 : props joueur basket COMBINÉS (PRA/PR/PA/RA) + format seuil « X+ ».
# v35 : premier BUTEUR (FIRSTSCORER) + arrêts du gardien par équipe (GKSAVES) via FotMob.
# v34 : props JOUEUR foot (PLAYERFB : arrêts/tirs/passes/tacles/fautes) via FotMob (Opta).
# v33 : props JOUEUR basket (PLAYERBK PTS/REB/AST) via box-score ESPN (matching strict).
# v32 : PREMIER BUT du match (FIRSTGOAL) réglé via les events FotMob (1er buteur).
# v31 : totaux de buts par mi-temps dérivés des PÉRIODES (bothhalves/1H/2H réglés
#                              même sans Flashscore).
# v30 : couverture EXHAUSTIVE — basket quart-temps/mi-temps (BQ*), tennis score
#                              exact/handicap jeux/tie-break (SETSCORE/GAMESHCAP/TIEBREAK), foot score
#                              exact + marque 2 MT (SCORE/TEAMBOTH). Plus de marché mal codé/non réglable.
# v29 : marchés MI-TEMPS foot réglés (TEAMHALF/HALFTOT/WINHALF) via le score par
#                              mi-temps ET par équipe (LiveScore periods) -> plus de « non réglable ».
# v28 : règlement des PRÉDICTIONS FANTÔMES (shadow) pour le calibrage.
# v27 : jeux d'UN joueur (« <joueur> moins de X.5 jeux ») = TEAMGAMES (≠ TOTGAMES) ;
#                              jambes de combiné réglées sur le code RE-DÉRIVÉ frais (code stocké périmé).
# v26 : 1er pari réglé sur SON propre code (plus forcé au résultat du pick quand ils
#                              divergent) + badge headline aligné sur le pari affiché (ex. CRB « moins 4.5 »).
# v25 : « <équipe> -1.5 buts » (ligne signée d'ÉQUIPE) = TEAMTOT UNDER, plus un
#                              handicap (corrige combinés CdM mal réglés, ex. Tchéquie-Afrique du Sud).
# v24 : « Total de buts +1.5 » (ligne signée) reconnu OVER/UNDER.
#                              v18 : « but dans les deux mi-temps » via les buts par mi-temps (df_su) +
#                              re-règlement des combinés au verdict incomplet (combo_tries, 8 essais).


# --------------------------------------------------------------- règlement (pur, depuis le score)
def settle_pick(code: str, score: dict) -> str | None:
    """'won'/'lost'/'push' selon le CODE et le score. None = non réglable ici (cf. HOLD1 -> async).
    score = {home, away, sets_home, sets_away, periods:{n:(h,a)}}."""
    if not code or not score:
        return None
    parts = code.upper().split()
    kind = parts[0]
    # WALKOVER / FORFAIT (tennis surtout) : le joueur qui AVANCE gagne. RÈGLE (demande user, jamais de
    # void) : tout pari SUR le vainqueur = gagné, sur le perdant = perdu. On lit le côté HOME/AWAY du
    # code (vainqueur/sets/jeux/handicap…). Marché sans côté clair (total de jeux/sets) -> non réglable.
    if score.get("walkover") and score.get("winner") in ("home", "away"):
        side = next((p for p in parts if p in ("HOME", "AWAY")), None)
        if side is None and kind == "1X2" and len(parts) > 1:
            side = {"1": "HOME", "2": "AWAY"}.get(parts[1])
        if side is None:
            return None
        return "won" if (score["winner"] == side.lower()) else "lost"
    h, a = score.get("home"), score.get("away")
    sh, sa = score.get("sets_home"), score.get("sets_away")
    periods = score.get("periods") or {}
    has_ha = h is not None and a is not None

    def _per(n):
        return periods.get(n) or periods.get(str(n))

    if kind in ("OVER", "UNDER") and has_ha and len(parts) > 1:
        try:
            line = float(parts[1])
        except ValueError:
            return None
        total = h + a
        return "push" if total == line else ("won" if ((total > line) == (kind == "OVER")) else "lost")
    if kind == "BTTS" and has_ha:
        both = h > 0 and a > 0
        return "won" if (both == (len(parts) < 2 or parts[1] == "YES")) else "lost"
    if kind == "1X2" and has_ha and len(parts) > 1:
        res = "1" if h > a else ("2" if a > h else "X")
        return "won" if parts[1] == res else "lost"
    if kind == "REGTIME" and len(parts) > 1:           # résultat du TEMPS RÉGLEMENTAIRE (90 min, hors prol.)
        p1, p2 = _per(1), _per(2)
        if p1 and p2:                                  # somme des 2 mi-temps (exclut une éventuelle prolongation)
            rh, ra = p1[0] + p2[0], p1[1] + p2[1]
        elif has_ha:
            rh, ra = h, a                              # repli : phase de groupes = pas de prolongation
        else:
            return None
        res = "HOME" if rh > ra else ("AWAY" if ra > rh else "DRAW")
        return "won" if parts[1] == res else "lost"
    if kind == "HCAP3" and has_ha and len(parts) >= 4:   # handicap 3 voies « (X-Y) » : 1X2 sur le score AJUSTÉ
        try:
            ah, aa = h + int(parts[2]), a + int(parts[3])   # score de départ ajouté au score réel
        except ValueError:
            return None
        win = "HOME" if ah > aa else ("AWAY" if aa > ah else "DRAW")
        return "won" if parts[1] == win else "lost"
    if kind == "DC" and has_ha and len(parts) > 1:
        ok = {"1X": h >= a, "12": h != a, "X2": a >= h}.get(parts[1])
        return None if ok is None else ("won" if ok else "lost")
    if kind == "DCHALF" and len(parts) >= 3:           # double chance sur une MI-TEMPS (1H/2H)
        per = _per(1 if parts[1] == "1H" else 2)       # score de CETTE période (periods LiveScore/SR)
        if not per:
            return None
        ph, pa = per
        ok = {"1X": ph >= pa, "12": ph != pa, "X2": pa >= ph}.get(parts[2])
        return None if ok is None else ("won" if ok else "lost")
    if kind == "WIN" and len(parts) > 1:
        if sh is not None and sa is not None and (sh or sa):
            hwin = sh > sa
        elif has_ha:
            hwin = h > a
        else:
            return None
        return "won" if ((parts[1] == "HOME") == hwin) else "lost"
    if kind == "SET" and len(parts) > 1 and sh is not None and sa is not None:
        got = (sh >= 1) if parts[1] == "HOME" else (sa >= 1)
        return "won" if got else "lost"
    # --- marchés tennis fins (depuis les jeux par set) ---
    if kind == "SETGAMES" and len(parts) >= 4:          # SETGAMES <n> OVER/UNDER <ligne>
        p = _per(_int(parts[1]))
        if not p:
            return None
        try:
            line = float(parts[3])
        except ValueError:
            return None
        total = p[0] + p[1]
        return "push" if total == line else ("won" if ((total > line) == (parts[2] == "OVER")) else "lost")
    if kind == "TOTGAMES" and len(parts) >= 3 and periods:   # TOTGAMES OVER/UNDER <ligne> (match)
        try:
            line = float(parts[2])
        except ValueError:
            return None
        total = sum(x[0] + x[1] for x in periods.values())
        return "push" if total == line else ("won" if ((total > line) == (parts[1] == "OVER")) else "lost")
    if kind == "TEAMGAMES" and len(parts) >= 4 and periods:   # TEAMGAMES HOME/AWAY OVER/UNDER <ligne>
        try:                                                   # jeux gagnés par UN joueur (somme des sets)
            line = float(parts[3])
        except ValueError:
            return None
        idx = 0 if parts[1] == "HOME" else 1
        total = sum(x[idx] for x in periods.values())
        return "push" if total == line else ("won" if ((total > line) == (parts[2] == "OVER")) else "lost")
    if kind == "SETWIN" and len(parts) >= 3:            # SETWIN <n> HOME/AWAY
        p = _per(_int(parts[1]))
        if not p:
            return None
        return "won" if ((parts[2] == "HOME") == (p[0] > p[1])) else "lost"
    if kind == "SETSCORE" and len(parts) >= 3 and sh is not None and sa is not None:  # score exact en sets
        tsh, tsa = _int(parts[1]), _int(parts[2])
        if tsh is None or tsa is None:
            return None
        return "won" if (sh == tsh and sa == tsa) else "lost"
    # --- marchés MI-TEMPS foot (score PAR mi-temps ET PAR équipe via LiveScore : periods[1]=(h1,a1),
    #     periods[2]=(h2,a2)). Rend réglables les paris « buts par équipe/MT », « gagne une MT », etc.
    if kind == "TEAMHALF" and len(parts) >= 5:          # TEAMHALF HOME/AWAY 1H/2H OVER/UNDER <ligne>
        p = _per(1 if parts[2] == "1H" else 2)
        if not p:
            return None
        try:
            line = float(parts[4])
        except ValueError:
            return None
        g = p[0] if parts[1] == "HOME" else p[1]
        return "push" if g == line else ("won" if ((g > line) == (parts[3] == "OVER")) else "lost")
    if kind == "HALFTOT" and len(parts) >= 4:           # HALFTOT 1H/2H OVER/UNDER <ligne> (total du MATCH dans une MT)
        p = _per(1 if parts[1] == "1H" else 2)
        if not p:
            return None
        try:
            line = float(parts[3])
        except ValueError:
            return None
        tot = p[0] + p[1]
        return "push" if tot == line else ("won" if ((tot > line) == (parts[2] == "OVER")) else "lost")
    if kind == "WINHALF" and len(parts) >= 2:           # WINHALF HOME/AWAY [NO] (gagne AU MOINS une MT)
        p1, p2 = _per(1), _per(2)
        if not p1 or not p2:
            return None
        i = 0 if parts[1] == "HOME" else 1
        o = 1 - i
        won_half = p1[i] > p1[o] or p2[i] > p2[o]        # l'équipe gagne au moins UNE mi-temps
        neg = len(parts) >= 3 and parts[2] == "NO"       # « … Non » = elle ne gagne AUCUNE mi-temps
        return "won" if (won_half != neg) else "lost"    # NO inverse le résultat
    if kind == "HALFRES" and len(parts) >= 3:           # HALFRES HOME/AWAY/DRAW 1H/2H : vainqueur (1X2) d'UNE
        p = _per(1 if parts[2] == "1H" else 2)          # mi-temps, sur le score de CETTE période (≠ WINHALF)
        if not p:
            return None
        if parts[1] == "DRAW":
            return "won" if p[0] == p[1] else "lost"
        i = 0 if parts[1] == "HOME" else 1
        return "won" if p[i] > p[1 - i] else "lost"
    if kind == "TEAMBOTH" and len(parts) >= 2:          # équipe marque dans les DEUX mi-temps
        p1, p2 = _per(1), _per(2)
        if not p1 or not p2:
            return None
        i = 0 if parts[1] == "HOME" else 1
        return "won" if (p1[i] >= 1 and p2[i] >= 1) else "lost"
    if kind == "BOTHHALVES" and len(parts) >= 2:        # un BUT (total, peu importe l'équipe) dans CHAQUE MT
        p1, p2 = _per(1), _per(2)
        if not p1 or not p2:
            return None
        both = (p1[0] + p1[1] >= 1) and (p2[0] + p2[1] >= 1)
        return "won" if (both == (parts[1] == "YES")) else "lost"
    if kind == "BTTSHALF" and len(parts) >= 3:          # les 2 équipes marquent dans une MI-TEMPS
        p = _per(1 if parts[1] == "1H" else 2)
        if not p:
            return None
        both = p[0] >= 1 and p[1] >= 1
        return "won" if (both == (parts[2] == "YES")) else "lost"
    if kind == "SCORE" and has_ha and len(parts) >= 3:  # score EXACT (foot)
        try:
            th, ta = int(parts[1]), int(parts[2])
        except ValueError:
            return None
        return "won" if (h == th and a == ta) else "lost"
    # --- BASKET : marchés par SEGMENT (quart-temps OU mi-temps) depuis les périodes LiveScore
    #     (periods[1..4] = Q1..Q4). `spec` = "1".."4" (quart) ou "H1"=Q1+Q2 / "H2"=Q3+Q4 (mi-temps). ---
    def _seg(spec):
        qs = {"H1": [1, 2], "H2": [3, 4]}.get(spec, [int(spec)] if spec.isdigit() else [])
        sh_, sa_, got = 0, 0, False
        for q in qs:
            pp = _per(q)
            if pp:
                sh_ += pp[0]; sa_ += pp[1]; got = True
        return (sh_, sa_) if got else None
    if kind in ("BQTOT", "BQTEAM", "BQWIN", "BQHCAP"):
        try:
            if kind == "BQTOT" and len(parts) >= 4:         # BQTOT <spec> OVER/UNDER <ligne>
                seg = _seg(parts[1])
                if not seg:
                    return None
                tot, line = seg[0] + seg[1], float(parts[3])
                return "push" if tot == line else ("won" if ((tot > line) == (parts[2] == "OVER")) else "lost")
            if kind == "BQTEAM" and len(parts) >= 5:        # BQTEAM HOME/AWAY <spec> OVER/UNDER <ligne>
                seg = _seg(parts[2])
                if not seg:
                    return None
                g, line = (seg[0] if parts[1] == "HOME" else seg[1]), float(parts[4])
                return "push" if g == line else ("won" if ((g > line) == (parts[3] == "OVER")) else "lost")
            if kind == "BQWIN" and len(parts) >= 3:         # BQWIN <spec> HOME/AWAY
                seg = _seg(parts[1])
                if not seg:
                    return None
                if seg[0] == seg[1]:
                    return "push"
                return "won" if ((parts[2] == "HOME") == (seg[0] > seg[1])) else "lost"
            if kind == "BQHCAP" and len(parts) >= 4:        # BQHCAP HOME/AWAY <spec> <ligne signée>
                seg = _seg(parts[2])
                if not seg:
                    return None
                line = float(parts[3])
                diff = (seg[0] + line - seg[1]) if parts[1] == "HOME" else (seg[1] + line - seg[0])
                return "push" if diff == 0 else ("won" if diff > 0 else "lost")
        except ValueError:
            return None
        return None
    # --- handicap & total d'équipe (depuis le score final h/a : basket points, foot buts) ---
    if kind == "HCAP" and has_ha and len(parts) >= 3:   # HCAP HOME/AWAY <ligne signée>
        try:
            line = float(parts[2])
        except ValueError:
            return None
        diff = (h + line - a) if parts[1] == "HOME" else (a + line - h)
        return "push" if diff == 0 else ("won" if diff > 0 else "lost")
    if kind == "SETHCAP" and sh is not None and sa is not None and len(parts) >= 3:  # handicap en SETS
        try:
            line = float(parts[2])
        except ValueError:
            return None
        diff = (sh + line - sa) if parts[1] == "HOME" else (sa + line - sh)
        return "push" if diff == 0 else ("won" if diff > 0 else "lost")
    if kind == "TEAMTOT" and has_ha and len(parts) >= 4:   # TEAMTOT HOME/AWAY OVER/UNDER <ligne>
        try:
            line = float(parts[3])
        except ValueError:
            return None
        val = h if parts[1] == "HOME" else a
        return "push" if val == line else ("won" if ((val > line) == (parts[2] == "OVER")) else "lost")
    if kind == "SETSTOT" and sh is not None and sa is not None and len(parts) >= 3:  # total de sets
        try:
            line = float(parts[2])
        except ValueError:
            return None
        total = sh + sa
        return "push" if total == line else ("won" if ((total > line) == (parts[1] == "OVER")) else "lost")
    if kind == "GAMESHCAP" and len(parts) >= 3 and periods:   # handicap de JEUX (tennis) sur l'écart TOTAL
        try:
            line = float(parts[2])
        except ValueError:
            return None
        th = sum(p[0] for p in periods.values())
        ta = sum(p[1] for p in periods.values())
        diff = (th + line - ta) if parts[1] == "HOME" else (ta + line - th)
        return "push" if diff == 0 else ("won" if diff > 0 else "lost")
    if kind == "TIEBREAK" and len(parts) >= 2 and periods:    # un tie-break a-t-il eu lieu ? (set 7-6/6-7)
        tb = any({p[0], p[1]} == {6, 7} for p in periods.values())
        return "won" if (tb == (parts[1] == "YES")) else "lost"
    # --- cartons / corners : depuis les STATS du match (event/{id}/statistics), cf. _event_stats ---
    if kind in ("CARDS", "REDCARDS", "CORNERS", "SHOTSOT", "SHOTS"):
        stats = score.get("stats") or {}
        kh, ka = {"CARDS": ("cards_h", "cards_a"), "REDCARDS": ("rc_h", "rc_a"),
                  "CORNERS": ("corners_h", "corners_a"),
                  "SHOTSOT": ("sot_h", "sot_a"), "SHOTS": ("shots_h", "shots_a")}[kind]
        hv, av = stats.get(kh), stats.get(ka)
        if hv is None or av is None:
            return None                                   # stats pas encore récupérées -> on retentera
        rest = parts[1:]
        side = rest.pop(0) if (rest and rest[0] in ("HOME", "AWAY")) else None
        if len(rest) < 2 or rest[0] not in ("OVER", "UNDER"):
            return None
        try:
            line = float(rest[1])
        except ValueError:
            return None
        val = (hv if side == "HOME" else av) if side else (hv + av)
        return "push" if val == line else ("won" if ((val > line) == (rest[0] == "OVER")) else "lost")
    return None


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- dérivation de code (texte -> code)
def code_from_pick(pick: str, sport: str, home: str, away: str) -> str:
    """Déduit un code règlable du texte du pick (analyses sans `pick_code`). '' si ambigu."""
    t = (pick or "").lower()
    if not t:
        return ""
    # Le CAMP (équipe/joueur) se lit sur la partie AVANT la parenthèse : le contexte entre parenthèses
    # cite souvent l'AUTRE camp (« Eala remporte un set (Zhang gagne le match) ») et fausserait la
    # détection. Le MARCHÉ (over/under, double chance, ligne…) se lit sur le texte ENTIER `t`.
    t_side = t.split("(")[0]
    # Jetons du nom : mots >= 4 lettres ; REPLI sur >= 2 si aucun (équipes à sigle court : TPS, VPS,
    # PSG…) -> sinon le côté HOME/AWAY reste indéterminé et le handicap/1X2 ne se règle jamais.
    names = lambda s: ([w for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if len(w) >= 4]
                       or [w for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if len(w) >= 2])
    h_all, a_all = names(home), names(away)
    # Désambiguïsation : ignore les jetons COMMUNS aux deux camps (ex. « Ironi » dans
    # « Elitzur Ironi Netanya » vs « Ironi Ness Ziona », ou « Maria » dans « Maria Sakkari »
    # vs « Tatjana Maria ») — sinon le pick matche les deux et le côté reste indéterminé.
    shared = set(h_all) & set(a_all)
    h = [w for w in h_all if w not in shared] or h_all
    a = [w for w in a_all if w not in shared] or a_all

    def which():
        hin = any(w in t_side for w in h)
        ain = any(w in t_side for w in a)
        return "HOME" if (hin and not ain) else ("AWAY" if (ain and not hin) else "")

    def side(kind, yesno=""):
        s = which()
        return f"{kind} {s}{(' ' + yesno) if (s and yesno) else ''}" if s else ""

    # PROPS JOUEUR non couverts par le box-score basique ESPN (interceptions, contres) -> ABSTENTION.
    if any(k in t for k in ("interception", "contre de ", "contres", "double-double", "triple-double")):
        return ""
    # BASKET — props JOUEUR (points/rebonds/passes — y compris COMBINÉS « points, rebonds & passes » et
    # format SEUIL « 20+ points ») d'un joueur NOMMÉ -> PLAYERBK (box-score ESPN, matching strict). Puis
    # marchés par QUART-TEMPS / MI-TEMPS (périodes). Tout AVANT les handlers génériques.
    if sport == "basket":
        _has = {"P": ("point" in t or "panier" in t), "R": "rebond" in t,
                "A": "passe" in t}      # au basket « passe(s) » = passe(s) décisive(s) (assists)
        _combo = "".join(s for s in ("P", "R", "A") if _has[s])
        _stat = {"P": "PTS", "R": "REB", "A": "AST", "PR": "PR", "PA": "PA",
                 "RA": "RA", "PRA": "PRA"}.get(_combo)
        _lead = (pick.strip().split() or [""])[0].lower()
        _kw = ("total", "nombre", "plus", "moins", "score", "le", "les", "over", "under", "écart",
               "ecart", "différence", "difference", "premier", "1er", "handicap")
        if _stat and not which() and _lead not in _kw:
            ln = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
            thr = re.search(r"(\d+)\s*\+", t)            # format seuil « 20+ points » -> plus de 19.5
            if ln:
                dirn, line = ("OVER" if ln.group(1) == "plus" else "UNDER"), ln.group(2).replace(",", ".")
            elif thr:
                dirn, line = "OVER", str(int(thr.group(1)) - 0.5)
            else:
                dirn = line = None
            if line is not None:                         # joueur = texte AVANT la ligne (« plus de » ou « X+ »)
                who = re.split(r"\s+(?:plus|moins)\s+de\s+|\s+\d+\s*\+", pick, maxsplit=1, flags=re.I)[0]
                who = re.sub(r"\s*(points?|paniers?|rebonds?|passes?\s*(?:décisives?|decis\w*)?|[,&et\s])+$",
                             "", who.strip(), flags=re.I).strip(" :-–—")
                if who and len(who) >= 2:
                    return f"PLAYERBK {_stat} {dirn} {line}|{who}"
        spec = None
        if "quart" in t:
            for pat, sp in [(("1er", "premier", "q1", "1 quart"), "1"),
                            (("2e ", "2ème", "2eme", "deuxième", "q2"), "2"),
                            (("3e ", "3ème", "3eme", "troisième", "q3"), "3"),
                            (("4e ", "4ème", "4eme", "quatrième", "q4"), "4")]:
                if any(k in t for k in pat):
                    spec = sp
                    break
        elif "mi-temps" in t or "mi temps" in t:
            spec = "H2" if any(k in t for k in ("2e mi", "2ème mi", "2eme mi", "seconde mi",
                                                "deuxième mi", "2nde mi")) else "H1"
        if spec:
            team = which()
            if "handicap" in t:
                sgn = re.search(r"([+\-−–])\s?(\d+(?:[.,]\d+)?)", t)
                if team and sgn:
                    val = (sgn.group(1).replace("−", "-").replace("–", "-") + sgn.group(2).replace(",", "."))
                    return f"BQHCAP {team} {spec} {val}"
            if any(k in t for k in ("gagne", "vainqueur", "remporte")) and team:
                return f"BQWIN {spec} {team}"
            ln = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
            sg = ln or re.search(r"([+\-])\s?(\d+[.,]?\d*)", t)
            if ln:
                dirn, line = ("OVER" if ln.group(1) == "plus" else "UNDER"), ln.group(2).replace(",", ".")
            elif sg:
                dirn, line = ("UNDER" if sg.group(1) == "-" else "OVER"), sg.group(2).replace(",", ".")
            else:
                return ""
            return f"BQTEAM {team} {spec} {dirn} {line}" if team else f"BQTOT {spec} {dirn} {line}"

    # FOOT — score EXACT (depuis h/a final) et « <équipe> marque dans les DEUX mi-temps » (depuis les
    # périodes : but de l'équipe en 1ère ET en 2e MT).
    if sport == "foot":
        # TEMPS RÉGLEMENTAIRE <équipe>/nul (résultat 90 min, hors prolongation) -> REGTIME
        if "temps réglementaire" in t or "temps reglementaire" in t:
            if "nul" in t or "match nul" in t or "draw" in t or "égalité" in t:
                return "REGTIME DRAW"
            s = which()
            return f"REGTIME {s}" if s else ""
        ms = re.search(r"score\s+exact\s+(\d+)\s*[-–]\s*(\d+)", t)
        if ms:
            return f"SCORE {ms.group(1)} {ms.group(2)}"
        # PREMIER BUTEUR (JOUEUR nommé) -> FIRSTSCORER (events FotMob, matching strict côté règlement).
        if "premier buteur" in t or "1er buteur" in t:
            who = re.sub(r"(?i)\b(?:le\s+)?(?:premier|1er)\s+buteur\b", "", pick).strip(" :-–—")
            if who and len(who) >= 3:
                return f"FIRSTSCORER|{who}"
        # JOUEUR « marque OU passe décisive » (buteur OU passeur) -> SCOREASSIST (events FotMob).
        # Nécessite les DEUX mots (« marque » ET « passe ») pour ne pas capter « passe déc. » (PLAYERFB)
        # ni « marque dans les 2 MT » (TEAMBOTH). Nom = avant le séparateur « - » (sinon avant « marque »).
        if "marque" in t and "passe" in t and "mi-temps" not in t and "mi temps" not in t:
            who = re.split(r"\s+[-–—:]\s+", pick.strip())[0].strip()
            if "marque" in who.lower():
                who = re.split(r"(?i)\bmarque\b", who)[0].strip(" :-–—")
            if who and len(who) >= 3:
                return f"SCOREASSIST|{who}"
        # PREMIER BUT du match par une ÉQUIPE (≠ premier BUTEUR) -> FIRSTGOAL (events FotMob).
        if ("premier but" in t or "1er but" in t or "ouvre le score" in t or "ouvre la marque" in t) \
                and "buteur" not in t and "mi-temps" not in t:
            team = which()
            if team:
                return f"FIRSTGOAL {team}"
        # ARRÊTS DU GARDIEN d'une ÉQUIPE -> GKSAVES (somme des arrêts des GK de l'équipe, FotMob).
        if ("arrêt" in t or "arret" in t) and "gardien" in t:
            team = which()
            ln = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
            if team and ln:
                dirn = "OVER" if ln.group(1) == "plus" else "UNDER"
                return f"GKSAVES {team} {dirn} {ln.group(2).replace(',', '.')}"
        if "marque" in t and ("mi-temps" in t or "mi temps" in t) and (
                "deux mi" in t or "2 mi-temps" in t or ("1ère" in t and ("2e" in t or "2ème" in t))):
            team = which()
            if team:
                return f"TEAMBOTH {team}"
        # PROPS JOUEUR foot (Opta via FotMob) : arrêts(GK)/passe décisive/tacle/faute + tirs (cadrés) d'un
        # joueur NOMMÉ -> PLAYERFB. Le total d'ÉQUIPE (tirs cadrés équipe) reste à la métrique (which()).
        _fs = ("SAVES" if (("arrêt" in t or "arret" in t) and "gardien" not in t)
               else "ASSISTS" if ("passe déc" in t or "passe decis" in t or "passes déc" in t or "passes decis" in t)
               else "TACKLES" if "tacle" in t else "FOULS" if "faute" in t
               else "SOT" if ("tir" in t and ("cadré" in t or "cadre" in t))
               else "SHOTS" if ("tir" in t and "cadr" not in t) else None)
        if _fs and not which():
            ln = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
            # Format Unibet « +0.5 » / « -1.5 » (signé) en plus de « plus/moins de X » (props tirs cadrés
            # joueur : « Bruno Fernandes - Tirs cadrés +0.5 »). « + » = OVER, « - » = UNDER.
            _hc = None if ln else re.search(r"([+\-−–])\s?(\d+(?:[.,]\d+)?)", pick)
            lead = (pick.strip().split() or [""])[0].lower()
            if (ln or _hc) and lead not in ("total", "nombre", "le", "les", "plus", "moins", "arrêts",
                                            "arrets", "premier", "1er", "gardien"):
                _cut = r"\s+(?:plus|moins)\s+de\s+" if ln else r"\s*[+\-−–]\s?\d"
                who = re.split(_cut, pick, maxsplit=1, flags=re.I)[0].strip()
                # Format Unibet « <Joueur> - <marché> » (ex. « Ismaïla Sarr - Tirs cadrés du joueur ») :
                # le NOM est AVANT le « - », le marché APRÈS. Sinon (pas de séparateur), on retire le
                # marché collé en suffixe. Bug vécu 2026-07-01 : sans ça, `who` gardait « - Tirs cadrés
                # du joueur » -> aucun joueur trouvé chez FotMob -> jambe jamais réglée (carte vide).
                if " - " in who:
                    who = who.split(" - ", 1)[0].strip()
                else:
                    who = re.sub(r"\s*(arrêts?|arrets?|passes?\s*décisives?|passes?\s*decis\w*|tacles?|fautes?"
                                 r"|tirs?\s*(?:cadrés?|cadres?)?(?:\s*du\s*joueur)?)\s*$",
                                 "", who, flags=re.I).strip(" -:–—")
                if who and len(who) >= 3:
                    if ln:
                        dirn, lnum = ("OVER" if ln.group(1) == "plus" else "UNDER"), ln.group(2)
                    else:
                        sgn = _hc.group(1).replace("−", "-").replace("–", "-")
                        dirn, lnum = ("UNDER" if sgn == "-" else "OVER"), _hc.group(2)
                    return f"PLAYERFB {_fs} {dirn} {lnum.replace(',', '.')}|{who}"

    # 1er jeu de service tenu (Oui/Non)
    if "jeu de service" in t or ("1er jeu" in t and "service" in t):
        yn = "NO" if (" non" in t or "perd" in t) else "YES"
        return side("HOLD1", yn)
    # TENNIS — marchés spécifiques (AVANT les handlers génériques qui régleraient sur h/a, indéfinis
    # au tennis = mauvais code). Aces/doubles fautes : stats joueur non dispo -> abstention.
    if sport == "tennis":
        if any(k in t for k in ("ace", "double faute", "double-faute")):
            return ""
        if "tie-break" in t or "tie break" in t or "jeu décisif" in t or "jeu decisif" in t:
            return "TIEBREAK NO" if (" non" in t or "aucun" in t or "sans" in t or "pas de" in t) else "TIEBREAK YES"
        # score exact en sets « Score 2-0 », « 2-1 <joueur> » (sans le mot « set »)
        ms = re.search(r"\b([0-3])\s*[-–]\s*([0-3])\b", t)
        if ms and which() and "jeux" not in t and "corner" not in t:
            big, small = ms.group(1), ms.group(2)
            return f"SETSCORE {big} {small}" if which() == "HOME" else f"SETSCORE {small} {big}"
        # handicap de JEUX « <joueur> -3.5 jeux » -> GAMESHCAP (écart total de jeux), JAMAIS HCAP.
        if "jeux" in t and "set" not in t and not ("plus" in t or "moins" in t):
            sgn = re.search(r"([+\-−–])\s?(\d+(?:[.,]\d+)?)", t)
            if sgn and which():
                val = (sgn.group(1).replace("−", "-").replace("–", "-") + sgn.group(2).replace(",", "."))
                return f"GAMESHCAP {which()} {val}"
    # total jeux d'un set (gère les deux ordres, y compris « Set 1 — Plus de 8.5 jeux »)
    m = re.search(r"jeux?\s+(?:du\s+)?set\s*(\d)", t) or re.search(r"set\s*(\d).*?jeux", t)
    if m and ("plus" in t or "moins" in t):
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            return f"SETGAMES {m.group(1)} {'OVER' if ln.group(1)=='plus' else 'UNDER'} {ln.group(2).replace(',', '.')}"
    # vainqueur d'un set précis : « remporte/gagne le set N » OU « Set N : Nom » / « Set N - Nom »
    m = re.search(r"(?:remporte|gagne)\s+le\s+set\s*(\d)", t) or re.search(r"\bset\s*(\d)\s*[:\-–]", t)
    if m:
        s = which()
        return f"SETWIN {m.group(1)} {s}" if s else ""
    # au moins un set (« un » ou « 1 »)
    if re.search(r"au moins (?:un|1) set", t) or re.search(r"(?:≥|>=)\s*1\s*set", t):
        return side("SET")
    # score exact en sets (tennis), ex. « pari de set 2-0 Kasatkina » -> sets du vainqueur nommé
    m = re.search(r"set\s*(\d)\s*[-–]\s*(\d)", t)
    if m and which():
        big, small = m.group(1), m.group(2)
        return f"SETSCORE {big} {small}" if which() == "HOME" else f"SETSCORE {small} {big}"
    # total jeux du MATCH (« Total de jeux moins de X.5 ») OU jeux d'UN JOUEUR (« <joueur> moins de X.5
    # jeux » = jeux gagnés par ce joueur -> TEAMGAMES, réglé sur la somme de SA colonne par set ; sinon
    # un « X moins de 10.5 jeux » serait réglé sur le total du MATCH = à l'envers).
    if "jeux" in t and ("plus" in t or "moins" in t) and "set" not in t:
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            dirn = "OVER" if ln.group(1) == "plus" else "UNDER"
            line = ln.group(2).replace(",", ".")
            w = which()
            is_match_total = any(k in t for k in ("total de jeux", "total des jeux", "jeux du match",
                                                  "jeux dans le match", "nombre de jeux"))
            if w and not is_match_total:
                return f"TEAMGAMES {w} {dirn} {line}"
            return f"TOTGAMES {dirn} {line}"
    # total de SETS du match (tennis) : « plus/moins de N sets » OU « (nombre) total de sets : moins de N »
    m = re.search(r"(plus|moins) de (\d+[.,]?\d*)\s*sets?\b", t)
    if not m and re.search(r"(?:total|nombre)[^.]{0,14}sets?", t):
        m = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
    if m:
        return f"SETSTOT {'OVER' if m.group(1)=='plus' else 'UNDER'} {m.group(2).replace(',', '.')}"
    # marchés MI-TEMPS (réglés via le score PAR mi-temps ET PAR équipe — LiveScore periods[1]/[2]) :
    # buts d'une équipe/du match dans une MT, « gagne une mi-temps ». Corners/cartons/tirs en MT :
    # laissés à la MÉTRIQUE (abstention "") comme avant.
    if "mi-temps" in t or "mi temps" in t:
        if "deux mi" in t or "2 mi-temps" in t or "both halves" in t:
            team = which()
            if team:                               # « <équipe> (marque un but) dans les 2 MT » -> TEAMBOTH
                return f"TEAMBOTH {team}"
            # « But dans les deux mi-temps Oui/Non » (total, n'importe quelle équipe) -> BOTHHALVES
            return f"BOTHHALVES {'NO' if (' non' in t or 'aucun' in t) else 'YES'}"
        if "deux équipes marquent" in t or "btts" in t:   # BTTS dans UNE mi-temps -> périodes
            half = "2H" if any(k in t for k in ("2e mi", "2ème mi", "2eme mi", "seconde mi",
                                                "deuxième mi", "2nde mi")) else "1H"
            return f"BTTSHALF {half} {'NO' if 'non' in t else 'YES'}"
        team = which()
        if ("gagne" in t or "remporte" in t or "vainqueur" in t) and team and "but" not in t:
            # « … Non » = l'équipe ne gagne AUCUNE mi-temps -> WINHALF … NO (sinon réglé comme « Oui »,
            # bug vu sur NZ-Belgique : « NZ gagne une MT Non » marqué perdu alors que NZ a perdu les 2).
            neg = "non" in re.findall(r"[a-zà-ÿ]+", t)
            return f"WINHALF {team} NO" if neg else f"WINHALF {team}"   # gagne AU MOINS une mi-temps
        if "but" in t and not any(k in t for k in ("corner", "carton", "tir")):
            half = "2H" if any(k in t for k in ("2e mi", "2ème mi", "2eme mi", "2nde mi",
                                                "seconde mi", "deuxième mi")) else "1H"
            ln = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
            if ln:
                dirn, line = ("OVER" if ln.group(1) == "plus" else "UNDER"), ln.group(2).replace(",", ".")
            else:
                sgn = re.search(r"([+\-])\s?(\d+[.,]?\d*)", t)
                if not sgn:
                    return ""
                dirn, line = ("UNDER" if sgn.group(1) == "-" else "OVER"), sgn.group(2).replace(",", ".")
            return f"TEAMHALF {team} {half} {dirn} {line}" if team else f"HALFTOT {half} {dirn} {line}"
        # DOUBLE CHANCE sur une mi-temps -> DCHALF (réglé sur le score de la période). Doit passer AVANT
        # le HALFRES/return "" ci-dessous (sinon « Double Chance - 1ère mi-temps X2 » finissait à code vide).
        if "double chance" in t or "ou nul" in t or "ou match nul" in t:
            half = "2H" if any(k in t for k in ("2e mi", "2ème mi", "2eme mi", "2nde mi",
                                                "seconde mi", "deuxième mi")) else "1H"
            dc = ("1X" if ("1x" in t or (team == "HOME" and "nul" in t))
                  else "X2" if ("x2" in t or (team == "AWAY" and "nul" in t))
                  else "12" if "12" in t else None)
            if dc:
                return f"DCHALF {half} {dc}"
        # « (2ème) mi-temps <équipe|nul> » SANS autre marché = VAINQUEUR (résultat 1X2) de CETTE
        # mi-temps -> HALFRES, réglé sur le score de la période. (≠ WINHALF « gagne AU MOINS une ».)
        if not any(k in t for k in ("corner", "carton", "tir", "but")):
            half = "2H" if any(k in t for k in ("2e mi", "2ème mi", "2eme mi", "2nde mi",
                                                "seconde mi", "deuxième mi")) else "1H"
            if team:
                return f"HALFRES {team} {half}"
            if "nul" in t:
                return f"HALFRES DRAW {half}"
        return ""                                  # autres marchés mi-temps -> inchangé
    # PREMIER À X POINTS (course au score) : 1re équipe à atteindre X points — réglé via les incidents
    # (event/{id}/incidents donne le score cumulé à chaque panier). Équipe nommée obligatoire.
    m = re.search(r"premi\w*\s+à\s+(\d+)\s*point", t) or re.search(r"first\s+to\s+(\d+)", t)
    if m and which():
        return f"FIRSTTO {which()} {m.group(1)}"
    # CARTONS / CORNERS : réglés depuis les STATS réelles du match (cf. _event_stats). Équipe nommée
    # -> total d'équipe ; sinon total du match. Carton ROUGE = marché oui/non (seuil 0.5).
    fam = "CARDS" if ("carton" in t or "card" in t) else ("CORNERS" if "corner" in t else "")
    if fam:
        sd = which()
        red = fam == "CARDS" and "rouge" in t
        base = (f"REDCARDS {sd}" if red else f"{fam} {sd}").strip()
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            return f"{base} {'OVER' if ln.group(1) == 'plus' else 'UNDER'} {ln.group(2).replace(',', '.')}"
        if red:   # marché binaire oui/non sans ligne -> seuil 0.5
            neg = any(w in t for w in ("aucun", "sans", " non", "pas de"))
            return f"{base} {'UNDER' if neg else 'OVER'} 0.5"
        # ligne SIGNÉE sans « plus/moins de » : « Total corners +7.5 » -> OVER, « ... -7.5 » -> UNDER.
        sgn = re.search(r"([+\-])\s*(\d+[.,]?\d*)", t)
        if sgn:
            return f"{base} {'UNDER' if sgn.group(1) == '-' else 'OVER'} {sgn.group(2).replace(',', '.')}"
        return ""    # carton/corner sans ligne exploitable -> on s'abstient
    # TIRS / TIRS CADRÉS (total du match ou d'une ÉQUIPE) -> SHOTSOT (cadrés) / SHOTS (tous), réglés sur
    # les STATS du match (Flashscore sot_h/a & shots_h/a, DÉJÀ récupérées, cf. _settle bloc need_stats).
    # Le pari JOUEUR est capté plus haut (PLAYERFB). NE JAMAIS laisser tomber dans TEAMTOT/OVER (= BUTS,
    # réglé à l'envers) : d'où le handler dédié ici, comme CORNERS/CARTONS.
    if re.search(r"\btirs?\b", t) or "shot" in t or "cadré" in t or "cadre" in t:
        base = (("SHOTSOT" if ("cadr" in t or "on target" in t) else "SHOTS") + " " + which()).strip()
        ln = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if ln:
            return f"{base} {'OVER' if ln.group(1) == 'plus' else 'UNDER'} {ln.group(2).replace(',', '.')}"
        sgn = re.search(r"([+\-−–])\s*(\d+[.,]?\d*)", t)
        if sgn:
            return f"{base} {'UNDER' if sgn.group(1) in ('-', '−', '–') else 'OVER'} {sgn.group(2).replace(',', '.')}"
        return ""    # tirs sans ligne exploitable -> abstention
    team = which()
    # total d'une ÉQUIPE (le score par équipe est connu) : « X marque +1.5 », « X +/- de N buts/pts »
    if team:
        mt = re.search(r"(plus|moins)\s+de\s+(\d+[.,]?\d*)", t)
        mm = re.search(r"marque\w*\s*\+?\s*(\d+[.,]?\d*)", t)
        if mt:
            return f"TEAMTOT {team} {'OVER' if mt.group(1)=='plus' else 'UNDER'} {mt.group(2).replace(',', '.')}"
        if mm:
            return f"TEAMTOT {team} OVER {mm.group(1).replace(',', '.')}"
        # ligne SIGNÉE d'un total d'ÉQUIPE : « <équipe> -1.5 buts » (= MOINS de 1.5) / « <équipe> +1.5 buts »
        # (= PLUS de). Le mot but/point distingue du HANDICAP (« <équipe> -1.5 » SANS unité, traité plus bas).
        # Vital : l'analyste abrège « moins de 1.5 buts » en « -1.5 buts » dans la ligne COMBO: -> sans ça,
        # c'était lu comme un handicap (ex. « Afrique du Sud -1.5 buts » réglé HCAP AWAY -1.5 = faux).
        if "handicap" not in t:
            sgn = re.search(r"([+\-−–])\s?(\d+[.,]?\d*)\s*(?:buts?|points?|pts?)", t)
            if sgn:
                return f"TEAMTOT {team} {'UNDER' if sgn.group(1) in ('-', '−', '–') else 'OVER'} {sgn.group(2).replace(',', '.')}"
    # total du MATCH (sans équipe nommée) — accepte buts/points + abréviations « pt / pts »
    m = re.search(r"(plus|moins) de (\d+[.,]?\d*)\s*(?:buts?|points?|pts?)", t)
    if m and not team:
        return f"{'OVER' if m.group(1)=='plus' else 'UNDER'} {m.group(2).replace(',', '.')}"
    # variante avec l'unité AVANT le nombre : « Nombre total de buts – Moins de 2.5 », ou « Total
    # Moins de 3.5 » sans unité (sets/jeux/cartons/corners déjà traités plus haut -> ici = total du
    # MATCH, buts foot / points basket).
    if not team and "total" in t:
        m2 = re.search(r"(plus|moins) de (\d+[.,]?\d*)", t)
        if m2:
            return f"{'OVER' if m2.group(1)=='plus' else 'UNDER'} {m2.group(2).replace(',', '.')}"
        # ligne SIGNÉE « Total de buts +1.5 » -> OVER, « ... -1.5 » -> UNDER (notation sans « plus de »).
        sgn = re.search(r"([+\-−–])\s?(\d+[.,]?\d*)", t)
        if sgn:
            return f"{'UNDER' if sgn.group(1) in ('-', '−', '–') else 'OVER'} {sgn.group(2).replace(',', '.')}"
    # « Plus de X » / « Moins de X » SEUL (sans unité, sans équipe) = total du MATCH (points basket
    # « Plus de 162.5 », buts foot). Les marchés à mot-clé (corners/cartons/tirs/sets/jeux) ont déjà
    # été traités/abstenus -> ici, un simple seuil chiffré = le total du match. (Vu : combiné basket
    # WNBA resté coincé sur « Plus de 162.5 » à code vide.)
    if not team:
        mu = re.search(r"(plus|moins)\s+de\s+(\d+(?:[.,]\d+)?)", t)
        if mu:
            return f"{'OVER' if mu.group(1) == 'plus' else 'UNDER'} {mu.group(2).replace(',', '.')}"
    # HANDICAP 3 VOIES « (X-Y) » : handicap avec score de DÉPART (ex. « 3-Way Handicap (1-0) <équipe> »).
    # Réglé sur le score FINAL ajusté (h+X vs a+Y) -> 1X2. DOIT passer AVANT le handicap générique :
    # « (1-0) » contient « -0 » qui serait sinon lu comme un handicap simple.
    if "handicap" in t and re.search(r"3[\s-]?way|3\s*voies|trois\s*voies", t):
        m3 = re.search(r"\(\s*(\d+)\s*[-:]\s*(\d+)\s*\)", t)
        if m3:
            x, y = m3.group(1), m3.group(2)
            if "nul" in t:
                return f"HCAP3 DRAW {x} {y}"
            # le nom d'équipe vient APRÈS « (X-Y) » -> which() (qui ne lit qu'AVANT la parenthèse) le
            # rate ; on détecte le camp sur le texte ENTIER.
            hin, ain = any(w in t for w in h), any(w in t for w in a)
            w = "HOME" if (hin and not ain) else ("AWAY" if (ain and not hin) else "")
            if w:
                return f"HCAP3 {w} {x} {y}"
    # handicap depuis le score final : « Équipe +X.X » / « Équipe -X.X » / « handicap Équipe +X.X »
    mh = re.search(r"([+\-−–]\s?\d+(?:[.,]\d+)?)", t)   # accepte le moins ASCII, Unicode (−) et tiret (–)
    if mh and team:
        # handicap en SETS (tennis, « X -1.5 set ») -> réglé sur les sets ; sinon points/buts.
        kind_h = "SETHCAP" if re.search(r"\bsets?\b", t) else "HCAP"
        val = (mh.group(1).replace(" ", "").replace(",", ".").replace("−", "-").replace("–", "-"))
        return f"{kind_h} {team} {val}"
    if "deux équipes marquent" in t or "btts" in t:
        if ("mi-temps" in t or "mi temps" in t) and "deux mi" not in t:   # BTTS dans UNE mi-temps -> périodes
            half = "2H" if any(k in t for k in ("2e mi", "2ème mi", "2eme mi", "seconde mi",
                                                "deuxième mi", "2nde mi")) else "1H"
            return f"BTTSHALF {half} {'NO' if 'non' in t else 'YES'}"
        return "BTTS NO" if "non" in t else "BTTS YES"
    if "double chance" in t:
        for k in ("1x", "12", "x2"):
            if k in t:
                if "mi-temps" in t or "mi temps" in t:   # double chance sur UNE mi-temps -> DCHALF
                    half = "2H" if any(x in t for x in ("2e mi", "2ème mi", "2eme mi", "seconde mi",
                                                        "deuxième mi", "2nde mi")) else "1H"
                    return f"DCHALF {half} {k.upper()}"
                return f"DC {k.upper()}"
    # Double chance phrasée « <équipe> ou nul » (= domicile/extérieur OU match nul) — fréquent en
    # jambe de combiné (« Angleterre ou nul »), échappait au filtre « double chance » -> code vide.
    if "ou nul" in t or "ou match nul" in t or "ou le nul" in t:
        w = which()
        if w == "HOME":
            return "DC 1X"
        if w == "AWAY":
            return "DC X2"
    if any(x in t for x in ("vainqueur", "gagne", "victoire")):
        if sport == "foot":
            s = side("X")
            return "1X2 1" if s.endswith("HOME") else ("1X2 2" if s.endswith("AWAY") else "")
        return side("WIN")
    # NOM D'ÉQUIPE SEUL (sans mot « gagne ») = MONEYLINE : cette équipe gagne. Fréquent en jambe de
    # combiné (« Las Vegas Aces (F) »). En DERNIER recours UNIQUEMENT, et seulement s'il ne reste AUCUN
    # mot-clé de marché (chiffre/seuil/…) -> aucun faux positif. (Vu : combinés WNBA à code vide.)
    w = which()
    if w and not re.search(r"\d|plus|moins|but|point|corner|carton|\btir|set|jeu|handicap|chance|"
                           r"mi.?temps|marque|over|under|cadr|total|nul", t):
        return ("1X2 1" if w == "HOME" else "1X2 2") if sport == "foot" else f"WIN {w}"
    return ""


# --------------------------------------------------------------- récupération des données SofaScore
def _score_from_event(sport: str, ev: dict) -> dict | None:
    st = ((ev.get("status") or {}).get("type") or "").lower()
    if st not in ("finished", "afterextra", "penalties"):
        return None
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    hc, ac = hs.get("current"), as_.get("current")
    if hc is None or ac is None:
        return None
    periods = {}
    for i in range(1, 6):
        ph, pa = hs.get(f"period{i}"), as_.get(f"period{i}")
        if ph is not None and pa is not None:
            periods[i] = (ph, pa)
    base = {"periods": periods, "first_serve": ev.get("firstToServe")}
    if sport == "tennis":
        base.update({"home": None, "away": None, "sets_home": hc, "sets_away": ac,
                     "label": f"{hc}-{ac} (sets)"})
    else:
        base.update({"home": hc, "away": ac, "sets_home": None, "sets_away": None,
                     "label": f"{hc}-{ac}"})
    return base


async def _event_votes(sofa: str) -> tuple | None:
    """Votes communauté (%home, %away, %draw) via event/{id}/votes. None si indispo. Sert à FIGER le
    sentiment public dans le sidecar (pub_*) quand le scan n'a pas pu les capturer (SofaScore bloqué)."""
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/votes")
        if r.status_code != 200:
            return None
        vote = (r.json() or {}).get("vote") or {}
        v1, vx, v2 = vote.get("vote1"), vote.get("voteX"), vote.get("vote2")
        total = (v1 or 0) + (vx or 0) + (v2 or 0)
        if not total:
            return None
        pct = lambda v: round(100 * v / total, 1) if v is not None else None
        return (pct(v1), pct(v2), pct(vx))
    except Exception:
        return None


async def _event_data(sport: str, sofa: str) -> dict | None:
    """Score complet (final + jeux par set + firstToServe) via event/{id}. None si pas fini."""
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}")
        if r.status_code != 200:
            return None
        return _score_from_event(sport, (r.json() or {}).get("event") or {})
    except Exception:
        return None


def _statnum(v):
    """Extrait l'entier en tête d'une valeur de stat SofaScore (ex. '5', '5 (60%)'). None sinon."""
    m = re.search(r"-?\d+", str(v if v is not None else ""))
    return int(m.group()) if m else None


async def _event_stats(sofa: str) -> dict | None:
    """Corners & cartons (jaunes/rouges) RÉELS d'un match terminé via event/{id}/statistics — dispo
    même après le match. Renvoie {corners_h/a, yc_h/a, rc_h/a, cards_h/a} ou None si indisponible."""
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/statistics")
        if r.status_code != 200:
            return None
        st = r.json() or {}
    except Exception:
        return None
    out: dict = {}
    for grp in st.get("statistics", []) or []:
        if grp.get("period") != "ALL":
            continue
        for sub in grp.get("groups", []) or []:
            for it in sub.get("statisticsItems", []) or []:
                nm = (it.get("name") or "").lower()
                if "corner" in nm:
                    out["corners_h"], out["corners_a"] = _statnum(it.get("home")), _statnum(it.get("away"))
                elif nm in ("yellow cards", "cartons jaunes"):
                    out["yc_h"], out["yc_a"] = _statnum(it.get("home")), _statnum(it.get("away"))
                elif nm in ("red cards", "cartons rouges"):
                    out["rc_h"], out["rc_a"] = _statnum(it.get("home")), _statnum(it.get("away"))
    if not out:
        return None
    # Stats présentes (match fini) mais pas de ligne « rouges » -> 0 carton rouge (et non « inconnu »).
    out.setdefault("rc_h", 0)
    out.setdefault("rc_a", 0)
    out["cards_h"] = (out.get("yc_h") or 0) + out["rc_h"]
    out["cards_a"] = (out.get("yc_a") or 0) + out["rc_a"]
    return out


async def _settle_hold1_flashscore(code: str, d: dict) -> str | None:
    """Repli GRATUIT pour « X tient son 1er jeu de service (Oui/Non) » via le jeu-par-jeu Flashscore
    (quand SofaScore est indisponible). code = 'HOLD1 HOME|AWAY YES|NO'. None si données absentes."""
    parts = code.upper().split()
    if len(parts) < 3:
        return None
    want_yes = parts[2] != "NO"
    import asyncio
    from app import flashscore
    held = await asyncio.to_thread(flashscore.settle_hold1,
                                   d.get("home", ""), d.get("away", ""), parts[1], d.get("start"))
    if held is None:
        return None
    return "won" if ((held == "won") == want_yes) else "lost"


async def _settle_hold1(sofa: str, code: str, score: dict) -> str | None:
    """Règle « X tient son 1er jeu de service (Oui/Non) » via le point-by-point. NB : `firstToServe`
    est peu fiable -> on cherche directement le PREMIER jeu du set 1 où CE joueur sert."""
    parts = code.upper().split()
    if len(parts) < 3:
        return None
    side = 1 if parts[1] == "HOME" else 2
    want_yes = parts[2] != "NO"
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/point-by-point")
        pbp = (r.json() or {}).get("pointByPoint") or [] if r.status_code == 200 else []
    except Exception:
        return None
    set1 = next((s for s in pbp if s.get("set") == 1), None)
    if not set1:
        return None
    games = sorted(set1.get("games") or [], key=lambda g: g.get("game") or 0)
    mine = next((g for g in games if (g.get("score") or {}).get("serving") == side), None)
    sc = (mine or {}).get("score") or {}
    if sc.get("scoring") not in (1, 2):
        return None
    held = sc.get("scoring") == side                # le serveur gagne son jeu = il le tient
    return "won" if (held == want_yes) else "lost"


async def _settle_firstto(sofa: str, code: str) -> str | None:
    """Règle « premier à X points » via event/{id}/incidents (score CUMULÉ à chaque panier). code =
    'FIRSTTO HOME 10' / 'FIRSTTO AWAY 10'. won si le camp nommé atteint X le PREMIER. None si indispo."""
    parts = code.split()
    if len(parts) < 3:
        return None
    want, n = parts[1], int(parts[2])
    try:
        r = await sofa_http.get(f"{_SOFA}/event/{sofa}/incidents")
        inc = (r.json() or {}).get("incidents") or [] if r.status_code == 200 else []
    except Exception:
        return None
    seq = [i for i in inc if i.get("homeScore") is not None and i.get("awayScore") is not None]
    if not seq:
        return None
    seq.sort(key=lambda i: i.get("id") or 0)            # ordre chronologique (l'API renvoie décroissant)
    for i in seq:
        h, a = i.get("homeScore"), i.get("awayScore")
        if h >= n or a >= n:                            # 1 panier/équipe -> 1 seul camp franchit X ici
            winner = "HOME" if (h >= n and h >= a) else "AWAY"
            return "won" if winner == want else "lost"
    return None


# --------------------------------------------------------------- repli scheduled-events (foot non résolu)
def _toks(s: str) -> set:
    return {w for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if len(w) >= 3}


async def _schedule_scores(sport: str, day: str):
    path = _SPORT_PATH.get(sport)
    by_id, by_name = {}, []
    if not path or not day:
        return by_id, by_name
    try:
        r = await sofa_http.get(f"{_SOFA}/sport/{path}/scheduled-events/{day}")
        evs = (r.json() or {}).get("events") or [] if r.status_code == 200 else []
    except Exception:
        evs = []
    for ev in evs:
        sc = _score_from_event(sport, ev)
        if not sc:
            continue
        eid = str(ev.get("id"))
        by_id[eid] = sc
        by_name.append((_toks((ev.get("homeTeam") or {}).get("name")),
                        _toks((ev.get("awayTeam") or {}).get("name")), sc, eid))
    return by_id, by_name


def _find_score(sched, d: dict) -> tuple:
    """(score, sofa_id) du match dans scheduled-events : par id, sinon par NOMS (retourne aussi
    l'id SofaScore résolu -> permet de FIGER le vrai sofa_id quand le scan ne l'avait pas)."""
    by_id, by_name = sched
    sofa = str(d.get("sofa_id") or "")
    if sofa in by_id:
        return by_id[sofa], sofa
    mh, ma = _toks(d.get("home", "")), _toks(d.get("away", ""))
    if not mh or not ma:
        return None, None
    # Matching par NOMS renforcé : on collecte TOUS les candidats des events du jour (la date est déjà
    # filtrée en amont). Si PLUSIEURS matchent (sigles courts ambigus type PSG/PSV, ou un tournoi avec
    # équipes homonymes), on S'ABSTIENT (None) plutôt que d'accepter le 1er au hasard -> jamais un FAUX
    # score sur un mauvais match ; le règlement re-tente via les autres sources / à la passe suivante.
    cands = [(sc, eid) for h, a, sc, eid in by_name
             if (h & mh and a & ma) or (h & ma and a & mh)]
    if len(cands) == 1:
        return cands[0]
    return None, None


# --------------------------------------------------------------- passe de règlement
def _mark_notified(side: str, flags: list, result_msg: dict | None = None) -> None:
    """R2 — FIGE les flags `notified_*` sur le sidecar, mais SEULEMENT après envoi Telegram réussi.
    Relit le sidecar (qui porte déjà le résultat persisté par la passe), pose les flags, réécrit en
    ATOMIQUE (.tmp + os.replace) pour ne jamais laisser un fichier à moitié écrit. No-op si échec :
    le pari reste « réglé non notifié » et sera re-tenté à la passe suivante (borné par notify_tries).
    `result_msg` = {chat: message_id} de la carte résultat envoyée -> mémorisé pour AUTO-RÉPARATION
    (suppression de cette carte si le règlement est corrigé plus tard)."""
    try:
        dd = json.load(open(side, encoding="utf-8"))
    except (OSError, ValueError):
        return
    for fl in flags:
        dd[fl] = True
    if result_msg:
        dd["result_msg"] = result_msg
    try:
        tmp = side + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dd, f, ensure_ascii=False)
        os.replace(tmp, side)
    except OSError:
        pass


async def settle_analyses() -> int:
    """Règle TOUS les matchs analysés terminés. Code = `pick_code` sinon dérivé. Score via
    event/{id} (id Sofa valide : donne aussi jeux par set + 1er service) ; repli scheduled-events
    par noms (foot non résolu). HOLD1 -> point-by-point. Renvoie le nombre de sidecars écrits."""
    if _settle_lock.locked():          # une passe tourne déjà -> ne pas en lancer une 2e en parallèle
        return 0
    async with _settle_lock:
        return await _settle_analyses_impl()


async def _settle_analyses_impl() -> int:
    pending = []
    for side in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(side, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        res = d.get("result")
        # On re-règle aussi : (a) les matchs d'une VERSION de logique antérieure (nouveaux marchés :
        # handicap, total d'équipe, score exact…) ; (b) ceux dont le sentiment public (pub_*) manque
        # ET qui ont un VRAI sofa_id (≤ 8 chiffres) -> on retente le backfill des votes jusqu'à
        # capture. Le règlement réutilise `result.raw` caché (0 réseau) ; seul le backfill appelle le
        # réseau. (Les matchs sans sofa_id exploitable ne sont PAS retentés en boucle.)
        # On retente le backfill des votes tant que le public manque (borné à 3 essais : la résolution
        # de l'id SofaScore par noms peut échouer si SofaScore est temporairement bloqué).
        votes_pending = (d.get("pub_home") is None and (d.get("votes_tries") or 0) < 3)
        # Combiné dont le verdict GLOBAL manque encore (jambes réglées au compte-gouttes : stats df_st
        # parfois en retard sur la fin du match) -> on retente pour le compléter.
        cmb = d.get("combo") or {}
        # TANT que le verdict global manque, on RE-TRAITE (plus de plafond d'abandon) : la finalisation
        # ci-dessous GARANTIT désormais un verdict une fois le match réellement terminé (jambe
        # indéterminée -> VOID, on règle sur le reste). Donc pas de boucle infinie : un match fini est
        # tranché en une passe ; un match pas encore fini est simplement re-tenté (comme avant).
        combo_pending = (bool(cmb.get("legs")) and cmb.get("result") is None)
        # Pari « le plus sûr » NON réglable sur un match pourtant fini (ex. tennis abandonné avant le 1er
        # set : 0-0 en sets, pas de vainqueur) -> on retente quelques fois puis on ABANDONNE (sinon le
        # sidecar n'est jamais figé et on le re-traite à chaque boucle indéfiniment).
        pick_settled = bool(res and res.get("pick_result") is not None)
        pick_giveup = (not pick_settled and (d.get("pick_tries") or 0) >= 6)
        # R2 — notif perdue à renvoyer : un pari réglé dont la notif n'est PAS encore PARTIE
        # (carte construite mais crash/échec d'envoi AVANT l'envoi) doit être re-traité. `notified_*`
        # est posé SEULEMENT après envoi réussi (cf. boucle de notif) -> « réglé ET non notifié »
        # = à ré-émettre. Le simple NON affiché pose son flag tout de suite (rien à envoyer) -> exclu.
        # Borné (notify_tries < 5) pour ne pas boucler si Telegram reste injoignable.
        _NRES = ("won", "lost", "push")
        notify_pending = ((((res or {}).get("pick_result") in _NRES and not d.get("notified_pick"))
                           or (bool(cmb.get("legs")) and cmb.get("result") in _NRES
                               and not d.get("notified_combo")))
                          and (d.get("notify_tries") or 0) < 5)
        if ((pick_settled or pick_giveup)
                and d.get("settle_v") == _SETTLE_VERSION
                and not votes_pending and not combo_pending and not notify_pending):
            continue
        # On tente le règlement dès que le match est PROBABLEMENT fini (`likely_finished`), pas seulement
        # à la fin de la fenêtre `status_of` (souvent trop longue, ex. tennis 210 min) : SofaScore
        # `event/{id}` GÈRE le filtre (score lu UNIQUEMENT si l'event est réellement terminé) -> un match
        # pas encore fini est simplement re-tenté à la boucle suivante. Évite le « terminé » sans score.
        if analyses.status_of(d) == "finished" or analyses.likely_finished(d):
            pending.append((side, d))
    if not pending:
        return 0
    n = 0
    sched_cache: dict = {}
    notify_msgs: list[str] = []   # transitions « en attente -> réglé » -> notif Telegram (fin de boucle)
    notify_cards: list = []       # données CARTE IMAGE de résultat (parallèle à notify_msgs)
    prev_bulk = sofa_http.allow_bulk_proxy
    sofa_http.allow_bulk_proxy = True   # autorise scheduled-events (repli) pendant le règlement
    try:
        for side, d in pending:
            sport = d.get("sport")
            # État AVANT règlement (lu du disque) -> dédup naturel : un re-règlement (bump de version)
            # ne re-notifie pas, l'ancien résultat n'étant plus None.
            prev_pick = (d.get("result") or {}).get("pick_result")
            prev_combo = (d.get("combo") or {}).get("result")
            code = (d.get("pick_code")
                    or code_from_pick(d.get("pick", ""), sport, d.get("home", ""), d.get("away", "")))
            sofa = str(d.get("sofa_id") or "")
            score = (d.get("result") or {}).get("raw")
            if _score_incomplete(score, sport):        # cache périmé (capté en cours) -> re-fetch frais
                score = None
            if not score:
                if sofa and len(sofa) <= 8:
                    score = await _event_data(sport, sofa)
                if not score:                       # repli : scheduled-events du jour, par noms
                    day = (d.get("start") or "")[:10]
                    if day:
                        if (sport, day) not in sched_cache:
                            sched_cache[(sport, day)] = await _schedule_scores(sport, day)
                        score, _ = _find_score(sched_cache[(sport, day)], d)
                if not score:                       # repli n°2 : sources GRATUITES (ESPN/FotMob) —
                    # SofaScore bloqué ne doit plus laisser des paris « en attente » indéfiniment.
                    # Couvre foot (FotMob), tennis ATP/WTA et basket NBA/WNBA (ESPN) ; les marchés
                    # stats (cartons/corners/HOLD1/FIRSTTO) restent pour SofaScore.
                    try:
                        from app import sources
                        score = await sources.final_score(sport, d)
                        if score:
                            log.info("règlement via %s : %s_%s %s", score.get("src", "alt"),
                                     sport, d.get("id"), score.get("label"))
                    except Exception:
                        score = None
                if not score:                       # repli n°3 : LiveScore (JSON propre, 3 sports,
                    # indépendant de SofaScore ; score détaillé mi-temps/quart-temps/sets/tie-breaks).
                    try:
                        from app import livescore
                        score = await asyncio.to_thread(livescore.final_score, sport, d)
                        if score:
                            log.info("règlement via livescore : %s_%s %s",
                                     sport, d.get("id"), score.get("label"))
                    except Exception:
                        score = None
                if not score:                       # repli n°4 : FLASHSCORE — couverture quasi UNIVERSELLE
                    # des ligues (NBL, BSN, ligues mineures…). Le score final est dans l'index ; le match
                    # est cherché par NOMS au jour du coup d'envoi. Règle les tickets qu'aucune autre
                    # source ne couvre (« régler tous les tickets »).
                    try:
                        from app import flashscore
                        score = await asyncio.to_thread(flashscore.final_score, sport, d)
                        if score:
                            log.info("règlement via flashscore : %s_%s %s",
                                     sport, d.get("id"), score.get("label"))
                    except Exception:
                        score = None
                if not score:
                    # Aucune source n'a ENCORE le score -> on RÉ-ESSAIE à la passe suivante (le score/
                    # les sets arrivent souvent avec du retard : ne JAMAIS trancher « nul » sur un score
                    # manquant, on veut le VRAI résultat). Le combiné reste simplement en attente.
                    # DERNIER RECOURS (complétude) : si le match est fini DEPUIS LONGTEMPS (> _VOID_AFTER_DAYS)
                    # et qu'AUCUNE source (tous les replis ci-dessus) n'a JAMAIS eu le score (ligues obscures :
                    # basket féminin « petits pays », qualifs mineures…), le prono resterait pending À VIE —
                    # le `continue` ne faisait jamais monter les tries ni atteindre la logique void du bloc
                    # combo, qui EXIGE un score. On VOID (remboursé = neutre) pour GARANTIR que tout prono
                    # d'un match terminé finit réglé. (cf. mémoire combo-settlement-void-guarantee : void =
                    # ULTIME recours, jamais sur score simplement en retard.)
                    if analyses.status_of(d) == "finished" and _match_age_days(d) >= _VOID_AFTER_DAYS:
                        _cmb = d.get("combo") or {}
                        if _cmb.get("legs") and _cmb.get("result") is None:
                            for _lg in _cmb["legs"]:
                                if _lg.get("result") is None:
                                    _lg["result"] = "void"
                            _cmb["result"] = "void"
                        if (d.get("result") or {}).get("pick_result") is None:
                            d.setdefault("result", {})["pick_result"] = "void"
                        for _b in (d.get("bets") or []):
                            if _b.get("result") is None:
                                _b["result"] = "void"
                        d["settle_v"] = _SETTLE_VERSION
                        d["noscore_void"] = True          # trace : réglé faute de score (pas un vrai résultat)
                        try:
                            json.dump(d, open(side, "w", encoding="utf-8"), ensure_ascii=False)
                            n += 1
                            log.info("VOID dernier recours %s_%s (score introuvable, match +%.1fj)",
                                     sport, d.get("id"), _match_age_days(d))
                        except OSError:
                            pass
                    continue
            # Pré-calcule les codes de TOUS les paris affichés -> on sait si on a besoin des STATS du
            # match (cartons/corners). SofaScore les expose même APRÈS le match (event/{id}/statistics).
            mid = os.path.basename(side)[len(sport) + 1:-5]    # {sport}_{id}.json -> id
            bet_list = analyses.bets_of(sport, mid)
            bet_codes = [code_from_pick(b["sel"], sport, d.get("home", ""), d.get("away", ""))
                         for b in bet_list]
            combo_codes = [leg.get("code", "") for leg in ((d.get("combo") or {}).get("legs") or [])]
            # Stats du match (corners/cartons/tirs) nécessaires si un code les vise OU si un combiné foot
            # est présent (ses jambes tirs/tirs cadrés/corners/cartons se règlent sur les stats df_st).
            need_stats = (any(c.startswith(("CARDS", "REDCARDS", "CORNERS", "SHOTSOT", "SHOTS"))
                              for c in [code, *bet_codes, *combo_codes])
                          or (sport == "foot" and (d.get("combo") or {}).get("legs")))
            # Combiné foot au verdict incomplet -> on REFETCH les stats même si le cache `result.raw`
            # en a déjà : elles peuvent être PARTIELLES (df_st en retard à la fin du match, sans les
            # tirs/1ère MT) et bloquer le règlement des jambes correspondantes.
            cmb_inc = (sport == "foot" and (d.get("combo") or {}).get("result") is None
                       and any(l.get("result") is None for l in (d.get("combo") or {}).get("legs") or []))
            if need_stats and (not score.get("stats") or cmb_inc):
                st = await _event_stats(sofa) if (sofa and len(sofa) <= 8) else None
                if st:
                    score["stats"] = {**(score.get("stats") or {}), **st}   # complète sans rien perdre
                # Foot : STATS via FotMob (source n°1 foot, couvre les CdM) — tirs cadrés/tirs/corners/cartons.
                # Testé 8/8 sur les combos tirs alors que Flashscore/GISMO ne couvraient pas ces matchs.
                cur = score.get("stats") or {}
                if sport == "foot" and ("sot_h" not in cur or "shots_h" not in cur):
                    try:
                        from app import sources as _srcf
                        import httpx as _hxf
                        async with _hxf.AsyncClient() as _fc:
                            fm = await _srcf.foot_match_stats(_fc, d.get("home", ""), d.get("away", ""),
                                                              d.get("start"))
                        if fm:
                            score["stats"] = _merge_stats(cur, fm)   # comble sans écraser, anti-faux-zéros
                    except Exception:
                        pass
                # Repli : Flashscore si les TIRS/TIRS CADRÉS manquent encore
                # (SofaScore mort renvoie {} ; ou `_event_stats` ne donne que corners/cartons sans SOT).
                # Sans ça, une jambe « tirs cadrés » réglable (donnée dispo chez Flashscore) était VOID
                # à tort -> pouvait transformer un combiné perdu en gagné.
                cur = score.get("stats") or {}
                if sport == "foot" and ("sot_h" not in cur or "shots_h" not in cur):
                    from app import flashscore
                    fs = await asyncio.to_thread(flashscore.foot_match_stats_by_names,
                                                 d.get("home", ""), d.get("away", ""), d.get("start"))
                    if fs:
                        score["stats"] = _merge_stats(cur, fs)   # comble SOT/tirs, anti-faux-zéros
                # Dernier repli TIRS : Sportradar GISMO `match_details` (tirs cadrés/tirs/corners par équipe)
                # quand Flashscore ne couvre pas le match. Tolérant (None si non résolu) -> ne bloque rien.
                cur = score.get("stats") or {}
                if sport == "foot" and ("sot_h" not in cur or "shots_h" not in cur):
                    try:
                        from app import sportradar as _srx
                        import httpx as _hx
                        async with _hx.AsyncClient() as _sc:
                            gs = await _srx.match_stats(_sc, sport, d.get("home", ""), d.get("away", ""),
                                                        d.get("start"))
                        if gs:
                            score["stats"] = _merge_stats(cur, gs)   # comble tirs GISMO, anti-faux-zéros
                    except Exception:
                        pass

            # PÉRIODES (jeux par set tennis : SETGAMES/TOTGAMES/SETSCORE ; mi-temps foot *_1H) : si le
            # score (souvent ESPN = score final seul) n'a PAS les périodes, LiveScore les fournit ->
            # on les récupère et fusionne. Sinon ces marchés restent « non réglables » alors que la
            # donnée EXISTE (re-sourcing : ne JAMAIS exclure un marché faute de pouvoir le valider).
            shadow_codes = [s.get("code", "") for s in (d.get("shadow") or [])]
            need_periods = (not score.get("periods")) and any(
                c.startswith(("SETGAMES", "TOTGAMES", "SETSCORE", "TEAMHALF", "HALFTOT", "WINHALF",
                              "TEAMBOTH", "BOTHHALVES", "BTTSHALF", "HALFRES", "BQTOT", "BQTEAM", "BQWIN",
                              "BQHCAP", "GAMESHCAP", "TIEBREAK", "REGTIME"))   # REGTIME : 90 min (somme des mi-temps)
                or "1H" in c or "2H" in c
                for c in [code, *bet_codes, *combo_codes, *shadow_codes] if c)
            if need_periods:
                from app import livescore as _lsmod
                lsc = await asyncio.to_thread(_lsmod.final_score, sport, d)
                if lsc and lsc.get("periods"):
                    score = {**score, "periods": lsc["periods"]}
                    if score.get("sets_home") is None and lsc.get("sets_home") is not None:
                        score["sets_home"], score["sets_away"] = lsc["sets_home"], lsc["sets_away"]
            if need_periods and not score.get("periods"):
                # Repli SPORTRADAR (GISMO, gratuit) : `match_info.periods` fournit les jeux par set
                # (tennis), les points par quart-temps (basket) et les mi-temps (foot) là où LiveScore/
                # Flashscore échouent -> rend enfin réglables jeux/tie-breaks/sets & quart-temps.
                try:
                    from app import sportradar as _sr
                    import httpx as _httpx
                    async with _httpx.AsyncClient() as _src_c:
                        srs = await _sr.final_score(_src_c, sport, d)
                    if srs and srs.get("periods"):
                        score = {**score, "periods": srs["periods"]}
                        if score.get("sets_home") is None and srs.get("sets_home") is not None:
                            score["sets_home"], score["sets_away"] = srs["sets_home"], srs["sets_away"]
                        log.info("périodes via sportradar : %s_%s %s",
                                 sport, d.get("id"), srs.get("label"))
                except Exception:
                    pass

            async def _settle_one(c):
                if not c:
                    return None
                if c.startswith("HOLD1"):
                    # SofaScore point-by-point d'abord (si id), SINON Flashscore (gratuit, jeu-par-jeu,
                    # sans id ni SofaScore — règle « 1er jeu de service » même quand Sofa est bloqué).
                    r = await _settle_hold1(sofa, c, score) if (sofa and len(sofa) <= 8) else None
                    if r is None and d.get("sport") == "tennis":
                        r = await _settle_hold1_flashscore(c, d)
                    return r
                if c.startswith("FIRSTTO") and sofa and len(sofa) <= 8:
                    return await _settle_firstto(sofa, c)   # premier à X points -> incidents SofaScore
                if c.startswith("SCOREASSIST"):             # « joueur marque ou passe déc. » -> events FotMob
                    _, _, who = c.partition("|")
                    if not who:
                        return None
                    from app import sources as _src
                    return await _src.player_scored_or_assisted(d, who)
                if c.startswith("FIRSTGOAL"):               # premier but du match -> events FotMob
                    from app import sources as _src
                    fg = await _src.first_goal_side(d)
                    if fg is None:
                        return None                         # indispo -> on retentera
                    if fg == "":                            # aucun but trouvé
                        return "push" if (score.get("home") == 0 and score.get("away") == 0) else None
                    pcs = c.split()
                    return "won" if (len(pcs) >= 2 and pcs[1] == fg) else "lost"
                if c.startswith("PLAYERBK"):                # prop joueur basket (PTS/REB/AST) -> box-score ESPN
                    head, _, who = c.partition("|")
                    hp = head.split()
                    if len(hp) < 4 or not who:
                        return None
                    try:
                        line = float(hp[3])
                    except ValueError:
                        return None
                    from app import sources as _src
                    val = await _src.basket_player_stat(d, who, hp[1])
                    if val is None:
                        return None                         # indispo OU joueur ambigu -> retente (jamais faux)
                    return "push" if val == line else ("won" if ((val > line) == (hp[2] == "OVER")) else "lost")
                if c.startswith("PLAYERFB"):                # prop joueur foot (Opta) -> FotMob playerStats
                    head, _, who = c.partition("|")
                    hp = head.split()
                    if len(hp) < 4 or not who:
                        return None
                    try:
                        line = float(hp[3])
                    except ValueError:
                        return None
                    from app import sources as _src
                    val = await _src.foot_player_stat(d, who, hp[1])
                    if val is None:
                        return None                         # indispo OU joueur ambigu -> retente (jamais faux)
                    return "push" if val == line else ("won" if ((val > line) == (hp[2] == "OVER")) else "lost")
                if c.startswith("GKSAVES"):                 # arrêts du gardien d'une ÉQUIPE (FotMob)
                    gp = c.split()
                    if len(gp) < 4:
                        return None
                    try:
                        line = float(gp[3])
                    except ValueError:
                        return None
                    from app import sources as _src
                    val = await _src.foot_player_stat(d, "", "SAVES", side=gp[1])
                    if val is None:
                        return None
                    return "push" if val == line else ("won" if ((val > line) == (gp[2] == "OVER")) else "lost")
                if c.startswith("FIRSTSCORER"):             # premier buteur (joueur) -> events FotMob
                    _, _, who = c.partition("|")
                    if not who:
                        return None
                    from app import sources as _src
                    sc = await _src.first_scorer(d)
                    if sc is None:
                        return None                         # indispo -> retente
                    if sc == "":                            # aucun but -> remboursé
                        return "push" if (score.get("home") == 0 and score.get("away") == 0) else None
                    from app.sources import _tok as _tk
                    return "won" if (_tk(who) and _tk(who) <= _tk(sc)) else "lost"
                return settle_pick(c, score)

            pr = await _settle_one(code)
            d["result"] = {"score": score.get("label"), "pick_result": pr, "raw": score}
            if pr is None and analyses.status_of(d) == "finished":   # non réglable (abandon…) -> compte l'essai
                d["pick_tries"] = (d.get("pick_tries") or 0) + 1
            # Règle CHAQUE pari affiché séparément (stats par pari 1/2/3, cadres verts/rouges).
            # Le 1er pari EST le pick « le plus sûr » -> on réutilise son code/résultat DÉJÀ résolus
            # (sinon un code de pari vide ou divergent le laisse « en attente » alors que le pick est
            # réglé : cas vu sur handicap noms courts / SETGAMES). Les paris suivants : réglés normalement.
            bets_out = []
            for i_b, (b, bc) in enumerate(zip(bet_list, bet_codes)):
                # Le 1er pari EST le pick « le plus sûr ». On n'hérite du résultat DÉJÀ résolu du pick
                # QUE si ce pari n'a PAS de code propre (sinon « en attente » alors que le pick est réglé,
                # cf. handicap noms courts / SETGAMES). S'il a son PROPRE code, on le règle dessus — même
                # s'il DIVERGE du pick (ex. ligne `pick` « moins de 5.5 » vs table « moins de 4.5 ») : sinon
                # le résultat du pick s'affiche À TORT sur un pari différent (faux gagné/perdu).
                if i_b == 0 and not bc:
                    bc, br = code, pr
                else:
                    if not bc:   # pari FINI qu'on ne sait pas régler -> à corriger (pas silencieux)
                        log.warning("règlement impossible (code vide) : %s_%s · %r", sport, mid, b.get("sel"))
                    br = await _settle_one(bc)
                bets_out.append({"sel": b["sel"], "odds": b["cote"], "code": bc, "result": br,
                                 "prob": b.get("prob")})   # confiance annoncée -> page calibration
            d["bets"] = bets_out
            # Le BADGE headline (pick_result) suit le pari AFFICHÉ « le plus sûr » (= 1er pari de la table),
            # pas une ligne `pick` éventuellement DIVERGENTE -> carte, badge et stats restent cohérents.
            if bets_out and bets_out[0].get("result") is not None:
                d["result"]["pick_result"] = bets_out[0]["result"]
            # FREEZE stats : dès qu'un simple RÉGLÉ est RETENU (for_history), on FIGE son statut + ses
            # détails dans d["stat_bet"] -> il reste compté À VIE (compteur MONOTONE, immunisé à la dérive
            # de calibration : plus de « nombre qui rebaisse »). On ne fige QUE les comptés -> on ne
            # RETIRE JAMAIS un pari. La calibration n'est pas touchée (elle garde toutes les prédictions).
            if not isinstance(d.get("stat_bet"), dict):
                try:
                    _sf = analyses.retained_bet(sport, mid, for_history=True)
                    if _sf:
                        _rr = next((bb.get("result") for bb in bets_out
                                    if analyses._norm_sel(bb.get("sel", "")) == analyses._norm_sel(_sf.get("sel", ""))),
                                   _sf.get("result"))
                        if _rr in ("won", "lost", "push"):
                            d["stat_bet"] = {"sel": _sf.get("sel"), "prob": _sf.get("prob"),
                                             "cote": _sf.get("cote"), "result": _rr}
                except Exception:
                    pass
            # COMBINÉ (grand tournoi) : règle chaque jambe via son code -> résultat global (toutes
            # gagnées = gagné ; une perdue = perdu ; sinon en attente). Les corners/cartons se règlent
            # désormais (Flashscore), donc les combinés type Qatar-Suisse se valident.
            combo = d.get("combo")
            # Totaux de buts PAR MI-TEMPS dérivés des PÉRIODES (LiveScore) si absents des stats df_st ->
            # « but dans les 2 mi-temps » et marchés 1H/2H foot se règlent même sans Flashscore.
            _per = score.get("periods") or {}
            if sport == "foot" and _per:
                _st = dict(score.get("stats") or {})
                if _per.get(1) and _st.get("goals_1h_total") is None:
                    _st["goals_1h_total"] = _per[1][0] + _per[1][1]
                if _per.get(2) and _st.get("goals_2h_total") is None:
                    _st["goals_2h_total"] = _per[2][0] + _per[2][1]
                score["stats"] = _st
            if combo and combo.get("legs"):
                stats = score.get("stats") or {}    # corners/cartons/tirs MATCH + variantes 1ère MT (_1h)
                vals = {"goals_h": score.get("home"), "goals_a": score.get("away"), **stats}
                any_lost, all_won, any_pending = False, True, False
                for leg in combo["legs"]:
                    info = analyses._leg_metric(leg, d.get("home", ""), d.get("away", ""))
                    lr = None
                    if info.get("live_ok"):           # métrique connue, match entier -> évaluateur unique
                        lr, _ = analyses._eval_leg(info, vals, final=True)
                    if lr is None:                     # non couvert OU données manquantes -> repli par code
                        # Code RE-DÉRIVÉ frais prioritaire (le code stocké peut être périmé : ancienne
                        # version du parseur -> mauvais marché, cf. audit). Repli sur le stocké si vide.
                        lc = (code_from_pick(leg.get("sel", ""), sport, d.get("home", ""), d.get("away", ""))
                              or leg.get("code"))
                        leg["code"] = lc
                        lr = await _settle_one(lc) if lc else None
                    leg["result"] = lr
                    if lr == "lost":
                        any_lost = True
                    if lr != "won":
                        all_won = False
                    if lr is None:
                        any_pending = True
                # OBJECTIF : RÉGLER pour de vrai (gagné/perdu). Tant qu'une jambe manque, on RÉ-ESSAIE —
                # le score/les stats/les sets arrivent SOUVENT avec du retard (ne jamais trancher trop
                # tôt). On ne « tranche » (void = ULTIME RECOURS pour une donnée réellement morte) QUE si
                # le match est fini DEPUIS LONGTEMPS (> _VOID_AFTER_DAYS) : d'ici là on continue d'essayer.
                # Quand TOUTES les jambes sont réglées, on finalise immédiatement (pas d'attente).
                _strict_fin = analyses.status_of(d) == "finished"
                _age_d = _match_age_days(d)
                if any_pending and ((d.get("combo_tries") or 0) < 8 or not _strict_fin
                                    or _age_d < _VOID_AFTER_DAYS):
                    combo["result"] = None
                    d["combo_tries"] = (d.get("combo_tries") or 0) + 1
                else:
                    # VERDICT GARANTI : une jambe encore indéterminée -> VOID ; on règle sur les jambes
                    # tranchées (push/void = cote 1, neutre). Perdant si une jambe perd ; gagnant si au
                    # moins une gagne et aucune ne perd ; remboursé si rien n'a pu être réglé.
                    won_odds, n_won, n_lost, n_void, nlegs = 1.0, 0, 0, 0, len(combo["legs"])
                    for leg in combo["legs"]:
                        r = leg.get("result")
                        if r is None:
                            leg["result"] = "void"; r = "void"
                        if r == "won":
                            won_odds *= float(leg.get("cote") or 1.0); n_won += 1
                        elif r == "lost":
                            n_lost += 1
                        elif r == "void":
                            n_void += 1
                    if n_lost:
                        combo["result"] = "lost"
                    elif n_won:
                        combo["result"] = "won"
                        if n_won < nlegs:                  # des jambes retirées (push/void) -> cote EFFECTIVE
                            combo["settle_odds"] = round(won_odds, 2)
                    else:
                        combo["result"] = "void"           # aucune jambe gagnante/perdante -> remboursé
                    if n_void:
                        log.info("combo %s_%s tranché (void=%d/%d) -> %s",
                                 sport, d.get("id"), n_void, nlegs, combo["result"])
            # PRÉDICTIONS FANTÔMES (calibrage) : on règle CHAQUE prédiction (métrique live OU code) ->
            # result. Elles ne pèsent QUE dans la calibration — jamais dans l'affichage/ROI/forme.
            shadow = d.get("shadow")
            if shadow:
                svals = {"goals_h": score.get("home"), "goals_a": score.get("away"),
                         **(score.get("stats") or {})}
                for sp in shadow:
                    if sp.get("result") in ("won", "lost", "push"):
                        continue
                    info = analyses._leg_metric(sp, d.get("home", ""), d.get("away", ""))
                    r = None
                    if info.get("live_ok"):
                        r, _ = analyses._eval_leg(info, svals, final=True)
                    if r is None:
                        c = (sp.get("code")
                             or code_from_pick(sp.get("sel", ""), sport, d.get("home", ""), d.get("away", "")))
                        sp["code"] = c
                        r = await _settle_one(c) if c else None
                    sp["result"] = r
            d["settle_v"] = _SETTLE_VERSION
            # CLV (Closing Line Value) du pari RÉSULTAT : figé UNE FOIS, ici au règlement — tant
            # qu'odds_history a encore la cote de clôture (purge à 48 h, le règlement tombe avant).
            # Stocké -> persiste après la purge. Juge d'edge le plus rapide. No-op si non calculable.
            if d.get("clv") is None:
                try:
                    from app import clv as _clvmod
                    _cv = _clvmod.clv_for_sidecar(d)
                    if _cv is not None:
                        d["clv"] = round(_cv, 4)
                except Exception:
                    pass
            # Backfill du sentiment public (barre « Public ») si le scan ne l'a pas capturé (SofaScore
            # bloqué au scan) -> FIGÉ une fois dans le sidecar, ne bouge plus ensuite. Si le sofa_id
            # n'est pas exploitable (résolution échouée au scan), on le RÉSOUT par noms via
            # scheduled-events (et on le fige) pour pouvoir lire les votes.
            if d.get("pub_home") is None:
                vsofa = sofa if (sofa.isdigit() and len(sofa) <= 8) else None
                if not vsofa:
                    day = (d.get("start") or "")[:10]
                    if day:
                        if (sport, day) not in sched_cache:
                            sched_cache[(sport, day)] = await _schedule_scores(sport, day)
                        _, rid = _find_score(sched_cache[(sport, day)], d)
                        if rid and rid.isdigit() and len(rid) <= 8:
                            vsofa = rid
                            d["sofa_id"] = rid          # on fige l'id SofaScore résolu
                if vsofa:
                    v = await _event_votes(vsofa)
                    if v and v[0] is not None:
                        d["pub_home"], d["pub_away"] = v[0] / 100, v[1] / 100
                        if len(v) > 2 and v[2] is not None:
                            d["pub_draw"] = v[2] / 100
                d["votes_tries"] = (d.get("votes_tries") or 0) + 1
            # Transition « en attente -> réglé » -> notification (simple ET/OU combiné).
            _chip = {"won": "✅ Réussi", "lost": "❌ Perdu", "push": "➖ Remboursé", "void": "➖ Remboursé"}
            _MARK = {"won": "✅", "lost": "❌", "push": "➖", "void": "➖"}   # validation/croix APRÈS le prono
            _emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}.get(sport, "•")
            _match = f"{d.get('home', '')} - {d.get('away', '')}"
            _sc = (d.get("result") or {}).get("score") or ""
            new_pick = (d.get("result") or {}).get("pick_result")
            new_combo = (d.get("combo") or {}).get("result")
            _parts = []
            _card_simple = _card_combo = None   # données carte image (résultat simple / combiné)
            _flags_to_set = []   # R2 : flags notified_* à POSER seulement APRÈS envoi Telegram réussi
            # Flags PERSISTANTS écrits avec le résultat -> notification IDEMPOTENTE : une fois notifié,
            # plus jamais re-notifié (re-règlement après bump de version, redémarrage, reload uvicorn…).
            # R2 : on ne se fie plus à `prev_pick is None` (transition) mais au flag `notified_pick`
            # (= notif RÉELLEMENT partie), pour pouvoir RÉ-ÉMETTRE une notif perdue par crash.
            if new_pick in _chip and not d.get("notified_pick"):
                # On ne notifie le SIMPLE que s'il a été PUBLIÉ = RETENU (confiance+EV+garde-fous),
                # combiné OU non. Un simple non retenu n'a pas de carte prono -> pas de carte résultat
                # non plus (cohérence Telegram/stats : posté = compté).
                _simple_shown = analyses.retained_bet(sport, mid) is not None
                if not _simple_shown:
                    d["notified_pick"] = True   # non affiché -> rien à envoyer, on FIGE tout de suite
                if _simple_shown:
                    _flags_to_set.append("notified_pick")   # affiché -> figé APRÈS envoi réussi
                    _m = _MARK.get(new_pick, "")   # ✅/❌ APRÈS le prono
                    _raw = (d.get("pick") or "").strip()
                    _pl = re.sub(r"@\s*([\d]+[.,][\d]+)", r"· <b>\1</b>", html.escape(_raw))
                    _parts.append(f"• {_pl} {_m}".strip() if _pl else f"• Pari simple {_m}".strip())
                    _sm = re.search(r"(.+?)\s*@\s*([\d]+[.,][\d]+)", _raw)
                    _card_simple = {"label": (_sm.group(1).strip() if _sm else _raw) or "Pari simple",
                                    "cote": (_sm.group(2).replace(",", ".") if _sm else ""),
                                    "mark": new_pick}
            if new_combo in _chip and not d.get("notified_combo"):
                _flags_to_set.append("notified_combo")   # R2 : figé APRÈS envoi réussi
                _m = _MARK.get(new_combo, "")
                _cb = d.get("combo") or {}
                _cco = _cb.get("real_odds") or _cb.get("total")
                _cl = (f"• <b>Combiné · cote {_cco}</b> {_m}".strip() if _cco
                       else f"• <b>Combiné</b> {_m}".strip())
                for _lg in _cb.get("legs", []):
                    _lr = _lg.get("result")
                    _cl += f"\n• {html.escape(str(_lg.get('sel', '')))} {_MARK.get(_lr, '·')}"
                _parts.append(_cl)
                _card_combo = {"cote": (f"{_cco:.2f}" if isinstance(_cco, float) else str(_cco or "")),
                               "mark": new_combo,
                               "legs": [(str(_lg.get("sel", "")), _lg.get("result"),
                                         _lg.get("cote") or "")
                                        for _lg in _cb.get("legs", [])]}
            if _parts and _flags_to_set:
                # R2 — compteur d'essais d'envoi (borne le re-traitement « réglé non notifié ») :
                # incrémenté à CHAQUE construction de carte, qu'on parvienne à l'envoyer ou non.
                d["notify_tries"] = (d.get("notify_tries") or 0) + 1
            if _parts:
                # En-tête : match (gras) + score, puis lieu/compétition · heure (comme le scan).
                _bits = []
                if d.get("comp"):
                    _bits.append(html.escape(str(d["comp"])))
                try:
                    _bits.append(datetime.fromisoformat((d.get("start") or "")
                                 .replace("Z", "+00:00")).strftime("%H:%M"))
                except ValueError:
                    pass
                _hdr = f"{_emo} <b>{html.escape(_match)}</b>"
                if _bits:
                    _hdr += f"\n<i>{' · '.join(_bits)}</i>"
                if _sc:                              # score du match sur sa propre ligne, AVANT le prono
                    _hdr += f"\nScore : <b>{html.escape(_sc)}</b>"
                notify_msgs.append(_hdr + "\n\n" + "\n".join(_parts))
                # --- données CARTE IMAGE de résultat (Option 2 : tout dans l'image) ---
                _sn = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}.get(sport, sport or "")
                _mt = ""
                try:
                    _d0 = datetime.fromisoformat((d.get("start") or "").replace("Z", "+00:00"))
                    _mt = f"{_fr_date(_d0)} · {_d0.strftime('%H:%M')}"
                except ValueError:
                    pass
                notify_cards.append({
                    "emoji": _emo, "_mid": mid, "cat": f"{_sn} · {d['comp']}" if d.get("comp") else _sn,
                    "match": str(_match).replace(" - ", " — "),
                    "meta": (f"terminé · {_mt}" if _mt else "terminé"),
                    "type": "result", "score": _sc,
                    "simple": _card_simple, "combo": _card_combo,
                    # AUTO-RÉPARATION : si une carte résultat a DÉJÀ été postée pour ce match (règlement
                    # corrigé -> reset puis re-règlement), on garde ses message_id pour la SUPPRIMER avant
                    # de poster la version corrigée (plus de « perdu » fantôme qui traîne dans le canal).
                    "_old_result_msg": d.get("result_msg"),
                    "_side": side, "_flags": list(_flags_to_set)})   # R2 : flags figés APRÈS envoi
            try:
                json.dump(d, open(side, "w", encoding="utf-8"), ensure_ascii=False)
                n += 1
            except OSError:
                pass
    finally:
        sofa_http.allow_bulk_proxy = prev_bulk
    # Notification Telegram des paris fraîchement réglés : UN MESSAGE PAR MATCH (pas de groupage,
    # pas de suppression). No-op si non configuré ; n'élève jamais.
    if notify_msgs:
        try:
            from app import notify
            if notify.configured():
                import sys
                _tools = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
                if _tools not in sys.path:
                    sys.path.insert(0, _tools)
                import card_image
                os.makedirs("data/_cards", exist_ok=True)
                for _i, (msg, card) in enumerate(zip(notify_msgs, notify_cards)):
                    sent = None
                    render_ok = False
                    if card:                        # carte image (Option 2 : tout dans l'image)
                        try:
                            png = f"data/_cards/res_{_i}.png"
                            await card_image.render_card(card, png)
                            render_ok = True        # la carte EXISTE -> le texte n'est plus un repli
                            # AUTO-RÉPARATION : carte résultat déjà postée pour ce match (correction) ->
                            # on la SUPPRIME avant d'envoyer la version corrigée (sinon 2 cartes en conflit).
                            if card.get("_old_result_msg"):
                                await asyncio.to_thread(notify.delete_messages, card["_old_result_msg"])
                            # répond à la carte PRONO du même match (fil prono -> résultat)
                            _reply = notify.get_prono(card.get("_mid"))
                            # envoi BLOQUANT (httpx upload) -> hors event loop pour ne pas figer l'API
                            sent = await asyncio.to_thread(notify.send_photo_sync, png, "", _reply)
                        except Exception as ce:
                            log.warning("carte résultat échouée, repli texte : %s", ce)
                    _ok = bool(sent)
                    # Repli texte UNIQUEMENT si la CARTE n'a pas pu être PRODUITE (pas de carte / rendu
                    # échoué). Si la carte est rendue mais l'ENVOI photo échoue/expire (sent vide alors
                    # que la photo a PU partir : rafale -> timeout Telegram), on NE poste PAS le texte ->
                    # plus de DOUBLON image+texte. _ok reste False -> R2 ré-essaie la CARTE à la passe
                    # suivante (flag non figé), bornée par notify_tries : zéro perte, zéro doublon.
                    if not render_ok:
                        _ok = bool(await notify.send(msg))
                    # R2 — on FIGE les flags notified_* SEULEMENT maintenant (envoi confirmé). Si l'envoi
                    # a échoué (ou crash avant cette ligne), les flags restent à False -> le pari réglé
                    # sera re-traité à la passe suivante (borné par notify_tries) : zéro perte, zéro doublon.
                    if _ok and card and card.get("_flags") and card.get("_side"):
                        _mark_notified(card["_side"], card["_flags"], sent if isinstance(sent, dict) else None)
        except Exception as exc:
            log.warning("notif règlement ignorée : %s", exc)
    return n
