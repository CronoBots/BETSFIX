# BETSFIX — Méthodologie d'analyse & sélection des pronos (par sport)

> Écrit **automatiquement** par `tools/methodology_doc.py` (run quotidien). Objectif : voir, **sport par sport**, quand la méthode d'analyse et de sélection se stabilise (= **optimale**). Lecture seule.
> Généré le 2026-07-28 08:15 UTC.

## Méthode commune (les 3 sports)
- **Confidence-first** : on classe par *probabilité honnête de gagner vs cote*, pas par cote.
- **Seuils de jeu** : confiance **≥ 65 %** (recalibrée) · **EV ≥ +3 %** · mise **¼ Kelly** (plafond 3 % de bankroll).
- **Garde-fous de cote** (mesurés) : cote **< 2.00** exigée ; zone **1.70–2.00** exige **≥ 72 %** de confiance (au-delà de 2.00 = ROI négatif → écarté).
- **1 seul pari par match**, le plus probable, **validé par 3 agents**.
- **Faits ≥ 2 sources** ; enrichissement multi-sources (FotMob/ESPN/Understat/Flashscore/…).
- **Exclusions de marché** : **automatiques et data-driven** (un marché est écarté si n ≥ 25 ET ROI/calibration mauvais — jamais de surapprentissage).

**Fiabilité de la calibration (globale)** : indice **98/100**, MAE 0.8, tendance **up** (n=6236). 
**Backtest de la politique (global)** : *garder la politique actuelle (aucun gain hors-échantillon significatif)*.

## Qu'est-ce qu'un sport « optimal » ?
**(A) ROI positif & STABLE** (ROI > 0, drawdown max ≤ 20 %, ≥ 20 paris réglés) **ET (B) calibration BONNE** (verdict *good*, MAE ≤ 5). Les deux ✅ = 🟢 optimal.

## ⚽ Football
🟠 **À AFFINER** — rentable mais calibration à resserrer

**État mesuré (paris joués)**  
ROI **+18.1%** · réussite **86%** · **104** réglés (89✓/15✗) · cote moy **@1.39** · drawdown max **2.5%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.2** (under) · réussite réelle **69%** vs confiance annoncée **66%** · n=1844

**Marchés écartés (auto)** : Corners, Les 2 marquent

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Cartons | 52 | 62% | +1% |
| Double chance | 121 | 82% | +4% |
| Total +/- | 411 | 69% | +8% 🟢 |
| Total équipe | 370 | 71% | +15% 🟢 |
| Vainqueur | 118 | 69% | +32% 🟢 |
| Handicap | 208 | 75% | +36% 🟢 |

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
- **[A] ROI positif & stable** : ✅ (ROI +18.1%, drawdown max 2.5%, 104 réglés)
- **[B] Calibration bonne** : ❌ (MAE 2.2, verdict under, réussite 69% vs confiance 66%)

## 🎾 Tennis
🔴 **À CORRIGER** — ni rentable ni bien calibré

**État mesuré (paris joués)**  
ROI **-22.6%** · réussite **53%** · **30** réglés (16✓/14✗) · cote moy **@1.45** · drawdown max **27.3%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **1.9** (over) · réussite réelle **61%** vs confiance annoncée **63%** · n=1993

**Marchés écartés (auto)** : Handicap, Jeux

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Jeux | 896 | 60% | -29% 🔴 |
| Sets | 665 | 63% | -9% |
| Vainqueur | 244 | 60% | -5% |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-07-27` marché « Handicap » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-06` marché « Jeux » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-04` marché « Sets » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI -22.6%, drawdown max 27.3%, 30 réglés)
- **[B] Calibration bonne** : ❌ (MAE 1.9, verdict over, réussite 61% vs confiance 63%)

## 🏀 Basket
🟢 **OPTIMAL** — ROI stable positif ET bien calibré

**État mesuré (paris joués)**  
ROI **+9.2%** · réussite **73%** · **22** réglés (16✓/6✗) · cote moy **@1.51** · drawdown max **11.4%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.1** (good) · réussite réelle **56%** vs confiance annoncée **57%** · n=1531

**Marchés écartés (auto)** : Total +/-, Vainqueur

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Total +/- | 409 | 54% | -71% 🔴 |
| Vainqueur | 205 | 59% | -6% |
| Handicap | 425 | 58% | +11% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-07-22` marché « Vainqueur » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-16` marché « Vainqueur » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ✅ (ROI +9.2%, drawdown max 11.4%, 22 réglés)
- **[B] Calibration bonne** : ✅ (MAE 2.1, verdict good, réussite 56% vs confiance 57%)

---
*Marché privilégiés/bannis en combiné (taux mesurés) : gravés dans `COMBO_MISSION` (`tools/generate_analyses.py`). Cf. aussi `LEARNING.md` (journal des auto-révisions) et `docs/SOURCES.md` (sources & résolubilité).*
