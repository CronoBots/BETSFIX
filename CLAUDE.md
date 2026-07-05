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

## Sources de données & analyse (état réel — 2026-06-17)

### Sources par rôle (toutes vérifiées vivantes, sauf SofaScore)
| Source | Rôle | Statut |
|---|---|---|
| **Unibet** | cotes + marchés + **sélection** des matchs (les 3 sports) | ✅ |
| **Pinnacle** | ancre « sharp » (proba de référence, faible marge) | ✅ |
| **FotMob** | foot : forme / blessés / H2H / météo | ✅ |
| **ESPN** | tennis (classement + forme) · basket (bilans + blessés WNBA/NBA) | ✅ |
| **Understat** | foot : xG (top-5 ligues) | ✅ |
| **Flashscore** | foot/tennis/basket : forme + H2H + service + compos | ✅ |
| **LiveScore** | scores **live** (onglet radar) + **règlement** des paris | ✅ |
| **Sportradar (GISMO)** | foot/tennis/basket : forme · **streaks de pari** (sans défaite/marque/BTTS/over) · H2H · classement · **moyennes buts-points & over 2.5** — feed LIBRE `lsc.fn.sportradar.com` (locale FR), `app/sportradar.py` branché à `sources.extras` + routeur `/sportradar/*` (dans `/docs`) · **+ RÈGLEMENT (v44)** : `sportradar.final_score()` lit `match_info.periods` → jeux/sets/tie-breaks tennis & quart-temps basket (repli `need_periods` dans settle_analyst) | ✅ |
| **SofaScore** | ex-source principale (Sportradar GISMO = le vrai upstream, le remplace en partie) | ❌ **MORTE** |

### ⚠️ SofaScore est MORT — NE PAS re-diagnostiquer à chaque fois
- `app/sofa_http`, `_sofa_extras`, `_resolve_sofa`, `tools/build_*elo` appellent
  ENCORE SofaScore → renvoient 0 / 403, **gérés sans planter** (vestige).
- Donc : `/{sport}/match/{id}/streaks|h2h|statistics` renvoient `{}` pour les 3 sports,
  et le scan logue « id SofaScore introuvable → repli id Unibet ». **C'est normal.**
- NE PAS conclure « tennis/basket cassés » : la **sélection (Unibet)** et
  l'**enrichissement (multi-sources)** marchent pour les 3 sports.
- Déjà acté : **Elo tennis RETIRÉ** (commit 4ee2d45) ; les builds Elo/tendances/
  serve-return collectent 0 → **garde-fou anti-écrasement** (ba61e1b). **Ne PAS
  relancer ces builds en espérant un résultat.** (cf. mémoire `build-sofascore-dead`.)

### L'enrichissement vivant = `app/sources.py`
- `sources.extras(client, sport, match)` → FotMob/ESPN/Understat + Flashscore,
  **branché au scan** (`tools/generate_analyses.py:674`), pour les **3 sports**.
- Tennis : le circuit ATP/WTA est **DÉDUIT** en cherchant les joueurs dans les 2
  classements ESPN (le champ `circuit` ex-SofaScore est vide) — fix commit b60710d.
  Avant ce fix, les matchs WTA n'avaient AUCUNE donnée ESPN (chute sur ATP par défaut).

### Le scan = `tools/generate_analyses.py`
- Pilote Claude headless (`claude -p`), **confidence-first**, faits web ≥2 sources.
  **DOIT** tourner en session `vince` (authentifiée) + **réseau requis**
  (lancer avec sandbox désactivé).
- Usage : `python tools/generate_analyses.py --sport foot,tennis,basket --top 3 --hours 24`
- Les **3 sports sont scannables**. Un `getaddrinfo failed` ponctuel = hoquet réseau
  transitoire (pas structurel) → relancer.
- Méthodo combinés : privilégier la « domination corrélée » (jambes qui tombent ensemble).
  **Taux de réussite par jambe mesuré 2026-06-18 (53 jambes réglées, 14 combinés)** :
  - 🔴 **BANNIR** : **TOUS les corners** (total/équipe/handicap/1ère MT — le marché le plus perdant,
    coupable dans 5/13 combinés perdus ; banni TOTALEMENT le 2026-06-19 sur demande user), tirs TOTAUX
    (0/2), cartons (57 %), premier but / mi-temps (non réglables).
  - 🟢 **PRIVILÉGIER** : résultat / double chance (83 %), tirs **cadrés** (83 %), buts total / équipe
    marque (79 %).
  - Gravé dans `tools/generate_analyses.py` (COMBO_MISSION) ; cf. mémoire `combo-construction-rules`.

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
- Note : les 3 autres projets n'ont plus de push auto non plus (à committer/pousser à la main, ou leur
  ajouter le même hook `post-commit`).
