@echo off
REM ============================================================================
REM  install_autostart.bat  --  BETSFIX
REM  Recree l'auto-demarrage 100%% sans login :
REM    - service Windows "Cloudflared" (tunnel api.betsfix.com)
REM    - tache planifiee "BETSFIX-api" (lance + relance l'API uvicorn au boot)
REM
REM  A double-cliquer apres avoir renomme/deplace le dossier du projet.
REM  Le script s'auto-eleve en administrateur (fenetre UAC) si besoin.
REM  Le token Cloudflare est lu depuis %USERPROFILE%\.cloudflared\api_token.txt
REM ============================================================================

REM --- Auto-elevation en administrateur -------------------------------------
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo Demande des droits administrateur...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo === Reinstallation de l'auto-demarrage BETSFIX ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\setup_full_service.ps1"

echo.
echo Termine. Appuie sur une touche pour fermer cette fenetre.
pause >nul
