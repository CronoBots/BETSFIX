"""Santé des sources — RUN QUOTIDIEN (à appeler après le scan).

- Ping léger de CHAQUE source (analyse + règlement) via app/source_health.py (100 % réseau, aucun effet
  de bord sur les données).
- Ajoute UNE ligne au journal machine data/source_health_log.jsonl (trace jour après jour + latences).
- Alerte Telegram UNIQUEMENT si une source CRITIQUE (Unibet/FotMob) est DOWN (pas pour une source
  secondaire dégradée -> pas de bruit).

Usage :  python tools/source_health.py            # ping + journal + alerte si source critique down
         python tools/source_health.py --quiet    # n'imprime rien (sauf source critique down)
"""
import asyncio
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import source_health   # noqa: E402

_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "source_health_log.jsonl")


def main() -> int:
    quiet = "--quiet" in sys.argv
    rep = asyncio.run(source_health.check_all())

    # journal machine : 1 ligne compacte par run (statut + sources down + latences)
    try:
        line = {"ts": rep["ts"], "status": rep["status"], "down": rep["down"],
                "down_critical": rep["down_critical"],
                "lat": {s["key"]: s["latency_ms"] for s in rep["sources"]}}
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if not quiet or rep["down_critical"]:
        print(f"SANTÉ SOURCES {rep['status'].upper()} — {len(rep['down'])} down / {len(rep['sources'])}")
        for s in rep["sources"]:
            mark = "✅" if s["ok"] else "❌"
            crit = "🔴" if s["critical"] else "  "
            print(f"  {mark} {crit} {s['label']:20} {s['latency_ms']:5}ms  {s['detail']}")

    # ALERTE Telegram seulement si une source CRITIQUE est morte (Unibet/FotMob -> le pipeline est en péril)
    if rep["down_critical"]:
        try:
            from app import notify
            downs = [s for s in rep["sources"] if not s["ok"] and s["critical"]]
            lines = ["🚨 *BETSFIX — source CRITIQUE indisponible*", ""]
            for s in downs:
                lines.append(f"❌ *{s['label']}* ({s['role']}) — {s['detail']}")
            lines.append("")
            lines.append("Les analyses/règlements peuvent être dégradés tant que la source est down.")
            notify.send_sync("\n".join(lines))
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
