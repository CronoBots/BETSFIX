# BETSFIX — scan quotidien automatique (tâche planifiée « BETSFIX Scan », compte vince).
# Lance l'analyste sur les 3 sports. SAUTE si un scan tourne déjà (anti-doublon — cf. le piège des
# 2 scans concurrents). Logue tout dans data/scan_cron.log. Le cache 6 h évite de regénérer l'inutile.
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\vince\BETSFIX'
$py   = 'C:\Users\vince\AppData\Local\Programs\Python\Python312\python.exe'
$log  = Join-Path $root 'data\scan_cron.log'
$flag = Join-Path $root 'data\scan_wave_first.flag'   # présent = mode WAVE-FIRST (analyse ~1h avant KO)
Set-Location $root
. (Join-Path $root 'deploy\_log.ps1')   # log concurrent-safe (Add-BfxStream / Write-BfxLogLine)

function Log($m) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-BfxStream $log
}

# Anti-doublon : si un generate_analyses tourne déjà (scan manuel ou passe précédente non finie),
# on ne lance PAS une 2e passe (deux scans concurrents = doublons de cartes).
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'generate_analyses' }
if ($running) {
    Log ("SKIP : un scan tourne déjà (PID {0})" -f ($running.ProcessId -join ','))
    exit 0
}

# MATIN = SLATE JOUR (2 créneaux, user 2026-08-07 : séparer Europe/Amériques par heure de coup d'envoi
# pour analyser chaque match au bon moment, une seule fois, sans re-scanner une région) :
#   1) PROGRAMME = SLATE JOUR (--ko-from 6 --ko-to 21) : écrit la liste des matchs de JOUR (data/day_programme.json)
#      pour l'accueil du site + le verrou --from-programme. SANS Telegram (--no-notify). Le SLATE NUIT est
#      sélectionné le SOIR (scan_evening.ps1, ~19h) quand ses cotes Unibet sont ENFIN ouvertes (fusionné dans
#      le même day_programme.json via la préservation same-day) -> le boost favori-net traite la nuit comme le
#      jour au lieu de l'écraser faute de cote à 10h (user 2026-08-30).
#   2) SCAN MATIN = SLATE JOUR SEULEMENT (--ko-from 6 --ko-to 21) : analyse et PUBLIE les matchs dont le
#      coup d'envoi (heure belge) est entre 06h et 21h (Europe + Asie-après-midi). Les matchs de NUIT
#      (21h→06h : Amériques + Europe tardive) sont ANALYSÉS LE SOIR par scan_evening.ps1 (~19h) -> données
#      fraîches du jour même au lieu de 12-20 h de retard. Aucun chevauchement : la bande de coup d'envoi
#      partitionne la journée, un match tombe dans UN seul slate. --force = full matin (ignore cache 6 h).
#   Plus de ré-analyse pré-match (user 2026-08-07) : le pick de CHAQUE slate est DÉFINITIF une fois posé.
Log 'PROGRAMME : SLATE JOUR (coup d''envoi 6h-21h) pour l''accueil site — la nuit est sélectionnée le soir'
# 2>&1 | Out-File : capture FIABLE du stdout+stderr natif de python (Out-File = cmdlet, $LASTEXITCODE reste python).
# FOOTBALL SEUL (user 2026-08-07) : tennis/basket retirés -> tout le budget Claude au foot.
# --top 10 = BUDGET TOTAL du jour (top-N GLOBAL ADAPTATIF, user 2026-08-24 : RETOUR à la SÉLECTIVITÉ de la
# période gagnante — ~5-10 matchs analysés EN PROFONDEUR/jour au lieu de ~20 survolés qui saturaient le
# forfait). Les 10 matchs les PLUS IMPORTANTS des 24 h, répartis par créneau selon leur coup d'envoi (jour
# analysé le matin, nuit le soir). Le split suit la vraie distribution (ex. 7 JOUR + 3 NUIT).
& $py 'tools\generate_analyses.py' --sport foot --top 6 --hours 24 --programme --no-notify --ko-from 6 --ko-to 21 2>&1 |
    Add-BfxStream $log
Log ("PROGRAMME DONE (exit {0})" -f $LASTEXITCODE)
# PLANIFIE LES PASSES DE RÈGLEMENT PAR MATCH (coup d'envoi − 1 h) sur « BETSFIX Scan Wave », d'après le
# programme tout juste écrit -> règlement rapide autour de chaque match (la ré-analyse est supprimée).
Log 'REANA SCHED : planification des passes de règlement (coup d''envoi - 1 h)'
& 'C:\Users\vince\BETSFIX\deploy\schedule_reana.ps1' 2>&1 | Add-BfxStream $log
# SCAN MATIN (analyse du slate JOUR en batch) : SAUTÉ en mode WAVE-FIRST (user 2026-08-11). Le matin ne fait
# alors que SÉLECTIONNER (programme ci-dessus) ; chaque match est analysé ~1h avant SON coup d'envoi par le
# sweep (deploy\scan_sweep.ps1, données/cotes fraîches). Sans le drapeau -> comportement batch inchangé.
if (Test-Path $flag) {
    Log 'SCAN MATIN : SAUTÉ (mode WAVE-FIRST) -> analyse par le sweep ~1h avant chaque coup d''envoi'
} else {
    # OPTION B (user 2026-08-23) : le matin ANALYSE tout le slate JOUR mais NE PUBLIE PAS (--no-notify). Chaque
    # pari est publié ~1 h avant SON coup d'envoi par la vague (scan_wave.ps1 --refresh-early), après re-analyse
    # sur données fraîches (compos/blessures/cotes) -> re-post si changé, abstention s'il ne valide plus. C'est
    # la mécanique de la période gagnante, sans les flips visibles (rien n'est posté avant d'être vérifié).
    Log 'SCAN MATIN : SLATE JOUR analysé SANS publier (--no-notify) -> publication à la vague KO - 1 h'
    & $py 'tools\generate_analyses.py' --sport foot --top 10 --hours 24 --from-programme --force --no-notify --ko-from 6 --ko-to 21 2>&1 |
        Add-BfxStream $log
    Log ("SCAN MATIN DONE (exit {0})" -f $LASTEXITCODE)
}

