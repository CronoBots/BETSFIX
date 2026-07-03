"""Re-poste les cartes IMAGE du DERNIER scan avec les bannières sport (en-tête par sport).

But : après un changement de gabarit de carte, regénérer + reposter les cartes des sidecars
existants SANS relancer le scan (coûteux). Vide d'abord le canal, puis poste 1 carte par match.

Usage : python tools/renotify_cards.py            # tous les sidecars générés < 3 h
        python tools/renotify_cards.py --ids a,b  # ids précis, dans l'ordre donné
"""
from __future__ import annotations

import argparse
import io
import json
import glob
import os
import re
import sys
from datetime import datetime, timezone

import httpx

# Console Windows en cp1252 : les ✓/✗/… des print faisaient planter le script APRÈS le vidage du canal
# (canal vidé mais republication avortée). On force stdout en UTF-8 tolérant.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import card_image  # noqa: E402
from app import analyses, notify  # noqa: E402
from app import card_data as _cd  # noqa: E402  (POINT UNIQUE de construction des cartes)


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
    notify.reset_pronos()                      # purge le suivi prono->résultat (atomique)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="ids précis séparés par des virgules (ordre conservé)")
    ap.add_argument("--hours", type=float, default=3.0, help="sidecars générés depuis N heures")
    ap.add_argument("--no-clear", action="store_true", help="ne pas vider le canal avant")
    args = ap.parse_args()

    sides = []
    if args.ids:
        wanted = [s.strip() for s in args.ids.split(",") if s.strip()]
        for f in glob.glob(os.path.join(analyses.DIR, "*.json")):
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
        for f in glob.glob(os.path.join(analyses.DIR, "*.json")):
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
        prono = _cd.build_prono_card(d)
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
            if _cd.is_settled(d):                                   # résultat EN RÉPONSE au prono
                res = _cd.build_result_card(d)
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
