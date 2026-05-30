# ============================================================================
#  Fait demarrer Claude "Remote Control" AU BOOT, SANS ouverture de session.
#  (comme un service : pas besoin de login Windows, donc compatible compte
#   Microsoft + PIN, et plus aucun profil fantome.)
#
#  Cree une tache planifiee "API-SPORT-remote" qui s'execute "que tu sois
#  connecte ou non", au demarrage du PC, et qui maintient
#  'claude --remote-control' en vie en boucle (run_remote_control.ps1).
#
#  PREREQUIS (obligatoire, une fois) :
#    1. Claude Code installe :  npm install -g @anthropic-ai/claude-code
#    2. Avoir lance UNE FOIS a la main, dans une vraie fenetre, pour te
#       connecter a ton compte et appairer le mobile :
#           claude --remote-control "API-SPORT"
#    3. Connaitre ton MOT DE PASSE de compte Microsoft (celui de l'email,
#       PAS le code PIN). Si tu ne le connais plus : account.microsoft.com
#
#  ACTIVER (PowerShell EN ADMINISTRATEUR) :
#       powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1
#  ANNULER :
#       powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1 -Disable
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

Write-Host ""
Write-Host "La tache doit memoriser tes identifiants Windows pour demarrer SANS" -ForegroundColor Cyan
Write-Host "ouverture de session. Saisis ton MOT DE PASSE de COMPTE MICROSOFT" -ForegroundColor Cyan
Write-Host "(celui de l'email) -- PAS le code PIN." -ForegroundColor Cyan
Write-Host ""

$defaultUser = "$env:COMPUTERNAME\$env:USERNAME"
$cred = Get-Credential -UserName $defaultUser -Message "Mot de passe du compte Microsoft (PAS le PIN)"
$plain = $cred.GetNetworkCredential().Password

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT60S"   # laisse le reseau + le tunnel monter d'abord
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

try {
  Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings `
    -User $cred.UserName -Password $plain -RunLevel Highest -Force | Out-Null
} catch {
  Write-Host ""
  Write-Host "ECHEC de l'enregistrement avec ce mot de passe." -ForegroundColor Red
  Write-Host "Verifie que c'est bien le mot de passe du COMPTE MICROSOFT (pas le PIN)." -ForegroundColor Red
  Write-Host "Detail: $($_.Exception.Message)" -ForegroundColor DarkGray
  throw
}

Write-Host ""
Write-Host "OK : Claude Remote Control '$Name' demarrera AU BOOT, sans ouverture de session." -ForegroundColor Green
Write-Host "Journal : deploy\remote_control.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "ETAPE SUIVANTE IMPORTANTE :" -ForegroundColor Cyan
Write-Host "  Desactive maintenant l'ouverture de session automatique cassee, qui creait" -ForegroundColor Gray
Write-Host "  le profil fantome :" -ForegroundColor Gray
Write-Host "      powershell -ExecutionPolicy Bypass -File .\deploy\disable_autologin.ps1" -ForegroundColor White
Write-Host ""
Write-Host "Pour annuler cette tache : .\deploy\setup_remote_control.ps1 -Disable" -ForegroundColor DarkGray
