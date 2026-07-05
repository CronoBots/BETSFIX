"""JOURNAL D'APPRENTISSAGE — voir le modèle progresser JOUR APRÈS JOUR (100 % lecture seule + journal).

Chaque jour, on prend une PHOTO des métriques clés (fiabilité, calibration, ROI, exclusions per-sport…),
on calcule les DELTAS vs la veille, et on écrit automatiquement les ÉVÉNEMENTS NOTABLES (un marché qui
s'écarte ou se ré-intègre tout seul, la fiabilité qui bouge) dans un journal lisible `LEARNING.md`.

But : rendre VISIBLE et TRAÇABLE l'auto-amélioration — on ne « croit » plus que le modèle apprend, on le
MESURE. N'a AUCUN effet sur la sélection/le règlement (il observe, il n'agit pas). Le socle d'intégrité
(app/selfcheck.py) garantit que ces mesures ne sont pas corrompues avant qu'on en tire des conclusions.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from app import analyses

_LOG = os.path.join(analyses._ROOT, "data", "learning_log.json")     # {date: snapshot}
_MD = os.path.join(analyses._ROOT, "LEARNING.md")                    # journal humain (auto-append)

_SPORTS = (("foot", "Football", "⚽"), ("tennis", "Tennis", "🎾"), ("basket", "Basket", "🏀"))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def snapshot() -> dict:
    """Photo des métriques APPRENANTES à l'instant t (sans effet de bord)."""
    cal = analyses.calibration()
    rel = analyses.calibration_reliability(buckets=12)
    st = analyses.stats_full().get("overall") or {}
    exics = analyses._excluded_by_sport()
    per_sport = {}
    for sp, fr, _ in _SPORTS:
        g = (cal.get("by_sport") or {}).get(fr) or {}
        per_sport[sp] = {"n": g.get("n") or 0, "win_rate": g.get("win_rate"), "avg_conf": g.get("avg_conf")}
    return {
        "date": _today(),
        "reliability": rel.get("index"),
        "reliability_trend": rel.get("trend"),
        "cal": {k: cal.get(k) for k in ("n", "n_shadow", "n_played", "mae", "win_rate", "avg_conf",
                                        "roi", "verdict")},
        "stats": {k: st.get(k) for k in ("settled", "won", "lost", "roi")},
        "exclusions": {sp: sorted(exics.get(sp, set())) for sp, _, _ in _SPORTS},
        "combo_props_allowed": analyses.combo_player_props_allowed()[0],
        "per_sport": per_sport,
        "events": [],
    }


def _load() -> dict:
    try:
        return json.load(open(_LOG, encoding="utf-8"))
    except Exception:
        return {}


