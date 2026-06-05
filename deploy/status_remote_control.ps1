# ============================================================================
#  Statut RAPIDE du Remote Control BETSFIX (suis-je connecte ?)
#
#  Repond en un coup d'oeil :
#    - le process 'claude --remote-control' tourne-t-il ?
#    - le fichier .remote-control.pid pointe-t-il vers un process vivant ?
#    - le journal a-t-il bouge recemment (donc la boucle est active) ?
#    - quand a eu lieu la derniere relance ?
#
#  Lancer (sur ton PC, dans le dossier BETSFIX) :
#       powershell -ExecutionPolicy Bypass -File .\deploy\status_remote_control.ps1
#
#  VERDICT final = la reponse a "suis-je connecte en remote control ?".
#  Rappel : le seul juge ABSOLU reste l'app Claude / claude.ai/code sur mobile :
#  si la session "BETSFIX" y apparait, tu es bien connecte.
# ============================================================================
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Section($t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }
function OK($t)   { Write-Host "[ OK ]  $t" -ForegroundColor Green }
function BAD($t)  { Write-Host "[ KO ]  $t" -ForegroundColor Red }
function INFO($t) { Write-Host "        $t" -ForegroundColor Gray }

Write-Host "STATUT REMOTE CONTROL - BETSFIX" -ForegroundColor White
INFO ("Date    : " + (Get-Date))
INFO ("Dossier : " + $root)

$alive = $false   # un process remote-control est-il en vie ?
$fresh = $false   # le journal a-t-il bouge recemment ?

# 1. Process 'claude --remote-control' en cours ?
Section "1. Process Claude remote-control"
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "remote-control" }
if ($procs) {
  $alive = $true
  $procs | ForEach-Object { OK ("PID " + $_.ProcessId + " : " + $_.CommandLine) }
} else {
  BAD "aucun process 'remote-control' en cours"
}

# 2. Fichier .remote-control.pid (PID de la boucle de supervision)
Section "2. Boucle de supervision (.remote-control.pid)"
$pidFile = Join-Path $root ".remote-control.pid"
if (Test-Path $pidFile) {
  $loopPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
  $p = Get-Process -Id $loopPid -ErrorAction SilentlyContinue
  if ($p) {
    OK ("boucle vivante (PID " + $loopPid + ", demarree " + $p.StartTime + ")")
    $alive = $true
  } else {
    BAD ("le fichier pointe PID " + $loopPid + " mais ce process est MORT (boucle arretee)")
  }
} else {
  INFO "(pas de .remote-control.pid : la boucle remote-control-loop.ps1 n'a jamais ecrit son PID)"
}

# 3. Tache planifiee d'auto-demarrage
Section "3. Tache d'auto-demarrage au boot"
$task = Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -match "remote" -and ($_.TaskName -match "BETSFIX" -or $_.TaskName -match "API-SPORT") }
if ($task) {
  $task | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    OK  ("tache '" + $_.TaskName + "'  [" + $_.State + "]")
    INFO ("derniere exec : " + $i.LastRunTime + "  (code " + $i.LastTaskResult + ")")
  }
} else {
  INFO "(aucune tache d'auto-demarrage -> tu lances le remote control a la main)"
}

# 4. Journal : a-t-il bouge recemment ?
Section "4. Journal remote_control.log"
$log = Join-Path $root "deploy\remote_control.log"
if (Test-Path $log) {
  $last = (Get-Item $log).LastWriteTime
  $age  = (Get-Date) - $last
  INFO ("derniere ecriture : " + $last + "  (il y a " + [int]$age.TotalMinutes + " min)")
  if ($age.TotalMinutes -le 15) { OK "journal recent -> la boucle tourne"; $fresh = $true }
  else { BAD "journal ancien (> 15 min) -> la boucle est peut-etre arretee" }
  Write-Host "        --- 8 dernieres lignes ---" -ForegroundColor DarkGray
  Get-Content $log -Tail 8 | ForEach-Object { INFO $_ }
} else {
  INFO "(pas de journal : la tache/boucle n'a jamais tourne)"
}

# ---------------------------------------------------------------------------
# VERDICT
# ---------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor White
if ($alive) {
  Write-Host " VERDICT : REMOTE CONTROL ACTIF (process en vie)" -ForegroundColor Green
  Write-Host " -> Ouvre l'app Claude / claude.ai/code : la session 'BETSFIX'" -ForegroundColor Gray
  Write-Host "    doit y apparaitre. C'est la confirmation finale." -ForegroundColor Gray
} elseif ($fresh) {
  Write-Host " VERDICT : PROBABLEMENT ACTIF (journal recent, process non vu)" -ForegroundColor Yellow
  Write-Host " -> Verifie sur mobile. Relance si besoin : claude --remote-control BETSFIX" -ForegroundColor Gray
} else {
  Write-Host " VERDICT : REMOTE CONTROL INACTIF" -ForegroundColor Red
  Write-Host " -> Relance-le :" -ForegroundColor Gray
  Write-Host "      claude --remote-control BETSFIX" -ForegroundColor White
  Write-Host "    ou via la boucle :" -ForegroundColor Gray
  Write-Host "      powershell -ExecutionPolicy Bypass -File .\remote-control-loop.ps1" -ForegroundColor White
}
Write-Host "========================================" -ForegroundColor White
