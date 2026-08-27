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

from app import userdb          # base utilisateurs SQLite (remplace le store JSON à plat, cf. userdb.py)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data")
_STORE = os.path.join(_DATA, "accounts.json")
_SECRET_FILE = os.path.join(_DATA, ".session_secret")

COOKIE = "bx_session"
_SESSION_MAX_AGE = 60 * 24 * 3600          # 60 jours
_PBKDF2_ROUNDS = 200_000
_lock = threading.Lock()

# --------------------------------------------------------------------------- FORMULES D'ABONNEMENT
# STRUCTURE des formules seulement — les PRIX réels vivent dans Stripe (un price_id par formule, cf.
# billing.py). `days` sert au repli local (sub_until) si le webhook tarde. Catalogue surchargéable via
# data/plans.json sans redéploiement.
TRIAL_DAYS = 7
_DEFAULT_PLANS = {
    "free":    {"label": "Gratuit",       "days": 0,          "paid": False},
    "trial":   {"label": "Essai gratuit", "days": TRIAL_DAYS, "paid": False},
    "monthly": {"label": "Mensuel",       "days": 30,         "paid": True},
    "yearly":  {"label": "Annuel",        "days": 365,        "paid": True},
}
# L'essai gratuit démarre-t-il automatiquement à l'inscription ? OFF par défaut (décision produit :
# offrir N jours de pronos gratuits = choix revenu). Activable via env BETSFIX_TRIAL_ON_SIGNUP=1.
TRIAL_ON_SIGNUP = os.environ.get("BETSFIX_TRIAL_ON_SIGNUP", "").strip() in ("1", "true", "on", "yes")


def plans() -> dict:
    """Catalogue des formules (défauts + surcharge data/plans.json si présent)."""
    p = dict(_DEFAULT_PLANS)
    try:
        with open(os.path.join(_DATA, "plans.json"), encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                if isinstance(v, dict):
                    p[k] = {**p.get(k, {}), **v}
    except (OSError, ValueError):
        pass
    return p

# --------------------------------------------------------------------------- ANTI BRUTE-FORCE (login)
# Fenêtre glissante en mémoire (process unique). Au-delà de _MAX_FAILS échecs / _FAIL_WINDOW, on bloque
# la clé (email+IP) le temps que la fenêtre expire. Suffisant pour ralentir un bourrage de mots de passe.
_MAX_FAILS = 8
_FAIL_WINDOW = 900                          # 15 min
_login_fails: dict[str, list] = {}


def login_blocked(key: str) -> bool:
    now = time.time()
    fails = [t for t in _login_fails.get(key, []) if now - t < _FAIL_WINDOW]
    _login_fails[key] = fails
    return len(fails) >= _MAX_FAILS


def note_login_fail(key: str) -> None:
    _login_fails.setdefault(key, []).append(time.time())


def note_login_ok(key: str) -> None:
    _login_fails.pop(key, None)

# PROPRIÉTAIRES : emails TOUJOURS considérés abonnés (immunisés Stripe). Deux sources cumulées :
#  • env BETSFIX_OWNER_EMAIL (lue au démarrage, séparée par virgules) ;
#  • fichier persistant data/owners.json (liste d'emails) — modifiable À CHAUD, sans redémarrage.
_OWNERS_FILE = os.path.join(_DATA, "owners.json")
_OWNERS_ENV = {e.strip().lower() for e in (os.environ.get("BETSFIX_OWNER_EMAIL") or "").split(",") if e.strip()}


def _owners() -> set:
    owners = set(_OWNERS_ENV)
    try:
        with open(_OWNERS_FILE, encoding="utf-8") as f:
            owners |= {_norm(e) for e in (json.load(f) or []) if str(e).strip()}
    except (OSError, ValueError):
        pass
    return owners


def add_owner(email: str) -> None:
    """Déclare un email PROPRIÉTAIRE (toujours abonné). Persistant, pris en compte immédiatement."""
    email = _norm(email)
    if not email:
        return
    with _lock:
        try:
            with open(_OWNERS_FILE, encoding="utf-8") as f:
                cur = json.load(f) or []
        except (OSError, ValueError):
            cur = []
        if email not in {_norm(e) for e in cur}:
            cur.append(email)
            os.makedirs(_DATA, exist_ok=True)
            tmp = _OWNERS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _OWNERS_FILE)


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


