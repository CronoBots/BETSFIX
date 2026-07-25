"""Montante quotidienne — fonctionnalité PRÉPARÉE le 2026-07-24 (activation ultérieure sur décision du
propriétaire).

PRINCIPE : mise de départ **10 €**, **UN** pari par jour (le plus sûr sélectionné). À chaque **GAIN**, la
TOTALITÉ (mise + gains) est **rejouée** le lendemain — capitalisation. À la première **PERTE**, la montante
s'arrête et on **repart à 10 €**. L'objectif est d'enchaîner les paliers.

TOTALEMENT ISOLÉ : lit/écrit UNIQUEMENT `data/montante_track.json` — jamais sidecars / ROI / stats /
calibration. Tant qu'aucun pari n'est enregistré, `state()["active"]` est False et la page affiche un aperçu
premium (concept + exemple) prêt à basculer sur les vraies données le jour où le propriétaire l'active.

Format `data/montante_track.json` :
    {
      "base_stake": 10.0,
      "steps": [
        {"date": "2026-07-25", "match": "Home - Away", "sport": "foot", "sel": "…",
         "cote": 1.45, "result": "won"|"lost"|null}
      ]
    }
Le capital de chaque palier est RE-DÉRIVÉ des cotes (jamais stocké — une seule source de vérité : la suite
des paris). Un `result` null = pari du jour EN ATTENTE.
"""
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK = os.path.join(_ROOT, "data", "montante_track.json")
BASE_STAKE = 10.0


def load() -> dict:
    try:
        with open(TRACK, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d: dict) -> None:
    os.makedirs(os.path.dirname(TRACK), exist_ok=True)
    tmp = TRACK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TRACK)


def _split_chains(steps: list, base: float) -> list:
    """Découpe la suite chronologique de paris en MONTANTES (chaînes) : une chaîne = suite de gains
    capitalisés, terminée par une PERTE (ou encore en cours). Chaque step reçoit son `stake` (capital
    engagé = capital courant) et son `payout` (capital après, si gagné). Renvoie une liste de dicts
    {steps, peak, result, palier} où `result` = 'lost' (terminée) / 'pending' (en cours) / 'won'
    (clôturée par choix — rare)."""
    chains, cur, cap = [], [], base
    for s in steps or []:
        cote = s.get("cote")
        stake = round(cap, 2)
        step = {**s, "stake": stake}
        res = s.get("result")
        if res == "won" and isinstance(cote, (int, float)):
            cap = cap * cote
            step["payout"] = round(cap, 2)
            cur.append(step)
        elif res == "lost":
            step["payout"] = 0.0
            cur.append(step)
            chains.append(_finish_chain(cur, base, "lost"))
            cur, cap = [], base
        elif res in ("push", "void"):                # remboursé -> capital inchangé, la montante continue
            step["payout"] = round(cap, 2)
            cur.append(step)
        else:                                        # pending (pari du jour non réglé)
            step["payout"] = None
            cur.append(step)
    if cur:
        chains.append(_finish_chain(cur, base, "pending"))
    return chains


def _finish_chain(steps: list, base: float, result: str) -> dict:
    wins = sum(1 for s in steps if s.get("result") == "won")
    # pic de capital atteint = plus haut payout gagné (ou la mise de base si rien gagné)
    peak = max([base] + [s["payout"] for s in steps if s.get("result") == "won" and s.get("payout")])
    return {"steps": steps, "peak": round(peak, 2), "result": result, "palier": wins}


