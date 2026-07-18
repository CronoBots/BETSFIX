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

MIN_ODDS = 1.95           # cote minimale du combiné (demande user 2026-07-17 : « au moins 1,95 »)
MAX_LEGS = 5             # borne haute (au-delà, taux de réussite trop faible)
MIN_LEGS = 2             # un « combiné » = au moins 2 jambes
MIN_LEG_PROB = 0.65      # « les plus probables » : jambe fiable seulement (relevé pour la sécurité)
MIN_LEG_ODDS = 1.06      # une jambe quasi-sûre à cote ~1.01 n'apporte rien vers le seuil
# NOTE : le garde-fou EV (MIN_COMBO_EV, 2026-07-14 : s'abstenir sans value) a été RETIRÉ le 2026-07-17
# sur demande user explicite : « 1 combiné multisport par jour, le plus fiable, ≥ 1,95, TOUJOURS compté
# au ROI » — même les jours sans edge. On publie donc le combiné le PLUS PROBABLE atteignant 1,95, chaque
# jour (None seulement si le vivier ne permet PAS d'atteindre 1,95). cf. mémoire combo-daily-multisport.

# Marchés en PALIERS DE FIABILITÉ (taux de réussite MESURÉS, cf. COMBO_MISSION). On compare le PREMIER
# jeton du code (ex. "SETWIN 1 HOME" -> "SETWIN"). Le combiné se construit d'abord AVEC LE PALIER 1 SEUL
# (les jambes les plus SAFE) et ne descend d'un palier QUE s'il ne peut pas atteindre la cote min sinon.
_TIER1 = {"WIN", "DC", "REGTIME"}                       # résultat / double chance (~83 %) = le plus fiable
_TIER2 = {"SHOTSOT", "TEAMTOT", "SET", "SETWIN"}        # tirs cadrés (83 %), équipe marque / au moins un set (~79 %)
_TIER3 = {"OVER", "UNDER", "TOTGAMES", "TEAMGAMES", "SETSCORE"}   # totaux (points/buts/jeux), score de sets (+ variance)
_ALLOWED = _TIER1 | _TIER2 | _TIER3


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


