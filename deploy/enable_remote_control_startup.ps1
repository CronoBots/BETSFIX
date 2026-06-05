# ============================================================================
#  Fait demarrer Claude "Remote Control" (session BETSFIX) AUTOMATIQUEMENT
#  a chaque ouverture de ta session Windows -- SANS droits admin, SANS mot
#  de passe (methode identique a CRYPTONAUTS / NEXBET).
#
#  C'EST LE CHAINON MANQUANT : remote-control-loop.ps1 dit qu'il est "lance
#  par le dossier Demarrage (claude-remote-control-betsfix.vbs)", mais ce
#  fichier .vbs n'existait nulle part et rien ne le creait. Du coup, au boot,
#  RIEN ne lancait la boucle -> la remote control ne demarrait jamais seule.
#  Ce script cree enfin ce .vbs dans le dossier Demarrage.
#
#  Le .vbs lance PowerShell en FENETRE CACHEE sur remote-control-loop.ps1,
#  qui maintient 'claude --remote-control BETSFIX' en vie en boucle.
#
#  PREREQUIS (une seule fois) :
#    1. Claude Code installe :  npm install -g @anthropic-ai/claude-code
#    2. T'etre connecte UNE FOIS a la main pour appairer le mobile :
#           claude --remote-control "BETSFIX"
#
#  ACTIVER :
#       powershell -ExecutionPolicy Bypass -File .\deploy\enable_remote_control_startup.ps1
#  ANNULER :
#       powershell -ExecutionPolicy Bypass -File .\deploy\enable_remote_control_startup.ps1 -Disable
# ============================================================================

param([switch]$Disable)
$ErrorActionPreference = "Stop"

$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$loop    = Join-Path $root "remote-control-loop.ps1"
$startup = [Environment]::GetFolderPath("Startup")
$vbs     = Join-Path $startup "claude-remote-control-betsfix.vbs"

if ($Disable) {
  if (Test-Path $vbs) {
    Remove-Item $vbs -Force
    Write-Host "Auto-demarrage Remote Control DESACTIVE (lanceur supprime)." -ForegroundColor Yellow
  } else {
    Write-Host "Aucun auto-demarrage trouve, rien a faire." -ForegroundColor DarkGray
  }
  return
}

if (-not (Test-Path $loop)) { throw "remote-control-loop.ps1 introuvable dans $root (fais un git pull)." }

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Host "ATTENTION : commande 'claude' introuvable." -ForegroundColor Red
  Write-Host "Installe-la d'abord : npm install -g @anthropic-ai/claude-code" -ForegroundColor Red
  Write-Host "(Le lanceur sera quand meme cree, mais il faut claude pour qu'il marche.)" -ForegroundColor DarkGray
}

# Le .vbs lance PowerShell en fenetre CACHEE (0 = hidden, pas de flash de
# console). remote-control-loop.ps1 herite alors de cette console cachee, ce
# dont 'claude --remote-control' a besoin pour rester en vie.
$vbsContent = @"
' Auto-genere par enable_remote_control_startup.ps1 -- NE PAS EDITER A LA MAIN.
' Lance la boucle Claude Remote Control (session BETSFIX) en fenetre cachee
' a chaque ouverture de session Windows.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$loop""", 0, False
"@

Set-Content -Path $vbs -Value $vbsContent -Encoding ASCII

Write-Host ""
Write-Host "OK : Claude Remote Control 'BETSFIX' demarrera a CHAQUE ouverture de" -ForegroundColor Green
Write-Host "session Windows, en arriere-plan (fenetre cachee)." -ForegroundColor Green
Write-Host "Lanceur cree : $vbs" -ForegroundColor DarkGray
Write-Host ""
Write-Host "POUR TESTER MAINTENANT sans rebooter, double-clique le .vbs ci-dessus" -ForegroundColor Cyan
Write-Host "(ou attends ta prochaine ouverture de session)." -ForegroundColor Cyan
Write-Host ""
Write-Host "Rappel : connecte-toi UNE FOIS a la main pour appairer le mobile :" -ForegroundColor Gray
Write-Host "    claude --remote-control ""BETSFIX""" -ForegroundColor White
Write-Host ""
Write-Host "Pour annuler : .\deploy\enable_remote_control_startup.ps1 -Disable" -ForegroundColor DarkGray
