"""COMBINÉ MULTISPORT DU JOUR (info seule) — demande user 2026-07-10.

Chaque jour, UN seul combiné cross-sport reprenant les paris LES PLUS PROBABLES parmi tous les matchs
analysés, optimisé pour un TAUX DE RÉUSSITE maximal sous contrainte cote ≥ 1.9. Peut mélanger sports et
types de paris. AU PLUS une jambe par match (jambes indépendantes -> cote = produit, proba = produit).

⚠️ TOTALEMENT ISOLÉ du ROI/stats/calibration réels (comme app/provisional.py) : ce module écrit UNIQUEMENT
dans `data/combo_daily_track.json`, ne touche JAMAIS aux sidecars, à `stat_bet`, à la calibration ni à
`list_for`. Suivi « info seule », mise à plat 1 unité. On mesurera le taux avant toute intégration au ROI.
"""
from __future__ import annotations

import glob
import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK_PATH = os.path.join(_ROOT, "data", "combo_daily_track.json")

MIN_ODDS = 1.90           # cote minimale du combiné (demande user 2026-07-22 : « > 1,9 » — révisé depuis
#                           le 1,95 du 2026-07-17, pour un peu plus de marge face au plancher de proba)
MAX_LEGS = 5             # borne haute (au-delà, taux de réussite trop faible)
MIN_LEGS = 2             # un « combiné » = au moins 2 jambes
MIN_LEG_PROB = 0.65      # « les plus probables » : jambe fiable seulement (relevé pour la sécurité)
MIN_LEG_ODDS = 1.06      # une jambe quasi-sûre à cote ~1.01 n'apporte rien vers le seuil
MIN_COMBO_PROB = 0.0     # PLUS DE PLANCHER (demande user 2026-07-25) : combiné FOOT présent CHAQUE JOUR dès
#                          que cote ≥ 1,9 atteignable (pick_combo renvoie déjà le plus probable). Historique :
#                          à 1,95 »). On garde la cote ≥ 1,95 (grosse cote) et on rejette seulement le vrai
#                          « pile ou face » perdant (ex. 49 % le 22/07). Plancher CONSTANT. ⚠️ à cote 1,95 le
#                          break-even value est ~51 % : 50 % n'est donc PAS strictement value-positif — c'est un
#                          seuil de FIABILITÉ (« mieux que pile ou face »), pas un garde-fou EV. Contrepartie :
#                          certains jours SANS combiné si cote ≥ 1,95 ET proba > 50 % ne sont pas satisfiables.
# NOTE : le garde-fou EV (MIN_COMBO_EV, 2026-07-14 : s'abstenir sans value) a été RETIRÉ le 2026-07-17
# sur demande user explicite : « 1 combiné multisport par jour, le plus fiable, ≥ 1,95, TOUJOURS compté
# au ROI » — même les jours sans edge. On publie le combiné le PLUS PROBABLE atteignant 1,95 (None si le
# vivier ne permet PAS d'atteindre 1,95 OU si la meilleure proba reste < 55 %). cf. mémoire combo-daily-multisport.

# Marchés en PALIERS DE FIABILITÉ (taux de réussite MESURÉS, cf. COMBO_MISSION). On compare le PREMIER
# jeton du code (ex. "SETWIN 1 HOME" -> "SETWIN"). Le combiné se construit d'abord AVEC LE PALIER 1 SEUL
# (les jambes les plus SAFE) et ne descend d'un palier QUE s'il ne peut pas atteindre la cote min sinon.
_TIER1 = {"WIN", "DC", "REGTIME"}                       # résultat / double chance (~83 %) = le plus fiable
_TIER2 = {"SHOTSOT", "TEAMTOT", "SET", "SETWIN"}        # tirs cadrés (83 %), équipe marque / au moins un set (~79 %)
_TIER3 = {"OVER", "UNDER", "TOTGAMES", "TEAMGAMES", "SETSCORE"}   # totaux (points/buts/jeux), score de sets (+ variance)
_ALLOWED = _TIER1 | _TIER2 | _TIER3
# Marchés « prop » qu'on n'analyse PAS (aucune stat dans le dossier) et que `code_from_pick` MAL-CODE en
# total de BUTS (ex. « Hors-jeu Vasco Plus de 1.5 » -> `TEAMTOT HOME OVER 1.5`) : ils passeraient le filtre
# de fiabilité comme un marché buts ET se règleraient sur les BUTS (faux). On les EXCLUT du combiné par
# libellé (demande user 2026-07-25 : « pourquoi une jambe hors-jeu si on n'a aucune stat dessus »).
_COMBO_SEL_BLOCK = ("hors-jeu", "hors jeu", "offside", "corner", "carton", "faute", "touche",
                    "coup de pied", "remise en jeu")


def day_key(now=None) -> str:
    """CLÉ-JOUR unique du combiné du jour = JOUR SPORTIF LOCAL (06h→06h, cf. web._sport_date) — SOURCE
    UNIQUE partagée par le scan (création) ET l'affichage (lecture), 2026-07-21. Avant : chacun calculait
    `datetime.now(UTC).strftime(%Y-%m-%d)` -> une vague nocturne (ex. 03h locale = 01h UTC) prenait la
    date UTC du LENDEMAIN du jour sportif => risque de 2e combiné le même jour sportif + désalignement
    avec le calendrier 06h→06h. À 09h (scan quotidien) les deux clés coïncident -> les clés existantes
    restent valides. Import local de web (pas de cycle au chargement)."""
    from datetime import datetime, timezone
    from app import web as _w
    now = now or datetime.now(timezone.utc)
    return _w._sport_date(_w.to_local(now) or now).isoformat()


def _tier(code: str) -> int:
    """Palier de fiabilité (1 = le plus safe) du marché d'un code. 9 si hors liste blanche."""
    tok = (code or "").split()[0] if code else ""
    if tok in _TIER1:
        return 1
    if tok in _TIER2:
        return 2
    if tok in _TIER3:
        return 3
    return 9


SIM_SPORTS = ()   # FOOT SEUL (user 2026-08-07) : tennis/basket retirés -> plus de combinés simulés. Foot = combiné compté au ROI


