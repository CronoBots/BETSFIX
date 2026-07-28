"""Pousse les données du COLLECTEUR vers le SERVEUR cloud (split cloud/collecteur, 2026-07-28).

Sens UNIQUE (collecteur -> cloud). Ne synchronise QUE la liste blanche de `deploy/sync_manifest.txt`, et
REFUSE catégoriquement de toucher aux fichiers possédés par le serveur (comptes/owners/secret) — double
garde-fou : ces chemins ne sont pas dans la liste blanche ET sont bloqués en dur ici.

Transport = rsync-over-SSH (incrémental, ne transfère que les octets modifiés). Destination via l'env
`BETSFIX_CLOUD_SSH` = « user@host:/chemin/vers/BETSFIX » (ex. « deploy@vps:/opt/betsfix »). Si l'env n'est
pas défini -> NO-OP silencieux (donc appelable dès aujourd'hui depuis reconcile sans rien casser tant que
le VPS n'existe pas).

Usage :
    python deploy/push_to_cloud.py            # pousse (rsync réel) si BETSFIX_CLOUD_SSH est défini
    python deploy/push_to_cloud.py --dry-run  # montre CE QUI serait poussé, ne transfère rien
"""
from __future__ import annotations

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_ROOT, "deploy", "sync_manifest.txt")

# GARDE-FOU DUR : jamais poussés, même si un jour on les ajoutait par erreur au manifeste. Le serveur les
# possède (inscriptions des membres, secret de session) ; les écraser = perdre les comptes.
_SERVER_OWNED = {"data/accounts.json", "data/owners.json", "data/.session_secret"}


def _read_manifest() -> list[str]:
    paths: list[str] = []
    with open(_MANIFEST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            paths.append(line.replace("\\", "/"))
    return paths


def _validate(paths: list[str]) -> list[str]:
    """Refuse tout chemin server-owned, hors-repo, ou inexistant -> jamais de fuite ni de commande bancale."""
    clean: list[str] = []
    for p in paths:
        norm = p.rstrip("/")
        if norm in _SERVER_OWNED:
            raise SystemExit(f"REFUS : « {p} » est possédé par le serveur (comptes/secret) — jamais poussé.")
        if p.startswith("/") or ".." in p.split("/"):
            raise SystemExit(f"REFUS : chemin non relatif au repo : « {p} ».")
        if not os.path.exists(os.path.join(_ROOT, norm)):
            print(f"  (ignoré, absent : {p})")
            continue
        clean.append(p)
    return clean


def main() -> int:
    dry = "--dry-run" in sys.argv
    dest = (os.environ.get("BETSFIX_CLOUD_SSH") or "").strip()
    paths = _validate(_read_manifest())
    if not paths:
        print("Rien à pousser (manifeste vide ou chemins absents).")
        return 0

    if dry or not dest:
        why = "--dry-run" if dry else "BETSFIX_CLOUD_SSH non défini -> NO-OP"
        print(f"[{why}] {len(paths)} entrée(s) de la liste blanche seraient poussées :")
        for p in paths:
            print(f"   + {p}")
        print("\nBloqués en dur (jamais poussés) : " + ", ".join(sorted(_SERVER_OWNED)))
        return 0

    # rsync incrémental, une entrée à la fois (relative -> même arborescence côté serveur). --relative
    # préserve « data/… » ; --delete retire côté cloud ce qui a disparu côté collecteur DANS ces chemins
    # seulement (jamais les fichiers server-owned, hors périmètre).
    rc = 0
    for p in paths:
        src = "./" + p                                     # ./ + --relative -> recrée data/... à l'identique
        cmd = ["rsync", "-az", "--relative", "--delete", src, dest + "/"]
        print("  $ " + " ".join(cmd))
        try:
            r = subprocess.run(cmd, cwd=_ROOT)
            rc = rc or r.returncode
        except FileNotFoundError:
            raise SystemExit("rsync introuvable. Installe rsync (Git-for-Windows/WSL) ou adapte le transport.")
    print("OK" if rc == 0 else f"rsync a renvoyé le code {rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
