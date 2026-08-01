# BETSFIX — Sources de données & résolubilité des marchés

> Référence **canonique** : quelles sources alimentent l'analyse, lesquelles permettent de **régler**
> (settle) chaque marché Unibet, et où sont les **trous** (marché non réglable → source à ajouter).
> Mis à jour par audit data-driven. **À tenir à jour à chaque nouvelle source / nouveau marché.**

## 1. Deux usages distincts d'une source
- **ANALYSE** (avant-match, dossier de l'analyste) : forme, classement, xG, **surface**, blessés, H2H…
- **RÈGLEMENT** (après-match, `settle_analyst`) : déterminer *gagné/perdu* via le **score final** + les
  **détails** (mi-temps, sets, quart-temps, tirs cadrés…). Un marché n'est réglable que si (a) son libellé
  Unibet est mappé vers un **code** (`code_from_pick`) ET (b) une source fournit la **donnée** correspondante.

## 2. Les sources
| Source | Rôle ANALYSE | Rôle RÈGLEMENT |
|---|---|---|
| **Unibet** (Kambi) | cotes, marchés, **sélection** des matchs (3 sports) | — |
| **Pinnacle** | ancre « sharp » (proba de référence) | — |
| **FotMob** | foot : forme / blessés / H2H / météo | score foot (repli) · **stats de match** : tirs cadrés / tirs / corners / cartons par équipe (`matchDetails` → `content.stats.Periods.All`, clés `ShotsOnTarget`/`total_shots`/`corners`/`yellow_cards`/`red_cards`) |
| **ESPN** | tennis : classement + forme · basket : bilans + blessés | score + **periods** (sets tennis, quart-temps basket) · box-score joueur basket |
| **Understat** | foot : xG (top-5 ligues) | — |
| **Flashscore** | forme + H2H (3 sports) | score (radar) |
| **LiveScore** | scores **live** | score + règlement |
| **Sportradar GISMO** (feed libre) | forme / streaks / H2H / classement (3 sports) | **`match_info.periods`** (mi-temps foot, sets/jeux tennis, quart-temps basket) · **`match_details`** (tirs cadrés, tirs, corners, possession — foot ; aces, doubles fautes — tennis) |
| **TennisExplorer** *(2026-07-03)* | tennis : **bilan par SURFACE** (Terre/Dur/Indoor/Gazon, carrière + année) | — |
| **SofaScore** | ❌ direct mort (403). Réactivable via **RapidAPI SportAPI7** (`sofa_http.py`) — mais **quota mensuel 15000 épuisé** | idem |

## 3. Matrice de résolubilité par marché (audit 2026-07-03)

### ⚽ Football
| Marché | Code(s) | Réglable | Source règlement |
|---|---|---|---|
| Vainqueur / Double chance | `1X2` `DC` `REGTIME` | ✅ | score final |
| Total buts (O/U) · Total équipe | `OVER/UNDER` `TEAMTOT` | ✅ | score (global + par équipe) |
| Handicap | `HCAP` `HCAP3` | ✅ | score final |
| Mi-temps (résultat / total / BTTS / équipe / DC) | `HALFRES` `HALFTOT` `WINHALF` `BTTSHALF` `TEAMHALF` `DCHALF` | ✅ | periods (GISMO) |
| Les 2 marquent | `BTTS` | ✅ | score par équipe |
| Corners (total / équipe / handicap) | `CORNERS` | ✅ | GISMO `match_details` (Corners) |
| Cartons | `CARDS` `REDCARDS` | ✅ | GISMO / source cartons |
| Premier but · Props joueur (buts/passe) | `FIRSTGOAL` `PLAYERFB` `SCOREASSIST` | ✅ (partiel) | événements |
| Tirs cadrés / tirs (total / équipe) | `SHOTSOT` `SHOTS` | ✅ | **FotMob `matchDetails`** (source n°1, `sot_h/a`,`shots_h/a`) → repli Flashscore → repli GISMO `match_details`. Testé **8/8** sur les combos tirs CdM. |
| But dans les deux mi-temps | `BOTHHALVES` | ✅ | periods (72/72 corrects) |
| **Corners 1ère mi-temps** | — | ❌ TROU | corners par mi-temps (GISMO timeline à explorer) |

### 🎾 Tennis
| Marché | Code(s) | Réglable | Source règlement |
|---|---|---|---|
| Vainqueur | `WIN` | ✅ | score final |
| Sets (total / handicap / score / vainqueur de set) | `SET` `SETSTOT` `SETSCORE` `SETWIN` | ✅ | periods (GISMO / ESPN) |
| Jeux (total / handicap / équipe / hold / par set) | `TOTGAMES` `GAMESHCAP` `TEAMGAMES` `HOLD1` `SETGAMES` | ✅ | periods (jeux par set) |
| Tie-break | `TIEBREAK` | ✅ | periods |
| **Aces / service** | — | ❌ TROU | → GISMO `match_details` (Aces) pour le règlement · service *saison* pour l'analyse : à sourcer |
| *(analyse)* **surface / bilan surface** | — | *analyse* | **TennisExplorer** ✅ |

### 🏀 Basket
| Marché | Code(s) | Réglable | Source règlement |
|---|---|---|---|
| Vainqueur · Handicap · Total (O/U) · Total équipe | `WIN` `HCAP` `OVER/UNDER` `TEAMTOT` | ✅ | score (global + par équipe) |
| Quart-temps / mi-temps (total / vainqueur / handicap) | `BQTOT` `BQWIN` `BQTEAM` `BQHCAP` | ✅ | periods (GISMO / ESPN) |
| Props joueur (points / rebonds / passes) | `PLAYERBK` | ✅ | box-score ESPN |

## 4. Trous à combler (priorisé)
1. ~~**Tirs cadrés / tirs foot**~~ **✅ COMBLÉ (2026-07-03)** → **FotMob `matchDetails`** (`sources.foot_match_stats`) fournit tirs cadrés / tirs / corners / cartons par équipe, branché en source n°1 du règlement foot (avant Flashscore/GISMO). Testé **8/8** sur les combos tirs. Leçon : tester d'abord les sources DÉJÀ branchées.
2. **But dans les deux mi-temps (foot)** → les periods (mi-temps) sont **déjà** récupérées ; il suffit d'ajouter le code + la logique (but MT1 > 0 ET but MT2 > 0).
3. **Aces / service tennis** → GISMO `match_details` (Aces) pour régler ; le service *saison* (analyse) reste à trouver.
4. **Corners 1ère mi-temps** → corners par mi-temps (GISMO `match_timeline` à explorer).
5. **Props buteur / passeur foot** → événements joueur (GISMO `match_timeline`).

## 5. Règle
- Toute **nouvelle source** ou **nouveau code de règlement** → mettre à jour ce fichier + la mémoire.
- Vérifier la résolubilité d'un marché **avant** de l'autoriser au pari (un pari non réglable fausse le
  suivi). Cf. [[combo-publish-all-legs]], `HISTORIQUE.md`.
