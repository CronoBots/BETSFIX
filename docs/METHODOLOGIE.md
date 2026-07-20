# BETSFIX — Méthodologie d'analyse & sélection des pronos (par sport)

> Écrit **automatiquement** par `tools/methodology_doc.py` (run quotidien). Objectif : voir, **sport par sport**, quand la méthode d'analyse et de sélection se stabilise (= **optimale**). Lecture seule.
> Généré le 2026-07-20 07:32 UTC.

## Méthode commune (les 3 sports)
- **Confidence-first** : on classe par *probabilité honnête de gagner vs cote*, pas par cote.
- **Seuils de jeu** : confiance **≥ 65 %** (recalibrée) · **EV ≥ +3 %** · mise **¼ Kelly** (plafond 3 % de bankroll).
- **Garde-fous de cote** (mesurés) : cote **< 2.00** exigée ; zone **1.70–2.00** exige **≥ 72 %** de confiance (au-delà de 2.00 = ROI négatif → écarté).
- **1 seul pari par match**, le plus probable, **validé par 3 agents**.
- **Faits ≥ 2 sources** ; enrichissement multi-sources (FotMob/ESPN/Understat/Flashscore/…).
- **Exclusions de marché** : **automatiques et data-driven** (un marché est écarté si n ≥ 25 ET ROI/calibration mauvais — jamais de surapprentissage).

**Fiabilité de la calibration (globale)** : indice **97/100**, MAE 0.9, tendance **up** (n=4696). 
**Backtest de la politique (global)** : *garder la politique actuelle (aucun gain hors-échantillon significatif)*.

## Qu'est-ce qu'un sport « optimal » ?
**(A) ROI positif & STABLE** (ROI > 0, drawdown max ≤ 20 %, ≥ 20 paris réglés) **ET (B) calibration BONNE** (verdict *good*, MAE ≤ 5). Les deux ✅ = 🟢 optimal.

## ⚽ Football
🟠 **À AFFINER** — rentable mais calibration à resserrer

**État mesuré (paris joués)**  
ROI **+19.6%** · réussite **86%** · **80** réglés (69✓/11✗) · cote moy **@1.39** · drawdown max **3.0%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.0** (under) · réussite réelle **69%** vs confiance annoncée **67%** · n=1397

**Marchés écartés (auto)** : Corners

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Cartons | 38 | 61% | +1% |
| Double chance | 81 | 83% | +1% |
| Total équipe | 280 | 72% | +11% 🟢 |
| Total +/- | 316 | 68% | +13% 🟢 |
| Vainqueur | 95 | 66% | +21% 🟢 |
| Handicap | 158 | 76% | +33% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-19` **Corners bannis** — Les corners, le marché le plus perdant au foot, sont exclus de tous les paris (simple et combiné).
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ✅ (ROI +19.6%, drawdown max 3.0%, 80 réglés)
- **[B] Calibration bonne** : ❌ (MAE 2.0, verdict under, réussite 69% vs confiance 67%)

## 🎾 Tennis
🟠 **À AFFINER** — bien calibré (prédictions honnêtes) mais ROI/stabilité KO : la value/sélection ne convertit pas la justesse en profit

**État mesuré (paris joués)**  
ROI **-23.9%** · réussite **52%** · **23** réglés (12✓/11✗) · cote moy **@1.44** · drawdown max **35.6%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **1.6** (good) · réussite réelle **62%** vs confiance annoncée **63%** · n=1459

**Marchés écartés (auto)** : Jeux

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Jeux | 637 | 62% | -29% 🔴 |
| Sets | 497 | 64% | -9% |
| Vainqueur | 188 | 64% | -4% |

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
- **[A] ROI positif & stable** : ❌ (ROI -23.9%, drawdown max 35.6%, 23 réglés)
- **[B] Calibration bonne** : ✅ (MAE 1.6, verdict good, réussite 62% vs confiance 63%)

## 🏀 Basket
⏳ **EN COURS** — échantillon à étoffer (19/20 réglés)

**État mesuré (paris joués)**  
ROI **+9.9%** · réussite **74%** · **19** réglés (14✓/5✗) · cote moy **@1.5** · drawdown max **13.2%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **1.8** (good) · réussite réelle **57%** vs confiance annoncée **57%** · n=1196

**Marchés écartés (auto)** : Total +/-

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Total +/- | 317 | 56% | -68% 🔴 |
| Vainqueur | 158 | 61% | +3% |
| Handicap | 328 | 57% | +9% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-05` **Combiné = cote réelle corrélée** — La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Ajustements automatiques (ce sport)**
- `2026-07-16` marché « Vainqueur » **réintégré** — Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI +9.9%, drawdown max 13.2%, 19 réglés — échantillon < 20)
- **[B] Calibration bonne** : ✅ (MAE 1.8, verdict good, réussite 57% vs confiance 57%)

---
*Marché privilégiés/bannis en combiné (taux mesurés) : gravés dans `COMBO_MISSION` (`tools/generate_analyses.py`). Cf. aussi `LEARNING.md` (journal des auto-révisions) et `docs/SOURCES.md` (sources & résolubilité).*
