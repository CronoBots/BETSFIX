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

import html
import json
import math
import os

from app import web

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


def upsert_prediction(store: dict, analysis, tour: str, now_iso: str,
                      start_time_iso: str | None = None) -> bool:
    """Crée/rafraîchit la prédiction d'un match à venir. Renvoie True si modifié."""
    key = str(analysis.match_id)
    rec = store.get(key, {})
    if rec.get("result"):  # déjà réglé : on ne touche plus
        return False

    value = next((v for v in analysis.value_bets if v.is_value), None)
    rec.update({
        "match_id": analysis.match_id,
        "tour": tour,
        "start_time": start_time_iso,
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


# --------------------------------------------------------------- dashboard
def _pct(x):
    return f"{round(x * 100)}%" if isinstance(x, (int, float)) else "—"


def render_dashboard(store: dict, rep: dict) -> str:
    """Page HTML mobile-friendly récapitulant performance + paris suivis."""
    e = html.escape
    recs = list(store.values())
    pending_value = [r for r in recs if not r.get("result") and r.get("value_pick")]
    pending_value.sort(key=lambda r: -(r["value_pick"].get("edge") or 0))
    settled = [r for r in recs if r.get("result")]
    settled.sort(key=lambda r: r["result"].get("settled_at", ""), reverse=True)

    roi = rep.get("value_roi")
    roi_color = "#9aa0a6" if roi is None else ("#34a853" if roi > 0 else "#ea4335")
    roi_txt = "—" if roi is None else f"{'+' if roi >= 0 else ''}{round(roi * 100, 1)}%"
    pnl = rep.get("value_pnl_unites", 0.0)

    def card(label, value, sub="", color="#e8eaed"):
        return (f'<div class="card"><div class="lbl">{e(label)}</div>'
                f'<div class="val" style="color:{color}">{e(str(value))}</div>'
                f'<div class="sub">{e(sub)}</div></div>')

    cards = "".join([
        card("ROI value", roi_txt, f"{rep.get('value_paris_regles', 0)} paris réglés", roi_color),
        card("P&L", f"{'+' if pnl >= 0 else ''}{pnl} u", "mise plate 1u"),
        card("Réussite value", _pct(rep.get("value_taux_reussite")),
             f"{rep.get('value_gagnes', 0)} gagnés"),
        card("Précision modèle", _pct(rep.get("precision_modele")),
             f"{rep.get('predictions_evaluees', 0)} matchs"),
        card("Brier", rep.get("brier") if rep.get("brier") is not None else "—", "plus bas = mieux"),
        card("Suivis", rep.get("matchs_suivis", 0), f"{rep.get('matchs_regles', 0)} réglés"),
    ])

    def pending_row(r):
        v = r["value_pick"]
        edge = round((v.get("edge") or 0) * 100, 1)
        return (f'<tr><td>{e(r["home"])}<br><span class="dim">v {e(r["away"])}</span></td>'
                f'<td><b>{e(v["player"])}</b><br><span class="dim">@ {v["odds"]}</span></td>'
                f'<td class="pos">+{edge}pts<br><span class="dim">{v.get("stake_pct")}%</span></td></tr>')

    def settled_row(r):
        res = r["result"]
        v = r.get("value_pick")
        if not v:
            outcome = "—"
        else:
            won = v["side"] == res["winner"]
            pnl_v = res.get("value_pnl")
            outcome = (f'<span class="pos">✓ +{pnl_v}u</span>' if won
                       else f'<span class="neg">✗ {pnl_v}u</span>')
        winner_name = r["home"] if res["winner"] == "home" else r["away"]
        pick_txt = e(v["player"] + " @" + str(v["odds"])) if v else "—"
        return (f'<tr><td>{e(r["home"])} v {e(r["away"])}<br>'
                f'<span class="dim">vainqueur : {e(winner_name)}</span></td>'
                f'<td>{pick_txt}</td><td>{outcome}</td></tr>')

    pending_html = ("".join(pending_row(r) for r in pending_value)
                    or '<tr><td colspan="3" class="dim">Aucun pari value en attente.</td></tr>')
    settled_html = ("".join(settled_row(r) for r in settled[:30])
                    or '<tr><td colspan="3" class="dim">Aucun match réglé pour l\'instant.</td></tr>')

    body = f"""<div class="grid">{cards}</div>
<div class="banner">⚠️ {e(rep.get("note", ""))} Le ROI n'est fiable qu'au-delà de 100 paris réglés.
 Ne pas conclure trop tôt. Jouez responsable.</div>
<h2>Paris value en attente ({len(pending_value)})</h2>
<table>{pending_html}</table>
<h2>Derniers résultats</h2>
<table>{settled_html}</table>"""
    return web.layout("Performance", "perf", body, refresh=True)


def render_today(store: dict) -> str:
    """Page 'Matchs à venir' : toutes les analyses suivies, triées par heure."""
    e = html.escape
    upcoming = [r for r in store.values() if not r.get("result")]
    upcoming.sort(key=lambda r: r.get("start_time") or "")

    def hhmm(iso):
        return iso[11:16] if iso and len(iso) >= 16 else "—"

    def row(r):
        hp = r.get("model_home_prob")
        if hp is None:
            fav, favp = "—", "—"
        elif hp >= 0.5:
            fav, favp = r["home"], _pct(hp)
        else:
            fav, favp = r["away"], _pct(1 - hp)
        v = r.get("value_pick")
        if v:
            edge = round((v.get("edge") or 0) * 100, 1)
            pick = f'<b class="pos">{e(v["player"])}</b> @{v["odds"]}<br><span class="dim">+{edge}pts · {v.get("stake_pct")}%</span>'
        else:
            pick = '<span class="dim">—</span>'
        tag = r.get("tour", "").upper()
        return (f'<tr><td>{hhmm(r.get("start_time"))}<br><span class="dim">{tag}</span></td>'
                f'<td>{e(r["home"])}<br>{e(r["away"])}<br>'
                f'<span class="dim">fav : {e(fav)} {favp} · conf {e(r.get("confidence") or "—")}</span></td>'
                f'<td>{pick}</td></tr>')

    rows = ("".join(row(r) for r in upcoming)
            or '<tr><td colspan="3" class="dim">Aucun match à venir suivi pour le moment.</td></tr>')
    body = (f'<div class="banner">Analyses des matchs à venir (≤ 48 h) avec cotes Unibet. '
            f'Heure UTC. Une "value" = avis du modèle, à confirmer par le suivi.</div>'
            f'<h2>Matchs à venir ({len(upcoming)})</h2>'
            f'<table><tr><td class="dim">Heure</td><td class="dim">Match</td>'
            f'<td class="dim">Value</td></tr>{rows}</table>')
    return web.layout("Matchs à venir", "matches", body, refresh=True)
