"""Client HTTP minimal SofaScore + utilitaires partagés par les outils tennis
(explore_aces / explore_breaks / explore_serve_return / explore_sets).

Extrait de l'ancien tools/build_elo.py lors du RETRAIT du signal Elo tennis
(2026-06-17). Ne contient AUCUNE logique Elo : juste l'accès réseau commun.
"""
from __future__ import annotations

import httpx

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
     "Origin": "https://www.sofascore.com"}
B = "https://api.sofascore.com/api/v1"
TOURNAMENTS = {"atp": 2480, "wta": 2577}


def _get(client, path):
    try:
        r = client.get(path, timeout=25)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def _is_singles(ev) -> bool:
    """Exclut le double : équipes à deux joueurs ou libellé 'X / Y'."""
    for side in ("homeTeam", "awayTeam"):
        t = ev.get(side) or {}
        if t.get("subTeams") or "/" in (t.get("name") or ""):
            return False
    return True
