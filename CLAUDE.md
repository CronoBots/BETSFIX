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
- **Confiance** = `app/confidence_pick.py` — DC/Handicap, conf ≥80, cote 1.05–1.50,
  le + sûr (~94 % / +7,7 %). Plafond cote **1.50** (`3abb5f0`).
- **Value** = `app/value_pick.py` — conf **≥68** (relevé de 58, MAJ 2026-09-01), cote 1.40–2.30, EV ≥ +5 %,
  cote la + haute, sur matchs **SANS** confiance. Marchés sauf bans **+ « Total Over » exclu** (`_VALUE_BAN_MARKETS`
  = seul marché value perdant : 56 %/−9 %). Perf ré-alignée ~87 % / +35 %. Optim via backtest fantômes 1/match +
  train/test anti-surapprentissage (mémoire `value-exclude-total-over`). **Historique RE-PIQUÉ** sous ce modèle
  (2026-09-01, pas d'abonnés) : 46→23 value (23 retirés = abstentions), stat_bet re-figé EXPLICITEMENT (pas via
  `retained_bet`/`stat_bet` qui ressuscitent le pari publié) + filigrane monotone remis à 0.
- Verrous dans `app/analyses.py` : `FOOT_MECHANICAL_ONLY=True` (le foot ne prend QUE
  le pari mécanique) · `REVEAL_ONLY_FINAL=True` (voir flux Option B).
- **Montante = RÉACTIVÉE AUTO** (2026-09-01, refonte) — `app/montante.py`. Sélection MÉCANIQUE = moteur
  Confiance borné : `pick_confidence_day` pioche dans le vivier fantômes complet (familles Vainqueur/DC/Total
  équipe), **VRAIE cote Unibet (omap) bornée 1.25-1.55**, confiance ≥80, le + sûr ; **PASS si rien** (survie).
  Capital **composé** (arrondi centime/palier), amorcé à la série réelle du user (**42,53 € / 7-0**, relancée
  23/08). Auto-réglée (`settle_pending`, marché propre). **HORS overall/hero** (`MONTANTE_ROI_ON=False` : unité
  composée ≠ ROI mise-plate → sa propre carte). **Publiée sur le site**, mais **Telegram OFF** (`TG_COMBO_MONTANTE=
  False`, broadcast en attente du feu vert user). Mémoire `montante-reactivated-confidence-auto`.

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
  slate jour + **vérif/pré-chauffe des LOGOS** + analyse **cachée** (Option B) + **combiné du jour** + planif des vagues.
- **~19h — scan NUIT** (`deploy/scan_evening.ps1` → `--ko-from 21 --ko-to 6`) : sélection slate nuit +
  fusion + **vérif/pré-chauffe des LOGOS** + analyse + **combiné du soir** + replanif des vagues.
- **LOGOS** (`tools/logo_check.py --quiet --alert`, 2026-09-02) : résout le blason des 2 équipes de chaque
  match du programme → **pré-chauffe `crest_cache.json`** (logo prêt à la publication), **vérifie l'URL en 200**,
  **auto-répare** via les fixtures FotMob du jour (ancrage sur l'adversaire reconnu + KO — indispensable quand
  les libellés n'ont aucun token commun : « Saint-Trond » ↔ « St.Truiden »), **alerte privée** si un logo manque.
- **KO−1h — vagues** (`deploy/scan_wave.ps1` → `--refresh-early`) : re-analyse chaque match ~1h avant SON
  coup d'envoi, **PUBLIE** le pari (app + Telegram), puis reconcile (règlement + résultats combinés par jambe).
  Cap **7+7** (jour+nuit). Mémoire `daily-construction-methodology` (flux de référence + invariants anti-bug).

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
  **Reste à faire** : câbler Stripe aux tiers ; héberger hors PC. **Plateforme comptes = Supabase** (pas Firebase,
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
- **Telegram** (MAJ 2026-09-01) : publie **Confiance + Value + combinés**. Value posté comme la confiance
  (carte + résultat « VALUE GAGNÉE @cote ✅ / PERDUE ❌ », label via flag figé `_is_value`). Un résultat simple
  n'est posté QU'en réponse à un prono réel (`get_prono`) — jamais d'orphelin. Cf. `telegram-foot-simple-only`.
- **Push PWA** (MAJ 2026-09-02) : notifie **simples + JAMBES + COMBINÉS** (`app/push.py` : `notify_leg`/
  `notify_combo`, libellés alignés Telegram), won/lost seulement. Garde **anti-doublon** (titre identique < 5 min,
  `data/push_sent.json`). Tier résultat via flag figé `_is_value`. Cartes **sans glose** (site + Telegram,
  `.mc-gloss/.cleg-gloss/.sgl` → `display:none`). Mémoire `push-pwa-legs-combos-dedup`.

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
