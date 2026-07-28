"""COMBINÉ SÉCURITÉ FOOT (info seule) — demande user 2026-07-28.

Chaque jour, UN combiné composé UNIQUEMENT de matchs de FOOT, en prenant pour chaque match la DOUBLE
CHANCE LA PLUS SÛRE (proba calibrée la plus haute), assemblées jusqu'à atteindre une cote d'ENVIRON 2.

⚠️ TOTALEMENT ISOLÉ du ROI/stats/calibration réels — EXACTEMENT comme le « combiné bonus » (Betmines) et
le combiné du jour simulé : ce module écrit UNIQUEMENT dans `data/combo_safe_track.json`, ne touche JAMAIS
aux sidecars, à `stat_bet`, à la calibration, à `list_for`, ni au ROI. Suivi « info seule », mise à plat
1 unité. On RÉUTILISE les briques éprouvées de `app/combo_daily.py` (sélection des candidats, optimiseur
`pick_combo`, résolution `_derive_combo`) sans rien y modifier -> zéro risque pour le combiné compté au ROI.
"""
from __future__ import annotations

import json
import os

from app import combo_daily as _cd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK_PATH = os.path.join(_ROOT, "data", "combo_safe_track.json")

TARGET_ODDS = 2.0        # cote CIBLE « environ 2 » : on assemble les DC les plus sûres jusqu'à l'atteindre
MAX_LEGS = 7             # borne haute (des DC très sûres à ~1.1 peuvent demander pas mal de jambes)
MIN_LEGS = 2            # un « combiné » = au moins 2 jambes
MIN_LEG_PROB = 0.55      # jambe DC fiable (les DC les plus sûres sont bien au-dessus ; garde-fou bas)
MIN_LEG_ODDS = 1.04      # une DC quasi-certaine à ~1.02 n'aide pas vers la cible


def day_key(now=None) -> str:
    """Clé-jour = JOUR SPORTIF LOCAL (06h→06h) — SOURCE UNIQUE partagée scan/affichage (cf. combo_daily)."""
    return _cd.day_key(now)


