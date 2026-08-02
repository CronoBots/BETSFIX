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
    if "carton" in s:
        return "cartons"
    if "corner" in s:
        return "corners"
    if "tir" in s and ("cadré" in s or "cadre" in s):
        return "tirs_cadres"
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
    "cartons": "Cartons",
    "corners": "Corners",
    "tirs_cadres": "Tirs cadrés",
    "combine_foot": "Combiné foot (double chance)",
    "autre": "Autre",
}


# ─────────────────────────── Sélection des paris à débriefer ───────────────────────────
# PÉRIMÈTRE (demande user 2026-08-02) : UNIQUEMENT les paris de la MÉTHODE ACTUELLE —
#   (1) SIMPLES foot joués (stat_bet figé) = le pari phare ;
#   (2) le NOUVEAU combiné foot = la DOUBLE CHANCE du jour (app/combo_daily, data/combo_daily_track.json).
# On NE débriefe PLUS les vieux combinés multi-marchés des sidecars (`d["combo"]`, ex-CdM) : erreur de
# jeunesse déjà corrigée -> n'encombre pas la mémoire (cf. [[history-corrections-before-subscribers]]).
def pending(sports=("foot",), limit: int | None = None) -> list:
    """Descripteurs des pertes À DÉBRIEFER, non encore traitées. Un descripteur = dict :
       {"kind":"simple","sport","fid","d"(sidecar),"lb"(sel/cote/prob),"date"} pour un simple joué perdu ;
       {"kind":"combo","sport":"foot","fid":"combo:<date>","entry"(combo_daily),"date"} pour le combiné
       double chance perdu. Les plus RÉCENTS d'abord. `sports` défaut foot (le phare)."""
    done = set(_load(DEBRIEFS_PATH).keys())
    items = []
    # (1) SIMPLES joués perdus (stat_bet figé = le pari compté).
    for sp in sports:
        for d in analyses.iter_meta(sp):
            fid = str(d.get("id"))
            if fid in done or not analyses.is_settled(d):
                continue
            sb = d.get("stat_bet")
            if not (isinstance(sb, dict) and sb.get("result") == "lost"):
                continue
            items.append({"kind": "simple", "sport": sp, "fid": fid, "d": d,
                          "lb": {"sel": sb.get("sel"), "cote": sb.get("cote"), "prob": sb.get("prob")},
                          "date": (d.get("start") or "")[:10]})
    # (2) NOUVEAU COMBINÉ FOOT (double chance) perdu — combo_daily_track.json, clé = date.
    if "foot" in sports:
        try:
            from app import combo_daily as _cd
            track = _cd._load("foot")
            for date, e in track.items():
                if not isinstance(e, dict) or e.get("result") != "lost":
                    continue
                fid = f"combo:{date}"
                if fid in done:
                    continue
                items.append({"kind": "combo", "sport": "foot", "fid": fid, "entry": e, "date": date})
        except Exception:
            pass
    items.sort(key=lambda it: it.get("date") or "", reverse=True)
    return items[:limit] if limit else items


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


_PROMPT_COMBO = """Tu es l'analyste POST-MATCH de BETSFIX. Le COMBINÉ FOOT DU JOUR (type « double chance », plusieurs
jambes) a PERDU. Explique FACTUELLEMENT pourquoi et si c'était ÉVITABLE.

COMBINÉ du {date} — cote {cote}, confiance annoncée {prob}. Résultat : PERDU.
JAMBES (avec résultat et pré-analyse) :
{legs}

Le combiné perd si UNE SEULE jambe tombe. TA TÂCHE : identifier la/les jambe(s) qui a/ont fait perdre et
DÉTERMINER si la construction était fautive (jambe qui contredit notre propre analyse, marché déconseillé,
jambe « coin-flip » ajoutée sans value) — cause évitable — ou si une jambe solide est juste tombée par
malchance (variance, process sain, AUCUNE leçon). Ne FABRIQUE pas de leçon s'il n'y en a pas.

Réponds UNIQUEMENT par un bloc JSON (aucun texte autour) :
```json
{{
  "cause": "un parmi: premisse_defensive|premisse_offensive|premisse_favori|carton_rouge|penalty|blessure|rotation|arbitrage|meteo|evenement_tardif|mauvais_marche|cote_sans_value|variance|autre",
  "evitable": true,
  "premisse_fausse": "la jambe/supposition qui a cassé le combiné (1 phrase courte)",
  "ce_qui_s_est_passe": "ce qui a réellement fait tomber la/les jambe(s) (1-2 phrases factuelles)",
  "lecon": "leçon de CONSTRUCTION du combiné si évitable, sinon chaîne vide",
  "confiance_analyse": 70
}}
```"""


