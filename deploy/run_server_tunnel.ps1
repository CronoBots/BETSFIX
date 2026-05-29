# Lance l'API (uvicorn) + le tunnel Cloudflare NOMMÉ (URL fixe).
# Appelé automatiquement au démarrage de session par la tâche planifiée
# créée par cloudflared_setup.ps1. Peut aussi se lancer à la main.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$cloudflared = "C:\Users\vince\cloudflared.exe"

# 1) API en arrière-plan
Start-Process -WindowStyle Hidden powershell -ArgumentList @(
  "-NoProfile", "-Command",
  "cd '$root'; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)

Start-Sleep -Seconds 4

# 2) Tunnel nommé (lit ~/.cloudflared/config.yml -> hostname fixe)
& $cloudflared tunnel run
