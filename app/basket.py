"""Module BASKET (NBA + WNBA) — **séparé du tennis**.

Modèle d'équipe simple et honnête : Elo d'équipe (tools/build_basket_elo.py) + avantage
du terrain -> probabilité de victoire, confrontée au moneyline Unibet pour repérer une
éventuelle value. Pas de simulation : un seul marché fiable (vainqueur) pour démarrer.

Deux ligues suivies ensemble (voir LEAGUES) : NBA (tournoi 132) et WNBA (486). Les ids
d'équipe SofaScore sont uniques entre ligues, donc un seul fichier Elo les contient
toutes. L'écart-type de marge diffère par ligue (NBA un peu plus dispersée).

Sources gratuites : SofaScore (scheduled-events basket) + Unibet BE (nba.json / wnba.json).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone


from app import web
from app.textutil import name_tokens

log = logging.getLogger("uvicorn")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELO_PATH = os.path.join(_ROOT, "data", "basket_elo.json")

WNBA_TID = 486
NBA_TID = 132
HOME_ADV = 65.0            # avantage du terrain en points Elo (~2.5-3 pts)
MODEL_TRUST = 0.50         # ancrage marché (l'Elo jeune est bruité -> on suit le book)
VALUE_THRESHOLD = 0.05
MIN_IMPLIED, MAX_IMPLIED = 0.25, 0.75
MAX_DISAGREEMENT = 0.15    # si le modèle dépasse le marché de +15 pts, c'est le modèle
                           # (Elo jeune) qui a tort -> pas de value (garde-fou comme le tennis)

# Ligues suivies (nom SofaScore -> config). L'écart-type de marge diffère :
# la NBA a des scores plus élevés et des marges un peu plus dispersées que la WNBA.
LEAGUES = {
    "NBA":  {"tid": NBA_TID,  "unibet": "/listView/basketball/nba.json",  "sigma": 12.5},
    "WNBA": {"tid": WNBA_TID, "unibet": "/listView/basketball/wnba.json", "sigma": 11.0},
}

# Constantes réseau SofaScore/Unibet : centralisées dans app/netconst.py (importées en tête).


# ----------------------------------------------------------------- Elo / proba








SPREAD_SIGMA = 11.0       # écart-type de la marge (points) en WNBA






_norm = name_tokens  # normalisation centralisée (cf. app/textutil.py)


def _devig(o1: float | None, o2: float | None) -> tuple[float, float] | None:
    if not o1 or not o2:
        return None
    a, b = 1 / o1, 1 / o2
    return a / (a + b), b / (a + b)


# ============================================================ moteur PERLE basket
# Deux lois normales : l'ÉCART (margin, depuis l'Elo) price le vainqueur et le handicap ;
# le TOTAL de points (depuis la forme de scoring des 2 équipes) price les over/under et les
# totaux par équipe. Même philosophie que le foot : garde-fou « le marché a raison » (strict sur
# l'écart, efficient ; plus souple sur les totaux où le modèle a un vrai signal), seuils par
# famille, et on tire du MÊME pool la CONFIANCE (proba max) et la VALUE (edge max).
B_MIN_PROB = 0.52
B_MIN_ODDS = 1.20          # confiance : petits favoris sûrs acceptés
B_VALUE_MIN_ODDS = 1.50    # value : cote qui paie
B_MIN_EDGE = 0.03
B_MARGIN_DISAGREEMENT = 0.15   # vainqueur/handicap : marché très efficient
B_TOTAL_DISAGREEMENT = 0.20    # totaux : modèle (forme) porteur d'un vrai signal
B_N_CONFIANCES = 2
B_CONF2_MIN_PROB = 0.62    # le 2e pari de confiance doit rester solide
MODEL_TRUST_B = 0.50








_B_NOT = ("joueur", "player", "rebond", "rebound", "passe", "assist", "3 points", "three",
          "interception", "steal", "contre", "block", "lancer", "free throw", "1er", "2e",
          "3e", "4e", "quart", "quarter", "mi-temps", "half", "période", "periode",
          " and ", "&", " or ")












def perle_live_status(perle, hp, ap):
    """Statut LIVE d'une perle basket : 'won'/'lost'/None. Seul le total de points est
    verrouillable en live (over atteint -> gagné ; under dépassé -> perdu). Moneyline/handicap
    -> None (réversibles tant que le match n'est pas fini)."""
    if not (isinstance(perle, dict) and hp is not None and ap is not None):
        return None
    if perle.get("kind") == "total" and perle.get("line") is not None and (hp + ap) > perle["line"]:
        return "won" if perle.get("side") == "over" else "lost"
    return None




# ----------------------------------------------------------------- données


# Fenêtre de récupération : logique COMMUNE aux 3 sports (cf. app/window.py).


















# ----------------------------------------------------------------- rendu (page)
def _fmt_time(ts) -> str:
    if not ts:
        return ""
    return web.fmt_local(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat())






RENDER_NET_BUDGET = 2.5   # s max d'attente réseau au rendu (sinon repli)


















def _card(r: dict) -> dict:
    """Dict _sport_row d'une rencontre basket (live / à venir), réutilisé par render + Directs."""
    p = r.get("model_home")
    # Barre « Bookmakers » RETIRÉE : la barre combinée « Cotes & chances » (_pick_bars) porte les
    # cotes ET le % de chance (total 100 %). On garde la comparaison de forme.
    sub_html = ""
    fm = r.get("form")
    if fm:
        sub_html += web.form_compare(r["home"], fm[0], r["away"], fm[1])
    pk = r.get("pick")
    badge = ""   # plus de badge VALUE en haut à droite (value dans la bannière + l'analyse)
    # 🟢 Halo « gagné » en LIVE : la perle est-elle déjà gagnée vu le score (points) ?
    hp_l, ap_l = r.get("home_pts"), r.get("away_pts")

    def _st(p):
        return perle_live_status(p, hp_l, ap_l) if r["status"] == "inprogress" else None
    sp, sp2, spv = _st(r.get("perle")), _st(r.get("perle2")), _st(r.get("perle_value"))
    female = r.get("female") if r.get("female") is not None \
        else (r.get("league") or "").upper() == "WNBA"
    return {"tour": r.get("league", "Basket"), "sport": "Basket", "icon": "🏀",
            "status": r["status"], "time": _fmt_time(r.get("start")),
            "start_ts": r.get("start"), "home": r["home"], "away": r["away"], "female": female,
            "url": f'/basket/match/{r["id"]}' if r.get("sofa_ok") else None,
            "score": (f'{r.get("home_pts")}-{r.get("away_pts")}'
                      if r["status"] == "inprogress" and r.get("home_pts") is not None else ""),
            "live_time": r.get("live_time", ""), "periods": r.get("periods"),
            "prob": p, "prob_labels": (r["home"].split()[-1], r["away"].split()[-1]),
            "sub": sub_html, "badge": badge, "pick": bool(pk),
            "live_won": sp == "won", "live_won2": sp2 == "won", "live_won_value": spv == "won",
            "live_lost": sp == "lost", "live_lost2": sp2 == "lost", "live_lost_value": spv == "lost",
            "perle": r.get("perle"), "perle2": r.get("perle2"), "pick_kind": "confiance",
            **(web.bars_two_way(p, r.get("imp_home"), r.get("votes"), r["home"], r["away"])
               if p is not None else
               web.analyst_bars(r.get("oh"), None, r.get("oa"), r.get("votes")))}




def render(rows: list[dict], finished_rows: list[dict] | None = None,
           paused: bool = False, frag: bool = False) -> str:
    # Cartes COMPLÈTES (barres + perle « à jouer ») dans chaque section À venir / En direct /
    # Terminés (plus de section Confiances séparée). Terminés : ✓/✗ + score réel.
    live, upcoming, fin = [], [], []
    for r in rows:
        card = _card(r)
        (live if r["status"] == "inprogress" else upcoming).append(card)
    for r in (finished_rows or []):
        card = _card({**r, "status": "finished"})
        card["score"] = r.get("res_score") or "terminé"
        card["badge"] = r.get("res_badge", "")
        fin.append(card)

    intro = ('🏀 <b>NBA & WNBA</b>. Touchez un match pour son analyse complète (forme, '
             f'face-à-face). {web.BARS_LEGEND}')
    return web.render_sport_matches("basket", "Basket NBA & WNBA", [], live, upcoming, fin,
                                    intro=intro, paused=paused, frag=frag, confidences=[])


# ----------------------------------------------------------------- suivi (séparé)
BASKET_TRACK_PATH = os.path.join(_ROOT, "data", "tracking_basket.json")




