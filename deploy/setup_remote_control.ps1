# ============================================================================
#  Fait REDEMARRER AUTOMATIQUEMENT la session Claude "Remote Control" apres un
#  reboot, pour que tu puisses la repiloter depuis ton mobile.
#
#  Comment ca marche : cree une tache planifiee "API-SPORT-remote" declenchee a
#  l'ouverture de session, qui lance run_remote_control.ps1 (lequel maintient
#  'claude --remote-control' en vie en boucle).
#
#  PREREQUIS (obligatoire, une fois) :
#    1. Claude Code installe :  npm install -g @anthropic-ai/claude-code
#    2. Avoir lance UNE FOIS a la main, dans une vraie fenetre, pour te
#       connecter a ton compte et appairer le mobile :
#           claude --remote-control "API-SPORT"
#
#  ACTIVER :  powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1
#  ANNULER :  powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1 -Disable
#  (aucun droit administrateur requis)
# ============================================================================

param([switch]$Disable, [string]$Name = "API-SPORT")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$task = "API-SPORT-remote"

if ($Disable) {
  if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false
    Write-Host "Auto-demarrage Remote Control DESACTIVE." -ForegroundColor Yellow
  } else {
    Write-Host "Aucun auto-demarrage trouve, rien a faire." -ForegroundColor DarkGray
  }
  return
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  throw "Commande 'claude' introuvable. Installe Claude Code : npm install -g @anthropic-ai/claude-code"
}

$loop = Join-Path $root "deploy\run_remote_control.ps1"
if (-not (Test-Path $loop)) { throw "run_remote_control.ps1 introuvable (fais un git pull)." }

$arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$loop`" -Name `"$Name`""

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "OK : la session Remote Control '$Name' se relancera a chaque OUVERTURE DE SESSION." -ForegroundColor Green
Write-Host "Journal : deploy\remote_control.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "NOTE - reboot 100% sans personne devant le PC :" -ForegroundColor Cyan
Write-Host "  La tache se declenche a l'ouverture de session. Si le PC reboote la nuit" -ForegroundColor Gray
Write-Host "  sans que tu ouvres ta session, Claude ne se relancera pas tant que personne" -ForegroundColor Gray
Write-Host "  ne se connecte. Pour un reboot vraiment autonome, active l'ouverture de" -ForegroundColor Gray
Write-Host "  session automatique de Windows (commande 'netplwiz'), en connaissance des" -ForegroundColor Gray
Write-Host "  implications de securite." -ForegroundColor Gray
Write-Host ""
Write-Host "Pour annuler : .\deploy\setup_remote_control.ps1 -Disable" -ForegroundColor DarkGray
