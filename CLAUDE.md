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

## Git
- Remote : `origin` = https://github.com/CronoBots/BETSFIX.git (branche `main`).
- Aucun push/commit automatique : rien dans les scripts ne fait de `git`,
  aucun hook actif. Pousser reste une action manuelle/explicite.
