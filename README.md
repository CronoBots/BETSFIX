# BETSFIX — Roland Garros API 🎾

API REST (Python / **FastAPI**) qui récupère **tous les matchs** et **toutes les
statistiques** de Roland Garros (simple messieurs **ATP** et dames **WTA**).

> **Note (2026) :** le projet a évolué d'une simple API Roland Garros vers une **PWA de paris
> multi-sports** (foot / tennis / basket). Les données ne proviennent **plus de SofaScore**
> (source morte) mais d'une **couche multi-sources gratuite et sans clé** (`app/sources.py`),
> croisée par l'analyste avant chaque pronostic.

### Sources de données (état réel)

| Source | Rôle | Statut |
|---|---|---|
| **Unibet** (Kambi) | cotes + marchés + **sélection** des matchs (3 sports) | ✅ |
| **Pinnacle** | ancre « sharp » (proba de référence) | ✅ |
| **FotMob** | foot : forme / blessés / H2H | ✅ |
| **ESPN** | tennis (classement / forme) · basket (bilans / blessés WNBA-NBA) | ✅ |
| **Understat** | foot : xG (top-5 ligues) | ✅ |
| **Flashscore** | foot / tennis / basket : forme + H2H + compos | ✅ |
| **LiveScore** | scores **live** + **règlement** des paris | ✅ |
| **Sportradar (GISMO)** | foot / tennis / basket : forme · série · H2H · classement — feed libre `lsc.fn.sportradar.com` (`app/sportradar.py`) | ✅ |
| **SofaScore** | ex-source principale | ❌ **morte** |

> Carte des sources canonique et tenue à jour : voir **`CLAUDE.md` § « Sources de données & analyse »**.

## Fonctionnalités

- 📋 Liste de **tous les matchs** d'une édition (passés, en cours, à venir)
- 🔍 Filtres par **round** (FR ou EN), **statut** et **joueur**
- 🎾 Matchs enrichis : **surface**, court/ville/pays, **durée** (totale et par set),
  têtes de série, premier au service
- 📊 **Statistiques détaillées** d'un match (aces, doubles fautes, % de service, points gagnés…)
- 📊 Statistiques de **tous les matchs terminés** en une seule requête (récupération parallèle)
- 🎯 **Déroulé point par point** (set → jeu → point)
- 🤝 **Head-to-head**, **pronostics des fans**, **séries et records**
- 💸 **Cotes** des matchs (fractionnaires + décimales) et **liste des éditions** disponibles
- 👤 **Fiches joueurs** complètes (taille, poids, main, gains, lieu de naissance…),
  **photo**, **classements** (ATP/WTA, Live, UTR) et **matchs récents**
- 📈 **Stats agrégées par joueur/tournoi/saison** (analyse de forme) : % 1ère/2ème
  balle, points de break sauvés/convertis, winners vs fautes directes, aces, tie-breaks
- 💰 **Cotes Unibet Belgique** (plateforme Kambi) matchées sur chaque match
- 🧠 **Analyse pré-match & value betting** : un modèle combine classement, forme,
  stats de surface et head-to-head, puis compare aux cotes Unibet pour repérer la
  *value* (edge) et proposer une mise (Kelly fractionné)
