# Optimisations possibles — audit du 2026-06-02

Audit en lecture seule sur 4 dimensions (perf/réseau, robustesse/correction, qualité du
modèle, qualité de code/tests). Rien n'a été modifié. Classé par priorité de décision.
Effort : S (≈<1h) · M (≈demi-journée) · L (≈jours).

---

## 🔴 TIER 0 — Bugs qui FAUSSENT la value (prioritaires pour un produit de paris)

Ce sont des défauts de correction qui produisent de mauvais paris ou corrompent les données.

1. **Faux positifs de matching des cotes foot/basket** — `unibet.py:180` (`_names_match`), `foot.py:209`, `basket.py:248`.
   Un seul token de nom partagé suffit, et la **date n'est pas vérifiée** côté foot/basket
   (contrairement au tennis). « Manchester United » vs « Newcastle United » partagent `united`.
   → on peut coller les cotes d'un AUTRE match sur la prédiction. **Impact élevé · effort M.**
   Piste : exiger ≥2 tokens partagés OU un token long, blacklist (`united/city/real/fc…`),
   propager la date.

2. **Cotes home/away potentiellement inversées** — `unibet.py:104` + `analysis.py:380` (`_match_winner_odds`).
   `swapped` est calculé mais les marchés ne sont jamais réétiquetés ; `_match_winner_odds` a un
   `return o1.odds, o2.odds` inconditionnel → si aucun label ne matche le home (accent/mojibake),
   l'ordre Kambi est renvoyé tel quel, sans garantie. **Impact élevé · effort S/M.**

3. **Avantage du terrain (+35 Elo) appliqué aux matchs CdM sur terrain neutre** — `foot.py:36,70`.
   Le « home » d'une Coupe du Monde est arbitraire → P(1)/P(2) systématiquement faussés sur le
   sport mis en avant. **Impact élevé · effort M.** Piste : `HOME_ADV=0` sauf vrai pays hôte
   (venue/country déjà dispo).

4. **Matchs jamais « finished » jamais réglés** — `routers/tracking.py:75`, `basket.py:481`, `foot.py:442`.
   postponed/retired/walkover/canceled ne sont pas gérés → l'enregistrement reste à vie, ré-essayé
   toutes les 3 h, **le store JSON gonfle sans borne** et fausse `matchs_suivis`. **Impact élevé · effort M.**
   Piste : régler les statuts terminaux non-finished (void/push) + purge par âge.

5. **Perte silencieuse de tout l'historique sur store corrompu** — `tracking.py:27`, `cache.py:48`.
   `load()` avale un JSON corrompu et renvoie `{}` → le prochain `save()` écrase tout, sans alerte
   ni sauvegarde. **Impact élevé · effort S.** Piste : renommer en `.bak` + log au lieu d'écraser.

6. **Elo manquant pour une équipe (sélection CdM, promu) → aucune prédiction, silencieusement** —
   `foot.py:227`, `basket.py:270`. Le cœur métier (CdM) peut ne rien produire si `build_foot_elo`
   ne couvre pas l'équipe. **Impact moyen-élevé · effort M.** Piste : Elo de base + log.

7. **Écritures concurrentes sur le store** — `tracking.py:35` + endpoints `POST /tracking/*`.
   La boucle de fond et un appel d'endpoint peuvent faire un read-modify-write concurrent →
   lost update. **Impact élevé sous charge · effort M.** Piste : lock asyncio par fichier, ou
   ne pas exposer les endpoints en écriture.

---

## 🟠 TIER 1 — Performance / gaspillage réel

8. **Cache disque de 58 Mo réécrit ENTIÈREMENT à chaque `set`, dans l'event loop** — `cache.py:39`.
   Bloque la boucle ~100-300 ms par save (throttle 10 s seulement). **LE plus gros gaspillage.**
   **Impact élevé · effort M.** Piste : save async (`to_thread`) + format incrémental (SQLite/shelve).

9. **Cache sans éviction : 88 % d'entrées mortes conservées + 14 Mo de `scheduled-events`/jour jamais purgés** —
   `cache.py`, `sofascore.py:338`. Le fichier ne fait que croître. **Impact élevé · effort M.**
   Piste : purge des entrées expirées >24 h, purge des clés datées passées, borne LRU.

10. **`scheduled-events` : ~4 Mo/jour téléchargés (tout le tennis mondial) pour ne garder qu'ATP/WTA simples** —
    `sofascore.py:338`. **Impact élevé · effort M.** Piste : ne pas cacher le brut sur disque, ou TTL long dédié.

11. **N+1 réseau séquentiels** — `routers/tracking.py:38` (snapshot), `:81` + `basket.py:481` + `foot.py:442` (settle),
    `foot.py:157` + `basket.py:209` (boards). Des centaines d'appels en série. **Impact élevé · effort M.**
    Piste : `asyncio.gather` borné par sémaphore (déjà fait dans `get_all_statistics`).

