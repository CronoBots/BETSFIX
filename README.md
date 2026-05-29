# API-SPORT — Roland Garros API 🎾

API REST (Python / **FastAPI**) qui récupère **tous les matchs** et **toutes les
statistiques** de Roland Garros (simple messieurs **ATP** et dames **WTA**).

Les données proviennent d'une **source gratuite et sans clé** : l'API publique
**SofaScore**. Toute la logique réseau est isolée dans une couche *provider*
(`app/providers/sofascore.py`), ce qui permet de brancher une autre source sans
toucher aux endpoints.

## Fonctionnalités

- 📋 Liste de **tous les matchs** d'une édition (passés, en cours, à venir)
- 🔍 Filtres par **round**, **statut** et **joueur**
- 📊 **Statistiques détaillées** d'un match (aces, doubles fautes, % de service, points gagnés…)
- 📊 Statistiques de **tous les matchs terminés** en une seule requête (récupération parallèle)
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
| `GET` | `/matches/tournament` | Infos sur l'édition courante |
| `GET` | `/statistics/{match_id}` | Statistiques détaillées d'un match |
| `GET` | `/statistics` | Statistiques de **tous les matchs terminés** |

### Paramètres communs

- `tour` : `atp` (hommes, défaut) ou `wta` (femmes)
- `season` : année de l'édition (ex : `2024`). Par défaut, l'édition la plus récente.

### Exemples

```bash
# Tous les matchs ATP de l'édition courante
curl "http://localhost:8000/matches?tour=atp"

# Tous les matchs WTA 2024, uniquement les finales
curl "http://localhost:8000/matches?tour=wta&season=2024&round=Finale"

# Tous les matchs de Djokovic
curl "http://localhost:8000/matches?tour=atp&player=djokovic"

# Statistiques d'un match précis
curl "http://localhost:8000/statistics/11958222"

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
    "round": "Finale",
    "status": "finished",
    "start_time": "2024-06-08T10:00:00Z",
    "home": {"id": 1, "name": "Alcaraz C.", "country": "Spain", "ranking": 3},
    "away": {"id": 2, "name": "Zverev A.", "country": "Germany", "ranking": 4},
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
| `RG_WTA_TOURNAMENT_ID` | `2483` | ID SofaScore du tournoi WTA |
| `CACHE_TTL_SECONDS` | `30` | Durée du cache |
| `HTTP_TIMEOUT` | `20` | Timeout des requêtes (s) |
| `HTTP_USER_AGENT` | *(navigateur)* | User-Agent envoyé à la source |

## Tests

Les tests **mockent** la source SofaScore (via `respx`), donc aucun accès
réseau n'est nécessaire pour les exécuter :

```bash
pytest -q
```

## Architecture

```
app/
├── main.py                 # Application FastAPI + routes racine
├── config.py               # Configuration (.env)
├── cache.py                # Cache mémoire TTL
├── models.py               # Modèles Pydantic (réponses normalisées)
├── dependencies.py         # Injection du provider
├── providers/
│   └── sofascore.py        # Source de données + normalisation
└── routers/
    ├── matches.py          # Endpoints /matches
    └── statistics.py       # Endpoints /statistics
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
