# Finalise le tunnel Cloudflare via TOKEN (créé dans le dashboard Zero Trust).
# Pas de certificat à transférer : tout est dans le token.
#
# USAGE :  ./deploy/setup_token.ps1 -Token "eyJ....(le token du dashboard)...."
#
# Fait : installe le tunnel comme service Windows (démarrage auto au boot) et
# crée une tâche qui lance l'API (uvicorn) à l'ouverture de session.

param([Parameter(Mandatory = $true)][string]$Token)
$ErrorActionPreference = "Stop"
$cf = "C:\Users\vince\cloudflared.exe"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# 1) Tunnel en service Windows (démarre au boot, avant même la session)
try { & $cf service uninstall } catch {}
& $cf service install $Token
Write-Host "Tunnel installé comme service Windows." -ForegroundColor Green

# 2) API (uvicorn) lancée à chaque ouverture de session
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -Command `"cd '$root'; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "API-SPORT-server" -Action $action -Trigger $trigger `
  -Settings $settings -Force | Out-Null
Write-Host "Tâche 'API-SPORT-server' créée (API au démarrage de session)." -ForegroundColor Green

# 3) Démarre l'API tout de suite (sans attendre un redémarrage)
Start-Process -WindowStyle Hidden powershell -ArgumentList @(
  "-NoProfile", "-Command",
  "cd '$root'; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)
Write-Host "`nC'est prêt. URL fixe = celle configurée dans 'Public Hostname' du dashboard." -ForegroundColor Cyan
