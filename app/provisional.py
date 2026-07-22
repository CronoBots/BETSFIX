"""Suivi SÉPARÉ (info seule) des PARIS PROVISOIRES — demande user 2026-07-09.

Un « provisoire » = le pari le plus probable affiché sur une ABSTENTION (aucun pari de value retenu).
On ne le joue PAS (value négative/marginale par construction), mais on veut MESURER, chiffres à l'appui,
ce que « jouer chaque provisoire » donnerait — pour VALIDER la discipline d'abstention par les données.

⚠️ TOTALEMENT ISOLÉ du ROI/stats réels : ce module écrit UNIQUEMENT dans `data/provisional_track.json`,
ne touche JAMAIS aux sidecars, à `stat_bet`, à la calibration ni à `list_for`. Mise à plat de 1 unité par
provisoire ; ROI = Σ(cote−1 si gagné, −1 si perdu) / n_réglés.
"""
from __future__ import annotations

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK_PATH = os.path.join(_ROOT, "data", "provisional_track.json")

# VOID « ultime recours » : aligné sur le chemin principal (settle_analyst._VOID_AFTER_DAYS = 3 j). Un
# provisoire dont le match est fini DEPUIS ce délai mais dont AUCUNE source ne rend de score (match reporté/
# annulé, ex. Supercopa argentine suspendue 2026-07-21 ; ou donnée réellement morte) est clos en `void` —
# sinon il resterait « en attente » À VIE (les provisoires n'avaient pas le void J+3 des paris/fantômes).
_VOID_AFTER_DAYS = 3.0


def _match_age_days(start_iso) -> float:
    """Jours écoulés depuis le coup d'envoi prévu (0 si date illisible → jamais de void prématuré)."""
    from datetime import datetime, timezone
    try:
        st = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - st).total_seconds() / 86400.0
    except (ValueError, AttributeError, TypeError):
        return 0.0


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


def record(sport: str, match_id, home: str, away: str, start: str, name: str,
           comp: str, sel: str, cote) -> None:
    """Enregistre (ou met à jour tant que non réglé) un pari provisoire. Ne garde QUE les paris dont le
    code de règlement est CALCULABLE (sinon impossible à régler -> inutile à suivre). No-op si déjà réglé
    (on ne réécrit pas un résultat figé). Appelé par le scan quand un provisoire est posé."""
    from app.settle_analyst import code_from_pick
    code = code_from_pick(sel or "", sport, home or "", away or "")
    if not code:                                  # non réglable -> on ne le suit pas
        return
    mid = str(match_id)
    d = _load()
    prev = d.get(mid)
    if isinstance(prev, dict) and prev.get("result") in ("won", "lost", "push"):
        return                                    # déjà réglé -> figé (jamais réécrit)
    # DÉDUP (demande user 2026-07-11 / élargie 2026-07-12) : si le match a DÉJÀ un pari RETENU (combiné ou
    # simple) OU s'il est une JAMBE DU COMBINÉ DU JOUR, il ne doit PAS être suivi EN DOUBLE comme provisoire
    # — sinon une seule erreur se répercute aux deux endroits, avec deux résultats possibles pour un seul
    # match. On n'enregistre pas (et on retire une entrée NON réglée).
    from app import analyses, combo_daily
    if (analyses.has_combo(sport, mid) or analyses.retained_bet(sport, mid) is not None
            or combo_daily.is_daily_leg(mid, home, away)):   # jambe de combiné : par id OU par NOM
        if isinstance(prev, dict) and prev.get("result") is None:
            d.pop(mid, None)
            _save(d)
        return
    d[mid] = {"sport": sport, "id": mid, "home": home, "away": away, "start": start,
              "name": name, "comp": comp, "sel": sel, "cote": cote, "code": code,
              "result": (prev or {}).get("result")}
    _save(d)


