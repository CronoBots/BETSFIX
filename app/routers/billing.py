"""Abonnement Stripe — Phase 2 (paiement récurrent).

INACTIF tant que Stripe n'est pas configuré (paquet `stripe` absent OU clés manquantes) : dans ce cas
/billing/subscribe|portal affichent une page d'attente (aucun crash). Dès que les clés sont fournies
(env OU data/stripe.json, gitignore), le vrai flux s'active :
  - POST /billing/subscribe       -> crée une Checkout Session Stripe et redirige vers le paiement ;
  - POST /billing/portal          -> ouvre le portail client Stripe (gérer / annuler) ;
  - POST /billing/webhook         -> reçoit les events Stripe (signés) -> accounts.set_subscription ;
  - GET  /billing/success|cancel  -> retours après paiement (success active aussitôt, en plus du webhook).

Config (priorité env, repli fichier data/stripe.json) :
  STRIPE_SECRET_KEY · STRIPE_PRICE_ID · STRIPE_PUBLISHABLE_KEY · STRIPE_WEBHOOK_SECRET · BETSFIX_PUBLIC_URL
Mise en place : `python tools/stripe_setup.py --secret sk_... --price price_... --webhook whsec_...`
puis `pip install stripe`. Webhook à créer côté Stripe -> https://api.betsfix.com/billing/webhook
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import accounts

log = logging.getLogger("uvicorn")
router = APIRouter(tags=["🖥️ Interface (pages HTML)"])

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_FILE = os.path.join(_ROOT, "data", "stripe.json")


def _config() -> dict:
    """Clés Stripe : env d'abord, repli sur data/stripe.json (gitignore)."""
    cfg = {}
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except (OSError, ValueError):
        cfg = {}
    g = lambda k: os.environ.get("STRIPE_" + k.upper()) or cfg.get(k, "")  # noqa: E731
    return {"secret_key": g("secret_key"), "price_id": g("price_id"),
            "publishable_key": g("publishable_key"), "webhook_secret": g("webhook_secret"),
            "public_url": (os.environ.get("BETSFIX_PUBLIC_URL") or cfg.get("public_url")
                           or "https://api.betsfix.com").rstrip("/")}


def _stripe():
    """Module stripe prêt à l'emploi, ou None si paquet absent / non configuré (-> repli page d'attente)."""
    cfg = _config()
    if not (cfg["secret_key"] and cfg["price_id"]):
        return None
    try:
        import stripe
    except ImportError:
        return None
    stripe.api_key = cfg["secret_key"]
    return stripe


def configured() -> bool:
    return _stripe() is not None


_WAIT = ("<!doctype html><html lang=fr><head><meta charset=utf-8>"
         "<meta name=viewport content='width=device-width,initial-scale=1'>"
         "<meta name=theme-color content='#0b0d12'><title>Abonnement · BETSFIX</title>"
         "<style>html,body{background:#0b0d12;color:#e9f1fb;font-family:ui-monospace,monospace;"
         "min-height:100dvh;display:flex;flex-direction:column;align-items:center;justify-content:center;"
         "gap:16px;padding:24px;text-align:center}a{color:#5fd0ff}.b{max-width:360px;line-height:1.6;"
         "font-size:14px;color:#cfe0f5}.t{font-size:18px;font-weight:800}</style></head><body>"
         "<div class=t>💳 Abonnement bientôt disponible</div>"
         "<div class=b>Le paiement par abonnement est en cours de mise en place. "
         "Reviens très bientôt pour débloquer tous les pronos ⭐.</div>"
         "<a href='/compte'>← Mon compte</a></body></html>")


def _wait() -> HTMLResponse:
    return HTMLResponse(_WAIT)


