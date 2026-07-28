"""RÔLE DE L'INSTANCE (préparation split cloud/collecteur, demande user 2026-07-28).

BETSFIX fait aujourd'hui DEUX métiers sur la même machine :
  • COLLECTEUR (maison, IP résidentielle) : scan `claude -p`, règlement, scores live, suivi des cotes,
    re-post Telegram… → parle aux SOURCES (Unibet/Pinnacle/FotMob/LiveScore/…), doit rester sur une
    connexion résidentielle pour ne pas être détecté comme bot.
  • SERVEUR (cloud, always-on) : sert le site aux utilisateurs. Ne parle JAMAIS aux sources → aucun
    risque anti-bot, peut vivre dans le cloud 24/7 même PC principal éteint.

Ce module expose le rôle courant. Par DÉFAUT = `collector` -> l'installation actuelle (maison) ne change
PAS d'un poil. On ne bascule en `server` que sur le VPS (via l'env `BETSFIX_ROLE=server` ou le fichier
`data/role.flag` contenant « server »).

En rôle `server` :
  • les boucles de COLLECTE sont désactivées (règlement, suivi cotes, refresh combo, warm combo) — le
    cloud ne scrape rien et n'écrit JAMAIS les analyses (source de vérité = le collecteur qui les pousse) ;
  • seules les optimisations de SERVICE tournent (pré-chauffe des panneaux, lecture du snapshot stats).
"""
from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FLAG = os.path.join(_ROOT, "data", "role.flag")

_VALID = {"collector", "server"}


def role() -> str:
    """Rôle courant : 'collector' (défaut) ou 'server'. Env `BETSFIX_ROLE` prioritaire, sinon
    `data/role.flag`, sinon 'collector'. Toute valeur inconnue -> 'collector' (fail-safe : en cas de
    doute on garde le comportement maison complet, jamais un serveur muet par erreur)."""
    v = (os.environ.get("BETSFIX_ROLE") or "").strip().lower()
    if v not in _VALID:
        try:
            with open(_FLAG, encoding="utf-8") as f:
                v = f.read().strip().lower()
        except OSError:
            v = ""
    return v if v in _VALID else "collector"


def is_server() -> bool:
    return role() == "server"


def is_collector() -> bool:
    return role() == "collector"
