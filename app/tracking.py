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
import logging
import math
import os

from app import web
from app.analysis import remove_vig

log = logging.getLogger("uvicorn")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(_ROOT, "data", "tracking.json")


# Cache mémoire du store invalidé par la date de modif (mtime) : le store ne change que
# toutes les ~3h (boucle de suivi), mais load() est appelé ~6-7×/requête. Tant que le
# fichier n'a pas changé, on évite le re-parse JSON (gros gain CPU/IO, 0 risque de péremption).
_load_cache: dict[str, tuple[float, dict]] = {}


def load(path: str = DATA_PATH) -> dict:
    """Charge le store de suivi (avec cache mtime). Un fichier CORROMPU est sauvegardé en .bak
    (jamais écrasé silencieusement par {}), pour ne pas perdre l'historique sans trace."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _load_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            store = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError:
        bak = path + ".corrupt.bak"
        try:
            os.replace(path, bak)
            log.error("tracking: %s corrompu -> sauvegardé en %s (repart de zéro)", path, bak)
        except OSError as exc:
            log.error("tracking: %s corrompu et non sauvegardable: %s", path, exc)
        return {}
    _load_cache[path] = (mtime, store)
    return store


def save(store: dict, path: str = DATA_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:   # amorce le cache pour éviter une relecture immédiate
        _load_cache[path] = (os.path.getmtime(path), store)
    except OSError:
        _load_cache.pop(path, None)


def upsert_prediction(store: dict, analysis, tour: str, now_iso: str,
                      start_time_iso: str | None = None) -> bool:
    """Crée/rafraîchit la prédiction d'un match à venir. Renvoie True si modifié."""
    key = str(analysis.match_id)
    rec = store.get(key, {})
    if rec.get("result"):  # déjà réglé : on ne touche plus
        return False

    value = next((v for v in analysis.value_bets if v.is_value), None)
    home_odds, away_odds = _odds_for(analysis, "home"), _odds_for(analysis, "away")
    rec.update({
        "match_id": analysis.match_id,
        "tour": tour,
        "start_time": start_time_iso,
        "home": analysis.home.name,
        "away": analysis.away.name,
        "model_home_prob": analysis.model_home_probability,
        "confidence": analysis.confidence,
        # Surface (terre/dur/gazon) : permet d'analyser la perf par type de court.
        "surface": analysis.ground_type,
        # Détail par facteur (Elo, classement, forme, surface, h2h) : pour savoir
        # APRÈS COUP quel facteur prédit bien et lequel nuit. Sans ça, on ne garde
        # que la proba finale et on ne peut rien diagnostiquer.
        "factors": [
            {"name": f.name, "home": f.home, "weight": f.weight}
            for f in analysis.factors
        ],
        # Cote courante : à mesure qu'on rafraîchit jusqu'au coup d'envoi, ce champ
        # converge vers la cote de CLÔTURE (la plus efficiente).
        "unibet_home_odds": home_odds,
        "unibet_away_odds": away_odds,
        "value_pick": ({
            "side": value.side, "player": value.player, "odds": value.odds,
            "edge": value.edge, "stake_pct": value.recommended_stake_pct,
        } if value else None),
        "last_update": now_iso,
    })
    rec.setdefault("first_logged", now_iso)
    # Cote d'OUVERTURE : figée au tout premier log (sert au calcul du CLV).
    rec.setdefault("open_home_odds", home_odds)
    rec.setdefault("open_away_odds", away_odds)
    store[key] = rec
    return True


def _odds_for(analysis, side: str):
    for v in analysis.value_bets:
        if v.side == side:
            return v.odds
    return None


def settle(store: dict, match_id: int, winner: str | None, total_games: int | None,
           now_iso: str, sets_home: int | None = None, sets_away: int | None = None,
           score: str | None = None) -> bool:
    """Enregistre le résultat réel d'un match suivi. Renvoie True si réglé.
    Règle aussi les PERLES (confiance + 2e + value) -> alimente « BETSFIX bat le marché ?»."""
    rec = store.get(str(match_id))
    if not rec or rec.get("result") or winner not in ("home", "away"):
        return False
    pick = rec.get("value_pick")
    pnl = None
    if pick and pick.get("odds"):
        won = pick["side"] == winner
        pnl = (pick["odds"] - 1) if won else -1.0  # mise plate de 1 unité
    # 🎯 P&L des perles tennis (None = marché non vérifiable -> exclu des stats)
    from app.markets import settle_tennis_perle

    def _pp(p):
        return settle_tennis_perle(p, winner, sets_home, sets_away, total_games,
                                   rec.get("home", ""), rec.get("away", ""), score)
    rec["result"] = {
        "winner": winner, "total_games": total_games, "settled_at": now_iso,
        "value_pnl": pnl,
        "perle_pnl": _pp(rec.get("perle")),
        "perle2_pnl": _pp(rec.get("perle2")),
        "perle_value_pnl": _pp(rec.get("perle_value")),
    }
    store[str(match_id)] = rec
    return True