def prune_retained() -> int:
    """Retire du suivi les provisoires NON ENCORE RÉGLÉS dont le match a désormais un PARI RETENU (combiné
    ou simple). Un match ne doit être suivi que par UN SEUL type de pari (dédup, demande user 2026-07-11) :
    sinon la même erreur se répercute à deux endroits, avec deux résultats contradictoires possibles pour un
    seul match. Ne touche JAMAIS un provisoire déjà réglé (compteur monotone préservé). Renvoie le nb retiré."""
    from app import analyses, combo_daily
    d = _load()
    removed = 0
    for mid in list(d.keys()):
        p = d.get(mid)
        if not isinstance(p, dict) or p.get("result") in ("won", "lost", "push"):
            continue                              # réglé = figé, jamais retiré (monotone)
        sport = p.get("sport")
        if (analyses.has_combo(sport, mid) or analyses.retained_bet(sport, mid) is not None
                or combo_daily.is_daily_leg(mid, p.get("home"), p.get("away"))):  # id OU nom
            d.pop(mid, None)
            removed += 1
    if removed:
        _save(d)
    return removed


def drop_unsettled(match_id) -> bool:
    """Retire du suivi le provisoire NON réglé d'un match. Sert quand la ré-analyse EFFACE le pari indicatif
    (l'affichage n'a plus rien -> le suivi non plus : cohérence Stats ↔ À venir, demande user 2026-07-13).
    Ne touche JAMAIS un réglé (compteur monotone). Renvoie True si retiré."""
    mid = str(match_id)
    d = _load()
    p = d.get(mid)
    if isinstance(p, dict) and p.get("result") is None:
        d.pop(mid, None)
        _save(d)
        return True
    return False


def reconcile_with_programme() -> int:
    """COHÉRENCE Stats ↔ À venir BIDIRECTIONNELLE (demande user 2026-07-13) : le suivi = EXACTEMENT les
    provisoires AFFICHÉS dans day_programme.
      • RETIRE les provisoires non réglés dont le match est dans le programme SANS pari affiché (ré-analyse
        qui a effacé le pari, jambe de combiné…).
      • AJOUTE au suivi les provisoires AFFICHÉS mais pas encore suivis (ex. Djurgården visible en À venir
        mais absent des stats) -> plus de « affiché mais pas suivi ».
    Ne touche jamais un réglé (monotone) ni les matchs hors programme (settle_pending les règle). `record`
    porte la dédup (combiné/retenu/non réglable). Renvoie le nb de changements."""
    import json
    path = os.path.join(_ROOT, "data", "day_programme.json")
    try:
        with open(path, encoding="utf-8") as f:
            prog = json.load(f)
    except (OSError, ValueError):
        return 0
    from app import analyses
    matches = prog.get("matches") or []

    def _shown(m) -> bool:                         # provisoire réellement AFFICHÉ (même filtre que web) ?
        prov = m.get("provisional") or {}
        if not prov.get("sel"):
            return False
        home, _, away = str(m.get("name", "")).partition(" - ")
        # FILTRE (demande user 2026-07-17) : sans value ET < 60 % confiance calibrée -> non affiché -> non suivi.
        return analyses.provisional_shown(m.get("sport"), prov.get("sel"), prov.get("cote"),
                                          prov.get("prob"), home, away)
    # ids DANS le programme SANS provisoire AFFICHÉ (pas de pari publié) -> l'affichage ne montre RIEN pour eux
    no_prov = {str(m.get("id") or "") for m in matches
               if not _shown(m) and m.get("status") != "bet"}
    d = _load()
    changed = 0
    for mid in list(d.keys()):                     # RETRAIT des non réglés que l'affichage ne montre plus
        p = d.get(mid)
        if isinstance(p, dict) and p.get("result") is None and mid in no_prov:
            d.pop(mid, None)
            changed += 1
    if changed:
        _save(d)
    tracked = set(_load().keys())
    for m in matches:                              # AJOUT des provisoires affichés mais pas encore suivis
        prov = m.get("provisional") or {}
        mid = str(m.get("id") or "")
        if not _shown(m) or m.get("status") == "bet" or mid in tracked:   # filtré/non affiché -> pas suivi
            continue
        home, _, away = str(m.get("name", "")).partition(" - ")
        record(m.get("sport"), mid, home, away, m.get("start", ""), m.get("name", ""),
               m.get("comp", ""), prov.get("sel"), prov.get("cote"))   # dédup + non-réglable gérés dans record
        if mid in _load():                         # record a bien ajouté (ni combiné/retenu ni non réglable)
            changed += 1
    return changed