- 🏆 Infos sur l'édition courante (saison, identifiants)
- ⚡ Cache mémoire avec TTL pour limiter les appels à la source
- 📖 Documentation interactive auto-générée sur `/docs`

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optionnel : personnaliser la config
```

## Lancement

```bash
uvicorn app.main:app --reload
```

Puis ouvrir :
- **Documentation interactive (Swagger)** : http://localhost:8000/docs
- **Schéma OpenAPI** : http://localhost:8000/openapi.json

> ⚠️ L'API a besoin d'un **accès réseau sortant vers `api.sofascore.com`**.
> Dans un environnement avec allowlist réseau (ex : Claude Code on the web), il
> faut autoriser cet hôte, sinon les endpoints renverront `502`.

## Endpoints

| Méthode | Chemin | Description |
|---------|--------|-------------|
| `GET` | `/` | Présentation et liste des endpoints |
| `GET` | `/health` | Healthcheck |
| `GET` | `/matches` | **Tous les matchs** (filtres : `tour`, `season`, `round`, `status`, `player`) |
| `GET` | `/matches/{match_id}` | Détail d'un match |
| `GET` | `/matches/round/{round}` | Matchs d'un **round** donné (FR ou EN) |
| `GET` | `/matches/{match_id}/point-by-point` | **Déroulé point par point** (set → jeu → point) |
| `GET` | `/matches/{match_id}/h2h` | **Confrontations directes** (head-to-head) |
| `GET` | `/matches/{match_id}/votes` | **Pronostics des fans** |
| `GET` | `/matches/{match_id}/streaks` | **Séries et records** autour du match |
| `GET` | `/matches/{match_id}/odds` | **Cotes** (paris) du match, en fractionnaire et décimal |
| `GET` | `/matches/{match_id}/odds/unibet` | **Cotes Unibet Belgique** matchées sur le match |
| `GET` | `/analysis/{match_id}` | **Analyse pré-match + value betting** (vainqueur, modèle vs cotes Unibet) |
| `GET` | `/analysis/{match_id}/markets` | **Value sur TOUS les marchés** (jeux, sets, tie-breaks, handicaps…) via simulation |
| `GET` | `/matches/seasons` | **Éditions disponibles** du tournoi (année + id) |
| `GET` | `/matches/tournament` | Infos sur l'édition courante |
| `GET` | `/statistics/{match_id}` | Statistiques détaillées d'un match |
| `GET` | `/statistics` | Statistiques de **tous les matchs terminés** |
| `GET` | `/players/{player_id}` | **Fiche joueur** (taille, poids, main, gains, naissance…) |
| `GET` | `/players/{player_id}/image` | **Photo** du joueur (image) |
| `GET` | `/players/{player_id}/statistics` | **Stats agrégées** (service, break, winners/UE, tie-breaks…) |
| `GET` | `/players/{player_id}/statistics/available` | Tournois/saisons avec stats disponibles |
| `GET` | `/players/{player_id}/rankings` | **Classements** (ATP/WTA, Live, UTR) |
| `GET` | `/players/{player_id}/matches` | **Matchs récents** du joueur |

### Paramètres communs

- `tour` : `atp` (hommes, défaut) ou `wta` (femmes)
- `season` : année de l'édition (ex : `2024`). Par défaut, l'édition la plus récente.
- `round` : nom du round, accepté en **français ou anglais**. La source SofaScore
  renvoie les noms en anglais (`Final`, `Semifinals`, `Quarterfinals`,
  `Round of 16/32/64/128`) ; les termes FR usuels sont aussi reconnus
  (`Finale`, `Demi-finale`, `Quart de finale`, `1er tour`…).

### Exemples

```bash
# Tous les matchs ATP de l'édition courante
curl "http://localhost:8000/matches?tour=atp"

# Tous les matchs WTA 2024, uniquement la finale (FR ou EN : 'Finale' = 'Final')
curl "http://localhost:8000/matches?tour=wta&season=2024&round=Finale"

# Les quarts de finale ATP 2024 (endpoint dédié)
curl "http://localhost:8000/matches/round/Quarterfinals?tour=atp&season=2024"

# Tous les matchs de Djokovic
curl "http://localhost:8000/matches?tour=atp&player=djokovic"

# Statistiques d'un match précis
curl "http://localhost:8000/statistics/11958222"

# Déroulé point par point d'un match
curl "http://localhost:8000/matches/11958222/point-by-point"

# Statistiques de tous les matchs terminés
curl "http://localhost:8000/statistics?tour=atp"
```

### Exemple de réponse (`/matches`)

```json
[
  {
    "id": 11958222,
    "tour": "atp",
    "tournament": "French Open",
    "season": 2024,
    "round": "Final",
    "round_slug": "final",
    "status": "finished",
    "court": "Court Philippe Chatrier",
    "city": "Paris",
    "country": "France",
    "ground_type": "Red clay",
    "start_time": "2024-06-08T10:00:00Z",
    "duration_seconds": 15564,
    "set_durations": [2588, 3130, 3910, 2569, 3367],
    "first_to_serve": "away",
    "home": {"id": 1, "name": "Alcaraz C.", "country": "Spain", "ranking": 3},
    "away": {"id": 2, "name": "Zverev A.", "country": "Germany", "ranking": 4},
    "home_seed": "3",
    "away_seed": "4",
    "home_score": {"sets_won": 3, "sets": [6, 2, 5, 6, 6], "tiebreaks": [null, null, null, null, null]},
    "away_score": {"sets_won": 2, "sets": [3, 6, 7, 1, 2], "tiebreaks": [null, null, 5, null, null]},
    "winner": "home",
    "has_statistics": true
  }
]
```

## Configuration (`.env`)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SOFASCORE_BASE_URL` | `https://api.sofascore.com/api/v1` | URL de base de la source |
| `RG_ATP_TOURNAMENT_ID` | `2480` | ID SofaScore du tournoi ATP |
| `RG_WTA_TOURNAMENT_ID` | `2577` | ID SofaScore du tournoi WTA |
| `CACHE_TTL_SECONDS` | `30` | Durée du cache |
| `HTTP_TIMEOUT` | `20` | Timeout des requêtes (s) |
| `HTTP_USER_AGENT` | *(navigateur)* | User-Agent envoyé à la source |
| `UNIBET_BASE_URL` | *(Kambi `ubbe`)* | API d'offre Unibet Belgique |
| `UNIBET_LANG` | `fr_BE` | Langue de l'offre Unibet |
| `UNIBET_MARKET` | `BE` | Marché Unibet (Belgique) |

