# ============================================================================
#  Automatisation COMPLÈTE (100 % sans login Windows) :
#    - le TUNNEL Cloudflare devient un service Windows (démarre au boot)
#    - l'API uvicorn devient une tâche planifiée SYSTEM (démarre au boot AUSSI,
#      sans ouverture de session, et se relance toute seule si elle plante)
#
#  Après ce script : tu rallumes/redémarres le PC et https://api.betsfix.com
#  remonte tout seul, même si PERSONNE ne se connecte à la session Windows.
#
#  PRÉREQUIS :
#    - Ouvrir PowerShell EN ADMINISTRATEUR (installation d'un service Windows).
#    - Avoir le token du tunnel (dashboard Zero Trust, ou
#      %USERPROFILE%\.cloudflared\api_token.txt).
#
#  USAGE :
#    powershell -ExecutionPolicy Bypass -File .\deploy\setup_full_service.ps1 -Token "eyJ...."
#    (le -Token est optionnel s'il existe déjà dans api_token.txt)
# ============================================================================

param(
  [string]$Token,
  [string]$PythonPath,
  [int]$Port = 8000
)
$ErrorActionPreference = "Stop"

# --- 0) Vérifie les droits administrateur ----------------------------------
$admin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  throw "Lance ce script dans un PowerShell ouvert EN ADMINISTRATEUR (clic droit > Exécuter en tant qu'administrateur)."
}

$cf   = "C:\Users\vince\cloudflared.exe"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# --- 1) Résout le token ----------------------------------------------------
$tokenFile = "$env:USERPROFILE\.cloudflared\api_token.txt"
if (-not $Token) {
  if (Test-Path $tokenFile) {
    $Token = (Get-Content $tokenFile -Raw).Trim()
    Write-Host "Token lu depuis $tokenFile" -ForegroundColor DarkGray
  } else {
    throw "Aucun -Token fourni et $tokenFile introuvable. Donne le token du dashboard."
  }
}

# --- 2) Résout le chemin de python.exe -------------------------------------
if (-not $PythonPath) {
  try { $PythonPath = (Get-Command python -ErrorAction Stop).Source } catch {}
  if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    $cand = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" `
            -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $PythonPath = $cand.FullName }
  }
}
if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
  throw "python.exe introuvable. Relance avec -PythonPath 'C:\chemin\vers\python.exe'."
}
Write-Host "Python : $PythonPath" -ForegroundColor Green

# --- 3) Tunnel Cloudflare en SERVICE Windows -------------------------------
Write-Host "`n[1/3] Installation du tunnel comme service Windows..." -ForegroundColor Cyan
try { & $cf service uninstall 2>$null } catch {}
& $cf service install $Token
Write-Host "Tunnel installé comme service 'Cloudflared' (démarrage auto au boot)." -ForegroundColor Green

# --- 4) Nettoie les anciennes tâches (évite les doublons de port 8000) -----
foreach ($t in @("API-SPORT-server", "API-SPORT-mobile", "API-SPORT-api", "BETSFIX-server", "BETSFIX-mobile")) {
  if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
    Write-Host "Ancienne tâche '$t' supprimée." -ForegroundColor DarkGray
  }
}

# --- 5) API uvicorn en tâche planifiée SYSTEM (au boot, sans session) ------
Write-Host "`n[2/3] Création de la tâche API (compte SYSTEM, au démarrage)..." -ForegroundColor Cyan
$loop = Join-Path $root "deploy\api_service_loop.ps1"
$arg  = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$loop`" -Python `"$PythonPath`" -Port $Port"

$action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $root
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "BETSFIX-api" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Tâche 'BETSFIX-api' créée (SYSTEM, AtStartup, auto-restart)." -ForegroundColor Green

# --- 6) Démarre tout maintenant (sans rebooter) ----------------------------
Write-Host "`n[3/3] Démarrage immédiat..." -ForegroundColor Cyan
# Libère le port 8000 si une instance manuelle traîne
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
  Write-Host "Le port $Port est déjà occupé : la tâche réutilisera l'instance existante." -ForegroundColor DarkYellow
}
Start-ScheduledTask -TaskName "BETSFIX-api"
Start-Sleep -Seconds 5

# --- 7) Vérifications ------------------------------------------------------
Write-Host "`n--- Vérifications ---" -ForegroundColor Cyan
Get-Service Cloudflared | Format-Table Status, Name, StartType -AutoSize
try {
  $h = Invoke-WebRequest "http://localhost:$Port/health" -UseBasicParsing -TimeoutSec 8
  Write-Host "API locale : OK ($($h.StatusCode))" -ForegroundColor Green
} catch {
  Write-Host "API locale pas encore prête (laisse-lui ~10 s, puis: curl http://localhost:$Port/health)" -ForegroundColor DarkYellow
}

Write-Host "`nTerminé. Redémarre le PC pour valider le 100 % sans login," -ForegroundColor Cyan
Write-Host "puis ouvre https://api.betsfix.com/docs sur ton mobile." -ForegroundColor Cyan
