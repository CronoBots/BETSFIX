"""Pages de compte : inscription, connexion, déconnexion, /compte (statut d'abonnement).

Phase 1 du paywall : email + mot de passe (cf. app/accounts.py), session par cookie signé. Le bouton
« S'abonner » (Stripe) sera branché en Phase 2 ; ici il renvoie une page d'attente claire.
"""
from __future__ import annotations

import html as _html
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import accounts, mailer

router = APIRouter(tags=["🖥️ Interface (pages HTML)"])


def _base_url(request: Request) -> str:
    """Base publique pour les liens des emails (env prioritaire, sinon l'hôte de la requête)."""
    env = (os.environ.get("BETSFIX_PUBLIC_URL") or "").strip().rstrip("/")
    return env or str(request.base_url).rstrip("/")


def _send_reset_email(request: Request, email: str) -> None:
    token = accounts.make_reset_token(email)
    if not token:                                  # compte inconnu -> on n'envoie rien (anti-énumération)
        return
    link = f"{_base_url(request)}/reset?token={_html.escape(token)}"
    mailer.send(email, "Réinitialise ton mot de passe BETSFIX",
                f"""<div style="font-family:system-ui,Arial;max-width:480px">
<h2>Réinitialisation du mot de passe</h2>
<p>Clique sur le lien ci-dessous pour choisir un nouveau mot de passe. Il expire dans 1 heure.</p>
<p><a href="{link}" style="background:#111;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none">Choisir un nouveau mot de passe</a></p>
<p style="color:#666;font-size:13px">Si tu n'es pas à l'origine de cette demande, ignore cet email.</p></div>""",
                text=f"Réinitialise ton mot de passe BETSFIX : {link} (valable 1 h).")


def _send_verify_email(request: Request, email: str) -> None:
    token = accounts.make_verify_token(email)
    link = f"{_base_url(request)}/verify?token={_html.escape(token)}"
    mailer.send(email, "Confirme ton email BETSFIX",
                f"""<div style="font-family:system-ui,Arial;max-width:480px">
<h2>Bienvenue sur BETSFIX 👋</h2>
<p>Confirme ton adresse pour sécuriser ton compte. Ton essai gratuit de {accounts.TRIAL_DAYS} jours est déjà actif.</p>
<p><a href="{link}" style="background:#111;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none">Confirmer mon email</a></p></div>""",
                text=f"Confirme ton email BETSFIX : {link}")

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
    # « Compte » n'est PLUS un onglet SPA (déplacé en bouton HAUT À DROITE, 2026-07-30) -> page PLEINE via
    # web.layout : logo + bouton compte + barre du bas (Accueil·Pronos·Live·Résultats·Montante). Taper un
    # onglet recharge la page (comme les autres pages layout). Le fragment (frag) reste dispo si besoin.
    return web.layout(title, "compte", inner)


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
<div class=alt><a href='/forgot'>Mot de passe oublié ?</a></div>
<div class=alt>Pas encore de compte ? <a href='/signup?next={e(nxt)}'>Créer un compte</a></div></div>""", frag)


def _signup_form(nxt: str = "/", err: str = "", email: str = "") -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Inscription", f"""<div class=acard><h1>Créer un compte</h1>
<div class=sub>Essai gratuit de {accounts.TRIAL_DAYS} jours : tu vois tous les pronos joués immédiatement. Ensuite, abonnement pour continuer — stats et résultats restent ouverts à tous.</div>
{err_html}<form method=post action='/signup'>
<input type=hidden name=next value='{e(nxt)}'>
<label>Email</label><input name=email type=email autocomplete=email value='{e(email)}' required>
<label>Mot de passe</label><input name=password type=password autocomplete=new-password minlength=8 required>
<div class=hint>8 caractères minimum.</div>
<button type=submit>Créer mon compte</button></form>
<div class=alt>Déjà inscrit ? <a href='/login?next={e(nxt)}'>Se connecter</a></div></div>""")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/"):
    if accounts.session_email(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return HTMLResponse(_login_form(_safe_next(next)))


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(request: Request, next: str = Form("/"), email: str = Form(...),
                       password: str = Form(...)):
    nxt = _safe_next(next)
    ip = request.client.host if request.client else "?"
    key = f"{accounts._norm(email)}|{ip}"                 # anti brute-force : email + IP
    if accounts.login_blocked(key):
        return HTMLResponse(_login_form(nxt, "Trop de tentatives. Réessaie dans quelques minutes.", email),
                            status_code=429)
    if not accounts.verify_login(email, password):
        accounts.note_login_fail(key)
        return HTMLResponse(_login_form(nxt, "Email ou mot de passe incorrect.", email), status_code=401)
    accounts.note_login_ok(key)
    resp = RedirectResponse(nxt, status_code=303)
    _set_cookie(resp, email)
    return resp


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(request: Request, next: str = "/"):
    if accounts.session_email(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return HTMLResponse(_signup_form(_safe_next(next)))


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_submit(request: Request, next: str = Form("/"), email: str = Form(...),
                        password: str = Form(...)):
    nxt = _safe_next(next)
    ok, err = accounts.create_user(email, password)
    if not ok:
        return HTMLResponse(_signup_form(nxt, err, email), status_code=400)
    try:
        _send_verify_email(request, email)         # best-effort : ne bloque jamais l'inscription
    except Exception:
        pass
    resp = RedirectResponse(nxt, status_code=303)
    _set_cookie(resp, email)
    return resp


@router.post("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(accounts.COOKIE, path="/")
    return resp


# --------------------------------------------------------------------------- mot de passe oublié
def _forgot_form(msg: str = "", ok: bool = False) -> str:
    box = f'<div class="{"ok" if ok else "err"}">{_html.escape(msg)}</div>' if msg else ""
    return _page("Mot de passe oublié", f"""<div class=acard><h1>Mot de passe oublié</h1>