def _compute(steps: list, base: float, sim: bool = False) -> dict:
    """Cœur partagé — construit l'état d'affichage à partir d'une suite chronologique de paris.
    `sim=True` (simulation basée sur l'historique, ex. simples foot) : on met en AVANT la MEILLEURE
    montante atteinte (pic de capital) plutôt que la chaîne en cours. Renvoie toujours un dict :
    - active     : au moins un pari ?
    - sim        : mode simulation (labels adaptés)
    - base       : mise de départ (10 €)
    - capital    : capital mis en avant (héros) — meilleur pic (sim) ou capital courant (réel)
    - palier     : nb de gains de la montante mise en avant
    - pending    : pari du jour non réglé (réel) ou None
    - featured   : la montante affichée dans l'échelle (meilleure en sim, en cours en réel)
    - chains     : montantes terminées (meilleures d'abord en sim, plus récentes en réel)
    - stats      : {n, best_capital, best_palier, avg_palier, total_profit}
    """
    chains = _split_chains(steps, base)
    active = bool(steps)
    current = chains[-1] if chains and chains[-1]["result"] == "pending" else None
    done = [c for c in chains if c["result"] != "pending"]
    best = max(chains, key=lambda c: c["peak"]) if chains else None
    # capital / palier de la chaîne EN COURS (montante réelle)
    cap, palier, pending = base, 0, None
    if current:
        palier = current["palier"]
        last = current["steps"][-1]
        if last.get("result") is None:               # pari du jour en attente
            pending = last
            cap = last.get("stake", base)
        else:                                         # tous gagnés, en attente du prochain pari
            cap = current["steps"][-1].get("payout") or base
    best_cap = max([base] + [c["peak"] for c in chains]) if chains else base
    best_pal = max([0] + [c["palier"] for c in chains]) if chains else 0
    avg_pal = round(sum(c["palier"] for c in done) / len(done), 1) if done else 0
    total_profit = round(sum(-base for c in done if c["result"] == "lost")
                         + (cap - base if current else 0.0), 2)
    if sim:                                           # SIMULATION -> vitrine de la meilleure série
        featured, hero_cap, hero_pal = best, best_cap, best_pal
        chains_out = sorted(done, key=lambda c: -c["peak"])
    else:                                             # RÉEL -> la montante en cours
        featured, hero_cap, hero_pal = current, cap, palier
        chains_out = list(reversed(done))
    return {
        "active": active, "sim": sim, "base": base,
        "capital": round(hero_cap, 2), "palier": hero_pal, "pending": pending,
        "featured": featured, "chains": chains_out,
        "stats": {"n": len(done), "best_capital": round(best_cap, 2), "best_palier": best_pal,
                  "avg_palier": avg_pal, "total_profit": total_profit},
    }


def state() -> dict:
    """État de la VRAIE montante (fichier de suivi). Vide tant qu'aucun pari n'est enregistré."""
    d = load()
    return _compute(d.get("steps") or [], float(d.get("base_stake") or BASE_STAKE), sim=False)


def foot_simples_bets() -> list:
    """Tous les SIMPLES foot RÉGLÉS et comptés (pari figé `stat_bet`), en ordre chronologique :
    {date, start, match, sel, cote, result}. Source de la SIMULATION de montante — reflète les vraies
    séries de victoires de nos simples foot. Lecture seule."""
    from app import analyses
    out = []
    for d in analyses.iter_meta("foot"):
        sb = d.get("stat_bet")
        if not isinstance(sb, dict) or sb.get("result") not in ("won", "lost", "push", "void"):
            continue
        st = str(d.get("start") or "")
        out.append({"date": st[:10], "start": st,
                    "match": f'{d.get("home", "")} - {d.get("away", "")}'.strip(" -"),
                    "sel": sb.get("sel"), "cote": sb.get("cote"), "result": sb.get("result")})
    out.sort(key=lambda b: b.get("start") or "")
    return out


SIM_WARMUP = 10   # on IGNORE les N premiers paris foot (rodage) dans la simulation — demande user 2026-07-24
                  # (les tout premiers paris ne sont pas représentatifs ; l'historique démarre après).


def simulate(bets: list | None = None) -> dict:
    """Simulation de montante sur une suite de paris (défaut : les simples foot, RODAGE des `SIM_WARMUP`
    premiers exclu). Met en avant la meilleure série. Ne touche à RIEN (pur calcul d'affichage)."""
    src = foot_simples_bets() if bets is None else bets
    if bets is None and len(src) > SIM_WARMUP:
        src = src[SIM_WARMUP:]                             # ne pas compter les premiers paris (rodage)
    return _compute(src, BASE_STAKE, sim=True)


