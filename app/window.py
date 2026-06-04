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
