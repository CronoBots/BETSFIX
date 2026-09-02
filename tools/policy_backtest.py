"""Backtest de la POLITIQUE DE SÉLECTION (seuils confiance/EV/cote) — RUN (à la demande ou quotidien).

⚠️ Ne PAS confondre avec l'ancien `tools/backtest.py` (calibration Elo tennis via SofaScore, hérité/mort).
Ici on rejoue les SÉLECTEURS MÉCANIQUES de prod (`confidence_pick`/`value_pick`, refonte 2026-08-29/09-01 —
plus l'ancienne porte `_recommend`) sur l'historique par match, on balaye leurs VRAIS leviers (bornes de
confiance/cote/EV), et on PROPOSE un changement UNIQUEMENT s'il améliore le ROI hors-échantillon de façon
significative. **N'applique JAMAIS rien** (lecture seule) : appliquer un seuil reste une décision explicite.
Journal `data/backtest_log.jsonl` + alerte Telegram si une amélioration significative apparaît.

Usage :  python tools/policy_backtest.py            # rapport complet
         python tools/policy_backtest.py --quiet    # verdict + reco seulement (pour le cron)
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import backtest   # noqa: E402

_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backtest_log.jsonl")


def main() -> int:
    quiet = "--quiet" in sys.argv
    r = backtest.analyze()
    val = r["validation"]

    if not quiet:
        b = r["baseline"]
        print(f"BACKTEST — {r['universe_n']} prédictions rejouées | fidélité porte↔prod {val['pct']}% "
              f"({val['agree']}/{val['total']})")
        for k in ("overall", "test"):
            m = b[k]
            print(f"  politique actuelle {k:7}: n={m['n']:4} réussite={m['hit_rate']}% "
                  f"ROI={m['roi']}% [IC {m.get('roi_lo')}, {m.get('roi_hi')}]")
        for param, rows in r["sweeps"].items():
            print(f"  {param}:")
            for row in rows:
                o, t = row["overall"], row["test"]
                mk = " ←actuel" if row["value"] == backtest.DEFAULT_POLICY[param] else ""
                print(f"     {str(row['value']):>5}: overall n={o['n']:4} ROI={str(o['roi']):>6}% | "
                      f"test n={t['n']:3} ROI={str(t['roi']):>6}% (IC bas {t.get('roi_lo')}){mk}")

    print(f"VERDICT : {r['verdict']}")
    for rec in r["recommendations"][:5]:
        print(f"   → {rec['note']}")

    # garde-fou : si la porte ne reproduit plus prod (< 98 %), le miroir a divergé de _recommend -> le
    # backtest n'est PAS fiable, on ne conclut pas et on signale (à corriger avant d'y croire).
    faithful = (val.get("pct") or 0) >= 98
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"verdict": r["verdict"], "faithful_pct": val.get("pct"),
                                 "n": r["universe_n"], "recs": [x["note"] for x in r["recommendations"]]},
                                ensure_ascii=False) + "\n")
    except Exception:
        pass

    # ALERTE TELEGRAM COUPÉE (user 2026-08-18) : ce backtest est purement informatif (suggestion sur du ROI
    # souvent négatif = bruit) et n'applique JAMAIS rien -> il ne doit PLUS polluer le canal Telegram. Les
    # recommandations restent visibles dans la CONSOLE (ci-dessus) et le JOURNAL `data/backtest_log.jsonl`.
    # Pour réactiver : remettre le `notify.send_sync(...)` des recommandations ici.
    if r["recommendations"] and faithful:
        print("   (alerte Telegram coupée — recommandations au log/console seulement)")
    if not faithful:
        print(f"⚠️ porte↔prod seulement {val.get('pct')}% : miroir divergent, backtest non concluant.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
