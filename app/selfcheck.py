"""AUTO-AUDIT (self-check) — garde-fou d'intégrité, 100 % LECTURE SEULE.

Chaque contrôle ici encode une RÈGLE D'OR ou une RÉGRESSION DÉJÀ SURVENUE (cf. HISTORIQUE.md + mémoire) :
le but est de RENDRE IMPOSSIBLE que ces confusions de stats/infos repassent silencieusement. On ne corrige
rien ici (aucun effet de bord sur la sélection/le règlement) : on DÉTECTE et on ALERTE. C'est le socle qui
rend sûre toute auto-optimisation future — on ne peut pas recalibrer tout seul sans un juge d'intégrité.

Contrôles (chacun renvoie {key, level, title, detail, items}) :
  1. sidecar_integrity   — JSON illisible / clés vitales manquantes.
  2. combo_unsettled_legs— combiné avec un RÉSULTAT publié mais une jambe NON réglée (règle « valider
                            chaque jambe avant de publier », bug v39).
  3. combo_coherence     — combiné 'gagné' avec une jambe 'perdue' (ou 'perdu' alors que TOUTES gagnées) :
                            incohérence logique du règlement combiné.
  4. combo_pricing       — cote affichée du combiné incohérente avec le catalogue (bug cotes FANTÔMES
                            carte≠Unibet, ex. 2.07 vs 1.17).
  5. odds_prob_sanity    — cote < 1.01 / proba hors [1,99] : donnée corrompue.
  6. settle_not_on_live  — un pari réglé sur un match dont le coup d'envoi est encore DANS LE FUTUR
                            (proxy du bug « réglé sur score live », Angleterre–Congo).
  7. stat_monotonic      — le compteur de paris comptés (stat_bet figé) a BAISSÉ vs le dernier relevé
                            (bug « le nombre qui valse 47↔59 »). Filigrane persistant.
  8. calibration_full    — la calibration compte-t-elle bien AU MOINS tous les paris joués (jamais filtrée).
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone

from app import analyses

_STATE = os.path.join(analyses._ROOT, "data", "selfcheck_state.json")   # filigrane monotonicité
_LOG = os.path.join(analyses._ROOT, "data", "selfcheck_log.jsonl")      # journal machine (1 ligne/run)

_LVL_RANK = {"ok": 0, "info": 1, "warn": 2, "error": 3}


def _load_rows() -> tuple[list, int]:
    """(liste de (path, dict) lisibles, nb de fichiers ILLISIBLES). Ne lève jamais."""
    rows, broken = [], 0
    for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            rows.append((p, json.load(open(p, encoding="utf-8"))))
        except Exception:
            broken += 1
    return rows, broken


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _check_integrity(rows, broken) -> dict:
    bad = []
    for p, d in rows:
        miss = [k for k in ("sport", "id") if not d.get(k)]
        if miss:
            bad.append(f"{os.path.basename(p)} (manque {','.join(miss)})")
    lvl = "error" if (broken or bad) else "ok"
    return {"key": "sidecar_integrity", "level": lvl,
            "title": "Intégrité des sidecars",
            "detail": f"{broken} illisible(s), {len(bad)} avec clé vitale manquante sur {len(rows)} fiches.",
            "items": ([f"{broken} fichier(s) JSON illisible(s)"] if broken else []) + bad[:20]}


def _check_combo_unsettled_legs(rows) -> dict:
    """Un combiné ne publie un résultat que quand il est DÉCIDÉ. Nuance importante : un combiné 'perdu'
    dès qu'UNE jambe est perdue l'est DÉFINITIVEMENT — les autres jambes n'ont plus d'importance (décidé
    tôt = légitime). On ne signale donc QUE les vraies incohérences : un 'gagné' avec une jambe non
    réglée (impossible), ou un 'perdu' PRÉMATURÉ (aucune jambe perdue mais des jambes encore en attente)."""
    bad = []
    for p, d in rows:
        c = d.get("combo") or {}
        legs = c.get("legs") or []
        res = c.get("result")
        if not legs or res not in ("won", "lost"):
            continue
        pend = [i + 1 for i, lg in enumerate(legs) if lg.get("result") not in ("won", "lost", "push", "void")]
        if not pend:
            continue
        tag = f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')}"
        if res == "won":
            bad.append(f"{tag} : combiné 'gagné' IMPOSSIBLE avec jambe(s) {pend} non réglée(s)")
        elif res == "lost" and not any(lg.get("result") == "lost" for lg in legs):
            bad.append(f"{tag} : combiné 'perdu' PRÉMATURÉ — aucune jambe perdue mais {pend} en attente")
    return {"key": "combo_unsettled_legs", "level": "error" if bad else "ok",
            "title": "Combiné publié avec jambe non réglée",
            "detail": f"{len(bad)} combiné(s) au résultat incohérent avec l'état de ses jambes.",
            "items": bad[:20]}


def _check_combo_coherence(rows) -> dict:
    bad = []
    for p, d in rows:
        c = d.get("combo") or {}
        legs = c.get("legs") or []
        res = c.get("result")
        if not legs or res not in ("won", "lost"):
            continue
        lr = [lg.get("result") for lg in legs]
        if any(x not in ("won", "lost", "push", "void") for x in lr):
            continue                                     # non entièrement réglé -> traité par le check 2
        won_all = all(x in ("won", "push", "void") for x in lr)
        any_lost = any(x == "lost" for x in lr)
        tag = f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')}"
        if res == "won" and any_lost:
            bad.append(f"{tag} : combiné 'gagné' mais une jambe 'perdue' ({lr})")
        elif res == "lost" and won_all:
            bad.append(f"{tag} : combiné 'perdu' alors que toutes les jambes passent ({lr})")
    return {"key": "combo_coherence", "level": "error" if bad else "ok",
            "title": "Cohérence règlement combiné ↔ jambes",
            "detail": f"{len(bad)} combiné(s) dont le résultat contredit ses jambes.",
            "items": bad[:20]}


# Bande « plausible » du ratio real_odds / produit, CALIBRÉE sur les données observées le 2026-07-02
# (n=60 : médiane 0.96, MAD 0.10, plage réelle 0.70–1.42) → médiane ± ~6·MAD, avec des bornes-plancher.
# La cote corrélée d'un bet builder S'ÉCARTE NORMALEMENT du produit (shave si jambes corrélées, prime si
# anti-corrélées) : on ne signale donc QUE les écarts GROSSIERS (corruption), pas la corrélation normale.
_PRICE_RATIO_LO, _PRICE_RATIO_HI = 0.45, 1.60


def _check_combo_pricing(rows) -> dict:
    """Invariant DUR : la cote « total » affichée DOIT égaler le produit des cotes de jambes (sinon
    incohérence carte↔jambes, cousine du bug des cotes FANTÔMES). Signal SOUPLE : la vraie cote Kambi
    (real_odds) hors d'une bande grossière du produit = donnée probablement corrompue."""
    hard, soft = [], []
    for p, d in rows:
        c = d.get("combo") or {}
        legs = c.get("legs") or []
        real, tot = _f(c.get("real_odds")), _f(c.get("total"))
        prod, ok_prod = 1.0, bool(legs)
        for lg in legs:
            v = _f(lg.get("cote"))
            if v is None or v < 1.01:
                ok_prod = False
            else:
                prod *= v
        if not ok_prod:
            continue
        tag = f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')}"
        # Tolérance 5 % : le `total` est figé à la construction du combiné, mais les cotes de jambes sont
        # RAFRAÎCHIES ensuite indépendamment (`combo_refreshed`) -> un léger décalage (quelques %) est NORMAL,
        # pas une cote fantôme (celles-ci sont des écarts GROSSIERS, ordre ×2). On ne signale que le grossier.
        if tot is not None and abs(tot - prod) > max(0.05, 0.05 * prod):
            hard.append(f"{tag} : total affiché {tot} ≠ produit des jambes {prod:.2f}")
        if real is not None and not (prod * _PRICE_RATIO_LO <= real <= prod * _PRICE_RATIO_HI):
            soft.append(f"{tag} : real_odds {real} hors bande plausible du produit {prod:.2f} "
                        f"([{prod*_PRICE_RATIO_LO:.2f}, {prod*_PRICE_RATIO_HI:.2f}])")
    items = hard + soft
    return {"key": "combo_pricing", "level": "warn" if items else "ok",
            "title": "Cohérence des cotes de combiné",
            "detail": f"{len(hard)} total≠produit (invariant dur), {len(soft)} vraie-cote anormale.",
            "items": items[:20]}


