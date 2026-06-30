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

# CSS SCOPÉ sous .acctwrap : la page Compte est désormais rendue DANS la coquille de l'app
# (web.layout -> barre du bas + thème + halos), donc on ne style que le contenu du compte et on
# évite tout conflit avec les classes globales (.card/.row/.badge existent ailleurs).
_CSS = """
.acctwrap{max-width:400px;margin:6px auto 0;width:100%}
.acctwrap .acard{background:linear-gradient(180deg,rgba(34,184,255,.07),rgba(34,184,255,.02));
  border:1px solid rgba(34,184,255,.22);border-radius:18px;padding:22px 20px}
.acctwrap h1{font-size:19px;font-weight:800;margin:0 0 4px;color:#e9f1fb}
.acctwrap .sub{font-size:12px;color:#90a4be;margin:0 0 18px;line-height:1.5}
.acctwrap label{display:block;font-size:11px;font-weight:700;color:#90a4be;text-transform:uppercase;
  letter-spacing:.04em;margin:14px 0 6px}
.acctwrap input{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);
  border-radius:11px;padding:12px 13px;color:#e9f1fb;font-family:inherit;font-size:14px}
.acctwrap input:focus{outline:none;border-color:rgba(34,184,255,.6)}
.acctwrap button{width:100%;margin-top:20px;background:#22b8ff;color:#04121c;border:0;border-radius:12px;
  padding:13px;font-family:inherit;font-size:14px;font-weight:800;cursor:pointer}
.acctwrap button.ghost{background:transparent;color:#5fd0ff;border:1px solid rgba(34,184,255,.35)}
.acctwrap .err{background:rgba(255,80,90,.12);border:1px solid rgba(255,80,90,.4);color:#ff9aa1;
  border-radius:10px;padding:10px 12px;font-size:12px;margin-bottom:14px;line-height:1.4}
.acctwrap .ok{background:rgba(25,196,106,.12);border:1px solid rgba(25,196,106,.4);color:#8df3c0;
  border-radius:10px;padding:10px 12px;font-size:12px;margin-bottom:14px;line-height:1.4}
.acctwrap .alt{text-align:center;font-size:12px;color:#90a4be;margin-top:18px}
.acctwrap a{color:#5fd0ff;text-decoration:none}
.acctwrap .arow{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:13px;
  padding:11px 0;border-top:1px solid rgba(255,255,255,.08);color:#e9f1fb}
.acctwrap .arow b{font-weight:800}
.acctwrap .abadge{font-size:11px;font-weight:800;border-radius:7px;padding:3px 9px}
.acctwrap .abadge.on{background:rgba(25,196,106,.18);color:#8df3c0}
.acctwrap .abadge.off{background:rgba(150,165,185,.16);color:#c0cbdb}
"""


def _page(title: str, body: str) -> str:
    """Rend le contenu Compte DANS la coquille app (barre du bas + thème). 'compte' = onglet actif."""
    from app import web                       # import paresseux (évite tout cycle à l'import)
    inner = f'<style>{_CSS}</style><div class="acctwrap">{body}</div>'
    return web.layout(title, "compte", inner)


def _safe_next(nxt: str | None) -> str:
    """N'autorise qu'un chemin interne (anti open-redirect)."""
    return nxt if (nxt and nxt.startswith("/") and not nxt.startswith("//")) else "/"


def _set_cookie(resp, email: str) -> None:
    resp.set_cookie(accounts.COOKIE, accounts.make_session(email), max_age=accounts._SESSION_MAX_AGE,
                    httponly=True, samesite="lax", path="/")


def _login_form(nxt: str = "/", err: str = "", email: str = "") -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Connexion", f"""<div class=acard><h1>Connexion</h1>
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
async def account_page(request: Request):
    email = accounts.session_email(request)
    if not email:
        return HTMLResponse(_login_form("/compte"))
    e = _html.escape
    sub = accounts.is_subscriber(email)
    badge = ('<span class="abadge on">✓ Abonné</span>' if sub
             else '<span class="abadge off">Non abonné</span>')
    if sub:
        action = ('<div class=ok>Ton abonnement est actif — tu vois tous les pronos ⭐.</div>'
                  '<form method=post action="/billing/portal"><button class=ghost type=submit>'
                  'Gérer mon abonnement</button></form>')
    else:
        action = ('<div class=sub>Débloque tous les pronos ⭐ (simples + combinés). '
                  'Les stats et résultats sont déjà ouverts.</div>'
                  '<form method=post action="/billing/subscribe"><button type=submit>'
                  "S'abonner</button></form>")
    return HTMLResponse(_page("Mon compte", f"""<div class=acard><h1>Mon compte</h1>
<div class=arow><span>Email</span><b>{e(email)}</b></div>
<div class=arow><span>Abonnement</span>{badge}</div>
{action}
<form method=post action='/logout'><button class=ghost type=submit style='margin-top:12px'>Se déconnecter</button></form>
</div>"""))