def settle_pending() -> int:
    """Règle les provisoires en attente dont le match est terminé, via Flashscore (couverture universelle,
    repli LiveScore) + `settle_pick`. Score PARTIEL -> on n'écrit RIEN (jamais de règlement sur du live).
    Renvoie le nombre nouvellement réglé. Sûr à rejouer (idempotent : ne retouche pas un déjà réglé)."""
    from app import analyses, flashscore, livescore
    from app.settle_analyst import settle_pick
    prune_retained()          # DÉDUP d'abord : un match devenu retenu (combiné/simple) sort du suivi provisoire
    reconcile_with_programme()  # COHÉRENCE : un match sans provisoire affiché sort aussi du suivi (Stats = À venir)
    d = _load()
    n = 0
    for mid, p in list(d.items()):
        if not isinstance(p, dict) or p.get("result") in ("won", "lost", "push"):
            continue
        sport = p.get("sport")
        # GARDE-FOU « match TERMINÉ » (bug 2026-07-17 : Botafogo-Santos & Tijuana-Tigres marqués « gagné »
        # AVANT le coup d'envoi — la recherche par NOMS de flashscore.final_score matchait un match ANTÉRIEUR
        # entre les mêmes équipes et renvoyait SON score). On aligne le chemin provisoire sur le chemin
        # PRINCIPAL (settle_analyses) : NE JAMAIS tenter de régler tant que le match n'est pas PROBABLEMENT
        # fini (assez de temps écoulé depuis le coup d'envoi). Empêche tout règlement sur un match à venir.
        if not analyses.likely_finished({"start": p.get("start"), "sport": sport}):
            continue
        q = {"home": p.get("home", ""), "away": p.get("away", ""), "start": p.get("start"),
             "sofa_id": ""}
        score = None
        try:
            score = flashscore.final_score(sport, q) or livescore.final_score(sport, q)
        except Exception:
            score = None
        # Repli SPORTRADAR (GISMO) : score DÉTAILLÉ par set/quart-temps/mi-temps (jeux tennis, points
        # basket) que Flashscore/LiveScore ne donnent souvent pas -> rend réglables TOTGAMES/SETGAMES/
        # tie-breaks/mi-temps (bug 2026-07-12 : provisoire tennis « Total de jeux » resté en attente car
        # settle_pending n'interrogeait QUE Flashscore/LiveScore, sans les périodes Sportradar). Aligne le
        # chemin provisoire sur la chaîne de règlement principale (qui a déjà ce repli).
        if not score or not score.get("periods"):
            try:
                import asyncio
                import httpx
                from app import sportradar

                async def _sr_score():
                    async with httpx.AsyncClient(timeout=20) as _c:
                        return await sportradar.final_score(_c, sport, q)
                srs = asyncio.run(_sr_score())
                if srs and (srs.get("periods") or srs.get("label")):
                    if not score:
                        score = srs
                    else:                          # complète les périodes manquantes, garde le reste
                        score = {**score, "periods": srs.get("periods") or score.get("periods"),
                                 "sets_home": score.get("sets_home") if score.get("sets_home") is not None
                                 else srs.get("sets_home"),
                                 "sets_away": score.get("sets_away") if score.get("sets_away") is not None
                                 else srs.get("sets_away")}
            except Exception:
                pass
        if not score:
            # VOID « ultime recours » (aligné settle_analyses / void_exhausted_shadows) : match fini DEPUIS
            # LONGTEMPS mais AUCUN score nulle part = reporté/annulé/donnée morte -> on clôt en `void` (neutre,
            # remboursé, HORS ROI comme un push) pour GARANTIR qu'un provisoire d'un match terminé finit réglé
            # et ne reste jamais « en attente » à vie. Sinon (match récent) : on retente au prochain cycle.
            if _match_age_days(p.get("start")) >= _VOID_AFTER_DAYS:
                p["result"] = "void"
                p["score"] = "reporté / sans score"
                n += 1
            continue
        try:
            res = settle_pick(p.get("code", ""), score)
        except Exception:
            res = None
        if res in ("won", "lost", "push"):
            p["result"] = res
            p["score"] = score.get("label") or ""
            n += 1
    if n:
        _save(d)
    return n


