"""CLI — génère les DÉBRIEFS des paris joués perdus (mémoire évolutive, demande user 2026-08-02).

Doit tourner dans la session `vince` AUTHENTIFIÉE (comme le scan) : il pilote Claude headless (`claude -p`)
via `run_claude` du scan. Purement additif — n'écrit que data/debriefs.json + data/lessons.json.

Usage :
    python tools/debrief.py                 # foot, toutes les pertes non encore débriefées
    python tools/debrief.py --sport foot --limit 10
    python tools/debrief.py --list          # ne génère rien, liste les pertes en attente
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import debrief


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="foot", help="foot,tennis,basket (défaut foot)")
    ap.add_argument("--limit", type=int, default=None, help="nb max de pertes à traiter")
    ap.add_argument("--list", action="store_true", help="liste les pertes en attente, sans générer")
    args = ap.parse_args()

    sports = tuple(s.strip() for s in args.sport.split(",") if s.strip())

    if args.list:
        todo = debrief.pending(sports, args.limit)
        print(f"{len(todo)} perte(s) en attente de débrief :")
        for sp, d, lb in todo:
            print(f"  · {sp} {d.get('start','')[:10]} {d.get('home')} — {d.get('away')} "
                  f"| {lb.get('sel')} @ {lb.get('cote')}")
        return 0

    # run_claude vit dans le scan (session authentifiée requise). Import tardif (dépendances lourdes).
    from tools.generate_analyses import run_claude

    def runner(prompt: str) -> str:
        return run_claude(prompt, timeout=240)

    res = debrief.run(runner, sports=sports, limit=args.limit)
    print(f"\nBilan : {res['ok']} débrief(s) OK · {res['fail']} échec(s) sur {res['todo']} à traiter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
