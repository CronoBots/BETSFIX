# ============================================================================
#  Installe le DEMARRAGE AUTOMATIQUE du Remote Control pour CE projet (BETSFIX).
#
#  Methode (identique a CRYPTONAUTS / NEXBET, sans admin ni mot de passe) :
#    - genere un lanceur VBS (claude-remote-control-betsfix.vbs) qui demarre
#      remote-control-loop.ps1 en FENETRE CACHEE,
#    - depose ce VBS dans le dossier Demarrage de Windows -> il se relance a
#      CHAQUE ouverture de session.
#
#  La boucle (remote-control-loop.ps1) relance claude s'il tombe ; le VBS dans
#  le Demarrage garantit, lui, que la boucle elle-meme revient apres un reboot.
#
#  ACTIVER  :  powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1
#  ANNULER  :  powershell -ExecutionPolicy Bypass -File .\deploy\setup_remote_control.ps1 -Disable
#  (aucun droit administrateur requis)
# ============================================================================

param([switch]$Disable, [switch]$NoStart)
$ErrorActionPreference = "Stop"

$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$loop    = Join-Path $root "remote-control-loop.ps1"
$startup = [Environment]::GetFolderPath("Startup")
$vbs     = Join-Path $startup "claude-remote-control-betsfix.vbs"

if ($Disable) {
  if (Test-Path $vbs) {
    Remove-Item $vbs
    Write-Host "Auto-demarrage du Remote Control DESACTIVE (lanceur supprime)." -ForegroundColor Yellow
  } else {
    Write-Host "Aucun lanceur trouve, rien a faire." -ForegroundColor DarkGray
  }
  return
}

if (-not (Test-Path $loop)) { throw "remote-control-loop.ps1 introuvable dans $root (fais un git pull)." }

# --- Genere le VBS (chemins absolus, fenetre cachee = style 0, sans attente) ---
$content = @"
' claude-remote-control-betsfix.vbs  (genere par setup_remote_control.ps1)
' Lance remote-control-loop.ps1 en fenetre CACHEE au demarrage de la session.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$root"
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$loop""", 0, False
"@
Set-Content -Path $vbs -Value $content -Encoding ASCII

Write-Host "OK : le Remote Control demarrera a CHAQUE ouverture de session Windows." -ForegroundColor Green
Write-Host "Lanceur : $vbs" -ForegroundColor DarkGray
Write-Host "Pour annuler : .\deploy\setup_remote_control.ps1 -Disable" -ForegroundColor DarkGray

# --- Demarre tout de suite (sans attendre un reboot) -----------------------
if (-not $NoStart) {
  Write-Host "`nDemarrage immediat..." -ForegroundColor Cyan
  & "$env:SystemRoot\System32\wscript.exe" $vbs
  Start-Sleep -Seconds 2
  Write-Host "Boucle lancee en arriere-plan (fenetre cachee)." -ForegroundColor Green
}
