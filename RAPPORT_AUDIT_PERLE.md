# Rapport d'audit — Système perle, modèle & rentabilité BETSFIX

*Audit complet du code (sélection perle, modèle de prédiction, détection value/staking, settlement) + simulation de mise réelle. Données au 5 juin 2026.*

---

## 1. Résultat financier réel — simulation 5 €/pari

Mise plate de 5 € sur **chaque** confiance et value distincte réglée (void exclus, même comptage que le tableau de l'app) :

| Sport | Paris | Misé | Confiance | Value | **Solde net** | ROI |
|---|---|---|---|---|---|---|
| 🎾 Tennis | 43 | 215 € | −4,50 € | **+13,40 €** | **+8,90 €** | +4,1 % |
| ⚽ Foot | 102 | 510 € | −9,50 € | **−23,10 €** | **−32,60 €** | −6,4 % |
| 🏀 Basket | 6 | 30 € | +6,20 € | +5,20 € | **+11,40 €** | +38 % |
| **TOTAL** | **151** | **755 €** | **−7,80 €** | **−4,50 €** | **−12,30 €** | **−1,6 %** |

**Diagnostic chiffré :** quasi-équilibre (−1,6 %). Le tennis est rentable, le basket est anecdotique (6 paris), et **la value foot (−23,10 €) est la seule vraie fuite**. C'est LA cible n°1.

> ⚠️ Échantillons encore faibles (objectif interne : 100 paris réglés/catégorie). Ces verdicts sont **directionnels**, pas définitifs. Bonne nouvelle : aucune purge auto dans le code → tout s'accumule désormais.

---

## 2. Ce qui FONCTIONNE (à garder)

- **L'infra de mesure est excellente** (`app/tracking.py`) : Brier modèle vs Brier marché, log-loss, surconfiance, CLV, table de calibration par tranche, intervalle de Wilson, *factor breakdown* (Brier de chaque facteur). Le code pose la **bonne question** (« bat-on le marché ? ») et non la précision absolue.
- **Le modèle tennis est rentable** : mélange pondéré dominé par le **classement** (poids 0.40, régression logistique validée sur ~17k matchs), Elo par surface, forme, service/retour, H2H. Value tennis +22 % de ROI.
- **Convention P&L correcte** partout (gain = cote−1 si gagné, −1 sinon ; vérifié 0 erreur). Gestion des **void** (reports/annulations) propre, exclus des stats.
- **Ancrage au marché** des modèles annexes (simulateur tennis recalé sur le book, garde-fous gradués foot) : design réfléchi.
- **Handicaps/totaux en lignes .5** → règlement binaire honnête, pas de push ambigu (sauf basket qui gère le push correctement).

---

## 3. Pourquoi la VALUE FOOT perd (−13,6 % ROI) — causes racines

Classées de la plus probable à la plus secondaire :

1. **Edge extrait d'une bande de pur bruit.** `edge = MODEL_TRUST·(mp − imp)` avec `MODEL_TRUST=0.50` → le seuil `VALUE_THRESHOLD=0.05` exige en réalité un écart brut `mp − imp ≥ 0.10`. Mais le garde-fou `MAX_DISAGREEMENT=0.15` autorise jusqu'à 0.15. **La value foot vit donc dans `mp−imp ∈ [0.10, 0.15]`** : exactement là où un Elo-foot jeune diverge le plus du marché 1X2 (le plus efficient qui soit) — surtout par **erreur de modèle**, pas par inefficience. (`app/foot.py:57-63, 987`)

2. **Dévig proportionnel biaisé favori-longshot.** `1/cote` normalisé (`_devig3`, `foot.py:138`) **sous-estime les favoris et surestime les outsiders**. Combiné à un Poisson qui sur-attribue des chances aux outsiders, ça **gonfle les edges sur cotes longues** → paris perdants répétés. Un dévig *power/Shin* corrigerait ça.

3. **Aucune recalibration.** `CALIB_SHRINK=1.00` rend `recalibrate()` inopérant (`analysis.py:79`), et il n'y a **aucune** recalibration en foot/basket. Une proba mal calibrée le reste après le mélange `MODEL_TRUST` (mélange ≠ calibration).

4. **On parie une cote périmée, pas la clôture.** Le snapshot fige les cotes au 1er log et ne rafraîchit plus un match « notstarted » jusqu'au coup d'envoi (`foot.py:1561-1564`). L'edge réel ≤ edge mesuré, et le CLV est faussé.

5. **Aucun filtre CLV>0 ni liquidité.** Le CLV est un *thermomètre* a posteriori, jamais un *filtre* de sélection. Amicaux inclus (`MAJOR_TIDS:851`).

---

## 4. Faiblesses transversales (les 3 sports)

- **Seuils trop bas et identiques partout** : `MIN_EDGE≈0.03`, `MIN_ODDS=1.20`, `MIN_PROB=0.52` (`markets.py:387`, `foot.py:152`, `basket.py:164`). Laisse passer des paris à edge réel quasi nul.
- **Confiance triée par proba brute** (`markets.py:430`, `foot.py:711`, `basket.py:322`) → biais vers les **gros favoris à petite cote** : le taux paraît correct mais le ROI est faible, et un favori qui tombe coûte cher. Le score `proba×edge` est calculé (`foot.py:681`) mais **non utilisé** par `best_picks`.
- **Marchés faibles non filtrés dans le pool perle** : sets tennis (qui ne produit jamais `is_value=True`), annexes foot (mi-temps, corners, cartons, score exact), totaux-par-équipe basket (non ajustés à la force adverse, contrairement au foot).
- **Garde-fous incohérents entre sports** : `MIN_IMPLIED/MAX_IMPLIED` appliqués dans les `board()` mais **PAS** dans les moteurs perle basket/tennis → des favoris écrasants (cote 1.10) peuvent ressortir. Disagreement perle tennis 0.20 vs vainqueur 0.15.

---

## 5. Bugs de settlement (faussent le taux mesuré)

- **Tennis « Set Handicap » jamais réglé** (`markets.py:485` ne gère que les handicaps en *jeux*) → 2/2 perles invisibles, alors que `sets_home/away` sont dispo. On **propose un pari qu'on ne sait pas noter**.
- **Tennis « Nombre total de Tiebreaks » jamais réglé** → 1/1 invisible (dérivable du score set par set).
- **Basket : perles non réglées si le score manque** (`basket.py:1081` imbrique le règlement perle dans `if score`) → clés `perle_pnl` absentes au lieu de None. À aligner sur le pattern foot.
- Conséquence : ~10 % des perles tennis sont **systématiquement non mesurées** → le ROI affiché ne reflète que les marchés instrumentés.

---

## 6. Plan d'optimisation — priorisé par impact/risque

### 🔴 P1 — Colmater la value foot (cible directe du −23 €)
1. Monter `VALUE_THRESHOLD` foot à **0.07–0.08** **et/ou** baisser `MAX_DISAGREEMENT` à **0.10** pour supprimer la bande de bruit `[0.10,0.15]`. (`foot.py:58,63`)
2. Remplacer le **dévig proportionnel par un dévig multiplicatif/power (ou Shin)**. (`foot.py:138`, `analysis.py:219`) — tue le biais favori-longshot.

### 🟠 P2 — Qualité de sélection (tous sports)
3. Trier la confiance par **`proba×edge`** au lieu de la proba brute (réutiliser le score déjà calculé `foot.py:681`).
4. Remonter `MIN_EDGE` à **0.05** et borner la cote de confiance (`~[1.35, 3.0]`).
5. Appliquer `MIN_IMPLIED/MAX_IMPLIED` + `MAX_DISAGREEMENT=0.15` **dans les moteurs perle** (pas seulement les `board`).

### 🟡 P3 — Retirer/durcir les marchés faibles du pool perle
6. Exclure (ou edge≥0.06-0.07) : sets tennis, annexes foot (HT/corners/cartons/score exact), `team_total` basket non ajusté.

### 🟢 P4 — Modèle (gain de fond, plus long)
7. **Recalibration par segment** (surface/tour/tranche de proba — Platt/isotonic), étendue à foot/basket. Toute l'infra de mesure existe déjà dans `tracking.py`.
8. **Blending modèle/marché adaptatif** au lieu de `MODEL_TRUST` fixe (mesuré par back-test).
9. Enrichir l'Elo tennis : K adaptatif, marge de victoire (`elo_math.py:20` déjà codé mais inutilisé en tennis), régression inter-saison.
10. Calibrer empiriquement `HOME_ADV`, `SUP_PER_100`, `GOALS_TOTAL` sur l'historique du projet.

### 🔵 P5 — Settlement & logging (pour pouvoir optimiser demain)
11. Régler « Set Handicap » et « Tiebreaks » tennis (ou cesser de les proposer). Sortir le règlement perle basket du `if score`.
12. **Logger la cote de clôture par perle** (rafraîchie jusqu'au KO) → permet le **CLV par perle**, le meilleur juge d'edge rapide.
13. Logger un `kind/line/side` **structuré** côté tennis (aujourd'hui texte libre → cause des bugs de settle).
14. Stocker la **raison du None** au règlement (`marché_inconnu` / `score_manquant` / `push`) pour distinguer non-mesurable vs bug.

---

## 7. Conclusion

Le système n'est **pas cassé** : il est à l'équilibre, avec un **tennis rentable** et une **value foot qui saigne**. Les deux corrections P1 (fenêtre d'edge + dévig) visent directement la fuite. Le reste (tri par proba×edge, recalibration, settlement) consolide. L'infrastructure de mesure est déjà de qualité — il manque surtout la **boucle d'ajustement** qui réinjecte ce qui est mesuré dans les seuils.

**Prochaine action recommandée :** commencer par P1 sur le foot (faible risque, impact direct), mesurer sur les prochains paris réglés, puis dérouler P2→P5.
