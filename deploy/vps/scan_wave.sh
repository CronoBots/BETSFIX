#!/usr/bin/env bash
# VAGUE (/30 min) — port de deploy/scan_wave.ps1 : re-vérifie/PUBLIE chaque match ~1h avant SON coup
# d'envoi (--refresh-early) puis reconcile. C'est ce qui PUBLIE les paris (app + Telegram) et règle les
# jambes. Le flock partagé (crontab) garantit qu'une vague ne chevauche pas un scan matin/soir en cours.
set -uo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source .venv/bin/activate
export TZ=Europe/Brussels
PY=python
LOG=data/scan_cron.log
{
  echo "[$(date '+%F %T')] VAGUE (--refresh-early)"
  $PY tools/generate_analyses.py --sport foot --top 10 --hours 3 --from-programme --refresh-early
  $PY tools/reconcile.py --no-bilan
} >> "$LOG" 2>&1
