"""DÉBRIEF — mémoire évolutive des paris JOUÉS PERDUS (demande user 2026-08-02).

Analyse a posteriori POURQUOI un pari joué a perdu : prémisse fausse ? malchance/variance ? cause
évitable ? Chaque perte reçoit un débrief structuré (data/debriefs.json) et alimente un agrégat de
LEÇONS récurrentes (data/lessons.json) — la « mémoire qui évolue ».

⚠️ TOTALEMENT ISOLÉ (comme app/combo_daily.py / app/provisional.py) : lecture des sidecars/.md +
écriture de SES DEUX fichiers uniquement. Ne touche JAMAIS aux sidecars, à `stat_bet`, au ROI, aux
stats ni à la calibration. La RÉTROACTION dans le scan (injecter les leçons dans le prompt) est une
décision SÉPARÉE, volontairement NON branchée ici (choix user : « analyse + mémoire d'abord »).

Distinction CLÉ portée par l'IA : un pari à forte confiance perd parfois par simple VARIANCE (le process
était bon) -> cause="variance", evitable=false, AUCUNE leçon. On ne « apprend » que des prémisses
réellement défaillantes / marchés déconseillés (evitable=true) — sinon on surajuste au bruit et on
saborde le taux phare.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from app import analyses

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBRIEFS_PATH = os.path.join(_ROOT, "data", "debriefs.json")   # {fid: débrief}
LESSONS_PATH = os.path.join(_ROOT, "data", "lessons.json")     # agrégat évolutif des leçons

# Taxonomie FIXE des causes (pour agréger proprement). "variance" = rien à apprendre (process sain).
CAUSES = (
    "premisse_defensive",   # le pari « peu de buts » / défensif a sauté (les buts sont venus)
    "premisse_offensive",   # le pari « des buts » / offensif n'est pas venu (match fermé)
    "premisse_favori",      # le favori a sous-performé / s'est fait surprendre
    "carton_rouge",         # expulsion qui a fait basculer le match
    "penalty",              # penalty (souvent tardif) décisif contre nous
    "blessure",             # absence / blessure clé non intégrée
    "rotation",             # effectif tourné / mise au repos non anticipée
    "arbitrage",            # décision d'arbitrage (VAR…) décisive
    "meteo",                # conditions météo
    "evenement_tardif",     # but / événement très tardif (90'+) qui renverse
    "mauvais_marche",       # marché que notre propre historique déconseille
    "cote_sans_value",      # pile ou face déguisé, pas de vraie value
    "variance",             # process sain, issue défavorable — AUCUNE leçon
    "autre",
)
_CAUSE_SET = set(CAUSES)

# Libellés lisibles (affichage FR).
CAUSE_LABEL = {
    "premisse_defensive": "Prémisse défensive fausse",
    "premisse_offensive": "Prémisse offensive fausse",
    "premisse_favori": "Favori défaillant",
    "carton_rouge": "Carton rouge",
    "penalty": "Penalty décisif",
    "blessure": "Absence / blessure clé",
    "rotation": "Effectif tourné",
    "arbitrage": "Décision d'arbitrage",
    "meteo": "Météo",
    "evenement_tardif": "Événement tardif (90'+)",
    "mauvais_marche": "Marché déconseillé",
    "cote_sans_value": "Cote sans value",
    "variance": "Variance (malchance)",
    "autre": "Autre",
}


# ─────────────────────────── I/O atomique ───────────────────────────
def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def all_debriefs() -> dict:
    return _load(DEBRIEFS_PATH)


def lessons() -> dict:
    return _load(LESSONS_PATH)


def debrief_for(fid) -> dict | None:
    return _load(DEBRIEFS_PATH).get(str(fid))


# ─────────────────────────── Classification marché ───────────────────────────
def market_family(sel: str) -> str:
    """Famille de marché normalisée (pour agréger les leçons). Volontairement grossière et robuste."""
    s = (sel or "").lower()
    if any(k in s for k in ("moins de", "under", "-2.5", "-3.5", "-1.5")) and "but" in s:
        return "total_moins_buts"
    if any(k in s for k in ("plus de", "over", "+2.5", "+3.5", "+1.5")) and "but" in s:
        return "total_plus_buts"
    if "double chance" in s or "ou nul" in s or "ne perd pas" in s or re.search(r"\+0\.5\b", s):
        return "double_chance"
    if "handicap" in s or re.search(r"[+-]\d\.5", s):
        return "handicap"
    if "les deux" in s or "btts" in s or "marque" in s:
        return "equipe_marque_btts"
    if any(k in s for k in ("vainqueur", "gagne", "victoire")) or re.search(r"\b1x2\b", s):
        return "resultat_1x2"
    return "autre"


MARKET_LABEL = {
    "total_moins_buts": "Moins de buts (Under)",
    "total_plus_buts": "Plus de buts (Over)",
    "double_chance": "Double chance",
    "handicap": "Handicap",
    "equipe_marque_btts": "Équipe marque / BTTS",
    "resultat_1x2": "Résultat (1X2)",
    "autre": "Autre",
}


# ─────────────────────────── Sélection des paris à débriefer ───────────────────────────
def _lost_played_bet(d: dict) -> dict | None:
    """Le PARI JOUÉ PERDU d'un sidecar (pour_history), ou None. Source = stat_bet figé (le pari compté) ;
    repli retained_bet(for_history) pour être robuste. Combiné perdu = traité aussi (combo.result)."""
    sb = d.get("stat_bet")
    if isinstance(sb, dict) and sb.get("result") == "lost":
        return {"sel": sb.get("sel"), "cote": sb.get("cote"), "prob": sb.get("prob"), "kind": "simple"}
    combo = d.get("combo") or {}
    if combo.get("legs") and combo.get("result") == "lost":
        legs = combo.get("legs") or []
        sel = " + ".join(str((l or {}).get("sel") or "") for l in legs)
        return {"sel": sel, "cote": combo.get("cote"), "prob": combo.get("prob"), "kind": "combo"}
    return None


def pending(sports=("foot",), limit: int | None = None) -> list:
    """Sidecars d'un/des sports portant un PARI JOUÉ PERDU sans débrief encore enregistré. Les plus RÉCENTS
    d'abord (on apprend des dernières pertes en priorité). `sports` défaut foot (le pari phare)."""
    done = set(_load(DEBRIEFS_PATH).keys())
    out = []
    for sp in sports:
        for d in analyses.iter_meta(sp):
            fid = str(d.get("id"))
            if fid in done:
                continue
            if not analyses.is_settled(d):
                continue
            lb = _lost_played_bet(d)
            if not lb:
                continue
            out.append((sp, d, lb))
    out.sort(key=lambda t: (t[1].get("start") or ""), reverse=True)
    return out[:limit] if limit else out


