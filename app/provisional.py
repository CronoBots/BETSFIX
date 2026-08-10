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


def _is_total(sel) -> bool:
    """Pari TOTAL (Under/Over buts du match) — EXCLU des provisoires affichés/comptés (user 2026-08-07) car
    ils perdent (Under −16 %, Over −21 %). Restent dans le track brut -> gardés pour la CALIBRATION. La
    SOURCE UNIQUE (stats/entries/equity_curve) applique ce même filtre -> compteur, liste et courbe cohérents."""
    _s = (sel or "").lower()
    return "moins de" in _s or "plus de" in _s

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


def _sidecar_for(sport: str, home: str, away: str, start=None):
    """Sidecar du match apparié par NOMS (home ET away, dé-accentué) ET DÉSAMBIGUÏSÉ PAR COUP D'ENVOI (`start`)
    quand fourni : on prend le match des mêmes équipes le PLUS PROCHE en temps, et on REJETTE (None) si le plus
    proche est à > 6 h du coup d'envoi visé. ⚠️ Sans ça, 2 affiches entre les mêmes équipes à des dates
    différentes (aller/retour) se confondaient et le règlement prenait le MAUVAIS score (bug user 2026-08-10 :
    provisoire « Västerås-Djurgårdens » du 10/08 réglé « 6-0 » avec le score du match INVERSE du 03/08). Le
    match est souvent déjà réglé côté analyses -> result.raw = autorité de vérité, 0 réseau. None si introuvable."""
    import re
    from datetime import datetime
    from app import analyses
    _stop = {"fc", "sc", "if"}

    def _tk(s):
        return set(re.findall(r"[a-z0-9]+", analyses._deacc(s or "").lower())) - _stop
    th, ta = _tk(home), _tk(away)
    if not (th and ta):
        return None
    _want = None
    if start:
        try:
            _want = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            _want = None
    best, best_gap = None, None
    for d in analyses.iter_meta(sport):
        dh, da = _tk(d.get("home")), _tk(d.get("away"))
        if not ((dh & th and da & ta) or (dh & ta and da & th)):
            continue
        if _want is None:
            return d                                   # pas de coup d'envoi visé -> 1er match (comportement d'avant)
        try:
            _dt = datetime.fromisoformat(str(d.get("start")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        gap = abs((_dt - _want).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = d, gap
    if best is not None and best_gap is not None and best_gap > 6 * 3600:
        return None                                    # collision d'affiche (autre date) -> ne pas prendre ce match
    return best


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
           comp: str, sel: str, cote, prob=None) -> None:
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
              # CONFIANCE de l'analyste (demande user 2026-07-28) : figée pour que la carte RÉSULTAT montre la
              # ligne verdict (Confiance/Marché/Value) COMME les pronos, sans dépendre d'un sidecar qui vieillit.
              "prob": prob if prob is not None else (prev or {}).get("prob"),
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
        # On ne retire QUE les doublons d'un VRAI pari joué (combiné same-match ou simple retenu). Une jambe
        # du COMBINÉ DU JOUR peut RESTER suivie en provisoire aussi (demande user 2026-07-25 : « ce n'est pas
        # grave si il passe en provisoire aussi ») — l'affichage la dédoublonne déjà (_programme_items).
        if analyses.has_combo(sport, mid) or analyses.retained_bet(sport, mid) is not None:
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
                                          prov.get("prob"), home, away, fid=prov.get("fid"))
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
        # « void » est TERMINAL ici (fix audit 2026-07-23) : sans lui dans la liste, une entrée voidée
        # repassait dans la boucle À CHAQUE cycle reconcile (10 min) — 3 appels réseau, ré-écriture du
        # result/score (écrasant un libellé informatif posé à la main), n+=1 fantôme et _save() à vie.
        if not isinstance(p, dict) or p.get("result") in ("won", "lost", "push", "void"):
            continue
        sport = p.get("sport")
        # GARDE-FOU « match TERMINÉ » (bug 2026-07-17 : Botafogo-Santos & Tijuana-Tigres marqués « gagné »
        # AVANT le coup d'envoi — la recherche par NOMS de flashscore.final_score matchait un match ANTÉRIEUR
        # entre les mêmes équipes et renvoyait SON score). On aligne le chemin provisoire sur le chemin
        # PRINCIPAL (settle_analyses) : NE JAMAIS tenter de régler tant que le match n'est pas PROBABLEMENT
        # fini (assez de temps écoulé depuis le coup d'envoi). Empêche tout règlement sur un match à venir.
        if not analyses.likely_finished({"start": p.get("start"), "sport": sport}):
            continue
        # GARDE « MATCH REPORTÉ / pas vraiment fini » (bug user 2026-07-28 : matchs WTA Vancouver reportés du
        # 26 au 29/07 -> date STOCKÉE périmée -> likely_finished(stale)=True -> réglé/voidé à tort alors que le
        # match n'a PAS été joué). On vérifie l'état RÉEL Unibet : s'il montre le match ENCORE À VENIR (reporté)
        # ou EN COURS, on NE règle/void PAS (aligné combo_daily + settle-never-on-live-score). Le tennis surtout
        # se décale -> `fresh_status` renvoie l'heure FRAÎCHE Unibet.
        try:
            from app import match_select as _ms
            _hl = bool(_ms.live_state_for(sport, p.get("home"), p.get("away")))
            _fst, _fdt = _ms.fresh_status(sport, p.get("home"), p.get("away"), "finished", _hl,
                                          start_iso=p.get("start"))
            if _hl or _fst in ("inprogress", "notstarted"):
                # heure fraîche postérieure -> on RAFRAÎCHIT la date stockée (le prochain cycle la réglera au bon moment)
                if _fdt is not None:
                    try:
                        p["start"] = _fdt.isoformat().replace("+00:00", "Z")
                        _save(d)
                    except Exception:
                        pass
                continue
        except Exception:
            pass
        q = {"home": p.get("home", ""), "away": p.get("away", ""), "start": p.get("start"),
             "sofa_id": ""}
        score = None
        # PRIORITÉ au score DÉJÀ RÉGLÉ du sidecar du match (result.raw) : autorité de vérité, 0 réseau. Le
        # match est souvent déjà réglé côté analyses (périodes captées) alors que le lookup PAR NOM de
        # Flashscore/LiveScore ÉCHOUE (nom tennis/étranger introuvable) -> sans ça le provisoire restait « EN
        # ATTENTE » à vie (bug user 2026-07-28, Cocciaretto-Tauson). Même correctif que combo_daily (2026-07-14).
        try:
            _sd = _sidecar_for(sport, p.get("home"), p.get("away"), p.get("start"))   # désambiguïsé par coup d'envoi
            _raw = ((_sd or {}).get("result") or {}).get("raw")
            if isinstance(_raw, dict) and (_raw.get("periods") or _raw.get("home") is not None
                                           or _raw.get("sets_home") is not None):
                score = _raw
        except Exception:
            score = None
        if score is None:
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
        # CODE RE-DÉRIVÉ du libellé (jamais le code STOCKÉ, périmé possible — cf. combo-single-source-of-truth) :
        # une forme « ...jeux Plus de 8.5 - Set 1 » figée en « OVER 8.5 » ne se réglait pas (bug 2026-07-28).
        try:
            from app.settle_analyst import code_from_pick as _cfp_pv
            _pc = (_cfp_pv(p.get("sel", ""), sport, p.get("home", ""), p.get("away", "")) or p.get("code", ""))
        except Exception:
            _pc = p.get("code", "")
        try:
            res = settle_pick(_pc, score)
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
    (bug vécu : compteur « 7 » vs liste de 11). Cf. `app/web.py:_prov_sport_graph` (affichage provisoires)."""
    return _load()


def entries(d: dict | None = None) -> list:
    """Liste des provisoires suivis, PLUS RÉCENT (coup d'envoi) en premier : {name, sel, cote, result,
    start, sport}. `result` = None => EN ATTENTE (match pas encore réglé). Sert à AFFICHER le détail (au
    clic sur le bloc) : sinon un provisoire « en attente » n'est visible nulle part une fois le match
    commencé (il a quitté « À venir »). Demande user 2026-07-10. `d` = snapshot partagé (cf. `load()`)."""
    d = _load() if d is None else d
    out = [{"name": p.get("name"), "sel": p.get("sel"), "cote": p.get("cote"),
            "result": p.get("result"), "start": p.get("start"), "sport": p.get("sport")}
           for p in d.values() if isinstance(p, dict) and not _is_total(p.get("sel"))]  # résultat seul (2026-08-07)
    out.sort(key=lambda x: x.get("start") or "", reverse=True)
    return out


def equity_curve(d: dict | None = None) -> list:
    """Série du PROFIT CUMULÉ (unités, mise à plat 1 u) des provisoires RÉGLÉS, ordonnée par coup
    d'envoi, commençant à 0 — pour le graphe d'équité « info seule ». Snapshot partagé avec stats()."""
    d = _load() if d is None else d
    settled = sorted((p for p in d.values()
                      if isinstance(p, dict) and p.get("result") in ("won", "lost")
                      and not _is_total(p.get("sel"))),      # résultat seul (2026-08-07) -> courbe = stats
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
        if _is_total(p.get("sel")):     # RÉSULTAT SEUL (2026-08-07) : totaux écartés (gardés en calibration)
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
