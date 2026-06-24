"""Re-poste les cartes IMAGE du DERNIER scan avec les bannières sport (en-tête par sport).

But : après un changement de gabarit de carte, regénérer + reposter les cartes des sidecars
existants SANS relancer le scan (coûteux). Vide d'abord le canal, puis poste 1 carte par match.

Usage : python tools/renotify_cards.py            # tous les sidecars générés < 3 h
        python tools/renotify_cards.py --ids a,b  # ids précis, dans l'ordre donné
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import card_image  # noqa: E402
from app import analyses, notify  # noqa: E402

_FR_J = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_FR_M = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
_EMO = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
_SN = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}


def _fr_date(dt) -> str:
    return f"{_FR_J[dt.weekday()]} {dt.day} {_FR_M[dt.month - 1]}"


def _card_for(d: dict) -> dict | None:
    """Reconstruit les données de carte d'un sidecar, à l'identique du scan."""
    sport = d.get("sport")
    combo = d.get("combo") or {}
    has_combo = bool(combo.get("legs"))
    pick = d.get("pick") or ""
    rb = analyses.retained_bet(sport, str(d.get("id")))
    pick_shown = bool(rb) if has_combo else bool(pick or rb)

    meta = ""
    try:
        dt = datetime.fromisoformat((d.get("start") or "").replace("Z", "+00:00"))
        meta = f"{_fr_date(dt)} · {dt.strftime('%H:%M')}"
    except ValueError:
        pass
    card = {"emoji": _EMO.get(sport, "•"),
            "cat": f"{_SN.get(sport, sport)} · {d['comp']}" if d.get("comp") else _SN.get(sport, sport),
            "match": str(d.get("name", "")).replace(" - ", " — "), "meta": meta}
    if has_combo:
        cote = (f"{combo['real_odds']:.2f}" if combo.get("real_odds") else f"{combo.get('total', '?')}")
        card.update(type="combo", cote=cote,
                    legs=[(str(l.get("sel", "")), str(l.get("cote", ""))) for l in combo["legs"]])
    elif pick_shown and rb:
        card.update(type="simple", pick=str(rb.get("sel", "")),
                    cote=(f"{rb['cote']:g}" if rb.get("cote") else ""), conf=rb.get("prob"))
    elif pick_shown:
        m = re.search(r"(.+?)\s*@\s*([\d]+[.,][\d]+)", pick)
        card.update(type="simple", pick=(m.group(1).strip() if m else pick),
                    cote=(m.group(2).replace(",", ".") if m else ""), conf=None)
    else:
        return None
    return card


def _settled(d: dict) -> bool:
    return bool((d.get("result") or {}).get("pick_result")) or bool((d.get("combo") or {}).get("result"))


def _result_card_for(d: dict) -> dict | None:
    """Carte RÉSULTAT d'un sidecar réglé (mirror de app/settle_analyst), avec score + verdict."""
    sport = d.get("sport")
    combo = d.get("combo") or {}
    has_combo = bool(combo.get("legs"))
    res = d.get("result") or {}
    pick_result = res.get("pick_result")
    combo_result = combo.get("result")
    simple_shown = (not has_combo) or (analyses.retained_bet(sport, str(d.get("id"))) is not None)

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
    meta = "terminé"
    try:
        dt = datetime.fromisoformat((d.get("start") or "").replace("Z", "+00:00"))
        meta = f"terminé · {_fr_date(dt)} · {dt.strftime('%H:%M')}"
    except ValueError:
        pass
    return {"emoji": _EMO.get(sport, "•"),
            "cat": f"{_SN.get(sport, sport)} · {d['comp']}" if d.get("comp") else _SN.get(sport, sport),
            "match": str(d.get("name", "")).replace(" - ", " — "), "meta": meta,
            "type": "result", "score": res.get("score") or "",
            "simple": card_simple, "combo": card_combo}


