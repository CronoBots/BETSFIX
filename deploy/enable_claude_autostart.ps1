# ============================================================================
#  Fait s'ouvrir Claude Code AUTOMATIQUEMENT à chaque ouverture de session.
#
#  Claude étant interactif (il ouvre une fenêtre et attend tes consignes), il a
#  besoin de ta SESSION Windows ouverte : il ne peut donc PAS démarrer "sans
#  login" comme le tunnel/API. Ce script place un raccourci vers claude.bat dans
#  le dossier Démarrage de Windows -> Claude s'ouvre dès que tu te connectes.
#
#  ACTIVER  :  powershell -ExecutionPolicy Bypass -File .\deploy\enable_claude_autostart.ps1
#  ANNULER  :  powershell -ExecutionPolicy Bypass -File .\deploy\enable_claude_autostart.ps1 -Disable
#  (aucun droit administrateur requis)
# ============================================================================

param([switch]$Disable)
$ErrorActionPreference = "Stop"

$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bat     = Join-Path $root "claude.bat"
$startup = [Environment]::GetFolderPath("Startup")
$lnk     = Join-Path $startup "Claude-API-SPORT.lnk"

if ($Disable) {
  if (Test-Path $lnk) {
    Remove-Item $lnk
    Write-Host "Auto-démarrage de Claude DÉSACTIVÉ (raccourci supprimé)." -ForegroundColor Yellow
  } else {
    Write-Host "Aucun auto-démarrage trouvé, rien à faire." -ForegroundColor DarkGray
  }
  return
}

if (-not (Test-Path $bat)) { throw "claude.bat introuvable dans $root (fais un git pull)." }

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $bat
$sc.WorkingDirectory = $root
$sc.WindowStyle      = 1
$sc.Description       = "Ouvre Claude Code dans le projet API-SPORT"
$sc.Save()

Write-Host "OK : Claude s'ouvrira à CHAQUE ouverture de session Windows." -ForegroundColor Green
Write-Host "Raccourci : $lnk" -ForegroundColor DarkGray
Write-Host "Pour annuler : .\deploy\enable_claude_autostart.ps1 -Disable" -ForegroundColor DarkGray
