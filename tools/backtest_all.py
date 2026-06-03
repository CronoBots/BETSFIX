"""Orchestrateur patient des backtests foot + basket.

La collecte SofaScore est rate-limitée : chaque passe des scripts AVANCE la collecte (cache
reprenable) puis s'arrête si le rate-limit retombe. Ce script relance les deux par vagues
espacées (15 min) jusqu'à ce que les caches soient prêts, puis exécute les backtests. Toute la
sortie est écrite dans data/backtest_results.txt (consultable à tout moment).

Lancement (en fond) :  python tools/backtest_all.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOOT_CACHE = os.path.join(_ROOT, "data", "foot_backtest_events.json")
BASK_CACHE = os.path.join(_ROOT, "data", "basket_backtest_events.json")
RESULTS = os.path.join(_ROOT, "data", "backtest_results.txt")
PY = sys.executable
ROUNDS, GAP_S = 14, 900   # jusqu'à ~3,5 h, une vague toutes les 15 min


def _ready(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f)) >= 200
    except Exception:
        return False


def main() -> None:
    with open(RESULTS, "w", encoding="utf-8") as out:
        for rnd in range(ROUNDS):
            for script in ("backtest_foot.py", "backtest_basket.py"):
                r = subprocess.run([PY, os.path.join("tools", script)], cwd=_ROOT,
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                out.write(f"\n===== vague {rnd + 1} · {script} =====\n"
                          f"{r.stdout or ''}{r.stderr or ''}")
                out.flush()
            if _ready(FOOT_CACHE) and _ready(BASK_CACHE):
                out.write("\n✓ Les deux caches sont prêts — backtests exécutés ci-dessus.\n")
                out.flush()
                break
            time.sleep(GAP_S)
        else:
            out.write("\n⚠️ Collecte incomplète après toutes les vagues (SofaScore récalcitrant).\n")


if __name__ == "__main__":
    main()
