"""Suivi des prédictions vs résultats réels — pour MESURER si le modèle gagne.

On enregistre, pour chaque match à venir : la proba du modèle, les cotes Unibet
(rafraîchies jusqu'au coup d'envoi ≈ cote de clôture) et la 'value' éventuelle.
Après le match, on note le résultat. Le rapport calcule alors des métriques
honnêtes : calibration (Brier/log-loss sur résultats réels), taux de réussite et
**ROI** des paris value — le seul juge de la rentabilité.

Stockage : data/tracking.json (dict indexé par match_id). Fonctions de calcul
pures et testables ; l'orchestration réseau est dans le routeur.
"""

from __future__ import annotations

import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(_ROOT, "data", "tracking.json")


def load(path: str = DATA_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save(store: dict, path: str = DATA_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def upsert_prediction(store: dict, analysis, tour: str, now_iso: str) -> bool:
    """Crée/rafraîchit la prédiction d'un match à venir. Renvoie True si modifié."""
    key = str(analysis.match_id)
    rec = store.get(key, {})
    if rec.get("result"):  # déjà réglé : on ne touche plus
        return False

    value = next((v for v in analysis.value_bets if v.is_value), None)
    rec.update({
        "match_id": analysis.match_id,
        "tour": tour,
        "home": analysis.home.name,
        "away": analysis.away.name,
        "model_home_prob": analysis.model_home_probability,
        "confidence": analysis.confidence,
        "unibet_home_odds": _odds_for(analysis, "home"),
        "unibet_away_odds": _odds_for(analysis, "away"),
        "value_pick": ({
            "side": value.side, "player": value.player, "odds": value.odds,
            "edge": value.edge, "stake_pct": value.recommended_stake_pct,
        } if value else None),
        "last_update": now_iso,
    })
    rec.setdefault("first_logged", now_iso)
    store[key] = rec
    return True


def _odds_for(analysis, side: str):
    for v in analysis.value_bets:
        if v.side == side:
            return v.odds
    return None


def settle(store: dict, match_id: int, winner: str | None, total_games: int | None,
           now_iso: str) -> bool:
    """Enregistre le résultat réel d'un match suivi. Renvoie True si réglé."""
    rec = store.get(str(match_id))
    if not rec or rec.get("result") or winner not in ("home", "away"):
        return False
    pick = rec.get("value_pick")
    pnl = None
    if pick and pick.get("odds"):
        won = pick["side"] == winner
        pnl = (pick["odds"] - 1) if won else -1.0  # mise plate de 1 unité
    rec["result"] = {
        "winner": winner, "total_games": total_games, "settled_at": now_iso,
        "value_pnl": pnl,
    }
    store[str(match_id)] = rec
    return True


# --------------------------------------------------------------- rapport
def report(store: dict) -> dict:
    settled = [r for r in store.values() if r.get("result")]
    pred = [r for r in settled if r.get("model_home_prob") is not None]

    # Calibration / précision du modèle sur résultats réels
    brier = ll = 0.0
    correct = 0
    for r in pred:
        p = min(max(r["model_home_prob"], 1e-6), 1 - 1e-6)
        y = 1 if r["result"]["winner"] == "home" else 0
        brier += (p - y) ** 2
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        if (p >= 0.5) == (y == 1):
            correct += 1
    n = len(pred)

    # Performance des paris 'value'
    picks = [r for r in settled if r.get("value_pick") and r["result"].get("value_pnl") is not None]
    pnl = sum(r["result"]["value_pnl"] for r in picks)
    wins = sum(1 for r in picks if r["result"]["value_pnl"] > 0)

    return {
        "matchs_suivis": len(store),
        "matchs_regles": len(settled),
        "predictions_evaluees": n,
        "precision_modele": round(correct / n, 3) if n else None,
        "brier": round(brier / n, 4) if n else None,
        "log_loss": round(ll / n, 4) if n else None,
        "value_paris_regles": len(picks),
        "value_gagnes": wins,
        "value_taux_reussite": round(wins / len(picks), 3) if picks else None,
        "value_pnl_unites": round(pnl, 2) if picks else 0.0,
        "value_roi": round(pnl / len(picks), 3) if picks else None,
        "note": (
            "Échantillon trop faible pour conclure (vise 100+ paris réglés)."
            if len(picks) < 100 else
            "ROI positif = le modèle bat le marché sur l'échantillon."
        ),
    }
