# BETSFIX — PLANIFIE LES PASSES DE RÈGLEMENT PAR MATCH (remplace le sondage « toutes les 30 min »).
# ⚠️ La RÉ-ANALYSE pré-match a été SUPPRIMÉE (user 2026-08-07) : scan_wave.ps1 ne fait plus QUE régler +
# selfcheck (le pick du matin est définitif). On garde néanmoins un déclencheur PONCTUEL par match pour que
# le RÈGLEMENT tourne vite autour de chaque rencontre (en complément de la boucle continue de l'API).
# Pour CHAQUE match du programme du jour (data/day_programme.json), pose sur la tâche « BETSFIX Scan Wave »
# un déclencheur à (coup d'envoi − 1 h). Précision à la minute, zéro sondage.
# Rejoué chaque matin par scan_daily.ps1 -> Set-ScheduledTask REMPLACE tous les déclencheurs (pas d'accumulation).
# L'ACTION de la tâche reste scan_wave.ps1 (règlement + selfcheck, PLUS de ré-analyse).
# -Dry : calcule et affiche seulement (ne modifie PAS la tâche).
param([switch]$Dry)

$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$log  = Join-Path $root 'data\scan_cron.log'
$task = 'BETSFIX Scan Wave'
function Log($m) { "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Out-File -Append -Encoding utf8 $log }

$progPath = Join-Path $root 'data\day_programme.json'
$trigs = @()
$seen  = @{}
try {
    $prog = Get-Content $progPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $now  = Get-Date
    foreach ($m in $prog.matches) {
        try { $ko = ([datetimeoffset]$m.start).LocalDateTime } catch { continue }
        $at = $ko.AddHours(-1)                                  # passe règlement = coup d'envoi − 1 h
        if ($at -le $now.AddMinutes(2)) { continue }            # déjà passé/imminent -> couvert par le scan matin
        $key = $at.ToString('yyyyMMddHHmm')
        if ($seen.ContainsKey($key)) { continue }               # coups d'envoi identiques -> 1 seul déclencheur
        $seen[$key] = $true
        $trigs += New-ScheduledTaskTrigger -Once -At $at
        if ($Dry) { "  + règlement {0}  (match {1} à {2})" -f $at.ToString('dd/MM HH:mm'), $m.name, $ko.ToString('HH:mm') }
    }
} catch {
    Log ("REANA SCHED : lecture programme KO : {0}" -f $_.Exception.Message)
}

if ($trigs.Count -eq 0) {
    # Aucun match futur : un déclencheur ponctuel lointain (placeholder) pour RETIRER le sondage 30 min ;
    # il sera remplacé au prochain scan matin. Le règlement reste assuré par la boucle continue de l'API.
    $trigs += New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddDays(1).AddHours(8).AddMinutes(55)
}

if ($Dry) {
    "TOTAL : {0} déclencheur(s) (DRY, tâche non modifiée)." -f $trigs.Count
    return
}
try {
    Set-ScheduledTask -TaskName $task -Trigger $trigs -ErrorAction Stop | Out-Null
    Log ("REANA SCHED : {0} passe(s) de règlement planifiée(s) à coup d'envoi - 1 h." -f $trigs.Count)
    "OK : {0} déclencheur(s) posé(s) sur « {1} »." -f $trigs.Count, $task
} catch {
    Log ("REANA SCHED : Set-ScheduledTask KO : {0}" -f $_.Exception.Message)
    "ÉCHEC : {0}" -f $_.Exception.Message
}
