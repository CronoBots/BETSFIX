"""Auditeur MÉTHODO & COMPLÉTUDE — agent ISOLÉ (même philosophie que `app/selfcheck.py`, mais il audite
la QUALITÉ d'une ANALYSE, pas l'intégrité des données).

Problème résolu : l'analyste (`claude -p`) reçoit un dossier riche mais, étant un LLM, peut « lire en
diagonale » et sous-exploiter des signaux forts (PPDA, arbitre, SHARP par marché, bilan dom/ext…).
Cet agent DÉDIÉ vérifie, APRÈS l'analyse, qu'elle a bien EXPLOITÉ les signaux présents au dossier et
respecté la méthode. Il renvoie un verdict + les signaux ignorés + un score de complétude — ce qui
permet à l'appelant de déclencher UNE re-passe ciblée et de TRACER la complétude au sidecar (mesurable).

Conception :
- `run_claude` est INJECTÉ (pas d'import de generate_analyses -> pas de cycle ; testable offline).
- Best-effort NON BLOQUANT : tout échec/sortie vide -> {} et l'analyse d'origine tient.
- Il AUDITE, il ne re-juge PAS le pari (ça, c'est le panel de validateurs 3-agents, en aval).
"""

from __future__ import annotations

import re

PROMPT = (
    "Tu es un AUDITEUR MÉTHODO indépendant et strict. On te donne le DOSSIER FACTUEL d'un match et "
    "l'ANALYSE qu'un analyste en a tirée. Ton UNIQUE mission : vérifier que l'analyse a EXPLOITÉ les "
    "SIGNAUX FORTS présents dans le dossier et respecté la méthode. Tu ne re-juges PAS le pari, tu "
    "audites la COMPLÉTUDE.\n\n"
    "Un signal est « exploité » s'il est pris en compte dans le raisonnement (cité, pondéré, OU "
    "explicitement écarté avec une raison). Vérifie, UNIQUEMENT parmi ceux réellement PRÉSENTS au "
    "dossier :\n"
    "- blessés/absents, forme récente, H2H (dont « H2H récents » avec scores) ;\n"
    "- ancre SHARP Pinnacle : « CONSENSUS SHARP » (1X2) ET « SHARP PAR MARCHÉ » (totaux/handicaps) ;\n"
    "- VALUE de-vig (proba juste « jXX% » vs proba estimée) ;\n"
    "- « Style … PPDA » (Understat), « Arbitre … » (cartons) ;\n"
    "- tennis : classement enrichi (points + tendance) + SURFACE + bilan par surface ;\n"
    "- basket : bilan DOMICILE/EXTÉRIEUR (dom./ext.), back-to-back.\n"
    "Un signal FORT présent au dossier mais totalement IGNORÉ (ou contredit SANS justification) = un "
    "MANQUE. Ne signale PAS un signal ABSENT du dossier, ni un détail mineur.\n\n"
    "Réponds en 3 lignes EXACTEMENT, RIEN d'autre :\n"
    "VERDICT: COMPLET   (ou INCOMPLET)\n"
    "MANQUES: <signaux forts ignorés, séparés par ' ; ' — ou 'aucun'>\n"
    "SCORE: <complétude, entier 0-100>\n")

_NONE = ("aucun", "aucune", "none", "rien", "-", "n/a", "ras")


def _parse(out: str) -> dict:
    """Sortie de l'auditeur -> {complete: bool, missing: [str], score: int|None}. Robuste au bruit."""
    txt = out or ""
    mv = re.search(r"VERDICT\s*:\s*\**\s*(COMPLET|INCOMPLET)", txt, re.I)
    complete = bool(mv) and mv.group(1).upper() == "COMPLET"
    mm = re.search(r"MANQUES?\s*:\s*\**\s*(.+)", txt)
    raw = (mm.group(1).strip() if mm else "").split("\n")[0].strip(" .*")
    missing: list = []
    if raw and raw.lower() not in _NONE:
        missing = [s.strip(" .*") for s in re.split(r"[;,]", raw) if s.strip(" .*")][:8]
    ms = re.search(r"SCORE\s*:\s*\**\s*(\d{1,3})", txt)
    score = min(int(ms.group(1)), 100) if ms else None
    if complete:                       # verdict COMPLET => pas de manque retenu (cohérence)
        missing = []
    return {"complete": complete, "missing": missing, "score": score}


def audit(run_claude, doss: str, analysis: str, sport: str, timeout: int = 200) -> dict:
    """Lance l'agent auditeur sur (dossier, analyse). `run_claude(prompt, timeout)` est injecté.
    Renvoie {complete, missing, score} ou {} si rien à auditer / échec / sortie vide (NON bloquant)."""
    if not doss or not analysis:
        return {}
    prompt = (PROMPT + f"\nSPORT : {sport}\n\n===== DOSSIER FACTUEL =====\n{doss}\n\n"
              f"===== ANALYSE À AUDITER =====\n{analysis}\n")
    try:
        out = run_claude(prompt, timeout)
    except Exception:
        return {}
    if not out or not out.strip():
        return {}
    return _parse(out)


def feedback(missing: list) -> str:
    """Consigne de RE-PASSE à concaténer au prompt de l'analyste quand l'audit a trouvé des manques."""
    return ("\n\n⚠️ AUDIT MÉTHODO — ta précédente analyse a IGNORÉ des signaux FORTS du dossier : "
            + " ; ".join(missing) + ".\nREFAIS l'analyse en les INTÉGRANT explicitement (mets à jour ta "
            "proba et ton pick si ces signaux le justifient), en respectant EXACTEMENT le même format.")
