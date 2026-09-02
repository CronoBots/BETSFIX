"""Contrôle MANUEL de la MONTANTE — inspection et interrupteur de secours.

⚠️ ÉTAT RÉEL (MAJ 2026-09-02) : la montante est **RÉACTIVÉE et AUTOMATIQUE** depuis le 2026-09-01.
Le pari du jour est choisi MÉCANIQUEMENT par `montante.pick_confidence_day` (vivier fantômes,
familles Vainqueur/DC/Total équipe, VRAIE cote Unibet bornée 1.25-1.55, confiance ≥80, PASS si rien),
appelé par le SCAN ; le règlement passe par `settle_pending`. Capital COMPOSÉ, amorcé à la série
réelle du propriétaire. Publiée sur le site, Telegram OFF (`TG_COMBO_MONTANTE=False`).
Hors overall/ROI (`MONTANTE_ROI_ON=False`) : unité composée ≠ mise plate.

Ce script ne fait donc PAS tourner la montante au quotidien — c'est un outil d'INSPECTION et un
interrupteur de secours. `--run` reste utile pour forcer un cycle à la main si le scan a échoué.

Usage :
  python tools/montante.py --status        # état courant (capital, palier, pari en attente, candidat)
  python tools/montante.py --run           # force UN cycle (règle l'en-cours + enregistre le jour)
  python tools/montante.py --activate      # interrupteur ON  (normalement déjà actif)
  python tools/montante.py --deactivate    # interrupteur OFF (repasse la page en simulation)

TOTALEMENT ISOLÉ dans data/montante_track.json. Mémoire `montante-reactivated-confidence-auto`.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import montante  # noqa: E402


def _today_iso() -> str:
    try:
        from app import web
        return web._sport_today().isoformat()          # jour sportif 06h→06h (cohérent avec le reste)
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()


def _status() -> None:
    st = montante.state()
    print("=== Montante ===")
    print(f"  activée         : {'OUI' if montante.is_active() else 'non (simulation affichée)'}")
    print(f"  paris enregistrés: {len((montante.load().get('steps') or []))}")
    if st.get("active"):
        print(f"  capital courant : {st['capital']} € · palier {st['palier']}")
        if st.get("pending"):
            p = st["pending"]
            print(f"  en attente      : {p.get('match')} · {p.get('sel')} @ {p.get('cote')}")
    else:
        sim = montante.simulate()
        print(f"  (simulation simples foot : meilleure montante {sim['capital']} € en "
              f"{sim['palier']} paliers, {sim['stats']['n']} montantes)")
    nxt = montante.pick_day_bet()
    if nxt:
        print(f"  prochain candidat : {nxt['match']} · {nxt['sel']} @ {nxt['cote']} (conf {nxt['prob']})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Contrôle de la montante quotidienne (interrupteur d'activation).")
    ap.add_argument("--status", action="store_true", help="affiche l'état")
    ap.add_argument("--activate", action="store_true", help="ACTIVE l'enregistrement quotidien")
    ap.add_argument("--deactivate", action="store_true", help="coupe l'enregistrement (retour simulation)")
    ap.add_argument("--run", action="store_true", help="exécute un cycle (règle + enregistre) si activée")
    a = ap.parse_args()
    if a.activate:
        montante.activate(True)
        print("✅ Montante ACTIVÉE — un pari foot/jour sera enregistré et réglé. (page = vraies données)")
    if a.deactivate:
        montante.activate(False)
        print("⏸ Montante désactivée — la page repasse en simulation.")
    if a.run:
        print("cycle:", montante.run_daily(_today_iso()))
    if a.status or not (a.activate or a.deactivate or a.run):
        _status()


if __name__ == "__main__":
    main()
