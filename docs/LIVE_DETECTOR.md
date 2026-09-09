# Note de conception — DÉTECTEUR LIVE (paris in-play sur stats en direct)

> **Statut : PROJET FUTUR (après le value screener + VPS).** Objectif : détecter des paris à jouer EN LIVE
> à partir des stats en direct du match (dominance non reflétée au score → value sur prochain but / over…).
> Rédigé le 2026-09-09. Collecteur de données DÉJÀ démarré (`tools/live_snapshot_collector.py`) car la donnée
> live est PÉRISSABLE. Cf. `docs/VALUE_SCREENER.md` (le pré-match, à faire d'abord), mémoire `wip-current-task`.

## 1. Faisable ? OUI — la matière première live existe
| Donnée live | Endpoint | Fraîcheur | Note |
|---|---|---|---|
| Score / minute / statut | `/fixtures?live=all` | 1 appel = tous les live | — |
| Stats live (tirs, possession, corners, attaques dangereuses, **xG live**) | `/fixtures/statistics` | maj en cours de match | xG top-5 ; sparse ailleurs |
| Buts / cartons | `/fixtures/events` | live | — |
| Cotes in-play | `/odds/live` | **~5 s** | ⚠️ voir §2 |

## 2. ⚠️ DÉCOUVERTE STRUCTURANTE : pas d'ancre sharp en live
`/odds/live` est un **feed AGRÉGÉ UNIQUE** — **pas de split par bookmaker, donc pas de Pinnacle in-play**. Clés :
`fixture, league, teams, status{stopped,blocked,finished}, update, odds[]`. Marchés présents : `Fulltime Result`
(1X2 in-play, bet 59), `Match Goals`/`Over/Under Line` (25/36), `3-Way Handicap` (21), `Asian Handicap`, corners…

**Conséquence** : la recette pré-match (`EV = proba_sharp × cote_book − 1`) **ne transpose PAS** en live (pas de
sharp de référence). Le détecteur live doit reposer sur un **modèle basé sur les STATS live** vs la cote de marché
agrégée :
```
proba_estimée(prochain but / over) ← modèle stats live (xG, tirs cadrés, attaques dangereuses, minute, score)
signal = proba_estimée − proba_implicite(cote in-play dé-viggée)   # > seuil ⇒ candidat live
gate : odds_status.stopped=False & blocked=False  (marché réellement jouable)
```
Le modèle stats→proba se **calibre sur les données accumulées** (cf. §5). C'est l'inverse du pré-match : là-bas
le sharp EST le modèle ; ici on doit CONSTRUIRE le modèle.

## 3. Le signal type (ce qu'on cherche)
- Équipe qui **domine** (xG live élevé, tirs cadrés, attaques dangereuses) mais **score bloqué** → value sur
  **prochain but** / **over** / **team total over** (marchés monotones = déjà réglables en live, cf. `settle-never-on-live-score`).
- Marché in-play qui **retarde** sur l'état réel (la cote n'a pas encore intégré la domination).
- On privilégie les marchés **monotones acquis** (OVER/BTTS) car BETSFIX sait déjà les régler en live (2 sources).

## 4. Les vrais défis (honnête — plus lourd que le pré-match)
1. **Vitesse** : edge éphémère (secondes) → 100 % mécanique, **pas de Claude par décision**.
2. **Latence publier→jouer** : BETSFIX **publie** (ne parie pas). Cibler des edges qui **persistent** (état de
   match stable sur plusieurs minutes), pas des blips de cote.
3. **Infra** : polling permanent basse latence → **VPS always-on obligatoire** (impensable sur le PC). Renforce la migration.
4. **Couverture** : stats/xG live fiables top-5 ; sparse ailleurs → gate qualité.
5. **Validation** : **on ne peut PAS backtester rétroactivement** (API-Football ne garde pas l'historique in-play).
   → il FAUT accumuler des snapshots en forward. **C'est déjà lancé.**
6. **Pas de sharp live** (§2) → le modèle stats doit être bon ET calibré, sinon on parie contre un marché in-play
   parfois efficace. Prudence : commencer par les situations les plus nettes (grosse domination + marché monotone).

## 5. Collecteur de données — DÉJÀ EN MARCHE
`tools/live_snapshot_collector.py` (lecture seule, best-effort) : capte périodiquement (défaut /180 s) les matchs
live des **ligues MAJEURES** (borne le quota) → `data/live_snapshots/<date>.jsonl`. Chaque record : ts, minute,
score, stats live (xG/tirs/possession/corners/attaques…), cotes in-play clés (Fulltime Result/Match Goals/O-U/
handicaps) + `odds_status`. **Quota** : ~3-10 matchs majeurs simultanés × 2 appels /180 s = quelques centaines
d'appels/jour (négligeable sur 7500). ⚠️ Pour l'instant lancé **à la main en arrière-plan** (aujourd'hui) →
**à pérenniser via une tâche planifiée** (décision user ; ne pas créer d'autostart en douce — cf. CLAUDE.md).

Une fois quelques semaines de données : entraîner/calibrer le modèle stats→proba (prochain but / over), mesurer
si les « candidats » détectés auraient été gagnants, puis (si oui) construire le détecteur en shadow → publication.

## 6. Séquencement
1. Fin migration (en cours).
2. **Value screener PRÉ-MATCH** (`docs/VALUE_SCREENER.md`) — plus simple, réutilise tout. **← d'abord.**
3. VPS + iProyal coupé.
4. **Détecteur LIVE** — ce doc. Nécessite le VPS (polling permanent) + le dataset accumulé.
5. *(déjà en cours)* collecteur de snapshots live → dataset de backtest.

## 7. Fichiers
- `tools/live_snapshot_collector.py` — collecteur (fait). Sortie `data/live_snapshots/<date>.jsonl` (gitignoré).
- Futurs : `tools/live_model.py` (modèle stats→proba, calibré sur le dataset), `tools/live_detector.py` (détection + gate).
- Réutilise : `app/apifootball.py` (`/fixtures?live`, `/fixtures/statistics`, `/odds/live`), logique « Chance live »
  (`live-chance-bar`) + règlement live OVER (`combo_daily._live_over_settle`) qui existent déjà.

---
**TL;DR** : faisable, premium, mais plus lourd que le pré-match. Piège n°1 = **pas d'ancre sharp en live** → il
faut construire un modèle stats→proba, calibré sur des données qu'on **doit capturer en forward** (collecteur déjà
lancé). À bâtir après le value screener et le VPS. Cibler d'abord les situations nettes + marchés monotones.
