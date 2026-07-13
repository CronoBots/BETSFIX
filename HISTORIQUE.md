# Historique des actions — BETSFIX

> Journal lisible de **chaque** changement/action (complément de l'historique git).
> **Règle de travail (demande user 2026-07-02) — À TOUJOURS RESPECTER :**
> 1. **AUCUNE RÉGRESSION.** Avant chaque changement : identifier ce qui pourrait être affecté
>    (fonctions **partagées**, logique **sport-aware**, règlement, affichage, calibration…).
>    Après : re-tester (AST, imports, endpoints, tests ciblés) et confirmer que rien d'autre n'a cassé.
> 2. **TOUT JOURNALISER** ici : quoi · pourquoi · fichiers · **vérif de non-régression faite** · résultat.

## Format
`YYYY-MM-DD — <action>` — pourquoi · fichiers · **régression vérifiée** · résultat

---

## 2026-07-11 — Combiné du jour : analyse dédiée par jambe + robustesse règlement

**Quoi** (demande user : « toutes les jambes du combiné du jour doivent être analysées comme les paris à
jouer et les provisoires » + « vérifie les bugs possibles + le règlement des terminés, ex. Espagne-Belgique ») :

1. **Analyse DÉDIÉE par jambe** (`generate_analyses.py:_analyze_combo_legs`) : en fin de scan, UN appel Claude
   produit une justification chiffrée PAR jambe (à partir des faits de chaque match) + une SYNTHÈSE. Stocké
   `leg['why']` / `combo['synth']`. N'analyse qu'1×/jour (figé après publication). Best-effort (jamais de
   crash si l'appel échoue). Validé sur appel RÉEL : chaque jambe a une analyse pro chiffrée (ex. « Connecticut
   amputé de 3 cadres, Golden State 16-7… Pinnacle 70 % »).
2. **Affichage** (`web.combo_legs_html(expandable=True)`) : dans l'onglet Stats, chaque jambe DOTÉE d'un `why`
   devient un `<details>` CLIQUABLE qui déplie sa justification ; la synthèse coiffe la carte. Bandeau
   accueil/Live = aperçu non cliquable (mène à Stats, pas de `<details>` dans un `<a>`).
3. **Règlement — jambe IRRÉCUPÉRABLE = VOID** (2 fixes) :
   - `settle_analyst` (combinés same-match) : une jambe à CODE VIDE (non dérivable, ex.
     « 3-Way Handicap 1ère MT (1-0) ») sur un match FINI est voidée IMMÉDIATEMENT (≠ donnée temporairement
     manquante qui attend 3 j). **Débloque Espagne-Belgique** (terminé 2-1) → GAGNÉ (cote eff. 2.11).
   - `combo_daily.settle_pending` : score trouvé mais code non réglable → void la jambe ; borne 8 essais si
     match introuvable. Le combiné du jour ne peut plus rester bloqué « en attente ».

**Vérifié** : 0 match terminé non réglé (pick + combiné) ; selfcheck **12 ✅** (cohérence combiné↔jambes OK) ;
compteur ROI **monotone 110/105 INCHANGÉ** ; tests (analyse leg + void irrécupérable). Le combiné du jour
reste 100 % hors ROI.

## 2026-07-11 — Provisoire ANALYSÉ et cohérent + reset du tracker

**Quoi** (retour user : « le provisoire est Victoire Juventude mais l'analyse dit qu'il n'y a pas de pari à
jouer — c'est incohérent ; le provisoire doit être analysé comme un pari à jouer, juste hors ROI ») :

**Cause** : en abstention le tableau « Paris classés » est VIDE (le prompt dit « mieux vaut vide + SKIP »),
donc `_provisional_pick` retombait sur le REPLI favori 1X2 BRUT — décorrélé du raisonnement (« Victoire
favori » vs « ne joue pas le favori, l'angle le plus solide serait un under »).

**Fix (0 impact sur les paris à jouer / le ROI)** :
1. Prompt (`generate_analyses.py`) : nouvelle section `## 🧪 Pari provisoire` OBLIGATOIRE dès un SKIP =
   le MEILLEUR angle que l'analyste a lui-même pointé (jamais un favori par défaut), analysé aussi
   sérieusement qu'un vrai pari + `PROV: <CODE>`.
2. `_provisional_pick` : lit cette section EN PRIORITÉ ; **repli favori brut SUPPRIMÉ** (plus jamais de
   provisoire incohérent — si l'analyste n'a rien désigné → None → match caché).
3. Affichage (`analyses.reasoning_html`) : montre « 🧪 Le pari provisoire » (l'analyse cohérente) en
   priorité ; `_structured` l'exclut (pas de doublon) ; `PROV:` retiré via `_strip`. Description du bloc
   Stats mise à jour (« favoris à cote courte » → « le meilleur angle indicatif analysé »).
4. **Tracker RÉINITIALISÉ** (choix user) : `data/provisional_track.json` vidé (sauvegarde `.pre-reset.bak`)
   car il mélangeait ~10 favoris bruts (ancienne méthode) + ~11 analysés -> le ROI provisoire n'était pas
   représentatif. Repart propre, se re-remplit cohéremment aux prochains scans.

**Validé** : re-scan complet -> **5/5 abstentions ont une section 🧪 cohérente** (Norvège-Angleterre → Plus
de 2.5 buts ; Giustino → au moins un set hand.+1.5 ; LA Sparks → moins de 92.5 pts…), 0 favori brut, le modèle
obéit à 100 % ; `reasoning_html` les affiche ; selfcheck **12 ✅** ; ROI réel **monotone 110/105 INCHANGÉ**.

## 2026-07-11 — Analyse des provisoires de nouveau visible (raisonnement restitué)

**Quoi** (retour user : « de nouveau aucune analyse des paris provisoires visible ») : déplier un provisoire
ne montrait que les barres « Cotes & chances » + « Informations » (les faits), SANS le raisonnement.

**Cause** : pour une abstention, la section « 📊 Paris classés » du `.md` = « *(aucun pari retenu — SKIP)* »
-> `bets_html` la filtre (vide) ; ET `_structured`/`render` RETIRE la section « 🎯 Le pari à jouer » (le
verdict/raisonnement) car pour un pari à jouer elle est distribuée SOUS les paris (`_verdict_notes`). Une
abstention n'ayant pas de paris affichés, ce raisonnement (1236 c : pourquoi le SKIP, l'angle le plus solide)
disparaissait complètement.

**Fix** : `analyses.reasoning_html(sport, id)` rend la section « 🎯 Le pari à jouer » dans un bloc déplié ;
ajouté au corps du provisoire (`app/web.py:_programme_items`) entre les paris et les faits. NE PAS l'ajouter
aux cartes à-jouer (doublon avec les notes par pari).

**Régression vérifiée** : provisoire Juventude affiche le raisonnement (« L'analyse du pari » + SKIP assumé)
+ les faits ; **265 tests** ; selfcheck **12 ✅** ; ROI **monotone 110/105 INCHANGÉ** (affichage seul).

## 2026-07-11 — Fin des cartes « Analysé · pas de pari conseillé » (publié devenu abstention)

**Quoi** (retour user : « pourquoi garder le match pas de pari conseillé ? ») : Juventude–Vila Nova
s'affichait « Analysé · pas de pari conseillé » alors qu'il A un provisoire (« Victoire Juventude-RS »
@1.74). Cause : le match avait été PUBLIÉ (get_prono) à un scan antérieur puis est devenu une ABSTENTION
(bets vidés au rescan). La règle « publié = affiché » (`list_for`, `get_prono AND not settled`) le gardait
au board -> `_sport_row` « pas de pari conseillé », et son provisoire était dédoublonné (déjà au board).

**Fix** (`app/analyses.py:list_for`) : un match publié+non réglé n'est gardé au board QUE s'il reste un
pari à AFFICHER (`retained_bet(for_history=True) is not None`). Si les bets ont été vidés (abstention pure),
il SORT de `list_for` -> son PROVISOIRE doré l'affiche (comme les autres abstentions, ex. NY Knicks). Le cas
« publié + pari encore figé » (Auger-Aliassime–Djokovic, [[published-pick-frozen-consistency]]) reste gardé
car `retained_bet(for_history=True)` renvoie le pari. Aucune perte : le match non compté (stat_bet vide)
reste sur disque + calibré (fantômes).

**Régression vérifiée** : Juventude hors `list_for` -> affiché en provisoire doré (mc-prov-tag) ; 5 tests
published/frozen/provisional OK ; **265 tests** ; selfcheck **12 ✅** ; compteur ROI **monotone 110/105
INCHANGÉ**.

## 2026-07-10 — Cohérence d'affichage : provisoires & combiné du jour = comme les paris à jouer

**Quoi** (demande user : « pourquoi des présentations différentes ? je veux les provisoires et le combiné du
jour aussi complets que les paris à jouer, dans les onglets sport ET Live ») :

1. **Provisoires = carte aussi complète qu'un pari à jouer** (`app/web.py:_programme_items`) : le corps
   déplié avait déjà barres « Cotes & chances » + tableau des paris + analyse ; il manquait le SCOREBOARD
   LIVE DÉTAILLÉ (sets/quart-temps/horloge). Ajouté via `_live_scoreboard(...)` en tête du corps quand le
   match est live (barres masquées en live, comme `_sport_row`). Le badge de tête montre déjà « 🟢 <score> ».
   Le marquage DORÉ « provisoire · hors ROI » est PRÉSERVÉ (un provisoire n'est pas un pari de value —
   ne pas confondre avec le vert « à jouer », cf. [[provisional-bet-per-match]]).
2. **Combiné du jour = carte dédiée enrichie sur accueil + Stats + LIVE** (choix user AskUserQuestion) :
   rendu des jambes UNIFIÉ dans `app/web.py:combo_legs_html` (badge résultat W/L/N/⏳ + SCORE LIVE 🟢 par
   jambe, ou score final si réglée) — utilisé par le bandeau accueil (`_combo_daily_banner`), la carte Stats
   (`routers/web._combo_daily_card` via alias `web.combo_legs_html`) ET l'onglet Live (bandeau ajouté en tête
   de `render_directs`). Cache live réchauffé en continu par le warmer -> score frais partout.

**Pourquoi la différence existait** : les provisoires et le combiné sont ISOLÉS du ROI (stockés à part, hors
`list_for`) -> je leur avais fait un rendu simplifié séparé. Corrigé : même richesse d'affichage, isolation
ROI intacte.

**Régression vérifiée** : AST OK ; combiné présent sur accueil+Live+Stats (rendu unifié) ; scoreboard
provisoire ajouté (badge « 🟢 6-4 0-0 » sur Sinner en cours) ; **265 tests** ; selfcheck **12 ✅** ; compteur
ROI **monotone 110/105 INCHANGÉ** (affichage seul, 0 impact ROI/stats/calibration).

## 2026-07-10 — Score EN DIRECT sur les cartes provisoires (onglet Live)

**Quoi** (demande user) : les cartes provisoires « en cours » (onglet Live) n'affichaient pas le score en
direct, contrairement aux vraies cartes. Fix (`app/web.py:_programme_items`) : on capture l'état live
(`match_select.live_state_for`, déjà appelé pour `_is_live`) et on en extrait le score via `live_fields`
(buts/points/sets, AUCUN réseau — cache). Le badge live passe de « 🟢 en cours » à « 🟢 <score> » (+ horloge
foot/basket si dispo), et retombe sur « 🟢 en cours » tant que le score n'est pas encore capté. Le cache live
est chaud dans `directs_page` (`fetch_live_odds` avant `_programme_items`) -> score frais.

**Régression vérifiée** : AST OK ; `/directs` affiche « 🟢 2-3 » sur la carte provisoire (Sinner-Djokovic en
cours) ; accueil/directs 200 (cache froid -> `live_fields`={} -> badge « en cours », pas de crash) ; 44 tests
(web/provisional/combo) OK. Aucun impact ROI (affichage seul).

## 2026-07-10 — Graphes provisoires + combiné du jour & FIX compteur bloqué

**Quoi** (demande user) : ajouter une courbe d'équité aux blocs « Paris provisoires » et « Combiné du jour »
(onglet Stats), comme les blocs Simples/Combinés. + retour user : « lors de l'animation les chiffres se
bloquent dans les graphiques » (ex. SIMPLES affichait « 2 % réussite · 3 paris » au lieu de 47 % · 66).

1. **Graphes d'équité** : `provisional.equity_curve()` + `combo_daily.equity_curve()` = profit cumulé
   (mise à plat 1 u) des réglés, ordonné, démarrant à 0. Rendus via `web._hero_chart(pts, uid)` (même
   composant animé vert/rouge que les Simples) dans `_provisional_card` (uid=prov) et `_combo_daily_card`
   (uid=combod), affichés dès 2 réglés (`len(curve) >= 3`). Snapshot partagé (compteur == liste == courbe).
2. **FIX compteur qui se fige** (`app/web.py` `cnt`) — CAUSE : `cnt` lisait sa cible depuis `textContent`
   À CHAQUE appel ; or `_sxAnim` (déclenché plusieurs fois : fin splash + tab-show + filet) fait
   `_c=0; cnt()` pour REJOUER l'anim -> s'il rejouait PENDANT la montée (« 2 »), il relisait « 2 » comme
   nouvelle cible -> figé à 2. Fix : cible MÉMORISÉE dans `nd._tv` (capturée 1×), toujours réutilisée ->
   un rejeu repart de la VRAIE valeur. Corrige aussi les KPI des nouvelles cartes (mêmes `.sx-kpi>b`).

**Régression vérifiée** : AST OK ; courbe provisoire réelle [0→1.66→1.02] (7 réglés) ; graphe combiné caché
tant que < 2 réglés ; `nd._tv` présent dans le bundle JS ; 13 tests (provisional+combo) OK. Aucun impact ROI
(equity_curve = info seule, lecture seule).

## 2026-07-10 — Combiné multisport DU JOUR (info seule, hors ROI)

**Quoi** (demande user) : chaque jour, UN seul combiné multisport reprenant les paris LES PLUS PROBABLES de
tous les matchs analysés, cote ≥ 1.9, taux de réussite maximal (mélange sports ET types de paris). Choix
user (AskUserQuestion) : **hors ROI réel** (suivi « info seule » d'abord, comme les provisoires) + **site +
Telegram** aux abonnés.

- **Nouveau module `app/combo_daily.py`** (calqué sur `provisional.py`, TOTALEMENT isolé : écrit seulement
  `data/combo_daily_track.json`, ne touche jamais sidecars/stat_bet/calibration/list_for) :
  - `pick_combo()` = moteur d'optimisation : MAXIMISE le produit des probabilités sous contrainte produit
    des cotes ≥ 1.9, ≤ 1 jambe/match, 2..5 jambes. Glouton par efficacité `log(cote)/(−log(prob))` +
    raffinement (retrait des jambes superflues + swaps). Validé sur cas synthétiques.
  - `_candidates_for_day()` = jambes des matchs À VENIR du jour, depuis `shadow`+`bets`, marchés en LISTE
    BLANCHE fiable (WIN/DC/OVER/UNDER/TEAMTOT/SET*/SHOTSOT/TOTGAMES… ; cf. COMBO_MISSION), prob ≥ 60 %.
    ⚠️ le code est RE-DÉRIVÉ du libellé via `code_from_pick` (un fantôme « Tiebreaks +0.5 » a l'ancien code
    générique `OVER 0.5` qui réglerait un total de BUTS = FAUX) → règlement correct garanti.
  - `settle_pending()` (branché reconcile.py après les provisoires) : règle les jambes (Flashscore+LiveScore
    +`settle_pick`), tranche lost si ≥1 perdue / won si toutes gagnées / void si toutes push.
  - `stats()`/`entries()`/`today()` sur snapshot partagé (compteur == liste), `telegram_text()` (HTML échappé).
- **Scan** (`generate_analyses.py` fin de `main()`) : construit + `record_daily` (figé dès l'envoi = published
  frozen) + notif Telegram `notify.send`. **Reconcile** : `settle_pending` branché.
- **Affichage** : bandeau doré « 🎯 Combiné du jour » en TÊTE de l'accueil (`web._combo_daily_banner`,
  cliquable → Stats) + carte détaillée dans l'onglet Stats (`routers/web._combo_daily_card` : jambes du jour
  + perf info-seule + historique), à côté du bloc provisoires.

**Résultat (état réel du jour)** : combiné 2026-07-10 = cote 2.18 · 41 % · 3 jambes multisport (foot DC 1X +
foot total équipe + tennis « au moins un set »). Le taux montera au scan du matin (beaucoup plus de matchs).

**Régression vérifiée** : AST OK (5 fichiers) ; **263 tests** (8 nouveaux : moteur cote≥1.9/≤1 par match/min 2
jambes/irréalisable, tranchage won/lost/void, cohérence stats==liste, telegram échappé) ; selfcheck **12 ✅** ;
compteur ROI **monotone 110/105 INCHANGÉ** (combiné du jour 100 % isolé, hors ROI/stats/calibration) ; rendu
accueil + Stats OK. Mémoire [[combo-daily-multisport]] créée.

## 2026-07-10 — Stats « fausses 1x sur 2 » + compteur provisoire ≠ liste

**Quoi** (retour user, 3 captures) : le bloc provisoires annonçait « 7 suivis / 3 réglés / 4 en attente »
(+ « cote moyenne 0.95 » impossible) alors que la LISTE en dessous contenait 11 entrées ; et les stats
générales étaient « pas les bonnes 1 fois sur 2 ». Diagnostic : `stats_full`/`provisional` sont
DÉTERMINISTES (vérifié : 5 appels stables) → ce n'était pas un calcul faux mais **deux problèmes de
cohérence/cache** :

1. **Compteur provisoire ≠ liste** (`app/provisional.py` + `app/routers/web.py:_provisional_card`) :
   `_provisional_card` appelait `stats()` PUIS `entries()` = **deux `_load()` séparés** du track. Si le scan
   (`record()`) ou le règlement (`settle_pending()`) réécrivait `provisional_track.json` ENTRE les deux, le
   compteur et la liste tombaient de part et d'autre de l'écriture → divergence (7 vs 11). Fix : `load()`
   public + `stats(d)`/`entries(d)` acceptent un **snapshot partagé** ; `_provisional_card` prend UN snapshot
   et le passe aux deux → compteur == liste TOUJOURS.
2. **Stats « 1x sur 2 »** (`app/routers/web.py:stats_page` + `app/main.py`) : la charge DIRECTE (`frag=0`,
   reload) rendait un corps FRAIS non caché, tandis que la navigation SPA (`frag=1`, clic onglet) servait un
   fragment CACHÉ 20 s → l'utilisateur alternait frais/périmé selon reload vs clic. Fix : `frag=0` et `frag=1`
   servent EXACTEMENT le même corps (cache partagé sous `panel/stats:*`) ; le warmer (`main.py`) garde
   `panel/stats:all` chaud (recalcul 15 s < TTL 20 s) → stats fraîches ET cohérentes quel que soit le mode.

**Régression vérifiée** : AST OK ; cohérence prouvée (compteur == liste = 11/5/6, `avg_cote`=1.51 jamais <1) ;
`frag=0` == `frag=1` stable sur 6 appels alternés (fini le « 1x sur 2 ») ; warmer `stats_page(frag=1)` OK ;
**255 tests** (3 nouveaux : cohérence snapshot + avg_cote≥1 + isolation d'écriture). Aucun impact ROI/stats
figées (les provisoires restent hors ROI ; le fix ne touche que l'AFFICHAGE et le cache).

## 2026-07-10 — Garde-fou selfcheck : fantômes non réglés (« pour ne plus que ça arrive »)

**Quoi** (demande user « Enregistre pour ne plus que cela arrive ») : rendre AUTOMATIQUE la détection d'un
trou de résolution des fantômes, au lieu de le découvrir à la main (cas Syrie-Irak & co, voidés faute de
traduction FR→EN des noms).

- **13e check** `app/selfcheck.py::_check_ghost_resolution` (+ helper `_finished_days_ago`, ajouté à `run()`) :
  repère un match TERMINÉ depuis >2 j dont la MAJORITÉ des fantômes (`shadow`) restent en attente (≥5 pending
  ET ≥50 % du total). Seuil anti-bruit calqué sur `data_completeness` : **info** à 1-2 matchs suspects (trou
  isolé/irréductible, ex. Malte-Armenia sans aucune source), **WARN dès 3** (plusieurs d'un coup = régression
  du pipeline → alerte Telegram via le run quotidien de `tools/selfcheck.py`). 100 % lecture seule.
- **Tests** `tests/test_selfcheck_ghosts.py` (5) : ok (fantômes réglés) · info (1 trou isolé) · **warn (3 matchs
  = scénario incident FR→EN)** · ignore les matchs récents (age<2j) · run complet reste ok/info.

**Régression vérifiée** : AST OK ; le check renvoie **info=1** (Malte-Armenia) sur l'état réel → `run()` reste
`status=info`, `counts` inchangés (0 error/warn) ; test synthétique 3 matchs → **warn** confirmé ; 252 tests
(5 nouveaux) ; aucun effet de bord (le check ne touche ni sélection ni règlement). Mémoire
[[ghost-resolution-guard]] créée. S'active au run quotidien (alerte si un futur trou apparaît).

## 2026-07-10 — Résoudre les fantômes non réglés (traduction FR→EN + marchés tennis)

**Quoi** (demande user : « résoudre les pronos fantôme non résolus et trouver des sources si les nôtres ne
suffisent pas ») : diagnostic de 146 fantômes en attente → **2 causes**, corrigées SANS nouvelle source.

1. **Traduction FR→EN des noms de pays** (`app/sources.py`) — CAUSE RACINE de 34 fantômes (3 matchs basket
   internationaux voidés à tort : Syrie-Irak, Suède-Rép.Tchèque, Roumanie-Grèce). Unibet nomme en FRANÇAIS
   (« Syrie », « Rép.Tchèque »), LiveScore/Flashscore en ANGLAIS → aucun match trouvé → void. Fix : (a) table
   `_FR_EN` COMPLÉTÉE (~60 pays manquants : syrie, roumanie, malte, armenie, georgie, lettonie… + abréviations
   « rep.tcheque ») ; (b) `_tok()` traduit désormais **jeton à jeton** (pas seulement le nom entier) → gère les
   suffixes « Malte (F) » et abréviations. LiveScore trouve alors 91-81 / 87-76 / 73-66 avec les noms FR. Ces
   sources étaient DÉJÀ branchées au règlement — c'était un défaut de matching de noms, pas un manque de source.
   (Malte(F)-Armenia(W) reste introuvable partout = vrai trou de source, laissé void.)

2. **Marchés tennis calculables mais mal codés** (`app/settle_analyst.py`) — ~23 fantômes tennis (tiebreaks,
   handicap de jeux, jeux du set 1) avaient un code FIGÉ générique (`OVER 0.5`, `HCAP HOME -4.5`) issu d'un
   `code_from_pick` d'AVANT les fixes GAMESHCAP/tiebreak → non réglables, alors que les données (jeux par set
   dans `result.raw.periods`) étaient DÉJÀ là. Fix : (a) `code_from_pick` reconnaît « (Nombre total de)
   Tiebreaks plus/moins de X » → `TIEBREAK OVER/UNDER X` (orthographe collée incluse) et « X jeux dans le set
   N » → `SETGAMES` ; (b) `settle_pick` gère `TIEBREAK OVER/UNDER <ligne>` en COMPTANT les sets 7-6/6-7 ; (c) le
   règlement des fantômes RE-DÉRIVE le code du libellé quand le code figé échoue (ne remplace que si ça règle) ;
   (d) trigger `shadow_pending` BORNÉ (`shadow_tries<4`) pour re-traiter un match fini aux fantômes en attente
   (0 réseau : raw en cache). Résout tiebreaks/handicap-jeux/jeux-set du backlog + tout futur fantôme.

**Résultat** : fantômes en attente **146 → 86**, dont **66 sur les matchs d'AUJOURD'HUI pas encore joués**
(normal). Résiduel réel = 20 : Malte-Armenia (10, aucune source), props joueur basket malformés au scan (4,
noms tronqués/joueuse hors match — bruit calibration, non corrigé), stats foot Série B brésilienne (4, FotMob
ne couvre pas → `stats=None`), services breakés tennis (1, non dérivable des sets). Tous BORNÉS (shadow_tries).

**Régression vérifiée** : AST OK (sources.py + settle_analyst.py) ; `settle_pick`/`code_from_pick` testés sur
données réelles (Safiullin-Djokovic : GAMESHCAP -4.5 lost / +7.5 won, TIEBREAK OVER 0.5 won — set 6-7) ;
selfcheck **12 ✅ / 0 erreur** ; compteur ROI **monotone 110 figés / 105 affichés INCHANGÉ** (fantômes hors
ROI) ; calibration exhaustive **2480 prédictions (2218 fantômes)** enrichie. Les 3 matchs re-réglés n'étaient
pas des paris retenus (`retained_bet=None`) → 0 impact ROI, seulement calibration/affichage.

## 2026-07-10 — Sans value : ni retenu ni affiché, mais nourrit les fantômes

**Quoi** (demande user, ex. Manas–Damas à Liège affiché sans raison) : un match SANS value ne doit être ni
retenu ni affiché, mais PEUT alimenter les fantômes (calibration).

1. **Plus d'affichage des abstentions sans provisoire** (`app/web.py:_programme_items`) : une abstention
   analysée sans même un pari provisoire (« pas de value ») est SAUTÉE du programme (accueil/onglets). On
   garde les provisoires (pick doré) et les matchs pas encore analysés (« Analyse à … »). -> Manas disparaît.
2. **Abstentions nourrissent les fantômes** (`tools/generate_analyses.py` + `app/analyses.py:list_for`) : au
   lieu de SUPPRIMER le sidecar d'une abstention « aucun pari ≥ seuil » (path-1, qui perdait son `shadow`), on
   écrit un sidecar MINIMAL `abstained` = méta + `shadow` SEULS (aucun pari/stat_bet/combo). `list_for` IGNORE
   les sidecars `abstained` -> jamais au board. `calibration()` lit leur `shadow` -> fantômes inclus. Le
   règlement (glob de tous les .json) règle leurs fantômes comme ceux des path-2.

**Régression vérifiée** : Manas absent du programme ; sidecar `abstained` absent de `list_for` mais lu par
calibration ; ROI/stats INCHANGÉS (ne comptent que les paris/stat_bet). AST · **242 tests** · selfcheck OK ·
`/health` OK. S'active pleinement au prochain scan (persistance des nouveaux `abstained`).

## 2026-07-10 — Provisoires : confiance affichée + visibles dans onglets sport ET Live

**Quoi** (retours user) :
1. **Enrichir l'affichage** (choix user parmi 3 options) : montrer la CONFIANCE du provisoire comme un vrai
   pari à jouer, MAIS clairement « hors ROI ». `_provisional_pick` capture la proba (3e col. du tableau ;
   repli = proba implicite de la cote). Carte : « Confiance N% · info, hors ROI » en doré (`.mc-prov-conf`).
2. **Voir les provisoires en cours dans le LIVE + section « En direct » des onglets sport** : un provisoire
   dont le match a COMMENCÉ quittait « À venir » et n'était visible nulle part. `_programme_items` marque
   désormais `_live` (état réel Unibet : score live) au lieu de jeter le match ; badge « 🟢 en cours ».
   Routage : accueil = à venir seul (live exclus) ; onglet sport = live→En direct / à venir→À venir ;
   onglet Live (`directs_page`) = injection des provisoires `_live` par sport. `_sport_row` rend un `_html`
   déjà prêt (1 ligne) -> débloque les sections live + `render_directs`.

**Régression vérifiée** : AST · **242 tests** · pages / /basket /directs /stats en 200 · tests d'intégration
(routage live/à-venir OK, badge en cours). Purement AFFICHAGE — provisoires toujours hors ROI/stats/calib
(pas de `.json`). Sur données actuelles : proba backfillée (implicite) pour affichage immédiat.

## 2026-07-10 — Provisoires : cartes CLIQUABLES (fiche d'analyse) + badge série repositionné

**Quoi** (retours user, 2 screenshots) :
1. **Badge série mal placé/rogné** : le chip 🔥/❄️ était dans la ligne des dots (overflow:hidden) -> bords
   coupés + décalait la série W/L. Déplacé dans l'EN-TÊTE, à côté de « Simples/Combinés » (`.spf-cv-hl`,
   pas d'overflow). Ligne W/L = dots seuls, alignés à droite (récents visibles).
2. **Provisoires non cliquables** : cliquer une carte provisoire ne faisait rien. Désormais la carte est un
   `<details>` qui déplie la FICHE D'ANALYSE (comme un pari à jouer). Le scan GARDE le `.md` des provisoires
   (`_prov["fid"]` stocké dans day_programme) ; `_programme_items` rend `analyses.render(sport, fid)` inline.
   ⚠️ Le `.md` est PUREMENT AFFICHAGE : list_for/stats/calibration ne lisent QUE les `.json` -> le provisoire
   n'a PAS de `.json` -> **0 impact ROI/stats/calibration** (vérifié : absent de list_for). Provisoire d'avant
   ce build = carte simple non cliquable jusqu'à la prochaine (ré)analyse.

**Question user tranchée** : les provisoires ne sont PAS comptés dans les stats/« derniers paris » — vérifié
0 fuite (les paris à handicap type Connecticut/Toronto sont des paris PUBLIÉS gagnés, comptés à juste titre).

**Régression vérifiée** : AST · **242 tests** · pages / /stats /foot /basket /app en 200 · **selfcheck OK** ·
test intégration (carte dépliable + analyse inline + match ABSENT de list_for). Purement affichage.

## 2026-07-10 — Graphes simple/combiné CLIQUABLES : derniers paris (W/L) + série en cours

**Quoi** (demande user) : en cliquant sur les graphiques Simples/Combinés des stats, voir les DERNIERS
paris gagnés/perdus (les W/L) + une indication de la SÉRIE en cours (nb gagnés/perdus d'affilée).

**Données** : `_agg_bets` accepte un 4e élément optionnel (meta {name,sel,sport}) par event et expose une
liste `recent` (15 derniers paris détaillés) ; la série `streak` était déjà calculée. Callers de stats_full
enrichis (nom + sel via stat_bet). `combo_stats` : `recent` + `streak` ajoutés à l'overall ET au by_sport.

**Affichage** : `_perf_curve_block` (courbes Simples/Combinés des ONGLETS SPORT) et les blocs Simples/
Combinés de l'ONGLET /stats deviennent des `<details>` : le graphe = résumé cliquable, le clic déplie
`_recent_bets_html` (pastille W/L/N + affiche + sélection + cote + date). La SÉRIE en cours (`_streak_chip`
🔥/❄️) est ajoutée au-dessus de chaque graphe. `_hero_chart` : `onclick=stopPropagation` sur les repères de
modèle -> taper un repère ne déplie pas le panneau. CSS `.spf-rec*`/`.spf-cv-x`/`.spf-cv-more`.

**Régression vérifiée** : décompactages 3-tuples de `_agg_bets` corrigés (tolèrent le 4e élément) ; AST OK ·
**242 tests** · pages /stats /foot /basket /app en 200 · selfcheck OK. Purement AFFICHAGE (stat_bet/ROI/
calibration inchangés).

## 2026-07-09 — Provisoires : affichés dans les onglets sport + suivi SÉPARÉ (info seule)

**Quoi** (demande user) : (A) afficher les cadres provisoires dans les onglets sport ; (B) NE PAS les
compter comme des paris à jouer, mais les suivre À PART pour mesurer « et si on jouait chaque provisoire ? ».

**A — Affichage onglets sport** : `render_sport_matches` injecte les items du programme du sport
(abstentions à venir + pari provisoire doré) dans « À venir », dédoublonnés par équipes avec les paris
retenus. `_programme_items` expose `_sport/_prov/home/away`. Testé : basket montre ses 3 provisoires.

**B — Suivi séparé (info seule)** : nouveau `app/provisional.py` — écrit UNIQUEMENT dans
`data/provisional_track.json`, JAMAIS dans sidecars/`stat_bet`/calibration/`list_for` -> **ROI réel
intact**. `record()` (posé par le scan quand un provisoire est calculé, code réglable seulement),
`settle_pending()` (règle les terminés via `flashscore.final_score`+`settle_pick`, repli LiveScore ;
branché dans `reconcile.py` après le règlement principal), `stats()` (n/réglés/réussite/ROI/cote moy,
mise à plat 1 u). Bloc « 🧪 Paris provisoires — info seule · hors ROI » dans l'onglet Stats
(`routers/web.py:_provisional_card`), avec la mention « ne compte PAS dans le ROI réel » + « si ce ROI
reste négatif, ça confirme qu'il faut s'abstenir ». Backfill initial : 5 suivis, 2 réglés (gagnés).

**Décision** : les provisoires ne sont JAMAIS des paris à jouer (value négative/marginale par
construction — piège « confiance ≠ value »). Le suivi séparé le PROUVERA par les données.

**Régression vérifiée** : AST (4 fichiers) · **242 tests** · `/health` OK · **selfcheck OK** (les 8
invariants du ROI réel intacts — le suivi est totalement isolé). Best-effort partout (try/except) : le
suivi ne peut jamais casser le scan ni la réconciliation.

## 2026-07-09 — Gel des pronos publiés : ancre par PRÉFIXE (pari publié+gagné enfin compté)

**Quoi** (suite du cas Connecticut Sun–Minnesota) : le filet « prono publié = compté » (`retained_bet`
for_history via une ANCRE) rapprochait le champ `pick` de la sélection `bets[].sel` en **égalité stricte**.
Or `pick` est une forme COURTE (« Connecticut Sun +14.5 ») et `sel` porte le suffixe du marché
(« … (hand., prol. incl.) ») -> l'ancre ratait -> un pari PUBLIÉ + GAGNÉ n'était jamais compté (affiché
« pas de pari »).

**Fix** (`app/analyses.py:retained_bet`) : rapprochement EXACT puis **PRÉFIXE/INCLUSION** (`startswith`
dans les deux sens). `for_history=False` (publication) **reste strict** (inchangé). + backfill `stat_bet`
des matchs sauvés : **1 seul** concerné (Connecticut, gagné @1.32) -> désormais compté (+1 victoire au ROI,
honnête) et affiché « ✓ Gagné » (play=True, dans list_for via stat_bet).

**Décisions user** : (a) COMPTER le pari (corriger l'ancre) plutôt que le masquer — cohérence Telegram=site=
stats, ROI honnête ; (b) NE PAS baisser le seuil EV (3 % reste : le backtest quotidien dit « politique
optimale », et ce cas était à 2,96 % correctement rejeté — le vrai bug était l'ancre, pas l'EV) ; (c)
arrondi EV d'affichage laissé tel quel (la rétention utilise la valeur précise `ev<0.03`).

**Régression vérifiée** : impact 1 match, `retained_bet(strict)` toujours None (publication intacte),
AST OK · **242 tests** · `/health` OK. Compteur MONOTONE respecté (on AJOUTE un compté, jamais retiré).

## 2026-07-09 — Affichage : un match RÉGLÉ non compté ne s'affiche plus (même publié)

**Quoi** (demande user, screenshot Connecticut Sun–Minnesota Lynx montrant « Match analysé · pas de pari »
sur un terminé) : une abstention n'a aucun intérêt affiché, elle ne sert qu'en fantôme (calibration).

**Diagnostic** : les VRAIES abstentions étaient DÉJÀ masquées par `list_for`. Ce match précis restait
visible car il avait été PUBLIÉ sur Telegram (`get_prono` True) mais son pari (EV pile au seuil 3 %,
78 % conf, publié + gagné) n'était ni retenu (`retained_bet`=None) ni compté (`stat_bet`=None). `list_for`
gardait tout match publié, même réglé et non compté -> carte « pas de pari » sur un terminé.

**Fix** (`app/analyses.py:list_for`) : la survie « par publication » (`get_prono`) ne s'applique plus qu'aux
matchs **PAS ENCORE RÉGLÉS** (cohérence Telegram=site pour l'à-venir). Un match **réglé mais non compté**
(stat_bet vide, pas de combiné, pas de pari retenu for_history) n'apparaît plus — le site ne montre QUE ce
qui est dans les stats. L'ancre `stat_bet` (terminés comptés) est intacte -> le fix Auger-Aliassime tient.

**Régression vérifiée** : impact chirurgical — EXACTEMENT 1 match masqué (le Connecticut), 0 match compté
ou à-venir publié touché (foot 108 / tennis 31 / basket 41 inchangés hors ce 1). Le match reste réglé +
calibré en coulisses (fantômes). AST OK · **242 tests** · `/health` OK.

## 2026-07-09 — Audit complet du dépôt + nettoyage du code mort

**Quoi** (demande user : « check complet de ce qui est en place, nettoyer le mort, vérifier chaque fichier »).
Audit read-only (3 agents Explore parallèles : app/, tools/+deploy/, racine/docs/static + pyflakes global +
compile/import de tous les .py). Puis nettoyage validé.

**État vérifié** : tout compile ; 49 modules `app/` s'importent ; 242 tests passent ; `/health` OK ;
selfcheck (8 invariants) OK ; **aucun module orphelin** (69 fichiers app/ tous référencés).

**Nettoyé (commits 2e23066, 864dde1)** :
- `app/analyses.py` : 5 fonctions mortes (0 call-site vérifié) — `_verdict_card`, `_ev_chip`, `_reco_event`
  + helpers orphelinés en cascade `_vc_icon`, `_odds_in`.
- 5 imports top-level inutilisés (time/io/2×math/re). Pré-checks `stripe`/`edge_tts` (noqa) conservés.
- 3 PNG junk (noms GUID, ~1,6 Mo). `build_backtest.bat` (cassé : appelait `backtest_model.py` inexistant,
  backtest Elo/SofaScore mort). `RAPPORT_AUDIT_PERLE.md` + `OPTIMISATIONS.md` (docs figées juin, remplacées
  par ce journal). `start_mobile.ps1` (remplacé par reconnexion.bat + deploy/run_mobile.ps1). README : retrait
  du paragraphe au lien mort (backtest_model.py/build_backtest.bat).

**CONSERVÉ volontairement (mort mais intentionnel)** : vestiges SofaScore/Elo (sofa_http, sofa_browser,
build_*_elo, explore_*, garde-fous réversibles cf. [[build-sofascore-dead-data-loss]]) ; outils manuels
setup/maintenance (make_icons, notify_setup, stripe_setup, video_pronos, screenshot_mobile, renotify_cards,
probe_sources) ; `static/banner_*.png` (utilisés par card_image) ; `mark.png` (choix user) ; `claude.bat` ;
tools non commités que le user gère en local (learning/policy_backtest/selfcheck/source_health).

**Régression vérifiée** : après chaque suppression — AST + import + pyflakes + **242 tests** + `/health`.
Reste noté : commentaires `backtest_model.py` dans app/analysis.py (justif. de CALIB_SHRINK) laissés exprès.

## 2026-07-09 — Provisoire = pari le plus PROBABLE (analysé) + tennis « commencé » = état réel

**Quoi** (2 retours user 2026-07-09) :
1. **Pari provisoire mal sourcé** : le backfill/`_provisional_pick` mettait « Victoire [favori] » (favori 1X2
   brut), et une abstention basket ressortait même en « À éviter / SKIP » (garbage). Cause : on lisait
   `_safe_pick` (section « Le pari à jouer », qui dit SKIP/à éviter en abstention). Fix : `_provisional_pick`
   lit désormais la **TÊTE du tableau « Paris classés par chance de passer »** (le pari #1 par probabilité,
   ANALYSÉ comme les vrais pronos), saute les lignes SKIP/à-éviter/en-tête ; le favori 1X2 n'est plus qu'un
   ultime repli (backfill sans analyse). Vérifié en live : Kostyuk -> « remporte au moins un set @1.28 »
   (tableau 76 % 🟢), plus « Victoire Kostyuk ».
2. **Tennis considéré « commencé » à l'heure PRÉVUE** : `_programme_items` retirait un match tennis dès son
   coup d'envoi programmé (figé au matin) — or au tennis un match est souvent DÉCALÉ (le précédent traîne).
   Fix : pour le tennis, « commencé » = **état réel Unibet** (`live_state_for` score live + `fresh_status`
   coup d'envoi ré-estimé), PAS l'heure figée. Un tennis pas encore live reste affiché (même heure passée) ;
   retiré seulement s'il est live OU >6 h après (sûrement fini). Foot/basket (coup d'envoi fixe) inchangés.

**Fichiers** : `tools/generate_analyses.py` (`_provisional_pick` réécrit + `_PROV_SKIP_RE`),
`app/web.py` (`_programme_items` : bloc tennis état-réel). Data : ré-analyse basket (3) + Kostyuk reposé
avec pari analysé (leurs provisoires d'avant venaient du backfill sans analyse / d'une vague sautée).

**Régression vérifiée** :
- `_provisional_pick` : 4 cas testés (tête de tableau vs SKIP en reco, 1re ligne à-éviter -> suivante, pas
  de tableau -> favori, vide -> None). Diag live Kostyuk : tableau -> pari analysé extrait. ✓
- `_programme_items` tennis : testé (tennis décalé non-live GARDÉ, tennis live RETIRÉ, basket inchangé). ✓
  Cache `live_state_for` chaud (home() appelle `fetch_live_odds('tennis')` avant `render_dashboard`) -> 0
  appel réseau ajouté. `fresh_status` déjà la référence temps-réel (cf. [[settle-never-on-live-score]]).
- AST OK (2 fichiers) · **suite complète 242 passed**.

**Résultat** : chaque match affiche le pari le plus probable ANALYSÉ (fini le favori brut/SKIP) ; un match
de tennis décalé ne disparaît plus de l'accueil avant d'avoir réellement commencé, et son heure suit
l'estimation Unibet.

## 2026-07-09 — Programme : heure seule (sans « Aujourd'hui ») + PARI PROVISOIRE sur chaque match

**Quoi** (2 demandes user 2026-07-09, capture accueil) :
1. **Badge horaire** : les cartes du programme affichaient « Aujourd'hui HH:MM » / « Demain HH:MM ». Comme
   le programme est DÉJÀ groupé par en-tête de jour, la date est redondante -> `fmt_local(dt, with_date=
   False)` = heure seule (HH:MM), aligné sur les cartes de pari vertes. (`app/web.py:_programme_items`)
2. **Pari provisoire** : un match analysé SANS value (abstention, ex. Gaubas/Kostyuk/Pelicans) n'affichait
   « Analyse à HH:MM » / « Pas de value ». Désormais il montre « le pari si l'on devait en jouer un »
   (avis analyste via `_safe_pick`, repli FAVORI 1X2 des cotes), en TEINTE DORÉE (≠ vert value confirmée)
   + mention de ré-analyse. (« comme pour Gauff », demande user.)

**Comment (sans casser les 3 couches Affichage/Stats/Calibration)** :
- Scan : nouveau `_provisional_pick(analysis, meta, m)` ; `_set_programme_status` prend un arg
  `provisional` écrit dans `data/day_programme.json` UNIQUEMENT (jamais dans un sidecar). Posé dans le
  bloc d'abstention (skip_reason) ET le cas « analysé non retenu ». Effacé quand le match passe « bet ».
- Site : `_programme_items` rend le provisoire comme une ligne de pari dorée `.mc-prov` + `.mc-reana-prov`
  (nouveau CSS, réutilise `--gold`). Repli sur l'ancien libellé si pas de provisoire. Note du bloc mise à
  jour (pari confirmé vert = value / provisoire doré = indicatif).

**Régression vérifiée — POINT CLÉ : ROI/stats/calibration INTACTS** :
- `day_programme.json` n'est lu QUE par l'affichage (`web.py:_programme_items`) et la lecture d'ids
  (`_load_programme_ids`) — grep exhaustif : AUCUN lecteur côté stats/ROI/calibration/règlement/selfcheck.
  Le provisoire ne peut donc PAS être compté (invariant « posté = compté » préservé : il n'est ni dans
  `bets`, ni `stat_bet`, ni `shadow`, ni `combo`).
- `_provisional_pick` : 4 cas testés (avis analyste, repli favori, vide->None, table). ✓
- `_set_programme_status` : transitions testées (abst+prov -> bet efface prov -> abst sans prov -> abst+
  prov2). ✓ Anciens appelants (statut seul) inchangés (arg optionnel, défaut None).
- `_programme_items` : rendu testé (provisoire doré avant ET après l'échéance -1h ; repli si absent). ✓
- AST OK (2 fichiers) · `import app.web` OK · `render_dashboard` OK · **suite complète 242 passed**.

**Résultat** : chaque match du programme montre un pari (confirmé vert OU provisoire doré) dès qu'il est
analysé, avec l'heure seule. Le provisoire se peuple aux vagues (-1 h) et au scan du matin ; il ne fausse
jamais le ROI/les stats. App en --reload -> l'affichage (badge horaire + rendu doré) est déjà actif ; les
picks provisoires apparaissent au fil des ré-analyses du jour.

## 2026-07-09 — Scan 09h : ré-analyse AUSSI les matchs déjà affichés (`--force` au matin)

**Quoi** : la passe « SCAN MATIN » de `deploy/scan_daily.ps1` passe de `--from-programme` à
`--from-programme --force`. Au matin, TOUT le programme est (ré)analysé, y compris les matchs déjà
affichés/publiés sur le site (le gel et le cache 6 h ne les sautent plus) -> chaque match a son pari du
jour dès 09h.

**Pourquoi** : demande user 2026-07-09 (« à 09h, donner déjà des paris à jouer pour tous les matchs, même
ceux déjà affichés, en précisant qu'une autre analyse sera faite 1h avant »). Q2 tranchée : ré-analyser à
09h aussi. Q1 : on garde l'abstention pour les vrais no-value (invariant « posté = compté » + garde-fou
value négative intacts) ; un match retenu montre déjà son pari.

**Fichiers** : `deploy/scan_daily.ps1` (flag + commentaires d'entête).

**Régression vérifiée** :
- `--force` × `--from-programme` sont orthogonaux (grep l.2027-2302) : from-programme FILTRE la liste
  (l.2048), force CONTOURNE gel (l.2092) + cache (l.2108), charge l'ancien pick (l.2100) et ne re-poste
  QUE si le pick a CHANGÉ (l.2298 skip-si-identique + l.2302). Donc pas de spam abonnés, ancien pick ->
  fantôme (calibration) si changé. Aucune exclusivité mutuelle des deux flags.
- La mention « une autre analyse sera faite ~1h avant » existe DÉJÀ côté site (`app/web.py:3985-3992`
  « 🔄 Ré-analyse à HH:MM · le pari peut encore changer », affichée pour tout pari à >1h du coup d'envoi)
  -> rien à ajouter, et elle apparaîtra désormais AUSSI sur les matchs jadis sautés.
- Parse PowerShell OK (`Parser::ParseFile` = PARSE OK). Pas de modif Python -> pas de risque AST.

**Résultat** : au scan de 09h, aucun match du programme n'est laissé sans (ré)analyse ; les picks du matin
portent la mention de ré-analyse rapprochée. Coût marginal (seuls les matchs jadis gelés/en-cache sont
re-analysés en plus). Comportement `--force` déjà éprouvé (commit 97c20cc).

## 2026-07-07 — Flashscore : couvrir les matchs FUTURS (cap jour +1 -> +10) — les 3 sports
- **Quoi** : `app/flashscore.py` `_day_offsets` — borne haute passée de **+1 à +10**. Un match à +2 jours ou
  plus était cherché sur le mauvais jour (offset clampé à 1) -> pas de forme/H2H Flashscore sur les matchs
  futurs (même trou « aujourd'hui/demain seulement » que Sportradar avait).
- **Pourquoi** (demande user « optimiser les sources au max ») : le cap venait du besoin RÈGLEMENT (archive
  passée [-10,1]). Pour l'ENRICHISSEMENT pré-match, il faut chercher le jour réel du match, futur inclus.
  Flashscore liste bien les jours futurs (offset +2 : 36 matchs, +3 : 56).
- **Vérif** : matchs à +2/+3/+4 j (Utah-Oklahoma, New Orleans-Minnesota…) résolvent maintenant. Anti-régression :
  borne basse -10 inchangée -> règlement (offsets ≤0) intact ; AST OK.
- **Résultat** : Flashscore enrichit les matchs de plusieurs jours à l'avance pour les 3 sports (complète le
  fix Sportradar du même jour).

## 2026-07-07 — Sportradar couvre les matchs FUTURS (fixtures de saison) — les 3 sports
- **Quoi** : `app/sportradar.py` — nouveau vivier de résolution ÉLARGI (`_candidate_pool`) : matchs DU JOUR
  (page statshub, comme avant) PLUS tous les matchs des compétitions ACTIVES aujourd'hui, FUTURS inclus,
  via `stats_season_fixtures2/{seasonid}`. `_resolve` matche contre ce vivier (noms + jour ±1, anti-homonyme).
- **Pourquoi** (demande user « trouver comment sélectionner le jour suivant sur Sportradar ») : la page
  statshub ne liste que le JOUR (foot ~69 ids, tous aujourd'hui) → les matchs de demain (scan à +24 h)
  étaient ratés. Mécanisme trouvé : chaque match porte `_seasonid` → `stats_season_fixtures2` renvoie toute
  la compétition, futurs inclus (date sous `time.uts`).
- **Vérif** : vivier foot 69 → 2326 (1027 futurs) · tennis 457 (91 futurs) · basket 1037 (331 futurs).
  France-Maroc (07-09) résout + `block()` produit forme/séries/H2H. Imports OK. Anti-régression : `_resolve`
  garde son scoring (score≥2, jour±1, refus si égalité au sommet) ; matchs du jour résolvent toujours.
- **Résultat** : Sportradar enrichit désormais les matchs de DEMAIN pour les 3 sports → relève data_score
  foot/tennis/basket (Sportradar n'est plus « jour même seulement »).

## 2026-07-06 — Basket : abstention si 0 source d'enrichissement (règle « faits ≥2 sources »)
- **Quoi** : dans le scan (`generate_analyses.py`, après `build_dossier`), un match **basket** sans AUCUNE
  source d'enrichissement (`sources_prov` tout vide → data_score 0) est ÉCARTÉ **avant** l'analyse Claude.
- **Pourquoi** (demande user, réponse au check bleu `data_completeness`) : le basket international obscur
  (AfroBasket, qualifs asiatiques : RD Congo-Côte d'Ivoire, Syrie-Iran) n'est couvert par aucune source
  (ESPN basket = NBA/WNBA seulement) → analyse sur cotes seules. On ne parie pas un match invérifiable.
- **Portée** : basket UNIQUEMENT. Foot/tennis gardés (un 0 y est un hoquet réseau transitoire, pas
  structurel ; FotMob/ESPN couvrent).
- **Vérif** : AST OK · condition testée (AfroBasket→écarté, NBA/WNBA→gardé, foot/tennis 0→gardés). Les 2
  fiches data_score 0 existantes (déjà jouées, aucun pari compté) sortiront de la fenêtre d'audit seules.
- **Résultat** : plus de nouvelle fiche basket analysée sur cotes seules → le check bleu se tarit à la racine.

## 2026-07-06 — Combiné JAMAIS dominé (garde-fou ABSOLU) — vraie cause = cap `cands[:6]`
- **Quoi** : CAUSE RACINE trouvée — `_build_combo_from_pool` faisait `cands = cands[:6]` (top confiance), ce
  qui COUPAIT les jambes à COTE HAUTE (BTTS @1.50, USA-MT @1.64 pour USA-Belgique). Il ne restait que des
  marchés courts/corrélés -> TOUS les combinés dominés. Fix : garder aussi les 3 jambes à plus haute cote
  (diversité de cotes) -> un combiné NON-DOMINÉ redevient possible (15 existaient pour USA-Belgique !).
  Puis : SUPPRESSION du dernier recours `wc_any` (qui acceptait un combiné dominé en CdM) -> garde-fou
  domination ABSOLU. selfcheck revenu strict (dominé = alerte, même CdM : ça ne doit JAMAIS arriver).
- **Pourquoi** (reproche user, à raison) : un combiné dont une jambe paye plus que le total est ABSURDE
  (jouer la jambe seule est strictement meilleur). Mon « exception CdM dominée acceptée » était une ERREUR.
  La bonne réponse : CONSTRUIRE un combiné non-dominé (inclure une jambe haute), pas accepter le dominé.
- **Fichiers** : `tools/generate_analyses.py` (cap + suppression wc_any), `app/selfcheck.py` (revert strict),
  sidecar+post USA-Belgique.
- **Vérif** : USA-Belgique 3j real 1.75 > jambe 1.50 NON-DOMINÉ · Argentine 1.95 > 1.64 · hors CdM abstention
  gardée · `selfcheck` 13/13 (0 dominé). USA-Belgique reposté (799 dominé supprimé -> 800 non-dominé).
- **Résultat** : CdM = un combiné par match ET jamais dominé. Le garde-fou est absolu pour tous les sports.

## 2026-07-06 — CdM : combiné OBLIGATOIRE par match (règle user) + exception domination
- **Quoi** : (1) `_make_combo`/`_build_combo_from_pool` — en CdM on ne s'abstient plus quand la désignation
  n'est pas combinable : repli optimiseur garanti + dernier recours `wc_any` (combiné priçable le plus sûr
  même dominé). Hors CdM = abstention inchangée. (2) `app/selfcheck.py` `_check_combo_not_dominated` : un
  combiné dominé en CdM est ACCEPTÉ (dernier recours, règle 1 combiné/match) ; hors CdM il reste une alerte.
  (3) USA-Belgique (CdM, joue avant le prochain scan) régénéré + reposté (msg 799).
- **Pourquoi** (demande user) : « Pour la CdM il faut ABSOLUMENT un combiné par match, exception à la règle. »
  Mon fix anti-substitution 332fd39 avait rendu l'abstention possible en CdM (cas USA-Belgique).
- **Fichiers** : `tools/generate_analyses.py`, `app/selfcheck.py`, sidecar+post USA-Belgique.
- **Vérif** : USA-Belgique/PT-ES/Argentine (CdM) -> combiné garanti ; hors CdM sans value -> abstention.
  `selfcheck` 13/13 (0 non-ok ; le dominé CdM d'USA-Belgique = exception acceptée). Source unique conservée
  (publié==réglé). AST OK.
- **⚠️ Tradeoff signalé au user** : le seul combiné possible d'USA-Belgique est MARGINALEMENT dominé
  (1.41 ≤ jambe 1.43) car Belgique = gros favori (tous ses marchés courts/corrélés) -> value < jambe seule.
  Accepté par la règle CdM, mais à arbitrer (forcer un combiné dominé vs abstention dans ce cas rare).

## 2026-07-06 — Re-scan --force des matchs à venir (nouveau système) + reconcile channel
- **Quoi** : `generate_analyses.py --force` (demande user) pour réanalyser les matchs à venir avec le
  nouveau système. 7 analyses (3 foot re-analysés, 2 tennis, 2 basket) en 33 min. Résultat posté :
  **PT-ES (795, combo 1.79)**, **Auger-Aliassime–Djokovic (796, « Djokovic vainqueur » — PAS « Jeux »)**,
  **Argentine-Égypte (797, combo 2.00)**. Abstentions (nouveau système, correct) : USA-Belgique (combo
  non combinable + simple non valué), Sinner-Struff, 2 basket.
- **Reconcile channel** : supprimé les anciens posts superséd/périmés (787, 791, 792) — vérifié via API
  qu'ils étaient bien absents (bot admin `can_delete_messages=True`, testé). Retiré du registre USA-Belgique
  (abstention) et Suisse-Colombie (hors fenêtre 24 h, sa carte 09:34 n'existait plus → sera reposté au
  prochain scan quand il entre dans la fenêtre). Sonde non-destructive (`editMessageReplyMarkup`) :
  795/796/797 EXISTENT, tous les anciens ABSENTS.
- **Fichiers** : `data/analyses/*` (sidecars re-générés) ; `data/notify_pronos.json`.
- **Vérif** : `selfcheck` 13/13 · chaque prono à venir cohérent (carte=sidecar=règlement) · Auger = marché
  « Vainqueur » (exclusion « Jeux » respectée). Zéro doublon restant.
- **Résultat** : channel aligné sur le nouveau système = 3 pronos frais corrects, abstentions non affichées.

## 2026-07-06 — Nettoyage channel Telegram (ciblé, validé user) après passage au nouveau système
- **Quoi** : supprimé 3 messages du channel — **790** (🎾 Fritz-Bublik « Total jeux +37.5 », marché « Jeux »
  désormais EXCLU → devenu abstention), **786** (⚽ PT-ES ancien combiné 3j, doublon périmé remplacé par 791),
  **780** (🎾 Fritz ancien, orphelin). Retiré Fritz (16385342) du registre `notify_pronos.json`.
- **Pourquoi** (demande user) : aligner le channel sur le nouveau système (combiné ancré + exclusion « Jeux »),
  retirer les doublons de la session (double-post 00:47/09:34) et le pari d'un marché maintenant écarté.
- **Fichiers** : `data/notify_pronos.json` (gitignore) ; suppressions via `notify.delete_messages`.
- **Vérif** : `retained_bet(Fritz)=None` (bien abstention) · suppressions CONFIRMÉES via API (deleteMessage →
  « message not found ») · 786/780 orphelins (non pointés → aucun règlement cassé). Périmètre ciblé validé
  par le user (pas de reset lourd). Reste à venir = 4 combinés foot corrects (787/788/791/792), tous cohérents.
- **Résultat** : channel propre, seuls les pronos valides du nouveau système subsistent. Historique réglé intact.

## 2026-07-06 — Exclusion de marché : ROI par (sport,marché) FANTÔMES INCLUS (mûrir sans paris réels)
- **Quoi** : dans `_excluded_by_sport` (`app/analyses.py`), le garde-fou (c) « ROI perdant » ne lit plus
  le ROI **global des paris joués** (`perf_breakdown`, lent ~1 pari/match) mais le **ROI par (sport,marché)
  de la calibration** (`calibration()['by_sport'][sport]['markets'][…]['roi']`, **fantômes inclus**).
  Mêmes seuils (`CALIB_MIN_N=25`, `CALIB_ROI_MAX=-15`).
- **Pourquoi** (demande user) : optimiser sur les **fantômes** (10-14/match) pour ne PAS attendre le
  goutte-à-goutte des paris réellement joués. Un marché bien calibré mais EV-négatif (tennis « Jeux »
  -21 % sur n=87) était invisible pour l'ancien garde-fou (calibration bonne + trop peu de paris joués).
- **Fichiers** : `app/analyses.py` (`_excluded_by_sport`) ; `tools/methodology_doc.py` (déjà surfacé le
  ROI fantôme par marché) ; doc régénéré.
- **Régression vérifiée** : AST OK · AVANT/APRÈS exclusions = seul **tennis {Jeux}** s'ajoute (foot/basket
  inchangés) · `selfcheck` 13/13 (0 non-ok hors info) · `exclusions_report()` OK. Auto-révisable (si le
  marché redevient rentable, il se réintègre).
- **Résultat** : tennis « Jeux » écarté de la sélection dès maintenant, sur un vrai échantillon fantôme,
  sans sur-couper (Sets -12 % > seuil, gardé). Chantier tennis (calibré mais ROI négatif) traité à la racine.

## 2026-07-05 — Combinés : proba conjointe corrigée de la corrélation + garde-fou PICK NONE
- **Quoi** : dans `_build_combo_from_pool` (`tools/generate_analyses.py`), la proba d'un combiné same-match
  n'est plus le **produit** des probas de jambes (= hypothèse d'**indépendance**) mais ce produit **ajusté**
  par la corrélation que le marché price déjà dans la **vraie cote Bet Builder** : `k = produit_cotes / real`
  (`k<1` = jambes anti-corrélées → proba abaissée ; `k>1` = domination corrélée → proba relevée). En plus,
  si l'analyste a écarté le match (`PICK: NONE`), on n'accepte un combiné **que** s'il a une vraie value
  (`best`) — plus de repli « le plus sûr » forcé.
- **Pourquoi** : combiné tennis FAA/ADF affiché « chance 41 % » alors que le match était **SKIP** (PICK NONE)
  et que les 2 jambes (FAA +1.5 set @1.23 · ADF +2.5 jeux @2.10) sont **anti-corrélées** (Unibet cote le
  combiné **3.70 > produit 2.58**) → proba réelle ~28 %, EV en réalité **nulle/négative**. Faux positif.
- **Fichiers** : `tools/generate_analyses.py` (`_build_combo_from_pool` +param `pick_none`, `_make_combo`
  passe `_parse_pick`) ; sidecar `data/analyses/tennis_16385335.json` (combo bidon retiré ; shadow/calibration
  intacts ; match à venir, rien de figé au ROI).
- **Régression vérifiée** : AST + import OK ; **test unitaire ciblé** (rejoue FAA/ADF) → REJETÉ à real 3.40
  ET 3.70, ET même avec un PICK réel (proba corrigée 28,5 % < seuil 33 %) ; **contrôle inverse** : combiné
  positivement corrélé (real 1.90 < produit 2.58) → proba **relevée** 41 %→55 %, EV 1.04, **gardé** (foot non
  cassé, mieux valorisé) ; **selfcheck 10/10 OK** avant et après ; compteur monotone 82 et calibration 1973
  inchangés.
- **Résultat** : le faux combiné disparaît ; règle de fond qui tue ce type de faux positif partout (pas
  seulement ce match). cf. [[kambi-betbuilder-pricing]], [[combo-construction-rules]].

### 2026-07-05 (suite) — audit post-scan + 3 raffinements
- **Audit auto** (scratchpad `audit_scan.py`) : re-price chaque combiné via Bet Builder (cote affichée ==
  vraie cote corrélée live ? → **drift 0,0 %** sur les 3 combinés du scan), `total==produit`, proba
  recalculée depuis le POOL, EV, garde-fou PICK NONE. A **révélé** un écart de proba (4-5 pts) sur le foot.
- **Raffinement 1 — `k` basé CATALOGUE** : le facteur `k` (et le `total`) utilisaient les cotes POOL (LLM)
  d'un côté, catalogue de l'autre → proba incohérente avec le total affiché. Désormais `k = nvp/real` avec
  `nvp` = produit des **vraies cotes catalogue Unibet** (`_leg_odds`, mêmes cotes que le `total`) → proba
  cohérente ET plus correcte (catalogue = vérité Unibet). Vérifié : coïncidence `total==produit`, proba
  auditable. `tools/generate_analyses.py`.
- **Raffinement 2 — invariant selfcheck `combo_ev_value`** : grave la règle → un combiné **à venir**
  tennis/basket doit porter une value réelle (`EV = real×prob/100 ≥ 0,95`) ; foot exclu (repli sûr autorisé) ;
  forward-only. `app/selfcheck.py` (11e invariant, **OK** au run).
- **Raffinement 3 — libellé honnête** : fini le « jambes variées peu corrélées » systématique ; le texte
  reflète `k` (domination corrélée / quasi indépendantes / peu liées à cote pleine).
- **Incident corrigé** : le script de recompute hors-scan effaçait un combo si `betbuilder_catalog` revenait
  vide (saturation transitoire) → rendu **non-destructif** (skip si catalogue indispo, jamais d'effacement).
  2 combos effacés → régénérés par un nouveau scan complet (code final).
- **Régression vérifiée** : AST + imports OK ; test unitaire corrélation (cotes catalogue ≠ POOL) → FAA/ADF
  toujours REJETÉ, corrélé+ gardé, `total==produit` OK ; selfcheck **11/11**, monotone 82, calibration
  inchangés.

### 2026-07-05 (suite 2) — Combiné foot : CdM only pour le repli forcé
- **Quoi** (demande user) : le repli « **un combiné par match** » (safest forcé, sans exigence de value)
  est désormais réservé aux matchs de **Coupe du Monde**. **Hors CdM, le foot s'ALIGNE sur tennis/basket** :
  combiné uniquement si VRAIE value (EV>1), sinon abstention.
- **Comment** : `_build_combo_from_pool` reçoit `is_wc` ; nouveau `_wc_foot = _foot and is_wc` remplace `_foot`
  aux 2 points de RELÂCHEMENT de value (filtre `best` et repli `safest/any_safe`). Les réglages de CIBLE foot
  (fourchette 1.75-2.25, filtre props, jambe ≥1.10) restent sur tout le foot. `_make_combo(comp=…)` détecte la
  CdM via `_is_big_match` (déjà existant, `_BIG_TOURNEYS`). Les 2 appels passent `comp=m.comp||m.circuit`.
  Invariant `combo_ev_value` affiné : n'exempte QUE le foot CdM (le foot hors CdM est désormais vérifié).
- **Fichiers** : `tools/generate_analyses.py`, `app/selfcheck.py`.
- **Régression vérifiée** : AST + imports OK ; **test unitaire** : foot CdM sans value → COMBO gardé (EV 0.72) ;
  foot hors-CdM sans value → ABSTENTION ; foot hors-CdM AVEC value → COMBO ; tennis inchangé. selfcheck
  **11/11 OK**. État actuel conforme sans re-scan (Brésil/Mexique = CdM → combo gardé ; Náutico Serie B = pas
  de combo). cf. [[combo-construction-rules]].

### 2026-07-05 (suite 3) — vérif affichage + suite de tests
- **Couche AFFICHAGE vérifiée** (`combo_html`) : la carte combiné n'affiche NI proba globale NI shave (par
  choix : le produit sous-estime) → la proba corrigée ne sert qu'à la **sélection** (scan), pas au rendu →
  **rien cassé** côté UI. Le badge de sûreté des paris SIMPLES est indépendant.
- **Point mathématique confirmé** : après correction, `EV = real × prob = produit_probas × produit_cotes`
  → **indépendante de `real`**. La correction ne « crée » pas de value (elle ne récompense plus un `real`
  gonflé par l'anti-corrélation) ; elle corrige la PROBA affichée et sélectionne sur l'EV RÉELLE.
- **Anti-régression tests** : les signatures modifiées (`_make_combo(comp=…)`, `_build_combo_from_pool(is_wc,
  pick_none)`) ont des appelants dans `tests/`. 2 tests cassaient — cause : cotes de jambes (2.0) physiquement
  incohérentes avec la cote combinée simulée (1.96), ce qui faisait exploser `k`. Corrigé (cotes = 1.40 =
  cote corrélée/jambe → k=1 → teste bien la calibration) + **ajout `test_combo_correlation_ajuste_la_proba`**
  (corrélé→proba relevée / anti→abaissée, EV ~constante). **Suite complète : 237 passed.**

### 2026-07-05 (suite 4) — Garde-fous LOGIQUE des combinés (audit adversarial)
- **Constat** (demande user « plus d'erreurs de paris illogiques ») : audit adversarial de 5 combinés
  proposés → **4/5 illogiques**. Cause racine : le combiné est fabriqué MÉCANIQUEMENT depuis le vivier
  `POOL` par l'optimiseur EV, **découplé du verdict de la fiche** (l'analyste écrit « no-bet »,
  « jamais en combiné », « PICK: NONE », « éviter les jeux » en prose, mais l'optimiseur ne lit que les
  lignes `POOL:` + le code `PICK:`). D'où des combinés qui CONTREDISENT leur propre fiche.
- **Erreurs types** : (a) combiné construit contre le verdict (Toronto « jamais en combiné », De Minaur
  PICK NONE) ; (b) jambes ANTI-corrélées = hedge, pas domination (Fritz k=0.86, De Minaur 0.96) ;
  (c) proba conjointe coin-flip (Toronto 46 %, Fritz 47 %) ; (d) jambe « passagère » @1.11 qui gonfle la
  cote sans edge ; (e) sélection « pas la moins mauvaise » (Mexique empile les 2 pires jambes, -20 %).
- **Fix A — filtres déterministes** (`tools/generate_analyses.py`, `_build_combo_from_pool`) :
  - `_COMBO_CORR_MIN = 0.999` : **corrélation positive obligatoire** (k = produit_cotes/vraie_cote ≥ ~1).
    k<1 = anti-corrélé = hedge → ÉCARTÉ. Tue Fritz/De Minaur/Mexique-v1. Le repli CdM ne prend QUE des
    combos corrélés (k≥0.999).
  - `_COMBO_CONJ_MIN = 0.55` : **proba conjointe ≥ 55 %** (au-dessus du coin-flip). Tue Toronto/Fritz.
    Le repli CdM (1 combiné/match) n'y est PAS soumis (garde un combiné).
  - Épargne la vraie domination corrélée (Brésil k=1.25, gardé).
- **Retrait immédiat** (fiches pas encore commencées) : De Minaur & Fritz combos RETIRÉS ; Mexique CdM
  corrigé (anti-corrélé -20 % k=0.99 → corrélé k=1.0, prob 48 %). Toronto/Brésil = inprogress, non touchés.
- **Régression vérifiée** : AST OK ; test `test_combo_correlation_ajuste` adapté (anti-corrélé → ÉCARTÉ) ;
  **237 tests passent** ; selfcheck **11/11 OK**. cf. [[combo-construction-rules]], [[combo-prob-market-correlation]].
- **Reste (fix B, non fait)** : faire décider l'analyste (ligne `COMBO: OUI/NON` explicite) pour respecter
  ses réserves en prose ; étendre le panel de validation au combiné. À cadrer.

### 2026-07-05 (suite 5) — Fix B : le COMBINÉ est DÉCIDÉ par l'analyste + prompt « domination corrélée »
- **Découverte** : le prompt (`_betbuilder_menu`) ordonnait des jambes **INDÉPENDANTES** (« ⚠️ CHANGEMENT DE
  LOGIQUE… Unibet rabote les corrélées -> value détruite ») — prémisse **FAUSSE** : `EV = real × proba =
  produit des value individuelles`, **indépendant de la corrélation**. Viser l'indépendance BAISSE la proba
  conjointe et fabrique des hedges anti-corrélés = les 4/5 illogismes de l'audit. Le prompt se battait contre
  le filtre A.
- **Fix (2 volets)** :
  1. **Prompt corrigé** (`_betbuilder_menu`) : retour à **DOMINATION CORRÉLÉE** (jambes qui tombent ENSEMBLE
     dans UN scénario), explication que la corrélation n'altère pas la value (elle monte la chance), interdit
     les jambes de scénarios OPPOSÉS.
  2. **Décision par l'analyste** : nouvelle ligne `COMBOPICK: <id>+<id>[+<id>]` (SON combiné, ids du POOL) ou
     `COMBOPICK: NONE` (abstention). `_parse_combo_designation` + `_make_combo` : la désignation est
     PRIORITAIRE (on ne price que ses jambes), NONE hors CdM = abstention respectée (résout Toronto « jamais
     en combiné » / De Minaur PICK NONE) ; en CdM, repli optimiseur garantit 1 combiné. Les filtres A
     (corrélation k, proba ≥55 %) restent le garde-fou -> une désignation incohérente est quand même écartée.
- **Point 3 (value/jambe) ABANDONNÉ** : casserait l'ancre légitime d'une domination corrélée (Brésil @1.18,
  value 0.94) et ne visait pas juste (De Minaur @1.11 a value 1.01>1). Le filtre corrélation le couvre.
- **Régression vérifiée** : AST OK ; test `test_combopick_designation` (désigné corrélé retenu, NONE
  hors-CdM abstenu, NONE CdM garanti, désigné anti-corrélé écarté) ; **238 tests OK**. Validation réelle =
  scan complet (en cours). Rétrocompat : fiches sans `COMBOPICK:` -> repli optimiseur (comportement A).

### 2026-07-05 (suite 6) — Garde-fou DOMINATION (combiné ≤ une de ses jambes) + fuite legacy
- **Signalé user (capture)** : combiné Mexique affiché **@1.47** alors que sa jambe « Moins de 2.5 » vaut
  **@1.58** -> la cote combinée est INFÉRIEURE à une jambe seule : jouer la jambe seule rapporte plus AVEC
  moins de risque. Combiné **DOMINÉ** = illogique. Cause : 2 jambes quasi-redondantes (« match fermé » ×2)
  -> rabotage de corrélation extrême. Mon filtre corrélation avait un plancher (pas d'anti-corrélation) mais
  **pas de plafond**.
- **Fix** : `_COMBO_MIN_LIFT = 1.10` — la vraie cote combinée doit dépasser d'au moins +10 % la cote de sa
  jambe la plus haute, SINON écarté (dans la boucle, s'applique à best/safest/dernier recours CdM). Écarte
  Mexique (1.47<1.74) ET Portugal (1.95 vs 1.87, +4 %). Les replis CdM ont rechoisi des combinés NON dominés
  (Mexique 1.70/lift 1.18, Portugal 1.90/lift 1.41).
- **Fuite legacy fermée** : avec catalogue Bet Builder présent, le combiné vient UNIQUEMENT de
  COMBOPICK/optimiseur (filtrés) — plus de repli sur le parseur `COMBO:` legacy non filtré (cas Chine-Taipei
  retiré).
- **Invariant selfcheck `combo_not_dominated`** (12e) : combiné à venir à `real_odds ≤ max(cote jambes)` = warn.
- **Régression vérifiée** : AST OK ; test `test_combo_rejette_domine_par_une_jambe` ; **11 tests combo OK** ;
  selfcheck **12/12**. Sidecars Mexique/Portugal recalculés (non dominés), Chine-Taipei retiré.
- **En suspens** : le repli CdM force encore un combiné même sur `COMBOPICK: NONE` (Portugal) -> à trancher
  (respecter le NONE en CdM, ou garder « 1 combiné/match »).

## 2026-07-02 (session) — condensé
- **Remote-control** : garde-fou singleton anti-doublon (`remote-control-loop.ps1`).
- **Onglets sport** : 2 courbes (Simples/Combinés), W/L + stats par courbe, en boutons, 14 pastilles.
- **Stats** : présentation compacte alignée sur les onglets sport ; repères de modèle **répartis par graphe**
  (simples→Simples, combinés→Combinés).
- **Règlement combinés** : cache de score **périmé** invalidé (`_score_incomplete` — LE fix qui débloque
  combinés ET simples) ; règleurs ajoutés (`PLAYERFB SOT +0.5`, `DCHALF`, `SCOREASSIST`) ; **void = ultime
  recours** (>3 j, donnée morte) ; jambe void affichée ➖.
- **Combinés (FOOT uniquement)** : cible **1.75–2.25**, jambe **≥ 1.10**, props joueur filtrées
  (auto-révisables via fantômes), **pricing lié au catalogue Bet Builder** (fini les cotes fantômes),
  repli = le PLUS SÛR (jamais le plus gourmand). **Hors-CdM (basket/tennis)** : combiné **seulement si
  value réelle** (EV>1), sinon abstention.

### ⚠️ Régressions SURVENUES cette session (à NE PLUS reproduire — origine de la règle ci-dessus)
- (a) Réglages combiné appliqués **par erreur au basket** (optimiseur partagé) → corrigé **sport-aware**.
- (b) Optimiseur **trop strict** (0.58/2.10) → **suppression de sidecars** + combiné **dégénéré à 1.03**
  → corrigé (repli le plus sûr + planchers `_COMBO_MEANINGFUL`/`_COMBO_LEG_MIN`).
- (c) **Cotes fantômes** (carte ≠ Unibet, ex. 2.07 vs 1.17) → jambes **liées au catalogue réel**.
- CAUSE : changements **enchaînés sans vérifier l'impact global**. → d'où cette procédure.

## Journal (à partir de maintenant)
- **2026-07-03** — **Règlement : BUT DANS LES 2 MI-TEMPS comblé + tirs mappés + INCIDENT re-règlement évité**.
  — objectif user : combler les trous de règlement « sans rien abîmer ». · **FAIT** : (1) `code_from_pick`
  mappe désormais tirs→`SHOTSOT/SHOTS` (au lieu de `return ""`) et « but dans les 2 MT »→`BOTHHALVES` ;
  règleur `settle_pick` gère SHOTSOT/SHOTS (stats sot_h/a, shots_h/a — même logique que CORNERS) et
  BOTHHALVES (periods, calqué sur TEAMBOTH) ; `need_stats`/`need_periods` étendus ; `sportradar.match_stats()`
  (NEW — tirs/tirs cadrés/corners via GISMO `match_details`) branché en repli après Flashscore. · **RÉSULTAT
  vérifié** : **BOTHHALVES = 72/72 corrects** (periods dispo) ✅ ; TIRS = mapping correct MAIS ni Flashscore
  ni GISMO ne couvrent les matchs CdM de cet env (0/6) → tirs non réglables ICI (void), réglables en prod
  réelle. · **⚠️ INCIDENT (leçon)** : bumper `_SETTLE_VERSION` 44→45 a déclenché un **re-règlement de masse**
  (297 sidecars) qui, faute de source de tirs sur les vieux matchs, aurait dé-réglé des combos. **DÉTECTÉ**
  (test « combos finis avec jambe non réglée ») → **bump ANNULÉ (retour v44)** → vérifié **0 combo dé-réglé,
  historique INTACT** (le settle loop ne re-traite PAS les combos déjà réglés, il garde leur résultat figé).
  · **régression vérifiée** : mapping = FORWARD-only (v44, pas de rétroactif) ; BOTHHALVES 72/72 ; codes
  existants inchangés (seuls tirs/2MT touchés) ; état stable settle_v={44:300} · **RÈGLE** : NE JAMAIS
  bumper `_SETTLE_VERSION` sans certitude que la donnée existe pour TOUS les matchs (sinon dé-réglage).
- **2026-07-03** — **AUDIT résolubilité des marchés + DOC des sources**. — pourquoi : demande user
  (revérifier tous les marchés Unibet, voir si nos sources les règlent, sourcer les trous, documenter,
  mémoriser) · **AUDIT data-driven** (codes/résultats des sidecars) : règlement quasi complet ✅ (vainqueur/
  DC/total/handicap/total équipe/BTTS, mi-temps foot, corners+cartons, sets/jeux/tie-break tennis, quart-
  temps+props basket). **1 trou** : foot « sans code » = 15 paris (tirs cadrés ~9, but 2 mi-temps ×3,
  corners 1re MT, props buteur). · **SOURCE trouvée** pour le trou n°1 : Sportradar GISMO `match_details`
  donne « Tirs cadrés / Tirs / Corners / Possession » (home/away, foot) — comme les aces au tennis. · **DOC**
  livrée : `docs/SOURCES.md` (NEW — matrice complète source×marché×règlement + trous §4) + endpoint
  `/health/markets` (`analyses.markets_coverage()`, matrice VIVANTE data-driven, dans /docs) · fichiers :
  `docs/SOURCES.md`, `app/analyses.py` (`markets_coverage()`), `app/main.py` (endpoint + import local
  `analyses`) · **régression vérifiée** : additif/lecture seule ; AST OK ; fix 500 (import `analyses`
  manquant dans main → import local comme les autres) → `/health/markets`=200 ; endpoints /selfcheck /stats
  =200 · mémoire `markets-resolvability-sources` créée · résultat : OK. **RESTE (à coder)** : règlement tirs
  cadrés (GISMO match_details → codes SOT) + but 2 mi-temps (periods déjà dispo → mapping).
- **2026-07-03** — **NOUVELLE SOURCE TENNIS : TennisExplorer (bilan par surface, gratuit + À JOUR)**. —
  pourquoi : le tennis manquait du facteur n°1 (niveau par surface) ; UTS/Sackmann périmés, RapidAPI/
  SofaScore en quota épuisé (429, 15000/mois plan PRO dépassé) · **TROUVÉ** après fouille : `tennisexplorer.com`
  est **à jour (données 2026)** et publie le bilan V/D **par surface** (Terre/Dur/Indoor/Gazon), carrière +
  année · fichiers : `app/tennisexplorer.py` (NEW — scrape HTML tolérant, cache, matching de noms par tokens,
  parse du tableau « Summary » ; UA explicite), `app/sources.py` (`_tennis_extras` : surface + `tennisexplorer.
  surface_facts()` en TÊTE des faits tennis) · **régression vérifiée** : additif + tolérant (try/except, jamais
  d'exception) ; AST OK ; testé en direct → Wimbledon : **Safiullin 23-16 gazon (9-3 en 2026) vs Fonseca 7-9
  (alors que Fonseca est #27 vs #132 ATP !)** ; extras() tennis 1075→2147 car., pas de plantage ; ~2 req/joueur
  cachées, gratuit (pas de quota) · résultat : OK, forward-looking (prochains scans tennis). **NB RapidAPI** :
  SofaScore RÉACTIVABLE via RapidAPI SportAPI7 (déjà branché `sofa_http.py`) mais quota mensuel épuisé → réserver
  au tennis si réactivé.
- **2026-07-03** — **Tennis : ajout de la SURFACE aux faits** (data la plus prédictive qui manquait) +
  constat sources. — pourquoi : tennis = pire ROI (**−38 %** sur 19 paris, sport aux données les + pauvres
  depuis SofaScore mort) ; user a choisi « trouver une source tennis » · **DIAGNOSTIC** : les archives Elo/
  surface idéales (**Ultimate Tennis Statistics, Jeff Sackmann GitHub**) sont **PÉRIMÉES** dans cet env
  (arrêtées ~nov 2024 : Alcaraz « Retired 2024 », Sackmann 2025/2026 = 404) → **NE PAS les brancher**
  (fausseraient l'analyse). Sources live OK : ESPN (classement+forme), Sportradar (forme+H2H+streaks),
  Flashscore. · **LIVRÉ** : `app/sources.py` `_surface_hint()` + `_TENNIS_SURFACE` (table tournois→surface)
  ; surface détectée via le **nom de TOURNOI ESPN** de la forme récente (fiable, ≠ la ville `comp` ambiguë :
  « Londres »=Wimbledon/gazon, « Stuttgart » a un tournoi terre ET un gazon) ; injectée en tête des faits
  tennis (`_tennis_extras`) · **régression vérifiée** : additif ; AST OK ; testé en direct → Wimbledon=Gazon
  sur 3 matchs, détection sur 81/112 sidecars (les non-détectés = tournois hors table, sans faux positif) ·
  résultat : OK, forward-looking (prochains scans tennis). **RESTE** : stats de service (Sportradar/
  Flashscore, à jour) ; surveiller si le tennis reste perdant (19 paris = petit échantillon).
- **2026-07-03** — **Cartes Telegram : coins BLEU-NOIR (fini le blanc) + largeur d'affichage UNIFORME**. —
  pourquoi : (1) sur Telegram les coins arrondis TRANSPARENTS étaient composés sur BLANC ; (2) Telegram
  réduit les images plus HAUTES → cartes de largeurs différentes selon la longueur du contenu · fix :
  `tools/card_image.py` nouveau post-traitement PIL `_normalize_card()` appelé après le screenshot : aplatit
  l'alpha sur un fond BLEU-NOIR `(8,12,20)` (coins sombres au lieu de blancs) ET normalise l'image à un
  RATIO FIXE `_CARD_RATIO=1.3` (padding de fond bleu-noir : vertical si carte courte, horizontal si haute)
  → **toutes les images ont le même ratio donc la MÊME largeur d'affichage Telegram**, seule la hauteur du
  contenu change · **régression vérifiée** : rendu image seul, no-op si PIL absent (jamais bloquant) ; AST
  OK ; 3 sports rendus → **ratio identique 1.3** (basket/foot/tennis), **coins = (8,12,20)** non blancs ;
  inspection visuelle foot+basket OK · résultat : OK ; republication en cours.
- **2026-07-03** — **Cartes Telegram : suppression du bord noir + largeur uniforme**. — pourquoi : demande
  user (bord noir à droite/bas ; tous les tickets doivent faire la même largeur peu importe le sport, seule
  la hauteur varie) · cause : le rendu fixait le viewport à `carte + 40px` puis capturait TOUT le viewport
  → la marge (+ marge du body) apparaissait en bande sombre · fichiers : `tools/card_image.py`
  (`html,body{margin:0;background:transparent}` ; capture CLIPPÉE sur le boundingRect EXACT de la carte
  via `Page.captureScreenshot(clip={x,y,w,h,scale:1})`, `deviceScaleFactor=2` conservé pour la HD) ·
  **régression vérifiée** : purement rendu image ; AST OK ; 3 sports rendus → **largeur identique 1840px**
  (foot/tennis/basket), hauteur variable (2612/2070/2008) ; inspection visuelle → plus aucun bord noir,
  carte pleine largeur · résultat : OK. NB : le fix s'applique aux PROCHAINES cartes ; republication des
  cartes déjà postées = sur demande (éviter de re-vider le canal sans nécessité).
- **2026-07-03** — **Cartes Telegram : coins arrondis BLANCS → transparents**. — pourquoi : après le clip
  exact, Chrome comblait les coins (hors carte arrondie) en BLANC par défaut · fix : `card_image.render_card`
  ajoute `Emulation.setDefaultBackgroundColorOverride({r,g,b,a:0})` avant la capture → PNG en RGBA, coins
  alpha 0 (se fondent dans le fond du chat) · **régression vérifiée** : test PIL sur le PNG rendu → mode
  RGBA, **4 coins alpha=0 (transparents)**, centre opaque (couleur carte) ; AST OK · résultat : OK.
- **2026-07-03** — **Cartes Telegram : CADRE BLEU style app + republication**. — pourquoi : demande user
  (cadre du même style que les cadres bleus de l'app) · l'app utilise `border:1px solid rgba(34,184,255,.60)`
  + halo accent ; reproduit sur `.card` : `border:2px solid rgba(34,184,255,.55)` + `box-shadow:inset 0 0 0
  1px rgba(34,184,255,.28), inset 0 0 80px rgba(34,184,255,.07)` (halo INSET car le clip exact couperait un
  halo externe ; cartes résultat gardent leur accent vert/rouge) · fichiers : `tools/card_image.py` (CSS
  `.card`) · **régression vérifiée** : rendu image seul ; AST OK ; carte rendue et inspectée (cadre bleu net,
  coins transparents, pas de bord) ; **republication faite** (`renotify_cards.py --hours 3`, sans crash grâce
  au fix encodage) : 7 cartes re-postées (Portugal prono+résultat, Corée, Australie-Égypte, Argentine-Cap-Vert,
  Colombie-Ghana, Las Vegas-Chicago ; 3 tennis abstention) · résultat : canal à jour au rendu final.
- **2026-07-03** — **FIX bug encodage `renotify_cards.py` (canal vidé sans republication)**. — pourquoi :
  le script plantait sur `UnicodeEncodeError` (✓/✗ en cp1252) au 1er `print` APRÈS `_clear_channel()` →
  canal VIDÉ mais republication avortée (l'utilisateur a vu ses messages récents disparaître) · fichiers :
  `tools/renotify_cards.py` (ajout `sys.stdout = io.TextIOWrapper(..., encoding='utf-8', errors='replace')`)
  · **régression vérifiée** : AST OK ; relancé → **7 cartes republiées** (Portugal prono+résultat, Corée,
  Australie-Égypte, Argentine-Cap-Vert, Colombie-Ghana, Las Vegas-Chicago ; 3 tennis en abstention ignorés)
  · résultat : canal restauré au nouveau format. **LEÇON** : le SCAN publie DÉJÀ au nouveau format
  (card_image à jour) → « supprime+republie » redondant pour les pronos frais ; renotify n'est utile que
  pour reformater d'ANCIENNES cartes postées avant une refonte du gabarit. Ne pas vider tout le canal sans
  nécessité.
- **2026-07-03** — **Bug notif Portugal-Croatie + garde-fou audit + scan/republication**. — (a) validation
  du **combiné gagné Portugal-Croatie** renvoyée sur Telegram (carte résultat jamais postée distinctement :
  `result_msg` pointait sur l'id du prono ; flags `notified_*` figés → aucune re-tentative) ; `result_msg`
  corrigé (nouvel id 704) ; (b) NOUVEAU contrôle `app/selfcheck.py:_check_result_card_posted` (détecte
  `notified_* && result_msg == id prono` = carte résultat non postée ; critère PRÉCIS → zéro faux positif,
  contrairement à « aucun result_msg » qui attrape 280 vieux matchs légitimes) ; (c) scan complet `--force`
  + republication au nouveau modèle via `renotify_cards.py`. — **régression vérifiée** : garde-fou read-only
  additif (9 checks, AST OK, vert après correction Portugal) ; diagnostic AVANT action → refus de reposter
  280 « suspects » (faux positifs, aurait spammé le canal de doublons) ; seul Portugal (symptôme précis)
  renvoyé · résultat : garde-fou OK ; scan+republication en cours.
- **2026-07-03** — **Telegram : lisibilité titre + libellé marché** (ajustement). — pourquoi : demande user
  (« Qualification… » trop petite ; le libellé marché « Cotes du match - Prolongations incluses » doit être
  de même taille que le pick et en blanc) · fichiers : `tools/card_image.py` (CSS `.topcomp` 17→20px ;
  `.legsel .mkt` 20→30px, couleur #8fa6c2→#eef4fb blanc, weight 600→700) · **régression vérifiée** :
  purement affichage, aucune donnée/règlement touché ; AST OK ; rendu inspecté · résultat : OK.
- **2026-07-03** — **Telegram : titre sport sur UNE ligne + air avant le nom des équipes** (ajustement du
  précédent). — pourquoi : demande user (pas de retour à la ligne après le sport ; compétition en plus petit
  à la suite pour tenir sur une ligne ; un peu plus d'espace au-dessus des 2 équipes) · fichiers :
  `tools/card_image.py` (`.top` repassé en inline — SPORT 30px + `.topcomp` 17px inline à la suite « · … » ;
  `.match` margin-top 12→20px) · **régression vérifiée** : purement affichage (titre), aucune donnée/règlement
  touché ; AST OK ; rendu inspecté (titre 1 ligne « BASKET · Qualification… », espace accru avant « Corée du
  Sud — Taipei Chinois ») · résultat : OK.
- **2026-07-03** — **Telegram : titre du sport agrandi + retour à la ligne logique**. — pourquoi : demande
  user (titre « Basket • Qualification… » trop petit, « - ASIE » coupé n'importe où) · fichiers :
  `tools/card_image.py` (`_card_html` : split de `cat` sur ` · ` → SPORT en gros sur une ligne, COMPÉTITION
  en casse normale sur la ligne suivante ; CSS `.top` 30px/900/flex + `.top .ico` 32px + nouvelle `.topcomp`
  23px) · **régression vérifiée** : purement AFFICHAGE (titre), aucune donnée/règlement touché ; `cat`
  conservé dans le dict (rétrocompat), simplement scindé au rendu ; `.top` est dans la partie COMMUNE de
  `_card_html` → titre cohérent sur publication ET résultat (vérifié visuellement sur combo « Qualification
  pour la Coupe du Monde - Asie » + résultat « NBL1 North ») ; AST OK · résultat : OK.
- **2026-07-03** — **Telegram : passage à la ligne marché/sélection + confirmation suppression du
  boilerplate synth**. — pourquoi : demande user (afficher « Marché - … : » sur une ligne PUIS la
  sélection dessous ; ne plus voir « Combiné optimisé sur la vraie cote… ») · fichiers : `app/card_data.py`
  (`_split_leg(sel, home, away)` → (marché, pick) : découpe sur le nom d'équipe en fin de libellé ou le
  marqueur « Plus/Moins de » ; combo legs = (marché, pick, cote, why), simple = market+pick),
  `tools/card_image.py` (helper `_selh` : marché discret « … : » + pick en avant sur 2 lignes ; CSS
  `.legsel/.mkt/.pk`) · **régression vérifiée (AVERTISSEMENT user : ne pas corrompre le règlement)** :
  (1) `_split_leg` appelé UNIQUEMENT dans `build_prono_card` (affichage) ; (2) le règlement lit
  `code_from_pick(leg['sel'])`+`leg['code']` du **SIDECAR**, jamais le libellé de carte ; `settle_analyst`
  n'importe pas `card_data` ; (3) `build_result_card` garde le `sel` ORIGINAL (pas de split) ; (4) **test de
  non-mutation : 60/60 sidecars → sel/code de règlement INCHANGÉS avant/après `build_prono_card`** ; AST OK,
  rendu combo inspecté (2 lignes OK, boilerplate absent) · **boilerplate** : `_clean_synth` droppe déjà
  62/62 des combo.why « optimisé… » (généré en dur ligne 1236 de generate_analyses, retiré à l'affichage) ;
  repli texte vérifié = ne contient pas le why · résultat : OK. + mémoire « penser en français » créée.
- **2026-07-03** — **Telegram : ANALYSE PAR JAMBE sur les cartes de PUBLICATION** (comme l'app), RIEN sur
  la carte résultat. — pourquoi : demande user (explication sérieuse/fiable/professionnelle par jambe) ·
  fichiers : `app/card_data.py` (`build_prono_card` : legs = (sel, cote, **why**) + `synth` du combiné ;
  `_pick_why` extrait le « pourquoi » du simple depuis le Verdict du .md via `_verdict_notes`+`_assign_notes`
  comme l'app ; `_clean_why` = pipeline app `_strip_sources`+`_units_to_pct`+`_sentence_case` **+ dé-branding
  `branding.debrand`** ; `_clean_synth` DROP la note technique auto-générée « Combiné optimisé sur la VRAIE
  cote … peu corrélées, chance estimée X% » et ne garde que les vraies synthèses de corrélation),
  `tools/card_image.py` (rendu `.legwhy` sous chaque jambe + `.synth` en tête pour type combo, `.legwhy`
  pour type simple ; **type result INCHANGÉ**) + CSS. · **régression vérifiée** : carte RÉSULTAT
  (`build_result_card`/type=="result") NON touchée (vérifié : aucun champ why, rendu sobre identique) ;
  legs de publication passées de 2-uple à 3-uple gérées dans le seul renderer `card_image` (les autres
  consommateurs — reconcile/renotify/settle — itèrent les legs SIDECAR (dicts), pas les tuples de carte →
  pas d'impact) ; 3 cartes réelles rendues et **inspectées visuellement** (combo : analyse par jambe pro +
  synth réelle, boilerplate supprimé, Pinnacle→« référence sharp » ; simple : rationnel complet ; résultat :
  sobre) ; imports à froid OK ; de-brand testé sur texte plat · **NB** : le dé-branding des cartes Telegram
  est INCONDITIONNEL (le produit ne doit jamais nommer une source, indépendamment du flag web public) ·
  résultat : OK.
- **2026-07-03** — **MODE PUBLIC / MASQUAGE DES SOURCES** (`app/branding.py`) : le stack de données =
  avantage compétitif → en public, personne ne doit voir les sources. UN SEUL interrupteur
  `hide_sources()` (env `BETSFIX_HIDE_SOURCES` OU fichier `data/hide_sources.flag`), **OFF par défaut**
  (phase de test = inchangée). Quand ON, pour tout visiteur NON propriétaire : (1) `debrand(html)` retire
  les **boutons-liens** vers les fiches SofaScore/Unibet + neutralise les URLs sources + remplace les NOMS
  affichés (« Cote Unibet »→« Cote marché », « SofaScore »→« les données », Flashscore/LiveScore/FotMob/
  Understat/Sportradar→« nos sources », Pinnacle→« référence sharp ») SANS toucher aux classes CSS ;
  (2) middleware `_gate_private` → **404** sur `/docs` `/redoc` `/openapi.json` (Swagger nomme les sources
  dans ses tags), les outils internes `/health/selfcheck|learning|backtest`, et les proxies source-nommés
  `/sportradar|/unibet|/flashscore|/livescore|/pinnacle`. Le **propriétaire** (local OU email dans
  `owners.json`, nouveau `accounts.is_owner`) voit TOUJOURS tout. — pourquoi : demande user (« quand l'app
  sera publique je ne veux pas que l'on voie les sources ») · fichiers : `app/branding.py` (NEW),
  `app/accounts.py` (`is_owner`), `app/main.py` (import + hook dé-branding dans `_paywall_dispatch` + gate
  `_gate_private`) · **régression vérifiée** : OFF par défaut → matrice testée : FLAG OFF toutes routes 200
  + sources visibles (inchangé) ; FLAG ON public → Unibet/SofaScore=0, liens sources supprimés, classes CSS
  `lnk-bn-sofa` INTACTES (mise en page OK), libellés neutres présents, `/docs`+`/unibet`=404 ; FLAG ON
  owner → tout visible + `/docs` 200 ; audité que le client public n'appelle AUCUN endpoint source
  (fetch = `/stats` + fragments match uniquement) → gate des proxies sûr · résultat : OK, interrupteur prêt
  pour la mise en public. **RESTE (à décider)** : valider le vocabulaire neutre ; endpoints GÉNÉRIQUES
  (`/matches` `/statistics` `/players` `/analysis`) non gatés (leur chemin ne nomme pas de source, et /docs
  masqué les rend non-découvrables) — à gater aussi si on veut le verrou maximal.
- **2026-07-03** — **BACKTEST / BAC À SABLE** (`app/backtest.py`, Phase 3, 100 % lecture seule). Rejoue la
  PORTE de prod (`_recommend` : conf recalibrée ≥ min_conf, cote < plafond, zone 1.70→72 %, EV ≥ plancher)
  sur les **1693 prédictions fantômes réglées** (20×+ le volume des paris joués → seul moyen d'estimer un
  seuil sans surapprendre). Découpage temporel train/test 70/30, IC ROI (±1.96·SE) + Wilson. Balaye
  min_conf/ev_floor/odds_cap ; **ne PROPOSE un changement que si la borne basse de l'IC candidat (test) >
  ROI test actuel** (amélioration hors-échantillon significative). **N'applique JAMAIS rien** (appliquer
  reste explicite). Garde-fou `validate_against_prod` = la porte reproduit la publication prod à **100 %**.
  Surfaces : `/health/backtest` + CLI `tools/policy_backtest.py` (journal `data/backtest_log.jsonl`, alerte
  Telegram si repérage significatif) + branché fin de `scan_daily.ps1`. — pourquoi : demande user
  (« augmenter ROI/fiabilité, recalibrer de jour en jour ») · fichiers : `app/backtest.py` (NEW),
  `tools/policy_backtest.py` (NEW — PAS d'écrasement de l'ancien `tools/backtest.py` = calibration Elo
  tennis SofaScore hérité/mort, laissé intact), `app/main.py` (endpoint), `deploy/scan_daily.ps1` (hook) ·
  **régression vérifiée** : (1) perf — 1er run rejouait `excluded_markets` (→ calibration+perf_breakdown)
  1700×18 fois = bloquait ; corrigé par cache `_EXC_CACHE` (résultat 1-2 s) ; (2) bug `d['result']` est un
  DICT `{pick_result}` pas une chaîne → faussait le garde-fou (0/0) ; corrigé (`_result_str`) → fidélité
  100 % ; (3) comparaison alignée sur la porte de PUBLICATION (for_history=False, avec exclusions) ; AST OK,
  endpoints tous 200 · **RÉSULTAT MÉTIER** : la politique actuelle sélectionne déjà un sous-ensemble à ROI
  **+15,2 %** (n=61) ; AUCUN changement de seuil n'est significatif hors-échantillon → **verdict : garder**
  (le moteur REFUSE d'overfitter, ex. ev_floor=8 = +28 % overall mais IC bas −22 %). Signal à surveiller :
  odds_cap 1.70 (ROI test +21,3 %, IC bas −4,9). · résultat : OK.
- **2026-07-03** — **JOURNAL D'APPRENTISSAGE** (`app/learning.py`, Phase 2 de l'auto-amélioration, choix
  user). Photo quotidienne des métriques (fiabilité, calibration, ROI, exclusions per-sport, props combiné),
  DELTAS vs la veille, et **auto-écriture des événements notables** dans `LEARNING.md` (marché écarté /
  ré-intégré tout seul, mouvement de fiabilité ≥3 pts ou ROI ≥3 pts). Stockage `data/learning_log.json`
  ({date: snapshot}, idempotent par jour). Exposé `/health/learning` (JSON : today + deltas + série
  historique + événements) + CLI `tools/learning.py` + branché en fin de `scan_daily.ps1` (après selfcheck).
  — pourquoi : demande user (« voir le modèle progresser jour après jour ») · fichiers : `app/learning.py`
  (NEW), `tools/learning.py` (NEW), `app/main.py` (endpoint), `deploy/scan_daily.ps1` (hook) · **régression
  vérifiée** : purement ADDITIF/observateur (n'écrit QUE son propre journal, aucune logique
  sélection/règlement/affichage touchée) ; détection d'événements VALIDÉE sur une veille synthétique (🔴
  Sets/Total/Vainqueur écartés, props, fiabilité +5, ROI +4.2 correctement détectés) ; baseline réelle du
  jour enregistrée ; AST OK, `/health/learning`+`/health/selfcheck`+`/stats` = 200 · résultat : OK.
- **2026-07-02** — **AUTO-AUDIT d'intégrité** (`app/selfcheck.py`, 100 % lecture seule) : socle anti-confusion.
  8 contrôles, chacun encode une RÉGRESSION DÉJÀ SURVENUE — intégrité sidecars, combiné publié avec jambe
  non réglée (nuance : 'perdu' décidé tôt = légitime), cohérence résultat↔jambes, cotes combiné (invariant
  DUR total=produit + bande souple real_odds CALIBRÉE sur données n=60), cotes/proba valides, aucun
  règlement avant fin de match, **compteur stats monotone** (filigrane `data/selfcheck_state.json`),
  calibration exhaustive. Exposé `/health/selfcheck` (JSON) + CLI `tools/selfcheck.py` (journal
  `data/selfcheck_log.jsonl`, alerte Telegram SEULEMENT sur erreur) + branché en fin de `scan_daily.ps1`
  (après reconcile). — pourquoi : demande user (« deviens l'outil le plus perfectionné, évite de te tromper
  ou confondre les stats, apprends/corrige/vérifie de jour en jour ») · fichiers : `app/selfcheck.py` (NEW),
  `tools/selfcheck.py` (NEW), `app/main.py` (endpoint), `deploy/scan_daily.ps1` (hook) · **régression
  vérifiée** : purement ADDITIF, aucune logique sélection/règlement/affichage touchée ; d'abord lancé en
  DÉTECTION → a trouvé 2 faux positifs que J'AI CORRIGÉS en calibrant les seuils SUR LES DONNÉES (combos
  'perdu' décidés tôt = normaux ; ratio real/produit 0.70–1.42 = corrélation normale, pas fantôme) → ne
  reste qu'1 vrai warn mineur (Angleterre–Congo total 2.61≠produit 2.53) ; AST OK, endpoints tous 200,
  filigrane+journal écrits · résultat : OK, socle en place pour l'auto-optimisation sûre (phases suivantes).
- **2026-07-02** — **Marchés écartés PROPRES À CHAQUE SPORT** (avant : exclusion globale). — pourquoi :
  demande user (« les marchés écartés doivent être propres à chaque sport ») · fichiers : `app/analyses.py`
  (nouv. `_excluded_by_sport()` + `excluded_markets(sport)` ; `auto_exclusions()` renvoie désormais l'UNION
  per-sport pour l'aperçu ; `exclusions_report()` restructuré par sport ; **3 callers de sélection**
  677/1298/1367 + `_reco_event` passés à `excluded_markets(sport)`), `app/web.py` (`render_exclusions()`
  par sport + CSS `.exq-sport*`) · **régression vérifiée** : (1) `auto_exclusions` est PARTAGÉE — repérée et
  disséquée AVANT ; les 3 sélecteurs + le bandeau `web.py:3097` (union) audités ; (2) `for_history` conserve
  bien `set()` (aucune exclusion en historique — compteur monotone intact) ; (3) **impact réel mesuré** :
  foot={Corners} et tennis={Sets} INCHANGÉS vs global ; SEUL le basket change (Vainqueur −8 / Total −9
  désormais écartés — sur-confiance que le global DILUAIT ; le basket garde Handicap +7) → tightening voulu,
  pas une régression ; AST+imports OK, endpoints /,/foot,/app,/basket,/stats,/directs = 200 · résultat : OK.
- **2026-07-02** — **Page Stats raccourcie** : 4 sections de détail (Edge / Fiabilité / Marchés écartés /
  Transparence) rendues **repliables** (accordéon natif `<details>`, sans JS), **fermées par défaut** ; la
  VUE D'ENSEMBLE reste toujours ouverte. — pourquoi : demande user (« la page des stats commence à être bien
  longue ») · fichiers : `app/web.py` (nouv. `sx_section_collapsible()` + CSS `.sx-acc*`), `app/routers/web.py`
  (`_home_stats` utilise le helper repliable) · **régression vérifiée** : `sx_section` (non repliable) laissé
  INTACT (autres appelants éventuels non touchés) ; markup `<details>/<summary>` **équilibré** (4/4), overview
  hors accordéon (avant le 1er `<details>`), 0 ouvert par défaut ; charts/pastilles-repères SVG rendent bien à
  l'ouverture (aucun JS ne dépend de la visibilité) ; endpoints tous 200 · résultat : OK.
- **2026-07-02** — Stats : nouvelle section **« Marchés écartés »** (transparence : quels types de paris
  sont exclus, pourquoi, seuils d'exclusion/réintégration selon le taux de réussite). — pourquoi : demande
  user (voir ce qui est écarté et pourquoi) · fichiers : `app/analyses.py` (`exclusions_report()` — READ
  ONLY), `app/web.py` (`render_exclusions()` + CSS), `app/routers/web.py` (section dans `_home_stats`) ·
  **régression vérifiée** : additif pur (aucune logique règlement/sélection touchée), AST+imports OK,
  endpoints /,/foot,/app,/basket,/stats,/directs tous 200, section rendue avec vraies données (14 lignes) ;
  corrigé au passage un libellé trompeur (« ROI OK » alors que ROI<0 sur <25 joués) · résultat : OK.
- **2026-07-02** — Mise en place du process anti-régression + `HISTORIQUE.md`. — pourquoi : trop de
  régressions au fil des optimisations (demande user) · fichiers : `HISTORIQUE.md` (doc, aucun code
  applicatif touché) · **régression vérifiée** : sans objet (documentation) · résultat : règle active,
  journal démarré.

## 2026-07-03 — Tirs cadrés/tirs foot : comblés par FotMob (source déjà branchée)
- **Contexte** : reproche user justifié — j'ai cherché longtemps une source externe pour régler les
  tirs cadrés (Flashscore/GISMO/TennisExplorer/RapidAPI…) alors que **FotMob**, DÉJÀ branché, expose
  ces stats. Leçon notée en mémoire (`check-connected-sources-first`).
- **Fait** : `sources.foot_match_stats(client, home, away, start)` (app/sources.py) lit FotMob
  `matchDetails` (`content.stats.Periods.All`, clés `ShotsOnTarget`/`total_shots`/`corners`/
  `yellow_cards`/`red_cards`) → `sot_h/a`, `shots_h/a`, `corners_h/a`, `cards_h/a`.
  Branché en **source n°1** du règlement foot dans `settle_analyst` (avant Flashscore → repli GISMO).
- **Anti-régression** : AST OK (settle_analyst + sources) ; `settle_v` stable {44:300, None:16} (aucun
  re-règlement de masse, pas de bump). Test ciblé : **8/8** combos tirs réglés correctement via FotMob
  (Allemagne +4.5 tirs cadrés=won, Cap-Vert -2.5=won, Total +8.5=won, etc.). FORWARD-only.
- **Docs** : `docs/SOURCES.md` §2 (FotMob règlement) + §3 (tirs ✅) + §4 (trou n°1 barré). Mémoires
  `markets-resolvability-sources` + `check-connected-sources-first` (nouvelle) + index MEMORY.md.

## 2026-07-03 (soir) — Correction complète du passé + garanties de règlement
Demande user : « corriger tout le passé, que tous les pronos non réglés le soient ». Méthode : dry-run
AVANT toute écriture (aucune régression tolérée).
- **Audit du passé (lecture seule)** : 14 combos + 21 paris re-vérifiés via le VRAI moteur (mêmes fonctions
  `_leg_metric`/`_eval_leg`/`settle_pick`) sur score cache + FotMob. Verdict : **0 combo au verdict faux**
  (les jambes tirs étaient déjà réglées via la métrique live ; seul le `code` stocké était vide = cosmétique)
  et **0 pari simple mal réglé** (les « 22 au code changé » = faux signaux : divergence ligne `pick` vs pari
  affiché `bets[0]`, le moteur suit `bets[0]`). Rien à réécrire sur l'historique.
- **Régression attrapée PAR le dry-run** : `foot_match_stats` renvoyait 0/0 sur un match FotMob non couvert
  (Série B brésilienne) → le merge `{**cur, **fm}` écrasait les vrais cartons du cache (faux won→lost sur
  Ceará-Avaí). FIX : (1) `foot_match_stats` renvoie None si tirs tous nuls (garde anti-faux-zéros) ;
  (2) merge `{**fm, **cur}` (FotMob COMBLE, n'écrase jamais le cache fiable). Dry-run re-vérifié : 0 faux.
- **Bug de complétude corrigé** : un combo dont AUCUNE source n'a jamais le score (ligues obscures : basket
  féminin « petits pays », qualifs FIBA) restait pending À VIE (le `continue` du bloc `not score`
  court-circuitait avant l'incrément de tries et la logique void). AJOUT d'un VOID de dernier recours dans
  ce bloc : match fini + âge ≥ `_VOID_AFTER_DAYS` (3j) + score introuvable partout → void (remboursé).
  Appliqué à Malte(F)-Arménie(W) (6j, introuvable partout) ; les 2 matchs du jour restent en ré-essai.
- **Compteur monotone (selfcheck)** : l'alerte « 72 < 73 » était un FAUX positif préexistant — le check
  lisait `stats_full().settled` (proxy qui exclut les combos pré-`_COMBO_COUNT_FROM` et retombe sur un
  recalcul live fluctuant). FIX : le check mesure désormais le VRAI invariant = nb de `stat_bet` FIGÉS
  (immuables, monotones). Backfill du seul pari réglé non figé (tennis Mochizuki). Filigrane recalé à 77.
- **Anti-régression** : AST OK (5 fichiers) ; imports globaux OK ; `settle_pick` standard OK (OVER/SHOTSOT/
  BOTHHALVES) ; selfcheck 8/9 ✅ (reste 1 warn PRÉEXISTANT : cote combo Angleterre-Congo = bet builder
  même-match, total≠produit normal, résultat lost correct — non lié). Backups sidecars dans scratchpad.

## 2026-07-03 (soir) — Audit démarche + durcissement structurel (analyse & règlement)
Audit complet des 2 pipelines (2 agents Explore) : démarche STRUCTURÉE et reproductible dans son squelette
(déclenchement à params fixes ; analyse = séquence fixe + méthodo centralisée METHODO/COMBO_MISSION per-sport ;
règlement = code_from_pick unique + chaîne de repli stricte + idempotence _SETTLE_VERSION/settle_v/tries +
void garanti). Le CONTENU rédigé par Claude varie (normal, LLM). 3 points durcis :
- **(1) Traçabilité de complétude** : `sources.extras(client, sport, match, prov=dict)` (param OPTIONNEL,
  non-cassant pour probe_sources) remplit `prov` avec les sources ayant répondu ; `build_dossier` la met
  dans `meta["sources_prov"]` ; `_write_sidecar` écrit `side["sources"]` + `side["data_score"]`. Nouvel
  invariant selfcheck `_check_data_completeness` (10e check, forward-only, warn dès 3 fiches à data_score 0
  = analysées sur COTES SEULES). Testé : Australie-Égypte → {fotmob,flashscore,sportradar}=3.
- **(2) Matching par noms strict** (`settle_analyst._find_score`) : collecte TOUS les candidats des events
  du jour ; si PLUSIEURS matchent (sigles courts ambigus) → s'ABSTIENT (None) au lieu d'accepter le 1er →
  jamais un faux score sur un mauvais match.
- **(3) Garde anti-faux-zéros GÉNÉRALISÉE** : helper unique `settle_analyst._merge_stats(cur,new)` (comble
  sans écraser + ignore sot/shots tous nuls = donnée absente) appliqué aux 3 merges (FotMob/Flashscore/GISMO)
  au lieu de merges dispersés. Testé unitairement (faux-zéros ignorés, cartons/corners préservés).
- **Anti-régression** : AST 4 fichiers OK · imports globaux OK · tests unitaires _merge_stats/_find_score OK ·
  selfcheck 10 checks (compteur figé 77 vert ; reste 1 warn PRÉEXISTANT Angleterre-Congo cote bet-builder).

## 2026-07-03 — Selfcheck 100% vert (dernier faux positif pricing)
- Le warn « total≠produit » (Angleterre-Congo) = faux positif : écart 3,2% dû au RAFRAÎCHISSEMENT des cotes
  de jambes après figement du `total` (pas une cote fantôme). Seuil dur `_check_combo_pricing` élargi
  0.03→0.05·prod (5%) : absorbe les décalages normaux, attrape toujours le grossier (×2). Selfcheck = 10/10 ✅.

## 2026-07-03 — Phase 4 : Santé des sources (surveillance proactive)
Nouveau module `app/source_health.py` : ping LÉGER en parallèle des 8 sources (analyse + règlement),
avec latence + statut. CRITIQUES = Unibet (sélection+cotes) + FotMob (foot analyse+règlement tirs) →
down = error ; les 6 autres (Pinnacle/ESPN/Understat/Flashscore/LiveScore/Sportradar) → down = warn.
Pings validés en réseau (réutilise `unibet.matches`/`livescore.matches`/`sportradar.gismo` + GET stables
FotMob/ESPN/Understat/Pinnacle/Flashscore). Complète la traçabilité de complétude PAR FICHE par une
surveillance GLOBALE en amont (détecte une source morte AVANT qu'elle dégrade les analyses).
- Surfaces : `GET /health/sources` (+ gaté en mode public) · CLI `tools/source_health.py` (journal
  `data/source_health_log.jsonl` + alerte Telegram SI source critique down) · branché fin `scan_daily.ps1`.
- Testé : 8/8 vertes (Unibet 214 matchs, FotMob/ESPN/Understat/Pinnacle/Flashscore/Sportradar OK,
  LiveScore 89), latences 180–700ms. AST + imports OK, endpoint enregistré, journal écrit.

## 2026-07-03 — Tableau de bord santé dans l'onglet Stats (privé)
Visibilité de l'auto-surveillance dans l'UI (fini les CLI pour vérifier l'état) : section repliable
« 🩺 Santé du système » en bas de l'onglet Stats = santé LIVE des 8 sources (pastille + latence + détail)
+ les 10 contrôles d'auto-audit (pastille par check). 
- **Confidentialité** : le panneau NOMME les sources (avantage compétitif) → servi via une route AJAX
  dédiée `/stats/health` protégée `accounts.is_owner` (chaîne VIDE = bloc invisible pour les visiteurs).
  Chargé en AJAX pour rester HORS du fragment Stats mutualisé (cache) et ne pas bloquer le rendu (ping
  sources ~1-2s). `/health/sources` + `/health/markets` ajoutés au gate `_PRIVATE_WHEN_PUBLIC`.
- **Impl** : `_system_health_html()` (selfcheck.run + source_health.check_all) rendu via
  `web.sx_section_collapsible` (styles inline, cohérent thème sombre/cyan) ; conteneur `#syshealth` +
  fetch injectés dans `stats_page`. `Request` ajouté aux imports de routers/web.py.
- **Testé** : AST + import app OK ; routes `/stats/health` + `/health/sources` enregistrées ; endpoint live
  8/8 sources en ligne ; `/stats/health` (localhost=propriétaire) → 200, 6.5 Ko ; aperçu visuel validé.

## 2026-07-04 — Résolution doublon de tâches de scan (cause des SKIP quotidiens)
Diag après reconnexion : le scan planifié de 09h SKIP tous les jours depuis le 27/06. CAUSE = DEUX tâches
de scan concurrentes en compte vince :
- `BETSFIX-analyses` (créée 06/06, ANCIENNE) : `generate_analyses --top 5`, 2×/jour (09h+16h). Tourne
  longtemps → fait SKIP la nouvelle.
- `BETSFIX Scan` (créée 26/06, COMPLÈTE) : `scan_daily.ps1` (scan+reconcile+selfcheck+learning+backtest+
  source_health), 1×/jour 09h. SKIP car l'ancienne tournait déjà → le pipeline d'auto-surveillance ne
  tournait JAMAIS via sa tâche (règlement quand même assuré par le settle loop de l'API en continu).
FIX (choix user = scan complet 2×/jour) : `Disable-ScheduledTask BETSFIX-analyses` (réversible, pas
supprimée) + `Set-ScheduledTask BETSFIX Scan` triggers 09h **+ 16h**. Résultat : pipeline COMPLET 2×/jour,
plus de SKIP. Le scan 11404 en cours (lancé par l'ancienne avant désactivation) non interrompu.
NB vérifié ce jour : traçage `data_score` opérationnel en prod (fiches à data_score=3, sources fotmob/
flashscore/sportradar) ; règlements rattrapés par le settle loop (3 combos CdM réglés : Australie-Égypte
gagné, Argentine-Cap-Vert perdu, Colombie-Ghana gagné).

## 2026-07-04 — Règlement combinés : PROPS JOUEUR réglables (cause racine Las Vegas)
Cas Las Vegas-Chicago (combo bloqué « en attente » alors qu'une jambe était perdue).
- **(A) Décision anticipée — ANNULÉE** : j'avais ajouté `if any_lost: combo["result"]="lost"` (trancher
  perdu dès qu'une jambe perd) + une garde carte. Les TESTS (`test_combo_legs_complete.py`) ont révélé que
  ça CONTREDIT la règle métier établie [[combo-publish-all-legs]] : on attend CHAQUE jambe (budget 8 essais)
  avant de trancher/publier. **Reverté** (A + garde). Le vrai bug était (B), qui résout Las Vegas seul.
- **(B) Props joueur réglables** (`code_from_pick` PLAYERBK) — GARDÉ : le nom du joueur était POLLUÉ par le format
  Unibet « {Joueur} - {stat} - Prolongations incluses » → `basket_player_stat` renvoyait None → jambe jamais
  réglée. Fix : couper au 1er tiret SÉPARATEUR (` - `, espaces → épargne « Jean-Pierre ») + retrait
  « prolongations incluses ». Testé 6 formats (Jackie Young/Chelsea Gray/LeBron/Jokic/seuil 20+/PRA) OK.
  Box-score ESPN avait les données (Jackie Young AST=5→won, Chelsea Gray AST=6→won). Audit : SEULES 2 jambes
  affectées (toutes Las Vegas) — aucun autre combo/pari/shadow historique.
- **Rattrapage Las Vegas** : 2 jambes réglées (won/won), combo=lost (toutes jambes réglées, une perdue),
  notif ré-armée → carte complète re-postée (auto-réparation). Backup sidecar.
- Anti-régression : AST OK ; fix B testé sur 6 formats de props ; audit historique = 0 autre cas ;
  **13 tests de règlement combiné PASSENT** (dont test_combo_legs_complete qui a bloqué le revert de A).
  MAJ test_half_handicap : « Plus de 20.5 tirs » → `SHOTS OVER 20.5` (tirs réglables depuis le 03/07).
  LEÇON : lancer les tests AVANT de valider un changement de logique de règlement (A aurait cassé la règle).

## 2026-07-04 — Combinés TENNIS sur-cotés (produit au lieu de la cote corrélée Unibet)
User (screenshots) : app BETSFIX affiche combiné Lehecka Set1+Match @ **1.83** (produit 1.28×1.43), Unibet
affiche **1.44** (vraie cote corrélée Bet Builder). Notre `unibet.betbuilder_odds` confirme 1.44.
CAUSE (2 bugs) :
1. **Tennis exclu du pricing** : `build_dossier` ne récupérait le catalogue Bet Builder que pour
   `sport in ("foot","basket")` (commentaire faux « tennis : 0 prepack ») → le tennis A un catalogue (196
   outcomes) → étendu à `("foot","basket","tennis")`.
2. **Vainqueur de match non canonisé** : `_resolve_combo` ne matchait pas « X gagne » ↔ catalogue « Cotes
   du match X » (Jaccard trop faible) → jambe match non résolue → tout le combiné retombait au produit.
   Fix `_normalize_leg_sel` : « X gagne / vainqueur / remporte » (hors set/jeu/mi-temps) -> « cotes du match X ».
   Testé : `_resolve_combo` résout maintenant les 2 jambes -> `betbuilder_odds` OK. Non-régression : libellés
   non-vainqueur INCHANGÉS (total/BTTS/set/tirs/corners), double chance intacte, auto-vérif cote ±12% protège.
- **Forward-only** : les prochains scans tennis pricent la vraie cote corrélée. Existant : 3 basket qualifs
  Afrique = pas de Bet Builder (produit légitime) ; Lehecka en cours = re-pricing live rejeté (cotes bougées)
  -> `real_odds` figé à 1.44 (valeur avant-match vérifiée). AST OK.

## 2026-07-04 — Prévention : garde-fou « combiné TOUJOURS à la vraie cote corrélée »
Demande user (suite du bug tennis 1.83≠1.44) : ne plus refaire l'erreur + en tenir compte à la CRÉATION.
- **Garde-fou création** (`_make_combo`) : un combiné BETSFIX est toujours même-match -> jambes corrélées ->
  sa cote DOIT être la vraie cote corrélée Unibet. Si `real_odds` absent (repli produit = SUR-évaluation ->
  fausse value/EV), le combiné n'est PAS retenu (abstention, log). Impact mesuré : n'écarte que les combinés
  non plaçables (matchs sans Bet Builder, ex. 3 basket qualifs Afrique) ; foot/tennis à venir tous déjà
  pricés corrélé -> zéro régression sur les combinés légitimes.
- **Invariant selfcheck n°11** `_check_combo_correlated_pricing` : combiné À VENIR encore au produit
  (real_odds=None) -> warn. Filet qui aurait capté le bug tennis + toute future régression du pricing.
- **Anti-régression** : AST OK ; **21 tests combiné PASSENT**. 2 tests `test_combo_calibrated_ev` étaient
  DÉJÀ rouges AVANT mes edits (confirmé par stash) — PÉRIMÉS depuis `_COMBO_REAL_MAX` 4.20→2.25 (02/07) :
  `_fake_bb` renvoyait 4.0 > 2.25. Corrigés (cote test 1.40 -> 1.96 dans la fourchette) ; test « barrière
  longshot » repositionné foot->basket (le FOOT garde un combiné phare de repli, la barrière dure = non-foot).

## 2026-07-13 — Règlement « à tout prix » des fantômes coincés (5 fixes durables + FIBA)
Demande user : « au lieu de voider, trouver une solution de règlement à tout prix ». 28 fantômes
(calibration) coincés « en attente » sur matchs terminés. Diagnostic : 3 BUGS de règlement + 1 lacune
de source + 1 défaut de génération — PAS un vrai manque de données.

**Fixes durables (anti-régression : n'affectent que la récupération, pas la logique de verdict) :**
1. `settle_analyst._settle_analyses_impl` : `need_stats` ignorait les `shadow_codes` → un tirs/corners/
   cartons présent UNIQUEMENT en fantôme ne déclenchait jamais la récupération des stats. Ajout des
   shadow_codes (def remontée). Débloqué tout le foot (FotMob/Flashscore avaient la donnée).
2. Même bloc : `result.raw.stats` PARTIEL ({goals_1h_total…} sans sot/corners/cards) faisait échouer
   `not score.get("stats")` → refetch sauté. Ajout d'un test « clé de stat précise manquante ».
3. `sources._bb_player_stat` : matching strict `qtok <= _tok(nm)` échouait sur un `who` TRONQUÉ figé
   dans le code (« Angel Rees »→« Reese », « Ogunbowal »→« Ogunbowale »). Nouveau `_name_tok_match`
   tolérant à la troncature (préfixe, ≥4 lettres), sécurité « 1 seul joueur = 1 règlement » conservée.
4. Tennis « Nombre total de services breakés » : `code_from_pick` le lisait comme total générique
   (« OVER 5.5 » car contient « total ») → code non règlable. Détection `TOTBREAKS` AVANT le total +
   handler via nouveau `flashscore.total_breaks` (somme « Break Points Converted » section Match).
5. Garde-fou génération : `code_from_pick` PLAYERBK — un `who` placeholder (« Points marqués joueur »,
   sans participant capté) retourne "" (abstention) au lieu d'émettre un prop indéfini/un total bidon.

**Source manquante (basket international, hors WNBA/NBA) :** Corée-Taipei & Malte-Armenia = FIBA, non
couvert par Sportradar/ESPN/LiveScore/Flashscore (tous None). Scores/quart-temps VÉRIFIÉS sur FIBA
officiel (source de vérité) et injectés : Malte 107-52 Armenia (MT 60-20) ; Corée 80-82 Taipei OT
(MT 41-30). ⚠️ RÉCUP : le combiné Malte (Malta -16 + Total >125.5) était void faute de score → en fait
GAGNÉ (les 2 jambes). HCAP -16/Vainqueur restent hors ROI (marchés basket exclus = abstentions).

**Résultat :** 28 → 0 fantômes pending. PICKS/COMBINÉS non réglés = 0. Selfcheck 0/0.

## 2026-07-13 (2) — Transparence : extrait d'analyse sur TOUTES les cartes provisoires
Demande user (2 captures) : des cartes de pari provisoire affichent le pari + cote + confiance mais
PAS le « pourquoi » (barre cyan) — ex. Jesper De Jong, Clement Tabur — alors que d'autres l'ont
(Djurgården, Alina Charaeva). Cause : `web._prov_why_snippet` ne lisait QUE la section « 🧪 Pari
provisoire » du .md ; quand l'analyste ne l'écrit pas, l'extrait était vide -> carte « sans analyse ».
Fix : repli en cascade sur « 🎯 Le pari à jouer » puis « 📋 Les faits » (même nettoyage + filtre
méta anti-abstention). Résultat : 0 carte provisoire sans extrait (4/4 des exemples ont désormais leur
lecture de match). Purement AFFICHAGE — rien touché au ROI/stats/calibration.
Obs. séparée à trancher : 3 matchs restent abstenus SANS provisoire (provisional=None, ID Unibet ≠ ID
sidecar) -> totalement cachés (règle 2026-07-10 « no-value sans provisoire = caché »).

## 2026-07-13 (3) — Dédup jambes de combiné du jour PAR NOM (pas seulement par id)
Reproche user : une jambe du combiné du jour (Atlanta Dream–LA Sparks) réapparaissait AUSSI en pari
provisoire — le doublon « même erreur à plusieurs endroits » qu'on voulait éviter. Cause = la dédup
(`_programme_items`) comparait `day_programme.id` (id Unibet 1026378509) à `leg_ids` (mid combiné
15415813) : IDs DIFFÉRENTS pour le même match -> dédup ratée. Fix : nouveau `combo_daily.leg_names(day)`
(paires home/away) + dédup PAR NOM via `_prog_pair` (robuste à l'écart d'id Unibet↔sidecar), en plus de
l'id. S'applique à l'accueil ET aux onglets sport (tous via `_programme_items`). Vérifié : Atlanta Dream
n'est plus QUE dans la carte combiné.

## 2026-07-13 (4) — Carte plus lisible : confiance colorée + verdict groupé
Demande user (capture carte provisoire WNBA) : rendre la carte plus lisible/intuitive. Maquette
avant/après proposée (4 réglages) ; user a retenu 2 via AskUserQuestion : (1) confiance colorée
(barre + % par niveau) et (2) verdict groupé (confiance ↔ cote sur une même bande). Implémenté :
helpers `web._conf_hue` (rouge <55 / ambre 55-67 / vert ≥68) + `web._verdict_strip` (bande confiance
gauche + cote droite, ré-analyse sous la barre ; repli auto sur l'ancien pied si pas de confiance).
CSS `.mc-verdict/.mc-vc/.mc-vbar`. Appliqué aux DEUX cartes (provisoire `_programme_items` + combiné du
jour `_combo_tg_card`). Non retenus par le user : marché en clair (« -9.5 » → « gagne de 10 pts+ ») et
puce indicatif/jour. Purement AFFICHAGE — ROI/stats/calibration intacts. Live HTTP 200.

## 2026-07-13 (5) — Carte premium : marché en clair + confiance qualifiée + finitions
Demande user (« optimise encore, clair/intuitif/professionnel/premium », pleins pouvoirs). Ajouts au
rendu des cartes provisoire + combiné :
- `web._plain_market(sel, sport)` : traduit le jargon SOUS la sélection (« -9.5 » → « gagne de 10 points
  ou plus » ; handicap +, totaux ; adapté foot/tennis/basket). '' si déjà clair (vainqueur, DC « ou nul »,
  set) → zéro bruit. Testé sur 8 cas.
- `web._conf_word(p)` : qualificatif (Faible <55 / Modérée 55-67 / Élevée 68-79 / Très élevée ≥80), affiché
  avant le % dans la bande verdict → niveau lu sur 3 canaux (mot + couleur + barre).
- Finitions CSS : barre creusée (ombre interne) + fill halo, libellés petites capitales espacées, rythme.
Purement AFFICHAGE (ROI/stats/calibration intacts). Live HTTP 200. Maquette avant/après régénérée.

## 2026-07-13 (6) — Suivi provisoire (Stats) : dédup jambes PAR NOM + cohérence Stats↔À venir
Reproches user (capture onglet Stats) : (1) une jambe de combiné (Atlanta Dream vainqueur) apparaît
ENCORE dans le suivi provisoire ; (2) des paris sont dans le suivi Stats mais pas dans « À venir ».
Cause : le suivi `app/provisional.py` excluait les jambes PAR ID (`mid in leg_ids()`) — même bug d'id
que l'affichage (id_match=False pour TOUT) ; et `_track_provisional` était un no-op quand la ré-analyse
effaçait le provisoire -> le suivi gardait le pari du matin que l'affichage avait retiré.
Fix : source unique `combo_daily.is_daily_leg(mid, home, away)` (id OU nom) + `leg_pairs()` ->
`record`/`prune_retained` dédupliquent PAR NOM. Nouveau `provisional.drop_unsettled(mid)` (retrait ciblé)
appelé par `_track_provisional` quand pas de provisoire, + `reconcile_with_programme()` (branché
`settle_pending`) qui retire du suivi les non-réglés SANS provisoire affiché dans day_programme.
Nettoyage immédiat : Atlanta Dream + 4 entrées périmées retirées ; reste = Minnesota (présent dans À
venir). Info seule hors ROI, jamais un réglé (monotone). Selfcheck 0/0.

## 2026-07-13 (7) — Cohérence suivi↔affichage bidirectionnelle + code_from_pick tolérant au pluriel
Suite reproche user (capture À venir vs Stats) : Djurgården affiché en provisoire dans À venir mais
ABSENT du suivi Stats (incohérence INVERSE de la précédente). Deux causes :
1. `code_from_pick('Djurgården IF vainqueur', …, 'Djurgårdens IF', …)` renvoyait '' : « Djurgården »
   (texte pari) ≠ « Djurgårdens » (nom Unibet, +s) -> `which()` trop strict -> pari « vainqueur » non
   codable = non réglable ET non suivi. Fix : `which()` tolère le pluriel/accord par PRÉFIXE (≥5 lettres),
   sous-chaîne d'abord ; ambiguïté 2-camps toujours "". Non-régression vérifiée (HCAP/DC/total intacts).
2. `reconcile_with_programme` ne faisait que RETIRER. Rendu BIDIRECTIONNEL : AJOUTE aussi au suivi les
   provisoires affichés mais pas encore suivis (via `record`, qui porte la dédup combiné/retenu/non-réglable).
Résultat : suivi PENDING == provisoires AFFICHÉS (0 écart dans les 2 sens). Djurgården + Minnesota suivis.
Info seule hors ROI, jamais un réglé (monotone). Selfcheck 0/0, live 200.

## 2026-07-13 (8) — Paris à jouer présentés comme les provisoires (premium unifié)
Demande user : « les paris à jouer présentés comme les provisoires ». Les paris à jouer À VENIR
(simple retenu, non-combiné) utilisaient une ligne compacte « • sél @cote » ; ils adoptent désormais la
MÊME présentation premium que les cartes provisoires : `mc-pick` (gras) + glose marché (`_plain_market`)
+ extrait d'analyse (`_prov_why_snippet`, lit « 🎯 Le pari à jouer ») + bande VERDICT (`_verdict_strip`,
confiance colorée + cote). Dans `web._sport_row`, seul le contenu `mc-sub` change ; live/terminé/combiné/
abstention gardent leur affichage adapté (résultat, score, compact). La confiance affichée = CALIBRÉE
(nouveau `retained_bet(...).cprob`) pour coller au tableau déplié. Cadre normal conservé (« À jouer ·
comptés au ROI » ≠ provisoires « Indicatif · hors ROI »). Selfcheck 0/0, live 200.

## 2026-07-13 (9) — Analyse : uniquement au dépli (retrait de l'extrait collant dans la carte repliée)
Demande user : l'analyse visible dans la carte REPLIÉE doit n'apparaître qu'au DÉPLI (message complet) ;
l'extrait faisait doublon avec l'analyse complète une fois la carte ouverte. Retrait de l'extrait cyan
`mc-note` de l'EN-TÊTE des cartes « pari à jouer » (`_sport_row`, bloc premium) ET « provisoire »
(`_programme_items`). La carte repliée = pick gras + marché en clair + bande verdict (épurée) ; l'analyse
COMPLÈTE reste dans le corps (mc-ana / render), affichée seulement au dépli. Laissé intact : la SYNTHÈSE
du combiné du jour (`_combo_tg_card`, mc-note) — carte NON dépliable, c'est sa seule description. Live 200.

## 2026-07-13 (10) — Carte dépliée refondue : l'analyse (raisonnement) enfin visible
Reproches user : « je ne vois pas les analyses une fois ouvert » + « réorganise complètement la carte
dépliée ». Cause : `analyses._structured` NE rendait PAS la section « 🎯 Le pari à jouer » (1400+ car. de
raisonnement) et repliait les faits dans un `<details>` « ℹ️ Informations ». Refonte :
- `_structured` réécrit -> sections premium empilées : **🎯 Pourquoi ce pari** (raisonnement, en 1er, section
  accentuée bleue ; 1re puce « **sél @cote :** » retirée car redondante avec l'en-tête) · **📋 Les faits**
  (VISIBLES, plus repliés) · **💰 Mise conseillée** (encadré doré) · séries/tendances à la suite.
- CSS `.da-sec/.da-sec-why/.da-sec-mise` (cartes de section).
- `_sport_row` : carte PREMIUM (pari à venir déjà en tête) -> ticket de pari REDONDANT retiré du corps ;
  ordre corps = Cotes & chances (barres) → ANALYSE → sources en bas.
Vaut aussi pour les cartes provisoires (même `to_html`). Frag 2139->3888 car. (raisonnement inclus).
Selfcheck 0/0, live 200. Preview artifact publiée.

## 2026-07-13 (11) — Cartes repliées premium 10/10 (police, séparateurs, espaces)
Demande user (captures À jouer + provisoires) : optimiser taille de police / lignes de séparation /
espaces, premium 10/10. Incohérences corrigées entre paris à jouer et provisoires :
- EN-TÊTE unifié : les paris à jouer premium passent de « Football · Ligue » à « SPORT • Ligue » (sport
  en accent via `mc-sport` ; vert=ROI / bleu=indicatif -> distinction subtile gardée).
- TIRET « — » (`mc-dash`) entre les équipes (fini le « vs » des paris à jouer).
- FILET fin `mc-div` entre équipes et pari (comme les provisoires) — séparait déjà « match » de « pari ».
- Classe `mc-prem` : padding roomier (14/16/13) + équipes 14,5 px sur 2 lignes possibles = même respiration
  que les provisoires, SANS toucher au cadre (pas de mc-tg).
- Hiérarchie : `.mc-pick` 15→15,5 px (héros, letter-spacing) ; `.mc-teams` 13,5→14 px.
Purement CSS/markup d'affichage. Live 200. Preview artifact.

## 2026-07-13 (12) — Intitulé de pari homogène : « Double chance 1X » -> « ... {équipe} ou nul »
Reproche user (carte combiné du jour) : 2 jambes = le MÊME pari (double chance domicile-ou-nul) mais 2
libellés : « Double chance Ceará-CE ou nul » vs « Double chance 1X » (l'analyste les formule différemment).
Fix AFFICHAGE : nouveau `web._pretty_sel(sel, home, away)` qui normalise la notation technique 1X/X2/12 en
forme explicite (« Double chance {domicile} ou nul » / « {ext} ou nul » / « {dom} ou {ext} »). Appliqué aux
jambes de combiné (`_combo_tg_legs` + `combo_legs_html`), au pari à jouer premium et au provisoire -> le même
pari s'affiche PAREIL partout. Purement affichage (données intactes). Live 200.

## 2026-07-14 — Intitulé DC : GARDER la mention « 1X » + la préciser (correction du (12))
Correction de compréhension : le user veut GARDER la mention technique « 1X » (pas la remplacer par
« ou nul »). `analyses.pretty_sel` refait : unifie les 2 formes (code « 1X » ET explicite « <équipe> ou
nul ») vers « Double chance 1X (<domicile> ou nul) » -> la mention 1X est présente ET précisée ET
identique partout. Centralisé dans `analyses.pretty_sel` (SOURCE UNIQUE), appliqué : jambes combiné
(_combo_tg_legs + combo_legs_html), tableau de paris (_bets_table), pari à jouer + provisoire, derniers
paris Stats (web + routers/web), Telegram combiné (combo_daily.telegram_text). web._pretty_sel = alias.
Purement affichage. Live 200, selfcheck 0/0.

## 2026-07-14 (2) — Pari PUBLIÉ = FIGÉ (ne plus faire peur après un rescan)
Problème user : pari « Moins de 2,5 buts » Itabaiana joué à 1.52 (publié Telegram+site), puis rescan ->
cote raccourcie 1.52->1.43 (value +3.4%->-1.3%) -> affiché « Analysé · pas de pari conseillé ». L'abonné
qui a parié voit son pari disparaître = effrayant. Solution « pari publié = figé » :
- `analyses.freeze_published_bet(sport, mid)` : gèle {sel, cote, prob} DÈS la publication (appelé après
  remember_prono dans le scan). Idempotent. `_write_sidecar` préserve `published_bet` à travers les rescans.
- `analyses.published_bet(sport, mid)` : renvoie le pari figé (cote conseillée) + `market_cote` (prix actuel).
- `web._sport_row` : un match PUBLIÉ affiche TOUJOURS `published_bet` (prix figé), jamais retiré ; mention
  `.mc-moved` « 🔒 Cote au conseil X · marché actuel Y » si la cote a bougé. Prioritaire sur le simple du moment.
- Itabaiana : `published_bet` restauré @1.52 (prix réel de la capture user, perdu à l'écrasement) -> affiche
  1.52 + mention. Non-régression : un pari sans mouvement de cote reste inchangé (Ceará 1.58, pas de mention).
Purement affichage + gel data (published_bet). Live 200, selfcheck 0/0.

## 2026-07-14 (3) — Combiné multisport DU JOUR compté au ROI (décision explicite user)
Jusqu'ici « info seule, hors ROI » (mémoire : jamais compté sans décision explicite). Décision prise :
- `combo_daily.roi_events()` : combinés du jour RÉGLÉS (won/lost ; void neutre exclu) -> (date, result,
  cote_effective, details), 1 pari/jour, mise 1 u.
- `analyses.stats_full` : injecte ces événements dans `overall` + `since_change` (+ `combo_form`), triés
  par date. Frozen au règlement -> monotone. Restent AUSSI dans leur suivi dédié.
- Selfcheck `stat_monotonic` porte sur les `stat_bet` FIGÉS (112), pas sur l'affichage -> non impacté
  (affichés au ROI 107->110, sous le filigrane). 3 combinés réglés ajoutés (+3.2u, tous gagnés).
- Libellés MAJ : carte accueil déplacée « Indicatif · hors ROI » -> « À jouer · comptés au ROI » (en tête) ;
  pied carte « 🎯 Compté au ROI · mise 1 u » ; Telegram idem ; carte Stats « compté au ROI »/« ROI combiné ».
Live 200, selfcheck 0/0.
