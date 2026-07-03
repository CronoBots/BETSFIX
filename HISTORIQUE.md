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
