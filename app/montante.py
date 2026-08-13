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
        # COHÉRENCE (bug user 2026-07-28 « pourquoi 13 paliers ? ») : le HERO doit décrire la MÊME chaîne que
        # celle affichée (`featured` = plus haut CAPITAL). Avant, hero_pal = best_pal (RECORD de paliers, pris
        # sur une AUTRE chaîne plus longue mais à capital plus bas) -> « 313,51 € » (chaîne à 12 paliers) mais
        # « 13 paliers » (chaîne à 215 €). On prend le palier de la chaîne FEATURED. Le record reste en stats.
        featured, hero_cap, hero_pal = best, best_cap, (best["palier"] if best else 0)
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


def montante_mids() -> set:
    """IDs de match de TOUS les paris montante (paliers enregistrés + palier en attente). Sert à FORCER
    le tier « confiance » de la montante — le pari sûr du jour (demande user 2026-08-11). Lecture seule."""
    out = set()
    try:
        for s in (load().get("steps") or []):
            if isinstance(s, dict) and s.get("mid"):
                out.add(str(s.get("mid")))
        pend = (state() or {}).get("pending") or {}
        if pend.get("mid"):
            out.add(str(pend.get("mid")))
    except Exception:
        pass
    return out


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


def _best_per_day(bets: list) -> list:
    """RÉALISME (demande user 2026-07-26) : une montante = **1 pari par jour** (on ne peut PAS rejouer le gain
    d'un pari sur un AUTRE pari du même jour — ils se jouent en même temps). On garde donc UN seul simple foot
    par JOUR SPORTIF (06h→06h) : le plus SÛR = cote la plus basse (logique montante). Chronologique. Sans ça,
    la simu enchaînait TOUS les simples (30/36 jours en avaient plusieurs) = 2 paliers le même jour = faux."""
    from datetime import datetime
    from app import web as _w
    byday: dict = {}
    for b in bets:
        try:
            day = _w._sport_date(_w.to_local(
                datetime.fromisoformat((b.get("start") or "").replace("Z", "+00:00")))).isoformat()
        except (ValueError, AttributeError, TypeError):
            day = (b.get("start") or "")[:10]
        cur = byday.get(day)
        if cur is None or (b.get("cote") or 99) < (cur.get("cote") or 99):   # le + sûr du jour
            byday[day] = b
    return [byday[d] for d in sorted(byday)]


SIM_SNAPSHOT = os.path.join(_ROOT, "data", "montante_sim_snapshot.json")


def _sim_frozen_sequence() -> list:
    """Séquence FIGÉE (1 pari/jour) de la simulation montante — APPEND-ONLY PAR DATE (demande user 2026-08-02).
    Une fois qu'un JOUR SPORTIF a son pari retenu (le plus sûr, réglé), il ne change PLUS, même si un pari se
    règle EN RETARD (match décalé) et s'ajoute à un jour passé. Sans ça, `_best_per_day` re-choisissait le pari
    d'un jour passé -> re-découpage des chaînes -> l'« Historique des montantes » valsait de jour en jour.
    Le fond de vérité = `data/montante_sim_snapshot.json` (isolé, hors ROI) : on n'y AJOUTE que des jours
    nouveaux, jamais on ne modifie un jour déjà figé. Les chaînes dérivées de cette séquence sont donc STABLES."""
    src = foot_simples_bets()
    if len(src) > SIM_WARMUP:
        src = src[SIM_WARMUP:]                             # rodage exclu (comme avant)
    live = _best_per_day(src)                              # 1 pari/jour (le plus sûr) — vue LIVE
    try:
        with open(SIM_SNAPSHOT, encoding="utf-8") as f:
            snap = json.load(f)
        frozen = snap.get("days") if isinstance(snap, dict) else {}
    except (OSError, ValueError):
        frozen = {}
    if not isinstance(frozen, dict):
        frozen = {}
    changed = False
    for b in live:
        day = b.get("date") or (b.get("start") or "")[:10]
        if not day or day in frozen:                       # jour DÉJÀ figé -> intouchable (stabilité)
            continue
        if b.get("result") in ("won", "lost", "push", "void"):   # on ne fige qu'un jour RÉGLÉ
            frozen[day] = {"date": day, "start": b.get("start"), "match": b.get("match"),
                           "sel": b.get("sel"), "cote": b.get("cote"), "result": b.get("result")}
            changed = True
    if changed:
        try:
            os.makedirs(os.path.dirname(SIM_SNAPSHOT), exist_ok=True)
            tmp = SIM_SNAPSHOT + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"days": frozen}, f, ensure_ascii=False)
            os.replace(tmp, SIM_SNAPSHOT)
        except OSError:
            pass
    return [frozen[d] for d in sorted(frozen)]