def _save(log: dict) -> None:
    try:
        json.dump(log, open(_LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    except Exception:
        pass


def _prev_of(log: dict, today: str) -> dict | None:
    days = sorted(d for d in log if d < today)
    return log[days[-1]] if days else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _delta(cur, prev):
    a, b = _num(cur), _num(prev)
    if a is None or b is None:
        return None
    return round(a - b, 1)


def _notable_events(cur: dict, prev: dict) -> list[str]:
    """Événements MARQUANTS (auto-révision d'exclusions, mouvements de fiabilité/ROI) -> lignes lisibles."""
    ev = []
    _lbl = {sp: (fr, ic) for sp, fr, ic in _SPORTS}
    for sp in cur.get("exclusions", {}):
        now = set(cur["exclusions"].get(sp) or [])
        was = set((prev.get("exclusions") or {}).get(sp) or [])
        fr, ic = _lbl.get(sp, (sp, ""))
        for m in sorted(now - was):
            ev.append(f"🔴 {ic} {fr} : le marché « {m} » vient d'être ÉCARTÉ (sur-confiance / ROI prouvés).")
        for m in sorted(was - now):
            ev.append(f"🟢 {ic} {fr} : le marché « {m} » est RÉ-INTÉGRÉ (repassé au-dessus des seuils).")
    if cur.get("combo_props_allowed") and not prev.get("combo_props_allowed"):
        ev.append("🟢 Combinés : les props joueur sont RÉ-INTÉGRÉES (validées par les fantômes).")
    elif prev.get("combo_props_allowed") and not cur.get("combo_props_allowed"):
        ev.append("🔴 Combinés : les props joueur ressortent (calibration repassée sous le seuil).")
    dr = _delta(cur.get("reliability"), prev.get("reliability"))
    if dr is not None and abs(dr) >= 3:
        ev.append(f"{'📈' if dr > 0 else '📉'} Indice de fiabilité {dr:+.0f} pts "
                  f"({prev.get('reliability')} → {cur.get('reliability')}).")
    droi = _delta((cur.get("stats") or {}).get("roi"), (prev.get("stats") or {}).get("roi"))
    if droi is not None and abs(droi) >= 3:
        ev.append(f"{'📈' if droi > 0 else '📉'} ROI des paris joués {droi:+.1f} pts.")
    return ev


def deltas(cur: dict, prev: dict | None) -> dict:
    if not prev:
        return {}
    return {
        "since": prev.get("date"),
        "reliability": _delta(cur.get("reliability"), prev.get("reliability")),
        "cal_mae": _delta((cur.get("cal") or {}).get("mae"), (prev.get("cal") or {}).get("mae")),
        "cal_roi": _delta((cur.get("cal") or {}).get("roi"), (prev.get("cal") or {}).get("roi")),
        "cal_win_rate": _delta((cur.get("cal") or {}).get("win_rate"), (prev.get("cal") or {}).get("win_rate")),
        "settled": _delta((cur.get("stats") or {}).get("settled"), (prev.get("stats") or {}).get("settled")),
        "stats_roi": _delta((cur.get("stats") or {}).get("roi"), (prev.get("stats") or {}).get("roi")),
        "n_predictions": _delta((cur.get("cal") or {}).get("n"), (prev.get("cal") or {}).get("n")),
    }


def _append_md(date: str, events: list[str]) -> None:
    if not events:
        return
    try:
        head = ""
        if not os.path.exists(_MD):
            head = ("# Journal d'apprentissage — BETSFIX\n\n"
                    "> Écrit **automatiquement** par `app/learning.py` (run quotidien). Chaque entrée = un\n"
                    "> événement d'auto-révision mesuré (exclusion de marché, ré-intégration, mouvement de\n"
                    "> fiabilité/ROI). Le modèle apprend — voici la trace.\n")
        with open(_MD, "a", encoding="utf-8") as fh:
            if head:
                fh.write(head)
            fh.write(f"\n## {date}\n")
            for e in events:
                fh.write(f"- {e}\n")
    except Exception:
        pass


def record() -> dict:
    """Enregistre la photo du jour, calcule les deltas vs la veille, auto-écrit les événements notables
    (idempotent sur la journée : ne ré-append pas un événement déjà écrit). Renvoie {today, deltas, new}."""
    log = _load()
    cur = snapshot()
    today = cur["date"]
    prev = _prev_of(log, today)
    ev = _notable_events(cur, prev) if prev else []
    already = set((log.get(today) or {}).get("events") or [])
    new = [e for e in ev if e not in already]
    cur["events"] = sorted(already | set(ev))
    log[today] = cur
    _save(log)
    _append_md(today, new)
    return {"today": cur, "deltas": deltas(cur, prev), "new_events": new, "prev_date": prev.get("date") if prev else None}


def report(days: int = 45) -> dict:
    """Vue pour l'endpoint : état du jour + deltas vs la veille + série historique compacte + derniers
    événements notables. LECTURE SEULE (ne modifie rien, contrairement à record())."""
    log = _load()
    if not log:
        return {"available": False}
    dates = sorted(log)[-days:]
    cur = log[dates[-1]]
    prev = _prev_of(log, cur["date"])
    hist = [{"date": d, "reliability": log[d].get("reliability"),
             "roi": (log[d].get("stats") or {}).get("roi"),
             "settled": (log[d].get("stats") or {}).get("settled")} for d in dates]
    recent_events = []
    for d in reversed(dates):
        for e in (log[d].get("events") or []):
            recent_events.append({"date": d, "event": e})
    return {"available": True, "today": cur, "deltas": deltas(cur, prev),
            "history": hist, "recent_events": recent_events[:15]}
