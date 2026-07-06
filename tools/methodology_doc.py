"""Documentation MÉTHODOLOGIE par sport — RUN QUOTIDIEN (après le scan/règlement).

Écrit `docs/METHODOLOGIE.md` : pour CHAQUE sport (foot/tennis/basket), la méthode d'analyse et de
SÉLECTION des pronos, son état MESURÉ (ROI, calibration), les repères méthodo, et une SCORECARD
d'optimalité. But : voir, sport par sport, QUAND la méthode est optimale.

« Optimal » (critères choisis) = (A) ROI positif & STABLE  ET  (B) calibration BONNE.

100 % LECTURE SEULE côté données de paris : n'écrit QUE docs/METHODOLOGIE.md.
Usage :  python tools/methodology_doc.py            # (ré)génère le doc
         python tools/methodology_doc.py --quiet    # sans imprimer le doc
"""
import io
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from app import analyses  # noqa: E402

DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "METHODOLOGIE.md")

# nom court (stats/exclusions/jalons) -> nom calibration by_sport
SPORTS = [("foot", "Football", "⚽ Football"),
          ("tennis", "Tennis", "🎾 Tennis"),
          ("basket", "Basket", "🏀 Basket")]

# Seuils d'optimalité (alignés sur les critères choisis : ROI stable + calibration).
_MIN_SETTLED = 20      # sous ce nombre de paris réglés, l'échantillon ne tranche pas encore
_MAX_DD_PCT = 20.0     # au-delà, le ROI n'est pas « stable »
_MAX_MAE = 5.0         # au-delà, la calibration n'est pas « bonne »


def _fmt_pct(v):
    return "—" if v is None else f"{'+' if v >= 0 else ''}{v}%"


def _scorecard(stat: dict, cal: dict) -> tuple[bool, bool, str, list]:
    """(A) ROI positif & stable, (B) calibration bonne -> (okA, okB, verdict, lignes détail)."""
    settled = stat.get("settled") or 0
    roi = stat.get("roi")
    dd = stat.get("dd_pct")
    mae = cal.get("mae")
    verdict_cal = cal.get("verdict")
    enough = settled >= _MIN_SETTLED

    okA = bool(enough and roi is not None and roi > 0 and dd is not None and dd <= _MAX_DD_PCT)
    okB = bool(verdict_cal == "good" and mae is not None and mae <= _MAX_MAE)

    lignes = [
        f"- **[A] ROI positif & stable** : {'✅' if okA else '❌'} "
        f"(ROI {_fmt_pct(roi)}, drawdown max {dd if dd is not None else '—'}%, {settled} réglés"
        + ("" if enough else f" — échantillon < {_MIN_SETTLED}") + ")",
        f"- **[B] Calibration bonne** : {'✅' if okB else '❌'} "
        f"(MAE {mae if mae is not None else '—'}, verdict {verdict_cal or '—'}, "
        f"réussite {cal.get('win_rate','—')}% vs confiance {cal.get('avg_conf','—')}%)",
    ]

    if not enough:
        verdict = f"⏳ **EN COURS** — échantillon à étoffer ({settled}/{_MIN_SETTLED} réglés)"
    elif okA and okB:
        verdict = "🟢 **OPTIMAL** — ROI stable positif ET bien calibré"
    elif okB and not okA:
        verdict = ("🟠 **À AFFINER** — bien calibré (prédictions honnêtes) mais ROI/stabilité KO : "
                   "la value/sélection ne convertit pas la justesse en profit")
    elif okA and not okB:
        verdict = "🟠 **À AFFINER** — rentable mais calibration à resserrer"
    else:
        verdict = "🔴 **À CORRIGER** — ni rentable ni bien calibré"
    return okA, okB, verdict, lignes


