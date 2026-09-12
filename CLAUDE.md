# BETSFIX — Notes projet

## ⛔ RÈGLES PERMANENTES — décisions DÉJÀ tranchées, ne pas re-débattre

> Ces points ont dû être répétés d'une session à l'autre. Ils sont **définitifs** jusqu'à ce que le
> user dise le contraire. **Avant de signaler un « problème », vérifier ici et en mémoire s'il est
> déjà tranché.** Re-poser une question réglée fait perdre du temps et de la confiance.

1. **AUCUN ABONNÉ à ce jour.** Donc re-piquer l'historique **rétroactivement est légitime** : les
   stats ne gardent QUE les paris conformes aux seuils **actuels**. ⛔ Ne PAS présenter « des paris
   publiés ne sont pas comptés » / « biais du survivant » / « posté == compté » comme un bug.
   ⚠️ Ça changera le jour où il y aura de vrais abonnés → les seuils ne s'appliqueront plus qu'en
   **forward**. Mémoire `no-subscribers-retroactive-repick-ok`.
2. **Tout ce qui s'AFFICHE = le PARI JOUÉ**, jamais le pick brut de Claude. Source unique :
   `analyses.stat_bet` / `played_result` (à venir : `display_perle`). ⛔ Ne jamais lire `d["pick"]`,
   `result.pick_result`, `bets_of`, **ni `retained_bet` sur un match RÉGLÉ** (il retombe sur le pick
   brut : c'est le bug récurrent n°1, réapparu 3×).
3. **Peu de paris Confiance/Value un jour donné = NORMAL** (créneau sans value). Ne pas
   re-diagnostiquer comme une panne d'ancre ou de quota.
4. **Ne JAMAIS recréer un mécanisme d'autostart** sans avoir vérifié **en admin** qu'il n'existe pas
   déjà (les tâches SYSTEM sont invisibles autrement — voir le piège plus bas).
5. **Foot uniquement** depuis 2026-08-07. Le code tennis/basket est dormant, pas actif.
6. **Un commit = déjà poussé** (hook `post-commit`). Il n'y a PAS d'auto-commit périodique (coupé).
7. **Vocabulaire UI : « pari joué »** — plus d'étoile ⭐ ni de « retenu ».
8. **Mémoire** : `MEMORY.md` est un INDEX (1 ligne ≤ ~145 c par mémoire, détail dans le fichier lié).
   S'il gonfle, il ne se charge que partiellement et je « repars de zéro » → ne jamais y mettre de détail.
9. **Un changement de règle se PROPAGE tout seul** (user 2026-09-02 : « ça devrait être
   automatique, je ne dois pas te dire ce genre de choses »). Dès qu'un critère de sélection change,
   je reconstruis et je **remplace** ce qui est publié mais **pas encore commencé** : réécrire le
   track (→ le site suit), **supprimer** l'ancien message Telegram, **reposter** par le chemin normal,
   **régénérer l'analyse par jambe**, `mark_sent`, puis selfcheck. On ne demande l'autorisation QUE si
   l'événement a commencé ou qu'un pari est déjà réglé. Mémoire `rule-change-propagates-automatically`.
10. **CLÔTURE DE TÂCHE — rien ne se perd entre deux conversations** (user 2026-09-02). AVANT de rendre
    la main, pour toute modif qui change le COMPORTEMENT du produit ou une DÉCISION :
    - **commit descriptif** (le *quoi/pourquoi/mesuré*, pas juste le *quoi*) — il est poussé tout seul ;
    - **mémoire** : créer/**mettre à jour** le fichier sujet concerné (`~/.claude/projects/…/memory/`)
      + **1 ligne** dans `MEMORY.md` (≤145 c). **Mettre à jour l'existant plutôt que dupliquer** ;
      marquer RÉSOLU ce qui l'est (ne pas laisser un bug corrigé décrit comme ouvert) ;
    - **CLAUDE.md** : mettre à jour la section concernée si le flux/les seuils/l'archi changent ;
    - un seuil ou un chiffre cité quelque part et modifié → **le corriger PARTOUT** (code, commentaire,
      CLAUDE.md, mémoire) : un commentaire périmé a déjà coûté des heures de re-diagnostic.
    ⛔ **Ne PAS mémoriser** ce que git/CLAUDE.md racontent déjà (détail d'implémentation, refactor
    routinier) ni ce qui n'a d'intérêt que dans la conversation : c'est ça qui avait fait gonfler
    l'index à 64 Ko et me faisait « repartir de zéro ». On mémorise une **décision**, un **piège**, une
    **mesure**, une **préférence** — pas un diff.
11. **CHECKPOINT du travail EN COURS — la session remote est JETABLE** (décision user 2026-09-05). La boucle
    `remote-control-loop.ps1` **efface l'historique de conversation** et repart en **session fraîche** à chaque
    relance (LogonTrigger/veille/coupure réseau/kill watchdog — souvent le matin) : le fil de la veille est
    perdu **par design** (prix de la stabilité remote ; remettre `--continue` réintroduit les gels watchdog). La
    **mémoire et le git survivent** → seule voie de continuité. Donc : quand le user dit **« checkpoint »**,
    **« je dors »**, **« je m'arrête »** (ou équivalent), je fige l'état dans la mémoire **`wip-current-task.md`**
    (tâche / état exact / prochaine étape / fichiers / pièges) + je mets à jour le **hook de l'entrée épinglée
    en tête de MEMORY.md** + je committe le code en cours (branche WIP si non fini). **Au DÉMARRAGE de session,
    lire `wip-current-task.md` EN PREMIER** : s'il décrit une tâche en cours, reprendre de là. Quand une tâche
    est finie → remettre l'entrée WIP à « AUCUNE ».

## Carte du démarrage automatique (Windows)

Au démarrage du PC, trois briques remontent. **Important : deux d'entre elles
tournent en compte SYSTEM et sont INVISIBLES depuis une session non-admin**
(voir le piège plus bas).

| Composant | Démarre | Sans login ? | Mécanisme |
|---|---|---|---|
| Tunnel Cloudflare | au boot | ✅ oui | Service Windows `Cloudflared` (StartType=Automatic) |
| API uvicorn `:8000` | au boot | ✅ oui | Tâche planifiée `BETSFIX-api` (User=SYSTEM, BootTrigger, auto-relance) → lance `deploy/api_service_loop.ps1` → `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` |
| Session Remote Control | à l'ouverture de session | ❌ (login requis) | Tâche planifiée `BETSFIX Remote Control` (User=vince, LogonTrigger) → lance `remote-control-loop.ps1` → `claude --remote-control BETSFIX --dangerously-skip-permissions` |

- URL publique = **`https://betsfix.com`** (+ `www`) depuis 2026-09-04, façade officielle. `api.betsfix.com`
  et `origin.betsfix.com` restent des alias (mêmes routes tunnel → `localhost:8000`, gardés pour le failover
  + scripts KV/deploy). Les 4 noms d'hôte sont des « routes d'application publiée » du MÊME tunnel (géré par
  le dashboard Zero Trust, PAS de config.yml local). URL officielle dans le code = `config.Settings.public_url`
  (env/.env `BETSFIX_PUBLIC_URL=https://betsfix.com`) → liens email + retours Stripe. ⚠️ Le worker failover
  `betsfix-failover` est encore devant `api.betsfix.com` seulement → à étendre à `betsfix.com` plus tard
  (sinon pas de filet quand le PC est down sur la façade betsfix.com). ⚠️ Ajouter le domaine racine déclenche
  une réémission du certif SSL Cloudflare → TLS instable quelques minutes/≤1 h (normal, se stabilise seul).
- `reconnexion.bat` = relance MANUELLE de secours (API + tunnel) si besoin.
- Le PID de la boucle remote est dans `.remote-control.pid`.
- Python utilisé : `C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe`
  (uvicorn/fastapi sont dans son `Lib\site-packages`, donc accessibles à SYSTEM).
- Voisin similaire sur la même machine : `CRYPTONAUTS`
  (il a sa tâche `<NOM> Remote Control`).

## ⚠️ Piège récurrent : les tâches SYSTEM sont invisibles sans élévation

Diagnostiquer le démarrage auto depuis une session normale **ment** :
- `Get-ScheduledTask` **masque** les tâches en compte SYSTEM / RunLevel Highest.
- `schtasks /query /tn <nom>` répond **« Accès refusé »** (≠ « introuvable »).
- Le dossier Démarrage (`shell:startup`) peut être vide alors que tout marche
  quand même (l'autostart passe par des **tâches planifiées**, pas par le dossier).

**Réflexe correct** : pour voir l'image réelle, relancer la requête **en admin**.
Exemple non-destructif (déclenche une fenêtre UAC) :

```powershell
Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',`
  'Get-ScheduledTask | ? {$_.Principal.UserId -match "Sys|SYSTEM"} | Format-Table TaskName,State; Read-Host'
```

Conséquence vécue : ne **jamais** recréer un mécanisme d'autostart (VBS dossier
Démarrage, nouvelle tâche…) avant d'avoir vérifié en admin qu'il n'existe pas
déjà → sinon **doublon** (ex. deux `claude --remote-control BETSFIX` qui se
disputent le même nom de session = aucune session visible côté claude.ai/code).

## Vérifs rapides

```powershell
# Tunnel
Get-Service Cloudflared | Format-Table Status,Name,StartType
# API locale
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
# Une seule session remote ? (doit montrer 1 boucle + 1 claude)
Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
  ? { $_.CommandLine -match 'remote-control BETSFIX' } | Select ProcessId
```

## ⚠️ BETSFIX = 100 % FOOTBALL (depuis 2026-08-07)

Tennis et basket ont été **retirés** (scan, UI, données supprimées) — mémoire
`football-only-tennis-basket-removed`. **Le foot est le seul sport.** Le code
sport-paramétré (tennis/basket) est **dormant** mais conservé. Toute mention
tennis/basket ci-dessous décrit un rôle **dormant**, pas actif.

## Sources de données & analyse (foot — MAJ 2026-08-29)

### Sources par rôle (toutes vérifiées vivantes)
| Source | Rôle (foot) | Statut |
|---|---|---|
| **Unibet** | cotes + marchés + **sélection** des matchs | ✅ |
| **Pinnacle** | ancre « sharp » (proba de référence, faible marge) | ✅ |
| **FotMob** | forme / blessés / H2H / météo | ✅ |
| **ESPN** | (dormant : ex-tennis/basket) | 💤 |
| **Understat** | xG (top-5 ligues) | ✅ |
| **Flashscore** | forme + H2H + compos | ✅ |
| **LiveScore** | scores **live** (onglet radar) + **règlement** des paris | ✅ |
| **Sportradar (GISMO)** | forme · **streaks de pari** (sans défaite/marque/BTTS/over) · H2H · classement · **moyennes buts & over 2.5** — feed LIBRE `lsc.fn.sportradar.com`, `app/sportradar.py` branché à `sources.extras` + routeur `/sportradar/*` · **+ RÈGLEMENT** : `sportradar.final_score()` lit `match_info.periods` (repli `need_periods` dans settle_analyst) | ✅ |
| **SofaScore** | séries de pari · votes · scores live · event/h2h/lineups/incidents (Sportradar GISMO reste l'upstream principal) | ✅ **re-vérifié vivant 2026-07-28** |

> **Ancre sharp** : Pinnacle brut via **iProyal** (proxy, prioritaire, monde entier, `app/pinnacle.py`) ;
> **The Odds API** en secours (~68 ligues, `app/theoddsapi.py`). Verrou `no_sharp` dans `build_dossier` : un
> match foot SANS ancre sharp live est **différé** → 100 % des paris publiés portent une ancre. Mémoire `sharp-anchor-theoddsapi`.
> **Garde anti-résolution-fausse (2026-09-02)** : si le favori sharp CONTREDIT le favori marché (réf omap,
> écarts nets opposés), l'ancre est JETÉE → `no_sharp` → différé (flag `sharp_conflict`). Évite un EV calculé
> sur une ancre inversée (cas Saint-Trond–Union : sharp 48 % dom vs omap 3.75 outsider). Mémoire `pinnacle-match-resolution-confusion`.

### ✅ SofaScore RE-VÉRIFIÉ VIVANT (2026-07-28) — l'ancien « MORT » était une panne temporaire
- **Contrôle empirique 2026-07-28** : les 3 voies (`app/sofa_http` cascade) répondent **HTTP 200**
  (direct curl_cffi + RapidAPI + proxy) sur live/search/event/h2h/incidents/votes/lineups, et
  `_resolve_sofa` **résout 4/4** vrais matchs (via le repli `/search/all`).
- **Cause racine de l'ancien « mort »** : une **DOUBLE panne SIMULTANÉE temporaire** — Cloudflare 403 sur
  le direct **ET** quota RapidAPI mensuel épuisé (cf. `generate_analyses.py:70`). Les deux voies HS en même
  temps → source jugée définitivement morte. **Les deux conditions ont disparu** (quota revenu, blocage levé).
- **RapidAPI = À GARDER** : il répond 200, c'est le filet payant qui rattrape sur 403/429. Ne pas le résilier.
- **404 ≠ mort** : `statistics` 404 sur un match amateur = pas de données (404 aussi sur RapidAPI), et
  `scheduled-events/{jour}` 404 sur ce endpoint bulk précis — mais la résolution passe par `/search/all`.
- **Surveillé en continu** : sonde `source_health._p_sofascore` (direct, repli RapidAPI si direct KO) →
  visible dans `/health/sources`. Suivre la stabilité quelques jours **avant** de re-brancher l'enrichissement.
- **Pas encore re-câblé** : l'enrichissement actif reste multi-sources (FotMob/ESPN/Understat/Sportradar) ;
  la réactivation de SofaScore dans le scan/affichage est une décision séparée (surface de régression).
- Reste vrai : **Elo tennis RETIRÉ** (4ee2d45) + garde-fou anti-écrasement des builds (ba61e1b).

### L'enrichissement vivant = `app/sources.py`
- `sources.extras(client, sport, match)` → FotMob/Understat + Flashscore + Sportradar,
  **branché au scan** (`tools/generate_analyses.py`).

### Le scan = `tools/generate_analyses.py`
- Pilote Claude headless (`claude -p`), faits web ≥2 sources.
  **DOIT** tourner en session `vince` (authentifiée) + **réseau requis**
  (lancer avec sandbox désactivé).
- Usage : `python tools/generate_analyses.py --sport foot --top 10 --hours 24`
  (`--sport` par défaut = `foot` ; **ignoré** s'il vise autre chose que le foot).
- Un `getaddrinfo failed` ponctuel = hoquet réseau transitoire → relancer.

### ⚠️ La SÉLECTION du pari est MÉCANIQUE (depuis fantômes), pas le pick Claude
Refonte 2026-08-29 (mémoire `confidence-bet-backtest-93-profile`). Claude **analyse**
et nourrit les **fantômes** ; le pari joué est ensuite choisi par des **sélecteurs
mécaniques** backtestés :
- **Confiance** = `app/confidence_pick.py` — DC/Handicap, conf ≥80, **cote 1.12–1.50**, le + sûr
  (~93 % / **+9 %**). Borne basse **relevée 1.05→1.12 le 2026-09-07** (backtest train/test) : la bande
  1.05–1.12 était NET-NÉGATIVE (45 paris, 89 % mais ROI −2,9 % — à cote ~1.08 il faut ~92 % pour l'équilibre).
  ROI +5,0 %→+9,0 %, robuste train +10,4 % / test +7,2 %, −32 % de volume. Historique **re-piqué** (45→abstention,
  1→value, 1 re-pick ; `stat_bet` re-figé via `backfill_stat_bets`, filigrane selfcheck réinitialisé à 118).
  Plafond cote **1.50** (`3abb5f0`).
- **Value** = `app/value_pick.py` — conf **≥66** · cote **1.30–2.30** · EV ≥ +5 % (MAJ 2026-09-12 : ABAISSÉ de
  68/1.40 car « plus aucune value depuis 12 j » ; re-backtest TRAIN/TEST juin→auj : 66/1.30 tient dans les DEUX
  moitiés, ~75-79 % / +21-24 %, en DOUBLANT le volume ~5→~12/mois ; 64/60/58 s'affaiblissent en test, EV+3 %=piège.
  ⚠️ cote 1.30 à SURVEILLER sur les stat_bet FIGÉS. Telegram Value reste OFF). Cote la + haute, sur matchs **SANS**
  confiance. Marchés sauf bans **+ « Total Over » exclu** (`_VALUE_BAN_MARKETS` = seul marché value perdant :
  56 %/−9 %). Optim via backtest fantômes 1/match + train/test (mémoire `value-exclude-total-over`). **Historique
  RE-PIQUÉ sous 66/1.30 le 2026-09-12** (user « les stats value doivent correspondre au filtre ») : re-lancé
  value_pick sur tous les sidecars réglés value-éligibles (hors confiance/combo/différés `no_sharp`/`sharp_conflict`/
  `_removed_wrong_anchor` → Aigles du Congo NON ré-ajouté), `value_bet` reposé + `stat_bet` re-figé via
  `backfill_stat_bets`. ADDITIF : **+14 value** (0 retiré, monotone intact, pas de reset filigrane), record value
  passé à ~34/40 · +29 %. Backup `data/_repick_value_backup_2026-09-12/`. Le 2026-09-01 (58→68) l'**historique avait été RE-PIQUÉ**
  (2026-09-01, pas d'abonnés) : 46→23 value (23 retirés = abstentions), stat_bet re-figé EXPLICITEMENT (pas via
  `retained_bet`/`stat_bet` qui ressuscitent le pari publié) + filigrane monotone remis à 0.
- Verrous dans `app/analyses.py` : `FOOT_MECHANICAL_ONLY=True` (le foot ne prend QUE
  le pari mécanique) · `REVEAL_ONLY_FINAL=True` (voir flux Option B).
- ⚠️ **INVARIANT (bug 2026-09-07)** : le vivier de sélection `confidence_pick.match_candidates` **EXCLUT**
  les ghosts `ghost_from=="pre_refresh"` (prédictions de l'analyse PRÉCÉDENTE reportées par
  `_carry_shadow_from_old` **pour le CALIBRAGE SEUL**). Sinon un vieux DC/handicap sûr est publié malgré une
  **abstention fraîche** (cas Cruz Azul : Confiance apparue vs fiche QC « abstention »). Seule l'analyse FRAÎCHE
  décide ; les `pre_refresh` restent dans `shadow` (calibration intacte). Mémoire `selection-excludes-pre-refresh-ghosts`.
- **Montante = SUPPRIMÉE (user 2026-09-11)** — tout le système de mise progressive a été **retiré du produit
  ET du code source** (module `app/montante.py` + `tools/montante.py` supprimés, affichage/stats/build/notifs
  purgés). Données archivées `data/_montante_removed_2026-09-11/`. ⛔ Ne PAS recréer sans demande explicite.
  Mémoire `montante-removed`.

### ⚠️ VERDICT / COTE / ANALYSE d'affichage = le pari JOUÉ, JAMAIS le pick brut (MAJ 2026-08-31)
Depuis la refonte mécanique, le pari joué (`stat_bet`/mécanique) **DIVERGE** du pick brut de Claude
(`d["pick"]` / tableau `.md`, qui note un AUTRE marché). Tout ce qui s'AFFICHE doit suivre le pari joué :
- **Source unique** = `analyses.played_result(d)` (combiné → `stat_bet` → repli `pick_result`). Vaut pour le
  bandeau, le chip « Terminés », le bord coloré, la **carte résultat** (site + Telegram), et la **cote publiée**.
- L'**analyse « Pourquoi ce pari »** suit aussi le pari joué : `played_why` (sonnet) généré dédié au scan
  (sinon l'affichage montrait le raisonnement du mauvais pari).
- Garde-fous selfcheck `_check_verdict_reads_played_bet` **et `_check_settled_prono_card_reads_played_bet`**
  (30e contrôle, 2026-09-02). Bugs récurrents (mémoires `result-verdict-follows-played-bet`,
  `session-2026-08-31-omap-combos-telegram`). ⛔ Ne JAMAIS lire `pick_result`/`d["pick"]`/`bets_of` pour un affichage.
- ⚠️ **PIÈGE `retained_bet` (2026-09-02)** : sur un match **RÉGLÉ**, `analyses.retained_bet()` ne reconstruit
  PLUS le pari mécanique (gardes `not stat_bet`) et retombe sur `_recommend()` = **pick brut du `.md`**, ou None.
  Mesuré : 7/115 cartes justes seulement (67 vides, 30 mauvais pari, **11 verdicts inversés**). Corrigé dans
  `card_data.build_prono_card`, `web._sport_row` (branche terminé) et `analysis_quality` (fiche QC privée).
  **Règle : sur un réglé → `stat_bet`.** `retained_bet` ne reste correct qu'AVANT règlement (scan/à-venir).

### omap = VRAIES cotes Unibet captées au scan (paris ET abstentions)
`_unibet_odds_map` (dans `build_dossier`) capte la cote Unibet réelle par code, persistée `side["omap"]` et
re-price fantômes+paris. Captée pour CHAQUE match — paris (`_write_sidecar`) **ET abstentions** (la fiche
minimale d'abstention ne l'écrivait pas → réparé 2026-08-31, re-fetch synchrone). Sans vraie cote, une jambe
est exclue du vivier combiné. Mémoire `omap-unibet-cote-capture` (RÉSOLU).

### Flux « Option B » (matin → vague KO−1h)
- **Matin (~10h)** : analyse + sélection mécanique **CACHÉE** (état « À analyser » ;
  un pari reste provisoire, `0f89733`).
- **Vague KO−1h** : recalcul sur cotes fraîches → **publication** (app + Telegram).
  La vague pose `prematch_done` puis publie (`published_bet`). Tant que non publié,
  le pari mécanique est masqué sur l'app (`757ac3e`).

### Monitoring `/monitor`
- Dashboard live (`tools/monitor.py`, route dans `app/routers/web.py`), consultable
  mobile : produits déployés, maturité des marchés, calibration brute, et vue
  **FORWARD réel vs Historique (backfill)** (`537171b`/`4a2ecf1`/`9783807`).
## Combinés du jour + du soir (MAJ 2026-08-31 — refonte complète)
> ⛔ **COMBINÉS STOPPÉS + MASQUÉS + HORS ROI « pour le moment » (user 2026-09-11)** — kill-switch
> `combo_daily.COMBO_ENABLED=False` **ET** `analyses.COMBO_ROI_ON=False`.
> `_build_combo_montante_from_analysis` est court-circuité (ne bâtit plus de combiné). **MASQUÉS PARTOUT** via
> `web._combos_shown()` (= `COMBO_ENABLED`) : plus aucune carte/onglet/zone combiné — zone « Combiné » du Pronos,
> carte à venir (`_combo_tg_card`), carte de match combiné (`_sport_row`), carte résultat (`_settled_bet_result_cards`),
> onglet + courbes « Combinés » des Stats (`render_stats`/`render_sport_perf`), **contribution au hero**
> (`routers/web._hero_card`, sinon le hero les recomptait à cause de `COMBO_ROI_ON=False`), et **% du jour du
> calendrier horizontal** (`_daily_all_results_map` — les jambes ne comptent plus). **HORS ROI** (`COMBO_ROI_ON=False`
> → retirés de `all_ev`/overall). Les combinés déjà réglés ne sont donc **plus comptés ni affichés** (revert = les
> 2 flags à True). **Conséquence VOULUE :
> plus d'analyse complète matin/soir** (elle n'existait QUE pour le combiné) → **une seule analyse par match à
> sa vague KO-1h** → fin de la DOUBLE analyse + des « premières abstentions » (fantômes `pre_refresh`).
> **Réactiver** = `COMBO_ENABLED=True`
> + rétablir la passe `--daily-combo` dans `scan_daily.ps1` **et** `scan_evening.ps1`. Le reste ci-dessous
> décrit le mécanisme conservé (dormant), réactivable tel quel. Mémoire `combos-stopped-single-wave-analysis`.

DEUX combinés/jour : variant `""` = **Combiné du jour** (scan matin, slate jour) · `"soir"` = **Combiné du
soir** (scan soir, slate nuit). `app/combo_daily.py` + `tools/generate_analyses._build_combo_montante_from_analysis`.
- **Vivier MULTI-MARCHÉS** (`_harvest_combo_legs`) : la sélection sûre la plus probable de CHAQUE match
  analysé, familles **Vainqueur / Double chance / Total équipe** (`_COMBO_ANALYSIS_MARKETS`, `match_candidates`),
  **VRAIE cote Unibet obligatoire** (omap). Plus « DC seule ».
- **Sélection SÉCURITÉ** : ≤3 jambes (`COMBO_MAX_LEGS_SAFE=3`), plancher de sûreté 50 % (`COMBO_MIN_SAFE_PROB`),
  on descend `COMBO_ODDS_LADDER` (1.95→1.40) → cote la plus HAUTE en restant sûr (fini le PASS systématique
  des nuits de gros favoris ET le combiné risqué à cote forcée).
- **COMPTÉS AU ROI + stats** : `analyses.COMBO_ROI_ON=True` ; variant "soir" ajouté à l'agrégation
  (`stats_full`, `combo_stats`, `pending_roi_bets`). Overall = Confiance + Value + Combiné.
- **RÈGLEMENT LIVE d'une jambe BUTS-OVER acquise (2026-09-02)** : une jambe monotone (total OVER, total équipe
  OVER, BTTS YES) déjà gagnée se valide EN LIVE (irréversible), sans attendre la fin — 2 sources live
  concordantes (Flashscore partiel `final_score(allow_live=True)` + LiveScore), anti-collision. JAMAIS
  Under/vainqueur/handicap/DC/périodes/stats. `combo_daily._live_over_settle`. Mémoire `settle-never-on-live-score`.
- **PLUS publiés sur Telegram NI en push PWA (user 2026-09-08)** : les combinés **et leurs jambes** restent
  **sur le SITE uniquement**. `combo_daily.notify_combos` respecte enfin `notify.TG_COMBO_MONTANTE=False` (il
  l'ignorait → les combinés partaient quand même : bug corrigé) ; le push PWA des jambes/combiné est coupé par
  `push.PUSH_LEGS_COMBOS=False` (rafale « JAMBE GAGNÉE » quand plusieurs jambes se règlent d'un coup = spam
  signalé par le user). Le code d'envoi (carte `tg_msg` + « JAMBE GAGNÉE ✅ » + « COMBINÉ GAGNÉ ✅ » global,
  idempotent `tg_msg`/`leg.tg_done`/`tg_result_done`, anti-spam `COMBO_TG_FROM`) reste **intact et gaté** →
  réactivable via `TG_COMBO_MONTANTE=True` (Telegram) / `PUSH_LEGS_COMBOS=True` (PWA). **Alerte privée owner**
  (`_owner_alert_once`) si aucun combiné, conservée. ⚠️ Les combinés DÉJÀ postés (jour 01→08/09, soir) restent
  dans le canal (forward-only ; suppression manuelle possible sur demande).
- **BANS DURS gravés** (`COMBO_MISSION`, taux par jambe mesuré 2026-06-18) : 🔴 **TOUS les corners**, tirs
  TOTAUX, cartons, premier but / mi-temps. 🟢 privilégier résultat / double chance (83 %), tirs **cadrés**
  (83 %), buts total / équipe marque (79 %).
- **Intitulé double chance uniforme** : « **\<équipe\> ou nul (1X)** » partout via `analyses.pretty_sel`.
- Mémoires : `two-combos-jour-soir`, `session-2026-08-31-omap-combos-telegram`, `telegram-foot-simple-only`.

## Timeline quotidienne (heure Europe/Brussels)
> ⚠️ **MAJ 2026-09-11 (combinés stoppés)** : le matin/soir ne font **PLUS l'analyse complète** du slate (elle
> n'existait que pour le combiné). Ils **SÉLECTIONNENT** seulement (+ logos + planif/replanif des vagues).
> **Toute l'analyse + la publication se font à la vague KO-1h** — une seule fois par match.
- **~10h — scan JOUR** (`deploy/scan_daily.ps1`) : **SÉLECTION seule** du slate jour (`--programme`) +
  **vérif/pré-chauffe des LOGOS** + **planif des vagues**. ~~analyse cachée + combiné du jour~~ (retirés).
- **~19h — scan NUIT** (`deploy/scan_evening.ps1`) : **SÉLECTION seule** du slate nuit (`--programme`) +
  fusion + **vérif/pré-chauffe des LOGOS** + **replanif des vagues**. ~~analyse + combiné du soir~~ (retirés).
- **LOGOS** (`tools/logo_check.py --quiet --alert`, 2026-09-02) : résout le blason des 2 équipes de chaque
  match du programme → **pré-chauffe `crest_cache.json`** (logo prêt à la publication), **vérifie l'URL en 200**,
  **auto-répare** via les fixtures FotMob du jour (ancrage sur l'adversaire reconnu + KO — indispensable quand
  les libellés n'ont aucun token commun : « Saint-Trond » ↔ « St.Truiden »), **alerte privée** si un logo manque.
- **KO−1h — vagues** (`deploy/scan_wave.ps1` → `--refresh-early`) : **analyse (UNE seule fois)** chaque match
  ~1h avant SON coup d'envoi, **PUBLIE** le pari (app + Telegram), puis reconcile (règlement + résultats
  combinés par jambe). Cap **10+10** (jour+nuit) + pack élite
  (relevé de 7+7 le 2026-09-11, permis par le retrait de la double analyse : ~2× moins de charge Claude/match →
  ~20-24 matchs/jour restent à/sous l'ancienne consommation). Mémoire `daily-construction-methodology`
  (flux de référence + invariants anti-bug) + `combos-stopped-single-wave-analysis`.
- **⛔ LE NOMBRE DE MATCHS N'AFFAIBLIT PAS L'ANALYSE/SÉLECTION (garantie, user 2026-09-12)** — décision tranchée,
  ne pas re-débattre : chaque match a son PROPRE `run_claude` (timeout INDÉPENDANT, `generate_analyses.py`
  boucle `for … in top`), **aucun budget token/temps PARTAGÉ ni retry réduit** selon le nombre. Les sélecteurs
  `confidence_pick`/`value_pick` sont **par match** (`pick_from_candidates`), **sans classement inter-matchs** (un
  pari valide n'est jamais retiré parce qu'il y en a d'autres). À la vague, `--from-programme` **fetch 200 puis
  filtre aux IDs du programme** → `--top 10` n'est PAS contraignant, **aucun match du programme n'est droppé**.
  17 matchs = 17 analyses complètes indépendantes. **Vérification PAR MATCH** = `tools/analysis_quality.py`
  (lancé à chaque vague dans `scan_wave.ps1`) : couverture (0 manqué), profondeur (.md ≥ 2,5 ko + panel),
  conversion, **et l'audit 4 piliers `_qc_audit` (SCAN/ANALYSE/SÉLECTION/SOURCES) sur CHAQUE match** — un pari
  JOUÉ+FINAL avec un pilier SÉLECTION/SOURCES ❌ (cote hors bande/hors omap, proba≫implicite, ancre fabriquée/
  absente, <2 sources) remonte en **alerte privée owner** (dédup/jour). Ligne bilan « Vérification : N/N paris
  vérifiés ». Selfcheck `_check_final_mechanical_bet_revealed` garde en plus qu'un pari mécanique dont la vague
  est passée est bien publié. **TRIPWIRE QUEUE vs CŒUR** (user « la dernière fois qu'on a augmenté le nombre
  c'était la catastrophe ») : le **rang de sélection PAR SLATE** est figé dans le programme puis le sidecar
  (`sel_rank`/`sel_slate`, reporté à la vague — le rang de la boucle vague n'est PAS le rang de sélection). Le
  match `--programme` tague le cœur top-N (0..N-1) ; élite forcé = pas de rang. `analysis_quality.py --tail-check`
  (lancé 1×/jour dans `scan_daily.ps1`) compare les paris JOUÉS réglés de la **QUEUE (rang ≥ 7 = ajoutés par le
  cap 7→10)** au **CŒUR (<7)** : réussite % + ROI + conversion du jour. Alerte privée SEULEMENT si la queue
  sous-performe NETTEMENT (n≥12, réussite −12 pts ET ROI −15 pts ET ROI queue négatif) → preuve chiffrée que le
  nombre ne dilue pas (et détecteur si un jour ça change). Mémoire `per-match-verification-count-independent`.

## Autres sous-systèmes
- **Auth / abonnement** : base users **SQLite** `app/userdb.py` (migration JSON→SQLite auto), API
  `app/accounts.py` (login anti brute-force, reset mdp `/forgot`+`/reset`, vérif email `/verify`),
  `app/mailer.py` (SMTP env `BETSFIX_SMTP_*`, repli `data/outbox`). Tiers free / trial(3j auto) / monthly.
  **Connexion/inscription par CODE à 6 chiffres (email) = chemin par DÉFAUT** (`/login`,`/signup`,`/compte`
  déconnecté → `_code_form` ; mot de passe conservé en repli via `?pw=1`). STATELESS : le code n'est PAS
  stocké — lié au jeton signé (`accounts.make_login_code`/`check_login_code`, secret HMAC + code en
  `extra_key`). `POST /auth/code` (envoie, throttle 5/15min) → page 6 cases (auto-avance/coller/auto-valide)
  → `POST /auth/verify` (anti-force-brute email+IP, `ensure_user` crée le compte+essai, `email_verified=1`).
  ⚠️ SMS volontairement écarté (coût récurrent) : même UX, canal EMAIL gratuit. Mémoire `email-code-passwordless-auth`.
  **SMTP = Brevo BRANCHÉ** (2026-09-04) : identifiants dans `.env` (gitignoré), lus via `config.Settings`
  (alias `BETSFIX_SMTP_*`) — PAS `os.environ` (invisible au SYSTEM). Free 300 mails/j.
  **Domaine `betsfix.com` AUTHENTIFIÉ chez Brevo** (DKIM `brevo1/brevo2._domainkey` CNAME + `brevo-code` TXT +
  `_dmarc` TXT, posés dans Cloudflare DNS en « DNS only »). Expéditeur = **`BETSFIX <noreply@betsfix.com>`**
  (pas de boîte à créer, envoi seul). Envoi réel vérifié (site → code depuis noreply@betsfix.com, plus de repli
  outbox). Le sous-domaine « branded » Brevo (tracking links) reste NON configuré = optionnel.
  **Stripe = MANAGED PAYMENTS** (décidé 2026-09-04, user « Stripe gère toute la TVA ») : Stripe = merchant of
  record, collecte+reverse+déclare la TVA (+3,5%/transaction) ; code prêt (`billing.py`, flag `managed_payments`
  dans `data/stripe.json`), abo **9,99€/mois**. **CONFIGURÉ + TESTÉ E2E en SANDBOX (2026-09-04)** : produit/prix
  (`price_1UC0rn…`, 9,99€ TVA incluse)/webhook créés par API, `/billing/subscribe`→checkout Managed Payments,
  webhook→abonné. ⚠️ Stripe a 2 env de test (« Mode test » vide vs « BETSFIX sandbox » où tout vit — comptes
  séparés). ⚠️ MP ne dispense PAS le user de déclarer son REVENU (fisc belge + statut). Mémoire `stripe-billing-managed-payments`.
  **Reste à faire** : Stripe **LIVE** (clés prod + KYC + recréer produit/webhook en prod) ; héberger hors PC. **Plateforme comptes = Supabase** (pas Firebase,
  décidé 2026-09-04) : projet dédié **BETSFIX** `xlmahadeeodkkxlplgrw` (org WTF, eu-central-1) **préparé**, table
  `public.users` = miroir de userdb (RLS ON), à ACTIVER au déménagement hors-PC (free tier se met en pause si
  inactif). Auth maison CONSERVÉE (pas de Supabase Auth). Mémoire `auth-subscription-scale-foundation`.
- **Filet de survie site** : Cloudflare Worker `betsfix-failover` devant `api.betsfix.com` sert un snapshot
  **KV** si le PC est down. `deploy/snapshot_to_kv.py` (tâche `BETSFIX-KV-Snapshot`, 30 min) + `deploy/worker/`.
  Jeton en env user `BETSFIX_KV_*`. Mémoire `cloudflare-kv-snapshot-failover`.
- **selfcheck** = `app/selfcheck.py` : ~30 garde-fous d'intégrité (compteur monotone, cohérence affichage↔stats,
  combinés, omap, **verdict = pari joué**…), lancé après chaque scan/reconcile.
  Vérif : `python -c "from app import selfcheck; print(selfcheck.run()['counts'])"` → doit être `error:0`.
- **Monitoring** : `/monitor` (`tools/monitor.py`) — forward réel vs backfill, calibration, maturité des marchés.
- **Perf accueil** (rendu ~10× plus rapide, 2026-09-01) : caches `_HMR_CACHE` (rows), `fragcache` (fragment
  jour), `_ODDS_CACHE` (cotes live 25 s, stale-while-revalidate, **non-bloquant au cold start** `4f6013c`),
  snapshot `data/_stats_snapshot.json`, et surtout **throttle 2 s de `_fid_index` + `iter_meta`** (`_FID_SIG_TTL`/
  `_ITERMETA_TTL`, `3a190fa`) : sans lui le rendu re-scannait+re-parsait les ~720 sidecars des MILLIERS de fois/
  requête. La barre de nav mobile est recalée à la réouverture (bfcache iOS) via `_relayout` sur `pageshow`.
- **UI** : calendrier stats = **taux de réussite** (jour/mois, plus le ROI), KPIs = jours-avec-paris + paris-joués
  (Confiance seule) ; « Programme du jour » **fermé** dès qu'un pari existe dans une catégorie ; intitulé DC
  « \<équipe\> ou nul (1X) ».
- **CARTES = style « CLASSIC » (logos d'équipe + nom en dessous) — DÉFAUT depuis 2026-09-06** (`CARD_STYLE`
  dans `app/web.py`, défaut `classic`). Le user a **annulé la refonte « signature »** du 2026-09-05 et voulu
  revenir aux cartes « début de semaine » : **vrais logos (crest) des 2 équipes + nom dessous**, score central
  (0-0 HT / TERMINÉ), confiance % + cote, ligne marché/edge/value, barre « confiance live », badge GAGNÉ/PERDU,
  « Pourquoi ce choix ». ⛔ Ne PAS re-proposer de basculer sur signature sans demande explicite.
  Les styles **signature** (`app/card_signature.py`, classes `.sg-*`), **ticket** (talon/code-barres) et
  **unibet** (E plat) restent **conservés intacts** (code mort, gate par flag) → réactivables via l'env
  **`BETSFIX_CARD_STYLE=signature|ticket|unibet`** (reload auto). Mémoires [[signature-card-style]], [[ticket-card-style]].
  - **RÉSULTAT gagné/perdu (MAJ 2026-09-06)** : le **CADRE coloré est CONSERVÉ** (vert gagné / rouge perdu) + un
    **badge ✓/✗ dans le COIN haut-droit** (`.mc-corner`, SVG coche/croix). Ce qui DISPARAÎT = le **gros bandeau
    plein-largeur « GAGNÉ/PERDU » sous les stats** (`cleg-resbadge`, retiré pour won/lost ; **conservé** pour
    REMBOURSÉ/ANNULÉ push/void qui n'ont pas de badge coin). Sur `.row.mc` (`_sport_row`) le score passe au
    centre + « Terminé » (plus de chip score haut-droite) ; sur `.cleg.cleg-res-live` (`_leg_card` live_layout,
    = cartes résultat) idem. Les **combinés** (`_combo_gold_card`) gardent leur bandeau `mc-combo-res`
    (layout à part : dots par jambe). ⚠️ NE PAS confondre « cadre » (bordure, gardée) et « bandeau » (retiré).
    **Sous le pari (fiche RÉSULTAT réglée) = Confiance + Cote SEULS** : la légende « marché % · edge · value »
    est RETIRÉE (métrique de value d'avant-match, inutile une fois réglé) via `hide_context=True`
    (`analyses.verdict_line` / `web._verdict_block`), passé par `_leg_card` quand won/lost/push/void. Avant
    règlement (à venir/live) : légende inchangée.
  - **FILIGRANE logo (MAJ 2026-09-06)** : sur **toutes** les cartes, **opacité .05** (discret, validé user),
    **taille UNIFIÉE 135px** (`.row.mc::before` ET `.cleg::before` ; avant 150 vs 120 = tailles différentes).
    `.row.mc::before` + **`.cleg::before`** (jambes/cartes-résultat). Pour les **combinés**, le logo vit
    dans le **cadre des JAMBES**, PAS sur le cadre global doré (`.row.mc.mc-tg-gold::before{content:none}`).
    **`.cleg::before` CENTRÉ verticalement** dans le cadre REPLIÉ (`top:0;bottom:0` + `background center 80px`,
    offset px FIXE depuis le haut = centre d'un cadre ~266px) → le logo **reste à la même place quand le pli
    « Pourquoi » se déplie** (la carte grandit vers le bas, l'offset top ne bouge pas).
    ⚠️ **EXCEPTION « Prochains lives » (onglet Live, user 2026-09-06)** : les cartes compactes de CETTE zone
    N'ONT PAS de filigrane. Les cartes du bloc sont marquées `_no_wm` → classe `mc-nowm` (`_sport_row`, branche
    `_compact`) → `.row.mc.mc-nowm::before{content:none}`. Toutes les AUTRES cartes compactes (autres onglets/
    zones) gardent le filigrane. Ciblage par flag (pas par sélecteur de zone) = pas de fuite ailleurs.
  - **CALENDRIER horizontal — % DE RÉUSSITE du jour (MAJ 2026-09-07 ; combinés retirés 2026-09-11)** : le point de
    couleur est REMPLACÉ par le **taux de réussite chiffré** des paris du jour (Confiance + Value ; ⚠️ **combinés
    EXCLUS depuis le 2026-09-11**, cf. `_combos_shown()` — `_daily_all_results_map` s'arrête à Confiance+Value tant
    que `COMBO_ENABLED=False`).
    Rendu `.dcd-pct` (`N%` coloré). Règle de couleur : **<50 % rouge · 50–75 % orange · >75 % vert**. Jour sans pari
    réglé -> place réservée, invisible. Cliquabilité du jour = tous paris (`rmap`). (`_daily_conf_results_map` reste
    utilisé ailleurs pour les KPI Confiance.)
  - **HALO/GLOW des cartes DANS la marge + bouton « haut de page » (MAJ 2026-09-06)** : le glow bleu des cartes
    (`0 0 18px rgba(34,184,255,.22)` sur `.row.pick/.spf/.live-empty`) semblait « coupé » car `#panels{overflow-x:
    clip}` (rogne le glissement d'onglet) avait sa boîte de clip à x=16 (= bord de carte, via `.wrap{padding:0 16px}`).
    Fix : `#panels` garde le clip mais sa boîte s'étend aux **bords de l'écran** (`margin -16 + padding 16`) → cartes
    toujours à x=16, le glow va DANS la marge et se fond au bord (blur ≤ marge 16px sinon léger cut). Idem glow bas
    de la pastille du jour (`.daycal-track` padding-bas 15px). **Bouton `#bfx-totop`** (coin bas-droit, discret) :
    smooth-scroll **MANUEL (rAF)** — `window.scrollTo({behavior:'smooth'})` est ignoré SANS erreur en PWA iOS.
    Mémoire `ui-halo-glow-clip-and-ios-scroll`.
- **Telegram = CONFIANCE UNIQUEMENT** (MAJ 2026-09-08) : le canal abonnés ne reçoit QUE les paris simples de
  tier **confiance**. La **Value** (comme le **combiné**, déjà OFF) reste sur le **SITE** +
  **push PWA** + **stats/ROI**, mais n'est PLUS annoncée sur Telegram. Gate unique `notify.tg_post_tier(tier)`
  (flags `TG_VALUE=False`, `TG_COMBO_MONTANTE=False`), appliqué à l'annonce (`generate_analyses`), au re-post
  (`reconcile._repost` + détection « manquée »), et au renotify manuel. Un résultat simple n'est posté QU'en
  réponse à un prono réel (`get_prono`) — donc supprimer l'annonce Value supprime aussi son résultat. Remettre
  `TG_VALUE=True` re-publie la value. Cf. `telegram-foot-simple-only`.
  - **CARTE IMAGE SIMPLE = LE VRAI `.row.mc` DU SITE, 100 % IDENTIQUE (MAJ 2026-09-12)** : `tools/card_image.py`
    ne REPRODUIT plus le look du site — il **rend le site**. `_site_card_html(d, settled=)` bâtit le markup EXACT
    de `web._sport_row` (carte premium à venir `mc-prem mc-flat` : `.mc-head`/`.mc-teams`/`.mc-sub`) et la page
    **inline la feuille de style ENTIÈRE `web.CSS`** + `<body class="sp-foot">` → chaque règle `.mc-*`/`.vb-*`/
    `:root` s'applique à l'identique (ZÉRO cherry-pick, **ZÉRO dérive future** : c'est la cause racine des écarts
    d'avant). Verdict = le VRAI `web._verdict_block`→`analyses.verdict_line` (fini le look-alike `_verdict_site_html`).
    Équipes/logos = markup `web._teams_vs_html`/`_crest_badge` (helpers `_site_teams`/`_site_crest`). **Bord GOLD**
    (site `--st-soon`, plus de bleu), fond radial bleu, **FILIGRANE** logo (`.row.mc::before`, réinjecté en data-URI
    car `/static/logo.png` injoignable en file://), gloss MASQUÉE (comme le site, règle globale `.mc-gloss{display:none}`).
    Décompte `.cd` rempli par le VRAI `web._COUNTDOWN_JS`. Écarts techniques file:// invisibles : logos par URL FotMob
    absolue (pas la route `/crest?name=`), largeur mobile fixe 430px. Clip screenshot = `.row.mc` (repli `.card`).
    **Fiche RÉSULTAT** (`settled=True`) = même carte : score au centre + « Terminé », bord coloré + **badge ✓/✗ coin**
    (`.mc-corner`), verdict Confiance+Cote seuls (`hide_context`). `_simple_card_html`/`_result_simple_card_html` = fins
    wrappers de `_site_card_html`. ⚠️ **Combiné** garde encore l'ANCIEN renderer look-alike (`_combo_card_html`/`_CSS_SIMPLE`/
    `_verdict_site_html`/`_team_logo_html`, conservés) — OFF sur Telegram de toute façon. **CROP SERRÉ** conservé
    (`_normalize_card(ratio=None, pad=40)` pour simple + résultat-simple) et **IMAGE SEULE** (`send_photo_sync(png,"")`).
    La RÉPONSE RÉSULTAT texte (`reply_sync`) reste inchangée. Mémoire `telegram-card-is-the-site-card`.
- **Push PWA** (MAJ 2026-09-08) : notifie les **paris SIMPLES** (nouveau prono + résultat, won/lost). Les
  **JAMBES et COMBINÉS ne poussent PLUS** (`push.PUSH_LEGS_COMBOS=False`) : quand plusieurs jambes se réglaient
  dans la même passe reconcile, chacune émettait « JAMBE GAGNÉE » → **rafale = spam** signalé par le user (capture
  2026-09-08). L'anti-doublon (`data/push_sent.json`, titre identique < 5 min) ne bloque que les titres
  IDENTIQUES, or chaque jambe a un titre distinct → il ne stoppait pas la rafale. `notify_leg`/`notify_combo`
  gardés mais court-circuités (`return 0`) → réactivables via `PUSH_LEGS_COMBOS=True`. Tier résultat via flag figé
  `_is_value`. Cartes **sans glose** (`.mc-gloss/.cleg-gloss/.sgl` → `display:none`). Mémoire `push-pwa-legs-combos-dedup`.
- **NOTIFS PAR MATCH (🔔, MAJ 2026-09-06)** : un bouton 🔔 par carte de match FOOT non terminée, **VISIBLE
  UNIQUEMENT en PWA** installée (classe `html.pwa` posée par JS `_BELL_JS` ; CSS `.mc-bell`). Abonnement
  **PAR MATCH, indépendant** du push global (`data/push_match_subs.json` `{mid:[endpoint]}`). La boucle
  `main._match_events_loop` poll le live ~45 s **seulement s'il y a des abonnés** (0 charge sinon) et
  `push.notify_match_events` détecte les transitions vs `data/push_match_state.json` : **début · but ·
  mi-temps · fin** (idempotent ; abos retirés à FT). Routes `/push/match/{subscribe,unsubscribe,list}`.
  ⚠️ **Live Activities iOS / Dynamic Island = natif only** (Swift/ActivityKit) → **hors de portée en PWA**
  (pas de Mac/App Store côté user, tranché 2026-09-06) ; le push PWA = bannières sur écran verrouillé, pas
  de carte persistante. Mémoire `push-pwa-per-match-notifications`.
- **APERÇU DU MATCH EN LIVE (« Match Center », MAJ 2026-09-11)** : sur chaque carte de match EN DIRECT,
  `_render_match_center` (stats API-Football via `_mc_stats`) est **VISIBLE D'OFFICE, SANS bouton** (user
  2026-09-11) : `_live_match_center_fold` rend un bloc `.mcx-live` + placeholder `.mcx[data-mcx-auto]`
  auto-chargé par `window._mcInit` (appelé après chaque swap SPA) + refresh global 30 s (plus de
  `<details>`/`<summary>`). **BARRE « QUI DOMINE LE MATCH »** en tête (`_mcx_domination_bar`) : agrège
  possession/tirs/cadrés/tirs surface/corners/xG(pondéré) en un indice de domination, barre 2 côtés +
  « \<équipe\> domine · N% ». **COULEURS PAR ÉQUIPE** (`_team_colors_pair`, hash md5 → teinte stable/distincte,
  S/L calés sur le thème sombre) sur la barre de domination ET les barres appariées (`.mcx-bh/.mcx-ba` via
  `--hc/--ac`, repli vert/bleu). Zéro dépendance/réseau/analyse de logo.

## ⚠️ 3 COUCHES à NE JAMAIS confondre (Affichage / Stats / Calibration) — juillet 2026

Après plusieurs allers-retours, la logique est figée. **Ne jamais les mélanger ni casser :**

1. **AFFICHAGE** (listes À venir / Terminés) = `analyses.list_for()`. On ne montre QUE ce sur quoi on
   mise : **combiné OU simple retenu**. Les **abstentions** (favori analysé mais SANS value → non retenu)
   sont **CACHÉES**. Mode par état : **à venir = publication** (avec exclusions, = Telegram) ·
   **terminé = for_history** (sans exclusions, = ce qui a été joué). `_sport_row`, `_result_badge`,
   `bets_html` s'alignent. **Confiance ≠ value** : un favori à cote courte (76 %@1.21) a une value
   NÉGATIVE → jamais affiché comme « à jouer ». Titres : « 📊 Le pari joué / à venir » / « Analyse du match ».

2. **STATS** (ROI / courbe / réussite) = `analyses.stat_bet(d)`, **FIGÉ** dans `d["stat_bet"]` au règlement
   (+ backfill). **Compteur MONOTONE : ne rebaisse JAMAIS.** ⛔ NE PAS revenir à un `retained_bet(for_history)`
   recalculé en direct dans `stats_full` → ça faisait valser le nombre (47↔59) et le ROI (biais du
   survivant). On ne fige QUE les comptés → on ne RETIRE jamais un pari.

3. **CALIBRATION** = `analyses.calibration()` lit **TOUTES** les prédictions (fantômes `d["shadow"]` +
   paris `d["bets"]`). **Indépendante** de l'affichage/du gel, **jamais filtrée**. Les abstentions la
   nourrissent via leurs fantômes.

**3 types de prédictions** : ⭐ **pari joué** (retenu → affiché + Telegram + ROI) · ⏸ **abstention**
(caché, PAS au ROI, mais réglé + calibré) · 👻 **fantôme** (10-14/match, calibration seule). Ne PAS
fusionner abstention et fantôme. **Vocabulaire UI : « pari joué » — plus d'étoile ⭐ ni de « retenu ».**
**Rien n'est jamais supprimé** (sidecars/.md/calibration intacts).

## Git
- Remote : `origin` = https://github.com/CronoBots/BETSFIX.git (branche `main`).
- **Politique (depuis 2026-07-05) : chaque commit descriptif est poussé sur `main` automatiquement**
  via le hook git local `post-commit` (`.git/hooks/post-commit` → `git push origin HEAD`, best-effort).
  Donc : faire un vrai commit = c'est poussé. Pas besoin de `git push` explicite.
- **L'auto-commit périodique « travail live » est COUPÉ pour les 4 projets** (demande user) via le flag
  `C:\Users\vince\.claude\.autocommit-off` : le script global `claude-autocommit.ps1` (boucle 180 s :
  `git add -u` + commit `auto: travail live …` + push, sur BETSFIX/CRYPTONAUTS/DIGITALCONCEPT.BE/TOUKIN)
  teste ce fichier à chaque cycle et **skippe tout** tant qu'il existe. Réactiver = supprimer le fichier.
- Filet BETSFIX : le hook local `commit-msg` rejette aussi tout commit `auto: travail live` (protège même
  si `.autocommit-off` est retiré un jour). Les hooks ne sont pas versionnés (locaux à cette copie) → à
  recréer si le repo est recloné.
- ⚠️ La note « aucun commit/push automatique » d'avant était FAUSSE (l'autocommit poussait en douce).
- **Politique appliquée aux 4 projets** (choix user : « push auto à chaque commit, pas de commit auto ») :
  hook `post-commit` posé sur BETSFIX, CRYPTONAUTS, DIGITALCONCEPT.BE. **TOUKIN** n'est pas un repo git
  (aucun push tant que `git init`+remote non faits). ⚠️ **CRYPTONAUTS** : local en retard sur `origin`
  → un push peut être rejeté jusqu'à `git pull` (à réconcilier à part).
