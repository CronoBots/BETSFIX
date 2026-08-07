# BETSFIX — Méthodologie d'analyse & sélection des pronos (par sport)

> Écrit **automatiquement** par `tools/methodology_doc.py` (run quotidien). Objectif : voir, **sport par sport**, quand la méthode d'analyse et de sélection se stabilise (= **optimale**). Lecture seule.
> Généré le 2026-08-07 07:35 UTC.

## Méthode commune (les 3 sports)
- **Confidence-first** : on classe par *probabilité honnête de gagner vs cote*, pas par cote.
- **Seuils de jeu** : confiance **≥ 65 %** (recalibrée) · **EV ≥ +3 %** · mise **¼ Kelly** (plafond 3 % de bankroll).
- **Garde-fous de cote** (mesurés) : cote **< 2.00** exigée ; zone **1.70–2.00** exige **≥ 72 %** de confiance (au-delà de 2.00 = ROI négatif → écarté).
- **1 seul pari par match**, le plus probable, **validé par 3 agents**.
- **Faits ≥ 2 sources** ; enrichissement multi-sources (FotMob/ESPN/Understat/Flashscore/…).
- **Exclusions de marché** : **automatiques et data-driven** (un marché est écarté si n ≥ 25 ET ROI/calibration mauvais — jamais de surapprentissage).

**Fiabilité de la calibration (globale)** : indice **94/100**, MAE 1.9, tendance **up** (n=3347). 
**Backtest de la politique (global)** : *garder la politique actuelle (aucun gain hors-échantillon significatif)*.

## Qu'est-ce qu'un sport « optimal » ?
**(A) ROI positif & STABLE** (ROI > 0, drawdown max ≤ 20 %, ≥ 20 paris réglés) **ET (B) calibration BONNE** (verdict *good*, MAE ≤ 5). Les deux ✅ = 🟢 optimal.

## ⚽ Football
🟠 **À AFFINER** — rentable mais calibration à resserrer

**État mesuré (paris joués)**  
ROI **+17.2%** · réussite **85%** · **134** réglés (114✓/20✗) · cote moy **@1.39** · drawdown max **1.9%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **1.9** (under) · réussite réelle **68%** vs confiance annoncée **67%** · n=2730

**Marchés écartés (auto)** : Corners, Les 2 marquent

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Les 2 marquent | 173 | 53% | -67% 🔴 |
| Cartons | 52 | 62% | +1% |
| Double chance | 217 | 81% | +3% |
| Total +/- | 692 | 68% | +3% |
| Total équipe | 531 | 69% | +10% 🟢 |
| Vainqueur | 181 | 65% | +16% 🟢 |
| Handicap | 262 | 76% | +36% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-19` **Corners bannis** — Les corners, le marché le plus perdant au foot, sont exclus de tous les paris (simple et combiné).
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-07-28` marché « Les 2 marquent » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ✅ (ROI +17.2%, drawdown max 1.9%, 134 réglés)
- **[B] Calibration bonne** : ❌ (MAE 1.9, verdict under, réussite 68% vs confiance 67%)

## 🎾 Tennis
⏳ **EN COURS** — échantillon à étoffer (0/20 réglés)

**État mesuré (paris joués)**  
ROI **—** · réussite **—%** · **—** réglés (—✓/—✗) · cote moy **@—** · drawdown max **—%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **—** (—) · réussite réelle **—%** vs confiance annoncée **—%** · n=—

**Marchés écartés (auto)** : aucune

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-08-07` marché « Handicap » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-08-07` marché « Jeux » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-07-27` marché « Handicap » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-06` marché « Jeux » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-04` marché « Sets » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI —, drawdown max —%, 0 réglés — échantillon < 20)
- **[B] Calibration bonne** : ❌ (MAE —, verdict —, réussite —% vs confiance —%)

## 🏀 Basket
⏳ **EN COURS** — échantillon à étoffer (0/20 réglés)

**État mesuré (paris joués)**  
ROI **—** · réussite **—%** · **—** réglés (—✓/—✗) · cote moy **@—** · drawdown max **—%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **—** (—) · réussite réelle **—%** vs confiance annoncée **—%** · n=—

**Marchés écartés (auto)** : aucune

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-08-07` marché « Premier à X pts » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-08-07` marché « Quart-temps/MT » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-08-07` marché « Total +/- » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-08-07` marché « Total équipe » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-08-07` marché « Vainqueur » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.
- `2026-08-02` marché « Premier à X pts » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-08-02` marché « Quart-temps/MT » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-08-02` marché « Total équipe » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-22` marché « Vainqueur » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-16` marché « Vainqueur » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI —, drawdown max —%, 0 réglés — échantillon < 20)
- **[B] Calibration bonne** : ❌ (MAE —, verdict —, réussite —% vs confiance —%)

---
*Marché privilégiés/bannis en combiné (taux mesurés) : gravés dans `COMBO_MISSION` (`tools/generate_analyses.py`). Cf. aussi `LEARNING.md` (journal des auto-révisions) et `docs/SOURCES.md` (sources & résolubilité).*