<div class=sub>Entre ton email : si un compte existe, tu recevras un lien pour choisir un nouveau mot de passe.</div>
{box}<form method=post action='/forgot'>
<label>Email</label><input name=email type=email autocomplete=email required>
<button type=submit>Envoyer le lien</button></form>
<div class=alt><a href='/login'>Retour à la connexion</a></div></div>""")


@router.get("/forgot", response_class=HTMLResponse, include_in_schema=False)
async def forgot_page():
    return HTMLResponse(_forgot_form())


@router.post("/forgot", response_class=HTMLResponse, include_in_schema=False)
async def forgot_submit(request: Request, email: str = Form(...)):
    try:
        _send_reset_email(request, email)          # silencieux si compte inconnu (anti-énumération)
    except Exception:
        pass
    # message IDENTIQUE que le compte existe ou non -> ne révèle pas quels emails sont inscrits
    return HTMLResponse(_forgot_form(
        "Si un compte existe avec cet email, un lien de réinitialisation vient d'être envoyé.", ok=True))


def _reset_form(token: str, err: str = "") -> str:
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Nouveau mot de passe", f"""<div class=acard><h1>Nouveau mot de passe</h1>
{err_html}<form method=post action='/reset'>
<input type=hidden name=token value='{e(token)}'>
<label>Nouveau mot de passe</label>
<input name=password type=password autocomplete=new-password minlength=8 required>
<button type=submit>Enregistrer</button></form></div>""")


@router.get("/reset", response_class=HTMLResponse, include_in_schema=False)
async def reset_page(token: str = ""):
    if not accounts.check_reset_token(token):
        return HTMLResponse(_page("Lien expiré",
            "<div class=acard><h1>Lien invalide ou expiré</h1>"
            "<div class=sub>Demande un nouveau lien.</div>"
            "<div class=alt><a href='/forgot'>Mot de passe oublié</a></div></div>"), status_code=400)
    return HTMLResponse(_reset_form(token))


@router.post("/reset", response_class=HTMLResponse, include_in_schema=False)
async def reset_submit(token: str = Form(...), password: str = Form(...)):
    email = accounts.check_reset_token(token)
    if not email:
        return HTMLResponse(_page("Lien expiré",
            "<div class=acard><h1>Lien invalide ou expiré</h1>"
            "<div class=alt><a href='/forgot'>Demander un nouveau lien</a></div></div>"), status_code=400)
    okpw, err = accounts.set_password(email, password)
    if not okpw:
        return HTMLResponse(_reset_form(token, err), status_code=400)
    resp = RedirectResponse("/compte", status_code=303)   # mot de passe changé -> connexion directe
    _set_cookie(resp, email)
    return resp


@router.get("/verify", response_class=HTMLResponse, include_in_schema=False)
async def verify_email(token: str = ""):
    email = accounts.check_verify_token(token)
    if not email:
        return HTMLResponse(_page("Lien invalide",
            "<div class=acard><h1>Lien de confirmation invalide ou expiré</h1></div>"), status_code=400)
    accounts.mark_verified(email)
    return HTMLResponse(_page("Email confirmé",
        "<div class=acard><h1>Email confirmé ✓</h1>"
        "<div class=sub>Merci — ton compte est sécurisé.</div>"
        "<div class=alt><a href='/compte'>Aller à mon compte</a></div></div>"))


@router.get("/compte", response_class=HTMLResponse, include_in_schema=False)
async def account_page(request: Request, frag: int = 0):
    email = accounts.session_email(request)
    if not email:                                  # non connecté -> formulaire de connexion dans l'onglet
        return HTMLResponse(_login_form("/compte", frag=bool(frag)))
    e = _html.escape
    sub = accounts.is_subscriber(email)
    plan = accounts.plan_of(email)
    u = accounts.get_user(email) or {}
    import time as _t
    trial_left = int(((u.get("trial_until") or 0) - _t.time()) // 86400 + 1) if plan == "trial" else 0
    plabel = (accounts.plans().get(plan) or {}).get("label", plan)
    if plan == "trial" and sub:
        badge = f'<span class="abadge on">Essai — {max(trial_left,0)} j restants</span>'
    elif sub:
        badge = f'<span class="abadge on">✓ Abonné · {e(plabel)}</span>'
    else:
        badge = '<span class="abadge off">Non abonné</span>'
    if sub and plan != "trial":
        action = ('<div class=ok>Ton abonnement est actif — tu vois tous les pronos joués.</div>'
                  '<form method=post action="/billing/portal"><button class=ghost type=submit>'
                  'Gérer mon abonnement</button></form>')
    else:
        head = ('<div class=ok>Essai gratuit en cours — profites-en pour voir les pronos joués. '
                'Abonne-toi pour continuer sans coupure.</div>' if plan == "trial"
                else '<div class=sub>Débloque tous les pronos joués (simples + combinés). '
                     'Les stats et résultats sont déjà ouverts.</div>')
        action = (head + '<form method=post action="/billing/subscribe"><button type=submit>'
                  "S'abonner</button></form>")
    return HTMLResponse(_page("Mon compte", f"""<div class=acard><h1>Mon compte</h1>
<div class=arow><span>Email</span><b>{e(email)}</b></div>
<div class=arow><span>Abonnement</span>{badge}</div>
{action}
<form method=post action='/logout'><button class=ghost type=submit style='margin-top:12px'>Se déconnecter</button></form>
</div>""", frag=bool(frag)))