def load() -> dict:
    """Snapshot du suivi provisoire (dict brut). Sert à dériver `stats()` ET `entries()` du MÊME état pour
    garantir que le compteur (n/réglés/en attente) et la liste affichée soient TOUJOURS cohérents — sinon
    deux `_load()` séparés peuvent tomber de part et d'autre d'une écriture (scan/règlement) et diverger
    (bug vécu : compteur « 7 » vs liste de 11). Cf. `app/routers/web.py:_provisional_card`."""
    return _load()


def entries(d: dict | None = None) -> list:
    """Liste des provisoires suivis, PLUS RÉCENT (coup d'envoi) en premier : {name, sel, cote, result,
    start, sport}. `result` = None => EN ATTENTE (match pas encore réglé). Sert à AFFICHER le détail (au
    clic sur le bloc) : sinon un provisoire « en attente » n'est visible nulle part une fois le match
    commencé (il a quitté « À venir »). Demande user 2026-07-10. `d` = snapshot partagé (cf. `load()`)."""
    d = _load() if d is None else d
    out = [{"name": p.get("name"), "sel": p.get("sel"), "cote": p.get("cote"),
            "result": p.get("result"), "start": p.get("start"), "sport": p.get("sport")}
           for p in d.values() if isinstance(p, dict)]
    out.sort(key=lambda x: x.get("start") or "", reverse=True)
    return out


def equity_curve(d: dict | None = None) -> list:
    """Série du PROFIT CUMULÉ (unités, mise à plat 1 u) des provisoires RÉGLÉS, ordonnée par coup
    d'envoi, commençant à 0 — pour le graphe d'équité « info seule ». Snapshot partagé avec stats()."""
    d = _load() if d is None else d
    settled = sorted((p for p in d.values()
                      if isinstance(p, dict) and p.get("result") in ("won", "lost")),
                     key=lambda p: p.get("start") or "")
    cur, out = 0.0, [0.0]
    for p in settled:
        c = p.get("cote")
        cur += (c - 1) if (p.get("result") == "won" and isinstance(c, (int, float))) else -1.0
        out.append(round(cur, 2))
    return out


def stats(d: dict | None = None) -> dict:
    """Agrégat INFO-SEULE : {n, settled, won, lost, pending, hit_rate, roi_pct, profit_units, avg_cote}.
    Mise à plat 1 unité. ROI = profit / n_réglés × 100. {} si aucun provisoire suivi. `d` = snapshot
    partagé avec `entries()` (cf. `load()`) → compteur et liste TOUJOURS cohérents."""
    d = _load() if d is None else d
    if not d:
        return {}
    won = lost = push = pending = 0
    profit = 0.0
    cotes = []
    for p in d.values():
        if not isinstance(p, dict):
            continue
        r = p.get("result")
        c = p.get("cote")
        if r == "won":
            won += 1
            if isinstance(c, (int, float)):
                profit += c - 1
                cotes.append(c)
        elif r == "lost":
            lost += 1
            profit -= 1
            if isinstance(c, (int, float)):
                cotes.append(c)
        elif r in ("push", "void"):            # void = remboursé/annulé (match reporté, donnée morte) = neutre, réglé, hors ROI
            push += 1
        else:
            pending += 1
    settled = won + lost + push
    graded = won + lost                            # réglés à cote (hors push) = base du ROI
    return {
        "n": len([p for p in d.values() if isinstance(p, dict)]),
        "settled": settled, "won": won, "lost": lost, "push": push, "pending": pending,
        "hit_rate": round(won / graded * 100) if graded else None,
        "roi_pct": round(profit / graded * 100, 1) if graded else None,
        "profit_units": round(profit, 2),
        "avg_cote": round(sum(cotes) / len(cotes), 2) if cotes else None,
    }