def _check_odds_prob_sanity(rows) -> dict:
    bad = []
    for p, d in rows:
        tag = f"{d.get('sport')} {os.path.basename(p)}"
        for b in (d.get("bets") or []):
            cote, prob = _f(b.get("cote")), _f(b.get("prob"))
            if cote is not None and (cote < 1.01 or cote > 1000):
                bad.append(f"{tag} : cote aberrante {cote} ({b.get('sel','?')[:32]})")
            if prob is not None and not (1 <= prob <= 99):
                bad.append(f"{tag} : proba hors [1,99] = {prob} ({b.get('sel','?')[:32]})")
        for lg in ((d.get("combo") or {}).get("legs") or []):
            cote = _f(lg.get("cote"))
            if cote is not None and (cote < 1.01 or cote > 1000):
                bad.append(f"{tag} : jambe cote aberrante {cote} ({lg.get('sel','?')[:32]})")
    return {"key": "odds_prob_sanity", "level": "warn" if bad else "ok",
            "title": "Cotes & probabilités valides",
            "detail": f"{len(bad)} valeur(s) de cote/proba hors bornes.",
            "items": bad[:20]}


def _check_settle_not_on_live(rows) -> dict:
    """Un pari NE DOIT PAS être réglé tant que le match n'est pas TERMINÉ. Proxy robuste sans source live :
    un résultat posé alors que le COUP D'ENVOI est encore dans le futur est forcément faux."""
    now = datetime.now(timezone.utc)
    bad = []
    for p, d in rows:
        if d.get("result") not in ("won", "lost", "push", "void"):
            continue
        st = d.get("start")
        if not st:
            continue
        try:
            dt = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt > now:
            bad.append(f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')} réglé '{d['result']}' "
                       f"mais coup d'envoi {st} (futur)")
    return {"key": "settle_not_on_live", "level": "error" if bad else "ok",
            "title": "Aucun règlement avant la fin du match",
            "detail": f"{len(bad)} pari(s) réglé(s) sur un match pas encore commencé.",
            "items": bad[:20]}