def _load() -> dict:
    try:
        with open(TRACK_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    tmp = TRACK_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(TRACK_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, TRACK_PATH)
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
    """(home, away) de chaque match JAMBE d'un combiné du jour. Complète `leg_ids` pour la DÉDUP : l'ID
    d'un même match DIFFÈRE souvent entre le combiné (mid sidecar/ESPN) et le programme (id Unibet) ->
    la dédup PAR ID seule LAISSE PASSER la jambe en provisoire (bug vécu Atlanta Dream : mid 15415813 ≠
    id Unibet 1026378509). Le dédoublonnage PAR NOM (cf. web._prog_pair) est robuste à cet écart d'id."""
    d = _load()
    days = [d.get(day)] if day is not None else list(d.values())
    out: list = []
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
               min_leg_odds: float = MIN_LEG_ODDS) -> dict | None:
    """Choisit les jambes MAXIMISANT le produit des probabilités sous contrainte produit des cotes
    ≥ min_odds, ≤ 1 jambe/match (`mid`), 2..max_legs jambes. Glouton par efficacité
    log(cote)/(−log(prob)) [pousse vers le seuil de cote en perdant le moins de proba] + raffinement
    (retrait des jambes superflues + swaps). None si irréalisable. cands : [{mid, sport, sel, cote,
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

    chosen.sort(key=lambda x: -x["prob"])
    return {"legs": chosen, "cote": round(odds(chosen), 2), "prob": prob(chosen)}


def _candidates_for_day(day: str) -> list[dict]:
    """Extrait les jambes candidates (marchés autorisés, réglables, prob ≥ seuil) de TOUS les matchs
    du jour `day` (YYYY-MM-DD) encore À VENIR. Source = fantômes `shadow` + pari retenu `bets`
    (dédup par (match, code), meilleure proba). `prob` renvoyé en fraction 0-1."""
    from app import analyses
    from app.settle_analyst import code_from_pick
    out: list[dict] = []
    for side in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(side, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (d.get("start") or "")[:10] != day:
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
            # RE-DÉRIVER le code depuis le LIBELLÉ (le code stocké peut être périmé/générique : un
            # fantôme « Tiebreaks +0.5 » a l'ancien code `OVER 0.5` qui réglerait un total de BUTS =
            # FAUX). code_from_pick reflète la logique de règlement ACTUELLE -> code correct + à jour.
            code = code_from_pick(p.get("sel") or "", d.get("sport"), d.get("home", ""),
                                  d.get("away", "")).strip()
            if not code or code.split()[0] not in _ALLOWED:
                continue
            pr, co = p.get("prob"), p.get("cote")
            if not isinstance(pr, (int, float)) or not isinstance(co, (int, float)):
                continue
            prf = pr / 100.0 if pr > 1 else float(pr)   # sidecars stockent la proba en %
            prev = best.get(code)
            if prev is None or prf > prev["prob"]:
                best[code] = {"mid": mid, "sport": d.get("sport"), "sel": p.get("sel"),
                              "cote": float(co), "prob": prf, "code": code, "name": d.get("name"),
                              "home": d.get("home"), "away": d.get("away"), "start": d.get("start"),
                              "comp": d.get("comp")}
        out.extend(best.values())
    return out


def build_for_day(day: str) -> dict | None:
    """Construit LE combiné du jour depuis les analyses, en privilégiant les jambes LES PLUS SAFE : on
    tente d'abord avec le PALIER 1 SEUL (résultat/double chance), puis 1+2 (tirs cadrés/équipe/sets),
    puis 1+2+3 (totaux) — on ne descend d'un palier que si la cote ≥ 1.9 est INATTEIGNABLE au précédent.
    None si aucun combiné fiable possible."""
    cands = _candidates_for_day(day)
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
    return {"date": day, "cote": combo["cote"], "prob": round(combo["prob"], 4),
            "legs": legs, "result": None, "sent": False, "created": None}


def telegram_text(cb: dict) -> str:
    """Message HTML (parse_mode=HTML) du combiné du jour pour Telegram. Noms échappés."""
    import html as _h
    emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
    out = ["🎯 <b>COMBINÉ DU JOUR</b> — multisport",
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


def record_daily(combo: dict, day: str) -> bool:
    """Enregistre le combiné du jour (UN par date). Ne réécrit PAS s'il est déjà ENVOYÉ (figé = ce qui a
    été posté aux abonnés) ou déjà réglé. Renvoie True si (ré)écrit."""
    if not combo or not combo.get("legs"):
        return False
    d = _load()
    prev = d.get(day)
    if isinstance(prev, dict) and (prev.get("sent") or prev.get("result") in ("won", "lost", "void")):
        return False                                  # figé (posté/réglé) -> jamais réécrit
    d[day] = combo
    _save(d)
    return True


def mark_sent(day: str) -> None:
    """Marque le combiné du jour comme ENVOYÉ (Telegram) -> figé (published = frozen)."""
    d = _load()
    if isinstance(d.get(day), dict):
        d[day]["sent"] = True
        _save(d)


def _derive_combo(legs: list) -> str | None:
    """Résultat d'un combiné depuis ses jambes : **lost** si ≥1 perdue ; **won** si ≥1 gagnée (push/void
    NEUTRES, retirées) ; **void** si QUE des push/void. **None** si ≥1 jambe encore en attente. Une jambe
    ANNULÉE (void) ne BLOQUE donc PAS le combiné : il gagne ou perd selon les AUTRES jambes (demande user
    2026-07-18)."""
    res = [l.get("result") for l in (legs or [])]
    if any(r not in ("won", "lost", "push", "void") for r in res):
        return None
    if "lost" in res:
        return "lost"
    if "won" in res:
        return "won"
    return "void"


def settle_pending() -> int:
    """Règle les jambes des combinés dont les matchs sont terminés (Flashscore + repli LiveScore +
    `settle_pick`), puis tranche le combiné : lost si ≥1 jambe perdue ; won si ≥1 gagnée (push/void
    retirés) ; void si toutes push/void. Une jambe ANNULÉE ne bloque pas le combiné. Idempotent (corrige
    même un résultat figé à tort). Renvoie le nombre de combinés nouvellement tranchés."""
    from app import flashscore, livescore, analyses as _an
    from app.settle_analyst import settle_pick
    d = _load()
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
        if _frozen and not _pending:
            continue
        try:
            _age = (_today - _dt.date.fromisoformat(day)).days
        except (ValueError, TypeError):
            _age = 0
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
            try:
                res = settle_pick(leg.get("code", ""), score)
            except Exception:
                res = None
            # SCORE TROUVÉ : si settle_pick tranche -> résultat ; sinon le code est IRRÉCUPÉRABLE
            # (non réglable sur ce match fini) -> VOID, on ne bloque pas le combiné dessus.
            leg["result"] = res if res in ("won", "lost", "push") else "void"
            leg["score"] = score.get("label") or ""
            changed = True
        legs = cb.get("legs") or []
        if not _frozen:
            cb["tries"] = (cb.get("tries") or 0) + 1
            changed = True                            # tries accumulés -> la borne void finit par mordre
        # BORNE : à tries≥8, on void SEULEMENT les jambes dont le MATCH est FINI (donnée morte) ; une jambe
        # dont le match n'a pas encore fini (coup d'envoi tardif) RESTE en attente -> plus de void prématuré
        # (bug 07-17 : Mirassol 23:00 voidé AVANT la fin -> combiné faussement « remboursé » alors que gagné).
        if (cb.get("tries") or 0) >= 8:
            for l in legs:
                if (l.get("result") not in ("won", "lost", "push", "void")
                        and _an.likely_finished({"start": l.get("start"), "sport": l.get("sport")})):
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
        _save(d)
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


def today(day: str, d: dict | None = None) -> dict | None:
    """Le combiné enregistré pour `day` (ou None). `d` = snapshot partagé (cf. `load()`)."""
    d = _load() if d is None else d
    cb = d.get(day)
    return cb if isinstance(cb, dict) else None


def entries(d: dict | None = None) -> list:
    """Combinés suivis, PLUS RÉCENT en premier : {date, cote, prob, result, legs}. Snapshot partagé."""
    d = _load() if d is None else d
    out = [cb for cb in d.values() if isinstance(cb, dict) and cb.get("legs")]
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def roi_events(d: dict | None = None) -> list:
    """Événements ROI des combinés du jour RÉGLÉS (demande user 2026-07-14 : « compter les combinés
    multisport du jour dans le ROI ») -> [(date, result, cote_effective, details)] injectable dans
    `analyses.stats_full` (courbe/ROI/réussite). 1 pari/jour, mise plate 1 u. `void` = neutre -> exclu.
    Cote effective d'un gagnant = produit des jambes GAGNÉES (push/void retirées), cohérent avec
    `_combo_result_profit` (profit + 1). Frozen dès le règlement -> compteur MONOTONE."""
    d = _load() if d is None else d
    out = []
    for cb in d.values():
        if not isinstance(cb, dict) or cb.get("result") not in ("won", "lost"):
            continue
        r = cb["result"]
        cote = (_combo_result_profit(cb) + 1) if r == "won" else (cb.get("cote") or 1.0)
        n = len(cb.get("legs") or [])
        out.append((cb.get("date") or "", r, cote,
                    {"name": f"Combiné du jour ({n} jambes)", "sel": "multisport",
                     "sport": "combiné", "combo_daily": True, "n_legs": n}))
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
