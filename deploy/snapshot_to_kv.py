"""Snapshot des pages PUBLIQUES du site -> Cloudflare Workers KV (filet de survie, 2026-08-14).

BUT : quand le PC collecteur est éteint / en reboot / coupé du réseau, le tunnel `api.betsfix.com`
tombe et le site est mort. Ce script pousse à chaque cycle une COPIE HTML des pages publiques dans un
namespace KV Cloudflare ; un Worker (déployé à part) sert cette copie en secours avec un bandeau
« données de HH:MM ». Le site ne tombe donc plus JAMAIS complètement.

CE QUI EST SNAPSHOTÉ = ce qu'un VISITEUR ANONYME voit (aucune donnée de compte : on interroge le site
en local SANS cookie de session -> le paywall masque déjà les pronos aux non-abonnés). Purement de
l'AFFICHAGE : ce script ne touche NI au règlement, NI aux stats, NI à la calibration, NI au chemin de
sélection des paris (cf. règle « protéger le phare »).

Le CSS et le JS sont INLINE dans chaque page (cf. web.spa_shell) -> la copie servie hors-ligne est
entièrement stylée et navigable. Seules les images (/static/*) manquent si l'origine est down : elles
dégradent proprement (alt/splash caché).

Config par variables d'environnement (persistées côté PC, hors dépôt git) :
  BETSFIX_KV_TOKEN      (SECRET) jeton API Cloudflare « Account · Workers KV Storage · Edit »
  BETSFIX_KV_ACCOUNT    id de compte Cloudflare        (défaut ci-dessous, non secret)
  BETSFIX_KV_NAMESPACE  id du namespace KV betsfix-snapshots (défaut ci-dessous, non secret)
  BETSFIX_LOCAL_BASE    base uvicorn locale            (défaut http://127.0.0.1:8000)

Usage :
    python deploy/snapshot_to_kv.py            # snapshot + push réel (si BETSFIX_KV_TOKEN défini)
    python deploy/snapshot_to_kv.py --dry-run  # récupère les pages, montre les tailles, n'écrit RIEN dans KV

Si BETSFIX_KV_TOKEN n'est pas défini -> NO-OP silencieux (0 en sortie) : appelable dès aujourd'hui
depuis le cycle sans rien casser tant que le secours n'est pas branché.
"""
from __future__ import annotations

import datetime
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# --- Identifiants NON secrets (repli si l'env n'est pas défini ; l'env reste prioritaire) -------------
_ACCOUNT_DEFAULT = "d353a7ca3cc5428ad65e96b53dc63573"
_NAMESPACE_DEFAULT = "957f17454a364ef3b9a071439efeac69"  # namespace KV « betsfix-snapshots »

# Pages publiques à copier. (chemin local, nom-de-clé). Pour chaque page on stocke DEUX variantes :
#   page:<nom>  = page COMPLÈTE (coquille SPA autonome) -> servie directement par le Worker en secours
#   frag:<nom>  = fragment seul  (?frag=1)              -> sert la navigation AJAX entre onglets hors-ligne
_PAGES = [
    ("/",           "home"),
    ("/accueil",    "accueil"),
    ("/stats",      "stats"),
    ("/directs",    "directs"),
    ("/montante",   "montante"),
    ("/calendrier", "calendrier"),
]

_MIN_BYTES = 500  # en-dessous = page d'erreur/vide -> on n'écrase PAS un bon snapshot précédent


def _cfg() -> dict:
    return {
        "token": (os.environ.get("BETSFIX_KV_TOKEN") or "").strip(),
        "account": (os.environ.get("BETSFIX_KV_ACCOUNT") or _ACCOUNT_DEFAULT).strip(),
        "namespace": (os.environ.get("BETSFIX_KV_NAMESPACE") or _NAMESPACE_DEFAULT).strip(),
        "base": (os.environ.get("BETSFIX_LOCAL_BASE") or "http://127.0.0.1:8000").rstrip("/"),
    }


def _fetch_local(base: str, path: str) -> bytes | None:
    """GET une page en local (anonyme, sans cookie). None si erreur ou trop courte."""
    url = base + path
    req = urllib.request.Request(url, headers={"User-Agent": "betsfix-snapshot/1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.status != 200:
                print(f"  ! {path} -> HTTP {r.status} (ignoré)")
                return None
            data = r.read()
    except (urllib.error.URLError, OSError) as e:
        print(f"  ! {path} -> échec local ({e}) (ignoré)")
        return None
    if len(data) < _MIN_BYTES:
        print(f"  ! {path} -> {len(data)} o (< {_MIN_BYTES}, suspect) (ignoré)")
        return None
    return data


def _kv_put(cfg: dict, key: str, value: bytes, content_type: str) -> bool:
    """PUT une valeur dans KV via l'API REST Cloudflare. La valeur = le HTML ; le content-type voulu
    est stocké en MÉTADONNÉE (le Worker le relira pour servir le bon en-tête)."""
    q = urllib.parse.quote(key, safe="")
    url = (f"https://api.cloudflare.com/client/v4/accounts/{cfg['account']}"
           f"/storage/kv/namespaces/{cfg['namespace']}/values/{q}")
    # API « bulk value + metadata » : multipart n'est pas nécessaire ici, on met la valeur en corps et
    # le content-type dans une clé méta séparée `_ct:<key>` (simple, lisible par le Worker).
    req = urllib.request.Request(url, data=value, method="PUT",
                                 headers={"Authorization": f"Bearer {cfg['token']}",
                                          "Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError) as e:
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                detail = " :: " + e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
        print(f"  ! KV PUT {key} -> échec ({e}){detail}")
        return False


def main() -> int:
    dry = "--dry-run" in sys.argv
    cfg = _cfg()
    if not dry and not cfg["token"]:
        print("BETSFIX_KV_TOKEN non défini -> NO-OP (aucun snapshot poussé).")
        return 0

    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    print(f"Snapshot KV @ {stamp} (base={cfg['base']}, dry={dry})")

    ok = 0
    fail = 0
    for path, name in _PAGES:
        full = _fetch_local(cfg["base"], path)
        frag_sep = "&" if "?" in path else "?"
        frag = _fetch_local(cfg["base"], f"{path}{frag_sep}frag=1")
        for variant, data in (("page", full), ("frag", frag)):
            if data is None:
                fail += 1
                continue
            key = f"{variant}:{name}"
            if dry:
                print(f"  [dry] {key} <- {len(data)} o")
                ok += 1
                continue
            if _kv_put(cfg, key, data, "text/html; charset=utf-8"):
                print(f"  + {key} ({len(data)} o)")
                ok += 1
            else:
                fail += 1

    # Horodatage global (le Worker l'affiche dans le bandeau « données de … »).
    if not dry:
        _kv_put(cfg, "_meta:updated", stamp.encode("utf-8"), "text/plain")

    print(f"Terminé : {ok} poussé(s), {fail} échec(s).")
    return 1 if fail and ok == 0 else 0  # échec DUR seulement si RIEN n'a pu être poussé


if __name__ == "__main__":
    raise SystemExit(main())
