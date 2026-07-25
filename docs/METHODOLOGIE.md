# BETSFIX — Méthodologie d'analyse & sélection des pronos (par sport)

> Écrit **automatiquement** par `tools/methodology_doc.py` (run quotidien). Objectif : voir, **sport par sport**, quand la méthode d'analyse et de sélection se stabilise (= **optimale**). Lecture seule.
> Généré le 2026-07-25 07:27 UTC.

## Méthode commune (les 3 sports)
- **Confidence-first** : on classe par *probabilité honnête de gagner vs cote*, pas par cote.
- **Seuils de jeu** : confiance **≥ 65 %** (recalibrée) · **EV ≥ +3 %** · mise **¼ Kelly** (plafond 3 % de bankroll).
- **Garde-fous de cote** (mesurés) : cote **< 2.00** exigée ; zone **1.70–2.00** exige **≥ 72 %** de confiance (au-delà de 2.00 = ROI négatif → écarté).
- **1 seul pari par match**, le plus probable, **validé par 3 agents**.
- **Faits ≥ 2 sources** ; enrichissement multi-sources (FotMob/ESPN/Understat/Flashscore/…).
- **Exclusions de marché** : **automatiques et data-driven** (un marché est écarté si n ≥ 25 ET ROI/calibration mauvais — jamais de surapprentissage).

**Fiabilité de la calibration (globale)** : indice **97/100**, MAE 0.9, tendance **up** (n=5536). 
**Backtest de la politique (global)** : *garder la politique actuelle (aucun gain hors-échantillon significatif)*.

## Qu'est-ce qu'un sport « optimal » ?
**(A) ROI positif & STABLE** (ROI > 0, drawdown max ≤ 20 %, ≥ 20 paris réglés) **ET (B) calibration BONNE** (verdict *good*, MAE ≤ 5). Les deux ✅ = 🟢 optimal.

## ⚽ Football
🟠 **À AFFINER** — rentable mais calibration à resserrer

**État mesuré (paris joués)**  
ROI **+16.7%** · réussite **85%** · **99** réglés (84✓/15✗) · cote moy **@1.38** · drawdown max **2.6%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.3** (under) · réussite réelle **69%** vs confiance annoncée **67%** · n=1655

**Marchés écartés (auto)** : Corners

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Cartons | 52 | 62% | +1% |
| Double chance | 97 | 84% | +4% |
| Total +/- | 365 | 68% | +10% 🟢 |
| Total équipe | 340 | 72% | +15% 🟢 |
| Vainqueur | 108 | 69% | +29% 🟢 |
| Handicap | 185 | 75% | +36% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-19` **Corners bannis** — Les corners, le marché le plus perdant au foot, sont exclus de tous les paris (simple et combiné).
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ✅ (ROI +16.7%, drawdown max 2.6%, 99 réglés)
- **[B] Calibration bonne** : ❌ (MAE 2.3, verdict under, réussite 69% vs confiance 67%)

## 🎾 Tennis
🟠 **À AFFINER** — bien calibré (prédictions honnêtes) mais ROI/stabilité KO : la value/sélection ne convertit pas la justesse en profit

**État mesuré (paris joués)**  
ROI **-24.0%** · réussite **52%** · **27** réglés (14✓/13✗) · cote moy **@1.46** · drawdown max **30.3%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.0** (good) · réussite réelle **62%** vs confiance annoncée **63%** · n=1743

**Marchés écartés (auto)** : Jeux

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Jeux | 775 | 61% | -29% 🔴 |
| Sets | 586 | 63% | -10% |
| Vainqueur | 219 | 63% | -3% |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-07-06` marché « Jeux » **écarté** — Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant).
- `2026-07-04` marché « Sets » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI -24.0%, drawdown max 30.3%, 27 réglés)
- **[B] Calibration bonne** : ✅ (MAE 2.0, verdict good, réussite 62% vs confiance 63%)

## 🏀 Basket
🟢 **OPTIMAL** — ROI stable positif ET bien calibré

**État mesuré (paris joués)**  
ROI **+9.2%** · réussite **73%** · **22** réglés (16✓/6✗) · cote moy **@1.51** · drawdown max **11.4%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **1.6** (good) · réussite réelle **57%** vs confiance annoncée **57%** · n=1386

**Marchés écartés (auto)** : Total +/-, Vainqueur

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Total +/- | 367 | 54% | -68% 🔴 |
| Vainqueur | 182 | 59% | -2% |
| Handicap | 383 | 59% | +11% 🟢 |

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
- **[B] Calibration bonne** : ✅ (MAE 1.6, verdict good, réussite 57% vs confiance 57%)

---
*Marché privilégiés/bannis en combiné (taux mesurés) : gravés dans `COMBO_MISSION` (`tools/generate_analyses.py`). Cf. aussi `LEARNING.md` (journal des auto-révisions) et `docs/SOURCES.md` (sources & résolubilité).*