def void(store: dict, match_id: int, reason: str, now_iso: str) -> bool:
    """Clôt un match qui n'aboutira pas (reporté/annulé/abandon) sans gagnant.

    Sans ça, un match jamais « finished » reste indéfiniment dans le store, ré-essayé à
    chaque passe et le faisant grossir sans fin. Un void est exclu des métriques (pas de
    gagnant, pas de P&L) mais marque le match comme réglé pour qu'on cesse de le suivre."""
    rec = store.get(str(match_id))
    if not rec or rec.get("result"):
        return False
    rec["result"] = {"winner": None, "void": True, "reason": reason,
                     "settled_at": now_iso, "value_pnl": None}
    store[str(match_id)] = rec
    return True


# --------------------------------------------------------------- rapport
def _market_home_prob(rec: dict) -> float | None:
    """Proba implicite (vig retirée) du marché de clôture pour 'home'."""
    devig = remove_vig(rec.get("unibet_home_odds"), rec.get("unibet_away_odds"))
    return devig[0] if devig else None


def clv_pct(rec: dict) -> float | None:
    """CLV du favori du modèle : cote d'ouverture vs clôture, en proba.

    >0 = la cote prise à l'ouverture battait la clôture (on a anticipé le marché).
    C'est le juge d'edge le plus rapide : pas besoin d'attendre le résultat, juste la
    cote de clôture (≈ dernier rafraîchissement avant le coup d'envoi).
    """
    hp = rec.get("model_home_prob")
    if hp is None:
        return None
    side = "home" if hp >= 0.5 else "away"
    op, cl = rec.get(f"open_{side}_odds"), rec.get(f"unibet_{side}_odds")
    if not op or not cl or op <= 1 or cl <= 1:
        return None
    return op / cl - 1.0


def calibration_table(pred: list[dict], bins: int = 5) -> list[dict]:
    """Proba prédite (côté favori) vs taux réel, par tranche. Pour la courbe de calib."""
    buckets = [[0, 0.0, 0] for _ in range(bins)]  # [n, somme_proba_fav, victoires_fav]
    for r in pred:
        hp = r["model_home_prob"]
        fav_home = hp >= 0.5
        pfav = hp if fav_home else 1 - hp
        fav_won = (r["result"]["winner"] == "home") == fav_home
        i = min(int(pfav * bins), bins - 1)
        buckets[i][0] += 1
        buckets[i][1] += pfav
        buckets[i][2] += 1 if fav_won else 0
    out = []
    for i, (cnt, sp, w) in enumerate(buckets):
        if cnt:
            out.append({"label": f"{int(i/bins*100)}-{int((i+1)/bins*100)}%",
                        "n": cnt, "predit": sp / cnt, "reel": w / cnt})
    return out


def _fav_metrics(pred: list[dict]) -> dict:
    """Métriques côté 'favori du modèle' sur un sous-ensemble de matchs réglés.

    pred_fav = proba moyenne annoncée au favori ; reel_fav = taux réel de victoire du
    favori. Quand pred_fav > reel_fav, le modèle est **surconfiant** sur ce sous-groupe.
    """
    n = len(pred)
    if not n:
        return {"n": 0, "precision": None, "brier": None, "pred_fav": None,
                "reel_fav": None, "surconfiance": None}
    correct = brier = pred_sum = real_sum = 0.0
    for r in pred:
        hp = min(max(r["model_home_prob"], 1e-6), 1 - 1e-6)
        fav_home = hp >= 0.5
        pfav = hp if fav_home else 1 - hp
        fav_won = (r["result"]["winner"] == "home") == fav_home
        brier += (pfav - (1.0 if fav_won else 0.0)) ** 2
        correct += 1.0 if fav_won else 0.0
        pred_sum += pfav
        real_sum += 1.0 if fav_won else 0.0
    return {
        "n": n,
        "precision": round(correct / n, 3),
        "brier": round(brier / n, 4),
        "pred_fav": round(pred_sum / n, 3),
        "reel_fav": round(real_sum / n, 3),
        "surconfiance": round(pred_sum / n - real_sum / n, 3),
    }


def _pick_type(r: dict) -> str:
    """Catégorie du pari (mutuellement exclusive) : Value (edge) > Confiance (favori net
    ≥ 65 %) > Autre. Sert à séparer les résultats par type de signal du modèle."""
    if r.get("value_pick"):
        return "Value"
    mh = r.get("model_home_prob")
    if mh is not None and max(mh, 1 - mh) >= 0.65:
        return "Confiance"
    return "Autre"


def breakdown(pred: list[dict], keyfn, order: list | None = None) -> list[dict]:
    """Découpe les prédictions par clé (surface, tour, confiance) + métriques par groupe."""
    groups: dict = {}
    for r in pred:
        k = keyfn(r)
        if k is None:
            continue
        groups.setdefault(k, []).append(r)
    keys = [k for k in (order or sorted(groups)) if k in groups]
    out = []
    for k in keys:
        m = _fav_metrics(groups[k])
        m["label"] = k
        out.append(m)
    return out


def surface_label(rec: dict) -> str | None:
    """Normalise la surface stockée en libellé court (terre/dur/gazon/autre)."""
    g = (rec.get("surface") or "").lower()
    if not g:
        return None
    if "clay" in g:
        return "terre"
    if "grass" in g:
        return "gazon"
    if "hard" in g:
        return "dur"
    return "autre"


