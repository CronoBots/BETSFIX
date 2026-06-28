"""Floutage serveur des PRONOS pour les non-abonnés.

Principe (sûr ET compatible avec le cache de panneaux) :
- Le HTML d'un pari est entouré de MARQUEURS commentaires `<!--PRONO-->…<!--/PRONO-->`. Ces marqueurs
  sont posés au rendu (donc présents dans le cache partagé), mais NE sont jamais envoyés tels quels.
- Au moment d'envoyer la réponse (middleware, app/main.py), on réécrit le HTML :
    • abonné / propriétaire -> on RETIRE juste les marqueurs (le pari s'affiche normalement) ;
    • visiteur non abonné  -> on REMPLACE tout le bloc marqué par un cache « 🔒 Réservé aux abonnés ».
  Le non-abonné ne reçoit donc JAMAIS les octets du pari (vrai paywall, pas un simple flou CSS).

Les stats, résultats, matchs et cotes de marché ne sont PAS marqués -> restent publics pour tous.
"""
from __future__ import annotations

import re

MARK_OPEN = "<!--PRONO-->"
MARK_CLOSE = "<!--/PRONO-->"
_BLOCK = re.compile(re.escape(MARK_OPEN) + r".*?" + re.escape(MARK_CLOSE), re.S)

# Cache affiché à la place du pari pour un non-abonné. Lien vers /compte (connexion / abonnement).
_LOCK = ('<a class="prono-lock" href="/compte">'
         '<span class="prono-lock-i">🔒</span>'
         '<span class="prono-lock-t"><b>Pari réservé aux abonnés</b>'
         '<small>Connecte-toi ou abonne-toi pour voir le prono</small></span>'
         '<span class="prono-lock-go">Débloquer →</span></a>')


def wrap(inner: str) -> str:
    """Entoure un HTML de pari des marqueurs paywall. No-op si déjà vide."""
    if not inner:
        return inner
    return f"{MARK_OPEN}{inner}{MARK_CLOSE}"


def has_marks(text: str) -> bool:
    return MARK_OPEN in text


def apply(html: str, can_see: bool) -> str:
    """Réécrit le HTML selon le droit du visiteur. Idempotent : sans marqueur, renvoie tel quel."""
    if MARK_OPEN not in html:
        return html
    if can_see:
        return html.replace(MARK_OPEN, "").replace(MARK_CLOSE, "")
    return _BLOCK.sub(_LOCK, html)
