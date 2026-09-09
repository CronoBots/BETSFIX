# Note de conception — VALUE SCREENER (crible de value large)

> **Statut : PROJET FUTUR** — à lancer **après** la fin de la migration API-Football (iProyal coupé, VPS).
> Objectif : trouver de l'edge/value sur une **liste de matchs bien plus large** que le slate actuel, sans
> exploser le coût. C'est le **levier de croissance n°1** du produit (plus de paris de qualité → plus d'abonnés).
> Rédigé le 2026-09-09. Cf. mémoire `wip-current-task` § migration + `optimization-playbook`.

## 1. Le problème que ça résout

Le moteur de sélection (Confiance/Value) est **à son optimum mesurable** (prouvé 3×). Le seul levier restant pour
**plus** de paris de qualité n'est PAS un réglage de seuil — c'est la **COUVERTURE**. Aujourd'hui on ne scanne
qu'un petit slate (matchs sélectionnés par Unibet), parce que le **scraping coûtait cher** (Go iProyal, blocages,
lenteur). La value par match est rare (~2 matchs sur 34 ont une EV+) → **plus de matchs analysés = plus de value
en absolu**, mécaniquement.

**Ce que la migration débloque** : API-Football donne `/fixtures?date` (tous les matchs du monde) + `/odds`
(ancre **Pinnacle** sharp + **Unibet** + 30 books) pour un **quota plat** (Pro 7500 req/j). La donnée n'est plus
le facteur limitant.

## ⚠️ 2bis. PROTOTYPE v1 TESTÉ 2026-09-09 — l'hypothèse « stage-1 = sharp×Unibet » est FAUSSE
`tools/value_screener.py` (lecture seule) exécuté sur le slate du jour : sur 70 matchs à venir, **16 ont
Pinnacle+Unibet**, et **0 candidat value** (EV `sharp×Unibet ≥ +5%`). Raison : **Pinnacle ≈ Unibet (marchés
efficients)** — Unibet ne bat quasi jamais Pinnacle de +5% sur les marchés cœur. → **Notre value ne vient PAS
d'un arbitrage Unibet-vs-Pinnacle**, elle vient du **modèle de Claude** (fantômes) qui diverge du marché. Donc le
crible cheap « sharp arbitrage » ne surface PAS notre value. **CORRECTION du design** : le stage-1 cheap doit être
un **MODÈLE** (pas un arbitrage). Piste n°1 = **/predictions (Poisson) d'API-Football** comme proxy gratuit de
« où un modèle diverge du marché » → flague les candidats → Claude confirme (stage-2). À tester. Sinon, la
couverture élargie = plus d'analyses Claude (coût qui scale, borné par le nb de matchs criblés).

**PROXY POISSON TESTÉ 2026-09-09 → INUTILISABLE.** Comparé sharp-EV vs poisson-EV sur 26 matchs (Pinnacle+Unibet+
1X2) : sharp flague **0** (efficient), Poisson flague **26/26** (bruit total). Les probas `/predictions.percent`
d'API-Football sont **peu fiables/plates** (Barça-Feyenoord : Feyenoord donné 33% @25.0 → « +725% EV » alors que
sharp=3% ; multiples 45%/50% plats). → **Poisson NON viable comme stage-1.**

### ⚖️ VERDICT DÉFINITIF (2026-09-09) — pas de raccourci cheap
Les DEUX proxies cheap échouent : **sharp = le marché** (trop efficient, 0 candidat) ; **Poisson API-Football =
bruit** (probas plates, tout flaggé faux). **La seule proba fiable qui trouve la VRAIE value = l'analyse Claude**
(fantômes). Conséquences : (1) l'**entonnoir cheap stage-1 sans-LLM N'EXISTE PAS** avec les signaux dispo ; (2)
**élargir la couverture = plus d'analyses Claude** (coût ∝ nb de matchs — à borner/prioriser) ; (3) c'est aussi la
**MOAT** de BETSFIX (edge = jugement Claude multi-sources, pas un arbitrage mécanique copiable). Le sharp peut au
mieux **PRIORISER** quels matchs valent une analyse Claude (pas les trouver). Ne PAS bâtir un funnel cheap illusoire.

## 2. L'insight clé : la value se détecte SANS LLM

L'analyse Claude (génération des fantômes + « pourquoi ») est la partie **chère et lente**. Mais on n'en a pas
besoin sur des centaines de matchs pour **repérer** la value : l'EV se calcule à partir des seules cotes.

```
EV(issue) = proba_sharp(Pinnacle dé-viggé) × cote_Unibet − 1
EV > 0  ⇒  Unibet « bat » le sharp sur cette issue  ⇒  candidat value
```

C'est exactement ce que fait déjà `CONSENSUS SHARP` dans `generate_analyses` — mais **enfoui dans la boucle
Claude**. Il suffit de le **sortir en amont** et de le faire tourner sur tout le slate.

## 3. Architecture — un ENTONNOIR

