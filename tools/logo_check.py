# -*- coding: utf-8 -*-
"""VÉRIFICATION DES LOGOS D'ÉQUIPE du programme du jour (scan matin + scan soir).

Pourquoi : les cartes (site ET Telegram) affichent le blason de chaque équipe via
`crest.team_id(nom)` -> `crest.logo_url(id)`. Une équipe non résolue par FotMob sort une carte
SANS logo — défaut visible par l'abonné, découvert seulement au moment de la publication
(vague KO-1 h), donc trop tard pour réagir.

Ce contrôle tourne juste APRÈS la sélection du programme (matin = slate JOUR, soir = slate NUIT) :

  1. RÉSOUT le blason des 2 équipes de chaque match du programme. `crest.team_id` PERSISTE ses
     succès dans data/crest_cache.json -> ce contrôle PRÉ-CHAUFFE le cache : au moment de la
     vague, le logo est déjà connu (pas de résolution à chaud pendant la publication).
  2. VÉRIFIE que l'URL du logo répond vraiment (HTTP 200). `logo_url` fabrique l'URL à partir de
     l'id SANS la tester : un id valide peut pointer vers une image absente (404) -> carte trouée
     malgré une résolution « réussie ».
  3. ALERTE EN PRIVÉ (data/owner_chat.txt, JAMAIS le canal abonnés) en listant les équipes à
     corriger — un alias dans `crest._ALIAS` suffit en général à réparer une résolution.

100 % lecture seule côté paris/stats : ne touche ni sidecar, ni sélection, ni ROI. N'écrit que le
cache de blasons (effet voulu) et son propre suivi de dédup.

Usage :
    python tools/logo_check.py                 # rapport console
    python tools/logo_check.py --quiet         # cron : sortie compacte
    python tools/logo_check.py --quiet --alert # + alerte PRIVÉE si logo manquant (1×/équipe/jour)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):          # idiome projet : console Windows en cp1252
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app import analyses as A      # noqa: E402
from app import crest as C         # noqa: E402

HTTP_TIMEOUT = 8.0
_SENT = os.path.join(os.path.dirname(A.DIR), "logo_check_sent.json")


def _prog_path() -> str:
    return os.path.join(os.path.dirname(A.DIR), "day_programme.json")


def _load_programme() -> dict:
    try:
        p = json.load(open(_prog_path(), encoding="utf-8"))
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _send_owner(text: str) -> bool:
    """Alerte EN PRIVÉ au propriétaire (data/owner_chat.txt) — JAMAIS le canal abonnés. Best-effort."""
    chat_p = os.path.join(os.path.dirname(A.DIR), "owner_chat.txt")
    if not os.path.exists(chat_p):
        return False
    try:
        from app import notify
        chat = open(chat_p, encoding="utf-8").read().strip()
        tok, _ = notify._config()
        if not (tok and chat):
            return False
        import httpx
        httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                   json={"chat_id": chat, "text": text[:3900]}, timeout=15)
        return True
    except Exception as exc:
        print(f"(alerte privée ignorée : {exc})")
        return False


_FM_DAY_CACHE: dict[str, list] = {}


def _fm_day(ymd: str) -> list:
    """Fixtures FotMob d'un jour : [(home, hid, away, aid, utc)]. Couvre BEAUCOUP plus de ligues que
    `crest._fetch` (qui interroge la recherche « suggest », muette sur la Pro League belge par ex.)."""
    if ymd in _FM_DAY_CACHE:
        return _FM_DAY_CACHE[ymd]
    out = []
    try:
        import httpx
        j = httpx.get("https://www.fotmob.com/api/data/matches", params={"date": ymd},
                      headers=C._UA, timeout=15).json()
        for lg in (j or {}).get("leagues") or []:
            for m in lg.get("matches") or []:
                h, a = m.get("home") or {}, m.get("away") or {}
                out.append(((h.get("longName") or h.get("name") or ""), h.get("id"),
                            (a.get("longName") or a.get("name") or ""), a.get("id"),
                            (m.get("status") or {}).get("utcTime")))
    except Exception:
        return []
    _FM_DAY_CACHE[ymd] = out
    return out


def _same_team(a: str, b: str) -> bool:
    """Deux libellés désignent-ils la même équipe ? (égalité normalisée, inclusion, ou token commun ≥4)."""
    na, nb = C._norm(C._clean(a)), C._norm(C._clean(b))
    if not (na and nb):
        return False
    if na == nb or na in nb or nb in na:
        return True
    import re as _r
    ta = {t for t in _r.split(r"[^a-z0-9]+", C._clean(a).lower()) if len(t) >= 4}
    tb = {t for t in _r.split(r"[^a-z0-9]+", C._clean(b).lower()) if len(t) >= 4}
    return bool(ta & tb)


def _repair_via_fixtures(home: str, away: str, start_iso: str) -> dict:
    """Résout les ids FotMob d'un match via les FIXTURES du jour, en s'ANCRANT sur le côté reconnu.
    Clé du problème : « Saint-Trond » (Unibet) et « St.Truiden » (FotMob) n'ont AUCUN token commun —
    aucune recherche par nom ne peut les rapprocher. Mais l'ADVERSAIRE, lui, matche (« Union
    Saint-Gilloise » ~ « Union St.Gilloise ») et le coup d'envoi concorde : on identifie donc la
    RENCONTRE, puis on lit l'id de l'équipe inconnue par sa POSITION (domicile/extérieur).
    Renvoie {nom_programme: id} pour ce qui a pu être résolu."""
    try:
        dt = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
    except Exception:
        return {}
    out = {}
    for delta in (0, -1, 1):                      # le jour FotMob peut décaler d'un cran (fuseau)
        ymd = datetime.fromtimestamp(dt.timestamp() + delta * 86400,
                                     tz=timezone.utc).strftime("%Y%m%d")
        for fh, fhid, fa, faid, utc in _fm_day(ymd):
            h_ok, a_ok = _same_team(home, fh), _same_team(away, fa)
            if not (h_ok or a_ok):
                continue
            try:                                   # garde anti-homonyme : coup d'envoi proche (≤6 h)
                if utc and abs(datetime.fromisoformat(str(utc).replace("Z", "+00:00")).timestamp()
                               - dt.timestamp()) > 6 * 3600:
                    continue
            except Exception:
                pass
            if fhid:
                out[home] = fhid
            if faid:
                out[away] = faid
            return out
    return out


def _cache_put(name: str, tid) -> bool:
    """Écrit un positif dans data/crest_cache.json (même format/verrou que `crest.team_id`)."""
    try:
        c = C._load()
        with C._LOCK:
            c[C._norm(name)] = tid
            os.makedirs(os.path.dirname(C._CACHE_FILE), exist_ok=True)
            json.dump(c, open(C._CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        C._NEG.discard(C._norm(name))
        return True
    except Exception:
        return False


def _url_ok(url: str) -> bool | None:
    """True = HTTP 200, False = code d'erreur, None = réseau injoignable (on n'accuse PAS le logo)."""
    try:
        import httpx
        r = httpx.head(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        if r.status_code == 405:                       # HEAD refusé -> on retente en GET
            r = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        return r.status_code == 200
    except Exception:
        return None


def check(check_http: bool = True, repair: bool = True) -> dict:
    """Résout + vérifie le blason des équipes du programme. Renvoie un rapport structuré."""
    prog = _load_programme()
    matches = prog.get("matches") or []
    teams: dict[str, list] = {}                        # nom -> [matchs concernés] (dédupliqué)
    pairs: list = []                                   # [(home, away, start)] -> réparation par fixtures
    for m in matches:
        label = str(m.get("name") or "")
        # Les entrées du programme ne portent PAS `home`/`away` (seulement `name` « A - B ») : on lit
        # le SIDECAR (source fiable, mêmes libellés que les cartes), repli sur le découpage du nom.
        home = away = ""
        d = None
        try:
            d = A.meta(m.get("sport") or "foot", str(m.get("id") or ""))
        except Exception:
            d = None
        if isinstance(d, dict):
            home, away = str(d.get("home") or ""), str(d.get("away") or "")
        if not (home and away) and " - " in label:
            home, away = [x.strip() for x in label.split(" - ", 1)]
        for nm in (home, away):
            nm = str(nm or "").strip()
            if nm:
                teams.setdefault(nm, []).append(label)
        pairs.append((home, away, m.get("start") or (d or {}).get("start") or ""))
    ok, unresolved, broken, unknown, repaired = [], [], [], [], []
    for nm in sorted(teams):
        tid = None
        try:
            tid = C.team_id(nm)                        # SUCCÈS -> persiste dans crest_cache.json (pré-chauffe)
        except Exception:
            tid = None
        if not tid and repair:
            # AUTO-RÉPARATION : la recherche par nom a échoué -> on passe par les FIXTURES du jour,
            # ancrées sur l'adversaire reconnu (« Saint-Trond » ↔ « St.Truiden » n'ont aucun token commun).
            for _h, _a, _st in pairs:
                if nm not in (_h, _a):
                    continue
                got = _repair_via_fixtures(_h, _a, _st)
                if got.get(nm) and _cache_put(nm, got[nm]):
                    tid = got[nm]
                    repaired.append((nm, tid))
                    break
        if not tid:
            unresolved.append((nm, teams[nm]))
            continue
        url = C.logo_url(tid)
        if not check_http:
            ok.append((nm, tid))
            continue
        st = _url_ok(url)
        if st is True:
            ok.append((nm, tid))
        elif st is False:
            broken.append((nm, tid, url))
        else:
            unknown.append((nm, tid))                  # réseau KO -> ni bon ni mauvais
    return {"day": prog.get("date") or "", "n_matches": len(matches), "n_teams": len(teams),
            "ok": ok, "unresolved": unresolved, "broken": broken, "unknown": unknown,
            "repaired": repaired}


def _dedup_new(day: str, names: list[str]) -> list[str]:
    """Ne garde que les équipes PAS encore signalées aujourd'hui (1 alerte/équipe/jour)."""
    try:
        st = json.load(open(_SENT, encoding="utf-8"))
        if not isinstance(st, dict):
            st = {}
    except Exception:
        st = {}
    done = set(st.get(day) or [])
    new = [n for n in names if n not in done]
    if new:
        st = {day: sorted(done | set(new))}            # on ne garde que le jour courant
        try:
            json.dump(st, open(_SENT, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
    return new


def run(quiet: bool = False, alert: bool = False, check_http: bool = True, repair: bool = True) -> int:
    r = check(check_http=check_http, repair=repair)
    day = r["day"] or datetime.now().strftime("%Y-%m-%d")
    bad = r["unresolved"] + [(b[0], None) for b in r["broken"]]
    n_ok, n_un, n_br = len(r["ok"]), len(r["unresolved"]), len(r["broken"])

    print(f"═══ LOGOS DES ÉQUIPES — {day} ═══")
    print(f"Programme : {r['n_matches']} match(s) · {r['n_teams']} équipe(s)")
    if not quiet:
        for nm, tid in r["ok"]:
            print(f"  ✅ {nm}  (id {tid})")
    for nm, tid in r.get("repaired") or []:
        print(f"  🔧 {nm} — RÉPARÉ via les fixtures du jour (id {tid}, mis en cache)")
    for nm, ms in r["unresolved"]:
        print(f"  ❌ {nm} — AUCUN blason (FotMob n'a pas résolu ce nom) · {', '.join(x for x in ms if x)[:60]}")
    for nm, tid, url in r["broken"]:
        print(f"  ⚠️ {nm} — id {tid} mais image INDISPONIBLE ({url})")
    if r["unknown"]:
        print(f"  … {len(r['unknown'])} équipe(s) non vérifiées (réseau injoignable) — sans conclusion")
    print("── BILAN ──")
    print(f"  {n_ok}/{r['n_teams']} logo(s) OK · {n_un} non résolu(s) · {n_br} image(s) cassée(s)")
    if n_un or n_br:
        print("  💡 Réparation : ajouter le nom dans `crest._ALIAS` (sigle -> nom FotMob complet).")

    if alert and bad:
        new = _dedup_new(day, [b[0] for b in bad])
        if new:
            lines = [f"🖼️ LOGOS MANQUANTS — {day}",
                     f"{len(new)} équipe(s) du programme sortiront une carte SANS blason :", ""]
            lines += [f"  • {n}" for n in new]
            lines += ["", "Réparation : alias dans crest._ALIAS (sigle -> nom FotMob)."]
            _send_owner("\n".join(lines))
            print(f"  (alerte privée envoyée : {len(new)} équipe(s))")
    return 1 if (n_un or n_br) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="n'affiche que les problèmes + le bilan")
    ap.add_argument("--alert", action="store_true",
                    help="alerte PRIVÉE au propriétaire si un logo manque (1×/équipe/jour)")
    ap.add_argument("--no-http", action="store_true",
                    help="ne teste pas l'URL (résolution d'id seule, aucun appel réseau sortant)")
    ap.add_argument("--no-repair", action="store_true",
                    help="ne tente PAS la résolution de secours par les fixtures FotMob (diagnostic brut)")
    args = ap.parse_args()
    sys.exit(run(quiet=args.quiet, alert=args.alert, check_http=not args.no_http,
                 repair=not args.no_repair))
