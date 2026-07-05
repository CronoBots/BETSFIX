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
