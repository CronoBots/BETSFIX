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
    """Base publique pour les liens des emails. Priorité : config.Settings.public_url (os.environ OU .env,
    visible au process SYSTEM), sinon l'hôte de la requête."""
    from app.config import get_settings
    cfg = (get_settings().public_url or os.environ.get("BETSFIX_PUBLIC_URL") or "").strip().rstrip("/")
    return cfg or str(request.base_url).rstrip("/")


# --------------------------------------------------------------------------- template email de marque
# Emails transactionnels HABILLÉS BETSFIX : table + styles INLINE (compat clients mail, y compris Outlook),
# carte sombre premium cohérente avec l'app. Pas d'image distante (robuste + le PC peut être down) : wordmark
# en texte. `color-scheme` évite que le mode sombre des clients réinvente les couleurs.
_MAIL_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORDMARK = os.path.join(_ROOT, "static", "wordmark.png")
_LOGO_CID = "bxlogo"


def _mail_images() -> dict:
    """Images à embarquer (CID) dans les emails : le wordmark en en-tête. Vide si le fichier manque."""
    return {_LOGO_CID: _WORDMARK} if os.path.exists(_WORDMARK) else {}


def _email_shell(heading: str, inner: str, preheader: str = "") -> str:
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#0b0d12">'
           f'{_html.escape(preheader)}</div>' if preheader else "")
    # Logo embarqué (CID) si présent, sinon repli wordmark texte (jamais d'image cassée).
    if os.path.exists(_WORDMARK):
        brand = (f'<img src="cid:{_LOGO_CID}" width="193" height="32" alt="BETSFIX" '
                 'style="display:inline-block;height:32px;width:193px;border:0;outline:none;text-decoration:none">')
    else:
        brand = (f'<div style="font:800 20px/1 {_MAIL_FONT};letter-spacing:.06em;color:#eaf4ff">'
                 'BETS<span style="color:#22b8ff">FIX</span></div>')
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"></head>
<body style="margin:0;padding:0;background:#0b0d12">{pre}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b0d12;padding:28px 14px">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#0f1621;border:1px solid #1e2a3a;border-radius:16px">
<tr><td align="center" style="padding:26px 26px 0;text-align:center">{brand}
<div style="font:600 10px/1.4 {_MAIL_FONT};color:#6f8299;margin-top:10px;letter-spacing:.14em">PRONOSTICS FOOTBALL · DONNÉES VÉRIFIÉES</div>
</td></tr>
<tr><td align="center" style="padding:16px 26px 0"><div style="width:64px;height:3px;margin:0 auto;font-size:0;line-height:0;border-radius:3px;background:#22b8ff;background:linear-gradient(90deg,#22b8ff,#34d27b)">&nbsp;</div></td></tr>
<tr><td align="center" style="padding:18px 26px 26px;text-align:center">
<h1 style="font:800 22px/1.28 {_MAIL_FONT};color:#eaf4ff;margin:0 0 12px">{heading}</h1>
{inner}
</td></tr>
<tr><td align="center" style="padding:15px 26px;background:#0b1119;border-top:1px solid #1e2a3a;border-radius:0 0 16px 16px;text-align:center">
<div style="font:400 11px/1.6 {_MAIL_FONT};color:#5f7288">
© BETSFIX · <a href="https://api.betsfix.com" style="color:#5fd0ff;text-decoration:none">betsfix.com</a><br>
Email automatique lié à ton compte — merci de ne pas y répondre.</div>
</td></tr></table></td></tr></table></body></html>"""


def _email_button(href: str, label: str) -> str:
    return (f'<table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:6px auto 18px"><tr>'
            f'<td style="border-radius:12px;background:#22b8ff">'
            f'<a href="{href}" style="display:inline-block;padding:13px 24px;font:700 15px/1 {_MAIL_FONT};'
            f'color:#04121c;text-decoration:none;border-radius:12px">{label}</a></td></tr></table>')


def _send_reset_email(request: Request, email: str) -> None:
    token = accounts.make_reset_token(email)
    if not token:                                  # compte inconnu -> on n'envoie rien (anti-énumération)
        return
    link = f"{_base_url(request)}/reset?token={_html.escape(token)}"
    inner = (f'<p style="font:400 15px/1.6 {_MAIL_FONT};color:#a7bcd6;margin:0 0 6px">'
             'Clique sur le bouton ci-dessous pour choisir un nouveau mot de passe. '
             'Ce lien expire dans <b style="color:#eaf4ff">1 heure</b>.</p>'
             f'{_email_button(link, "Choisir un nouveau mot de passe")}'
             f'<p style="font:400 12px/1.5 {_MAIL_FONT};color:#6f8299;margin:0">'
             "Si tu n'es pas à l'origine de cette demande, ignore simplement cet email.</p>")
    mailer.send(email, "Réinitialise ton mot de passe BETSFIX",
                _email_shell("Réinitialise ton mot de passe", inner,
                             preheader="Lien de réinitialisation (valable 1 h)"),
                text=f"Réinitialise ton mot de passe BETSFIX : {link} (valable 1 h).",
                inline_images=_mail_images())


def _send_verify_email(request: Request, email: str) -> None:
    token = accounts.make_verify_token(email)
    link = f"{_base_url(request)}/verify?token={_html.escape(token)}"
    inner = (f'<p style="font:400 15px/1.6 {_MAIL_FONT};color:#a7bcd6;margin:0 0 6px">'
             f'Confirme ton adresse pour sécuriser ton compte. Ton essai gratuit de '
             f'<b style="color:#eaf4ff">{accounts.TRIAL_DAYS} jours</b> est déjà actif — profites-en pour '
             'voir tous les pronos joués.</p>'
             f'{_email_button(link, "Confirmer mon email")}')
    mailer.send(email, "Confirme ton email BETSFIX",
                _email_shell("Bienvenue sur BETSFIX 👋", inner,
                             preheader="Confirme ton adresse pour sécuriser ton compte"),
                text=f"Confirme ton email BETSFIX : {link}",
                inline_images=_mail_images())


def _send_code_email(request: Request, email: str, code: str) -> None:
    """Envoie le code à 6 chiffres (connexion sans mot de passe). Le sujet PORTE le code -> visible dès
    la notification, sans ouvrir l'email."""
    inner = (f'<p style="font:400 15px/1.6 {_MAIL_FONT};color:#a7bcd6;margin:0 0 18px">'
             'Entre ce code pour te connecter à BETSFIX. Il expire dans '
             '<b style="color:#eaf4ff">10 minutes</b>.</p>'
             f'<div style="font:800 34px/1 {_MAIL_FONT};letter-spacing:12px;color:#06283c;background:#eaf3ff;'
             'border:1px solid #cfe6ff;border-radius:12px;padding:20px 10px;text-align:center;margin:0 0 16px">'
             f'{_html.escape(code)}</div>'
             f'<p style="font:400 12px/1.5 {_MAIL_FONT};color:#6f8299;margin:0">'
             "Si tu n'es pas à l'origine de cette demande, ignore simplement cet email.</p>")
    mailer.send(email, f"{code} — ton code de connexion BETSFIX",
                _email_shell("Ton code de connexion", inner,
                             preheader=f"Code : {code} (valable 10 minutes)"),
                text=f"Ton code de connexion BETSFIX : {code} (valable 10 minutes).",
                inline_images=_mail_images())

