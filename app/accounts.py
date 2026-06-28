"""Comptes & sessions BETSFIX (Phase 1 du paywall abonnement).

Objectif : un visiteur peut créer un compte (email + mot de passe), se connecter, et l'app sait s'il
est ABONNÉ. Les pronos ne sont servis qu'aux abonnés (cf. app/paywall.py) ; les stats/résultats restent
publics. Le STATUT d'abonnement (`sub_active`) est mis à jour par Stripe en Phase 2 (webhook) ; ici on
ne fait que le stocker/lire.

Zéro dépendance externe : mot de passe haché en PBKDF2-HMAC-SHA256 (stdlib), session = cookie SIGNÉ
(HMAC) sans état serveur. Store JSON atomique dans data/accounts.json (gitignore).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data")
_STORE = os.path.join(_DATA, "accounts.json")
_SECRET_FILE = os.path.join(_DATA, ".session_secret")

COOKIE = "bx_session"
_SESSION_MAX_AGE = 60 * 24 * 3600          # 60 jours
_PBKDF2_ROUNDS = 200_000
_lock = threading.Lock()

# Emails TOUJOURS considérés abonnés (le propriétaire). Séparés par virgule dans BETSFIX_OWNER_EMAIL.
_OWNERS = {e.strip().lower() for e in (os.environ.get("BETSFIX_OWNER_EMAIL") or "").split(",") if e.strip()}


# --------------------------------------------------------------------------- secret de signature
def _secret() -> bytes:
    """Secret HMAC des sessions : env BETSFIX_SESSION_SECRET, sinon fichier généré une fois."""
    env = os.environ.get("BETSFIX_SESSION_SECRET")
    if env:
        return env.encode()
    try:
        with open(_SECRET_FILE, "rb") as f:
            data = f.read().strip()
            if data:
                return data
    except OSError:
        pass
    sec = secrets.token_hex(32).encode()
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_SECRET_FILE, "wb") as f:
            f.write(sec)
    except OSError:
        pass
    return sec


# --------------------------------------------------------------------------- store
def _load() -> dict:
    try:
        with open(_STORE, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(_DATA, exist_ok=True)
    tmp = _STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STORE)             # atomique


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    e = _norm(email)
    return bool(e) and e.count("@") == 1 and "." in e.split("@")[-1] and " " not in e and len(e) <= 200


# --------------------------------------------------------------------------- mot de passe
def hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}:{dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = (stored or "").split(":")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# --------------------------------------------------------------------------- comptes
def get_user(email: str) -> dict | None:
    return _load().get(_norm(email))


def create_user(email: str, pw: str) -> tuple[bool, str]:
    """Crée un compte. (True, '') si OK, sinon (False, message d'erreur)."""
    email = _norm(email)
    if not valid_email(email):
        return False, "Adresse email invalide."
    if len(pw or "") < 8:
        return False, "Le mot de passe doit faire au moins 8 caractères."
    with _lock:
        data = _load()
        if email in data:
            return False, "Un compte existe déjà avec cet email."
        data[email] = {"pw": hash_pw(pw), "created": int(time.time()),
                       "sub_active": False, "sub_until": None,
                       "stripe_customer": None, "stripe_sub": None}
        _save(data)
    return True, ""


def verify_login(email: str, pw: str) -> bool:
    u = get_user(email)
    return bool(u and verify_pw(pw, u.get("pw", "")))


def is_subscriber(email: str) -> bool:
    """Abonné actif ? Propriétaire = toujours oui. Sinon `sub_active` (Stripe), avec tolérance
    d'une date de fin `sub_until` future (au cas où sub_active n'a pas encore été rebasculé)."""
    email = _norm(email)
    if not email:
        return False
    if email in _OWNERS:
        return True
    u = get_user(email)
    if not u:
        return False
    if u.get("sub_active"):
        return True
    until = u.get("sub_until")
    return bool(until and until > time.time())


def set_subscription(email: str, active: bool, until: float | None = None,
                     stripe_customer: str | None = None, stripe_sub: str | None = None) -> None:
    """Met à jour le statut d'abonnement (appelé par le webhook Stripe en Phase 2)."""
    email = _norm(email)
    with _lock:
        data = _load()
        u = data.get(email)
        if not u:                       # Stripe peut connaître un email sans compte local -> on le crée vide
            u = {"pw": "", "created": int(time.time())}
        u["sub_active"] = bool(active)
        if until is not None:
            u["sub_until"] = until
        if stripe_customer is not None:
            u["stripe_customer"] = stripe_customer
        if stripe_sub is not None:
            u["stripe_sub"] = stripe_sub
        data[email] = u
        _save(data)


# --------------------------------------------------------------------------- sessions (cookie signé)
def make_session(email: str) -> str:
    email = _norm(email)
    payload = base64.urlsafe_b64encode(f"{email}|{int(time.time())}".encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(token: str | None) -> str | None:
    """Email de la session si le cookie est valide et non expiré, sinon None."""
    if not token or "." not in token:
        return None
    payload, _, sig = token.partition(".")
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    try:
        pad = "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload + pad).decode()
        email, _, ts = raw.partition("|")
        if int(ts) + _SESSION_MAX_AGE < time.time():
            return None
        return email or None
    except (ValueError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- aide requête (paywall)
def _is_local(request) -> bool:
    """Requête locale du PROPRIÉTAIRE (machine) : pas de passage par Cloudflare. Toujours autorisée
    à voir les pronos (dev + outils locaux + réchauffeur de cache)."""
    try:
        if request.headers.get("cf-connecting-ip"):
            return False                # trafic public via le tunnel
        host = request.client.host if request.client else ""
        return host in ("127.0.0.1", "::1", "localhost")
    except Exception:
        return False


def session_email(request) -> str | None:
    try:
        return read_session(request.cookies.get(COOKIE))
    except Exception:
        return None


def can_see_picks(request) -> bool:
    """Le visiteur a-t-il droit aux pronos ? Propriétaire local OU abonné connecté."""
    if _is_local(request):
        return True
    return is_subscriber(session_email(request) or "")