@router.post("/billing/subscribe", response_class=HTMLResponse, include_in_schema=False)
async def subscribe(request: Request):
    s = _stripe()
    if not s:                                          # non configuré -> page d'attente (Phase 2 pas prête)
        return _wait()
    email = accounts.session_email(request)
    if not email:
        return RedirectResponse("/login?next=/compte", status_code=303)
    cfg = _config()
    u = accounts.get_user(email) or {}
    try:
        sess = s.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": cfg["price_id"], "quantity": 1}],
            customer=u.get("stripe_customer") or None,
            customer_email=None if u.get("stripe_customer") else email,
            client_reference_id=email,
            metadata={"email": email},
            allow_promotion_codes=True,
            success_url=f"{cfg['public_url']}/billing/success?cs={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{cfg['public_url']}/compte",
        )
        return RedirectResponse(sess.url, status_code=303)
    except Exception as exc:                           # erreur Stripe -> on ne casse pas l'UX
        log.warning("stripe subscribe: %s", exc)
        return _wait()


@router.post("/billing/portal", include_in_schema=False)
async def portal(request: Request):
    s = _stripe()
    email = accounts.session_email(request)
    if not s:
        return _wait()
    if not email:
        return RedirectResponse("/login?next=/compte", status_code=303)
    cust = (accounts.get_user(email) or {}).get("stripe_customer")
    if not cust:                                       # pas encore client Stripe -> retour compte
        return RedirectResponse("/compte", status_code=303)
    cfg = _config()
    try:
        sess = s.billing_portal.Session.create(customer=cust, return_url=f"{cfg['public_url']}/compte")
        return RedirectResponse(sess.url, status_code=303)
    except Exception as exc:
        log.warning("stripe portal: %s", exc)
        return RedirectResponse("/compte", status_code=303)


def _activate_from_session(s, sess) -> None:
    """Active l'abonnement local depuis une Checkout Session payée."""
    paid = (sess.get("payment_status") in ("paid", "no_payment_required")
            or sess.get("status") == "complete")
    if not paid:
        return
    email = (sess.get("client_reference_id") or (sess.get("metadata") or {}).get("email")
             or sess.get("customer_email") or "")
    if not email:
        return
    sub = sess.get("subscription")
    until = None
    if sub:
        try:
            until = s.Subscription.retrieve(sub).get("current_period_end")
        except Exception:
            pass
    accounts.set_subscription(email, True, until=until,
                              stripe_customer=sess.get("customer"), stripe_sub=sub)


def _handle_event(s, event) -> None:
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if etype == "checkout.session.completed":
        _activate_from_session(s, obj)
    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        cust = obj.get("customer")
        email = accounts.find_by_stripe_customer(cust)
        if not email:
            try:
                email = (s.Customer.retrieve(cust).get("email") or "") if cust else ""
            except Exception:
                email = ""
        if email:
            active = (etype != "customer.subscription.deleted"
                      and obj.get("status") in ("active", "trialing"))
            accounts.set_subscription(email, active, until=obj.get("current_period_end"),
                                      stripe_customer=cust, stripe_sub=obj.get("id"))


@router.post("/billing/webhook", include_in_schema=False)
async def webhook(request: Request):
    s = _stripe()
    if not s:
        return JSONResponse({"ok": False, "reason": "stripe non configuré"}, status_code=503)
    cfg = _config()
    payload = await request.body()                     # CORPS BRUT requis pour la vérif de signature
    sig = request.headers.get("stripe-signature", "")
    try:
        if cfg["webhook_secret"]:
            event = s.Webhook.construct_event(payload, sig, cfg["webhook_secret"])
        else:                                          # pas de secret -> on accepte sans vérifier (dev only)
            event = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        log.warning("stripe webhook signature: %s", exc)
        return JSONResponse({"ok": False}, status_code=400)
    try:
        _handle_event(s, event)
    except Exception as exc:                           # jamais d'erreur 500 -> Stripe ne re-spamme pas
        log.warning("stripe webhook handle: %s", exc)
    return JSONResponse({"ok": True})


@router.get("/billing/success", include_in_schema=False)
async def success(request: Request, cs: str = ""):
    """Retour après paiement : active TOUT DE SUITE (en plus du webhook) pour un accès immédiat."""
    s = _stripe()
    if s and cs:
        try:
            _activate_from_session(s, s.checkout.Session.retrieve(cs))
        except Exception as exc:
            log.warning("stripe success: %s", exc)
    return RedirectResponse("/compte", status_code=303)


@router.get("/billing/cancel", include_in_schema=False)
async def cancel():
    return RedirectResponse("/compte", status_code=303)
