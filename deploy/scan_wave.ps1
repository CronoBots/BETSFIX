# BETSFIX — PASSE « ~1 h avant chaque match » (tâche « BETSFIX Scan Wave », compte vince) : RÈGLEMENT SEUL.
# ⛔ LA RÉ-ANALYSE PRÉ-MATCH A ÉTÉ SUPPRIMÉE (demande user 2026-08-07 : « plus aucun changement de pari sur
# un match analysé à 09h — le plus simple c'est supprimer la ré-analyse »). Le pick du MATIN (scan_daily)
# est DÉFINITIF : plus de `generate_analyses --refresh-early` ici, donc plus aucun flip morning->vague.
# Cette passe ne fait plus que : (1) RÈGLEMENT silencieux des matchs finis (poste les résultats vite,
# --no-bilan) et (2) auto-audit d'intégrité. Le scan complet (analyse + méthodo + revue…) reste 1×/jour
# le matin dans scan_daily.ps1. Le garde-fou code (generate_analyses : une vague ne ré-analyse jamais un
# match déjà analysé) reste en place en ceinture-bretelles au cas où --refresh-early serait relancé à la main.
# ⏰ DÉCLENCHEMENT PAR MATCH : scan_daily.ps1 pose chaque matin, via deploy/schedule_reana.ps1, UN
# déclencheur ponctuel à (coup d'envoi − 1 h) par match -> le règlement tourne peu après chaque fin de match.
param([double]$WindowHours = 1.5)

$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
Set-Location $root

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Out-File -Append -Encoding utf8 $log
}

# Anti-course : si le SCAN MATIN (generate_analyses) tourne encore, on ne règle PAS en parallèle (évite de
# régler un sidecar en cours de réécriture). Le règlement repassera au prochain déclencheur / boucle API.
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("WAVE SKIP : le scan matin tourne encore (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# (Ré-analyse pré-match SUPPRIMÉE 2026-08-07 : plus de `generate_analyses --refresh-early` -> le pick du
#  matin ne change plus jamais. $WindowHours n'est plus utilisé que pour la compat de signature de la tâche.)

# RÉCONCILIATION : règle tout ce qui est réglable (poste les résultats peu après la fin des matchs),
# re-poste les pronos imminents dont l'envoi a été manqué, et envoie un BILAN Telegram. Passages
# fréquents -> résultats postés VITE (fini le « posté 3 jours après »).
Log 'WAVE RECONCILE : règlement SILENCIEUX (résultats postés, pas de bilan)'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("WAVE RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'WAVE SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("WAVE SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)
