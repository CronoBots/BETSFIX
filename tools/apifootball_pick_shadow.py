"""SHADOW DES PICKS — « le pari mécanique changerait-il avec les cotes API-Football ? » (Étape 2 migration).

Pour chaque match analysé, on RE-JOUE les VRAIS sélecteurs (confidence_pick / value_pick) sur une COPIE du
sidecar dont l'omap est remplacé par celui d'API-Football (fantômes re-pricés). On compare le pick obtenu au
pick RÉEL (cotes scrapées). Si identique → migrer les cotes ne change pas la sélection. 100 % lecture seule :
ne touche NI le sidecar réel NI le pari joué. Écrit un rapport data/apifootball_shadow/picks/<date>/.

C'est LA mesure qui autorise (ou non) la bascule des cotes : on veut « picks identiques » sur plusieurs jours.

Usage : python tools/apifootball_pick_shadow.py [--date 2026-09-08] [--limit 20]
"""
from __future__ import annotations

import argparse
import copy
import glob
import io
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import analyses as A                 # noqa: E402
from app import apifootball as AF             # noqa: E402
from app import confidence_pick as CP         # noqa: E402
from app import value_pick as VP              # noqa: E402


def _reprice(d: dict, af_omap: dict) -> dict:
    """Copie du sidecar avec l'omap = API-Football + fantômes/bets re-pricés depuis af_omap."""
    d2 = copy.deepcopy(d)
    d2["omap"] = dict(af_omap)
    for p in (d2.get("shadow") or []):
        code = CP.code_from_pick(p.get("sel") or "", "foot", d2.get("home", ""), d2.get("away", "")).strip()
        if code in af_omap:
            p["cote"] = af_omap[code]
    for b in (d2.get("bets") or []):
        code = CP.code_from_pick(b.get("sel") or "", "foot", d2.get("home", ""), d2.get("away", "")).strip()
        if code in af_omap:
            b["odds"] = af_omap[code]
            b["cote"] = af_omap[code]
    return d2


def _pick_str(p) -> str:
    return f"{p.get('code')} @{p.get('cote')}" if p else "—(abstention)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    if not AF.configured():
        print("✗ clé API-Football absente."); return 2
    day = args.date

    bfx = []
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("start") or "")[:10] != day:
            continue
        if d.get("shadow") or d.get("bets"):
            bfx.append(d)
    bfx.sort(key=lambda d: d.get("start") or "")
    bfx = bfx[:args.limit]
    print(f"═══ SHADOW PICKS (cotes API-Football) — {day} · {len(bfx)} match(s) ═══")
    if not bfx:
        print("  (rien à comparer)"); return 0

    out_dir = os.path.join(os.path.dirname(A.DIR), "apifootball_shadow", "picks", day)
    os.makedirs(out_dir, exist_ok=True)

    same = diff = miss = 0
    diffs = []
    with AF._client() as cl:
        for d in bfx:
            name = d.get("name")
            f = AF.resolve_fixture(cl, d.get("home"), d.get("away"), d.get("start"))
            if not f:
                miss += 1
                print(f"  ? {str(name)[:34]:34} — pas de fixture API-Football")
                continue
            af_omap = AF.unibet_omap(AF.raw_odds(cl, f["id"]))
            if not af_omap:
                miss += 1
                print(f"  ? {str(name)[:34]:34} — pas de cotes API-Football")
                continue
            # picks RÉELS (cotes scrapées) vs SHADOW (cotes API-Football)
            real_c, real_v = CP.pick_for_sidecar(d), VP.pick_for_sidecar(d)
            d2 = _reprice(d, af_omap)
            sh_c, sh_v = CP.pick_for_sidecar(d2), VP.pick_for_sidecar(d2)

            def _cmp(r, s):
                if (r is None) and (s is None):
                    return "SAME"          # abstention des deux côtés
                if r and s and r.get("code") == s.get("code"):
                    return "SAME"
                return "DIFF"

            cc, cv = _cmp(real_c, sh_c), _cmp(real_v, sh_v)
            ok = (cc == "SAME" and cv == "SAME")
            same += ok
            if not ok:
                diff += 1
                diffs.append((name, real_c, sh_c, real_v, sh_v, cc, cv))
            json.dump({"mid": d.get("id"), "name": name, "af_fixture": f["id"], "af_omap": af_omap,
                       "real_confiance": _pick_str(real_c), "shadow_confiance": _pick_str(sh_c),
                       "real_value": _pick_str(real_v), "shadow_value": _pick_str(sh_v),
                       "confiance": cc, "value": cv},
                      open(os.path.join(out_dir, f"foot_{d.get('id')}.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            tag = "✅" if ok else "⚠"
            print(f"  {tag} {str(name)[:30]:30} Conf[{cc}] {_pick_str(real_c)} → {_pick_str(sh_c)}"
                  f"  ·  Val[{cv}] {_pick_str(real_v)} → {_pick_str(sh_v)}")

    tot = same + diff
    print(f"\n── Picks IDENTIQUES : {same}/{tot}  ·  différents : {diff}  ·  non résolus : {miss} ──")
    if diffs:
        print("\n=== PICKS QUI CHANGERAIENT (à comprendre avant bascule) ===")
        for name, rc, sc, rv, sv, cc, cv in diffs:
            if cc == "DIFF":
                print(f"  Confiance · {str(name)[:34]:34} {_pick_str(rc)}  →  {_pick_str(sc)}")
            if cv == "DIFF":
                print(f"  Value     · {str(name)[:34]:34} {_pick_str(rv)}  →  {_pick_str(sv)}")
    else:
        print("  ✅ Aucun changement — la bascule des cotes ne modifierait AUCUN pick sur ce slate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
