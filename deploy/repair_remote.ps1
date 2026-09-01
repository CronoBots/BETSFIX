# ============================================================================
#  repair_remote.ps1  --  REPARE la session Remote Control "BETSFIX" en 1 clic.
#
#  Complement de deploy\diagnose_remote.ps1 (qui, lui, ne MODIFIE rien) :
#  ici on agit. AUCUN DROIT ADMIN REQUIS (repli direct si la tache planifiee
#  n'est pas joignable depuis une session non elevee -- cf. piege CLAUDE.md :
#  "Acces refuse" != "introuvable").
#
#  Ce qu'il fait, dans l'ordre :
#    1. Verifie claude.exe + les identifiants claude.ai (panne n.1 : /login).
#    2. Nettoie TOUT (loops orphelines + claude remote BETSFIX) -> etat vierge,
#       plus de doublon qui se dispute le nom de session.
#    3. Relance via la tache planifiee "BETSFIX Remote Control" si possible,
#       sinon lance directement remote-control-loop.ps1 (fenetre cachee).
#    4. ATTEND et VERIFIE : exactement 1 loop + 1 claude, connexion Anthropic
#       etablie. Dit clairement si c'est bon ou ce qui bloque encore.
#
#  USAGE :  double-clic sur reparer_remote.bat  (a la racine du projet)
#     ou :  powershell -ExecutionPolicy Bypass -File .\deploy\repair_remote.ps1
# ============================================================================

$ErrorActionPreference = "Continue"
$SessionName = "BETSFIX"
$TaskName    = "BETSFIX Remote Control"
$root        = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$loop        = Join-Path $root "remote-control-loop.ps1"

function Section($n) { Write-Host "`n=== $n ===" -ForegroundColor Cyan }
function OK($m)      { Write-Host "  [OK]   $m" -ForegroundColor Green }
function WARN($m)    { Write-Host "  [!]    $m" -ForegroundColor Yellow }
function BAD($m)     { Write-Host "  [X]    $m" -ForegroundColor Red }
function INFO($m)    { Write-Host "  ->     $m" -ForegroundColor DarkGray }

function Get-RemoteClaude {
    @(Get-CimInstance Win32_Process -Filter "Name='claude.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -match "remote-control $SessionName" })
}
function Get-Loops {
    @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -match "$SessionName\\remote-control-loop" })
}

Write-Host "REPARATION REMOTE CONTROL -- $SessionName" -ForegroundColor White
Write-Host ("PC: {0}   User: {1}   {2}" -f $env:COMPUTERNAME, $env:USERNAME, (Get-Date))

if (-not (Test-Path $loop)) {
    BAD "Introuvable : $loop"
    INFO "Fais un 'git pull' dans le dossier du projet, puis relance."
    exit 1
}

# --- 1. Prerequis : claude installe + compte logge --------------------------
Section "1. Prerequis"
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Claude\claude.exe"
    if (Test-Path $candidate) { $claude = $candidate }
}
if ($claude) { OK "claude trouve : $claude" }
else {
    BAD "commande 'claude' INTROUVABLE -> rien ne pourra demarrer."
    INFO "Installe-la : npm install -g @anthropic-ai/claude-code   (Node 18+)"
    exit 1
}

$cred = @(
    (Join-Path $env:USERPROFILE ".claude\.credentials.json"),
    (Join-Path $env:USERPROFILE ".claude.json")
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($cred) { OK "identifiants claude.ai presents ($cred)" }
else {
    BAD "aucun identifiant claude.ai : la session ne peut PAS s'ouvrir."
    INFO "Lance 'claude', tape /login, puis relance cette reparation."
    exit 1
}

# --- 2. Nettoyage : on repart d'un etat vierge ------------------------------
Section "2. Nettoyage (anti-doublon)"
$before = @(Get-Loops) + @(Get-RemoteClaude)
if ($before.Count -eq 0) { INFO "rien ne tournait (session bien coupee)." }
foreach ($p in $before) {
    INFO ("kill PID {0} ({1})" -f $p.ProcessId, $p.Name)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
OK "etat vierge : plus aucune loop/claude $SessionName."

# --- 3. Relance -------------------------------------------------------------
Section "3. Relance"
$started = $false
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    OK "tache planifiee '$TaskName' demarree (etat: $($task.State))."
    # la tache met quelques secondes a lancer la loop : on lui laisse sa chance
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 2
        if ((Get-Loops).Count -gt 0) { $started = $true; break }
    }
    if (-not $started) { WARN "la tache n'a produit aucune loop en 20 s -> repli direct." }
} catch {
    WARN "tache planifiee non joignable depuis cette session ($($_.Exception.Message.Split([char]10)[0]))."
    INFO "Normal sans elevation, ou tache absente. On lance directement la boucle."
}

if (-not $started) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File","`"$loop`"") `
        -WindowStyle Hidden
    OK "boucle lancee directement : $loop"
    INFO "Filet de secours seulement : pour l'autostart au logon, lance UNE FOIS"
    INFO "  deploy\setup_remote_control.ps1 dans un PowerShell EN ADMINISTRATEUR."
}

# --- 4. Verification --------------------------------------------------------
Section "4. Verification (jusqu'a 60 s)"
$claudeProc = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $procs = Get-RemoteClaude
    if ($procs.Count -ge 1) { $claudeProc = $procs[0]; break }
}
if (-not $claudeProc) {
    BAD "aucune session claude --remote-control $SessionName apres 60 s."
    INFO "Regarde le journal : remote-control-loop.log (a la racine du projet)."
    INFO "Puis lance le diagnostic complet : diagnose_remote.bat"
    exit 1
}
OK ("session claude demarree (PID {0})." -f $claudeProc.ProcessId)

$conn = $false
for ($i = 0; $i -lt 20; $i++) {
    $c = @(Get-NetTCPConnection -OwningProcess $claudeProc.ProcessId -State Established -ErrorAction SilentlyContinue |
           Where-Object { $_.RemoteAddress -notin '127.0.0.1','::1' })
    if ($c.Count -gt 0) { $conn = $true; break }
    Start-Sleep -Seconds 2
}
if ($conn) { OK "connexion Anthropic etablie -> la session doit apparaitre sur claude.ai/code." }
else {
    WARN "pas encore de connexion Anthropic apres 40 s."
    INFO "Le watchdog de la boucle la relancera automatiquement si elle reste figee."
    INFO "Verifie ta connexion internet, puis reessaie."
}

$loops = Get-Loops
if ($loops.Count -eq 1) { OK "exactement 1 boucle superviseur (PID $($loops[0].ProcessId))." }
elseif ($loops.Count -gt 1) {
    WARN "$($loops.Count) boucles detectees -- le singleton du watchdog va en garder UNE (la plus ancienne)."
}

Write-Host "`nTermine. Ouvre l'app Claude > onglet Code : la session '$SessionName' doit etre la." -ForegroundColor White
