# BETSFIX — RÈGLEMENT AUTOMATIQUE CONTINU (tâche « BETSFIX Reconcile », compte vince, toutes les 10 min).
# Demande user 2026-07-21 : « le pari du jour et le combiné viennent de se terminer mais ne sont pas
# réglés — comment automatiser pour que ce soit fait directement ? ». Avant, le règlement n'était
# déclenché que par les VAGUES (heures fixes ~KO−1h de chaque match) : un match finissant entre deux
# vagues attendait la suivante (voire le scan du lendemain). Cette boucle règle en continu :
# résultats postés ≤ 10 min après la fin réelle de chaque match.
#
# LÉGER par conception : tools/reconcile.py ne fait AUCUN appel Claude (pas de session requise) ; s'il
# n'y a rien à régler, il parcourt les pending et sort en quelques secondes. --no-bilan : poste les
# cartes de résultat, jamais de récap. Le selfcheck reste dans scan_daily/scan_wave (pas besoin ici
# toutes les 10 min).
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
Set-Location $root

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Out-File -Append -Encoding utf8 $log
}

# Anti-doublon : si un scan (vague/complet) OU un autre reconcile tourne déjà, on passe son tour —
# jamais deux règlements concurrents (écritures sidecars/notifs).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses|reconcile\.py' }
if ($running) {
    exit 0    # silencieux : passage suivant dans 10 min (pas de spam du log)
}

$out = & $py 'tools\reconcile.py' --no-bilan 2>&1
# Ne journalise QUE s'il s'est passé quelque chose (règlement/re-post) -> log lisible, pas 144 lignes/jour.
$txt = ($out | Out-String)
if ($txt -match 'Réglés à l''instant : [1-9]' -or $txt -match 'Regles a l''instant : [1-9]' -or
    $txt -match 're-postés : [1-9]' -or $LASTEXITCODE -ne 0) {
    Log ("RECONCILE-10MIN (exit {0})" -f $LASTEXITCODE)
    $out | Out-File -Append -Encoding utf8 $log
}