12. **DEUX systèmes de cache + DEUX disjoncteurs anti-403 coexistent** — `cache.py`+provider (tennis, back-off
    exponentiel 30→300 s) vs `sportcache.py`+foot/basket (fixe 90 s). Incohérent, double surface. **Impact
    moyen-élevé · effort L.** (recoupe #20)

13. **Refresh de fond non borné** — `sofascore.py:169`. Une page touchant 50 chemins périmés lance 50 tâches →
    rafale → 403. **Impact moyen · effort M.** Piste : sémaphore global + court-circuit si breaker ouvert.

14. **Clients httpx recréés à chaque appel** (foot/basket) **+ pas de pooling partagé** — `foot.py:219`, `basket.py:262`.
    Handshakes TLS répétés. **Impact moyen · effort M.**

15. **`tracking.load()` (lecture+parse disque) appelé 7× par rendu de la page d'accueil** — `routers/web.py:93`.
    **Impact moyen · effort S.** Piste : cache par mtime (comme `elo.load_cached`).

Plus : `report()` recalculé à chaque hit (cache mtime), `sportcache` sans expiration (fuite lente),
TTL 120 s trop court pour l'agenda, `tracking.save` en `indent=2`, marqueur de rebuild écrit avant succès.

---

## 🟡 TIER 2 — Qualité du modèle (justesse & value)

16. **Calibration du classement entraînée sur Roland-Garros (terre) mais appliquée à TOUTES les surfaces** —
    `analysis.py:36`, `tools/backtest.py`. Le facteur de plus gros poids (0.40) → modèle **sous-confiant
    sur dur/gazon**. **Impact élevé · effort M.** Piste : recalibrer b0/b1 par surface sur le circuit complet.

17. **Poids du modèle désynchronisés des back-tests, forme (0.20) + h2h (0.05) jamais back-testés** —
    `analysis.py:50` vs `backtest_combined.py:146`. 25 % du poids sans validation hors-échantillon.
    **Impact élevé · effort M.**

18. **`MODEL_TRUST=0.35` arbitraire pilote TOUTE la détection de value** — `analysis.py:53`. Jamais mesuré.
    **Impact élevé · effort M.** Piste : back-tester contre cotes de clôture / via le tracking.

19. **Marchés buts foot (Over 2.5, BTTS) calculés puis jetés, jamais confrontés au book** — `foot.py:76,242`.
    Détecteur de value gratuit et à fort volume Unibet. **Impact moyen · effort S → quick win value.**

20. **Elo basket sans marge de victoire (MOV) ni régression inter-saison** — `tools/build_basket_elo.py:85`.
    Un blowout et un OT bougent l'Elo pareil. **Impact moyen · effort M.**

21. **Données SofaScore riches récupérées mais purement décoratives** — pregame-form, point-by-point, streaks,
    votes ne nourrissent pas `build_analysis`. **Impact moyen · effort M.** (pipeline de diagnostic
    `factor_breakdown` déjà en place pour mesurer le gain).

22. **Seuils de value en dur, dupliqués entre modules, non back-testés** — `analysis.py:62`, `foot.py:41`,
    `basket.py:37`, etc. **Impact moyen · effort M.**

Plus : forme ignore le score/marge (binaire W/L), pas de facteur fatigue/repos, `MAX_DISAGREEMENT`
asymétrique, shrink mesuré sur 2 facteurs appliqué à 5, devig « power method » pour les gros favoris,
marchés tennis « 1er set / total de jeux d'un set » non modélisés.
**Point sain confirmé** : le devig et la comparaison modèle/marché retirent bien le vig des deux côtés.

---

## 🟢 TIER 3 — Qualité de code, cohérence, tests

23. **Normalisation de noms (NFKD) réécrite 6 fois** — `unibet.py:169`, `livescore.py:143`, `rankings.py:27`,
    `foot.py:109`, `basket.py:117`, `matches.py:28`. **Impact élevé (maintenabilité) · effort M.**
    Piste : `app/textutil.py` avec `fold()` + `name_tokens()`. Une correction de matching profite aux 3 sports.

24. **Fetch/cache/disjoncteur dupliqués hors provider** — foot/basket contournent `SofaScoreProvider`/`UnibetProvider`
    (cf. #12). **Effort L.** Piste : router foot/basket vers les providers (le `find_event_odds` est déjà multi-sport).

25. **Squelette de suivi foot vs basket dupliqué à ~80 %** — `enrich_display`/`_match_odds`/`_upsert`/`run_snapshot`/
    `run_settle` jumeaux. **Effort L.** Piste : socle `team_sport.py`.

26. **Aucun test pour foot.py / basket.py / `find_event_odds` / endpoints `/odds/unibet` / helpers récents** —
    (`_classify_tag`, `_is_upcoming`, `_fold`, computed fields, `_inv_norm`). C'est le code qui « produit les
    paris », sans filet. **Impact élevé (fiabilité) · effort M.** Beaucoup de fonctions pures faciles à tester.

27. **Obsolescence « Roland Garros »** — summaries/docstrings (`matches.py:1,37`, `sofascore.py:282`) ET le repli
    LiveScore qui ne montre QUE RG (`livescore.py:68,115`) alors que `track_full_tour=True` suit tout le circuit.
    **Cosmétique (docs) + fonctionnel (repli). Effort S/M.**

28. **Quick wins sans risque (effort S)** : `_noop` + import `asyncio` dupliqué (`analysis.py:3,34`), commentaire
    orphelin indenté (`basket.py:302`), import au milieu du fichier (`sofascore.py:69`), config `rg_*` mal nommée,
    tags de routeur morts (écrasés par `_retag_routes`), gestion d'erreurs hétérogène (`except Exception` large
    → ciblé), docstrings basket « WNBA » au lieu de « NBA & WNBA », cycle d'import web↔foot/basket.

---

## Recommandation de séquencement (à valider ensemble)

- **D'abord (correction, haute valeur)** : #1, #2, #4, #5 — ce sont des bugs qui faussent la value ou
  perdent des données. Effort raisonnable, gros retour.
- **Ensuite (perf, gros gaspillage)** : #8 + #9 + #10 (le cache) en un seul chantier ; puis #11 (paralléliser).
- **Puis (fondations)** : #23 (normalisation centralisée) + #26 (tests) — débloquent tout le reste sereinement,
  puis #12/#24 (unifier cache/disjoncteur).
- **En continu (modèle)** : #16, #17, #18, #19 — la plupart se mesurent avec le tracking déjà en place.
- **Au fil de l'eau** : les quick wins #28.
