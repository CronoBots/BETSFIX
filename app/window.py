"""Fenêtre de récupération des matchs — **logique commune aux 3 sports** (foot, basket, tennis).

Un seul endroit définit « jusqu'à quand on va chercher les matchs à venir ». Foot, basket et
tennis importent tous d'ici : changer `HORIZON_HOURS` ici suffit à tout aligner, sans risque
qu'un sport diverge des autres.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# Fenêtre unique : seuls les matchs commençant dans les prochaines HORIZON_HOURS sont
# récupérés/traités (moins de matchs = moins d'appels SofaScore/Unibet = moins de pauses).
HORIZON_HOURS = 24


def cutoff(now: datetime | None = None) -> datetime:
    """Borne haute de la fenêtre : on ignore tout match débutant après `now + HORIZON_HOURS`."""
    return (now or datetime.now(timezone.utc)) + timedelta(hours=HORIZON_HOURS)


def agenda_days() -> int:
    """Nombre de jours d'agenda quotidien à tirer pour couvrir la fenêtre, passage de minuit
    inclus (ex. 24 h -> aujourd'hui + demain = 2). Utilisé par les sports qui parcourent
    l'agenda jour par jour (basket, tennis)."""
    return math.ceil(HORIZON_HOURS / 24) + 1


def _as_dt(start) -> datetime | None:
    """datetime UTC depuis un ISO str, un epoch (s) ou un datetime ; None si non interprétable."""
    if isinstance(start, datetime):
        dt = start
    elif isinstance(start, (int, float)):
        dt = datetime.fromtimestamp(start, tz=timezone.utc)
    elif isinstance(start, str):
        try:
            dt = datetime.fromisoformat(start)
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def within(start, now: datetime | None = None) -> bool:
    """Vrai si le match (heure `start` = ISO/epoch/datetime) tombe dans la fenêtre [now, now+H].
    Filtre d'AFFICHAGE des picks lus dans le store : un match au-delà de la fenêtre n'est pas
    montré comme « à venir » même s'il traîne encore dans le suivi. Heure inconnue -> non exclu."""
    dt = _as_dt(start)
    if dt is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now <= dt <= cutoff(now)