# RÉCONCILIATION : après le scan, on règle tout ce qui est réglable (poste les résultats),
# on re-poste les pronos imminents dont l'envoi a été manqué, et on envoie un BILAN Telegram
# (réglés / en attente / BLOQUÉS / re-postés). Garantit qu'au matin tout est réglé ET posté.
Log 'RECONCILE : règlement + vérif Telegram'
& $py 'tools\reconcile.py' 2>&1 | Add-BfxStream $log
Log ("RECONCILE DONE (exit {0})" -f $LASTEXITCODE)

# DÉBRIEF DES PERTES (mémoire évolutive, demande user 2026-08-02) : après le règlement, on analyse POURQUOI
# chaque nouveau pari joué PERDU a perdu (malchance/variance vs prémisse évitable) et on alimente
# data/lessons.json. Pilote Claude headless (comme le scan) -> DOIT tourner dans cette session authentifiée.
# Incrémental (ne traite que les pertes non encore débriefées). Purement additif (jamais ROI/stats/calib).
Log 'DEBRIEF : analyse des paris joués perdus (mémoire évolutive)'
& $py 'tools\debrief.py' --sport foot 2>&1 | Add-BfxStream $log
Log ("DEBRIEF DONE (exit {0})" -f $LASTEXITCODE)

# AUTO-AUDIT d'intégrité (100 % lecture seule) : vérifie qu'aucune confusion de stats/règlement ne s'est
# glissée (chaque contrôle encode une régression déjà survenue). Avance le filigrane de monotonicité et
# alerte Telegram UNIQUEMENT en cas d'ERREUR. Ne bloque jamais le scan (Continue).
Log 'SELFCHECK : auto-audit d''intégrité'
& $py 'tools\selfcheck.py' --quiet 2>&1 | Add-BfxStream $log
Log ("SELFCHECK DONE (exit {0})" -f $LASTEXITCODE)

# JOURNAL D'APPRENTISSAGE : photo du jour + deltas vs la veille + auto-écriture des événements notables
# (marché écarté / ré-intégré, mouvement de fiabilité/ROI) dans LEARNING.md. Lecture seule.
Log 'LEARNING : journal d''apprentissage'
& $py 'tools\learning.py' --quiet 2>&1 | Add-BfxStream $log
Log ("LEARNING DONE (exit {0})" -f $LASTEXITCODE)

# BACKTEST de la politique de sélection (lecture seule) : rejoue les seuils sur l'historique, propose un
# changement SEULEMENT s'il est significatif hors-échantillon (alerte Telegram). N'applique JAMAIS rien.
Log 'BACKTEST : politique de sélection'
& $py 'tools\policy_backtest.py' --quiet 2>&1 | Add-BfxStream $log
Log ("BACKTEST DONE (exit {0})" -f $LASTEXITCODE)

# DOC MÉTHODOLOGIE par sport (lecture seule) : régénère docs/METHODOLOGIE.md — méthode + état mesuré
# (ROI/calibration) + scorecard d'optimalité par sport. Placé APRÈS le backtest pour reprendre son verdict.
Log 'METHODO : doc méthodologie par sport'
& $py 'tools\methodology_doc.py' --quiet 2>&1 | Add-BfxStream $log
Log ("METHODO DONE (exit {0})" -f $LASTEXITCODE)

# REVUE QUOTIDIENNE (propriétaire, lecture seule) : consolide l'état par sport + détecte les écarts à
# l'optimum -> propositions. Écrit docs/REVUE.md + journal. `--telegram` = push PRIVÉ si data/owner_chat.txt
# existe (JAMAIS le canal abonnés). Placé APRÈS methodo/backtest pour reprendre leurs verdicts frais.
Log 'REVUE : revue quotidienne proprietaire'
& $py 'tools\daily_review.py' --quiet --telegram 2>&1 | Add-BfxStream $log
Log ("REVUE DONE (exit {0})" -f $LASTEXITCODE)

# SANTÉ DES SOURCES (Phase 4) : ping live de chaque source (analyse + règlement). Détecte une source
# morte AVANT qu'elle dégrade les analyses. Alerte Telegram UNIQUEMENT si une source CRITIQUE (Unibet/
# FotMob) est down. Journal data/source_health_log.jsonl. Ne bloque jamais le scan (Continue).
Log 'SOURCES : santé des sources'
& $py 'tools\source_health.py' --quiet 2>&1 | Add-BfxStream $log
Log ("SOURCES DONE (exit {0})" -f $LASTEXITCODE)