def _check_stat_monotonic(rows) -> dict:
    """Le compteur de paris COMPTÉS ne doit JAMAIS baisser. On mesure le VRAI invariant monotone = le
    nombre de `stat_bet` FIGÉS (immuables, jamais retirés), et NON `stats_full().settled` : ce dernier est
    un proxy d'AFFICHAGE qui exclut délibérément les combinés antérieurs à _COMBO_COUNT_FROM et retombe sur
    un recalcul live (for_history) tant qu'un pari réglé n'est pas encore figé -> il FLUCTUE (ex. 72↔73) et
    déclenchait une fausse alerte. Le compte des stat_bet figés, lui, ne fait que monter (cf. mémoire
    stats-display-calibration : gel = compteur monotone)."""
    n = sum(1 for _, d in rows
            if isinstance(d.get("stat_bet"), dict) and d["stat_bet"].get("result") in ("won", "lost", "push"))
    shown = int((analyses.stats_full().get("overall") or {}).get("settled") or 0)   # nombre AFFICHÉ (ROI)
    prev = {}
    try:
        prev = json.load(open(_STATE, encoding="utf-8"))
    except Exception:
        prev = {}
    hw = int(prev.get("settled_hw") or 0)
    lvl, items = "ok", []
    if n < hw:
        lvl = "error"
        items = [f"compteur de paris FIGÉS = {n} < filigrane {hw} (le nombre a BAISSÉ)"]
    return {"key": "stat_monotonic", "level": lvl,
            "title": "Compteur de stats monotone",
            "detail": f"paris figés = {n} (filigrane = {max(hw, n)}) · affichés au ROI = {shown}.",
            "items": items, "_settled": n, "_hw": hw}


