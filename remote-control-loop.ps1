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

# --- GARDE-FOU ANTI-DOUBLON (singleton) ------------------------------------
# Piege vecu (2026-07-02) : un superviseur ORPHELIN (son parent Task Scheduler
# est mort) + son claude survivent a un precedent logon. La tache "BETSFIX
# Remote Control" (IgnoreNew) ne "voit" PAS cet orphelin -> au logon suivant
# elle relance une 2e instance, et DEUX "claude --remote-control BETSFIX" se
# disputent le meme nom de session => session invisible/instable cote claude.ai.
# Regle : au demarrage, CE superviseur devient le SEUL proprietaire du projet.
# Il tue tout AUTRE superviseur BETSFIX (hors lui-meme) et TOUT claude remote
# BETSFIX preexistant (le sien n'est pas encore lance) avant de continuer.
try {
    # NE PLUS tuer les autres loops ICI : si DEUX loops montent quasi en meme
    # temps (ex. orphelin d'un ancien logon + relance tache), chacune tuait le
    # claude de l'autre puis les deux SURVIVAIENT et se disputaient le nom pour
    # toujours (relais mobile jamais monte -> invisible, 2026-07-17). Le doublon
    # est desormais resolu EN CONTINU par le watchdog (regle "loop la plus
    # ancienne gagne", ordre total, sans course). Ici on tue seulement un claude
    # BETSFIX preexistant (le notre n'est pas encore lance).
    Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
        Where-Object { $_.CommandLine -match "remote-control $SessionName" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch { }
# ---------------------------------------------------------------------------

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

# --- Watchdog : auto-repare un claude FIGE (vivant mais 0 connexion Anthropic) ---
# Cas vecu le 2026-06-24 : au boot, claude peut rester bloque AVANT d'ouvrir la
# connexion remote (onboarding/race reseau). Resultat : invisible sur mobile, et
# la boucle ci-dessous ne le relance PAS car il ne se TERMINE pas (il reste fige).
# Le watchdog le tue apres ~40 s sans connexion etablie vers Anthropic -> la
# boucle le relance alors frais. Tourne dans un job separe, parametre par $SessionName.
$logFile = Join-Path $PSScriptRoot "remote-control-loop.log"
Get-Job -Name "wd-$SessionName" -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
Start-Job -Name "wd-$SessionName" -ArgumentList $SessionName, $logFile, $PID -ScriptBlock {
    param($session, $log, $myLoopPid)
    $strikes = 0
    while ($true) {
        Start-Sleep -Seconds 20

        # --- SINGLETON PERMANENT : la loop la plus ANCIENNE gagne -----------
        # Ordre total (StartTime puis PID) => convergence deterministe vers UNE
        # seule loop, sans course d'entre-tuerie. Si JE suis la loop en trop
        # (une autre est plus ancienne), je retire MON claude puis MOI-meme.
        $loops = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
                   Where-Object { $_.CommandLine -match "$session\\remote-control-loop" })
        if ($loops.Count -gt 1) {
            $mine = $loops | Where-Object { $_.ProcessId -eq $myLoopPid } | Select-Object -First 1
            if ($mine) {
                $older = $loops | Where-Object {
                    $_.ProcessId -ne $myLoopPid -and (
                        ($_.CreationDate -lt $mine.CreationDate) -or
                        ($_.CreationDate -eq $mine.CreationDate -and $_.ProcessId -lt $myLoopPid)
                    )
                }
                if ($older) {
                    "$(Get-Date -Format s) SINGLETON: loop $session en double (moi PID $myLoopPid, plus jeune) -> auto-retrait" | Out-File -FilePath $log -Append -Encoding utf8
                    Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
                        Where-Object { $_.CommandLine -match "remote-control $session" -and $_.ParentProcessId -eq $myLoopPid } |
                        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
                    Stop-Process -Id $myLoopPid -Force -ErrorAction SilentlyContinue
                    return
                }
            }
        }

        $p = Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
             Where-Object { $_.CommandLine -match "remote-control $session" } |
             Select-Object -First 1
        if (-not $p) { $strikes = 0; continue }
        $conns = @(Get-NetTCPConnection -OwningProcess $p.ProcessId -State Established -ErrorAction SilentlyContinue |
                   Where-Object { $_.RemoteAddress -notin '127.0.0.1','::1' })
        # Sante = au moins UNE connexion ET pas une "tempete de retry".
        # Tempete = >=15 connexions TOUTES vers une seule IP : le canal API
        # boucle en reconnexion mais le relais mobile (2e IP) n'est jamais monte
        # -> session invisible sur mobile (2026-07-17). Une session saine a le
        # relais => >=2 IP distinctes ; une session idle a peu de connexions.
        $distinct = @($conns | Select-Object -ExpandProperty RemoteAddress -Unique).Count
        $storm = ($conns.Count -ge 15 -and $distinct -le 1)
        if ($conns.Count -gt 0 -and -not $storm) { $strikes = 0; continue }
        $strikes++
        if ($strikes -ge 9) {
            $why = if ($storm) { "tempete retry ($($conns.Count) conn / 1 IP, relais absent)" } else { "0 connexion ~180s" }
            "$(Get-Date -Format s) WATCHDOG: claude $session malsain ($why, PID $($p.ProcessId)) -> kill" | Out-File -FilePath $log -Append -Encoding utf8
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $strikes = 0
        }
    }
} | Out-Null

while ($true) {
    $start = Get-Date
    try {
        # On appelle claude DIRECTEMENT (operateur &), pas via Start-Process
        # -WindowStyle Hidden. claude a besoin d'heriter de la console (cachee)
        # de ce PowerShell pour le mode remote-control -- comme NEXBET. Avec
        # Start-Process detache, claude perd sa console et ressort aussitot.
        #
        # Session FRAICHE (pas de --continue) : comme CRYPTONAUTS. Le flag
        # --continue reprenait une conversation LOCALE et laissait la session
        # remote se figer (0 connexion Anthropic -> kills watchdog en boucle).
        & $claude @argsFresh
    } catch {
        # crash/timeout reseau : on relance apres une courte pause
    }
    Start-Sleep -Seconds 3
}