# Le CSS du compte (scopé .acctwrap) est désormais GLOBAL dans web.py (toujours chargé) -> le contenu
# marche aussi bien en page pleine qu'en FRAGMENT injecté dans le panneau SPA.

# --------------------------------------------------------------------------- connexion par code (6 chiffres)
_OTP_CSS = """<style>
.acctwrap .otp-row{display:flex;gap:8px;justify-content:center;margin:8px 0 4px}
.acctwrap .otp-b{width:46px;height:58px;flex:0 0 auto;text-align:center;font-size:26px;font-weight:800;
 color:#eaf4ff;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.14);border-radius:12px;
 caret-color:#22b8ff;padding:0;margin:0}
.acctwrap .otp-b:focus{outline:none;border-color:#22b8ff;box-shadow:0 0 0 3px rgba(34,184,255,.25)}
.acctwrap .otp-b.filled{border-color:rgba(34,184,255,.55);background:rgba(34,184,255,.08)}
.acctwrap .otp-tip{text-align:center;font-size:11px;color:#7c90ab;margin:8px 0 0}
@media(max-width:360px){.acctwrap .otp-b{width:40px;height:52px;font-size:22px}}
</style>"""

_OTP_JS = """<script>(function(){
var f=document.getElementById('otpf');if(!f)return;
var bs=[].slice.call(f.querySelectorAll('.otp-b'));
function upd(){for(var i=0;i<bs.length;i++)bs[i].classList.toggle('filled',!!bs[i].value);}
function full(){return bs.every(function(b){return b.value.length===1;});}
function trySubmit(){if(full()){upd();f.submit();}}
bs.forEach(function(b,i){
 b.addEventListener('input',function(){
  b.value=b.value.replace(/[^0-9]/g,'').slice(0,1);
  if(b.value&&i<bs.length-1)bs[i+1].focus();upd();trySubmit();});
 b.addEventListener('keydown',function(ev){
  if(ev.key==='Backspace'&&!b.value&&i>0){bs[i-1].focus();bs[i-1].value='';upd();ev.preventDefault();}
  else if(ev.key==='ArrowLeft'&&i>0)bs[i-1].focus();
  else if(ev.key==='ArrowRight'&&i<bs.length-1)bs[i+1].focus();});
 b.addEventListener('paste',function(ev){
  var t=((ev.clipboardData||window.clipboardData).getData('text')||'').replace(/[^0-9]/g,'').slice(0,6);
  if(!t)return;ev.preventDefault();
  for(var j=0;j<6;j++)bs[j].value=t[j]||'';
  bs[Math.min(t.length,5)].focus();upd();trySubmit();});
});
})();</script>"""


