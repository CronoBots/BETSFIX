#!/usr/bin/env bash
# ── Provisionnement VPS BETSFIX (Ubuntu 22.04+) ────────────────────────────────────────────────
# Idempotent. À lancer une fois sur le VPS après `git clone`. Cf. docs/VPS_MIGRATION.md.
# Prérequis décidés côté user : VPS Ubuntu, clés régénérées (API-Football + secrets) dans .env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"     # racine du repo
cd "$ROOT"

echo "== 1) Paquets système (python3.12, chromium pour les cartes-images/sofa_browser, git) =="
sudo apt-get update -y
# chromium : REQUIS pour tools/card_image.py (cartes Telegram) + app/sofa_browser.py (repli).
# Le code trouve le binaire via shutil.which("chromium") -> on l'installe + on expose `chrome`.
sudo apt-get install -y python3.12 python3.12-venv python3-pip chromium-browser git tzdata curl fonts-liberation
sudo ln -sf "$(command -v chromium-browser || command -v chromium)" /usr/local/bin/chrome 2>/dev/null || true

echo "== 2) Fuseau horaire Europe/Brussels (les scans/cron raisonnent en heure belge) =="
sudo timedatectl set-timezone Europe/Brussels || true

echo "== 3) venv + dépendances Python =="
python3.12 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
if [ -f requirements.txt ]; then pip install -r requirements.txt
else pip install fastapi "uvicorn[standard]" httpx curl_cffi pydantic pydantic-settings python-multipart stripe; fi

echo "== 4) Claude Code CLI (pour claude -p = analyses ET la remote control) =="
# Abonnement Pro Max (PAS une clé API payée au token) : on installe le CLI et on AUTHENTIFIE À LA MAIN
# une fois (voir README). L'auth persiste dans ~/.claude.
command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code || \
  echo "  ⚠️ installe Node puis: npm install -g @anthropic-ai/claude-code"

echo "== 5) .env (secrets) =="
[ -f .env ] || { echo "  ⚠️ CRÉE .env (BETSFIX_APIFOOTBALL_KEY RÉGÉNÉRÉE, BETSFIX_SMTP_*, Stripe, KV, etc.)"; \
  echo "  Modèle minimal :"; cat <<'ENV'
BETSFIX_APIFOOTBALL_KEY=___RÉGÉNÉRÉE___
BETSFIX_PUBLIC_URL=https://betsfix.com
ODDS_API_KEY=___
# SofaScore n'utilise PLUS iProyal (sofa_proxy vide OK) ; iProyal RETIRÉ.
ENV
}

echo "== 6) Vérifs =="
source .venv/bin/activate
python -c "import app.main; print('import app OK')"
python -c "from app import apifootball as A; import httpx; c=A._client(); print('API-Football live:', len(A.live_all(c))); c.close()" || true
chrome --version || echo "  ⚠️ chromium introuvable (cartes Telegram KO)"
claude --version || echo "  ⚠️ claude CLI absent (scans KO) -> installe + auth"

echo "== OK. Ensuite : authentifier Claude (claude login), installer systemd + cron (cf. README.md), DOUBLE-RUN. =="
