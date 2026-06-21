"""Configuration du bot Telegram pour les notifications BETSFIX.

Étapes (une seule fois) :
  1. Sur Telegram, ouvre @BotFather -> /newbot -> choisis un nom -> il te donne un TOKEN
     (du genre 123456789:AAH...). Garde-le.
  2. Cherche TON bot dans Telegram et envoie-lui n'importe quel message (ex. « salut »).
  3. Lance :  python tools/notify_setup.py --token 123456789:AAH...
     -> le script détecte automatiquement ton chat_id, écrit data/notify.json et
        t'envoie un message de test.

Autres usages :
  python tools/notify_setup.py --token <TOKEN> --chat <CHAT_ID>   # chat_id fourni à la main
  python tools/notify_setup.py --test                            # renvoie un test avec la config actuelle
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

try:                                  # console Windows (cp1252) -> évite UnicodeEncodeError sur ✓/✗
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "data", "notify.json")


def _detect_chat(token: str) -> str | None:
    """Récupère le chat_id du dernier message reçu par le bot (getUpdates)."""
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        print(f"  ✗ getUpdates a échoué : {data}")
        return None
    chats = []
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        ch = (msg.get("chat") or {}).get("id")
        if ch is not None:
            chats.append(str(ch))
    if not chats:
        print("  ✗ Aucun message reçu. Envoie d'abord un message à ton bot dans Telegram, puis relance.")
        return None
    return chats[-1]   # le plus récent


def _save(token: str, chat: str) -> None:
    os.makedirs(os.path.dirname(_CFG), exist_ok=True)
    json.dump({"telegram_token": token, "telegram_chat_id": chat},
              open(_CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  ✓ Configuration écrite : {_CFG}")


def _test() -> bool:
    sys.path.insert(0, _ROOT)
    from app import notify
    if not notify.configured():
        print("  ✗ Pas de configuration trouvée (data/notify.json absent et variables d'env vides).")
        return False
    ok = notify.send_sync("✅ BETSFIX : notifications Telegram activées. "
                          "Tu recevras les nouveaux scans et les paris réglés.")
    print("  ✓ Message de test envoyé." if ok else "  ✗ Échec de l'envoi du test (vérifie token/chat_id).")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Configure les notifications Telegram BETSFIX.")
    ap.add_argument("--token", help="Token du bot (donné par @BotFather).")
    ap.add_argument("--chat", help="chat_id (sinon détecté automatiquement via getUpdates).")
    ap.add_argument("--test", action="store_true", help="Envoie juste un message de test avec la config actuelle.")
    args = ap.parse_args()

    if args.test and not args.token:
        _test()
        return
    if not args.token:
        ap.error("--token requis (ou --test pour tester la config existante).")

    chat = args.chat or _detect_chat(args.token)
    if not chat:
        sys.exit(1)
    print(f"  ✓ chat_id : {chat}")
    _save(args.token, chat)
    _test()


if __name__ == "__main__":
    main()
