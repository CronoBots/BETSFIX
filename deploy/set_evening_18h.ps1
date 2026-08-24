# BETSFIX — décale la tâche « BETSFIX Scan Soir » à 18h00 (user 2026-08-24).
# But : le scan du soir analyse le slate NUIT ET (re)construit le combiné + la montante du jour (--daily-combo)
# depuis TOUT le slate encore à venir (soir + nuit). À 18h -> inclut les matchs KO >= ~19h. À LANCER EN ADMIN.
$ErrorActionPreference = 'Stop'
$task = 'BETSFIX Scan Soir'
try {
    $trig = New-ScheduledTaskTrigger -Daily -At 18:00
    Set-ScheduledTask -TaskName $task -Trigger $trig | Out-Null
    Write-Host "OK : '$task' decalee a 18h00." -ForegroundColor Green
    (Get-ScheduledTask -TaskName $task).Triggers | Format-List StartBoundary
} catch {
    Write-Host "ECHEC : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "La tache '$task' existe-t-elle ? (le batch soir doit avoir ete installe)"
}
Write-Host "`n(fenetre laissee ouverte — tu peux la fermer)"
