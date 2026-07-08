# BETSFIX — RE-VÉRIFICATION « ~1 h avant chaque match » (tâche « BETSFIX Scan Wave », compte vince).
# Système HYBRIDE (choix user 2026-07-08) : les picks sont DÉJÀ publiés le matin (scan_daily). Ici on
# RE-ANALYSE chaque match du programme ~1-1.5 h avant SON coup d'envoi (--refresh-early : un match publié
# le matin = analysé « trop tôt » -> ré-analysé quand il approche) et on ne REPUBLIE QUE SI LE PRONO A
# CHANGÉ (cotes/compos/blessures). Si inchangé : rien n'est reposté (le pick du matin reste). Tourne
# FRÉQUEMMENT (~toutes les 30 min, sur :10 et :40 — JAMAIS :00, cf. collision avec le scan 09h).
# Version LÉGÈRE : scan + règlement SILENCIEUX (--no-bilan : poste les résultats, pas de récap à chaque
# passage) + selfcheck. Les gros calculs quotidiens (programme, scan matin, méthodo, revue, backtest,
# apprentissage, santé sources, bilan) restent dans scan_daily.ps1 (1×/jour, matin).
param([double]$WindowHours = 1.5)

$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
Set-Location $root

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Out-File -Append -Encoding utf8 $log
}

# Anti-doublon : si un scan (vague OU complet) tourne déjà, on NE lance PAS une 2e passe concurrente.
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("WAVE SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

Log ("WAVE START scan foot,tennis,basket --hours {0} --from-programme --refresh-early" -f $WindowHours)
& $py 'tools\generate_analyses.py' --sport foot,tennis,basket --top 3 --hours $WindowHours --from-programme --refresh-early 2>&1 |
    Out-File -Append -Encoding utf8 $log
Log ("WAVE SCAN DONE (exit {0})" -f $LASTEXITCODE)

# RÉCONCILIATION : règle tout ce qui est réglable (poste les résultats peu après la fin des matchs),
# re-poste les pronos imminents dont l'envoi a été manqué, et envoie un BILAN Telegram. Passages
# fréquents -> résultats postés VITE (fini le « posté 3 jours après »).
Log 'WAVE RECONCILE : règlement SILENCIEUX (résultats postés, pas de bilan)'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("WAVE RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'WAVE SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("WAVE SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)
