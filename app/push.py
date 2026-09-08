"""Notifications PUSH web (PWA) — « nouveau prono » (demande user 2026-08-16).

Chaîne complète :
  navigateur (service worker + pushManager.subscribe avec la CLÉ PUBLIQUE VAPID)
   -> POST /push/subscribe  -> abonnement stocké ici (data/push_subs.json)
   -> à la publication d'un prono : `push.notify_new_prono(...)` chiffre + envoie à chaque abonnement
      (pywebpush + clé PRIVÉE VAPID) -> le service worker affiche la notification.

Best-effort STRICT : toute panne (clé, réseau, lib absente) -> log debug, JAMAIS d'exception qui casse le
scan ou une route. Les abonnements MORTS (404/410) sont purgés automatiquement.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("betsfix.push")

_DATA = "data"
_VAPID_PEM = os.path.join(_DATA, "push_vapid.pem")       # clé PRIVÉE VAPID (PKCS8 PEM)
_VAPID_PUB = os.path.join(_DATA, "push_vapid_pub.txt")   # clé PUBLIQUE (b64url, applicationServerKey)
_SUBS = os.path.join(_DATA, "push_subs.json")            # [{endpoint, keys:{p256dh,auth}}, ...]
_SENT = os.path.join(_DATA, "push_sent.json")            # {titre: ts} — anti-doublon d'envoi (fenêtre courte)
_SUB_CLAIM = "mailto:noreply@betsfix.com"                # identifiant de l'expéditeur (VAPID `sub`)
_DEDUP_WINDOW = 300     # s : un TITRE identique n'est pas ré-envoyé dans cette fenêtre (défend contre les
#                         doubles tirs — passes reconcile concurrentes, 2 process — et les re-livraisons)

# PUSH PWA COMBINÉS/JAMBES = OFF (user 2026-09-08, capture « spam notification pwa ») : quand plusieurs
# jambes se règlent dans la MÊME passe reconcile, chaque jambe émettait son push « JAMBE GAGNÉE » -> rafale
# (l'anti-doublon ne bloque que les TITRES identiques, or chaque jambe a un titre distinct). Les combinés/
# jambes restent sur le SITE mais ne poussent PLUS en PWA (aligné sur Telegram = Confiance simple only).
# Remettre True pour re-notifier jambes + combiné global en PWA.
PUSH_LEGS_COMBOS = False

_LOCK = threading.Lock()
_PUB_CACHE: str | None = None


def _dup_recent(title: str) -> bool:
    """True si `title` a DÉJÀ été envoyé il y a < _DEDUP_WINDOW (et enregistre l'envoi sinon). Persisté sur
    disque -> tient entre passes ET entre process (API vs tâche reconcile). Lecture fraîche à chaque appel
    (fenêtre de course minime, acceptable pour de la notif). Purge > 1 h."""
    now = time.time()
    with _LOCK:
        try:
            m = json.load(open(_SENT, encoding="utf-8"))
            if not isinstance(m, dict):
                m = {}
        except (OSError, ValueError):
            m = {}
        recent = (title in m) and (now - float(m.get(title) or 0) < _DEDUP_WINDOW)
        if not recent:
            m = {k: v for k, v in m.items() if now - float(v or 0) < 3600}   # purge > 1 h
            m[title] = now
            try:
                tmp = _SENT + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(m, f, ensure_ascii=False)
                os.replace(tmp, _SENT)
            except OSError:
                pass
        return recent


def _ensure_keys() -> None:
    """Génère la paire VAPID (EC P-256) au 1er appel si absente. PEM privé + clé publique b64url."""
    if os.path.exists(_VAPID_PEM) and os.path.exists(_VAPID_PUB):
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    raw = priv.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)  # 65 o
    pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    os.makedirs(_DATA, exist_ok=True)
    with open(_VAPID_PEM, "wb") as f:
        f.write(pem)
    with open(_VAPID_PUB, "w", encoding="utf-8") as f:
        f.write(pub)


def public_key() -> str:
    """Clé publique VAPID (b64url) = `applicationServerKey` du client. '' si indispo (lib absente…)."""
    global _PUB_CACHE
    if _PUB_CACHE is not None:
        return _PUB_CACHE
    try:
        _ensure_keys()
        with open(_VAPID_PUB, encoding="utf-8") as f:
            _PUB_CACHE = f.read().strip()
    except Exception as exc:                     # lib crypto absente / disque : push désactivé proprement
        log.debug("push public_key indispo: %s", exc)
        _PUB_CACHE = ""
    return _PUB_CACHE


def _load_subs() -> list:
    try:
        return json.load(open(_SUBS, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_subs(subs: list) -> None:
    try:
        os.makedirs(_DATA, exist_ok=True)
        tmp = _SUBS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(subs, f, ensure_ascii=False)
        os.replace(tmp, _SUBS)
    except OSError as exc:
        log.debug("push _save_subs: %s", exc)


def add_sub(sub: dict) -> bool:
    """Enregistre un abonnement push (dédupliqué par endpoint). True si ajouté/déjà présent."""
    ep = (sub or {}).get("endpoint")
    if not ep or not (sub.get("keys") or {}).get("p256dh"):
        return False
    with _LOCK:
        subs = _load_subs()
        if not any(s.get("endpoint") == ep for s in subs):
            subs.append({"endpoint": ep, "keys": sub.get("keys")})
            _save_subs(subs)
    return True


def remove_sub(endpoint: str) -> None:
    if not endpoint:
        return
    with _LOCK:
        subs = [s for s in _load_subs() if s.get("endpoint") != endpoint]
        _save_subs(subs)


def sub_count() -> int:
    return len(_load_subs())


def send_push(title: str, body: str, url: str = "/", tag: str = "prono") -> int:
    """Envoie une notification à TOUS les abonnés. Purge les abonnements morts (404/410). Renvoie le
    nombre d'envois réussis. Best-effort : jamais d'exception propagée. Anti-doublon : un TITRE identique
    déjà envoyé il y a < _DEDUP_WINDOW est ignoré (défend contre les doubles tirs / re-livraisons)."""
    if _dup_recent(title):
        log.debug("push doublon ignoré (fenêtre %ss): %s", _DEDUP_WINDOW, title)
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except Exception as exc:
        log.debug("pywebpush absent: %s", exc)
        return 0
    try:
        _ensure_keys()
    except Exception as exc:
        log.debug("push clés indispo: %s", exc)
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    subs = _load_subs()
    ok, dead = 0, []
    for s in subs:
        try:
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": s["keys"]},
                    data=payload, vapid_private_key=_VAPID_PEM,
                    vapid_claims={"sub": _SUB_CLAIM}, ttl=3600)
            ok += 1
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):               # abonnement expiré/révoqué -> purge
                dead.append(s.get("endpoint"))
            else:
                log.debug("push envoi KO (%s): %s", code, exc)
        except Exception as exc:
            log.debug("push envoi erreur: %s", exc)
    if dead:
        with _LOCK:
            rest = [s for s in _load_subs() if s.get("endpoint") not in dead]
            _save_subs(rest)
    return ok


# ═══════════════════════════════════════════════════════════════════════════ #
# NOTIFS PAR MATCH (PWA, user 2026-09-06) — un 🔔 par match : début / but / mi-temps / fin.  #
# Abonnement PAR MATCH, indépendant du push global : {match_id: [endpoint,...]}. Détection   #
# des transitions vs l'état stocké -> push aux abonnés de CE match seulement (pas broadcast). #
# ═══════════════════════════════════════════════════════════════════════════ #
_MATCH_SUBS = os.path.join(_DATA, "push_match_subs.json")    # {match_id: [endpoint,...]}
_MATCH_STATE = os.path.join(_DATA, "push_match_state.json")  # {match_id: {score, kickoff, ht, ft}}


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as exc:
        log.debug("push save %s: %s", path, exc)


def add_match_sub(match_id, endpoint) -> bool:
    """Abonne un endpoint aux événements d'UN match (🔔). Dédupliqué."""
    if not (match_id and endpoint):
        return False
    with _LOCK:
        d = _load_json(_MATCH_SUBS, {})
        lst = d.setdefault(str(match_id), [])
        if endpoint not in lst:
            lst.append(endpoint)
        _save_json(_MATCH_SUBS, d)
    return True


def remove_match_sub(match_id, endpoint) -> None:
    with _LOCK:
        d = _load_json(_MATCH_SUBS, {})
        key = str(match_id)
        if key in d:
            d[key] = [e for e in d[key] if e != endpoint]
            if not d[key]:
                d.pop(key, None)
            _save_json(_MATCH_SUBS, d)


def match_subs_for(endpoint) -> list:
    """match_ids suivis par CET endpoint (état des boutons 🔔 au chargement de la page)."""
    if not endpoint:
        return []
    return [mid for mid, lst in _load_json(_MATCH_SUBS, {}).items() if endpoint in (lst or [])]


def subscribed_match_ids() -> list:
    return list(_load_json(_MATCH_SUBS, {}).keys())


def _match_endpoints(match_id) -> list:
    return list(_load_json(_MATCH_SUBS, {}).get(str(match_id), []))


def send_push_to(endpoints: list, title: str, body: str = "", url: str = "/", tag: str = "match") -> int:
    """Envoie à un SOUS-ENSEMBLE d'endpoints (notifs par match). PAS d'anti-doublon par titre (deux matchs
    peuvent partager « BUT ! ») — la dédup est portée par l'état d'événement de chaque match."""
    if not endpoints:
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return 0
    try:
        _ensure_keys()
    except Exception:
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    subs = [s for s in _load_subs() if s.get("endpoint") in endpoints]
    ok, dead = 0, []
    for s in subs:
        try:
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": s["keys"]},
                    data=payload, vapid_private_key=_VAPID_PEM,
                    vapid_claims={"sub": _SUB_CLAIM}, ttl=3600)
            ok += 1
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                dead.append(s.get("endpoint"))
        except Exception:
            pass
    if dead:
        with _LOCK:
            _save_subs([s for s in _load_subs() if s.get("endpoint") not in dead])
        for _mid in subscribed_match_ids():          # purge aussi les abos par match morts
            for _ep in dead:
                remove_match_sub(_mid, _ep)
    return ok


