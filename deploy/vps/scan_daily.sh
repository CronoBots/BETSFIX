#!/usr/bin/env bash
# Scan JOUR (~10h Brussels) — port fidèle de deploy/scan_daily.ps1 : programme slate jour + logos +
# combiné du jour (caché, Option B) + reconcile. Cron l'entoure d'un flock (anti-doublon, cf. crontab.txt).
set -uo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source .venv/bin/activate
export TZ=Europe/Brussels
PY=python
LOG=data/scan_cron.log
{
  echo "[$(date '+%F %T')] SCAN JOUR (slate 6h->21h)"
  $PY tools/generate_analyses.py --sport foot --top 7 --hours 24 --programme --no-notify --ko-from 6 --ko-to 21
  $PY tools/logo_check.py --quiet --alert
  $PY tools/generate_analyses.py --sport foot --top 10 --hours 24 --from-programme --force --no-notify --daily-combo --ko-from 6 --ko-to 21
  $PY tools/reconcile.py
  echo "[$(date '+%F %T')] SCAN JOUR DONE"
} >> "$LOG" 2>&1
