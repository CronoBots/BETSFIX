# ============================================================================
#  REPARER le Remote Control apres un DEPLACEMENT / RENOMMAGE du dossier
#  (ex : "api-sport" renomme en "BETSFIX").
#
#  POURQUOI c'est casse :
#    L'auto-demarrage avait memorise le CHEMIN ABSOLU de l'ancien dossier
#    (dans le lanceur du dossier Demarrage et/ou la tache planifiee).
#    Apres le renommage, ce chemin n'existe plus -> au boot, rien ne se lance,
#    donc la session "BETSFIX" n'apparait plus sur le mobile.
#
#  CE QUE FAIT CE SCRIPT (depuis le NOUVEau dossier, il se relocalise seul) :
#    1. Supprime les anciens lanceurs (Demarrage) qui pointaient vers api-sport
#    2. (Re)cree un lanceur dans le dossier Demarrage -> remote-control-loop.ps1
#       avec le CHEMIN ACTUEL (survit aux futurs renommages : relance ce script)
#    3. Desactive l'ancienne tache planifiee "API-SPORT-remote" si elle traine
#    4. DEMARRE le remote control tout de suite (pas besoin de rebooter)
#
#  AUCUN droit administrateur, AUCUN mot de passe (methode dossier Demarrage).
#
#  USAGE (sur ton PC, dans le dossier BETSFIX) :
#       powershell -ExecutionPolicy Bypass -File .\deploy\reparer_remote_control.ps1
# ============================================================================
$ErrorActionPreference = "Continue"

# --- On se relocalise tout seul : $root = le dossier ACTUEL du projet --------
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function OK($t)   { Write-Host "[ OK ]  $t" -ForegroundColor Green }
function BAD($t)  { Write-Host "[ KO ]  $t" -ForegroundColor Red }
function INFO($t) { Write-Host "        $t" -ForegroundColor Gray }
function Step($t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }

Write-Host "REPARATION REMOTE CONTROL - BETSFIX" -ForegroundColor White
INFO ("Nouveau dossier : " + $root)

$loop = Join-Path $root "remote-control-loop.ps1"
if (-not (Test-Path $loop)) {
  BAD "remote-control-loop.ps1 introuvable dans ce dossier. Fais un 'git pull' d'abord."
  return
}

$startup = [Environment]::GetFolderPath("Startup")
$vbsName = "claude-remote-control-betsfix.vbs"
$vbsPath = Join-Path $startup $vbsName

# --- 1) Nettoyage des anciens lanceurs casses (qui pointent vers api-sport) --
Step "1. Nettoyage des anciens lanceurs du dossier Demarrage"
$removed = 0
Get-ChildItem $startup -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -in ".vbs", ".lnk", ".bat", ".cmd" } |
  ForEach-Object {
    $isClaude = $false
    if ($_.Extension -in ".vbs", ".bat", ".cmd") {
      $txt = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
      if ($txt -match "remote-control" -or $txt -match "api-sport" -or $txt -match "BETSFIX") { $isClaude = $true }
    } elseif ($_.Extension -eq ".lnk") {
      try {
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($_.FullName)
        if ($sc.TargetPath -match "api-sport" -or $sc.WorkingDirectory -match "api-sport" -or
            $sc.Arguments -match "remote-control-loop" -or $sc.TargetPath -match "claude") { $isClaude = $true }
      } catch {}
    }
    # On ne supprime PAS notre nouveau vbs (recree juste apres), mais on vire
    # tout ancien lanceur Claude qui pointe ailleurs (ancien chemin).
    if ($isClaude -and $_.Name -ne $vbsName) {
      Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
      INFO ("supprime : " + $_.Name)
      $removed++
    }
  }
if ($removed -eq 0) { INFO "(aucun ancien lanceur a supprimer)" } else { OK "$removed ancien(s) lanceur(s) supprime(s)" }

# --- 2) (Re)creation du lanceur sur le CHEMIN ACTUEL ------------------------
Step "2. Creation du lanceur sur le nouveau chemin"
# Un .vbs lance PowerShell en fenetre CACHEE (0) sans console visible.
$ps = "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$loop"""
$vbs = @"
' Lanceur auto du Remote Control BETSFIX (genere par reparer_remote_control.ps1).
' Demarre la boucle remote-control-loop.ps1 en fenetre cachee a l'ouverture de session.
Set sh = CreateObject("WScript.Shell")
sh.Run "$ps", 0, False
"@
Set-Content -Path $vbsPath -Value $vbs -Encoding ASCII
OK ("lanceur ecrit : " + $vbsPath)
INFO ("-> cible : " + $loop)

# --- 3) Ancienne tache planifiee "API-SPORT-remote" (chemin mort) -----------
Step "3. Ancienne tache planifiee"
$task = Get-ScheduledTask -TaskName "API-SPORT-remote" -ErrorAction SilentlyContinue
if ($task) {
  try {
    Unregister-ScheduledTask -TaskName "API-SPORT-remote" -Confirm:$false -ErrorAction Stop
    OK "ancienne tache 'API-SPORT-remote' supprimee (elle pointait vers l'ancien dossier)"
  } catch {
    BAD "impossible de supprimer 'API-SPORT-remote' (droits insuffisants)."
    INFO "Ouvre PowerShell EN ADMINISTRATEUR et lance :"
    INFO "   Unregister-ScheduledTask -TaskName 'API-SPORT-remote' -Confirm:`$false"
  }
} else {
  INFO "(aucune tache 'API-SPORT-remote' : tu utilisais deja le dossier Demarrage)"
}

# --- 4) Demarrage immediat (sans reboot) ------------------------------------
Step "4. Demarrage immediat du remote control"
$already = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -match "remote-control" }
if ($already) {
  INFO "une session remote-control tourne deja, je ne la double pas."
  $already | ForEach-Object { INFO ("PID " + $_.ProcessId) }
} else {
  Start-Process wscript.exe -ArgumentList "`"$vbsPath`"" -WorkingDirectory $root
  Start-Sleep -Seconds 3
  $now = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "remote-control" }
  if ($now) { OK "remote control DEMARRE" } else { INFO "lance... (laisse ~10 s puis verifie le statut)" }
}

# --- Fin --------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor White
Write-Host " REPARATION TERMINEE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor White
INFO "1. Verifie sur ton MOBILE (app Claude / claude.ai/code) : la session"
INFO "   'BETSFIX' doit apparaitre d'ici ~10-20 s."
INFO "2. Controle a tout moment l'etat avec :"
INFO "      powershell -ExecutionPolicy Bypass -File .\deploy\status_remote_control.ps1"
INFO "3. ASTUCE : si tu renommes/deplaces encore le dossier un jour, relance"
INFO "   simplement CE script depuis le nouveau dossier, il se reconfigure seul."
