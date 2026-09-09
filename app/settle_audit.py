"""Filet d'audit du RÈGLEMENT via API-Football — garde-fou anti-faux-score (« ça ne doit jamais arriver »).

Après le règlement, compare le score de chaque match FOOT réglé récemment au score AUTORITATIF d'API-Football
(`/fixtures?date` renvoie déjà le score → 1 appel/jour, très économe). API-Football a attrapé le cas réel
FC Bruges-Aston Villa 5-3 (FotMob corrompu) vs 2-3.

Règles de SÛRETÉ :
- **Abstention** (aucun pari joué, aucune jambe de combiné) : le score n'est qu'un AFFICHAGE (0 ROI) → correction
  automatique vers le score API-Football.
- **Pari joué (`stat_bet`) ou jambe de combiné** : ROI/stats EN JEU → JAMAIS auto-corrigé. ALERTE PRIVÉE owner
  (`notify.send_owner_sync`) pour vérification/correction manuelle prudente.
Compare au score FINAL (`goals`, INCLUT la prolongation) — pas au 90 min — sinon faux positifs sur les matchs
AET/PEN (BETSFIX affiche « 1-2 (a.p.) », suffixe retiré à la comparaison). Matching nom + KO ±90 min, seuil 0.6
(évite les homonymes type CSKA Sofia vs CSKA 1948). Best-effort : ne lève JAMAIS, n'impacte NI stats NI sélection.

Dédup : marque `af_audited` sur le sidecar (avec `af_audit_tries` pour borner les non-résolus).
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone

from app import analyses as A
from app import apifootball as AF

_MAX_TRIES = 3


def _combo_leg_mids() -> dict:
    from app import combo_daily as CD
    out = {}
    for variant in ("", "soir"):
        try:
            d = CD._load("foot", variant)
        except Exception:
            continue
        for day, cb in (d or {}).items():
            for lg in (cb.get("legs") or []):
                mid = str(lg.get("mid") or "")
                if mid:
                    out[mid] = (variant or "jour", day, lg.get("sel"), lg.get("result"))
    return out


def _save(p: str, d: dict) -> None:
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, p)


def audit_recent(days: int = 2, fix_abstentions: bool = True, alert: bool = True) -> dict:
    """Audite les matchs foot réglés des `days` derniers jours pas encore audités. Renvoie un résumé."""
    if not AF.configured():
        return {"skipped": "no_key"}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    leg_mids = _combo_leg_mids()

    # sidecars réglés récents, PAS encore audités
    todo = {}   # day -> [(path, d)]
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        day = (d.get("start") or "")[:10]
        if not day or day < cutoff:
            continue
        if not (A.is_settled(d) and (d.get("result") or {}).get("score")):
            continue
        if d.get("af_audited") or int(d.get("af_audit_tries") or 0) >= _MAX_TRIES:
            continue
        todo.setdefault(day, []).append((p, d))

    if not todo:
        return {"audited": 0, "diff": 0, "fixed": 0, "alerts": 0}

    n = fixed = alerts = 0
    alert_lines = []
    try:
        cl = AF._client()
    except Exception:
        return {"skipped": "no_client"}
    try:
        for day, rows in todo.items():
            try:
                fx = AF._get(cl, "/fixtures", date=day).get("response", [])
            except Exception:
                continue
            afx = [{"nh": AF._norm(x["teams"]["home"]["name"]), "na": AF._norm(x["teams"]["away"]["name"]),
                    "ts": AF._ts(x["fixture"]["date"]), "final": x.get("goals") or {},
                    "status": x["fixture"]["status"]["short"]} for x in fx]
            for p, d in rows:
                n += 1
                bts = AF._ts(d.get("start")); nh, na = AF._norm(d.get("home")), AF._norm(d.get("away"))
                best, bs = None, 0.0
                for a in afx:
                    if bts and a["ts"] and abs(bts - a["ts"]) > 5400:
                        continue
                    s = (AF._ov(nh, a["nh"]) + AF._ov(na, a["na"])) / 2
                    if s > bs:
                        bs, best = s, a
                if not best or bs < 0.6 or best["final"].get("home") is None:
                    d["af_audit_tries"] = int(d.get("af_audit_tries") or 0) + 1   # non résolu -> retry borné
                    _save(p, d)
                    continue
                af = f"{best['final']['home']}-{best['final']['away']}"
                bf = str(d["result"]["score"]).split(" (")[0].strip()
                mid = str(d.get("id"))
                if af == bf:
                    d["af_audited"] = True
                    _save(p, d)
                    continue
                # ÉCART
                played = bool(d.get("stat_bet"))
                in_combo = mid in leg_mids
                if played or in_combo:
                    enjeu = "PARI JOUÉ" if played else f"JAMBE COMBINÉ ({leg_mids[mid][2]})"
                    alert_lines.append(f"❌ {d.get('name')} : BETSFIX {d['result']['score']} vs API-Football {af}"
                                       f"  [{enjeu}] — ROI en jeu, VÉRIFIER (id {mid})")
                    alerts += 1
                    d["af_audit_flag"] = {"betsfix": d["result"]["score"], "apifootball": af, "enjeu": enjeu}
                    d["af_audited"] = True     # marqué (alerté) — ne pas re-spammer
                    _save(p, d)
                elif fix_abstentions:
                    h, a2 = best["final"]["home"], best["final"]["away"]
                    old = d["result"]["score"]
                    d["result"]["score"] = af
                    d["result"]["raw"] = {**(d["result"].get("raw") or {}), "home": h, "away": a2,
                                          "label": af, "src": "apifootball(audit)"}
                    d["af_audited"] = True
                    _save(p, d)
                    fixed += 1
                    print(f"  [settle_audit] abstention corrigée : {d.get('name')} {old} -> {af}")
    finally:
        try:
            cl.close()
        except Exception:
            pass

    if alert and alert_lines:
        try:
            from app import notify
            notify.send_owner_sync("⚠️ BETSFIX — audit règlement API-Football (privé)\n\n"
                                   "Écart de score sur un pari joué / une jambe de combiné (ROI en jeu, "
                                   "NON auto-corrigé) :\n\n" + "\n".join(alert_lines))
        except Exception:
            pass

    return {"audited": n, "diff": alerts + fixed, "fixed": fixed, "alerts": alerts}
