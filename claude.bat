@echo off
REM ============================================================
REM  Lance Claude Code EN LOCAL dans le dossier du projet.
REM
REM  Double-clique ce fichier pour ouvrir une session Claude qui
REM  agit directement sur CE PC : les requetes (SofaScore, Unibet)
REM  partent de ton IP belge -> pas de blocage bot ni de geo-restriction.
REM
REM  Prerequis (une fois) :
REM    - Node.js 18+        : https://nodejs.org
REM    - Claude Code        : npm install -g @anthropic-ai/claude-code
REM ============================================================

title Claude Code - API-SPORT
cd /d "%~dp0"

where claude >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERREUR] La commande "claude" est introuvable.
  echo.
  echo Installe Claude Code d'abord, dans PowerShell :
  echo     npm install -g @anthropic-ai/claude-code
  echo (Node.js 18+ requis : https://nodejs.org^)
  echo.
  pause
  exit /b 1
)

echo Demarrage de Claude Code dans : %CD%
echo.
claude