# ===== MÉCANISME D'ENREGISTREMENT (prêt à activer — demande user 2026-07-24) =====
# L'activation est un simple INTERRUPTEUR : créer le fichier data/montante_active.flag (ou
# `tools/montante.py --activate`). Tant qu'il n'existe pas, rien n'est enregistré (la page reste en
# simulation). Une fois actif, la tâche quotidienne (reconcile) règle l'en-cours puis enregistre le pari
# foot le PLUS SÛR du jour.
ACTIVE_FLAG = os.path.join(_ROOT, "data", "montante_active.flag")


def is_active() -> bool:
    return os.path.exists(ACTIVE_FLAG)


def activate(on: bool = True) -> None:
    if on:
        os.makedirs(os.path.dirname(ACTIVE_FLAG), exist_ok=True)
        open(ACTIVE_FLAG, "w", encoding="utf-8").close()
    elif os.path.exists(ACTIVE_FLAG):
        os.remove(ACTIVE_FLAG)


# MARCHÉS ÉLIGIBLES à la montante (demande user 2026-07-25) : marchés POPULAIRES et FIABLES, cote 1.25–1.45.
# On EXCLUT les marchés aléatoires (scores exacts, buteurs). Identifiés par le CODE règlable `code_from_pick`
# (source de vérité du règlement) -> le pari de la montante est réglé sur SON marché, pas sur notre value.
MONT_MIN_ODDS, MONT_MAX_ODDS = 1.25, 1.45


def _montante_eligible_code(code: str) -> bool:
    """Le code de règlement correspond-il à un marché sûr autorisé pour la montante ?
    - Double chance 1X / X2                (DC 1X, DC X2)
    - Plus de 1.5 buts (total match)       (OVER 1.5)
    - Favori gagne à domicile              (1X2 1)  -> filtré en cote 1.25–1.45 = c'est bien le favori
    - Une équipe marque au moins 1 but     (TEAMTOT HOME/AWAY OVER 0.5)
    (Draw No Bet : non distingué proprement dans nos données -> couvert par la double chance / favori.)"""
    c = (code or "").upper().strip()
    return c in ("DC 1X", "DC X2", "OVER 1.5", "1X2 1",
                 "TEAMTOT HOME OVER 0.5", "TEAMTOT AWAY OVER 0.5")


def pick_day_bet() -> dict | None:
    """Le pari foot À VENIR le PLUS SÛR pour la montante (demande user 2026-07-25) : parmi les marchés
    POPULAIRES/FIABLES (`_montante_eligible_code`) en cote 1.25–1.45, celui de confiance CALIBRÉE MAX.
    Source = prédictions du sidecar (fantômes `shadow` + pari retenu `bets`) -> réglées via leur `result`
    (comme la calibration). None si aucun candidat foot à venir. Lecture seule."""
    from app import analyses
    from app.settle_analyst import code_from_pick
    try:
        from app.analyses import calibrated_conf as _cc, _cool_conf as _cool
    except Exception:
        _cc = _cool = None
    best = None
    for d in analyses.iter_meta("foot"):
        if analyses.status_of(d) != "notstarted":          # seulement les matchs pas encore commencés
            continue
        mid = str(d.get("id") or "")
        if not mid:
            continue
        home, away = d.get("home", ""), d.get("away", "")
        preds = list(d.get("shadow") or [])
        for b in (d.get("bets") or []):                    # le pari retenu compte aussi (cote sous `odds`)
            preds.append({"sel": b.get("sel"), "cote": b.get("odds"), "prob": b.get("prob")})
        seen = set()
        for p in preds:
            sel = p.get("sel") or ""
            cote = p.get("cote")
            if not isinstance(cote, (int, float)) or not (MONT_MIN_ODDS <= cote <= MONT_MAX_ODDS):
                continue
            code = (code_from_pick(sel, "foot", home, away) or "").strip()
            if not _montante_eligible_code(code):
                continue
            prob = p.get("prob")
            if not isinstance(prob, (int, float)):
                continue
            pct = prob if prob > 1 else prob * 100.0
            if _cc:                                         # confiance CALIBRÉE + refroidie (le plus sûr)
                try:
                    _c2 = _cool(_cc(pct, "foot", code), "foot", code, d.get("streaks"))
                    if _c2 is not None:
                        pct = _c2
                except Exception:
                    pass
            key = (mid, code)
            if key in seen:                                 # 1 seul candidat par (match, marché) : le meilleur
                continue
            seen.add(key)
            if best is None or pct > best["prob"]:
                best = {"mid": mid, "sport": "foot",
                        "match": d.get("name") or f'{home} - {away}'.strip(" -"),
                        "sel": sel, "cote": float(cote), "code": code, "prob": round(pct, 1),
                        "start": d.get("start") or ""}
    return best


