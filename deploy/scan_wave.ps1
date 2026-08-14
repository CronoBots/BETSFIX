# BETSFIX — PASSE « ~1 h avant chaque match » (tâche « BETSFIX Scan Wave », compte vince).
# RÔLE :
#  (1) ANALYSE les matchs IMMINENTS PAS ENCORE analysés (1re analyse ~1 h avant leur coup d'envoi) ->
#      FILET FIABLE pour le SLATE NUIT : si le scan du soir (scan_evening, batch unique à 19 h) RATE (cas
#      2026-08-08 : tourné mais rien analysé), chaque match de nuit a quand même SA propre vague qui l'analyse.
#      ⚠️ Ce n'est PAS de la ré-analyse : le garde-fou code `_generated_today` (generate_analyses) SAUTE tout
#      match DÉJÀ analysé aujourd'hui (matin OU soir) -> le pick reste DÉFINITIF, AUCUN flip. Seuls les
#      matchs NEUFS (jamais analysés) sont traités.
#  (2) RÈGLEMENT silencieux des matchs finis (--no-bilan) + auto-audit.
# ⏰ DÉCLENCHEMENT PAR MATCH : scan_daily.ps1 pose chaque matin, via deploy/schedule_reana.ps1, UN
# déclencheur ponctuel à (coup d'envoi − 1 h) par match.
param([double]$WindowHours = 1.5)

$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
Set-Location $root
. (Join-Path $root 'deploy\_log.ps1')   # log concurrent-safe (Add-BfxStream / Write-BfxLogLine)

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-BfxStream $log
}

# Anti-course : si le SCAN MATIN (generate_analyses) tourne encore, on ne règle PAS en parallèle (évite de
# régler un sidecar en cours de réécriture). Le règlement repassera au prochain déclencheur / boucle API.
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("WAVE SKIP : le scan matin tourne encore (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# ANALYSE des matchs imminents NON encore analysés (1re analyse -> filet nuit). Le garde-fou `_generated_today`
# saute tout match déjà analysé aujourd'hui : donc AUCUNE ré-analyse / AUCUN flip du pick du matin.
# ⚠️ Point décimal « . » obligatoire (locale FR -> "1,5" rejeté par argparse) -> InvariantCulture.
$hours = $WindowHours.ToString([System.Globalization.CultureInfo]::InvariantCulture)
Log ("WAVE ANALYSE : matchs imminents NON analysés (1re analyse, --refresh-early, jamais de ré-analyse)")
& $py 'tools\generate_analyses.py' --sport foot --top 8 --hours $hours --from-programme --refresh-early 2>&1 |
    Add-BfxStream $log
Log ("WAVE ANALYSE DONE (exit {0})" -f $LASTEXITCODE)

# RÉCONCILIATION : règle tout ce qui est réglable (poste les résultats peu après la fin des matchs),
# re-poste les pronos imminents dont l'envoi a été manqué, et envoie un BILAN Telegram. Passages
# fréquents -> résultats postés VITE (fini le « posté 3 jours après »).
Log 'WAVE RECONCILE : règlement SILENCIEUX (résultats postés, pas de bilan)'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Add-BfxStream $log
Log ("WAVE RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'WAVE SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Add-BfxStream $log
Log ("WAVE SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)
