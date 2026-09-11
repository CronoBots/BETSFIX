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

# Détecte un scan/vague concurrent. ⚠️ ON NE SKIPPE PLUS tout le scan du soir là-dessus (bug user 2026-08-30) :
# la SÉLECTION nuit (écrit juste le programme, AUCUNE carte) + la replanification des vagues DOIVENT tourner
# même si une vague KO-1h est en cours — sinon, les soirs où un match joue à 20h, la vague de 19h faisait
# SKIPper tout le scan et AUCUN match de nuit n'entrait au programme (la nuit n'est plus sélectionnée qu'ICI).
# Seule la 2e passe d'ANALYSE est différée si un scan concurrent tourne (deux analyses = races/doublons de
# cartes) : les matchs de nuit non analysés à 19h sont alors pris par LEUR vague KO-1h (1re analyse = filet nuit).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }

# SLATE NUIT : coup d'envoi 21h→06h (heure belge). PLUS D'ANALYSE COMPLÈTE le soir (user 2026-09-11) : elle
# n'existait QUE pour bâtir le COMBINÉ DU SOIR. Combinés STOPPÉS (combo_daily.COMBO_ENABLED=False) -> le soir
# ne fait QUE : (1) SÉLECTIONNER le slate nuit (fusion au programme), (2) pré-chauffer les logos, (3) REPLANIFIER
# les vagues KO-1 h (indispensable : les matchs de nuit n'existent au programme qu'ICI). Chaque match de nuit est
# analysé UNE SEULE FOIS à sa vague, qui publie. Réactiver le combiné du soir = COMBO_ENABLED
# =True + rétablir une passe « --from-programme --no-notify --daily-combo --ko-from 21 --ko-to 6 » après la replanif.
# 1) SÉLECTION du slate NUIT (adaptatif, cotes de nuit ouvertes) -> fusionné dans day_programme.json.
# TOUJOURS exécutée (safe même si une vague tourne : écrit le programme, aucune carte, aucun sidecar).
Log 'SCAN SOIR : SÉLECTION du SLATE NUIT (coup d''envoi 21h-6h, cotes fraîches) -> fusion au programme'
& $py 'tools\generate_analyses.py' --sport foot --top 10 --hours 24 --programme --no-notify --ko-from 21 --ko-to 6 2>&1 |
    Add-BfxStream $log
Log ("SCAN SOIR PROGRAMME NUIT DONE (exit {0})" -f $LASTEXITCODE)
# 1b) REPLANIFIE les vagues KO-1 h (user 2026-08-30) : les matchs de NUIT ne sont AJOUTÉS au programme
# qu'ICI (le matin n'écrit que le jour) -> sans ça, ils n'auraient JAMAIS leur vague de publication à
# KO-1 h (schedule_reana ne tourne sinon qu'à 10h, avant que la nuit existe). Set-ScheduledTask REMPLACE
# tous les déclencheurs de « BETSFIX Scan Wave » -> repose les matchs encore à venir (jour tardif + nuit),
# zéro accumulation, zéro doublon. Les vagues de jour déjà passées sont ignorées (at <= now). TOUJOURS.
# LOGOS DES ÉQUIPES (user 2026-09-02) : même contrôle qu'au matin, appliqué au programme APRÈS fusion
# du slate NUIT -> les clubs sud-américains / d'Europe tardive (souvent absents de la recherche FotMob
# par nom) sont résolus via les fixtures du jour et mis en cache AVANT leur vague de publication.
Log 'LOGOS : vérification/pré-chauffe des blasons (programme JOUR + NUIT fusionnés)'
& $py 'tools\logo_check.py' --quiet --alert 2>&1 | Add-BfxStream $log
Log ("LOGOS DONE (exit {0})" -f $LASTEXITCODE)

Log 'SCAN SOIR : REPLANIFICATION des vagues KO-1 h (inclut désormais le slate NUIT)'
& 'C:\Users\vince\BETSFIX\deploy\schedule_reana.ps1' 2>&1 | Add-BfxStream $log
Log ("SCAN SOIR REANA SCHED DONE (exit {0})" -f $LASTEXITCODE)

# RÉCONCILIATION : règle ce qui est réglable (poste les résultats des matchs de l'après-midi/soirée finis),
# re-poste les pronos imminents manqués. Silencieux (pas de bilan — le bilan reste 1×/jour le matin).
Log 'SOIR RECONCILE : règlement SILENCIEUX'
& $py 'tools\reconcile.py' --no-bilan 2>&1 | Add-BfxStream $log
Log ("SOIR RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (lecture seule) : garde-fou anti-régression, alerte Telegram seulement si ERREUR.
Log 'SOIR SELFCHECK'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Add-BfxStream $log
Log ("SOIR SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)

# SHADOW API-Football (LECTURE SEULE, best-effort) : cf. scan_daily.ps1. Compare l'ancre/omap du soir à
# API-Football, accumule data/apifootball_shadow/<date>.json. Inactif sans BETSFIX_APIFOOTBALL_KEY (.env).
# N'influence NI sélection NI stats NI sidecars. Throttle 0.5 s (plan Pro = 300/min).
Log 'SOIR SHADOW API-FOOTBALL : comparaison lecture seule'
$env:BETSFIX_APIFOOTBALL_THROTTLE = '0.5'   # Pro = 300/min
& $py 'tools\apifootball_shadow.py' 2>&1 | Add-BfxStream $log
Log ("SOIR SHADOW API-FOOTBALL DONE (exit {0})" -f $LASTEXITCODE)

# SHADOW DES PICKS (Étape 2 migration, LECTURE SEULE) : cf. scan_daily.ps1. Compare le pick réel au pick sur
# cotes API-Football, accumule data/apifootball_shadow/picks/<date>/. N'influence NI sélection NI pari joué.
Log 'SOIR SHADOW PICKS API-FOOTBALL : le pick changerait-il ? (lecture seule)'
& $py 'tools\apifootball_pick_shadow.py' 2>&1 | Add-BfxStream $log
Log ("SOIR SHADOW PICKS API-FOOTBALL DONE (exit {0})" -f $LASTEXITCODE)
