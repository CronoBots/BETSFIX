# BETSFIX — scan quotidien automatique (tâche planifiée « BETSFIX Scan », compte vince).
# Lance l'analyste sur les 3 sports. SAUTE si un scan tourne déjà (anti-doublon — cf. le piège des
# 2 scans concurrents). Logue tout dans data/scan_cron.log. Le cache 6 h évite de regénérer l'inutile.
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
Set-Location $root

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Out-File -Append -Encoding utf8 $log
}

# Anti-doublon : si un generate_analyses tourne déjà (scan manuel ou passe précédente non finie),
# on ne lance PAS une 2e passe (deux scans concurrents = doublons de cartes).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

Log 'START scan foot,tennis,basket --top 3 --hours 24'
# 2>&1 | Out-File : capture FIABLE du stdout+stderr natif de python (le `*>>` ne récupérait pas la
# sortie sous tâche cachée). Out-File étant un cmdlet, $LASTEXITCODE reste celui de python.
& $py 'tools\generate_analyses.py' --sport foot,tennis,basket --top 3 --hours 24 2>&1 |
    Out-File -Append -Encoding utf8 $log
Log ("DONE (exit {0})" -f $LASTEXITCODE)
