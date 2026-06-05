# ============================================================================
#  diagnose_remote.ps1  --  Pourquoi la session "BETSFIX" n'apparait-elle pas
#  sur le mobile (app Claude > onglet Code) ?
#
#  Execute toute la checklist de depannage d'un coup, SANS rien modifier :
#    1. claude installe ?
#    2. compte claude.ai logge sur ce PC ? (et lequel)
#    3. UNE SEULE session --remote-control BETSFIX tourne ? (0 = morte, 2+ = doublon)
#    4. doublon de tache planifiee ? (le tueur de session n.1)
#    5. etat de la tache officielle "BETSFIX Remote Control"
#    6. anti-veille (standby) actif ?
#
#  USAGE (aucun droit admin requis ; double-clic possible via un .bat, ou) :
#    powershell -ExecutionPolicy Bypass -File .\deploy\diagnose_remote.ps1
#
#  Voir aussi : deploy\setup_remote_control.ps1 (la SOURCE DE VERITE de l'autostart).
# ============================================================================

$ErrorActionPreference = "Continue"
$SessionName = "BETSFIX"
$TaskName    = "BETSFIX Remote Control"

function Section($n) { Write-Host "`n=== $n ===" -ForegroundColor Cyan }
function OK($m)      { Write-Host "  [OK]   $m" -ForegroundColor Green }
function WARN($m)    { Write-Host "  [!]    $m" -ForegroundColor Yellow }
function BAD($m)     { Write-Host "  [X]    $m" -ForegroundColor Red }
function INFO($m)    { Write-Host "  ->     $m" -ForegroundColor DarkGray }

Write-Host "RAPPORT DE DIAGNOSTIC REMOTE CONTROL -- $SessionName" -ForegroundColor White
Write-Host ("PC: {0}   User: {1}   {2}" -f $env:COMPUTERNAME, $env:USERNAME, (Get-Date))

# --- 1. claude installe ? ---------------------------------------------------
Section "1. Commande 'claude'"
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Claude\claude.exe"
    if (Test-Path $candidate) { $claude = $candidate }
}
if ($claude) { OK "trouve : $claude" }
else {
    BAD "commande 'claude' INTROUVABLE."
    INFO "Installe-la : npm install -g @anthropic-ai/claude-code   (Node 18+ requis)"
}

# --- 2. compte claude.ai logge ? --------------------------------------------
Section "2. Compte claude.ai sur ce PC (cause de panne n.1)"
$credPaths = @(
    (Join-Path $env:USERPROFILE ".claude\.credentials.json"),
    (Join-Path $env:USERPROFILE ".claude.json")
)
$cred = $credPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($cred) {
    OK "fichier d'identifiants present : $cred"
    $email = $null
    try {
        $j = Get-Content $cred -Raw | ConvertFrom-Json
        $email = $j.oauthAccount.emailAddress
        if (-not $email) { $email = $j.email }
    } catch {}
    if ($email) { INFO "Compte detecte : $email" }
    WARN "VERIFIE QUE C'EST EXACTEMENT LE MEME COMPTE que sur l'app mobile."
    INFO "Dans une session claude, tape /status pour confirmer le compte affiche."
} else {
    BAD "aucun fichier d'identifiants (.credentials.json / .claude.json) trouve."
    INFO "Lance 'claude', tape /login -> claude.ai, accepte la confiance du dossier, /exit."
}

# --- 3. session --remote-control BETSFIX en cours ? -------------------------
Section "3. Session 'claude --remote-control $SessionName' en cours"
$procs = @(Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
    Where-Object { $_.CommandLine -match "remote-control $SessionName" })