```
┌─ ÉTAGE 1 — CRIBLE LARGE (sans Claude, ~centimes de quota) ──────────────────────┐
│  Pour TOUS les matchs du jour ayant ancre sharp + cotes Unibet :                │
│   • EV = proba_sharp × cote_Unibet − 1  (par issue : 1X2, DC, O/U, TEAMTOT…)     │
│   • 2e signal : /predictions (Poisson) d'API-Football, sans LLM                  │
│   • gate qualité : ligue avec ancre sharp fiable + données suffisantes           │
│  → sort une liste RANKÉE de ~10-30 CANDIDATS (EV+ concordante sharp∩prédiction)  │
└──────────────────────────────────────────────────────────────────────────────┘
                                   │  (seulement les candidats)
┌─ ÉTAGE 2 — ANALYSE PROFONDE (Claude, coût borné) ──────────────────────────────┐
│  claude -p sur les candidats SEULEMENT → fantômes + « pourquoi » + pièges       │
│  (blessés, compos, contexte) via l'enrichissement API-Football hybride           │
└──────────────────────────────────────────────────────────────────────────────┘
                                   │
┌─ ÉTAGE 3 — SÉLECTEURS MÉCANIQUES (inchangés) ──────────────────────────────────┐
│  confidence_pick / value_pick sur les candidats analysés → paris joués          │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Le coût LLM reste borné** (Claude ne tourne que sur ~10-30 candidats, comme aujourd'hui) alors que la
**couverture explose** (crible sur des centaines de matchs).

## 4. Le quota n'est pas un frein (maths)

- Pro = **7500 req/j**, 300/min.
- Crible large : `/fixtures?date` = 1 appel (tous les matchs) ; `/odds` par match. Cribler ~300 matchs ≈
  **300-600 appels** (odds + éventuellement predictions). → **< 10 %** du quota journalier.
- Étage 2 (Claude) : ~10-30 analyses = même ordre qu'aujourd'hui.
- **Conclusion : on peut cribler tout le programme mondial du jour sans s'approcher du plafond.**

## 5. Garde-fous (NON négociables)

1. **Protéger le phare** : le crible large nourrit surtout la **Value**. La **Confiance reste stricte**
   (seuils inchangés) — **aucune dilution** du taux de réussite « pari simple foot ». Cf. `protect-foot-simple-success-rate`.
2. **Gate qualité data** : ne cribler que les matchs avec **ancre sharp fiable** (verrou `no_sharp` déjà en place)
   + données suffisantes. Les ligues sans xG/blessés/règlement fiable sont **exclues** ou marquées « données dégradées ».
3. **Fiabilité du règlement** : vérifier que les ligues exotiques se **règlent** bien via API-Football **avant**
   d'y engager du ROI (auditer avec `apifootball_score_audit` sur l'univers élargi).
4. **Re-valider les seuils sur l'univers élargi** : les seuils Value/Confiance ont été backtestés sur le slate
   ACTUEL. Les marchés obscurs sont plus souvent « soft » (plus de value) MAIS plus bruités → **backtester le
   crible sur le nouvel univers** (fantômes) avant de publier. Cf. `optimization-playbook-backtest-workflow-monitor`.
5. **Anti-abstention-survivante** : garder la discipline 3-couches (Affichage/Stats/Calibration) — les nouveaux
   matchs criblés qui n'aboutissent pas restent des **abstentions/fantômes** (calibration), pas du bruit publié.

## 6. Plan de validation (shadow-first, comme la migration)

1. Écrire le crible en **lecture seule** : sur le slate du jour élargi, produire la liste rankée + EV, **sans
   rien publier**. Comparer aux candidats trouvés par le flux actuel (le crible doit au moins retrouver les mêmes).
2. Faire tourner en **shadow quelques jours** : mesurer combien de candidats value EN PLUS il sort, et backtester
   leur profil (fantômes) → EV+ réelle ou bruit ?
3. Si le profil tient (taux de réussite/ROI conformes), **brancher l'étage 2** (Claude sur les candidats en plus)
   derrière un flag, puis publier progressivement (Value d'abord, jamais la Confiance sans validation dédiée).

## 7. Prérequis (avant de lancer)

- ✅ Migration API-Football : règlement + sharp + cotes LIVE (fait 2026-09-09).
- ⏳ Enrichissement hybride armé (pour l'étage 2 sur les nouveaux matchs).
- ⏳ iProyal coupé + VPS (pour que le crible large tourne hors-PC, sans contrainte proxy).
- ⏳ Confirmer la fiabilité règlement sur les ligues qu'on veut ajouter.

## 8. Fichiers pressentis

- `tools/value_screener.py` — étage 1 (crible large, lecture seule au départ).
- Réutilise : `app/apifootball.py` (`resolve_fixture`/`raw_odds`/`sharp_anchor`/`unibet_omap`/`predictions`),
  `app/confidence_pick.py`, `app/value_pick.py` (sélecteurs inchangés), `app/backtest.py` (validation).
- Sortie shadow : `data/value_screener/<date>.json`.

---
**TL;DR** : oui, c'est faisable et c'est le levier de croissance n°1. La clé = un **entonnoir** (crible EV
sans-LLM sur tout le programme → Claude seulement sur les candidats). Le quota API-Football le permet largement.
À lancer une fois la migration finie, en shadow-first, en protégeant le phare.