def _track_path(sport: str = "foot", variant: str = "") -> str:
    """Fichier de suivi du combiné du jour par sport. FOOT = combo_daily_track.json (compté au ROI,
    inchangé) ; tennis/basket = combo_daily_<sport>.json (SIMULÉ, hors ROI — demande user 2026-07-25 :
    un combiné par jour aussi en tennis/basket pour muscler les analyses/stats).
    `variant` (user 2026-08-19) : VARIANTE FOOT dans un fichier dédié (ex. « cote2 » -> combo_daily_cote2.json)
    pour le 2ᵉ combiné du jour (Cote 2), indépendant du Sûr, HORS ROI. Défaut '' = comportement inchangé."""
    if variant:
        return os.path.join(_ROOT, "data", f"combo_daily_{variant}.json")
    return TRACK_PATH if sport == "foot" else os.path.join(_ROOT, "data", f"combo_daily_{sport}.json")


def _load(sport: str = "foot", variant: str = "") -> dict:
    try:
        with open(_track_path(sport, variant), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict, sport: str = "foot", variant: str = "") -> None:
    path = _track_path(sport, variant)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def load() -> dict:
    """Snapshot brut du suivi (dict par date). Sert à dériver `stats()` ET `entries()` du MÊME état."""
    return _load()


def leg_ids(day: str | None = None) -> set:
    """Ids (mid) des matchs qui sont JAMBES d'un combiné du jour. `day` -> uniquement ce jour ; None ->
    toutes les dates du suivi. Sert à la DÉDUP « un pari n'apparaît pas à plusieurs endroits » (demande
    user 2026-07-12) : un match jambe du combiné du jour ne doit PAS être AUSSI suivi/affiché comme
    provisoire (sinon la même erreur se répercute à deux endroits, avec deux résultats possibles)."""
    d = _load()
    days = [d.get(day)] if day is not None else list(d.values())
    out: set = set()
    for entry in days:
        if isinstance(entry, dict):
            for leg in entry.get("legs") or []:
                mid = str(leg.get("mid") or "")
                if mid:
                    out.add(mid)
    return out


def leg_names(day: str | None = None) -> list:
    """(home, away) de chaque match JAMBE d'un combiné du jour, TOUS COMBINÉS confondus (Sûr variant='' +
    Cote 2 variant='cote2'). Complète `leg_ids` pour la DÉDUP : l'ID d'un même match DIFFÈRE souvent entre le
    combiné (mid sidecar/ESPN) et le programme (id Unibet) -> la dédup PAR ID seule LAISSE PASSER la jambe en
    provisoire (bug vécu Atlanta Dream : mid 15415813 ≠ id Unibet 1026378509). Le dédoublonnage PAR NOM (cf.
    web._prog_pair) est robuste à cet écart d'id. ⚠️ user 2026-08-20 : lire les DEUX tracks (le 2e combiné est
    dans un fichier dédié `combo_daily_cote2.json`) — sinon les jambes du Cote 2 (ex. Coquimbo) fuitaient dans
    la grille « Programme du jour » = doublon (match affiché à la fois en combiné ET en programme)."""
    out: list = []
    for _var in ("", "cote2", "soir"):
        d = _load(variant=_var)
        days = [d.get(day)] if day is not None else list(d.values())
        for entry in days:
            if isinstance(entry, dict):
                for leg in entry.get("legs") or []:
                    h, a = leg.get("home"), leg.get("away")
                    if h and a:
                        out.append((h, a))
                    elif leg.get("name") and " - " in str(leg.get("name")):
                        _h, _, _a = str(leg["name"]).partition(" - ")
                        out.append((_h, _a))
    return out


def _pair_key(home, away) -> frozenset:
    """Clé de match = paire de noms normalisés (mêmes règles que web._prog_pair) -> robuste à l'écart
    d'id Unibet↔sidecar. Source unique pour comparer un match à une jambe de combiné PAR NOM."""
    import re
    n = lambda s: re.sub(r"\W+", "", (s or "").lower())
    return frozenset(x for x in (n(home), n(away)) if x)


def leg_pairs(day: str | None = None) -> set:
    """Ensemble des clés-noms (`_pair_key`) des jambes de combiné du jour -> dédup PAR NOM prête à l'emploi."""
    return {_pair_key(h, a) for (h, a) in leg_names(day)}


def is_daily_leg(mid, home: str = "", away: str = "", day: str | None = None) -> bool:
    """SOURCE UNIQUE de la dédup « pas de jambe de combiné à plusieurs endroits » : vrai si ce match est une
    jambe d'un combiné du jour, par ID **OU par NOM**. Le nom est INDISPENSABLE car l'id diffère entre le
    combiné (mid sidecar/ESPN) et le programme/suivi (id Unibet) -> l'exclusion par id seule laissait passer
    la jambe (bug vécu Atlanta Dream). `day=None` -> toutes les dates du suivi."""
    if str(mid or "") in leg_ids(day):
        return True
    if home and away:
        return _pair_key(home, away) in leg_pairs(day)
    return False


# ------------------------------------------------------------------ moteur de sélection
def _prod(xs):
    p = 1.0
    for x in xs:
        p *= x
    return p


