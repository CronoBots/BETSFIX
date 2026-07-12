"""Module FOOT (Coupe du Monde + grandes compétitions) — **séparé** du tennis/basket.

Spécificité : 3 issues (1-X-2, le match nul existe). Modèle : Elo d'équipe
(tools/build_foot_elo.py) -> supériorité de buts -> double Poisson -> P(1)/P(X)/P(2),
confronté au 1X2 Unibet pour repérer une value. Filtre « grandes compétitions » par ID
(Coupe du Monde + top championnats + C1/C3), pas les petits championnats.

⚠️ Modèle jeune + venues neutres en CdM : avantage terrain faible, value à confirmer.
Sources gratuites : SofaScore + Unibet BE.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone


from app import flags, web
from app.textutil import name_tokens

log = logging.getLogger("uvicorn")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELO_PATH = os.path.join(_ROOT, "data", "foot_elo.json")

# Grandes compétitions (SofaScore unique-tournament id -> libellé court).
MAJOR_TIDS = {16: "Coupe du Monde", 17: "Premier League", 8: "LaLiga", 23: "Serie A",
              35: "Bundesliga", 34: "Ligue 1", 7: "Ligue des Champions",
              679: "Europa League", 1: "Euro", 18: "Coupe du Monde",
              851: "Amicaux Int."}



# Compétitions à venues majoritairement NEUTRES : le « domicile » SofaScore est
# arbitraire (sauf pays hôte), donc aucun avantage terrain ne doit s'appliquer.
NEUTRAL_COMPS = {"Coupe du Monde", "Euro"}

HOME_ADV = 35.0           # faible : beaucoup de venues neutres en grand tournoi
GOALS_TOTAL = 2.7         # total de buts moyen (baseline)
SUP_PER_100 = 0.45        # 100 pts Elo ~ 0.45 but de supériorité
# Fenêtre de récupération : logique COMMUNE aux 3 sports (cf. app/window.py). Un match entre dans
# la fenêtre (et reçoit sa perle) ~HORIZON_HOURS avant le coup d'envoi.
MODEL_TRUST = 0.50
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.12, 0.80
# Garde-fou « le marché a raison » par fiabilité du marché : si le modèle s'écarte de plus que
# le seuil de la cote dévigée, c'est le MODÈLE qui a tort -> on écarte (pas de fausse value).
# Indispensable sur les équipes EXTRÊMES (Andorre, Saint-Marin…) où la régularisation surévalue.
MAX_DISAGREEMENT = 0.15    # résultat 1X2 / double chance / handicap : marché très efficient
GOALS_DISAGREEMENT = 0.20  # totaux, par équipe, BTTS : marché efficient, modèle a un vrai signal
ANNEX_DISAGREEMENT = 0.25  # mi-temps, corners/cartons : marchés moins efficients, modèle approximatif

# Constantes réseau SofaScore/Unibet : centralisées dans app/netconst.py (importées en tête).


# ----------------------------------------------------------------- modèle










_norm = name_tokens  # normalisation centralisée (cf. app/textutil.py)


def _devig3(o1, ox, o2):
    odds = [o1, ox, o2]
    if not all(odds):
        return None
    raws = [1 / o for o in odds]
    tot = sum(raws)
    return [r / tot for r in raws]


# ----------------------------------------------------------- moteur « perle rare » (foot)
# On price, depuis la grille Poisson, les marchés que le modèle SAIT estimer (1X2, Plus/Moins
# de buts, les 2 équipes marquent, double chance), on compare à la cote Unibet (dévig PAR marché),
# et on sort le pari au meilleur ÉQUILIBRE confiance × value. (Pas les paris joueurs/corners :
# aucun modèle pour ça.)
PERLE_MIN_PROB = 0.52      # le pari doit rester plausible (plus probable que non)
PERLE_MIN_ODDS = 1.20      # pool : la CONFIANCE accepte les petites cotes (gros favori sûr, gain modeste)
VALUE_MIN_ODDS = 1.50      # la VALUE, elle, exige une cote qui paie vraiment
PERLE_MIN_EDGE = 0.03      # value minimale pour entrer dans le pool (modèle > marché dévig)
PERLE_MIN_EDGE_ANNEX = 0.05  # mi-temps/corners/cartons : modèle approximatif -> on exige plus
N_CONFIANCES = 2           # nb max de paris de confiance proposés par match (types de marché distincts)
CONF2_MIN_PROB = 0.62      # le 2e pari de confiance doit rester solide (sinon on n'en propose qu'un)


# --- buts attendus par FORME RÉELLE (attaque/défense des derniers matchs) -----------------
# C'est ce qui rend les marchés totaux/BTTS porteurs d'un vrai signal par équipe (l'Elo seul
# ne donne qu'une base de buts générique). Régularisé vers la moyenne ligue (petits échantillons
# + force de calendrier), borné contre les aberrations.
LEAGUE_GPG = 1.35          # buts/équipe/match de référence
FORM_SHRINK = 6            # nb de matchs « fictifs » à la moyenne (régularisation)
GOALS_HOME_BASE = 1.45     # buts attendus à domicile (équipes de force moyenne)
GOALS_AWAY_BASE = 1.15     # ... à l'extérieur
GOALS_NEUTRAL_BASE = 1.30  # ... terrain neutre


SOS_SHRINK = 3             # régularisation du modèle ajusté force-de-calendrier (vers att/def = 1)
_STR_CLAMP = (0.40, 1.85)




























# familles de marchés « de RÉSULTAT / marge » (efficients) : on les évalue mais on s'aligne sur
# le marché si le modèle s'en écarte trop (garde-fou MAX_DISAGREEMENT) -> pas de faux edge.
_RESULT_KINDS = {"1x2", "dc", "hasian"}

# --- mi-temps : ~45 % des buts tombent en 1re période (stat foot stable). On découpe les lambdas
# plein-temps et on price chaque mi-temps comme un double Poisson. Modèle APPROXIMATIF (ratio
# constant, pas de profil par équipe) -> marchés mi-temps toujours sous garde-fou (pas de faux edge).
HALF1_SHARE = 0.45
_H1_MARK = ("1re mi", "1ère mi", "1ere mi", "première mi", "premiere mi", "1st half",
            "first half", "1re période", "1re periode", "1ère période", "mi-temps 1")
_H2_MARK = ("2e mi", "2ème mi", "2eme mi", "2nde mi", "2nd half", "second half",
            "deuxième mi", "deuxieme mi", "mi-temps 2", "2e période", "2e periode")






# --- corners & cartons : même Poisson que les buts, sur la FORME corners/cartons par équipe
# (moyennes pour/contre de la compétition). Modèle annexe -> toujours sous garde-fou.
CORNER_HOME_ADV = 0.3      # léger surplus de corners à domicile
_CARD_KMAX = 13
_CORNER_KMAX = 24












# Marqueurs d'un marché qu'on NE price PAS : sous-période (mi-temps, 15 min), joueur/buteur,
# corners/cartons, prolongation, handicap (push/lignes asiatiques trop fragiles à régler), et
# combinés « résultat & autre marché ». Notre modèle price le plein-temps -> tout le reste fausse.
# NB : les marqueurs de mi-temps NE sont PLUS ici (on price les mi-temps, cf. _market_period).
# Restent exclus : segments courts (quart/minute/15min), corners/cartons/tirs, joueurs, combinés.
# NB : corners/cartons NE sont plus exclus (modèle dédié, cf. _price_special) — mais restent
# exclus : segments courts, tirs/hors-jeu/penalties, joueurs, combinés, prolongation.
_NOT_FULLTIME = (
    "quart", "quarter", "minute", "15 min", "intervalle", "10 min",
    "buteur", "joueur", "player", "scorer", "tir", "shot", "frappe",
    "hors-jeu", "offside", "penalt", "poteau", "prolongation", "extra time",
    " and ", "&", " or ", " win and", "to win", " et ",
    "avance", "ahead",   # « Temps réglementaire - 2 buts d'avance » ≠ résultat 1X2
)








_PERIOD_LBL = {"h1": " (1re MT)", "h2": " (2e MT)"}
















def perle_live_status(perle, hs, as_):
    """Statut LIVE d'une perle vu le score : 'won' (déjà gagnée, verrouillée), 'lost' (déjà
    perdue, verrouillée) ou None (encore en jeu). Seuls les marchés MONOTONES verrouillables en
    live (un but de plus ne peut pas inverser le résultat) ; 1X2/handicap/dc -> None (réversibles)."""
    if not (isinstance(perle, dict) and hs is not None and as_ is not None):
        return None
    kind, side, line, team = perle.get("kind"), perle.get("side"), perle.get("line"), perle.get("team")
    if kind == "ou" and line is not None and (hs + as_) > line:
        return "won" if side == "over" else "lost"
    if kind == "team_ou" and line is not None:
        g = hs if team == "home" else as_
        if g > line:
            return "won" if side == "over" else "lost"
    if kind == "btts" and hs >= 1 and as_ >= 1:        # les 2 ont marqué
        return "won" if side == "yes" else "lost"
    return None




# ----------------------------------------------------------------- données


































# ----------------------------------------------------------------- rendu
def _fmt_time(ts) -> str:
    if not ts:
        return ""
    return web.fmt_local(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat())








def _model_line(r: dict) -> str:
    # La barre de cotes « Bookmakers » est RETIRÉE : la barre combinée « Cotes & chances » (_pick_bars)
    # porte désormais les cotes ET le % de chance (total 100 %). On garde la comparaison de forme.
    sub = ""
    fm = r.get("form")
    if fm:
        sub += web.form_compare(r["home"], fm[0], r["away"], fm[1])
    return sub


def _card(r: dict) -> dict:
    """Dict _sport_row d'une rencontre foot (live / à venir), réutilisé par render + Directs."""
    pk = r.get("pick")
    # plus de badge VALUE en haut à droite : la value est dans la bannière « À JOUER » + l'analyse
    badge = ""
    # 🟢 Halo « gagné » en LIVE : la perle est-elle déjà/en passe d'être gagnée vu le score ?
    hs = as_ = None
    if r.get("status") == "inprogress" and r.get("score"):
        try:
            hs, as_ = (int(x) for x in str(r["score"]).split("-"))
        except (ValueError, AttributeError):
            hs = as_ = None

    def _st(p):
        return perle_live_status(p, hs, as_) if hs is not None else None
    sp, sp2, spv = _st(r.get("perle")), _st(r.get("perle2")), _st(r.get("perle_value"))
    return {"tour": r.get("comp"), "sport": "Foot", "icon": "⚽",
            "status": r["status"], "time": _fmt_time(r.get("start")),
            "start_ts": r.get("start"), "home": r["home"], "away": r["away"],
            "female": r.get("female"), "score": r.get("score", ""), "live_time": r.get("live_time", ""),
            "fstats": r.get("fstats"),   # cartons/corners live -> box-score foot (demande user 2026-07-12)
            "home_flag": flags.flag(r["home"]), "away_flag": flags.flag(r["away"]),
            "url": f'/foot/match/{r["id"]}' if r.get("sofa_ok") else None,
            "prob": r.get("probs"), "sub": _model_line(r), "badge": badge, "pick": bool(pk),
            "live_won": sp == "won", "live_won2": sp2 == "won", "live_won_value": spv == "won",
            "live_lost": sp == "lost", "live_lost2": sp2 == "lost", "live_lost_value": spv == "lost",
            "perle": r.get("perle"), "perle2": r.get("perle2"), "pick_kind": "confiance",
            **(web.bars_foot(r.get("probs"), r.get("imp"), r.get("votes"), r["home"], r["away"])
               if r.get("probs") else
               web.analyst_bars(r.get("o1"), r.get("ox"), r.get("o2"), r.get("votes")))}




