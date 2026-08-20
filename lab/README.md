# lab/ — Labo de backtest ISOLÉ (hors prod)

**Aucun lien avec l'application.** Ce dossier est un bac à sable de recherche : il n'importe rien de
`app/`, n'écrit rien dans `data/`, et n'est jamais appelé par le scan ni l'API. On peut le supprimer
sans effet sur BETSFIX.

## But
Valider la MÉTHODE (Elo maison + backtest value/calibration/CLV vs la clôture sharp) sur des données
GRATUITES, sans abonnement data, avant de toucher quoi que ce soit en prod.

## Données
- **football-data.co.uk** (CSV gratuits) : résultats + cotes 1X2, dont **Pinnacle** (PS*) et
  **Pinnacle clôture** (PSC*). Téléchargées à la volée, mises en cache dans `fd_cache/` (git-ignoré).

## `backtest_poc.py`
Ingestion → Elo maison walk-forward (par ligue, home advantage) → modèle 1X2 (1 paramètre de nul
ajusté sur le TRAIN) → évaluation OUT-OF-SAMPLE (test chrono) :
- log-loss modèle vs clôture Pinnacle,
- calibration de P(domicile),
- backtest value (ROI, réussite, cote moyenne).

Lancer : `python lab/backtest_poc.py` (stdlib seul, réseau requis au 1er run).

## Résultats (top-5 européen, ~18 000 matchs, test out-of-sample)
- **Elo bien calibré** (P prédite ≈ fréquence réelle).
- **Ne bat pas le sharp** : log-loss Elo 0,998 vs clôture Pinnacle 0,967 ; CLV moyen **négatif** (−0,25 pp,
  45 % battent la clôture) ; jouer au prix EARLY ne sauve rien (ROI ≈ −8,7 % ≈ prix clôture).
- **La leçon en or (ROI par tranche de cote, edge>5 %)** : le modèle saigne surtout sur les **outsiders
  (cote >4 : ROI −13,5 %)** ; les **favoris/cotes courtes** sont quasi à l'équilibre (2,5–4,0 : −2,8 %).
  → confirme chiffres à l'appui la méthodo BETSFIX : rester sur les marchés SÛRS (double chance, favoris),
  fuir les cotes longues. L'edge « value » sur outsiders est un piège.

Le POC est un INSTRUMENT : toute variante de stratégie se mesure sur 17 k matchs en quelques secondes.
