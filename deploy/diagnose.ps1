# ============================================================================
#  Diagnostic : pourquoi "rien ne se lance seul ?"
#  Lance :  powershell -ExecutionPolicy Bypass -File .\deploy\diagnose.ps1
#  Puis copie-colle TOUT le rapport a Claude.
# ============================================================================
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Section($t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }
function OK($t)   { Write-Host "[ OK ]  $t" -ForegroundColor Green }
function BAD($t)  { Write-Host "[ KO ]  $t" -ForegroundColor Red }
function INFO($t) { Write-Host "        $t" -ForegroundColor Gray }

Write-Host "RAPPORT DE DIAGNOSTIC BETSFIX" -ForegroundColor White
INFO ("Date        : " + (Get-Date))
INFO ("Dossier     : " + $root)
INFO ("Utilisateur : " + $env:USERNAME)

# 1. Claude installe ?
Section "1. Claude Code"
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) { OK ("claude trouve : " + $claude.Source) } else { BAD "commande 'claude' INTROUVABLE (npm install -g @anthropic-ai/claude-code)" }

# 2. Connecte a un compte ? (presence du dossier de config)
Section "2. Connexion au compte Claude"
$cfg = Join-Path $env:USERPROFILE ".claude"
if (Test-Path $cfg) { OK (".claude existe ($cfg)") } else { BAD "pas de dossier .claude -> jamais connecte ? Lance 'claude' une fois a la main." }

# 3. Tache planifiee Remote Control
Section "3. Tache 'BETSFIX-remote' (auto-relance Claude)"
$t = Get-ScheduledTask -TaskName "BETSFIX-remote" -ErrorAction SilentlyContinue
if ($t) {
  OK "tache enregistree"
  INFO ("Etat        : " + $t.State)
  $info = $t | Get-ScheduledTaskInfo
  INFO ("Derniere exec : " + $info.LastRunTime + "  (resultat code: " + $info.LastTaskResult + ")")
  INFO ("Prochaine    : " + $info.NextRunTime)
} else { BAD "tache absente -> lance setup_remote_control.ps1" }

# 4. Autres taches / services API + tunnel
Section "4. API + tunnel Cloudflare"
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like "*BETSFIX*" -or $_.TaskName -like "*API-SPORT*" -or $_.TaskName -like "*sport*" -or $_.TaskName -like "*tunnel*" } | ForEach-Object { INFO ("Tache: " + $_.TaskName + "  [" + $_.State + "]") }
Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*cloudflared*" -or $_.DisplayName -like "*cloudflare*" } | ForEach-Object { if ($_.Status -eq "Running") { OK ("Service " + $_.Name + " : RUNNING") } else { BAD ("Service " + $_.Name + " : " + $_.Status) } }

# 5. Ouverture de session automatique
Section "5. Ouverture de session Windows automatique"
$w = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$auto = (Get-ItemProperty $w -Name AutoAdminLogon -ErrorAction SilentlyContinue).AutoAdminLogon
$dun  = (Get-ItemProperty $w -Name DefaultUserName -ErrorAction SilentlyContinue).DefaultUserName
if ($auto -eq "1") { OK ("AutoAdminLogon = 1 (compte: " + $dun + ")") } else { BAD "login auto DESACTIVE (AutoAdminLogon != 1) -> un reboot sans toi ne demarre rien" }

# 6. Processus claude actuellement en vie ?
Section "6. Processus en cours"
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "remote-control" }
if ($procs) { $procs | ForEach-Object { OK ("PID " + $_.ProcessId + " : " + $_.CommandLine) } } else { BAD "aucun processus 'remote-control' en cours" }

# 7. Journal
Section "7. Journal remote_control.log (20 dernieres lignes)"
$log = Join-Path $root "deploy\remote_control.log"
if (Test-Path $log) { Get-Content $log -Tail 20 } else { INFO "(pas encore de journal -> la tache n'a jamais tourne)" }

Write-Host "`n===== FIN DU RAPPORT (copie tout ce qui precede) =====" -ForegroundColor White
