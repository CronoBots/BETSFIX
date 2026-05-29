# Lanceur permanent : démarre l'API (uvicorn) + le tunnel Cloudflare nommé.
# Exécuté automatiquement par la tâche planifiée "API-SPORT-mobile" à chaque
# ouverture de session. Aucun droit admin requis.
#
# URL fixe publique : https://api.betsfix.com  (configurée dans le dashboard)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$cloudflared = "C:\Users\vince\cloudflared.exe"
$token = (Get-Content "$env:USERPROFILE\.cloudflared\api_token.txt" -Raw).Trim()

# 1) API en arrière-plan (si pas déjà lancée sur le port 8000)
$running = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $running) {
  Start-Process -WindowStyle Hidden powershell -ArgumentList @(
    "-NoProfile", "-Command",
    "cd '$root'; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
  )
  Start-Sleep -Seconds 4
}

# 2) Tunnel nommé (URL fixe via le token)
& $cloudflared tunnel run --token $token
