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

# Le CSS du compte (scopé .acctwrap) est désormais GLOBAL dans web.py (toujours chargé) -> le contenu
# marche aussi bien en page pleine qu'en FRAGMENT injecté dans le panneau SPA.


def _page(title: str, body: str, frag: bool = False) -> str:
    """Contenu Compte. `frag=True` -> fragment seul (injecté dans le panneau SPA, bascule SANS
    rechargement, comme un onglet sport) ; sinon page complète via web.layout (barre du bas + thème),
    onglet 'compte' actif."""
    inner = f'<div class="acctwrap">{body}</div>'
    if frag:
        return inner
    from app import web                       # import paresseux (évite tout cycle à l'import)
    # Coquille SPA complète (onglet 'compte' actif) -> depuis cette page aussi, taper un autre onglet
    # bascule SANS rechargement (cohérent avec tout le reste).
    return web.spa_shell("compte", title, inner)


def _safe_next(nxt: str | None) -> str:
    """N'autorise qu'un chemin interne (anti open-redirect)."""
    return nxt if (nxt and nxt.startswith("/") and not nxt.startswith("//")) else "/"


def _set_cookie(resp, email: str) -> None:
    resp.set_cookie(accounts.COOKIE, accounts.make_session(email), max_age=accounts._SESSION_MAX_AGE,
                    httponly=True, samesite="lax", path="/")


def _login_form(nxt: str = "/", err: str = "", email: str = "", frag: bool = False) -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Connexion", f"""<div class=acard><h1>Connexion</h1>
<div class=sub>Accède aux pronos joués réservés aux abonnés. Les statistiques et résultats restent ouverts à tous.</div>
{err_html}<form method=post action='/login'>
<input type=hidden name=next value='{e(nxt)}'>
<label>Email</label><input name=email type=email autocomplete=email value='{e(email)}' required>
<label>Mot de passe</label><input name=password type=password autocomplete=current-password required>
<button type=submit>Se connecter</button></form>
<div class=alt>Pas encore de compte ? <a href='/signup?next={e(nxt)}'>Créer un compte</a></div></div>""", frag)


def _signup_form(nxt: str = "/", err: str = "", email: str = "") -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Inscription", f"""<div class=acard><h1>Créer un compte</h1>
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
async def account_page(request: Request, frag: int = 0):
    email = accounts.session_email(request)
    if not email:                                  # non connecté -> formulaire de connexion dans l'onglet
        return HTMLResponse(_login_form("/compte", frag=bool(frag)))
    e = _html.escape
    sub = accounts.is_subscriber(email)
    badge = ('<span class="abadge on">✓ Abonné</span>' if sub
             else '<span class="abadge off">Non abonné</span>')
    if sub:
        action = ('<div class=ok>Ton abonnement est actif — tu vois tous les pronos joués.</div>'
                  '<form method=post action="/billing/portal"><button class=ghost type=submit>'
                  'Gérer mon abonnement</button></form>')
    else:
        action = ('<div class=sub>Débloque tous les pronos joués (simples + combinés). '
                  'Les stats et résultats sont déjà ouverts.</div>'
                  '<form method=post action="/billing/subscribe"><button type=submit>'
                  "S'abonner</button></form>")
    return HTMLResponse(_page("Mon compte", f"""<div class=acard><h1>Mon compte</h1>
<div class=arow><span>Email</span><b>{e(email)}</b></div>
<div class=arow><span>Abonnement</span>{badge}</div>
{action}
<form method=post action='/logout'><button class=ghost type=submit style='margin-top:12px'>Se déconnecter</button></form>
</div>""", frag=bool(frag)))