def _is_halftime(period_id, running, minute) -> bool:
    """Mi-temps foot : période « pause/mi-temps » explicite, OU 1re MT terminée (horloge ARRÊTÉE à ≥45')."""
    pid = str(period_id or "").upper()
    if any(k in pid for k in ("HALFTIME", "HALF_TIME", "PAUSE", "BREAK")):
        return True
    return ("FIRST" in pid) and (running is False) and isinstance(minute, int) and minute >= 45


def notify_match_events(match_id, home, away, hs, as_, period_id, running, minute, finished: bool) -> None:
    """Détecte les transitions live d'un match et pousse aux abonnés 🔔 (idempotent via l'état stocké) :
    DÉBUT (1re fois vu en direct) · BUT (total de buts en hausse) · MI-TEMPS · FIN (réglé). Best-effort."""
    eps = _match_endpoints(match_id)
    if not eps:
        return
    hn, an = str(home or ""), str(away or "")
    score = f"{hs}-{as_}" if (isinstance(hs, int) and isinstance(as_, int)) else None
    is_live = (score is not None) or (isinstance(minute, int) and minute > 0)
    events: list = []
    with _LOCK:
        all_st = _load_json(_MATCH_STATE, {})
        st = all_st.get(str(match_id), {})
        if is_live and not st.get("kickoff"):                          # DÉBUT
            st["kickoff"] = True
            events.append(("kickoff", f"⚽ Coup d'envoi — {hn} vs {an}"))
        prev = st.get("score")
        if score and prev and score != prev:                           # BUT (total de buts en hausse)
            try:
                ph, pa = (int(x) for x in prev.split("-"))
                if (hs + as_) > (ph + pa):
                    scorer = hn if hs > ph else an
                    events.append(("goal", f"⚽ BUT {scorer} ! {hn} {hs}-{as_} {an}"))
            except (ValueError, TypeError):
                pass
        if score:
            st["score"] = score
        if not st.get("ht") and _is_halftime(period_id, running, minute):   # MI-TEMPS
            st["ht"] = True
            events.append(("ht", f"⏸️ Mi-temps — {hn} {score or ''} {an}"))
        if finished and not st.get("ft"):                              # FIN
            st["ft"] = True
            events.append(("ft", f"🏁 Fin du match — {hn} {score or st.get('score') or ''} {an}"))
        all_st[str(match_id)] = st
        _save_json(_MATCH_STATE, all_st)
    for tag, title in events:
        send_push_to(eps, re.sub(r"\s{2,}", " ", title).strip(), "",
                     f"/foot/match/{match_id}", f"m{match_id}-{tag}")
    if any(t == "ft" for t, _ in events):                              # match fini -> plus rien à notifier
        with _LOCK:
            d = _load_json(_MATCH_SUBS, {})
            d.pop(str(match_id), None)
            _save_json(_MATCH_SUBS, d)


