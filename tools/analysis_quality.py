#!/usr/bin/env python3
"""CONTRÔLE QUALITÉ D'ANALYSE (user 2026-08-25) — vérifie que CHAQUE match du jour est analysé « comme en
période gagnante » : couverture 100 %, profondeur par match, et taux de CONVERSION (analysé -> pari).

Priorité user : être SÛR que chaque match du programme est bien analysé (pas sauté, pas bâclé). Lecture SEULE.

3 indicateurs :
  1. COUVERTURE : chaque match du programme a-t-il une analyse ? Un match NON analysé n'est un PROBLÈME que si
     sa vague KO−1 h est PASSÉE (sinon il est juste « en attente » de sa vague, normal sous Option B).
  2. PROFONDEUR : `.md` présent + substantiel (>= MIN_MD o) + panel exécuté (`validation`). Flag les stubs.
  3. CONVERSION : paris retenus / matchs analysés. ~40-50 % = analyse profonde (période gagnante) ;
     ~10 % = signal d'ALERTE (analyse redevenue bâclée, cf. dilution 13-23/08).

Usage :  python tools/analysis_quality.py            (jour courant)
         python tools/analysis_quality.py --date 2026-08-25
Sortie : rapport lisible + code retour 1 si ALERTE (match manqué OU conversion basse), 0 sinon.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Console Windows en cp1252 : les ═ / ✅ / emojis des logs crasheraient (UnicodeEncodeError) -> exit 1
# indistinct d'une vraie ALERTE. On force la sortie en UTF-8 (idiome projet, cf. generate_analyses.py).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app import analyses as A  # noqa: E402

MIN_MD = 2500            # octets : en-dessous, l'analyse est un stub (les vraies font ~2900-5900 o)
CONV_ALERT = 0.25        # conversion < 25 % -> alerte (gagnante ~0.49 ; diluée ~0.11)
WAVE_LEAD_H = 0.75       # la vague analyse à KO-1.0 h ; on ne crie "manqué" qu'APRÈS + une marge de ~15 min
#                          (KO-0.75 h). ⚠️ Doit être < 1.0 : un seuil >= au lead de la vague (ex. l'ancien 1.6)
#                          déclarait "manqué" AVANT que la vague ne tourne -> faux positif (Atletico Grau
#                          2026-09-01 : alerté à KO-1.6h=20:24 alors que la vague était prévue à 21:00).


def _prog_path() -> str:
    return os.path.join(os.path.dirname(A.DIR), "day_programme.json")


def _load_programme() -> dict:
    try:
        p = json.load(open(_prog_path(), encoding="utf-8"))
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _send_owner(text: str) -> bool:
    """Envoie une alerte EN PRIVÉ au propriétaire (data/owner_chat.txt) — JAMAIS le canal abonnés. Best-effort."""
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


_ALERT_STATE = os.path.join(os.path.dirname(A.DIR), "quality_alerts.json")


def _new_issues(day: str, keys: list[str]) -> list[str]:
    """DÉDUP : ne renvoie que les problèmes PAS ENCORE alertés aujourd'hui (évite le spam à chaque vague)."""
    try:
        st = json.load(open(_ALERT_STATE, encoding="utf-8"))
    except Exception:
        st = {}
    done = set(st.get(day) or [])
    new = [k for k in keys if k not in done]
    if new:
        st = {day: sorted(done | set(new))}          # on ne garde que le jour courant
        try:
            json.dump(st, open(_ALERT_STATE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return new


def _sidecar_for(mid: str, home: str, away: str):
    """Sidecar (json) + chemin .md d'un match du programme : par id d'abord, repli par home/away."""
    jp = os.path.join(A.DIR, f"foot_{mid}.json")
    if os.path.exists(jp):
        try:
            return json.load(open(jp, encoding="utf-8")), jp[:-5] + ".md"
        except Exception:
            pass
    hn, an = (home or "").lower(), (away or "").lower()
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("home", "") or "").lower() == hn and (d.get("away", "") or "").lower() == an:
            return d, p[:-5] + ".md"
    return None, None


def run(date: str | None = None, send_alert: bool = False) -> int:
    prog = _load_programme()
    matches = prog.get("matches") or []
    day = date or prog.get("date") or ""
    if date:
        matches = [m for m in matches if (m.get("start") or "")[:10] == date]
    now = datetime.now(timezone.utc).timestamp()

    analysed = pending = missed = 0
    bets = 0
    shallow = []          # matchs analysés mais .md manquant/stub
    missed_list = []      # matchs dont la vague est passée SANS analyse
    print(f"═══ CONTRÔLE QUALITÉ D'ANALYSE — {day or 'jour courant'} ═══")
    print(f"Programme : {len(matches)} match(s)\n")
    for m in matches:
        mid = str(m.get("id") or "")
        home, away = m.get("home", ""), m.get("away", "")
        name = m.get("name") or f"{home} - {away}"
        d, md = _sidecar_for(mid, home, away)
        is_analysed = bool(d and (d.get("bets") or d.get("shadow") or d.get("abstained") or d.get("stat_bet")))
        ko = _ts(m.get("start"))
        wave_due = ko is not None and now >= (ko - WAVE_LEAD_H * 3600)   # la vague aurait dû tourner
        if is_analysed:
            analysed += 1
            rb = A.retained_bet("foot", str(d.get("id") or mid)) if d else None
            has_bet = bool(rb and rb.get("sel"))
            if has_bet:
                bets += 1
            # profondeur
            md_ok = bool(md and os.path.exists(md) and os.path.getsize(md) >= MIN_MD)
            panel = bool(d.get("validation") or d.get("votes")) if d else False
            flag = ""
            if not md_ok:
                shallow.append(name); flag = "  ⚠️ .md manquant/stub"
            tag = "PARI" if has_bet else ("abstention" if (d or {}).get("abstained") else "analysé")
            depth = "profond" + ("+panel" if panel else "") if md_ok else "SUPERFICIEL?"
            print(f"  ✅ {name[:34]:34} {tag:11} [{depth}]{flag}")
        elif wave_due:
            missed += 1; missed_list.append(name)
            print(f"  ❌ {name[:34]:34} NON ANALYSÉ (vague KO−1 h PASSÉE) — MANQUÉ")
        else:
            pending += 1
            lead = (ko - now) / 3600 if ko else 0
            print(f"  ⏳ {name[:34]:34} en attente de sa vague (KO dans {lead:.1f} h)")

    conv = bets / analysed if analysed else 0
    print("\n── BILAN ──")
    print(f"  Couverture : {analysed} analysé(s) · {pending} en attente de vague · {missed} MANQUÉ(s)")
    print(f"  Conversion : {bets} pari(s) / {analysed} analysé(s) = {100*conv:.0f}%  "
          f"(gagnante ~49 % · diluée ~11 %)")
    if shallow:
        print(f"  Profondeur : ⚠️ {len(shallow)} match(s) au .md manquant/stub : {', '.join(s[:20] for s in shallow)}")
    else:
        print(f"  Profondeur : ✅ tous les .md sont substantiels")

    alert = []
    if missed:
        alert.append(f"{missed} match(s) MANQUÉ(s) (vague passée sans analyse) : {', '.join(missed_list)}")
    # CONVERSION : jugée SEULEMENT en fin de journée (pending == 0). Tôt le matin, seules les abstentions du
    # slate jour sont analysées (les vagues n'ont pas tourné) -> conversion trompeuse. Les abstentions du matin
    # seront re-analysées près du KO (Option B) et peuvent devenir des paris.
    if pending == 0 and analysed >= 4 and conv < CONV_ALERT:
        alert.append(f"conversion {100*conv:.0f}% < {100*CONV_ALERT:.0f}% -> analyse peut-être superficielle")
    if shallow:
        alert.append(f"{len(shallow)} analyse(s) superficielle(s) (.md stub)")
    print()
    if alert:
        print("🔴 ALERTE :")
        for a in alert:
            print(f"   - {a}")
        # ENVOI PRIVÉ (owner) — dédupliqué : chaque problème n'alerte qu'UNE fois par jour.
        keys = [f"missed:{n}" for n in missed_list] + [f"shallow:{n}" for n in shallow]
        if pending == 0 and analysed >= 4 and conv < CONV_ALERT:
            keys.append("conversion")
        new = _new_issues(day, keys)
        if send_alert and new:
            msg = (f"⚠️ BETSFIX — contrôle qualité {day}\n\n" + "\n".join(f"• {a}" for a in alert)
                   + f"\n\nCouverture {analysed} analysé · {missed} manqué · conversion {100*conv:.0f}%.")
            if _send_owner(msg):
                print(f"   → alerte privée envoyée ({len(new)} nouveau(x) problème(s)).")
        return 1
    if pending:
        print(f"🟡 OK pour l'instant ({pending} match(s) encore à analyser par leur vague — à revérifier plus tard).")
    else:
        print("🟢 OK : couverture complète, profondeur et conversion saines.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="jour ISO (défaut : programme courant)")
    ap.add_argument("--alert", action="store_true",
                    help="envoie une alerte PRIVÉE au propriétaire (data/owner_chat.txt) si problème, "
                         "dédupliquée (1 fois/problème/jour). Sans ce flag : rapport console seul.")
    args = ap.parse_args()
    sys.exit(run(args.date, send_alert=args.alert))