def pending_count(sports=("foot",)) -> int:
    return len(pending(sports))


# ─────────────────────────── Prompt & parsing ───────────────────────────
_PROMPT = """Tu es l'analyste POST-MATCH de BETSFIX. Un PARI JOUÉ a PERDU. Explique FACTUELLEMENT pourquoi.

MATCH : {home} — {away}  ({comp}, {date})
PARI JOUÉ (perdu) : {sel}  — cote {cote}, confiance annoncée {prob}
SCORE FINAL : {score}

ANALYSE PRÉ-MATCH (ce que nous avions écrit AVANT le match) :
{md}

TA TÂCHE : déterminer la CAUSE de la perte et si elle était ÉVITABLE.
RÈGLE ABSOLUE : un pari à forte confiance perd parfois par simple VARIANCE — le raisonnement était bon,
l'issue juste défavorable. Dans ce cas : cause="variance", evitable=false, lecon="". NE FABRIQUE PAS une
leçon quand il n'y en a pas. Ne mets evitable=true QUE pour une vraie prémisse défaillante (un signal
qu'on aurait dû voir avant) ou un marché que notre historique déconseille. Reste factuel, pas de méta-proba.

Réponds UNIQUEMENT par un bloc JSON (aucun texte autour) :
```json
{{
  "cause": "un parmi: premisse_defensive|premisse_offensive|premisse_favori|carton_rouge|penalty|blessure|rotation|arbitrage|meteo|evenement_tardif|mauvais_marche|cote_sans_value|variance|autre",
  "evitable": true,
  "premisse_fausse": "la supposition pré-match qui a cassé (1 phrase courte)",
  "ce_qui_s_est_passe": "ce qui a réellement décidé le match (1-2 phrases factuelles)",
  "lecon": "leçon actionnable si evitable, sinon chaîne vide",
  "confiance_analyse": 70
}}
```"""


def build_prompt(sport: str, d: dict, lb: dict) -> str:
    fid = str(d.get("id"))
    md = analyses.load(sport, d.get("id")) or "(analyse pré-match indisponible)"
    board = analyses.result_board(d, sport) or {}
    score = board.get("score") or (analyses.result_chip(d)[1] if analyses.result_chip(d) else "?")
    try:
        ld = analyses.pretty_sel(lb.get("sel") or "", d.get("home", ""), d.get("away", ""))
    except Exception:
        ld = lb.get("sel") or "?"
    prob = lb.get("prob")
    prob_txt = f"{round(prob * 100)}%" if isinstance(prob, (int, float)) and prob <= 1 else (
        f"{prob}%" if prob else "?")
    return _PROMPT.format(
        home=d.get("home", "?"), away=d.get("away", "?"), comp=d.get("comp", ""),
        date=(d.get("start") or "")[:10], sel=ld, cote=lb.get("cote") or "?",
        prob=prob_txt, score=score, md=md[:6000])


