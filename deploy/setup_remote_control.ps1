# ============================================================================
#  (Re)cree la tache planifiee "BETSFIX Remote Control" qui lance et MAINTIENT
#  la session Claude Remote Control de ce projet a chaque ouverture de session.
#
#  C'est la SOURCE DE VERITE du demarrage auto du remote control : si la tache
#  est perdue ou cassee, relance ce script pour la reconstruire a l'identique.
#
#  Mecanisme :
#    Tache (User=vince, InteractiveToken, LogonTrigger)
#      -> powershell -File remote-control-loop.ps1   (fenetre cachee)
#         -> claude --remote-control BETSFIX --dangerously-skip-permissions
#
#  Robustesse :
#    - LogonTrigger          : demarre a l'ouverture de session vince.
#    - Repetition 10 min     : FILET DE SECURITE. Si la boucle meurt entre deux
#                              logons (veille, kill...), la tache la relance.
#    - MultipleInstances=IgnoreNew : la repetition ne cree PAS de doublon tant
#                              qu'une instance tourne deja (1 seule session).
#    - RestartOnFailure 999x/1min : relance si l'action sort en erreur.
#    - AllowStartIfOnBatteries : ne pas bloquer sur PC portable.
#
#  USAGE (aucun droit admin requis, c'est une tache en compte vince) :
#    powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1
#    powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1 -Restart
#      (-Restart : recree ET redemarre la session maintenant, sinon ne touche
#       pas a une session deja en cours)
# ============================================================================

param([switch]$Restart)
$ErrorActionPreference = "Stop"

$TaskName = "BETSFIX Remote Control"
$root     = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$loop     = Join-Path $root "remote-control-loop.ps1"

if (-not (Test-Path $loop)) { throw "Introuvable : $loop (fais un git pull)." }

# --- Droits admin requis ----------------------------------------------------
# La tache est dans le dossier racine du planificateur : sa creation/modif
# exige une elevation (sinon "Acces refuse"). Cf. piege documente dans CLAUDE.md.
$admin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  throw "Lance ce script dans un PowerShell EN ADMINISTRATEUR (clic droit > Executer en tant qu'administrateur)."
}

# --- Action : la boucle superviseur, en fenetre cachee ---------------------
$arg    = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$loop`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg

# --- Trigger : a l'ouverture de session ------------------------------------
$trigger = New-ScheduledTaskTrigger -AtLogOn

# --- Principal : compte vince, session interactive -------------------------
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
  -LogonType Interactive -RunLevel Limited

# --- Settings : auto-relance, pas de doublon, jamais de timeout ------------
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null

# --- Repetition filet de securite (toutes les 10 min, INDEFINIMENT) ---------
# On l'applique APRES coup : une duree vide = "indefiniment". (TimeSpan::MaxValue
# est hors limites du XML Windows et fait echouer Register/Set.) Avec IgnoreNew,
# si la boucle meurt entre deux logons (veille, kill), elle repart sans doublon.
$t = Get-ScheduledTask -TaskName $TaskName
$t.Triggers[0].Repetition.Interval = 'PT10M'
$t.Triggers[0].Repetition.Duration = ''
$t.Triggers[0].Repetition.StopAtDurationEnd = $false
Set-ScheduledTask -InputObject $t | Out-Null
Write-Host "OK : tache '$TaskName' (re)creee (LogonTrigger + repetition 10 min)." -ForegroundColor Green

# --- Demarrage immediat -----------------------------------------------------
$running = Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
  Where-Object { $_.CommandLine -match 'remote-control BETSFIX' }

if ($Restart -or -not $running) {
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "Session remote control demarree." -ForegroundColor Green
} else {
  Write-Host "Une session BETSFIX tourne deja (PID $($running.ProcessId)) : laissee intacte." -ForegroundColor DarkGray
  Write-Host "Utilise -Restart pour forcer une session fraiche." -ForegroundColor DarkGray
}

Write-Host "`nVerif :" -ForegroundColor Cyan
Get-ScheduledTask -TaskName $TaskName | Format-Table TaskName,State -Auto
