# BETSFIX - ACTIVE / DESACTIVE le mode WAVE-FIRST (analyse ~2h avant le coup d'envoi). A LANCER EN ADMIN.
#
# ACTIVER (defaut) :
#   1) Cree/majnstalle la tache "BETSFIX Scan Sweep" (toutes les 30 min) qui lance deploy\scan_sweep.ps1,
#      en CLONANT le principal (compte vince authentifie) + les reglages de la tache "BETSFIX Scan".
#   2) SEULEMENT APRES (tache posee) : cree le drapeau data\scan_wave_first.flag -> le matin/soir cessent
#      d'analyser en batch, le sweep devient l'analyseur principal. Ordre = jamais "matin sans analyse ET
#      sweep absent".
#
# DESACTIVER (revert) :   .\setup_wave_first.ps1 -Off
#   1) Supprime le drapeau -> le batch matin/soir REanalyse immediatement (retour a l'etat actuel).
#   2) Desactive la tache sweep (gardee, desactivee -> reactivation instantanee sans re-cloner).
#
# Idempotent. Ne touche a AUCUNE autre tache. ASCII pur (Windows PowerShell 5.1).

param(
    [switch]$Off,
    [string]$SweepTask = 'BETSFIX Scan Sweep',
    [string]$ModelTask = 'BETSFIX Scan',
    [string]$EveningTask = 'BETSFIX Scan Soir',   # batch NUIT : redondant en wave-first (le sweep analyse la nuit)
    [int]$EveryMinutes = 30
)
$ErrorActionPreference = 'Stop'
$root = 'C:\Users\vince\BETSFIX'
$flag = Join-Path $root 'data\scan_wave_first.flag'
$sweep = Join-Path $root 'deploy\scan_sweep.ps1'

# --- verif elevation ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host 'ERREUR : lance ce script en ADMINISTRATEUR.' -ForegroundColor Red; exit 1 }

# ============================ DESACTIVATION ============================
if ($Off) {
    if (Test-Path $flag) { Remove-Item $flag -Force; Write-Host 'OK : drapeau retire -> retour au batch matin/soir.' -ForegroundColor Green }
    else { Write-Host 'Drapeau deja absent (mode batch).' -ForegroundColor Yellow }
    try { Disable-ScheduledTask -TaskName $SweepTask -ErrorAction Stop | Out-Null
          Write-Host "OK : tache '$SweepTask' desactivee (conservee)." -ForegroundColor Green }
    catch { Write-Host "Tache '$SweepTask' absente (rien a desactiver)." -ForegroundColor Yellow }
    # Retour au batch -> le scan du SOIR redevient utile (il ré-analyse le slate nuit) : on le REACTIVE.
    try { Enable-ScheduledTask -TaskName $EveningTask -ErrorAction Stop | Out-Null
          Write-Host "OK : tache '$EveningTask' reactivee (batch nuit)." -ForegroundColor Green }
    catch { Write-Host "Tache '$EveningTask' absente." -ForegroundColor Yellow }
    return
}

# ============================ ACTIVATION ============================
# --- tache modele (pour cloner principal + settings = compte vince authentifie) ---
try { $src = Get-ScheduledTask -TaskName $ModelTask -ErrorAction Stop }
catch {
    Write-Host "Tache modele '$ModelTask' introuvable. Taches BETSFIX presentes :" -ForegroundColor Yellow
    Get-ScheduledTask | Where-Object { $_.TaskName -match 'BETSFIX' } | Format-Table TaskName, State
    exit 1
}

# --- action : lance le sweep en PowerShell (comme les autres taches BETSFIX) ---
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$action = New-ScheduledTaskAction -Execute $psExe `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $sweep)

# --- declencheur : toutes les $EveryMinutes minutes, indefiniment ---
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(1))
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

Register-ScheduledTask -TaskName $SweepTask -Action $action -Principal $src.Principal `
    -Settings $src.Settings -Trigger $trigger -Force | Out-Null
try { Enable-ScheduledTask -TaskName $SweepTask -ErrorAction Stop | Out-Null } catch {}
Write-Host "OK : '$SweepTask' posee (toutes les $EveryMinutes min, compte vince)." -ForegroundColor Green

# --- SEULEMENT MAINTENANT (tache posee) : armer le drapeau -> le batch cesse, le sweep prend le relais ---
New-Item -ItemType File -Path $flag -Force | Out-Null
Set-Content -Path $flag -Value ("wave-first active {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm')) -Encoding utf8
Write-Host "OK : mode WAVE-FIRST ACTIVE (drapeau $flag)." -ForegroundColor Green

# Le scan du SOIR (batch nuit) devient REDONDANT (le sweep analyse la nuit ~2h avant chaque KO ET reconcilie
# toutes les 30 min) -> on le DESACTIVE (conserve, reactive par -Off). La LISTE reste faite 1x le matin.
try { Disable-ScheduledTask -TaskName $EveningTask -ErrorAction Stop | Out-Null
      Write-Host "OK : tache '$EveningTask' desactivee (redondante en wave-first)." -ForegroundColor Green }
catch { Write-Host "Tache '$EveningTask' absente (rien a desactiver)." -ForegroundColor Yellow }

# --- recap ---
Write-Host "`nRecap :" -ForegroundColor Cyan
foreach ($tn in @($ModelTask, 'BETSFIX Scan Soir', 'BETSFIX Scan Wave', $SweepTask)) {
    try { $info = Get-ScheduledTaskInfo -TaskName $tn -ErrorAction Stop
          "  {0,-22} -> prochain : {1}" -f $tn, $info.NextRunTime }
    catch { "  {0,-22} -> (absente)" -f $tn }
}
Write-Host "`nMESURE : comparer le taux phare (Confiance) + ROI sur 1-2 semaines vs le batch. Revert = -Off." -ForegroundColor Cyan