## Analyse & paris (`/analysis/{match_id}`)

L'endpoint d'analyse combine plusieurs facteurs en une probabilité de victoire,
puis la confronte aux **cotes Unibet Belgique** pour repérer la *value* :

| Facteur | Poids | Source |
|---------|-------|--------|
| Classement | 0,40 | Rangs ATP/WTA, **modèle calibré** (régression logistique) — meilleur prédicteur aux back-tests |
| **Elo par surface** | 0,20 | Force réelle pondérée par les adversaires, **note terre battue** distincte (`tools/build_elo.py`) |
| Forme vs attente | 0,20 | Sur-/sous-performance vs rang de l'adversaire, **pondérée par récence**, spécifique terre |
| Surface (service/retour) | 0,15 | **Domination service+retour** (tenue de service + taux de break, historique par surface, pondéré récence) — validé au niveau Elo (`tools/explore_serve_return.py`). Repli sur stats de saison si pas de note. |
| Head-to-head | 0,05 | Confrontations directes |

Les poids sont **renormalisés** sur les facteurs présents : un joueur sans note Elo
retombe proprement sur le classement. La forme ne compte plus une victoire « 1 » à
plat — elle mesure l'écart au résultat *attendu* selon le rang de l'adversaire, donc
battre un top-10 pèse bien plus que battre un n°200.

### Calibration & notes Elo (back-test)

Le facteur classement est **calibré sur ~1150 matchs RG historiques** (ATP+WTA,
8 saisons) via `tools/backtest.py` — `P = sigmoid(b0 + b1·(ln rang_adv − ln rang))`,
avec `b0≈0,02` (≈0 : pas d'avantage « home » au tennis) et `b1≈0,40`. Sur jeu de
test séparé : **log-loss 0,64**, **Brier 0,22**, **précision 64 %**, et une
calibration fidèle (proba prédite ≈ taux réel observé). Relancer : `python tools/backtest.py`.

Les notes **Elo** (global + terre battue, base 1500, K=24) se construisent à part :
`python tools/build_elo.py` collecte l'historique SofaScore des joueurs RG, déroule
l'Elo chronologiquement et écrit `data/elo_ratings.json`. Tant que ce fichier n'existe
pas, le modèle fonctionne en repli sur le classement. À relancer périodiquement pour
garder les notes fraîches.

Pour **évaluer et améliorer** le modèle à grande échelle, `tools/backtest_model.py`
(ou double-clic `build_backtest.bat`) rejoue l'historique en **walk-forward** : il
prédit chaque match avec l'Elo construit *uniquement sur le passé*, puis met l'Elo à
jour (aucune fuite). Il imprime le Brier/log-loss/précision de chaque variante
(classement seul, Elo seul, Elo+classement), l'**ablation par facteur** (combiner
aide-t-il ?), la **calibration**, la perf **par surface**, et le **shrink optimal**
anti-surconfiance à reporter dans `CALIB_SHRINK` (`app/analysis.py`). Cette dernière
constante tempère la proba du modèle vers 0,5 pour corriger la surconfiance détectée
(le modèle annonçant plus que le taux réel de victoire du favori).

### Détection de value (prudente)

- La marge du bookmaker (*vig*) est retirée → probabilité implicite « juste ».
- **Ancrage au marché** : le marché étant sharp, la proba retenue est
  `0,35·modèle + 0,65·marché`. On ne signale une value que sur un **vrai désaccord**.
- **Garde-fous (sélectifs)** : edge minimal **4 %** ; on ne parie que sur des cotes
  **modérées** (implicite 0,22–0,68, soit ~@1,45–@4,3) car le modèle est le plus fiable
  sur les probas moyennes et le book le plus sharp aux extrêmes ; pas de value si
  l'écart modèle↔marché est *énorme* (> 15 pts) ou si la **confiance** est faible.
- Mise via **Kelly fractionné** (¼ Kelly, plafonnée à 5 % de bankroll).
- Chaque réponse indique un niveau de **confiance** (élevée / moyenne / faible).

> ⚠️ **Avertissement.** Modèle d'aide à la décision, **transparent mais sans
> garantie de gain**. Il est volontairement *sélectif* : sur la plupart des matchs
> il conseille l'**abstention** (les cotes sont conformes au modèle) — c'est normal,
> la vraie value est rare. À utiliser comme aide à la réflexion, pas comme source de
> vérité. Les cotes Unibet ne sont disponibles que pour les matchs **à venir / en
> cours**. Jouez de manière responsable, uniquement ce que vous pouvez perdre.