def parse(out: str) -> dict | None:
    """Extrait le JSON du débrief (bloc ```json``` ou premier {…}). None si illisible."""
    if not out:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", out, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        m2 = re.search(r"\{.*\}", out, re.S)
        raw = m2.group(0) if m2 else None
    if raw is None:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    cause = str(obj.get("cause") or "autre").strip().lower()
    if cause not in _CAUSE_SET:
        cause = "autre"
    return {
        "cause": cause,
        "evitable": bool(obj.get("evitable")) and cause != "variance",
        "premisse_fausse": str(obj.get("premisse_fausse") or "").strip()[:400],
        "ce_qui_s_est_passe": str(obj.get("ce_qui_s_est_passe") or "").strip()[:600],
        "lecon": str(obj.get("lecon") or "").strip()[:400],
        "confiance_analyse": obj.get("confiance_analyse"),
    }


# ─────────────────────────── Enregistrement + agrégat ───────────────────────────
def _lesson_key(sport: str, fam: str, cause: str) -> str:
    return f"{sport}|{fam}|{cause}"


def record(sport: str, d: dict, lb: dict, parsed: dict) -> dict:
    """Écrit le débrief du pari + met à jour l'agrégat des leçons. Renvoie le débrief stocké."""
    fid = str(d.get("id"))
    fam = market_family(lb.get("sel") or "")
    board = analyses.result_board(d, sport) or {}
    entry = {
        "sport": sport, "fid": fid, "home": d.get("home", ""), "away": d.get("away", ""),
        "comp": d.get("comp", ""), "date": (d.get("start") or "")[:10],
        "sel": lb.get("sel"), "cote": lb.get("cote"), "prob": lb.get("prob"),
        "kind": lb.get("kind"), "market_family": fam,
        "score": board.get("score") or "", **parsed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    dbs = _load(DEBRIEFS_PATH)
    dbs[fid] = entry
    _save(DEBRIEFS_PATH, dbs)

    # AGRÉGAT évolutif : compteurs par (sport × famille de marché × cause).
    les = _load(LESSONS_PATH)
    key = _lesson_key(sport, fam, entry["cause"])
    slot = les.get(key) or {"sport": sport, "market_family": fam, "cause": entry["cause"],
                            "count": 0, "evitable": 0, "leagues": {}, "examples": [], "lecon": ""}
    slot["count"] += 1
    if entry["evitable"]:
        slot["evitable"] += 1
        if entry["lecon"]:
            slot["lecon"] = entry["lecon"]     # dernière leçon actionnable consolidée
    comp = entry["comp"] or "?"
    slot["leagues"][comp] = (slot["leagues"].get(comp) or 0) + 1
    slot["examples"] = ([fid] + [e for e in slot["examples"] if e != fid])[:6]
    les[key] = slot
    _save(LESSONS_PATH, les)
    return entry


def run(runner, sports=("foot",), limit: int | None = None, log=print) -> dict:
    """Génère les débriefs manquants. `runner(prompt)->str` = l'appel Claude headless (injecté par le CLI,
    pour garder ce module libre de l'import lourd du scan). Renvoie un petit bilan."""
    todo = pending(sports, limit)
    log(f"[debrief] {len(todo)} perte(s) à débriefer")
    ok = fail = 0
    for sp, d, lb in todo:
        fid = str(d.get("id"))
        try:
            out = runner(build_prompt(sp, d, lb))
            parsed = parse(out)
            if not parsed:
                fail += 1
                log(f"[debrief] ⚠️ parse KO {sp} {fid} {d.get('home')}—{d.get('away')}")
                continue
            record(sp, d, lb, parsed)
            ok += 1
            log(f"[debrief] ✓ {d.get('home')}—{d.get('away')} · {parsed['cause']}"
                f"{' · évitable' if parsed['evitable'] else ''}")
        except Exception as e:                                 # best-effort, jamais bloquant
            fail += 1
            log(f"[debrief] ✗ {sp} {fid} : {e}")
    return {"todo": len(todo), "ok": ok, "fail": fail}


# ─────────────────────────── Vues agrégées (affichage) ───────────────────────────
def summary(sports=("foot",)) -> dict:
    """Synthèse pour l'affichage : total débriefs, évitables vs variance, top causes, top marchés à risque,
    leçons actionnables (récurrentes d'abord)."""
    dbs = _load(DEBRIEFS_PATH)
    rows = [e for e in dbs.values() if isinstance(e, dict) and e.get("sport") in sports]
    n = len(rows)
    evit = sum(1 for e in rows if e.get("evitable"))
    by_cause: dict = {}
    by_market: dict = {}
    for e in rows:
        by_cause[e.get("cause")] = (by_cause.get(e.get("cause")) or 0) + 1
        if e.get("evitable"):
            by_market[e.get("market_family")] = (by_market.get(e.get("market_family")) or 0) + 1
    les = _load(LESSONS_PATH)
    actionable = sorted(
        [s for s in les.values() if isinstance(s, dict) and s.get("sport") in sports
         and s.get("evitable") and s.get("lecon")],
        key=lambda s: (s.get("evitable") or 0, s.get("count") or 0), reverse=True)
    return {
        "total": n, "evitable": evit, "variance": n - evit,
        "by_cause": sorted(by_cause.items(), key=lambda t: t[1], reverse=True),
        "by_market": sorted(by_market.items(), key=lambda t: t[1], reverse=True),
        "lessons": actionable,
    }
