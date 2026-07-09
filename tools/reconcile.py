"""Réconciliation quotidienne BETSFIX — appelée par le scan 09h (deploy/scan_daily.ps1).

But : garantir qu'au matin **TOUT est (1) réglé et (2) posté sur Telegram**.
  1) `settle_analyses()` règle tout ce qui est réglable → poste AUSSI les résultats (idempotent) ;
  2) re-poste les pronos À VENIR qui n'ont PAS de carte Telegram (envoi manqué au scan) ;
  3) envoie un BILAN Telegram : réglés, encore en attente (matchs finis bloqués), à venir, re-postés.

Tolérant : chaque étape est best-effort et n'élève jamais (une réconciliation ratée ne casse rien).
Usage : `python tools/reconcile.py`  ·  `--dry` = inventaire seul (ni règlement ni post, pour tester).
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # racine projet -> app.*

# Console Windows (cp1252) : un print() d'emoji (🔄 🔧 ⚠…) lève UnicodeEncodeError et FAISAIT PLANTER la
# réconciliation avant son bilan (crash vu 2026-07-06, exit 1). On force stdout/stderr en UTF-8 tolérant.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app import analyses, card_data, notify, settle_analyst  # noqa: E402

_STUCK_H = 5     # match commencé il y a plus de N h et toujours pas réglé = bloqué (marge tennis long)


def _now():
    return datetime.now(timezone.utc)


def _start(d: dict):
    try:
        return datetime.fromisoformat((d.get("start") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _played(d: dict) -> bool:
    """Le match a-t-il un pari JOUÉ (simple retenu OU combiné) — càd quelque chose qui DEVAIT être posté ?"""
    if (d.get("combo") or {}).get("legs"):
        return True
    return analyses.retained_bet(d.get("sport"), d.get("id")) is not None


def _label(d: dict) -> str:
    return d.get("name") or f"{d.get('home','?')} – {d.get('away','?')}"


async def _repost(d: dict) -> bool:
    """Re-poste la carte prono d'un match à venir dont l'envoi a été manqué. False si rien à faire."""
    try:
        import card_image  # tools/card_image.py (même dossier)
        card = card_data.build_prono_card(d)
        if not card:                       # pas de value à publier -> normal, on n'envoie rien
            return False
        os.makedirs("data/_cards", exist_ok=True)
        png = f"data/_cards/reconcile_{d.get('sport')}_{d.get('id')}.png"
        await card_image.render_card(card, png)
        sent = notify.send_photo_sync(png, "")
        if sent:
            notify.remember_prono(card.get("_mid") or str(d.get("id")), sent, card.get("match"))
            return True
    except Exception as exc:               # rendu / envoi KO -> on n'insiste pas (best-effort)
        print(f"  (re-post {_label(d)} ignoré : {exc})")
    return False


def _reset_premature() -> int:
    """AUTO-RÉPARATION d'un règlement PRÉMATURÉ : un match réglé alors qu'il est encore dans sa fenêtre
    de jeu ET confirmé **LIVE** par Flashscore = réglé à tort (score live pris pour final). On le remet
    à « non réglé » -> il se re-règlera CORRECTEMENT une fois fini. On GARDE `result_msg` : la carte
    résultat erronée déjà postée sera alors SUPPRIMÉE automatiquement (cf. settle_analyst). Précis : on
    ne reset QUE si le statut Flashscore == LIVE (jamais un vrai match fini vite). Renvoie le nb réparé."""
    from app import flashscore
    from app.analyses import _DUR_MIN
    n = 0
    for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not analyses.is_settled(d):
            continue
        st = _start(d)
        if not st or (_now() - st).total_seconds() / 60 >= _DUR_MIN.get(d.get("sport"), 150):
            continue                       # hors fenêtre de jeu -> forcément fini, on ne touche pas
        try:
            live = flashscore.match_status(d.get("sport"), d.get("home", ""),
                                           d.get("away", ""), d.get("start")) == "2"
        except Exception:
            live = False
        if not live:
            continue                       # statut ≠ LIVE (fini / introuvable) -> on n'annule pas
        for k in ("result", "stat_bet", "clv", "settle_v", "notified_pick", "notified_combo",
                  "pick_tries", "combo_tries", "notify_tries", "pick_giveup"):
            d.pop(k, None)                 # on GARDE result_msg -> suppression auto de l'ancienne carte
        for b in (d.get("bets") or []):
            b.pop("result", None)
        if d.get("combo"):
            d["combo"]["result"] = None
            for leg in d["combo"].get("legs") or []:
                leg["result"] = None
        tmp = p + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, p)
        n += 1
        print(f"  ⚠ règlement prématuré ANNULÉ (match encore LIVE) : {_label(d)}")
    return n


async def reconcile(dry: bool = False, no_bilan: bool = False) -> dict:
    # 0) AUTO-RÉPARATION : annule les règlements prématurés (match encore live) -> re-réglés à l'étape 1.
    n_reset = 0 if dry else _reset_premature()

    # 1) RÈGLEMENT : règle tout ce qui peut l'être (poste les résultats, idempotent via notified_*).
    n_settled = 0
    if not dry:
        try:
            n_settled = await settle_analyst.settle_analyses()
        except Exception as exc:
            print(f"  (règlement ignoré : {exc})")
        # SUIVI SÉPARÉ des provisoires (info seule, hors ROI réel) : règle les provisoires terminés.
        try:
            from app import provisional as _pvt
            _npv = await asyncio.to_thread(_pvt.settle_pending)
            if _npv:
                print(f"  · {_npv} pari(s) provisoire(s) réglé(s) (suivi info-seule).")
        except Exception as exc:
            print(f"  (suivi provisoires ignoré : {exc})")

    # 2) INVENTAIRE : parcourt les fiches, classe chaque match JOUÉ.
    stuck, upcoming, unposted = [], [], []
    for p in glob.glob(os.path.join(analyses.DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _played(d):
            continue                       # abstention / fantôme seul -> rien à poster ni régler
        st = _start(d)
        settled = analyses.is_settled(d)
        if not settled:
            if st and _now() - st > timedelta(hours=_STUCK_H):
                stuck.append(d)            # match fini depuis longtemps mais toujours pas réglé
            else:
                upcoming.append(d)         # attend légitimement son résultat
        # posté ? un match À VENIR IMMINENT (< 3 h) analysé mais SANS carte Telegram = envoi manqué -> à
        # re-poster. Borne 3 h alignée sur le nouveau système « pari publié ~2 h avant le match » (dispatcher) :
        # on ne rattrape QUE les envois ratés imminents, JAMAIS republier le backlog (bug vécu : la borne
        # 36 h héritée du « tout publier le matin » republiait tous les matchs à venir d'un coup après reset).
        if st and _now() < st < _now() + timedelta(hours=3) and not notify.get_prono(str(d.get("id"))):
            unposted.append(d)

    # 3) RE-POST des pronos à venir manqués (sauf --dry).
    reposted = 0
    if not dry:
        for d in unposted:
            if await _repost(d):
                reposted += 1

    # 4) BILAN Telegram.
    tout_ok = not stuck and not unposted
    lines = ["🔄 <b>Réconciliation BETSFIX — 09h</b>"]
    if n_reset:
        lines.append(f"🔧 Règlements prématurés corrigés : <b>{n_reset}</b>")
    lines.append(f"✅ Réglés à l'instant : <b>{n_settled}</b>")
    lines.append(f"📅 En attente de résultat (matchs à venir/en cours) : <b>{len(upcoming)}</b>")
    if stuck:
        lines.append(f"⏳ <b>BLOQUÉS</b> (finis depuis &gt;{_STUCK_H} h, non réglés) : <b>{len(stuck)}</b>")
        for d in stuck[:8]:
            lines.append(f"   • {_label(d)} ({d.get('sport')})")
        if len(stuck) > 8:
            lines.append(f"   … +{len(stuck) - 8}")
    lines.append(f"📤 Pronos re-postés (envoi manqué) : <b>{reposted}</b>")
    if tout_ok:
        lines.append("🟢 Tout est réglé et posté.")
    msg = "\n".join(lines)

    print(msg.replace("<b>", "").replace("</b>", "").replace("&gt;", ">"))
    # `--no-bilan` (vagues rapprochées, ~toutes les 30 min) : on RÈGLE et poste les résultats, mais on
    # NE POSTE PAS le bilan récap à chaque passage (sinon spam du canal). Le bilan reste au run du matin.
    if not dry and not no_bilan:
        try:
            notify.send_sync(msg)
        except Exception as exc:
            print(f"  (bilan Telegram ignoré : {exc})")
    return {"reset": n_reset, "settled": n_settled, "upcoming": len(upcoming),
            "stuck": len(stuck), "reposted": reposted}


if __name__ == "__main__":
    asyncio.run(reconcile(dry="--dry" in sys.argv, no_bilan="--no-bilan" in sys.argv))