def _code_form(nxt: str = "/", err: str = "", email: str = "", frag: bool = False) -> str:
    """Entrée du flux SANS mot de passe : email -> on envoie un code à 6 chiffres. Unifie connexion et
    inscription (compte créé au 1er code validé)."""
    e = _html.escape
    err_html = f'<div class=err>{e(err)}</div>' if err else ""
    return _page("Connexion", f"""<div class=acard><h1>Connexion / inscription</h1>
<div class=sub>Entre ton email : on t'envoie un code à 6 chiffres, pas de mot de passe à retenir.
Nouveau ? Ton compte est créé avec {accounts.TRIAL_DAYS} jours d'essai. Stats et résultats restent ouverts à tous.</div>
{err_html}<form method=post action='/auth/code'>
<input type=hidden name=next value='{e(nxt)}'>
<label>Email</label><input name=email type=email autocomplete=email inputmode=email value='{e(email)}' required autofocus>
<button type=submit>Recevoir mon code</button></form>
<div class=alt><a href='/login?pw=1&next={e(nxt)}'>Utiliser un mot de passe</a></div></div>""", frag)


def _otp_page(nxt: str, email: str, token: str, err: str = "", info: str = "") -> str:
    """Saisie du code à 6 chiffres (auto-avance, coller pour tout remplir, auto-validation)."""
    e = _html.escape
    box = (f'<div class=err>{e(err)}</div>' if err
           else f'<div class=ok>{e(info)}</div>' if info else "")
    boxes = "".join(
        f'<input class=otp-b name="c{i}" inputmode=numeric pattern="[0-9]*" maxlength=1 '
        f'autocomplete="{"one-time-code" if i == 0 else "off"}" aria-label="chiffre {i + 1}"'
        f'{" autofocus" if i == 0 else ""}>' for i in range(6))
    body = f"""<div class=acard><h1>Entre ton code</h1>
<div class=sub>On a envoyé un code à 6 chiffres à <b>{e(email)}</b>. Il expire dans 10 minutes.</div>
{box}<form id=otpf method=post action='/auth/verify'>
<input type=hidden name=next value='{e(nxt)}'>
<input type=hidden name=email value='{e(email)}'>
<input type=hidden name=token value='{e(token)}'>
<div class=otp-row>{boxes}</div>
<div class=otp-tip>Astuce : colle le code pour remplir toutes les cases d'un coup.</div>
<button type=submit>Valider</button></form>
<form method=post action='/auth/code' style='margin-top:2px'>
<input type=hidden name=next value='{e(nxt)}'>
<input type=hidden name=email value='{e(email)}'>
<button class=ghost type=submit>Renvoyer un code</button></form>
<div class=alt><a href='/login?next={e(nxt)}'>Changer d'email</a></div></div>{_OTP_CSS}{_OTP_JS}"""
    return _page("Code de connexion", body)


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
async def login_page(request: Request, next: str = "/", pw: int = 0):
    if accounts.session_email(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    nxt = _safe_next(next)
    return HTMLResponse(_login_form(nxt) if pw else _code_form(nxt))


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
async def signup_page(request: Request, next: str = "/", pw: int = 0):
    if accounts.session_email(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    nxt = _safe_next(next)
    return HTMLResponse(_signup_form(nxt) if pw else _code_form(nxt))


# --------------------------------------------------------------------------- flux code (6 chiffres)
@router.post("/auth/code", response_class=HTMLResponse, include_in_schema=False)
async def auth_code(request: Request, next: str = Form("/"), email: str = Form(...)):
    """Demande d'un code : valide l'email, l'envoie, puis affiche la page de saisie du code."""
    nxt = _safe_next(next)
    em = accounts._norm(email)
    if not accounts.valid_email(em):
        return HTMLResponse(_code_form(nxt, "Adresse email invalide.", email), status_code=400)
    if not accounts.code_send_allowed(em):
        return HTMLResponse(_code_form(nxt, "Trop de codes demandés. Réessaie dans quelques minutes.", email),
                            status_code=429)
    code, token = accounts.make_login_code(em)
    try:
        _send_code_email(request, em, code)        # best-effort : ne casse jamais le flux (repli outbox)
    except Exception:
        pass
    accounts.note_code_sent(em)
    return HTMLResponse(_otp_page(nxt, em, token, info="Code envoyé. Regarde tes emails (et les spams)."))


@router.post("/auth/verify", response_class=HTMLResponse, include_in_schema=False)
async def auth_verify(request: Request, next: str = Form("/"), email: str = Form(...),
                      token: str = Form(...), c0: str = Form(""), c1: str = Form(""),
                      c2: str = Form(""), c3: str = Form(""), c4: str = Form(""), c5: str = Form("")):
    """Vérifie le code : compte créé (si nouveau) + session ouverte. Anti-force-brute par email+IP."""
    nxt = _safe_next(next)
    em = accounts._norm(email)
    ip = request.client.host if request.client else "?"
    key = f"code|{em}|{ip}"
    if accounts.login_blocked(key):
        return HTMLResponse(_otp_page(nxt, em, token, "Trop de tentatives. Redemande un code."),
                            status_code=429)
    code = (c0 + c1 + c2 + c3 + c4 + c5).strip()
    verified = accounts.check_login_code(token, code)     # email de CONFIANCE (issu du jeton signé)
    if not verified:
        accounts.note_login_fail(key)
        return HTMLResponse(_otp_page(nxt, em, token, "Code incorrect ou expiré. Réessaie."),
                            status_code=401)
    accounts.note_login_ok(key)
    accounts.ensure_user(verified)                        # crée le compte + essai si nouveau
    accounts.mark_verified(verified)                      # saisir un code reçu par mail PROUVE le contrôle de la boîte
    resp = RedirectResponse(nxt, status_code=303)
    _set_cookie(resp, verified)
    return resp


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
    if not email:                                  # non connecté -> flux code (sans mot de passe) dans l'onglet
        return HTMLResponse(_code_form("/compte", frag=bool(frag)))
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
