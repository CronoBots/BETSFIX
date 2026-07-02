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
- **2026-07-02** — Mise en place du process anti-régression + `HISTORIQUE.md`. — pourquoi : trop de
  régressions au fil des optimisations (demande user) · fichiers : `HISTORIQUE.md` (doc, aucun code
  applicatif touché) · **régression vérifiée** : sans objet (documentation) · résultat : règle active,
  journal démarré.