def prune_match_state() -> None:
    """Retire l'état des matchs qui n'ont plus d'abonnés (nettoyage best-effort, appelé par la boucle)."""
    with _LOCK:
        subs = _load_json(_MATCH_SUBS, {})
        st = _load_json(_MATCH_STATE, {})
        keep = {k: v for k, v in st.items() if k in subs}
        if len(keep) != len(st):
            _save_json(_MATCH_STATE, keep)


# ───────────────────────────────────────────────────────────────────────────── #
# MESSAGES DES NOTIFICATIONS PUSH (PWA) — PERSONNALISABLES (user 2026-08-24).      #
# C'est le SEUL endroit à éditer pour changer les textes reçus sur le téléphone.   #
# Variables : {match} = équipes  ·  {pick} = pari + cote  ·  {tier} = CONFIANCE/VALUE ·  #
#   {cote} = « @1.28 » (prono).  NOUVEAU PARI (user 2026-08-30) = TITRE SEUL             #
#   « NOUVELLE <TIER> @<cote> », sans équipes ni pari (won/lost gardent {match}/{pick}). #
# Paris SIMPLES Confiance/Value + JAMBES et COMBINÉS notifient (user 2026-09-02 :   #
# « active les notifs pour les jambes et combinés »). Montante toujours coupée.      #
# ───────────────────────────────────────────────────────────────────────────── #
MSG = {
    # NOUVEAU PARI (user 2026-08-30) : TITRE SEUL « NOUVELLE <TIER> @<cote> » — NI équipes NI pari joué.
    "prono": {"title": "NOUVELLE {tier}{cote}", "body": ""},   # {tier} = CONFIANCE / VALUE · {cote} = « @1.28 »
    # RÉSULTAT (user 2026-08-30, aligné Telegram) : TITRE SEUL « <TIER> GAGNÉE @<cote> ✅ » — cote SI GAGNÉ.
    "won":   {"title": "{tier} GAGNÉE{cote} ✅", "body": ""},
    "lost":  {"title": "{tier} PERDUE ❌",       "body": ""},
    "push":  {"title": "{tier} REMBOURSÉE ➖",   "body": ""},
}
_TIER_LABEL = {"confiance": "Confiance", "value": "Value", "montante": "Montante"}
_TIER_LABEL_UP = {"confiance": "CONFIANCE", "value": "VALUE", "montante": "MONTANTE"}


