"""Paris RÉELLEMENT joués par l'utilisateur (mise + cote obtenues) + bilan € + couverture live.

- Stockage simple JSON (`data/my_bets.json`). Chaque pari réfère un match analysé + la position de
  pari jouée (1/2/3) -> le résultat est repris du règlement automatique des analyses (sidecar `bets`).
- Couverture « ASSURANCE » : pendant le live, si le pari d'avant-match est sur le VAINQUEUR, on
  calcule la mise à poser sur le camp adverse (cote live) pour RÉCUPÉRER sa mise si ça tourne mal,
  tout en gardant le gros gain si ça passe. Garanti seulement quand les cotes le permettent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from app import analyses

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "data", "my_bets.json")


def load() -> list:
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f) or []
    except (OSError, ValueError):
        return []


START_BANKROLL = 100.0   # capital de DÉPART simulé (période de test) — fixe, pas de saisie


def bankroll() -> float:
    """Bankroll (€) servant au calibrage des mises = bankroll SIMULÉE COURANTE (100 € de départ +
    résultats déjà réglés). Les mises se COMPOSENT donc sur le solde qui évolue : quand le portefeuille
    monte, on mise un peu plus ; quand il baisse, un peu moins (¼ Kelly sur le capital réel). Pas de
    saisie utilisateur pendant le test."""
    return sim_balance()["balance"]


def sim_balance() -> dict:
    """Bilan de la BANKROLL SIMULÉE : part de 100 €, évolue avec le résultat (réglé) des paris `sim`.
    Renvoie start, balance (=100+pnl réglé), pnl, staked, pending, settled, count, roi."""
    items = [enrich(b) for b in load() if b.get("sim")]
    settled = [x for x in items if x.get("pnl") is not None]
    pnl = round(sum(x["pnl"] for x in settled), 2)
    settled_stake = sum(x["stake"] for x in settled)
    return {"start": START_BANKROLL, "balance": round(START_BANKROLL + pnl, 2), "pnl": pnl,
            "staked": round(sum(x["stake"] for x in items), 2),
            "count": len(items), "settled": len(settled), "pending": len(items) - len(settled),
            "roi": (round(100 * pnl / settled_stake, 1) if settled_stake else None),
            "clv": clv_stats()}


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def capture_closing() -> int:
    """Capture la cote de CLÔTURE des paris simulés dont le match approche du coup d'envoi (fenêtre
    [start−25 min, start]) et calcule le CLV (cote prise vs clôture). FORWARD-ONLY : après le coup
    d'envoi le marché pré-match disparaît -> pari marqué `clv_missed` (on n'insiste plus). Re-price
    via les marchés Unibet (app.unibet + app.clv). Renvoie le nb de captures. Best-effort."""
    from app import clv, unibet
    bets = load()
    now = datetime.now(timezone.utc)
    changed = 0
    for b in bets:
        if b.get("close_odds") is not None or b.get("clv_missed"):
            continue
        m = analyses.meta(b.get("sport"), b.get("match_id")) or {}
        start = _parse_dt(m.get("start"))
        if not start:
            continue
        mins = (start - now).total_seconds() / 60.0
        if mins > 25:                       # trop tôt -> on capturera à un prochain passage
            continue
        if mins < -2:                       # coup d'envoi passé -> clôture ratée, on arrête d'essayer
            b["clv_missed"] = True
            changed += 1
            continue
        home, away = m.get("home", ""), m.get("away", "")
        eid = unibet.find_id(home, away, b.get("sport")) \
            or (str(b.get("match_id")) if b.get("sport") == "foot" else None)
        close = clv.price_pick(b.get("code", ""), home, away, unibet.markets(eid)) if eid else None
        if close:
            b["close_odds"] = round(float(close), 3)
            b["clv"] = clv.clv_pct(b.get("odds"), close)
            b["clv_at"] = now.isoformat()
            changed += 1
        # sinon (issue introuvable) : on retentera jusqu'à ce que mins<-2 -> missed
    if changed:
        _save(bets)
    return changed


def clv_stats() -> dict:
    """Bilan CLV des paris simulés capturés : {n, avg_pct, beat_pct}. n=0 si rien encore capturé.
    avg_pct = CLV moyen (%), beat_pct = % de paris ayant battu la clôture (CLV>0)."""
    vals = [b["clv"] for b in load() if isinstance(b.get("clv"), (int, float))]
    if not vals:
        return {"n": 0, "avg_pct": None, "beat_pct": None}
    pos = sum(1 for v in vals if v > 0)
    return {"n": len(vals), "avg_pct": round(100 * sum(vals) / len(vals), 1),
            "beat_pct": round(100 * pos / len(vals))}


def recommended_bets() -> list:
    """ASSISTANT : pour chaque match analysé à venir/en cours, le pari ✅ À JOUER (value EV+) détecté
    par `analyses._recommend`, avec la MISE € conseillée (si bankroll définie) + le lien Unibet.
    Triés par coup d'envoi. C'est la liste « place ça pour faire monter le portefeuille »."""
    bk = bankroll()
    out = []
    from app.settle_analyst import code_from_pick
    ex_sports, ex_markets = analyses.auto_exclusions()   # catégories écartées SI prouvées perdantes
    for sport in ("foot", "tennis", "basket"):
        if sport in ex_sports:                           # sport exclu par la calibration (preuve suffisante)
            continue
        for d in analyses.list_for(sport):
            # Statut PILOTÉ PAR UNIBET (même logique que les cartes des onglets) : un match
            # RETARDÉ est « finished » à l'horloge alors qu'il est réellement EN COURS -> il
            # disparaissait de Paris à jouer tout en restant ⭐ sur sa carte (vécu 2026-06-12).
            from app import match_select
            st = analyses.status_of(d)
            ls = match_select.live_state_for(sport, d.get("home"), d.get("away"))
            st, _ = match_select.fresh_status(sport, d.get("home"), d.get("away"), st,
                                              bool((ls or {}).get("score")),
                                              start_iso=d.get("start"))
            if st not in ("notstarted", "inprogress"):
                continue
            bets = analyses.bets_of(sport, d.get("id"))
            # On ne recommande (et ne simule) QUE des paris : (a) RÉGLABLES (sinon track-record faux),
            # (b) dans un MARCHÉ non écarté par la calibration (uniquement ceux PROUVÉS perdants).
            ok, cprobs = set(), []
            for i, b in enumerate(bets):
                code = code_from_pick(b.get("sel", ""), sport, d.get("home", ""), d.get("away", ""))
                cprobs.append(analyses.calibrated_conf(b.get("prob"), sport, code))  # confiance recalibrée
                if code and analyses.market_of(code) not in ex_markets:
                    ok.add(i)
            reco = analyses._recommend(bets, ok, cprobs)
            if reco.get("verdict") != "play" or reco.get("idx") is None:
                continue
            b = bets[reco["idx"]]
            comp = (d.get("comp") or "").upper()
            tour = ("wta" if comp == "WTA" else "atp") if sport == "tennis" else ""
            url = {"foot": f'/foot/match/{d.get("id")}',
                   "basket": f'/basket/match/{d.get("id")}',
                   "tennis": f'/app/match/{d.get("id")}?tour={tour}'}.get(sport, "")
            out.append({"sport": sport, "match_id": d.get("id"), "pari": reco["idx"],
                        "home": d.get("home", ""), "away": d.get("away", ""), "sel": b["sel"],
                        "cote": b["cote"], "prob": b.get("prob"), "ev": reco["ev"],
                        "stake_pct": reco["stake_pct"],
                        "stake_eur": (round(bk * reco["stake_pct"] / 100, 2) if bk else None),
                        "start": d.get("start", ""), "status": st,
                        "comp": d.get("comp", ""), "url": url,
                        "sofa_url": d.get("sofa_url"), "unibet_url": d.get("unibet_url")})
    out.sort(key=lambda x: x["start"] or "")
    return out


def sync_simulation(reco: list | None = None) -> int:
    """SIMULATION : enregistre automatiquement (champ `sim=True`) chaque pari RETENU par le système
    (`recommended_bets`) qui n'est pas déjà loggé, avec la mise conseillée sur la bankroll courante.
    Bâtit un track record réel du système SANS engager d'argent. Idempotent (dédup sport+match+pari).
    Renvoie le nb de paris simulés ajoutés."""
    reco = recommended_bets() if reco is None else reco
    bets = load()
    # Dédup PAR MATCH (pas par (match, pari)) : la simulation ne joue qu'UN pari par match. Si un match
    # est RE-SCANNÉ et que le pari recommandé change d'index (P1->P2) ou de formulation, la clé
    # (match, pari) différait -> doublon. Clé par match -> un seul pari simulé par match, garanti.
    seen = {(b.get("sport"), str(b.get("match_id"))) for b in bets}
    new_id = max((b.get("id", 0) for b in bets), default=0)
    added = 0
    for r in reco:
        key = (r["sport"], str(r["match_id"]))
        if key in seen or not r.get("stake_eur") or not r.get("cote"):
            continue
        new_id += 1
        bets.append({"id": new_id, "sport": r["sport"], "match_id": str(r["match_id"]),
                     "pari": int(r["pari"]), "sel": r["sel"], "stake": round(r["stake_eur"], 2),
                     "odds": round(float(r["cote"]), 3),
                     "code": _derive_code(r["sport"], r["match_id"], r["sel"]),
                     "sim": True, "placed_at": datetime.now(timezone.utc).isoformat()})
        seen.add(key)
        added += 1
    if added:
        _save(bets)
    return added


def _save(bets: list) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(bets, f, ensure_ascii=False)


def _derive_code(sport: str, match_id: str, sel: str) -> str:
    """Code règlable/couvrable du pari (OVER/UNDER, WIN, 1X2…) dérivé de la sélection. Permet la
    couverture live AVANT que le règlement (post-match) n'ait peuplé les codes du sidecar."""
    from app.settle_analyst import code_from_pick
    m = analyses.meta(sport, match_id) or {}
    return code_from_pick(sel or "", sport, m.get("home", ""), m.get("away", "")) or ""