def _check_calibration_full() -> dict:
    """La calibration ne doit JAMAIS être filtrée : elle compte TOUTES les prédictions (fantômes + paris),
    donc au moins autant que les paris joués réglés."""
    cal = analyses.calibration()
    n_pred = int(cal.get("n") or 0)
    settled = int((analyses.stats_full().get("overall") or {}).get("settled") or 0)
    lvl, items = "ok", []
    if n_pred < settled:
        lvl = "error"
        items = [f"calibration n={n_pred} < paris joués réglés {settled} (calibration filtrée ?)"]
    return {"key": "calibration_full", "level": lvl,
            "title": "Calibration exhaustive (jamais filtrée)",
            "detail": f"{n_pred} prédictions calibrées ({cal.get('n_shadow') or 0} fantômes), "
                      f"≥ {settled} paris joués.",
            "items": items}


def _check_result_card_posted(rows) -> dict:
    """Une carte RÉSULTAT doit être postée comme message DISTINCT (id ≠ carte prono). Symptôme du bug vécu
    (Portugal-Croatie) : `notified_*` figé MAIS `result_msg` pointe sur l'id du PRONO -> la carte résultat
    n'est jamais partie et le système ne re-tentera plus. Critère PRÉCIS (result_msg == prono) -> zéro faux
    positif (un « aucun result_msg » sur un vieux match = normal, le champ est récent)."""
    try:
        from app import notify
    except Exception:
        return {"key": "result_card_posted", "level": "ok", "title": "Carte résultat réellement postée",
                "detail": "notify indisponible — contrôle sauté.", "items": []}
    bad = []
    for p, d in rows:
        if not (d.get("notified_pick") or d.get("notified_combo")):
            continue
        rm = d.get("result_msg")
        if not rm:
            continue
        try:
            prono = notify.get_prono(str(d.get("id")))
        except Exception:
            prono = None
        if prono and rm == prono:
            bad.append(f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')} : result_msg == id du "
                       f"prono → carte résultat non postée (validation non reçue)")
    return {"key": "result_card_posted", "level": "error" if bad else "ok",
            "title": "Carte résultat réellement postée",
            "detail": f"{len(bad)} match(s) « notifié » dont la carte résultat n'a pas été postée distinctement.",
            "items": bad[:20]}


def _check_combo_correlated_pricing(rows) -> dict:
    """Un combiné BETSFIX est TOUJOURS même-match -> ses jambes sont CORRÉLÉES -> sa cote DOIT être la vraie
    cote corrélée Unibet (Bet Builder), JAMAIS le produit naïf (qui SUR-évalue -> fausse value/EV -> combiné
    retenu à tort). Détecte les combinés À VENIR encore affichés au PRODUIT (`real_odds` absent). Le garde-fou
    de création (`_make_combo`) les écarte désormais ; ceux listés sont du legacy (créés avant) et partent une
    fois les matchs joués. Encode le bug tennis (pricé 1.83 au lieu de 1.44 corrélé)."""
    bad = []
    for p, d in rows:
        c = d.get("combo") or {}
        if not c.get("legs") or c.get("result") is not None:
            continue                                     # réglé/figé -> pas concerné
        if analyses.status_of(d) == "finished":
            continue
        if c.get("real_odds") is None:
            bad.append(f"{d.get('sport')} {d.get('home', '?')}–{d.get('away', '?')} : combiné au produit "
                       f"{c.get('total')} (cote non corrélée)")
    return {"key": "combo_correlated_pricing", "level": "warn" if bad else "ok",
            "title": "Combiné à la vraie cote corrélée",
            "detail": f"{len(bad)} combiné(s) à venir encore au produit (cote sur-évaluée / non plaçable).",
            "items": bad[:20]}


