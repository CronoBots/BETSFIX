"""Contrôle de la MONTANTE (fonctionnalité préparée 2026-07-24 ; activation = un simple interrupteur).

Usage :
  python tools/montante.py --status        # état courant + activée ou non
  python tools/montante.py --activate       # ACTIVE l'enregistrement quotidien (interrupteur ON)
  python tools/montante.py --deactivate     # coupe l'enregistrement (retour à la simulation)
  python tools/montante.py --run            # exécute UN cycle (règle l'en-cours + enregistre le pari du
                                            #   jour) — no-op si non activée. La tâche reconcile l'appelle.

Tant que la montante n'est pas activée : rien n'est enregistré, la page /montante affiche la SIMULATION
sur les simples foot. Une fois activée, un pari foot par jour (le plus sûr) est enregistré et réglé par
nos résultats. TOTALEMENT ISOLÉ (data/montante_track.json), hors ROI.
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
