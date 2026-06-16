# remote-control-loop.ps1
# Maintient Claude Remote Control actif en permanence POUR CE PROJET (BETSFIX).
# Lance par la TACHE PLANIFIEE "BETSFIX Remote Control" (User=vince, LogonTrigger),
# (re)creee par deploy\setup_remote_control.ps1. PAS par le dossier Demarrage :
# il n'y a aucun .vbs remote dans shell:startup, le seul vbs la-bas est l'API.
# Si la session s'arrete (timeout reseau, veille, crash...), elle est relancee.
#
# Le PID de la boucle est ecrit dans .remote-control.pid pour qu'un
# desinstallateur puisse arreter UNIQUEMENT ce projet.
# (Methode identique a CRYPTONAUTS / NEXBET : pas d'admin, pas de mot de passe.)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

# --- Nom de session : UNIQUE a ce projet (evite la confusion avec d'autres) ---
$SessionName = "BETSFIX"

# --- Localiser claude.exe ---
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Claude\claude.exe"
    if (Test-Path $candidate) { $claude = $candidate }
}
if (-not $claude) { $claude = "claude" }

$pidFile = Join-Path $PSScriptRoot ".remote-control.pid"

# But : la session remote doit REPRENDRE automatiquement la derniere
# conversation BETSFIX a chaque boot (et non repartir de zero).
#   --remote-control <name> : etablit la session distante VISIBLE sur claude.ai/code
#   --continue              : reprend la derniere conversation de CE dossier
# Les deux flags se CUMULENT : la session distante reste visible ET reprend le fil.
# (L'ancien probleme "aucune session visible" venait d'un --continue utilise
#  SANS --remote-control ; tant que --remote-control est present, c'est bon.)
# Repli : si --continue ressort en <30 s (rien a reprendre, ex. 1er boot apres
#  purge de l'historique), on relance une session FRAICHE -> voir la boucle.
$argsResume = @("--remote-control", $SessionName, "--continue", "--dangerously-skip-permissions")
$argsFresh  = @("--remote-control", $SessionName, "--dangerously-skip-permissions")

# PID de la boucle
Set-Content -Path $pidFile -Value $PID -Encoding ASCII

while ($true) {
    $start = Get-Date
    try {
        # On appelle claude DIRECTEMENT (operateur &), pas via Start-Process
        # -WindowStyle Hidden. claude a besoin d'heriter de la console (cachee)
        # de ce PowerShell pour le mode remote-control -- comme NEXBET. Avec
        # Start-Process detache, claude perd sa console et ressort aussitot.
        & $claude @argsResume
    } catch {
        # crash/timeout reseau : on relance apres une courte pause
    }
    # Si --continue ressort tout de suite (<30 s), c'est qu'il n'y avait aucune
    # conversation a reprendre : on bascule sur une session FRAICHE pour ne pas
    # boucler dans le vide, puis on continue normalement.
    if (((Get-Date) - $start).TotalSeconds -lt 30) {
        try { & $claude @argsFresh } catch { }
    }
    Start-Sleep -Seconds 10
}