def _check_combo_ev_value(rows) -> dict:
    """Depuis la correction de corrélation (2026-07-05), la proba d'un combiné same-match intègre la
    corrélation que le marché price dans la VRAIE cote Bet Builder (k = total/real). Conséquence : un
    combiné À VENIR en tennis/basket ne doit JAMAIS être retenu sans VALUE réelle — EV = real_odds ×
    prob/100 doit rester ≥ ~1 (le code exige real*prob>1 à la création hors-foot). Un combiné hors-foot
    affiché avec EV<1 = correction/garde-fou contourné (cas FAA/ADF : 3.40 × 28 % = 0.95 -> abstention).
    Le foot COUPE DU MONDE est exclu (repli « 1 combiné par match » voulu) ; le foot HORS CdM est aligné sur
    tennis/basket (exigence de value, depuis 2026-07-05). Forward-only : seulement les combinés à venir."""
    bad = []
    for p, d in rows:
        c = d.get("combo") or {}
        if not c.get("legs") or c.get("result") is not None:
            continue
        comp = (d.get("comp") or "").lower()
        if d.get("sport") == "foot" and ("coupe du monde" in comp or "world cup" in comp):
            continue                                        # foot CdM SEULEMENT : repli « 1 combiné/match »
        #   (hors CdM, le foot est aligné sur tennis/basket -> exigence de value applicable)
        if analyses.status_of(d) == "finished":
            continue
        real, prob = _f(c.get("real_odds")), _f(c.get("prob"))
        if real is None or prob is None:
            continue
        ev = real * prob / 100
        if ev < 0.95:                                       # marge : cotes rafraîchies -> léger jeu toléré
            bad.append(f"{d.get('sport')} {d.get('home', '?')}–{d.get('away', '?')} : EV={ev:.2f} "
                       f"(cote {real} × {prob:.0f}%) — combiné sans value réelle")
    return {"key": "combo_ev_value", "level": "warn" if bad else "ok",
            "title": "Combiné à venir porteur de value (hors-foot)",
            "detail": f"{len(bad)} combiné(s) tennis/basket à venir retenu(s) sans value (EV<0.95).",
            "items": bad[:20]}


def _check_combo_not_dominated(rows) -> dict:
    """Un combiné À VENIR doit PAYER PLUS que chacune de ses jambes seule : `real_odds` > max(cote des
    jambes). Sinon il est DOMINÉ — jouer la jambe seule rapporte davantage AVEC moins de risque (arrive
    quand 2 jambes sont quasi-redondantes -> rabotage extrême de la cote corrélée, cas Mexique 1.47 < jambe
    1.58). Forward-only : seulement les combinés à venir."""
    bad = []
    for p, d in rows:
        c = d.get("combo") or {}
        legs = c.get("legs") or []
        if not legs or c.get("result") is not None:
            continue
        if analyses.status_of(d) == "finished":
            continue
        real = _f(c.get("real_odds"))
        legodds = [_f(l.get("cote")) for l in legs if _f(l.get("cote"))]
        if real is None or not legodds:
            continue
        # ABSOLU (y compris CdM) : un combiné dont une jambe paye PLUS que le total est ABSURDE (jouer la
        # jambe seule est strictement meilleur) -> ne doit JAMAIS exister. Le garde-fou de création le rejette
        # et la diversité de cotes du vivier garantit un combiné non-dominé même pour un gros favori.
        if real <= max(legodds):
            bad.append(f"{d.get('sport')} {d.get('home', '?')}–{d.get('away', '?')} : "
                       f"combiné @{real} ≤ jambe @{max(legodds)} (dominé)")
    return {"key": "combo_not_dominated", "level": "warn" if bad else "ok",
            "title": "Combiné non dominé par une jambe",
            "detail": f"{len(bad)} combiné(s) à venir à cote ≤ leur jambe la plus haute (dominé).",
            "items": bad[:20]}


