"""Compteur de CONSOMMATION du proxy résidentiel iProyal (facturé au Go). Somme les octets RÉELLEMENT
téléchargés via le proxy (Pinnacle + SofaScore, les 2 consommateurs) -> affiché dans le panneau Santé pour
suivre l'avancement du forfait (user 2026-08-13, abonnement 2 Go). Estimation locale de CE QU'ON TIRE : le
compteur iProyal officiel reste l'autorité, mais on est le seul consommateur de ce proxy -> proche du réel.

Persistant (`data/iproyal_usage.json`), démarré au rechargement. Zéro dépendance, best-effort (jamais lève)."""

from __future__ import annotations

import json
import os
import time as _time

_FILE = os.path.join("data", "iproyal_usage.json")
_CAP_GB = 2.0                        # forfait acheté (Go) — user 2026-08-13


def _load() -> dict:
    try:
        return json.load(open(_FILE, encoding="utf-8"))
    except Exception:
        return {"bytes": 0, "calls": 0, "since": int(_time.time())}


def add_bytes(n: int, host: str = "") -> None:
    """Ajoute `n` octets téléchargés via le proxy (best-effort, jamais d'exception)."""
    if not n or n < 0:
        return
    try:
        d = _load()
        d["bytes"] = int(d.get("bytes", 0)) + int(n)
        d["calls"] = int(d.get("calls", 0)) + 1
        if host:
            by = d.setdefault("by_host", {})
            by[host] = int(by.get(host, 0)) + int(n)
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        json.dump(d, open(_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def reset() -> None:
    """Remet le compteur à zéro (à appeler à chaque rechargement du forfait iProyal)."""
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        json.dump({"bytes": 0, "calls": 0, "since": int(_time.time())},
                  open(_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def stats() -> dict:
    """{used_mb, used_gb, cap_gb, pct, remaining_gb, calls, since} pour l'affichage Santé."""
    d = _load()
    used = int(d.get("bytes", 0))
    cap = _CAP_GB * 1e9
    return {
        "used_mb": round(used / 1e6, 1),
        "used_gb": round(used / 1e9, 3),
        "cap_gb": _CAP_GB,
        "pct": round(100 * used / cap, 1) if cap else 0.0,
        "remaining_gb": round(max(0.0, cap - used) / 1e9, 3),
        "calls": int(d.get("calls", 0)),
        "since": d.get("since"),
    }
