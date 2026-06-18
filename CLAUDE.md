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
- Voisins similaires sur la même machine : `PRONOSTICS`, `CRYPTONAUTS`
  (chacun a sa tâche `<NOM> Remote Control`).

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
| **SofaScore** | ex-source principale | ❌ **MORTE** |

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
  - 🔴 **BANNIR** : tirs TOTAUX (0/2), cartons (57 %), **corners PAR ÉQUIPE + handicaps corners**
    (le marché le plus perdant, ~58 % ; coupable dans 5/13 combinés perdus), premier but / mi-temps
    (non réglables).
  - 🟢 **PRIVILÉGIER** : résultat / double chance (83 %), tirs **cadrés** (83 %), buts total / équipe
    marque (79 %), **corners TOTAUX over** (≠ par équipe), corners 1ère MT (75 %).
  - Gravé dans `tools/generate_analyses.py` (COMBO_MISSION) ; cf. mémoire `combo-construction-rules`.

## Git
- Remote : `origin` = https://github.com/CronoBots/BETSFIX.git (branche `main`).
- Aucun push/commit automatique : rien dans les scripts ne fait de `git`,
  aucun hook actif. Pousser reste une action manuelle/explicite.
