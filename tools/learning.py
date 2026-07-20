"""Journal d'apprentissage — RUN QUOTIDIEN (à appeler après le scan/règlement).

Prend la photo du jour, calcule les deltas vs la veille, auto-écrit les événements notables dans
LEARNING.md. 100 % lecture seule côté données de paris (n'écrit que son propre journal).

Usage :  python tools/learning.py            # enregistre + imprime deltas + nouveaux événements
         python tools/learning.py --quiet
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import learning   # noqa: E402


def main() -> int:
    quiet = "--quiet" in sys.argv
    r = learning.record()
    d = r.get("deltas") or {}
    if not quiet:
        t = r["today"]
        print(f"APPRENTISSAGE {t['date']} — fiabilité {t.get('reliability')} ({t.get('reliability_trend')}) "
              f"· {(t.get('cal') or {}).get('n')} prédictions · ROI joué {(t.get('stats') or {}).get('roi')}")
        if d:
            print(f"  Δ depuis {d.get('since')} : fiabilité {d.get('reliability')}, ROI {d.get('stats_roi')}, "
                  f"+{d.get('settled')} réglés, +{d.get('n_predictions')} prédictions")
        else:
            print("  (premier relevé — pas encore de comparaison)")
    for e in r.get("new_events") or []:
        print(f"  ★ {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
