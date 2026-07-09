"""Revue quotidienne BETSFIX — POUR LE PROPRIÉTAIRE (pas les abonnés). RUN QUOTIDIEN (après le scan).

Consolide l'état par sport (optimalité), la fiabilité de la calibration, le verdict du backtest, et
DÉTECTE les écarts à l'optimum -> PROPOSITIONS (mécaniques, à seuil). Compare à la veille (deltas).
100 % LECTURE SEULE côté données de paris. Écrit `docs/REVUE.md` + journal `data/revue_log.jsonl`.

Usage :  python tools/daily_review.py            # (ré)génère la revue + imprime
         python tools/daily_review.py --quiet
         python tools/daily_review.py --telegram  # pousse AUSSI en privé SI un chat proprio est configuré
                                                   #   (data/owner_chat.txt = chat_id perso, JAMAIS le canal abonnés)
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import analyses  # noqa: E402
from tools.methodology_doc import _scorecard, SPORTS  # noqa: E402  (même logique d'optimalité)

try:                       # console Windows cp1252 -> emojis sans crash (une seule fois, après les imports)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(_ROOT, "docs", "REVUE.md")
LOG = os.path.join(_ROOT, "data", "revue_log.jsonl")
_MIN_SETTLED = 20     # sous ce nb de réglés, on ne conclut pas (aligné methodology_doc)


def _backtest_verdict() -> tuple[str, list]:
    try:
        with open(os.path.join(_ROOT, "data", "backtest_log.jsonl"), encoding="utf-8") as fh:
            last = json.loads([l for l in fh if l.strip()][-1])
        return last.get("verdict", "—"), last.get("recs") or []
    except Exception:
        return "—", []


def _prev_state() -> dict:
    """Dernière revue (pour les deltas)."""
    try:
        with open(LOG, encoding="utf-8") as fh:
            return json.loads([l for l in fh if l.strip()][-1]).get("state", {})
    except Exception:
        return {}


def build(now: datetime) -> tuple[str, dict]:
    sf = analyses.stats_full()["by_sport"]
    cal_by = analyses.calibration().get("by_sport") or {}
    rel = analyses.calibration_reliability() or {}
    bt_verdict, bt_recs = _backtest_verdict()
    prev = _prev_state()

    state: dict = {}          # verdict machine par sport (pour les deltas)
    lines: list = []
    props: list = []          # propositions (nécessitent TA décision)

    lines.append("# BETSFIX — Revue quotidienne (propriétaire)")
    lines.append("")
    lines.append("> Générée automatiquement par `tools/daily_review.py`. Interne — NE PAS diffuser aux abonnés.")
    lines.append(f"> {now.strftime('%Y-%m-%d %H:%M UTC')}.")
    lines.append("")
    lines.append(f"**Fiabilité calibration (globale)** : {rel.get('index','—')}/100, tendance **{rel.get('trend','—')}** "
                 f"(n={rel.get('n','—')}) · **Backtest** : *{bt_verdict}*.")
    lines.append("")
    lines.append("## État par sport")

    for short, calname, title in SPORTS:
        stat = sf.get(short) or {}
        cal = cal_by.get(calname) or {}
        okA, okB, verdict, _ = _scorecard(stat, cal)
        # code machine COMPACT du verdict (pour comparer à hier)
        code = ("optimal" if (okA and okB) else
                "en_cours" if (stat.get("settled") or 0) < _MIN_SETTLED else
                "a_affiner" if (okA or okB) else "a_corriger")
        state[short] = code
        # delta vs veille
        delta = ""
        if prev.get(short) and prev[short] != code:
            delta = f"  _(hier : {prev[short]})_"
        roi = stat.get("roi")
        lines.append(f"- {title} — {verdict}{delta}")
        lines.append(f"  ROI {'' if roi is None else ('+' if roi >= 0 else '')}{roi}% · "
                     f"réussite {stat.get('pct','—')}% · {stat.get('settled','—')} réglés · "
                     f"drawdown {stat.get('dd_pct','—')}% · calibration MAE {cal.get('mae','—')}")

        # ---- PROPOSITIONS mécaniques (à seuil) ----
        n = stat.get("settled") or 0
        if roi is not None and roi <= -10 and n >= 25 and okB:
            props.append(f"🎯 **{title}** : bien calibré mais ROI {roi}% sur {n} réglés → resserrer la "
                         f"sélection (zone de cote / marché perdant). Backtestable avant décision.")
        if roi is not None and roi <= -25 and n >= _MIN_SETTLED:
            props.append(f"🔴 **{title}** : ROI {roi}% (saignée) → à traiter en priorité.")

    for name, g in (bt_recs or []):
        props.append(f"🧪 **Backtest** propose un changement de seuil : {name} — à valider.")

    lines.append("")
    lines.append("## Propositions (nécessitent ta décision)")
    if props:
        lines.extend("- " + p for p in props)
        lines.append("")
        lines.append("_Réponds-moi (Claude) pour qu'on applique/backteste une proposition._")
    else:
        lines.append("- Aucune. Laisser mûrir + observer.")
    return "\n".join(lines) + "\n", {"state": state, "props": len(props)}


def main():
    now = datetime.now(timezone.utc)
    md, meta = build(now)
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now.isoformat(), **meta}, ensure_ascii=False) + "\n")

    if "--telegram" in sys.argv:                     # push PRIVÉ uniquement (jamais le canal abonnés)
        chat_p = os.path.join(_ROOT, "data", "owner_chat.txt")
        if os.path.exists(chat_p):
            try:
                from app import notify
                chat = open(chat_p, encoding="utf-8").read().strip()
                tok, _ = notify._config()
                if tok and chat:
                    import httpx
                    httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                               json={"chat_id": chat, "text": md[:3900]}, timeout=15)
                    print("revue poussée en privé (owner_chat).")
            except Exception as e:
                print(f"(push privé ignoré : {e})")
        else:
            print("(pas de data/owner_chat.txt -> pas de push privé ; revue dans docs/REVUE.md)")

    if "--quiet" not in sys.argv:
        print(md)
    else:
        print(f"docs/REVUE.md régénéré ({meta['props']} proposition(s)).")


if __name__ == "__main__":
    main()