def simulate(bets: list | None = None) -> dict:
    """Simulation de montante (défaut : les simples foot joués). MÊME logique que la vraie montante :
    RODAGE des `SIM_WARMUP` premiers exclu, puis **1 pari/jour** — mais la séquence est désormais FIGÉE
    (`_sim_frozen_sequence`, append-only par date) -> l'historique de la simulation ne valse PLUS d'un jour à
    l'autre (demande user 2026-08-02). Met en avant la meilleure série. Pur affichage, hors ROI."""
    src = _sim_frozen_sequence() if bets is None else bets
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
    """Le palier du jour = le SIMPLE VALUE JOUÉ le plus SÛR des matchs foot à venir (demande user 2026-07-26).
    ÉLIGIBLE-SEULEMENT (user 2026-08-13) : on n'engage la montante QUE sur les marchés SÛRS
    (`_montante_eligible_code` : DC / +1.5 buts / favori domicile 1X2 / équipe marque — mesurés à 88 % de
    réussite RÉELLE). PLUS de repli sur les marchés inéligibles (totaux Over/Under…) : si aucun pari éligible
    à venir ce jour, la montante SAUTE le jour (None) plutôt que d'engager un pari moins sûr. Dans le pool
    éligible, le PLUS CONFIANT (confiance calibrée la plus haute ; à égalité, cote la plus basse). Le palier
    EST un pari value du jour (réglé via son `stat_bet`). None si aucun simple value éligible. Lecture seule."""
    from app import analyses
    from app.settle_analyst import code_from_pick
    cands = []
    for d in analyses.iter_meta("foot"):
        if analyses.status_of(d) != "notstarted":          # seulement les matchs pas encore commencés
            continue
        mid = str(d.get("id") or "")
        if not mid:
            continue
        # PARI *JOUÉ* SEULEMENT : la montante prend un pari RETENU/publié (celui de « Paris à jouer »), pas une
        # ABSTENTION. On se fie à `retained_bet` (source unique) qui renvoie DÉJÀ None sur une abstention (garde
        # e182d3c). ⚠️ NE PAS exiger un champ `bets` structuré : beaucoup de paris joués sont stockés en `pick`
        # (sans `bets`) -> l'ancien filtre `not d.get("bets")` les EXCLUAIT à tort et la montante ne prenait plus
        # de palier (bug user 2026-08-02 : FC Midtjylland « gagne @1.32 » retenu mais `bets`=None -> sauté).
        rb = analyses.retained_bet("foot", mid) or analyses.published_bet("foot", mid)
        if not rb or not rb.get("sel") or rb.get("result") in ("won", "lost", "push"):
            continue
        cote = rb.get("cote")
        if not isinstance(cote, (int, float)):
            continue
        _code = rb.get("code") or code_from_pick(rb.get("sel", ""), "foot", d.get("home", ""), d.get("away", ""))
        cands.append({"mid": mid, "sport": "foot",
                      "match": d.get("name") or f'{d.get("home", "")} - {d.get("away", "")}'.strip(" -"),
                      "sel": rb.get("sel"), "cote": float(cote), "code": _code or "",
                      "prob": rb.get("cprob") or rb.get("prob"), "start": d.get("start") or "",
                      "_elig": _montante_eligible_code(_code)})
    if not cands:
        return None
    # SÉLECTION (demande user 2026-08-02) : parmi les PARIS SIMPLES À JOUER (déjà value/retenus), on prend le
    # PLUS CONFIANT (confiance calibrée la plus HAUTE). À confiance égale, marché fiable puis cote la plus
    # basse (le plus sûr). Le palier est ainsi TOUJOURS un pari joué remplissant les conditions demandées.
    pool = [c for c in cands if c["_elig"]]            # ÉLIGIBLE-SEULEMENT : plus de repli sur les inéligibles
    if not pool:                                       # aucun marché sûr éligible ce jour -> pas de montante
        return None
    best = max(pool, key=lambda c: ((c.get("prob") or 0), -c["cote"]))
    best.pop("_elig", None)
    return best


def record_day(date_iso: str) -> bool:
    """Enregistre le pari du jour (1 SEUL par jour). Refuse si un pari est déjà EN ATTENTE (on attend son
    résultat avant d'engager le palier suivant) ou si le jour est déjà enregistré. True si ajouté."""
    d = load()
    # DÉMARRAGE au scan du LENDEMAIN de l'activation (demande user 2026-07-25) : `start_date` = jour
    # d'activation ; on n'enregistre qu'à partir du jour STRICTEMENT suivant (le 1er palier vient d'un
    # vrai scan quotidien, pas d'un ajout manuel le jour même).
    _sd = d.get("start_date") or ""
    if _sd and date_iso <= _sd:
        return False
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
    """Règle les paliers en attente : le palier = le SIMPLE VALUE JOUÉ du match, donc son résultat = celui du
    `stat_bet` figé (déjà calculé par nos règlements, aucune source réseau). Nb réglé/corrigé.

    AUTO-CORRECTION (user 2026-08-06 « le palier 7 a été perdu ») : un palier DÉJÀ réglé est re-vérifié —
    si le `stat_bet` du sidecar a CHANGÉ depuis (ex. faux résultat corrigé : Fluminense 0-0→1-3, le pari
    « Moins de 2.5 » passe won→lost), la montante SUIT. L'échelle est recalculée en aval (`_compute` casse la
    chaîne sur une perte). Sans ça, un palier gagné à tort restait « won » et la montante continuait à tort."""
    from app import analyses
    d = load()
    n = 0
    for s in d.get("steps") or []:
        if not s.get("mid"):
            continue
        m = analyses.meta("foot", s.get("mid"))
        if not m or not analyses.is_settled(m):
            continue
        sb = m.get("stat_bet") or {}
        r = sb.get("result")
        if r not in ("won", "lost", "push", "void") or s.get("result") == r:
            continue                                        # pas réglé côté sidecar, OU déjà à jour
        s["result"] = r                                     # nouveau règlement OU correction (la montante suit)
        if sb.get("cote"):
            s["cote"] = sb["cote"]                          # cote finale figée
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