def _vs(match: str) -> str:
    """« A - B » / « A — B » -> « A vs B » (séparateur d'équipes lisible dans la notif)."""
    m = str(match or "")
    for sep in (" — ", " – ", " - "):
        if sep in m:
            return m.replace(sep, " vs ")
    return m


def notify_new_prono(match: str, pick: str, tier: str = "confiance", sport: str = "foot", cote=None) -> int:
    """Notif « nouveau pari » — TITRE SEUL « NOUVELLE <TIER> @<cote> » (user 2026-08-30 : ni équipes ni pari
    joué). `cote` = cote du pari ; à défaut on la lit dans `pick` (« … @ 1.28 »). `match`/`pick` non affichés."""
    m = MSG["prono"]
    _tl = _TIER_LABEL_UP.get((tier or "confiance").lower(), "CONFIANCE")
    if cote is None:                                    # repli : extraire la cote de `pick` (« … @ 1.28 »)
        _mt = re.search(r"@\s*([0-9]+(?:[.,][0-9]+)?)", str(pick or ""))
        cote = _mt.group(1).replace(",", ".") if _mt else None
    if cote is not None:                                # cote à 2 décimales (« @1.60 », pas « @1.6 »)
        try:
            cote = f"{float(str(cote).replace(',', '.')):.2f}"
        except (ValueError, TypeError):
            pass
    _ct = f" @{cote}" if cote else ""
    title = m["title"].format(tier=_tl, cote=_ct)
    return send_push(title, m.get("body", ""), url="/", tag="prono")


