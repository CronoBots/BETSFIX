# BETSFIX — scan quotidien automatique (tâche planifiée « BETSFIX Scan », compte vince).
# Lance l'analyste sur les 3 sports. SAUTE si un scan tourne déjà (anti-doublon — cf. le piège des
# 2 scans concurrents). Logue tout dans data/scan_cron.log. Le cache 6 h évite de regénérer l'inutile.
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
Set-Location $root

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Out-File -Append -Encoding utf8 $log
}

# Anti-doublon : si un generate_analyses tourne déjà (scan manuel ou passe précédente non finie),
# on ne lance PAS une 2e passe (deux scans concurrents = doublons de cartes).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# MATIN = SYSTÈME HYBRIDE (choix user 2026-07-08) :
#   1) PROGRAMME : écrit la LISTE du jour (data/day_programme.json) pour l'accueil du site + le verrou
#      --from-programme des vagues. SANS Telegram (--no-notify) : la liste ne spamme pas le canal.
#   2) SCAN MATIN COMPLET : analyse TOUS les matchs DU PROGRAMME (--from-programme = exactement la liste
#      écrite en 1, aucune dérive de sélection) et PUBLIE les picks retenus -> CHAQUE match du jour a son
#      pari prêt dès le matin (demande user 2026-07-08). Pose le statut (bet/abstained) sur le programme.
#      --force (demande user 2026-07-09) : ré-analyse AUSSI les matchs DÉJÀ AFFICHÉS/publiés (contourne le
#      gel + le cache 6 h) -> aucun match du programme n'est sauté au matin. Sans spam : un pick INCHANGÉ
#      n'est PAS re-posté ; un pick CHANGÉ est republié et l'ancien devient un fantôme (calibration).
#   Puis les vagues (scan_wave.ps1, ~1 h avant chaque match) RE-VÉRIFIENT : le pari retenu pour le ROI/stats
#   est TOUJOURS le DERNIER généré ; si le prono a CHANGÉ, l'ancien devient un « fantôme » (calibration).
Log 'PROGRAMME : liste du jour (accueil site)'
# 2>&1 | Out-File : capture FIABLE du stdout+stderr natif de python (Out-File = cmdlet, $LASTEXITCODE reste python).
& $py 'tools\generate_analyses.py' --sport foot,tennis,basket --top 5 --hours 24 --programme --no-notify 2>&1 |
    Out-File -Append -Encoding utf8 $log
Log ("PROGRAMME DONE (exit {0})" -f $LASTEXITCODE)
# PLANIFIE LA RÉ-ANALYSE PAR MATCH (coup d'envoi − 1 h) sur « BETSFIX Scan Wave », d'après le programme
# tout juste écrit -> remplace le sondage 30 min par un déclenchement précis, un par match.
Log 'REANA SCHED : planification des ré-analyses (coup d''envoi - 1 h)'
& 'C:\Users\vince\BETSFIX\deploy\schedule_reana.ps1' 2>&1 | Out-File -Append -Encoding utf8 $log
Log 'SCAN MATIN : analyse de TOUT le programme (y compris matchs deja affiches) + publication des picks'
& $py 'tools\generate_analyses.py' --sport foot,tennis,basket --top 5 --hours 24 --from-programme --force 2>&1 |
    Out-File -Append -Encoding utf8 $log
Log ("SCAN MATIN DONE (exit {0})" -f $LASTEXITCODE)

# RÉCONCILIATION : après le scan, on règle tout ce qui est réglable (poste les résultats),
# on re-poste les pronos imminents dont l'envoi a été manqué, et on envoie un BILAN Telegram
# (réglés / en attente / BLOQUÉS / re-postés). Garantit qu'au matin tout est réglé ET posté.
Log 'RECONCILE : règlement + vérif Telegram'
& $py 'tools\reconcile.py' 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (100 % lecture seule) : vérifie qu'aucune confusion de stats/règlement ne s'est
# glissée (chaque contrôle encode une régression déjà survenue). Avance le filigrane de monotonicité et
# alerte Telegram UNIQUEMENT en cas d'ERREUR. Ne bloque jamais le scan (Continue).
Log 'SELFCHECK : auto-audit d''intégrité'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)

# JOURNAL D'APPRENTISSAGE : photo du jour + deltas vs la veille + auto-écriture des événements notables
# (marché écarté / ré-intégré, mouvement de fiabilité/ROI) dans LEARNING.md. Lecture seule.
Log 'LEARNING : journal d''apprentissage'
& $py 'tools\learning.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("LEARNING DONE (exit {0})" -f $LASTEXITCODE)

# BACKTEST de la politique de sélection (lecture seule) : rejoue les seuils sur l'historique, propose un
# changement SEULEMENT s'il est significatif hors-échantillon (alerte Telegram). N'applique JAMAIS rien.
Log 'BACKTEST : politique de sélection'
& $py 'tools\policy_backtest.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("BACKTEST DONE (exit {0})" -f $LASTEXITCODE)

# DOC MÉTHODOLOGIE par sport (lecture seule) : régénère docs/METHODOLOGIE.md — méthode + état mesuré
# (ROI/calibration) + scorecard d'optimalité par sport. Placé APRÈS le backtest pour reprendre son verdict.
Log 'METHODO : doc méthodologie par sport'
& $py 'tools\methodology_doc.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("METHODO DONE (exit {0})" -f $LASTEXITCODE)

# REVUE QUOTIDIENNE (propriétaire, lecture seule) : consolide l'état par sport + détecte les écarts à
# l'optimum -> propositions. Écrit docs/REVUE.md + journal. `--telegram` = push PRIVÉ si data/owner_chat.txt
# existe (JAMAIS le canal abonnés). Placé APRÈS methodo/backtest pour reprendre leurs verdicts frais.
Log 'REVUE : revue quotidienne proprietaire'
& $py 'tools\daily_review.py' --quiet --telegram 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("REVUE DONE (exit {0})" -f $LASTEXITCODE)

# SANTÉ DES SOURCES (Phase 4) : ping live de chaque source (analyse + règlement). Détecte une source
# morte AVANT qu'elle dégrade les analyses. Alerte Telegram UNIQUEMENT si une source CRITIQUE (Unibet/
# FotMob) est down. Journal data/source_health_log.jsonl. Ne bloque jamais le scan (Continue).
Log 'SOURCES : santé des sources'
& $py 'tools\source_health.py' --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
Log ("SOURCES DONE (exit {0})" -f $LASTEXITCODE)
