# Maintient une session Claude Code "Remote Control" sur CE PC, pilotable
# depuis le mobile (app Claude / claude.ai/code). Si la session se termine
# (deconnexion, crash...), elle est relancee automatiquement apres 5 s.
#
# IMPORTANT - la 1re fois, lance-la A LA MAIN dans une vraie fenetre pour te
# connecter a ton compte Anthropic et appairer le mobile (QR / lien) :
#     cd C:\Users\vince\API-SPORT
#     claude --remote-control "API-SPORT"
# Une fois appaire, l'auto-demarrage (setup_remote_control.ps1) la relancera seul.

param([string]$Name = "API-SPORT")
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$log = Join-Path $root "deploy\remote_control.log"
function Write-Log($m) {
  Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -ErrorAction SilentlyContinue
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Log "ERREUR: commande 'claude' introuvable. Installe : npm install -g @anthropic-ai/claude-code"
  return
}

Write-Log "Superviseur Remote Control demarre (name=$Name, dir=$root)"
while ($true) {
  Write-Log "Lancement de claude --remote-control '$Name'"
  try {
    claude --remote-control $Name
  } catch {
    Write-Log "Exception: $($_.Exception.Message)"
  }
  Write-Log "Session terminee. Relance dans 5 s."
  Start-Sleep -Seconds 5
}
