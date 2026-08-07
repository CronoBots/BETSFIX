# BETSFIX - POSE LES 2 CRENEAUX DE SCAN (a lancer EN ADMIN).
# 1) Decale la tache du MATIN "BETSFIX Scan" (slate JOUR) a 10:00.
# 2) Cree la tache du SOIR "BETSFIX Scan Soir" (slate NUIT) a 19:00, en CLONANT le principal + les
#    reglages + l'action de la tache du matin (meme compte vince / meme session), action = scan_evening.ps1.
# Idempotent : -Force remplace la tache du soir si elle existe deja. Ne touche a rien d'autre.
# Heures modifiables en tete (MorningAt / EveningAt). (ASCII pur : Windows PowerShell 5.1.)
param(
    [string]$MorningAt = '10:00',
    [string]$EveningAt = '19:00',
    [string]$MorningTask = 'BETSFIX Scan',
    [string]$EveningTask = 'BETSFIX Scan Soir'
)
$ErrorActionPreference = 'Stop'

# --- verif elevation ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host 'ERREUR : lance ce script en ADMINISTRATEUR.' -ForegroundColor Red; exit 1 }

# --- tache du matin (modele) ---
try { $src = Get-ScheduledTask -TaskName $MorningTask -ErrorAction Stop }
catch {
    Write-Host "Tache '$MorningTask' introuvable. Taches BETSFIX presentes :" -ForegroundColor Yellow
    Get-ScheduledTask | Where-Object { $_.TaskName -match 'BETSFIX' } | Format-Table TaskName, State
    exit 1
}
Write-Host "Action(s) de la tache du matin (controle) :" -ForegroundColor Cyan
$src.Actions | Format-List Execute, Arguments, WorkingDirectory

# --- 1) MATIN -> $MorningAt (garde principal/action/settings) ---
Set-ScheduledTask -TaskName $MorningTask -Trigger (New-ScheduledTaskTrigger -Daily -At $MorningAt) | Out-Null
Write-Host "OK : '$MorningTask' planifiee chaque jour a $MorningAt." -ForegroundColor Green

# --- 2) SOIR : clone l'action (scan_daily.ps1 -> scan_evening.ps1) ---
$actions = @()
$matched = $false
foreach ($a in $src.Actions) {
    $arg2 = $a.Arguments
    if ($arg2 -match 'scan_daily\.ps1') { $arg2 = $arg2 -replace 'scan_daily\.ps1', 'scan_evening.ps1'; $matched = $true }
    $actions += New-ScheduledTaskAction -Execute $a.Execute -Argument $arg2 -WorkingDirectory $a.WorkingDirectory
}
if (-not $matched) {
    Write-Host "ATTENTION : 'scan_daily.ps1' non trouve dans l'action du matin - verifie l'action ci-dessus" -ForegroundColor Yellow
    Write-Host "et adapte l'argument de la tache du soir vers deploy\scan_evening.ps1." -ForegroundColor Yellow
}
Register-ScheduledTask -TaskName $EveningTask -Action $actions -Principal $src.Principal `
    -Settings $src.Settings -Trigger (New-ScheduledTaskTrigger -Daily -At $EveningAt) -Force | Out-Null
Write-Host "OK : '$EveningTask' creee, chaque jour a $EveningAt (slate NUIT)." -ForegroundColor Green

# --- recap ---
Write-Host "`nRecap des taches BETSFIX :" -ForegroundColor Cyan
foreach ($tn in @($MorningTask, $EveningTask, 'BETSFIX Scan Wave')) {
    try {
        $info = Get-ScheduledTaskInfo -TaskName $tn -ErrorAction Stop
        "  {0} -> prochain lancement : {1}" -f $tn, $info.NextRunTime
    } catch {
        "  {0} -> (absente)" -f $tn
    }
}