def _check_data_completeness(rows) -> dict:
    """Complétude des données d'analyse : chaque fiche RÉCENTE trace les sources multi ayant réellement
    répondu (`data_score` = nb de sources). Une fiche à data_score 0 a été analysée sur les COTES SEULES
    (aucun enrichissement FotMob/ESPN/Understat/Flashscore/Sportradar) -> démarche DÉGRADÉE (l'analyse n'a
    pas été faite « de la même manière » que les autres). Forward-only : n'examine QUE les fiches portant
    le champ (les anciennes, sans traçage, sont ignorées). Seuil anti-bruit : warn seulement à partir de 3
    fiches dégradées (un cas isolé = match obscur, pas une régression du pipeline)."""
    traced = [d for _, d in rows if isinstance(d.get("data_score"), int)]
    if not traced:
        return {"key": "data_completeness", "level": "ok",
                "title": "Complétude des données d'analyse",
                "detail": "aucune fiche tracée (traçage forward-only, s'active aux prochains scans)."}
    zero = [d for d in traced if d.get("data_score", 0) == 0]
    n, nz = len(traced), len(zero)
    lvl = "warn" if nz >= 3 else ("info" if nz else "ok")
    items = [f"{d.get('sport')} {d.get('home', '?')}–{d.get('away', '?')} : 0 source (cotes seules)"
             for d in zero[:10]]
    return {"key": "data_completeness", "level": lvl,
            "title": "Complétude des données d'analyse",
            "detail": f"{nz}/{n} fiche(s) tracée(s) analysée(s) sur cotes seules (data_score 0).",
            "items": items}


def _finished_days_ago(d) -> int | None:
    """Nb de jours depuis le coup d'envoi (None si date illisible). Sert à ne PAS alerter sur un match
    tout juste fini (règlement encore en cours)."""
    s = (d.get("start") or "")[:10]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).days


def _check_ghost_resolution(rows) -> dict:
    """Fantômes (calibrage) non réglés sur un match TERMINÉ depuis >2 j. Normal : 1-2 marchés exotiques
    restent par match (services breakés, box-score absent, stats non couvertes). ANORMAL : un match dont la
    MAJORITÉ des fantômes sont en attente = TROU de résolution SYSTÉMIQUE — c'est le signal exact qu'a
    produit l'incident 2026-07-10 (3 matchs basket voidés à tort faute de traduction FR→EN des noms de
    pays : chacun ~100 % de fantômes pending). Seuil anti-bruit calqué sur data_completeness : INFO à 1-2
    matchs suspects (trou isolé/irréductible, ex. Malte-Armenia sans aucune source), WARN dès 3 (plusieurs
    d'un coup = régression du pipeline de résolution, ex. nouveau marché mal codé ou noms non traduits).
    Forward-safe : détecte le trou TÔT (auto) au lieu de le découvrir à la main. Cf. [[markets-resolvability-sources]]."""
    SETTLED = ("won", "lost", "push", "void")
    suspect = []
    for _, d in rows:
        sh = d.get("shadow") or []
        if len(sh) < 4:                            # trop peu de fantômes pour juger un « ratio »
            continue
        age = _finished_days_ago(d)
        if age is None or age < 2:                 # pas assez vieux -> règlement peut être en cours
            continue
        if analyses.status_of(d) != "finished":
            continue
        pend = sum(1 for s in sh if s.get("result") not in SETTLED)
        if pend >= 5 and pend >= 0.5 * len(sh):    # majorité en attente = trou, pas 1-2 marchés exotiques
            suspect.append((d, pend, len(sh)))
    n = len(suspect)
    lvl = "warn" if n >= 3 else ("info" if n else "ok")
    items = [f"{d.get('sport')} {d.get('home', '?')}–{d.get('away', '?')} : {p}/{t} fantômes en attente"
             for d, p, t in suspect[:10]]
    return {"key": "ghost_resolution", "level": lvl,
            "title": "Fantômes réglés sur match terminé",
            "detail": (f"{n} match(s) terminé(s) avec une MAJORITÉ de fantômes non réglés "
                       f"(trou de résolution : noms non traduits ? marché non codé ? source absente ?)."),
            "items": items}


