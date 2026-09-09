# API-Football v3 — référence LOCALE (sondée live, plan Pro)

## SYNTHÈSE MIGRATION — chaque ancienne source → endpoint API-Football

| Ancienne source (scrape) | Rôle | Remplacé par | État |
|---|---|---|---|
| **LiveScore** | scores live + **règlement** | `/fixtures?date` (`score.fulltime` reg + `goals` final) | ✅ **FLIP #1 FAIT** |
| **Unibet** | cotes/marchés (**omap**) | `/odds?fixture` bookmaker **16** | ✅ **FLIP #2 PRÊT** (flag off) |
| **Understat** | **xG** top-5 | `/fixtures/statistics` type `expected_goals` | ✅ **couvert** (top-5, délai ~qq h post-FT) |
| **Sportradar GISMO** | forme · streaks · moy buts/over · H2H · classement | `/teams/statistics` + `/standings` + `/fixtures/headtohead` | ✅ couvert |
| **FotMob** | forme / blessés / H2H | `/injuries` + `/teams/statistics` + `/fixtures/headtohead` | ✅ couvert (⚠ **météo** = pas d'endpoint, facteur mineur) |
| **Flashscore** | forme · H2H · **compos** | `/fixtures/lineups` + `/fixtures/headtohead` | ✅ couvert |
| **Pinnacle** (iProyal/TheOddsAPI) | **ancre sharp** | `/odds?fixture` bookmaker **4** | ⚠️ couverture OK, **valeurs divergent** → PAS prêt |
| **SofaScore** | séries · votes · live | `/teams/statistics` + `/predictions` (équivalent) | ✅ couvert |
| **ESPN** | dormant | — | — |

**BONUS — data NOUVELLE (qu'on n'avait pas) :** `/predictions` = distribution **Poisson**, %win, advice, comparaison
att/def/forme pré-calculés. À traiter comme **feature/fantôme** (une opinion de plus), **jamais** comme notre analyse.

⚠️ **SÉLECTION des matchs** : aujourd'hui c'est Unibet qui décide quels matchs on scanne. `/fixtures?date` liste
**tous** les matchs → la sélection pourrait passer par API-Football (par ligue). **Décision à prendre** (pas un flip auto).

**xG confirmé top-5** (sondé) : PL 1.01 · La Liga 0.65 · Serie A 1.61 · Ligue 1 2.94 · Bundesliga 2.37 (délai qq h post-FT).

---

## INVENTAIRE COMPLET v3 (audit 2026-09-09 — TOUS les endpoints confirmés joignables)

**✅ Sondés + documentés ci-dessous (17) — couvrent 100 % des besoins BETSFIX :**
status · fixtures (+live) · fixtures/statistics (xG) · fixtures/events · fixtures/lineups · fixtures/players ·
odds (pré-match) · odds/live · odds/bookmakers · odds/bets · predictions · injuries · fixtures/headtohead ·
standings · teams/statistics · teams · leagues · players/topscorers.

**✅ Confirmés joignables mais NON nécessaires à BETSFIX (16) — existent, testés OK :**
| Endpoint | Résultats | Utilité BETSFIX |
|---|---|---|
| `sidelined` | 13/joueur | 🟡 **suspensions/absences historiques** — pourrait compléter `injuries` là où sa couverture=False (Belgique) |
| `odds/live/bets` | 266 | 🟡 **catalogue des paris in-play** — NÉCESSAIRE pour le mapping du futur détecteur live |
| `venues` | 709 | 🟢 stade (affichage cosmétique) |
| `players/squads`, `players`, `players/topassists`, `players/topyellowcards` | 1-20 | joueurs (hors nos marchés) |
| `fixtures/rounds` | 38 | libellé de journée (mineur) |
| `coachs` | 3 | entraîneur (mineur) |
| `timezone` (427) · `countries` (171) · `leagues/seasons` (20) · `teams/seasons` (17) · `transfers` (353) · `trophies` (27) · `odds/mapping` (100) | — | ❌ inutiles pour nous |

**Conclusion audit** : la surface v3 est **entièrement cartographiée**. Les 17 endpoints documentés couvrent règlement +
cotes + ancre sharp + enrichissement + xG. Rien d'utile ne manque. 2 endpoints notés pour le futur : `odds/live/bets`
(mapping détecteur live) et `sidelined` (blessés Belgique/Amérique du Sud où `injuries`=False).

---

> Généré par sonde sur fixture réel 1635609 (league 2, season 2026, teams 575/1026).
> Base `https://v3.football.api-sports.io` · header `x-apisports-key` · GET only.
> Échantillons JSON complets dans `docs/apifootball_samples/`.

### status
`GET /status` params `{}` → **0 résultat(s)** · fichier `status.json`
```
{account, subscription, requests}
```
- `account` → {firstname, lastname, email}
- `subscription` → {plan, end, active}
- `requests` → {current, limit_day}

### fixtures (par id)
`GET /fixtures` params `{'id': 1635609}` → **1 résultat(s)** · fichier `fixtures.json`
```
{fixture, league, teams, goals, score, events, lineups, statistics, players}
```
- `fixture` → {id, referee, timezone, date, timestamp, periods, venue, status}
- `league` → {id, name, country, logo, flag, season, round, standings}
- `teams` → {home, away}
- `goals` → {home, away}
- `score` → {halftime, fulltime, extratime, penalty}
- `events` → [12] {time, team, player, assist, type, detail, comments}
- `lineups` → [2] {team, formation, coach, startXI, substitutes}
- `statistics` → [2] {team, statistics}

### fixture statistics (xG/tirs/possession)
`GET /fixtures/statistics` params `{'fixture': 1635609}` → **2 résultat(s)** · fichier `fixture_statistics.json`
```
{team, statistics}
```
- `team` → {id, name, logo}
- `statistics` → [16] {type, value}

### fixture events (buts/cartons)
`GET /fixtures/events` params `{'fixture': 1635609}` → **12 résultat(s)** · fichier `fixture_events.json`
```
{time, team, player, assist, type, detail, comments}
```
- `time` → {elapsed, extra}
- `team` → {id, name, logo}
- `player` → {id, name}
- `assist` → {id, name}
- `type` → Card
- `detail` → Yellow Card
- `comments` → Time Wasting

### fixture lineups (compos)
`GET /fixtures/lineups` params `{'fixture': 1635609}` → **2 résultat(s)** · fichier `fixture_lineups.json`
```
{team, formation, coach, startXI, substitutes}
```
- `team` → {id, name, logo, colors}
- `formation` → 4-4-2
- `coach` → {id, name, photo}
- `startXI` → [11] {player}
- `substitutes` → [10] {player}

### fixture players (stats+xG joueur)
`GET /fixtures/players` params `{'fixture': 1635609}` → **2 résultat(s)** · fichier `fixture_players.json`
```
{team, players}
```
- `team` → {id, name, logo, update}
- `players` → [22] {player, statistics}

### odds pre-match
`GET /odds` params `{'fixture': 1635609}` → **1 résultat(s)** · fichier `odds_pre-match.json`
```
{league, fixture, update, bookmakers}
```
- `league` → {id, name, country, logo, flag, season}
- `fixture` → {id, timezone, date, timestamp}
- `update` → 2026-09-08T16:31:25+00:00
- `bookmakers` → [14] {id, name, bets}

### predictions
`GET /predictions` params `{'fixture': 1635609}` → **1 résultat(s)** · fichier `predictions.json`
```
{predictions, league, teams, comparison, h2h}
```
- `predictions` → {winner, win_or_draw, under_over, goals, advice, percent}
- `league` → {id, name, country, logo, flag, season}
- `teams` → {home, away}
- `comparison` → {form, att, def, poisson_distribution, h2h, goals, total}
- `h2h` → [0]

### injuries (blessés)
`GET /injuries` params `{'fixture': 1635609}` → **0 résultat(s)** · fichier `injuries.json`
```
[0]
```

### head2head
`GET /fixtures/headtohead` params `{'h2h': '575-1026'}` → **1 résultat(s)** · fichier `head2head.json`
```
{fixture, league, teams, goals, score}
```
- `fixture` → {id, referee, timezone, date, timestamp, periods, venue, status}
- `league` → {id, name, country, logo, flag, season, round, standings}
- `teams` → {home, away}
- `goals` → {home, away}
- `score` → {halftime, fulltime, extratime, penalty}

### standings (classement)
`GET /standings` params `{'league': 2, 'season': 2026}` → **1 résultat(s)** · fichier `standings.json`
```
{league}
```
- `league` → {id, name, country, logo, flag, season, standings}

### team statistics (forme/moy buts)
`GET /teams/statistics` params `{'team': 575, 'league': 2, 'season': 2026}` → **11 résultat(s)** · fichier `team_statistics.json`
```
{league, team, form, fixtures, goals, biggest, clean_sheet, failed_to_score, penalty, lineups, cards}
```
- `league` → {id, name, country, logo, flag, season}
- `team` → {id, name, logo}
- `form` → DWW
- `fixtures` → {played, wins, draws, loses}
- `goals` → {for, against}
- `biggest` → {streak, wins, loses, goals}
- `clean_sheet` → {home, away, total}
- `failed_to_score` → {home, away, total}

### team info
`GET /teams` params `{'id': 575}` → **1 résultat(s)** · fichier `team_info.json`
```
{team, venue}
```
- `team` → {id, name, code, country, founded, national, logo}
- `venue` → {id, name, address, city, capacity, surface, image}

### league info
`GET /leagues` params `{'id': 2}` → **1 résultat(s)** · fichier `league_info.json`
```
{league, country, seasons}
```
- `league` → {id, name, type, logo}
- `country` → {name, code, flag}
- `seasons` → [16] {year, start, end, current, coverage}

### topscorers
`GET /players/topscorers` params `{'league': 2, 'season': 2026}` → **20 résultat(s)** · fichier `topscorers.json`
```
{player, statistics}
```
- `player` → {id, name, firstname, lastname, age, birth, nationality, height, weight, injured, photo}
- `statistics` → [1] {team, league, games, substitutes, shots, goals, passes, tackles, duels, dribbles, fouls, cards, penalty}

### odds bookmakers (liste)
`GET /odds/bookmakers` params `{}` → **33 résultat(s)** · fichier `odds_bookmakers.json`
```
{id, name}
```
- `id` → 1
- `name` → 10Bet

### odds bets (liste marchés)
`GET /odds/bets` params `{}` → **338 résultat(s)** · fichier `odds_bets.json`
```
{id, name}
```
- `id` → 1
- `name` → Match Winner
