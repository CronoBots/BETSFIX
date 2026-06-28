"""Abonnement Stripe — PHASE 2 (à brancher quand les clés Stripe sont fournies).

Pour l'instant : stubs qui affichent une page d'attente claire (pas de 404), et l'emplacement du
webhook est réservé. Quand le compte Stripe + le prix récurrent seront prêts, on remplira :
  - POST /billing/subscribe  -> crée une Checkout Session Stripe et redirige vers le paiement.
  - POST /billing/portal     -> ouvre le portail client Stripe (gérer/annuler).
  - POST /billing/webhook    -> reçoit les events Stripe et met à jour accounts.set_subscription(...).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import accounts

router = APIRouter(tags=["🖥️ Interface (pages HTML)"])

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


@router.post("/billing/subscribe", response_class=HTMLResponse, include_in_schema=False)
async def subscribe(request: Request):
    return HTMLResponse(_WAIT)


@router.post("/billing/portal", response_class=HTMLResponse, include_in_schema=False)
async def portal(request: Request):
    return HTMLResponse(_WAIT)
