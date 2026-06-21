"""Notifications Telegram (canal choisi par l'utilisateur).

Deux déclencheurs branchés ailleurs :
  • fin de scan  -> tools/generate_analyses.py (nouveaux paris)
  • règlement    -> app/settle_analyst.py (résultat d'un pari)

Configuration (jamais commitée — `data/` est gitignore) :
  data/notify.json : {"telegram_token": "123:ABC...", "telegram_chat_id": "12345678"}
  (chat_id peut être une liste séparée par des virgules pour notifier plusieurs destinataires)
Repli par variables d'environnement : BETSFIX_TG_TOKEN / BETSFIX_TG_CHAT.

Le module est TOLÉRANT : si non configuré ou si Telegram répond mal, il ne lève
jamais — une notif ratée ne doit JAMAIS casser un scan ni le règlement.
"""
from __future__ import annotations

import json
import logging
import os

import httpx

log = logging.getLogger("betsfix.notify")

_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "notify.json")


def _config() -> tuple[str | None, list[str]]:
    """(token, [chat_id, …]). Priorité aux variables d'environnement, repli sur data/notify.json."""
    tok = os.environ.get("BETSFIX_TG_TOKEN")
    chat = os.environ.get("BETSFIX_TG_CHAT")
    if not (tok and chat):
        try:
            c = json.load(open(_CFG_PATH, encoding="utf-8"))
            tok = tok or c.get("telegram_token")
            chat = chat or c.get("telegram_chat_id")
        except (OSError, ValueError):
            pass
    if not (tok and chat):
        return None, []
    chats = [s.strip() for s in str(chat).split(",") if s.strip()]
    return tok, chats


def configured() -> bool:
    tok, chats = _config()
    return bool(tok and chats)


async def send(text: str) -> bool:
    """Envoie `text` à tous les chats configurés. Renvoie True si au moins un envoi a réussi.
    No-op silencieux si non configuré ; n'élève jamais."""
    tok, chats = _config()
    if not (tok and chats):
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    ok = False
    try:
        async with httpx.AsyncClient(timeout=12) as cl:
            for ch in chats:
                try:
                    r = await cl.post(url, json={
                        "chat_id": ch, "text": text[:4000],
                        "disable_web_page_preview": True,
                    })
                    ok = ok or (r.status_code == 200)
                    if r.status_code != 200:
                        log.warning("notif Telegram %s -> HTTP %s : %s", ch, r.status_code, r.text[:200])
                except Exception as exc:                      # réseau / DNS transitoire
                    log.warning("notif Telegram %s échouée : %s", ch, exc)
    except Exception as exc:
        log.warning("notif Telegram (client) échouée : %s", exc)
    return ok


def send_sync(text: str) -> bool:
    """Variante synchrone (contextes hors boucle asyncio). Mêmes garanties de tolérance."""
    tok, chats = _config()
    if not (tok and chats):
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    ok = False
    try:
        with httpx.Client(timeout=12) as cl:
            for ch in chats:
                try:
                    r = cl.post(url, json={
                        "chat_id": ch, "text": text[:4000],
                        "disable_web_page_preview": True,
                    })
                    ok = ok or (r.status_code == 200)
                except Exception as exc:
                    log.warning("notif Telegram %s échouée : %s", ch, exc)
    except Exception as exc:
        log.warning("notif Telegram (client) échouée : %s", exc)
    return ok
