"""Notifications PUSH web (PWA) — « nouveau prono » (demande user 2026-08-16).

Chaîne complète :
  navigateur (service worker + pushManager.subscribe avec la CLÉ PUBLIQUE VAPID)
   -> POST /push/subscribe  -> abonnement stocké ici (data/push_subs.json)
   -> à la publication d'un prono : `push.notify_new_prono(...)` chiffre + envoie à chaque abonnement
      (pywebpush + clé PRIVÉE VAPID) -> le service worker affiche la notification.

Best-effort STRICT : toute panne (clé, réseau, lib absente) -> log debug, JAMAIS d'exception qui casse le
scan ou une route. Les abonnements MORTS (404/410) sont purgés automatiquement.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading

log = logging.getLogger("betsfix.push")

_DATA = "data"
_VAPID_PEM = os.path.join(_DATA, "push_vapid.pem")       # clé PRIVÉE VAPID (PKCS8 PEM)
_VAPID_PUB = os.path.join(_DATA, "push_vapid_pub.txt")   # clé PUBLIQUE (b64url, applicationServerKey)
_SUBS = os.path.join(_DATA, "push_subs.json")            # [{endpoint, keys:{p256dh,auth}}, ...]
_SUB_CLAIM = "mailto:noreply@betsfix.com"                # identifiant de l'expéditeur (VAPID `sub`)

_LOCK = threading.Lock()
_PUB_CACHE: str | None = None


def _ensure_keys() -> None:
    """Génère la paire VAPID (EC P-256) au 1er appel si absente. PEM privé + clé publique b64url."""
    if os.path.exists(_VAPID_PEM) and os.path.exists(_VAPID_PUB):
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    raw = priv.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)  # 65 o
    pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    os.makedirs(_DATA, exist_ok=True)
    with open(_VAPID_PEM, "wb") as f:
        f.write(pem)
    with open(_VAPID_PUB, "w", encoding="utf-8") as f:
        f.write(pub)


def public_key() -> str:
    """Clé publique VAPID (b64url) = `applicationServerKey` du client. '' si indispo (lib absente…)."""
    global _PUB_CACHE
    if _PUB_CACHE is not None:
        return _PUB_CACHE
    try:
        _ensure_keys()
        with open(_VAPID_PUB, encoding="utf-8") as f:
            _PUB_CACHE = f.read().strip()
    except Exception as exc:                     # lib crypto absente / disque : push désactivé proprement
        log.debug("push public_key indispo: %s", exc)
        _PUB_CACHE = ""
    return _PUB_CACHE


def _load_subs() -> list:
    try:
        return json.load(open(_SUBS, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_subs(subs: list) -> None:
    try:
        os.makedirs(_DATA, exist_ok=True)
        tmp = _SUBS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(subs, f, ensure_ascii=False)
        os.replace(tmp, _SUBS)
    except OSError as exc:
        log.debug("push _save_subs: %s", exc)


def add_sub(sub: dict) -> bool:
    """Enregistre un abonnement push (dédupliqué par endpoint). True si ajouté/déjà présent."""
    ep = (sub or {}).get("endpoint")
    if not ep or not (sub.get("keys") or {}).get("p256dh"):
        return False
    with _LOCK:
        subs = _load_subs()
        if not any(s.get("endpoint") == ep for s in subs):
            subs.append({"endpoint": ep, "keys": sub.get("keys")})
            _save_subs(subs)
    return True


def remove_sub(endpoint: str) -> None:
    if not endpoint:
        return
    with _LOCK:
        subs = [s for s in _load_subs() if s.get("endpoint") != endpoint]
        _save_subs(subs)


def sub_count() -> int:
    return len(_load_subs())


def send_push(title: str, body: str, url: str = "/", tag: str = "prono") -> int:
    """Envoie une notification à TOUS les abonnés. Purge les abonnements morts (404/410). Renvoie le
    nombre d'envois réussis. Best-effort : jamais d'exception propagée."""
    try:
        from pywebpush import webpush, WebPushException
    except Exception as exc:
        log.debug("pywebpush absent: %s", exc)
        return 0
    try:
        _ensure_keys()
    except Exception as exc:
        log.debug("push clés indispo: %s", exc)
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    subs = _load_subs()
    ok, dead = 0, []
    for s in subs:
        try:
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": s["keys"]},
                    data=payload, vapid_private_key=_VAPID_PEM,
                    vapid_claims={"sub": _SUB_CLAIM}, ttl=3600)
            ok += 1
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):               # abonnement expiré/révoqué -> purge
                dead.append(s.get("endpoint"))
            else:
                log.debug("push envoi KO (%s): %s", code, exc)
        except Exception as exc:
            log.debug("push envoi erreur: %s", exc)
    if dead:
        with _LOCK:
            rest = [s for s in _load_subs() if s.get("endpoint") not in dead]
            _save_subs(rest)
    return ok


def notify_new_prono(match: str, pick: str, sport: str = "foot") -> int:
    """Notif « nouveau prono » (appelée à la publication). `match` = « A - B », `pick` = le pari + cote."""
    title = "⚽ Nouveau prono BETSFIX"
    body = f"{match} — {pick}" if pick else match
    return send_push(title, body, url="/", tag="prono")
