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

## Résultats v3 (45 632 matchs, EFFICACES vs PEU EFFICACES, out-of-sample)
Elo bien calibré. Le modèle NAÏF ne bat le sharp NULLE PART (CLV négatif partout) — mais le détail par
tranche de cote est décisif :

| Groupe | favoris <1,8 | 2,5–4 | outsiders >4 |
|---|---|---|---|
| Top-5 EU (efficace) | −5,5 % | −2,8 % | −13,5 % |
| 2e div/secondaires (peu efficace) | **+0,2 %** | **−0,4 %** | **−20,6 %** |

Leçons chiffrées :
1. **L'edge vit sur les COTES COURTES / favoris** — et sur les ligues PEU efficaces, les favoris sont
   **à l'équilibre vs Pinnacle** (+0,2 %) → donc **+EV contre un book plus mou**. C'est exactement le terrain
   de BETSFIX (double chance ~1,1–1,3, ligues Amériques/2e div).
2. **Les cotes longues sont un incinérateur** (−13 à −21 %) → tout « value » sur outsiders est du bruit.
3. Le modèle naïf n'a **aucun edge global** (CLV<0) → l'edge réel de BETSFIX doit venir de la couche
   Claude/enrichissement ; le labo permet maintenant de MESURER si elle bat ce baseline.

Le POC est un INSTRUMENT : toute variante (seuil, marché, ligue, modèle) se mesure sur ~45 k matchs en secondes.

## `optimize.py` — optimisation HONNÊTE (params réglés sur TRAIN, évalués 1× sur TEST)
Elo pondéré écart de buts + stratégies proches du produit, tuning anti-overfit. Résultats OUT-OF-SAMPLE
(ligues peu efficaces, ~10 k matchs test) :

| Stratégie | ROI test | CLV test | verdict |
|---|---|---|---|
| Back du favori (Elo) | −1,2 % | −3,6 % | quasi équilibre, mais CLV négatif = pas d'edge réel |
| Double chance (Elo, proxy) | −5,3 % | −3,8 % | négatif |
| Ancre Pinnacle → parier Bet365 | (69 train / **6 test**) | — | **échantillon trop mince = non concluant** |

**Conclusion honnête (le plafond) :** on **ne fabrique pas d'edge avec stats publiques + cotes seules contre
un book sharp**. Le back-favori optimisé frôle l'équilibre mais garde un CLV négatif ; la value d'un book mou
(Bet365, grosse marge) vs Pinnacle est **trop rare** pour constituer une stratégie (la marge mange l'écart).
→ l'edge de BETSFIX doit venir d'une INFO que le sharp n'a pas au moment du pari (couche Claude : compos,
blessés tardifs, contexte) ou du TIMING (parier avant que la ligne se forme). Le labo est prêt à MESURER ça.
