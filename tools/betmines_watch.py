"""Suivi « info seule » du DOUBLE quotidien de Betmines — demande user 2026-07-23.

But : MESURER leur taux de réussite réel (« leur taux de réussite est pas mal et j'aimerais m'y
intéresser ») avant d'envisager de s'en inspirer. On ne copie RIEN dans nos pronos.

v2 (2026-07-23) : passe du scrape HTML à leur **API publique** (découverte dans le bundle Nuxt) :
  GET https://api.betmines.com/betmines/v1/bets?isDailyBet=true&isRiskyBet=false&from=<day>T10:00:00Z
→ renvoie LE Double le plus proche après `from` : équipes, ligue, marché (betResult O15/O25/U25/GG…),
cote par jambe (betResultQuote), statut par jambe (betResultStatus 1=gagné/2=perdu), score final
(ftScore), cote totale (quote), verdict (winning). + stats d'équipe (position, moyennes 5 derniers) →
servent aux ANALYSES DE JAMBES (« pourquoi cette jambe », comme le combiné du jour, via `claude -p`).

TOTALEMENT ISOLÉ : écrit UNIQUEMENT `data/betmines_track.json` — jamais sidecars/ROI/stats/calibration.
RÈGLEMENT : notre calcul depuis `ftScore` (over/under) PRIORITAIRE ; leur `betResultStatus` en repli
(marchés non codés), tracé `settle_src`.

Usage : python tools/betmines_watch.py [--force] [--backfill N]
Appelé par scan_daily (1×/jour) + reconcile (throttlé 6 h en interne).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK = os.path.join(_ROOT, "data", "betmines_track.json")
_API = ("https://api.betmines.com/betmines/v1/bets"
        "?isDailyBet=true&isRiskyBet=false&from={day}T10:00:00Z")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# betResult -> (libellé lisible, ligne signée : >0 over / <0 under / None = non codé)
_MARKETS = {"O05": ("Plus de 0.5 buts", 0.5), "O15": ("Plus de 1.5 buts", 1.5),
            "O25": ("Plus de 2.5 buts", 2.5), "O35": ("Plus de 3.5 buts", 3.5),
            "U15": ("Moins de 1.5 buts", -1.5), "U25": ("Moins de 2.5 buts", -2.5),
            "U35": ("Moins de 3.5 buts", -3.5), "U45": ("Moins de 4.5 buts", -4.5),
            "GG": ("Les deux équipes marquent", None), "NG": ("Une équipe ne marque pas", None),
            "1": ("Victoire domicile", None), "2": ("Victoire extérieur", None),
            "X": ("Match nul", None), "1X": ("Double chance 1X", None),
            "X2": ("Double chance X2", None)}


def _load() -> dict:
    try:
        with open(TRACK, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    tmp = TRACK + ".tmp"
    os.makedirs(os.path.dirname(TRACK), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TRACK)


def _api_double(day: str) -> dict | None:
    """Le Double dont le PREMIER match est le jour `day` (YYYY-MM-DD). None si aucun/erreur.
    L'API renvoie le Double le plus proche APRÈS from -> on vérifie que dateFirstMatch tombe bien ce jour."""
    req = urllib.request.Request(_API.format(day=day), headers={"User-Agent": _UA,
                                                                "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    if not isinstance(data, list) or not data:
        return None
    bet = data[0]
    if str(bet.get("dateFirstMatch", ""))[:10] != day:
        return None                                    # le Double renvoyé est celui d'un autre jour
    legs = []
    for fx in bet.get("fixtures") or []:
        f = fx.get("fixture") or {}
        code = str(fx.get("betResult") or "")
        label, line = _MARKETS.get(code, (code or "Pari", None))
        lt, vt = f.get("localTeam") or {}, f.get("visitorTeam") or {}
        legs.append({
            "home": lt.get("name") or "?", "away": vt.get("name") or "?",
            "comp": " - ".join(x for x in (((f.get("league") or {}).get("country") or {}).get("name"),
                                           (f.get("league") or {}).get("name")) if x),
            "market": label, "code": code, "line": line,
            "cote": fx.get("betResultQuote"),
            "score": f.get("ftScore") or None,
            "their_status": fx.get("betResultStatus"),  # 1=gagné 2=perdu (0/None = en attente)
            "start": f.get("dateTime"),
            "fixture_id": f.get("id"),                  # -> /fixtures/{id} pour les stats détaillées
            "stats": {"pos_h": f.get("localTeamPosition"), "pos_a": f.get("visitorTeamPosition")},
            "result": None})
    if not legs:
        return None
    return {"date": day, "bet_id": bet.get("id"), "legs": legs,
            "total_odds": bet.get("quote"), "their_winning": bet.get("winning"),
            "result": None, "captured": datetime.now(timezone.utc).isoformat(timespec="seconds")}


_SEUIL = {0.5: "05", 1.5: "15", 2.5: "25", 3.5: "35", 4.5: "45"}


def _enrich_leg(leg: dict) -> None:
    """Appelle /fixtures/{id} (les % sont à 0 dans /bets) pour remplir : `stats` détaillées (% over/under
    domicile/ext, clean-sheet, GG, moyennes buts, H2H) ET `prob` = CONFIANCE dérivée pour le marché de la
    jambe (base de la ligne verdict Confiance/Marché/Cote). Best-effort. Appelé pour le Double du JOUR seul."""
    fid = leg.get("fixture_id")
    if not fid or leg.get("prob") is not None:          # déjà enrichi / pas d'id
        return
    try:
        req = urllib.request.Request(
            f"https://api.betmines.com/betmines/v1/fixtures/{fid}",
            headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            f = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return
    lt, vt = f.get("localTeam") or {}, f.get("visitorTeam") or {}

    def _num(t, k):
        v = t.get(k)
        return v if isinstance(v, (int, float)) else None

    s = leg.setdefault("stats", {})
    seuil = _SEUIL.get(abs(leg.get("line") or 0))
    if seuil:                                           # over/under buts
        h_ctx = _num(lt, f"totalHomeOver{seuil}Percentage")
        a_ctx = _num(vt, f"totalAwayOver{seuil}Percentage")
        h_l5 = _num(lt, f"totalLast5Over{seuil}Percentage")
        a_l5 = _num(vt, f"totalLast5Over{seuil}Percentage")
        vals = [x for x in (h_ctx, a_ctx, h_l5, a_l5) if x is not None]
        over_pct = round(sum(vals) / len(vals)) if vals else None
        if over_pct is not None:
            leg["prob"] = over_pct if (leg.get("line") or 0) > 0 else max(1, 100 - over_pct)
        s.update({"over_ctx_h": h_ctx, "over_ctx_a": a_ctx, "over_l5_h": h_l5, "over_l5_a": a_l5})
    else:                                               # marchés non-buts (1X2/GG…) : proba implicite de leur cote
        co = leg.get("cote")
        if isinstance(co, (int, float)) and co > 1:
            leg["prob"] = round(100.0 / co)
    s.update({
        "cs_h": _num(lt, "cleanSheetPercentage"), "cs_a": _num(vt, "cleanSheetPercentage"),
        "gg_h": _num(lt, "totalGGPercentage"), "gg_a": _num(vt, "totalGGPercentage"),
        "avg_h": _num(lt, "totalGolsMeanLatestMatches"), "avg_a": _num(vt, "totalGolsMeanLatestMatches"),
        "avg_conc_h": _num(lt, "totalConcededGolsMeanLatestMatches"),
        "avg_conc_a": _num(vt, "totalConcededGolsMeanLatestMatches"),
        "h2h_h": f.get("totalGolsMeanLocalTeamH2H"), "h2h_a": f.get("totalGolsMeanVisitorTeamH2H"),
    })


def _settle_leg(leg: dict) -> None:
    """NOTRE règlement depuis le score final (over/under buts codés) ; repli = leur betResultStatus
    (marchés non codés : GG/1X2…), tracé `settle_src`."""
    if leg.get("result") in ("won", "lost", "push"):
        return
    sc = leg.get("score")
    ln = leg.get("line")
    if sc and isinstance(ln, (int, float)):
        try:
            h, a = str(sc).split("-")
            total = int(h) + int(a)
            leg["result"] = ("won" if total > ln else "lost") if ln > 0 else \
                            ("won" if total < abs(ln) else "lost")
            leg["settle_src"] = "score"
            return
        except (ValueError, AttributeError):
            pass
    st = leg.get("their_status")
    if st in (1, 2):
        leg["result"] = "won" if st == 1 else "lost"
        leg["settle_src"] = "betmines"


def _resolve_claude() -> str | None:
    """claude.exe SANS le cwd du repo (piège connu : le claude.bat lanceur du projet masque le vrai)."""
    old = os.getcwd()
    try:
        os.chdir(os.path.expanduser("~"))
        return shutil.which("claude") or shutil.which("claude.CMD") or shutil.which("claude.cmd")
    finally:
        os.chdir(old)


def _analyze_legs(cb: dict) -> bool:
    """Analyses de JAMBES façon combiné du jour (demande user 2026-07-23 : « avec en plus ses propres
    analyses de jambes ») : UN appel `claude -p` produit LEGn: <justification> par jambe, stocké
    `leg["why"]` -> pli « Pourquoi cette jambe » à l'affichage. Best-effort (jamais bloquant) ; appelé
    UNIQUEMENT pour le Double du JOUR (pas le backfill). True si au moins un `why` écrit."""
    legs = [l for l in cb.get("legs") or [] if not l.get("why")]
    if not legs:
        return False
    exe = _resolve_claude()
    if not exe:
        return False
    def _pc(v):                                         # pourcentage lisible ou '?'
        return f"{round(v)} %" if isinstance(v, (int, float)) else "?"

    def _mn(v):
        return f"{v:.1f}" if isinstance(v, (int, float)) else "?"

    blocs = []
    for i, l in enumerate(cb["legs"], 1):
        s = l.get("stats") or {}
        ligne = (f"[{i}] {l.get('comp')} — {l.get('home')} vs {l.get('away')} — pari : {l.get('market')} "
                 f"@{l.get('cote')}\n    Classement : {l.get('home')} {s.get('pos_h') or '?'}e, "
                 f"{l.get('away')} {s.get('pos_a') or '?'}e.")
        # % over/under contextuels (domicile/extérieur) + forme 5 derniers, si le marché est un total buts
        if s.get("over_ctx_h") is not None or s.get("over_ctx_a") is not None:
            seuil = _SEUIL.get(abs(l.get("line") or 0), "?")
            ligne += (f"\n    Matchs à +{seuil.lstrip('0') if isinstance(seuil,str) else seuil} buts — "
                      f"{l.get('home')} à domicile {_pc(s.get('over_ctx_h'))} (5 derniers "
                      f"{_pc(s.get('over_l5_h'))}) ; {l.get('away')} à l'extérieur "
                      f"{_pc(s.get('over_ctx_a'))} (5 derniers {_pc(s.get('over_l5_a'))}).")
        ligne += (f"\n    Moyennes buts (5 derniers) : {l.get('home')} {_mn(s.get('avg_h'))} marqués / "
                  f"{_mn(s.get('avg_conc_h'))} encaissés ; {l.get('away')} {_mn(s.get('avg_a'))} marqués / "
                  f"{_mn(s.get('avg_conc_a'))} encaissés."
                  f"\n    Clean-sheet : {l.get('home')} {_pc(s.get('cs_h'))}, {l.get('away')} "
                  f"{_pc(s.get('cs_a'))}. Les deux marquent (GG) : {l.get('home')} {_pc(s.get('gg_h'))}, "
                  f"{l.get('away')} {_pc(s.get('gg_a'))}."
                  f"\n    H2H moyenne buts : {l.get('home')} {_mn(s.get('h2h_h'))}, {l.get('away')} "
                  f"{_mn(s.get('h2h_a'))}.")
        blocs.append(ligne)
    prompt = (
        "Tu es un analyste PRO du pari sportif. Justifie chaque jambe du combiné ci-dessous en 2 phrases "
        "COMPLÈTES et FACTUELLES (français impeccable) : appuie-toi sur les chiffres fournis (classement, "
        "% de matchs au-dessus du seuil, clean-sheet, GG, moyennes de buts, H2H) et sur ce que tu sais de "
        "ces équipes/ligues ; termine par une courte réserve honnête (« bémol : … »). "
        "N'invente AUCUN chiffre. Pas de méta (ni value, ni proba). "
        "Réponds AU FORMAT EXACT, une ligne par jambe, RIEN d'autre :\n"
        "LEG1: <justification>\nLEG2: <justification>\n(… une ligne LEGn par jambe)\n\nJambes :\n"
        + "\n".join(blocs))
    try:
        # prompt via STDIN (comme generate_analyses.run_claude) : en ARGUMENT, le wrapper claude.CMD
        # Windows tronque au premier retour à la ligne (bug reproduit : prompt coupé).
        out = subprocess.run([exe, "-p"], input=prompt, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=180).stdout or ""
    except Exception:
        return False
    wrote = False
    for i, l in enumerate(cb["legs"], 1):
        mm = re.search(rf"^\s*LEG\s*{i}\s*:\s*(.+)", out, re.M)
        if mm and not l.get("why"):
            l["why"] = mm.group(1).strip()
            wrote = True
    return wrote


def run(force: bool = False, backfill: int = 0) -> None:
    d = _load()
    # THROTTLE 6 h (hors --force/backfill) : appelé aussi par la boucle reconcile (10 min).
    _meta = d.get("_meta") or {}
    if not force and not backfill:
        try:
            last = datetime.fromisoformat(_meta.get("last_run", "2000-01-01T00:00:00+00:00"))
            if (datetime.now(timezone.utc) - last).total_seconds() < 6 * 3600:
                return
        except ValueError:
            pass
    _meta["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    d["_meta"] = _meta
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    days = [today] if not backfill else [
        (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(backfill, -1, -1)]
    # 1) CAPTURE (API) — une entrée par jour ; jamais réécrite une fois RÉGLÉE (les `why` sont préservés).
    for day in days:
        if isinstance(d.get(day), dict) and d[day].get("result") in ("won", "lost"):
            continue
        try:
            cb = _api_double(day)
        except Exception as exc:
            print(f"betmines: API KO pour {day} ({exc})")
            continue
        if not cb:
            continue
        prev = d.get(day) or {}
        for old in (prev.get("legs") or []):           # préserve why/result d'une capture antérieure
            for leg in cb["legs"]:
                if (leg["home"], leg["away"]) == (old.get("home"), old.get("away")):
                    for k in ("why", "result", "settle_src"):
                        if old.get(k) and not leg.get(k):
                            leg[k] = old[k]
        d[day] = cb
        print(f"betmines: Double {day} capturé ({len(cb['legs'])} jambes @ {cb.get('total_odds')})")
    # 2) RÈGLEMENT + verdict (toutes gagnées = won ; ≥1 perdue = lost).
    for day, cb in d.items():
        if day.startswith("_") or not isinstance(cb, dict) or cb.get("result") in ("won", "lost"):
            continue
        for leg in cb.get("legs") or []:
            _settle_leg(leg)
        res = [leg.get("result") for leg in cb.get("legs") or []]
        if res and all(r == "won" for r in res):
            cb["result"] = "won"
        elif any(r == "lost" for r in res):
            cb["result"] = "lost"
    # 3) ENRICHISSEMENT + ANALYSES de jambes du Double du JOUR (comme le combiné du jour) — pas le backfill.
    cbt = d.get(today)
    if isinstance(cbt, dict) and cbt.get("legs"):
        # `fixture_id` manque sur les Doubles capturés avant l'enrichissement (ou déjà réglés donc non
        # re-capturés) : on le rapatrie par un re-fetch léger, SANS toucher result/why/settle_src.
        if any(not l.get("fixture_id") for l in cbt["legs"]):
            try:
                fresh = _api_double(today)
                for fl in (fresh or {}).get("legs") or []:
                    for leg in cbt["legs"]:
                        if (leg.get("home"), leg.get("away")) == (fl.get("home"), fl.get("away")):
                            leg["fixture_id"] = fl.get("fixture_id")
            except Exception:
                pass
        for leg in cbt["legs"]:                         # % détaillés (0 dans /bets) + `prob` (confiance)
            _enrich_leg(leg)
        if _analyze_legs(cbt):
            print("betmines: analyses de jambes écrites (pli « pourquoi »)")
    _save(d)
    # 4) BILAN mesuré.
    done = [c for k, c in d.items() if not k.startswith("_")
            and isinstance(c, dict) and c.get("result") in ("won", "lost")]
    if done:
        w = sum(1 for c in done if c["result"] == "won")
        pnl = sum((c.get("total_odds") or 0) - 1 if c["result"] == "won" else -1 for c in done)
        print(f"betmines: bilan {w}/{len(done)} Doubles gagnés · P&L simulé {pnl:+.2f} (mise plate)")


if __name__ == "__main__":
    _bf = 0
    if "--backfill" in sys.argv:
        try:
            _bf = int(sys.argv[sys.argv.index("--backfill") + 1])
        except (IndexError, ValueError):
            _bf = 30
    run(force="--force" in sys.argv, backfill=_bf)
