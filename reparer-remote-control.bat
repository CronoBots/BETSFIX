@echo off
REM ============================================================
REM  REPARE le Remote Control apres un deplacement/renommage
REM  du dossier (ex: api-sport -> BETSFIX).
REM
REM  Double-clique ce fichier : il se relocalise tout seul,
REM  nettoie les anciens lanceurs casses, en recree un sur le
REM  bon chemin, et redemarre le remote control tout de suite.
REM ============================================================
title BETSFIX - Reparer Remote Control
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\reparer_remote_control.ps1"
echo.
echo ------------------------------------------------------------
echo  Termine. Verifie la session BETSFIX sur ton mobile.
echo ------------------------------------------------------------
pause >nul
