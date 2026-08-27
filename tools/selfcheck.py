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

    # ALERTE Telegram : uniquement sur ERREUR (confusion stats/règlement réelle). La surveillance « Sets »
    # tennis a été retirée avec les autres sports (app 100 % foot) -> plus aucun WARN n'alerte, que des erreurs.
    _ALERT_ON_WARN: set = set()
    alerts = [c for c in rep["checks"]
              if c["level"] == "error" or (c["level"] == "warn" and c["key"] in _ALERT_ON_WARN)]
    if alerts:
        # ANTI-SPAM (bug user 2026-07-28 : message Telegram EN BOUCLE — selfcheck tourne souvent (reconcile /
        # runs manuels) et re-postait la MÊME alerte à chaque fois). On ne re-notifie une alerte IDENTIQUE
        # qu'au plus une fois toutes les _COOLDOWN_H heures (dé-dup par signature). Une alerte NOUVELLE
        # (nouvelle erreur / autre marché) passe tout de suite.
        _COOLDOWN_H = 12
        _sig = "|".join(sorted(f"{c['key']}:{c['level']}" for c in alerts))
        _state_path = os.path.join(os.path.dirname(selfcheck._LOG), "selfcheck_alert_state.json")
        _send = True
        try:
            from datetime import datetime
            _now = datetime.fromisoformat(rep["ts"])
            with open(_state_path, encoding="utf-8") as fh:
                _prev = json.load(fh)
            if _prev.get("sig") == _sig and _prev.get("ts"):
                if (_now - datetime.fromisoformat(_prev["ts"])).total_seconds() < _COOLDOWN_H * 3600:
                    _send = False               # même alerte déjà notifiée récemment -> on ne re-spamme pas
        except Exception:
            _send = True                        # état illisible / 1er run -> on notifie
        if _send:
            try:
                from app import notify
                lines = ["⚠️ *BETSFIX — auto-audit : à surveiller*", ""]
                for c in alerts:
                    _ic = "❌" if c["level"] == "error" else "⚠️"
                    lines.append(f"{_ic} *{c['title']}* — {c['detail']}")
                    for it in c["items"][:4]:
                        lines.append(f"  • {it}")
                notify.send_sync("\n".join(lines))
                with open(_state_path, "w", encoding="utf-8") as fh:   # mémorise la dernière notif envoyée
                    json.dump({"sig": _sig, "ts": rep["ts"]}, fh)
            except Exception:
                pass
        return 1 if any(c["level"] == "error" for c in alerts) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