def _clear_channel():
    tok, chats = notify._config()
    if not (tok and chats):
        return
    base = f"https://api.telegram.org/bot{tok}"
    with httpx.Client(timeout=15) as cl:
        r = cl.post(base + "/sendMessage", json={"chat_id": chats[0], "text": "."})
        mid = (r.json().get("result") or {}).get("message_id")
        if not mid:
            return
        for ch in chats:
            for i in range(mid, 0, -1):
                try:
                    cl.post(base + "/deleteMessage", json={"chat_id": ch, "message_id": i})
                except Exception:
                    pass
    notify._save_sent([])
    try:                                       # purge le suivi prono->résultat (ids supprimés)
        json.dump({}, open(notify._PRONO_PATH, "w", encoding="utf-8"))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="ids précis séparés par des virgules (ordre conservé)")
    ap.add_argument("--hours", type=float, default=3.0, help="sidecars générés depuis N heures")
    ap.add_argument("--no-clear", action="store_true", help="ne pas vider le canal avant")
    args = ap.parse_args()

    sides = []
    if args.ids:
        wanted = [s.strip() for s in args.ids.split(",") if s.strip()]
        for f in __import__("glob").glob(os.path.join(analyses.DIR, "*.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(d.get("id")) in wanted:
                sides.append((wanted.index(str(d.get("id"))), d))
        sides.sort(key=lambda x: x[0])
        sides = [d for _, d in sides]
    else:
        cutoff = datetime.now(timezone.utc).timestamp() - args.hours * 3600
        rows = []
        for f in __import__("glob").glob(os.path.join(analyses.DIR, "*.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if os.path.getmtime(f) >= cutoff:
                rows.append((os.path.getmtime(f), d))
        rows.sort(key=lambda x: x[0])           # ordre chronologique de génération
        sides = [d for _, d in rows]

    if not sides:
        print("aucun sidecar à reposter"); return
    sides.sort(key=lambda d: d.get("start") or "")   # ORDRE CHRONOLOGIQUE des coups d'envoi
    # DÉDUP par paire d'équipes : jamais 2 fois le même match -> on garde le PLUS RÉCENT
    _seen = {}
    for d in sides:
        _seen[notify._norm_name(d.get("name"))] = d   # le dernier (start le + tard) écrase
    sides = sorted(_seen.values(), key=lambda d: d.get("start") or "")
    print(f"{len(sides)} carte(s) à reposter (dédupliquées par match)")
    if not notify.configured():
        print("notify non configuré"); return
    if not args.no_clear:
        print("vidage du canal…"); _clear_channel()
    os.makedirs("data/_cards", exist_ok=True)
    n = 0
    for i, d in enumerate(sides):
        prono = _card_for(d)
        if not prono:
            print(f"  - {d.get('name')} : calibration seule -> ignoré"); continue
        try:
            ppng = f"data/_cards/renotify_{i}p.png"
            card_image.render_card_sync(prono, ppng)
            sent = notify.send_photo_sync(ppng, "")           # carte PRONO
            if sent:
                notify.remember_prono(str(d.get("id")), sent, d.get("name"))
                n += 1
            print(f"  ✓ {prono['match']} (prono) -> {'posté' if sent else 'ÉCHEC'}")
            if _settled(d):                                   # résultat EN RÉPONSE au prono
                res = _result_card_for(d)
                if res:
                    rpng = f"data/_cards/renotify_{i}r.png"
                    card_image.render_card_sync(res, rpng)
                    rsent = notify.send_photo_sync(rpng, "", reply_to=sent)
                    if rsent:
                        n += 1
                    print(f"      ↳ résultat {res['score']} -> {'posté (reply)' if rsent else 'ÉCHEC'}")
        except Exception as exc:
            print(f"  ✗ {prono['match']} : {exc}")
    print(f"Terminé : {n} carte(s) postée(s).")


if __name__ == "__main__":
    main()