def factor_breakdown(pred: list[dict]) -> list[dict]:
    """Précision/Brier de CHAQUE facteur pris isolément (comme s'il décidait seul).

    C'est le diagnostic clé pour améliorer le modèle : si un facteur a un Brier pire
    que 0.25 (= pile ou face) ou une précision < 50 %, il dégrade le mélange et son
    poids devrait baisser. Trié du meilleur (Brier le plus bas) au pire.
    """
    acc: dict = {}
    for r in pred:
        y = 1 if r["result"]["winner"] == "home" else 0
        for f in r.get("factors") or []:
            h = f.get("home")
            if h is None:
                continue
            d = acc.setdefault(f.get("name") or "?",
                               {"n": 0, "correct": 0, "brier": 0.0, "w": 0.0})
            hc = min(max(h, 1e-6), 1 - 1e-6)
            d["n"] += 1
            d["correct"] += 1 if (hc >= 0.5) == (y == 1) else 0
            d["brier"] += (hc - y) ** 2
            d["w"] += f.get("weight") or 0.0
    out = [{"name": k, "n": d["n"],
            "precision": round(d["correct"] / d["n"], 3),
            "brier": round(d["brier"] / d["n"], 4),
            "poids": round(d["w"] / d["n"], 3)}
           for k, d in acc.items() if d["n"]]
    out.sort(key=lambda x: x["brier"])
    return out


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Intervalle de confiance 95% (score de Wilson) sur un taux de réussite binomial.
    Honnête sur petit échantillon : N=10 donne un intervalle large, N=200 un intervalle serré.
    Renvoie (borne_basse, borne_haute) en proportions, ou None si aucun pari."""
    if not n:
        return None
    p = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def report(store: dict) -> dict:
    settled = [r for r in store.values() if r.get("result")]
    # Les void (matchs annulés/reportés, sans gagnant) sont exclus des métriques.
    pred = [r for r in settled
            if r.get("model_home_prob") is not None and not r["result"].get("void")]

    # Calibration / précision du modèle ET baseline marché, sur résultats réels
    brier = ll = 0.0
    mkt_brier = mkt_ll = 0.0
    correct = mkt_n = 0
    for r in pred:
        p = min(max(r["model_home_prob"], 1e-6), 1 - 1e-6)
        y = 1 if r["result"]["winner"] == "home" else 0
        brier += (p - y) ** 2
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        if (p >= 0.5) == (y == 1):
            correct += 1
        # Baseline : le marché (cotes de clôture dévig) prédit-il mieux ?
        mp = _market_home_prob(r)
        if mp is not None:
            mp = min(max(mp, 1e-6), 1 - 1e-6)
            mkt_brier += (mp - y) ** 2
            mkt_ll += -(y * math.log(mp) + (1 - y) * math.log(1 - mp))
            mkt_n += 1
    n = len(pred)

    # CLV (closing line value) du favori du modèle — juge d'edge sans attendre N matchs
    clvs = [c for c in (clv_pct(r) for r in pred) if c is not None]

    # Performance des paris 'value' (ANCIEN value_pick — gardé pour le dashboard détaillé)
    picks = [r for r in settled if r.get("value_pick") and r["result"].get("value_pnl") is not None]
    pnl = sum(r["result"]["value_pnl"] for r in picks)
    wins = sum(1 for r in picks if r["result"]["value_pnl"] > 0)

    # 🎯 Performance des PERLES (ce qu'on recommande vraiment) : CONFIANCE (perle + 2e pari) et VALUE.
    def _distinct_perle2(r):
        # 2e confiance comptée seulement si elle est d'un TYPE DIFFÉRENT de la 1re (même sélection
        # OU même type de marché `kind`/`market` -> pas comptée 2 fois).
        p, p2 = r.get("perle"), r.get("perle2")
        if not (isinstance(p2, dict) and p2.get("selection")):
            return False
        if isinstance(p, dict):
            if p.get("selection") == p2.get("selection"):
                return False
            for key in ("kind", "market"):
                if p.get(key) and p.get(key) == p2.get(key):
                    return False
        return True
    conf_pnls = []
    for r in settled:
        if r["result"].get("perle_pnl") is not None:
            conf_pnls.append(r["result"]["perle_pnl"])
        if r["result"].get("perle2_pnl") is not None and _distinct_perle2(r):
            conf_pnls.append(r["result"]["perle2_pnl"])

    def _distinct_value(r):
        # Value seulement si le pari DIFFÈRE de la confiance (sinon c'est le MÊME pari -> il est
        # déjà compté en Confiance ; on ne le compte pas une 2e fois en Value).
        pv, p = r.get("perle_value"), r.get("perle")
        if not (isinstance(pv, dict) and pv.get("selection")):
            return False
        return not (isinstance(p, dict) and p.get("selection") == pv.get("selection"))
    val_pnls = [r["result"]["perle_value_pnl"] for r in settled
                if r["result"].get("perle_value_pnl") is not None and _distinct_value(r)]
    cwins = sum(1 for x in conf_pnls if x > 0)
    vwins = sum(1 for x in val_pnls if x > 0)
    # Track record GLOBAL de la perle (depuis sa mise en place) : matchs réglés ayant
    # eu une perle, et ROI toutes perles confondues (confiance + value) -> base du verdict.
    perle_settled = [r for r in settled
                     if r.get("perle") and r["result"].get("perle_pnl") is not None]
    all_pnls = conf_pnls + val_pnls
    perle_roi = (sum(all_pnls) / len(all_pnls)) if all_pnls else None

    overall = _fav_metrics(pred)

    return {
        "matchs_suivis": len(store),
        "matchs_regles": len(settled),
        "predictions_evaluees": n,
        "precision_modele": round(correct / n, 3) if n else None,
        "brier": round(brier / n, 4) if n else None,
        "log_loss": round(ll / n, 4) if n else None,
        # Baseline marché : si le marché fait MIEUX (Brier/LL plus bas), le modèle
        # n'apporte pas d'edge — c'est la vraie question, pas la précision absolue.
        "brier_marche": round(mkt_brier / mkt_n, 4) if mkt_n else None,
        "log_loss_marche": round(mkt_ll / mkt_n, 4) if mkt_n else None,
        "bat_le_marche": (None if not mkt_n else (brier / n) < (mkt_brier / mkt_n)),
        # CLV : > 0 en moyenne = on prend de meilleures cotes que la clôture
        "clv_evalue": len(clvs),
        "clv_moyen": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "clv_positif_pct": round(sum(1 for c in clvs if c > 0) / len(clvs), 3) if clvs else None,
        "value_paris_regles": len(picks),
        "value_gagnes": wins,
        "value_taux_reussite": round(wins / len(picks), 3) if picks else None,
        "value_pnl_unites": round(pnl, 2) if picks else 0.0,
        "value_roi": round(pnl / len(picks), 3) if picks else None,
        # 🎯 PERLES (le tableau « bat le marché » s'appuie là-dessus) : confiance + value.
        "perle_conf_regles": len(conf_pnls), "perle_conf_gagnes": cwins,
        "perle_conf_taux": round(cwins / len(conf_pnls), 3) if conf_pnls else None,
        "perle_conf_roi": round(sum(conf_pnls) / len(conf_pnls), 3) if conf_pnls else None,
        "perle_value_regles": len(val_pnls), "perle_value_gagnes": vwins,
        "perle_value_taux": round(vwins / len(val_pnls), 3) if val_pnls else None,
        "perle_value_roi": round(sum(val_pnls) / len(val_pnls), 3) if val_pnls else None,
        # Base du tableau « bat le marché » : échantillon perle + ROI global.
        "perle_matchs_regles": len(perle_settled),
        "perle_paris_regles": len(all_pnls),
        "perle_roi_global": round(perle_roi, 3) if perle_roi is not None else None,
        # Paris d'avant-match en attente de résultat (matchs non terminés ayant une perle) :
        # montre que le palmarès va se remplir, plutôt qu'un tableau « vide ».
        "perle_en_attente": sum(1 for r in store.values()
                                if r.get("perle") and not r.get("result")),
        # Surconfiance globale : proba moyenne annoncée au favori − taux réel.
        # >0 = le modèle promet plus qu'il ne réalise (à corriger par recalibration).
        "surconfiance": overall["surconfiance"],
        # Découpes : où le modèle marche / ne marche pas (data pour l'améliorer).
        # Séparation des résultats par TYPE de pari (Confiance / Value / Autre).
        "par_type": breakdown(pred, _pick_type, order=["Confiance", "Value", "Autre"]),
        "par_confiance": breakdown(pred, lambda r: r.get("confidence"),
                                   order=["élevée", "moyenne", "faible"]),
        "par_surface": breakdown(pred, surface_label,
                                 order=["terre", "dur", "gazon", "autre"]),
        "par_tour": breakdown(pred, lambda r: (r.get("tour") or "").upper() or None,
                              order=["ATP", "WTA"]),
        # Le diagnostic clé : quel facteur prédit bien, lequel plombe le mélange.
        "par_facteur": factor_breakdown(pred),
        "note": (
            "Échantillon trop faible pour conclure (vise 100+ paris réglés)."
            if len(picks) < 100 else
            "ROI positif = le modèle bat le marché sur l'échantillon."
        ),
    }


# --------------------------------------------------------------- dashboard
def _pct(x):
    return f"{round(x * 100)}%" if isinstance(x, (int, float)) else "—"


def _signed_pct(x, dec: int = 1) -> str:
    if x is None:
        return "—"
    return f"{'+' if x >= 0 else ''}{round(x * 100, dec)}%"


def _proof_row(icon: str, name: str, rep: dict, url: str) -> str:
    """Une LIGNE du tableau Preuve (1 sport) : sport (+ nb matchs) | fiabilité | confiance | value.
    Couleur du sport sur le liseré gauche. Détail complet au tap (dashboard)."""
    e = html.escape
    # Track record de la PERLE (depuis sa mise en place) : « matchs notés » = matchs
    # perle réglés, verdict = ROI perle global (la perle bat-elle le marché ?).
    n = rep.get("perle_matchs_regles") or 0
    np = rep.get("perle_paris_regles") or 0
    roi_g = rep.get("perle_roi_global")
    if n == 0:
        verdict, vcls = "En collecte", "na"
    elif np < 30:
        verdict, vcls = "En rodage", "na"
    elif roi_g is not None and roi_g > 0:
        verdict, vcls = "✓ Plus fiable", "ok"
    elif roi_g is not None and roi_g < 0:
        verdict, vcls = "✗ Moins fiable", "ko"
    else:
        verdict, vcls = "En rodage", "na"
    accent = {"tennis": "#d7e64a", "foot": "#2ee27f", "basket": "#ff9f43"}.get(name.lower(), "")
    # Confiance : perles « À JOUER » réglées -> nb gagnés (gros) + taux de réussite (petit dessous)
    cn = rep.get("perle_conf_regles") or 0
    if cn:
        cw = rep.get("perle_conf_gagnes") or 0
        ctaux = round((rep.get("perle_conf_taux") or 0) * 100)
        croi = rep.get("perle_conf_roi") or 0      # ROI réel : un taux élevé peut cacher un ROI négatif
        conf_cell = (f'<span class="ptab-conf">{cw}/{cn}'
                     f'<span class="ptab-pct">{ctaux}% · '
                     f'<b class="{"pos" if croi >= 0 else "neg"}">{"+" if croi >= 0 else ""}{round(croi * 100)}%</b>'
                     f'</span></span>')
    else:
        conf_cell = '<span class="ptab-conf na">—</span>'
    # Value : perles « value » réglées -> nb gagnés (gros) + ROI coloré (petit dessous)
    vn = rep.get("perle_value_regles") or 0
    if vn:
        roi = rep.get("perle_value_roi") or 0
        vtaux = round((rep.get("perle_value_taux") or 0) * 100)   # % de réussite (gagne peu mais peut payer)
        roi_txt = f'{"+" if roi >= 0 else ""}{round(roi * 100)}%'
        val_cell = (f'<span class="ptab-val">{rep.get("perle_value_gagnes") or 0}/{vn}'
                    f'<span class="ptab-pct">{vtaux}% · '
                    f'<b class="{"pos" if roi >= 0 else "neg"}">{roi_txt}</b></span></span>')
    else:
        val_cell = '<span class="ptab-val na">—</span>'
    # Colonne Fiabilité : verdict + mini-barre de progression PROPRE À CE SPORT vers la preuve
    # (100 paris réglés) — portion pleine = réglés, portion estompée = en attente d'avant-match.
    TARGET = 100
    wait_n = rep.get("perle_en_attente") or 0
    sp = min(round(np / TARGET * 100), 100)
    wp = min(round(wait_n / TARGET * 100), 100 - sp)
    bar = (f'<span class="pbar2"><span class="pg-done" style="width:{sp}%"></span>'
           f'<span class="pg-wait" style="width:{wp}%"></span></span>')
    sub = (f'{np} réglé{"s" if np > 1 else ""} · {wait_n} en cours' if wait_n
           else f'{np} pari{"s" if np > 1 else ""} réglé{"s" if np > 1 else ""}')
    style = f' style="--sc:{accent}"' if accent else ""
    return (f'<a class="ptab-row" href="{e(url)}"{style}>'
            f'<span class="ptab-sport">{icon} {e(name)}</span>'
            f'<span class="ptab-verdict {vcls}">{verdict}{bar}'
            f'<span class="ptab-vsub">{sub}</span></span>'
            f'{conf_cell}{val_cell}</a>')


def _rate_chart(reports: list[tuple]) -> str:
    """Graphique « Taux & ROI par sport » : pour chaque sport, une barre de TAUX de réussite
    (confiance, repère à 50 %) + une barre ROI DIVERGENTE (value, origine centrée à 0 %).
    Réutilise `reports` (même ordre que le tableau) -> reste cohérent avec « bat le marché »."""
    accents = {"tennis": "#d7e64a", "foot": "#2ee27f", "basket": "#ff9f43"}
    out = []
    for icon, name, rep, _url in reports:
        sc = accents.get(name.lower(), "var(--border)")
        # Confiance : taux de réussite (doit dépasser 50 %) -> barre 0-100 % + repère médian.
        cn = rep.get("perle_conf_regles") or 0
        if cn:
            taux = rep.get("perle_conf_taux") or 0.0
            pct = round(taux * 100)
            cls = "ok" if taux >= 0.5 else "ko"
            croi = rep.get("perle_conf_roi") or 0.0   # ROI réel sous le taux (le juge de rentabilité)
            rcls = "pos" if croi >= 0 else "neg"
            rtxt = f'{"+" if croi >= 0 else ""}{round(croi * 100)}%'
            conf = (f'<div class="rc-line"><span class="rc-lbl">Confiance</span>'
                    f'<span class="rc-track"><span class="rc-fill {cls}" style="width:{pct}%"></span>'
                    f'<span class="rc-tick"></span></span>'
                    f'<span class="rc-val">{pct}%<span class="rc-roi-sub {rcls}">{rtxt}</span></span></div>')
        else:
            conf = ('<div class="rc-line"><span class="rc-lbl">Confiance</span>'
                    '<span class="rc-track"></span><span class="rc-val na">—</span></div>')
        # Value : ROI (seul juge de rentabilité) -> barre divergente, +droite (vert) / −gauche (rouge),
        # bornée à ±50 % pour l'échelle visuelle ; le chiffre exact reste affiché à droite.
        vn = rep.get("perle_value_regles") or 0
        if vn:
            roi = rep.get("perle_value_roi") or 0.0
            w = round(min(abs(roi) / 0.5, 1.0) * 50)
            seg = (f'<span class="rc-pos" style="width:{w}%"></span>' if roi >= 0
                   else f'<span class="rc-neg" style="width:{w}%"></span>')
            sign = "+" if roi >= 0 else "−"
            vcls = "pos" if roi >= 0 else "neg"
            arrow = "▲" if roi >= 0 else "▼"
            vtaux = round((rep.get("perle_value_taux") or 0) * 100)   # % de réussite (sous le ROI)
            val = (f'<div class="rc-line"><span class="rc-lbl">Value ROI</span>'
                   f'<span class="rc-roi"><span class="rc-zero"></span>{seg}</span>'
                   f'<span class="rc-val {vcls}">{arrow} {sign}{abs(round(roi * 100))}%'
                   f'<span class="rc-roi-sub muted">{vtaux}%</span></span></div>')
        else:
            val = ('<div class="rc-line"><span class="rc-lbl">Value ROI</span>'
                   '<span class="rc-roi"><span class="rc-zero"></span></span>'
                   '<span class="rc-val na">—</span></div>')
        out.append(f'<div class="rc-row" style="--sc:{sc}">'
                   f'<span class="rc-sport">{icon} {html.escape(name)}</span>'
                   f'<div class="rc-bars">{conf}{val}</div></div>')
    return ('<div class="rchart"><div class="rchart-t">📈 Taux de réussite & ROI par sport</div>'
            + "".join(out) + '</div>')


def render_proof(reports: list[tuple]) -> str:
    """Section « Preuve » : UN tableau (1 ligne par sport) pour comparer d'un coup d'œil.
    `reports` = [(icon, name, rep, url), ...]."""
    head = ('<div class="ptab-h"><span>Sport</span><span>Fiabilité</span>'
            '<span class="ph-conf">Confiance</span><span class="ph-val">Value</span></div>')
    rows = "".join(_proof_row(i, n, r, u) for i, n, r, u in reports)
    # Légende des mini-barres (progression PAR SPORT dans la colonne Fiabilité).
    cap = ('<div class="ptab-cap"><span class="pg-lg done"></span> réglés · '
           '<span class="pg-lg wait"></span> en attente · objectif <b>100</b> = preuve solide</div>')
    table = f'<div class="ptab">{head}{rows}</div>{cap}{_rate_chart(reports)}'
    info = ('Sur les paris « perle » déjà réglés, sont-ils gagnants face au marché ? '
            '<b>Fiabilité</b> le dit (sur le ROI global) : <b>✓ Plus fiable</b> / '
            '<b>✗ Moins fiable</b> que le marché, <b>En rodage</b> = pas encore assez de recul, '
            '<b>En collecte</b> = aucun pari encore réglé. '
            '<b>Confiance</b> = perles « À jouer » gagnées (doit passer souvent). '
            '<b>Value</b> = <b>ROI</b> des perles « grosse cote » : elles perdent souvent (normal), '
            'seul le ROI compte. Touche une ligne pour les chiffres détaillés.')
    return web._section('📊 BETSFIX bat le marché ?', table, open_=True, info=info)


def render_dashboard(store: dict, rep: dict, sport: str = "tennis") -> str:
    """Page 'Fiabilité du modèle' : le modèle prédit-il bien ? (calibration).

    `sport` = tennis / basket : suivis séparés, avec une bascule en tête de page.
    """
    e = html.escape
    recs = list(store.values())
    settled = [r for r in recs if r.get("result") and r.get("model_home_prob") is not None
               and not r["result"].get("void")]
    settled.sort(key=lambda r: r["result"].get("settled_at", ""), reverse=True)

    prec = rep.get("precision_modele")
    prec_color = "#9aa0a6" if prec is None else ("#34a853" if prec >= 0.5 else "#ea4335")

    def card(label, value, sub="", color="#e8eaed"):
        return (f'<div class="card"><div class="lbl">{e(label)}</div>'
                f'<div class="val" style="color:{color}">{e(str(value))}</div>'
                f'<div class="sub">{e(sub)}</div></div>')

    def num(x):
        return x if x is not None else "—"

    # Le modèle bat-il le marché ? (Brier plus bas = mieux)
    b_mod, b_mkt = rep.get("brier"), rep.get("brier_marche")
    if b_mod is not None and b_mkt is not None:
        beat = rep.get("bat_le_marche")
        brier_color = "#34a853" if beat else "#ea4335"
        brier_sub = "bat le marché ✓" if beat else f"marché : {b_mkt}"
    else:
        brier_color, brier_sub = "#e8eaed", "plus bas = mieux"

    # CLV : juge d'edge le plus rapide
    clv = rep.get("clv_moyen")
    clv_color = "#9aa0a6" if clv is None else ("#34a853" if clv > 0 else "#ea4335")
    clv_txt = "—" if clv is None else f"{'+' if clv >= 0 else ''}{round(clv * 100, 1)}%"

    cards = "".join([
        card("Favori gagnant", _pct(prec), f"{rep.get('predictions_evaluees', 0)} matchs", prec_color),
        card("Justesse (modèle)", num(b_mod), brier_sub, brier_color),
        card("Justesse (cotes)", num(b_mkt), "réf. à battre"),
        card("Cotes prises (CLV)", clv_txt, f"{rep.get('clv_evalue', 0)} paris · >0 = bon", clv_color),
        card("Erreur (log-loss)", num(rep.get("log_loss")), f"cotes : {num(rep.get('log_loss_marche'))}"),
        card("Matchs suivis", rep.get("matchs_suivis", 0), f"{rep.get('matchs_regles', 0)} réglés"),
    ])

    # Courbe de calibration : proba prédite vs taux réel par tranche
    calib = calibration_table(settled)
    if calib:
        calib_rows = "".join(
            f'<tr><td>{e(b["label"])}</td><td>{b["n"]}</td>'
            f'<td>{round(b["predit"]*100)}%</td><td>{round(b["reel"]*100)}%</td></tr>'
            for b in calib)
        calib_html = (
            '<h2>Calibration (favori du modèle)</h2>'
            '<div class="banner">Une proba bien calibrée = "prédit" ≈ "réel". '
            'Si le modèle dit 70 % et que ça gagne ~70 % du temps, il est honnête.</div>'
            '<table><tr><td class="dim">proba prédite</td><td class="dim">n</td>'
            '<td class="dim">prédit (moy)</td><td class="dim">réel</td></tr>'
            f'{calib_rows}</table>')
    else:
        calib_html = ""

    # Surconfiance : le modèle promet-il plus qu'il ne réalise ?
    # ⚠️ On n'alerte (et ne conseille de recalibrer) qu'au-delà d'un échantillon
    # suffisant : sur < 50 matchs, l'écart est du bruit (le back-test 16k a déjà
    # tranché : pas de surconfiance réelle). Conseiller un changement de CALIB_SHRINK
    # sur 20 matchs serait du sur-ajustement.
    sc = rep.get("surconfiance")
    n_calib = rep.get("predictions_evaluees", 0)
    if sc is None:
        surconf_html = ""
    elif n_calib < 50:
        surconf_html = (
            f'<div class="banner">Calibration : écart prédit−réel {"+" if sc >= 0 else ""}'
            f'{round(sc*100)} pts sur {n_calib} matchs — <b>échantillon trop faible</b> '
            f'pour conclure (vise 50+). Ne change rien sur si peu.</div>')
    elif sc > 0.03:
        surconf_html = (
            f'<div class="banner warn">⚠️ <b>Trop optimiste de +{round(sc*100)} pts</b> sur '
            f'{n_calib} matchs : le modèle annonce un peu plus de chances que le taux réel — '
            f'à recalibrer (légère sur-confiance).</div>')
    elif sc < -0.03:
        surconf_html = (
            f'<div class="banner">Sous-confiance {round(sc*100)} pts sur {n_calib} matchs : '
            f'le favori gagne plus souvent que le modèle ne l\'annonce.</div>')
    else:
        surconf_html = (f'<div class="banner">✓ Calibration saine sur {n_calib} matchs '
                        '(prédit ≈ réel pour le favori).</div>')

    # Tableau générique d'une découpe (par confiance / surface / tour)
    def breakdown_table(title, rows, help_txt=""):
        if not rows:
            return ""
        trs = "".join(
            f'<tr><td>{e(str(r["label"]))}</td><td>{r["n"]}</td>'
            f'<td>{_pct(r["precision"])}</td>'
            f'<td>{r["brier"] if r["brier"] is not None else "—"}</td>'
            f'<td>{_pct(r.get("pred_fav"))} / {_pct(r.get("reel_fav"))}</td></tr>'
            for r in rows)
        help_html = f'<div class="banner">{help_txt}</div>' if help_txt else ""
        return (f'<h2>{e(title)}</h2>{help_html}'
                '<table><tr><td class="dim">groupe</td><td class="dim">n</td>'
                '<td class="dim">précis.</td><td class="dim">Brier</td>'
                '<td class="dim">prédit/réel fav</td></tr>'
                f'{trs}</table>')

    # Le diagnostic clé : performance de chaque facteur pris isolément
    factors_rep = rep.get("par_facteur") or []
    if factors_rep:
        frows = "".join(
            f'<tr><td>{e(r["name"])}</td><td>{r["n"]}</td>'
            f'<td>{_pct(r["precision"])}</td>'
            f'<td style="color:{"#34a853" if r["brier"] < 0.25 else "#ea4335"}">{r["brier"]}</td>'
            f'<td class="dim">{round(r["poids"]*100)}%</td></tr>'
            for r in factors_rep)
        factors_html = (
            '<h2>Performance par facteur</h2>'
            '<div class="banner">Chaque facteur comme s\'il décidait seul. '
            'Brier &lt; 0.25 (vert) = il aide ; &gt; 0.25 (rouge) = il dégrade le '
            'mélange et son poids devrait baisser. Trié du meilleur au pire.</div>'
            '<table><tr><td class="dim">facteur</td><td class="dim">n</td>'
            '<td class="dim">précis.</td><td class="dim">Brier</td>'
            '<td class="dim">poids</td></tr>'
            f'{frows}</table>')
    else:
        factors_html = ""

    breakdowns_html = (
        breakdown_table("Par type de pari", rep.get("par_type"),
                        "Résultats SÉPARÉS par type : Confiance (favori net ≥ 65 %), "
                        "Value (edge sur la cote, souvent des outsiders) et Autre. "
                        "Le ROI des Value est détaillé plus bas.")
        + breakdown_table("Par niveau de confiance", rep.get("par_confiance"),
                        "Le modèle est-il plus fiable quand il est 'confiant' ? "
                        "Sinon, le score de confiance ne veut rien dire.")
        + breakdown_table("Par surface", rep.get("par_surface"))
        + breakdown_table("Par circuit", rep.get("par_tour"),
                          "ATP (hommes) vs WTA (femmes) : si l'un décroche, "
                          "le modèle lui conviendrait moins."))

    # Track record des paris conseillés (value réglées) — le vrai juge de rentabilité
    bets = [r for r in recs if r.get("value_pick") and r.get("result")
            and r["result"].get("value_pnl") is not None]
    bets.sort(key=lambda r: r["result"].get("settled_at", ""), reverse=True)
    if bets:
        def bet_row(r):
            v, res = r["value_pick"], r["result"]
            pnl = res["value_pnl"]
            won = pnl > 0
            mark = ('<span class="pos">✓ gagné</span>' if won
                    else '<span class="neg">✗ perdu</span>')
            return (f'<tr><td>{e(r["home"])} v {e(r["away"])}<br>'
                    f'<span class="dim">{e(v.get("player") or "")} @{v.get("odds")}</span></td>'
                    f'<td>{mark}</td>'
                    f'<td class="{"pos" if won else "neg"}">'
                    f'{"+" if pnl >= 0 else ""}{round(pnl, 2)}</td></tr>')
        pnl_tot = rep.get("value_pnl_unites", 0) or 0
        roi = rep.get("value_roi")
        bets_html = (
            f'<h2>Track record des paris conseillés ({len(bets)})</h2>'
            f'<div class="banner">Résultat réel des « paris à jouer » (value), mise plate '
            f'1 unité. P&amp;L total <b>{"+" if pnl_tot >= 0 else ""}{pnl_tot} u</b> · '
            f'réussite {_pct(rep.get("value_taux_reussite"))} · '
            f'ROI {_pct(roi) if roi is not None else "—"}. '
            f'Peu significatif tant qu\'on n\'a pas ~100 paris réglés.</div>'
            '<table><tr><td class="dim">pari</td><td class="dim">résultat</td>'
            f'<td class="dim">P&amp;L (u)</td></tr>'
            f'{"".join(bet_row(r) for r in bets[:30])}</table>')
    else:
        bets_html = ""

    def settled_row(r):
        res = r["result"]
        hp = r.get("model_home_prob") or 0
        fav = r["home"] if hp >= 0.5 else r["away"]
        favp = round(max(hp, 1 - hp) * 100)
        winner_name = r["home"] if res["winner"] == "home" else r["away"]
        ok = (res["winner"] == "home") == (hp >= 0.5)
        mark = '<span class="pos">✓</span>' if ok else '<span class="neg">✗</span>'
        return (f'<tr><td>{e(r["home"])} v {e(r["away"])}</td>'
                f'<td>favori {e(fav)} {favp}%</td>'
                f'<td>{mark} {e(winner_name)}</td></tr>')

    settled_html = ("".join(settled_row(r) for r in settled[:30])
                    or '<tr><td colspan="3" class="dim">Aucun match réglé pour l\'instant.</td></tr>')

    body = f"""<div class="grid">{cards}</div>
