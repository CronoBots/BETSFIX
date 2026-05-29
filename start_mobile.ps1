# Lance l'API + un tunnel Cloudflare pour y accéder depuis le mobile.
# Les requêtes (SofaScore, Unibet) partent de CE PC -> IP belge -> Unibet OK.
# Usage : clic droit > Exécuter avec PowerShell, ou : ./start_mobile.ps1
# Arrêt : ferme les deux fenêtres (ou Ctrl+C dans chacune).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cloudflared = "C:\Users\vince\cloudflared.exe"

# 1) Démarre l'API (uvicorn) dans une nouvelle fenêtre
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "cd '$here'; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)

Start-Sleep -Seconds 4

# 2) Démarre le tunnel : il affichera l'URL https://....trycloudflare.com
Write-Host "Le tunnel va afficher une URL https://....trycloudflare.com" -ForegroundColor Cyan
Write-Host "Ouvre cette URL + /docs sur ton mobile.`n" -ForegroundColor Cyan
& $cloudflared tunnel --url http://localhost:8000 --no-autoupdate
