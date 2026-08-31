# BETSFIX — Notes projet

## Carte du démarrage automatique (Windows)

Au démarrage du PC, trois briques remontent. **Important : deux d'entre elles
tournent en compte SYSTEM et sont INVISIBLES depuis une session non-admin**
(voir le piège plus bas).

| Composant | Démarre | Sans login ? | Mécanisme |
|---|---|---|---|
| Tunnel Cloudflare | au boot | ✅ oui | Service Windows `Cloudflared` (StartType=Automatic) |
| API uvicorn `:8000` | au boot | ✅ oui | Tâche planifiée `BETSFIX-api` (User=SYSTEM, BootTrigger, auto-relance) → lance `deploy/api_service_loop.ps1` → `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` |
| Session Remote Control | à l'ouverture de session | ❌ (login requis) | Tâche planifiée `BETSFIX Remote Control` (User=vince, LogonTrigger) → lance `remote-control-loop.ps1` → `claude --remote-control BETSFIX --dangerously-skip-permissions` |

- URL publique mobile : `https://api.betsfix.com` (le tunnel pointe sur `127.0.0.1:8000`).
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
- **Confiance** = `app/confidence_pick.py` — DC/Handicap, conf ≥80, cote 1.05–1.50,
  le + sûr (~94 % / +7,7 %). Plafond cote **1.50** (`3abb5f0`).
- **Value** = `app/value_pick.py` — tous marchés sauf bans, conf ≥58, cote 1.40–2.30,
  EV ≥ +5 %, cote la + haute, sur matchs **SANS** confiance (~72 % / +15,9 %).
- Verrous dans `app/analyses.py` : `FOOT_MECHANICAL_ONLY=True` (le foot ne prend QUE
  le pari mécanique) · `REVEAL_ONLY_FINAL=True` (voir flux Option B).
- **Montante = DÉSACTIVÉE** (`2c2f85e`, refonte à venir) — rien dans la catégorie.

### ⚠️ VERDICT / COTE / ANALYSE d'affichage = le pari JOUÉ, JAMAIS le pick brut (MAJ 2026-08-31)
Depuis la refonte mécanique, le pari joué (`stat_bet`/mécanique) **DIVERGE** du pick brut de Claude
(`d["pick"]` / tableau `.md`, qui note un AUTRE marché). Tout ce qui s'AFFICHE doit suivre le pari joué :
- **Source unique** = `analyses.played_result(d)` (combiné → `stat_bet` → repli `pick_result`). Vaut pour le
  bandeau, le chip « Terminés », le bord coloré, la **carte résultat** (site + Telegram), et la **cote publiée**.
- L'**analyse « Pourquoi ce pari »** suit aussi le pari joué : `played_why` (sonnet) généré dédié au scan
  (sinon l'affichage montrait le raisonnement du mauvais pari).
- Garde-fou selfcheck `_check_verdict_reads_played_bet`. Bugs récurrents (mémoires `result-verdict-follows-played-bet`,
  `session-2026-08-31-omap-combos-telegram`). ⛔ Ne JAMAIS lire `pick_result`/`d["pick"]`/`bets_of` pour un affichage.

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
- **PUBLIÉS sur Telegram** (`combo_daily.notify_combos("foot")`, appelé par `reconcile.py` après `settle_all`) :
  (1) CARTE IMAGE du combiné, (2) « JAMBE GAGNÉE @cote ✅ / PERDUE ❌ » par jambe dès qu'elle est réglée,
  (3) « COMBINÉ DU JOUR/SOIR GAGNÉ @cote ✅ / PERDU ❌ » global. Idempotent (flags `tg_msg`/`leg.tg_done`/
  `tg_result_done`), anti-spam `COMBO_TG_FROM`. **Alerte privée owner** (`_owner_alert_once`) si aucun combiné.
- **BANS DURS gravés** (`COMBO_MISSION`, taux par jambe mesuré 2026-06-18) : 🔴 **TOUS les corners**, tirs
  TOTAUX, cartons, premier but / mi-temps. 🟢 privilégier résultat / double chance (83 %), tirs **cadrés**
  (83 %), buts total / équipe marque (79 %).
- **Intitulé double chance uniforme** : « **\<équipe\> ou nul (1X)** » partout via `analyses.pretty_sel`.
- Mémoires : `two-combos-jour-soir`, `session-2026-08-31-omap-combos-telegram`, `telegram-foot-simple-only`.

## Timeline quotidienne (heure Europe/Brussels)
- **~10h — scan JOUR** (`deploy/scan_daily.ps1` → `generate_analyses --ko-from 6 --ko-to 21`) : sélection
  slate jour + analyse **cachée** (Option B) + **combiné du jour** + planif des vagues.
- **~19h — scan NUIT** (`deploy/scan_evening.ps1` → `--ko-from 21 --ko-to 6`) : sélection slate nuit +
  fusion + analyse + **combiné du soir** + replanif des vagues.
- **KO−1h — vagues** (`deploy/scan_wave.ps1` → `--refresh-early`) : re-analyse chaque match ~1h avant SON
  coup d'envoi, **PUBLIE** le pari (app + Telegram), puis reconcile (règlement + résultats combinés par jambe).
  Cap **7+7** (jour+nuit). Mémoire `daily-construction-methodology` (flux de référence + invariants anti-bug).

## Autres sous-systèmes
- **Auth / abonnement** : base users **SQLite** `app/userdb.py` (migration JSON→SQLite auto), API
  `app/accounts.py` (login anti brute-force, reset mdp `/forgot`+`/reset`, vérif email `/verify`),
  `app/mailer.py` (SMTP env `BETSFIX_SMTP_*`, repli `data/outbox`). Tiers free / trial(3j auto) / monthly.
  **Reste à faire** : câbler Stripe aux tiers, choisir un SMTP, héberger hors PC. Mémoire `auth-subscription-scale-foundation`.
- **Filet de survie site** : Cloudflare Worker `betsfix-failover` devant `api.betsfix.com` sert un snapshot
  **KV** si le PC est down. `deploy/snapshot_to_kv.py` (tâche `BETSFIX-KV-Snapshot`, 30 min) + `deploy/worker/`.
  Jeton en env user `BETSFIX_KV_*`. Mémoire `cloudflare-kv-snapshot-failover`.
- **selfcheck** = `app/selfcheck.py` : ~30 garde-fous d'intégrité (compteur monotone, cohérence affichage↔stats,
  combinés, omap, **verdict = pari joué**…), lancé après chaque scan/reconcile.
  Vérif : `python -c "from app import selfcheck; print(selfcheck.run()['counts'])"` → doit être `error:0`.
- **Monitoring** : `/monitor` (`tools/monitor.py`) — forward réel vs backfill, calibration, maturité des marchés.
- **Perf accueil** : caches `_HMR_CACHE` (rows), `fragcache` (fragment jour), `_ODDS_CACHE` (cotes live 25 s,
  stale-while-revalidate, **non-bloquant au cold start** depuis `4f6013c`), snapshot `data/_stats_snapshot.json`.
- **UI** : calendrier stats = **taux de réussite** (jour/mois, plus le ROI) ; « Programme du jour » **fermé**
  dès qu'un pari existe dans une catégorie ; intitulé DC « \<équipe\> ou nul (1X) ».

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
