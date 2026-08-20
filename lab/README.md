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

## Résultat v1 (top-5 européen, 18 011 matchs)
Elo bien calibré, MAIS on **ne bat pas la clôture** (ROI value vs close négatif) → l'edge doit venir
du TIMING (parier avant fermeture) ou de ligues moins efficaces. Voir l'historique git pour l'évolution.
