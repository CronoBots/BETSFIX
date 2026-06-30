"""Configure les clés Stripe pour BETSFIX (écrit data/stripe.json, gitignore).

Active la Phase 2 (paiement par abonnement) SANS toucher au code ni redémarrer le service
(les clés sont relues à chaque requête). Pré-requis : `pip install stripe`.

Étapes côté Stripe (https://dashboard.stripe.com) :
  1. Récupère ta clé secrète (Developers -> API keys) : sk_test_... (ou sk_live_...).
  2. Crée un Produit + un Prix RÉCURRENT (ex. 9,99 €/mois) -> copie le Price ID : price_...
  3. Crée un Webhook (Developers -> Webhooks) vers https://api.betsfix.com/billing/webhook,
     événements : checkout.session.completed, customer.subscription.updated,
     customer.subscription.deleted -> copie le signing secret : whsec_...

Usage :
  python tools/stripe_setup.py --secret sk_test_... --price price_... --webhook whsec_... [--pub pk_test_...]
  python tools/stripe_setup.py --show     # affiche la config actuelle (clés masquées)
"""
from __future__ import annotations

import argparse
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FILE = os.path.join(_ROOT, "data", "stripe.json")


def _load() -> dict:
    try:
        with open(_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _mask(v: str) -> str:
    return (v[:7] + "…" + v[-4:]) if v and len(v) > 12 else ("(vide)" if not v else "***")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", help="clé secrète Stripe (sk_...)")
    ap.add_argument("--price", help="Price ID du prix récurrent (price_...)")
    ap.add_argument("--webhook", help="signing secret du webhook (whsec_...)")
    ap.add_argument("--pub", help="clé publishable (pk_...) — optionnelle")
    ap.add_argument("--public-url", help="URL publique (def. https://api.betsfix.com)")
    ap.add_argument("--show", action="store_true", help="affiche la config actuelle (masquée)")
    a = ap.parse_args()

    cfg = _load()
    if a.show:
        for k in ("secret_key", "price_id", "webhook_secret", "publishable_key", "public_url"):
            print(f"  {k:16}: {_mask(cfg.get(k, ''))}")
        print(f"\n  fichier: {_FILE}")
        return

    if a.secret:
        cfg["secret_key"] = a.secret.strip()
    if a.price:
        cfg["price_id"] = a.price.strip()
    if a.webhook:
        cfg["webhook_secret"] = a.webhook.strip()
    if a.pub:
        cfg["publishable_key"] = a.pub.strip()
    if a.public_url:
        cfg["public_url"] = a.public_url.strip().rstrip("/")

    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)

    ready = bool(cfg.get("secret_key") and cfg.get("price_id"))
    print(f"✓ écrit dans {_FILE}")
    for k in ("secret_key", "price_id", "webhook_secret", "publishable_key"):
        print(f"  {k:16}: {_mask(cfg.get(k, ''))}")
    print("\n" + ("✅ Stripe est PRÊT (pense à `pip install stripe`)."
                  if ready else "⚠️ Manque secret_key et/ou price_id -> Stripe reste inactif."))
    try:
        import stripe  # noqa: F401
    except ImportError:
        print("⚠️ paquet `stripe` non installé : `pip install stripe`")


if __name__ == "__main__":
    main()