# --------------------------------------------------------------------------- store (SQLite via userdb)
# Historique : le store était un JSON à plat réécrit en entier à chaque écriture. Il est désormais
# adossé à SQLite (app/userdb.py) — migration transparente (accounts.json importé au 1er accès puis
# archivé). `_load`/`_save` restent pour compat, mais les chemins chauds passent par des upserts 1-ligne.
def _load() -> dict:
    return userdb.all_users()


def _save(data: dict) -> None:
    for email, rec in (data or {}).items():
        userdb.upsert(email, **{k: v for k, v in rec.items() if k != "email"})


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
    return userdb.get(_norm(email))


def create_user(email: str, pw: str) -> tuple[bool, str]:
    """Crée un compte. (True, '') si OK, sinon (False, message d'erreur)."""
    email = _norm(email)
    if not valid_email(email):
        return False, "Adresse email invalide."
    if len(pw or "") < 8:
        return False, "Le mot de passe doit faire au moins 8 caractères."
    with _lock:
        if userdb.exists(email):
            return False, "Un compte existe déjà avec cet email."
        userdb.upsert(email, pw=hash_pw(pw), created=int(time.time()),
                      plan="free", sub_active=0, sub_until=None,
                      stripe_customer=None, stripe_sub=None)
        if TRIAL_ON_SIGNUP:
            _start_trial_locked(email)
    return True, ""


def _start_trial_locked(email: str, days: int | None = None) -> bool:
    """Démarre l'essai gratuit (une fois par compte). Appelé sous _lock. True si démarré."""
    u = userdb.get(email)
    if not u or u.get("trial_used"):
        return False
    until = time.time() + (days or TRIAL_DAYS) * 86400
    userdb.upsert(email, plan="trial", trial_until=until, trial_used=1)
    return True


def start_trial(email: str, days: int | None = None) -> bool:
    """Démarre l'essai gratuit pour un compte (une seule fois). Renvoie True si démarré."""
    with _lock:
        return _start_trial_locked(_norm(email), days)


def verify_login(email: str, pw: str) -> bool:
    u = get_user(email)
    return bool(u and verify_pw(pw, u.get("pw", "")))


def is_subscriber(email: str) -> bool:
    """Abonné actif ? Propriétaire = toujours oui. Sinon `sub_active` (Stripe), avec tolérance
    d'une date de fin `sub_until` future (au cas où sub_active n'a pas encore été rebasculé)."""
    email = _norm(email)
    if not email:
        return False
    if email in _owners():                         # propriétaire -> toujours abonné (immunisé Stripe)
        return True
    u = get_user(email)
    if not u:
        return False
    if u.get("sub_active"):
        return True
    now = time.time()
    # tolérance : abonnement payé dont le webhook n'a pas encore rebasculé sub_active, OU essai en cours
    return bool((u.get("sub_until") and u["sub_until"] > now)
                or (u.get("trial_until") and u["trial_until"] > now))


def plan_of(email: str) -> str:
    """Formule courante d'un compte : 'trial' si essai en cours, sinon le plan stocké (défaut 'free')."""
    email = _norm(email)
    if email in _owners():
        return "vip"
    u = get_user(email)
    if not u:
        return "free"
    if not u.get("sub_active") and (u.get("trial_until") or 0) > time.time():
        return "trial"
    return u.get("plan") or "free"


def find_by_stripe_customer(customer_id: str) -> str | None:
    """Email local rattaché à un customer Stripe (pour le webhook). None si inconnu (requête INDEXÉE)."""
    return userdb.find_by_stripe_customer(customer_id)


def set_subscription(email: str, active: bool, until: float | None = None,
                     stripe_customer: str | None = None, stripe_sub: str | None = None,
                     plan: str | None = None) -> None:
    """Met à jour le statut d'abonnement (appelé par le webhook Stripe en Phase 2). Stripe peut connaître
    un email sans compte local -> upsert crée alors une ligne vide (pw='')."""
    email = _norm(email)
    fields = {"sub_active": 1 if active else 0}
    if until is not None:
        fields["sub_until"] = until
    if stripe_customer is not None:
        fields["stripe_customer"] = stripe_customer
    if stripe_sub is not None:
        fields["stripe_sub"] = stripe_sub
    if plan is not None:
        fields["plan"] = plan
    with _lock:
        userdb.upsert(email, **fields)


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


def is_owner(request) -> bool:
    """Le VISITEUR est-il le PROPRIÉTAIRE ? (machine locale OU email connecté listé dans owners.json).
    Sert à ne montrer les SOURCES réelles qu'au propriétaire et une version neutre au public (le
    stack de données = avantage compétitif, caché dès que le mode public est activé)."""
    if _is_local(request):
        return True
    return _norm(session_email(request) or "") in _owners()