def build() -> str:
    now = datetime.now(timezone.utc)
    sf = analyses.stats_full()["by_sport"]
    cal_all = analyses.calibration()
    cal_by = cal_all.get("by_sport") or {}
    rel = analyses.calibration_reliability() or {}
    miles = list(analyses.MODEL_MILESTONES)

    # verdict backtest (global) — info
    bt = "—"
    try:
        import json
        p = os.path.join(os.path.dirname(DOC), "..", "data", "backtest_log.jsonl")
        with open(p, encoding="utf-8") as fh:
            last = [l for l in fh if l.strip()][-1]
        bt = json.loads(last).get("verdict", "—")
    except Exception:
        pass

    L = []
    L.append("# BETSFIX — Méthodologie d'analyse & sélection des pronos (par sport)")
    L.append("")
    L.append("> Écrit **automatiquement** par `tools/methodology_doc.py` (run quotidien). Objectif : voir, "
             "**sport par sport**, quand la méthode d'analyse et de sélection se stabilise (= **optimale**). "
             "Lecture seule.")
    L.append(f"> Généré le {now.strftime('%Y-%m-%d %H:%M UTC')}.")
    L.append("")
    L.append("## Méthode commune (les 3 sports)")
    L.append("- **Confidence-first** : on classe par *probabilité honnête de gagner vs cote*, pas par cote.")
    L.append("- **Seuils de jeu** : confiance **≥ 65 %** (recalibrée) · **EV ≥ +3 %** · mise **¼ Kelly** "
             "(plafond 3 % de bankroll).")
    L.append("- **Garde-fous de cote** (mesurés) : cote **< 2.00** exigée ; zone **1.70–2.00** exige "
             "**≥ 72 %** de confiance (au-delà de 2.00 = ROI négatif → écarté).")
    L.append("- **1 seul pari par match**, le plus probable, **validé par 3 agents**.")
    L.append("- **Faits ≥ 2 sources** ; enrichissement multi-sources (FotMob/ESPN/Understat/Flashscore/…).")
    L.append("- **Exclusions de marché** : **automatiques et data-driven** (un marché est écarté si n ≥ 25 "
             "ET ROI/calibration mauvais — jamais de surapprentissage).")
    L.append("")
    L.append(f"**Fiabilité de la calibration (globale)** : indice **{rel.get('index','—')}/100**, "
             f"MAE {rel.get('mae','—')}, tendance **{rel.get('trend','—')}** (n={rel.get('n','—')}). ")
    L.append(f"**Backtest de la politique (global)** : *{bt}*.")
    L.append("")
    L.append("## Qu'est-ce qu'un sport « optimal » ?")
    L.append(f"**(A) ROI positif & STABLE** (ROI > 0, drawdown max ≤ {int(_MAX_DD_PCT)} %, "
             f"≥ {_MIN_SETTLED} paris réglés) **ET (B) calibration BONNE** (verdict *good*, MAE ≤ {int(_MAX_MAE)}). "
             "Les deux ✅ = 🟢 optimal.")
    L.append("")

    for short, calname, title in SPORTS:
        stat = sf.get(short) or {}
        cal = cal_by.get(calname) or {}
        okA, okB, verdict, sclines = _scorecard(stat, cal)
        excl = sorted(analyses.excluded_markets(short)) or ["aucune"]
        L.append(f"## {title}")
        L.append(verdict)
        L.append("")
        L.append("**État mesuré (paris joués)**  ")
        L.append(f"ROI **{_fmt_pct(stat.get('roi'))}** · réussite **{stat.get('pct','—')}%** · "
                 f"**{stat.get('settled','—')}** réglés ({stat.get('won','—')}✓/{stat.get('lost','—')}✗) · "
                 f"cote moy **@{stat.get('avg_odds','—')}** · drawdown max **{stat.get('dd_pct','—')}%**")
        L.append("")
        L.append("**Calibration** (toutes prédictions, fantômes inclus)  ")
        L.append(f"MAE **{cal.get('mae','—')}** ({cal.get('verdict','—')}) · réussite réelle "
                 f"**{cal.get('win_rate','—')}%** vs confiance annoncée **{cal.get('avg_conf','—')}%** "
                 f"· n={cal.get('n','—')}")
        L.append("")
        L.append(f"**Marchés écartés (auto)** : {', '.join(excl)}")
        L.append("")
        # ROI par marché FANTÔMES INCLUS : le signal qui MÛRIT VITE (sans attendre les paris réels).
        mkts = cal.get("markets") or {}
        _rows = [(name, mg) for name, mg in mkts.items() if mg.get("roi") is not None]
        if _rows:
            L.append("**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  ")
            L.append("| Marché | n | Réussite | ROI |")
            L.append("|---|---|---|---|")
            for name, mg in sorted(_rows, key=lambda x: (x[1].get("roi") or 0)):
                flag = " 🔴" if (mg.get("roi") or 0) <= -15 else (" 🟢" if (mg.get("roi") or 0) >= 5 else "")
                L.append(f"| {name} | {mg.get('n')} | {mg.get('win_rate')}% | "
                         f"{_fmt_pct(mg.get('roi'))}{flag} |")
            L.append("")
        L.append("**Repères méthodo (ce sport)**")
        _sm = [m for m in miles if (m[4] if len(m) > 4 else "all") in ("all", short)]
        for m in _sm:
            L.append(f"- `{m[0]}` **{m[1]}** — {m[2]}")
        L.append("")
        L.append("**Scorecard d'optimalité**")
        L.extend(sclines)
        L.append("")

    L.append("---")
    L.append("*Marché privilégiés/bannis en combiné (taux mesurés) : gravés dans `COMBO_MISSION` "
             "(`tools/generate_analyses.py`). Cf. aussi `LEARNING.md` (journal des auto-révisions) et "
             "`docs/SOURCES.md` (sources & résolubilité).*")
    return "\n".join(L) + "\n"


def main():
    md = build()
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    tmp = DOC + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(md)
    os.replace(tmp, DOC)
    if "--quiet" not in sys.argv:
        print(md)
    else:
        print(f"docs/METHODOLOGIE.md régénéré ({len(md)} car.).")


if __name__ == "__main__":
    main()