def _check_provisional_dedup() -> dict:
    """Un provisoire NON réglé ne doit JAMAIS être suivi si son match a DÉJÀ un pari RETENU (combiné ou
    simple) OU s'il est une JAMBE DU COMBINÉ DU JOUR : sinon le même match est suivi 2× et une seule erreur
    se répercote à deux endroits, avec deux résultats possibles (demande user 2026-07-11 ; élargie au
    combiné du jour 2026-07-12). La dédup est assurée par `provisional.prune_retained` ; ce check détecte
    tout contournement futur. Ignore les provisoires déjà réglés (compteur monotone)."""
    dup = []
    try:
        from app import provisional, combo_daily
        d = provisional.load()
        _daily_legs = combo_daily.leg_ids()
        for mid, p in d.items():
            if not isinstance(p, dict) or p.get("result") in ("won", "lost", "push"):
                continue
            sport = p.get("sport")
            if analyses.has_combo(sport, mid) or analyses.retained_bet(sport, mid) is not None:
                dup.append(f"{p.get('name', '?')} : provisoire « {p.get('sel')} » alors que le match a "
                           f"un pari retenu (doublon)")
            elif mid in _daily_legs:
                dup.append(f"{p.get('name', '?')} : provisoire « {p.get('sel')} » alors que le match est "
                           f"une jambe du combiné du jour (doublon)")
    except Exception:
        pass
    return {"key": "provisional_dedup", "level": "error" if dup else "ok",
            "title": "Provisoire non dupliqué d'un pari retenu ou d'une jambe du combiné du jour",
            "detail": f"{len(dup)} provisoire(s) suivi(s) dont le match a déjà un pari ailleurs (doublon).",
            "items": dup[:20]}


def run(persist: bool = False) -> dict:
    """Lance TOUS les contrôles. `persist=True` met à jour le filigrane de monotonicité (à réserver au
    run quotidien de confiance). Renvoie {status, ts, counts, checks:[...]}. Ne lève jamais."""
    rows, broken = _load_rows()
    checks = [
        _check_integrity(rows, broken),
        _check_combo_unsettled_legs(rows),
        _check_combo_coherence(rows),
        _check_combo_pricing(rows),
        _check_odds_prob_sanity(rows),
        _check_settle_not_on_live(rows),
        _check_result_card_posted(rows),
        _check_stat_monotonic(rows),
        _check_calibration_full(),
        _check_data_completeness(rows),
        _check_combo_correlated_pricing(rows),
        _check_combo_ev_value(rows),
        _check_combo_not_dominated(rows),
        _check_ghost_resolution(rows),
        _check_provisional_dedup(),
    ]
    worst = max((_LVL_RANK.get(c["level"], 0) for c in checks), default=0)
    status = {0: "ok", 1: "info", 2: "warn", 3: "error"}[worst]
    counts = {"error": sum(c["level"] == "error" for c in checks),
              "warn": sum(c["level"] == "warn" for c in checks),
              "ok": sum(c["level"] == "ok" for c in checks)}
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if persist:
        sm = next((c for c in checks if c["key"] == "stat_monotonic"), {})
        try:
            json.dump({"settled_hw": max(sm.get("_hw", 0), sm.get("_settled", 0)), "ts": ts},
                      open(_STATE, "w", encoding="utf-8"))
        except Exception:
            pass
    for c in checks:                                     # champs internes -> hors sortie publique
        c.pop("_settled", None); c.pop("_hw", None)
    return {"status": status, "ts": ts, "counts": counts, "checks": checks,
            "sidecars": len(rows), "broken": broken}