<div class="banner">Perf <b>{e("basket (WNBA)" if sport == "basket" else "tennis")}</b> — mesure si le
 <b>modèle prédit bien le vainqueur</b> (calibration sur résultats réels). Ce n'est <b>pas</b>
 un outil pour battre le book. Fiable à partir de ~100 matchs réglés.</div>
{surconf_html}
{bets_html}
{calib_html}
{factors_html}
{breakdowns_html}
<h2>Le modèle vs résultats réels</h2>
<table><tr><td class="dim">match</td><td class="dim">prédiction</td>
<td class="dim">vainqueur</td></tr>{settled_html}</table>"""
    return web.layout("Fiabilité", sport, body, subnav="perf", refresh=True)


def render_today(store: dict) -> str:
    """Page 'Matchs à venir' : toutes les analyses suivies, triées par heure."""
    e = html.escape
    upcoming = [r for r in store.values() if not r.get("result")]
    upcoming.sort(key=lambda r: r.get("start_time") or "")

    def hhmm(iso):
        return web.fmt_local(iso, with_date=False) or "—"

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
            f'Heures en fuseau belge. Une "value" = avis du modèle, à confirmer par le suivi.</div>'
            f'<h2>Matchs à venir ({len(upcoming)})</h2>'
            f'<table><tr><td class="dim">Heure</td><td class="dim">Match</td>'
            f'<td class="dim">Value</td></tr>{rows}</table>')
    return web.layout("Matchs à venir", "tennis", body, subnav="matchs", refresh=True)
