"""Reconstruit TOUTES les notes du modèle, en une fois.

Enchaîne : Elo (force des joueurs) -> tendances d'aces -> domination service/retour.
Lancé automatiquement chaque semaine par la boucle de fond (app/main.py), ou à la main
(double-clic build_data.bat, ou `python tools/build_data_all.py`).

Chaque étape est isolée : si l'une échoue, les autres se font quand même.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

import build_basket_elo  # noqa: E402
import build_foot_elo  # noqa: E402
import build_serve_return  # noqa: E402
import build_tendencies  # noqa: E402


def main():
    steps = (("tendances d'aces", build_tendencies.main),
             ("domination service/retour", build_serve_return.main),
             ("Elo d'équipe basket (WNBA)", build_basket_elo.main),
             ("Elo des sélections (foot)", build_foot_elo.main))
    for name, fn in steps:
        print(f"\n========== {name} ==========")
        try:
            fn()
        except Exception as exc:  # ne bloque pas les étapes suivantes
            print(f"[!] Étape '{name}' échouée : {exc}")
    print("\n✓ Reconstruction des notes terminée.")


if __name__ == "__main__":
    main()
