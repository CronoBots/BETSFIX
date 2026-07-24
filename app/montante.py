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


def state() -> dict:
    """État complet pour l'affichage. Toujours renvoyé (même vide) :
    - active        : au moins un pari enregistré ?
    - base          : mise de départ (10 €)
    - capital       : capital ENGAGÉ sur le prochain palier de la montante en cours (base si à zéro)
    - palier        : nb de gains consécutifs de la montante en cours
    - pending       : le pari du jour non réglé (dict) ou None
    - current       : la chaîne en cours (dict de _finish_chain) ou None
    - chains         : toutes les montantes terminées (plus récentes d'abord)
    - stats         : {n, best_capital, best_palier, avg_palier, total_profit}
    """
    d = load()
    base = float(d.get("base_stake") or BASE_STAKE)
    steps = d.get("steps") or []
    chains = _split_chains(steps, base)
    active = bool(steps)
    current = chains[-1] if chains and chains[-1]["result"] == "pending" else None
    done = [c for c in chains if c["result"] != "pending"]
    # capital engagé sur le prochain palier de la chaîne en cours
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
    # profit simulé cumulé = somme, par chaîne terminée, de (pic si close-out — ici perte => -base) ;
    # simplifié : profit d'une montante perdue = -base (on ne perd QUE la mise de départ) ; en cours = latent.
    total_profit = round(sum(-base for c in done if c["result"] == "lost"), 2)
    return {
        "active": active, "base": base, "capital": round(cap, 2), "palier": palier,
        "pending": pending, "current": current,
        "chains": list(reversed(done)),
        "stats": {"n": len(done), "best_capital": round(best_cap, 2), "best_palier": best_pal,
                  "avg_palier": avg_pal, "total_profit": total_profit},
    }


def example() -> dict:
    """Montante d'EXEMPLE (aperçu premium de la page avant activation) — purement illustrative, jamais
    enregistrée. 4 paliers gagnés à partir de 10 €."""
    demo = [
        {"date": "J1", "match": "Exemple A – B", "sel": "Double chance 1X", "cote": 1.45, "result": "won"},
        {"date": "J2", "match": "Exemple C – D", "sel": "Moins de 3.5 buts", "cote": 1.40, "result": "won"},
        {"date": "J3", "match": "Exemple E – F", "sel": "Équipe 1 gagne", "cote": 1.55, "result": "won"},
        {"date": "J4", "match": "Exemple G – H", "sel": "Plus de 1.5 but", "cote": 1.35, "result": "won"},
    ]
    chain = _split_chains(demo, BASE_STAKE)[0]
    return chain
