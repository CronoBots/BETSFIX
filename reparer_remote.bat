@echo off
REM ============================================================
REM  Reparation Remote Control BETSFIX (mobile / claude.ai/code)
REM
REM  Double-clique ce fichier quand la session "BETSFIX" a
REM  disparu du telephone : il nettoie les doublons et RELANCE
REM  la session (aucun droit admin necessaire).
REM
REM  Pour seulement DIAGNOSTIQUER sans rien changer :
REM  utilise diagnose_remote.bat
REM ============================================================

title Reparation Remote Control - BETSFIX
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\repair_remote.ps1"

echo.
pause
