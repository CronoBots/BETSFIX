# BETSFIX — SCAN DU SOIR = SLATE NUIT (tâche planifiée « BETSFIX Scan Soir », compte vince).
# 2 créneaux (user 2026-08-07) : le matin (scan_daily) analyse le SLATE JOUR (coup d'envoi 6h→21h, heure
# belge : Europe + Asie-après-midi) ; CE scan analyse le SLATE NUIT (coup d'envoi 21h→06h : Amériques —
# MLS/Brésil/Argentine/Liga MX — + Europe tardive) avec des données du JOUR MÊME, au lieu des ~12-20 h de
# retard d'un scan unique du matin. Partition par HEURE DE COUP D'ENVOI -> chaque match tombe dans UN seul
# slate, AUCUNE région n'est re-scannée (le filtre --ko-from/--ko-to exclut le slate jour d'ici).
#
# SÉLECTIONNE le SLATE NUIT ICI (user 2026-08-30) : le matin n'écrit que le slate JOUR ; à 19h les cotes
# Unibet des matchs de nuit (Amériques) sont ENFIN ouvertes -> on choisit le top-N NUIT au moment où le
# boost favori-net peut vraiment les juger (fini l'écrasement faute de cote à 10h). Le build ci-dessous
# FUSIONNE la nuit dans data/day_programme.json en RECONDUISANT le slate jour du matin (préservation
# same-day) -> ne re-poste PAS le programme, ne touche PAS aux matchs de jour. Analyse + PUBLIE ensuite les
# puis règle (résultats postés vite) + auto-audit. Les gros calculs quotidiens (méthodo/revue/backtest/
# débrief/santé sources) restent dans scan_daily.ps1 (1×/jour, matin). Pas de ré-analyse : le pick du soir
# est DÉFINITIF (comme celui du matin).
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
$flag = Join-Path $root 'data\scan_wave_first.flag'   # présent = mode WAVE-FIRST (le sweep analyse la nuit)
Set-Location $root
. (Join-Path $root 'deploy\_log.ps1')   # log concurrent-safe (Add-BfxStream / Write-BfxLogLine)

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-BfxStream $log
}

# Anti-doublon : si un generate_analyses tourne déjà (scan matin non fini, ou double déclenchement), on ne
# lance PAS une 2e passe concurrente (deux scans = doublons de cartes).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("SOIR SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# SLATE NUIT : coup d'envoi 21h→06h (heure belge). --hours 12 couvre 19h -> 07h (tout le slate nuit depuis
# ~19h). --from-programme = uniquement la liste du matin (aucune dérive de sélection). PAS de --force : les
# matchs de nuit n'ont pas encore été analysés (le matin ne fait que le slate jour) -> analyse normale ;
# un match de jour (coup d'envoi < 21h) est EXCLU par la bande -> jamais re-scané ici.
# SCAN SOIR (analyse du slate NUIT en batch) : SAUTÉ en mode WAVE-FIRST (user 2026-08-11) -> le sweep
# analyse chaque match de nuit ~1h avant SON coup d'envoi. Sans le drapeau -> comportement batch inchangé.
if (Test-Path $flag) {
    Log 'SCAN SOIR : SAUTÉ (mode WAVE-FIRST) -> analyse par le sweep ~1h avant chaque coup d''envoi'
} else {
    # OPTION B (user 2026-08-23) : le soir ANALYSE le slate NUIT mais NE PUBLIE PAS (--no-notify) ; chaque pari
    # de nuit est publié ~1 h avant SON coup d'envoi par la vague (re-analyse fraîche -> publie ou s'abstient).
    # --daily-combo (user 2026-08-24) : À LA FIN de cette passe, on (re)construit LE combiné + LA montante du jour
    # depuis les paris analysés ENCORE À VENIR (soir+nuit) -> une seule construction figée, meilleur vivier.
    # 1) SÉLECTION du slate NUIT (adaptatif, cotes de nuit ouvertes) -> fusionné dans day_programme.json.
    Log 'SCAN SOIR : SÉLECTION du SLATE NUIT (coup d''envoi 21h-6h, cotes fraîches) -> fusion au programme'
    & $py 'tools\generate_analyses.py' --sport foot --top 10 --hours 24 --programme --no-notify --ko-from 21 --ko-to 6 2>&1 |
        Add-BfxStream $log
    Log ("SCAN SOIR PROGRAMME NUIT DONE (exit {0})" -f $LASTEXITCODE)
    # 1b) REPLANIFIE les vagues KO-1 h (user 2026-08-30) : les matchs de NUIT ne sont AJOUTÉS au programme
    # qu'ICI (le matin n'écrit que le jour) -> sans ça, ils n'auraient JAMAIS leur vague de publication à
    # KO-1 h (schedule_reana ne tourne sinon qu'à 10h, avant que la nuit existe). Set-ScheduledTask REMPLACE
    # tous les déclencheurs de « BETSFIX Scan Wave » -> repose les matchs encore à venir (jour tardif + nuit),
    # zéro accumulation, zéro doublon. Les vagues de jour déjà passées sont ignorées (at <= now).
    Log 'SCAN SOIR : REPLANIFICATION des vagues KO-1 h (inclut désormais le slate NUIT)'
    & 'C:\Users\vince\BETSFIX\deploy\schedule_reana.ps1' 2>&1 | Add-BfxStream $log
    Log ("SCAN SOIR REANA SCHED DONE (exit {0})" -f $LASTEXITCODE)
    # 2) ANALYSE du slate NUIT SANS publier (Option B) + construction combiné/montante du jour (soir+nuit).
    Log 'SCAN SOIR : SLATE NUIT analysé SANS publier + construction combiné/montante du jour (soir+nuit)'
    & $py 'tools\generate_analyses.py' --sport foot --top 10 --hours 12 --from-programme --no-notify --daily-combo --ko-from 21 --ko-to 6 2>&1 |
        Add-BfxStream $log
    Log ("SCAN SOIR DONE (exit {0})" -f $LASTEXITCODE)
}

# RÉCONCILIATION : règle ce qui est réglable (poste les résultats des matchs de l'après-midi/soirée finis),
# re-poste les pronos imminents manqués. Silencieux (pas de bilan — le bilan reste 1×/jour le matin).
Log 'SOIR RECONCILE : règlement SILENCIEUX'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Add-BfxStream $log
Log ("SOIR RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'SOIR SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Add-BfxStream $log
Log ("SOIR SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)
