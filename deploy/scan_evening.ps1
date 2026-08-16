# BETSFIX — SCAN DU SOIR = SLATE NUIT (tâche planifiée « BETSFIX Scan Soir », compte vince).
# 2 créneaux (user 2026-08-07) : le matin (scan_daily) analyse le SLATE JOUR (coup d'envoi 6h→21h, heure
# belge : Europe + Asie-après-midi) ; CE scan analyse le SLATE NUIT (coup d'envoi 21h→06h : Amériques —
# MLS/Brésil/Argentine/Liga MX — + Europe tardive) avec des données du JOUR MÊME, au lieu des ~12-20 h de
# retard d'un scan unique du matin. Partition par HEURE DE COUP D'ENVOI -> chaque match tombe dans UN seul
# slate, AUCUNE région n'est re-scannée (le filtre --ko-from/--ko-to exclut le slate jour d'ici).
#
# Utilise le PROGRAMME déjà écrit le matin (data/day_programme.json, journée complète 06h→06h) -> ne
# reconstruit PAS la liste, ne re-poste PAS le programme. Analyse + PUBLIE uniquement les picks de nuit,
# puis règle (résultats postés vite) + auto-audit. Les gros calculs quotidiens (méthodo/revue/backtest/
# débrief/santé sources) restent dans scan_daily.ps1 (1×/jour, matin). Pas de ré-analyse : le pick du soir
# est DÉFINITIF (comme celui du matin).
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
$flag = Join-Path $root 'data\scan_wave_first.flag'   # présent = mode WAVE-FIRST (le sweep analyse la nuit)
Set-Location $root
. (Join-Path $root 'deploy\_log.ps1')   # log concurrent-safe (Add-BfxStream / Write-BfxLogLine)

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-BfxStream $log
}

# Anti-doublon : si un generate_analyses tourne déjà (scan matin non fini, ou double déclenchement), on ne
# lance PAS une 2e passe concurrente (deux scans = doublons de cartes).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("SOIR SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# SLATE NUIT : coup d'envoi 21h→06h (heure belge). --hours 12 couvre 19h -> 07h (tout le slate nuit depuis
# ~19h). --from-programme = uniquement la liste du matin (aucune dérive de sélection). PAS de --force : les
# matchs de nuit n'ont pas encore été analysés (le matin ne fait que le slate jour) -> analyse normale ;
# un match de jour (coup d'envoi < 21h) est EXCLU par la bande -> jamais re-scané ici.
# SCAN SOIR (analyse du slate NUIT en batch) : SAUTÉ en mode WAVE-FIRST (user 2026-08-11) -> le sweep
# analyse chaque match de nuit ~2h avant SON coup d'envoi. Sans le drapeau -> comportement batch inchangé.
if (Test-Path $flag) {
    Log 'SCAN SOIR : SAUTÉ (mode WAVE-FIRST) -> analyse par le sweep ~2h avant chaque coup d''envoi'
} else {
    Log 'SCAN SOIR : SLATE NUIT (coup d''envoi 21h->06h heure belge) + publication des picks'
    & $py 'tools\generate_analyses.py' --sport foot --top 20 --hours 12 --from-programme --ko-from 21 --ko-to 6 2>&1 |
        Add-BfxStream $log
    Log ("SCAN SOIR DONE (exit {0})" -f $LASTEXITCODE)
}

# RÉCONCILIATION : règle ce qui est réglable (poste les résultats des matchs de l'après-midi/soirée finis),
# re-poste les pronos imminents manqués. Silencieux (pas de bilan — le bilan reste 1×/jour le matin).
Log 'SOIR RECONCILE : règlement SILENCIEUX'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Add-BfxStream $log
Log ("SOIR RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'SOIR SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Add-BfxStream $log
Log ("SOIR SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)