def _prob_txt(prob) -> str:
    if isinstance(prob, (int, float)):
        return f"{round(prob * 100)}%" if prob <= 1 else f"{round(prob)}%"
    return f"{prob}%" if prob else "?"


def build_prompt(item: dict) -> str:
    """Prompt du débrief selon le type (simple joué OU combiné double chance)."""
    if item.get("kind") == "combo":
        e = item.get("entry") or {}
        legs_txt = []
        for i, l in enumerate(e.get("legs") or [], 1):
            r = (l.get("result") or "?").upper()
            mark = "  ⟵ JAMBE PERDANTE" if l.get("result") == "lost" else ""
            why = (l.get("why") or "").strip()
            legs_txt.append(
                f"{i}. [{r}{mark}] {l.get('name','?')} — {l.get('sel','?')} (cote {l.get('cote','?')}, "
                f"score {l.get('score','?')})" + (f"\n   Pré-analyse : {why}" if why else ""))
        return _PROMPT_COMBO.format(
            date=item.get("date", "?"), cote=e.get("cote", "?"), prob=_prob_txt(e.get("prob")),
            legs="\n".join(legs_txt)[:6000])
    # SIMPLE joué.
    sport, d, lb = item["sport"], item["d"], item["lb"]
    md = analyses.load(sport, d.get("id")) or "(analyse pré-match indisponible)"
    board = analyses.result_board(d, sport) or {}
    score = board.get("score") or (analyses.result_chip(d)[1] if analyses.result_chip(d) else "?")
    try:
        ld = analyses.pretty_sel(lb.get("sel") or "", d.get("home", ""), d.get("away", ""))
    except Exception:
        ld = lb.get("sel") or "?"
    return _PROMPT.format(
        home=d.get("home", "?"), away=d.get("away", "?"), comp=d.get("comp", ""),
        date=(d.get("start") or "")[:10], sel=ld, cote=lb.get("cote") or "?",
        prob=_prob_txt(lb.get("prob")), score=score, md=md[:6000])


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


def _entry_from_item(item: dict, parsed: dict) -> tuple[str, str, dict]:
    """Construit (fid, famille_marché, débrief) depuis un descripteur pending + le JSON parsé."""
    if item.get("kind") == "combo":
        e = item.get("entry") or {}
        legs = e.get("legs") or []
        lost = [l for l in legs if l.get("result") == "lost"]
        fam = "combine_foot"
        sel = " + ".join(str((l or {}).get("sel") or "") for l in legs)
        home = "Combiné " + (item.get("date") or "")
        return item["fid"], fam, {
            "sport": "foot", "fid": item["fid"], "home": home, "away": f"{len(legs)} jambes",
            "comp": "Combiné double chance", "date": item.get("date", ""),
            "sel": sel, "cote": e.get("cote"), "prob": e.get("prob"), "kind": "combo",
            "market_family": fam, "score": ", ".join(f"{l.get('name','?')} {l.get('score','?')}"
                                                      for l in lost)[:200], **parsed}
    sport, d, lb = item["sport"], item["d"], item["lb"]
    fam = market_family(lb.get("sel") or "")
    board = analyses.result_board(d, sport) or {}
    return str(d.get("id")), fam, {
        "sport": sport, "fid": str(d.get("id")), "home": d.get("home", ""), "away": d.get("away", ""),
        "comp": d.get("comp", ""), "date": (d.get("start") or "")[:10],
        "sel": lb.get("sel"), "cote": lb.get("cote"), "prob": lb.get("prob"),
        "kind": "simple", "market_family": fam, "score": board.get("score") or "", **parsed}


def record(item: dict, parsed: dict) -> dict:
    """Écrit le débrief (simple OU combiné) + met à jour l'agrégat des leçons. Renvoie le débrief stocké."""
    fid, fam, entry = _entry_from_item(item, parsed)
    entry["generated_at"] = datetime.now(timezone.utc).isoformat()
    dbs = _load(DEBRIEFS_PATH)
    dbs[fid] = entry
    _save(DEBRIEFS_PATH, dbs)

    # AGRÉGAT évolutif : compteurs par (sport × famille de marché × cause).
    sport = entry["sport"]
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
    for item in todo:
        fid = item["fid"]
        _lbl = (f"combiné {item.get('date')}" if item.get("kind") == "combo"
                else f"{item['d'].get('home')}—{item['d'].get('away')}")
        try:
            out = runner(build_prompt(item))
            parsed = parse(out)
            if not parsed:
                fail += 1
                log(f"[debrief] ⚠️ parse KO {fid} {_lbl}")
                continue
            record(item, parsed)
            ok += 1
            log(f"[debrief] ✓ {_lbl} · {parsed['cause']}"
                f"{' · évitable' if parsed['evitable'] else ''}")
        except Exception as e:                                 # best-effort, jamais bloquant
            fail += 1
            log(f"[debrief] ✗ {fid} : {e}")
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