def pick_combo(cands: list[dict], min_odds: float = MIN_ODDS, max_legs: int = MAX_LEGS,
               min_legs: int = MIN_LEGS, min_leg_prob: float = MIN_LEG_PROB,
               min_leg_odds: float = MIN_LEG_ODDS, min_combo_prob: float = MIN_COMBO_PROB) -> dict | None:
    """Choisit les jambes MAXIMISANT le produit des probabilités sous contrainte produit des cotes
    ≥ min_odds, ≤ 1 jambe/match (`mid`), 2..max_legs jambes. Glouton par efficacité
    log(cote)/(−log(prob)) [pousse vers le seuil de cote en perdant le moins de proba] + raffinement
    (retrait des jambes superflues + swaps). None si irréalisable OU si la MEILLEURE proba atteignable
    reste < min_combo_prob (plancher : pas de combiné « pile ou face »). cands : [{mid, sport, sel, cote,
    prob(0-1), code, name, home, away, start, comp}]."""
    pool = [c for c in cands
            if c.get("code") and isinstance(c.get("cote"), (int, float))
            and isinstance(c.get("prob"), (int, float))
            and c["prob"] >= min_leg_prob and c["cote"] >= min_leg_odds]
    if not pool:
        return None
    # jusqu'à 3 marchés par match (laisse « grosse jambe sûre » vs « petite très sûre » à l'optimiseur)
    by_mid: dict = {}
    for c in sorted(pool, key=lambda x: -x["prob"]):
        by_mid.setdefault(c["mid"], [])
        if len(by_mid[c["mid"]]) < 3:
            by_mid[c["mid"]].append(c)
    flat = [c for lst in by_mid.values() for c in lst]

    def odds(ls):
        return _prod([x["cote"] for x in ls])

    def prob(ls):
        return _prod([x["prob"] for x in ls])

    def eff(c):
        risk = -math.log(c["prob"])
        return math.log(c["cote"]) / risk if risk > 1e-9 else float("inf")

    chosen: list = []
    used: set = set()
    while odds(chosen) < min_odds and len(chosen) < max_legs:
        avail = [c for c in flat if c["mid"] not in used]
        if not avail:
            break
        nxt = max(avail, key=eff)
        chosen.append(nxt)
        used.add(nxt["mid"])
    while len(chosen) < min_legs:                    # force le minimum de jambes (jambe la + sûre dispo)
        avail = [c for c in flat if c["mid"] not in used]
        if not avail:
            break
        nxt = max(avail, key=lambda c: c["prob"])
        chosen.append(nxt)
        used.add(nxt["mid"])
    if odds(chosen) < min_odds or len(chosen) < min_legs:
        return None

    improved = True
    while improved:                                  # retire toute jambe superflue (reste ≥ seuil) -> +proba
        improved = False
        for c in sorted(chosen, key=lambda x: x["prob"]):
            if len(chosen) <= min_legs:
                break
            rest = [x for x in chosen if x is not c]
            if odds(rest) >= min_odds and prob(rest) > prob(chosen):
                chosen, used, improved = rest, {x["mid"] for x in rest}, True
                break
    improved = True
    while improved:                                  # swaps 1-pour-1 qui gardent ≥ seuil et augmentent la proba
        improved = False
        for c in list(chosen):
            for r in flat:
                if r["mid"] in (used - {c["mid"]}) or r is c:
                    continue
                cand = [r if x is c else x for x in chosen]
                if len({x["mid"] for x in cand}) != len(cand):
                    continue
                if odds(cand) >= min_odds and prob(cand) > prob(chosen):
                    chosen, used, improved = cand, {x["mid"] for x in cand}, True
                    break
            if improved:
                break

    best_prob = prob(chosen)
    if best_prob < min_combo_prob:                   # PLANCHER (demande user 2026-07-22) : le combiné le PLUS
        return None                                  # probable du jour reste sous 55 % -> pas de combiné (mieux
        #                                              vaut aucun qu'un « pile ou face », ex. 49 % le 22/07).
    chosen.sort(key=lambda x: -x["prob"])
    return {"legs": chosen, "cote": round(odds(chosen), 2), "prob": best_prob}


