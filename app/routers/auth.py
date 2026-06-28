"""Pages de compte : inscription, connexion, déconnexion, /compte (statut d'abonnement).

Phase 1 du paywall : email + mot de passe (cf. app/accounts.py), session par cookie signé. Le bouton
« S'abonner » (Stripe) sera branché en Phase 2 ; ici il renvoie une page d'attente claire.
"""
from __future__ import annotations

import html as _html

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import accounts

router = APIRouter(tags=["🖥️ Interface (pages HTML)"])

_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:#0b0d12;color:#e9f1fb;font-family:'JetBrains Mono',ui-monospace,monospace}
body{min-height:100dvh;display:flex;flex-direction:column;align-items:center;padding:34px 16px 40px}
a{color:#5fd0ff;text-decoration:none}
.logo{font-weight:800;letter-spacing:.16em;font-size:14px;color:#5fd0ff;text-transform:uppercase;margin-bottom:22px}
.card{width:100%;max-width:400px;background:linear-gradient(180deg,rgba(34,184,255,.07),rgba(34,184,255,.02));
  border:1px solid rgba(34,184,255,.22);border-radius:18px;padding:24px 22px}
h1{font-size:19px;font-weight:800;margin-bottom:4px}
.sub{font-size:12px;color:#90a4be;margin-bottom:20px;line-height:1.5}
label{display:block;font-size:11px;font-weight:700;color:#90a4be;text-transform:uppercase;
  letter-spacing:.04em;margin:14px 0 6px}
input{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);border-radius:11px;
  padding:12px 13px;color:#e9f1fb;font-family:inherit;font-size:14px}
input:focus{outline:none;border-color:rgba(34,184,255,.6)}
button{width:100%;margin-top:20px;background:#22b8ff;color:#04121c;border:0;border-radius:12px;
  padding:13px;font-family:inherit;font-size:14px;font-weight:800;cursor:pointer}
button.ghost{background:transparent;color:#5fd0ff;border:1px solid rgba(34,184,255,.35)}
.err{background:rgba(255,80,90,.12);border:1px solid rgba(255,80,90,.4);color:#ff9aa1;border-radius:10px;
  padding:10px 12px;font-size:12px;margin-bottom:14px;line-height:1.4}
.ok{background:rgba(25,196,106,.12);border:1px solid rgba(25,196,106,.4);color:#8df3c0;border-radius:10px;
  padding:10px 12px;font-size:12px;margin-bottom:14px;line-height:1.4}
.alt{text-align:center;font-size:12px;color:#90a4be;margin-top:18px}
.row{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:13px;
  padding:11px 0;border-top:1px solid rgba(255,255,255,.08)}
.row b{font-weight:800}
.badge{font-size:11px;font-weight:800;border-radius:7px;padding:3px 9px}
.badge.on{background:rgba(25,196,106,.18);color:#8df3c0}
.badge.off{background:rgba(150,165,185,.16);color:#c0cbdb}
.back{display:block;text-align:center;margin-top:22px;font-size:12px;color:#90a4be}
.foot{font-size:10px;color:#5a6b82;margin-top:26px;text-align:center;line-height:1.5}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=fr><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<meta name=theme-color content='#0b0d12'><title>{_html.escape(title)} · BETSFIX</title>"
            f"<link rel=preconnect href='https://fonts.googleapis.com'>"
            f"<link href='https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap' rel=stylesheet>"
            f"<style>{_CSS}</style></head><body>"
            f"<a class=logo href='/'>◆ BETSFIX</a>{body}"
            f"<a class=back href='/'>← Retour à l'app</a>"
            f"<div class=foot>18+ · Outil informatif, sans garantie · Jouez responsable</div>"
            f"</body></html>")


def _safe_next(nxt: str | None) -> str:
    """N'autorise qu'un chemin interne (anti open-redirect)."""
    return nxt if (nxt and nxt.startswith("/") and not nxt.startswith("//")) else "/"


def _set_cookie(resp, email: str) -> None:
    resp.set_cookie(accounts.COOKIE, accounts.make_session(email), max_age=accounts._SESSION_MAX_AGE,
                    httponly=True, samesite="lax", path="/")


def _login_form(nxt: str = "/", err: str = "", email: str = "") -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Connexion", f"""<div class=card><h1>Connexion</h1>
<div class=sub>Accède aux pronos ⭐ réservés aux abonnés. Les statistiques et résultats restent ouverts à tous.</div>
{err_html}<form method=post action='/login'>
<input type=hidden name=next value='{e(nxt)}'>
<label>Email</label><input name=email type=email autocomplete=email value='{e(email)}' required>
<label>Mot de passe</label><input name=password type=password autocomplete=current-password required>
<button type=submit>Se connecter</button></form>
<div class=alt>Pas encore de compte ? <a href='/signup?next={e(nxt)}'>Créer un compte</a></div></div>""")


def _signup_form(nxt: str = "/", err: str = "", email: str = "") -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Inscription", f"""<div class=card><h1>Créer un compte</h1>
<div class=sub>Gratuit. Tu vois aussitôt toutes les stats et résultats ; les pronos se débloquent avec l'abonnement.</div>
{err_html}<form method=post action='/signup'>
<input type=hidden name=next value='{e(nxt)}'>
<label>Email</label><input name=email type=email autocomplete=email value='{e(email)}' required>
<label>Mot de passe</label><input name=password type=password autocomplete=new-password minlength=8 required>
<button type=submit>Créer mon compte</button></form>
<div class=alt>Déjà inscrit ? <a href='/login?next={e(nxt)}'>Se connecter</a></div></div>""")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/"):
    if accounts.session_email(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return HTMLResponse(_login_form(_safe_next(next)))


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(next: str = Form("/"), email: str = Form(...), password: str = Form(...)):
    nxt = _safe_next(next)
    if not accounts.verify_login(email, password):
        return HTMLResponse(_login_form(nxt, "Email ou mot de passe incorrect.", email), status_code=401)
    resp = RedirectResponse(nxt, status_code=303)
    _set_cookie(resp, email)
    return resp


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(request: Request, next: str = "/"):
    if accounts.session_email(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return HTMLResponse(_signup_form(_safe_next(next)))


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_submit(next: str = Form("/"), email: str = Form(...), password: str = Form(...)):
    nxt = _safe_next(next)
    ok, err = accounts.create_user(email, password)
    if not ok:
        return HTMLResponse(_signup_form(nxt, err, email), status_code=400)
    resp = RedirectResponse(nxt, status_code=303)
    _set_cookie(resp, email)
    return resp


@router.post("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(accounts.COOKIE, path="/")
    return resp


@router.get("/compte", response_class=HTMLResponse, include_in_schema=False)
async def account_page(request: Request):
    email = accounts.session_email(request)
    if not email:
        return HTMLResponse(_login_form("/compte"))
    e = _html.escape
    sub = accounts.is_subscriber(email)
    badge = ('<span class="badge on">✓ Abonné</span>' if sub
             else '<span class="badge off">Non abonné</span>')
    if sub:
        action = ('<div class=ok>Ton abonnement est actif — tu vois tous les pronos ⭐.</div>'
                  '<form method=post action="/billing/portal"><button class=ghost type=submit>'
                  'Gérer mon abonnement</button></form>')
    else:
        action = ('<div class=sub>Débloque tous les pronos ⭐ (simples + combinés). '
                  'Les stats et résultats sont déjà ouverts.</div>'
                  '<form method=post action="/billing/subscribe"><button type=submit>'
                  "S'abonner</button></form>")
    return HTMLResponse(_page("Mon compte", f"""<div class=card><h1>Mon compte</h1>
<div class=row><span>Email</span><b>{e(email)}</b></div>
<div class=row><span>Abonnement</span>{badge}</div>
{action}
<form method=post action='/logout'><button class=ghost type=submit style='margin-top:12px'>Se déconnecter</button></form>
</div>"""))