def _fmt_cote(cote) -> str:
    """« @1.13 » (2 décimales) ou '' si absente/illisible."""
    if cote is None:
        return ""
    try:
        return f" @{float(str(cote).replace(',', '.')):.2f}"
    except (ValueError, TypeError):
        return ""


def notify_leg(sel: str, mark: str, cote=None) -> int:
    """Notif PWA d'une JAMBE de combiné réglée — « JAMBE GAGNÉE @1.13 ✅ » (aligné Telegram). Cote SI gagné.
    La sélection est en corps de notif (« Plus de 0.5 but FC Midtjylland »). won/lost seulement (le
    « remboursé » d'un push/void = bruit, non notifié)."""
    if not PUSH_LEGS_COMBOS:                             # combinés/jambes = site only (anti-spam PWA)
        return 0
    _vw, _ve = {"won": ("GAGNÉE", "✅"), "lost": ("PERDUE", "❌")}.get(mark, (None, None))
    if _vw is None:
        return 0
    title = f"JAMBE {_vw}{_fmt_cote(cote) if mark == 'won' else ''} {_ve}"
    return send_push(title, str(sel or ""), url="/directs", tag=f"leg:{str(sel or '')[:24]}")


def notify_combo(label: str, mark: str, cote=None) -> int:
    """Notif PWA du COMBINÉ global — « COMBINÉ DU JOUR GAGNÉ @1.55 ✅ ». Cote SI gagné. won/lost seulement."""
    if not PUSH_LEGS_COMBOS:                             # combinés/jambes = site only (anti-spam PWA)
        return 0
    _gw, _ge = {"won": ("GAGNÉ", "✅"), "lost": ("PERDU", "❌")}.get(mark, (None, None))
    if _gw is None:
        return 0
    title = f"{(label or 'COMBINÉ').upper()} {_gw}{_fmt_cote(cote) if mark == 'won' else ''} {_ge}"
    return send_push(title, "", url="/", tag="combo")


def notify_result(match: str, mark: str, pick: str = "", tier: str = "confiance", cote=None) -> int:
    """Notif RÉSULTAT d'un pari simple — TITRE SEUL « <TIER> GAGNÉE @<cote> ✅ » / « <TIER> PERDUE ❌ »
    (user 2026-08-30, aligné sur Telegram). Cote UNIQUEMENT si gagné. Ni équipes ni pari. `cote` = cote du
    pari (repli : lue dans `pick` « … @ 1.28 »). push/void -> « … REMBOURSÉE ➖ »."""
    m = MSG.get(mark) or MSG.get("push")
    if not m:
        return 0
    _tl = _TIER_LABEL_UP.get((tier or "confiance").lower(), "CONFIANCE")
    _ct = ""
    if mark == "won":                                   # cote affichée SEULEMENT sur un gain
        if cote is None:
            _mt = re.search(r"@\s*([0-9]+(?:[.,][0-9]+)?)", str(pick or ""))
            cote = _mt.group(1).replace(",", ".") if _mt else None
        if cote is not None:
            try:
                cote = f"{float(str(cote).replace(',', '.')):.2f}"
            except (ValueError, TypeError):
                pass
        _ct = f" @{cote}" if cote else ""
        # ⚠️ GARDE ANTI-FANTÔME (user 2026-09-03) : un pari GAGNÉ a TOUJOURS une cote (la cote jouée figée).
        # Un « GAGNÉE » SANS cote = carte DÉGRADÉE (piège retained_bet sur un réglé / match re-voidé pendant la
        # passe) -> notif fantôme récurrente (« CONFIANCE/VALUE GAGNÉE ✅ » sans @cote). On SUPPRIME. Ne touche
        # PAS un vrai pari (qui porte sa cote). Les pertes n'affichent jamais de cote -> guard scopé au « won ».
        if not _ct:
            try:                                        # trace dédiée (les logs process sont peu fiables) ->
                with open(os.path.join(_DATA, "push_phantom.log"), "a", encoding="utf-8") as _pf:
                    _pf.write(f"won-no-cote SUPPRIMÉ : match={match!r} tier={tier!r} pick={str(pick)[:80]!r}\n")
            except OSError:
                pass
            log.warning("push résultat SUPPRIMÉ (gagné SANS cote = fantôme) : match=%s tier=%s", match, tier)
            return 0
    return send_push(m["title"].format(tier=_tl, cote=_ct), m.get("body", ""), url="/", tag="result")
