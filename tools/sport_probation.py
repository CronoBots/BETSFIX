"""Contrôle MANUEL de la probation par sport — demande user 2026-07-24 : un sport en pause n'est JAMAIS
réactivé tout seul, seulement sur accord explicite du proprio.

Usage :
  python tools/sport_probation.py --status                 # état + qui est PRÊT à réactiver
  python tools/sport_probation.py --pause tennis           # met un sport en pause manuellement
  python tools/sport_probation.py --reactivate tennis      # RÉACTIVE (seul moyen de sortir de pause)

Entrée en pause = AUTO (protection, ROI calibration ≤ -8). Sortie = ce script UNIQUEMENT.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import analyses  # noqa: E402


def _status() -> None:
    paused = analyses._load_sport_probation()
    ready = analyses.sport_reactivation_ready()
    cal = analyses.calibration(min_conf=analyses._MIN_CONF).get("by_sport") or {}
    print("=== Probation par sport ===")
    if not paused:
        print("  (aucun sport en pause)")
    for name, g in cal.items():
        sp = analyses._SPORT_FR.get(name, name.lower())
        if sp not in paused:
            continue
        roi = g.get("roi")
        tag = "  ✅ PRÊT à réactiver (ton accord requis)" if sp in ready else "  ⏳ pas encore remonté"
        print(f"  ⏸ {sp:7} · ROI calibration {roi}% (n={g.get('n')}) · seuil retour ≥ {analyses.SPORT_ROI_BACK}%{tag}")
    if ready:
        print(f"\n  -> Pour réactiver : python tools/sport_probation.py --reactivate {sorted(ready)[0]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Contrôle manuel de la probation par sport (jamais auto-réactivé).")
    ap.add_argument("--status", action="store_true", help="affiche l'état + les sports prêts à réactiver")
    ap.add_argument("--pause", metavar="SPORT", help="met un sport en pause (foot/tennis/basket)")
    ap.add_argument("--reactivate", metavar="SPORT", help="réactive un sport (seul moyen de sortir de pause)")
    a = ap.parse_args()
    if a.pause:
        sp = a.pause.strip().lower()
        print(f"{sp} mis en pause." if analyses.pause_sport(sp) else f"{sp} était déjà en pause.")
    if a.reactivate:
        sp = a.reactivate.strip().lower()
        if analyses.reactivate_sport(sp):
            print(f"✅ {sp} RÉACTIVÉ — ses paris/provisoires/combos re-sortent normalement dès le prochain scan.")
        else:
            print(f"{sp} n'était pas en pause (rien à faire).")
    if a.status or not (a.pause or a.reactivate):
        _status()


if __name__ == "__main__":
    main()