### Mesure de la fiabilité (page « Perf » / `/tracking`)

On logge chaque prédiction + ses cotes (rafraîchies jusqu'au coup d'envoi), puis le
résultat. Le rapport `/tracking/report` et le dashboard donnent alors :

- **Précision / Brier / log-loss du modèle**, et — surtout — les **mêmes métriques
  pour le marché** (cotes de clôture dévig) en regard. La vraie question n'est pas
  « le modèle a-t-il raison ? » mais « fait-il **mieux que le marché** ? » (`bat_le_marche`).
- **CLV (closing line value)** : compare la cote d'**ouverture** (premier log) à la
  cote de **clôture** sur le favori du modèle. Un CLV moyen **> 0** = on prend de
  meilleures cotes que la clôture → signe d'edge, lisible **bien avant** d'avoir 100
  résultats (pas besoin d'attendre l'issue des matchs).
- **Courbe de calibration** : proba prédite vs taux réel par tranche (50-60 %, …).

> Fiable à partir de **~100 matchs réglés** — en dessous, tout écart est du bruit. Ne
> recalibre **pas** le modèle sur une poignée de résultats.

## Tests

Les tests **mockent** la source SofaScore (via `respx`), donc aucun accès
réseau n'est nécessaire pour les exécuter :

```bash
pytest -q
```

## Accès mobile via TON PC (tunnel Cloudflare)

Pour que les requêtes partent de **ton PC** (IP belge → cotes Unibet OK) tout en
y accédant depuis le mobile, l'API est exposée par un **tunnel Cloudflare**
(`cloudflared`) sur une URL fixe (ex. `https://api.betsfix.com`). Scripts dans
`deploy/` :

| Script | Rôle |
|--------|------|
| `reconnexion.bat` | **Dépannage rapide** : double-clic → relance API + tunnel (garde la fenêtre ouverte). |
| `deploy/run_mobile.ps1` | Lance l'API + le tunnel (URL fixe via token). |
| `deploy/setup_token.ps1` | Tunnel en **service Windows** + API en tâche **à l'ouverture de session**. |
| `deploy/setup_full_service.ps1` | **Automatisation 100 % sans login** : tunnel ET API démarrent au boot (API en tâche SYSTEM, auto-relance). |
| `deploy/api_service_loop.ps1` | Superviseur qui maintient uvicorn en vie (utilisé par la tâche SYSTEM). |

**Automatisation complète (recommandée)** — dans un PowerShell *Administrateur* :

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
powershell -ExecutionPolicy Bypass -File .\deploy\setup_full_service.ps1 -Token "eyJ...."
```

Le `-Token` est facultatif s'il existe déjà dans `%USERPROFILE%\.cloudflared\api_token.txt`.
Après un redémarrage, `https://api.betsfix.com/docs` remonte tout seul, même sans
ouvrir la session Windows.

> **Error 1033** sur le mobile = `cloudflared` ne tourne pas côté PC (PC éteint/en
> veille, ou service arrêté). Vérifs : `Get-Service Cloudflared`,
> `curl http://localhost:8000/health`.

## Architecture

```
app/
├── main.py                 # Application FastAPI + routes racine
├── config.py               # Configuration (.env)
├── cache.py                # Cache mémoire TTL
├── models.py               # Modèles Pydantic (réponses normalisées)
├── analysis.py             # Modèle vainqueur (calibré) + value betting
├── markets.py              # Simulateur de match -> value sur tous les marchés
├── dependencies.py         # Injection des providers (SofaScore + Unibet)
├── providers/
│   ├── sofascore.py        # Source de données sportives + normalisation
│   └── unibet.py           # Cotes Unibet Belgique (Kambi) + matching
└── routers/
    ├── matches.py          # Endpoints /matches (+ round, point-by-point, h2h, votes, streaks, odds)
    ├── statistics.py       # Endpoints /statistics
    ├── players.py          # Endpoints /players (fiche, stats, classements, matchs)
    └── analysis.py         # Endpoint /analysis (value betting)
tests/
├── fixtures.py             # Réponses SofaScore factices
└── test_api.py             # Tests d'intégration (source mockée)
```

## Notes

- Les **identifiants de tournoi** SofaScore peuvent évoluer ; ils sont
  configurables via `.env`. L'API résout automatiquement la saison la plus
  récente, ou celle correspondant à l'année passée en paramètre.
- Les statistiques ne sont disponibles que pour les **matchs terminés**.
- Ce projet utilise une API tierce non officielle ; à réserver à un usage
  raisonnable et respectueux des conditions d'utilisation de la source.
