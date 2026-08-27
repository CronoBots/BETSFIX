"""Base utilisateurs BETSFIX — SQLite (remplace le store JSON à plat, pour le passage à l'échelle).

POURQUOI SQLite et pas le JSON d'avant :
  • Le store JSON (`data/accounts.json`) réécrivait TOUT le fichier à CHAQUE écriture (O(n)), sous un
    unique verrou process. Passé quelques milliers de comptes : lent, et une écriture interrompue pouvait
    corrompre l'ensemble.
  • SQLite (stdlib, ZÉRO infra) : écritures atomiques ligne-à-ligne, mode WAL (lecteurs concurrents non
    bloqués par un écrivain), requêtes INDEXÉES (`find_by_stripe_customer` devient un index, plus un scan
    complet). Migration transparente : au premier accès, `accounts.json` est importé puis ARCHIVÉ.
  • Chemin de sortie propre : le même schéma se transpose vers Postgres si un jour l'échelle l'exige.

Ce module ne connaît RIEN du hachage de mot de passe ni des sessions (cf. app/accounts.py) : il ne fait
que PERSISTER des enregistrements utilisateur. `accounts.py` reste la seule surface d'API publique.

Colonnes d'un utilisateur (toutes optionnelles sauf email/created) :
  email PRIMARY KEY · pw · created · plan · sub_active · sub_until · trial_until · trial_used ·
  stripe_customer · stripe_sub · email_verified · updated
Les colonnes sont ajoutées de façon ADDITIVE (ALTER TABLE idempotent) : un ancien fichier .db se met à
niveau tout seul, jamais de perte.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data")
_DB = os.path.join(_DATA, "users.db")
_JSON_LEGACY = os.path.join(_DATA, "accounts.json")

# Colonnes du schéma → valeur par défaut SQL. `email` est la clé primaire (posée à part).
_COLUMNS = {
    "pw": "TEXT NOT NULL DEFAULT ''",
    "created": "INTEGER NOT NULL DEFAULT 0",
    "plan": "TEXT NOT NULL DEFAULT 'free'",
    "sub_active": "INTEGER NOT NULL DEFAULT 0",
    "sub_until": "REAL",
    "trial_until": "REAL",
    "trial_used": "INTEGER NOT NULL DEFAULT 0",
    "stripe_customer": "TEXT",
    "stripe_sub": "TEXT",
    "email_verified": "INTEGER NOT NULL DEFAULT 0",
    "updated": "INTEGER",
}
# Champs exposés en dict (ordre stable). `email` inclus.
_FIELDS = ("email", *_COLUMNS.keys())

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


# --------------------------------------------------------------------------- connexion / schéma
def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        os.makedirs(_DATA, exist_ok=True)
        conn = sqlite3.connect(_DB, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")        # lecteurs concurrents non bloqués
        conn.execute("PRAGMA synchronous=NORMAL")      # durable + rapide (WAL)
        conn.execute("PRAGMA foreign_keys=ON")
        cols = ", ".join(f"{name} {ddl}" for name, ddl in _COLUMNS.items())
        conn.execute(f"CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, {cols})")
        # ajout ADDITIF des colonnes manquantes (mise à niveau d'un vieux .db, sans perte)
        have = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        for name, ddl in _COLUMNS.items():
            if name not in have:
                # SQLite refuse un DEFAULT non constant en ALTER : on retombe sur une valeur simple
                default = ddl.replace("NOT NULL", "").strip()
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {default}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users(stripe_customer)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_sub_active ON users(sub_active)")
        conn.commit()
        _conn = conn
        _import_legacy_json(conn)
        return conn


def _import_legacy_json(conn: sqlite3.Connection) -> None:
    """Migration unique : importe data/accounts.json (s'il existe) puis l'archive en .imported."""
    if not os.path.exists(_JSON_LEGACY):
        return
    try:
        with open(_JSON_LEGACY, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    n = 0
    for email, rec in data.items():
        if not isinstance(rec, dict):
            continue
        # n'écrase JAMAIS un compte déjà présent en base (la base fait autorité après migration)
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            continue
        fields = {k: rec.get(k) for k in _COLUMNS if k in rec}
        fields.setdefault("created", int(rec.get("created") or time.time()))
        _raw_upsert(conn, email, fields)
        n += 1
    conn.commit()
    try:
        os.replace(_JSON_LEGACY, _JSON_LEGACY + ".imported")
    except OSError:
        pass
    if n:
        print(f"[userdb] migration : {n} compte(s) importé(s) depuis accounts.json -> users.db")


# --------------------------------------------------------------------------- écriture / lecture
def _raw_upsert(conn: sqlite3.Connection, email: str, fields: dict) -> None:
    """UPSERT bas niveau (dans une connexion donnée, sans commit). `fields` ⊆ colonnes."""
    fields = {k: v for k, v in fields.items() if k in _COLUMNS}
    fields["updated"] = int(time.time())
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE users SET {sets} WHERE email=?", (*fields.values(), email))
    else:
        fields.setdefault("created", int(time.time()))
        cols = ", ".join(("email", *fields.keys()))
        ph = ", ".join(["?"] * (1 + len(fields)))
        conn.execute(f"INSERT INTO users ({cols}) VALUES ({ph})", (email, *fields.values()))


def upsert(email: str, **fields) -> None:
    """Crée ou met à jour un utilisateur (une seule ligne, atomique)."""
    conn = _connect()
    with _lock:
        _raw_upsert(conn, email, fields)
        conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = {k: row[k] for k in row.keys()}
    d["sub_active"] = bool(d.get("sub_active"))
    d["email_verified"] = bool(d.get("email_verified"))
    d["trial_used"] = bool(d.get("trial_used"))
    return d


def get(email: str) -> dict | None:
    conn = _connect()
    with _lock:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    return _row_to_dict(row)


def find_by_stripe_customer(customer_id: str) -> str | None:
    if not customer_id:
        return None
    conn = _connect()
    with _lock:
        row = conn.execute("SELECT email FROM users WHERE stripe_customer=? LIMIT 1",
                           (customer_id,)).fetchone()
    return row["email"] if row else None


def all_users() -> dict:
    """Tous les comptes en dict {email: record} — pour l'admin/compat, PAS le chemin chaud."""
    conn = _connect()
    with _lock:
        rows = conn.execute("SELECT * FROM users").fetchall()
    return {r["email"]: _row_to_dict(r) for r in rows}


def count(active_only: bool = False) -> int:
    conn = _connect()
    q = "SELECT COUNT(*) c FROM users"
    if active_only:
        q += " WHERE sub_active=1"
    with _lock:
        return int(conn.execute(q).fetchone()["c"])


def exists(email: str) -> bool:
    conn = _connect()
    with _lock:
        return conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone() is not None