def render(rows: list[dict], finished_rows: list[dict] | None = None,
           paused: bool = False, frag: bool = False) -> str:
    # Cartes COMPLÈTES (barres Bookmakers/Unibet/Public + perle « à jouer » en avant) dans CHAQUE
    # section À venir / En direct / Terminés (plus de section Confiances séparée — le pari est sur
    # la carte). Les terminés portent ✓/✗ + score réel.
    live, upcoming, fin = [], [], []
    for r in rows:
        card = _card(r)
        (live if r["status"] == "inprogress" else upcoming).append(card)
    for r in (finished_rows or []):
        card = _card({**r, "status": "finished"})        # barres + perle depuis la ligne complète
        card["score"] = r.get("res_score") or "terminé"  # score réel du match
        card["badge"] = r.get("res_badge", "")           # ✅ Réussi / ❌ Perdu
        fin.append(card)

    intro = ('⚽ <b>Foot international & grandes compétitions</b>. Touchez un match pour son '
             f'analyse complète (forme, face-à-face). {web.BARS_LEGEND}')
    if not (live or upcoming or fin):
        intro += ' La Coupe du Monde démarre le 11 juin.'
    return web.render_sport_matches("foot", "Football", [], live, upcoming, fin,
                                    intro=intro, paused=paused, frag=frag, confidences=[])


# ----------------------------------------------------------------- suivi (3 issues)
FOOT_TRACK_PATH = os.path.join(_ROOT, "data", "tracking_foot.json")
_CODE_TO_WINNER = {"1": "home", "X": "draw", "2": "away"}






