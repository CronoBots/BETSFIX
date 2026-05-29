# Configuration unique du tunnel Cloudflare NOMMÉ (URL fixe) + démarrage auto.
#
# PRÉREQUIS (à faire AVANT, une seule fois) :
#   1. Avoir un domaine actif sur Cloudflare.
#   2. Lancer :  C:\Users\vince\cloudflared.exe tunnel login
#      (autorise dans le navigateur et choisis ton domaine)
#
# USAGE :
#   ./deploy/cloudflared_setup.ps1 -Hostname "api.tondomaine.com"
#
# Ce script : crée le tunnel, route le DNS, écrit la config, et installe une
# tâche planifiée qui lance l'API + le tunnel à chaque ouverture de session.

param(
  [Parameter(Mandatory = $true)][string]$Hostname,
  [string]$TunnelName = "api-sport"
)
$ErrorActionPreference = "Stop"
$cf = "C:\Users\vince\cloudflared.exe"
$cfgdir = "$env:USERPROFILE\.cloudflared"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path "$cfgdir\cert.pem")) {
  throw "Authentification manquante. Lance d'abord : $cf tunnel login"
}

# 1) Crée le tunnel (ignore l'erreur s'il existe déjà)
try { & $cf tunnel create $TunnelName } catch { Write-Host "Tunnel déjà existant, on continue." }

# 2) Récupère l'UUID du tunnel
$uuid = (& $cf tunnel list --output json | ConvertFrom-Json |
         Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1).id
if (-not $uuid) { throw "Impossible de retrouver l'UUID du tunnel $TunnelName" }
Write-Host "Tunnel: $TunnelName ($uuid)" -ForegroundColor Green

# 3) Route le DNS (crée l'enregistrement CNAME vers le tunnel)
& $cf tunnel route dns $TunnelName $Hostname

# 4) Écrit la config du tunnel
$config = @"
tunnel: $uuid
credentials-file: $cfgdir\$uuid.json
ingress:
  - hostname: $Hostname
    service: http://localhost:8000
  - service: http_status:404
"@
Set-Content -Path "$cfgdir\config.yml" -Value $config -Encoding ascii
Write-Host "Config écrite: $cfgdir\config.yml" -ForegroundColor Green

# 5) Tâche planifiée : démarre API + tunnel à l'ouverture de session
$launcher = Join-Path $root "deploy\run_server_tunnel.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "API-SPORT-mobile" -Action $action -Trigger $trigger `
  -Settings $settings -Force | Out-Null
Write-Host "Tâche planifiée 'API-SPORT-mobile' créée (démarrage auto)." -ForegroundColor Green

Write-Host "`nTerminé. Ton API sera accessible sur : https://$Hostname/docs" -ForegroundColor Cyan
Write-Host "Démarre-la maintenant sans rebooter : ./deploy/run_server_tunnel.ps1" -ForegroundColor Cyan