def _candidates_for_day(day: str, sport: str = "foot") -> list[dict]:
    """Extrait les jambes candidates (marchés autorisés, réglables, prob ≥ seuil) des matchs du `sport`
    du jour SPORTIF `day`, encore À VENIR. Source = fantômes `shadow` + pari retenu `bets` (dédup par
    (match, code), meilleure proba). `prob` en fraction 0-1. `sport` : foot (ROI) / tennis / basket (simulé)."""
    from datetime import datetime
    from app import analyses
    from app import web as _w
    from app.settle_analyst import code_from_pick

    def _sport_day_of(iso: str) -> str:
        """Jour SPORTIF (06h→06h local) d'un coup d'envoi — cohérent avec tout le reste de l'app. Un match
        de 02h la nuit suivante appartient encore au jour sportif de la veille (demande user 2026-07-25 :
        le combiné du jour peut inclure les matchs jusqu'à 06h le lendemain)."""
        try:
            return _w._sport_date(_w.to_local(datetime.fromisoformat((iso or "").replace("Z", "+00:00")))).isoformat()
        except (ValueError, AttributeError, TypeError):
            return (iso or "")[:10]
    # Un combiné du jour PAR SPORT (demande user 2026-07-25). FOOT = compté au ROI -> même discipline que les
    # simples : si le foot est bridé (ex_sports/background), pas de combiné foot. Tennis/basket = SIMULÉ (hors
    # ROI) -> on le construit MÊME s'ils sont en pause (justement pour mesurer leur potentiel combiné).
    if sport == "foot":
        try:
            _ex_sports, _ = analyses.auto_exclusions()
            _ex_sports = set(_ex_sports) | analyses.background_sports()
        except Exception:
            _ex_sports = set()
        if "foot" in _ex_sports:                       # foot bridé -> aucun combiné foot (cas rare)
            return []
    out: list[dict] = []
    for side in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(side, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("sport") != sport:                    # UN SEUL sport par combiné (demande user 2026-07-25)
            continue
        if _sport_day_of(d.get("start")) != day:       # JOUR SPORTIF (06h→06h) : inclut la nuit jusqu'à 06h
            continue
        if analyses.status_of(d) != "notstarted":      # déjà commencé/fini -> pas jouable au combiné du jour
            continue
        mid = str(d.get("id") or "")
        if not mid:
            continue
        preds = list(d.get("shadow") or [])
        for b in (d.get("bets") or []):                 # le pari retenu compte aussi (cote sous `odds`)
            preds.append({"sel": b.get("sel"), "cote": b.get("odds"), "prob": b.get("prob"),
                          "code": b.get("code")})
        best: dict = {}
        for p in preds:
            _sel_l = str(p.get("sel") or "").lower()
            if any(w in _sel_l for w in _COMBO_SEL_BLOCK):   # marché prop sans stat -> jamais en jambe
                continue
            # RE-DÉRIVER le code depuis le LIBELLÉ (le code stocké peut être périmé/générique : un
            # fantôme « Tiebreaks +0.5 » a l'ancien code `OVER 0.5` qui réglerait un total de BUTS =
            # FAUX). code_from_pick reflète la logique de règlement ACTUELLE -> code correct + à jour.
            code = code_from_pick(p.get("sel") or "", d.get("sport"), d.get("home", ""),
                                  d.get("away", "")).strip()
            # « DC 12 » (1 OU 2 = double chance SANS le nul) BANNI du combiné (demande user 2026-07-29) :
            # c'est la double chance la PLUS faible — elle perd exactement sur le NUL, l'issue la plus
            # corrélée d'un match serré. On ne joue en jambe « double chance » que 1X / X2 (favori + nul).
            if code == "DC 12":
                continue
            if not code or code.split()[0] not in _ALLOWED:
                continue
            pr, co = p.get("prob"), p.get("cote")
            if not isinstance(pr, (int, float)) or not isinstance(co, (int, float)):
                continue
            # CALIBRATION + REFROIDISSEMENT (audit 2026-07-23) : la sélection des jambes utilisait la proba
            # BRUTE du LLM alors que le combiné du jour est COMPTÉ AU ROI — la sur-confiance se compose en
            # combiné (même boucle de feedback que le combiné de match, generate_analyses ~1449). On calibre
            # (calibrated_conf) puis on refroidit (OVER total-équipe basket, _cool_conf) AVANT les seuils
            # MIN_LEG_PROB / MIN_COMBO_PROB. Affichage des jambes déjà calibré (web._leg_card) -> cohérent.
            _pct = pr if pr > 1 else pr * 100.0
            try:
                from app.analyses import calibrated_conf as _cc, _cool_conf as _cool
                _pct2 = _cool(_cc(_pct, d.get("sport"), code), d.get("sport"), code, d.get("streaks"))
                if _pct2 is not None:
                    _pct = _pct2
            except Exception:
                pass
            prf = _pct / 100.0
            prev = best.get(code)
            if prev is None or prf > prev["prob"]:
                best[code] = {"mid": mid, "sport": d.get("sport"), "sel": p.get("sel"),
                              "cote": float(co), "prob": prf, "code": code, "name": d.get("name"),
                              "home": d.get("home"), "away": d.get("away"), "start": d.get("start"),
                              "comp": d.get("comp")}
        out.extend(best.values())
    return out


def build_for_day(day: str, sport: str = "foot") -> dict | None:
    """Construit LE combiné du jour du `sport` (foot=ROI, tennis/basket=simulé), en maximisant la proba
    sous cote ≥ 1.9. None si aucun combiné fiable possible ce jour-là."""
    cands = _candidates_for_day(day, sport)
    # LE PLUS FIABLE (demande user 2026-07-17) : on maximise la PROBABILITÉ de gain sur TOUS les marchés
    # analysés. L'ancienne escalade par paliers (résultat/DC d'abord) s'arrêtait au 1er palier atteignant le
    # seuil et, forcée à 1,95, imposait PLUS de jambes -> combiné MOINS probable (mesuré 2026-07-17 :
    # palier≤2 = 36 %/EV−24 % vs tous marchés = 46 %/EV−10 %). pick_combo renvoie le combiné le plus probable
    # sous la contrainte de cote -> exactement « le plus fiable ». (`_tier` conservé pour d'éventuels tris.)
    combo = pick_combo(cands)
    if not combo:
        return None
    # PLUS DE GARDE-FOU VALUE (demande user 2026-07-17) : le combiné du jour est publié CHAQUE jour dès que
    # le vivier atteint 1,95, qu'il ait une value ou non, et TOUJOURS compté au ROI. `pick_combo` renvoie
    # déjà le combiné le PLUS PROBABLE sous la contrainte de cote -> « le plus fiable ». (Historique :
    # l'ancien filtre EV≥0.05, 2026-07-14, s'abstenait sans edge ; retiré sur choix explicite du proprio.)
    legs = [{"mid": l["mid"], "sport": l["sport"], "name": l.get("name"), "home": l.get("home"),
             "away": l.get("away"), "start": l.get("start"), "comp": l.get("comp"),
             "sel": l["sel"], "cote": l["cote"], "prob": round(l["prob"], 4),
             "code": l["code"], "result": None, "score": None} for l in combo["legs"]]
    return {"date": day, "sport": sport, "cote": combo["cote"], "prob": round(combo["prob"], 4),
            "legs": legs, "result": None, "sent": False, "created": None}


def telegram_text(cb: dict) -> str:
    """Message HTML (parse_mode=HTML) du combiné du jour pour Telegram. Noms échappés."""
    import html as _h
    emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
    out = ["🎯 <b>COMBINÉ FOOT DU JOUR</b>",
           f"Cote <b>@{cb.get('cote')}</b> · chances <b>{round((cb.get('prob') or 0) * 100)}%</b> "
           f"· {len(cb.get('legs') or [])} jambes", ""]
    from app.analyses import pretty_sel as _psel
    for l in cb.get("legs") or []:
        _s = _psel(str(l.get('sel') or ''), l.get('home', ''), l.get('away', ''))
        out.append(f"{emo.get(l.get('sport'), '•')} <b>{_h.escape(_s)}</b> "
                   f"@{l.get('cote')}")
        out.append(f"   <i>{_h.escape(str(l.get('name') or ''))}</i>")
    out += ["", "🎯 <i>Compté au ROI (mise 1 u) — les paris les plus probables du jour.</i>"]
    return "\n".join(out)


def record_daily(combo: dict, day: str, sport: str = "foot", variant: str = "") -> bool:
    """Enregistre le combiné du jour du `sport` (UN par date). Ne réécrit PAS s'il est déjà ENVOYÉ (figé) ou
    déjà réglé. Renvoie True si (ré)écrit. `variant` -> fichier dédié (2ᵉ combiné « cote2 », hors ROI)."""
    if not combo or not combo.get("legs"):
        return False
    # GARDE ANTI-CONTAMINATION CROISÉE (bug 2026-07-26) : un vieux combiné TENNIS avait survécu dans le
    # track FOOT après la bascule multisport->par-sport (b073954) et risquait d'être compté au ROI foot.
    # Un combiné n'entre QUE dans le track de SON sport : toutes ses jambes DOIVENT être du `sport` du track.
    if (combo.get("sport") and combo["sport"] != sport) or \
            any(l.get("sport") and l.get("sport") != sport for l in combo.get("legs") or []):
        return False
    d = _load(sport, variant)
    prev = d.get(day)
    if isinstance(prev, dict) and (prev.get("sent") or prev.get("result") in ("won", "lost", "void")):
        return False                                  # figé (posté/réglé) -> jamais réécrit
    d[day] = combo
    _save(d, sport, variant)
    return True


def mark_sent(day: str, sport: str = "foot", variant: str = "") -> None:
    """Marque le combiné du jour du `sport` comme ENVOYÉ (Telegram) -> figé (published = frozen)."""
    d = _load(sport, variant)
    if isinstance(d.get(day), dict):
        d[day]["sent"] = True
        _save(d, sport, variant)


def enrich_narratives(day: str, whys_by_mid: dict, synth: str | None = None, sport: str = "foot",
                      variant: str = "") -> bool:
    """Met à jour UNIQUEMENT le NARRATIF (« pourquoi » des jambes + synthèse) d'un combiné DÉJÀ FIGÉ, SANS
    jamais toucher à la SÉLECTION (mid/sel/cote/prob/code) ni au RÉSULTAT (result/score/sent). Sert au
    combiné du matin ancré Pinnacle (user 2026-08-17) : le « pourquoi » chiffré Pinnacle posé le matin est
    remplacé par le « pourquoi » factuel complet dès que la vague a analysé le match. Idempotent. Renvoie
    True si un champ a changé (donc sauvegarde)."""
    d = _load(sport, variant)
    cb = d.get(day)
    if not isinstance(cb, dict):
        return False
    changed = False
    for leg in cb.get("legs") or []:
        w = whys_by_mid.get(str(leg.get("mid") or ""))
        if w and w != leg.get("why"):
            leg["why"] = w
            changed = True
    if synth and synth != cb.get("synth"):
        cb["synth"] = synth
        changed = True
    if changed:
        _save(d, sport, variant)
    return changed


def _derive_combo(legs: list) -> str | None:
    """Résultat d'un combiné depuis ses jambes : **lost** si ≥1 perdue ; **won** si ≥1 gagnée (push/void
    NEUTRES, retirées) ; **void** si QUE des push/void. **None** si ≥1 jambe encore en attente. Une jambe
    ANNULÉE (void) ne BLOQUE donc PAS le combiné : il gagne ou perd selon les AUTRES jambes (demande user
    2026-07-18)."""
    res = [l.get("result") for l in (legs or [])]
    # UNE JAMBE PERDUE -> combiné PERDU IMMÉDIATEMENT, sans attendre le règlement des autres jambes (demande
    # user 2026-07-28 : un combiné est mort dès qu'une jambe saute). Testé AVANT la présence de jambes en
    # attente.
    if "lost" in res:
        return "lost"
    if any(r not in ("won", "lost", "push", "void") for r in res):
        return None                       # aucune perdue mais ≥1 jambe encore en attente -> pas tranché
    if "won" in res:
        return "won"
    return "void"


def settle_all() -> int:
    """Règle les combinés du jour de TOUS les sports (foot compté au ROI + tennis/basket simulés) + la
    VARIANTE « Cote 2 » du foot (2ᵉ combiné du jour, hors ROI, user 2026-08-19)."""
    return (sum(settle_pending(sp) for sp in ("foot",) + SIM_SPORTS)
            + settle_pending("foot", "cote2")
            + settle_pending("foot", "soir"))     # « Combiné du soir » (user 2026-08-30)


def settle_pending(sport: str = "foot", variant: str = "") -> int:
    """Règle les jambes des combinés du jour du `sport` dont les matchs sont terminés (Flashscore + repli
    LiveScore + `settle_pick`), puis tranche le combiné : lost si ≥1 jambe perdue ; won si ≥1 gagnée
    (push/void retirés) ; void si toutes push/void. Idempotent. Renvoie le nb de combinés tranchés.
    `variant` (ex. « cote2 ») -> règle le 2ᵉ combiné du jour dans son fichier dédié."""
    from app import flashscore, livescore, analyses as _an
    from app.settle_analyst import settle_pick
    d = _load(sport, variant)
    # RÉCHAUFFE le cache Unibet (heure fraîche + score live) AVANT la borne void : le règlement tourne souvent
    # dans un process FROID (tâche reconcile) où les caches sont vides -> un match DÉCALÉ/EN COURS paraît
    # « fini » et se fait voider à tort (bug 2026-07-23 : Hanfmann-Baez affiché « ANNULÉ » en plein 1er set).
    # En contexte API (boucle async déjà active + cache chaud via le warmer), asyncio.run lève -> ignoré (les
    # caches sont déjà chauds, la garde live/fresh fonctionne quand même).
    try:
        import asyncio as _aio
        from app import match_select as _ms0
        for _sp0 in {l.get("sport") for cb in d.values() if isinstance(cb, dict)
                     for l in (cb.get("legs") or []) if l.get("sport")}:
            try:
                _aio.run(_ms0._fetch_live_odds_now(_sp0))
            except Exception:
                pass
    except Exception:
        pass
    n = 0
    changed = False           # persiste la PROGRESSION (jambes réglées + tries) même si le combiné n'est
    #                           pas encore tranché -> les tries s'accumulent (borne void OK) et les jambes
    #                           déjà réglées ne sont pas re-fetchées à chaque passe.
    import datetime as _dt
    _today = _dt.datetime.now(_dt.timezone.utc).date()
    for day, cb in list(d.items()):
        if not isinstance(cb, dict):
            continue
        # Combiné DÉJÀ tranché (ROI figé, compteur monotone) : on ne recalcule JAMAIS son résultat. MAIS on
        # continue à FINALISER ses jambes encore en attente/void pour l'AFFICHAGE — une jambe void'ée trop tôt
        # (match fini APRÈS la borne 8 essais, cf. Nuno Borges 2026-07-14 : combiné perdu via l'autre jambe,
        # Borges resté « remboursé » alors qu'il a gagné 2-0) doit montrer son vrai résultat. Borné à 3 j pour
        # ne pas re-taper les sources indéfiniment (au-delà, le void restant est définitif).
        _frozen = cb.get("result") in ("won", "lost", "void")
        _pending = any(l.get("result") not in ("won", "lost", "push")
                       for l in (cb.get("legs") or []))
        try:
            _age = (_today - _dt.date.fromisoformat(day)).days
        except (ValueError, TypeError):
            _age = 0
        # SELF-HEAL COLLISION DE NOM (fix 2026-08-09, 0 réseau) : une jambe FOOT déjà réglée par RÉSOLUTION DE
        # NOM (avant que le sidecar du match existe) a pu capter le score d'un match HOMONYME — cas vécu :
        # Sparta-Feyenoord réglé « 7-0 » au lieu de « 0-1 » -> DC Feyenoord faussement PERDUE (affichage cassé).
        # Dès que le sidecar (AUTORITÉ, result.raw) a un score final DIFFÉRENT, on RÉALIGNE la jambe dessus,
        # même DÉJÀ réglée. Tourne AVANT la garde de skip (un combiné figé + toutes jambes réglées passait à
        # travers sinon). Borné à 3 j. On corrige la JAMBE (affichage) ; le résultat GLOBAL d'un combiné déjà
        # tranché (ROI figé, compteur monotone) n'est PAS recalculé ici — seule la passe non-figée le fait.
        if _age <= 3:
            for leg in cb.get("legs") or []:
                if leg.get("result") not in ("won", "lost", "push") or leg.get("sport") not in (None, "foot"):
                    continue
                try:
                    _sm2 = _an.meta(leg.get("sport"), str(leg.get("mid") or "")) or {}
                    _raw2 = (_sm2.get("result") or {}).get("raw")
                    _lbl2 = _raw2.get("label") if isinstance(_raw2, dict) else None
                    if not (isinstance(_raw2, dict) and _raw2.get("home") is not None
                            and _lbl2 and _lbl2 != leg.get("score")):
                        continue
                    from app.settle_analyst import code_from_pick as _cfp_h
                    _lc_h = (_cfp_h(leg.get("sel", ""), leg.get("sport"),
                                    leg.get("home", ""), leg.get("away", "")) or leg.get("code", ""))
                    _res_h = settle_pick(_lc_h, _raw2)
                    if _res_h in ("won", "lost", "push"):
                        leg["result"], leg["score"], changed = _res_h, _lbl2, True
                except Exception:
                    pass
        if _frozen and not _pending:
            continue                     # (une éventuelle correction self-heal est persistée par le _save final)
        if _frozen and _age > 3:
            continue
        for leg in cb.get("legs") or []:
            if leg.get("result") in ("won", "lost", "push"):   # void = RÉVISABLE (pas won/lost/push définitifs)
                continue
            q = {"home": leg.get("home", ""), "away": leg.get("away", ""),
                 "start": leg.get("start"), "sofa_id": ""}
            score = None
            # PRIORITÉ au score DÉJÀ RÉGLÉ du sidecar du match (result.raw) : autorité de vérité, 0 réseau.
            # Le match est souvent déjà réglé côté analyses alors que le lookup PAR NOM échoue (nom
            # brésilien/WNBA introuvable chez Flashscore) -> sans ça, la jambe était VOIDée à tort après 8
            # essais et le combiné remboursé alors qu'il avait GAGNÉ (bug vécu 2026-07-13). Fix 2026-07-14.
            try:
                from app import analyses as _an
                _sm = _an.meta(leg.get("sport"), str(leg.get("mid") or "")) or {}
                _raw = (_sm.get("result") or {}).get("raw")
                if isinstance(_raw, dict) and (_raw.get("home") is not None
                                               or _raw.get("sets_home") is not None
                                               or _raw.get("periods")):
                    score = _raw
            except Exception:
                score = None
            # ⛔ GARDE-FOU « JAMAIS DE RÈGLEMENT SUR UN MATCH PAS FINI » (source-agnostique).
            # On n'interroge une source de score EXTERNE que si NOTRE horloge dit que le match devrait déjà
            # être terminé (`likely_finished`). Sans ça, une source pouvait renvoyer un FAUX score « final »
            # d'un AUTRE match homonyme déjà fini (collision de noms) pendant que le nôtre est EN COURS — bug
            # vécu 2026-07-18 : Sport Recife-Operário réglé « lost 3-0 » via sportradar en pleine 66e minute
            # (vrai live 2-1, Operário avait marqué) -> combiné faussement perdu. Le sidecar `result.raw`
            # ci-dessus reste autorisé (c'est le règlement DÉJÀ vérifié du match lui-même). cf. mémoire
            # settle-never-on-live-score. Match pas encore « fini par l'horloge » -> on laisse la jambe en
            # attente (info-seule, aucune urgence : la passe suivante / le scan 09h la règlera pour de vrai).
            _leg_done = _an.likely_finished({"start": leg.get("start"), "sport": leg.get("sport")})
            if score is None and _leg_done:
                try:
                    score = flashscore.final_score(leg.get("sport"), q) or \
                        livescore.final_score(leg.get("sport"), q)
                except Exception:
                    score = None
            if _leg_done and (not score or not score.get("periods")):
                # Repli SPORTRADAR (GISMO) : score final + périodes détaillées que Flashscore/LiveScore
                # ne donnent pas toujours (et matching de nom brésilien corrigé côté sportradar). Aligne le
                # règlement du combiné du jour sur les autres chemins de règlement.
                try:
                    import asyncio
                    import httpx
                    from app import sportradar

                    async def _sr_score():
                        async with httpx.AsyncClient(timeout=20) as _c:
                            return await sportradar.final_score(_c, leg.get("sport"), q)
                    srs = asyncio.run(_sr_score())
                    if srs and (srs.get("periods") or srs.get("label")):
                        score = srs if not score else {**score, "periods": srs.get("periods") or score.get("periods")}
                except Exception:
                    pass
            if not score:
                continue                              # pas de score final fiable -> on retente (borné plus bas)
            # CODE RE-DÉRIVÉ du LIBELLÉ (jamais le code STOCKÉ, qui peut être périmé/faux — cf. mémoire
            # combo-single-source-of-truth). Bug 2026-07-28 : une jambe « Nombre total de jeux Plus de 8.5 -
            # Set 1 » portait un code figé « OVER 8.5 » (générique) -> settle_pick ne résolvait pas -> VOID au
            # lieu de GAGNÉE. code_from_pick (corrigé) rend « SETGAMES 1 OVER 8.5 ».
            try:
                from app.settle_analyst import code_from_pick as _cfp_leg
                _lc = (_cfp_leg(leg.get("sel", ""), leg.get("sport"),
                                leg.get("home", ""), leg.get("away", "")) or leg.get("code", ""))
            except Exception:
                _lc = leg.get("code", "")
            try:
                res = settle_pick(_lc, score)
            except Exception:
                res = None
            # SCORE TROUVÉ : si settle_pick tranche -> résultat ; sinon le code est IRRÉCUPÉRABLE
            # (non réglable sur ce match fini) -> VOID, on ne bloque pas le combiné dessus.
            leg["result"] = res if res in ("won", "lost", "push") else "void"
            _lbl = score.get("label") or ""
            # TENNIS : afficher le DÉTAIL par set (« 6-7 6-3 6-4 ») et non « 2-1 (sets) » — sinon le scoreboard
            # lit « 2-1 » comme les jeux du set 1 (bug 2026-07-28). On reconstruit depuis les périodes.
            if leg.get("sport") == "tennis" and isinstance(score.get("periods"), dict) and score.get("periods"):
                try:
                    _pk = sorted(score["periods"].items(), key=lambda kv: int(kv[0]))
                    _det = " ".join(f"{int(v[0])}-{int(v[1])}" for _, v in _pk if isinstance(v, (list, tuple)) and len(v) >= 2)
                    if _det:
                        _lbl = _det
                        leg["periods"] = [(int(k), int(v[0]), int(v[1])) for k, v in _pk
                                          if isinstance(v, (list, tuple)) and len(v) >= 2]
                except Exception:
                    pass
            leg["score"] = _lbl
            changed = True
        legs = cb.get("legs") or []
        if not _frozen:
            # tries = VRAIES TENTATIVES de règlement (audit 2026-07-23) : n'incrémente QUE si au moins une
            # jambe non réglée est likely_finished (on a réellement cherché un score). Avant, chaque passe
            # reconcile (10 min) incrémentait dès la CRÉATION du combiné -> tries 39+ avant même le coup
            # d'envoi, borne « 8 essais » consommée d'avance -> void à la 1ère passe post-match (racine de
            # l'incident Hanfmann « ANNULÉ » en plein set).
            if any(l.get("result") not in ("won", "lost", "push", "void")
                   and _an.likely_finished({"start": l.get("start"), "sport": l.get("sport")})
                   for l in legs):
                cb["tries"] = (cb.get("tries") or 0) + 1
                changed = True                        # tries accumulés -> la borne void finit par mordre
        # BORNE : à tries≥8, on void SEULEMENT les jambes dont le MATCH est FINI (donnée morte) ; une jambe
        # dont le match n'a pas encore fini (coup d'envoi tardif) RESTE en attente -> plus de void prématuré
        # (bug 07-17 : Mirassol 23:00 voidé AVANT la fin -> combiné faussement « remboursé » alors que gagné).
        # + BORNE TEMPORELLE (audit 2026-07-23) : jamais de void < 12 h après le coup d'envoi — avec le
        # cycle 10 min, tries≥8 ne représente que ~80 min de retries ; un score en retard de publication
        # (matching de noms) mérite plus de temps avant l'ultime recours.
        if (cb.get("tries") or 0) >= 8:
            from datetime import datetime as _dtm, timezone as _tzu
            from app import match_select as _ms
            for l in legs:
                if l.get("result") in ("won", "lost", "push", "void"):
                    continue
                if not _an.likely_finished({"start": l.get("start"), "sport": l.get("sport")}):
                    continue
                try:
                    _age_h = (_dtm.now(_tzu.utc)
                              - _dtm.fromisoformat(str(l.get("start")).replace("Z", "+00:00"))
                              ).total_seconds() / 3600.0
                except (ValueError, TypeError):
                    _age_h = 0.0
                if _age_h < 12.0:
                    continue                           # trop tôt pour l'ultime recours -> on retente
                # GARDE « MATCH PAS RÉELLEMENT FINI » (fix 2026-07-23, bug Hanfmann-Baez affiché « ANNULÉ »
                # en plein 1er set) : likely_finished se base sur l'heure STOCKÉE, PÉRIMÉE si le match a été
                # DÉCALÉ (tennis qui glisse, ex. 12:10 -> 12:50). Avant de void, on vérifie l'état RÉEL Unibet :
                # un SCORE LIVE ou un coup d'envoi Unibet encore FUTUR = match NON fini -> on NE VOID PAS.
                # home/away STOCKÉS sur la jambe (audit : name peut manquer, le re-parse était fragile).
                _lh, _la = l.get("home"), l.get("away")
                if not (_lh and _la):
                    _lh, _, _la = str(l.get("name") or "").partition(" - ")
                try:
                    _has_live = bool(_ms.live_state_for(l.get("sport"), _lh, _la))
                    _st, _ = _ms.fresh_status(l.get("sport"), _lh, _la, "finished", _has_live,
                                              start_iso=l.get("start"))
                except Exception:
                    continue                           # garde indisponible (réseau ?) -> on NE void PAS (fail-safe)
                if _has_live or _st in ("inprogress", "notstarted"):
                    continue                           # en cours OU décalé (pas encore commencé) -> pas de void
                l["result"] = "void"
        # RÉSULTAT (re)DÉRIVÉ : jambe annulée NEUTRE (ne bloque pas). Corrige aussi un résultat FIGÉ À TORT
        # (ex. void pose par la borne alors que 2 jambes ont finalement GAGNÉ -> won). demande user 2026-07-18.
        _dv = _derive_combo(legs)
        if _dv is None:
            continue                                  # encore des jambes VRAIMENT en attente
        if _dv != cb.get("result"):
            cb["result"] = _dv
            changed = True
            n += 1
    if changed:
        _save(d, sport, variant)  # BUG 2026-07-26 : `_save(d)` sans sport écrivait le dict du sport COURANT
        #                          dans le fichier FOOT (défaut) -> settle_pending('tennis') écrasait
        #                          combo_daily_track.json avec le combiné tennis à chaque reconcile.
    return n


def _combo_result_profit(cb: dict) -> float:
    """Profit info-seule (mise à plat 1 u) d'un combiné réglé : cote EFFECTIVE (push retirées) − 1 si
    gagné, −1 si perdu, 0 si remboursé."""
    if cb.get("result") == "won":
        eff = _prod([l["cote"] for l in cb.get("legs") or [] if l.get("result") == "won"])
        # Cote effective ARRONDIE à 2 décimales — une cote de pari est toujours à 2 décimales chez le book ;
        # sans ça le produit des jambes s'affichait « 1,5428 » (demande user 2026-07-18). Source unique :
        # roi_events / equity_curve / stats passent tous par ici -> affichage ET ROI restent cohérents.
        return round(eff, 2) - 1
    if cb.get("result") == "lost":
        return -1.0
    return 0.0


def today(day: str, d: dict | None = None, sport: str = "foot", variant: str = "") -> dict | None:
    """Le combiné enregistré pour `day` (du `sport`) ou None. `d` = snapshot partagé (cf. `load()`).
    `variant` (ex. « cote2 ») -> 2ᵉ combiné du jour dans son fichier dédié."""
    d = _load(sport, variant) if d is None else d
    cb = d.get(day)
    return cb if isinstance(cb, dict) else None


def entries(d: dict | None = None, sport: str = "foot", variant: str = "") -> list:
    """Combinés suivis du `sport` (foot=ROI, tennis/basket=simulé), PLUS RÉCENT en premier :
    {date, cote, prob, result, legs}. Snapshot partagé (`d`) ou chargé pour `sport`.
    `variant` (« cote2 ») -> 2ᵉ combiné du jour dans son fichier dédié."""
    d = _load(sport, variant) if d is None else d
    out = [cb for cb in d.values() if isinstance(cb, dict) and cb.get("legs")]
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def _leg_summ(cb: dict) -> list:
    """Résumé COMPACT des jambes d'un combiné pour l'historique (demande user 2026-07-26 : voir les jambes
    qui composaient chaque combiné) : [{name, sel, cote, result}] par jambe."""
    return [{"name": l.get("name") or f'{l.get("home", "")} - {l.get("away", "")}'.strip(" -"),
             "sel": l.get("sel"), "cote": l.get("cote"), "result": l.get("result")}
            for l in (cb.get("legs") or [])]


def roi_events(d: dict | None = None, variant: str = "") -> list:
    """Événements ROI des combinés du jour RÉGLÉS (demande user 2026-07-14 : « compter les combinés
    multisport du jour dans le ROI ») -> [(date, result, cote_effective, details)] injectable dans
    `analyses.stats_full` (courbe/ROI/réussite). 1 pari/jour, mise plate 1 u. `void` = neutre -> exclu.
    Cote effective d'un gagnant = produit des jambes GAGNÉES (push/void retirées), cohérent avec
    `_combo_result_profit` (profit + 1). Frozen dès le règlement -> compteur MONOTONE.
    `variant` (user 2026-08-19) : « cote2 » -> événements du 2ᵉ combiné du jour (bilan combinés, hors ROI officiel)."""
    d = _load("foot", variant) if d is None else d
    _tier_lbl = ("Combiné du soir" if variant == "soir"
                 else "Combiné Cote 2" if variant == "cote2" else "Combiné du jour")
    out = []
    for cb in d.values():
        if not isinstance(cb, dict) or cb.get("result") not in ("won", "lost"):
            continue
        r = cb["result"]
        cote = (_combo_result_profit(cb) + 1) if r == "won" else (cb.get("cote") or 1.0)
        n = len(cb.get("legs") or [])
        # SPORT du combiné = sport unique de toutes les jambes (None si multisport) -> permet de le ventiler
        # dans le « Combinés » du sport (demande user 2026-07-25).
        _sports = {l.get("sport") for l in (cb.get("legs") or []) if l.get("sport")}
        _leg_sport = next(iter(_sports)) if len(_sports) == 1 else None
        out.append((cb.get("date") or "", r, cote,
                    {"name": f"{_tier_lbl} ({n} jambes)", "sel": "football",
                     "sport": "combiné", "combo_daily": True, "n_legs": n, "leg_sport": _leg_sport,
                     "tier": ("soir" if variant == "soir" else "cote2" if variant == "cote2" else "sur"),
                     "legs": _leg_summ(cb)}))
    return out


def sim_events(sport: str) -> list:
    """Événements des combinés du jour SIMULÉS d'un sport en arrière-plan (tennis/basket) : HORS ROI
    officiel, injectés dans `combo_stats.by_sport[sport]` seulement, pour nourrir le suivi du sport et
    « gonfler le moteur » (demande user 2026-07-25). Même format que `roi_events`, lit `combo_daily_{sport}.json`.
    Un combiné SIMULÉ n'alimente JAMAIS les compteurs overall (rows/curve/crecent)."""
    d = _load(sport)
    out = []
    for cb in d.values():
        if not isinstance(cb, dict) or cb.get("result") not in ("won", "lost"):
            continue
        r = cb["result"]
        cote = (_combo_result_profit(cb) + 1) if r == "won" else (cb.get("cote") or 1.0)
        n = len(cb.get("legs") or [])
        out.append((cb.get("date") or "", r, cote,
                    {"name": f"Combiné du jour ({n} jambes)", "sel": "Combiné du jour",
                     "sport": sport, "combo_daily": True, "n_legs": n, "leg_sport": sport,
                     "legs": _leg_summ(cb)}))
    return out


def multisport_legs(sport: str | None = None) -> list:
    """Jambes des combinés du jour MULTISPORT (jambes de sports DIFFÉRENTS), au format d'entrée provisoire
    ({sport, name, sel, cote, result, start}) -> reversées dans les provisoires de CHAQUE sport pour ne pas
    perdre leurs stats (demande user 2026-07-25). Un combiné MONO-sport n'est PAS ici (déjà ventilé dans le
    « Combinés » du sport via roi_events / leg_sport). `sport` filtre (None = tous)."""
    d = _load()
    out = []
    for cb in d.values():
        if not isinstance(cb, dict):
            continue
        legs = cb.get("legs") or []
        _sports = {l.get("sport") for l in legs if l.get("sport")}
        if len(_sports) <= 1:                          # mono-sport / vide -> PAS multisport
            continue
        for l in legs:
            lsp = l.get("sport")
            if not lsp or (sport and lsp != sport):
                continue
            out.append({"sport": lsp, "name": f'{l.get("home", "")} - {l.get("away", "")}'.strip(" -"),
                        "sel": l.get("sel") or l.get("market"), "cote": l.get("cote"),
                        "result": l.get("result"), "start": l.get("start"), "_combo_leg": True})
    return out


def equity_curve(d: dict | None = None) -> list:
    """Série du PROFIT CUMULÉ (unités, mise à plat 1 u) des combinés du jour RÉGLÉS, ordonnée par date,
    commençant à 0 — pour le graphe d'équité « info seule ». Snapshot partagé avec stats()."""
    d = _load() if d is None else d
    settled = sorted((cb for cb in d.values()
                      if isinstance(cb, dict) and cb.get("result") in ("won", "lost")),
                     key=lambda cb: cb.get("date") or "")
    cur, out = 0.0, [0.0]
    for cb in settled:
        cur += _combo_result_profit(cb)
        out.append(round(cur, 2))
    return out


def stats(d: dict | None = None) -> dict:
    """Agrégat INFO-SEULE : {n, settled, won, lost, void, pending, hit_rate, roi_pct, profit_units,
    avg_cote}. Mise à plat 1 u. ROI = profit / n_tranchés (hors void) × 100. {} si aucun combiné.
    Snapshot partagé avec `entries()` -> compteur et liste TOUJOURS cohérents."""
    d = _load() if d is None else d
    cbs = [cb for cb in d.values() if isinstance(cb, dict) and cb.get("legs")]
    if not cbs:
        return {}
    won = lost = void = pending = 0
    profit = 0.0
    cotes = []
    for cb in cbs:
        r = cb.get("result")
        if r == "won":
            won += 1
            profit += _combo_result_profit(cb)
            cotes.append(cb.get("cote"))
        elif r == "lost":
            lost += 1
            profit -= 1
            cotes.append(cb.get("cote"))
        elif r == "void":
            void += 1
        else:
            pending += 1
    graded = won + lost
    cotes = [c for c in cotes if isinstance(c, (int, float))]
    return {
        "n": len(cbs), "settled": won + lost + void, "won": won, "lost": lost, "void": void,
        "pending": pending,
        "hit_rate": round(won / graded * 100) if graded else None,
        "roi_pct": round(profit / graded * 100, 1) if graded else None,
        "profit_units": round(profit, 2),
        "avg_cote": round(sum(cotes) / len(cotes), 2) if cotes else None,
    }
