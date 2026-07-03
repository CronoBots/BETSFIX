"""POINT UNIQUE de construction des DONNÉES de carte Telegram (prono / résultat) depuis un sidecar.

Avant, la même logique (branchement combo/simple/calibration, regex `… @cote`, mapping legs/mark/score)
était dupliquée à l'identique dans 3 endroits — le scan (`tools/generate_analyses.py`), le règlement
(`app/settle_analyst.py`) et le repost (`tools/renotify_cards.py`) — d'où un risque de désynchro du
gabarit. Ici on centralise la DONNÉE ; le RENDU image reste dans `tools/card_image.py`.

Une carte = un dict consommé par `card_image.render_card`. Les clés `_mid`/`_start` (privées) servent
au fil prono->résultat (reply Telegram) et au tri chronologique ; elles sont ignorées au rendu.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from app import analyses


def _clean_why(w) -> str:
    """Explication PROFESSIONNELLE et fiable d'une sélection : même pipeline que l'app (retire la liste
    « Sources : … », met les mises en % de bankroll, remet en phrase propre). '' si vide."""
    return analyses._sentence_case(analyses._units_to_pct(analyses._strip_sources(str(w or "")))).strip()


def _pick_why(d: dict, sel: str) -> str:
    """« Pourquoi » du pari SIMPLE retenu, extrait du Verdict de l'analyse (.md) — comme sur l'app
    (_verdict_notes + _assign_notes, déjà nettoyés/sans sources). '' si introuvable."""
    try:
        md = os.path.join(analyses.DIR, f"{d.get('sport')}_{d.get('id')}.md")
        if not sel or not os.path.exists(md):
            return ""
        notes, _ = analyses._verdict_notes(open(md, encoding="utf-8").read())
        if not notes:
            return ""
        assigned = analyses._assign_notes([sel], notes)      # {0: why} si apparié
        return _clean_why(assigned.get(0, ""))
    except Exception:
        return ""

# Date courte FR + maps sport (centralisées ici — étaient copiées dans 3-4 fichiers).
_FR_J = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_FR_M = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
SPORT_EMOJI = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
SPORT_NAME = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}


def fr_date(dt) -> str:
    return f"{_FR_J[dt.weekday()]} {dt.day} {_FR_M[dt.month - 1]}"


def _cat(d: dict) -> str:
    sport = d.get("sport")
    sn = SPORT_NAME.get(sport, sport or "")
    return f"{sn} · {d['comp']}" if d.get("comp") else sn


def _dt(d):
    try:
        return datetime.fromisoformat((d.get("start") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def is_settled(d: dict) -> bool:
    return bool((d.get("result") or {}).get("pick_result")) or bool((d.get("combo") or {}).get("result"))


def build_prono_card(d: dict) -> dict | None:
    """Données de la carte PRONO d'un match (avant-match) depuis le sidecar. None si « calibration
    seule » (rien à afficher). Combiné = combiné seul ; sinon le pari simple retenu / le plus sûr."""
    sport = d.get("sport")
    combo = d.get("combo") or {}
    has_combo = bool(combo.get("legs"))
    pick = d.get("pick") or ""
    rb = analyses.retained_bet(sport, str(d.get("id")))
    # On ne publie un SIMPLE que s'il est RETENU (passe confiance+EV+garde-fous) — combiné OU non.
    # Sinon Telegram postait des paris (favoris sans value, ex. @1.14) que les stats ne comptent PAS
    # -> incohérence. Désormais : posté = compté. Si rien n'est retenu, on s'abstient (pas de carte).
    pick_shown = bool(rb)

    dt = _dt(d)
    meta = f"{fr_date(dt)} · {dt.strftime('%H:%M')}" if dt else ""
    card = {"emoji": SPORT_EMOJI.get(sport, "•"), "_mid": str(d.get("id")),
            "_start": str(d.get("start") or ""), "cat": _cat(d),
            "match": str(d.get("name", "")).replace(" - ", " — "), "meta": meta}
    if has_combo:
        cote = (f"{combo['real_odds']:.2f}" if combo.get("real_odds") else f"{combo.get('total', '?')}")
        # ANALYSE PAR JAMBE (comme l'app) : chaque sélection porte son « pourquoi » sérieux + la synthèse
        # du combiné (corrélation). Uniquement sur la carte de PUBLICATION (pas la carte résultat).
        card.update(type="combo", cote=cote,
                    legs=[(str(l.get("sel", "")), str(l.get("cote", "")), _clean_why(l.get("why")))
                          for l in combo["legs"]],
                    synth=_clean_why(combo.get("why")))
    elif pick_shown and rb:
        card.update(type="simple", pick=str(rb.get("sel", "")),
                    cote=(f"{rb['cote']:g}" if rb.get("cote") else ""), conf=rb.get("prob"),
                    why=_pick_why(d, str(rb.get("sel", ""))))
    elif pick_shown:
        m = re.search(r"(.+?)\s*@\s*([\d]+[.,][\d]+)", pick)
        card.update(type="simple", pick=(m.group(1).strip() if m else pick),
                    cote=(m.group(2).replace(",", ".") if m else ""), conf=None)
    else:
        return None
    return card


def build_result_card(d: dict) -> dict | None:
    """Données de la carte RÉSULTAT d'un sidecar réglé (score + verdict par jambe/global). None si rien
    de réglé à montrer. Le simple n'est inclus que s'il est AFFICHÉ (cohérence avec la carte prono)."""
    sport = d.get("sport")
    combo = d.get("combo") or {}
    has_combo = bool(combo.get("legs"))
    res = d.get("result") or {}
    pick_result = res.get("pick_result")
    combo_result = combo.get("result")
    # Cohérent avec la carte prono : le résultat du SIMPLE n'est montré que s'il était RETENU (donc
    # publié). Un simple non retenu n'a pas de carte prono -> pas de carte résultat non plus.
    simple_shown = analyses.retained_bet(sport, str(d.get("id"))) is not None

    card_simple = card_combo = None
    if pick_result and simple_shown:
        raw = (d.get("pick") or "").strip()
        m = re.search(r"(.+?)\s*@\s*([\d]+[.,][\d]+)", raw)
        card_simple = {"label": (m.group(1).strip() if m else raw) or "Pari simple",
                       "cote": (m.group(2).replace(",", ".") if m else ""), "mark": pick_result}
    if combo_result:
        cco = combo.get("real_odds") or combo.get("total")
        card_combo = {"cote": (f"{cco:.2f}" if isinstance(cco, float) else str(cco or "")),
                      "mark": combo_result,
                      "legs": [(str(l.get("sel", "")), l.get("result"), l.get("cote") or "")
                               for l in combo.get("legs", [])]}
    if not (card_simple or card_combo):
        return None
    dt = _dt(d)
    meta = f"terminé · {fr_date(dt)} · {dt.strftime('%H:%M')}" if dt else "terminé"
    return {"emoji": SPORT_EMOJI.get(sport, "•"), "_mid": str(d.get("id")), "cat": _cat(d),
            "match": str(d.get("name", "")).replace(" - ", " — "), "meta": meta,
            "type": "result", "score": res.get("score") or "",
            "simple": card_simple, "combo": card_combo}
