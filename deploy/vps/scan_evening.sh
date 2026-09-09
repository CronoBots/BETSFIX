#!/usr/bin/env bash
# Scan SOIR (~19h Brussels) — port de deploy/scan_evening.ps1 : slate NUIT (21h->6h) + logos +
# combiné du soir + reconcile silencieux.
set -uo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source .venv/bin/activate
export TZ=Europe/Brussels
PY=python
LOG=data/scan_cron.log
{
  echo "[$(date '+%F %T')] SCAN SOIR (slate 21h->6h)"
  $PY tools/generate_analyses.py --sport foot --top 7 --hours 24 --programme --no-notify --ko-from 21 --ko-to 6
  $PY tools/logo_check.py --quiet --alert
  $PY tools/generate_analyses.py --sport foot --top 10 --hours 12 --from-programme --no-notify --daily-combo --ko-from 21 --ko-to 6
  $PY tools/reconcile.py --no-bilan
  echo "[$(date '+%F %T')] SCAN SOIR DONE"
} >> "$LOG" 2>&1
