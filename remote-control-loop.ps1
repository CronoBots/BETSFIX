# remote-control-loop.ps1
# BETSFIX -- keep-alive Remote Control (Windows / PowerShell).
# MODELE IDENTIQUE A PRONOSTICS (nexbet-keepalive.ps1) QUI FONCTIONNE.
#
# Maintient la session "BETSFIX" en vie : si claude s'arrete/plante (crash,
# timeout reseau >10min, veille...), on relance automatiquement.
# A executer DETACHE (-Detach) pour SURVIVRE a la fermeture de la fenetre
# PowerShell -- c'est CA qui faisait que rien ne "demarrait tout seul" avant :
# l'ancienne version tournait au premier plan et mourait des qu'on fermait la
# fenetre, et le .vbs cense la lancer cachee n'existait pas.
#
# Usage :
#   # Lancer en tache de fond (survit a la fermeture du terminal) :
#   .\remote-control-loop.ps1 -Detach
#
#   # Au premier plan (pour debug, Ctrl+C pour arreter) :
#   .\remote-control-loop.ps1
#
#   # Arreter le keep-alive + la session claude :
#   .\remote-control-loop.ps1 -Stop
#
#   # Forcer une session VIDE (sinon --continue reprend le dernier fil) :
#   .\remote-control-loop.ps1 -Detach -Fresh
#
# Pour qu'il redemarre AUSSI apres un REBOOT (bonus que PRONOSTICS n'a pas),
# installe le lanceur du dossier Demarrage :
#   .\deploy\enable_remote_control_startup.ps1

param(
    [string]$RepoDir = $PSScriptRoot,
    [string]$Name    = "BETSFIX",
    [switch]$SafePermissions,   # garder la validation (sinon auto-accept)
    [switch]$Fresh,             # demarrer une session vide (sinon --continue)
    [switch]$Detach,            # relancer ce script en fenetre cachee, puis rendre la main
    [switch]$Stop               # arreter le keep-alive + la session claude
)

$ErrorActionPreference = "SilentlyContinue"
$flagFile = Join-Path $env:LOCALAPPDATA "BETSFIX\keepalive.run"
New-Item -ItemType Directory -Force -Path (Split-Path $flagFile) | Out-Null

if ($Stop) {
    Remove-Item $flagFile -Force -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='claude.exe'" |
        Where-Object { $_.CommandLine -match 'remote-control' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "Keep-alive arrete et session Remote Control fermee." -ForegroundColor Yellow
    return
}

if ($Detach) {
    # Relance CE script SANS -Detach, en fenetre cachee, comme process
    # independant. Quand tu fermes ton PowerShell, ce process-la survit.
    $self = $MyInvocation.MyCommand.Path
    $a = @("-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",$self,
           "-RepoDir",$RepoDir,"-Name",$Name)
    if ($SafePermissions) { $a += "-SafePermissions" }
    if ($Fresh)           { $a += "-Fresh" }
    Start-Process -FilePath "powershell.exe" -ArgumentList $a -WindowStyle Hidden
    Write-Host "Keep-alive lance en tache de fond (fenetre cachee)." -ForegroundColor Green
    Write-Host "Tu peux FERMER ce PowerShell : la session 'BETSFIX' reste active." -ForegroundColor Green
    Write-Host "Pour arreter plus tard :  .\remote-control-loop.ps1 -Stop" -ForegroundColor Gray
    return
}

# ---- Boucle keep-alive (process detache) -------------------------------
Set-Content -Path $flagFile -Value "running" -Encoding ASCII
# PID de la boucle (pour un desinstallateur / le diagnostic).
Set-Content -Path (Join-Path $PSScriptRoot ".remote-control.pid") -Value $PID -Encoding ASCII
Set-Location $RepoDir
$permFlag = if ($SafePermissions) { @() } else { @("--dangerously-skip-permissions") }
# Par defaut on REPREND le dernier fil du depot (--continue) pour garder le
# contexte, EXACTEMENT comme PRONOSTICS. -Fresh force une session vide.
$contFlag = if ($Fresh) { @() } else { @("--continue") }

while (Test-Path $flagFile) {
    # Session interactive remote (auto-accept). Tant que le flag existe, on
    # relance si claude se termine (crash, timeout reseau >10min...).
    claude --remote-control $Name @contFlag @permFlag
    if (-not (Test-Path $flagFile)) { break }   # arret demande via -Stop
    Start-Sleep -Seconds 5
}
