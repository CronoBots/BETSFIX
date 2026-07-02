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
