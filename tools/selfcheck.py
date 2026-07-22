"""Auto-audit d'intégrité — RUN QUOTIDIEN (à appeler après le scan/règlement).

- Lance tous les contrôles de app/selfcheck.py (100 % lecture seule).
- Met à jour le filigrane de monotonicité (persist=True).
- Ajoute UNE ligne au journal machine data/selfcheck_log.jsonl (trace jour après jour).
- Alerte Telegram UNIQUEMENT si des ERREURS (pas pour un simple warn -> pas de bruit).

Usage :  python tools/selfcheck.py            # run + persist + journal + alerte si erreur
         python tools/selfcheck.py --no-persist
         python tools/selfcheck.py --quiet    # n'imprime rien (sauf erreurs)
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import selfcheck   # noqa: E402

_ICON = {"ok": "✅", "info": "ℹ️", "warn": "⚠️", "error": "❌"}


def main() -> int:
    persist = "--no-persist" not in sys.argv
    quiet = "--quiet" in sys.argv
    rep = selfcheck.run(persist=persist)

    # journal machine : 1 ligne compacte par run (jour après jour)
    try:
        line = {"ts": rep["ts"], "status": rep["status"], "counts": rep["counts"],
                "flags": [c["key"] for c in rep["checks"] if c["level"] in ("warn", "error")]}
        with open(selfcheck._LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if not quiet or rep["status"] == "error":
        print(f"AUTO-AUDIT {rep['status'].upper()} — {rep['counts']} — {rep['sidecars']} fiches")
        for c in rep["checks"]:
            if c["level"] != "ok" or not quiet:
                print(f"  {_ICON[c['level']]} {c['title']} — {c['detail']}")
                for it in c["items"][:6]:
                    print(f"      - {it}")

    # ALERTE Telegram : sur ERREUR (confusion stats/règlement réelle) OU sur un WARN de SURVEILLANCE ciblé
    # (_ALERT_ON_WARN) — demande user 2026-07-22 : être prévenu si la sur-confiance du marché « Sets » tennis
    # revient, SANS bruiter sur les autres warns (la politique « alerte seulement sur erreur » reste pour eux).
    _ALERT_ON_WARN = {"tennis_sets_overconfidence"}
    alerts = [c for c in rep["checks"]
              if c["level"] == "error" or (c["level"] == "warn" and c["key"] in _ALERT_ON_WARN)]
    if alerts:
        try:
            from app import notify
            lines = ["⚠️ *BETSFIX — auto-audit : à surveiller*", ""]
            for c in alerts:
                _ic = "❌" if c["level"] == "error" else "⚠️"
                lines.append(f"{_ic} *{c['title']}* — {c['detail']}")
                for it in c["items"][:4]:
                    lines.append(f"  • {it}")
            notify.send_sync("\n".join(lines))
        except Exception:
            pass
        return 1 if any(c["level"] == "error" for c in alerts) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
