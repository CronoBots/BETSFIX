# BETSFIX — SWEEP « analyse ~1 h avant le coup d'envoi » (tâche « BETSFIX Scan Sweep », compte vince).
#
# MODE WAVE-FIRST (user 2026-08-11) : au lieu d'analyser tout le slate en batch le matin (un match de 20h
# analysé à 10h = 10 h de retard, sans compos), CHAQUE match est analysé ~1 h avant SON coup d'envoi (user
# 2026-08-18 ; était ~2 h), avec des cotes/compos mûres. UNE seule analyse (garde-fou code `_generated_today`
# -> match déjà analysé sauté => AUCUN flip du pick). Le matin ne fait plus que SÉLECTIONNER (écrire le programme).
#
# RÔLE de ce sweep (toutes les ~30 min) :
#   1) ANALYSE les matchs du PROGRAMME dont le coup d'envoi tombe dans les 1,5 h à venir et qui ne sont pas
#      encore analysés (1re et seule analyse). Fenêtre 1,5 h + grille 30 min => chaque match est pris ~1 h
#      avant son coup d'envoi, avec 2-5 tentatives de secours avant le coup d'envoi (robuste aux ratés).
#   2) RÈGLEMENT silencieux des matchs finis + auto-audit.
#
# RÉVERSIBLE : ne fait RIEN tant que le drapeau data\scan_wave_first.flag n'existe pas (installer la tâche
# est donc inoffensif). L'activation (création du drapeau) se fait via deploy\setup_wave_first.ps1 (admin),
# qui n'arme le drapeau qu'APRÈS avoir posé cette tâche -> jamais « matin sans analyse ET sweep absent ».

param([double]$WindowHours = 1.5)   # ~1 h avant le coup d'envoi (user 2026-08-18 ; sweep toutes les 30 min -> analyse 1 h→1,5 h avant). Était 2.5.

$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
$flag = Join-Path $root 'data\scan_wave_first.flag'
Set-Location $root
. (Join-Path $root 'deploy\_log.ps1')   # log concurrent-safe (Add-BfxStream / Write-BfxLogLine)

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-BfxStream $log
}

# RÉVERSIBILITÉ : mode wave-first désactivé -> sweep no-op (le batch matin/soir analyse comme avant).
if (-not (Test-Path $flag)) { exit 0 }

# Anti-course : si un generate_analyses tourne déjà (scan matin/soir ou sweep précédent non fini), on ne
# lance PAS une 2e passe concurrente (deux scans = doublons de cartes / sidecar réécrit en parallèle).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("SWEEP SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# ANALYSE des matchs du programme dont le coup d'envoi est dans les $WindowHours à venir et NON encore
# analysés (1re et seule analyse -> pick définitif, jamais de flip via _generated_today). --from-programme =
# uniquement la sélection du matin (aucune dérive). Pas de bande --ko-from/--ko-to : jour ET nuit couverts.
# --top 24 : ne jamais écrêter un match du programme présent dans la fenêtre (une fenêtre 1,5 h a peu de matchs).
# ⚠️ Point décimal « . » obligatoire (locale FR -> "2,5" rejeté par argparse) -> InvariantCulture.
$hours = $WindowHours.ToString([System.Globalization.CultureInfo]::InvariantCulture)
Log ("SWEEP ANALYSE : matchs du programme à coup d'envoi <= {0} h, non analysés (1re analyse)" -f $hours)
& $py 'tools\generate_analyses.py' --sport foot --top 24 --hours $hours --from-programme 2>&1 |
    Add-BfxStream $log
Log ("SWEEP ANALYSE DONE (exit {0})" -f $LASTEXITCODE)

# RÉCONCILIATION : règle ce qui est réglable (poste les résultats vite), re-poste les pronos imminents
# manqués. Silencieux (le bilan Telegram reste 1x/jour, le matin).
Log 'SWEEP RECONCILE : règlement SILENCIEUX'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Add-BfxStream $log
Log ("SWEEP RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'SWEEP SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Add-BfxStream $log
Log ("SWEEP SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)
