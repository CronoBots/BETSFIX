# BETSFIX — Méthodologie d'analyse & sélection des pronos (par sport)

> Écrit **automatiquement** par `tools/methodology_doc.py` (run quotidien). Objectif : voir, **sport par sport**, quand la méthode d'analyse et de sélection se stabilise (= **optimale**). Lecture seule.
> Généré le 2026-07-06 22:55 UTC.

## Méthode commune (les 3 sports)
- **Confidence-first** : on classe par *probabilité honnête de gagner vs cote*, pas par cote.
- **Seuils de jeu** : confiance **≥ 65 %** (recalibrée) · **EV ≥ +3 %** · mise **¼ Kelly** (plafond 3 % de bankroll).
- **Garde-fous de cote** (mesurés) : cote **< 2.00** exigée ; zone **1.70–2.00** exige **≥ 72 %** de confiance (au-delà de 2.00 = ROI négatif → écarté).
- **1 seul pari par match**, le plus probable, **validé par 3 agents**.
- **Faits ≥ 2 sources** ; enrichissement multi-sources (FotMob/ESPN/Understat/Flashscore/…).
- **Exclusions de marché** : **automatiques et data-driven** (un marché est écarté si n ≥ 25 ET ROI/calibration mauvais — jamais de surapprentissage).

**Fiabilité de la calibration (globale)** : indice **95/100**, MAE 1.8, tendance **up** (n=2391). 
**Backtest de la politique (global)** : *garder la politique actuelle (aucun gain hors-échantillon significatif)*.

## Qu'est-ce qu'un sport « optimal » ?
**(A) ROI positif & STABLE** (ROI > 0, drawdown max ≤ 20 %, ≥ 20 paris réglés) **ET (B) calibration BONNE** (verdict *good*, MAE ≤ 5). Les deux ✅ = 🟢 optimal.

## ⚽ Football
🟠 **À AFFINER** — rentable mais calibration à resserrer

**État mesuré (paris joués)**  
ROI **+12.2%** · réussite **81%** · **42** réglés (34✓/8✗) · cote moy **@1.39** · drawdown max **4.8%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.7** (under) · réussite réelle **69%** vs confiance annoncée **67%** · n=742

**Marchés écartés (auto)** : Corners

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Double chance | 43 | 84% | -4% |
| Cartons | 12 | 50% | +1% |
| Total équipe | 173 | 72% | +2% |
| Total +/- | 174 | 68% | +11% 🟢 |
| Vainqueur | 56 | 71% | +18% 🟢 |
| Handicap | 67 | 76% | +35% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-19` **Corners bannis** — Les corners, le marché le plus perdant au foot, sont exclus de tous les paris (simple et combiné).
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ✅ (ROI +12.2%, drawdown max 4.8%, 42 réglés)
- **[B] Calibration bonne** : ❌ (MAE 2.7, verdict under, réussite 69% vs confiance 67%)

## 🎾 Tennis
🟠 **À AFFINER** — bien calibré (prédictions honnêtes) mais ROI/stabilité KO : la value/sélection ne convertit pas la justesse en profit

**État mesuré (paris joués)**  
ROI **-33.8%** · réussite **45%** · **20** réglés (9✓/11✗) · cote moy **@1.44** · drawdown max **40.9%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **2.2** (good) · réussite réelle **62%** vs confiance annoncée **64%** · n=808

**Marchés écartés (auto)** : Jeux

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Jeux | 312 | 61% | -29% 🔴 |
| Sets | 306 | 64% | -12% |
| Vainqueur | 121 | 61% | -6% |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI -33.8%, drawdown max 40.9%, 20 réglés)
- **[B] Calibration bonne** : ✅ (MAE 2.2, verdict good, réussite 62% vs confiance 64%)

## 🏀 Basket
⏳ **EN COURS** — échantillon à étoffer (16/20 réglés)

**État mesuré (paris joués)**  
ROI **+4.2%** · réussite **69%** · **16** réglés (11✓/5✗) · cote moy **@1.52** · drawdown max **15.7%**

**Calibration** (toutes prédictions, fantômes inclus)  
MAE **3.9** (good) · réussite réelle **58%** vs confiance annoncée **60%** · n=557

**Marchés écartés (auto)** : Total +/-

**ROI par marché (fantômes inclus — mûrit sans attendre les paris réels)**  
| Marché | n | Réussite | ROI |
|---|---|---|---|
| Total +/- | 157 | 55% | -64% 🔴 |
| Vainqueur | 86 | 64% | +2% |
| Handicap | 163 | 60% | +6% 🟢 |

**Repères méthodo (ce sport)**
- `2026-06-09` **Seuil ≥65 %** — Aucun pari n'est retenu sous 65 % de confiance honnête.
- `2026-06-16` **1 pari/match** — Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.
- `2026-06-26` **Combinés calibrés** — Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.
- `2026-07-06` **Combiné = pari désigné** — Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.

**Scorecard d'optimalité**
- **[A] ROI positif & stable** : ❌ (ROI +4.2%, drawdown max 15.7%, 16 réglés — échantillon < 20)
- **[B] Calibration bonne** : ✅ (MAE 3.9, verdict good, réussite 58% vs confiance 60%)

---
*Marché privilégiés/bannis en combiné (taux mesurés) : gravés dans `COMBO_MISSION` (`tools/generate_analyses.py`). Cf. aussi `LEARNING.md` (journal des auto-révisions) et `docs/SOURCES.md` (sources & résolubilité).*