switch ($procs.Count) {
    0 { BAD "AUCUNE session en cours. Rien ne peut apparaitre sur le mobile."
        INFO "Lance-la : claude --remote-control `"$SessionName`" --dangerously-skip-permissions"
        INFO "ou relance l'autostart : .\deploy\setup_remote_control.ps1 -Restart (PowerShell admin)" }
    1 { OK ("exactement 1 session (PID {0}). C'est l'etat attendu." -f $procs[0].ProcessId) }
    default {
        BAD ("DOUBLON : {0} sessions tournent en parallele -> elles se disputent le nom" -f $procs.Count)
        INFO "= AUCUNE session visible sur claude.ai/code. Tue les intrus, garde-en une seule :"
        $procs | ForEach-Object { INFO ("  PID {0} : {1}" -f $_.ProcessId, $_.CommandLine) }
        INFO "Stop-Process -Id <PID> pour tuer un doublon."
    }
}
$pidFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".remote-control.pid"
if (Test-Path $pidFile) { INFO ("PID de la boucle superviseur (.remote-control.pid) : {0}" -f (Get-Content $pidFile -Raw).Trim()) }

# --- 4. doublon de tache planifiee ? ----------------------------------------
Section "4. Taches planifiees liees au Remote Control"
$tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -match 'BETSFIX|Remote Control' })
if ($tasks.Count -eq 0) {
    WARN "aucune tache d'autostart trouvee (visible sans elevation)."
    INFO "Cree-la : .\deploy\setup_remote_control.ps1 (PowerShell administrateur)."
    INFO "NB: une tache en compte SYSTEM peut etre masquee ici -> revoir en admin (cf. CLAUDE.md)."
} else {
    $tasks | ForEach-Object { INFO ("Tache: '{0}'  [{1}]" -f $_.TaskName, $_.State) }
    $official = @($tasks | Where-Object { $_.TaskName -eq $TaskName })
    $others   = @($tasks | Where-Object { $_.TaskName -ne $TaskName })
    if ($official.Count -eq 1 -and $others.Count -eq 0) {
        OK "une seule tache, la bonne : '$TaskName'."
    } elseif ($others.Count -ge 1) {
        BAD "DOUBLON de tache d'autostart -> risque de 2 sessions concurrentes."
        $others | ForEach-Object {
            INFO ("Supprime l'intrus : Unregister-ScheduledTask -TaskName `"{0}`" -Confirm:`$false" -f $_.TaskName)
        }
    }
}

# --- 5. detail de la tache officielle ---------------------------------------
Section "5. Detail de la tache '$TaskName'"
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($t) {
    OK ("etat : {0}" -f $t.State)
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($info) {
        INFO ("derniere execution : {0}  (resultat: {1})" -f $info.LastRunTime, $info.LastTaskResult)
    }
} else {
    WARN "tache '$TaskName' introuvable sans elevation."
    INFO "Recree-la : .\deploy\setup_remote_control.ps1 (PowerShell admin)."
}

# --- 6. anti-veille ---------------------------------------------------------
Section "6. Mise en veille (standby) sur secteur"
try {
    $sb = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null | Select-String 'Index de param.* courant' -SimpleMatch:$false)
    # Robuste a la langue : on lit la valeur AC directement.
    $line = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null) -match 'AC|secteur'
    $acHex = ((powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null | Select-String '0x[0-9a-fA-F]+' | Select-Object -First 1).Matches.Value)
    if ($acHex) {
        $sec = [Convert]::ToInt32($acHex,16)
        if ($sec -eq 0) { OK "veille desactivee sur secteur (standby-timeout-ac = 0)." }
        else { WARN ("veille apres {0} s sur secteur -> la session mourra. Corrige : powercfg /change standby-timeout-ac 0" -f $sec) }
    } else { INFO "impossible de lire le timeout (langue/format). Au besoin : powercfg /change standby-timeout-ac 0" }
} catch { INFO "lecture standby impossible : powercfg /change standby-timeout-ac 0 pour etre sur." }

Write-Host "`n--- Fin du rapport. Corrige les lignes [X] puis [!] en priorite. ---`n" -ForegroundColor White