def record_day(date_iso: str) -> bool:
    """Enregistre le pari du jour (1 SEUL par jour). Refuse si un pari est déjà EN ATTENTE (on attend son
    résultat avant d'engager le palier suivant) ou si le jour est déjà enregistré. True si ajouté."""
    d = load()
    steps = d.get("steps") or []
    if any(s.get("result") is None for s in steps):        # un palier non réglé -> on attend
        return False
    if any(s.get("date") == date_iso for s in steps):      # déjà enregistré aujourd'hui
        return False
    pick = pick_day_bet()
    if not pick:
        return False
    steps.append({"date": date_iso, "match": pick["match"], "sel": pick["sel"],
                  "cote": pick["cote"], "mid": pick["mid"], "code": pick.get("code"),
                  "sport": "foot", "result": None})
    d["steps"] = steps
    d.setdefault("base_stake", BASE_STAKE)
    save(d)
    return True


def settle_pending() -> int:
    """Règle les paris en attente de la montante sur LEUR PROPRE marché (le pari de la montante peut différer
    de notre value) : on retrouve la prédiction du même CODE dans le sidecar (stat_bet OU fantôme `shadow`,
    tous deux déjà réglés par nos règlements) et on lit son `result`. Aucune source réseau. Nb réglé."""
    from app import analyses
    from app.settle_analyst import code_from_pick
    _fin = ("won", "lost", "push", "void")
    d = load()
    n = 0
    for s in d.get("steps") or []:
        if s.get("result") is not None or not s.get("mid"):
            continue
        m = analyses.meta("foot", s.get("mid"))
        if not m or not analyses.is_settled(m):
            continue
        home, away = m.get("home", ""), m.get("away", "")
        want = (s.get("code") or code_from_pick(s.get("sel") or "", "foot", home, away) or "").strip().upper()
        if not want:
            continue
        res = None
        # 1) le pari joué (stat_bet) porte-t-il ce marché ? 2) sinon un fantôme réglé du même code.
        sb = m.get("stat_bet") or {}
        if sb.get("sel") and sb.get("result") in _fin:
            if code_from_pick(sb.get("sel"), "foot", home, away).strip().upper() == want:
                res = sb["result"]
        if res is None:
            for p in m.get("shadow") or []:
                if p.get("result") not in _fin:
                    continue
                if code_from_pick(p.get("sel") or "", "foot", home, away).strip().upper() == want:
                    res = p["result"]
                    break
        if res is not None:
            s["result"] = res
            n += 1
    if n:
        save(d)
    return n


def run_daily(date_iso: str) -> dict:
    """Cycle quotidien de la montante (appelé par la tâche reconcile si ACTIVÉE) : régler l'en-cours puis
    enregistrer le pari foot du jour. No-op si l'interrupteur est éteint."""
    if not is_active():
        return {"active": False}
    settled = settle_pending()
    added = record_day(date_iso)
    return {"active": True, "settled": settled, "recorded": added}


def example() -> dict:
    """Montante d'EXEMPLE (aperçu premium de la page avant activation) — purement illustrative, jamais
    enregistrée. 4 paliers gagnés à partir de 10 €."""
    demo = [
        {"date": "J1", "match": "Exemple A – B", "sel": "Double chance 1X", "cote": 1.42, "result": "won"},
        {"date": "J2", "match": "Exemple C – D", "sel": "Plus de 1.5 but", "cote": 1.35, "result": "won"},
        {"date": "J3", "match": "Exemple E – F", "sel": "Favori gagne à domicile", "cote": 1.40, "result": "won"},
        {"date": "J4", "match": "Exemple G – H", "sel": "Équipe marque (+0.5 but)", "cote": 1.30, "result": "won"},
    ]
    chain = _split_chains(demo, BASE_STAKE)[0]
    return chain