def _bet_result(meta: dict, b: dict):
    """Résultat ('won'/'lost'/'push'/None) du pari simulé, retrouvé dans le règlement du sidecar PAR
    SÉLECTION (puis par code) — robuste à un RE-SCAN qui réordonne/reformule les paris (sinon l'index
    `pari` stocké devient périmé et pointe le mauvais pari). Repli final sur l'index."""
    bl = (meta or {}).get("bets") or []
    sel = analyses._norm_sel(b.get("sel", ""))
    if sel:
        for x in bl:
            if analyses._norm_sel(x.get("sel", "")) == sel:
                return x.get("result")
    code = b.get("code")
    if code:
        for x in bl:
            if (x.get("code") or "") == code:
                return x.get("result")
    pari = b.get("pari", 0)
    return bl[pari].get("result") if 0 <= pari < len(bl) else None


def enrich(b: dict) -> dict:
    """Ajoute au pari joué : infos match, statut, résultat, gain/perte (€), gain potentiel."""
    m = analyses.meta(b["sport"], b["match_id"]) or {}
    res = _bet_result(m, b)
    stake, odds = b.get("stake", 0.0), b.get("odds", 0.0)
    pnl = None
    if res == "won":
        pnl = round(stake * (odds - 1), 2)
    elif res == "lost":
        pnl = round(-stake, 2)
    elif res == "push":
        pnl = 0.0
    code = b.get("code") or _derive_code(b["sport"], b["match_id"], b.get("sel", ""))
    return {**b, "home": m.get("home", ""), "away": m.get("away", ""),
            "comp": m.get("comp", ""), "start": m.get("start", ""),
            "status": analyses.status_of(m) if m else "notstarted",
            "result": res, "pnl": pnl, "potential": round(stake * odds, 2),
            "code": code, "_meta": m}


