"""Sélection des matchs IMPORTANTS à analyser en profondeur — AVANT toute analyse coûteuse.

Critère unique : **profondeur de marché Unibet** (`nonLiveBoCount` du listView) = l'importance
selon le book. Les gros matchs ont 400-549 marchés, la médiane ~23, l'eSports et les ligues
obscures 0-2. On EXCLUT l'eSports et on garde le **top N par sport** (défaut 10/jour).

Volontairement indépendant de l'Elo du modèle (qui se trompe) : on se fie au marché + au volume
d'offres, dispo dans le listView SANS appel réseau par match. Fonctions pures + un fetch async.
"""

from __future__ import annotations

UNIBET_B = "https://eu-offering-api.kambicdn.com/offering/v2018/ubbe"
UNIBET_PARAMS = {"lang": "fr_BE", "market": "BE", "client_id": "2", "channel_id": "1"}
# listView Unibet par sport de l'app.
LISTVIEW = {"foot": "football", "tennis": "tennis", "basket": "basketball"}


def _is_esport(group: str, path_names: list) -> bool:
    """Vrai si l'évènement est de l'eSports (à exclure) — détecté sur le nom de groupe/chemin."""
    blob = (group + " " + " ".join(path_names)).lower()
    return "esport" in blob or "e-sport" in blob or "cyber" in blob


def rank_important(events: list, top_n: int = 10) -> list:
    """Depuis les items `events` d'un listView Unibet, renvoie le TOP N matchs par profondeur de
    marché (`nonLiveBoCount`), eSports exclus. Chaque item : id, name, home, away, comp, markets, start."""
    rows = []
    for it in events or []:
        ev = it.get("event", it) if isinstance(it, dict) else {}
        group = ev.get("group") or ""
        path_names = [p.get("name", "") for p in (ev.get("path") or [])]
        if _is_esport(group, path_names):
            continue
        rows.append({
            "id": ev.get("id"),
            "name": ev.get("name", ""),
            "home": ev.get("homeName", ""),
            "away": ev.get("awayName", ""),
            "comp": group,
            "markets": ev.get("nonLiveBoCount", 0) or 0,
            "start": ev.get("start"),
        })
    rows.sort(key=lambda r: r["markets"], reverse=True)
    return rows[:top_n]


async def fetch_important(sport: str, top_n: int = 10, client=None) -> list:
    """Récupère le listView Unibet du sport et renvoie le top N matchs importants (cf. rank_important)."""
    import httpx
    path = LISTVIEW.get(sport, "football")
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(f"{UNIBET_B}/listView/{path}.json", params=UNIBET_PARAMS,
                             headers={"User-Agent": "Mozilla/5.0"})
        events = (r.json() or {}).get("events") or []
    finally:
        if own:
            await client.aclose()
    return rank_important(events, top_n)
