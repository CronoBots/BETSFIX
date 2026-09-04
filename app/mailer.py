"""Envoi d'emails transactionnels BETSFIX (réinitialisation de mot de passe, vérification d'email).

PROVIDER-AGNOSTIQUE : SMTP standard -> marche avec n'importe quel fournisseur (Gmail, SendGrid,
Mailgun, Resend, Brevo, OVH...). Configuration par variables d'environnement :

    BETSFIX_SMTP_HOST     hôte SMTP (ex. smtp.sendgrid.net)
    BETSFIX_SMTP_PORT     port (587 STARTTLS par défaut ; 465 -> SSL direct)
    BETSFIX_SMTP_USER     identifiant SMTP
    BETSFIX_SMTP_PASS     mot de passe / clé API SMTP
    BETSFIX_MAIL_FROM     expéditeur, ex. "BETSFIX <no-reply@betsfix.com>" (défaut : SMTP_USER)
    BETSFIX_SMTP_SSL      "1" pour SSL direct (sinon STARTTLS)

Si NON configuré (dev, ou fournisseur pas encore choisi) : l'email est ÉCRIT dans data/outbox/*.eml et
un avertissement est loggé — JAMAIS d'exception (l'inscription / le reset ne doivent jamais casser
faute d'email). `configured()` dit si un vrai envoi est possible.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import time
from email.message import EmailMessage

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUTBOX = os.path.join(_ROOT, "data", "outbox")


def _cfg() -> dict:
    # Source = config.Settings (pydantic), qui lit les clés BETSFIX_SMTP_* depuis os.environ OU .env : le
    # process SYSTEM (uvicorn) les voit via le fichier .env, sans variable Machine ni élévation. (Avant, ce
    # module lisait os.environ directement -> invisible au SYSTEM qui n'a que le .env.)
    from app.config import get_settings
    s = get_settings()
    host = (s.smtp_host or "").strip()
    user = (s.smtp_user or "").strip()
    return {
        "host": host, "user": user, "pass": (s.smtp_pass or "").strip(),
        "port": int(s.smtp_port or 587),
        "from": (s.mail_from or "").strip() or user or "no-reply@betsfix.com",
        "ssl": bool(s.smtp_ssl) or int(s.smtp_port or 587) == 465,
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["user"] and c["pass"])


def _outbox_write(to: str, subject: str, body: str) -> None:
    """Repli dev : dépose l'email sur disque au lieu de l'envoyer (aucun fournisseur configuré)."""
    try:
        os.makedirs(_OUTBOX, exist_ok=True)
        safe = "".join(ch if ch.isalnum() else "_" for ch in to)[:40]
        path = os.path.join(_OUTBOX, f"{int(time.time())}_{safe}.eml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"To: {to}\nSubject: {subject}\n\n{body}\n")
        print(f"[mailer] SMTP non configuré -> email déposé dans {path}")
    except OSError as exc:
        print(f"[mailer] échec écriture outbox : {exc}")


def send(to: str, subject: str, html: str, text: str | None = None,
         inline_images: dict | None = None) -> bool:
    """Envoie un email. True si remis au SMTP, False si repli outbox / erreur. Ne lève jamais.
    `inline_images` = {cid: chemin_fichier} -> images EMBARQUÉES (CID), référencées par `src="cid:<cid>"`
    dans le HTML. Méthode fiable multi-clients (Gmail/Apple/Outlook) pour un logo, sans image distante."""
    if not configured():
        _outbox_write(to, subject, text or html)
        return False
    c = _cfg()
    msg = EmailMessage()
    msg["From"] = c["from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or "Votre client email ne supporte pas le HTML.")
    msg.add_alternative(html, subtype="html")
    if inline_images:
        html_part = msg.get_payload()[-1]              # la partie text/html (après le text/plain)
        for cid, path in inline_images.items():
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                subtype = (os.path.splitext(path)[1].lstrip(".").lower() or "png")
                html_part.add_related(data, maintype="image", subtype=subtype, cid=f"<{cid}>")
            except OSError:
                pass                                   # image absente -> on n'attache rien (jamais d'erreur)
    try:
        if c["ssl"] or c["port"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=20,
                                  context=ssl.create_default_context()) as s:
                s.login(c["user"], c["pass"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(c["user"], c["pass"])
                s.send_message(msg)
        return True
    except Exception as exc:                      # réseau, auth, TLS... -> on ne casse jamais l'appelant
        print(f"[mailer] échec envoi à {to} : {exc}")
        _outbox_write(to, subject, text or html)
        return False
