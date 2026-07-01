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


async def reconcile(dry: bool = False) -> dict:
    # 1) RÈGLEMENT : règle tout ce qui peut l'être (poste les résultats, idempotent via notified_*).
    n_settled = 0
    if not dry:
        try:
            n_settled = await settle_analyst.settle_analyses()
        except Exception as exc:
            print(f"  (règlement ignoré : {exc})")

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
        # posté ? un match À VENIR IMMINENT (< 36 h) sans carte Telegram = envoi manqué -> à re-poster.
        # Borne 36 h : on ne re-poste pas des matchs lointains (et on ne re-spamme pas tout l'historique).
        if st and _now() < st < _now() + timedelta(hours=36) and not notify.get_prono(str(d.get("id"))):
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
    if not dry:
        try:
            notify.send_sync(msg)
        except Exception as exc:
            print(f"  (bilan Telegram ignoré : {exc})")
    return {"settled": n_settled, "upcoming": len(upcoming),
            "stuck": len(stuck), "reposted": reposted}


if __name__ == "__main__":
    asyncio.run(reconcile(dry="--dry" in sys.argv))
