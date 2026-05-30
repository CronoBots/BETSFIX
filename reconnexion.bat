@echo off
REM ============================================================
REM  Relance rapide de l'acces remote (API + tunnel Cloudflare)
REM
REM  Double-clique ce fichier pour tout redemarrer d'un coup :
REM    - lance l'API (uvicorn) si elle ne tourne pas deja
REM    - lance le tunnel Cloudflare (URL fixe https://api.betsfix.com)
REM
REM  Garde la fenetre OUVERTE : tant qu'elle est ouverte, le
REM  tunnel vit. La fermer = couper l'acces (Error 1033).
REM ============================================================

title API-SPORT - Reconnexion remote
cd /d "%~dp0"

echo ============================================================
echo   API-SPORT : redemarrage API + tunnel
echo   Mobile : https://api.betsfix.com/docs
echo ============================================================
echo.
echo   NE FERME PAS cette fenetre tant que tu veux l'acces.
echo.

REM -ExecutionPolicy Bypass : contourne le blocage des .ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\run_mobile.ps1"

echo.
echo ------------------------------------------------------------
echo   Le tunnel s'est arrete. Appuie sur une touche pour fermer.
echo ------------------------------------------------------------
pause >nul
