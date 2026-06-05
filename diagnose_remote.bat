@echo off
REM ============================================================
REM  Diagnostic Remote Control BETSFIX (mobile / claude.ai/code)
REM
REM  Double-clique ce fichier : il dit pourquoi la session
REM  "BETSFIX" n'apparait pas sur le telephone (compte, doublon,
REM  session morte, tache, veille...). Il ne MODIFIE rien.
REM ============================================================

title Diagnostic Remote Control - BETSFIX
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\diagnose_remote.ps1"

echo.
pause
