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
    un résultat posé alors que le COUP D'ENVOI est encore dans le futur est forcément faux (source de score
    matchée par NOMS sur une rencontre ANTÉRIEURE entre les mêmes équipes -> bug 2026-07-17 Tijuana-Tigres
    « gagné » AVANT le coup d'envoi). ⚠️ FIX 2026-07-17 : le sidecar principal range le règlement dans des
    SOUS-champs (`result.pick_result`, `stat_bet.result`, `bets[].result`, `combo.result`), PAS dans un
    `result` string -> l'ancien test `d.get("result") in (...)` (un DICT, jamais dans le tuple) ne voyait
    RIEN et laissait passer tout règlement prématuré du chemin principal. On inspecte les vrais champs."""
    now = datetime.now(timezone.utc)
    _S = ("won", "lost", "push", "void")
    bad = []
    for p, d in rows:
        st = d.get("start")
        if not st:
            continue
        try:
            dt = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt <= now:
            continue                                 # match commencé -> règlement légitime
        hits = []                                    # coup d'envoi FUTUR : aucun champ de règlement admis
        if (d.get("result") or {}).get("pick_result") in _S:
            hits.append("result")
        if (d.get("stat_bet") or {}).get("result") in ("won", "lost", "push"):
            hits.append("stat_bet")
        if any((b or {}).get("result") in _S for b in (d.get("bets") or [])):
            hits.append("bets")
        if (d.get("combo") or {}).get("result") in ("won", "lost"):
            hits.append("combo")
        if hits:
            bad.append(f"{d.get('sport')} {d.get('home','?')}–{d.get('away','?')} : "
                       f"{'/'.join(hits)} réglé(s) mais coup d'envoi {st} (futur)")
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
    # + paris du 1er scan (remplacés au rescan, figés dans stat_bet_first — comptés au ROI, 2026-07-21)
    n += sum(1 for _, d in rows
             if isinstance(d.get("stat_bet_first"), dict)
             and d["stat_bet_first"].get("result") in ("won", "lost", "push"))
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


def _check_provisional_settle_finished() -> dict:
    """Un provisoire ne doit JAMAIS être réglé (won/lost/push) tant que son match n'est pas PROBABLEMENT
    FINI (bug 2026-07-17 : Botafogo-Santos & Tijuana-Tigres marqués « gagné » AVANT le coup d'envoi — la
    recherche par NOMS de final_score matchait un match ANTÉRIEUR entre les mêmes équipes). Le garde
    `analyses.likely_finished` dans `settle_pending` doit l'empêcher ; ce check détecte tout règlement
    prématuré résiduel/futur. Encode la régression."""
    bad = []
    try:
        from app import provisional
        for mid, p in provisional.load().items():
            if not isinstance(p, dict) or p.get("result") not in ("won", "lost", "push"):
                continue
            if not analyses.likely_finished({"start": p.get("start"), "sport": p.get("sport")}):
                bad.append(f"{p.get('name', '?')} : provisoire réglé '{p.get('result')}' "
                           f"(score {p.get('score', '?')}) alors que le match n'est PAS fini "
                           f"(coup d'envoi {p.get('start')})")
    except Exception:
        pass
    return {"key": "provisional_settle_finished", "level": "error" if bad else "ok",
            "title": "Provisoire jamais réglé avant la fin du match",
            "detail": f"{len(bad)} provisoire(s) réglé(s) avant la fin du match (règlement prématuré).",
            "items": bad[:20]}


def _check_combo_daily_settle_finished() -> dict:
    """Une jambe du combiné du jour ne doit JAMAIS être réglée (won/lost/push) tant que son match n'est pas
    PROBABLEMENT FINI. Encode la régression 2026-07-18 : Sport Recife-Operário réglé « lost 3-0 » via le
    repli sportradar EN PLEINE 66e minute (vrai live 2-1) — `final_score` avait matché par NOMS un AUTRE
    match « Sport Recife » déjà terminé. Le garde `analyses.likely_finished` dans `settle_pending` doit
    l'empêcher ; ce check détecte tout règlement prématuré résiduel. Tolère une jambe dont le MATCH est déjà
    réglé dans son propre sidecar (règlement légitime du match lui-même, avant la fenêtre d'horloge)."""
    bad = []
    try:
        from app import combo_daily
        for day, cb in combo_daily.load().items():
            if not isinstance(cb, dict):
                continue
            for leg in cb.get("legs") or []:
                if leg.get("result") not in ("won", "lost", "push"):
                    continue
                if analyses.likely_finished({"start": leg.get("start"), "sport": leg.get("sport")}):
                    continue                          # fini par l'horloge -> règlement légitime
                sm = analyses.meta(leg.get("sport"), str(leg.get("mid") or "")) or {}
                if analyses.is_settled(sm):
                    continue                          # match déjà réglé dans son sidecar -> légitime
                bad.append(f"{day} {leg.get('sport')} {leg.get('name', '?')} : jambe réglée "
                           f"'{leg.get('result')}' (score {leg.get('score', '?')}) alors que le match "
                           f"n'est PAS fini (coup d'envoi {leg.get('start')})")
    except Exception:
        pass
    return {"key": "combo_daily_settle_finished", "level": "error" if bad else "ok",
            "title": "Jambe de combiné du jour jamais réglée avant la fin du match",
            "detail": f"{len(bad)} jambe(s) réglée(s) avant la fin du match (règlement prématuré).",
            "items": bad[:20]}


def _check_extratime_regulation(rows) -> dict:
    """Un match de foot allé aux PROLONGATIONS doit régler ses marchés 90 MIN (1X2, over/under, mi-temps,
    REGTIME…) sur le score RÉGLEMENTAIRE, JAMAIS sur le score final (prolongation incluse). Régression
    passée (erreur grave Argentine-Suisse 3-1 réglé « won » alors que le temps réglementaire était 1-1 :
    FotMob fond les buts de prolongation dans la 2e mi-temps). Le règlement stocke désormais le score
    réglementaire dans `raw` (reg_home/reg_away/reg_periods + after_extra). Ce check RE-RÈGLE chaque jambe /
    pari / stat_bet 90-min sur CE réglementaire stocké et flague toute divergence avec le résultat figé."""
    from app.settle_analyst import settle_pick, code_from_pick
    bad = []
    for p, d in rows:
        if d.get("sport") != "foot":
            continue
        raw = (d.get("result") or {}).get("raw") or {}
        if not raw.get("after_extra") or raw.get("reg_home") is None:
            continue
        reg = {"home": raw.get("reg_home"), "away": raw.get("reg_away"),
               "winner": ("home" if raw["reg_home"] > raw["reg_away"] else
                          ("away" if raw["reg_away"] > raw["reg_home"] else "draw")),
               "periods": {int(k): tuple(v) for k, v in (raw.get("reg_periods") or {}).items()},
               "stats": raw.get("stats") or {}}
        nm = f"{d.get('home','?')}–{d.get('away','?')}"
        checks = [(l.get("code"), l.get("result"), l.get("sel", "")) for l in ((d.get("combo") or {}).get("legs") or [])]
        checks += [(b.get("code"), b.get("result"), b.get("sel", "")) for b in (d.get("bets") or [])]
        sb = d.get("stat_bet")
        if isinstance(sb, dict) and sb.get("sel"):
            checks.append((code_from_pick(sb["sel"], "foot", d.get("home", ""), d.get("away", "")),
                           sb.get("result"), sb.get("sel", "")))
        for code, res, sel in checks:
            if not code or res not in ("won", "lost", "push"):
                continue
            exp = settle_pick(code, reg)
            if exp in ("won", "lost", "push") and exp != res:
                bad.append(f"{nm} : « {sel[:40]} » figé '{res}' mais réglementaire ({reg['home']}-{reg['away']}) = '{exp}'")
    return {"key": "extratime_regulation", "level": "error" if bad else "ok",
            "title": "Marchés 90 min réglés sur le temps réglementaire (hors prolongation)",
            "detail": f"{len(bad)} pari(s) 90-min réglé(s) sur le score prolongation-incluse au lieu du réglementaire.",
            "items": bad[:20]}


def _check_bet_gloss_coverage(rows) -> dict:
    """CHAQUE pari AFFICHÉ (simple retenu/publié + jambe de combiné) d'un match à venir/en cours doit porter
    sa ligne d'explication en clair « ↳ » (demande user 2026-07-17 : « valable pour N'IMPORTE QUEL pari joué »).
    Le rendu passe par `web._bet_gloss` = TOTAL (cas précis `_plain_market`, sinon repli générique sûr) → il
    ne renvoie '' que si le `sel` est vide. Ce garde-fou vérifie 2 choses :
    - ERREUR : un pari joué dont `_bet_gloss` est vide (sel vide/corrompu) — jamais de carte sans « ↳ ».
    - INFO : un pari tombé sur le repli GÉNÉRIQUE (`_plain_market` vide) → marché à coder précisément un jour
      (l'abonné voit déjà une explication, mais générique). C'est le nudge qui remplace l'ancien WARN.
    100 % lecture seule ; ne juge que les matchs NON terminés (ce qui est encore affiché aux abonnés)."""
    from app import web
    empty, generic = [], []
    for p, d in rows:
        try:
            if analyses.status_of(d) == "finished":
                continue
            sport = (d.get("sport") or os.path.basename(p).split("_", 1)[0] or "").lower()
            home, away = d.get("home", ""), d.get("away", "")
            mid = d.get("id")
            sels = []
            rb = analyses.published_bet(sport, mid) or analyses.retained_bet(sport, mid)
            if rb and rb.get("sel"):
                sels.append(rb["sel"])
            for leg in ((d.get("combo") or {}).get("legs") or []):
                if leg.get("sel"):
                    sels.append(leg["sel"])
            for sel in sels:
                if not (sel or "").strip():
                    continue
                if not web._bet_gloss(sel, sport, home, away):
                    empty.append(f"{sport} {home}–{away} : « {sel[:50]} » SANS explication")
                elif not web._plain_market(sel, sport, home, away):
                    generic.append(f"{sport} {home}–{away} : « {sel[:50]} » (glose générique)")
        except Exception:
            continue
    lvl = "error" if empty else ("info" if generic else "ok")
    return {"key": "bet_gloss_coverage", "level": lvl,
            "title": "Explication en clair (« ↳ ») sur CHAQUE pari",
            "detail": (f"{len(empty)} pari(s) SANS explication (anomalie) · {len(generic)} sur repli "
                       f"générique (marché à coder précisément dans web._plain_market)."),
            "items": (empty + generic)[:20]}


def _check_tennis_sets_overconfidence(rows) -> dict:
    """SURVEILLANCE (demande user 2026-07-22) du marché « Sets » tennis (« remporte au moins un set »),
    coupable historique du ROI tennis : juin = annoncé 78 % → réel 56 % (−21 pts de sur-confiance) ; redressé
    en juillet (+3 pts). On NE COUPE PAS le marché tant qu'il performe, mais on ALERTE si la sur-confiance
    REVIENT : écart réussite réelle − confiance annoncée ≤ −15 pts sur les 40 dernières prédictions Sets
    tennis à HAUTE confiance (≥ 65 %, la zone de jeu), joués + fantômes. Seuil CALIBRÉ sur données (juin −21
    aurait alerté, juillet +3 non). Lecture seule — signale, ne modifie rien. Alerte Telegram ciblée (cf.
    tools/selfcheck.py _ALERT_ON_WARN) sans réintroduire de bruit sur les autres warns."""
    import re as _re
    _is_set = lambda sel: bool(_re.search(
        r"au moins un set|remporte.*set|1er set|premier set|sans perdre.*set", (sel or "").lower()))
    preds = []
    for _p, d in rows:
        if d.get("sport") != "tennis":
            continue
        day = (d.get("start") or "")[:10]
        cands = ([d["stat_bet"]] if d.get("stat_bet") else []) + (d.get("shadow") or [])
        for c in cands:
            if not isinstance(c, dict) or c.get("result") not in ("won", "lost"):
                continue
            if not _is_set(c.get("sel")):
                continue
            p = c.get("prob")
            if p is None:
                p = c.get("cprob")
            if p is None:
                continue
            p = p if p > 1 else p * 100
            if p >= 65:
                preds.append((day, p, c.get("result")))
    preds.sort()
    window = preds[-40:]
    MIN_N, GAP_WARN = 25, -15
    n = len(window)
    if n < MIN_N:
        return {"key": "tennis_sets_overconfidence", "level": "ok",
                "title": "Sur-confiance marché « Sets » tennis (surveillance)",
                "detail": f"{n} prédiction(s) Sets tennis à conf ≥ 65 % (min {MIN_N}) — surveillance en attente de données.",
                "items": []}
    w = sum(r[2] == "won" for r in window)
    real = round(100 * w / n)
    conf = round(sum(r[1] for r in window) / n)
    gap = real - conf
    over = gap <= GAP_WARN
    items = ([f"Sets tennis (40 derniers, conf ≥ 65 %) : annoncé {conf}% mais réel {real}% → sur-confiance "
              f"{gap:+} pts (seuil {GAP_WARN}). Le marché re-déraille comme en juin → envisager de l'écarter "
              f"ou de recalibrer sa confiance à la baisse."] if over else [])
    return {"key": "tennis_sets_overconfidence", "level": "warn" if over else "ok",
            "title": "Sur-confiance marché « Sets » tennis (surveillance)",
            "detail": f"40 derniers Sets tennis (conf ≥ 65 %) : annoncé {conf}% → réel {real}% "
                      f"(écart {gap:+} pts ; alerte si ≤ {GAP_WARN}).",
            "items": items}


def _check_uniform_labels(rows) -> dict:
    """UNIFORMITÉ DES LIBELLÉS (demande user 2026-07-23, IMPÉRATIVE « ça ne doit plus JAMAIS arriver ») : deux
    paris à MÊME issue de règlement DOIVENT afficher le MÊME intitulé (`pretty_sel`). Détecte les DIVERGENCES
    (ex. « X vainqueur » vs « X gagne (temps réglementaire) »). GARDE-FOU contre la RÉCURRENCE : les fixes
    ad hoc de pretty_sel laissaient repasser tout nouveau format non couvert, SANS détection -> l'user
    re-signalait manuellement. Ici on regroupe par issue canonique (codes équivalents REGTIME/1X2/WIN) et on
    signale (INFO) toute issue à ≥2 intitulés. Masque les noms d'équipe (≥4 lettres) pour comparer la STRUCTURE."""
    import re as _re
    from app import analyses as _an
    from app.settle_analyst import code_from_pick as _cfp

    def _canon(code):
        if not code:
            return None
        p = code.split()
        if p[0] in ("REGTIME", "1X2", "WIN"):
            side = {"1": "HOME", "2": "AWAY", "X": "DRAW"}.get(p[1] if len(p) > 1 else "",
                                                               p[1] if len(p) > 1 else "")
            return ("WIN", side)
        return tuple(p)

    seen: dict = {}
    for _p, d in rows:
        if (d.get("result") or {}).get("pick_result") or d.get("stat_bet"):
            continue                                   # match réglé -> affichage figé, hors périmètre
        sp, mid = d.get("sport"), str(d.get("id"))
        home, away = d.get("home", ""), d.get("away", "")
        try:
            sels = [b.get("sel") for b in (_an.bets_of(sp, mid) or []) if b.get("sel")]
        except Exception:
            sels = []
        for sel in sels:
            c = _canon(_cfp(sel, sp, home, away))
            if not c:
                continue
            st = _an.pretty_sel(sel, home, away)
            for nm in (home, away):                    # masque les noms d'équipe -> compare la STRUCTURE
                for tok in _re.findall(r"[A-Za-zÀ-ÿ]{4,}", nm or ""):
                    st = _re.sub(_re.escape(tok), "•", st, flags=_re.I)
            st = _re.sub(r"[•\s]+", " ", st).strip()
            seen.setdefault((sp, c), set()).add(st)
    diverg = [f"{sp} {'/'.join(map(str, c))} → " + " ⁄ ".join(sorted(forms))
              for (sp, c), forms in seen.items() if len(forms) > 1]
    return {"key": "uniform_labels", "level": "info" if diverg else "ok",
            "title": "Libellés uniformes (même issue = même intitulé)",
            "detail": f"{len(diverg)} issue(s) à intitulés DIVERGENTS (à converger dans analyses.pretty_sel).",
            "items": diverg[:20]}


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
        _check_provisional_settle_finished(),
        _check_combo_daily_settle_finished(),
        _check_extratime_regulation(rows),
        _check_bet_gloss_coverage(rows),
        _check_tennis_sets_overconfidence(rows),
        _check_uniform_labels(rows),
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