def _load() -> dict:
    try:
        with open(TRACK_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    tmp = TRACK_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(TRACK_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, TRACK_PATH)
    except OSError:
        pass


def load() -> dict:
    """Snapshot brut du suivi (dict par date) — dérive `stats()` ET `entries()` du MÊME état."""
    return _load()


# ------------------------------------------------------------------ construction
def _safe_dc_candidates(day: str) -> list[dict]:
    """Candidats = pour chaque match de FOOT du jour, la DOUBLE CHANCE la PLUS SÛRE (proba calibrée max).
    Réutilise `combo_daily._candidates_for_day` (déjà : foot du jour sportif, À VENIR, marchés réglables,
    proba CALIBRÉE + refroidie, code re-dérivé) puis filtre aux codes `DC …` et garde la plus sûre par match."""
    cands = _cd._candidates_for_day(day, "foot")
    best: dict = {}                                  # mid -> meilleure DC (proba la plus haute)
    for c in cands:
        if (c.get("code") or "").split()[:1] != ["DC"]:
            continue
        mid = c.get("mid")
        if not mid:
            continue
        if mid not in best or c["prob"] > best[mid]["prob"]:
            best[mid] = c
    return list(best.values())


def _combo_from_cands(cands: list[dict]) -> dict | None:
    """Assemble le combiné (optimiseur `pick_combo`) à partir de candidats DC déjà prêts (cotes correctes)."""
    combo = _cd.pick_combo(cands, min_odds=TARGET_ODDS, max_legs=MAX_LEGS, min_legs=MIN_LEGS,
                           min_leg_prob=MIN_LEG_PROB, min_leg_odds=MIN_LEG_ODDS, min_combo_prob=0.0)
    if not combo:
        return None
    legs = [{"mid": l["mid"], "sport": "foot", "name": l.get("name"), "home": l.get("home"),
             "away": l.get("away"), "start": l.get("start"), "comp": l.get("comp"),
             "sel": l["sel"], "cote": l["cote"], "prob": round(l["prob"], 4),
             "code": l["code"], "result": None, "score": None} for l in combo["legs"]]
    return {"date": None, "sport": "foot", "cote": combo["cote"], "prob": round(combo["prob"], 4),
            "legs": legs, "result": None, "sent": False, "created": None}


def build_for_day(day: str) -> dict | None:
    """Construit LE combiné sécurité foot du jour à partir des cotes DC des prédictions (SANS réseau).
    ⚠️ Repli/tests seulement : les cotes DC des shadows peuvent être MAL APPARIÉES (cf. `dc_odds`) — la
    voie de PRODUCTION est `build_for_day_async` (vraies cotes DC Unibet). None si vivier insuffisant."""
    cb = _combo_from_cands(_safe_dc_candidates(day))
    if cb:
        cb["date"] = day
    return cb


async def build_for_day_async(day: str, client=None) -> dict | None:
    """Construit LE combiné sécurité foot en lisant les VRAIES cotes « Double chance » d'Unibet par match
    (`match_select.dc_odds`) au lieu des cotes DC des shadows (parfois mal appariées, bug Santos 2026-07-28).
    -> le combiné AFFICHÉ colle EXACTEMENT au ticket Unibet (cote totale = produit des DC réelles). Voie de
    PRODUCTION (scan). None si vivier insuffisant."""
    import httpx
    from app import match_select as _ms
    cands = _safe_dc_candidates(day)
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        for c in cands:
            try:
                parts = (c.get("code") or "").split()      # "DC 1X" -> outcome "1X"
                key = parts[1].upper() if len(parts) > 1 else ""
                real = (await _ms.dc_odds(c.get("mid"), client)).get(key)
                if isinstance(real, (int, float)) and real >= 1.01:
                    c["cote"] = real                       # AUTORITÉ Unibet (remplace la cote shadow)
            except Exception:
                pass                                       # cote indispo -> on garde la cote shadow existante
    finally:
        if own:
            await client.aclose()
    cb = _combo_from_cands(cands)
    if cb:
        cb["date"] = day
    return cb


def record_daily(combo: dict, day: str) -> bool:
    """Enregistre le combiné sécurité du jour (UN par date). Ne réécrit PAS s'il est déjà ENVOYÉ (figé) ou
    déjà réglé. Toutes les jambes doivent être du FOOT (garde anti-contamination). Renvoie True si (ré)écrit."""
    if not combo or not combo.get("legs"):
        return False
    if any(l.get("sport") and l.get("sport") != "foot" for l in combo.get("legs") or []):
        return False
    d = _load()
    prev = d.get(day)
    if isinstance(prev, dict) and (prev.get("sent") or prev.get("result") in ("won", "lost", "void")):
        return False                                  # figé (posté/réglé) -> jamais réécrit
    d[day] = combo
    _save(d)
    return True


def mark_sent(day: str) -> None:
    """Marque le combiné sécurité du jour comme ENVOYÉ (Telegram) -> figé (published = frozen)."""
    d = _load()
    if isinstance(d.get(day), dict):
        d[day]["sent"] = True
        _save(d)


# ------------------------------------------------------------------ règlement (foot, info seule)
def settle_pending() -> int:
    """Règle les jambes des combinés sécurité dont les matchs FOOT sont terminés, puis tranche le combiné
    (lost si ≥1 jambe perdue ; won si ≥1 gagnée, push/void retirés ; void si toutes push/void). Idempotent.

    SIDECAR-FIRST : le score de vérité vient du sidecar du match (`result.raw`), déjà réglé par le pipeline
    principal (0 réseau, autorité), avec repli Flashscore/LiveScore + garde `likely_finished` (JAMAIS de
    règlement sur un match pas fini). Une jambe SANS score fiable reste EN ATTENTE (pas de void prématuré) ;
    void seulement si le score est trouvé mais le code irrésolvable, ou après 3 j sur un match fini muet."""
    from datetime import datetime, timezone
    from app import analyses as _an, flashscore, livescore
    from app.settle_analyst import settle_pick, code_from_pick
    d = _load()
    changed = False
    n = 0
    today = datetime.now(timezone.utc).date()
    for day, cb in list(d.items()):
        if not isinstance(cb, dict):
            continue
        frozen = cb.get("result") in ("won", "lost", "void")
        pending = any(l.get("result") not in ("won", "lost", "push") for l in cb.get("legs") or [])
        if frozen and not pending:
            continue
        try:
            age = (today - datetime.fromisoformat(day).date()).days
        except (ValueError, TypeError):
            age = 0
        for leg in cb.get("legs") or []:
            if leg.get("result") in ("won", "lost", "push"):
                continue
            score = None
            try:                                       # 1) sidecar result.raw (autorité, 0 réseau)
                sm = _an.meta("foot", str(leg.get("mid") or "")) or {}
                raw = (sm.get("result") or {}).get("raw")
                if isinstance(raw, dict) and raw.get("home") is not None:
                    score = raw
            except Exception:
                score = None
            leg_done = _an.likely_finished({"start": leg.get("start"), "sport": "foot"})
            if score is None and leg_done:             # 2) repli Flashscore/LiveScore (match fini seulement)
                q = {"home": leg.get("home", ""), "away": leg.get("away", ""),
                     "start": leg.get("start"), "sofa_id": ""}
                try:
                    score = flashscore.final_score("foot", q) or livescore.final_score("foot", q)
                except Exception:
                    score = None
            if not score:
                if leg_done and age > 3:               # match fini mais muet 3 j -> void (donnée morte)
                    leg["result"] = "void"
                    changed = True
                continue                               # sinon on laisse EN ATTENTE (pas de void prématuré)
            # CODE RE-DÉRIVÉ du LIBELLÉ (jamais le code stocké, cf. combo-single-source-of-truth)
            try:
                code = code_from_pick(leg.get("sel", ""), "foot",
                                      leg.get("home", ""), leg.get("away", "")) or leg.get("code", "")
            except Exception:
                code = leg.get("code", "")
            try:
                res = settle_pick(code, score)
            except Exception:
                res = None
            leg["result"] = res if res in ("won", "lost", "push") else "void"
            leg["score"] = score.get("label") or ""
            changed = True
        dv = _cd._derive_combo(cb.get("legs") or [])
        if dv is None:
            continue
        if dv != cb.get("result"):
            cb["result"] = dv
            changed = True
            n += 1
    if changed:
        _save(d)
    return n


# ------------------------------------------------------------------ lecture / stats / Telegram
def today(day: str, d: dict | None = None) -> dict | None:
    """Le combiné sécurité enregistré pour `day` ou None. `d` = snapshot partagé (cf. `load()`)."""
    d = _load() if d is None else d
    cb = d.get(day)
    return cb if isinstance(cb, dict) else None


def entries(d: dict | None = None) -> list:
    """Combinés sécurité suivis, PLUS RÉCENT en premier : {date, cote, prob, result, legs}."""
    d = _load() if d is None else d
    out = [cb for cb in d.values() if isinstance(cb, dict) and cb.get("legs")]
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def leg_pairs(day: str | None = None) -> set:
    """Clés-noms (`combo_daily._pair_key`) des jambes -> dédup PAR NOM (un match jambe du combiné sécurité
    n'a pas à réapparaître ailleurs si on le souhaite). `day=None` -> toutes les dates."""
    d = _load()
    days = [d.get(day)] if day is not None else list(d.values())
    out: set = set()
    for entry in days:
        if isinstance(entry, dict):
            for leg in entry.get("legs") or []:
                out.add(_cd._pair_key(leg.get("home"), leg.get("away")))
    return out


def stats(d: dict | None = None) -> dict:
    """Agrégat INFO-SEULE : {n, settled, won, lost, void, pending, hit_rate, roi_pct, profit_units,
    avg_cote}. Mise à plat 1 u. {} si aucun combiné. Snapshot partagé avec `entries()`."""
    d = _load() if d is None else d
    cbs = [cb for cb in d.values() if isinstance(cb, dict) and cb.get("legs")]
    if not cbs:
        return {}
    won = lost = void = pending = 0
    profit = 0.0
    cotes = []
    for cb in cbs:
        r = cb.get("result")
        if r == "won":
            won += 1
            profit += _cd._combo_result_profit(cb)
            cotes.append(cb.get("cote"))
        elif r == "lost":
            lost += 1
            profit -= 1
            cotes.append(cb.get("cote"))
        elif r == "void":
            void += 1
        else:
            pending += 1
    graded = won + lost
    cotes = [c for c in cotes if isinstance(c, (int, float))]
    return {
        "n": len(cbs), "settled": won + lost + void, "won": won, "lost": lost, "void": void,
        "pending": pending,
        "hit_rate": round(won / graded * 100) if graded else None,
        "roi_pct": round(profit / graded * 100, 1) if graded else None,
        "profit_units": round(profit, 2),
        "avg_cote": round(sum(cotes) / len(cotes), 2) if cotes else None,
    }


def telegram_text(cb: dict) -> str:
    """Message HTML (parse_mode=HTML) du combiné sécurité pour Telegram. Noms échappés."""
    import html as _h
    from app.analyses import pretty_sel as _psel
    out = ["🛡️ <b>COMBINÉ SÉCURITÉ FOOT</b>",
           f"Cote <b>@{cb.get('cote')}</b> · chances <b>{round((cb.get('prob') or 0) * 100)}%</b> "
           f"· {len(cb.get('legs') or [])} jambes (double chance)", ""]
    for l in cb.get("legs") or []:
        _s = _psel(str(l.get('sel') or ''), l.get('home', ''), l.get('away', ''))
        out.append(f"⚽ <b>{_h.escape(_s)}</b> @{l.get('cote')}")
        out.append(f"   <i>{_h.escape(str(l.get('name') or ''))}</i>")
    out += ["", "🛡️ <i>Info seule (hors ROI) — double chance la plus sûre, cible ~2.</i>"]
    return "\n".join(out)
